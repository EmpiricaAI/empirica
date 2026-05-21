"""Tests for _heal_project_yaml_project_id_at_init in session-init hook.

Closes the slug-shaped project_id gap surfaced by check_project_drift
on aiworkhorse. Legacy projects (empirica, empirica-outreach,
empirica-platform) were init'd before project-init started writing
UUIDs into .empirica/project.yaml. This hook step heals the yaml
at session-init boundary by looking up the canonical UUID via
workspace.db global_projects.trajectory_path.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import yaml

HOOK_PATH = (
    Path(__file__).parent.parent
    / "empirica" / "plugins" / "claude-code-integration"
    / "hooks" / "session-init.py"
)


def _load_hook_module():
    """Import the session-init.py hook as a module despite the dash in the name."""
    spec = importlib.util.spec_from_file_location("session_init_hook", HOOK_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # session-init.py sys.path.inserts plugin lib for project_resolver — mirror it
    plugin_lib = HOOK_PATH.parent.parent / "lib"
    sys.path.insert(0, str(plugin_lib))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(plugin_lib))
    return mod


def _make_workspace_db(tmp_path: Path, trajectory: str, project_uuid: str) -> Path:
    """Create a minimal workspace.db with one global_projects row."""
    ws_dir = tmp_path / ".empirica" / "workspace"
    ws_dir.mkdir(parents=True)
    db = ws_dir / "workspace.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE global_projects ("
        "id TEXT PRIMARY KEY, name TEXT, trajectory_path TEXT)"
    )
    conn.execute(
        "INSERT INTO global_projects (id, name, trajectory_path) VALUES (?, ?, ?)",
        (project_uuid, "test-proj", trajectory),
    )
    conn.commit()
    conn.close()
    return db


def _make_project_yaml(project_root: Path, project_id_value: str) -> Path:
    """Create a .empirica/project.yaml with the given project_id."""
    (project_root / ".empirica").mkdir(parents=True)
    yaml_path = project_root / ".empirica" / "project.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "name": "Test", "ai_id": "test", "project_id": project_id_value,
        "version": "2.0",
    }, sort_keys=False))
    return yaml_path


def test_no_op_when_yaml_has_uuid(tmp_path, monkeypatch, capsys):
    """UUID-shape project_id should not trigger any healing."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    uuid = "748a81a2-ac14-45b8-a185-994997b76828"
    proj = tmp_path / "myproj"
    _make_project_yaml(proj, uuid)
    _make_workspace_db(tmp_path, str(proj / ".empirica"), uuid)

    mod._heal_project_yaml_project_id_at_init(str(proj))

    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == uuid  # unchanged
    err = capsys.readouterr().err
    assert "healed" not in err


def test_no_op_when_no_project_yaml(tmp_path, capsys):
    """Missing project.yaml is silent — non-fatal."""
    mod = _load_hook_module()
    mod._heal_project_yaml_project_id_at_init(str(tmp_path / "no-such-project"))
    err = capsys.readouterr().err
    # Either silent or skipped, but never errors
    assert "Traceback" not in err


def test_no_op_when_not_in_workspace_db(tmp_path, monkeypatch, capsys):
    """Slug-shape yaml but project not in workspace.db → leave alone."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "myproj"
    _make_project_yaml(proj, "myproj")
    # workspace.db exists but doesn't contain this trajectory
    _make_workspace_db(tmp_path, "/some/other/path", "other-uuid")

    mod._heal_project_yaml_project_id_at_init(str(proj))

    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == "myproj"  # unchanged
    err = capsys.readouterr().err
    assert "healed" not in err


def test_no_op_when_workspace_db_missing(tmp_path, monkeypatch, capsys):
    """Slug-shape yaml but no workspace.db → leave alone, non-fatal."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj = tmp_path / "myproj"
    _make_project_yaml(proj, "myproj")
    # No workspace.db created

    mod._heal_project_yaml_project_id_at_init(str(proj))

    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == "myproj"  # unchanged


def test_rewrites_yaml_when_slug_shape(tmp_path, monkeypatch, capsys):
    """Slug-shape yaml + project in workspace.db → migrate to UUID."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    uuid = "748a81a2-ac14-45b8-a185-994997b76828"
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, "empirica")
    _make_workspace_db(tmp_path, str(proj / ".empirica"), uuid)

    mod._heal_project_yaml_project_id_at_init(str(proj))

    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == uuid
    err = capsys.readouterr().err
    assert "healed" in err
    assert "748a81a2" in err


def test_preserves_other_yaml_fields(tmp_path, monkeypatch):
    """Migration only swaps project_id; other fields untouched."""
    mod = _load_hook_module()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    uuid = "b8aab845-6e7f-49c1-b50e-2ecf5ebbbd0b"
    proj = tmp_path / "myproj"
    (proj / ".empirica").mkdir(parents=True)
    yaml_path = proj / ".empirica" / "project.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "version": "2.0",
        "name": "Custom Name",
        "ai_id": "myai",
        "project_id": "myproj",
        "description": "preserve me",
        "tags": ["a", "b"],
        "evidence_profile": "code",
    }, sort_keys=False))
    _make_workspace_db(tmp_path, str(proj / ".empirica"), uuid)

    mod._heal_project_yaml_project_id_at_init(str(proj))

    cfg = yaml.safe_load(yaml_path.read_text())
    assert cfg["project_id"] == uuid
    assert cfg["name"] == "Custom Name"
    assert cfg["ai_id"] == "myai"
    assert cfg["description"] == "preserve me"
    assert cfg["tags"] == ["a", "b"]
    assert cfg["evidence_profile"] == "code"
    assert cfg["version"] == "2.0"


def test_no_op_with_empty_project_root(capsys):
    """Empty/None project_root is silent — non-fatal."""
    mod = _load_hook_module()
    mod._heal_project_yaml_project_id_at_init(None)
    mod._heal_project_yaml_project_id_at_init("")
    err = capsys.readouterr().err
    assert "Traceback" not in err
