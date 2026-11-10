"""suggest_links_for_artifact — semantic neighbour candidates after *-log.

Called by the 6 *-log command handlers (finding, unknown, dead-end,
mistake, assumption, decision) right after Qdrant auto-embed completes.
Returns top-K semantically similar existing artifacts so the AI can
anchor edges via `--related-to <id>` (single-link) or a follow-up
`log-artifacts` graph batch (multi-link).

Closes the 'AI doesn't think to link artifacts' gap that the v0.5
substrate measures via edges_with_artifacts in the retrospective.

Graceful degradation: returns [] if Qdrant unreachable, embedding
fails, or no neighbours clear the similarity threshold.

Backwards compat: legacy Qdrant payloads (embedded before the
artifact_id field was added) are resolved via a SQLite reverse-hash
map — every artifact UUID in the project is md5-hashed once and
matched against the Qdrant point_id of each hit.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.65

# (table, id_col, text_cols, type_label) — order picks first non-null text
_ARTIFACT_TABLES: list[tuple[str, str, list[str], str]] = [
    ("project_findings", "id", ["finding"], "finding"),
    ("project_unknowns", "id", ["unknown"], "unknown"),
    ("project_dead_ends", "id", ["approach"], "dead_end"),
    ("mistakes_made", "id", ["mistake"], "mistake"),
    ("assumptions", "id", ["assumption"], "assumption"),
    ("decisions", "id", ["choice"], "decision"),
]


def suggest_links_for_artifact(
    project_id: str,
    artifact_text: str,
    exclude_id: str,
    *,
    project_path: str | Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return semantically similar existing artifacts to anchor edges to.

    Args:
        project_id: Active project UUID. Scopes the Qdrant search.
        artifact_text: Text content of the just-logged artifact (the
            full body is fine; embedding model handles truncation).
        exclude_id: UUID of the just-logged artifact, filtered from results.
        project_path: Optional project root for the SQLite legacy-point
            fallback. If None, walks from cwd via InstanceResolver.
        top_k: Maximum candidates to return (default 5).
        similarity_threshold: Cosine score floor (default 0.65).

    Returns:
        List of dicts shaped:
          {"id": uuid, "type": one of finding|unknown|dead_end|mistake|
                                       assumption|decision,
           "summary": str ≤120 chars,
           "similarity_score": float (3dp)}
        Sorted by similarity descending. Empty list on any failure path.
    """
    if not project_id or not artifact_text:
        return []

    try:
        from empirica.core.qdrant.collections import (
            _assumptions_collection,
            _decisions_collection,
            _memory_collection,
        )
        from empirica.core.qdrant.connection import (
            _get_embedding_safe,
            qdrant_search_client,
        )
    except ImportError:
        return []

    client = qdrant_search_client()
    if client is None:
        return []

    try:
        vector = _get_embedding_safe(artifact_text)
    except Exception as e:
        logger.debug(f"suggest_links: embedding failed: {e}")
        return []
    if vector is None:
        return []

    collections = [
        _memory_collection(project_id),  # finding, unknown, dead_end, mistake
        _assumptions_collection(project_id),  # assumption
        _decisions_collection(project_id),  # decision
    ]

    # Fetch a generous slice from each collection; we'll dedupe + cap below.
    per_collection_limit = max(top_k * 4, 20)
    all_hits: dict[str, dict] = {}
    legacy_hits: list[tuple[int, float]] = []  # (point_id, score) for fallback

    for collection in collections:
        try:
            results = client.query_points(
                collection_name=collection,
                query=vector,
                limit=per_collection_limit,
                score_threshold=similarity_threshold,
                with_payload=True,
            ).points
        except Exception as e:
            logger.debug(f"suggest_links: search failed on {collection}: {e}")
            continue

        for r in results:
            payload = getattr(r, "payload", None) or {}
            score = float(getattr(r, "score", 0.0))
            if score < similarity_threshold:
                continue
            art_id = payload.get("artifact_id")
            if art_id:
                if art_id == exclude_id:
                    continue
                existing = all_hits.get(art_id)
                if existing and existing["similarity_score"] >= score:
                    continue
                all_hits[art_id] = {
                    "id": art_id,
                    "type": payload.get("type") or "unknown",
                    "summary": _extract_summary(payload),
                    "similarity_score": round(score, 3),
                }
            else:
                # Legacy payload — defer until we have the reverse-hash map
                point_id = getattr(r, "id", None)
                if isinstance(point_id, int):
                    legacy_hits.append((point_id, score))

    if legacy_hits:
        _resolve_legacy_hits(legacy_hits, project_id, project_path, exclude_id, all_hits, similarity_threshold)

    ranked = sorted(
        all_hits.values(),
        key=lambda h: h["similarity_score"],
        reverse=True,
    )
    return ranked[:top_k]


def _resolve_legacy_hits(
    legacy_hits: list[tuple[int, float]],
    project_id: str,
    project_path: str | Path | None,
    exclude_id: str,
    out: dict[str, dict],
    threshold: float,
) -> None:
    """Reverse-hash legacy Qdrant points (no artifact_id in payload) against
    the SQLite artifact UUIDs for this project. Mutates `out` in place.
    """
    db_path = _resolve_db_path(project_path)
    if db_path is None or not db_path.exists():
        return

    # point_id → highest score we've seen for it
    point_scores: dict[int, float] = {}
    for pid, score in legacy_hits:
        if pid not in point_scores or score > point_scores[pid]:
            point_scores[pid] = score

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        for table, id_col, text_cols, type_label in _ARTIFACT_TABLES:
            try:
                cols = f"{id_col}, {', '.join(text_cols)}"
                cur.execute(
                    f"SELECT {cols} FROM {table} WHERE project_id = ?",
                    (project_id,),
                )
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                continue

            for row in rows:
                art_id = row[0]
                if art_id == exclude_id or art_id in out:
                    continue
                pid = int(hashlib.md5(art_id.encode()).hexdigest()[:15], 16)
                score = point_scores.get(pid)
                if score is None or score < threshold:
                    continue
                summary = ""
                for col_value in row[1:]:
                    if col_value:
                        summary = str(col_value)[:120]
                        break
                out[art_id] = {
                    "id": art_id,
                    "type": type_label,
                    "summary": summary,
                    "similarity_score": round(score, 3),
                }
    finally:
        conn.close()


def _resolve_db_path(project_path: str | Path | None) -> Path | None:
    """Resolve the SQLite path for the active project, falling back to
    InstanceResolver when no path is provided."""
    if project_path:
        return Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        root = R.project_path()
        if root:
            return Path(root) / ".empirica" / "sessions" / "sessions.db"
    except Exception as e:
        logger.debug(f"_resolve_db_path: InstanceResolver lookup failed: {e}")
    return None


def _extract_summary(payload: dict) -> str:
    """Pull a ≤120-char summary from whichever content field is populated.

    Different embed functions store content under different keys:
      memory.py        → 'text'
      embed_assumption → 'assumption'
      embed_decision   → 'choice'
    Fall back to text_full / assumption_full / choice_full when truncated.
    """
    for key in ("text", "assumption", "choice"):
        value = payload.get(key)
        if value:
            return str(value)[:120]
    for key in ("text_full", "assumption_full", "choice_full"):
        value = payload.get(key)
        if value:
            return str(value)[:120]
    return ""
