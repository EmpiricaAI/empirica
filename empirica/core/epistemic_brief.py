"""
Epistemic Brief — Quantified project epistemic profile.

Generates a categorised, ranked summary of a project's epistemic state:
- Knowledge State: findings density, domain coverage, fact confidence
- Risk Profile: unresolved unknowns, dead-ends, stale assumptions
- Calibration Health: grounded score, bias patterns, drift
- Active Work: open goals, stale goals, transaction velocity
- Anti-Patterns: dead-ends and mistakes ranked by impact × recency

The brief is generated at bootstrap time and surfaced at project-switch.
"""

import logging
import time

logger = logging.getLogger(__name__)


def generate_epistemic_brief(project_id: str, db_path: str | None = None, limit: int = 5) -> dict:
    """Generate a quantified epistemic brief for a project.

    Composes from SQLite (artifact counts, calibration) and Qdrant (semantic
    search for patterns). Falls back gracefully when Qdrant is unavailable.

    Args:
        project_id: Project UUID
        db_path: Path to sessions.db (auto-resolved if None)
        limit: Max items per category

    Returns:
        Dict with categories: knowledge_state, risk_profile, calibration_health,
        active_work, anti_patterns, learning_velocity
    """
    # Resolve local project_id (may differ from workspace.db UUID)
    try:
        db = _get_db(db_path)
        local_pid = _resolve_local_project_id(db, project_id)
        db.close()
    except Exception:
        local_pid = project_id

    brief = {
        "project_id": local_pid,
        "generated_at": time.time(),
        "knowledge_state": _build_knowledge_state(local_pid, db_path),
        "risk_profile": _build_risk_profile(local_pid, db_path, limit),
        "calibration_health": _build_calibration_health(local_pid, db_path),
        "active_work": _build_active_work(local_pid, db_path),
        "anti_patterns": _build_anti_patterns(local_pid, db_path, limit),
        "learning_velocity": _build_learning_velocity(local_pid, db_path),
    }
    return brief


def _get_db(db_path=None):
    """Get a SessionDatabase connection."""
    from empirica.data.session_database import SessionDatabase

    return SessionDatabase(db_path=db_path) if db_path else SessionDatabase()


def _resolve_local_project_id(db, workspace_project_id: str) -> str:
    """Resolve the local project_id from sessions.db.

    workspace.db and sessions.db may have different UUIDs for the same project.
    Falls back to the most-used project_id in findings if the workspace ID doesn't match.
    """
    cursor = db.conn.cursor()
    # Try workspace ID first
    cursor.execute("SELECT COUNT(*) FROM project_findings WHERE project_id = ?", (workspace_project_id,))
    if cursor.fetchone()[0] > 0:
        return workspace_project_id
    # Fallback: most-used project_id in this DB
    try:
        cursor.execute(
            "SELECT project_id, COUNT(*) as cnt FROM project_findings GROUP BY project_id ORDER BY cnt DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    return workspace_project_id


def _build_knowledge_state(project_id: str, db_path: str | None = None) -> dict:
    """Quantify what the project knows."""
    try:
        db = _get_db(db_path)
        cursor = db.conn.cursor()

        counts = {}
        for name, table in {
            "findings": "project_findings",
            "unknowns": "project_unknowns",
            "dead_ends": "project_dead_ends",
            "mistakes": "mistakes_made",
            "goals": "goals",
            "sessions": "sessions",
        }.items():
            try:
                if name == "sessions" or name in ("findings", "unknowns", "dead_ends"):
                    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,))
                elif name == "mistakes":
                    cursor.execute(
                        f"""SELECT COUNT(*) FROM {table} m
                        JOIN sessions s ON m.session_id = s.session_id
                        WHERE s.project_id = ?""",
                        (project_id,),
                    )
                elif name == "goals":
                    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,))
                counts[name] = cursor.fetchone()[0]
            except Exception:
                counts[name] = 0

        # Domain coverage from findings subjects
        domains = []
        try:
            cursor.execute(
                """
                SELECT subject, COUNT(*) as cnt, AVG(impact) as avg_impact
                FROM project_findings
                WHERE project_id = ? AND subject IS NOT NULL AND subject != ''
                GROUP BY subject ORDER BY cnt DESC LIMIT 5
            """,
                (project_id,),
            )
            for row in cursor.fetchall():
                domains.append({"domain": row[0], "findings_count": row[1], "avg_impact": round(row[2] or 0, 2)})
        except Exception:
            pass

        # Eidetic fact confidence (if Qdrant available)
        avg_confidence = None
        try:
            from empirica.core.qdrant.eidetic import search_eidetic

            facts = search_eidetic(project_id, "project knowledge", limit=20)
            if facts:
                confidences = [f.get("confidence", 0.5) for f in facts if f.get("confidence")]
                if confidences:
                    avg_confidence = round(sum(confidences) / len(confidences), 2)
        except Exception:
            pass

        db.close()
        return {
            "artifact_counts": counts,
            "domains": domains,
            "avg_fact_confidence": avg_confidence,
            "total_artifacts": sum(v for k, v in counts.items() if k != "sessions"),
        }
    except Exception as e:
        logger.debug(f"Knowledge state failed: {e}")
        return {"artifact_counts": {}, "domains": [], "total_artifacts": 0}


