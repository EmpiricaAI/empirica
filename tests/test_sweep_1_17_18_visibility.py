"""Findings from the 1.17.18 pre-publish sweep, visibility and release area (goal c42e1294)."""

from __future__ import annotations

import contextlib

from empirica.core.canonical.empirica_git import graph_note_export as gx


def _recording_lock(events):
    @contextlib.contextmanager
    def lock(root):
        events.append("lock")
        yield
        events.append("unlock")

    return lock


def test_the_export_reads_the_rebuild_marker_inside_the_lock(monkeypatch, tmp_path):
    """Read before the lock, a rebuild could start while the export waited, and its untagged rows read as withdrawals."""
    events = []
    monkeypatch.setattr(gx, "_baseline_lock", _recording_lock(events))
    monkeypatch.setattr(gx, "_rows_of_unfinished_rebuild", lambda conn, root: events.append("unknown") or set())
    monkeypatch.setattr(gx, "_export_visibility_notes", lambda *a, **k: {})
    gx.export_visibility_notes(None, str(tmp_path))
    assert events == ["lock", "unknown", "unlock"]


def test_the_rebuild_marker_is_written_inside_the_lock(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(gx, "_baseline_lock", _recording_lock(events))
    real_write = type(tmp_path).write_text

    def write(self, *a, **k):
        if self.name == gx.REBUILD_MARKER:
            events.append("marker")
        return real_write(self, *a, **k)

    monkeypatch.setattr(type(tmp_path), "write_text", write)
    gx.rebuild_started(str(tmp_path), {"a"})
    assert events == ["lock", "marker", "unlock"]


def test_a_profile_import_with_notes_off_leaves_visibility_alone(monkeypatch, tmp_path):
    """With the opt-in unset, import applied peers' notes: a row withdrawn here could come back shared and reach Cortex."""
    from empirica.core.canonical.empirica_git import profile_import

    monkeypatch.delenv("EMPIRICA_VISIBILITY_NOTES", raising=False)
    called = []
    monkeypatch.setattr(gx, "prepare_visibility_rebuild", lambda *a, **k: called.append("prepare") or set())
    monkeypatch.setattr(gx, "apply_visibility_notes", lambda *a, **k: called.append("apply") or 0)
    importer = profile_import.ProfileImporter(workspace_root=str(tmp_path))
    for name in ("import_findings", "import_unknowns", "import_dead_ends", "import_mistakes", "import_decisions",
                 "import_assumptions", "import_goals", "import_sources"):  # fmt: skip
        monkeypatch.setattr(importer, name, lambda db: 0)
    importer._stats = {}
    from types import SimpleNamespace

    importer.import_all(SimpleNamespace(conn=None))
    assert called == []
