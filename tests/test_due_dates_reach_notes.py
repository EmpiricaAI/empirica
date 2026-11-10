"""Writers put the due date in the note, so a rebuild can restore it (1.15.0 sweep, D3b; goal 8a26a2c3).

Readers first (test_due_dates_survive_rebuild.py); these are the writers: log-artifacts and the single verbs write
`due_at` into unknown and assumption notes and goal_data, taken from the row, and resolving an unknown keeps every key its
note held (edges, created_at, due_at). Built in a throwaway git repo: the stores resolve the repository from the working
directory.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import graph_commands as gc
from empirica.cli.command_handlers.sync_commands import _rebuild_insert_breadcrumbs
from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore
from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def practice(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _log(practice, nodes):
    out = gc.log_artifacts_graph(
        {"nodes": nodes, "edges": []}, session_id="sess-1", project_id="proj-1", project_path=str(practice)
    )
    assert out["ok"] and not out["errors"], out
    return out["created"]


def test_log_artifacts_writes_the_due_date_into_unknown_and_assumption_notes(practice):
    ids = _log(
        practice,
        [
            {"ref": "u", "type": "unknown", "data": {"unknown": "when?", "due": "2026-12-01"}},
            {"ref": "a", "type": "assumption", "data": {"assumption": "y", "confidence": 0.6, "due": "2026-12-02"}},
            {"ref": "n", "type": "unknown", "data": {"unknown": "undated"}},
        ],
    )
    assert GitUnknownStore().load_unknown(ids["u"])["due_at"] == "2026-12-01"
    assert GitAssumptionStore().load_assumption(ids["a"])["due_at"] == "2026-12-02"
    assert "due_at" not in GitUnknownStore().load_unknown(ids["n"]), "an undated note carries no key"


def test_an_unknowns_due_date_round_trips_through_its_note_and_a_rebuild(practice, tmp_path):
    ids = _log(practice, [{"ref": "u", "type": "unknown", "data": {"unknown": "when?", "due": "2026-12-01"}}])
    notes = GitUnknownStore().discover_unknowns()
    assert [n["unknown_id"] for n in notes] == [ids["u"]]
    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    fresh.conn.execute("PRAGMA foreign_keys=OFF")
    try:
        _rebuild_insert_breadcrumbs(
            fresh, [], notes, [], [], set(), {"findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0}
        )
        row = fresh.conn.execute("SELECT due_at FROM project_unknowns WHERE id = ?", (ids["u"],)).fetchone()
    finally:
        fresh.close()
    assert row is not None and row[0] == "2026-12-01"


def test_resolving_an_unknown_keeps_what_its_note_held(practice):
    store = GitUnknownStore()
    assert store.store_unknown(
        unknown_id="u-1",
        project_id="p",
        session_id="s",
        ai_id="a",
        unknown="why?",
        created_at="2026-03-04T05:06:07+00:00",
        due_at="2026-12-01",
        extra={"edges": [{"to": "f-1", "relation": "raised_by"}]},
    )
    assert store.resolve_unknown("u-1", "answered")
    note = store.load_unknown("u-1")
    assert note["resolved"] is True and note["resolved_by"] == "answered"
    assert note["created_at"] == "2026-03-04T05:06:07+00:00", "not re-dated to the resolution"
    assert note["due_at"] == "2026-12-01"
    assert note["edges"] == [{"to": "f-1", "relation": "raised_by"}]


def test_goals_create_writes_the_rows_due_date_into_goal_data(monkeypatch):
    from empirica.cli.command_handlers import goal_commands as goals

    seen = {}

    class _Store:
        def store_goal(self, *, goal_id, session_id, ai_id, goal_data):
            seen.update(goal_data)
            return True

    monkeypatch.setattr("empirica.core.canonical.empirica_git.GitGoalStore", _Store)
    monkeypatch.setattr(goals, "_link_goal_to_beads", lambda *a, **k: None, raising=False)
    goal = SimpleNamespace(id="g-1", objective="ship x")
    scope = SimpleNamespace(to_dict=lambda: {})
    try:
        goals._goal_post_create_integrations(
            goal, "s", "ship x", scope, [], 0.1, 0.1, 0.1, None, None, None,
            SimpleNamespace(ai_id="a", use_beads=False), {}, "json", {}, due_at="2026-12-03",
        )  # fmt: skip
    except Exception:
        pass  # later integrations (Qdrant) are not this test's concern; the note write comes first
    assert seen.get("due_at") == "2026-12-03"
