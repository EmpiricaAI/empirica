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

from empirica.core.fact_confidence import finding_fact_confidence
from empirica.core.mistake_text import build_mistake_text

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
        text = build_mistake_text(m.get("mistake"), m.get("prevention"))
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


def _read_decisions_and_assumptions(db, project_id: str) -> tuple[list[dict], list[dict]]:
    """The two artifact types that had no re-embed path until 2026-09-06.

    Shared by `_embed_project_from_db` AND the `project-embed` verb, which are
    parallel implementations of the same job. Adding these types to each by
    copy-paste would have made a third divergent path for exactly the defect that
    produced the first two.

    Note the table names: `decisions` and `assumptions`. `project_decisions` does
    not exist — a query against it returns zero rows forever and reports a
    successful rebuild.
    """
    cur = db.conn.cursor()
    cur.execute(
        """
        SELECT id, choice, rationale, alternatives, confidence_at_decision, reversibility,
               entity_type, entity_id, session_id, transaction_id, goal_id, created_timestamp
        FROM decisions WHERE project_id = ? ORDER BY created_timestamp DESC
        """,
        (project_id,),
    )
    decisions = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT id, assumption, confidence, status, resolution_finding_id,
               entity_type, entity_id, session_id, transaction_id, goal_id, created_timestamp
        FROM assumptions WHERE project_id = ? ORDER BY created_timestamp DESC
        """,
        (project_id,),
    )
    assumptions = [dict(row) for row in cur.fetchall()]
    return decisions, assumptions


def _embed_typed_decisions(project_id: str, decisions: list[dict]) -> int:
    """Refill `project_<id>_decisions`. Returns the count that landed, not the count tried."""
    from empirica.core.qdrant.intent_layer import embed_decision

    n = 0
    for d in decisions:
        did = str(d.get("id", ""))
        if not did or not (d.get("choice") or "").strip():
            continue
        try:
            ok = embed_decision(
                project_id=project_id,
                decision_id=did,
                choice=d.get("choice") or "",
                rationale=d.get("rationale") or "",
                alternatives=d.get("alternatives"),
                confidence_at_decision=d.get("confidence_at_decision"),
                reversibility=d.get("reversibility") or "committal",
                entity_type=d.get("entity_type") or "project",
                entity_id=d.get("entity_id"),
                session_id=d.get("session_id"),
                transaction_id=d.get("transaction_id"),
                timestamp=d.get("created_timestamp"),
            )
        except Exception as e:
            logger.warning(f"decision {did} failed to re-embed: {e}")
            continue
        n += 1 if ok else 0
    if n < len([d for d in decisions if (d.get("choice") or "").strip()]):
        # Say so. A rebuild that silently under-fills is the shape that made this
        # gap invisible for as long as it existed.
        logger.warning(f"re-embedded {n} of {len(decisions)} decisions — the rest are NOT in Qdrant")
    return n


def _embed_typed_assumptions(project_id: str, assumptions: list[dict]) -> int:
    """Refill `project_<id>_assumptions`. Returns the count that landed."""
    from empirica.core.qdrant.intent_layer import embed_assumption

    n = 0
    for a in assumptions:
        aid = str(a.get("id", ""))
        if not aid or not (a.get("assumption") or "").strip():
            continue
        try:
            ok = embed_assumption(
                project_id=project_id,
                assumption_id=aid,
                assumption=a.get("assumption") or "",
                confidence=a.get("confidence") if a.get("confidence") is not None else 0.5,
                status=a.get("status") or "unverified",
                resolution_finding_id=a.get("resolution_finding_id"),
                entity_type=a.get("entity_type") or "project",
                entity_id=a.get("entity_id"),
                session_id=a.get("session_id"),
                transaction_id=a.get("transaction_id"),
                timestamp=a.get("created_timestamp"),
            )
        except Exception as e:
            logger.warning(f"assumption {aid} failed to re-embed: {e}")
            continue
        n += 1 if ok else 0
    if n < len([a for a in assumptions if (a.get("assumption") or "").strip()]):
        logger.warning(f"re-embedded {n} of {len(assumptions)} assumptions — the rest are NOT in Qdrant")
    return n


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
        # Present from the start, so a zero is a measured zero rather than a
        # missing key — the distinction that made this gap invisible.
        "decisions": 0,
        "assumptions": 0,
        "memory_total": 0,
    }

    try:
        # Gather all artifacts from SQLite
        findings = db.get_project_findings(project_id)
        # resolved=False is load-bearing. `get_project_findings` filters deprecated
        # /resolved rows internally, but `get_project_unknowns` takes a `resolved`
        # parameter that DEFAULTS TO None (no filter) — so omitting it embedded
        # answered questions as though still open. Reported by mesh-support: a
        # resolved unknown returned at rank 1 from project-search with no resolution
        # marker, so a reader could not tell it was answered and it kept
        # re-surfacing. Findings behaved correctly after the same rebuild, which is
        # what made the asymmetry hard to see — the difference is in the GETTERS,
        # not in the embed or the payload.
        unknowns = db.get_project_unknowns(project_id, resolved=False)

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

        # Decisions and assumptions — through the SHARED reader, which the
        # `project-embed` verb also calls. These two types had NO re-embed path
        # at all until 2026-09-06: this function covered six types and named
        # neither, while `recreate_project_collections` dropped their
        # collections, so a rebuild deleted every decision and assumption point
        # permanently and reported success (drop side fixed in 8fdfe751).
        #
        # Both destinations are filled, deliberately. The single verbs write the
        # TYPED collections; `log-artifacts` writes `memory`. Converging those
        # two write paths is an open decision — a rebuild's job is to REPRODUCE
        # what the writers would have written, not to pick the winner.
        decisions, assumptions = _read_decisions_and_assumptions(db, project_id)

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

        # ONE destination per type: decisions and assumptions go to their TYPED
        # collections, not to memory as well.
        #
        # bf0955f8 wrote BOTH, reasoning that a rebuild should reproduce what the
        # writers would have written. That was wrong, and measuring showed why:
        # retrieval searches memory AND the typed collections (both are in
        # `_SEARCH_COLLECTIONS`) and keys results BY COLLECTION with no
        # cross-collection dedup — so an artifact in both appears in both
        # buckets. Reproducing two writers means duplicating every artifact they
        # disagree about. 62 decisions were already double-listed; writing both
        # would have made it all 595 on the next run.
        #
        # Typed wins because the payload is strictly richer — choice, rationale,
        # alternatives, reversibility, confidence_at_decision, versus memory's
        # single concatenated `text` — and it carries the higher search boost
        # (1.3 against 1.2). Nothing is retrievable from memory that is not
        # retrievable from typed.
        counts["decisions"] = _embed_typed_decisions(project_id, decisions)
        counts["assumptions"] = _embed_typed_assumptions(project_id, assumptions)

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
            base_confidence = finding_fact_confidence(impact)
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
