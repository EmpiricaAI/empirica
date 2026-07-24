"""
Epistemic Intent Layer: assumptions, decisions, and intent edges.
Forward-compatible collections (populated when CLI commands exist).
"""

from __future__ import annotations

from empirica.core.qdrant.collections import (
    _assumptions_collection,
    _decisions_collection,
    _intents_collection,
)
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)


def embed_assumption(
    project_id: str,
    assumption_id: str,
    assumption: str,
    confidence: float = 0.5,
    status: str = "unverified",
    resolution_finding_id: str | None = None,
    entity_type: str = "project",
    entity_id: str | None = None,
    session_id: str | None = None,
    transaction_id: str | None = None,
    domain: str | None = None,
    timestamp: float | None = None,
    qdrant_url: str | None = None,
) -> bool:
    """Embed an assumption (unverified belief) for semantic search.

    Assumptions have an urgency_signal that increases with age for unverified
    items: older unverified = higher risk. Resolved assumptions: urgency=0.

    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return False

    try:
        import hashlib
        import time as _time

        _, _, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return False
        coll = _assumptions_collection(project_id)

        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            from qdrant_client.models import Distance

            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        ts = timestamp or _time.time()
        # Urgency: increases with age for unverified assumptions
        urgency = 0.0
        if status == "unverified":
            age_days = (_time.time() - ts) / 86400
            urgency = min(1.0, (age_days / 30.0) * (1.0 - confidence))

        vector = _get_embedding_safe(assumption)
        if vector is None:
            return False

        payload = {
            "artifact_id": assumption_id,
            "type": "assumption",
            "assumption": assumption[:500],
            "assumption_full": assumption if len(assumption) <= 500 else None,
            "confidence": confidence,
            "status": status,
            "resolution_finding_id": resolution_finding_id,
            "entity_type": entity_type,
            "entity_id": entity_id or project_id,
            "session_id": session_id,
            "transaction_id": transaction_id,
            "domain": domain,
            "created_at": ts,
            "timestamp": ts,
            "urgency_signal": urgency,
        }

        point_id = int(hashlib.md5(assumption_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed assumption: {e}")
        return False


def embed_decision(
    project_id: str,
    decision_id: str,
    choice: str,
    rationale: str,
    alternatives: str | None = None,
    confidence_at_decision: float | None = None,
    reversibility: str = "committal",
    entity_type: str = "project",
    entity_id: str | None = None,
    session_id: str | None = None,
    transaction_id: str | None = None,
    timestamp: float | None = None,
    qdrant_url: str | None = None,
) -> bool:
    """Embed a decision (recorded choice point) for semantic search.

    Decisions are permanent audit trail — no decay applied.

    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return False

    try:
        import hashlib
        import time as _time

        _, _, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return False
        coll = _decisions_collection(project_id)

        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            from qdrant_client.models import Distance

            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        # Rich text for embedding: choice + rationale + alternatives
        embed_text = f"{choice}. Rationale: {rationale}"
        if alternatives:
            embed_text += f". Alternatives: {alternatives}"

        vector = _get_embedding_safe(embed_text)
        if vector is None:
            return False

        payload = {
            "artifact_id": decision_id,
            "type": "decision",
            "choice": choice[:500],
            "choice_full": choice if len(choice) <= 500 else None,
            "rationale": rationale[:500] if rationale else None,
            "alternatives": alternatives,
            "confidence_at_decision": confidence_at_decision,
            "reversibility": reversibility,
            "entity_type": entity_type,
            "entity_id": entity_id or project_id,
            "session_id": session_id,
            "transaction_id": transaction_id,
            "created_at": timestamp or _time.time(),
            "timestamp": timestamp or _time.time(),
        }

        point_id = int(hashlib.md5(decision_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed decision: {e}")
        return False


def embed_intent_edge(
    project_id: str,
    intent_id: str,
    direction: str,
    source_artifact_id: str,
    source_artifact_type: str,
    target_artifact_id: str,
    target_artifact_type: str,
    confidence_at_crossing: float,
    reversibility: str = "exploratory",
    cascade_phase: str = "check",
    reasoning: str | None = None,
    vectors_snapshot: dict | None = None,
    entity_type: str = "project",
    entity_id: str | None = None,
    session_id: str | None = None,
    transaction_id: str | None = None,
    timestamp: float | None = None,
    qdrant_url: str | None = None,
) -> bool:
    """Embed an IntentEdge (provenance graph: noetic↔praxic transform).

    IntentEdges are permanent provenance — no decay. Retrieval ranks by recency.

    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return False

    try:
        import hashlib
        import json as _json
        import time as _time

        _, _, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return False
        coll = _intents_collection(project_id)

        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            from qdrant_client.models import Distance

            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        # Rich text for semantic search over intent reasoning
        embed_text = (
            f"{direction} intent: {reasoning or 'no reasoning provided'}. "
            f"{source_artifact_type} → {target_artifact_type} at confidence {confidence_at_crossing:.2f}"
        )

        vector = _get_embedding_safe(embed_text)
        if vector is None:
            return False

        payload = {
            "type": "intent_edge",
            "direction": direction,
            "source_artifact_id": source_artifact_id,
            "source_artifact_type": source_artifact_type,
            "target_artifact_id": target_artifact_id,
            "target_artifact_type": target_artifact_type,
            "confidence_at_crossing": confidence_at_crossing,
            "reversibility": reversibility,
            "cascade_phase": cascade_phase,
            "reasoning": reasoning[:500] if reasoning else None,
            "vectors_snapshot": _json.dumps(vectors_snapshot) if vectors_snapshot else None,
            "entity_type": entity_type,
            "entity_id": entity_id or project_id,
            "session_id": session_id,
            "transaction_id": transaction_id,
            "timestamp": timestamp or _time.time(),
        }

        point_id = int(hashlib.md5(intent_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed intent edge: {e}")
        return False


# --- Search functions for Intent Layer artifacts ---


def search_assumptions(
    project_id: str,
    query: str,
    status: str | None = None,
    entity_type: str | None = None,
    min_urgency: float = 0.0,
    limit: int = 5,
    qdrant_url: str | None = None,
) -> list[dict]:
    """Search assumptions by semantic similarity with optional filters.

    Args:
        status: Filter by 'unverified', 'verified', 'falsified'
        entity_type: Filter by entity type
        min_urgency: Minimum urgency_signal threshold (0.0-1.0)
        qdrant_url: optional per-request Qdrant URL (per-org routing)
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return []

    try:
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return []
        coll = _assumptions_collection(project_id)

        if not client.collection_exists(coll):
            return []

        vector = _get_embedding_safe(query)
        if vector is None:
            return []

        conditions = []
        if status:
            from qdrant_client.models import FieldCondition, MatchValue

            conditions.append(FieldCondition(key="status", match=MatchValue(value=status)))
        if entity_type:
            from qdrant_client.models import FieldCondition, MatchValue

            conditions.append(FieldCondition(key="entity_type", match=MatchValue(value=entity_type)))

        query_filter = None
        if conditions:
            from qdrant_client.models import Filter

            query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=coll,
            query=vector,
            query_filter=query_filter,
            limit=limit * 2 if min_urgency > 0 else limit,
            with_payload=True,
        )

        items = [
            {
                "assumption": r.payload.get("assumption", ""),
                "confidence": r.payload.get("confidence", 0.5),
                "status": r.payload.get("status", "unverified"),
                "urgency_signal": r.payload.get("urgency_signal", 0.0),
                "domain": r.payload.get("domain"),
                "entity_type": r.payload.get("entity_type"),
                "entity_id": r.payload.get("entity_id"),
                "score": r.score,
            }
            for r in results.points
        ]

        # Filter by urgency threshold
        if min_urgency > 0:
            items = [i for i in items if (i["urgency_signal"] or 0) >= min_urgency]

        return items[:limit]
    except Exception as e:
        logger.warning(f"Failed to search assumptions: {e}")
        return []


def search_decisions(
    project_id: str,
    query: str,
    reversibility: str | None = None,
    entity_type: str | None = None,
    limit: int = 5,
    qdrant_url: str | None = None,
) -> list[dict]:
    """Search decisions by semantic similarity with optional filters.

    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return []

    try:
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return []
        coll = _decisions_collection(project_id)

        if not client.collection_exists(coll):
            return []

        vector = _get_embedding_safe(query)
        if vector is None:
            return []

        conditions = []
        if reversibility:
            from qdrant_client.models import FieldCondition, MatchValue

            conditions.append(FieldCondition(key="reversibility", match=MatchValue(value=reversibility)))
        if entity_type:
            from qdrant_client.models import FieldCondition, MatchValue

            conditions.append(FieldCondition(key="entity_type", match=MatchValue(value=entity_type)))

        query_filter = None
        if conditions:
            from qdrant_client.models import Filter

            query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=coll,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "choice": r.payload.get("choice", ""),
                "rationale": r.payload.get("rationale", ""),
                "alternatives": r.payload.get("alternatives"),
                "confidence_at_decision": r.payload.get("confidence_at_decision"),
                "reversibility": r.payload.get("reversibility"),
                "entity_type": r.payload.get("entity_type"),
                "score": r.score,
            }
            for r in results.points
        ]
    except Exception as e:
        logger.warning(f"Failed to search decisions: {e}")
        return []


def search_intents(
    project_id: str,
    query: str,
    direction: str | None = None,
    cascade_phase: str | None = None,
    limit: int = 5,
    qdrant_url: str | None = None,
) -> list[dict]:
    """Search IntentEdges by semantic similarity with optional filters.

    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return []

    try:
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return []
        coll = _intents_collection(project_id)

        if not client.collection_exists(coll):
            return []

        vector = _get_embedding_safe(query)
        if vector is None:
            return []

        conditions = []
        if direction:
            from qdrant_client.models import FieldCondition, MatchValue

            conditions.append(FieldCondition(key="direction", match=MatchValue(value=direction)))
        if cascade_phase:
            from qdrant_client.models import FieldCondition, MatchValue

            conditions.append(FieldCondition(key="cascade_phase", match=MatchValue(value=cascade_phase)))

        query_filter = None
        if conditions:
            from qdrant_client.models import Filter

            query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=coll,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "direction": r.payload.get("direction"),
                "reasoning": r.payload.get("reasoning", ""),
                "source_artifact_type": r.payload.get("source_artifact_type"),
                "target_artifact_type": r.payload.get("target_artifact_type"),
                "confidence_at_crossing": r.payload.get("confidence_at_crossing"),
                "reversibility": r.payload.get("reversibility"),
                "cascade_phase": r.payload.get("cascade_phase"),
                "score": r.score,
            }
            for r in results.points
        ]
    except Exception as e:
        logger.warning(f"Failed to search intents: {e}")
        return []


# =============================================================================
# NOETIC RAG: Decay & Cross-Layer Sync Functions
# =============================================================================
