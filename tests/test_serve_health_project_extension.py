"""Tests for /api/v1/health project-info extension (v0.5 LOCAL-ARTIFACTS).

The extension surface adds project_id, project_path, project_name, project_slug,
repo_url to the existing health response. Extension uses these to dispatch
daemon-first vs Cortex-fallback per query (project_id match) and to populate
the dropdown for Empirica-only users (no Cortex available).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from empirica.api.serve_app import create_serve_app


def _make_project(root: Path, name: str, *, project_id: str | None = None) -> Path:
    """Make a directory with .empirica/project.yaml inside `root`."""
    proj = root / name
    proj.mkdir(parents=True)
    (proj / ".empirica").mkdir()
    yaml_lines = [f"name: {name}\n"]
    if project_id is not None:
        yaml_lines.append(f"project_id: {project_id}\n")
    (proj / ".empirica" / "project.yaml").write_text("".join(yaml_lines), encoding="utf-8")
    return proj


@pytest.fixture
def reset_daemon_cache():
    """Reset the daemon's project cache between tests."""
    import empirica.api.daemon_project as dp
    dp._cached = False
    dp._cached_project = None
    yield
    dp._cached = False
    dp._cached_project = None


def test_health_includes_project_fields_when_project_resolves(tmp_path, monkeypatch, reset_daemon_cache):
    proj = _make_project(tmp_path, "extension-test", project_id="cortex-uuid-xyz")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        monkeypatch.setenv("PWD", str(proj))
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["project_id"] == "cortex-uuid-xyz"
    assert data["project_name"] == "extension-test"
    assert data["project_slug"] == "extension-test"
    assert data["project_path"] == str(proj)


def test_health_returns_null_project_fields_outside_any_project(tmp_path, monkeypatch, reset_daemon_cache):
    """Daemon launched outside any project tree → all project_* fields null, but daemon still healthy."""
    bare = tmp_path / "no-project-here"
    bare.mkdir()

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(bare)
        monkeypatch.setenv("PWD", str(bare))
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["project_id"] is None
    assert data["project_name"] is None
    assert data["project_slug"] is None
    assert data["project_path"] is None


def test_health_project_id_null_for_local_only_project(tmp_path, monkeypatch, reset_daemon_cache):
    """Local-only project (not registered on Cortex) → project_id=null but other fields populated.

    Extension uses this signal to surface a 'register on Cortex to share' hint.
    """
    proj = _make_project(tmp_path, "local-only")  # no project_id

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        monkeypatch.setenv("PWD", str(proj))
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["project_id"] is None  # not on Cortex yet
    assert data["project_name"] == "local-only"
    assert data["project_slug"] == "local-only"


def test_health_existing_fields_still_present(tmp_path, monkeypatch, reset_daemon_cache):
    """Backwards compat: ok/version/api_version/integrations still in response."""
    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PWD", str(tmp_path))
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/health")

    data = response.json()
    assert "ok" in data
    assert "version" in data
    assert "api_version" in data
    assert "ollama" in data
    assert "qdrant" in data
    assert "claude_mem" in data
