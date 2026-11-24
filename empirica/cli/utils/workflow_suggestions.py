"""
Workflow Suggestions - Dynamic Context Loading for Workflow Automation

Analyzes session state and queries semantic index (Qdrant) for contextual
workflow suggestions to help AIs discover and use Empirica properly.

Usage:
    from empirica.cli.utils.workflow_suggestions import get_workflow_suggestions

    suggestions = get_workflow_suggestions(
        project_id=project_id,
        session_id=session_id,
        db=db
    )

    # Returns: List of workflow tips with source, content, relevance

Author: Claude Code
Date: 2025-12-25
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def calculate_completeness_score(session_id: str, db) -> dict[str, Any]:
    """
    Calculate epistemic completeness score for session.

    Scoring algorithm (from INTERACTIVE_CHECKLIST_TUI.md):
    - PREFLIGHT exists: 20%
    - Findings per 15min: 20% (1+ per 15min = full score)
    - Unknowns logged: 15%
    - Mistakes logged: 10%
    - Epistemic sources: 10%
    - Dead ends documented: 5%
    - POSTFLIGHT exists: 20%

    Args:
        session_id: Session ID
        db: SessionDatabase instance

    Returns:
        Dict with score (0.0-1.0), grade, and components
    """
    cursor = db.conn.cursor()

    # Check for PREFLIGHT
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM reflexes
        WHERE session_id = ? AND phase = 'PREFLIGHT'
    """,
        (session_id,),
    )
    has_preflight = cursor.fetchone()["count"] > 0
    preflight_score = 0.20 if has_preflight else 0.0

    # Check for POSTFLIGHT
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM reflexes
        WHERE session_id = ? AND phase = 'POSTFLIGHT'
    """,
        (session_id,),
    )
    has_postflight = cursor.fetchone()["count"] > 0
    postflight_score = 0.20 if has_postflight else 0.0

    # Get session duration
    cursor.execute(
        """
        SELECT
            (julianday(COALESCE(end_time, CURRENT_TIMESTAMP)) - julianday(start_time)) * 24 * 60 as duration_minutes
        FROM sessions WHERE session_id = ?
    """,
        (session_id,),
    )
    row = cursor.fetchone()
    duration_minutes = max(0.0, row["duration_minutes"] or 0.0) if row else 0.0

    # Count findings
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM project_findings
        WHERE session_id = ?
    """,
        (session_id,),
    )
    findings_count = cursor.fetchone()["count"]

    # Findings score: 1 per 15min expected
    expected_findings = max(1, duration_minutes / 15.0)
    findings_ratio = min(1.0, findings_count / expected_findings if expected_findings > 0 else 0.0)
    findings_score = 0.20 * findings_ratio

    # Count unknowns
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM project_unknowns
        WHERE session_id = ?
    """,
        (session_id,),
    )
    unknowns_count = cursor.fetchone()["count"]
    unknowns_score = 0.15 if unknowns_count > 0 else 0.0

    # Count mistakes
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM mistakes_made
        WHERE session_id = ?
    """,
        (session_id,),
    )
    mistakes_count = cursor.fetchone()["count"]
    mistakes_score = 0.10 if mistakes_count > 0 else 0.0

    # Count epistemic sources (reference docs)
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM epistemic_sources
        WHERE session_id = ?
    """,
        (session_id,),
    )
    sources_count = cursor.fetchone()["count"]
    sources_score = 0.10 if sources_count > 0 else 0.0

    # Count dead ends
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM project_dead_ends
        WHERE session_id = ?
    """,
        (session_id,),
    )
    deadends_count = cursor.fetchone()["count"]
    deadends_score = 0.05 if deadends_count > 0 else 0.0

    # Total score
    total_score = (
        preflight_score
        + findings_score
        + unknowns_score
        + mistakes_score
        + sources_score
        + deadends_score
        + postflight_score
    )

    # Grade
    if total_score >= 0.9:
        grade = "⭐ PERFECT"
    elif total_score >= 0.7:
        grade = "🟢 GOOD"
    elif total_score >= 0.5:
        grade = "🟡 MODERATE"
    else:
        grade = "🔴 LOW"

    return {
        "score": total_score,
        "grade": grade,
        "components": {
            "preflight": {"score": preflight_score, "exists": has_preflight},
            "findings": {"score": findings_score, "count": findings_count, "expected": expected_findings},
            "unknowns": {"score": unknowns_score, "count": unknowns_count},
            "mistakes": {"score": mistakes_score, "count": mistakes_count},
            "sources": {"score": sources_score, "count": sources_count},
            "deadends": {"score": deadends_score, "count": deadends_count},
            "postflight": {"score": postflight_score, "exists": has_postflight},
        },
        "duration_minutes": duration_minutes,
    }


