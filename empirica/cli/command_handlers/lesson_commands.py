"""
Lesson Commands - CLI handlers for Empirica Lessons

Commands:
- lesson-create: Create a new lesson from JSON input
- lesson-load: Load and display a lesson
- lesson-list: List all lessons
- lesson-search: Search for lessons
- lesson-replay-start: Start tracking a lesson replay
- lesson-replay-end: End a lesson replay
- lesson-stats: Show lesson storage statistics
"""

import json
import logging
import sys
from argparse import Namespace
from typing import Any

logger = logging.getLogger(__name__)


def handle_lesson_create_command(args: Namespace) -> dict[str, Any]:
    """
    Create a new lesson from JSON input.

    Usage:
        empirica lesson-create --name "My Lesson" --input lesson.json
        cat lesson.json | empirica lesson-create -

    JSON format:
    {
        "name": "Lesson Name",
        "version": "1.0",
        "description": "What this lesson teaches",
        "epistemic": {
            "source_confidence": 0.9,
            "teaching_quality": 0.85,
            "reproducibility": 0.8,
            "expected_delta": {"know": 0.3, "do": 0.2, "uncertainty": -0.25}
        },
        "steps": [
            {"order": 1, "phase": "noetic", "action": "Read docs"},
            {"order": 2, "phase": "praxic", "action": "Execute", "critical": true}
        ],
        "domain": "example",
        "tags": ["tag1", "tag2"]
    }
    """
    from empirica.core.lessons import (
        EpistemicDelta,
        Lesson,
        LessonEpistemic,
        LessonPhase,
        LessonStep,
        get_lesson_storage,
    )

    getattr(args, "output", "json")

    try:
        # Get input data
        input_data = None

        # From stdin
        if getattr(args, "input", None) == "-":
            input_data = json.load(sys.stdin)
        # From file
        elif getattr(args, "input", None):
            with open(args.input) as f:
                input_data = json.load(f)
        # From inline JSON
        elif getattr(args, "json", None):
            input_data = json.loads(args.json)
        else:
            return {"ok": False, "error": "No input provided. Use --input FILE, --json JSON, or pipe to stdin"}

        # Build lesson object
        name = input_data.get("name", getattr(args, "name", "Unnamed Lesson"))
        version = input_data.get("version", "1.0")

        # Parse epistemic data
        epistemic_data = input_data.get("epistemic", {})
        delta_data = epistemic_data.get("expected_delta", {})
        expected_delta = EpistemicDelta(
            know=delta_data.get("know", 0),
            do=delta_data.get("do", 0),
            context=delta_data.get("context", 0),
            clarity=delta_data.get("clarity", 0),
            coherence=delta_data.get("coherence", 0),
            signal=delta_data.get("signal", 0),
            uncertainty=delta_data.get("uncertainty", 0),
        )

        epistemic = LessonEpistemic(
            source_confidence=epistemic_data.get("source_confidence", 0.8),
            teaching_quality=epistemic_data.get("teaching_quality", 0.8),
            reproducibility=epistemic_data.get("reproducibility", 0.7),
            expected_delta=expected_delta,
        )

        # Parse steps
        steps = []
        for step_data in input_data.get("steps", []):
            phase_str = step_data.get("phase", "praxic").lower()
            phase = LessonPhase.NOETIC if phase_str == "noetic" else LessonPhase.PRAXIC

            step = LessonStep(
                order=step_data.get("order", len(steps) + 1),
                phase=phase,
                action=step_data.get("action", ""),
                target=step_data.get("target"),
                code=step_data.get("code"),
                critical=step_data.get("critical", False),
                expected_outcome=step_data.get("expected_outcome"),
                error_recovery=step_data.get("error_recovery"),
                timeout_ms=step_data.get("timeout_ms"),
            )
            steps.append(step)

        # Create lesson
        lesson = Lesson(
            id=Lesson.generate_id(name, version),
            name=name,
            version=version,
            description=input_data.get("description", ""),
            epistemic=epistemic,
            steps=steps,
            domain=input_data.get("domain"),
            tags=input_data.get("tags", []),
            suggested_tier=input_data.get("suggested_tier", "free"),
            suggested_price=input_data.get("suggested_price", 0.0),
            created_by=input_data.get("created_by", "cli"),
        )

        # Store lesson
        storage = get_lesson_storage()
        result = storage.create_lesson(lesson)

        return {
            "ok": True,
            "lesson_id": lesson.id,
            "name": lesson.name,
            "version": lesson.version,
            "step_count": len(steps),
            "cold_path": result.get("cold_path"),
            "elapsed_ms": result.get("elapsed_ms"),
            "message": f'Lesson "{name}" created successfully',
        }

    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Invalid JSON: {e}"}
    except Exception as e:
        logger.exception("Failed to create lesson")
        return {"ok": False, "error": str(e)}