def _build_risk_profile(project_id: str, db_path: str | None = None, limit: int = 5) -> dict:
    """Quantify what the project doesn't know or got wrong."""
    try:
        db = _get_db(db_path)
        cursor = db.conn.cursor()

        # Unresolved unknowns
        unresolved = []
        try:
            cursor.execute(
                """
                SELECT unknown, impact, created_timestamp FROM project_unknowns
                WHERE project_id = ? AND is_resolved = 0
                ORDER BY impact DESC NULLS LAST, created_timestamp DESC LIMIT ?
            """,
                (project_id, limit),
            )
            for row in cursor.fetchall():
                unresolved.append({"unknown": row[0], "impact": row[1]})
        except Exception:
            pass

        # Stale assumptions (from Qdrant if available)
        stale_assumptions = []
        try:
            from empirica.core.qdrant.vector_store import search_assumptions

            assumptions = search_assumptions(project_id, "unverified belief", limit=10)
            now = time.time()
            for a in assumptions:
                age_days = (now - (a.get("timestamp") or now)) / 86400
                if age_days > 7 and a.get("confidence", 1.0) < 0.8:
                    stale_assumptions.append(
                        {
                            "assumption": a.get("assumption", ""),
                            "confidence": a.get("confidence"),
                            "age_days": round(age_days),
                        }
                    )
        except Exception:
            pass

        db.close()
        return {
            "unresolved_unknowns": unresolved,
            "unresolved_count": len(unresolved),
            "stale_assumptions": stale_assumptions[:limit],
        }
    except Exception as e:
        logger.debug(f"Risk profile failed: {e}")
        return {"unresolved_unknowns": [], "unresolved_count": 0, "stale_assumptions": []}


def _build_calibration_health(project_id: str, db_path: str | None = None) -> dict:
    """Quantify calibration accuracy."""
    try:
        # Read from .breadcrumbs.yaml (canonical calibration source)
        import yaml

        from empirica.config.path_resolver import get_empirica_root

        calibration = {}
        try:
            root = get_empirica_root()
            if root:
                bc_path = root.parent / ".breadcrumbs.yaml"
                if bc_path.exists():
                    with open(bc_path) as f:
                        bc = yaml.safe_load(f) or {}
                    cal = bc.get("calibration", {})
                    gcal = bc.get("grounded_calibration", {})
                    calibration = {
                        "observations": cal.get("observations", 0),
                        "grounded_score": gcal.get("latest_score"),
                        "grounded_coverage": gcal.get("latest_coverage"),
                        "overestimates": cal.get("overestimates", []),
                        "underestimates": cal.get("underestimates", []),
                    }
        except Exception:
            pass

        return calibration
    except Exception as e:
        logger.debug(f"Calibration health failed: {e}")
        return {}


