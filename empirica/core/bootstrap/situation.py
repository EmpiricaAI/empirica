"""build_situation — compaction-recovery narrative for bootstrap.

Synthesizes "where am I right now?" — the field an AI needs MOST after
compaction when its working memory has just been compressed.

Consumed by:
  - CLI bootstrap: SessionDatabase._build_situation (thin delegate)
  - Daemon HTTP: build_bootstrap_payload attaches as top-level `situation` field

Fields produced:
  - project:            "<name> @ <branch>" shorthand
  - active_transaction: in-flight PREFLIGHT state (from transaction file)
  - active_goal:        most recent in_progress > planned goal + subtasks
  - last_praxic_action: most recent commit (sha + msg + at)
  - next_focus:         derived — pending subtask > goal-linked unknown > recent project unknown

All sub-fields are best-effort — missing data → field omitted from result.
Cost: ~5ms typical (transaction file + 2-3 SQL queries + 1 subprocess for git).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Connection helper ───────────────────────────────────────────────────


def _open_db(project_path: Path | str) -> sqlite3.Connection:
    db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ── Field synthesizers ──────────────────────────────────────────────────


def _active_transaction() -> dict | None:
    """Read in-flight PREFLIGHT state from the transaction file."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        tx = R.transaction_read()
        if not tx:
            return None
        return {
            "id": tx.get("transaction_id"),
            "status": tx.get("status"),
            "opened_at": tx.get("preflight_timestamp"),
            "work_type": tx.get("work_type"),
            "work_context": tx.get("work_context"),
            "domain": tx.get("domain"),
            "criticality": tx.get("criticality"),
            "session_id": tx.get("session_id"),
        }
    except Exception as e:
        logger.debug(f"situation: transaction read skipped: {e}")
        return None


