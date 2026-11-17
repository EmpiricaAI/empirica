"""
Qdrant collection naming, initialization, and migration utilities.
"""

from __future__ import annotations

# Public API — collection name functions are imported by vector_store, memory, etc.
__all__ = [
    "_assumptions_collection",
    "_calibration_collection",
    "_decisions_collection",
    "_docs_collection",
    "_eidetic_collection",
    "_episodic_collection",
    "_epistemics_collection",
    "_global_eidetic_collection",
    "_global_learnings_collection",
    "_goals_collection",
    "_intents_collection",
    "_memory_collection",
    "_workspace_index_collection",
    "init_collections",
    "project_collection_names",
]

from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)


def _docs_collection(project_id: str) -> str:
    return f"project_{project_id}_docs"


def _memory_collection(project_id: str) -> str:
    return f"project_{project_id}_memory"


def _epistemics_collection(project_id: str) -> str:
    """Collection for epistemic learning trajectories (PREFLIGHT → POSTFLIGHT deltas)"""
    return f"project_{project_id}_epistemics"


def _global_learnings_collection() -> str:
    """Global collection for high-impact learnings across all projects."""
    return "global_learnings"


def _eidetic_collection(project_id: str) -> str:
    """Collection for eidetic memory (stable facts with confidence scoring)."""
    return f"project_{project_id}_eidetic"


def _episodic_collection(project_id: str) -> str:
    """Collection for episodic memory (session narratives with temporal decay)."""
    return f"project_{project_id}_episodic"


def _global_eidetic_collection() -> str:
    """Global eidetic facts (high-confidence cross-project knowledge)."""
    return "global_eidetic"


def _goals_collection(project_id: str) -> str:
    """Collection for goals and subtasks (semantic search across sessions)."""
    return f"project_{project_id}_goals"


def _calibration_collection(project_id: str) -> str:
    """Collection for grounded calibration data (verification summaries + trajectory)."""
    return f"project_{project_id}_calibration"


# --- Forward-compatible collections for Epistemic Intent Layer ---


def _assumptions_collection(project_id: str) -> str:
    """Collection for assumptions (unverified beliefs with urgency decay)."""
    return f"project_{project_id}_assumptions"


def _decisions_collection(project_id: str) -> str:
    """Collection for decisions (recorded choice points with rationale)."""
    return f"project_{project_id}_decisions"


def _intents_collection(project_id: str) -> str:
    """Collection for IntentEdges (provenance graph: noetic↔praxic transforms)."""
    return f"project_{project_id}_intents"


def _workspace_index_collection() -> str:
    """Global workspace index collection (cross-project entity-navigable pointers)."""
    return "workspace_index"


#: Every per-project collection, the registry a store is measured against. Collections are created lazily on first write,
#: so a store may legitimately lack some; a caller that cannot tell whether the store it reached is the only one (the local
#: server's fallback) must treat a missing one as unknown, not empty (goal 24979a81 B4).
PROJECT_COLLECTIONS = (
    _docs_collection,
    _memory_collection,
    _epistemics_collection,
    _eidetic_collection,
    _episodic_collection,
    _goals_collection,
    _calibration_collection,
    _assumptions_collection,
    _decisions_collection,
    _intents_collection,
)


def project_collection_names(project_id: str) -> list[str]:
    """The names of every collection ``project_id`` can own, from :data:`PROJECT_COLLECTIONS`."""
    return [name(project_id) for name in PROJECT_COLLECTIONS]


def init_collections(project_id: str) -> bool:
    """Check Qdrant availability for a project.

    Previously this eagerly created all 10 collection types per project,
    causing collection bloat (#49: 176 collections, 122 empty, WAL lock panics).

    Now just checks availability. Collections are created lazily by individual
    sub-modules (memory.py, eidetic.py, goals.py, etc.) on first write.
    All read paths gracefully handle missing collections.
    """
    if not _check_qdrant_available():
        return False
    client = _get_qdrant_client()
    return client is not None


def init_global_collection() -> bool:
    """Initialize global learnings collection. Returns False if Qdrant not available."""
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, _ = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _global_learnings_collection()
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))
            logger.info(f"Created global_learnings collection with vector size {vector_size}")
        return True
    except Exception as e:
        logger.debug(f"Failed to init global collection: {e}")
        return False


