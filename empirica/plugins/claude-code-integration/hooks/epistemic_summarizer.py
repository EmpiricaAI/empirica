"""
Epistemic Summarizer - Confidence-weighted context for post-compaction.

Replaces chronological ordering with epistemic relevance ranking.
Design principle: Trust the system, observe results, iterate. Hedging prevents real testing.
"""

import math
import sqlite3
import time
from pathlib import Path

# Type confidence scores (epistemic reliability)
# These can be tuned based on observed gaps after compaction
TYPE_CONFIDENCE = {
    "finding": 0.9,  # Validated learnings - high confidence
    "dead_end": 0.85,  # Important to avoid - cost was paid
    "mistake": 0.85,  # Cost was paid, lesson is real
    "subtask": 0.80,  # Structured work items - actionable
    "goal": 0.75,  # Structural, but context-dependent
    "unknown": 0.6,  # Questions, inherently uncertain
}

# Importance to impact mapping for subtasks (they don't have explicit impact scores)
IMPORTANCE_TO_IMPACT = {
    "critical": 0.95,
    "high": 0.80,
    "medium": 0.60,
    "low": 0.40,
}

# Recency decay parameters
# Half-life of 24 hours means items lose half their recency weight per day
RECENCY_HALF_LIFE_HOURS = 24
DECAY_CONSTANT = math.log(2) / RECENCY_HALF_LIFE_HOURS  # ~0.029

# Weight for an item we cannot date. Deliberately mid-scale: an undateable item
# should rank below anything fresh and above nothing at all. Equivalent to ~4.6
# days old, so a missing timestamp DEGRADES the ordering rather than inverting
# it — the previous behaviour (default to now) put undateable items at the very
# top forever.
NEUTRAL_RECENCY = 0.05

# --- Relevance -------------------------------------------------------------
#
# Relevance is FIRST CLASS: it drives what is RETRIEVED, not merely how a
# fixed set is ordered. Re-ranking a pool that was already cut to "top N by
# impact" cannot surface the artifact ranked N+1, however relevant it is — so
# a task-driven query runs and its results are merged into the pool.
#
# Blend is ADDITIVE, not multiplicative. A multiplicative relevance term would
# zero out anything the query does not match, which destroys the case that
# matters most: a dead-end from months ago about exactly this task SHOULD
# surface. The objection was never to age — it was to ancient IRRELEVANT
# artifacts crowding out today's work.
RELEVANCE_SHARE = 0.65
RECENCY_SHARE = 0.35

# Used when no relevance signal exists (no task_context, or Qdrant unreachable).
# Mid-scale so the blend degrades to recency-dominated ordering rather than
# collapsing — but the caller MUST announce the degradation; see
# `relevance_unavailable_note`.
NEUTRAL_RELEVANCE = 0.5


def calculate_weight(item: dict, item_type: str, relevance: float | None = None) -> float:
    """
    Calculate epistemic weight for ranking.

    Formula: weight = impact * type_confidence * (RELEVANCE_SHARE * relevance
                                                  + RECENCY_SHARE * recency)

    `relevance` is the semantic match against the current task_context, in
    [0, 1]. Passing None means "no signal available" and uses
    NEUTRAL_RELEVANCE — which the caller must ANNOUNCE rather than let the
    reader assume the block is task-matched when it is not.

    Args:
        item: Dictionary with 'impact' and timestamp fields
        item_type: One of 'finding', 'dead_end', 'mistake', 'subtask', 'goal', 'unknown'

    Returns:
        Weight score between 0.0 and 1.0
    """
    # Impact from database, default 0.5 if not set
    # Subtasks use importance field instead of impact
    if item_type == "subtask":
        importance = item.get("importance", "medium")
        impact = IMPORTANCE_TO_IMPACT.get(importance, 0.6)
    else:
        impact = item.get("impact", 0.5)

    # Type-based confidence multiplier
    type_conf = TYPE_CONFIDENCE.get(item_type, 0.5)

    # Calculate recency decay
    # Try multiple timestamp field names for compatibility
    timestamp = item.get("created_timestamp") or item.get("timestamp") or item.get("created_at")

    # Handle string timestamps
    if isinstance(timestamp, str):
        try:
            from datetime import datetime

            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            timestamp = None

    if timestamp is None:
        # An item we cannot date must NOT be treated as new.
        #
        # This defaulted to time.time(), which gave every undateable item
        # recency=1.0 — the maximum — permanently. Measured: an item with no
        # timestamp scored 0.81, identical to one created this second, while a
        # correctly-dated 8-month-old scored 0.0. So the entire recency term was
        # inert for any item whose fetch omitted the column, and `goals` is
        # exactly such a fetch: project-bootstrap returns goals with no
        # timestamp field at all.
        #
        # The result was an EPISTEMIC FOCUS block that surfaced the same
        # high-impact items every session regardless of age or of what the work
        # was about, which is how 2025-12 findings stayed pinned at the top for
        # eight months.
        #
        # Neutral rather than punitive: NEUTRAL_RECENCY ranks an undateable item
        # below anything fresh and above nothing, so a missing column degrades
        # the ordering instead of inverting it.
        recency = NEUTRAL_RECENCY
    else:
        age_hours = (time.time() - timestamp) / 3600
        recency = math.exp(-DECAY_CONSTANT * age_hours)

    if relevance is None:
        # NO SIGNAL for the whole block — pure recency, exactly as before.
        #
        # Blending a neutral constant here would hand every ancient artifact a
        # floor of RELEVANCE_SHARE * 0.5, reintroducing the problem this change
        # exists to remove. Degrading means "behave as you did before relevance
        # existed", not "invent a middling score".
        blended = recency
    else:
        # SIGNAL AVAILABLE — every item is scored on the same scale, including
        # items the query did not match (relevance 0.0). Mixing the two modes
        # within one block would let a MATCHED item score lower than an
        # unmatched one, which is incoherent: matching must never hurt.
        rel = max(0.0, min(1.0, relevance))
        blended = (RELEVANCE_SHARE * rel) + (RECENCY_SHARE * recency)
    return round(impact * type_conf * blended, 2)


