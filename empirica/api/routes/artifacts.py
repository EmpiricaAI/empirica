"""Artifact list endpoints for the local daemon (v0.5 LOCAL-ARTIFACTS).

Eight per-type GETs sharing project-scoping + edge attachment:
  /api/v1/goals
  /api/v1/findings
  /api/v1/decisions
  /api/v1/unknowns
  /api/v1/dead-ends
  /api/v1/mistakes
  /api/v1/assumptions
  /api/v1/sources

All endpoints are scoped to the daemon's active project (resolved at startup
via `daemon_project.get_cached_daemon_project()`). If the daemon is launched
outside any project, every endpoint returns 503 with a hint.

Each row carries `related_to[]` populated from the `artifact_edges` table
(post-migration 041). Inverse-edge queries are cheap thanks to the
`(to_id, relation)` index.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from empirica.api.daemon_project import (
    get_cached_daemon_project,
    resolve_for_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


# ── Project-scope resolution ─────────────────────────────────────────


def _resolve_project_dict(
    project_id: str | None = None,
    path: str | None = None,
) -> dict:
    """Resolve the project dict for one request.

    Precedence:
      1. `?path=Y` — power-user bypass, opens Y/.empirica/ directly.
      2. `?project_id=X` — registry lookup. 404 if X not in registry.
      3. Neither — falls back to daemon's cached CWD-bound project. 503
         if daemon wasn't launched inside any project tree.

    Returns a project dict (project_id / project_path / project_name /
    project_slug / repo_url) or raises HTTPException (404 / 503).
    """
    if project_id or path:
        project = resolve_for_request(project_id=project_id, project_path_override=path)
        if not project or not project.get("project_path"):
            if path:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "path is not an Empirica project",
                        "hint": (
                            f"No .empirica/project.yaml found at or above {path}. "
                            "Use ?project_id=X with a registered project, or omit "
                            "both params to use the daemon's CWD-bound project."
                        ),
                    },
                )
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "project not registered with daemon",
                    "hint": (
                        "Run `empirica projects-discover --register` or add the "
                        "project to ~/.empirica/registry.yaml"
                    ),
                },
            )
        return project

    project = get_cached_daemon_project()
    if not project or not project.get("project_path"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Daemon not bound to a project. Run `empirica serve` from inside "
                "a project tree, register projects via `empirica projects-discover "
                "--register`, or pass ?project_id=X / ?path=Y per request."
            ),
        )
    return project


# ── DB connection (per-request) ──────────────────────────────────────


class _ReadOnlyDB:
    """Lightweight sqlite3 wrapper for daemon read endpoints.

    Daemon doesn't need SessionDatabase's migration runner / repositories /
    schema-create behavior — it's a read-only consumer of a project's existing
    sqlite. Direct sqlite3 keeps the route handlers fast and avoids dragging
    in the full SessionDatabase init chain.
    """

    def __init__(self, db_path: str):
        import sqlite3
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        if self.conn:
            self.conn.close()


def _open_db_for(project: dict) -> _ReadOnlyDB:
    """Open a read-only sqlite connection to the resolved project's sqlite."""
    from pathlib import Path
    project_path = project["project_path"]
    db_path = str(Path(project_path) / ".empirica" / "sessions" / "sessions.db")
    return _ReadOnlyDB(db_path)


def _open_db() -> _ReadOnlyDB:
    """Backward-compat alias for endpoints that haven't been threaded with
    per-request `?project_id=X` / `?path=Y` yet.

    Resolves the daemon's CWD-bound active project (current single-project
    behavior). New endpoints should prefer `_open_db_for(_resolve_project_dict(...))`
    instead so they honor per-request project selection.
    """
    project = _resolve_project_dict(None, None)
    return _open_db_for(project)


# ── Edge attachment ──────────────────────────────────────────────────


