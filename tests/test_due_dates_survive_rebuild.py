"""A due date survives the git notes (1.15.0 sweep, D3; goal 8a26a2c3).

`due_at` lived only in SQLite. `sync-pull --rebuild` and profile import recreate goals and unknowns (and, on import,
assumptions) from their notes, so either one silently dropped every date. Readers first: each takes `due_at` from a note
that carries it and leaves the column empty for an older note that does not.

Profile import also never imported a goal at all: its INSERT left out `goal_data`, which is NOT NULL, and INSERT OR IGNORE
turned the failure into a silent skip.
"""

from __future__ import annotations

import uuid

import pytest

from empirica.cli.command_handlers.sync_commands import _rebuild_ensure_goals, _rebuild_insert_breadcrumbs
from empirica.core.canonical.empirica_git.profile_import import ProfileImporter
from empirica.data.session_database import SessionDatabase

PID, SID = str(uuid.uuid4()), str(uuid.uuid4())
WHEN = "2026-03-04T05:06:07+00:00"


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    d.conn.execute("PRAGMA foreign_keys=OFF")
    yield d
    d.close()


def _due(db, table, id_):
    row = db.conn.execute(f"SELECT due_at FROM {table} WHERE id = ?", (id_,)).fetchone()
    assert row is not None, f"no {table} row {id_}"
    return row[0]


# ── rebuild ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_rebuild_restores_a_goals_due_date(db, monkeypatch):
    dated, undated = str(uuid.uuid4()), str(uuid.uuid4())
    notes = [
        {"goal_id": g, "session_id": SID, "created_at": WHEN, "goal_data": {"objective": "x", "status": "planned", **d}}
        for g, d in ((dated, {"due_at": "2026-12-01"}), (undated, {}))
    ]

    class _Store:
        def discover_goals(self, *a, **k):
            return notes

    monkeypatch.setattr("empirica.core.canonical.empirica_git.goal_store.GitGoalStore", _Store)
    _rebuild_ensure_goals(db, 0.0, {"goals": 0})
    assert _due(db, "goals", dated) == "2026-12-01"
    assert _due(db, "goals", undated) is None


def test_rebuild_restores_an_unknowns_due_date(db):
    dated, undated = str(uuid.uuid4()), str(uuid.uuid4())
    unknowns = [
        {"unknown_id": u, "project_id": PID, "session_id": SID, "created_at": WHEN, "unknown": text, **extra}
        for u, text, extra in ((dated, "when?", {"due_at": "2026-12-01"}), (undated, "why?", {}))
    ]
    _rebuild_insert_breadcrumbs(
        db, [], unknowns, [], [], set(), {"findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0}
    )
    assert _due(db, "project_unknowns", dated) == "2026-12-01"
    assert _due(db, "project_unknowns", undated) is None


# ── profile import ───────────────────────────────────────────────────────────────────────────────────────────────────


def _importer(monkeypatch, tmp_path, notes: dict[str, dict]):
    imp = ProfileImporter(workspace_root=str(tmp_path))
    monkeypatch.setattr(
        imp,
        "_discover_refs",
        lambda prefix: [k.split("/")[-1] for k in notes if k.startswith(prefix.replace("refs/notes/", ""))],
    )
    monkeypatch.setattr(imp, "_load_note", lambda ref: notes.get(ref))
    return imp


def test_profile_import_carries_due_dates_and_imports_goals_at_all(db, monkeypatch, tmp_path):
    u, a, g = (str(uuid.uuid4()) for _ in range(3))
    notes = {
        f"empirica/unknowns/{u}": {"unknown_id": u, "project_id": PID, "session_id": SID, "created_at": WHEN,
                                   "unknown": "when?", "due_at": "2026-12-01"},
        f"empirica/assumptions/{a}": {"assumption_id": a, "project_id": PID, "session_id": SID, "created_at": WHEN,
                                      "assumption": "y holds", "confidence": 0.6, "due_at": "2026-12-02"},
        f"empirica/goals/{g}": {"goal_id": g, "session_id": SID, "created_at": WHEN,
                                "goal_data": {"objective": "ship x", "status": "planned", "due_at": "2026-12-03"}},
    }  # fmt: skip
    imp = _importer(monkeypatch, tmp_path, notes)
    imp.import_unknowns(db)
    imp.import_assumptions(db)
    assert imp.import_goals(db) == 1, "a goal note is imported, not skipped on goal_data NOT NULL"
    assert _due(db, "project_unknowns", u) == "2026-12-01"
    assert _due(db, "assumptions", a) == "2026-12-02"
    assert _due(db, "goals", g) == "2026-12-03"