def _active_goal(conn: sqlite3.Connection, project_id: str) -> dict | None:
    """Most recent open goal (in_progress > planned) + its subtasks.

    Recency-aware picker: status_rank (in_progress=0, planned=1) primary,
    created_timestamp DESC secondary. Belt-and-suspenders project scope —
    accepts goals matched by g.project_id OR by session→project join, since
    Scoped to project_id ALONE. This used to widen with
    `OR g.session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)`
    to catch older rows with project_id unpopulated. Measured 2026-08-02: that
    branch reached 0 real goals — every row it added was an "E2E test goal
    workflow" fixture whose session also lacks a project_id (22 of them), so it
    was importing test noise into injected context and nothing else.

    Removed per the rule that sessions are compaction boundaries and must not
    scope lifecycle: a session join here silently makes "my goals" mean "goals
    from sessions that touched this project", which is a different and wider
    claim than the caller asks for. If legacy project-less goals ever matter,
    backfill project_id — do not widen the read.
    """
    try:
        cur = conn.execute(
            """
            SELECT g.id, g.objective, g.description, g.scope,
                   g.created_timestamp, g.status
            FROM goals g
            WHERE g.project_id = ?
              AND g.is_completed = 0
              AND g.status IN ('in_progress', 'planned')
            ORDER BY
              CASE g.status WHEN 'in_progress' THEN 0
                            WHEN 'planned'     THEN 1
                            ELSE 2 END,
              g.created_timestamp DESC
            LIMIT 1
            """,
            (project_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("scope"), str) and d["scope"]:
            try:
                d["scope"] = json.loads(d["scope"])
            except (ValueError, TypeError):
                pass
        sub_cur = conn.execute(
            """
            SELECT id, description, epistemic_importance, status,
                   created_timestamp
            FROM subtasks
            WHERE goal_id = ?
            ORDER BY created_timestamp
            """,
            (d["id"],),
        )
        d["subtasks"] = [dict(s) for s in sub_cur.fetchall()]
        return d
    except Exception as e:
        logger.debug(f"situation: active goal skipped: {e}")
        return None


def _last_praxic_action(project_root: str | Path) -> dict | None:
    """Most recent commit (sha + msg + at)."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H%x00%s%x00%aI"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split("\0", 2)
        if len(parts) != 3:
            return None
        sha, msg, at = parts
        return {"commit": sha[:9], "message": msg, "at": at}
    except Exception as e:
        logger.debug(f"situation: last commit skipped: {e}")
        return None


def _project_shorthand(conn: sqlite3.Connection, project_id: str, project_root: str | Path) -> str | None:
    """`<project_name> @ <branch>` — both best-effort."""
    try:
        cur = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
        row = cur.fetchone()
        proj_name = row["name"] if row else "unknown"
        br_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
        branch = br_result.stdout.strip() if br_result.returncode == 0 else None
        return f"{proj_name} @ {branch}" if branch else proj_name
    except Exception as e:
        logger.debug(f"situation: project shorthand skipped: {e}")
        return None


def _next_focus(
    conn: sqlite3.Connection,
    project_id: str,
    active_goal: dict | None,
) -> str:
    """Priority cascade — recency-aware.

    1. Pending/in-progress subtask of active_goal (preserves the open thread)
    2. Most-recent unresolved unknown LINKED to active_goal (goal-scoped focus)
    3. Most-recent unresolved unknown across project (recency-aware fallback;
       the prior 'oldest unknown' picker surfaced stale items after compaction)
    4. Generic prompt
    """
    if active_goal:
        for sub in active_goal.get("subtasks", []):
            if sub.get("status") in (None, "pending", "in_progress"):
                return f"Subtask: {sub.get('description', '')[:120]}"

        goal_id = active_goal.get("id")
        if goal_id:
            try:
                cur = conn.execute(
                    """
                    SELECT unknown FROM project_unknowns
                    WHERE project_id = ? AND is_resolved = 0 AND goal_id = ?
                    ORDER BY created_timestamp DESC
                    LIMIT 1
                    """,
                    (project_id, goal_id),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return f"Goal-linked unknown: {row[0][:120]}"
            except Exception:  # noqa: S110 — best-effort focus fallback
                pass

    try:
        cur = conn.execute(
            """
            SELECT unknown FROM project_unknowns
            WHERE project_id = ? AND is_resolved = 0
            ORDER BY created_timestamp DESC
            LIMIT 1
            """,
            (project_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return f"Open unknown: {row[0][:120]}"
    except Exception:  # noqa: S110 — best-effort focus fallback
        pass

    return "No specific focus — review goals-list / unknown-list"


# ── Public composer ─────────────────────────────────────────────────────


def build_situation(
    project_path: str | Path,
    project_id: str | None,
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Compose the situation block.

    Args:
        project_path: Project root containing `.empirica/sessions/sessions.db`.
            Used for DB open and (by default) git operations.
        project_id: Canonical project UUID. None → returns transaction-only
            view (no DB lookups).
        project_root: Override for git operations if different from project_path
            (e.g. when daemon serves a project from a different cwd). Defaults
            to `project_path`.

    Returns:
        Dict with the keys present below when their data was groundable:
        `project`, `active_transaction`, `active_goal`, `last_praxic_action`,
        `next_focus`. All keys are optional; missing data → omitted key.
    """
    project_path = Path(project_path)
    git_root = Path(project_root) if project_root is not None else project_path
    situation: dict[str, Any] = {}

    active_transaction = _active_transaction()
    if active_transaction is not None:
        situation["active_transaction"] = active_transaction

    if project_id is None:
        # No DB-scoped resolution possible. Still surface transaction state +
        # a best-effort last commit so the AI sees *something* anchored.
        last_praxic = _last_praxic_action(git_root)
        if last_praxic is not None:
            situation["last_praxic_action"] = last_praxic
        return situation

    conn = _open_db(project_path)
    try:
        active_goal = _active_goal(conn, project_id)
        if active_goal is not None:
            situation["active_goal"] = active_goal

        last_praxic = _last_praxic_action(git_root)
        if last_praxic is not None:
            situation["last_praxic_action"] = last_praxic

        project_shorthand = _project_shorthand(conn, project_id, git_root)
        if project_shorthand is not None:
            situation["project"] = project_shorthand

        situation["next_focus"] = _next_focus(conn, project_id, active_goal)
    finally:
        conn.close()

    return situation
