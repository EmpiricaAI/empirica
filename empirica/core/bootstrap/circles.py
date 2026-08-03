"""Three-circle queries for the bootstrap aggregator.

  circle_1_active_state(...)         — recency-decayed active work
  circle_2_persistent_reference(...) — load-bearing structural items
  circle_3_topic_relevant_backlog()  — similarity-pulled backlog + anti-clobber

Each returns a dict matching its sub-tree of the wire shape (schema v2).
The composer in `payload.py` joins them.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from .decay import circle_1_weight, circle_2_weight

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────


def _open_db(project_path: Path | str) -> sqlite3.Connection:
    db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    return sqlite3.connect(str(db_path))


def _to_iso(epoch: Any) -> str | None:
    """Normalize timestamp to ISO 8601. Accepts REAL epoch or already-ISO string."""
    if epoch is None:
        return None
    if isinstance(epoch, str):
        return epoch
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _safe_json(raw: Any) -> dict:
    """Parse JSON column to dict, return {} on any error."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Circle 1: active state ──────────────────────────────────────────────


def circle_1_active_state(
    project_path: Path | str,
    project_id: str,
    *,
    limits: dict | None = None,
) -> dict[str, list[dict]]:
    """Surface in-progress goals + their subtasks + recent within-goal artifacts.

    Recency decay applies as a tiebreaker via per-type half-lives. In-progress
    goals/subtasks have ∞ half-life — status > age. Findings/decisions decay at
    30d, dead_ends/mistakes at 14d.
    """
    limits = limits or {}
    cap = lambda key, default: int(limits.get(key, default))  # noqa: E731

    conn = _open_db(project_path)
    cur = conn.cursor()
    out: dict[str, list[dict]] = {
        "in_progress_goals": [],
        "active_subtasks": [],
        "recent_findings": [],
        "recent_decisions": [],
        "recent_dead_ends": [],
        "recent_mistakes": [],
    }

    try:
        # 1. In-progress goals (∞ life while open). Order by created desc.
        cur.execute(
            "SELECT id, objective, status, is_completed, goal_data, "
            "session_id, transaction_id, created_timestamp "
            "FROM goals WHERE project_id = ? AND is_completed = 0 "
            "ORDER BY created_timestamp DESC LIMIT ?",
            (project_id, cap("in_progress_goals", 10)),
        )
        goal_rows = cur.fetchall()
        active_goal_ids: list[str] = []
        for r in goal_rows:
            gd = _safe_json(r[4])
            weight = circle_1_weight(0.9, "goal_open", r[7])  # high default impact for active goals
            out["in_progress_goals"].append(
                {
                    "id": r[0],
                    "type": "goal",
                    "objective": r[1],
                    "status": r[2],
                    "is_completed": bool(r[3]),
                    "subtasks_inline": gd.get("subtasks", [])[:5],  # quick peek
                    "session_id": r[5],
                    "transaction_id": r[6],
                    "created_at": _to_iso(r[7]),
                    "weight": weight,
                    "surface_reason": "active",
                }
            )
            active_goal_ids.append(r[0])

        # 2. Active subtasks. The subtasks table is keyed off the parent goal.
        if active_goal_ids:
            placeholders = ",".join("?" * len(active_goal_ids))
            try:
                cur.execute(
                    f"SELECT id, name, status, importance, goal_id, created_timestamp "
                    f"FROM subtasks WHERE goal_id IN ({placeholders}) "
                    f"AND COALESCE(is_completed, 0) = 0 "
                    f"ORDER BY created_timestamp DESC LIMIT ?",
                    (*active_goal_ids, cap("active_subtasks", 20)),
                )
                for r in cur.fetchall():
                    weight = circle_1_weight(0.8, "subtask_open", r[5])
                    out["active_subtasks"].append(
                        {
                            "id": r[0],
                            "type": "subtask",
                            "name": r[1],
                            "status": r[2],
                            "importance": r[3],
                            "goal_id": r[4],
                            "created_at": _to_iso(r[5]),
                            "weight": weight,
                            "surface_reason": "active",
                        }
                    )
            except sqlite3.OperationalError:
                # subtasks table shape differs across migrations; skip gracefully
                pass

        # 3. Recent findings within active goals (last 7 days, decayed)
        cutoff_7d = time.time() - 7 * 24 * 3600
        if active_goal_ids:
            placeholders = ",".join("?" * len(active_goal_ids))
            cur.execute(
                f"SELECT id, finding, finding_data, impact, epistemic_source, "
                f"session_id, goal_id, transaction_id, created_timestamp "
                f"FROM project_findings "
                # is_resolved = 0: `resolve_finding` documents itself as "kept for
                # history, dropped from live retrieval" and this query never
                # honoured it, so a finding explicitly marked superseded kept
                # surfacing at full weight beside the artifact that replaced it.
                # Unknowns were already filtered here; findings were not.
                f"WHERE project_id = ? AND created_timestamp >= ? AND is_resolved = 0 "
                f"AND goal_id IN ({placeholders}) "
                f"ORDER BY created_timestamp DESC LIMIT ?",
                (project_id, cutoff_7d, *active_goal_ids, cap("recent_findings", 10)),
            )
            for r in cur.fetchall():
                weight = circle_1_weight(r[3], "finding", r[8])
                out["recent_findings"].append(
                    {
                        "id": r[0],
                        "type": "finding",
                        "summary": (r[1] or "")[:120],
                        "body": r[1] or "",
                        "impact": r[3],
                        "epistemic_source": r[4],
                        "session_id": r[5],
                        "goal_id": r[6],
                        "transaction_id": r[7],
                        "created_at": _to_iso(r[8]),
                        "weight": weight,
                        "surface_reason": "active",
                    }
                )

        # 4. Recent decisions within active goals (last 7d, decayed)
        if active_goal_ids:
            placeholders = ",".join("?" * len(active_goal_ids))
            cur.execute(
                f"SELECT id, choice, rationale, alternatives, "
                f"confidence_at_decision, reversibility, outcome, "
                f"session_id, goal_id, transaction_id, created_timestamp, "
                f"epistemic_source "
                f"FROM decisions "
                f"WHERE project_id = ? AND created_timestamp >= ? "
                f"AND goal_id IN ({placeholders}) "
                f"ORDER BY created_timestamp DESC LIMIT ?",
                (project_id, cutoff_7d, *active_goal_ids, cap("recent_decisions", 5)),
            )
            for r in cur.fetchall():
                weight = circle_1_weight(r[4], "decision_recent", r[10])
                out["recent_decisions"].append(
                    {
                        "id": r[0],
                        "type": "decision",
                        "choice": r[1],
                        "rationale": r[2],
                        "alternatives": r[3],
                        "confidence_at_decision": r[4],
                        "reversibility": r[5],
                        "outcome": r[6],
                        "session_id": r[7],
                        "goal_id": r[8],
                        "transaction_id": r[9],
                        "created_at": _to_iso(r[10]),
                        "epistemic_source": r[11],
                        "weight": weight,
                        "surface_reason": "active",
                    }
                )

        # 5. Recent dead-ends (last 14d — type-specific window)
        cutoff_14d = time.time() - 14 * 24 * 3600
        cur.execute(
            "SELECT id, approach, why_failed, dead_end_data, impact, "
            "epistemic_source, session_id, goal_id, transaction_id, created_timestamp "
            "FROM project_dead_ends "
            "WHERE project_id = ? AND created_timestamp >= ? "
            "ORDER BY created_timestamp DESC LIMIT ?",
            (project_id, cutoff_14d, cap("recent_dead_ends", 5)),
        )
        for r in cur.fetchall():
            weight = circle_1_weight(r[4], "dead_end", r[9])
            out["recent_dead_ends"].append(
                {
                    "id": r[0],
                    "type": "dead_end",
                    "approach": r[1],
                    "why_failed": r[2],
                    "impact": r[4],
                    "epistemic_source": r[5],
                    "session_id": r[6],
                    "goal_id": r[7],
                    "transaction_id": r[8],
                    "created_at": _to_iso(r[9]),
                    "weight": weight,
                    "surface_reason": "active",
                }
            )

        # 6. Recent mistakes (last 14d)
        try:
            cur.execute(
                "SELECT id, mistake, why_wrong, prevention, mistake_data, "
                "epistemic_source, session_id, goal_id, transaction_id, created_timestamp "
                "FROM mistakes_made "
                "WHERE project_id = ? AND created_timestamp >= ? "
                "ORDER BY created_timestamp DESC LIMIT ?",
                (project_id, cutoff_14d, cap("recent_mistakes", 5)),
            )
            for r in cur.fetchall():
                weight = circle_1_weight(0.6, "mistake", r[9])
                out["recent_mistakes"].append(
                    {
                        "id": r[0],
                        "type": "mistake",
                        "mistake": r[1],
                        "why_wrong": r[2],
                        "prevention": r[3],
                        "epistemic_source": r[5],
                        "session_id": r[6],
                        "goal_id": r[7],
                        "transaction_id": r[8],
                        "created_at": _to_iso(r[9]),
                        "weight": weight,
                        "surface_reason": "active",
                    }
                )
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()

    return out


