"""build_bootstrap_payload — composer for the three-circle wire shape.

Pure function consumed by:
  - CLI hooks (post-compact.py / session-init.py)
  - Daemon endpoint GET /api/v1/bootstrap
  - MCP tool mcp__empirica__bootstrap_context

See docs/specs/PROPOSAL_BOOTSTRAP_AGGREGATOR.md for the design rationale.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .circles import (
    circle_1_active_state,
    circle_2_persistent_reference,
    circle_3_topic_relevant_backlog,
)
from .edges import attach_edges_to_payload
from .situation import build_situation
from .topic import DEFAULT_SIMILARITY_THRESHOLD, detect_active_topic

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2"


def build_bootstrap_payload(
    project_path: str | Path,
    session_id: str | None = None,
    *,
    project_id: str | None = None,
    transaction_state: dict | None = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    limits: dict | None = None,
) -> dict[str, Any]:
    """Build the bootstrap context payload (schema_version "2").

    Single source of truth for the three-circle surfacing model. Caller can
    override limits per-section; otherwise per-spec defaults apply (see
    PROPOSAL_BOOTSTRAP_AGGREGATOR.md → Wire shape → `limits` block).

    Args:
        project_path: Project root containing .empirica/sessions/sessions.db.
        session_id: Active session UUID (informational; queries scope by project_id).
        project_id: Canonical project UUID (looked up if not provided).
        transaction_state: Optional pre-resolved transaction state. If None,
            payload still works (circle 3 falls back to recent-findings topic).
        similarity_threshold: Cosine threshold for circle 3 (default 0.65).
        limits: Optional per-section caps override. Shape matches the
            wire's `limits` block.

    Returns:
        Dict matching the v2 wire shape. All artifact IDs are UUIDs the
        v0.5 daemon endpoints can resolve.
    """
    project_path = Path(project_path)
    if project_id is None:
        project_id = _resolve_project_id(project_path)

    project_name = _resolve_project_name(project_path)

    # Active topic detection (used by circle 3 only)
    topic = detect_active_topic(
        project_path,
        project_id,
        similarity_threshold=similarity_threshold,
        transaction_state=transaction_state,
    )

    # Three circles
    if project_id:
        c1 = circle_1_active_state(project_path, project_id, limits=_subdict(limits, "active_state"))
        c2 = circle_2_persistent_reference(project_path, project_id,
                                            limits=_subdict(limits, "persistent_reference"))
        c3 = circle_3_topic_relevant_backlog(project_path, project_id, topic,
                                              limits=_subdict(limits, "topic_relevant_backlog"))
    else:
        # No project_id resolved — empty payload but valid shape
        c1 = _empty_circle_1()
        c2 = _empty_circle_2()
        c3 = _empty_circle_3()

    # Compaction-recovery narrative — same shape as CLI bootstrap's
    # top-level `situation` field. Best-effort; missing data → fields
    # omitted, never raises. Placed first so the AI sees state before
    # deep lists (attention-decay-aware).
    situation = build_situation(project_path, project_id)

    payload: dict[str, Any] = {
        "situation": situation,
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "project_path": str(project_path),
        "project_name": project_name,
        "session_id": session_id,
        "ai_id": _resolve_ai_id(),
        "transaction_state": transaction_state or {"active": False},
        "active_topic": topic,
        "active_state": c1,
        "persistent_reference": c2,
        "topic_relevant_backlog": c3,
        "calibration": _load_calibration_summary(project_path),
        "limits": _effective_limits(limits),
    }

    # Sidecar blocks — same shape as CLI bootstrap. Best-effort, additive.
    # Extension Overview pane consumes these for project context + activity
    # rhythm + the flow chip. (proposal prop_sf63hrj7xvd3je2gcbzitwsnbi)
    _attach_sidecar_blocks(payload, project_path, project_id)

    # Single batched edge fold across all surfaced items
    attach_edges_to_payload(project_path, payload)

    return payload


def _attach_sidecar_blocks(
    payload: dict[str, Any], project_path: Path, project_id: str | None
) -> None:
    """Surface CLI-only fields on the HTTP wire — Option B per proposal.

    Three blocks query the DB directly with bare SQL (decouples from
    SessionDatabase schema evolution). flow_metrics requires SessionDatabase's
    assessment-aggregation logic, so we delegate there inside an isolated
    try/except — if init fails (e.g. schema mismatch in a test fixture),
    flow_metrics gracefully degrades to absent.

    All four blocks are best-effort. Missing data → key omitted, never raises.
    """
    if project_id is None:
        return
    db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return

    try:
        project_block = _build_project_block(db_path, project_id)
        if project_block:
            payload["project"] = project_block
    except Exception as e:
        logger.debug(f"sidecar project block skipped: {e}")

    try:
        gs = _build_git_status(project_path)
        if gs:
            payload["git_status"] = gs
    except Exception as e:
        logger.debug(f"sidecar git_status skipped: {e}")

    try:
        payload["reference_docs_count"] = _count_reference_docs(db_path, project_id)
    except Exception as e:
        logger.debug(f"sidecar reference_docs_count skipped: {e}")

    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase(db_path=str(db_path))
        flow = db.calculate_flow_metrics(project_id, limit=5)
        if flow and flow.get("current_flow"):
            payload["flow_metrics"] = flow
    except Exception as e:
        logger.debug(f"sidecar flow_metrics skipped: {e}")


def _build_project_block(db_path: Path, project_id: str) -> dict | None:
    """Direct-SQL build of the project metadata block. Returns None if no row."""
    import json as _json
    import sqlite3 as _sq
    conn = _sq.connect(str(db_path))
    conn.row_factory = _sq.Row
    try:
        row = conn.execute(
            "SELECT id, name, description, status, repos, "
            "total_sessions, total_goals, project_type "
            "FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        repos = row["repos"] or "[]"
        if isinstance(repos, str):
            try:
                repos = _json.loads(repos)
            except (ValueError, TypeError):
                repos = []
        # Live counts (the stored denormalized counters drift — see
        # session_database._count_project_artifacts).
        try:
            ts = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        except Exception:
            ts = row["total_sessions"] or 0
        # Transactions live as transaction_id columns scattered across the
        # artifact tables — there is no standalone `transactions` table.
        # Count distinct transaction_ids across all artifact tables scoped
        # to this project's sessions. (Fixes prop_aq5p from extension AI:
        # previous query targeted a non-existent table, silent except
        # swallowed the error, tt always returned 0 on the bootstrap card.)
        try:
            tt = conn.execute("""
                WITH project_sessions AS (
                    SELECT session_id FROM sessions WHERE project_id = ?
                )
                SELECT COUNT(DISTINCT transaction_id) FROM (
                    SELECT transaction_id FROM goals
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM project_findings
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM project_unknowns
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM project_dead_ends
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM mistakes_made
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM assumptions
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM decisions
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                    UNION
                    SELECT transaction_id FROM reflexes
                        WHERE session_id IN (SELECT session_id FROM project_sessions)
                        AND transaction_id IS NOT NULL
                )
            """, (project_id,)).fetchone()[0]
        except _sq.OperationalError:
            # Narrow exception: catches "no such table" / "no such column"
            # if the schema drifts again, surfaces other DB errors instead
            # of silently swallowing them.
            tt = 0
        try:
            tg = conn.execute(
                "SELECT COUNT(*) FROM goals WHERE project_id = ? OR "
                "session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)",
                (project_id, project_id),
            ).fetchone()[0]
        except Exception:
            tg = row["total_goals"] or 0
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"] or "",
            "status": row["status"] or "active",
            "repos": repos,
            "total_sessions": ts,
            "total_transactions": tt,
            "total_goals": tg,
            "type": row["project_type"] or "product",
        }
    finally:
        conn.close()


def _build_git_status(project_path: Path) -> dict | None:
    """Direct subprocess git for the activity-rhythm block."""
    import subprocess as _sp
    try:
        br = _sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_path), capture_output=True, text=True, timeout=2,
        )
        if br.returncode != 0:
            return None
        branch = br.stdout.strip()

        uncommitted = 0
        untracked = 0
        try:
            st = _sp.run(
                ["git", "status", "--porcelain"],
                cwd=str(project_path), capture_output=True, text=True, timeout=2,
            )
            for line in st.stdout.splitlines():
                if line.startswith("??"):
                    untracked += 1
                elif line.strip():
                    uncommitted += 1
        except Exception:  # noqa: S110 — best-effort git introspection
            pass

        recent: list[str] = []
        try:
            lg = _sp.run(
                ["git", "log", "-3", "--format=%h %s"],
                cwd=str(project_path), capture_output=True, text=True, timeout=2,
            )
            if lg.returncode == 0:
                recent = [ln for ln in lg.stdout.splitlines() if ln.strip()]
        except Exception:  # noqa: S110 — best-effort git introspection
            pass

        return {
            "current_branch": branch,
            "uncommitted_changes": uncommitted,
            "untracked_files": untracked,
            "recent_commits": recent,
        }
    except Exception:
        return None


def _count_reference_docs(db_path: Path, project_id: str) -> int:
    """Direct SQL count of project-scoped reference docs."""
    import sqlite3 as _sq
    conn = _sq.connect(str(db_path))
    try:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM project_reference_docs WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        except Exception:
            return 0
    finally:
        conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────


def _subdict(limits: dict | None, section: str) -> dict | None:
    """Pull just the section's limits sub-dict for a circle query."""
    if not limits:
        return None
    return limits.get(section) if isinstance(limits, dict) else None


