"""GET /entities refuses ``?q=`` combined with an org scope (goal 4444d565).

``?q=`` returned the semantically ranked set before ``parent_org`` / ``org_id`` was ever read, so a caller that scoped a search to
one organisation got every organisation's hits, with a 200 and ``ranked: true``: a scoped query that silently was not. Scoping
by organisation lives in crm-mcp (the 2026-09-17 ruling retired core's local membership spine as a source of truth), so core does
not grow a second, drifting implementation of it inside the semantic path: it refuses the combination and says where scoping
lives.

The control is every call that is NOT the combination: each must behave exactly as before.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from empirica.api.routes import entities
from empirica.api.routes.entities import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def semantic():
    hits = [{"id": "o-1", "type": "organization", "name": "A", "status": "active", "score": 0.9}]
    with patch.object(entities, "_semantic_entity_search", return_value=hits) as m:
        yield m


@pytest.mark.parametrize("scope", ["parent_org=o-1", "org_id=o-1"])
def test_a_semantic_query_with_an_org_scope_is_refused(client, semantic, scope):
    r = client.get(f"/api/v1/entities?q=acme&{scope}")
    assert r.status_code == 400
    assert "crm-mcp" in r.json()["detail"]
    semantic.assert_not_called()


def test_a_semantic_query_alone_still_ranks(client, semantic):
    r = client.get("/api/v1/entities?q=acme")
    assert r.status_code == 200 and r.json()["ranked"] is True and r.json()["count"] == 1
    semantic.assert_called_once()


def test_an_org_scope_without_a_query_is_not_refused(client, semantic, tmp_path):
    """Control: the refusal is for the combination only. The SQL path keeps its scoping and never touches the semantic one."""
    import sqlite3

    from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema

    conn = sqlite3.connect(str(tmp_path / "w.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    repo = WorkspaceDBRepository(conn)
    with patch("empirica.data.repositories.workspace_db.WorkspaceDBRepository.open", return_value=repo):
        r = client.get("/api/v1/entities?org_id=o-unknown")
    assert r.status_code == 200 and r.json().get("ranked") is not True
    semantic.assert_not_called()