# ── Circle 2: persistent reference ──────────────────────────────────────


def circle_2_persistent_reference(
    project_path: Path | str,
    project_id: str,
    *,
    limits: dict | None = None,
) -> dict[str, list[dict]]:
    """Surface load-bearing structural items. NO recency decay.

    - Decisions with active outcome (outcome IS NULL): architectural choices.
    - Verified or falsified assumptions: now ground truth, audit trail.
    - Sources: citation base.
    """
    limits = limits or {}
    cap = lambda key, default: int(limits.get(key, default))  # noqa: E731

    conn = _open_db(project_path)
    cur = conn.cursor()
    out: dict[str, list[dict]] = {
        "decisions_with_active_outcome": [],
        "verified_assumptions": [],
        "sources": [],
    }

    try:
        # 1. Decisions with active outcome (outcome IS NULL = rationale still load-bearing)
        cur.execute(
            "SELECT id, choice, rationale, alternatives, "
            "confidence_at_decision, reversibility, outcome, "
            "session_id, transaction_id, created_timestamp, epistemic_source "
            "FROM decisions WHERE project_id = ? AND outcome IS NULL "
            "ORDER BY confidence_at_decision DESC NULLS LAST, created_timestamp DESC "
            "LIMIT ?",
            (project_id, cap("decisions_with_active_outcome", 10)),
        )
        for r in cur.fetchall():
            weight = circle_2_weight(r[4], "decision")
            out["decisions_with_active_outcome"].append(
                {
                    "id": r[0],
                    "type": "decision",
                    "choice": r[1],
                    "rationale": r[2],
                    "alternatives": r[3],
                    "confidence_at_decision": r[4],
                    "reversibility": r[5],
                    "outcome": r[6],
                    "session_id": r[7],
                    "transaction_id": r[8],
                    "created_at": _to_iso(r[9]),
                    "epistemic_source": r[10],
                    "weight": weight,
                    "surface_reason": "persistent",
                }
            )

        # 2. Verified / falsified assumptions
        cur.execute(
            "SELECT id, assumption, confidence, status, resolution_finding_id, "
            "session_id, transaction_id, created_timestamp, resolved_timestamp, "
            "epistemic_source "
            "FROM assumptions WHERE project_id = ? "
            "AND status IN ('verified', 'falsified') "
            "ORDER BY confidence DESC, created_timestamp DESC LIMIT ?",
            (project_id, cap("verified_assumptions", 10)),
        )
        for r in cur.fetchall():
            weight = circle_2_weight(r[2], "assumption")
            out["verified_assumptions"].append(
                {
                    "id": r[0],
                    "type": "assumption",
                    "summary": (r[1] or "")[:120],
                    "body": r[1] or "",
                    "confidence": r[2],
                    "status": r[3],
                    "resolution_finding_id": r[4],
                    "session_id": r[5],
                    "transaction_id": r[6],
                    "created_at": _to_iso(r[7]),
                    "resolved_at": _to_iso(r[8]),
                    "epistemic_source": r[9],
                    "weight": weight,
                    "surface_reason": "persistent",
                }
            )

        # 3. Sources — never decay. Order by confidence + recency to avoid arbitrary order.
        cur.execute(
            "SELECT id, title, source_url, source_type, description, "
            "confidence, epistemic_layer, session_id, discovered_by_ai, discovered_at "
            "FROM epistemic_sources WHERE project_id = ? "
            "ORDER BY confidence DESC, discovered_at DESC LIMIT ?",
            (project_id, cap("sources", 10)),
        )
        for r in cur.fetchall():
            weight = circle_2_weight(r[5], "source")
            out["sources"].append(
                {
                    "id": r[0],
                    "type": "source",
                    "title": r[1],
                    "url": r[2],
                    "source_type": r[3],
                    "description": r[4],
                    "confidence": r[5],
                    "epistemic_layer": r[6],
                    "session_id": r[7],
                    "discovered_by_ai": r[8],
                    "created_at": _to_iso(r[9]),
                    "weight": weight,
                    "surface_reason": "persistent",
                }
            )
    finally:
        conn.close()

    return out


