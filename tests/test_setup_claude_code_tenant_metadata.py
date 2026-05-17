"""Tests for the tenant-metadata helpers in setup_claude_code.

Covers the cortex Phase 1 mesh wiring (prop_jc5f4h5r2rdpdnzv5fmz7ky74u):
  - _fetch_tenant_metadata: REST happy / 401 / network error / malformed JSON
  - _persist_tenant_metadata: new file / merge / no-op-when-current / no project.yaml
  - _resolve_tenant_overrides: pulls --org-id / --tenant-slug / --mesh-id-prefix
  - _resolve_and_persist_tenant_metadata: flag-only / REST / flag-overrides-REST
"""

from __future__ import annotations

import io
import json
import types
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.setup_claude_code import (
    _fetch_tenant_metadata,
    _persist_tenant_metadata,
    _resolve_and_persist_tenant_metadata,
    _resolve_tenant_overrides,
)

# ─── _resolve_tenant_overrides ──────────────────────────────────────────

def test_resolve_overrides_all_none_when_args_bare():
    args = types.SimpleNamespace()
    assert _resolve_tenant_overrides(args) == {
        "org_id": None, "tenant_slug": None, "mesh_id_prefix": None,
    }


def test_resolve_overrides_picks_up_set_flags():
    args = types.SimpleNamespace(
        org_id="org-acme", tenant_slug="acme", mesh_id_prefix="acme_acme",
    )
    assert _resolve_tenant_overrides(args) == {
        "org_id": "org-acme", "tenant_slug": "acme", "mesh_id_prefix": "acme_acme",
    }


# ─── _fetch_tenant_metadata ─────────────────────────────────────────────

class _FakeResponse:
    """Minimal urlopen()-style context manager that exposes `read()`."""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _fake_response(payload: dict) -> _FakeResponse:
    return _FakeResponse(payload)


def test_fetch_happy_path_returns_three_fields():
    payload = {
        "user": "David", "org": "org-empirica",
        "org_id": "org-empirica",
        "tenant_slug": "david",
        "mesh_id_prefix": "empirica_david",
        "extra": "ignored",
    }
    with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
        result = _fetch_tenant_metadata("https://cortex.example.com", "ctx_test")
    assert result == {
        "org_id": "org-empirica",
        "tenant_slug": "david",
        "mesh_id_prefix": "empirica_david",
    }


def test_fetch_strips_trailing_slash_on_cortex_url():
    payload = {"org_id": "x", "tenant_slug": "y", "mesh_id_prefix": "x_y"}
    captured: dict = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return _fake_response(payload)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        _fetch_tenant_metadata("https://cortex.example.com/", "ctx_test")

    assert captured["url"] == "https://cortex.example.com/v1/tenant/me"
    assert captured["auth"] == "Bearer ctx_test"


