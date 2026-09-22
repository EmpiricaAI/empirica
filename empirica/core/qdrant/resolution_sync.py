"""Tell the vector store that an artifact was resolved.

The read-time reconcile in `search()` and `search_eidetic()` is what guarantees
a retracted finding is not served. This is the second layer, for readers that
bypass those two functions and read Qdrant directly: the payload's
`is_resolved` was written once at embed and frozen, so such a reader saw a
finding retracted hours ago as live, with `confirmed: true` computed without
the fact that would falsify it (cortex, 2026-09-21).

Best effort. Qdrant down, no collection, no point: nothing raised, the SQLite
resolution has already happened and is authoritative.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def mark_resolved_in_qdrant(
    project_id: str | None,
    artifact_id: str,
    *,
    resolution_kind: str | None = None,
    superseded_by: str | None = None,
    is_resolved: bool = True,
) -> dict[str, bool]:
    """Set resolution fields on the memory point and, for findings, the eidetic
    fact promoted from it. Returns which collections were updated."""
    done = {"memory": False, "eidetic": False}
    if not project_id or not artifact_id:
        return done
    try:
        from empirica.core.qdrant.collections import _eidetic_collection, _memory_collection
        from empirica.core.qdrant.connection import _check_qdrant_available, _get_qdrant_client
        from empirica.core.qdrant.point_ids import artifact_point_id

        if not _check_qdrant_available(project_id=project_id):
            return done
        client = _get_qdrant_client(project_id=project_id)
        if client is None:
            return done
        payload = {
            "is_resolved": bool(is_resolved),
            "resolution_kind": resolution_kind,
            "superseded_by": superseded_by,
        }
        point_id = artifact_point_id(artifact_id)
        for name, coll in (("memory", _memory_collection(project_id)), ("eidetic", _eidetic_collection(project_id))):
            try:
                if not client.collection_exists(coll):
                    continue
                client.set_payload(collection_name=coll, payload=payload, points=[point_id])
                done[name] = True
            except Exception as exc:  # a missing point is the common case for eidetic
                logger.debug("resolution not synced to %s for %s: %s", coll, artifact_id[:8], exc)
    except Exception as exc:
        logger.debug("resolution sync skipped for %s: %s", artifact_id[:8], exc)
    return done
