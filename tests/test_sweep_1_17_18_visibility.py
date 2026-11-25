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


def test_a_rebuild_with_notes_off_leaves_visibility_alone(monkeypatch):
    """Second pass: sync-pull --rebuild applied peers' notes with the opt-in unset, as profile import did."""
    from types import SimpleNamespace

    from empirica.cli.command_handlers import sync_commands

    monkeypatch.delenv("EMPIRICA_VISIBILITY_NOTES", raising=False)
    monkeypatch.setattr(gx, "apply_visibility_notes", lambda *a, **k: pytest_fail())
    rebuilt = {}
    sync_commands._rebuild_visibility(SimpleNamespace(adapter=SimpleNamespace(conn=None)), rebuilt, root="/nonexistent")
    assert rebuilt.get("visibility_restored", 0) == 0


def pytest_fail():
    raise AssertionError("visibility notes applied with the opt-in unset")


def test_a_rebuild_with_notes_off_prepares_nothing_and_clears_an_old_marker(monkeypatch, tmp_path):
    """Third pass: the prepare gate had no test, and a marker from an earlier crashed rebuild was never cleared."""
    from empirica.cli.command_handlers import sync_commands

    monkeypatch.delenv("EMPIRICA_VISIBILITY_NOTES", raising=False)
    monkeypatch.setattr(gx, "prepare_visibility_rebuild", lambda *a, **k: pytest_fail())
    (tmp_path / ".empirica").mkdir()
    marker = tmp_path / ".empirica" / gx.REBUILD_MARKER
    marker.write_text("{}")
    assert sync_commands._prepare_visibility(None, str(tmp_path)) is None
    assert not marker.exists()


def test_a_rebuild_with_notes_on_prepares(monkeypatch, tmp_path):
    from empirica.cli.command_handlers import sync_commands

    monkeypatch.setenv("EMPIRICA_VISIBILITY_NOTES", "1")
    monkeypatch.setattr(gx, "prepare_visibility_rebuild", lambda conn, root: {"a"})
    assert sync_commands._prepare_visibility(None, str(tmp_path)) == {"a"}
