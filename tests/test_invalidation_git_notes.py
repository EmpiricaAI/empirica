"""Invalidating a dead end or a mistake must survive a rebuild from git notes.

Reported by empirica.philipp.empirica-mesh-support (prop_bxjntibm, defect 4), and named a known gap in
``graph_commands._persist_filter_resolution_to_notes`` since migration 060: invalidation set ``is_invalidated`` in SQLite and
nothing else, so a from-notes rebuild re-created the row VALID. A practice then steers away from an approach it had already
found wrong, or toward a constraint it had retired. On this practice 676 dead ends were invalidated in SQLite alone.

Notes are the canonical log, so a note that disagrees with SQLite is a pending revert. The fix mirrors resolution: stamp the
ACTIVE note (it stays on its original commit), re-apply the stamp when a rebuild re-creates the row, and let
``doctor --reconcile-notes`` repair the backlog.

Every assertion is paired with its opposite: an artifact that was NOT invalidated must stay valid at every stage.
"""

from __future__ import annotations

import ast
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from empirica.core.canonical.empirica_git.dead_end_store import GitDeadEndStore
from empirica.core.canonical.empirica_git.mistake_store import GitMistakeStore
from empirica.data.session_database import SessionDatabase

SRC = Path(__file__).resolve().parents[1] / "empirica"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    p = tmp_path / "r"
    p.mkdir()

    def run(*a):
        return subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (p / "f.txt").write_text("x")
    run("add", ".")
    run("commit", "-qm", "init")
    monkeypatch.chdir(p)
    return p


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    d.conn.execute("PRAGMA foreign_keys=OFF")
    yield d
    d.close()


def _new_dead_end(repo, approach="tried X"):
    de = str(uuid.uuid4())
    assert GitDeadEndStore(workspace_root=str(repo)).store_dead_end(
        de, "p1", "s1", "ai", approach, "it does not work because Y"
    )
    return de


def _new_mistake(repo, text="did Z"):
    mid = str(uuid.uuid4())
    assert GitMistakeStore(workspace_root=str(repo)).store_mistake(
        mid, "p1", "s1", "ai", text, "wrong because W", "do V"
    )
    return mid


def _note(repo, namespace, aid):
    ref = f"empirica/{namespace}/{aid}"
    listing = subprocess.run(["git", "notes", f"--ref={ref}", "list"], cwd=repo, capture_output=True, text=True).stdout
    lines = [ln for ln in listing.splitlines() if ln.strip()]
    blob = lines[0].split()[0]
    body = subprocess.run(["git", "cat-file", "-p", blob], cwd=repo, capture_output=True, text=True).stdout
    return json.loads(body), len(lines)


# --- 1. the stamp ------------------------------------------------------------------------------------------------------


def test_invalidating_a_dead_end_stamps_its_note_and_keeps_the_text(repo):
    from empirica.cli.command_handlers.graph_commands import _stamp_invalidation

    de, other = _new_dead_end(repo, "tried X"), _new_dead_end(repo, "tried A")
    assert _stamp_invalidation("dead_end", de, "wrong: X works with flag Q", 1234.5, "david") is True

    note, _ = _note(repo, "dead_ends", de)
    assert note["is_invalidated"] is True
    assert note["invalidation_reason"] == "wrong: X works with flag Q"
    assert note["approach"] == "tried X" and note["why_failed"] == "it does not work because Y"
    untouched, _ = _note(repo, "dead_ends", other)
    assert not untouched.get("is_invalidated"), "control: an artifact nobody invalidated stays valid"


def test_invalidating_a_mistake_stamps_its_note(repo):
    from empirica.cli.command_handlers.graph_commands import _stamp_invalidation

    mid, other = _new_mistake(repo), _new_mistake(repo, "did Q")
    assert _stamp_invalidation("mistake", mid, "prevention no longer applies", 99.0, None) is True
    note, _ = _note(repo, "mistakes", mid)
    assert note["is_invalidated"] is True and note["mistake"] == "did Z" and note["prevention"] == "do V"
    assert not _note(repo, "mistakes", other)[0].get("is_invalidated")


def test_the_stamp_stays_on_the_original_commit_instead_of_forking_the_note(repo):
    """A re-store at the new HEAD would leave two notes under one ref, and a reader takes whichever sorts first."""
    from empirica.cli.command_handlers.graph_commands import _stamp_invalidation

    de = _new_dead_end(repo)
    (repo / "g.txt").write_text("y")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "second"], cwd=repo, check=True, capture_output=True)

    assert _stamp_invalidation("dead_end", de, "wrong", 1.0, None) is True
    note, entries = _note(repo, "dead_ends", de)
    assert entries == 1, "exactly one note entry under the ref"
    assert note["is_invalidated"] is True


def test_a_missing_note_is_reported_not_raised(repo):
    from empirica.cli.command_handlers.graph_commands import _stamp_invalidation

    assert _stamp_invalidation("dead_end", str(uuid.uuid4()), "wrong", 1.0, None) is False


def test_the_bulk_filter_path_stamps_notes_for_dead_ends_and_mistakes(repo):
    from empirica.cli.command_handlers.graph_commands import _persist_filter_resolution_to_notes

    de, mid, keep = _new_dead_end(repo), _new_mistake(repo), _new_dead_end(repo, "tried B")
    _persist_filter_resolution_to_notes("dead_end", [de], "gardened: obsolete")
    _persist_filter_resolution_to_notes("mistake", [mid], "gardened: obsolete")
    assert _note(repo, "dead_ends", de)[0]["is_invalidated"] is True
    assert _note(repo, "mistakes", mid)[0]["is_invalidated"] is True
    assert not _note(repo, "dead_ends", keep)[0].get("is_invalidated")


