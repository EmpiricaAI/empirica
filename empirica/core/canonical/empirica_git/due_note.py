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

#: Row fields whose change must reach the artifact's git note, or a rebuild restores the old value (goals 1e885685,
#: 25f4ab9a). ``due_at`` applies to every type in ``_TABLES``.
NOTE_STATE_FIELDS = {
    "assumption": frozenset({"status", "due_at"}),
    "decision": frozenset({"outcome", "outcome_assessed_at", "regret_score"}),
}


def note_needs_restamp(artifact_type: str, updated_fields) -> bool:
    """Whether an update to ``updated_fields`` of an ``artifact_type`` row must rewrite its note."""
    fields = set(updated_fields)
    if "due_at" in fields and artifact_type in _TABLES:
        return True
    return bool(fields & NOTE_STATE_FIELDS.get(artifact_type, frozenset()))


def restamp_note(conn, artifact_type: str, artifact_id: str) -> bool:
    """Rewrite the note of ``artifact_id`` from its row: the due date, an assumption's status, a decision's assessment."""
    if artifact_type == "decision":
        return restamp_decision(conn, artifact_id)
    if artifact_type == "source":
        return restamp_source(conn, artifact_id)
    return restamp_due(conn, artifact_type, artifact_id)


def restamp_due(conn, artifact_type: str, artifact_id: str) -> bool:
    """Rewrite the note of ``artifact_id`` with the due date its row carries now (None removes it)."""
    table = _TABLES.get(artifact_type)
    if table is None:
        return False
    try:
        from empirica.core.canonical.empirica_git.goal_note_state import _project_root, restamp_goal_note

        if artifact_type == "goal":
            return bool(restamp_goal_note(conn, artifact_id, "due_moved"))
        if artifact_type == "assumption":
            return restamp_assumption(conn, artifact_id)
        row = conn.execute(f"SELECT due_at FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            return False
        return _restore_unknown(_project_root(conn), artifact_id, row[0])
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


def restamp_assumption(conn, assumption_id: str) -> bool:
    """Rewrite an assumption's note from its row: status (verified / falsified), when and by which finding it was resolved,
    and its due date. Assumption notes recorded only their state at creation, so a rebuild could never have told a
    verified assumption from an open one (goal 1e885685). Best-effort: returns False and logs, never raises."""
    try:
        from empirica.core.canonical.empirica_git.goal_note_state import _project_root

        row = conn.execute(
            "SELECT status, resolved_timestamp, resolution_finding_id, due_at FROM assumptions WHERE id = ?",
            (assumption_id,),
        ).fetchone()
        if row is None:
            return False
        state = {"resolved_timestamp": row[1], "resolution_finding_id": row[2]}
        return _restore_assumption(_project_root(conn), assumption_id, row[3], status=row[0], state=state)
    except Exception as e:
        logger.warning("assumption note %s not rewritten: %s", str(assumption_id)[:8], e)
        return False


def _restore_assumption(
    root: str | None, assumption_id: str, due_at: str | None, *, status: str | None = None, state: dict | None = None
) -> bool:
    from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore

    store = GitAssumptionStore(workspace_root=root)
    note = store.load_assumption(assumption_id)
    if not note:
        return False
    keep = {k: v for k, v in note.items() if k != "due_at"}
    keep.update({k: v for k, v in (state or {}).items() if v is not None})
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
            status=status or note.get("status") or "unverified",
            created_at=note.get("created_at"),
            extra=keep,
        )
    )


def restamp_decision(conn, decision_id: str) -> bool:
    """Rewrite a decision's note with its assessment: outcome (upheld / reversed / mixed), when it was assessed, and the
    self-assessed regret. Decision notes recorded only the state at creation, so a rebuild could not tell a reversed
    decision from an unassessed one (goal 25f4ab9a). Best-effort: returns False and logs, never raises."""
    try:
        from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore
        from empirica.core.canonical.empirica_git.goal_note_state import _project_root

        row = conn.execute(
            "SELECT outcome, outcome_assessed_at, regret_score FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            return False
        store = GitDecisionStore(workspace_root=_project_root(conn))
        note = store.load_decision(decision_id)
        if not note:
            return False
        keep = dict(note)
        keep.update({"outcome": row[0], "outcome_assessed_at": row[1], "regret_score": row[2]})
        return bool(
            store.store_decision(
                decision_id=decision_id,
                project_id=note.get("project_id") or "",
                session_id=note.get("session_id") or "",
                ai_id=note.get("ai_id") or "unknown",
                choice=note.get("choice") or "",
                rationale=note.get("rationale") or "",
                alternatives=note.get("alternatives"),
                confidence=note.get("confidence_at_decision", 0.7),
                reversibility=note.get("reversibility") or "exploratory",
                goal_id=note.get("goal_id"),
                created_at=note.get("created_at"),
                extra=keep,
            )
        )
    except Exception as e:
        logger.warning("decision note %s not rewritten: %s", str(decision_id)[:8], e)
        return False


def restamp_source(conn, source_id: str) -> bool:
    """Rewrite a source's note with its lifecycle: archived or not, why, what replaced it, when, and its current URL.
    Source notes recorded only the state at creation, so a rebuild could not tell an archived source from a live one
    (goal 25f4ab9a). Best-effort: returns False and logs, never raises."""
    try:
        from empirica.core.canonical.empirica_git.goal_note_state import _project_root
        from empirica.core.canonical.empirica_git.source_store import GitSourceStore

        row = conn.execute(
            "SELECT archived, archive_reason, archive_target_id, archived_at, source_url FROM epistemic_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            return False
        store = GitSourceStore(workspace_root=_project_root(conn))
        note = store.load_source(source_id)
        if not note:
            return False
        keep = dict(note)
        keep.update(
            {"archived": bool(row[0]), "archive_reason": row[1], "archive_target_id": row[2], "archived_at": row[3]}
        )
        return bool(
            store.store_source(
                source_id=source_id,
                project_id=note.get("project_id") or "",
                session_id=note.get("session_id") or "",
                title=note.get("title") or "",
                source_type=note.get("source_type") or "document",
                source_url=row[4] if row[4] is not None else note.get("source_url"),
                doc_path=note.get("doc_path"),
                description=note.get("description"),
                confidence=note.get("confidence", 0.7),
                direction=note.get("direction") or "noetic",
                ai_id=note.get("ai_id") or "",
                created_at=note.get("created_at"),
                extra=keep,
            )
        )
    except Exception as e:
        logger.warning("source note %s not rewritten: %s", str(source_id)[:8], e)
        return False