def _build_active_work(project_id: str, db_path: str | None = None) -> dict:
    """Quantify active work state."""
    try:
        db = _get_db(db_path)
        cursor = db.conn.cursor()

        open_goals = 0
        stale_goals = 0
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM goals WHERE project_id = ? AND is_completed = 0 AND COALESCE(status, '') != 'abandoned'",
                (project_id,),
            )
            open_goals = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM goals WHERE project_id = ? AND status = 'stale'", (project_id,))
            stale_goals = cursor.fetchone()[0]
        except Exception:
            pass

        # Recent transaction count
        recent_transactions = 0
        try:
            week_ago = time.time() - (7 * 86400)
            cursor.execute(
                """
                SELECT COUNT(*) FROM reflexes
                WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
                AND phase = 'POSTFLIGHT' AND timestamp > ?
            """,
                (project_id, week_ago),
            )
            recent_transactions = cursor.fetchone()[0]
        except Exception:
            pass

        db.close()
        return {
            "open_goals": open_goals,
            "stale_goals": stale_goals,
            "recent_transactions_7d": recent_transactions,
        }
    except Exception as e:
        logger.debug(f"Active work failed: {e}")
        return {"open_goals": 0, "stale_goals": 0, "recent_transactions_7d": 0}


def _build_anti_patterns(project_id: str, db_path: str | None = None, limit: int = 5) -> dict:
    """Surface dead-ends and mistakes as explicit warnings."""
    try:
        db = _get_db(db_path)
        cursor = db.conn.cursor()

        dead_ends = []
        try:
            cursor.execute(
                """
                SELECT approach, why_failed, impact, created_timestamp
                FROM project_dead_ends
                WHERE project_id = ?
                ORDER BY impact DESC NULLS LAST, created_timestamp DESC LIMIT ?
            """,
                (project_id, limit),
            )
            for row in cursor.fetchall():
                dead_ends.append(
                    {
                        "approach": row[0],
                        "why_failed": row[1],
                        "impact": row[2],
                    }
                )
        except Exception:
            pass

        mistakes = []
        try:
            cursor.execute(
                """
                SELECT m.mistake, m.prevention, m.why_wrong
                FROM mistakes_made m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE s.project_id = ?
                ORDER BY m.created_timestamp DESC LIMIT ?
            """,
                (project_id, limit),
            )
            for row in cursor.fetchall():
                mistakes.append(
                    {
                        "mistake": row[0],
                        "prevention": row[1],
                        "why_wrong": row[2],
                    }
                )
        except Exception:
            pass

        db.close()
        return {
            "dead_ends": dead_ends,
            "mistakes": mistakes,
            "total_warnings": len(dead_ends) + len(mistakes),
        }
    except Exception as e:
        logger.debug(f"Anti-patterns failed: {e}")
        return {"dead_ends": [], "mistakes": [], "total_warnings": 0}


def _build_learning_velocity(project_id: str, db_path: str | None = None) -> dict:
    """Measure learning trajectory across recent transactions."""
    try:
        db = _get_db(db_path)
        cursor = db.conn.cursor()

        # Get recent PREFLIGHT→POSTFLIGHT deltas
        deltas = []
        try:
            cursor.execute(
                """
                SELECT know, uncertainty, completion, timestamp
                FROM reflexes
                WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
                AND phase = 'POSTFLIGHT'
                ORDER BY timestamp DESC LIMIT 10
            """,
                (project_id,),
            )
            for row in cursor.fetchall():
                if row[0] is not None:
                    deltas.append(
                        {
                            "know": row[0],
                            "uncertainty": row[1],
                            "completion": row[2],
                        }
                    )
        except Exception:
            pass

        velocity = {}
        if deltas:
            velocity["transactions_sampled"] = len(deltas)
            velocity["avg_know"] = round(sum(d["know"] for d in deltas) / len(deltas), 2)
            velocity["avg_uncertainty"] = round(sum(d.get("uncertainty", 0.5) or 0.5 for d in deltas) / len(deltas), 2)
            velocity["avg_completion"] = round(sum(d.get("completion", 0) or 0 for d in deltas) / len(deltas), 2)

        db.close()
        return velocity
    except Exception as e:
        logger.debug(f"Learning velocity failed: {e}")
        return {}


def _format_knowledge_state(brief: dict, lines: list) -> None:
    """Format knowledge state section of the epistemic brief."""
    ks = brief.get("knowledge_state", {})
    counts = ks.get("artifact_counts", {})
    if not counts:
        return
    lines.append("")
    lines.append("📊 Knowledge State")
    parts = []
    if counts.get("findings", 0):
        parts.append(f"{counts['findings']} findings")
    if counts.get("sessions", 0):
        parts.append(f"{counts['sessions']} sessions")
    if counts.get("goals", 0):
        parts.append(f"{counts['goals']} goals")
    if parts:
        lines.append(f"   {' │ '.join(parts)}")
    for d in ks.get("domains", [])[:3]:
        lines.append(f"   Domain: {d['domain']} ({d['findings_count']} findings, impact: {d['avg_impact']})")
    if ks.get("avg_fact_confidence"):
        lines.append(f"   Fact confidence: {ks['avg_fact_confidence']}")