def test_fetch_http_error_returns_none():
    err = urllib.error.HTTPError(
        url="https://cortex/v1/tenant/me", code=401,
        msg="Unauthorized", hdrs=None, fp=io.BytesIO(b""),  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = _fetch_tenant_metadata("https://cortex.example.com", "bad_key")
    assert result is None


def test_fetch_network_error_returns_none():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        assert _fetch_tenant_metadata("https://cortex.example.com", "ctx_test") is None


def test_fetch_malformed_json_returns_none():
    class _BadResponse:
        def read(self):
            return b"<<not json>>"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    with patch("urllib.request.urlopen", return_value=_BadResponse()):
        assert _fetch_tenant_metadata("https://cortex.example.com", "ctx_test") is None


# ─── _persist_tenant_metadata ───────────────────────────────────────────

def _seed_project_yaml(project_root: Path, contents: dict | None = None) -> Path:
    import yaml
    (project_root / ".empirica").mkdir(parents=True, exist_ok=True)
    pyaml = project_root / ".empirica" / "project.yaml"
    pyaml.write_text(yaml.safe_dump(contents or {}), encoding="utf-8")
    return pyaml


def test_persist_writes_fields_into_existing_project_yaml(tmp_path):
    import yaml
    pyaml = _seed_project_yaml(tmp_path, {"name": "test", "ai_id": "test"})
    wrote = _persist_tenant_metadata(
        tmp_path, org_id="org-x", tenant_slug="x", mesh_id_prefix="x_x",
    )
    assert wrote is True
    data = yaml.safe_load(pyaml.read_text(encoding="utf-8"))
    assert data["org_id"] == "org-x"
    assert data["tenant_slug"] == "x"
    assert data["mesh_id_prefix"] == "x_x"
    assert data["name"] == "test"  # preserves existing keys
    assert data["ai_id"] == "test"


def test_persist_returns_false_when_no_project_yaml(tmp_path):
    wrote = _persist_tenant_metadata(
        tmp_path, org_id="org-x", tenant_slug="x", mesh_id_prefix="x_x",
    )
    assert wrote is False


def test_persist_returns_false_when_all_fields_none(tmp_path):
    _seed_project_yaml(tmp_path)
    wrote = _persist_tenant_metadata(
        tmp_path, org_id=None, tenant_slug=None, mesh_id_prefix=None,
    )
    assert wrote is False


def test_persist_no_op_when_values_already_match(tmp_path):
    _seed_project_yaml(tmp_path, {"org_id": "x", "tenant_slug": "y", "mesh_id_prefix": "x_y"})
    wrote = _persist_tenant_metadata(
        tmp_path, org_id="x", tenant_slug="y", mesh_id_prefix="x_y",
    )
    assert wrote is False  # no diff = no rewrite


def test_persist_partial_update_only_writes_supplied_fields(tmp_path):
    import yaml
    _seed_project_yaml(tmp_path, {"org_id": "old", "ai_id": "test"})
    wrote = _persist_tenant_metadata(
        tmp_path, org_id=None, tenant_slug="new", mesh_id_prefix=None,
    )
    assert wrote is True
    data = yaml.safe_load((tmp_path / ".empirica" / "project.yaml").read_text())
    assert data["org_id"] == "old"   # preserved (not supplied)
    assert data["tenant_slug"] == "new"  # added
    assert "mesh_id_prefix" not in data


# ─── _resolve_and_persist_tenant_metadata (end-to-end) ─────────────────

def test_resolve_flags_only_skips_rest_entirely(tmp_path):
    import yaml
    _seed_project_yaml(tmp_path)
    args = types.SimpleNamespace(
        org_id="org-flag", tenant_slug="flag", mesh_id_prefix="flag_flag",
    )
    with patch("urllib.request.urlopen") as mock_open:
        result = _resolve_and_persist_tenant_metadata(args, 'json', project_root=tmp_path)
    mock_open.assert_not_called()
    assert result == {"org_id": "org-flag", "tenant_slug": "flag", "mesh_id_prefix": "flag_flag"}
    data = yaml.safe_load((tmp_path / ".empirica" / "project.yaml").read_text())
    assert data["org_id"] == "org-flag"


def test_resolve_flags_partial_then_rest_fills_gaps(tmp_path):
    import yaml
    _seed_project_yaml(tmp_path)
    args = types.SimpleNamespace(
        org_id="org-flag", tenant_slug=None, mesh_id_prefix=None,
    )
    rest_payload = {
        "org_id": "org-rest", "tenant_slug": "rest", "mesh_id_prefix": "rest_rest",
    }

    with patch(
        "empirica.cli.command_handlers.setup_claude_code.get_credentials_loader",
        create=True,
    ) as mock_loader_factory, patch(
        "urllib.request.urlopen", return_value=_fake_response(rest_payload),
    ):
        mock_loader = mock_loader_factory.return_value
        mock_loader.get_cortex_config.return_value = {
            "url": "https://cortex.example.com", "api_key": "ctx_test",
        }
        result = _resolve_and_persist_tenant_metadata(args, 'json', project_root=tmp_path)

    # Flag wins for org_id; REST fills the other two
    assert result == {
        "org_id": "org-flag",  # flag overrode REST
        "tenant_slug": "rest",
        "mesh_id_prefix": "rest_rest",
    }
    data = yaml.safe_load((tmp_path / ".empirica" / "project.yaml").read_text())
    assert data["org_id"] == "org-flag"
    assert data["tenant_slug"] == "rest"


def test_resolve_returns_none_when_no_creds_no_flags(tmp_path):
    args = types.SimpleNamespace()
    with patch(
        "empirica.cli.command_handlers.setup_claude_code.get_credentials_loader",
        create=True,
    ) as mock_loader_factory:
        mock_loader = mock_loader_factory.return_value
        mock_loader.get_cortex_config.return_value = {}  # no creds
        result = _resolve_and_persist_tenant_metadata(args, 'json', project_root=tmp_path)
    assert result is None


@pytest.mark.parametrize("output_format", ["json", "human"])
def test_resolve_rest_failure_does_not_crash(tmp_path, output_format, capsys):
    _seed_project_yaml(tmp_path)
    args = types.SimpleNamespace()

    with patch(
        "empirica.cli.command_handlers.setup_claude_code.get_credentials_loader",
        create=True,
    ) as mock_loader_factory, patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("no route"),
    ):
        mock_loader = mock_loader_factory.return_value
        mock_loader.get_cortex_config.return_value = {
            "url": "https://cortex.example.com", "api_key": "ctx_test",
        }
        result = _resolve_and_persist_tenant_metadata(args, output_format, project_root=tmp_path)

    assert result is None
    if output_format == 'human':
        captured = capsys.readouterr()
        assert "Couldn't fetch tenant metadata" in captured.out
