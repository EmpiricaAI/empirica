"""
Goal and subtask semantic search and embedding.
"""

from __future__ import annotations

import json

from empirica.core.qdrant.collections import _goals_collection
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)
from empirica.core.qdrant.text_preview import preview_fields


def embed_goal(
    project_id: str,
    goal_id: str,
    objective: str,
    session_id: str | None = None,
    ai_id: str | None = None,
    scope_breadth: float | None = None,
    scope_duration: float | None = None,
    scope_coordination: float | None = None,
    estimated_complexity: float | None = None,
    success_criteria: list[str] | None = None,
    status: str = "in_progress",
    tags: list[str] | None = None,
    timestamp: float | None = None,
    description: str | None = None,
) -> bool:
    """
    Embed a goal to Qdrant for semantic search across sessions.

    Called automatically when goals are created. Enables:
    - "Find goals similar to this task"
    - "What goals have been completed for similar objectives?"
    - Post-compact context recovery via semantic retrieval

    Args:
        project_id: Project UUID
        goal_id: Goal UUID
        objective: Goal objective/description (main searchable text)
        session_id: Session where goal was created
        ai_id: AI that created the goal
        scope_breadth: How wide the goal spans (0-1)
        scope_duration: Expected lifetime (0-1)
        scope_coordination: Multi-agent coordination needed (0-1)
        estimated_complexity: Complexity estimate (0-1)
        success_criteria: List of success criteria descriptions
        status: Goal status (in_progress, complete, blocked)
        tags: Optional tags for filtering
        timestamp: Creation timestamp

    Returns:
        True if successful, False if Qdrant not available
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _goals_collection(project_id)

        # Ensure collection exists
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        # Build rich text for embedding - combines objective, description, criteria
        text_parts = [objective]
        if description:
            text_parts.append(description)
        if success_criteria:
            text_parts.append("Success criteria: " + "; ".join(success_criteria[:5]))
        embed_text = ". ".join(text_parts)

        vector = _get_embedding_safe(embed_text)
        if vector is None:
            return False

        import hashlib
        import time

        payload = {
            "type": "goal",
            # The goal's own id. Without it, retrieval hands back goal_id=None and
            # the PREFLIGHT/CHECK reconciler (_reconcile_goals_against_sqlite) has
            # nothing to look up — a completed goal keeps surfacing as in_progress
            # forever. The md5 point_id is one-way, so the id must ride the payload.
            "goal_id": goal_id,
            "created_at": timestamp or time.time(),
            **preview_fields("objective", objective),
            **preview_fields("description", description),
            "session_id": session_id,
            "ai_id": ai_id,
            "scope": {
                "breadth": scope_breadth,
                "duration": scope_duration,
                "coordination": scope_coordination,
            },
            "estimated_complexity": estimated_complexity,
            "success_criteria": success_criteria or [],
            "status": status,
            "tags": tags or [],
            "timestamp": timestamp or time.time(),
            "is_completed": status == "complete",
        }

        point_id = int(hashlib.md5(goal_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed goal: {e}")
        return False


def embed_subtask(
    project_id: str,
    subtask_id: str,
    description: str,
    goal_id: str,
    goal_objective: str | None = None,
    session_id: str | None = None,
    ai_id: str | None = None,
    epistemic_importance: str = "medium",
    status: str = "pending",
    completion_evidence: str | None = None,
    findings: list[str] | None = None,
    unknowns: list[str] | None = None,
    timestamp: float | None = None,
) -> bool:
    """
    Embed a subtask to Qdrant for semantic search.

    Subtasks are linked to goals. Enables:
    - "What subtasks have been done for similar objectives?"
    - "Find completed work related to this task"

    Args:
        project_id: Project UUID
        subtask_id: Subtask UUID
        description: Subtask description (main searchable text)
        goal_id: Parent goal UUID
        goal_objective: Parent goal objective (for richer embedding)
        session_id: Session where subtask was created
        ai_id: AI that created the subtask
        epistemic_importance: critical/high/medium/low
        status: pending/in_progress/completed/blocked/skipped
        completion_evidence: Evidence of completion (commit hash, etc.)
        findings: Findings discovered while working on subtask
        unknowns: Unknowns discovered while working on subtask
        timestamp: Creation timestamp

    Returns:
        True if successful, False if Qdrant not available
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _goals_collection(project_id)

        # Ensure collection exists
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        # Build rich text for embedding - combines subtask + goal context
        text_parts = [description]
        if goal_objective:
            text_parts.append(f"Part of goal: {goal_objective}")
        if findings:
            text_parts.append("Findings: " + "; ".join(findings[:3]))
        embed_text = ". ".join(text_parts)

        vector = _get_embedding_safe(embed_text)
        if vector is None:
            return False

        import hashlib
        import time

        payload = {
            "type": "subtask",
            # The subtask's OWN id — distinct from goal_id below, which is the
            # PARENT's. Reconciliation needs both: subtask_id to correct the
            # subtask's own status, goal_id to drop subtasks of completed goals.
            "subtask_id": subtask_id,
            **preview_fields("description", description),
            "goal_id": goal_id,
            "goal_objective": goal_objective[:200] if goal_objective else None,
            "session_id": session_id,
            "ai_id": ai_id,
            "epistemic_importance": epistemic_importance,
            "status": status,
            "completion_evidence": completion_evidence,
            "findings": findings or [],
            "unknowns": unknowns or [],
            "created_at": timestamp or time.time(),
            "timestamp": timestamp or time.time(),
            "is_completed": status == "completed",
        }

        point_id = int(hashlib.md5(subtask_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed subtask: {e}")
        return False


def search_goals(
    project_id: str,
    query: str,
    item_type: str | None = None,
    status: str | None = None,
    ai_id: str | None = None,
    include_subtasks: bool = True,
    limit: int = 10,
) -> list[dict]:
    """
    Semantic search for goals and subtasks across all sessions.

    Use this for:
    - Post-compact context recovery: "What was I working on?"
    - Task discovery: "Find goals similar to this task"
    - Progress tracking: "What's been completed for X?"

    Args:
        project_id: Project UUID
        query: Semantic search query (e.g., "authentication system")
        item_type: Filter by type ("goal" or "subtask"), None for both
        status: Filter by status (in_progress, complete, pending, etc.)
        ai_id: Filter by AI that created it
        include_subtasks: If False, only return goals
        limit: Maximum results

    Returns:
        List of matching goals/subtasks with scores and metadata
    """
    if not _check_qdrant_available():
        return []

    try:
        client = _get_qdrant_client()
        if client is None:
            return []
        coll = _goals_collection(project_id)

        if not client.collection_exists(coll):
            return []

        vector = _get_embedding_safe(query)
        if vector is None:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = []

        # Filter by type
        if item_type:
            conditions.append(FieldCondition(key="type", match=MatchValue(value=item_type)))
        elif not include_subtasks:
            conditions.append(FieldCondition(key="type", match=MatchValue(value="goal")))

        # Filter by status
        if status:
            conditions.append(FieldCondition(key="status", match=MatchValue(value=status)))

        # Filter by AI
        if ai_id:
            conditions.append(FieldCondition(key="ai_id", match=MatchValue(value=ai_id)))

        query_filter = Filter(must=conditions) if conditions else None

        results = client.query_points(
            collection_name=coll,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "score": getattr(r, "score", 0.0) or 0.0,
                "type": (r.payload or {}).get("type"),
                "objective": (r.payload or {}).get("objective_full") or (r.payload or {}).get("objective"),
                "description": (r.payload or {}).get("description_full") or (r.payload or {}).get("description"),
                "goal_id": (r.payload or {}).get("goal_id"),
                "subtask_id": (r.payload or {}).get("subtask_id"),
                "session_id": (r.payload or {}).get("session_id"),
                "ai_id": (r.payload or {}).get("ai_id"),
                "status": (r.payload or {}).get("status"),
                "is_completed": (r.payload or {}).get("is_completed", False),
                "scope": (r.payload or {}).get("scope"),
                "success_criteria": (r.payload or {}).get("success_criteria", []),
                "findings": (r.payload or {}).get("findings", []),
                "tags": (r.payload or {}).get("tags", []),
                "timestamp": (r.payload or {}).get("timestamp"),
            }
            for r in results.points
        ]
    except Exception as e:
        logger.debug(f"search_goals failed: {e}")
        return []


def update_goal_status(
    project_id: str,
    goal_id: str,
    status: str,
    completion_evidence: str | None = None,
) -> bool:
    """
    Update goal status in Qdrant (e.g., when completed).

    Args:
        project_id: Project UUID
        goal_id: Goal UUID
        status: New status (in_progress, complete, blocked)
        completion_evidence: Evidence of completion

    Returns:
        True if updated successfully
    """
    if not _check_qdrant_available():
        return False

    try:
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _goals_collection(project_id)

        if not client.collection_exists(coll):
            return False

        import hashlib

        point_id = int(hashlib.md5(goal_id.encode()).hexdigest()[:15], 16)

        # Get existing point
        points = client.retrieve(collection_name=coll, ids=[point_id], with_payload=True, with_vectors=True)
        if not points:
            return False

        point = points[0]
        payload = point.payload or {}
        payload["status"] = status
        # Both spellings are live: the SQL layer writes 'completed', this module's
        # embed default docstring says 'complete'. Normalizing here keeps the flag
        # true for either — an exact-match on one spelling silently recorded
        # is_completed=False for the other.
        payload["is_completed"] = status in ("complete", "completed")
        # Backfill: points embedded before goal_id rode the payload can gain it
        # here, so the PREFLIGHT/CHECK reconciler can look them up from now on.
        payload.setdefault("goal_id", goal_id)
        if completion_evidence:
            payload["completion_evidence"] = completion_evidence

        from qdrant_client.models import PointStruct

        updated_point = PointStruct(id=point_id, vector=point.vector, payload=payload)
        client.upsert(collection_name=coll, points=[updated_point])
        return True
    except Exception as e:
        logger.warning(f"Failed to update goal status: {e}")
        return False


def sync_goals_to_qdrant(project_id: str) -> int:
    """
    Sync all goals and subtasks from SQLite to Qdrant.

    Use this for:
    - Initial setup when enabling Qdrant
    - Re-sync after switching embedding providers

    Returns:
        Number of items synced
    """
    if not _check_qdrant_available():
        return 0

    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        synced = 0

        cursor = db.conn.cursor()

        # Sync goals
        cursor.execute(
            """
            SELECT g.id, g.objective, g.session_id, g.scope, g.estimated_complexity,
                   g.status, g.created_timestamp, s.ai_id
            FROM goals g
            LEFT JOIN sessions s ON g.session_id = s.session_id
            WHERE g.session_id IN (
                SELECT session_id FROM sessions WHERE project_id = ?
            )
        """,
            (project_id,),
        )

        for row in cursor.fetchall():
            goal_id, objective, session_id, scope_json, complexity, status, ts, ai_id = row

            scope = json.loads(scope_json) if scope_json else {}

            # Get success criteria
            cursor.execute("SELECT description FROM success_criteria WHERE goal_id = ?", (goal_id,))
            criteria = [r[0] for r in cursor.fetchall()]

            if embed_goal(
                project_id=project_id,
                goal_id=goal_id,
                objective=objective,
                session_id=session_id,
                ai_id=ai_id,
                scope_breadth=scope.get("breadth"),
                scope_duration=scope.get("duration"),
                scope_coordination=scope.get("coordination"),
                estimated_complexity=complexity,
                success_criteria=criteria,
                status=status or "in_progress",
                timestamp=ts,
            ):
                synced += 1

        # Sync subtasks
        cursor.execute(
            """
            SELECT st.id, st.description, st.goal_id, g.objective, st.status,
                   st.epistemic_importance, st.completion_evidence, st.created_timestamp,
                   g.session_id
            FROM subtasks st
            JOIN goals g ON st.goal_id = g.id
            WHERE g.session_id IN (
                SELECT session_id FROM sessions WHERE project_id = ?
            )
        """,
            (project_id,),
        )

        for row in cursor.fetchall():
            subtask_id, desc, goal_id, goal_obj, status, importance, evidence, ts, session_id = row

            if embed_subtask(
                project_id=project_id,
                subtask_id=subtask_id,
                description=desc,
                goal_id=goal_id,
                goal_objective=goal_obj,
                session_id=session_id,
                epistemic_importance=importance or "medium",
                status=status or "pending",
                completion_evidence=evidence,
                timestamp=ts,
            ):
                synced += 1

        db.close()
        return synced
    except Exception as e:
        logger.warning(f"Failed to sync goals to Qdrant: {e}")
        return 0


# =============================================================================
# GROUNDED CALIBRATION EMBEDDING (v1.5.0)
# =============================================================================
