"""`rebuild --qdrant-only` — the safe Qdrant resync path.

The default `rebuild` (and `rebuild --qdrant`) reconstructs SQLite from git notes
FIRST (_rebuild_from_notes), which reverts any direct-SQL/bulk change not yet
persisted to notes — e.g. an epistemic-garden bulk resolve. `--qdrant-only`
skips that notes-import step and only re-embeds Qdrant from the CURRENT SQLite,
so it never touches (and never reverts) SQLite. These tests pin that contract:
notes-import is skipped, Qdrant re-embed runs, and the exit code tracks success.
"""

from __future__ import annotations

import types

import empirica.cli.command_handlers.sync_commands as sc


def _args(**kw):
    base = {"output": "json", "from_notes": True, "qdrant": False, "qdrant_only": False, "verbose": False}
    base.update(kw)
    return types.SimpleNamespace(**base)


# The handler imports rebuild_qdrant_from_db locally from vector_store at call time,
# so patch it at the source module.
_QDRANT_TARGET = "empirica.core.qdrant.vector_store.rebuild_qdrant_from_db"


def test_qdrant_only_skips_notes_import(monkeypatch):
    called = {"notes": 0, "qdrant": 0}

    def fake_notes():
        called["notes"] += 1
        return {"findings": 0}

    def fake_qdrant():
        called["qdrant"] += 1
        return {"ok": True, "total_projects": 2, "successful": 2}

    monkeypatch.setattr(sc, "_rebuild_from_notes", fake_notes)
    monkeypatch.setattr(_QDRANT_TARGET, fake_qdrant)

    rc = sc.handle_rebuild_command(_args(qdrant_only=True))

    assert rc == 0
    assert called["notes"] == 0, "notes-import MUST be skipped under --qdrant-only (else it reverts direct-SQL)"
    assert called["qdrant"] == 1, "Qdrant re-embed must run"


def test_default_rebuild_still_imports_notes(monkeypatch):
    """Regression guard: the default path (no --qdrant-only) must STILL import notes,
    so --qdrant-only is a genuine opt-in and doesn't change existing behavior."""
    called = {"notes": 0}

    def fake_notes():
        called["notes"] += 1
        return {"findings": 0}

    monkeypatch.setattr(sc, "_rebuild_from_notes", fake_notes)
    sc.handle_rebuild_command(_args(qdrant_only=False, qdrant=False))
    assert called["notes"] == 1


def test_qdrant_only_propagates_failure(monkeypatch):
    def fake_notes():  # must never be called
        raise AssertionError("notes-import ran under --qdrant-only")

    monkeypatch.setattr(sc, "_rebuild_from_notes", fake_notes)
    monkeypatch.setattr(_QDRANT_TARGET, lambda: {"ok": False, "error": "Qdrant not available"})

    rc = sc.handle_rebuild_command(_args(qdrant_only=True))
    assert rc == 1
