"""Tests for ERM §6.2 entity-row points in workspace_index (PR-a: writer + search).

Pure helpers (text composition §4, self-ref tags, stable id, format routing) are
tested directly. The writer + search integration is covered with a mocked Qdrant
stack (no live server) — capturing the upserted point payload and the query filter.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import empirica.core.qdrant.workspace_index as wi

# ── pure: text composition (§4) ───────────────────────────────────────────────


def test_compose_contact_text_order():
    t = wi._compose_entity_text(
        "contact", "Carly", "runs the foundation", {"company": "Empirica Foundation", "role": "Admiral"}
    )
    assert t == "Carly · Empirica Foundation · Admiral · runs the foundation"


def test_compose_org_text_uses_industry():
    t = wi._compose_entity_text("organization", "NLE", "Live events co", {"industry": "Live Entertainment"})
    assert t == "NLE · Live events co · Live Entertainment"


def test_compose_engagement_text_domain_stage_outcome():
    t = wi._compose_entity_text(
        "engagement", "Carly onboarding", "the onboarding", {"symptom": "seat stall"}, domain="onboarding", stage="live"
    )
    assert t == "Carly onboarding · onboarding · live · seat stall · the onboarding"


def test_compose_drops_empty_parts():
    assert wi._compose_entity_text("contact", "Solo", "") == "Solo"


def test_compose_role_falls_back_to_title():
    t = wi._compose_entity_text("contact", "X", "", {"title": "CTO"})
    assert "CTO" in t


# ── pure: canonicalization, self-ref, stable id ───────────────────────────────


def test_canon_org_to_organization():
    assert wi._canon_entity_type("org") == "organization"
    assert wi._canon_entity_type("organization") == "organization"
    assert wi._canon_entity_type("contact") == "contact"
    assert wi._canon_entity_type("weird") == "weird"


def test_self_refs_tag_own_field_only():
    assert wi._entity_self_refs("engagement", "e-1") == {"contact_ids": [], "org_ids": [], "engagement_ids": ["e-1"]}
    assert wi._entity_self_refs("org", "o-1")["org_ids"] == ["o-1"]
    assert wi._entity_self_refs("contact", "c-1")["contact_ids"] == ["c-1"]


def test_point_id_stable_and_distinct():
    a = wi._entity_point_id("engagement", "e-1")
    assert a == wi._entity_point_id("engagement", "e-1")  # stable → upsert, not dup
    assert a != wi._entity_point_id("engagement", "e-2")
    assert a != wi._entity_point_id("contact", "e-1")  # type namespaced


# ── pure: format routing ──────────────────────────────────────────────────────


def test_format_point_routes_entity():
    pt = SimpleNamespace(
        payload={"point_kind": "entity", "entity_type": "contact", "entity_id": "c-1", "status": "active"}
    )
    out = wi._format_point(pt, 0.9)
    assert out["point_kind"] == "entity" and out["entity_id"] == "c-1" and out["status"] == "active"
    assert "artifact_id" not in out


def test_format_point_routes_legacy_artifact():
    pt = SimpleNamespace(payload={"artifact_type": "finding", "artifact_id": "f-1"})  # no point_kind
    out = wi._format_point(pt, 0.7)
    assert out["artifact_id"] == "f-1" and "point_kind" not in out


# ── mocked Qdrant: writer ─────────────────────────────────────────────────────


def _patches(client):
    def _point(id, vector, payload):
        return SimpleNamespace(id=id, vector=vector, payload=payload)

    return [
        patch.object(wi, "_check_qdrant_available", return_value=True),
        patch.object(wi, "_get_qdrant_client", return_value=client),
        patch.object(wi, "_get_embedding_safe", return_value=[0.1] * 8),
        patch.object(wi, "_ensure_collection", return_value=True),
        patch.object(wi, "_get_qdrant_imports", return_value=(None, MagicMock(), MagicMock(), _point)),
    ]


def test_writer_upserts_entity_payload():
    client = MagicMock()
    with contextlib.ExitStack() as stack:
        for p in _patches(client):
            stack.enter_context(p)
        ok = wi.embed_entity_to_workspace_index(
            "org",
            "empirica-foundation",
            "Empirica Foundation",
            "the foundation",
            metadata={"industry": "AI"},
            emoji_state="🟢",
        )
    assert ok is True
    point = client.upsert.call_args.kwargs["points"][0]
    pl = point.payload
    assert pl["point_kind"] == "entity"
    assert pl["entity_type"] == "organization"  # canonicalized from "org"
    assert pl["org_ids"] == ["empirica-foundation"] and pl["contact_ids"] == []  # self-ref
    assert pl["status"] == "active" and pl["project_id"] == "workspace"
    assert point.id == wi._entity_point_id("org", "empirica-foundation")  # stable


def test_writer_returns_false_when_qdrant_unavailable():
    with patch.object(wi, "_check_qdrant_available", return_value=False):
        assert wi.embed_entity_to_workspace_index("contact", "c-1", "X") is False


def test_writer_returns_false_when_embedding_none():
    client = MagicMock()
    with contextlib.ExitStack() as stack:
        for p in _patches(client):
            stack.enter_context(p)
        with patch.object(wi, "_get_embedding_safe", return_value=None):
            assert wi.embed_entity_to_workspace_index("contact", "c-1", "X") is False
        client.upsert.assert_not_called()


# ── mocked Qdrant: search filter construction ─────────────────────────────────


def _run_search(**kwargs):
    """Run search with a mocked client; return the query_filter it built."""
    client = MagicMock()
    client.collection_exists.return_value = True
    client.query_points.return_value = SimpleNamespace(points=[])
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(wi, "_check_qdrant_available", return_value=True))
        stack.enter_context(patch.object(wi, "_get_qdrant_client", return_value=client))
        stack.enter_context(patch.object(wi, "_get_embedding_safe", return_value=[0.1] * 8))
        wi.search_workspace_index(query_text="q", **kwargs)
    return client.query_points.call_args.kwargs["query_filter"]


def _keys(conds):
    return [c.key for c in (conds or [])]


def test_search_entity_kind_adds_must():
    f = _run_search(point_kind="entity")
    assert "point_kind" in _keys(f.must)
    assert "status" in _keys(f.must)  # default status=active applied for entity


def test_search_artifact_kind_uses_must_not():
    f = _run_search(point_kind="artifact")
    assert "point_kind" in _keys(f.must_not)
    # status NOT applied for artifact points (they lack the field)
    assert "status" not in _keys(f.must)


def test_search_status_none_omits_status_filter():
    f = _run_search(point_kind="entity", status=None)
    assert "status" not in _keys(f.must)


def test_search_entity_type_only_filters_by_type():
    f = _run_search(point_kind="entity", entity_type="org")
    keys = _keys(f.must)
    assert "entity_type" in keys
    et_cond = next(c for c in f.must if c.key == "entity_type")
    assert et_cond.match.value == "organization"  # canonicalized


def test_search_both_kinds_no_point_kind_condition():
    # point_kind=None (unified) → no point_kind must/must_not, no status filter
    f = _run_search(point_kind=None)
    assert f is None or ("point_kind" not in _keys(f.must) and "point_kind" not in _keys(f.must_not))