# ── Circle 3: topic-relevant backlog ────────────────────────────────────


def circle_3_topic_relevant_backlog(
    project_path: Path | str,
    project_id: str,
    topic: dict,
    *,
    limits: dict | None = None,
) -> dict[str, list[dict]]:
    """Similarity-pulled backlog + anti-clobber for completed work.

    If `topic.detected` is False: skip entirely, return empty arrays.

    If Qdrant is reachable: embed `topic.text`, run cosine search filtered by
    artifact type, threshold gate, per-type budgets.

    If Qdrant unavailable: graceful fallback — return open backlog
    (unknowns, assumptions, planned goals) ranked by impact only,
    with smaller per-type budgets and NO completed-work surfacing.
    """
    limits = limits or {}

    out: dict[str, list[dict]] = {
        "open_unknowns": [],
        "open_assumptions": [],
        "planned_goals": [],
        "completed_goals_relevant": [],
        "resolved_unknowns_relevant": [],
        "dead_ends_relevant": [],
    }

    if not topic.get("detected"):
        return out

    threshold = float(topic.get("similarity_threshold", 0.65))
    topic_text = topic.get("text") or ""
    if not topic_text:
        return out

    # Try Qdrant similarity pull
    qdrant_results = _qdrant_similarity_pull(project_id, topic_text, threshold)

    if qdrant_results is None:
        # Fallback: impact-ranked open backlog only
        return _fallback_open_backlog_only(project_path, project_id, limits)

    # Qdrant returned a hit set keyed by artifact id → similarity score
    # Now enrich each by querying the source table for full row data,
    # bucketing into out[] by type/status, capped per slot.
    return _enrich_qdrant_hits(project_path, project_id, qdrant_results, limits, threshold)


