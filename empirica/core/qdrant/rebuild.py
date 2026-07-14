"""
Qdrant rebuild from SQLite — rebuild all collections from persistent DB state.

Used by `empirica rebuild --qdrant` to restore Qdrant after:
- Model/dimension change (e.g., nomic-embed-text → qwen3-embedding)
- Qdrant data loss or fresh deployment
- Collection corruption

Iterates all workspace projects, recreates collections at current dimensions,
and re-embeds all artifacts from each project's sessions.db.
"""

import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_all_projects() -> list[dict]:
    """Get all active projects from workspace.db."""
    workspace_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
    if not workspace_db.exists():
        return []

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, trajectory_path
            FROM global_projects
            WHERE status = 'active'
        """)
        projects = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return projects
    except Exception as e:
        logger.warning(f"Failed to read workspace.db: {e}")
        return []


def _build_finding_items(findings: list[dict]) -> list[dict]:
    """Build memory items from findings."""
    items = []
    for f in findings:
        fid = f.get("finding_id") or str(f.get("id", ""))
        if not fid:
            continue
        items.append(
            {
                "id": fid,
                "text": f.get("finding", ""),
                "type": "finding",
                "goal_id": f.get("goal_id"),
                "subtask_id": f.get("subtask_id"),
                "session_id": f.get("session_id"),
                "timestamp": f.get("created_timestamp"),
                "subject": f.get("subject"),
            }
        )
    return items


def _build_unknown_items(unknowns: list[dict]) -> list[dict]:
    """Build memory items from unknowns."""
    items = []
    for u in unknowns:
        uid = u.get("unknown_id") or str(u.get("id", ""))
        if not uid:
            continue
        items.append(
            {
                "id": uid,
                "text": u.get("unknown", ""),
                "type": "unknown",
                "goal_id": u.get("goal_id"),
                "subtask_id": u.get("subtask_id"),
                "session_id": u.get("session_id"),
                "timestamp": u.get("created_timestamp"),
                "subject": u.get("subject"),
                "is_resolved": u.get("is_resolved", False),
            }
        )
    return items


def _build_mistake_items(mistakes: list[dict]) -> list[dict]:
    """Build memory items from mistakes."""
    items = []
    for m in mistakes:
        mid_str = str(m.get("id", ""))
        if not mid_str:
            continue
        text = f"{m.get('mistake', '')} Prevention: {m.get('prevention', '')}"
        items.append(
            {
                "id": f"mistake_{mid_str}",
                "text": text,
                "type": "mistake",
                "session_id": m.get("session_id"),
                "timestamp": m.get("created_timestamp"),
            }
        )
    return items


def _build_dead_end_items(dead_ends: list[dict]) -> list[dict]:
    """Build memory items from dead ends."""
    items = []
    for d in dead_ends:
        did = d.get("dead_end_id") or str(d.get("id", ""))
        if not did:
            continue
        text = f"DEAD END: {d.get('approach', '')} Why failed: {d.get('why_failed', '')}"
        items.append(
            {
                "id": did,
                "text": text,
                "type": "dead_end",
                "session_id": d.get("session_id"),
                "goal_id": d.get("goal_id"),
                "subtask_id": d.get("subtask_id"),
                "timestamp": d.get("created_timestamp"),
            }
        )
    return items


def _build_lesson_items(lessons: list[dict]) -> list[dict]:
    """Build memory items from lessons."""
    items = []
    for lesson in lessons:
        lid = str(lesson.get("id", ""))
        if not lid:
            continue
        text = f"LESSON: {lesson.get('name', '')} - {lesson.get('description', '')} Domain: {lesson.get('domain', '')}"
        items.append(
            {
                "id": f"lesson_{lid}",
                "text": text,
                "type": "lesson",
                "lesson_id": lesson.get("id"),
                "domain": lesson.get("domain"),
                "tags": lesson.get("tags"),
                "timestamp": lesson.get("created_timestamp"),
            }
        )
    return items


def _build_snapshot_items(snapshots: list[dict]) -> list[dict]:
    """Build memory items from epistemic snapshots."""
    items = []
    for snap in snapshots:
        context = snap.get("context_summary", "")
        if not context:
            continue
        sid = snap.get("snapshot_id") or str(snap.get("id", ""))
        if not sid:
            continue
        text = f"SESSION NARRATIVE: {context}"
        items.append(
            {
                "id": f"snap_{sid}",
                "text": text,
                "type": "episodic",
                "session_id": snap.get("session_id"),
                "snapshot_id": snap.get("snapshot_id"),
                "timestamp": snap.get("timestamp"),
            }
        )
    return items


def _embed_project_from_db(project_id: str, db_path: str, project_root: str) -> dict[str, Any]:
    """Re-embed all artifacts for a project from its sessions.db into Qdrant.

    This is the core embed logic extracted for reuse by both project-embed
    and rebuild_qdrant_from_db.

    Returns dict with counts of embedded items per type.
    """
    from empirica.core.qdrant.connection import _check_qdrant_available
    from empirica.core.qdrant.eidetic import embed_eidetic
    from empirica.core.qdrant.memory import upsert_memory
    from empirica.data.session_database import SessionDatabase

    if not _check_qdrant_available():
        return {"error": "Qdrant not available"}

    db = SessionDatabase(db_path=db_path)
    counts = {
        "findings": 0,
        "unknowns": 0,
        "mistakes": 0,
        "dead_ends": 0,
        "lessons": 0,
        "snapshots": 0,
        "eidetic": 0,
        "code_api": 0,
        "memory_total": 0,
    }

    try:
        # Gather all artifacts from SQLite
        findings = db.get_project_findings(project_id)
        unknowns = db.get_project_unknowns(project_id)

        cur = db.conn.cursor()

        # Mistakes
        cur.execute(
            """
            SELECT m.id, m.mistake, m.prevention, m.session_id
            FROM mistakes_made m
            JOIN sessions s ON m.session_id = s.session_id
            WHERE s.project_id = ?
            ORDER BY m.created_timestamp DESC
        """,
            (project_id,),
        )
        mistakes = [dict(row) for row in cur.fetchall()]

        # Dead ends
        cur.execute(
            """
            SELECT id, approach, why_failed, session_id, goal_id, subtask_id, created_timestamp
            FROM project_dead_ends
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """,
            (project_id,),
        )
        dead_ends = [dict(row) for row in cur.fetchall()]

        # Lessons
        cur.execute("""
            SELECT id, name, description, domain, tags, lesson_data, created_timestamp
            FROM lessons
            ORDER BY created_timestamp DESC
        """)
        lessons = [dict(row) for row in cur.fetchall()]

        # Epistemic snapshots (episodic memory)
        cur.execute(
            """
            SELECT snapshot_id, session_id, context_summary, timestamp
            FROM epistemic_snapshots
            WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
            ORDER BY timestamp DESC
        """,
            (project_id,),
        )
        snapshots = [dict(row) for row in cur.fetchall()]

        db.close()

        # Build memory items using ACTUAL artifact IDs from SQLite.
        # Must match embed_single_memory_item() ID scheme (string UUIDs →
        # md5 hash in upsert_memory). See project_embed.py for full rationale.
        mem_items: list[dict] = []
        mem_items.extend(_build_finding_items(findings))
        mem_items.extend(_build_unknown_items(unknowns))
        mem_items.extend(_build_mistake_items(mistakes))
        mem_items.extend(_build_dead_end_items(dead_ends))
        mem_items.extend(_build_lesson_items(lessons))
        mem_items.extend(_build_snapshot_items(snapshots))

        upsert_memory(project_id, mem_items)

        counts["findings"] = len(findings)
        counts["unknowns"] = len(unknowns)
        counts["mistakes"] = len(mistakes)
        counts["dead_ends"] = len(dead_ends)
        counts["lessons"] = len(lessons)
        counts["snapshots"] = len(snapshots)
        counts["memory_total"] = len(mem_items)

        # Eidetic rehydration from findings
        for f in findings:
            finding_text = f.get("finding", "")
            if not finding_text:
                continue
            content_hash = hashlib.md5(finding_text.encode()).hexdigest()
            impact = f.get("impact")
            base_confidence = float(impact) if impact else 0.6
            try:
                success = embed_eidetic(
                    project_id=project_id,
                    fact_id=f.get("id", content_hash),
                    content=finding_text,
                    fact_type="fact",
                    domain=f.get("subject"),
                    source_sessions=[f.get("session_id")] if f.get("session_id") else None,
                    source_findings=[f.get("id")] if f.get("id") else None,
                    confidence=base_confidence,
                    tags=[f.get("subject")] if f.get("subject") else None,
                )
                if success:
                    counts["eidetic"] += 1
            except Exception as e:
                logger.debug(f"Eidetic embed failed for finding {f.get('id', 'unknown')}: {e}")

        # Code API embedding
        try:
            from empirica.core.qdrant.code_embeddings import embed_project_code

            code_root = Path(project_root)
            if code_root.is_dir():
                code_result = embed_project_code(project_id, code_root)
                counts["code_api"] = code_result.get("modules_embedded", 0)
        except Exception as e:
            logger.debug(f"Code embedding skipped for {project_id}: {e}")

    except Exception as e:
        logger.error(f"Failed to embed project {project_id}: {e}")
        counts["error"] = str(e)

    return counts


def rebuild_qdrant_from_db() -> dict:
    """Rebuild all Qdrant collections from SQLite for all workspace projects.

    Steps:
    1. Get all active projects from workspace.db
    2. For each project: recreate collections at current dimensions, re-embed from DB
    3. Recreate global collections

    Returns summary dict with per-project results.
    """
    from empirica.core.qdrant.collections import (
        recreate_global_collections,
        recreate_project_collections,
    )
    from empirica.core.qdrant.connection import _check_qdrant_available

    if not _check_qdrant_available():
        return {"ok": False, "error": "Qdrant not available"}

    projects = _get_all_projects()
    if not projects:
        return {"ok": False, "error": "No projects found in workspace.db"}

    results = {
        "ok": True,
        "projects": {},
        "global_collections": None,
        "total_projects": len(projects),
        "successful": 0,
        "failed": 0,
    }

    for project in projects:
        project_id = project["id"]
        project_name = project.get("name", project_id)
        trajectory_path = project.get("trajectory_path", "")

        if not trajectory_path or not Path(trajectory_path).is_dir():
            results["projects"][project_name] = {"error": f"Path not found: {trajectory_path}"}
            results["failed"] += 1
            continue

        # Find sessions.db — trajectory_path may point to .empirica/ or project root
        if trajectory_path.endswith(".empirica"):
            db_path = os.path.join(trajectory_path, "sessions", "sessions.db")
            project_root = os.path.dirname(trajectory_path)
        else:
            db_path = os.path.join(trajectory_path, ".empirica", "sessions", "sessions.db")
            project_root = trajectory_path

        if not os.path.exists(db_path):
            results["projects"][project_name] = {"skipped": "No sessions.db"}
            continue

        logger.info(f"Rebuilding Qdrant for project: {project_name} ({project_id})")

        # Step 1: Recreate collections with current dimensions
        try:
            recreate_result = recreate_project_collections(project_id)
        except Exception as e:
            results["projects"][project_name] = {"error": f"Collection recreate failed: {e}"}
            results["failed"] += 1
            continue

        # Step 2: Re-embed from DB — per-project guard (mirrors the Step 1 guard
        # above) so one bad/old-schema project DB can't crash the whole rebuild.
        # _embed_project_from_db opens SessionDatabase (which runs migrations)
        # before its own try block, so an old project DB missing a column (e.g.
        # 'ai_id') would otherwise raise uncaught and abort every remaining project.
        try:
            embed_result = _embed_project_from_db(project_id, db_path, project_root)
        except Exception as e:
            results["projects"][project_name] = {"error": f"Embed failed: {e}"}
            results["failed"] += 1
            continue

        results["projects"][project_name] = {
            "collections": recreate_result,
            "embedded": embed_result,
        }

        if "error" in embed_result:
            results["failed"] += 1
        else:
            results["successful"] += 1

    # Step 3: Recreate global collections
    try:
        results["global_collections"] = recreate_global_collections()
    except Exception as e:
        results["global_collections"] = {"error": str(e)}

    return results
