"""Active topic detection for circle 3 similarity-triggered surfacing.

Three-step deterministic resolver per PROPOSAL_BOOTSTRAP_AGGREGATOR.md:

  1. Active transaction → use task_context + active goal.objective
  2. Recent (last 7d) high-impact findings → top 3 by impact, joined as text
  3. Otherwise → no topic; circle 3 is skipped entirely

Returned dict shape (matches wire's `active_topic` block):
  {
    "detected": bool,
    "source": "transaction" | "recent_findings" | "none",
    "text": "<seed text — internal use>",
    "text_preview": "<first 200 chars — wire>",
    "similarity_threshold": 0.65,
  }
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_SIMILARITY_THRESHOLD = 0.65
RECENT_FINDINGS_HOURS = 7 * 24


def detect_active_topic(
    project_path: Path | str,
    project_id: str | None,
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    transaction_state: dict | None = None,
) -> dict[str, Any]:
    """Detect the active topic for circle 3 similarity-pull.

    Returns a topic dict with `detected=False` when no signal is available
    — caller should skip circle 3 entirely in that case.
    """
    db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return _no_topic(similarity_threshold)

    # Step 1: active transaction's task context + active goal objective
    if transaction_state and transaction_state.get("active"):
        topic_text = _from_transaction(db_path, transaction_state)
        if topic_text:
            return {
                "detected": True,
                "source": "transaction",
                "text": topic_text,
                "text_preview": topic_text[:200],
                "similarity_threshold": similarity_threshold,
            }

    # Step 2: recent high-impact findings as multi-seed
    if project_id:
        topic_text = _from_recent_findings(db_path, project_id)
        if topic_text:
            return {
                "detected": True,
                "source": "recent_findings",
                "text": topic_text,
                "text_preview": topic_text[:200],
                "similarity_threshold": similarity_threshold,
            }

    # Step 3: no signal
    return _no_topic(similarity_threshold)


def _no_topic(similarity_threshold: float) -> dict[str, Any]:
    return {
        "detected": False,
        "source": "none",
        "text": "",
        "text_preview": "",
        "similarity_threshold": similarity_threshold,
    }


def _from_transaction(db_path: Path, transaction_state: dict) -> str | None:
    """Compose topic text from active transaction's task_context + active goal."""
    parts: list[str] = []
    task_context = transaction_state.get("task_context")
    if task_context:
        parts.append(str(task_context))

    # Active goal — take the in-progress goal joined to this transaction
    transaction_id = transaction_state.get("transaction_id")
    if transaction_id:
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT objective FROM goals "
                # `is_completed = 0` alone is not "open": an ABANDONED goal is
                # deliberately left at is_completed=0 so no completion metric
                # counts it as delivered, which means this filter would inject
                # dead goals as current work. The other two injection paths use
                # status allowlists and exclude it by construction; this one did
                # not, so the exclusion is explicit here.
                "WHERE transaction_id = ? AND is_completed = 0 "
                "AND (status IS NULL OR status != 'abandoned') "
                "ORDER BY created_timestamp DESC LIMIT 1",
                (transaction_id,),
            )
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                parts.append(str(row[0]))
        except sqlite3.Error:
            pass

    text = " | ".join(p for p in parts if p)
    return text or None


def _from_recent_findings(db_path: Path, project_id: str) -> str | None:
    """Top-3 recent high-impact findings joined as multi-seed topic text."""
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cutoff = time.time() - RECENT_FINDINGS_HOURS * 3600
        cur.execute(
            "SELECT finding FROM project_findings "
            # Superseded findings must not win topic matches — see circles.py.
            "WHERE project_id = ? AND created_timestamp >= ? AND is_resolved = 0 "
            "ORDER BY impact DESC, created_timestamp DESC LIMIT 3",
            (project_id, cutoff),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error:
        return None

    parts = [r[0] for r in rows if r and r[0]]
    if not parts:
        return None
    return " | ".join(parts)