def _qdrant_similarity_pull(project_id: str, topic_text: str, threshold: float) -> dict[str, float] | None:
    """Embed topic_text, run Qdrant cosine search across project's eidetic
    + episodic collections. Return {artifact_id: similarity_score} for hits
    above threshold. Return None if Qdrant is unreachable.
    """
    try:
        from empirica.core.qdrant.connection import (
            _get_embedding_safe,
            _get_qdrant_client,
        )
    except ImportError:
        return None

    client = _get_qdrant_client()
    if client is None:
        return None

    try:
        vector = _get_embedding_safe(topic_text)
    except Exception as e:
        logger.debug(f"circle_3: _get_embedding_safe failed: {e}")
        return None
    if vector is None:
        return None

    # Search across the project's memory collection (artifacts).
    # query_points is the modern Qdrant API; falls back to search if unavailable.
    hits: dict[str, float] = {}
    try:
        from empirica.core.qdrant.collections import _memory_collection

        collection = _memory_collection(project_id)
        try:
            response = client.query_points(
                collection_name=collection,
                query=vector,
                limit=100,
                score_threshold=threshold,
            )
            results = response.points  # query_points returns wrapped object
        except Exception:
            # Older Qdrant clients still expose .search()
            results = client.search(  # type: ignore[attr-defined]
                collection_name=collection,
                query_vector=vector,
                limit=100,
                score_threshold=threshold,
            )
        for r in results:
            payload = getattr(r, "payload", None) or {}
            art_id = payload.get("artifact_id") or payload.get("id")
            if art_id and getattr(r, "score", 0) >= threshold:
                hits[str(art_id)] = float(r.score)
    except Exception as e:
        logger.debug(f"circle_3: qdrant search failed (treating as unreachable): {e}")
        return None

    return hits


