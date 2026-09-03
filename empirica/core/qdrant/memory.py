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
from empirica.core.qdrant.point_ids import artifact_point_id


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

        point_id = artifact_point_id(item_id)

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
                point_id = artifact_point_id(raw_id)
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


#: How many neighbours to fetch per collection to CALIBRATE the lexical check.
#:
#: Not for reranking — reranking on the lexical signal was measured to cost recall
#: and was dropped (see `lexical.py`). The depth exists because the confirmation
#: metric weights each query token by how rare it is *among the candidates*, and
#: that estimate from five documents is noise. Fifty gives it something to measure
#: against; the returned rows are still the dense top-`limit`, unreordered.
#:
#: It is emphatically NOT the same move as raising `limit`. Cortex measured that
#: raising the RETURNED count makes the confabulation bigger, not smaller, and core
#: measured what it would buy: off-phrasing recall goes 2/10 at limit=5 to 4/10 at
#: limit=30 to 6/10 at limit=100 — nobody reads a hundred results for four more
#: answers.
_CANDIDATE_DEPTH = 50

#: Which payload field carries the human-meaningful text of each band, for lexical
#: confirmation. A band absent here is reranked on dense score alone rather than
#: silently scoring zero lexical — an unlisted band would otherwise become
#: permanently unconfirmable, which reads as "nothing here matched" forever.
_TEXT_FIELDS = {
    "docs": ("doc_path", "concepts", "tags"),
    "memory": ("text",),
    "eidetic": ("content", "domain"),
    "episodic": ("narrative", "outcome"),
    "assumptions": ("assumption", "domain"),
    "decisions": ("choice", "rationale"),
    "goals": ("objective",),
}


def _band_text(fields: tuple[str, ...]):
    """Build the text extractor for one band. Joins because several bands carry
    their meaning across two fields — a decision is its choice AND its rationale,
    and matching only the title would miss the half that explains it."""

    def extract(item: dict) -> str:
        parts = []
        for f in fields:
            v = item.get(f)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, (list, tuple)):
                parts.extend(str(x) for x in v)
        return " ".join(parts)

    return extract


def search(
    project_id: str, query_text: str, kind: str = "focused", limit: int = 5, qdrant_url: str | None = None
) -> dict[str, list[dict]]:
    """
    Semantic search over project knowledge.

    Args:
        project_id: Project UUID
        query_text: Search query
        kind: "focused" (docs + memory + eidetic + episodic), "all", "intelligence",
              or single collection name
        limit: Max results per collection
        qdrant_url: optional per-request Qdrant URL (per-org routing); None = default resolution

    Returns empty results if Qdrant not available.

    kind values:
        "focused" — docs + memory + eidetic + episodic (default, for local context)
        "all" — identical to "focused"; retained as a backward-compat alias
        "intelligence" — memory + eidetic + episodic + assumptions + decisions + goals
                         (skips docs, designed for Cortex cross-project queries)
        single name — "docs", "memory", "eidetic", "episodic", "assumptions", "decisions", "goals"

    ``memory`` was missing from ``focused`` until 2026-07-27, and ``focused`` is the
    CLI default. ``memory`` is where ``finding-log`` / ``decision-log`` /
    ``mistake-log`` / ``deadend-log`` write — so a practice could not retrieve its own
    artifacts by searching its own project. The write path worked, the default read
    path could not see it, and nothing failed loudly: the ``memory`` key was absent
    from the result dict entirely, so the renderer's memory band simply never fired.

    Reported by cortex after it cost three weeks on a client pipeline — they searched
    their own project for a finding they had logged themselves, got generic module
    summaries, and began re-deriving it from code. Only ``--global`` returned it, and
    ``--global`` reads a PROMOTED SUBSET (capped per POSTFLIGHT, impact >= 0.7), so
    everything below that bar was unreachable from the project that wrote it.

    That ``_COLLECTION_BOOST`` already weighted ``memory`` at 1.2 — second only to
    ``decisions`` — is the tell: nobody tunes a relevance boost for a collection the
    default path never queries. The omission was a bug, not a cost tradeoff.

    Consequence, stated so it is not rediscovered: ``focused`` and ``all`` are now the
    same set. That is the intended end state — ``all`` was already documented as
    backward compat — not an accidental collision.
    """
    # "all" is a backward-compat alias of "focused". Sharing one branch keeps them
    # from drifting apart again — the original bug was precisely that "all" carried
    # `memory` and the DEFAULT did not.
    if kind in ("focused", "all"):
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
        # `artifact_id` is the DB row id and it was NOT projected, so callers got
        # semantically-ranked results they could not join back to anything. It
        # sits in the payload; only the projection dropped it. Same field-omission
        # shape as goals-list not SELECTing description — the data was always
        # there, the reader just never asked for it.
        "memory": (
            _memory_collection,
            ["type", "text", "session_id", "goal_id", "timestamp", "impact", "artifact_id"],
        ),
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
        candidates = _search_single_collection(
            client,
            coll_fn,
            project_id,
            query_text,
            fields,
            boost,
            max(limit, _CANDIDATE_DEPTH),
            query_filter,
        )
        results[kind_name] = _confirm_band(kind_name, query_text, candidates, limit)

    if results:
        return results

    # REST fallback
    logger.debug("Trying REST fallback for search")
    try:
        for kind_name in search_kinds:
            if kind_name not in _SEARCH_COLLECTIONS:
                continue
            coll_fn, fields = _SEARCH_COLLECTIONS[kind_name]
            candidates = _rest_search_collection(
                client,
                coll_fn,
                project_id,
                query_text,
                fields,
                max(limit, _CANDIDATE_DEPTH),
                qdrant_url=qdrant_url,
            )
            # Same rerank as the client path. A fallback that quietly returns
            # unconfirmed rows would make the confirmation signal depend on which
            # transport happened to answer — and the transport is invisible to the
            # caller, so the inconsistency would be unattributable.
            results[kind_name] = _confirm_band(kind_name, query_text, candidates, limit)
        return results
    except Exception as e:
        logger.debug(f"REST search also failed: {e}")
        return empty_result


def _confirm_band(kind_name: str, query_text: str, candidates: list[dict], limit: int) -> list[dict]:
    """Annotate the dense top-`limit` with lexical confirmation, calibrated on the
    full candidate pool. Dense order is preserved.

    Non-raising by design: retrieval that returned five rows must not start
    returning an error because the annotator hit something unexpected. The
    degradation is LOGGED and the rows are left WITHOUT a `confirmed` field rather
    than stamped `False` — "the signal is unavailable" and "the signal says nothing
    matched" are opposite instructions to a caller, and collapsing them would
    reintroduce the defect one layer up.
    """
    text_fields = _TEXT_FIELDS.get(kind_name)
    if not text_fields:
        return candidates[:limit]
    try:
        from empirica.core.qdrant.lexical import annotate

        # Calibrate over the whole pool, return the dense top-`limit`. Annotating
        # only the five returned rows would compute rarity from five documents,
        # which is the estimate this depth exists to avoid.
        annotate(query_text, candidates, _band_text(text_fields))
        return candidates[:limit]
    except Exception as e:
        logger.warning(f"lexical confirmation unavailable for band {kind_name}: {e}")
        return candidates[:limit]


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