def rank_items(
    items: list[tuple[dict, str]],
    relevance_by_id: dict[str, float] | None = None,
) -> list[tuple[float, dict, str]]:
    """
    Rank items by epistemic weight, blended with task relevance.

    Args:
        items: List of (item_dict, item_type) tuples
        relevance_by_id: artifact id -> semantic score for the current task.
            None (or a missing id) means no signal for that item.

    Returns:
        List of (weight, item_dict, item_type) sorted descending by weight
    """
    weighted = []
    for item, item_type in items:
        # None means "no signal for this BLOCK". When a signal exists, an item
        # absent from the results genuinely scored 0 relevance — not unknown.
        rel = None if relevance_by_id is None else relevance_by_id.get(str(item.get("id", "")), 0.0)
        weight = calculate_weight(item, item_type, relevance=rel)
        weighted.append((weight, item, item_type))

    return sorted(weighted, key=lambda x: x[0], reverse=True)


def fetch_relevance(
    project_id: str | None, task_context: str | None, limit: int = 25
) -> tuple[dict | None, str | None]:
    """Semantic scores for the current task. Returns (scores_by_id, degradation_note).

    The note is NOT decoration. A silent empty result here is indistinguishable
    from "nothing is relevant", and the block would then read as task-matched
    while being ranked purely on recency — fallback-masks-primary, on the
    surface that shapes what every session pays attention to.
    """
    if not task_context or not project_id:
        return None, "no task_context — ranked by recency and impact only"
    try:
        # The `memory` collection, NOT `epistemics`. Measured on this store:
        # epistemics holds 0 points while memory holds 5,734 — finding-log,
        # decision-log and the rest write there. Querying epistemics would have
        # made this feature ship INERT: every call returning nothing, degrading
        # forever, behind a green test suite and a plausible design.
        from empirica.core.qdrant.memory import search as memory_search

        results = memory_search(project_id, task_context, kind="memory", limit=limit)
    except Exception as exc:  # import or transport failure
        return None, f"relevance unavailable ({type(exc).__name__}) — ranked by recency and impact only"

    hits = []
    for bucket in (results or {}).values():
        hits.extend(bucket or [])
    if not hits:
        return None, "relevance unavailable (semantic search returned nothing) — ranked by recency and impact only"

    # artifact_id is the DB `id`, which is what the pool items carry.
    scores: dict[str, float] = {}
    for h in hits:
        aid = h.get("artifact_id") or h.get("id")
        if aid is None:
            continue
        score = float(h.get("score") or 0.0)
        key = str(aid)
        if score > scores.get(key, -1.0):
            scores[key] = score
    return (scores or None), (None if scores else "relevance unavailable (no artifact ids in results)")