def _enrich_qdrant_hits(
    project_path: Path | str,
    project_id: str,
    qdrant_hits: dict[str, float],
    limits: dict,
    threshold: float,
) -> dict[str, list[dict]]:
    """Given a hit set {id: score}, enrich each from sqlite and bucket by type."""
    out: dict[str, list[dict]] = {
        "open_unknowns": [],
        "open_assumptions": [],
        "planned_goals": [],
        "completed_goals_relevant": [],
        "resolved_unknowns_relevant": [],
        "dead_ends_relevant": [],
    }

    if not qdrant_hits:
        return out

    conn = _open_db(project_path)
    cur = conn.cursor()
    placeholders = ",".join("?" * len(qdrant_hits))
    ids = list(qdrant_hits.keys())

    try:
        # Open unknowns
        cur.execute(
            f"SELECT id, unknown, impact, created_timestamp "
            f"FROM project_unknowns WHERE id IN ({placeholders}) "
            f"AND project_id = ? AND is_resolved = 0",
            (*ids, project_id),
        )
        for r in cur.fetchall():
            out["open_unknowns"].append(
                _pack_topic_match(
                    r[0],
                    "unknown",
                    {"summary": (r[1] or "")[:120], "body": r[1] or "", "impact": r[2], "created_at": _to_iso(r[3])},
                    qdrant_hits[r[0]],
                )
            )

        # Resolved unknowns (anti-clobber: similar past resolutions)
        cur.execute(
            f"SELECT id, unknown, resolved_by, resolved_timestamp, created_timestamp "
            f"FROM project_unknowns WHERE id IN ({placeholders}) "
            f"AND project_id = ? AND is_resolved = 1",
            (*ids, project_id),
        )
        for r in cur.fetchall():
            out["resolved_unknowns_relevant"].append(
                _pack_topic_match(
                    r[0],
                    "unknown",
                    {
                        "summary": (r[1] or "")[:120],
                        "body": r[1] or "",
                        "resolved_by": r[2],
                        "resolved_at": _to_iso(r[3]),
                        "created_at": _to_iso(r[4]),
                        "status": "resolved",
                    },
                    qdrant_hits[r[0]],
                )
            )

        # Open assumptions
        cur.execute(
            f"SELECT id, assumption, confidence, status, created_timestamp "
            f"FROM assumptions WHERE id IN ({placeholders}) "
            f"AND project_id = ? AND status = 'unverified'",
            (*ids, project_id),
        )
        for r in cur.fetchall():
            out["open_assumptions"].append(
                _pack_topic_match(
                    r[0],
                    "assumption",
                    {
                        "summary": (r[1] or "")[:120],
                        "body": r[1] or "",
                        "confidence": r[2],
                        "status": r[3],
                        "created_at": _to_iso(r[4]),
                    },
                    qdrant_hits[r[0]],
                )
            )

        # Planned goals
        cur.execute(
            f"SELECT id, objective, status, created_timestamp "
            f"FROM goals WHERE id IN ({placeholders}) "
            f"AND project_id = ? AND status = 'planned'",
            (*ids, project_id),
        )
        for r in cur.fetchall():
            out["planned_goals"].append(
                _pack_topic_match(
                    r[0],
                    "goal",
                    {"objective": r[1], "status": r[2], "is_completed": False, "created_at": _to_iso(r[3])},
                    qdrant_hits[r[0]],
                )
            )

        # Completed goals (anti-clobber)
        cur.execute(
            f"SELECT id, objective, status, completed_timestamp, created_timestamp "
            f"FROM goals WHERE id IN ({placeholders}) "
            f"AND project_id = ? AND is_completed = 1",
            (*ids, project_id),
        )
        for r in cur.fetchall():
            out["completed_goals_relevant"].append(
                _pack_topic_match(
                    r[0],
                    "goal",
                    {
                        "objective": r[1],
                        "status": r[2],
                        "is_completed": True,
                        "completed_at": _to_iso(r[3]),
                        "created_at": _to_iso(r[4]),
                    },
                    qdrant_hits[r[0]],
                )
            )

        # Dead-ends matching topic
        cur.execute(
            f"SELECT id, approach, why_failed, impact, created_timestamp "
            f"FROM project_dead_ends WHERE id IN ({placeholders}) "
            f"AND project_id = ?",
            (*ids, project_id),
        )
        for r in cur.fetchall():
            out["dead_ends_relevant"].append(
                _pack_topic_match(
                    r[0],
                    "dead_end",
                    {"approach": r[1], "why_failed": r[2], "impact": r[3], "created_at": _to_iso(r[4])},
                    qdrant_hits[r[0]],
                )
            )
    finally:
        conn.close()

    # Apply per-type caps, sorted by similarity desc
    for key in out:
        out[key].sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
        out[key] = out[key][: _default_cap(key, limits)]

    return out