def handle_lesson_load_command(args: Namespace) -> dict[str, Any]:
    """
    Load and display a lesson.

    Usage:
        empirica lesson-load --id <lesson_id>
        empirica lesson-load --id <lesson_id> --steps-only
    """
    from empirica.core.lessons import get_lesson_storage

    lesson_id = getattr(args, "id", None) or getattr(args, "lesson_id", None)
    if not lesson_id:
        return {"ok": False, "error": "Lesson ID required (--id)"}

    storage = get_lesson_storage()
    lesson = storage.get_lesson(lesson_id)

    if not lesson:
        return {"ok": False, "error": f"Lesson not found: {lesson_id}"}

    steps_only = getattr(args, "steps_only", False)

    if steps_only:
        steps = getattr(lesson, "steps", [])
        return {"ok": True, "lesson_id": lesson.id, "name": lesson.name, "steps": [s.to_dict() for s in steps]}

    to_dict_fn = getattr(lesson, "to_dict", None)
    return {"ok": True, "lesson": to_dict_fn() if to_dict_fn else {"id": lesson.id, "name": lesson.name}}


def handle_lesson_list_command(args: Namespace) -> dict[str, Any]:
    """
    List all lessons.

    Usage:
        empirica lesson-list
        empirica lesson-list --domain browser-automation
        empirica lesson-list --limit 20
    """
    from empirica.core.lessons import get_lesson_storage

    domain = getattr(args, "domain", None)
    limit = getattr(args, "limit", 20)

    storage = get_lesson_storage()
    lessons = storage.search_lessons(domain=domain, limit=limit)

    return {"ok": True, "count": len(lessons), "lessons": lessons}


def handle_lesson_search_command(args: Namespace) -> dict[str, Any]:
    """
    Search for lessons.

    Usage:
        empirica lesson-search --query "browser automation"
        empirica lesson-search --improves know
        empirica lesson-search --domain git
    """
    from empirica.core.lessons import get_lesson_storage

    query = getattr(args, "query", None)
    improves = getattr(args, "improves", None)
    domain = getattr(args, "domain", None)
    limit = getattr(args, "limit", 10)

    storage = get_lesson_storage()
    lessons = storage.search_lessons(query=query, domain=domain, improves_vector=improves, limit=limit)

    return {"ok": True, "query": query or improves or domain, "count": len(lessons), "lessons": lessons}


def handle_lesson_recommend_command(args: Namespace) -> dict[str, Any]:
    """
    Get lesson recommendations based on current epistemic state.

    Usage:
        empirica lesson-recommend --session-id <session_id>
        empirica lesson-recommend --know 0.4 --uncertainty 0.6
    """
    from empirica.core.lessons import get_lesson_storage

    # Get epistemic state from args or session
    epistemic_state = {}

    session_id = getattr(args, "session_id", None)
    if session_id:
        # Load from session's last PREFLIGHT
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.adapter.conn.cursor()
        cursor.execute(
            """
            SELECT know, do, context, uncertainty
            FROM reflexes
            WHERE session_id = ? AND phase = 'PREFLIGHT'
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id,),
        )
        row = cursor.fetchone()
        if row:
            epistemic_state = {
                "know": row[0] or 0,
                "do": row[1] or 0,
                "context": row[2] or 0,
                "uncertainty": row[3] or 0.5,
            }

    # Override with explicit args
    if getattr(args, "know", None) is not None:
        epistemic_state["know"] = args.know
    if getattr(args, "do", None) is not None:
        epistemic_state["do"] = args.do
    if getattr(args, "context", None) is not None:
        epistemic_state["context"] = args.context
    if getattr(args, "uncertainty", None) is not None:
        epistemic_state["uncertainty"] = args.uncertainty

    if not epistemic_state:
        return {"ok": False, "error": "Provide --session-id or epistemic vectors (--know, --do, etc.)"}

    threshold = getattr(args, "threshold", 0.6)
    storage = get_lesson_storage()
    recommendations = storage.find_best_lesson_for_gap(epistemic_state, threshold)

    return {"ok": True, "epistemic_state": epistemic_state, "threshold": threshold, "recommendations": recommendations}


def handle_lesson_stats_command(args: Namespace) -> dict[str, Any]:
    """
    Show lesson storage statistics.

    Usage:
        empirica lesson-stats
    """
    from empirica.core.lessons import get_lesson_storage

    storage = get_lesson_storage()
    stats = storage.stats()

    return {"ok": True, "stats": stats}


def handle_lesson_embed_command(args: Namespace) -> dict[str, Any]:
    """
    Embed all lessons into Qdrant for semantic search.

    Usage:
        empirica lesson-embed
        empirica lesson-embed --force  # Re-embed all
    """
    import empirica.core.lessons.storage as mod
    from empirica.core.lessons import get_lesson_storage

    # Clear singleton to force fresh Qdrant connection
    mod._storage = None

    storage = get_lesson_storage()

    if not storage._qdrant:
        return {"ok": False, "error": "Qdrant not available. Install qdrant-client."}

    getattr(args, "force", False)
    embedded = []
    failed = []

    # Get all lessons from WARM layer
    cursor = storage._conn.cursor()
    cursor.execute("SELECT id FROM lessons")
    lesson_ids = [row[0] for row in cursor.fetchall()]

    for lesson_id in lesson_ids:
        lesson = storage.get_lesson(lesson_id)
        if lesson:
            try:
                result = storage._write_search(lesson)
                if result:
                    embedded.append({"id": lesson_id, "name": lesson.name})
                else:
                    failed.append({"id": lesson_id, "error": "write failed"})
            except Exception as e:
                failed.append({"id": lesson_id, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "embedded_count": len(embedded),
        "failed_count": len(failed),
        "embedded": embedded,
        "failed": failed if failed else None,
        "collection": storage._qdrant_collection,
    }