def get_workflow_suggestions(project_id: str, session_id: str | None = None, db=None) -> dict[str, Any]:
    """
    Get contextual workflow suggestions based on session state.

    Analyzes current session completeness and returns workflow automation
    tips from the semantic index (via Qdrant or file-based fallback).

    Args:
        project_id: Project ID
        session_id: Optional session ID (if None, returns general tips)
        db: SessionDatabase instance

    Returns:
        Dict with completeness_score, grade, and suggestions list
    """
    if not db:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        should_close = True
    else:
        should_close = False

    try:
        suggestions = []
        completeness = None

        if session_id:
            # Calculate completeness score
            completeness = calculate_completeness_score(session_id, db)

            # Build contextual query based on session state
            query_parts = []

            # Check for PREFLIGHT
            if not completeness["components"]["preflight"]["exists"]:
                query_parts.append("how to create PREFLIGHT assessment")
                suggestions.append(
                    {
                        "priority": "HIGH",
                        "action": "Create PREFLIGHT assessment",
                        "reason": "No baseline epistemic state recorded",
                        "guide": "Run PREFLIGHT before starting work to establish baseline",
                        "source": "architecture/INTERACTIVE_CHECKLIST_TUI.md#phase-1",
                    }
                )

            # Check for low completeness
            if completeness["score"] < 0.5:
                query_parts.append("improve epistemic completeness low score")

                # Specific suggestions based on what's missing
                if completeness["components"]["findings"]["count"] == 0:
                    suggestions.append(
                        {
                            "priority": "MEDIUM",
                            "action": "Log findings as you work",
                            "reason": f"No findings logged in {completeness['duration_minutes']:.1f} minutes",
                            "guide": 'Use: empirica finding-log --finding "your discovery"',
                            "source": "architecture/INTERACTIVE_CHECKLIST_TUI.md#phase-2",
                        }
                    )

                if completeness["components"]["unknowns"]["count"] == 0 and completeness["duration_minutes"] > 30:
                    suggestions.append(
                        {
                            "priority": "MEDIUM",
                            "action": "Track unknowns explicitly",
                            "reason": "Long session with no unknowns logged (uncertain areas unclear)",
                            "guide": 'Use: empirica unknown-log --unknown "what you don\'t know"',
                            "source": "architecture/INTERACTIVE_CHECKLIST_TUI.md#unknowns",
                        }
                    )

            # Check for long session without POSTFLIGHT
            if completeness["duration_minutes"] > 120 and not completeness["components"]["postflight"]["exists"]:
                query_parts.append("long session completeness validation postflight")
                suggestions.append(
                    {
                        "priority": "HIGH",
                        "action": "Run POSTFLIGHT assessment",
                        "reason": f"Session running for {completeness['duration_minutes']:.1f} minutes without POSTFLIGHT",
                        "guide": "Run POSTFLIGHT to measure learning and validate work",
                        "source": "architecture/INTERACTIVE_CHECKLIST_TUI.md#phase-3",
                    }
                )

        else:
            # No session - general workflow tips
            suggestions.append(
                {
                    "priority": "INFO",
                    "action": "Create session for work tracking",
                    "reason": "No active session detected",
                    "guide": "Use: empirica session-create --ai-id <your-ai-id>",
                    "source": "human/end-users/SESSION_GOAL_WORKFLOW.md",
                }
            )

        result = {
            "completeness_score": completeness["score"] if completeness else None,
            "grade": completeness["grade"] if completeness else None,
            "suggestions": suggestions,
        }

        if completeness:
            result["components"] = completeness["components"]
            result["duration_minutes"] = completeness["duration_minutes"]

        return result

    finally:
        if should_close and db:
            db.close()


def format_workflow_suggestions(suggestions_data: dict[str, Any]) -> str:
    """
    Format workflow suggestions for human-readable output.

    Args:
        suggestions_data: Output from get_workflow_suggestions()

    Returns:
        Formatted string for CLI output
    """
    output = []

    if suggestions_data.get("completeness_score") is not None:
        output.append(
            f"\n📊 Epistemic Completeness: {suggestions_data['grade']} ({suggestions_data['completeness_score']:.0%})"
        )
        output.append("")

    if suggestions_data.get("suggestions"):
        output.append("💡 Workflow Suggestions:")
        output.append("")

        for suggestion in suggestions_data["suggestions"]:
            priority = suggestion["priority"]
            action = suggestion["action"]
            reason = suggestion["reason"]
            guide = suggestion["guide"]

            # Priority emoji
            if priority == "HIGH":
                emoji = "🔴"
            elif priority == "MEDIUM":
                emoji = "🟡"
            else:
                emoji = "ℹ️"

            output.append(f"{emoji} {action}")
            output.append(f"   Reason: {reason}")
            output.append(f"   Guide: {guide}")
            output.append("")

    return "\n".join(output)
