"""
Core memory operations: embed, upsert, and search for memory items and docs.
"""

from __future__ import annotations

from empirica.core.qdrant.collections import (
    _assumptions_collection,
    _decisions_collection,
    _docs_collection,
    _eidetic_collection,
    _episodic_collection,
    _goals_collection,
    _memory_collection,
)
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_for_collection,
    _get_embeddings_batch_for_collection,
    _get_qdrant_client,
    _get_qdrant_imports,
    _rest_search,
    logger,
)


def embed_single_memory_item(
    project_id: str,
    item_id: str,
    text: str,
    item_type: str,
    session_id: str | None = None,
    goal_id: str | None = None,
    subtask_id: str | None = None,
    subject: str | None = None,
    impact: float | None = None,
    is_resolved: bool | None = None,
    resolved_by: str | None = None,
    timestamp: str | None = None,
    qdrant_url: str | None = None,
) -> bool:
    """
    Embed a single memory item (finding, unknown, mistake, dead_end) to Qdrant.
    Called automatically when logging epistemic breadcrumbs.

    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.

    Returns True if successful, False if Qdrant not available or embedding failed.
    This is a non-blocking operation - core Empirica works without it.
    """
    # Check if Qdrant is available (graceful degradation)
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return False

    try:
        _, _, _, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return False
        coll = _memory_collection(project_id)

        vector = _get_embedding_for_collection(client, coll, text, create_if_missing=True)
        if vector is None:
            return False

        payload = {
            "artifact_id": item_id,
            "type": item_type,
            "text": text[:500] if text else None,
            "text_full": text if len(text) <= 500 else None,
            "session_id": session_id,
            "goal_id": goal_id,
            "subtask_id": subtask_id,
            "subject": subject,
            "impact": impact,
            "is_resolved": is_resolved,
            "resolved_by": resolved_by,
            "timestamp": timestamp,
        }

        # Use hash of item_id for numeric Qdrant point ID
        import hashlib

        point_id = int(hashlib.md5(item_id.encode()).hexdigest()[:15], 16)

        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        # Log but don't fail - embedding is enhancement, not critical path
        import logging

        logging.getLogger(__name__).warning(f"Failed to embed memory item: {e}")
        return False


