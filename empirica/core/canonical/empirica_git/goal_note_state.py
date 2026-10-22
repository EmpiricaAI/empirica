"""A goal's lifecycle state in its git note.

A goal note was written once, at creation, and never again: lineage only ever recorded "resumed". The canonical log could not say
whether a goal had been completed, abandoned, archived or reopened, so a from-notes rebuild brought every goal back as
``in_progress``. Each transition now restamps the note with the goal's state from SQLite (the source of truth) plus a lineage entry,
and ``plan_goal_notes`` / ``apply_goal_notes`` repair the backlog of notes nobody restamped.

Best-effort by design: a verb must not fail because its audit trail could not be written, so ``restamp_goal_note`` returns False
and logs instead of raising.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from empirica.core.canonical.empirica_git.goal_store import GitGoalStore

logger = logging.getLogger(__name__)

#: The goals-table columns a note carries, with the value a note that never recorded them is read as.
STATE_FIELDS: dict[str, Any] = {
    "status": None,
    "is_completed": 0,
    "completed_timestamp": None,
    "completion_reason": None,
    "archived": 0,
    "archived_at": None,
    "project_id": None,
}

_SELECT = (
    "SELECT id, session_id, objective, scope, estimated_complexity, created_timestamp, goal_data, "
    "status, is_completed, completed_timestamp, {reason}, COALESCE(archived, 0), archived_at, project_id "
    "FROM goals WHERE {where}"
)


def _project_root(conn) -> str | None:
    """The project a connection's database belongs to: ``<root>/.empirica/sessions/sessions.db`` -> ``<root>``."""
    try:
        for _, name, path in conn.execute("PRAGMA database_list").fetchall():
            if name == "main" and path:
                parts = Path(path).parts
                if ".empirica" in parts:
                    return str(Path(*parts[: parts.index(".empirica")]))
    except Exception:
        pass
    return None


def _has_reason_column(conn) -> bool:
    try:
        return any(r[1] == "completion_reason" for r in conn.execute("PRAGMA table_info(goals)"))
    except Exception:
        return False


def _rows(conn, where: str, params: tuple = ()) -> list[dict[str, Any]]:
    reason = "completion_reason" if _has_reason_column(conn) else "NULL"
    out = []
    for r in conn.execute(_SELECT.format(reason=reason, where=where), params).fetchall():
        out.append(
            {
                "id": r[0],
                "session_id": r[1],
                "objective": r[2],
                "scope": r[3],
                "estimated_complexity": r[4],
                "created_timestamp": r[5],
                "goal_data": r[6],
                "state": dict(zip(STATE_FIELDS, r[7:], strict=True)),
            }
        )
    return out


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _same_state(note: dict[str, Any] | None, state: dict[str, Any]) -> bool:
    data = (note or {}).get("goal_data") or {}
    return all(
        data.get(k, STATE_FIELDS[k]) == (state[k] if state[k] is not None else STATE_FIELDS[k]) for k in STATE_FIELDS
    )


def _stamp(store: GitGoalStore, row: dict[str, Any], action: str) -> bool:
    existing = store.load_goal(row["id"])
    state = dict(row["state"])
    if existing:
        goal_data = {**(existing.get("goal_data") or {}), **state}
        lineage = list(existing.get("lineage") or [])
        ai_id = existing.get("ai_id") or "unknown"
        created_at = existing.get("created_at")
        epistemic = existing.get("epistemic_state")
    else:
        goal_data = {
            "objective": row["objective"],
            "scope": _json(row["scope"], {}),
            "estimated_complexity": row["estimated_complexity"],
            **state,
        }
        lineage = []
        ai_id = "unknown"
        created_at = (
            datetime.fromtimestamp(row["created_timestamp"], tz=timezone.utc).isoformat()
            if isinstance(row["created_timestamp"], (int, float))
            else None
        )
        epistemic = None
    goal_data["state_updated_at"] = time.time()
    lineage.append({"ai_id": ai_id, "timestamp": datetime.now(timezone.utc).isoformat(), "action": action})
    return bool(
        store.store_goal(
            row["id"],
            row["session_id"] or "",
            ai_id,
            goal_data,
            epistemic_state=epistemic,
            lineage=lineage,
            created_at=created_at,
        )
    )


def restamp_goal_note(conn, goal_id: str, action: str, project_root: str | None = None) -> bool:
    """Write the goal's SQLite state into its note (creating the note if it has none). Never raises."""
    try:
        rows = _rows(conn, "id = ?", (goal_id,))
        if not rows:
            return False
        root = project_root or _project_root(conn)
        return _stamp(GitGoalStore(root) if root else GitGoalStore(), rows[0], action)
    except Exception as e:
        logger.warning(f"goal note restamp skipped for {goal_id} ({action}): {type(e).__name__}: {e}")
        return False


def plan_goal_notes(conn, project_root: str) -> dict[str, Any]:
    """Goals whose note disagrees with SQLite on any state field. Pure read."""
    store = GitGoalStore(project_root)
    drifted, missing = [], 0
    for row in _rows(conn, "1 = 1"):
        note = store.load_goal(row["id"])
        if note is None:
            missing += 1
        if not _same_state(note, row["state"]):
            drifted.append({"id": row["id"], "objective": (row["objective"] or "")[:80], "has_note": note is not None})
    return {"drifted": drifted, "without_note": missing}


def apply_goal_notes(conn, project_root: str) -> dict[str, Any]:
    """Restamp every drifted goal's note. Idempotent: a second pass finds nothing."""
    plan = plan_goal_notes(conn, project_root)
    done = failed = 0
    for g in plan["drifted"]:
        if restamp_goal_note(conn, g["id"], "reconciled", project_root):
            done += 1
        else:
            failed += 1
    return {"restamped": done, "failed": failed, "planned": len(plan["drifted"])}
