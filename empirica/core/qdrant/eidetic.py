"""
Eidetic memory: stable facts with confidence scoring.
"""
from __future__ import annotations

from empirica.core.qdrant.collections import _eidetic_collection
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)


def embed_eidetic(
    project_id: str,
    fact_id: str,
    content: str,
    fact_type: str = "fact",
    domain: str | None = None,
    confidence: float = 0.5,
    confirmation_count: int = 1,
    source_sessions: list[str] | None = None,
    source_findings: list[str] | None = None,
    tags: list[str] | None = None,
    timestamp: str | None = None,
) -> bool:
    """
    Embed an eidetic memory entry (stable fact with confidence).

    Eidetic memory stores facts that persist across sessions:
    - Facts confirmed multiple times have higher confidence
    - Confidence grows with confirmation_count
    - Domain tagging enables domain-specific retrieval

    Returns True if successful, False if Qdrant not available.
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _eidetic_collection(project_id)

        # Ensure collection exists
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        vector = _get_embedding_safe(content)
        if vector is None:
            return False

        import hashlib
        import time

        payload = {
            "type": fact_type,  # fact, pattern, signature, behavior, constraint
            "content": content[:500] if content else None,
            "content_full": content if len(content) <= 500 else None,
            "content_hash": hashlib.md5(content.encode()).hexdigest(),
            "domain": domain,
            "confidence": confidence,
            "confirmation_count": confirmation_count,
            "first_seen": timestamp or time.time(),
            "last_confirmed": timestamp or time.time(),
            "source_sessions": source_sessions or [],
            "source_findings": source_findings or [],
            "tags": tags or [],
        }

        point_id = int(hashlib.md5(fact_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed eidetic: {e}")
        return False


def search_eidetic(
    project_id: str,
    query: str,
    fact_type: str | None = None,
    domain: str | None = None,
    min_confidence: float = 0.0,
    limit: int = 5,
) -> list[dict]:
    """
    Search eidetic memory for relevant facts.

    Args:
        project_id: Project UUID
        query: Semantic search query
        fact_type: Filter by type (fact, pattern, signature, etc.)
        domain: Filter by domain (auth, api, db, etc.)
        min_confidence: Minimum confidence threshold
        limit: Max results

    Returns:
        List of matching eidetic entries with scores
    """
    if not _check_qdrant_available():
        return []

    try:
        client = _get_qdrant_client()
        if client is None:
            return []
        coll = _eidetic_collection(project_id)

        if not client.collection_exists(coll):
            return []

        vector = _get_embedding_safe(query)
        if vector is None:
            return []

        # Build filter conditions
        from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

        conditions = []
        if fact_type:
            conditions.append(FieldCondition(key="type", match=MatchValue(value=fact_type)))
        if domain:
            conditions.append(FieldCondition(key="domain", match=MatchValue(value=domain)))
        if min_confidence > 0:
            conditions.append(FieldCondition(key="confidence", range=Range(gte=min_confidence)))

        query_filter = Filter(must=conditions) if conditions else None

        results = client.query_points(
            collection_name=coll,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "id": str(r.id),
                "score": r.score,
                "content": r.payload.get("content_full") or r.payload.get("content"),
                "content_hash": r.payload.get("content_hash"),
                "type": r.payload.get("type"),
                "domain": r.payload.get("domain"),
                "confidence": r.payload.get("confidence"),
                "confirmation_count": r.payload.get("confirmation_count"),
                "source_sessions": r.payload.get("source_sessions", []),
                "tags": r.payload.get("tags", []),
                # creation time (unix or ISO) for read-time recency ranking
                "first_seen": r.payload.get("first_seen"),
                "last_confirmed": r.payload.get("last_confirmed"),
            }
            for r in results.points
        ]
    except Exception as e:
        logger.warning(f"Failed to search eidetic: {e}")
        return []


def confirm_eidetic_fact(
    project_id: str,
    content_hash: str,
    session_id: str,
    confidence_boost: float = 0.1,
) -> bool:
    """
    Confirm an existing eidetic fact, boosting its confidence.

    When the same fact is observed again, we boost confidence
    rather than creating a duplicate.

    Args:
        project_id: Project UUID
        content_hash: MD5 hash of the fact content
        session_id: Session confirming this fact
        confidence_boost: Amount to increase confidence (default 0.1)

    Returns:
        True if fact was found and updated, False otherwise
    """
    if not _check_qdrant_available():
        return False

    try:
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _eidetic_collection(project_id)

        if not client.collection_exists(coll):
            return False

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        # Find existing fact by content hash
        results = client.scroll(
            collection_name=coll,
            scroll_filter=Filter(
                must=[FieldCondition(key="content_hash", match=MatchValue(value=content_hash))]
            ),
            limit=1,
            with_payload=True,
            with_vectors=True,
        )

        points, _ = results
        if not points:
            return False

        point = points[0]
        payload = point.payload

        # Update confidence (max 0.95)
        new_confidence = min(0.95, payload.get("confidence", 0.5) + confidence_boost)

        # Update confirmation count
        new_count = payload.get("confirmation_count", 1) + 1

        # Add session to source list
        sessions = payload.get("source_sessions", [])
        if session_id not in sessions:
            sessions.append(session_id)

        import time
        payload["confidence"] = new_confidence
        payload["confirmation_count"] = new_count
        payload["source_sessions"] = sessions
        payload["last_confirmed"] = time.time()

        from qdrant_client.models import PointStruct
        updated_point = PointStruct(id=point.id, vector=point.vector, payload=payload)
        client.upsert(collection_name=coll, points=[updated_point])

        logger.info(f"Confirmed eidetic fact: confidence {new_confidence:.2f}, confirmations {new_count}")
        return True
    except Exception as e:
        logger.warning(f"Failed to confirm eidetic fact: {e}")
        return False

