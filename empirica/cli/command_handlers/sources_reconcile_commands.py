"""`empirica sources-reconcile` — adopt catalogue uuids for matched local sources.

Empirica slice of the unified source-identity model: a source has ONE
uuid shared with the central catalogue. Local rows minted before the
shared-identity era (or on a second device) carry their own uuid4; this
verb matches them against the catalogue by content identity and PK-swaps
the local row to the catalogue uuid so daemon content reads resolve by
the shared id.

Four phases:

  1. **Backfill** — local rows missing content identity get it computed
     (file-backed rows only; lazy half of migration 050). Runs even in
     dry-run: identity columns are additive metadata, not the swap.
  2. **Discovery** — catalogue candidates looked up by content_hash via
     ``GET /v1/sources/catalogue``. This endpoint is NOT yet part of the
     pinned cross-component contract — discovery degrades gracefully
     (reports rows-ready-for-matching) until the catalogue side deploys.
  3. **Confirm** — proposed {local_uuid, cortex_uuid} pairs POSTed to
     ``/v1/sources/reconcile`` (pinned contract). The catalogue validates
     hash + tenancy; rejections come back typed (cortex_uuid_not_found →
     re-register as fresh; hash_mismatch → divergent fork, no swap).
  4. **Adopt** (``--apply``) — per confirmed pair. The DEFAULT is a
     non-destructive ALIAS: the local row keeps its PK and stores the
     catalogue uuid in ``cortex_uuid`` (the daemon resolves ``id OR
     cortex_uuid``, so both address the same source — no cascade, no
     Qdrant change, offline-safe). ``--converge`` instead PK-SWAPS in one
     SQLite transaction — epistemic_sources PK, artifact_edges
     from_id/to_id, archive_target_id supersession pointers,
     project_findings.source_refs JSON arrays, plus best-effort
     workspace-DB entity_artifacts. Qdrant points are NOT re-pointed on
     swap — ``empirica rebuild`` regenerates them from SQLite.

Dry-run by default; ``--apply`` adopts (alias), ``--apply --converge`` swaps.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CATALOGUE_LOOKUP_PATH = "/v1/sources/catalogue"
RECONCILE_PATH = "/v1/sources/reconcile"

# P2 sync-when-small: only bodies at/below this size are pushed to cortex.
# Threshold is empirica-owned tenant policy (cortex hard-caps at 100MB);
# override per-tenant via the EMPIRICA_SMALL_BODY_THRESHOLD env var.
_SMALL_BODY_THRESHOLD = int(os.environ.get("EMPIRICA_SMALL_BODY_THRESHOLD", 1024 * 1024))  # 1 MiB


def _push_source_body_to_cortex(cortex_url, api_key, cortex_uuid: str, content: bytes, mime_type: str | None) -> dict:
    """Best-effort POST /v1/sources/{id}/body — upload a small source body so a
    remote peer can fetch it (P2 sync-when-small). Idempotent cortex-side
    (dedupe on body_hash); server verifies its own SHA-256. Never raises."""
    req = urllib.request.Request(
        f"{cortex_url}/v1/sources/{cortex_uuid}/body",
        data=content,
        method="POST",
        headers={
            "Content-Type": mime_type or "application/octet-stream",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            return {"pushed": True, "status": resp.status, "size_bytes": len(content)}
    except urllib.error.HTTPError as e:
        return {"pushed": False, "status": e.code, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"pushed": False, "error": f"{type(e).__name__}: {e}"}


def _maybe_push_small_body(cortex_url, api_key, cortex_uuid: str, row: dict | None) -> dict | None:
    """Upload a source's body to cortex IF it is small (<= threshold) and has
    readable local content. Returns a per-source status dict, or None when
    skipped (too large / no size / no local path / unreadable). Best-effort."""
    if not row or not cortex_url or not api_key:
        return None
    size = row.get("size_bytes")
    path = row.get("canonical_path")
    if size is None or size > _SMALL_BODY_THRESHOLD or not path:
        return None
    try:
        p = Path(str(path).replace("file://", ""))
        if not p.is_file():
            return None
        content = p.read_bytes()
    except OSError:
        return None
    result = _push_source_body_to_cortex(cortex_url, api_key, cortex_uuid, content, row.get("mime_type"))
    result["cortex_uuid"] = cortex_uuid
    return result


def _run_register_shared_backfill(args, project_id, output) -> int:
    """One-time convergence: push existing local-only shared/public sources up to
    cortex's catalogue (POST /v1/sources/register) + stamp cortex_uuid.

    For sources logged before `source-add` started auto-registering shared
    sources — materializes them on the shared surface the extension + sources-map
    read. Skips already-registered (cortex_uuid IS NOT NULL) rows.
    """
    from empirica.cli.command_handlers.artifact_log_commands import _register_source_in_cortex
    from empirica.cli.command_handlers.projects_commands import _resolve_cortex_config
    from empirica.data.session_database import SessionDatabase

    cortex_url, api_key = _resolve_cortex_config(args)
    if not (cortex_url and api_key):
        _emit(output, {"ok": False, "error": "cortex not configured (set cortex.url + cortex.api_key)"})
        return 1

    db = SessionDatabase()
    try:
        cols = ("id", "title", "source_type", "visibility", "content_hash", "size_bytes", "canonical_path", "mime_type")
        rows = db.conn.execute(
            f"SELECT {', '.join(cols)} FROM epistemic_sources "
            "WHERE project_id = ? AND COALESCE(archived, 0) = 0 "
            "AND visibility IN ('shared', 'public') AND cortex_uuid IS NULL",
            (project_id,),
        ).fetchall()
        registered, failed = 0, []
        for raw in rows:
            r = dict(raw) if hasattr(raw, "keys") else dict(zip(cols, raw, strict=False))
            identity = {k: r.get(k) for k in ("content_hash", "size_bytes", "canonical_path", "mime_type")}
            res = _register_source_in_cortex(
                cortex_url, api_key, r["id"], project_id, r["title"], r["source_type"], r["visibility"], identity
            )
            if res.get("registered"):
                db.conn.execute("UPDATE epistemic_sources SET cortex_uuid = ? WHERE id = ?", (r["id"], r["id"]))
                # Commit per-source: the backfill is a long network loop that can
                # be interrupted/reaped; a single end-of-loop commit would lose
                # ALL progress on interruption. Per-source persist makes it
                # resumable (re-run skips cortex_uuid IS NOT NULL rows).
                db.conn.commit()
                registered += 1
            else:
                failed.append({"id": str(r["id"])[:8], "error": res.get("error")})
        _emit(output, {"ok": True, "candidates": len(rows), "registered": registered, "failed": failed})
        return 0 if not failed else 2
    finally:
        db.close()


def handle_sources_reconcile_command(args) -> int:
    from empirica.cli.command_handlers.projects_commands import (
        _resolve_cortex_config,
    )
    from empirica.data.session_database import SessionDatabase

    output = getattr(args, "output", "human")
    apply = bool(getattr(args, "apply", False))
    converge = bool(getattr(args, "converge", False))
    push_bodies = bool(getattr(args, "push_bodies", False))

    project_id = getattr(args, "project_id", None)
    if not project_id:
        project_id = _resolve_active_project_id()
    if not project_id:
        _emit(
            output,
            {
                "ok": False,
                "error": "Could not resolve project_id",
                "hint": "Pass --project-id or run inside an active project",
            },
        )
        return 1

    if getattr(args, "register_shared", False):
        return _run_register_shared_backfill(args, project_id, output)

    db = SessionDatabase()
    try:
        rows = _load_local_sources(db, project_id)
        backfilled = _backfill_identity(db, rows)

        cortex_url, api_key = _resolve_cortex_config(args)
        candidates, discovery_status = _discover_candidates(
            cortex_url,
            api_key,
            rows,
        )

        proposed = _propose_matches(rows, candidates)
        confirmed: list[dict] = []
        rejected: list[dict] = []
        if proposed:
            confirmed, rejected, confirm_status = _confirm_matches(
                cortex_url,
                api_key,
                proposed,
            )
        else:
            confirm_status = "skipped_no_matches"

        swapped: list[dict] = []
        aliased: list[dict] = []
        bodies_pushed: list[dict] = []
        by_id = {r["id"]: r for r in rows}
        if apply and confirmed:
            for pair in confirmed:
                if converge:
                    # Opt-in: destructive one-uuid PK-swap + cascade.
                    swapped.append(_swap_source_id(db, project_id, pair["local_uuid"], pair["cortex_uuid"]))
                else:
                    # Default: non-destructive alias adopt (daemon resolves id OR cortex_uuid).
                    aliased.append(_set_cortex_uuid_alias(db, project_id, pair["local_uuid"], pair["cortex_uuid"]))
                # P2 sync-when-small: push the body for small sources so remote peers can fetch it.
                if push_bodies:
                    pushed = _maybe_push_small_body(
                        cortex_url, api_key, pair["cortex_uuid"], by_id.get(pair["local_uuid"])
                    )
                    if pushed is not None:
                        bodies_pushed.append(pushed)

        payload = {
            "ok": True,
            "dry_run": not apply,
            "mode": "converge" if converge else "alias",
            "project_id": project_id,
            "local_sources": len(rows),
            "backfilled_identity": backfilled,
            "discovery": discovery_status,
            "candidates": len(candidates),
            "proposed": len(proposed),
            "confirm": confirm_status,
            "confirmed": confirmed,
            "rejected": rejected,
            "swapped": swapped,
            "aliased": aliased,
            "bodies_pushed": bodies_pushed,
        }
        _emit(output, payload, _render_human(payload))
        return 0
    finally:
        db.close()


def _resolve_active_project_id() -> str | None:
    try:
        from empirica.data.session_database import SessionDatabase
        from empirica.utils.session_resolver import InstanceResolver as R

        session_id = R.session_id()
        if not session_id:
            return None
        db = SessionDatabase()
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT project_id FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            return (row["project_id"] if isinstance(row, sqlite3.Row) else row[0]) if row else None
        finally:
            db.close()
    except Exception:
        return None


def _load_local_sources(db, project_id: str) -> list[dict]:
    """Non-archived rows with everything the matcher needs."""
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, title, source_url, content_hash, size_bytes, "
        "canonical_path, mime_type, source_metadata "
        "FROM epistemic_sources "
        "WHERE project_id = ? AND COALESCE(archived, 0) = 0",
        (project_id,),
    )
    rows = []
    for r in cursor.fetchall():
        rows.append(
            {
                "id": r[0],
                "title": r[1],
                "source_url": r[2],
                "content_hash": r[3],
                "size_bytes": r[4],
                "canonical_path": r[5],
                "mime_type": r[6],
                "source_metadata": r[7],
            }
        )
    return rows


def _backfill_identity(db, rows: list[dict]) -> int:
    """Lazy half of migration 050: compute identity for file-backed rows
    that predate the columns. Mutates `rows` in place and persists —
    additive metadata, safe in dry-run."""
    from empirica.cli.command_handlers.artifact_log_commands import (
        _compute_content_identity,
    )

    backfilled = 0
    for row in rows:
        if row["content_hash"]:
            continue
        path = row["canonical_path"] or _doc_path_from_metadata(row)
        if not path and row["source_url"] and not str(row["source_url"]).startswith(("http://", "https://")):
            path = row["source_url"]
        if not path:
            continue
        identity = _compute_content_identity(path)
        if not identity["content_hash"]:
            continue
        db.conn.execute(
            "UPDATE epistemic_sources SET content_hash = ?, size_bytes = ?, "
            "canonical_path = ?, mime_type = ? WHERE id = ?",
            (
                identity["content_hash"],
                identity["size_bytes"],
                identity["canonical_path"],
                identity["mime_type"],
                row["id"],
            ),
        )
        row.update(identity)
        backfilled += 1
    if backfilled:
        db.conn.commit()
    return backfilled


def _doc_path_from_metadata(row: dict) -> str | None:
    try:
        meta = json.loads(row.get("source_metadata") or "{}")
        return meta.get("doc_path")
    except (json.JSONDecodeError, TypeError):
        return None


# Catalogue lookup accepts at most this many hashes per call (server-side
# cap in the pinned contract). Larger practices get chunked requests.
CATALOGUE_LOOKUP_BATCH = 500


def _discover_candidates(
    cortex_url: str | None,
    api_key: str | None,
    rows: list[dict],
) -> tuple[dict[str, dict], str]:
    """Look up catalogue rows by content_hash, chunked to the server cap.

    Returns ({content_hash: catalogue_row}, status). Hashes with no
    catalogue match are simply absent from the response. Connection
    errors degrade to an empty candidate set with an honest status so
    the verb stays useful (backfill still ran).
    """
    if not cortex_url or not api_key:
        return {}, "skipped_no_cortex_config"
    hashes = sorted({r["content_hash"] for r in rows if r["content_hash"]})
    if not hashes:
        return {}, "skipped_no_hashed_rows"
    candidates: dict[str, dict] = {}
    try:
        for i in range(0, len(hashes), CATALOGUE_LOOKUP_BATCH):
            body = _http_json(
                f"{cortex_url}{CATALOGUE_LOOKUP_PATH}",
                api_key,
                method="POST",
                payload={"content_hashes": hashes[i : i + CATALOGUE_LOOKUP_BATCH]},
            )
            candidates.update(
                {c["content_hash"]: c for c in body.get("sources", []) if c.get("content_hash") and c.get("id")}
            )
        return candidates, "ok"
    except urllib.error.HTTPError as e:
        return {}, f"unavailable_http_{e.code}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {}, f"unavailable: {e}"


def _propose_matches(
    rows: list[dict],
    candidates: dict[str, dict],
) -> list[dict]:
    """Pair local rows with catalogue rows by content_hash. Rows whose id
    already equals the catalogue id are reconciled — skip."""
    proposed = []
    for row in rows:
        cand = candidates.get(row["content_hash"] or "")
        if not cand or cand["id"] == row["id"]:
            continue
        proposed.append(
            {
                "local_uuid": row["id"],
                "cortex_uuid": cand["id"],
                "content_hash": row["content_hash"],
                "canonical_path": row["canonical_path"],
            }
        )
    return proposed


def _confirm_matches(
    cortex_url: str | None,
    api_key: str | None,
    proposed: list[dict],
) -> tuple[list[dict], list[dict], str]:
    """POST the pinned reconcile contract. Catalogue validates hash +
    tenancy; we swap only what it confirms."""
    if not cortex_url or not api_key:
        return [], [], "skipped_no_cortex_config"
    try:
        body = _http_json(
            f"{cortex_url}{RECONCILE_PATH}",
            api_key,
            method="POST",
            payload={"matches": proposed},
        )
        return (body.get("confirmed", []), body.get("rejected", []), "ok")
    except urllib.error.HTTPError as e:
        return [], [], f"unavailable_http_{e.code}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return [], [], f"unavailable: {e}"


def _set_cortex_uuid_alias(db, project_id: str, local_uuid: str, cortex_uuid: str) -> dict:
    """Non-destructive adopt (Unified Source Identity, Option A): record the
    catalogue uuid as an ALIAS on the local row WITHOUT rewriting its PK.

    The daemon resolves ``id OR cortex_uuid``, so both address the same source —
    no edge cascade, no Qdrant re-key, offline-safe. Use ``--converge`` for the
    destructive one-uuid PK-swap (``_swap_source_id``) when full convergence is
    wanted. Returns a status dict mirroring ``_swap_source_id``'s shape.
    """
    result = {"local_uuid": local_uuid, "cortex_uuid": cortex_uuid, "aliased": False}
    cursor = db.conn.cursor()
    try:
        cursor.execute(
            "UPDATE epistemic_sources SET cortex_uuid = ? WHERE id = ? AND project_id = ?",
            (cortex_uuid, local_uuid, project_id),
        )
        db.conn.commit()
        if cursor.rowcount == 0:
            result["error"] = "local row not found"
        else:
            result["aliased"] = True
    except Exception as e:
        db.conn.rollback()
        result["error"] = str(e)
    return result


def _swap_source_id(
    db,
    project_id: str,
    local_uuid: str,
    cortex_uuid: str,
) -> dict:
    """PK-swap one source to its catalogue uuid + cascade every local
    reference. One SQLite transaction — all-or-nothing per source.

    Cascade surface (verified against schema):
      - epistemic_sources.id (the row itself)
      - artifact_edges.from_id / to_id (sourced_from edges)
      - epistemic_sources.archive_target_id (supersession pointers)
      - project_findings.source_refs (JSON array of source uuids,
        migration 036 — explicit --source linking; the auto-extracted
        file-path refs in finding data are paths, not uuids, untouched)
      - workspace-DB entity_artifacts (separate database, best-effort)

    Qdrant points keep the old id until `empirica rebuild` regenerates
    them from SQLite.
    """
    result = {
        "local_uuid": local_uuid,
        "cortex_uuid": cortex_uuid,
        "swapped": False,
        "edges": 0,
        "archive_targets": 0,
        "finding_refs": 0,
        "entity_links": "skipped",
    }
    cursor = db.conn.cursor()
    try:
        cursor.execute("BEGIN")
        cursor.execute(
            "UPDATE epistemic_sources SET id = ? WHERE id = ? AND project_id = ?",
            (cortex_uuid, local_uuid, project_id),
        )
        if cursor.rowcount == 0:
            db.conn.rollback()
            result["error"] = "local row not found (already swapped?)"
            return result

        cursor.execute(
            "UPDATE artifact_edges SET from_id = ? WHERE from_id = ?",
            (cortex_uuid, local_uuid),
        )
        edges = cursor.rowcount
        cursor.execute(
            "UPDATE artifact_edges SET to_id = ? WHERE to_id = ?",
            (cortex_uuid, local_uuid),
        )
        edges += cursor.rowcount
        result["edges"] = edges

        cursor.execute(
            "UPDATE epistemic_sources SET archive_target_id = ? WHERE archive_target_id = ?",
            (cortex_uuid, local_uuid),
        )
        result["archive_targets"] = cursor.rowcount

        result["finding_refs"] = _swap_finding_source_refs(
            cursor,
            project_id,
            local_uuid,
            cortex_uuid,
        )

        db.conn.commit()
        result["swapped"] = True
    except sqlite3.Error as e:
        db.conn.rollback()
        result["error"] = str(e)
        return result

    result["entity_links"] = _swap_workspace_entity_links(
        local_uuid,
        cortex_uuid,
    )
    return result


def _swap_finding_source_refs(
    cursor,
    project_id: str,
    local_uuid: str,
    cortex_uuid: str,
) -> int:
    """Rewrite source_refs JSON arrays on findings that cite the old id."""
    cursor.execute(
        "SELECT id, source_refs FROM project_findings WHERE project_id = ? AND source_refs LIKE ?",
        (project_id, f"%{local_uuid}%"),
    )
    updated = 0
    for finding_id, refs_json in cursor.fetchall():
        try:
            refs = json.loads(refs_json or "[]")
        except json.JSONDecodeError:
            continue
        if local_uuid not in refs:
            continue
        refs = [cortex_uuid if r == local_uuid else r for r in refs]
        cursor.execute(
            "UPDATE project_findings SET source_refs = ? WHERE id = ?",
            (json.dumps(refs), finding_id),
        )
        updated += 1
    return updated


def _swap_workspace_entity_links(local_uuid: str, cortex_uuid: str) -> str:
    """Best-effort swap in the global workspace DB's entity_artifacts.
    Separate database — failure here must not unwind the project-DB swap."""
    try:
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository

        repo = WorkspaceDBRepository()
        cursor = repo.conn.cursor()
        cursor.execute(
            "UPDATE entity_artifacts SET artifact_id = ? WHERE artifact_type = 'source' AND artifact_id = ?",
            (cortex_uuid, local_uuid),
        )
        repo.conn.commit()
        n = cursor.rowcount
        repo.conn.close()
        return f"updated_{n}"
    except Exception as e:
        return f"skipped: {e}"


def _http_json(
    url: str,
    api_key: str,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _emit(output: str, payload: dict, human: str | None = None) -> None:
    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(human or json.dumps(payload, indent=2, default=str))


def _render_human(p: dict) -> str:
    lines = [
        f"sources-reconcile — {'DRY RUN' if p['dry_run'] else 'APPLIED'}",
        f"  Local sources:        {p['local_sources']}",
        f"  Identity backfilled:  {p['backfilled_identity']}",
        f"  Catalogue discovery:  {p['discovery']} ({p['candidates']} candidates)",
        f"  Matches proposed:     {p['proposed']}",
        f"  Confirm call:         {p['confirm']}",
        f"  Confirmed:            {len(p['confirmed'])}",
        f"  Rejected:             {len(p['rejected'])}",
    ]
    for r in p["rejected"][:10]:
        lines.append(f"    - {r.get('local_uuid', '?')[:8]} → {r.get('reason')}")
    if p["dry_run"] and p["confirmed"]:
        lines.append("  Run with --apply to perform the swaps.")
    for s in p["swapped"]:
        tag = "✓" if s.get("swapped") else f"! {s.get('error')}"
        lines.append(
            f"  {tag} {s['local_uuid'][:8]} → {s['cortex_uuid'][:8]} "
            f"(edges={s['edges']}, finding_refs={s['finding_refs']}, "
            f"entity_links={s['entity_links']})"
        )
    if sys.stdout.isatty() and not p["dry_run"] and p["swapped"]:
        lines.append("  Note: run `empirica rebuild` to re-point Qdrant entries.")
    return "\n".join(lines)
