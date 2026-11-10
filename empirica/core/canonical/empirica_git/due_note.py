"""A moved or cleared due date reaches the git note (1.16, D3c; goal 8a26a2c3).

The create paths write ``due_at`` into the note. A snooze (``update-artifacts`` or ``PATCH /artifacts/{id}`` setting
``due_at``) changed only SQLite, so a rebuild brought back the date the item was created with. ``restamp_due`` reads the
date from the row, which is the source of truth, and rewrites the note: a goal through the lifecycle restamp (``due_at`` is
one of its state fields), an unknown or an assumption by re-storing the note it loads with every other key kept.

Best-effort, like every note write: a verb must not fail because its audit trail could not be written. It returns False
and logs instead of raising.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TABLES = {"goal": "goals", "unknown": "project_unknowns", "assumption": "assumptions"}


def restamp_due(conn, artifact_type: str, artifact_id: str) -> bool:
    """Rewrite the note of ``artifact_id`` with the due date its row carries now (None removes it)."""
    table = _TABLES.get(artifact_type)
    if table is None:
        return False
    try:
        from empirica.core.canonical.empirica_git.goal_note_state import _project_root, restamp_goal_note

        if artifact_type == "goal":
            return bool(restamp_goal_note(conn, artifact_id, "due_moved"))
        row = conn.execute(f"SELECT due_at FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            return False
        due_at = row[0]
        root = _project_root(conn)
        if artifact_type == "unknown":
            return _restore_unknown(root, artifact_id, due_at)
        return _restore_assumption(root, artifact_id, due_at)
    except Exception as e:
        logger.warning("due date not written to the %s note %s: %s", artifact_type, str(artifact_id)[:8], e)
        return False


def _restore_unknown(root: str | None, unknown_id: str, due_at: str | None) -> bool:
    from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore

    store = GitUnknownStore(workspace_root=root)
    note = store.load_unknown(unknown_id)
    if not note:
        return False
    keep = {k: v for k, v in note.items() if k != "due_at"}
    return bool(
        store.store_unknown(
            unknown_id=unknown_id,
            project_id=note.get("project_id") or "",
            session_id=note.get("session_id") or "",
            ai_id=note.get("ai_id") or "unknown",
            unknown=note.get("unknown") or "",
            goal_id=note.get("goal_id"),
            subtask_id=note.get("subtask_id"),
            resolved=bool(note.get("resolved")),
            resolved_by=note.get("resolved_by"),
            created_at=note.get("created_at"),
            resolved_at=note.get("resolved_at"),
            due_at=due_at,
            extra=keep,
        )
    )


def _restore_assumption(root: str | None, assumption_id: str, due_at: str | None) -> bool:
    from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore

    store = GitAssumptionStore(workspace_root=root)
    note = store.load_assumption(assumption_id)
    if not note:
        return False
    keep = {k: v for k, v in note.items() if k != "due_at"}
    return bool(
        store.store_assumption(
            assumption_id=assumption_id,
            project_id=note.get("project_id") or "",
            session_id=note.get("session_id") or "",
            ai_id=note.get("ai_id") or "unknown",
            assumption=note.get("assumption") or "",
            confidence=note.get("confidence", 0.5),
            domain=note.get("domain"),
            goal_id=note.get("goal_id"),
            due_at=due_at,
            status=note.get("status") or "unverified",
            created_at=note.get("created_at"),
            extra=keep,
        )
    )
