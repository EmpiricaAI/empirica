"""PREFLIGHT can name the goals a transaction works on (goal_ids), so a goal carried across transactions is in play.

ecodex carries one goal across many transactions (a 471-commit fold, worked cluster by cluster). The Sentinel counted a goal
as in play only when it was created in the transaction, a task on it moved since PREFLIGHT, or a finding was logged against
it, so a carried goal drew the no-goal nudge on every gated call until a task landed (~15 times in one transaction). Nothing
recorded "this transaction works goal X" (prop_bb7zspcksngi5ismdssij3uq2i; David approved 2026-11-14).

goal_ids rides the transaction file and the PREFLIGHT checkpoint. goals.transaction_id is not rewritten: it records where a
goal was created.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

from empirica.cli.command_handlers import _workflow_preflight as wp
from empirica.cli.command_handlers.artifact_log_commands import _resolve_goal_for_artifact
from empirica.cli.validation import PreflightInput
from empirica.data.session_database import SessionDatabase

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
_VECTORS = {"know": 0.7, "uncertainty": 0.3}


@pytest.fixture
def world(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".empirica" / "sessions").mkdir(parents=True)
    db_path = str(project / ".empirica" / "sessions" / "sessions.db")
    monkeypatch.setenv("EMPIRICA_SESSION_DB", db_path)
    db = SessionDatabase(db_path=db_path)
    pid = db.create_project(name="carry")
    other = db.create_project(name="elsewhere")
    sid = db.create_session(ai_id="t", project_id=pid)
    counter = [0]

    def goal(status="in_progress", transaction_id="tx-earlier", project_id=None, completed=0):
        counter[0] += 1
        gid = f"goal{counter[0]:03d}-{status}-0000-0000-000000000000"
        db.conn.execute(
            "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status, is_completed, "
            "project_id, transaction_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (gid, sid, f"obj {gid}", "{}", time.time() + counter[0], json.dumps({"subtasks": []}), status, completed,
             project_id or pid, transaction_id),
        )  # fmt: skip
        db.conn.commit()
        return gid

    yield types.SimpleNamespace(db=db, pid=pid, other=other, sid=sid, goal=goal)
    db.close()


# ── the field ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_schema_takes_goal_ids_and_bounds_them():
    ok = PreflightInput(session_id="s", vectors=_VECTORS, goal_ids=["abc12345"])
    assert ok.goal_ids == ["abc12345"]
    assert PreflightInput(session_id="s", vectors=_VECTORS).goal_ids is None
    with pytest.raises(ValueError):
        PreflightInput(session_id="s", vectors=_VECTORS, goal_ids=[""])
    with pytest.raises(ValueError):
        PreflightInput(session_id="s", vectors=_VECTORS, goal_ids=[f"g{i}" for i in range(11)])


# ── resolved and refused at PREFLIGHT, before the transaction opens ──────────────────────────────────────────────────────


def test_ids_resolve_by_prefix_in_order_without_duplicates(world):
    a, b = world.goal(), world.goal(status="planned")
    assert wp._preflight_resolve_goal_ids([b[:7], a, b], world.sid, world.db) == [b, a]


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("missing", "matches no goal"),
        ("completed", "is completed"),
        ("abandoned", "is abandoned"),
        ("foreign", "belongs to"),
    ],
)
def test_a_goal_that_cannot_be_worked_here_is_refused(world, kind, message):
    gid = {
        "missing": lambda: "nosuchgoal",
        "completed": lambda: world.goal(status="completed", completed=1),
        "abandoned": lambda: world.goal(status="abandoned"),
        "foreign": lambda: world.goal(project_id=world.other),
    }[kind]()
    with pytest.raises(ValueError, match=message):
        wp._preflight_resolve_goal_ids([gid], world.sid, world.db)


def test_no_goal_ids_is_no_work(world):
    assert wp._preflight_resolve_goal_ids(None, world.sid, world.db) == []


# ── recorded with the transaction, and goals.transaction_id left alone ──────────────────────────────────────────────────


def test_the_transaction_file_and_checkpoint_carry_them(tmp_path, monkeypatch):
    tx_file = tmp_path / ".empirica" / "active_transaction.json"
    tx_file.parent.mkdir(parents=True)
    tx_file.write_text(json.dumps({"transaction_id": "tx-now", "status": "open"}))
    monkeypatch.setattr(wp.R, "instance_suffix", lambda: "")
    parsed = dict.fromkeys(("work_context", "work_type", "domain", "criticality", "predicted_check_outcomes"))
    wp._preflight_enrich_transaction_file(str(tmp_path), {**parsed, "goal_ids": ["g-1", "g-2"]})
    assert json.loads(tx_file.read_text())["goal_ids"] == ["g-1", "g-2"]

    import inspect

    assert "goal_ids" in inspect.signature(wp._preflight_create_checkpoint).parameters


# ── the Sentinel counts a named goal as in play ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def gate():
    lib = str(_HOOK_DIR.parent / "lib")
    sys.path.insert(0, lib)
    try:
        spec = importlib.util.spec_from_file_location("sentinel_gate_goal_ids", _HOOK_DIR / "sentinel-gate.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(lib)


def _empty_cursor():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE goals (id TEXT, project_id TEXT, transaction_id TEXT, status TEXT);
        CREATE TABLE subtasks (id TEXT, goal_id TEXT, created_timestamp REAL, completed_timestamp REAL);
        CREATE TABLE project_findings (id TEXT, transaction_id TEXT, goal_id TEXT);
        """
    )
    return conn.cursor()


