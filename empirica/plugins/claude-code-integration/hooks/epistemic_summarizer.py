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
    'finding': 0.9,      # Validated learnings - high confidence
    'dead_end': 0.85,    # Important to avoid - cost was paid
    'mistake': 0.85,     # Cost was paid, lesson is real
    'subtask': 0.80,     # Structured work items - actionable
    'goal': 0.75,        # Structural, but context-dependent
    'unknown': 0.6,      # Questions, inherently uncertain
}

# Importance to impact mapping for subtasks (they don't have explicit impact scores)
IMPORTANCE_TO_IMPACT = {
    'critical': 0.95,
    'high': 0.80,
    'medium': 0.60,
    'low': 0.40,
}

# Recency decay parameters
# Half-life of 24 hours means items lose half their recency weight per day
RECENCY_HALF_LIFE_HOURS = 24
DECAY_CONSTANT = math.log(2) / RECENCY_HALF_LIFE_HOURS  # ~0.029


def calculate_weight(item: dict, item_type: str) -> float:
    """
    Calculate epistemic weight for ranking.

    Formula: weight = impact * type_confidence * recency_decay

    Args:
        item: Dictionary with 'impact' and timestamp fields
        item_type: One of 'finding', 'dead_end', 'mistake', 'subtask', 'goal', 'unknown'

    Returns:
        Weight score between 0.0 and 1.0
    """
    # Impact from database, default 0.5 if not set
    # Subtasks use importance field instead of impact
    if item_type == 'subtask':
        importance = item.get('importance', 'medium')
        impact = IMPORTANCE_TO_IMPACT.get(importance, 0.6)
    else:
        impact = item.get('impact', 0.5)

    # Type-based confidence multiplier
    type_conf = TYPE_CONFIDENCE.get(item_type, 0.5)

    # Calculate recency decay
    # Try multiple timestamp field names for compatibility
    timestamp = (
        item.get('created_timestamp') or
        item.get('timestamp') or
        item.get('created_at') or
        time.time()
    )

    # Handle string timestamps
    if isinstance(timestamp, str):
        try:
            from datetime import datetime
            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()
        except (ValueError, AttributeError):
            timestamp = time.time()

    age_hours = (time.time() - timestamp) / 3600
    recency = math.exp(-DECAY_CONSTANT * age_hours)

    return round(impact * type_conf * recency, 2)


def rank_items(items: list[tuple[dict, str]]) -> list[tuple[float, dict, str]]:
    """
    Rank items by epistemic weight.

    Args:
        items: List of (item_dict, item_type) tuples

    Returns:
        List of (weight, item_dict, item_type) sorted descending by weight
    """
    weighted = []
    for item, item_type in items:
        weight = calculate_weight(item, item_type)
        weighted.append((weight, item, item_type))

    return sorted(weighted, key=lambda x: x[0], reverse=True)


