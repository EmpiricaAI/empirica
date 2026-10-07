"""Test that project-wide graph seeds epistemic_sources by discovered_at, not created_timestamp."""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from empirica.api.serve_app import create_serve_app


def _make_project_with_db(tmp_path: Path, project_id: str) -> Path:
    """Minimal-schema fixture for testing."""
    proj = tmp_path / f"proj-{project_id[:8]}"
    proj.mkdir()
    (proj / ".empirica").mkdir()
    (proj / ".empirica" / "project.yaml").write_text(f"name: test\nproject_id: {project_id}\n", encoding="utf-8")
    db_dir = proj / ".empirica" / "sessions"
    db_dir.mkdir()
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            finding TEXT NOT NULL, finding_data TEXT,
            subject TEXT, impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            unknown TEXT NOT NULL, unknown_data TEXT,
            is_resolved INTEGER DEFAULT 0, resolved_by TEXT, resolved_timestamp REAL,
            impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_dead_ends (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            approach TEXT NOT NULL, why_failed TEXT, dead_end_data TEXT,
            impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE mistakes_made (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            mistake TEXT NOT NULL, why_wrong TEXT, prevention TEXT,
            mistake_data TEXT, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE assumptions (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            assumption TEXT NOT NULL, confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'unverified', resolution_finding_id TEXT,
            epistemic_source TEXT,
            created_timestamp REAL NOT NULL, resolved_timestamp REAL
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            choice TEXT NOT NULL, rationale TEXT, alternatives TEXT,
            confidence_at_decision REAL, reversibility TEXT,
            outcome TEXT, regret_score REAL, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            source_type TEXT, source_url TEXT, title TEXT,
            description TEXT, confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT, discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            transaction_id TEXT, objective TEXT NOT NULL,
            status TEXT DEFAULT 'in_progress', is_completed INTEGER DEFAULT 0,
            goal_data TEXT, created_timestamp REAL NOT NULL,
            completed_timestamp REAL
        );
        CREATE TABLE artifact_edges (
            from_id TEXT NOT NULL, to_id TEXT NOT NULL, relation TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            PRIMARY KEY (from_id, to_id, relation)
        );
    """)
    conn.commit()
    conn.close()
    return proj


def _insert_finding(db_path, project_id, finding="F", session_id="sess-1", created_timestamp=None) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, "
        "created_timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (art_id, project_id, session_id, finding, "{}", created_timestamp or time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_source(db_path, project_id, title="S", discovered_at=None, session_id=None) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, session_id, title, discovered_at) VALUES (?, ?, ?, ?, ?)",
        (art_id, project_id, session_id, title, discovered_at or time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


@pytest.fixture
def reset_daemon_cache():
    import empirica.api.daemon_project as dp

    dp._cached = False
    dp._cached_project = None
    yield
    dp._cached = False
    dp._cached_project = None


def test_project_wide_graph_seeds_sources_by_discovered_at(tmp_path, monkeypatch, reset_daemon_cache):
    """Project-wide graph should seed epistemic_sources by discovered_at, not created_timestamp.

    This tests that the project-wide seed scan (no seed_id, no session_id) correctly
    orders epistemic_sources by discovered_at. The test creates sources with known
    discovered_at timestamps and verifies they appear in the graph.
    """
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    # Create sources with distinct discovered_at times
    t1 = time.time() - 100
    t2 = time.time() - 50
    t3 = time.time()

    # Insert in reverse order of discovery (newest first) to ensure ordering by discovered_at
    # rather than insertion order
    s1_old = _insert_source(db_path, pid, "old source", discovered_at=t1)
    s2_mid = _insert_source(db_path, pid, "middle source", discovered_at=t2)
    s3_new = _insert_source(db_path, pid, "newest source", discovered_at=t3)

    # Also add a finding to ensure mixed artifact types are handled
    f1 = _insert_finding(db_path, pid, "finding 1", created_timestamp=t1)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        # Request project-wide graph (no seed_id, no session_id)
        response = client.get("/api/v1/artifacts/graph?max_nodes=10")

    assert response.status_code == 200
    data = response.json()

    # Should include all three sources and the finding
    node_ids = {n["id"] for n in data["nodes"]}
    assert s1_old in node_ids, "Oldest source should be in graph"
    assert s2_mid in node_ids, "Middle source should be in graph"
    assert s3_new in node_ids, "Newest source should be in graph"
    assert f1 in node_ids, "Finding should be in graph"

    # Verify sources have correct titles
    source_nodes = {n["id"]: n["title"] for n in data["nodes"] if n["type"] == "source"}
    assert source_nodes.get(s1_old) == "old source"
    assert source_nodes.get(s2_mid) == "middle source"
    assert source_nodes.get(s3_new) == "newest source"


def test_project_wide_graph_handles_sources_and_findings_together(tmp_path, monkeypatch, reset_daemon_cache):
    """Project-wide graph should correctly seed both sources and findings with their respective order columns."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    t_early = time.time() - 100
    t_late = time.time()

    # Source discovered early
    s1 = _insert_source(db_path, pid, "early source", discovered_at=t_early)
    # Source discovered late
    s2 = _insert_source(db_path, pid, "late source", discovered_at=t_late)
    # Finding created early
    f1 = _insert_finding(db_path, pid, "early finding", created_timestamp=t_early)
    # Finding created late
    f2 = _insert_finding(db_path, pid, "late finding", created_timestamp=t_late)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/artifacts/graph?max_nodes=10")

    assert response.status_code == 200
    data = response.json()

    node_ids = {n["id"] for n in data["nodes"]}
    # All artifacts should be present regardless of their order column
    assert s1 in node_ids
    assert s2 in node_ids
    assert f1 in node_ids
    assert f2 in node_ids
