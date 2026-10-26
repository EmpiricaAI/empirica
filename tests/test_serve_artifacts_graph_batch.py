"""Tests for graph + batch endpoints (v0.5 LOCAL-ARTIFACTS T4).

GET /api/v1/artifacts/graph (with seed_id, session_id, types, max_nodes)
POST /api/v1/artifacts/log
POST /api/v1/artifacts/resolve
POST /api/v1/artifacts/delete
"""

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
    """Same minimal-schema fixture as T2/T3."""
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


def _insert_finding(db_path, project_id, finding="F", session_id="sess-1") -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, "
        "created_timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (art_id, project_id, session_id, finding, "{}", time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_decision(db_path, project_id, choice="D") -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO decisions (id, project_id, choice, rationale, created_timestamp) VALUES (?, ?, ?, ?, ?)",
        (art_id, project_id, choice, "because", time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_unknown(db_path, project_id, unknown="U") -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, unknown_data, "
        "is_resolved, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess", unknown, "{}", 0, time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_edge(db_path, from_id, to_id, relation="related"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
        (from_id, to_id, relation),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def reset_daemon_cache():
    import empirica.api.daemon_project as dp

    dp._cached = False
    dp._cached_project = None
    yield
    dp._cached = False
    dp._cached_project = None


# ── Graph endpoint ───────────────────────────────────────────────────


def test_graph_with_seed_id_returns_neighborhood(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    # Build: f1 -evidence-> d1 -informs-> f2
    f1 = _insert_finding(db_path, pid, "f1")
    d1 = _insert_decision(db_path, pid, "d1")
    f2 = _insert_finding(db_path, pid, "f2")
    _insert_edge(db_path, f1, d1, "evidence")
    _insert_edge(db_path, d1, f2, "informs")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        # depth=1 from f1 → reaches d1 (and via incoming, nothing back)
        depth1 = client.get(f"/api/v1/artifacts/graph?seed_id={f1}&depth=1").json()
        # depth=2 from f1 → reaches d1 then f2
        depth2 = client.get(f"/api/v1/artifacts/graph?seed_id={f1}&depth=2").json()

    assert {n["id"] for n in depth1["nodes"]} == {f1, d1}
    assert {n["id"] for n in depth2["nodes"]} == {f1, d1, f2}


def test_graph_depth_zero_returns_just_seed(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, "alone")
    f2 = _insert_finding(db_path, pid, "neighbor")
    _insert_edge(db_path, f1, f2, "ref")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get(f"/api/v1/artifacts/graph?seed_id={f1}&depth=0")

    data = response.json()
    assert {n["id"] for n in data["nodes"]} == {f1}
    # depth=0 means "just the seed", no edges traversed
    assert data["edges"] == []


def test_graph_walks_bidirectionally(tmp_path, monkeypatch, reset_daemon_cache):
    """BFS should follow edges in both directions (incoming + outgoing)."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, "target")
    f2 = _insert_finding(db_path, pid, "incoming source")
    # f2 → f1 (so f1 has only an INCOMING edge)
    _insert_edge(db_path, f2, f1, "evidence")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get(f"/api/v1/artifacts/graph?seed_id={f1}&depth=1")

    data = response.json()
    assert {n["id"] for n in data["nodes"]} == {f1, f2}


def test_graph_with_session_id_filters_seeds(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    _insert_finding(db_path, pid, "from session A", session_id="sess-A")
    _insert_finding(db_path, pid, "from session B", session_id="sess-B")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/artifacts/graph?session_id=sess-A&depth=0")

    data = response.json()
    # Only one finding from session A
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["title"] == "from session A"


def test_graph_types_filter_excludes_nodes(tmp_path, monkeypatch, reset_daemon_cache):
    """types=finding should exclude decision nodes from the graph."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, "f1")
    d1 = _insert_decision(db_path, pid, "d1")
    _insert_edge(db_path, f1, d1, "evidence")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get(f"/api/v1/artifacts/graph?seed_id={f1}&depth=1&types=finding")

    data = response.json()
    types = {n["type"] for n in data["nodes"]}
    assert types == {"finding"}
    # Decision was filtered out, so the edge to it is also dropped
    assert data["edges"] == []


def test_graph_503_when_no_project(tmp_path, monkeypatch, reset_daemon_cache):
    bare = tmp_path / "outside"
    bare.mkdir()

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(bare)
        monkeypatch.setenv("PWD", str(bare))
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/artifacts/graph")

    assert response.status_code == 503


def test_graph_route_does_not_match_artifact_id_capture(tmp_path, monkeypatch, reset_daemon_cache):
    """Regression: /artifacts/graph must route to the graph handler, not /artifacts/{id}=graph."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/artifacts/graph")

    # If routing was wrong, this would 404 with "Artifact graph not found".
    # Correct routing → 200 with {nodes, edges, project_id}
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "edges" in body


# ── Batch /artifacts/log ──────────────────────────────────────────────


def test_batch_log_503_when_no_project(tmp_path, monkeypatch, reset_daemon_cache):
    bare = tmp_path / "outside"
    bare.mkdir()

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(bare)
        monkeypatch.setenv("PWD", str(bare))
        client = TestClient(create_serve_app())
        response = client.post("/api/v1/artifacts/log", json={"nodes": [], "edges": []})

    assert response.status_code == 503


# ── Batch /artifacts/resolve ──────────────────────────────────────────


def test_batch_resolve_unknowns(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    u1 = _insert_unknown(db_path, pid, "Q1")
    u2 = _insert_unknown(db_path, pid, "Q2")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.post(
            "/api/v1/artifacts/resolve",
            json={"ids": [u1, u2], "resolved_by": "investigation"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] == 2
    assert data["not_found"] == 0


def test_batch_resolve_fails_a_finding_in_a_project_db_that_predates_the_lifecycle_with_its_hint(
    tmp_path, monkeypatch, reset_daemon_cache
):
    """Findings DO have resolve semantics (migrations 057/060/061). This fixture project's database predates those columns, so the finding
    cannot be resolved there: the item is FAILED with the migration hint and the batch still answers. It was once asserted as `skipped`
    ("no resolve semantics"), which was false twice over: findings have the semantics, and the cause was an old schema."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid)
    u1 = _insert_unknown(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.post(
            "/api/v1/artifacts/resolve",
            json={"ids": [f1, u1], "resolved_by": "x"},
        )

    data = response.json()
    assert data["resolved"] == 1
    assert data["skipped"] == 0 and data["failed"] == 1
    failed = next(r for r in data["results"] if r["outcome"] == "failed")
    assert failed["id"] == f1 and "predates" in failed["reason"] and "migration" in failed["hint"].lower()


def test_batch_resolve_still_skips_a_decision_which_genuinely_has_no_resolve_semantics(
    tmp_path, monkeypatch, reset_daemon_cache
):
    """Control for the test above: a skip is for a TYPE with no lifecycle, not for a project whose schema is old."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"
    d1 = _insert_decision(db_path, pid)
    u1 = _insert_unknown(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        data = (
            TestClient(create_serve_app())
            .post("/api/v1/artifacts/resolve", json={"ids": [d1, u1], "resolved_by": "x"})
            .json()
        )

    assert (data["resolved"], data["skipped"], data["failed"]) == (1, 1, 0)
    assert next(r for r in data["results"] if r["id"] == d1)["reason"] == "no resolve semantics"


def test_batch_resolve_422_when_no_ids_or_items(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.post("/api/v1/artifacts/resolve", json={})

    assert response.status_code == 422


# ── Batch /artifacts/delete ───────────────────────────────────────────


def test_batch_delete_removes_multiple_artifacts(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, "delete me")
    u1 = _insert_unknown(db_path, pid, "delete me too")
    _insert_decision(db_path, pid, "keep")  # not in delete batch — should remain

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.post(
            "/api/v1/artifacts/delete",
            json={"ids": [f1, u1]},
        )

    data = response.json()
    assert data["deleted"] == 2

    # Verify only d1 remains
    conn = sqlite3.connect(str(db_path))
    f_count = conn.execute("SELECT COUNT(*) FROM project_findings").fetchone()[0]
    u_count = conn.execute("SELECT COUNT(*) FROM project_unknowns").fetchone()[0]
    d_count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    conn.close()
    assert f_count == 0
    assert u_count == 0
    assert d_count == 1


def test_batch_delete_handles_missing_ids(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.post(
            "/api/v1/artifacts/delete",
            json={"ids": [f1, "ghost-id-not-real"]},
        )

    data = response.json()
    assert data["deleted"] == 1
    assert data["not_found"] == 1


def test_batch_delete_422_when_body_empty(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.post("/api/v1/artifacts/delete", json={})

    assert response.status_code == 422
