"""
Pattern Retrieval for Cognitive Workflow Hooks

Provides pattern retrieval for PREFLIGHT (proactive loading) and CHECK (reactive validation).
Integrates with Qdrant memory collections for lessons, dead_ends, and findings.

ANTI-GAMING: Calibration-specific feedback (calibration_warnings in PREFLIGHT, calibration_bias
in CHECK) has been removed from AI-facing output. Specific vector gaps and directions gave the
AI an "answer key" for gaming CHECK gates. Calibration data is now user-facing only
(calibration-report, statusline). The Sentinel uses it internally for threshold inflation.

Defaults:
- similarity_threshold: 0.7
- limit: 3
- optional: True (graceful fail if Qdrant unavailable)
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Defaults
# NOTE: Threshold lowered to 0.5 because placeholder embeddings (hash-based)
# produce max scores of ~0.55-0.60. Real ML embeddings would score 0.7-0.9.
DEFAULT_THRESHOLD = 0.5
DEFAULT_LIMIT = 3

# Time gap thresholds for human context awareness (in seconds)
# These are metadata signals for Claude, not retrieval quantity controls
TIME_GAP_THRESHOLDS = {
    "continuation": 30 * 60,  # < 30 minutes = likely same work session
    "short_break": 4 * 60 * 60,  # < 4 hours = human took a break
    # > 4 hours = human was away for extended period
}


def compute_time_gap_info(last_session_timestamp: float | None = None) -> dict[str, Any]:
    """
    Compute time gap information since last session.

    Returns metadata for Claude to understand human time context.
    This is a SIGNAL for awareness, not a control for retrieval quantity.

    Args:
        last_session_timestamp: Unix timestamp of last session end (or None if unknown)

    Returns:
        {
            "gap_seconds": float,
            "gap_human_readable": "4h 23m",
            "gap_category": "continuation" | "short_break" | "extended_away",
            "note": "Human-friendly context note"
        }
    """
    import time

    if last_session_timestamp is None:
        return {
            "gap_seconds": None,
            "gap_human_readable": "unknown",
            "gap_category": "unknown",
            "note": "No previous session timestamp available",
        }

    gap_seconds = time.time() - last_session_timestamp

    # Format human-readable
    hours = int(gap_seconds // 3600)
    minutes = int((gap_seconds % 3600) // 60)
    if hours > 0:
        gap_human_readable = f"{hours}h {minutes}m"
    else:
        gap_human_readable = f"{minutes}m"

    # Categorize
    if gap_seconds < TIME_GAP_THRESHOLDS["continuation"]:
        category = "continuation"
        note = "Continuing recent work session"
    elif gap_seconds < TIME_GAP_THRESHOLDS["short_break"]:
        category = "short_break"
        note = f"Returning after {gap_human_readable} break"
    else:
        category = "extended_away"
        note = f"Human was away for {gap_human_readable} - may benefit from context recap"

    return {
        "gap_seconds": gap_seconds,
        "gap_human_readable": gap_human_readable,
        "gap_category": category,
        "note": note,
    }


def get_qdrant_url() -> str | None:
    """Check if Qdrant is configured."""
    return os.getenv("EMPIRICA_QDRANT_URL")


def _search_memory_by_type(
    project_id: str, query_text: str, memory_type: str, limit: int = DEFAULT_LIMIT, min_score: float = DEFAULT_THRESHOLD
) -> list[dict]:
    """
    Search memory collection filtered by type.
    Returns empty list if Qdrant not available (optional behavior).
    """
    try:
        from .vector_store import _check_qdrant_available, _get_embedding_safe, _get_qdrant_client, _memory_collection

        if not _check_qdrant_available():
            return []

        qvec = _get_embedding_safe(query_text)
        if qvec is None:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = _get_qdrant_client()
        coll = _memory_collection(project_id)

        if not client.collection_exists(coll):
            return []

        query_filter = Filter(must=[FieldCondition(key="type", match=MatchValue(value=memory_type))])

        results = client.query_points(
            collection_name=coll, query=qvec, query_filter=query_filter, limit=limit, with_payload=True
        )

        # Filter by min_score and return
        return [
            {"score": getattr(r, "score", 0.0) or 0.0, **(r.payload or {})}
            for r in results.points
            if (getattr(r, "score", 0.0) or 0.0) >= min_score
        ]
    except Exception as e:
        logger.debug(f"_search_memory_by_type({memory_type}) failed: {e}")
        return []


def _search_related_docs(
    project_id: str, query_text: str, limit: int = DEFAULT_LIMIT, min_score: float = DEFAULT_THRESHOLD
) -> list[dict]:
    """
    Search docs collection for documents related to a query.
    Used to find supporting documentation for retrieved memory entries.

    Returns list of related docs with path, description, and relevance score.
    """
    try:
        from .vector_store import _check_qdrant_available, _docs_collection, _get_embedding_safe, _get_qdrant_client

        if not _check_qdrant_available():
            return []

        qvec = _get_embedding_safe(query_text)
        if qvec is None:
            return []

        client = _get_qdrant_client()
        coll = _docs_collection(project_id)

        if not client.collection_exists(coll):
            return []

        results = client.query_points(collection_name=coll, query=qvec, limit=limit, with_payload=True)

        # Format results
        return [
            {
                "doc_path": (r.payload or {}).get("doc_path", ""),
                "description": (r.payload or {}).get("description", ""),
                "doc_type": (r.payload or {}).get("doc_type", ""),
                "tags": (r.payload or {}).get("tags", []),
                "score": getattr(r, "score", 0.0) or 0.0,
            }
            for r in results.points
            if (getattr(r, "score", 0.0) or 0.0) >= min_score
        ]
    except Exception as e:
        logger.debug(f"_search_related_docs failed: {e}")
        return []


def _compute_adaptive_limits(vectors: dict | None, base_limit: int) -> dict[str, int]:
    """Compute per-collection retrieval limits based on vector state.

    Higher uncertainty → more context from all collections (up to 2x).
    Low know → more lessons, dead-ends, assumptions.
    Low context → more episodic, goals, decisions.
    """
    if not vectors:
        return dict.fromkeys(
            [
                "lessons",
                "dead_ends",
                "mistakes",
                "findings",
                "eidetic",
                "episodic",
                "goals",
                "assumptions",
                "decisions",
                "global_dead_ends",
                "docs",
            ],
            base_limit,
        )

    uncertainty = vectors.get("uncertainty", 0.5)
    know = vectors.get("know", 0.5)
    context = vectors.get("context", 0.5)

    # Base multiplier: scales 1.0x at u=0.0 to 2.0x at u=1.0
    uncertainty_mult = 1.0 + uncertainty

    # Knowledge gap: low know → more procedural/warning context
    know_gap = max(0.0, 1.0 - know)  # 0.0 at know=1.0, 1.0 at know=0.0

    # Context gap: low context → more situational awareness
    context_gap = max(0.0, 1.0 - context)  # 0.0 at context=1.0, 1.0 at context=0.0

    # Cap adaptive growth at MAX_PER_SECTION: the gap bonuses balloon counts
    # worst exactly post-compaction (high uncertainty + low know/context), which
    # is precisely when the injected block should stay lean, not grow.
    def _limit(base_mult: float, gap_bonus: float = 0.0) -> int:
        return max(1, min(MAX_PER_SECTION, int(base_limit * base_mult * uncertainty_mult + gap_bonus)))

    return {
        "lessons": _limit(1.0, know_gap * 2),
        "dead_ends": _limit(1.0, know_gap * 2),
        "mistakes": _limit(1.0, know_gap * 2),
        "findings": _limit(1.0),
        "eidetic": _limit(1.0, know_gap),
        "episodic": _limit(1.0, context_gap * 2),
        "goals": _limit(1.0, context_gap * 2),
        "assumptions": _limit(1.0, know_gap * 2),
        "decisions": _limit(1.0, context_gap),
        "global_dead_ends": max(1, min(MAX_PER_SECTION, int(2 * uncertainty_mult))),
        "docs": _limit(1.0),
    }


def _enrich_memory_types(result, project_id, task_context, limits, include_eidetic, include_episodic):
    """Enrich with eidetic facts, episodic narratives, and global dead-ends."""
    if include_eidetic:
        try:
            from .vector_store import search_eidetic

            # Over-fetch + recency-rerank (confidence as longevity modulator,
            # first_seen as the age) so stale eidetic facts sink.
            eidetic_raw = search_eidetic(
                project_id, task_context, min_confidence=0.5, limit=limits["eidetic"] * _RECENCY_OVERFETCH
            )
            eidetic_ranked = _apply_recency_rerank(
                eidetic_raw, limits["eidetic"], modulator_key="confidence", ts_key="first_seen"
            )
            result["eidetic_facts"] = [
                {
                    "content": e.get("content", ""),
                    "confidence": e.get("confidence", 0.5),
                    "domain": e.get("domain"),
                    "confirmation_count": e.get("confirmation_count", 1),
                    "score": e.get("score", 0.0),
                    "recency_weight": e.get("recency_weight", 1.0),
                    "effective_score": e.get("effective_score", e.get("score", 0.0)),
                }
                for e in eidetic_ranked
            ]
        except Exception as e:
            logger.debug(f"Eidetic retrieval failed: {e}")
            result["eidetic_facts"] = []
    if include_episodic:
        try:
            from .vector_store import search_episodic

            result["episodic_narratives"] = [
                {
                    "narrative": ep.get("narrative", ""),
                    "outcome": ep.get("outcome"),
                    "learning_delta": ep.get("learning_delta", {}),
                    "recency_weight": ep.get("recency_weight", 1.0),
                    "score": ep.get("score", 0.0),
                }
                for ep in search_episodic(project_id, task_context, limit=limits["episodic"], apply_recency_decay=True)
            ]
        except Exception as e:
            logger.debug(f"Episodic retrieval failed: {e}")
            result["episodic_narratives"] = []
    try:
        from .vector_store import search_global_dead_ends

        raw = search_global_dead_ends(f"Approach for: {task_context}", limit=limits["global_dead_ends"])
        if raw:
            result["global_dead_ends"] = [
                {
                    "approach": g.get("approach", g.get("text", "")),
                    "why_failed": g.get("why_failed", ""),
                    "project": g.get("project_name", "other project"),
                    "score": g.get("score", 0.0),
                }
                for g in raw
            ]
    except Exception as e:
        logger.debug(f"Global dead-ends retrieval failed: {e}")


def _apply_goal_reconciliation(raw_goals, live_map):
    """Reconcile retrieved goals against authoritative live status (retrieval hygiene).

    ``live_map`` maps ``goal_id -> (status, is_completed)`` from the local SQLite.
    Drops goals completed in SQLite (a stale Qdrant payload still reading
    ``in_progress``), corrects stale status on open goals, and keeps goals absent
    from the map (cross-project or subtask) unchanged. Pure function — unit-testable.
    """
    out = []
    for g in raw_goals:
        gid = g.get("goal_id")
        if gid and gid in live_map:
            status, is_completed = live_map[gid]
            if is_completed or status == "completed":
                continue  # drop: completed in SQLite, stale-in_progress in Qdrant
            if status and status != g.get("status"):
                g = {**g, "status": status}  # correct stale status in place
        out.append(g)
    return out


def _reconcile_goals_against_sqlite(raw_goals):
    """Look up live goal status from the local project SQLite and reconcile.

    Best-effort: on ANY failure returns ``raw_goals`` unchanged — this runs in the
    PREFLIGHT/CHECK hot-path and must never break retrieval. Cross-project goals
    (``goal_id`` absent from the local ``goals`` table) are kept as-is; correcting
    their status would need the owning project's DB (deferred to v2).
    """
    if not raw_goals:
        return raw_goals
    try:
        ids = [g.get("goal_id") for g in raw_goals if g.get("goal_id")]
        if not ids:
            return raw_goals
        import sqlite3
        from pathlib import Path

        from empirica.data.session_database import _resolve_canonical_project_root

        root = _resolve_canonical_project_root()
        if not root:
            return raw_goals
        db_path = Path(root) / ".empirica" / "sessions" / "sessions.db"
        if not db_path.is_file():
            return raw_goals
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            placeholders = ",".join("?" for _ in ids)
            cur = conn.execute(
                f"SELECT id, status, COALESCE(is_completed, 0) FROM goals WHERE id IN ({placeholders})",
                ids,
            )
            live_map = {row[0]: (row[1], bool(row[2])) for row in cur.fetchall()}
        finally:
            conn.close()
        return _apply_goal_reconciliation(raw_goals, live_map)
    except Exception as e:
        logger.debug(f"goal reconciliation skipped (keeping raw): {e}")
        return raw_goals


def _reconcile_findings_against_sqlite(raw_findings):
    """Drop findings resolved/superseded in the local SQLite (#307 retrieval hygiene).

    Qdrant's ``is_resolved`` payload is frozen at embed time, so a finding resolved
    AFTER it was embedded still reads unresolved in the vector store and keeps
    resurfacing in PREFLIGHT/CHECK relevant_findings. Read-time reconcile against the
    authoritative SQLite is the fix (same pattern as goal reconciliation).

    Matches by ``artifact_id`` (present on findings embedded after #307) with a
    text-prefix fallback for older embeds that predate the id-in-payload change —
    the Qdrant ``text`` field is truncated to 500 chars, so we compare on that prefix.

    Best-effort: on ANY failure returns ``raw_findings`` unchanged — this runs in the
    PREFLIGHT/CHECK hot-path and must never break retrieval.
    """
    if not raw_findings:
        return raw_findings
    try:
        import sqlite3
        from pathlib import Path

        from empirica.data.session_database import _resolve_canonical_project_root

        root = _resolve_canonical_project_root()
        if not root:
            return raw_findings
        db_path = Path(root) / ".empirica" / "sessions" / "sessions.db"
        if not db_path.is_file():
            return raw_findings
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            # Column may be absent on DBs predating migration_057 — bail to raw if so.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(project_findings)").fetchall()}
            if "is_resolved" not in cols:
                return raw_findings
            cur = conn.execute("SELECT id, finding FROM project_findings WHERE is_resolved = 1")
            resolved_ids: set[str] = set()
            resolved_text_prefixes: set[str] = set()
            for row in cur.fetchall():
                if row[0]:
                    resolved_ids.add(row[0])
                if row[1]:
                    resolved_text_prefixes.add(row[1][:500])
        finally:
            conn.close()
        if not resolved_ids and not resolved_text_prefixes:
            return raw_findings

        out = []
        for f in raw_findings:
            aid = f.get("artifact_id")
            if aid and aid in resolved_ids:
                continue  # drop: resolved by id
            if not aid:
                text = f.get("text_full") or f.get("text") or ""
                if text and text[:500] in resolved_text_prefixes:
                    continue  # drop: resolved by text-prefix (pre-#307 embed)
            out.append(f)
        return out
    except Exception as e:
        logger.debug(f"finding reconciliation skipped (keeping raw): {e}")
        return raw_findings


def _enrich_knowledge_graph(
    result,
    project_id,
    task_context,
    threshold,
    limits,
    include_goals,
    include_assumptions,
    include_decisions,
    include_related_docs,
):
    """Enrich with goals, assumptions, decisions, and docs."""
    if include_goals:
        try:
            from .vector_store import search_goals

            raw = search_goals(project_id, task_context, include_subtasks=True, limit=limits["goals"])
            raw = _reconcile_goals_against_sqlite(raw)  # retrieval hygiene: drop completed, correct stale
            if raw:
                result["related_goals"] = [
                    {
                        "objective": g.get("objective") or g.get("description", ""),
                        "status": g.get("status", ""),
                        "type": g.get("type", "goal"),
                        "goal_id": g.get("goal_id", ""),
                        "score": g.get("score", 0.0),
                    }
                    for g in raw
                ]
        except Exception as e:
            logger.debug(f"Goals retrieval failed: {e}")
    if include_assumptions:
        try:
            from .vector_store import search_assumptions

            raw = search_assumptions(project_id, task_context, status="unverified", limit=limits["assumptions"])
            if raw:
                result["unverified_assumptions"] = [
                    {
                        "assumption": a.get("assumption", ""),
                        "confidence": a.get("confidence", 0.5),
                        "urgency_signal": a.get("urgency_signal", 0.0),
                        "domain": a.get("domain"),
                        "score": a.get("score", 0.0),
                    }
                    for a in raw
                ]
        except Exception as e:
            logger.debug(f"Assumptions retrieval failed: {e}")
    if include_decisions:
        try:
            from .vector_store import search_decisions

            raw = search_decisions(project_id, task_context, limit=limits["decisions"])
            if raw:
                result["prior_decisions"] = [
                    {
                        "choice": d.get("choice", ""),
                        "rationale": d.get("rationale", ""),
                        "reversibility": d.get("reversibility", ""),
                        "confidence_at_decision": d.get("confidence_at_decision", 0.5),
                        "score": d.get("score", 0.0),
                    }
                    for d in raw
                ]
        except Exception as e:
            logger.debug(f"Decisions retrieval failed: {e}")
    if include_related_docs:
        try:
            raw = _search_related_docs(project_id, task_context, limit=limits["docs"], min_score=threshold)
            result["related_docs"] = [
                {
                    "doc_path": d.get("doc_path", ""),
                    "description": d.get("description", ""),
                    "doc_type": d.get("doc_type", ""),
                    "tags": d.get("tags", []),
                    "score": d.get("score", 0.0),
                }
                for d in raw
            ]
        except Exception as e:
            logger.debug(f"Related docs retrieval failed: {e}")
            result["related_docs"] = []


def _enrich_task_patterns(
    result,
    project_id,
    task_context,
    threshold,
    limits,
    include_eidetic,
    include_episodic,
    include_related_docs,
    include_goals,
    include_assumptions,
    include_decisions,
):
    """Enrich task patterns with optional retrieval types."""
    _enrich_memory_types(result, project_id, task_context, limits, include_eidetic, include_episodic)
    _enrich_knowledge_graph(
        result,
        project_id,
        task_context,
        threshold,
        limits,
        include_goals,
        include_assumptions,
        include_decisions,
        include_related_docs,
    )


# Over-fetch factor so recency re-ranking can actually drop a stale-but-similar
# item in favour of a fresher relevant one (not just reorder the top-N).
_RECENCY_OVERFETCH = 3


def _apply_recency_rerank(
    items: list[dict],
    limit: int,
    *,
    modulator_key: str = "impact",
    ts_key: str = "timestamp",
) -> list[dict]:
    """Re-rank artifacts by recency at READ time (decay P1): effective_score =
    cosine score x time-decay weight, then take top `limit`. Over-fetch upstream
    so a stale-but-similar item can be dropped for a fresher relevant one.

    Reuses FindingsDeprecationEngine.calculate_time_decay — the canonical decay
    curve, which until now was applied only in the breadcrumbs path, NOT this
    Qdrant PREFLIGHT retrieval. The longevity modulator (longer tau = resists
    ageing) comes from `modulator_key`: impact for findings, confidence for
    lessons/eidetic (a well-established fact stays relevant longer). Timestamp
    from `ts_key`: findings/lessons store an ISO `timestamp`; eidetic stores
    `first_seen`. Ranking-only: NO stored confidence mutation. Dead-ends are
    deliberately NOT recency-ranked (never-decay decision).

    calculate_time_decay only float()s strings, so an ISO value must be
    normalised to unix first or it silently scores 0.5 for everything.
    Missing/unparseable timestamp -> neutral weight 1.0 (never penalise on bad
    data).
    """
    if not items:
        return []
    try:
        from datetime import datetime

        from empirica.core.findings_deprecation import FindingsDeprecationEngine

        def _unix(ts):
            if ts is None:
                return None
            if isinstance(ts, (int, float)):
                return float(ts)
            try:
                return float(ts)  # already unix-stringified
            except (ValueError, TypeError):
                try:
                    return datetime.fromisoformat(ts).timestamp()  # ISO-8601
                except (ValueError, TypeError):
                    return None

        for it in items:
            unix_ts = _unix(it.get(ts_key))
            recency = (
                FindingsDeprecationEngine.calculate_time_decay(unix_ts, longevity=it.get(modulator_key))
                if unix_ts
                else 1.0
            )
            it["recency_weight"] = round(recency, 4)
            it["effective_score"] = (it.get("score", 0.0) or 0.0) * recency
        items.sort(key=lambda it: it.get("effective_score", 0.0), reverse=True)
        return items[:limit]
    except Exception as e:
        logger.debug(f"_apply_recency_rerank failed; falling back to score order: {e}")
        return items[:limit]


# --- Context budget (lean-by-default teaser) -------------------------------
# PREFLIGHT / CHECK / bootstrap inject a *ranked teaser*, not a full dump — the
# same context is retrievable on demand (investigate, project-search,
# commit-context). Without this the patterns block balloons: counts scale up on
# uncertainty/gaps (worst exactly post-compaction), item content is never
# truncated, and the same artifact recurs across sections (a finding also shows
# up as an eidetic fact). Three knobs, all env-overridable; set
# EMPIRICA_PATTERN_BUDGET_OFF=1 for the full untrimmed result (the escape hatch,
# mirroring cortex's enrich=false on SER projections).
MAX_ITEM_CHARS = int(os.getenv("EMPIRICA_PATTERN_MAX_ITEM_CHARS", "280"))
MAX_PER_SECTION = int(os.getenv("EMPIRICA_PATTERN_MAX_PER_SECTION", "5"))
MAX_TOTAL_CHARS = int(os.getenv("EMPIRICA_PATTERN_MAX_TOTAL_CHARS", "8000"))

# Per-section text fields. First entry is the dedup signature (the field whose
# value duplicates across sections); all entries are truncated. A section absent
# here is skipped by dedup but still generically truncated.
_SECTION_TEXT_FIELDS = {
    # PREFLIGHT (retrieve_task_patterns)
    "lessons": ["description", "name"],
    "dead_ends": ["why_failed", "approach"],
    "global_dead_ends": ["why_failed", "approach"],
    "prior_mistakes": ["mistake", "prevention"],
    "relevant_findings": ["finding"],
    "eidetic_facts": ["content"],
    "episodic_narratives": ["narrative"],
    "related_goals": ["objective"],
    "unverified_assumptions": ["assumption"],
    "prior_decisions": ["rationale", "choice"],
    "related_docs": ["description"],
    # CHECK (check_against_patterns)
    "dead_end_matches": ["why_failed", "approach"],
    "mistake_matches": ["mistake", "prevention"],
    "related_findings": ["finding"],
    "eidetic_context": ["content"],
    "active_goals": ["objective"],
}

# Scalar / metadata keys that are never item-lists — skipped by the budget pass.
_NON_ITEM_KEYS = frozenset({"time_gap", "mistake_risk", "has_warnings", "_context_budget"})

# Sections whose single top item is protected from budget eviction.
_BUDGET_PROTECTED = frozenset({"lessons", "dead_ends", "prior_mistakes", "relevant_findings"})


def _content_sig(text: str) -> str:
    """Stable signature for cross-section dedup: hash of whitespace-normalized,
    lowercased text. Exact-duplicate detection only — won't collapse merely
    similar items."""
    return hashlib.md5(" ".join(text.lower().split()).encode("utf-8")).hexdigest()


def _truncate_text(text: str, max_chars: int) -> str:
    """Truncate to max_chars at a word boundary, appending an overflow marker.

    Delegates to the shared ``context_budget.truncate_text`` — single source
    of truth for display-string truncation across context-injection sites.
    """
    from empirica.core.context_budget import truncate_text

    return truncate_text(text, max_chars)


def _item_sections(result: dict):
    """Yield (key, list-of-items) for the budgetable sections of a result."""
    for key, val in result.items():
        if key not in _NON_ITEM_KEYS and isinstance(val, list):
            yield key, val


def _dedup_sections(result: dict) -> int:
    """B2: drop items whose primary-field text already appeared in an earlier
    section (insertion order = priority). Returns count dropped."""
    seen: set[str] = set()
    dropped = 0
    for key, items in _item_sections(result):
        fields = _SECTION_TEXT_FIELDS.get(key)
        if not fields:
            continue
        primary, kept = fields[0], []
        for it in items:
            txt = it.get(primary) if isinstance(it, dict) else None
            if not isinstance(txt, str) or not txt.strip():
                kept.append(it)
                continue
            sig = _content_sig(txt)
            if sig in seen:
                dropped += 1
                continue
            seen.add(sig)
            kept.append(it)
        result[key] = kept
    return dropped


def _truncate_sections(result: dict, max_chars: int) -> None:
    """B1: truncate the text fields of every item."""
    for key, items in _item_sections(result):
        fields = _SECTION_TEXT_FIELDS.get(key)
        for it in items:
            if not isinstance(it, dict):
                continue
            targets = fields or [k for k, v in it.items() if isinstance(v, str)]
            for f in targets:
                if isinstance(it.get(f), str):
                    it[f] = _truncate_text(it[f], max_chars)


def _item_rank(it: dict) -> float:
    """Ranking key for budget eviction: effective_score > score > similarity."""
    for k in ("effective_score", "score", "similarity"):
        v = it.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def _result_chars(result: dict) -> int:
    return sum(
        len(v)
        for _, items in _item_sections(result)
        for it in items
        if isinstance(it, dict)
        for v in it.values()
        if isinstance(v, str)
    )


def _enforce_total_budget(result: dict, max_total: int) -> int:
    """B3: drop the lowest-ranked item until under the total-char budget. Keeps
    the top item of the core triad. Returns count dropped."""
    dropped = 0
    while _result_chars(result) > max_total:
        worst = None  # (rank, key, index)
        for key, items in _item_sections(result):
            floor = 1 if key in _BUDGET_PROTECTED else 0
            if len(items) <= floor:
                continue
            for idx, it in enumerate(items):
                if isinstance(it, dict) and (worst is None or _item_rank(it) < worst[0]):
                    worst = (_item_rank(it), key, idx)
        if worst is None:
            break
        del result[worst[1]][worst[2]]
        dropped += 1
    return dropped


# Per-category list keys eligible for the user-configurable injection cap.
_INJECTION_CATEGORY_KEYS = (
    "lessons",
    "dead_ends",
    "mistakes",
    "prior_mistakes",
    "relevant_findings",
    "related_findings",
    "eidetic_facts",
    "eidetic_context",
    "episodic_narratives",
    "related_goals",
    "active_goals",
    "unverified_assumptions",
    "prior_decisions",
    "related_docs",
    "global_dead_ends",
)


def _resolve_injection_caps() -> dict:
    """Resolve the user-configurable artifact-injection caps.

    Precedence: env var > ``.empirica/config.yaml`` ``artifact_injection`` block >
    default (``None`` = uncapped, preserving prior behaviour). Keys:

    - ``max_per_category`` (env ``EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY``) — bounds
      each category list.
    - ``max_total`` (env ``EMPIRICA_MAX_ARTIFACTS_TOTAL``) — bounds the combined set.

    Never raises — a bad value is ignored (falls back to uncapped for that key).
    """
    caps: dict = {"max_per_category": None, "max_total": None}
    cfg: dict = {}
    try:
        from empirica.config.path_resolver import load_empirica_config

        block = (load_empirica_config() or {}).get("artifact_injection")
        if isinstance(block, dict):
            cfg = block
    except Exception:
        cfg = {}
    for key, env in (
        ("max_per_category", "EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY"),
        ("max_total", "EMPIRICA_MAX_ARTIFACTS_TOTAL"),
    ):
        raw = os.getenv(env)
        if raw is None:
            raw = cfg.get(key)
        if raw is None:
            continue
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        if val > 0:
            caps[key] = val
    return caps


def _apply_injection_caps(result: dict, caps: dict | None = None) -> tuple[int, int]:
    """Truncate per-category injection lists to ``max_per_category`` and the combined
    set to ``max_total``. Lists are already score-ranked (highest first), so the
    dropped tail is the lowest-ranked. Mutates ``result`` in place; returns
    ``(capped_per_category, capped_total)`` drop counts. No-op when both caps are
    ``None`` (default) — preserves prior behaviour exactly.
    """
    if caps is None:
        caps = _resolve_injection_caps()
    mpc = caps.get("max_per_category")
    mt = caps.get("max_total")
    capped_pc = 0
    capped_total = 0
    if mpc:
        for key in _INJECTION_CATEGORY_KEYS:
            items = result.get(key)
            if isinstance(items, list) and len(items) > mpc:
                capped_pc += len(items) - mpc
                result[key] = items[:mpc]
    if mt:
        lists = {k: result[k] for k in _INJECTION_CATEGORY_KEYS if isinstance(result.get(k), list)}
        current = sum(len(v) for v in lists.values())
        while current > mt:
            # drop the lowest-ranked (tail) item of the currently-largest category
            biggest = max(lists, key=lambda k: len(lists[k]))
            if not lists[biggest]:
                break
            lists[biggest].pop()
            capped_total += 1
            current -= 1
    return capped_pc, capped_total


def _injection_measure_view(result: dict, caps: dict, capped_pc: int, capped_total: int) -> dict:
    """The canonical 6-field injection measure-view (mirrors cortex's served block
    so `empirica status`, the practitioner response, and the extension panel render
    ONE consistent shape):

    - ``injected_per_category`` / ``injected_total`` — what actually landed (post
      cap + dedup + budget). The VOLUME. Answers "do I need a cap?"
    - ``cap_per_category`` / ``cap_total`` — the configured ceiling (``None`` = uncapped).
    - ``capped_per_category`` / ``capped_total`` — what the cap dropped. Answers
      "is the cap biting?"

    Volume is surfaced even when uncapped (capped_* all 0 in the default) — that's
    the "measure before you tune" the injection-cap work is for.
    """
    injected_pc = {k: len(result[k]) for k in _INJECTION_CATEGORY_KEYS if isinstance(result.get(k), list) and result[k]}
    return {
        "injected_per_category": injected_pc,
        "injected_total": sum(injected_pc.values()),
        "cap_per_category": caps.get("max_per_category"),
        "cap_total": caps.get("max_total"),
        "capped_per_category": capped_pc,
        "capped_total": capped_total,
    }


def _apply_context_budget(result: dict, apply_budget: bool = True) -> dict:
    """Lean-by-default post-pass over an assembled patterns/warnings dict:
    dedup across sections (B2) → truncate long item text (B1) → enforce a total
    char budget (B3). Records a `_context_budget` note when anything was trimmed
    so the elision is legible (no silent truncation). Best-effort: any failure
    returns the full result untouched. Disable via apply_budget=False or
    EMPIRICA_PATTERN_BUDGET_OFF=1."""
    if not apply_budget or os.getenv("EMPIRICA_PATTERN_BUDGET_OFF") == "1":
        return result
    try:
        caps = _resolve_injection_caps()
        capped_pc, capped_total = _apply_injection_caps(result, caps)  # user cap first (config/env)
        deduped = _dedup_sections(result)
        _truncate_sections(result, MAX_ITEM_CHARS)
        elided = _enforce_total_budget(result, MAX_TOTAL_CHARS)
        budget: dict = {}
        # Injection measure-view: surfaced whenever there's injected volume OR a cap
        # is configured — the "measure before you tune" signal (extension prop_3por4fwg,
        # mirrors cortex's served 6-field block). Volume shows even uncapped, so a
        # drops-only line no longer reads blank in the default.
        measure = _injection_measure_view(result, caps, capped_pc, capped_total)
        if measure["injected_total"] or measure["cap_per_category"] or measure["cap_total"]:
            budget.update(measure)
        # Trim note: only when dedup/elision actually happened (not for the measure alone).
        if deduped or elided:
            budget["deduped"] = deduped
            budget["elided_for_budget"] = elided
            budget["note"] = (
                "Lean teaser — duplicates removed, long items truncated, "
                "lowest-ranked elided to fit budget. Full context via "
                "`empirica investigate` / `project-search` / `commit-context`."
            )
        if budget:
            result["_context_budget"] = budget
    except Exception as e:  # never let budgeting break retrieval
        logger.debug(f"_apply_context_budget failed; returning full result: {e}")
    return result


def retrieve_task_patterns(
    project_id: str,
    task_context: str,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
    last_session_timestamp: float | None = None,
    include_eidetic: bool = False,
    include_episodic: bool = False,
    include_related_docs: bool = False,
    include_goals: bool = False,
    include_assumptions: bool = False,
    include_decisions: bool = False,
    vectors: dict | None = None,
    apply_budget: bool = True,
) -> dict[str, Any]:
    """
    PREFLIGHT hook: Retrieve relevant patterns for a task (Noetic RAG).

    Returns patterns that should inform the AI before starting work:
    - lessons: Procedural knowledge (HOW to do things)
    - dead_ends: Failed approaches (what NOT to try)
    - relevant_findings: High-impact facts
    - eidetic_facts: Stable facts with confidence (optional)
    - episodic_narratives: Recent session arcs (optional)
    - related_docs: Reference documents related to retrieved memory (optional)
    - related_goals: Goals/subtasks related to task context (optional)
    - unverified_assumptions: Unverified beliefs that may affect this work (optional)
    - prior_decisions: Past decisions relevant to this area (optional)
    - time_gap: Metadata about time since last session (for human context awareness)

    Args:
        project_id: Project ID
        task_context: Description of the task being undertaken
        threshold: Minimum similarity score (default 0.5)
        limit: Max patterns per type (default 3)
        last_session_timestamp: Used to compute time gap metadata
        include_eidetic: Include eidetic facts in retrieval
        include_episodic: Include episodic narratives in retrieval
        include_related_docs: Include related reference docs in retrieval
        include_goals: Include related goals/subtasks
        include_assumptions: Include unverified assumptions ("What are you assuming?")
        include_decisions: Include prior decisions ("What was already decided?")
        vectors: Current epistemic vectors for adaptive depth scaling
    """
    # Compute time gap metadata (signal for Claude, not retrieval control)
    time_gap_info = compute_time_gap_info(last_session_timestamp)

    if not get_qdrant_url():
        return {
            "lessons": [],
            "dead_ends": [],
            "prior_mistakes": [],
            "relevant_findings": [],
            "time_gap": time_gap_info,
        }

    # Adaptive limits: scale retrieval depth by vector state
    limits = _compute_adaptive_limits(vectors, limit)

    # Search for lessons (procedural knowledge). Over-fetch, then recency-rerank
    # with confidence as the longevity modulator so stale lessons sink.
    lessons_raw = _search_memory_by_type(
        project_id, f"How to: {task_context}", "lesson", limits["lessons"] * _RECENCY_OVERFETCH, threshold
    )
    lessons_ranked = _apply_recency_rerank(
        lessons_raw, limits["lessons"], modulator_key="confidence", ts_key="timestamp"
    )
    lessons = [
        {
            "name": l.get("text", "").replace("LESSON: ", "").split(" - ")[0] if l.get("text") else "",
            "description": l.get("text", "").split(" - ")[1].split(" Domain:")[0] if " - " in l.get("text", "") else "",
            "domain": l.get("domain", ""),
            "confidence": l.get("confidence", 0.8),
            "score": l.get("score", 0.0),
            "recency_weight": l.get("recency_weight", 1.0),
            "effective_score": l.get("effective_score", l.get("score", 0.0)),
        }
        for l in lessons_ranked
    ]

    # Search for dead ends (what NOT to try)
    dead_ends_raw = _search_memory_by_type(
        project_id, f"Approach for: {task_context}", "dead_end", limits["dead_ends"], threshold
    )
    dead_ends = [
        {
            "approach": d.get("text", "").replace("DEAD END: ", "").split(" Why failed:")[0] if d.get("text") else "",
            "why_failed": d.get("text", "").split("Why failed: ")[1] if "Why failed:" in d.get("text", "") else "",
            "score": d.get("score", 0.0),
        }
        for d in dead_ends_raw
    ]

    # Search for prior mistakes (errors to not repeat). Sibling anti-pattern to
    # dead_ends — embedded as "{mistake} Prevention: {prevention}" (type=mistake),
    # parsed back out the same way. Always retrieved (not opt-in): a logged mistake
    # must nudge attention on a similar task, else mistake-log is a write-only sink.
    mistakes_raw = _search_memory_by_type(
        project_id, f"Mistake or pitfall doing: {task_context}", "mistake", limits["mistakes"], threshold
    )
    prior_mistakes = [
        {
            "mistake": m.get("text", "").split(" Prevention:")[0] if m.get("text") else "",
            "prevention": m.get("text", "").split("Prevention: ")[1] if "Prevention:" in m.get("text", "") else "",
            "score": m.get("score", 0.0),
        }
        for m in mistakes_raw
    ]

    # Search for relevant findings (high-impact facts). Over-fetch, then re-rank
    # by recency at read-time so stale findings sink below fresh relevant ones.
    findings_raw = _search_memory_by_type(
        project_id, task_context, "finding", limits["findings"] * _RECENCY_OVERFETCH, threshold
    )
    findings_raw = _reconcile_findings_against_sqlite(findings_raw)  # #307: drop resolved/superseded
    findings_ranked = _apply_recency_rerank(
        findings_raw, limits["findings"], modulator_key="impact", ts_key="timestamp"
    )
    relevant_findings = [
        {
            "finding": f.get("text", ""),
            "impact": f.get("impact", 0.5),
            "score": f.get("score", 0.0),
            "recency_weight": f.get("recency_weight", 1.0),
            "effective_score": f.get("effective_score", f.get("score", 0.0)),
        }
        for f in findings_ranked
    ]

    # ANTI-GAMING: Calibration warnings (specific overestimate/underestimate patterns from
    # similar past tasks) are no longer surfaced to the AI. They provide an "answer key"
    # for gaming self-assessment vectors. Calibration data is available to the USER via
    # calibration-report and statusline. The Sentinel uses it for threshold inflation.

    # Build result
    result = {
        "lessons": lessons,
        "dead_ends": dead_ends,
        "prior_mistakes": prior_mistakes,
        "relevant_findings": relevant_findings,
        "time_gap": time_gap_info,
    }

    # Enrich with optional retrieval types
    _enrich_task_patterns(
        result,
        project_id,
        task_context,
        threshold,
        limits,
        include_eidetic,
        include_episodic,
        include_related_docs,
        include_goals,
        include_assumptions,
        include_decisions,
    )
    return _apply_context_budget(result, apply_budget)


def _enrich_check_warnings(
    warnings,
    project_id,
    current_approach,
    threshold,
    limit,
    include_findings,
    include_eidetic,
    include_goals,
    include_assumptions,
):
    """Enrich CHECK warnings with optional findings, eidetic, goals, and assumptions."""
    if include_findings and current_approach:
        try:
            raw = _search_memory_by_type(project_id, current_approach, "finding", limit, threshold)
            if raw:
                warnings["related_findings"] = [
                    {"finding": f.get("text", ""), "impact": f.get("impact", 0.5), "score": f.get("score", 0.0)}
                    for f in raw
                ]
        except Exception as e:
            logger.debug(f"CHECK findings retrieval failed: {e}")
    if include_eidetic and current_approach:
        try:
            from .vector_store import search_eidetic

            raw = search_eidetic(project_id, current_approach, min_confidence=0.5, limit=limit)
            if raw:
                warnings["eidetic_context"] = [
                    {
                        "content": e.get("content", ""),
                        "confidence": e.get("confidence", 0.5),
                        "domain": e.get("domain"),
                        "score": e.get("score", 0.0),
                    }
                    for e in raw
                ]
        except Exception as e:
            logger.debug(f"CHECK eidetic retrieval failed: {e}")
    if include_goals:
        try:
            from .vector_store import search_goals

            raw = search_goals(
                project_id, current_approach or "current work", status="in_progress", include_subtasks=True, limit=limit
            )
            raw = _reconcile_goals_against_sqlite(raw)  # retrieval hygiene: drop completed, correct stale
            if raw:
                warnings["active_goals"] = [
                    {
                        "objective": g.get("objective") or g.get("description", ""),
                        "status": g.get("status", ""),
                        "type": g.get("type", "goal"),
                        "score": g.get("score", 0.0),
                    }
                    for g in raw
                ]
        except Exception as e:
            logger.debug(f"CHECK goals retrieval failed: {e}")
    if include_assumptions:
        try:
            from .vector_store import search_assumptions

            raw = search_assumptions(
                project_id, current_approach or "current approach", status="unverified", limit=limit
            )
            if raw:
                warnings["unverified_assumptions"] = [
                    {
                        "assumption": a.get("assumption", ""),
                        "confidence": a.get("confidence", 0.5),
                        "urgency_signal": a.get("urgency_signal", 0.0),
                        "score": a.get("score", 0.0),
                    }
                    for a in raw
                ]
        except Exception as e:
            logger.debug(f"CHECK assumptions retrieval failed: {e}")


def check_against_patterns(
    project_id: str,
    current_approach: str,
    vectors: dict | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
    include_findings: bool = False,
    include_eidetic: bool = False,
    include_goals: bool = False,
    include_assumptions: bool = False,
    apply_budget: bool = True,
) -> dict[str, Any]:
    """
    CHECK hook: Validate current approach against known patterns (Noetic RAG).

    Returns warnings if the approach matches known failures or
    if vector patterns indicate risk. Optionally enriches with
    findings, eidetic facts, goals, and unverified assumptions.

    Args:
        project_id: Project ID
        current_approach: Description of current approach/plan
        vectors: Current epistemic vectors (know, uncertainty, etc.)
        threshold: Minimum similarity for dead_end match (default 0.7)
        limit: Max warnings to return (default 3)
        include_findings: Include related findings as context
        include_eidetic: Include eidetic facts (stable knowledge)
        include_goals: Include active goals for alignment check
        include_assumptions: Include unverified assumptions as risk signal
    """
    if not get_qdrant_url():
        return {"dead_end_matches": [], "mistake_matches": [], "mistake_risk": None, "has_warnings": False}

    warnings = {"dead_end_matches": [], "mistake_matches": [], "mistake_risk": None, "has_warnings": False}

    # Check if current approach matches known dead ends
    if current_approach:
        dead_ends = _search_memory_by_type(project_id, f"Approach: {current_approach}", "dead_end", limit, threshold)

        warnings["dead_end_matches"] = [
            {
                "approach": d.get("text", "").replace("DEAD END: ", "").split(" Why failed:")[0]
                if d.get("text")
                else "",
                "why_failed": d.get("text", "").split("Why failed: ")[1] if "Why failed:" in d.get("text", "") else "",
                "similarity": d.get("score", 0.0),
            }
            for d in dead_ends
        ]

        # Check if the current approach matches known MISTAKES (sibling anti-pattern
        # to dead_ends — surfaced here at the point of action, not just PREFLIGHT).
        mistakes = _search_memory_by_type(
            project_id, f"Mistake or pitfall: {current_approach}", "mistake", limit, threshold
        )
        warnings["mistake_matches"] = [
            {
                "mistake": m.get("text", "").split(" Prevention:")[0] if m.get("text") else "",
                "prevention": m.get("text", "").split("Prevention: ")[1] if "Prevention:" in m.get("text", "") else "",
                "similarity": m.get("score", 0.0),
            }
            for m in mistakes
        ]

    # Check vector patterns for mistake risk
    if vectors:
        know = vectors.get("know", 0.5)
        uncertainty = vectors.get("uncertainty", 0.5)

        # High uncertainty + low know = historical mistake pattern
        if uncertainty >= 0.5 and know <= 0.4:
            warnings["mistake_risk"] = (
                f"High risk pattern: uncertainty={uncertainty:.2f}, know={know:.2f}. "
                "Historical data shows mistakes occur when acting with high uncertainty and low knowledge. "
                "Consider more investigation before proceeding."
            )
        # Acting with very low context awareness
        elif vectors.get("context", 0.5) <= 0.3:
            warnings["mistake_risk"] = (
                f"Low context awareness ({vectors.get('context', 0):.2f}). "
                "Proceeding without understanding current state increases mistake probability."
            )

    # ANTI-GAMING: Calibration bias details (specific vectors, directions, magnitudes)
    # are no longer surfaced to the AI. They provide an "answer key" for gaming CHECK.
    # Calibration bias data is available to the USER via calibration-report and statusline.
    # The Sentinel uses this data internally for threshold inflation (dynamic_thresholds.py)
    # but does not expose specifics to the AI.

    # Enrich with optional retrieval types
    _enrich_check_warnings(
        warnings,
        project_id,
        current_approach,
        threshold,
        limit,
        include_findings,
        include_eidetic,
        include_goals,
        include_assumptions,
    )

    # Set has_warnings flag
    warnings["has_warnings"] = (
        bool(warnings["dead_end_matches"])
        or bool(warnings["mistake_matches"])
        or bool(warnings["mistake_risk"])
        or bool(warnings.get("unverified_assumptions"))
    )

    return _apply_context_budget(warnings, apply_budget)


def search_lessons_for_task(
    project_id: str,
    task_context: str,
    domain: str | None = None,
    limit: int = DEFAULT_LIMIT,
    min_score: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """
    Search for relevant lessons for a specific task.
    Optionally filter by domain.

    Args:
        project_id: Project ID
        task_context: What you're trying to do
        domain: Optional domain filter (e.g., "notebooklm", "git")
        limit: Max results
        min_score: Minimum similarity score

    Returns:
        List of lessons with name, description, domain, confidence, score
    """
    try:
        from .vector_store import _check_qdrant_available, _get_embedding_safe, _get_qdrant_client, _memory_collection

        if not _check_qdrant_available():
            return []

        qvec = _get_embedding_safe(f"Lesson for: {task_context}")
        if qvec is None:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = _get_qdrant_client()
        coll = _memory_collection(project_id)

        if not client.collection_exists(coll):
            return []

        # Build filter
        conditions = [FieldCondition(key="type", match=MatchValue(value="lesson"))]
        if domain:
            conditions.append(FieldCondition(key="domain", match=MatchValue(value=domain)))

        query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=coll, query=qvec, query_filter=query_filter, limit=limit, with_payload=True
        )

        lessons = []
        for r in results.points:
            score = getattr(r, "score", 0.0) or 0.0
            if score < min_score:
                continue

            payload = r.payload or {}
            text = payload.get("text", "")

            # Parse the embedded text format: "LESSON: name - description Domain: domain"
            name = text.replace("LESSON: ", "").split(" - ")[0] if text else ""
            desc = text.split(" - ")[1].split(" Domain:")[0] if " - " in text else ""

            lessons.append(
                {
                    "name": name,
                    "description": desc,
                    "domain": payload.get("domain", ""),
                    "confidence": payload.get("confidence", 0.8),
                    "tags": payload.get("tags", []),
                    "score": score,
                }
            )

        return lessons
    except Exception as e:
        logger.debug(f"search_lessons_for_task failed: {e}")
        return []
