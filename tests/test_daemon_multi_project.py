"""Tests for the v1.10.0 daemon multi-project routing.

Covers:
- /api/v1/health surfaces known_projects from registry
- ?project_id=X lookup hits + 404 misses
- ?path=Y bypass + 404 on non-empirica paths
- No params falls back to CWD-bound daemon project (existing behavior)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from empirica.api.registry import save_registry
from empirica.api.serve_app import create_serve_app


# ─── Fixtures ──────────────────────────────────────────────────────────


def _seed_project(root: Path, name: str, project_id: str) -> Path:
    """Create a minimal project tree with .empirica/project.yaml + sessions.db."""
    proj = root / name
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    yaml_path = proj / ".empirica" / "project.yaml"
    yaml_path.write_text(
        yaml.safe_dump({"project_id": project_id, "name": name}),
        encoding="utf-8",
    )
    # Seed a minimal sessions.db with project_findings + projects tables
    db_path = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            finding TEXT,
            finding_data TEXT,
            impact REAL,
            epistemic_source TEXT,
            confidence REAL DEFAULT 0.5,
            recorded_at TIMESTAMP,
            created_timestamp REAL,
            related_finding_ids TEXT,
            session_id TEXT,
            goal_id TEXT,
            subtask_id TEXT,
            transaction_id TEXT,
            subject TEXT
        );
        CREATE TABLE artifact_edges (
            from_id TEXT,
            to_id TEXT,
            relation TEXT
        );
    """)
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)", (project_id, name))
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, impact) "
        "VALUES (?, ?, ?, ?)",
        (f"f-{name}", project_id, f"finding in {name}", 0.5),
    )
    conn.commit()
    conn.close()
    return proj


# ─── /api/v1/health surfaces known_projects ────────────────────────────


def test_health_includes_known_projects_from_registry(tmp_path: Path):
    registry_path = tmp_path / "registry.yaml"
    save_registry({
        "version": 1,
        "projects": [
            {"project_id": "p1", "slug": "p1", "name": "P1", "path": "/tmp/p1"},
            {"project_id": "p2", "slug": "p2", "name": "P2", "path": "/tmp/p2"},
        ],
    }, registry_path)

    app = create_serve_app()
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path):
        with TestClient(app) as client:
            r = client.get("/api/v1/health")
            assert r.status_code == 200
            body = r.json()
            assert "known_projects" in body
            assert len(body["known_projects"]) == 2
            assert {p["project_id"] for p in body["known_projects"]} == {"p1", "p2"}


def test_health_empty_known_projects_when_registry_missing(tmp_path: Path):
    registry_path = tmp_path / "nonexistent.yaml"
    app = create_serve_app()
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path):
        with TestClient(app) as client:
            r = client.get("/api/v1/health")
            assert r.status_code == 200
            assert r.json()["known_projects"] == []


# ─── ?project_id=X registry routing ────────────────────────────────────


def test_findings_routes_by_project_id(tmp_path: Path):
    """?project_id=X looks up registry, opens that project's sqlite."""
    proj_a = _seed_project(tmp_path, "alpha", "alpha-id")
    proj_b = _seed_project(tmp_path, "beta", "beta-id")

    registry_path = tmp_path / "registry.yaml"
    save_registry({
        "version": 1,
        "projects": [
            {"project_id": "alpha-id", "slug": "alpha", "name": "alpha", "path": str(proj_a)},
            {"project_id": "beta-id", "slug": "beta", "name": "beta", "path": str(proj_b)},
        ],
    }, registry_path)

    app = create_serve_app()
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path):
        with TestClient(app) as client:
            r_a = client.get("/api/v1/findings?project_id=alpha-id")
            assert r_a.status_code == 200
            data_a = r_a.json()
            assert data_a["project_id"] == "alpha-id"
            assert len(data_a["findings"]) == 1
            assert data_a["findings"][0]["id"] == "f-alpha"

            r_b = client.get("/api/v1/findings?project_id=beta-id")
            assert r_b.status_code == 200
            data_b = r_b.json()
            assert data_b["project_id"] == "beta-id"
            assert data_b["findings"][0]["id"] == "f-beta"


def test_findings_404_when_project_id_not_in_registry(tmp_path: Path):
    registry_path = tmp_path / "registry.yaml"
    save_registry({"version": 1, "projects": []}, registry_path)

    app = create_serve_app()
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path):
        with TestClient(app) as client:
            r = client.get("/api/v1/findings?project_id=nope")
            assert r.status_code == 404
            detail = r.json()["detail"]
            assert "not registered" in detail["error"]
            assert "projects-discover" in detail["hint"]


def test_findings_404_when_registry_path_stale(tmp_path: Path):
    """Registry entry points at a path that no longer has .empirica/."""
    registry_path = tmp_path / "registry.yaml"
    save_registry({
        "version": 1,
        "projects": [
            {"project_id": "stale-id", "slug": "stale", "name": "stale",
             "path": str(tmp_path / "nonexistent")},
        ],
    }, registry_path)

    app = create_serve_app()
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path):
        with TestClient(app) as client:
            r = client.get("/api/v1/findings?project_id=stale-id")
            assert r.status_code == 404


# ─── ?path=Y power-user bypass ─────────────────────────────────────────


def test_findings_routes_by_path_bypass(tmp_path: Path):
    """?path=Y opens Y/.empirica/ directly, no registry lookup."""
    proj = _seed_project(tmp_path, "gamma", "gamma-id")

    # Registry is empty — proves path bypass doesn't need registry
    registry_path = tmp_path / "registry.yaml"
    save_registry({"version": 1, "projects": []}, registry_path)

    app = create_serve_app()
    with patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path):
        with TestClient(app) as client:
            r = client.get(f"/api/v1/findings?path={proj}")
            assert r.status_code == 200
            data = r.json()
            assert data["project_id"] == "gamma-id"
            assert data["findings"][0]["id"] == "f-gamma"


def test_findings_404_when_path_not_empirica_project(tmp_path: Path):
    barren = tmp_path / "barren"
    barren.mkdir()

    app = create_serve_app()
    with TestClient(app) as client:
        r = client.get(f"/api/v1/findings?path={barren}")
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert "not an Empirica project" in detail["error"]


# ─── Backward-compat: no params → CWD-bound fallback ───────────────────


def test_findings_no_params_uses_cached_daemon_project(tmp_path: Path):
    """When no ?project_id= and no ?path=, falls back to CWD-bound project."""
    proj = _seed_project(tmp_path, "delta", "delta-id")

    cached_project_dict = {
        "project_id": "delta-id",
        "project_path": str(proj),
        "project_name": "delta",
        "project_slug": "delta",
        "repo_url": None,
    }

    app = create_serve_app()
    # Patch at the import-site in routes/artifacts.py (where _resolve_project_dict
    # imports it) — patching the source module wouldn't affect already-bound names.
    with patch(
        "empirica.api.routes.artifacts.get_cached_daemon_project",
        return_value=cached_project_dict,
    ):
        with TestClient(app) as client:
            r = client.get("/api/v1/findings")
            assert r.status_code == 200
            data = r.json()
            assert data["project_id"] == "delta-id"
            assert data["findings"][0]["id"] == "f-delta"


def test_findings_503_when_no_cached_project_and_no_params():
    """No cached project + no params = 503 (existing daemon-not-bound contract)."""
    app = create_serve_app()
    with patch(
        "empirica.api.routes.artifacts.get_cached_daemon_project",
        return_value=None,
    ):
        with TestClient(app) as client:
            r = client.get("/api/v1/findings")
            assert r.status_code == 503
            assert "Daemon not bound" in r.json()["detail"]
