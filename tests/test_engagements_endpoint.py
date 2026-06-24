"""CCR-1 (prop_kiamoy5z): GET /api/v1/engagements EngagementMin feed.

Two layers:
- get_engagement_projection() — the daemon-side enrichment (counts + synthesized
  metadata). This is the load-bearing logic; tested directly against an
  in-memory workspace.db.
- the route — a TestClient smoke confirming the EngagementMin shape + filters
  wire through (workspace.db pointed at a temp file via EMPIRICA_WORKSPACE_DB).
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema


@pytest.fixture
def repo() -> WorkspaceDBRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    return WorkspaceDBRepository(conn)


def _membership(repo, etype, eid, gtype, gid, role=None):
    now = time.time()
    repo._execute(
        """INSERT INTO entity_memberships
           (entity_type, entity_id, group_type, group_id, role, joined_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (etype, eid, gtype, gid, role, now, now),
    )


def _registry(repo, etype, eid, name, metadata=None):
    now = time.time()
    repo._execute(
        """INSERT INTO entity_registry
           (entity_type, entity_id, display_name, source_db, source_table, created_at, metadata)
           VALUES (?, ?, ?, 'test', 'test', ?, ?)""",
        (etype, eid, name, now, json.dumps(metadata) if metadata else None),
    )


def _artifact(repo, artifact_type, artifact_id, engagement_id):
    now = time.time()
    repo._execute(
        """INSERT INTO entity_artifacts
           (id, artifact_type, artifact_id, entity_type, entity_id, engagement_id, created_at)
           VALUES (?, ?, ?, 'engagement', ?, ?, ?)""",
        (f"{artifact_type}-{artifact_id}", artifact_type, artifact_id, engagement_id, engagement_id, now),
    )


# ── get_engagement_projection ────────────────────────────────────────────────


def test_projection_zero_when_bare(repo):
    repo.create_engagement("e1", "Ticket", domain="support", stage="support.new")
    p = repo.get_engagement_projection("e1")
    assert p["member_count"] == 0
    assert p["goal_count"] == 0
    assert p["linked_artifact_count"] == 0
    assert p["org_display"] is None
    assert p["severity"] is None
    assert p["assignee_id"] is None


def test_projection_counts(repo):
    repo.create_engagement("e1", "Ticket", domain="support", stage="support.new")
    # 2 members of the engagement
    _membership(repo, "contact", "c1", "engagement", "e1", role="member")
    _membership(repo, "contact", "c2", "engagement", "e1", role="member")
    # 3 linked artifacts, 2 of which are goals
    _artifact(repo, "finding", "f1", "e1")
    _artifact(repo, "goal", "g1", "e1")
    _artifact(repo, "goal", "g2", "e1")
    p = repo.get_engagement_projection("e1")
    assert p["member_count"] == 2
    assert p["linked_artifact_count"] == 3
    assert p["goal_count"] == 2  # daemon-local artifact→goal linkage


def test_projection_org_display_via_ticket_of(repo):
    repo.create_engagement("e1", "Ticket", domain="support", stage="support.new")
    _registry(repo, "organization", "o1", "Acme Corp")
    _membership(repo, "engagement", "e1", "organization", "o1", role="ticket_of")
    p = repo.get_engagement_projection("e1")
    assert p["org_display"] == "Acme Corp"


def test_projection_org_display_ignores_non_ticket_of(repo):
    repo.create_engagement("e1", "Ticket", domain="support", stage="support.new")
    _registry(repo, "organization", "o1", "Acme Corp")
    # a non-ticket_of edge must not resolve as the org_display
    _membership(repo, "engagement", "e1", "organization", "o1", role="watching")
    assert repo.get_engagement_projection("e1")["org_display"] is None


def test_projection_severity_assignee_passthrough(repo):
    repo.create_engagement("e1", "Ticket", domain="support", stage="support.new")
    _registry(
        repo,
        "engagement",
        "e1",
        "Ticket",
        metadata={"severity": "high", "assignee_id": "u-7", "assignee_display": "Sam"},
    )
    p = repo.get_engagement_projection("e1")
    assert p["severity"] == "high"
    assert p["assignee_id"] == "u-7"
    assert p["assignee_display"] == "Sam"


def test_projection_tolerates_garbage_metadata(repo):
    repo.create_engagement("e1", "Ticket", domain="support", stage="support.new")
    repo._execute(
        """INSERT INTO entity_registry
           (entity_type, entity_id, display_name, source_db, source_table, created_at, metadata)
           VALUES ('engagement', 'e1', 'Ticket', 'test', 'test', ?, '{not json')""",
        (time.time(),),
    )
    # must not raise — severity/assignee just resolve to None
    p = repo.get_engagement_projection("e1")
    assert p["severity"] is None


# ── route smoke (TestClient + temp workspace.db) ─────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    db = tmp_path / "workspace.db"
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(db))
    # Seed the temp workspace.db with one support engagement + org + metadata.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    r = WorkspaceDBRepository(conn)
    r.create_engagement("e1", "Login broken", domain="support", stage="support.resolved")
    r._execute("UPDATE engagements SET outcome = 'resolved' WHERE engagement_id = 'e1'", ())
    _registry(r, "organization", "o1", "Acme Corp")
    _registry(r, "engagement", "e1", "Login broken", metadata={"severity": "high"})
    _membership(r, "engagement", "e1", "organization", "o1", role="ticket_of")
    conn.commit()
    conn.close()

    from empirica.api.serve_app import create_serve_app

    return TestClient(create_serve_app())


def test_route_returns_engagement_min(client):
    r = client.get("/api/v1/engagements")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1
    e = body["engagements"][0]
    assert e["id"] == "e1"
    assert e["domain"] == "support"
    assert e["stage"] == "support.resolved"
    assert e["outcome"] == "resolved"
    assert e["metadata"]["org_display"] == "Acme Corp"
    assert e["metadata"]["severity"] == "high"
    assert e["member_count"] == 0


def test_route_domain_filter(client):
    assert client.get("/api/v1/engagements?domain=support").json()["count"] == 1
    assert client.get("/api/v1/engagements?domain=sales").json()["count"] == 0


def test_route_invalid_lifecycle_422(client):
    r = client.get("/api/v1/engagements?lifecycle=not_a_state")
    assert r.status_code == 422