def _default_cap(key: str, limits: dict) -> int:
    """Per-type slot budgets for circle 3."""
    defaults = {
        "open_unknowns": 5,
        "open_assumptions": 5,
        "planned_goals": 5,
        "completed_goals_relevant": 3,
        "resolved_unknowns_relevant": 5,
        "dead_ends_relevant": 3,
    }
    return int(limits.get(key, defaults.get(key, 5)))


def _pack_topic_match(art_id: str, atype: str, fields: dict, similarity: float) -> dict:
    """Build a topic-match item with consistent surface_reason + similarity_score."""
    item = {
        "id": art_id,
        "type": atype,
        "weight": round(similarity, 3),
        "similarity_score": round(similarity, 3),
        "surface_reason": "topic_match",
    }
    item.update(fields)
    return item


def _fallback_open_backlog_only(
    project_path: Path | str,
    project_id: str,
    limits: dict,
) -> dict[str, list[dict]]:
    """Qdrant unreachable fallback: open backlog by impact, no anti-clobber."""
    cap = lambda key, default: int(limits.get(key, default))  # noqa: E731

    conn = _open_db(project_path)
    cur = conn.cursor()
    out: dict[str, list[dict]] = {
        "open_unknowns": [],
        "open_assumptions": [],
        "planned_goals": [],
        "completed_goals_relevant": [],
        "resolved_unknowns_relevant": [],
        "dead_ends_relevant": [],
    }

    try:
        # Open unknowns by impact
        cur.execute(
            "SELECT id, unknown, impact, created_timestamp "
            "FROM project_unknowns WHERE project_id = ? AND is_resolved = 0 "
            "ORDER BY impact DESC, created_timestamp DESC LIMIT ?",
            (project_id, cap("open_unknowns", 5)),
        )
        for r in cur.fetchall():
            out["open_unknowns"].append(
                {
                    "id": r[0],
                    "type": "unknown",
                    "summary": (r[1] or "")[:120],
                    "body": r[1] or "",
                    "impact": r[2],
                    "created_at": _to_iso(r[3]),
                    "weight": float(r[2] or 0.5),
                    "surface_reason": "topic_match_fallback",
                }
            )

        # Open assumptions by confidence (high-stakes unverified surface first)
        cur.execute(
            "SELECT id, assumption, confidence, status, created_timestamp "
            "FROM assumptions WHERE project_id = ? AND status = 'unverified' "
            "ORDER BY confidence DESC, created_timestamp DESC LIMIT ?",
            (project_id, cap("open_assumptions", 5)),
        )
        for r in cur.fetchall():
            out["open_assumptions"].append(
                {
                    "id": r[0],
                    "type": "assumption",
                    "summary": (r[1] or "")[:120],
                    "body": r[1] or "",
                    "confidence": r[2],
                    "status": r[3],
                    "created_at": _to_iso(r[4]),
                    "weight": float(r[2] or 0.5),
                    "surface_reason": "topic_match_fallback",
                }
            )

        # Planned goals (recent)
        cur.execute(
            "SELECT id, objective, status, created_timestamp "
            "FROM goals WHERE project_id = ? AND status = 'planned' "
            "ORDER BY created_timestamp DESC LIMIT ?",
            (project_id, cap("planned_goals", 5)),
        )
        for r in cur.fetchall():
            out["planned_goals"].append(
                {
                    "id": r[0],
                    "type": "goal",
                    "objective": r[1],
                    "status": r[2],
                    "is_completed": False,
                    "created_at": _to_iso(r[3]),
                    "weight": 0.5,
                    "surface_reason": "topic_match_fallback",
                }
            )
    finally:
        conn.close()

    return out
