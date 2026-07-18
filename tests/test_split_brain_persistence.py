"""Regression tests for the split-brain project-persistence fix.

Franci/NLE verified diagnosis (2026-07-18): a session displayed the correct
project but PERSISTED its session row + artifacts to a stale project, because
session-create re-resolved the project_id from a context-file chain that read a
3-month-old global ~/.empirica/active_work.json — while the healer that would
have caught it silently missed the workspace.db row on a trajectory_path form
mismatch.

Four legs, tested here:
  1. session-init pins the session with an explicit --project-id resolved from
     the cwd-validated project_root (_resolve_canonical_project_id_for_root +
     _create_empirica_session argv).
  2. session_create's generic active_work.json read is gated on is_headless()
     (matches session_resolver's canonical invariant).
  3. the healers' trajectory lookup tolerates BOTH <root> and <root>/.empirica
     forms (_lookup_project_id_by_trajectory).
  4. a "healed" session bind is elevated to a loud split_brain_corrected signal.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import yaml

HOOK_PATH = (
    Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
)

UUID_A = "839e2223-c351-44f6-9ce6-6592fbcdd569"  # empirica-ai-consulting (correct)
UUID_B = "d5fea011-119b-47b3-8342-40e5fb7bd544"  # Q-Trading (the stale contaminator)


def _load_hook_module():
    """Import session-init.py despite the dash in the filename."""
    spec = importlib.util.spec_from_file_location("session_init_hook", HOOK_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    plugin_lib = HOOK_PATH.parent.parent / "lib"
    sys.path.insert(0, str(plugin_lib))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(plugin_lib))
    return mod


def _make_workspace_db(tmp_path: Path, trajectory: str, project_uuid: str) -> Path:
    ws_dir = tmp_path / ".empirica" / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    db = ws_dir / "workspace.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS global_projects (id TEXT PRIMARY KEY, name TEXT, trajectory_path TEXT)")
    conn.execute(
        "INSERT INTO global_projects (id, name, trajectory_path) VALUES (?, ?, ?)",
        (project_uuid, "test-proj", trajectory),
    )
    conn.commit()
    conn.close()
    return db


def _make_project_yaml(project_root: Path, project_id_value: str) -> Path:
    (project_root / ".empirica").mkdir(parents=True, exist_ok=True)
    yaml_path = project_root / ".empirica" / "project.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {"name": "Test", "ai_id": "test", "project_id": project_id_value, "version": "2.0"},
            sort_keys=False,
        )
    )
    return yaml_path


# ---------------------------------------------------------------------------
# Leg 3 — trajectory_path lookup tolerates both stored forms
# ---------------------------------------------------------------------------


def test_trajectory_lookup_matches_dotempirica_form(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_workspace_db(tmp_path, str(proj / ".empirica"), UUID_A)
    assert mod._lookup_project_id_by_trajectory(str(proj)) == UUID_A


def test_trajectory_lookup_matches_bare_root_form(tmp_path, monkeypatch):
    """THE FIX: a project registered with trajectory_path=<root> (no /.empirica)
    — the projects-discover form — must still resolve. The old exact-match on
    <root>/.empirica silently missed this, defeating the ghost-project_id heal."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_workspace_db(tmp_path, str(proj), UUID_A)  # bare root form
    assert mod._lookup_project_id_by_trajectory(str(proj)) == UUID_A