def _attach_related_to(
    db, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """For each row in `rows`, populate `row['related_to']` from artifact_edges.

    related_to[] format (per spec wire contract):
        [{"id": "<other-artifact-id>", "type": "<type>", "relation": "<rel>"}, ...]

    Uses one query for all rows (`from_id IN (...)`), then in-memory groups.
    Edge rows whose target type can't be inferred (target not found in any
    artifact table) get `type: "unknown"` so the wire contract still holds.
    """
    if not rows or not db.conn:
        return rows
    from_ids = [r["id"] for r in rows]
    if not from_ids:
        return rows

    placeholders = ",".join("?" * len(from_ids))
    cursor = db.conn.cursor()
    # Older project sessions.db files (pre-edges schema) lack artifact_edges.
    # Treat as no-edges rather than 500ing — the daemon's job is to serve
    # what each project's DB actually has. Schema drift is normal across
    # projects registered at different times. The per-table type lookups
    # below already use the same defensive pattern.
    import sqlite3 as _sqlite3
    try:
        cursor.execute(
            f"SELECT from_id, to_id, relation FROM artifact_edges WHERE from_id IN ({placeholders})",
            from_ids,
        )
        raw_edges = cursor.fetchall()
    except _sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            for row in rows:
                row["related_to"] = []
            return rows
        raise

    # Resolve to_id → type by checking each artifact table once for the union of to_ids
    to_ids = list({e[1] for e in raw_edges})
    type_index: dict[str, str] = {}
    if to_ids:
        type_lookups = [
            ("finding", "project_findings"),
            ("unknown", "project_unknowns"),
            ("dead_end", "project_dead_ends"),
            ("mistake", "mistakes_made"),
            ("assumption", "assumptions"),
            ("decision", "decisions"),
            ("source", "epistemic_sources"),
            ("goal", "goals"),
        ]
        ph = ",".join("?" * len(to_ids))
        for type_label, table in type_lookups:
            try:
                cursor.execute(f"SELECT id FROM {table} WHERE id IN ({ph})", to_ids)
                for row in cursor.fetchall():
                    type_index[row[0]] = type_label
            except Exception as e:
                logger.debug(f"_attach_related_to: type lookup on {table} failed: {e}")

    # Group edges by from_id
    edges_by_from: dict[str, list[dict[str, str]]] = {}
    for from_id, to_id, relation in raw_edges:
        edges_by_from.setdefault(from_id, []).append({
            "id": to_id,
            "type": type_index.get(to_id, "unknown"),
            "relation": relation,
        })

    for row in rows:
        row["related_to"] = edges_by_from.get(row["id"], [])
    return rows


# ── Common shape helpers ──────────────────────────────────────────────


def _to_iso(epoch_or_iso: Any) -> str | None:
    """Normalize timestamp to ISO 8601 string. Accepts REAL epoch or already-ISO TEXT."""
    if epoch_or_iso is None:
        return None
    if isinstance(epoch_or_iso, str):
        return epoch_or_iso  # assume already ISO
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(epoch_or_iso), tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _parse_data_json(raw: Any) -> dict:
    """Parse JSON data column to dict, returning {} on any error."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Per-type read helpers ────────────────────────────────────────────


def _list_findings(db, project_id: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, finding, finding_data, impact, epistemic_source, "
        "session_id, goal_id, subtask_id, transaction_id, "
        "subject, created_timestamp "
        "FROM project_findings WHERE project_id = ? "
        "ORDER BY created_timestamp DESC LIMIT ?",
        (project_id, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "finding",
            "title": (r[1] or "")[:100],
            "body": r[1] or "",
            "impact": r[3],
            "epistemic_source": r[4],
            "session_id": r[5],
            "goal_id": r[6],
            "subtask_id": r[7],
            "transaction_id": r[8],
            "subject": r[9],
            "created_at": _to_iso(r[10]),
            "data": _parse_data_json(r[2]),
        }
        for r in rows
    ]


def _list_unknowns(db, project_id: str, status: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    where = "project_id = ?"
    params: list[Any] = [project_id]
    if status == "open":
        where += " AND is_resolved = 0"
    elif status == "resolved":
        where += " AND is_resolved = 1"
    cursor.execute(
        f"SELECT id, unknown, unknown_data, impact, epistemic_source, "
        f"session_id, goal_id, subtask_id, transaction_id, "
        f"is_resolved, resolved_by, resolved_timestamp, created_timestamp "
        f"FROM project_unknowns WHERE {where} "
        f"ORDER BY created_timestamp DESC LIMIT ?",
        (*params, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "unknown",
            "title": (r[1] or "")[:100],
            "body": r[1] or "",
            "impact": r[3],
            "epistemic_source": r[4],
            "session_id": r[5],
            "goal_id": r[6],
            "subtask_id": r[7],
            "transaction_id": r[8],
            "status": "resolved" if r[9] else "open",
            "resolved_by": r[10],
            "resolved_at": _to_iso(r[11]),
            "created_at": _to_iso(r[12]),
            "data": _parse_data_json(r[2]),
        }
        for r in rows
    ]


def _list_dead_ends(db, project_id: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, approach, why_failed, dead_end_data, impact, epistemic_source, "
        "session_id, goal_id, subtask_id, transaction_id, created_timestamp "
        "FROM project_dead_ends WHERE project_id = ? "
        "ORDER BY created_timestamp DESC LIMIT ?",
        (project_id, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "dead_end",
            "title": (r[1] or "")[:100],
            "body": r[1] or "",
            "why_failed": r[2],
            "impact": r[4],
            "epistemic_source": r[5],
            "session_id": r[6],
            "goal_id": r[7],
            "subtask_id": r[8],
            "transaction_id": r[9],
            "created_at": _to_iso(r[10]),
            "data": _parse_data_json(r[3]),
        }
        for r in rows
    ]


def _list_mistakes(db, project_id: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, mistake, why_wrong, prevention, mistake_data, epistemic_source, "
        "session_id, goal_id, transaction_id, created_timestamp "
        "FROM mistakes_made WHERE project_id = ? "
        "ORDER BY created_timestamp DESC LIMIT ?",
        (project_id, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "mistake",
            "title": (r[1] or "")[:100],
            "body": r[1] or "",
            "why_wrong": r[2],
            "prevention": r[3],
            "epistemic_source": r[5],
            "session_id": r[6],
            "goal_id": r[7],
            "transaction_id": r[8],
            "created_at": _to_iso(r[9]),
            "data": _parse_data_json(r[4]),
        }
        for r in rows
    ]


def _table_has_column(db, table: str, column: str) -> bool:
    """Cheap PRAGMA-based column existence check.

    Used to keep daemon endpoints schema-resilient: the same query shape
    must work on old project DBs (migration 043/045 hasn't run yet —
    no `description` column) and new ones (column present). Falls back
    to False on any error so callers default to the old-shape SELECT.

    Driver: David, 2026-05-17 — daemon's _list_goals/_list_assumptions/
    _list_decisions all forgot to SELECT `description` after migrations
    043+045 added the column. Extension rendered title-only goals even
    when bodies were stored. This helper lets each list endpoint
    conditionally include description without breaking old-schema DBs.
    """
    try:
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cursor.fetchall())
    except Exception:
        return False


def _list_assumptions(
    db, project_id: str, confidence_min: float, limit: int
) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    has_desc = _table_has_column(db, "assumptions", "description")
    desc_col = ", description" if has_desc else ""
    cursor.execute(
        f"SELECT id, assumption, confidence, status, resolution_finding_id, "
        f"session_id, goal_id, transaction_id, created_timestamp, resolved_timestamp, "
        f"epistemic_source{desc_col} "
        f"FROM assumptions WHERE project_id = ? AND confidence >= ? "
        f"ORDER BY created_timestamp DESC LIMIT ?",
        (project_id, confidence_min, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "assumption",
            "title": (r[1] or "")[:100],
            "body": r[1] or "",
            "description": (r[11] if has_desc and len(r) > 11 else None),
            "confidence": r[2],
            "status": r[3],
            "resolution_finding_id": r[4],
            "session_id": r[5],
            "goal_id": r[6],
            "transaction_id": r[7],
            "created_at": _to_iso(r[8]),
            "resolved_at": _to_iso(r[9]),
            "epistemic_source": r[10],
        }
        for r in rows
    ]


def _list_decisions(db, project_id: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    has_desc = _table_has_column(db, "decisions", "description")
    desc_col = ", description" if has_desc else ""
    cursor.execute(
        f"SELECT id, choice, rationale, alternatives, confidence_at_decision, "
        f"reversibility, outcome, regret_score, "
        f"session_id, goal_id, transaction_id, created_timestamp, epistemic_source{desc_col} "
        f"FROM decisions WHERE project_id = ? "
        f"ORDER BY created_timestamp DESC LIMIT ?",
        (project_id, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "decision",
            "title": (r[1] or "")[:100],
            "choice": r[1],
            "rationale": r[2],
            "alternatives": r[3],
            "confidence_at_decision": r[4],
            "reversibility": r[5],
            "outcome": r[6],
            "regret_score": r[7],
            "session_id": r[8],
            "goal_id": r[9],
            "transaction_id": r[10],
            "created_at": _to_iso(r[11]),
            "epistemic_source": r[12],
            "description": (r[13] if has_desc and len(r) > 13 else None),
        }
        for r in rows
    ]


def _list_sources(db, project_id: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, title, source_url, source_type, description, confidence, "
        "epistemic_layer, session_id, discovered_by_ai, discovered_at "
        "FROM epistemic_sources WHERE project_id = ? "
        "ORDER BY discovered_at DESC LIMIT ?",
        (project_id, limit),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "type": "source",
            "title": r[1],
            "url": r[2],
            "source_type": r[3],
            "description": r[4],
            "confidence": r[5],
            "epistemic_layer": r[6],
            "session_id": r[7],
            "discovered_by_ai": r[8],
            "created_at": _to_iso(r[9]),
        }
        for r in rows
    ]


def _list_goals(db, project_id: str, status: str, limit: int) -> list[dict[str, Any]]:
    cursor = db.conn.cursor()
    where = "project_id = ?"
    params: list[Any] = [project_id]
    if status == "active":
        where += " AND is_completed = 0"
    elif status == "completed":
        where += " AND is_completed = 1"
    elif status == "planned":
        where += " AND status = 'planned'"
    has_desc = _table_has_column(db, "goals", "description")
    desc_col = ", description" if has_desc else ""
    cursor.execute(
        f"SELECT id, objective, status, is_completed, goal_data, "
        f"session_id, transaction_id, created_timestamp, completed_timestamp{desc_col} "
        f"FROM goals WHERE {where} "
        f"ORDER BY created_timestamp DESC LIMIT ?",
        (*params, limit),
    )
    rows = cursor.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        gd = _parse_data_json(r[4])
        out.append({
            "id": r[0],
            "type": "goal",
            "objective": r[1],
            "description": (r[9] if has_desc and len(r) > 9 else None),
            "status": r[2],
            "is_completed": bool(r[3]),
            "tasks": gd.get("subtasks", []),
            "session_id": r[5],
            "transaction_id": r[6],
            "created_at": _to_iso(r[7]),
            "completed_at": _to_iso(r[8]),
            "data": gd,
        })
    return out


# ── Endpoint handlers ────────────────────────────────────────────────


@router.get("/findings")
async def list_findings(
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None, description="Registered project_id; falls back to daemon's CWD-bound project if omitted"),
    path: str | None = Query(None, description="Filesystem path to project (power-user bypass; .empirica/project.yaml must exist)"),
):
    """List recent findings. Scope: `?project_id=X` (registry), `?path=Y`
    (bypass), or daemon's CWD-bound project (default)."""
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"findings": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_findings(db, proj_pid, limit)
        rows = _attach_related_to(db, rows)
        return {"findings": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/unknowns")
async def list_unknowns(
    status: str = Query("open", pattern="^(open|resolved|all)$"),
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"unknowns": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_unknowns(db, proj_pid, status, limit)
        rows = _attach_related_to(db, rows)
        return {"unknowns": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/dead-ends")
async def list_dead_ends(
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"dead_ends": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_dead_ends(db, proj_pid, limit)
        rows = _attach_related_to(db, rows)
        return {"dead_ends": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/mistakes")
async def list_mistakes(
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"mistakes": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_mistakes(db, proj_pid, limit)
        rows = _attach_related_to(db, rows)
        return {"mistakes": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/assumptions")
async def list_assumptions(
    confidence_min: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"assumptions": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_assumptions(db, proj_pid, confidence_min, limit)
        rows = _attach_related_to(db, rows)
        return {"assumptions": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/decisions")
async def list_decisions(
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"decisions": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_decisions(db, proj_pid, limit)
        rows = _attach_related_to(db, rows)
        return {"decisions": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/sources")
async def list_sources(
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"sources": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_sources(db, proj_pid, limit)
        rows = _attach_related_to(db, rows)
        return {"sources": rows, "project_id": proj_pid}
    finally:
        db.close()


@router.get("/sources/{source_id}/content")
async def get_source_content(
    source_id: str,
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    """Return the content for a source row, or signal that the client should
    fetch the URL directly.

    Response shapes:
      - URL source (http/https) → ``{"source_id": ..., "kind": "url", "url": ...}``
        Client fetches the URL directly (avoids CORS-on-localhost-proxy
        complexity for the simple case).
      - Local file source → ``{"source_id": ..., "kind": "file", "path": ...,
        "content": <text>, "size_bytes": N, "encoding": "utf-8"}``.
      - 404 — source_id not found in ``epistemic_sources`` for the resolved
        project, OR a file source whose path does not exist on disk.
      - 422 — path source resolves outside the project tree (defense in
        depth against ``../`` traversal).

    Closes the extension blocker reported in prop_fzb63fnlx5 + prop_bcsecxo2rr:
    daemon previously served only metadata, so the extension's source viewer
    rendered empty. With this endpoint a viewer can branch on ``kind`` and
    render inline or hand the URL to the browser.
    """
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        raise HTTPException(
            status_code=503,
            detail="Daemon has no active project. Pass ?project_id= or ?path=.",
        )

    db = _open_db_for(project)
    try:
        row = _fetch_source_row(db, proj_pid, source_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"source_id {source_id!r} not found in project {proj_pid!r}",
            )

        url = row["source_url"] or ""
        if _looks_like_url(url):
            return {
                "source_id": source_id,
                "kind": "url",
                "url": url,
                "title": row["title"],
                "source_type": row["source_type"],
            }

        return _resolve_file_source(
            source_id, row, project["project_path"],
        )
    finally:
        db.close()


def _fetch_source_row(db, project_id: str, source_id: str) -> dict | None:
    """Read one ``epistemic_sources`` row by id, scoped to project."""
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, title, source_url, source_type, description "
        "FROM epistemic_sources WHERE project_id = ? AND id = ? LIMIT 1",
        (project_id, source_id),
    )
    r = cursor.fetchone()
    if not r:
        return None
    return {
        "id": r[0], "title": r[1], "source_url": r[2],
        "source_type": r[3], "description": r[4],
    }


def _looks_like_url(value: str) -> bool:
    """Detect URL sources for the kind branch."""
    return value.startswith(("http://", "https://"))


# Conventional locations a source path may resolve against, relative to
# the project root. First match wins. Order matters: explicit sources
# dir first, then docs, then the project root itself for backwards-
# compatible "path was stored relative to the project" rows.
_SOURCE_PATH_PREFIXES = ("", ".empirica/sources", "docs", "docs/sources")

# Cap bytes returned — file sources are read into memory + JSON-encoded
# for the response. 10MB is generous for a viewer endpoint without
# being a DOS vector. Larger files return a truncation marker so the
# viewer can fall back to "open in editor" UX.
_MAX_SOURCE_CONTENT_BYTES = 10 * 1024 * 1024


def _resolve_file_source(
    source_id: str, row: dict, project_path: str,
) -> dict:
    """Walk the fallback paths for a non-URL source and return content + meta.

    Raises HTTPException(404) when no candidate resolves, and
    HTTPException(422) when a candidate escapes the project tree.
    """
    from pathlib import Path

    raw = row["source_url"] or ""
    root = Path(project_path).resolve()
    resolved: Path | None = None
    candidates: list[Path] = []
    for prefix in _SOURCE_PATH_PREFIXES:
        if not raw:
            continue
        candidate = (root / prefix / raw) if prefix else (root / raw)
        candidates.append(candidate)
        try:
            candidate_resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        # Defense in depth — refuse traversal even if the file exists.
        try:
            candidate_resolved.relative_to(root)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"source path {raw!r} resolves outside project root "
                    f"{str(root)!r} — refusing to serve"
                ),
            ) from None
        if candidate_resolved.is_file():
            resolved = candidate_resolved
            break

    # Also accept an absolute path that lands inside the project tree.
    if resolved is None and raw.startswith("/"):
        try:
            abs_resolved = Path(raw).resolve()
            abs_resolved.relative_to(root)
            if abs_resolved.is_file():
                resolved = abs_resolved
        except (OSError, RuntimeError, ValueError):
            resolved = None

    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"file source {raw!r} not found on disk for project "
                f"{str(root)!r}; tried prefixes {_SOURCE_PATH_PREFIXES!r}"
            ),
        )

    size = resolved.stat().st_size
    if size > _MAX_SOURCE_CONTENT_BYTES:
        return {
            "source_id": source_id,
            "kind": "file",
            "path": str(resolved.relative_to(root)),
            "size_bytes": size,
            "truncated": True,
            "content": None,
            "title": row["title"],
            "source_type": row["source_type"],
            "hint": (
                f"file exceeds {_MAX_SOURCE_CONTENT_BYTES} byte cap; "
                "open it directly in an editor"
            ),
        }

    try:
        content = resolved.read_text(encoding="utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        # Binary or unknown encoding — return base64 so the viewer can
        # branch on encoding to render or download.
        import base64
        content = base64.b64encode(resolved.read_bytes()).decode("ascii")
        encoding = "base64"

    return {
        "source_id": source_id,
        "kind": "file",
        "path": str(resolved.relative_to(root)),
        "size_bytes": size,
        "encoding": encoding,
        "content": content,
        "title": row["title"],
        "source_type": row["source_type"],
    }


@router.get("/goals")
async def list_goals(
    status: str = Query("active", pattern="^(active|completed|planned|all)$"),
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"goals": [], "project_id": None}
    db = _open_db_for(project)
    try:
        rows = _list_goals(db, proj_pid, status, limit)
        rows = _attach_related_to(db, rows)
        return {"goals": rows, "project_id": proj_pid}
    finally:
        db.close()


# ── Polymorphic ID lookup (shared by T3 single CRUD + T4 graph) ──────


# (artifact_type, table, id_col) — the polymorphic lookup map. Mirrors
# graph_commands._ARTIFACT_TABLES but adds source for completeness.
_TYPE_TABLE_MAP: list[tuple[str, str, str]] = [
    ("finding", "project_findings", "id"),
    ("unknown", "project_unknowns", "id"),
    ("dead_end", "project_dead_ends", "id"),
    ("mistake", "mistakes_made", "id"),
    ("assumption", "assumptions", "id"),
    ("decision", "decisions", "id"),
    ("source", "epistemic_sources", "id"),
    ("goal", "goals", "id"),
]


def _resolve_artifact_by_id(db, artifact_id: str) -> tuple[str, str, str] | None:
    """Polymorphic lookup: find which table contains `artifact_id`.

    Returns (artifact_type, table, id_col) or None if not found in any table.
    """
    cursor = db.conn.cursor()
    for artifact_type, table, id_col in _TYPE_TABLE_MAP:
        try:
            cursor.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ? LIMIT 1", (artifact_id,))
            if cursor.fetchone():
                return (artifact_type, table, id_col)
        except Exception as e:
            logger.debug(f"_resolve_artifact_by_id: lookup on {table} failed: {e}")
    return None


# ── Graph endpoint (T4) — defined BEFORE /artifacts/{id} so FastAPI ──
# ── matches the static "/graph" path before the dynamic capture. ─────


def _walk_graph(  # noqa: C901 — graph walker has multiple branches but reads linearly
    db, seed_id: str | None, session_id: str | None,
    depth: int, max_nodes: int, type_filter: set[str] | None,
    project_id_for_seed: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """BFS over artifact_edges (bidirectional). Returns (nodes, edges).

    project_id_for_seed: used only when neither seed_id nor session_id is
    provided — scopes the project-wide seed scan to a specific project
    (passed by the route handler after resolving ?project_id= / ?path=).
    Falls back to cached daemon project for legacy callers.
    """
    cursor = db.conn.cursor()

    seeds: set[str] = set()
    if seed_id:
        seeds.add(seed_id)
    elif session_id:
        for _type, table, id_col in _TYPE_TABLE_MAP:
            try:
                cursor.execute(f"SELECT {id_col} FROM {table} WHERE session_id = ? LIMIT ?",
                               (session_id, max_nodes))
                for row in cursor.fetchall():
                    seeds.add(row[0])
                    if len(seeds) >= max_nodes:
                        break
            except Exception:
                continue
            if len(seeds) >= max_nodes:
                break
    else:
        project_id = project_id_for_seed
        if project_id is None:
            project = get_cached_daemon_project() or {}
            project_id = project.get("project_id")
        if project_id:
            for _type, table, id_col in _TYPE_TABLE_MAP:
                try:
                    cursor.execute(
                        f"SELECT {id_col} FROM {table} WHERE project_id = ? "
                        f"ORDER BY created_timestamp DESC LIMIT ?",
                        (project_id, max_nodes),
                    )
                    for row in cursor.fetchall():
                        seeds.add(row[0])
                        if len(seeds) >= max_nodes:
                            break
                except Exception:
                    continue
                if len(seeds) >= max_nodes:
                    break

    if not seeds:
        return ([], [])

    visited: set[str] = set(seeds)
    edges_collected: set[tuple[str, str, str]] = set()

    # Pre-edges-schema project DBs lack artifact_edges. Skip the walk in
    # that case — visited stays at seeds, edges_collected stays empty,
    # node resolution below still produces {id, type, title} dicts so the
    # response shape matches. Cheaper than wrapping every cursor.execute
    # in try/except inside the walk loop. Same defensive pattern used in
    # _attach_related_to.
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_edges'"
    )
    edges_table_exists = cursor.fetchone() is not None

    if edges_table_exists:
        frontier = set(seeds)
        for _ in range(depth):
            if not frontier or len(visited) >= max_nodes:
                break
            ph = ",".join("?" * len(frontier))
            cursor.execute(
                f"SELECT from_id, to_id, relation FROM artifact_edges WHERE from_id IN ({ph})",
                list(frontier),
            )
            new_frontier: set[str] = set()
            for from_id, to_id, relation in cursor.fetchall():
                edges_collected.add((from_id, to_id, relation))
                if to_id not in visited and len(visited) < max_nodes:
                    visited.add(to_id)
                    new_frontier.add(to_id)
            cursor.execute(
                f"SELECT from_id, to_id, relation FROM artifact_edges WHERE to_id IN ({ph})",
                list(frontier),
            )
            for from_id, to_id, relation in cursor.fetchall():
                edges_collected.add((from_id, to_id, relation))
                if from_id not in visited and len(visited) < max_nodes:
                    visited.add(from_id)
                    new_frontier.add(from_id)
            frontier = new_frontier

    nodes_out: list[dict] = []
    title_col_for: dict[str, str] = {
        "finding": "finding", "unknown": "unknown",
        "dead_end": "approach", "mistake": "mistake",
        "assumption": "assumption", "decision": "choice",
        "source": "title", "goal": "objective",
    }
    for artifact_id in visited:
        for artifact_type, table, id_col in _TYPE_TABLE_MAP:
            if type_filter and artifact_type not in type_filter:
                continue
            try:
                title_col = title_col_for[artifact_type]
                cursor.execute(
                    f"SELECT {title_col} FROM {table} WHERE {id_col} = ? LIMIT 1",
                    (artifact_id,),
                )
                row = cursor.fetchone()
                if row:
                    title = (row[0] or "")[:100] if row[0] else ""
                    nodes_out.append({"id": artifact_id, "type": artifact_type, "title": title})
                    break
            except Exception:
                continue

    node_ids_kept = {n["id"] for n in nodes_out}
    edges_out = [
        {"from": f, "to": t, "relation": r}
        for (f, t, r) in edges_collected
        if f in node_ids_kept and t in node_ids_kept
    ]
    return (nodes_out, edges_out)


# ── Bootstrap aggregator (v0.6 spec) ─────────────────────────────────


@router.get("/bootstrap")
async def get_bootstrap(
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    """Three-circle artifact graph bootstrap context. Scope: `?project_id=X`,
    `?path=Y`, or daemon's CWD-bound project (default).
    """
    from empirica.core.bootstrap import build_bootstrap_payload

    project = _resolve_project_dict(project_id, path)
    payload = build_bootstrap_payload(
        project_path=project["project_path"],
        project_id=project.get("project_id"),
    )
    return payload


@router.get("/artifacts/graph")
async def get_artifact_graph(
    seed_id: str | None = Query(None),
    session_id: str | None = Query(None),
    depth: int = Query(2, ge=0, le=10),
    types: str | None = Query(None),
    max_nodes: int = Query(500, ge=1, le=2000),
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    """Connected component as nodes + edges. Scope: `?project_id=X`, `?path=Y`,
    or daemon's CWD-bound project (default).

    seed_id: BFS from one artifact (depth N).
    session_id: graph of artifacts created in that session.
    Neither: project-wide graph (capped at max_nodes, default 500).
    types: comma-separated type filter (finding,decision,...).
    """
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    if not proj_pid:
        return {"nodes": [], "edges": [], "project_id": None}

    type_filter: set[str] | None = None
    if types:
        type_filter = {t.strip() for t in types.split(",") if t.strip()}

    db = _open_db_for(project)
    try:
        nodes, edges = _walk_graph(
            db, seed_id, session_id, depth, max_nodes, type_filter,
            project_id_for_seed=proj_pid,
        )
        return {"nodes": nodes, "edges": edges, "project_id": proj_pid}
    finally:
        db.close()


# ── Single-artifact CRUD (T3) — registered AFTER /artifacts/graph ────


def _list_one_by_type(db, artifact_type: str, artifact_id: str, project_id: str | None = None) -> dict | None:
    """Return the single-row dict for an artifact, using the per-type list helpers.

    `project_id` should be passed by the route handler (resolved from
    ?project_id= / ?path= / cached project). Falls back to cached daemon
    project for legacy callers.
    """
    if project_id is None:
        project = get_cached_daemon_project() or {}
        project_id = project.get("project_id")
    if not project_id:
        return None
    if artifact_type == "finding":
        rows = _list_findings(db, project_id, limit=1000)
    elif artifact_type == "unknown":
        rows = _list_unknowns(db, project_id, "all", limit=1000)
    elif artifact_type == "dead_end":
        rows = _list_dead_ends(db, project_id, limit=1000)
    elif artifact_type == "mistake":
        rows = _list_mistakes(db, project_id, limit=1000)
    elif artifact_type == "assumption":
        rows = _list_assumptions(db, project_id, confidence_min=0.0, limit=1000)
    elif artifact_type == "decision":
        rows = _list_decisions(db, project_id, limit=1000)
    elif artifact_type == "source":
        rows = _list_sources(db, project_id, limit=1000)
    elif artifact_type == "goal":
        rows = _list_goals(db, project_id, "all", limit=1000)
    else:
        return None
    for row in rows:
        if row.get("id") == artifact_id:
            return row
    return None


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    project_id: str | None = Query(None),
    path: str | None = Query(None),
):
    """Fetch one artifact + all its edges (full neighborhood). Scope:
    `?project_id=X`, `?path=Y`, or daemon's CWD-bound project (default).

    Type is inferred polymorphically by scanning artifact tables.
    Returns 404 if the id doesn't exist in any table.
    """
    project = _resolve_project_dict(project_id, path)
    proj_pid = project.get("project_id")
    db = _open_db_for(project)
    try:
        resolved = _resolve_artifact_by_id(db, artifact_id)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
        artifact_type, _table, _id_col = resolved
        row = _list_one_by_type(db, artifact_type, artifact_id, project_id=proj_pid)
        if not row:
            raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not in active project")
        rows = _attach_related_to(db, [row])
        return {"artifact": rows[0]}
    finally:
        db.close()


@router.patch("/artifacts/{artifact_id}/resolve")
async def resolve_artifact(artifact_id: str, body: dict):
    """Mark an artifact as resolved.

    Per-type semantics (matches `empirica resolve-artifacts` CLI):
    - unknown:    is_resolved = 1, resolved_by, resolved_timestamp
    - assumption: status = 'verified'
    - goal:       is_completed = 1, status = 'completed', completed_timestamp

    For other types (finding/decision/dead_end/mistake/source) — 422 (no resolve semantics).
    """
    import time

    resolved_by = body.get("resolved_by", "")
    project = get_cached_daemon_project()
    if not project:
        raise HTTPException(status_code=503, detail="Daemon not bound to a project")

    db = _open_db()
    try:
        resolved = _resolve_artifact_by_id(db, artifact_id)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
        artifact_type, _table, _id_col = resolved
        cursor = db.conn.cursor()
        now = time.time()

        if artifact_type == "unknown":
            cursor.execute(
                "UPDATE project_unknowns SET is_resolved = 1, resolved_by = ?, "
                "resolved_timestamp = ? WHERE id = ?",
                (resolved_by, now, artifact_id),
            )
        elif artifact_type == "assumption":
            cursor.execute(
                "UPDATE assumptions SET status = 'verified', resolved_timestamp = ? WHERE id = ?",
                (now, artifact_id),
            )
        elif artifact_type == "goal":
            cursor.execute(
                "UPDATE goals SET is_completed = 1, status = 'completed', "
                "completed_timestamp = ? WHERE id = ?",
                (now, artifact_id),
            )
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Artifact type '{artifact_type}' has no resolve semantics",
            )

        db.conn.commit()
        return {"ok": True, "type": artifact_type, "id": artifact_id, "action": "resolved"}
    finally:
        db.close()


# Whitelisted PATCH fields per artifact type. Anything outside this map is
# silently dropped — defensive, prevents accidental schema mutation from the API.
_PATCH_WHITELIST: dict[str, set[str]] = {
    "finding": {"impact", "subject", "epistemic_source"},
    "unknown": {"impact", "subject", "epistemic_source"},
    "dead_end": {"impact", "subject", "epistemic_source"},
    "mistake": {"prevention", "epistemic_source"},
    "assumption": {"confidence", "status", "epistemic_source"},
    "decision": {"outcome", "regret_score", "epistemic_source"},
    "source": {"confidence", "description"},
    "goal": {"objective", "status"},
}


@router.patch("/artifacts/{artifact_id}")
async def patch_artifact(artifact_id: str, body: dict):
    """Partial update on an artifact. Only whitelisted fields per type.

    Body shape: {"<field>": <value>, ...}. Unknown fields are silently dropped.
    Returns 200 with {ok, type, id, updated_fields} or 404 / 422 / 503.
    """
    project = get_cached_daemon_project()
    if not project:
        raise HTTPException(status_code=503, detail="Daemon not bound to a project")

    db = _open_db()
    try:
        resolved = _resolve_artifact_by_id(db, artifact_id)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
        artifact_type, table, id_col = resolved

        whitelist = _PATCH_WHITELIST.get(artifact_type, set())
        updates = {k: v for k, v in body.items() if k in whitelist}
        if not updates:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No whitelisted fields in body. Allowed for {artifact_type}: "
                    f"{sorted(whitelist)}"
                ),
            )

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [artifact_id]
        cursor = db.conn.cursor()
        cursor.execute(f"UPDATE {table} SET {set_clause} WHERE {id_col} = ?", params)
        db.conn.commit()

        return {
            "ok": True,
            "type": artifact_type,
            "id": artifact_id,
            "updated_fields": sorted(updates.keys()),
        }
    finally:
        db.close()


# ── Batch endpoints (T4) ─────────────────────────────────────────────


@router.post("/artifacts/log")
async def post_artifacts_log(body: dict):
    """Batch log a graph (nodes + edges). Proxies to log_artifacts_graph().

    Body shape matches `empirica log-artifacts` CLI: {nodes: [...], edges: [...]}.
    """
    project = get_cached_daemon_project()
    if not project:
        raise HTTPException(status_code=503, detail="Daemon not bound to a project")

    from empirica.cli.command_handlers.graph_commands import log_artifacts_graph
    result = log_artifacts_graph(
        body,
        project_id=project.get("project_id"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "log failed"))
    return result


@router.post("/artifacts/resolve")
async def post_artifacts_resolve(body: dict):
    """Batch resolve. Body: {ids: [...], resolved_by?: "..."} or {items: [{id, type}, ...]}."""
    import time

    project = get_cached_daemon_project()
    if not project:
        raise HTTPException(status_code=503, detail="Daemon not bound to a project")

    ids = body.get("ids") or [item.get("id") for item in body.get("items", []) if item.get("id")]
    if not ids:
        raise HTTPException(status_code=422, detail="Body must include 'ids' or 'items'")
    resolved_by = body.get("resolved_by", "")

    db = _open_db()
    try:
        cursor = db.conn.cursor()
        now = time.time()
        results: list[dict] = []
        for art_id in ids:
            resolved = _resolve_artifact_by_id(db, art_id)
            if not resolved:
                results.append({"id": art_id, "outcome": "not_found"})
                continue
            artifact_type, _table, _id_col = resolved
            if artifact_type == "unknown":
                cursor.execute(
                    "UPDATE project_unknowns SET is_resolved = 1, resolved_by = ?, "
                    "resolved_timestamp = ? WHERE id = ?",
                    (resolved_by, now, art_id),
                )
                results.append({"id": art_id, "type": artifact_type, "outcome": "resolved"})
            elif artifact_type == "assumption":
                cursor.execute(
                    "UPDATE assumptions SET status = 'verified', resolved_timestamp = ? WHERE id = ?",
                    (now, art_id),
                )
                results.append({"id": art_id, "type": artifact_type, "outcome": "resolved"})
            elif artifact_type == "goal":
                cursor.execute(
                    "UPDATE goals SET is_completed = 1, status = 'completed', "
                    "completed_timestamp = ? WHERE id = ?",
                    (now, art_id),
                )
                results.append({"id": art_id, "type": artifact_type, "outcome": "resolved"})
            else:
                results.append({
                    "id": art_id, "type": artifact_type, "outcome": "skipped",
                    "reason": "no resolve semantics",
                })
        db.conn.commit()
        return {
            "ok": True,
            "resolved": sum(1 for r in results if r["outcome"] == "resolved"),
            "skipped": sum(1 for r in results if r["outcome"] == "skipped"),
            "not_found": sum(1 for r in results if r["outcome"] == "not_found"),
            "results": results,
        }
    finally:
        db.close()


@router.post("/artifacts/delete")
async def post_artifacts_delete(body: dict):
    """Batch delete. Body: {ids: [...]} or {items: [{id, type}, ...]}.

    Each delete fans out to all three storage layers via _delete_single_artifact.
    """
    project = get_cached_daemon_project()
    if not project:
        raise HTTPException(status_code=503, detail="Daemon not bound to a project")
    project_id = project.get("project_id")
    project_path = project.get("project_path")

    raw_items = body.get("items")
    if raw_items is None and body.get("ids"):
        raw_items = [{"id": art_id} for art_id in body["ids"]]
    if not raw_items:
        raise HTTPException(status_code=422, detail="Body must include 'ids' or 'items'")

    db = _open_db()
    try:
        from empirica.cli.command_handlers.graph_commands import _delete_single_artifact
        cursor = db.conn.cursor()
        results: list[dict] = []
        for raw in raw_items:
            art_id = raw.get("id")
            if not art_id:
                results.append({"outcome": "missing_id"})
                continue
            # Polymorphic resolve if type not provided
            if not raw.get("type"):
                resolved = _resolve_artifact_by_id(db, art_id)
                if not resolved:
                    results.append({"id": art_id, "outcome": "not_found"})
                    continue
                raw = {**raw, "type": resolved[0]}
            result = _delete_single_artifact(
                cursor, raw, project_id=project_id, dry_run=False, project_path=project_path,
            )
            if result is None:
                results.append({"id": art_id, "outcome": "skipped"})
            elif result.get("error"):
                results.append({"id": art_id, "outcome": "failed", "reason": result["error"]})
            else:
                results.append({**result, "outcome": "deleted"})
        db.conn.commit()
        return {
            "ok": True,
            "deleted": sum(1 for r in results if r.get("outcome") == "deleted"),
            "not_found": sum(1 for r in results if r.get("outcome") == "not_found"),
            "failed": sum(1 for r in results if r.get("outcome") == "failed"),
            "results": results,
        }
    finally:
        db.close()


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str):
    """Delete an artifact across all three storage layers.

    sqlite (row + dangling edges) + Qdrant (vector point) + git notes
    (refs/notes/empirica/{type}/{id}). Closes the documented delete-git-notes
    gap from `empirica delete-artifacts` CLI.
    """
    project = get_cached_daemon_project()
    if not project:
        raise HTTPException(status_code=503, detail="Daemon not bound to a project")
    project_id = project.get("project_id")
    project_path = project.get("project_path")

    db = _open_db()
    try:
        resolved = _resolve_artifact_by_id(db, artifact_id)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
        artifact_type, _table, _id_col = resolved

        # Reuse the existing _delete_single_artifact (now extended with git-notes cleanup).
        from empirica.cli.command_handlers.graph_commands import _delete_single_artifact

        cursor = db.conn.cursor()
        result = _delete_single_artifact(
            cursor,
            {"type": artifact_type, "id": artifact_id},
            project_id=project_id,
            dry_run=False,
            project_path=project_path,
        )
        db.conn.commit()

        if result is None or result.get("error"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "delete failed") if result else "delete failed",
            )

        return {
            "ok": True,
            "type": result["type"],
            "id": result["id"],
            "action": result["action"],
            "edges_removed": result.get("edges_removed", 0),
            "git_notes_cleaned": result.get("git_notes_cleaned", False),
        }
    finally:
        db.close()
