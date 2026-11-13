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
    Search epistemic learning trajectories by semantic similarity, keeping only results that match ``filters``: each key a
    payload path ("deltas.know"), each value either the exact value or a range ``{"$gte"|"$lte"|"$gt"|"$lt": n}`` (any other operator is refused). The filters were
    accepted and never applied (1.16.x); they are applied to the results, over-fetching so the limit still fills, which
    works the same on the client and the REST path.
    Returns empty list if Qdrant not available.
    """
    _check_filters(filters)  # an operator it does not know used to be ignored and matched everything (1.16.1 sweep)
    if not _check_qdrant_available():
        return []

    qvec = _get_embedding_safe(query_text)
    if qvec is None:
        return []
    fetch = limit if not filters else max(limit * 4, limit + 20)

    try:
        client = _get_qdrant_client(project_id=project_id)
        if client is None:
            return []
        coll = _epistemics_collection(project_id)
        results = client.query_points(collection_name=coll, query=qvec, limit=fetch, with_payload=True)
        found = [{"score": getattr(r, "score", 0.0) or 0.0, **(r.payload or {})} for r in results.points]
        return [r for r in found if _matches(r, filters)][:limit]
    except Exception as e:
        logger.debug(f"search_epistemics failed: {e}")

    # REST fallback
    try:
        coll = _epistemics_collection(project_id)
        rd = _rest_search(coll, qvec, fetch)
        found = [{"score": d.get("score", 0.0), **(d.get("payload") or {})} for d in rd]
        return [r for r in found if _matches(r, filters)][:limit]
    except Exception as e:
        logger.debug(f"search_epistemics REST fallback failed: {e}")
        return []


_OPERATORS = ("$gte", "$lte", "$gt", "$lt")


def _check_filters(filters: dict | None) -> None:
    """Refuse a range operator this module does not apply, rather than ignore it."""
    for path, want in (filters or {}).items():
        if isinstance(want, dict):
            unknown = [k for k in want if k not in _OPERATORS]
            if unknown:
                raise ValueError(f"filter {path!r}: unsupported operator(s) {unknown}; use {', '.join(_OPERATORS)}")


def _matches(payload: dict, filters: dict | None) -> bool:
    """Whether a result's payload satisfies every filter (see search_epistemics). A missing value does not match."""
    for path, want in (filters or {}).items():
        value: object = payload
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(want, dict):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            if "$gte" in want and value < want["$gte"]:
                return False
            if "$lte" in want and value > want["$lte"]:
                return False
            if "$gt" in want and value <= want["$gt"]:
                return False
            if "$lt" in want and value >= want["$lt"]:
                return False
        elif value != want:
            return False
    return True