def format_item(weight: float, item: dict, item_type: str) -> str:
    """Format a single item for display."""
    type_label = item_type.replace("_", "-").title()

    if item_type == "finding":
        text = item.get("finding", "Unknown finding")
    elif item_type == "unknown":
        text = item.get("unknown", "Unknown question")
    elif item_type == "dead_end":
        approach = item.get("approach", "?")
        why_failed = item.get("why_failed", "?")
        text = f"{approach} → {why_failed}"
    elif item_type == "goal":
        text = item.get("objective", "Unknown goal")
        status = item.get("status", "pending")
        text = f"{text} ({status})"
    elif item_type == "subtask":
        # internal type tag stays 'subtask' (matches SubTask class + subtasks table);
        # user-visible fallback string says 'task' (matches CLI vocabulary)
        text = item.get("description", "Unknown task")
        importance = item.get("importance", "medium")
        goal_context = item.get("goal_objective", "")
        if goal_context:
            text = f"[{importance}] {text} (→ {goal_context})"
        else:
            text = f"[{importance}] {text}"
    elif item_type == "mistake":
        text = item.get("mistake", "Unknown mistake")
    else:
        text = str(item)

    # Truncate long text
    if len(text) > 100:
        text = text[:97] + "..."

    return f"- [{weight:.2f}] **{type_label}:** {text}"


def _collect_typed_items(
    findings: list[dict],
    unknowns: list[dict],
    dead_ends: list[dict],
    goals: list[dict],
    mistakes: list[dict] | None = None,
    subtasks: list[dict] | None = None,
) -> list[tuple[dict, str]]:
    """Collect all artifact items with their type labels."""
    all_items: list[tuple[dict, str]] = []
    for f in findings or []:
        all_items.append((f, "finding"))
    for u in unknowns or []:
        all_items.append((u, "unknown"))
    for d in dead_ends or []:
        all_items.append((d, "dead_end"))
    for g in goals or []:
        all_items.append((g, "goal"))
    for m in mistakes or []:
        all_items.append((m, "mistake"))
    for st in subtasks or []:
        all_items.append((st, "subtask"))
    return all_items


def _format_tier(
    lines: list[str],
    header: str,
    items: list[tuple[float, dict, str]],
) -> None:
    """Append a weight-tier section to output lines."""
    if not items:
        return
    lines.append(header)
    for w, i, t in items:
        lines.append(format_item(w, i, t))
    lines.append("")


def format_epistemic_focus(
    findings: list[dict],
    unknowns: list[dict],
    dead_ends: list[dict],
    goals: list[dict],
    mistakes: list[dict] | None = None,
    subtasks: list[dict] | None = None,
    max_items: int = 15,
    session_id: str | None = None,
    task_context: str | None = None,
    project_id: str | None = None,
) -> str:
    """
    Format epistemically-weighted summary for injection.

    Returns markdown with items ranked by confidence * impact * recency.
    Pure epistemic ranking - no chronological fallback.

    Args:
        findings: List of finding dicts with 'finding', 'impact', timestamp
        unknowns: List of unknown dicts with 'unknown', 'impact', timestamp
        dead_ends: List of dead_end dicts with 'approach', 'why_failed', timestamp
        goals: List of goal dicts with 'objective', 'status', timestamp
        mistakes: Optional list of mistake dicts
        subtasks: Optional list of subtask dicts with 'description', 'importance', timestamp
        max_items: Maximum items to include in output
        session_id: Optional session ID for retrieval guidance

    Returns:
        Markdown-formatted epistemic focus section
    """
    all_items = _collect_typed_items(
        findings,
        unknowns,
        dead_ends,
        goals,
        mistakes,
        subtasks,
    )

    if not all_items:
        return "## EPISTEMIC FOCUS\n\n*No breadcrumbs logged yet.*\n"

    relevance, degraded = fetch_relevance(project_id, task_context)
    ranked = rank_items(all_items, relevance_by_id=relevance)[:max_items]

    # Group by weight tier
    critical = [(w, i, t) for w, i, t in ranked if w > 0.7]
    important = [(w, i, t) for w, i, t in ranked if 0.4 <= w <= 0.7]
    context_items = [(w, i, t) for w, i, t in ranked if w < 0.4]

    header = "## EPISTEMIC FOCUS (Confidence-Ranked)" if degraded else "## EPISTEMIC FOCUS (Relevance-Ranked)"
    lines = [header + "\n"]
    if degraded:
        # Say it. A block that silently ranks on recency while the reader
        # assumes it is task-matched is worse than one that admits it — they
        # would trust it for a question it never answered.
        lines.append(f"> ⚠️ {degraded}\n")
    _format_tier(lines, "### Critical (weight > 0.7)", critical)
    _format_tier(lines, "### Important (weight 0.4-0.7)", important)
    _format_tier(lines, "### Context (weight < 0.4)", context_items)
    lines.append("---")

    # Retrieval guidance footer
    session_hint = f" --session-id {session_id}" if session_id else ""
    lines.append(f"📊 **{len(ranked)} items ranked** | For deeper context:")
    lines.append(f"- `empirica project-bootstrap{session_hint}` (full load + subtasks)")
    lines.append('- `empirica project-search --task "<query>"` (Qdrant semantic)')
    lines.append("- `git notes show --ref=breadcrumbs HEAD` (session narrative)\n")

    return "\n".join(lines)