def _format_risk_profile(brief: dict, lines: list) -> None:
    """Format risk profile section of the epistemic brief."""
    rp = brief.get("risk_profile", {})
    if rp.get("unresolved_count", 0) <= 0 and not rp.get("stale_assumptions"):
        return
    lines.append("")
    lines.append("⚠️  Risk Profile")
    if rp["unresolved_count"]:
        lines.append(f"   {rp['unresolved_count']} unresolved unknowns")
    for u in rp.get("unresolved_unknowns", [])[:3]:
        lines.append(f"   ❓ {u['unknown'][:70]}")
    for a in rp.get("stale_assumptions", [])[:2]:
        lines.append(f"   ⏰ Stale assumption ({a['age_days']}d): {a['assumption'][:60]}")


def _format_anti_patterns(brief: dict, lines: list) -> None:
    """Format anti-patterns section of the epistemic brief."""
    ap = brief.get("anti_patterns", {})
    if ap.get("total_warnings", 0) <= 0:
        return
    lines.append("")
    lines.append("🚫 Anti-Patterns")
    for de in ap.get("dead_ends", [])[:3]:
        lines.append(f"   AVOID: {de['approach'][:50]}")
        lines.append(f"          → {de['why_failed'][:50]}")
    for m in ap.get("mistakes", [])[:2]:
        lines.append(f"   FIX: {m['mistake'][:50]}")
        if m.get("prevention"):
            lines.append(f"        Prevention: {m['prevention'][:50]}")


def _format_calibration_health(brief: dict, lines: list) -> None:
    """Format calibration health section of the epistemic brief."""
    ch = brief.get("calibration_health", {})
    if ch.get("grounded_score") is None:
        return
    lines.append("")
    lines.append("🎯 Calibration")
    score = ch["grounded_score"]
    coverage = ch.get("grounded_coverage", 0)
    lines.append(f"   Score: {score:.2f} │ Coverage: {coverage:.0%} │ Observations: {ch.get('observations', 0)}")
    if ch.get("overestimates"):
        lines.append(f"   Tends to overestimate: {', '.join(ch['overestimates'][:4])}")
    if ch.get("underestimates"):
        lines.append(f"   Tends to underestimate: {', '.join(ch['underestimates'][:4])}")


def _format_active_work(brief: dict, lines: list) -> None:
    """Format active work section of the epistemic brief."""
    aw = brief.get("active_work", {})
    if aw.get("open_goals", 0) <= 0 and aw.get("recent_transactions_7d", 0) <= 0:
        return
    lines.append("")
    lines.append("🔄 Active Work")
    parts = []
    if aw.get("open_goals"):
        parts.append(f"{aw['open_goals']} open goals")
    if aw.get("stale_goals"):
        parts.append(f"{aw['stale_goals']} stale")
    if aw.get("recent_transactions_7d"):
        parts.append(f"{aw['recent_transactions_7d']} transactions (7d)")
    lines.append(f"   {' │ '.join(parts)}")


def format_brief_human(brief: dict) -> str:
    """Format epistemic brief for human-readable terminal output."""
    lines = []
    lines.append("")
    lines.append("━" * 50)
    lines.append("📋 EPISTEMIC BRIEF")
    lines.append("━" * 50)

    _format_knowledge_state(brief, lines)
    _format_risk_profile(brief, lines)
    _format_anti_patterns(brief, lines)
    _format_calibration_health(brief, lines)
    _format_active_work(brief, lines)

    # Learning Velocity
    lv = brief.get("learning_velocity", {})
    if lv.get("transactions_sampled"):
        lines.append("")
        lines.append("📈 Learning Velocity")
        lines.append(
            f"   Last {lv['transactions_sampled']} transactions: "
            f"know={lv.get('avg_know', '?')} "
            f"uncertainty={lv.get('avg_uncertainty', '?')} "
            f"completion={lv.get('avg_completion', '?')}"
        )

    lines.append("━" * 50)
    return "\n".join(lines)
