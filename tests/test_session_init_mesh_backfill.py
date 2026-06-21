"""Tests for _heal_mesh_metadata_at_init in the session-init hook.

Closes the seat gap (goal #7): projects init'd before the strict-canonical
seat era have an ``ai_id`` but no {org_id, tenant_slug, mesh_id_prefix,
canonical_seat}, so ``cortex_session_init`` returns ``multi_project_no_seat``
for a multi-practice api_key. This hook step backfills the mesh metadata from
cortex's ``/v1/tenant/me`` at session-init, persisting the strict canonical
3-form seat.

Read-only against cortex (a GET); it never passes a ``seat`` to session_init
(that is the separate, cortex-gated Phase 2). Idempotent + non-fatal.
"""

from __future__ import annotations

import importlib.util
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
    plugin_lib = HOOK_PATH.parent.parent / "lib"
    sys.path.insert(0, str(plugin_lib))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(plugin_lib))
    return mod


class _FakeLoader:
    """Stand-in for the credentials loader — returns a fixed cortex config."""

    def __init__(self, cfg: dict):
        self._cfg = cfg

    def reload(self) -> None:
        pass

    def get_cortex_config(self) -> dict:
        return self._cfg


def _make_project_yaml(project_root: Path, fields: dict) -> Path:
    (project_root / ".empirica").mkdir(parents=True)
    yaml_path = project_root / ".empirica" / "project.yaml"
    yaml_path.write_text(yaml.safe_dump(fields, sort_keys=False))
    return yaml_path


def _patch_creds(monkeypatch, cfg: dict) -> None:
    monkeypatch.setattr(
        "empirica.config.credentials_loader.get_credentials_loader",
        lambda: _FakeLoader(cfg),
    )


def _patch_fetch(monkeypatch, result, calls: list | None = None):
    """Patch setup_claude_code._fetch_tenant_metadata to return `result`.

    If `calls` is provided, each invocation appends to it (call-count assert).
    """
    def _fake(cortex_url, api_key):
        if calls is not None:
            calls.append((cortex_url, api_key))
        return result
    monkeypatch.setattr(
        "empirica.cli.command_handlers.setup_claude_code._fetch_tenant_metadata",
        _fake,
    )
    return calls


_META = {
    "org_id": "org-empirica",
    "tenant_slug": "david",
    "mesh_id_prefix": "empirica.david",
}


def test_backfills_when_ai_id_only(tmp_path, monkeypatch, capsys):
    """ai_id-only yaml + creds + REST → canonical_seat + mesh fields written."""
    mod = _load_hook_module()
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, {"version": "2.0", "name": "Empirica", "ai_id": "empirica"})
    _patch_creds(monkeypatch, {"url": "https://cortex.test", "api_key": "k"})
    _patch_fetch(monkeypatch, dict(_META))

    mod._heal_mesh_metadata_at_init(str(proj))

    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["canonical_seat"] == "empirica.david.empirica"
    assert cfg["mesh_id_prefix"] == "empirica.david"
    assert cfg["org_id"] == "org-empirica"
    assert cfg["tenant_slug"] == "david"
    # ai_id and other fields preserved
    assert cfg["ai_id"] == "empirica"
    assert cfg["name"] == "Empirica"
    err = capsys.readouterr().err
    assert "backfilled" in err
    assert "empirica.david.empirica" in err


def test_idempotent_when_canonical_seat_present(tmp_path, monkeypatch, capsys):
    """Already-seated yaml → no REST call, unchanged (fast-path before network)."""
    mod = _load_hook_module()
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, {
        "ai_id": "empirica", "canonical_seat": "empirica.david.empirica",
    })
    _patch_creds(monkeypatch, {"url": "https://cortex.test", "api_key": "k"})
    calls: list = []
    _patch_fetch(monkeypatch, dict(_META), calls)

    mod._heal_mesh_metadata_at_init(str(proj))

    assert calls == []  # fast-path returned before any fetch
    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert cfg["canonical_seat"] == "empirica.david.empirica"
    assert "backfilled" not in capsys.readouterr().err


def test_second_call_is_noop_after_backfill(tmp_path, monkeypatch):
    """Backfill then re-run → second run hits the idempotent fast-path."""
    mod = _load_hook_module()
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, {"ai_id": "empirica"})
    _patch_creds(monkeypatch, {"url": "https://cortex.test", "api_key": "k"})
    calls: list = []
    _patch_fetch(monkeypatch, dict(_META), calls)

    mod._heal_mesh_metadata_at_init(str(proj))   # writes seat
    mod._heal_mesh_metadata_at_init(str(proj))   # should not fetch again

    assert len(calls) == 1  # only the first run reached the network


def test_no_op_when_no_creds(tmp_path, monkeypatch, capsys):
    """No cortex creds → silent no-op, no REST call, yaml unchanged."""
    mod = _load_hook_module()
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, {"ai_id": "empirica"})
    _patch_creds(monkeypatch, {})  # no url/api_key
    calls: list = []
    _patch_fetch(monkeypatch, dict(_META), calls)

    mod._heal_mesh_metadata_at_init(str(proj))

    assert calls == []
    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert "canonical_seat" not in cfg
    assert "Traceback" not in capsys.readouterr().err


def test_no_op_when_no_ai_id(tmp_path, monkeypatch):
    """yaml without ai_id → leave alone, never guess a seat."""
    mod = _load_hook_module()
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, {"version": "2.0", "name": "no-ai"})
    _patch_creds(monkeypatch, {"url": "https://cortex.test", "api_key": "k"})
    calls: list = []
    _patch_fetch(monkeypatch, dict(_META), calls)

    mod._heal_mesh_metadata_at_init(str(proj))

    assert calls == []
    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert "canonical_seat" not in cfg


def test_no_op_when_fetch_lacks_prefix(tmp_path, monkeypatch):
    """REST returns no mesh_id_prefix → can't compose a seat, leave alone."""
    mod = _load_hook_module()
    proj = tmp_path / "empirica"
    _make_project_yaml(proj, {"ai_id": "empirica"})
    _patch_creds(monkeypatch, {"url": "https://cortex.test", "api_key": "k"})
    _patch_fetch(monkeypatch, {"org_id": "org-empirica", "tenant_slug": "david",
                               "mesh_id_prefix": None})

    mod._heal_mesh_metadata_at_init(str(proj))

    cfg = yaml.safe_load((proj / ".empirica" / "project.yaml").read_text())
    assert "canonical_seat" not in cfg


def test_no_op_when_no_project_yaml(tmp_path, capsys):
    """Missing project.yaml → silent, non-fatal."""
    mod = _load_hook_module()
    mod._heal_mesh_metadata_at_init(str(tmp_path / "no-such-project"))
    assert "Traceback" not in capsys.readouterr().err


def test_no_op_with_empty_project_root(capsys):
    """None / empty project_root → silent, non-fatal."""
    mod = _load_hook_module()
    mod._heal_mesh_metadata_at_init(None)
    mod._heal_mesh_metadata_at_init("")
    assert "Traceback" not in capsys.readouterr().err
