"""
Epistemic learning trajectory storage and search.
"""

from __future__ import annotations

import uuid

from empirica.core.qdrant.collections import _epistemics_collection
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _ensure_collection_matches_vector,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _rest_search,
    logger,
)


def upsert_epistemics(project_id: str, items: list[dict]) -> int:
    """
    Store epistemic learning trajectories (PREFLIGHT → POSTFLIGHT deltas).
    Returns number of items upserted, or 0 if Qdrant not available.
    """
    if not _check_qdrant_available():
        return 0

    try:
        _, _, _, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(project_id=project_id)
        if client is None:
            return 0
        coll = _epistemics_collection(project_id)
        points = []

        for item in items:
            vector = _get_embedding_safe(item.get("text", ""))
            if vector is None:
                continue
            payload = dict(item.get("metadata", {}))
            payload["item_id"] = str(item["id"])
            points.append(PointStruct(id=_point_id(item["id"]), vector=vector, payload=payload))

        if points:
            # Created on first write, like the other per-project collections (goal 6f1e9bb6: it never was, and every
            # upsert failed on the missing collection before it could fail on the id).
            _ensure_collection_matches_vector(client, coll, len(points[0].vector), create_if_missing=True)
            client.upsert(collection_name=coll, points=points)
        return len(points)
    except Exception as e:
        logger.warning(f"Failed to upsert epistemics: {e}")
        return 0


def _point_id(item_id) -> int | str:
    """A Qdrant point id for ``item_id``: an int or a UUID as it is, anything else (``session_<uuid>``) as a stable
    UUIDv5, so the same item replaces its point. Qdrant rejects any other string; the original rides in ``item_id``."""
    if isinstance(item_id, int):
        return item_id
    try:
        return str(uuid.UUID(str(item_id)))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"empirica:epistemics:{item_id}"))


def search_epistemics(project_id: str, query_text: str, filters: dict | None = None, limit: int = 5) -> list[dict]:
    """
    Search epistemic learning trajectories by semantic similarity.
    Returns empty list if Qdrant not available.
    """
    if not _check_qdrant_available():
        return []

    qvec = _get_embedding_safe(query_text)
    if qvec is None:
        return []

    try:
        client = _get_qdrant_client(project_id=project_id)
        if client is None:
            return []
        coll = _epistemics_collection(project_id)
        results = client.query_points(collection_name=coll, query=qvec, limit=limit, with_payload=True)
        return [{"score": getattr(r, "score", 0.0) or 0.0, **(r.payload or {})} for r in results.points]
    except Exception as e:
        logger.debug(f"search_epistemics failed: {e}")

    # REST fallback
    try:
        coll = _epistemics_collection(project_id)
        rd = _rest_search(coll, qvec, limit)
        return [{"score": d.get("score", 0.0), **(d.get("payload") or {})} for d in rd]
    except Exception as e:
        logger.debug(f"search_epistemics REST fallback failed: {e}")
        return []
