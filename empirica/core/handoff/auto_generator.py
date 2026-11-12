"""
Auto-generate handoff reports from cascades data - TOKEN EFFICIENT

Generates semantic handoff summaries (~500 tokens) from session cascades,
avoiding token bloat from storing full cascade data.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _compute_epistemic_trajectory(cascades) -> dict:
    """Compute knowledge/uncertainty trajectories and key learning moments."""
    knowledge_trajectory = []
    uncertainty_trajectory = []
    key_learning_moments = []
    prev_know = 0.5

    for i, cascade in enumerate(cascades, 1):
        epistemic_delta = cascade[3]
        if not epistemic_delta:
            continue
        try:
            delta_data = json.loads(epistemic_delta)
            know = delta_data.get("know", 0.5)
            uncertainty = delta_data.get("uncertainty", 0.5)

            knowledge_trajectory.append(round(know, 2))
            uncertainty_trajectory.append(round(uncertainty, 2))

            if know - prev_know > 0.10:
                task = cascade[1][:50]
                key_learning_moments.append(f"CASCADE {i}: {task}... (+{know - prev_know:.2f} knowledge)")
            prev_know = know
        except Exception:
            logger.debug("Failed to parse cascade vectors for knowledge trajectory")

    return {
        "knowledge": knowledge_trajectory,
        "uncertainty": uncertainty_trajectory,
        "moments": key_learning_moments,
    }


def _extract_cascade_artifacts(cascades) -> dict:
    """Extract findings, investigation notes, and actions from cascades."""
    key_findings = []
    investigation_notes = []
    actions_taken = []

    for cascade in cascades:
        context_json = cascade[2]
        if not context_json:
            continue
        try:
            context = json.loads(context_json)
            if "check_findings" in context:
                findings = context["check_findings"]
                if isinstance(findings, list):
                    key_findings.extend(findings)
            if "investigation_log" in context:
                for log_entry in context["investigation_log"]:
                    if "findings" in log_entry:
                        investigation_notes.extend(log_entry["findings"])
            if "act_log" in context:
                for act_entry in context["act_log"]:
                    if "actions" in act_entry:
                        actions_taken.extend(act_entry["actions"])
        except Exception:
            logger.debug("Failed to parse cascade context for findings extraction")

    all_findings = key_findings + investigation_notes
    key_findings = list(dict.fromkeys(all_findings))[:5]

    return {
        "findings": key_findings,
        "all_findings": all_findings,
        "actions": actions_taken,
    }


def _extract_remaining_unknowns(cascades) -> list:
    """Extract remaining unknowns from the last cascade's CHECK context."""
    if not cascades:
        return []
    last_context_json = cascades[-1][2]
    if not last_context_json:
        return []
    try:
        last_context = json.loads(last_context_json)
        unknowns = last_context.get("check_unknowns", [])
        if isinstance(unknowns, list):
            return unknowns[:3]
    except Exception:
        logger.debug("Failed to parse last cascade context for unknowns")
    return []