def recreate_collection(collection_name: str) -> bool:
    """
    Delete and recreate a collection with the current embeddings provider's dimensions.
    Use when switching embedding providers (e.g., local hash -> Ollama).

    WARNING: This deletes all data in the collection!

    Returns True if successful.
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, _ = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        vector_size = _get_vector_size()

        # Delete if exists
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
            logger.info(f"Deleted collection {collection_name}")

        # Create with new dimensions
        client.create_collection(
            collection_name, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE)
        )
        logger.info(f"Created collection {collection_name} with {vector_size} dimensions")
        return True
    except Exception as e:
        logger.warning(f"Failed to recreate collection {collection_name}: {e}")
        return False


#: The ONLY collections a rebuild may drop — the ones `_embed_project_from_db`
#: can actually put back. Deriving the drop set from the refill set is the whole
#: point: two hand-maintained lists drifted, and the drift was silent and lossy.
#:
#: `recreate_project_collections` used to drop TEN collections while the refill
#: covered three (docs / memory / eidetic, via upsert_memory, embed_eidetic and
#: embed_project_code). `rebuild.py` contains ZERO references to decisions or
#: assumptions. So `empirica rebuild --qdrant-only` — which the gardening
#: guidance recommends as the way to refresh embedded payloads — permanently
#: destroyed every point in the other seven and reported success.
#:
#: Measured 2026-09-06 on one box: 4,781 points across up to 24 practices —
#: calibration 1,960, episodic 1,688, goals 1,036, decisions 92, assumptions 5.
#: Not recoverable: nothing re-embeds them, so the loss is silent AND permanent.
#: Decisions and assumptions JOINED this set on 2026-09-06, once `rebuild.py`
#: grew `_embed_typed_decisions` / `_embed_typed_assumptions`. That is the
#: intended direction of travel: a collection becomes droppable by acquiring a
#: refiller, never by someone deciding it is safe to lose.
REBUILDABLE_COLLECTIONS = (
    _docs_collection,
    _memory_collection,
    _eidetic_collection,
    _decisions_collection,
    _assumptions_collection,
)


def recreate_project_collections(project_id: str) -> dict:
    """
    Recreate the REBUILDABLE collections for a project at current dimensions.

    Returns `{collection_name: bool}` for what was recreated, plus a `preserved`
    key naming what was deliberately left alone and why.

    **A collection nothing can refill is not dropped.** The alternative is
    destroying it, because "recreate" here means delete-then-create-empty and
    the refill step covers only three of the ten collections this used to clear.

    The cost of preserving them is real and stated rather than hidden: if the
    embedding dimensions changed, a preserved collection keeps the OLD ones and
    will not accept new vectors. That is a loud failure at the next write, which
    is strictly better than a silent, permanent, unrecoverable deletion — and
    the caller is told, so it can be acted on rather than discovered.
    """
    results: dict = {}
    for coll_fn in REBUILDABLE_COLLECTIONS:
        name = coll_fn(project_id)
        results[name] = recreate_collection(name)

    preserved = []
    for coll_fn in (
        _epistemics_collection,
        _episodic_collection,
        _goals_collection,
        _calibration_collection,
        _assumptions_collection,
        _decisions_collection,
        _intents_collection,
    ):
        name = coll_fn(project_id)
        try:
            client = _get_qdrant_client()
            if client is not None and client.collection_exists(name):
                preserved.append({"collection": name, "points": client.count(name, exact=True).count})
        except Exception as e:  # reporting-only; never let it block the rebuild
            preserved.append({"collection": name, "points": None, "error": f"{type(e).__name__}: {e}"})

    results["preserved"] = {
        "collections": preserved,
        "reason": (
            "no rebuild path exists for these — the re-embed step covers docs/memory/eidetic only. "
            "They are left intact rather than emptied. If embedding dimensions changed, they still "
            "carry the old ones and will reject new vectors until re-created deliberately."
        ),
    }
    return results


def recreate_global_collections() -> dict:
    """
    Recreate global collections (global_learnings, personas) with current dimensions.

    Returns dict with success status for each collection.
    """
    results = {}
    for name in ["global_learnings", "personas", "workspace_index"]:
        results[name] = recreate_collection(name)
    return results


def get_collection_info() -> list[dict]:
    """
    Get info about all Qdrant collections including dimensions and point counts.
    Useful for diagnosing dimension mismatches.
    """
    if not _check_qdrant_available():
        return []

    try:
        client = _get_qdrant_client()
        if client is None:
            return []
        collections = client.get_collections()
        info = []
        for c in collections.collections:
            try:
                coll_info = client.get_collection(c.name)
                vectors_config = coll_info.config.params.vectors
                dim = getattr(vectors_config, "size", None)
                info.append(
                    {
                        "name": c.name,
                        "dimensions": dim,
                        "points": coll_info.points_count,
                    }
                )
            except Exception:
                info.append({"name": c.name, "dimensions": None, "points": 0})
        return info
    except Exception as e:
        logger.warning(f"Failed to get collection info: {e}")
        return []


def cleanup_empty_collections(dry_run: bool = True) -> dict:
    """
    Delete empty Qdrant collections to reduce resource usage (#49).

    Args:
        dry_run: If True, only report what would be deleted. If False, actually delete.

    Returns:
        Dict with 'total', 'empty', 'deleted'/'would_delete', and 'details' list.
    """
    if not _check_qdrant_available():
        return {"error": "Qdrant not available"}

    try:
        client = _get_qdrant_client()
        if client is None:
            return {"error": "Could not connect to Qdrant"}

        collections = client.get_collections()
        total = len(collections.collections)
        empty = []
        non_empty = []

        for c in collections.collections:
            try:
                coll_info = client.get_collection(c.name)
                points = coll_info.points_count or 0
                if points == 0:
                    empty.append(c.name)
                else:
                    non_empty.append({"name": c.name, "points": points})
            except Exception:
                empty.append(c.name)

        deleted = []
        if not dry_run:
            for name in empty:
                try:
                    client.delete_collection(name)
                    deleted.append(name)
                    logger.info(f"Deleted empty collection: {name}")
                except Exception as e:
                    logger.warning(f"Failed to delete {name}: {e}")

        return {
            "total": total,
            "empty_count": len(empty),
            "non_empty_count": len(non_empty),
            "dry_run": dry_run,
            "deleted" if not dry_run else "would_delete": deleted if not dry_run else empty,
            "kept": non_empty,
        }
    except Exception as e:
        logger.warning(f"Failed to cleanup collections: {e}")
        return {"error": str(e)}