def format_item(weight: float, item: dict, item_type: str) -> str:
    """Format a single item for display."""
    type_label = item_type.replace('_', '-').title()

    if item_type == 'finding':
        text = item.get('finding', 'Unknown finding')
    elif item_type == 'unknown':
        text = item.get('unknown', 'Unknown question')
    elif item_type == 'dead_end':
        approach = item.get('approach', '?')
        why_failed = item.get('why_failed', '?')
        text = f"{approach} → {why_failed}"
    elif item_type == 'goal':
        text = item.get('objective', 'Unknown goal')
        status = item.get('status', 'pending')
        text = f"{text} ({status})"
    elif item_type == 'subtask':
        # internal type tag stays 'subtask' (matches SubTask class + subtasks table);
        # user-visible fallback string says 'task' (matches CLI vocabulary)
        text = item.get('description', 'Unknown task')
        importance = item.get('importance', 'medium')
        goal_context = item.get('goal_objective', '')
        if goal_context:
            text = f"[{importance}] {text} (→ {goal_context})"
        else:
            text = f"[{importance}] {text}"
    elif item_type == 'mistake':
        text = item.get('mistake', 'Unknown mistake')
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
    for f in (findings or []):
        all_items.append((f, 'finding'))
    for u in (unknowns or []):
        all_items.append((u, 'unknown'))
    for d in (dead_ends or []):
        all_items.append((d, 'dead_end'))
    for g in (goals or []):
        all_items.append((g, 'goal'))
    for m in (mistakes or []):
        all_items.append((m, 'mistake'))
    for st in (subtasks or []):
        all_items.append((st, 'subtask'))
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
    session_id: str | None = None
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
        findings, unknowns, dead_ends, goals, mistakes, subtasks,
    )

    if not all_items:
        return "## EPISTEMIC FOCUS\n\n*No breadcrumbs logged yet.*\n"

    ranked = rank_items(all_items)[:max_items]

    # Group by weight tier
    critical = [(w, i, t) for w, i, t in ranked if w > 0.7]
    important = [(w, i, t) for w, i, t in ranked if 0.4 <= w <= 0.7]
    context_items = [(w, i, t) for w, i, t in ranked if w < 0.4]

    lines = ["## EPISTEMIC FOCUS (Confidence-Ranked)\n"]
    _format_tier(lines, "### Critical (weight > 0.7)", critical)
    _format_tier(lines, "### Important (weight 0.4-0.7)", important)
    _format_tier(lines, "### Context (weight < 0.4)", context_items)
    lines.append("---")

    # Retrieval guidance footer
    session_hint = f" --session-id {session_id}" if session_id else ""
    lines.append(f"📊 **{len(ranked)} items ranked** | For deeper context:")
    lines.append(f"- `empirica project-bootstrap{session_hint}` (full load + subtasks)")
    lines.append("- `empirica project-search --task \"<query>\"` (Qdrant semantic)")
    lines.append("- `git notes show --ref=breadcrumbs HEAD` (session narrative)\n")

    return "\n".join(lines)


def log_compact_effectiveness(
    session_id: str,
    pre_vectors: dict,
    post_check_vectors: dict,
    items_surfaced: int,
    db_path: Path | None = None
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
    pre_know = pre_vectors.get('know', 0.5)
    post_know = post_check_vectors.get('know', 0.5)
    know_recovery = post_know / max(pre_know, 0.1)

    pre_context = pre_vectors.get('context', 0.5)
    post_context = post_check_vectors.get('context', 0.5)
    context_recovery = post_context / max(pre_context, 0.1)

    pre_uncertainty = pre_vectors.get('uncertainty', 0.5)
    post_uncertainty = post_check_vectors.get('uncertainty', 0.5)
    uncertainty_delta = post_uncertainty - pre_uncertainty

    # Effectiveness score: high recovery + low uncertainty increase
    effectiveness = (know_recovery + context_recovery) / 2 - uncertainty_delta

    metrics = {
        'session_id': session_id,
        'timestamp': time.time(),
        'pre_know': pre_know,
        'post_know': post_know,
        'know_recovery': round(know_recovery, 3),
        'pre_context': pre_context,
        'post_context': post_context,
        'context_recovery': round(context_recovery, 3),
        'pre_uncertainty': pre_uncertainty,
        'post_uncertainty': post_uncertainty,
        'uncertainty_delta': round(uncertainty_delta, 3),
        'items_surfaced': items_surfaced,
        'effectiveness_score': round(effectiveness, 3)
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

        cursor.execute("""
            INSERT INTO compact_effectiveness
            (session_id, timestamp, pre_know, post_know, know_recovery,
             pre_context, post_context, context_recovery,
             pre_uncertainty, post_uncertainty, uncertainty_delta,
             items_surfaced, effectiveness_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            metrics['timestamp'],
            metrics['pre_know'],
            metrics['post_know'],
            metrics['know_recovery'],
            metrics['pre_context'],
            metrics['post_context'],
            metrics['context_recovery'],
            metrics['pre_uncertainty'],
            metrics['post_uncertainty'],
            metrics['uncertainty_delta'],
            metrics['items_surfaced'],
            metrics['effectiveness_score']
        ))

        conn.commit()
        conn.close()
        metrics['logged'] = True
    except Exception as e:
        metrics['logged'] = False
        metrics['error'] = str(e)

    return metrics


def get_effectiveness_history(
    db_path: Path | None = None,
    limit: int = 10
) -> list[dict]:
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

        cursor.execute("""
            SELECT * FROM compact_effectiveness
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]
    except Exception:
        return []