def auto_generate_handoff(session_id: str, db_path: str = "./.empirica/sessions/sessions.db") -> dict:
    """
    Auto-generate handoff report from cascades data in database.

    Token efficiency: ~500 tokens vs 2000+ if storing full cascade data

    Args:
        session_id: Session UUID
        db_path: Path to session database

    Returns:
        dict: Handoff report data ready for storage

    Raises:
        ValueError: If session not found or no cascades exist
    """
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=db_path)

    try:
        # 1. Get session metadata
        cursor = db.conn.cursor()
        cursor.execute(
            """
            SELECT ai_id, start_time, end_time, bootstrap_level, total_cascades
            FROM sessions
            WHERE session_id = ?
        """,
            (session_id,),
        )

        session = cursor.fetchone()
        if not session:
            raise ValueError(f"Session {session_id} not found")

        ai_id, start_time, end_time, _, _ = session

        # 2. Get all cascades for this session
        cursor.execute(
            """
            SELECT cascade_id, task, context_json, epistemic_delta,
                   started_at, completed_at, final_confidence
            FROM cascades
            WHERE session_id = ?
            ORDER BY started_at ASC
        """,
            (session_id,),
        )

        cascades = cursor.fetchall()

        if not cascades:
            raise ValueError(f"No cascades found for session {session_id}")

        # 3. Extract task summary from first cascade
        first_cascade = cascades[0]
        task_summary = first_cascade[1]  # task column

        # 4. Calculate epistemic trajectory (COMPACT!)
        trajectory = _compute_epistemic_trajectory(cascades)
        knowledge_trajectory = trajectory["knowledge"]
        uncertainty_trajectory = trajectory["uncertainty"]
        key_learning_moments = trajectory["moments"]

        # 5. Extract key findings from CHECK phases and investigation logs
        extraction = _extract_cascade_artifacts(cascades)
        key_findings = extraction["findings"]
        all_findings = extraction["all_findings"]
        actions_taken = extraction["actions"]

        # 6. Get remaining unknowns from last CHECK
        remaining_unknowns = _extract_remaining_unknowns(cascades)

        # 7. Get artifacts from git diff (simplified - just note that files were modified)
        artifacts = _get_artifacts_from_session(session_id, start_time)

        # 8. Auto-generate next session context
        if knowledge_trajectory:
            first_know = knowledge_trajectory[0]
            last_know = knowledge_trajectory[-1]
            confidence_delta = last_know - first_know

            if confidence_delta > 0.2:
                next_context = (
                    f"High confidence gained (+{confidence_delta:.2f}). Work validated and ready for next phase."
                )
            elif confidence_delta > 0.1:
                next_context = f"Moderate progress (+{confidence_delta:.2f}). Review findings before proceeding."
            else:
                next_context = f"Minimal change ({confidence_delta:+.2f}). Some unknowns remain - investigate further."
        else:
            next_context = "No epistemic data available. Manual review recommended."

        # 9. Calculate duration
        duration_seconds = 0
        if start_time and end_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(end_time)
                duration_seconds = int((end_dt - start_dt).total_seconds())
            except Exception:
                logger.debug("Failed to parse session timestamps for duration calculation")

        # 10. Build compact handoff report
        return {
            "session_id": session_id,
            "ai_id": ai_id,
            "task_summary": task_summary,
            "key_findings": key_findings if key_findings else ["No findings captured"],
            "remaining_unknowns": remaining_unknowns if remaining_unknowns else [],
            "next_session_context": next_context,
            "artifacts_created": artifacts,
            "actions_completed": actions_taken[:10] if actions_taken else [],  # Top 10 actions
            "epistemic_deltas": {
                "knowledge_trajectory": " → ".join(map(str, knowledge_trajectory)) if knowledge_trajectory else "N/A",
                "uncertainty_trajectory": " → ".join(map(str, uncertainty_trajectory))
                if uncertainty_trajectory
                else "N/A",
                "overall_delta": f"+{knowledge_trajectory[-1] - knowledge_trajectory[0]:.2f}"
                if len(knowledge_trajectory) >= 2
                else "N/A",
                "key_learning_moments": key_learning_moments[:3],  # Top 3 only
            },
            "productivity_metrics": {
                "cascades_run": len(cascades),
                "findings_discovered": len(all_findings),
                "actions_taken": len(actions_taken),
            },
            "duration_seconds": duration_seconds,
            "cascades_completed": len(cascades),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    finally:
        db.close()


def _get_artifacts_from_session(session_id: str, start_time: str) -> list[str]:
    """
    Get list of files modified during session using git diff.

    Args:
        session_id: Session UUID
        start_time: Session start timestamp

    Returns:
        List of modified file paths
    """
    import subprocess

    try:
        # Try to get git diff since session start
        if start_time:
            # Get files changed since start_time
            result = subprocess.run(
                ["git", "diff", "--name-only", f"@{{since={start_time}}}"], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0 and result.stdout.strip():
                files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
                return files[:10]  # Limit to 10 files
    except Exception as e:
        logger.debug(f"Could not get git diff: {e}")

    # Fallback: return empty list
    return []


def close_session(session_id: str, db_path: str = "./.empirica/sessions/sessions.db") -> None:
    """
    Close session by setting end_time in database.

    Args:
        session_id: Session UUID
        db_path: Path to session database
    """
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=db_path)

    try:
        cursor = db.conn.cursor()
        cursor.execute(
            """
            UPDATE sessions
            SET end_time = ?
            WHERE session_id = ?
        """,
            (datetime.now(timezone.utc).isoformat(), session_id),
        )

        db.conn.commit()
        from empirica.data.global_sessions import close_global_sessions

        close_global_sessions([session_id])
        logger.info(f"Session {session_id} closed successfully")

    finally:
        db.close()