def test_trajectory_lookup_unregistered_returns_none(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_workspace_db(tmp_path, "/some/other/path", "other-uuid")
    assert mod._lookup_project_id_by_trajectory(str(proj)) is None


def test_trajectory_lookup_none_root_and_no_db(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert mod._lookup_project_id_by_trajectory(None) is None
    assert mod._lookup_project_id_by_trajectory(str(tmp_path / "proj")) is None  # no workspace.db


# ---------------------------------------------------------------------------
# Leg 1a — canonical project_id resolution for a validated root
# ---------------------------------------------------------------------------


def test_canonical_id_prefers_uuid_yaml(tmp_path, monkeypatch):
    """project.yaml UUID wins directly — no workspace.db needed."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "proj"
    _make_project_yaml(proj, UUID_A)
    # deliberately NO workspace.db — yaml UUID must suffice
    assert mod._resolve_canonical_project_id_for_root(str(proj)) == UUID_A


def test_canonical_id_falls_back_to_trajectory_on_slug_yaml(tmp_path, monkeypatch):
    """Slug-shaped yaml → fall through to workspace.db (bare-root form)."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "proj"
    _make_project_yaml(proj, "some-slug")
    _make_workspace_db(tmp_path, str(proj), UUID_A)
    assert mod._resolve_canonical_project_id_for_root(str(proj)) == UUID_A


def test_canonical_id_none_when_unresolvable(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()  # no yaml, no workspace.db
    assert mod._resolve_canonical_project_id_for_root(str(proj)) is None


# ---------------------------------------------------------------------------
# Leg 1b — session-create is invoked with an explicit --project-id
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, argv):
        self.argv = argv
        self.returncode = 0
        self.stdout = '{"session_id": "sess-123"}'
        self.stderr = ""


def test_create_session_passes_project_id(monkeypatch):
    mod = _load_hook_module()
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["argv"] = cmd
        return _FakeCompleted(cmd)

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    sid, err = mod._create_empirica_session("test-ai", dict(os.environ), project_id=UUID_A)
    assert err is None and sid == "sess-123"
    assert "--project-id" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--project-id") + 1] == UUID_A


def test_create_session_omits_project_id_when_none(monkeypatch):
    mod = _load_hook_module()
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["argv"] = cmd
        return _FakeCompleted(cmd)

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    mod._create_empirica_session("test-ai", dict(os.environ), project_id=None)
    assert "--project-id" not in captured["argv"]


# ---------------------------------------------------------------------------
# Leg 4 — a healed (wrong) binding is elevated to a loud split-brain signal
# ---------------------------------------------------------------------------


def _stub_bootstrap_chain(mod, monkeypatch, heal_status):
    monkeypatch.setattr(mod, "_create_empirica_session", lambda *a, **k: ("sess-123", None))
    monkeypatch.setattr(mod, "_heal_session_project_id_at_init", lambda *a, **k: heal_status)
    monkeypatch.setattr(mod, "_heal_project_yaml_project_id_at_init", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_heal_project_yaml_ai_id_at_init", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_heal_mesh_metadata_at_init", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_run_bootstrap", lambda *a, **k: ({}, None))
    monkeypatch.setattr(mod, "_cortex_remote_sync", lambda *a, **k: None)


def test_split_brain_flag_set_on_healed(tmp_path, monkeypatch, capsys):
    mod = _load_hook_module()
    _stub_bootstrap_chain(mod, monkeypatch, heal_status="healed")
    result = mod.create_session_and_bootstrap("test-ai", project_id=UUID_A)
    assert result.get("split_brain_corrected") is True
    assert "SPLIT-BRAIN" in capsys.readouterr().err


def test_no_split_brain_flag_when_ok(tmp_path, monkeypatch, capsys):
    mod = _load_hook_module()
    _stub_bootstrap_chain(mod, monkeypatch, heal_status="ok")
    result = mod.create_session_and_bootstrap("test-ai", project_id=UUID_A)
    assert "split_brain_corrected" not in result
    assert "SPLIT-BRAIN" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Leg 2 — session_create ignores generic active_work.json in interactive mode
# ---------------------------------------------------------------------------


def test_generic_active_work_ignored_when_interactive(tmp_path, monkeypatch):
    """Interactive (is_headless False) + stale generic active_work.json present,
    no instance/tty context → resolution must return None (NOT the stale id)."""
    from empirica.cli.command_handlers import session_create as sc

    empirica_dir = tmp_path / ".empirica"
    empirica_dir.mkdir(parents=True)
    (empirica_dir / "active_work.json").write_text(f'{{"project_id": "{UUID_B}"}}')

    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(sc.R, "instance_id", staticmethod(lambda: None))
    monkeypatch.setattr(sc.R, "tty_session", staticmethod(lambda warn_if_stale=False: None))
    monkeypatch.setattr(sc.R, "is_headless", staticmethod(lambda: False))

    assert sc._resolve_from_context_files() is None


def test_generic_active_work_used_when_headless(tmp_path, monkeypatch):
    """Headless (containers/CI) → the generic active_work.json IS the fallback."""
    from empirica.cli.command_handlers import session_create as sc

    empirica_dir = tmp_path / ".empirica"
    empirica_dir.mkdir(parents=True)
    (empirica_dir / "active_work.json").write_text(f'{{"project_id": "{UUID_A}"}}')

    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(sc.R, "instance_id", staticmethod(lambda: None))
    monkeypatch.setattr(sc.R, "tty_session", staticmethod(lambda warn_if_stale=False: None))
    monkeypatch.setattr(sc.R, "is_headless", staticmethod(lambda: True))

    assert sc._resolve_from_context_files() == UUID_A