def _resolve_project_id(project_path: Path) -> str | None:
    """Resolve project UUID via canonical InstanceResolver path,
    or via projects.name → projects.id slug→UUID lookup."""
    try:
        from empirica.api.daemon_project import _read_project_yaml, _resolve_project_uuid
        project_yaml = _read_project_yaml(project_path)
        yaml_id = project_yaml.get("project_id") if isinstance(project_yaml, dict) else None
        return _resolve_project_uuid(project_path, yaml_id)
    except Exception:
        return None


def _resolve_project_name(project_path: Path) -> str:
    """Read project.yaml's display_name → name → folder name."""
    try:
        from empirica.api.daemon_project import _read_project_yaml
        py = _read_project_yaml(project_path)
        if isinstance(py, dict):
            return py.get("display_name") or py.get("name") or project_path.name
    except Exception as e:
        logger.debug(f"_resolve_project_name fallback to folder name: {e}")
    return project_path.name


def _resolve_ai_id() -> str:
    """Resolve active session's ai_id; defaults to 'claude-code'."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        ctx = R.context()
        if isinstance(ctx, dict):
            return ctx.get("ai_id") or "claude-code"
    except Exception as e:
        logger.debug(f"_resolve_ai_id fallback to claude-code: {e}")
    return "claude-code"


def _load_calibration_summary(project_path: Path) -> dict:
    """Light calibration block — biases from .breadcrumbs.yaml when available.

    Full calibration grounding is the post-test pipeline's job; here we
    just surface a summary so the AI sees its known biases at bootstrap.
    """
    try:
        import yaml
        bc_path = project_path / ".breadcrumbs.yaml"
        if not bc_path.exists():
            return {}
        with open(bc_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return {
            "track_1_observations": data.get("calibration", {}).get("observations") if isinstance(data.get("calibration"), dict) else None,
            "biases_summary": _summarize_biases(data.get("calibration", {})),
        }
    except Exception:
        return {}


def _summarize_biases(calibration: Any) -> str | None:
    """Compress per-vector biases into one-line summary if present."""
    if not isinstance(calibration, dict):
        return None
    biases = calibration.get("biases")
    if not isinstance(biases, dict) or not biases:
        return None
    # Top 3 absolute biases
    items = [(k, v) for k, v in biases.items() if isinstance(v, (int, float))]
    items.sort(key=lambda x: abs(x[1]), reverse=True)
    if not items:
        return None
    parts = [f"{k}: {v:+.2f}" for k, v in items[:3]]
    return ", ".join(parts)


# ── Defaults & empties ──────────────────────────────────────────────────


_DEFAULT_LIMITS = {
    "active_state": {
        "in_progress_goals": 10,
        "active_subtasks": 20,
        "recent_findings": 10,
        "recent_decisions": 5,
        "recent_dead_ends": 5,
        "recent_mistakes": 5,
    },
    "persistent_reference": {
        "decisions_with_active_outcome": 10,
        "verified_assumptions": 10,
        "sources": 10,
    },
    "topic_relevant_backlog": {
        "open_unknowns": 5,
        "open_assumptions": 5,
        "planned_goals": 5,
        "completed_goals_relevant": 3,
        "resolved_unknowns_relevant": 5,
        "dead_ends_relevant": 3,
    },
}


def _effective_limits(override: dict | None) -> dict:
    """Merge user override on top of defaults (shallow merge per section)."""
    if not override:
        return _DEFAULT_LIMITS
    merged: dict = {}
    for section, defaults in _DEFAULT_LIMITS.items():
        section_override = override.get(section, {}) if isinstance(override, dict) else {}
        merged[section] = {**defaults, **section_override}
    return merged


def _empty_circle_1() -> dict:
    return {
        "in_progress_goals": [],
        "active_subtasks": [],
        "recent_findings": [],
        "recent_decisions": [],
        "recent_dead_ends": [],
        "recent_mistakes": [],
    }


def _empty_circle_2() -> dict:
    return {
        "decisions_with_active_outcome": [],
        "verified_assumptions": [],
        "sources": [],
    }


def _empty_circle_3() -> dict:
    return {
        "open_unknowns": [],
        "open_assumptions": [],
        "planned_goals": [],
        "completed_goals_relevant": [],
        "resolved_unknowns_relevant": [],
        "dead_ends_relevant": [],
    }