def test_a_named_goal_silences_the_nudge_and_none_does_not(gate, monkeypatch):
    monkeypatch.setattr(gate, "_tool_call_count", 8)
    assert (
        "no goal in play" in gate._check_goalless_work(_empty_cursor(), "", "tx-now", 1.0).lower()
    )  # positive control
    assert gate._check_goalless_work(_empty_cursor(), "", "tx-now", 1.0, goal_ids=["g-1"]) == ""


def test_the_tracker_passes_the_transaction_files_goal_ids(gate):
    import inspect

    src = inspect.getsource(gate)
    assert '"goal_ids": tx.get("goal_ids")' in src
    assert 'goal_ids=tx.get("goal_ids")' in inspect.getsource(gate._goalless_from_counted_tx)


# ── artifacts in the window attach to the named goal ────────────────────────────────────────────────────────────────────


def test_an_artifact_attaches_to_the_first_open_named_goal(world):
    carried = world.goal(transaction_id="tx-long-ago")
    world.goal(transaction_id="tx-earlier")  # newer: the project tier alone would pick it
    got = _resolve_goal_for_artifact(
        None, None, world.db, transaction_id="tx-now", project_id=world.pid, bound_goal_ids=[carried]
    )
    assert got == carried


def test_a_goal_created_in_the_window_still_comes_first(world):
    carried = world.goal(transaction_id="tx-long-ago")
    fresh = world.goal(transaction_id="tx-now")
    got = _resolve_goal_for_artifact(None, None, world.db, transaction_id="tx-now", bound_goal_ids=[carried])
    assert got == fresh


def test_a_named_goal_closed_mid_window_is_skipped(world):
    done = world.goal(status="completed", completed=1)
    carried = world.goal()
    got = _resolve_goal_for_artifact(None, None, world.db, transaction_id="tx-now", bound_goal_ids=[done, carried])
    assert got == carried


def test_without_a_bound_list_the_resolver_reads_the_transaction_file(world, monkeypatch):
    from empirica.cli.command_handlers import artifact_log_commands as alc

    carried = world.goal(transaction_id="tx-long-ago")
    monkeypatch.setattr(alc.R, "transaction_read", lambda *a, **k: {"transaction_id": "tx-now", "goal_ids": [carried]})
    assert _resolve_goal_for_artifact(None, None, world.db, transaction_id="tx-now") == carried
    # A file for another transaction is not this window's binding.
    monkeypatch.setattr(alc.R, "transaction_read", lambda *a, **k: {"transaction_id": "tx-old", "goal_ids": [carried]})
    assert _resolve_goal_for_artifact(None, None, world.db, transaction_id="tx-now") is None


def test_a_refused_goal_is_returned_as_a_refusal_and_a_good_one_is_resolved_in_place(world, monkeypatch):
    from empirica.data import session_database

    monkeypatch.setattr(session_database, "SessionDatabase", lambda *a, **k: world.db)
    monkeypatch.setattr(world.db, "close", lambda: None)
    gid = world.goal()
    parsed = {"goal_ids": [gid[:8]]}
    assert wp._preflight_bind_goal_ids(parsed, world.sid) is None
    assert parsed["goal_ids"] == [gid]
    refusal = wp._preflight_bind_goal_ids({"goal_ids": ["nosuchgoal"]}, world.sid)
    assert refusal == {
        "refuse": True,
        "message": "goal_ids: goal_id 'nosuchgoal' matches no goal (full id or unique prefix required)",
    }
    assert wp._preflight_bind_goal_ids({}, world.sid) is None


def test_a_wildcard_typed_as_a_prefix_matches_nothing_it_does_not_spell(world):
    world.goal()
    with pytest.raises(ValueError, match="matches no goal"):
        wp._preflight_resolve_goal_ids(["%"], world.sid, world.db)
    with pytest.raises(ValueError, match="matches no goal"):
        wp._preflight_resolve_goal_ids(["g_al"], world.sid, world.db)  # "_" is not a one-character wildcard


def test_an_ambiguous_prefix_is_refused(world):
    world.goal()
    world.goal()
    with pytest.raises(ValueError, match="ambiguous"):
        wp._preflight_resolve_goal_ids(["goal00"], world.sid, world.db)
