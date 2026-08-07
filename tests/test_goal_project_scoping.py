"""Goals stranded with an empty project_id are invisible to the default listing.

A goal with no `project_id` is unaddressable rather than lost: every
project-scoped listing skips it, and the project-scoped one is the default.
Measured 2026-08-07 on the empirica practice — 305 of 1520 — and the pattern is
fleet-wide (cortex 72, one archive 216, autopilot 9).

Migration 065 writes **only where the goal's own session row names a project**.
That restraint is the load-bearing part: a practice db can hold more than one
registered project (this one holds `Empirica` and `empirica-platform`), so
assigning the remainder to the main project is an inference — and an inference
shipped as a migration hardens into a fact on every machine at once.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from empirica.data.migrations.migrations import migration_065_backfill_goal_project_id_from_session as migrate

PROJ = "748a81a2-ac14-45b8-a185-994997b76828"
OTHER = "47e34466-47ba-4a30-80be-987ec17416d8"


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT, goal_data TEXT)")
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT)")
    return conn


def test_a_session_evidenced_goal_is_scoped(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g1', 's1', '', NULL)")
    migrate(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT project_id FROM goals WHERE id='g1'").fetchone()[0] == PROJ


def test_a_goal_with_no_evidence_is_left_alone(tmp_path):
    """The restraint that keeps an inference out of 39 databases."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO goals VALUES ('g2', 's-missing', '', NULL)")
    conn.execute("INSERT INTO sessions VALUES ('s2', '')")
    conn.execute("INSERT INTO goals VALUES ('g3', 's2', '', NULL)")
    migrate(conn.cursor())
    conn.commit()
    rows = dict(conn.execute("SELECT id, project_id FROM goals").fetchall())
    assert rows["g2"] == "", "no session row — nothing evidences a project"
    assert rows["g3"] == "", "session exists but names no project — still no evidence"


def test_an_existing_project_id_is_never_overwritten(tmp_path):
    """Including one pointing at the practice's OTHER registered project."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g4', 's1', ?, NULL)", (OTHER,))
    migrate(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT project_id FROM goals WHERE id='g4'").fetchone()[0] == OTHER


def test_provenance_is_recorded_so_the_write_is_reversible(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g5', 's1', '', ?)", (json.dumps({"objective": "x"}),))
    migrate(conn.cursor())
    conn.commit()
    data = json.loads(conn.execute("SELECT goal_data FROM goals WHERE id='g5'").fetchone()[0])
    assert data["objective"] == "x", "existing goal_data must survive"
    bf = data["project_id_backfill"]
    assert bf["assigned"] == PROJ and bf["prior"] == "" and bf["tier"] == "session_evidence"


def test_malformed_goal_data_does_not_break_the_row(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g6', 's1', '', 'not json')")
    migrate(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT project_id FROM goals WHERE id='g6'").fetchone()[0] == PROJ


def test_missing_tables_are_a_noop(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    migrate(conn.cursor())  # must not raise


# ---- --scope: project / practice / fleet -----------------------------------
#
# `--all-projects` crossed project_ids inside ONE sessions.db and was read as
# "the whole fleet". Measured 2026-08-07: it reached 1520 goals in one practice
# while the fleet held 726 open across 25 practices, unreachable by any flag.


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A synthetic practice + two registered peers.

    These tests originally read the developer's own machine — a resolvable
    session db and a populated ~/.empirica/registry.yaml. Both are true here and
    neither is true in CI, so they passed locally and failed on every runner.
    A test that asserts on ambient state measures the box, not the code.
    """
    import yaml

    own = tmp_path / "own" / ".empirica" / "sessions" / "sessions.db"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"")

    entries = []
    for slug in ("peer-a", "peer-b"):
        peer = tmp_path / slug
        db = peer / ".empirica" / "sessions" / "sessions.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"")
        entries.append({"path": str(peer), "slug": slug})
    # A registered path whose db does not exist must be skipped, not listed.
    entries.append({"path": str(tmp_path / "never-initialised"), "slug": "peer-ghost"})

    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)
    (home / ".empirica" / "registry.yaml").write_text(yaml.safe_dump({"projects": entries}))

    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(
        "empirica.config.path_resolver.get_session_db_path",
        lambda: own,
    )
    return {"own": str(own), "peers": ["peer-a", "peer-b"]}


def test_scope_project_and_practice_read_only_this_db(fleet):
    from empirica.cli.command_handlers.goal_commands import _dbs_for_scope

    for scope in ("project", "practice"):
        dbs = _dbs_for_scope(scope)
        assert len(dbs) == 1, f"{scope} must not leave this practice's db"
        assert dbs[0][1] == "this practice"
        assert dbs[0][0] == fleet["own"]


def test_scope_fleet_reaches_other_practices(fleet):
    from empirica.cli.command_handlers.goal_commands import _dbs_for_scope

    dbs = _dbs_for_scope("fleet")
    assert len(dbs) > 1, "fleet must reach registered peers — that is the whole point"
    assert dbs[0][1] == "this practice", "own db stays first"
    assert len({d[0] for d in dbs}) == len(dbs), "no db listed twice"
    assert {d[1] for d in dbs} == {"this practice", *fleet["peers"]}


def test_fleet_skips_registered_paths_with_no_database(fleet):
    """A registered project that was never initialised has no db to open."""
    from empirica.cli.command_handlers.goal_commands import _dbs_for_scope

    assert "peer-ghost" not in {label for _, label in _dbs_for_scope("fleet")}


def test_fleet_falls_back_to_own_db_when_registry_is_unreadable(tmp_path, monkeypatch):
    """No registry is a degraded fleet, not an empty one — returning [] would
    have made `--scope fleet` silently report zero goals everywhere."""
    from empirica.cli.command_handlers.goal_commands import _dbs_for_scope

    own = tmp_path / "own" / ".empirica" / "sessions" / "sessions.db"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"")
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: empty_home))
    monkeypatch.setattr("empirica.config.path_resolver.get_session_db_path", lambda: own)

    dbs = _dbs_for_scope("fleet")
    assert [label for _, label in dbs] == ["this practice"]


def test_fleet_opens_peer_databases_READ_ONLY():
    """A peer's graph is never gardened from here. The sweep counts; it never writes."""
    import inspect

    from empirica.cli.command_handlers import goal_commands as m

    src = inspect.getsource(m._print_fleet_goal_summary)
    assert "mode=ro" in src, "peer dbs must be opened read-only"
    for write_verb in ("UPDATE", "DELETE", "INSERT", "commit()"):
        assert write_verb not in src, f"fleet summary must never {write_verb}"


def test_shared_scope_is_absent_rather_than_guessed():
    """`shared` needs a membership source (SER participants) that is not settled.
    An arm that silently guesses its members is worse than one that does not exist."""
    from pathlib import Path

    from empirica.cli.parsers.checkpoint_parsers import __file__ as pf

    src = Path(pf).read_text()
    assert '"project", "practice", "fleet"' in src
    assert '"shared"' not in src.split("--scope")[1][:400]