# --- 2. the rebuild re-applies it --------------------------------------------------------------------------------------


def test_a_rebuild_reapplies_invalidation_to_the_recreated_row(db):
    from empirica.cli.command_handlers.sync_commands import _rebuild_apply_resolution

    pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    de = db.log_dead_end(project_id=pid, session_id=sid, approach="a", why_failed="b")
    mid = db.log_mistake(session_id=sid, project_id=pid, mistake="m", why_wrong="w", prevention="p")
    keep = db.log_dead_end(project_id=pid, session_id=sid, approach="c", why_failed="d")

    _rebuild_apply_resolution(db, "dead_ends", de, {"is_invalidated": True, "invalidation_reason": "wrong"})
    _rebuild_apply_resolution(db, "mistakes", mid, {"is_invalidated": True, "invalidation_reason": "retired"})
    _rebuild_apply_resolution(db, "dead_ends", keep, {"approach": "c"})

    def inv(table, i):
        return db.conn.execute(f"SELECT is_invalidated, invalidation_reason FROM {table} WHERE id = ?", (i,)).fetchone()

    assert tuple(inv("project_dead_ends", de)) == (1, "wrong")
    assert tuple(inv("mistakes_made", mid)) == (1, "retired")
    assert not inv("project_dead_ends", keep)[0], "control: a note without the stamp rebuilds valid"


def test_the_whole_round_trip_survives_a_rebuild_from_notes(repo, db):
    """Note -> stamp -> discover -> rebuild into a fresh database: the invalidated one stays invalidated, the other valid."""
    from empirica.cli.command_handlers.graph_commands import _stamp_invalidation
    from empirica.cli.command_handlers.sync_commands import _rebuild_insert_breadcrumbs

    gone = _new_dead_end(repo, "tried X")
    _new_dead_end(repo, "tried A")  # the control: never invalidated
    _stamp_invalidation("dead_end", gone, "wrong", 5.0, None)

    discovered = GitDeadEndStore(workspace_root=str(repo)).discover_dead_ends()
    assert len(discovered) == 2, "positive control: both notes were discovered"
    rebuilt = {"findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0}
    _rebuild_insert_breadcrumbs(db, [], [], discovered, [], set(), rebuilt)
    assert rebuilt["dead_ends"] == 2

    rows = {
        r[0]: r[1]
        for r in db.conn.execute("SELECT approach, COALESCE(is_invalidated, 0) FROM project_dead_ends").fetchall()
    }
    assert rows == {"tried X": 1, "tried A": 0}


# --- 3. the backlog is repairable --------------------------------------------------------------------------------------


def test_reconcile_finds_and_stamps_notes_for_rows_invalidated_before_the_fix(repo, db):
    from empirica.core.canonical.empirica_git.note_reconcile import apply, plan

    pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    old = db.log_dead_end(project_id=pid, session_id=sid, approach="old", why_failed="w")
    fine = db.log_dead_end(project_id=pid, session_id=sid, approach="fine", why_failed="w")
    for i in (old, fine):
        GitDeadEndStore(workspace_root=str(repo)).store_dead_end(i, pid, sid, "ai", "text", "why")
    db.conn.execute(
        "UPDATE project_dead_ends SET is_invalidated = 1, invalidation_reason = 'wrong', invalidated_at = 7.0 WHERE id = ?",
        (old,),
    )
    db.conn.commit()

    the_plan = plan(db, str(repo))
    assert the_plan["types"]["dead_ends"]["unstamped"] == [old], "only the invalidated one, not the valid control"

    receipt = apply(db, str(repo), the_plan)
    assert receipt["stamped"] >= 1 and not receipt["failed"]
    note, _ = _note(repo, "dead_ends", old)
    assert note["is_invalidated"] is True and note["invalidation_reason"] == "wrong"
    assert not _note(repo, "dead_ends", fine)[0].get("is_invalidated")
    assert plan(db, str(repo))["types"]["dead_ends"]["unstamped"] == [], "idempotent: a second plan finds nothing"


def test_reconcile_covers_mistakes_as_well(repo, db):
    from empirica.core.canonical.empirica_git.note_reconcile import plan

    pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    mid = db.log_mistake(session_id=sid, project_id=pid, mistake="m", why_wrong="w", prevention="p")
    GitMistakeStore(workspace_root=str(repo)).store_mistake(mid, pid, sid, "ai", "m", "w", "p")
    db.conn.execute("UPDATE mistakes_made SET is_invalidated = 1, invalidation_reason = 'x' WHERE id = ?", (mid,))
    db.conn.commit()
    assert plan(db, str(repo))["types"]["mistakes"]["unstamped"] == [mid]


# --- 4. no writer can drift away from the stamp ------------------------------------------------------------------------


def test_every_function_that_invalidates_in_sqlite_also_stamps_the_note():
    """The gap existed because three writers were each hand-written. This pins the invariant instead of the count."""
    tree = ast.parse((SRC / "cli" / "command_handlers" / "graph_commands.py").read_text())
    writers, unstamped = [], []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        source = ast.get_source_segment((SRC / "cli" / "command_handlers" / "graph_commands.py").read_text(), fn) or ""
        if "SET is_invalidated = 1" in source:
            writers.append(fn.name)
            if "_stamp_invalidation(" not in source and "_persist_filter_resolution_to_notes(" not in source:
                unstamped.append(fn.name)
    assert len(writers) >= 2, "positive control: the scan must find the known writers"
    assert unstamped == [], f"{unstamped} invalidate in SQLite without stamping the note"