def upsert_docs(project_id: str, docs: list[dict], qdrant_url: str | None = None) -> int:
    """
    Upsert documentation embeddings.
    docs: List of {id, text, metadata:{doc_path, tags, concepts, questions, use_cases}}
    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    Returns number of docs upserted, or 0 if Qdrant not available.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return 0

    try:
        _, _, _, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return 0
        coll = _docs_collection(project_id)

        # Batch embed texts (chunked to avoid API payload limits / per-batch timeouts).
        # Without chunking, a single POST of N×~1200-char prompts can exceed the
        # provider's read timeout on slower local embedders (e.g. CPU Ollama).
        import os

        texts = [d.get("text", "") for d in docs]
        embed_batch_size = int(os.environ.get("EMPIRICA_EMBED_BATCH_SIZE", "50"))
        vectors = []
        create_if_missing = True  # only on first batch; subsequent batches reuse the collection
        for i in range(0, len(texts), embed_batch_size):
            batch_texts = texts[i : i + embed_batch_size]
            batch_vectors = _get_embeddings_batch_for_collection(
                client,
                coll,
                batch_texts,
                create_if_missing=create_if_missing,
            )
            vectors.extend(batch_vectors)
            create_if_missing = False

        points = []
        for d, vector in zip(docs, vectors):
            if vector is None:
                continue
            payload = {
                "doc_path": d.get("metadata", {}).get("doc_path"),
                "tags": d.get("metadata", {}).get("tags", []),
                "concepts": d.get("metadata", {}).get("concepts", []),
                "questions": d.get("metadata", {}).get("questions", []),
                "use_cases": d.get("metadata", {}).get("use_cases", []),
            }
            points.append(PointStruct(id=d["id"], vector=vector, payload=payload))
        if points:
            client.upsert(collection_name=coll, points=points)
        return len(points)
    except Exception as e:
        logger.warning(f"Failed to upsert docs: {e}")
        return 0


def upsert_memory(project_id: str, items: list[dict], qdrant_url: str | None = None) -> int:
    """
    Upsert memory embeddings (findings, unknowns, mistakes, dead_ends).
    items: List of {id, text, type, goal_id, subtask_id, session_id, timestamp, ...}
    qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution.
    Returns number of items upserted, or 0 if Qdrant not available.
    """
    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return 0

    try:
        _, _, _, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
        if client is None:
            return 0
        coll = _memory_collection(project_id)

        # Batch embed texts (chunked to avoid API payload limits)
        import hashlib
        import os

        texts = [it.get("text", "") for it in items]
        # batch size tunable for slower local embedders (Intel Mac + Ollama: use 10)
        embed_batch_size = int(os.environ.get("EMPIRICA_EMBED_BATCH_SIZE", "50"))
        vectors = []
        for i in range(0, len(texts), embed_batch_size):
            batch_texts = texts[i : i + embed_batch_size]
            batch_vectors = _get_embeddings_batch_for_collection(
                client,
                coll,
                batch_texts,
                create_if_missing=not client.collection_exists(coll),
            )
            vectors.extend(batch_vectors)

        points = []
        for it, vector in zip(items, vectors):
            if vector is None:
                continue
            text = it.get("text", "")
            # Extract source file refs for provenance in search results
            source_files = None
            try:
                from empirica.utils.finding_refs import parse_file_references

                file_refs = parse_file_references(text)
                if file_refs:
                    source_files = [r["file"] for r in file_refs]
            except Exception:
                pass

            payload = {
                "artifact_id": it.get("id"),
                "type": it.get("type", "unknown"),
                "text": text[:500] if text else None,
                "text_full": text if len(text) <= 500 else None,
                "goal_id": it.get("goal_id"),
                "subtask_id": it.get("subtask_id"),
                "session_id": it.get("session_id"),
                "timestamp": it.get("timestamp"),
                "subject": it.get("subject"),
                "impact": it.get("impact"),
                "is_resolved": it.get("is_resolved"),
                "resolved_by": it.get("resolved_by"),
                "source_files": source_files,
            }
            raw_id = it["id"]
            if isinstance(raw_id, str):
                point_id = int(hashlib.md5(raw_id.encode()).hexdigest()[:15], 16)
            else:
                point_id = raw_id
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))
        if points:
            # Batch upserts to stay under Qdrant's payload size limit (32MB)
            batch_size = 200
            for i in range(0, len(points), batch_size):
                batch = points[i : i + batch_size]
                client.upsert(collection_name=coll, points=batch)
        return len(points)
    except Exception as e:
        logger.warning(f"Failed to upsert memory: {e}")
        return 0


def search(
    project_id: str, query_text: str, kind: str = "focused", limit: int = 5, qdrant_url: str | None = None
) -> dict[str, list[dict]]:
    """
    Semantic search over project knowledge.

    Args:
        project_id: Project UUID
        query_text: Search query
        kind: "focused" (docs + eidetic + episodic), "all", "intelligence", or single collection name
        limit: Max results per collection
        qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution

    Returns empty results if Qdrant not available.

    kind values:
        "focused" — docs + eidetic + episodic (default, for local context)
        "all" — docs + memory + eidetic + episodic (backward compat)
        "intelligence" — memory + eidetic + episodic + assumptions + decisions + goals
                         (skips docs, designed for Cortex cross-project queries)
        single name — "docs", "memory", "eidetic", "episodic", "assumptions", "decisions", "goals"
    """
    if kind == "focused":
        search_kinds = ["docs", "eidetic", "episodic"]
    elif kind == "all":
        search_kinds = ["docs", "memory", "eidetic", "episodic"]
    elif kind == "intelligence":
        search_kinds = ["memory", "eidetic", "episodic", "assumptions", "decisions", "goals"]
    else:
        search_kinds = [kind]
    empty_result = {k: [] for k in search_kinds}

    if not _check_qdrant_available(qdrant_url=qdrant_url, project_id=project_id):
        return empty_result

    # Collection config: (name, collection_fn, payload_fields)
    _SEARCH_COLLECTIONS = {
        "docs": (_docs_collection, ["doc_path", "tags", "concepts"]),
        "memory": (_memory_collection, ["type", "text", "session_id", "goal_id", "timestamp", "impact"]),
        "eidetic": (_eidetic_collection, ["type", "content", "confidence", "domain", "created_at", "first_seen"]),
        "episodic": (_episodic_collection, ["type", "narrative", "session_id", "outcome", "created_at", "timestamp"]),
        "assumptions": (
            _assumptions_collection,
            ["assumption", "confidence", "status", "domain", "created_at", "timestamp"],
        ),
        "decisions": (_decisions_collection, ["choice", "rationale", "reversibility", "created_at", "timestamp"]),
        "goals": (_goals_collection, ["objective", "status", "scope", "created_at"]),
    }

    # Boost weights per collection type — findings/decisions score higher than code docs
    _COLLECTION_BOOST = {
        "decisions": 1.3,
        "memory": 1.2,
        "assumptions": 1.1,
        "eidetic": 1.0,
        "episodic": 0.9,
        "goals": 0.8,
        "docs": 0.5,
    }

    # For intelligence searches, filter out code_api entries from eidetic
    # (module doc signatures are 52% of eidetic — noise for cross-project queries)
    _intelligence_filter = None
    if kind == "intelligence":
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            _intelligence_filter = Filter(must_not=[FieldCondition(key="type", match=MatchValue(value="code_api"))])
        except ImportError:
            pass

    results: dict[str, list[dict]] = {}
    client = _get_qdrant_client(qdrant_url=qdrant_url, project_id=project_id)
    if client is None:
        return empty_result

    # Query each collection via client
    for kind_name in search_kinds:
        if kind_name not in _SEARCH_COLLECTIONS:
            continue
        coll_fn, fields = _SEARCH_COLLECTIONS[kind_name]
        boost = _COLLECTION_BOOST.get(kind_name, 1.0)
        query_filter = _intelligence_filter if (kind_name == "eidetic" and _intelligence_filter) else None
        results[kind_name] = _search_single_collection(
            client,
            coll_fn,
            project_id,
            query_text,
            fields,
            boost,
            limit,
            query_filter,
        )

    if results:
        return results

    # REST fallback
    logger.debug("Trying REST fallback for search")
    try:
        for kind_name in search_kinds:
            if kind_name not in _SEARCH_COLLECTIONS:
                continue
            coll_fn, fields = _SEARCH_COLLECTIONS[kind_name]
            results[kind_name] = _rest_search_collection(
                client,
                coll_fn,
                project_id,
                query_text,
                fields,
                limit,
                qdrant_url=qdrant_url,
            )
        return results
    except Exception as e:
        logger.debug(f"REST search also failed: {e}")
        return empty_result


def _search_single_collection(client, coll_fn, project_id, query_text, fields, boost, limit, query_filter):
    """Search a single Qdrant collection via client, returning formatted results."""
    try:
        coll_name = coll_fn(project_id)
        if not client.collection_exists(coll_name):
            return []
        qvec = _get_embedding_for_collection(client, coll_name, query_text, create_if_missing=False)
        if qvec is None:
            return []
        resp = client.query_points(
            collection_name=coll_name, query=qvec, limit=limit, with_payload=True, query_filter=query_filter
        )
        return [
            {"score": (getattr(r, "score", 0.0) or 0.0) * boost, **{f: (r.payload or {}).get(f) for f in fields}}
            for r in resp.points
        ]
    except Exception as e:
        logger.debug(f"collection query failed: {e}")
        return []


def _rest_search_collection(client, coll_fn, project_id, query_text, fields, limit, qdrant_url=None):
    """Search a single collection via REST fallback."""
    coll_name = coll_fn(project_id)
    if client.collection_exists(coll_name):
        qvec = _get_embedding_for_collection(client, coll_name, query_text, create_if_missing=False)
    else:
        qvec = None
    if qvec is None:
        return []
    raw = _rest_search(coll_name, qvec, limit, qdrant_url=qdrant_url)
    return [{"score": d.get("score", 0.0), **{f: (d.get("payload") or {}).get(f) for f in fields}} for d in raw]
