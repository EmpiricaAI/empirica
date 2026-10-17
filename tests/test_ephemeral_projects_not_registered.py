"""project-init must not register throwaway directories in the user's real workspace registry.

Every project-init wrote a row to `global_projects` and a mirror to `entity_registry`, including `/tmp/.tmpXXXXXX` directories created
by tests and agents: 32 dead rows in David's real registry, 9 on one day (empirica-workspace, prop_w3yho2t7). The registry is shared,
so the rows reach every tool that lists projects. A REAL home registering a temp path is the pollution; a sandboxed home (HOME itself
in a temp directory, which is what tests use) keeps registering whatever it is told, so existing tests and fixtures are unaffected.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from empirica.cli.command_handlers import workspace_init as wi


@pytest.fixture
def zones(tmp_path, monkeypatch):
    """A logical 'temp zone' and a logical 'real home', both under pytest's tmp_path so nothing touches the real registry."""
    tmpzone = tmp_path / "tmpzone"
    realhome = tmp_path / "realhome"
    tmpzone.mkdir()
    realhome.mkdir()
    monkeypatch.setattr(wi, "_temp_roots", lambda: [tmpzone])
    monkeypatch.setenv("HOME", str(realhome))
    monkeypatch.delenv("EMPIRICA_REGISTER_EPHEMERAL", raising=False)
    return tmpzone, realhome


def _rows(home: Path, table: str = "global_projects") -> list[tuple]:
    db = home / ".empirica" / "workspace" / "workspace.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _register(trajectory: Path) -> bool:
    return wi._register_in_workspace_db(project_id=str(uuid.uuid4()), name="p", trajectory_path=str(trajectory))


class TestPredicate:
    def test_a_temp_path_under_a_real_home_is_ephemeral(self, zones):
        tmpzone, realhome = zones
        assert wi._is_ephemeral_registration(str(tmpzone / ".tmpab12cd" / ".empirica"), realhome) is True

    def test_a_normal_project_is_not(self, zones):
        _, realhome = zones
        assert wi._is_ephemeral_registration(str(realhome / "work" / "proj" / ".empirica"), realhome) is False

    def test_a_sandboxed_home_registers_everything(self, zones):
        tmpzone, _ = zones
        assert wi._is_ephemeral_registration(str(tmpzone / "p" / ".empirica"), tmpzone / "home") is False

    def test_the_override_environment_variable_disables_the_guard(self, zones, monkeypatch):
        tmpzone, realhome = zones
        monkeypatch.setenv("EMPIRICA_REGISTER_EPHEMERAL", "1")
        assert wi._is_ephemeral_registration(str(tmpzone / "p" / ".empirica"), realhome) is False

    @pytest.mark.parametrize(
        "path",
        ["/var/folders/zz/abc/T/x/.empirica", "/private/tmp/x/.empirica", "/private/var/folders/q/T/x/.empirica"],
    )
    def test_the_macos_temp_locations_are_recognised_by_default(self, path, monkeypatch):
        monkeypatch.undo()
        assert wi._is_ephemeral_registration(path, Path("/Users/someone")) is True

    def test_the_default_roots_include_the_systems_temp_directory(self):
        import tempfile

        assert Path(tempfile.gettempdir()).resolve() in [r.resolve() for r in wi._temp_roots()]


class TestRegistration:
    def test_a_temp_project_is_not_written_to_the_real_registry(self, zones):
        tmpzone, realhome = zones
        assert _register(tmpzone / ".tmpab12cd" / ".empirica") is False
        assert _rows(realhome) == [] and _rows(realhome, "entity_registry") == []

    def test_a_normal_project_is_registered_in_both_tables(self, zones):
        _, realhome = zones
        assert _register(realhome / "work" / "proj" / ".empirica") is True
        assert len(_rows(realhome)) == 1 and len(_rows(realhome, "entity_registry")) >= 1

    def test_the_override_registers_a_temp_project(self, zones, monkeypatch):
        tmpzone, realhome = zones
        monkeypatch.setenv("EMPIRICA_REGISTER_EPHEMERAL", "1")
        assert _register(tmpzone / "p" / ".empirica") is True
        assert len(_rows(realhome)) == 1

    def test_a_sandboxed_home_still_registers(self, zones, monkeypatch):
        tmpzone, _ = zones
        sandboxed = tmpzone / "sandbox-home"
        sandboxed.mkdir()
        monkeypatch.setenv("HOME", str(sandboxed))
        assert _register(tmpzone / "p" / ".empirica") is True
        assert len(_rows(sandboxed)) == 1