def log_compact_effectiveness(
    session_id: str, pre_vectors: dict, post_check_vectors: dict, items_surfaced: int, db_path: Path | None = None
) -> dict:
    """
    Log effectiveness metrics for each compact.

    Tracked metrics:
    - know_recovery: How much knowledge was preserved (post/pre ratio)
    - context_recovery: Context understanding after compact
    - items_surfaced: Number of items in epistemic focus
    - uncertainty_delta: Change in uncertainty post-compact
    - effectiveness_score: Combined metric

    Args:
        session_id: The session being compacted
        pre_vectors: Epistemic vectors before compact
        post_check_vectors: Epistemic vectors after compact CHECK
        items_surfaced: Number of items shown in epistemic focus
        db_path: Path to database (defaults to .empirica/sessions/sessions.db)

    Returns:
        Dict with calculated metrics
    """
    if db_path is None:
        db_path = Path.cwd() / ".empirica" / "sessions" / "sessions.db"

    # Calculate metrics
    pre_know = pre_vectors.get("know", 0.5)
    post_know = post_check_vectors.get("know", 0.5)
    know_recovery = post_know / max(pre_know, 0.1)

    pre_context = pre_vectors.get("context", 0.5)
    post_context = post_check_vectors.get("context", 0.5)
    context_recovery = post_context / max(pre_context, 0.1)

    pre_uncertainty = pre_vectors.get("uncertainty", 0.5)
    post_uncertainty = post_check_vectors.get("uncertainty", 0.5)
    uncertainty_delta = post_uncertainty - pre_uncertainty

    # Effectiveness score: high recovery + low uncertainty increase
    effectiveness = (know_recovery + context_recovery) / 2 - uncertainty_delta

    metrics = {
        "session_id": session_id,
        "timestamp": time.time(),
        "pre_know": pre_know,
        "post_know": post_know,
        "know_recovery": round(know_recovery, 3),
        "pre_context": pre_context,
        "post_context": post_context,
        "context_recovery": round(context_recovery, 3),
        "pre_uncertainty": pre_uncertainty,
        "post_uncertainty": post_uncertainty,
        "uncertainty_delta": round(uncertainty_delta, 3),
        "items_surfaced": items_surfaced,
        "effectiveness_score": round(effectiveness, 3),
    }

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Create tracking table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compact_effectiveness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                pre_know REAL,
                post_know REAL,
                know_recovery REAL,
                pre_context REAL,
                post_context REAL,
                context_recovery REAL,
                pre_uncertainty REAL,
                post_uncertainty REAL,
                uncertainty_delta REAL,
                items_surfaced INTEGER,
                effectiveness_score REAL
            )
        """)

        cursor.execute(
            """
            INSERT INTO compact_effectiveness
            (session_id, timestamp, pre_know, post_know, know_recovery,
             pre_context, post_context, context_recovery,
             pre_uncertainty, post_uncertainty, uncertainty_delta,
             items_surfaced, effectiveness_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                metrics["timestamp"],
                metrics["pre_know"],
                metrics["post_know"],
                metrics["know_recovery"],
                metrics["pre_context"],
                metrics["post_context"],
                metrics["context_recovery"],
                metrics["pre_uncertainty"],
                metrics["post_uncertainty"],
                metrics["uncertainty_delta"],
                metrics["items_surfaced"],
                metrics["effectiveness_score"],
            ),
        )

        conn.commit()
        conn.close()
        metrics["logged"] = True
    except Exception as e:
        metrics["logged"] = False
        metrics["error"] = str(e)

    return metrics


def get_effectiveness_history(db_path: Path | None = None, limit: int = 10) -> list[dict]:
    """
    Query compact effectiveness history for analysis.

    Args:
        db_path: Path to database
        limit: Maximum records to return

    Returns:
        List of effectiveness records, newest first
    """
    if db_path is None:
        db_path = Path.cwd() / ".empirica" / "sessions" / "sessions.db"

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM compact_effectiveness
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (limit,),
        )

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]
    except Exception:
        return []
