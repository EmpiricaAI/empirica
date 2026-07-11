"""Tests for ERM §6.2 search surfaces (PR-c): CLI --semantic + daemon ?q=.

Mocked-Qdrant unit tests pin the wiring (routing, V-4 projection). The §8 Carly
acceptance is an integration test that runs only where Qdrant is live — skipped
in CI (`-m "not integration"`), exercised on a box with embeddings.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import empirica.cli.command_handlers.entity_commands as ec
from empirica.api.routes import entities as ent
from empirica.core.qdrant import connection as conn

_WI_SEARCH = "empirica.core.qdrant.workspace_index.search_workspace_index"


# ── daemon _semantic_entity_search (V-4 ranked alternative) ───────────────────


def test_semantic_entity_search_projects_v4_shape():
    hits = [
        {
            "entity_id": "e-1",
            "entity_type": "engagement",
            "display_name": "Onboarding",
            "status": "active",
            "score": 0.88,
            "text": "…",
        }
    ]
    with patch(_WI_SEARCH, return_value=hits) as m:
        out = ent._semantic_entity_search("onboarding", "engagement", "active", 20)
    assert out == [{"id": "e-1", "type": "engagement", "name": "Onboarding", "status": "active", "score": 0.88}]
    assert m.call_args.kwargs["point_kind"] == "entity"
    assert m.call_args.kwargs["query_text"] == "onboarding"


def test_semantic_status_all_maps_to_none():
    with patch(_WI_SEARCH, return_value=[]) as m:
        ent._semantic_entity_search("q", None, "all", 5)
    assert m.call_args.kwargs["status"] is None  # all → include archived


# ── CLI entity-search --semantic routing ──────────────────────────────────────


def test_cli_semantic_routes_to_vector():
    ns = SimpleNamespace(
        query="carly", type="contact", status="active", limit=10, output="json", semantic=True, verbose=False
    )
    with patch(_WI_SEARCH, return_value=[]) as m:
        ec.handle_entity_search_command(ns)
    assert m.called and m.call_args.kwargs["point_kind"] == "entity"


def test_cli_default_is_sql_not_vector():
    ns = SimpleNamespace(query="x", type=None, status="active", limit=10, output="json", semantic=False, verbose=False)
    repo = MagicMock()
    repo.search_entities.return_value = []
    cm = MagicMock()
    cm.__enter__.return_value = repo
    cm.__exit__.return_value = False
    with patch(_WI_SEARCH) as m, patch.object(ec.WorkspaceDBRepository, "open", return_value=cm):
        ec.handle_entity_search_command(ns)
    m.assert_not_called()  # default path must not touch the vector index
    repo.search_entities.assert_called_once()


# ── §8 Carly acceptance — live-Qdrant integration (skipped in CI) ─────────────


@pytest.mark.integration
def test_carly_acceptance_semantic_hits():
    if not conn._check_qdrant_available():
        pytest.skip("Qdrant unavailable — §8 acceptance is a live integration test")

    from empirica.core.qdrant.workspace_index import embed_entity_to_workspace_index, search_workspace_index

    # Carly's 3 entities (org / contact / engagement) — §8 fixture.
    embed_entity_to_workspace_index("organization", "o-carly-accept", "Empirica Foundation", "a foundation customer")
    embed_entity_to_workspace_index(
        "contact", "c-carly-accept", "Carly", metadata={"company_name": "Empirica Foundation"}
    )
    embed_entity_to_workspace_index(
        "engagement", "e-carly-accept", "Carly foundation onboarding", "onboarding", domain="onboarding", stage="live"
    )

    # §8.1 — semantic engagement hit above a sane floor.
    eng = search_workspace_index(
        query_text="onboarding for a foundation customer", entity_type="engagement", point_kind="entity", limit=5
    )
    assert any(h["entity_id"] == "e-carly-accept" for h in eng)

    # §8.2 — contact hit.
    con = search_workspace_index(query_text="Carly", entity_type="contact", point_kind="entity", limit=5)
    assert any(h["entity_id"] == "c-carly-accept" for h in con)

    # §8.4 — idempotent re-embed keeps the same point (stable id).
    embed_entity_to_workspace_index("engagement", "e-carly-accept", "Carly foundation onboarding", "onboarding")
    eng2 = search_workspace_index(query_text="onboarding foundation", entity_type="engagement", point_kind="entity")
    ids = [h["entity_id"] for h in eng2]
    assert ids.count("e-carly-accept") == 1  # no duplicate point
