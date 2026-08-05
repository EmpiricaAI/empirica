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


def _run_register_shared_backfill(args, project_id, output, practice_scope: bool = True) -> int:
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
        cols = (
            "id",
            "title",
            "source_type",
            "visibility",
            "content_hash",
            "size_bytes",
            "canonical_path",
            "mime_type",
            "project_id",
        )
        # Practice-scoped candidate read (same class as 75f0663c5). A practice's
        # project_id drifts, so `WHERE project_id = ?` made a bare run report
        # `candidates: 0, registered: 0` — which reads as "nothing to do" and is
        # actually "looked in the wrong place" (cortex, prop_4qwhtflam5h27gyty2prerbflq).
        #
        # Safe to widen WITHOUT touching the write below, unlike the --apply path:
        # this function owns both, and its UPDATE is `WHERE id = ?` with no project
        # filter, so the stamp cannot silently miss. Verified by resolving each SQL
        # line to its enclosing def — my first reading of the grep hits paired them
        # wrongly and would have blocked this one-liner behind the trio.
        #
        # An explicit --project-id keeps the strict read: a deliberate cross-project
        # query must not silently return the local practice.
        scoped = "1=1" if practice_scope else "project_id = ?"
        params: list = [] if practice_scope else [project_id]
        rows = db.conn.execute(
            f"SELECT {', '.join(cols)} FROM epistemic_sources "
            f"WHERE {scoped} AND COALESCE(archived, 0) = 0 "
            "AND visibility IN ('shared', 'public') AND cortex_uuid IS NULL",
            params,
        ).fetchall()
        registered, failed, rehomed = 0, [], []
        for raw in rows:
            r = dict(raw) if hasattr(raw, "keys") else dict(zip(cols, raw, strict=False))
            identity = {k: r.get(k) for k in ("content_hash", "size_bytes", "canonical_path", "mime_type")}
            res = _register_source_in_cortex(
                cortex_url, api_key, r["id"], project_id, r["title"], r["source_type"], r["visibility"], identity
            )
            if res.get("registered"):
                # Registered under the ACTIVE project_id, not the row's stored one.
                # Deliberate: cortex's catalogue is keyed per project, so registering
                # a drifted row under its stale id would propagate the drift onto the
                # shared surface instead of converging it. Reported per row below —
                # re-homing provenance silently would be the same defect class this
                # whole fix is about.
                if r.get("project_id") and r["project_id"] != project_id:
                    rehomed.append({"id": str(r["id"])[:8], "was": str(r["project_id"])[:8]})
                db.conn.execute("UPDATE epistemic_sources SET cortex_uuid = ? WHERE id = ?", (r["id"], r["id"]))
                # Commit per-source: the backfill is a long network loop that can
                # be interrupted/reaped; a single end-of-loop commit would lose
                # ALL progress on interruption. Per-source persist makes it
                # resumable (re-run skips cortex_uuid IS NOT NULL rows).
                db.conn.commit()
                registered += 1
            else:
                failed.append({"id": str(r["id"])[:8], "error": res.get("error")})
        payload = {"ok": True, "candidates": len(rows), "registered": registered, "failed": failed}
        if rehomed:
            payload["rehomed"] = rehomed
            payload["rehomed_note"] = (
                f"{len(rehomed)} source(s) were stored under a drifted project_id and are now "
                f"registered in cortex under the active id {str(project_id)[:8]}"
            )
        _emit(output, payload)
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
        return _run_register_shared_backfill(
            args, project_id, output, practice_scope=getattr(args, "project_id", None) is None
        )

    if getattr(args, "backfill_citations", False):
        # Purely local (no cortex) — dispatch before the catalogue path so any
        # practice can run it offline.
        #
        # `--project-id` selects the DATABASE here, not just a row filter. A bare
        # SessionDatabase() resolves the db from SESSION context (transaction →
        # active_work → TTY → instance_projects) and deliberately ignores CWD
        # ("CWD is unreliable with Claude Code", get_session_db_path), so gardening
        # another practice by cd-ing into it silently re-reads the ACTIVE practice's
        # db and reports its numbers under the other practice's name. Resolving the
        # path from the registry is what makes this usable per-practice.
        db_path = _project_db_path(project_id)
        db = SessionDatabase(db_path=db_path) if db_path else SessionDatabase()
        try:
            payload = _run_citation_backfill(db, project_id, apply)
            payload["db_path"] = str(getattr(db, "db_path", "") or "")
        finally:
            db.close()
        _emit(output, payload, _render_citation_human(payload))
        return 0 if payload.get("ok") else 1

    db = SessionDatabase()
    try:
        rows = _load_local_sources(db, project_id, practice_scope=getattr(args, "project_id", None) is None)
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
                    aliased.append(_set_cortex_uuid_alias(db, pair["local_uuid"], pair["cortex_uuid"]))
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


def _load_local_sources(db, project_id: str, practice_scope: bool = True) -> list[dict]:
    """Non-archived rows with everything the matcher needs.

    Practice-scoped by default — see `_run_register_shared_backfill`. Unlike that
    function this read IS coupled to filtered writes downstream
    (`_set_cortex_uuid_alias`, `_swap_source_id` and its finding-ref cascade), so
    widening it alone would leave those writes matching zero rows in silence. They
    are widened in the same commit; the writes now key on `id` only, which is
    unique within a practice because the db path IS the practice boundary.
    """
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT id, title, source_url, content_hash, size_bytes, "
        "canonical_path, mime_type, source_metadata "
        "FROM epistemic_sources "
        f"WHERE {'1=1' if practice_scope else 'project_id = ?'} AND COALESCE(archived, 0) = 0",
        [] if practice_scope else [project_id],
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


# Artifact tables that can carry the legacy `source_refs` citation column.
# (label, table) — label is what the report calls the citing artifact type.
_CITATION_TABLES: tuple[tuple[str, str], ...] = (
    ("finding", "project_findings"),
    ("unknown", "project_unknowns"),
    ("dead_end", "project_dead_ends"),
    ("mistake", "mistakes_made"),
    ("assumption", "assumptions"),
    ("decision", "decisions"),
)


def _parse_source_refs(raw: Any) -> list[str]:
    """Parse a `source_refs` cell into source ids.

    The column has been written both as a JSON list and as a comma-separated
    string over the years, so accept both rather than assuming one shape.
    """
    if not raw:
        return []
    text = str(raw).strip()
    if not text or text in ("[]", "null", "None"):
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
        if isinstance(parsed, str):
            text = parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _project_db_path(project_id: str) -> str | None:
    """Resolve a registered project's session DB from the global registry.

    Returns None when the project isn't registered or has no DB on disk, so the
    caller can fall back to session-context resolution.
    """
    try:
        from empirica.api.registry import find_by_project_id, load_registry

        entry = find_by_project_id(load_registry(), project_id)
    except Exception:
        return None
    path = (entry or {}).get("path")
    if not path:
        return None
    candidate = Path(path) / ".empirica" / "sessions" / "sessions.db"
    return str(candidate) if candidate.is_file() else None


def _run_citation_backfill(db, project_id: str, apply: bool) -> dict:
    """Promote legacy `source_refs` COLUMN citations into real `sourced_from` EDGES,
    and report this practice's citation health.

    Why: `--source` historically serialized ids into the `source_refs` column only,
    so those citations were invisible to the artifact graph — weave/connectivity,
    `sources-map`, the daemon's `related_from` projection, and `sanctify`'s
    zombie-source check all read EDGES. `breadcrumbs._attach_sources` writes the edge
    for new logs; this recovers the ones written before that landed.

    Honest expectation: the recoverable set is small (measured 2026-07-25 across the
    whole local fleet: 446 sources, only 6 artifacts carrying `source_refs`). The
    backfill is cheap, correct and idempotent, but it is NOT what makes a practice's
    sources well-cited — that is citation discipline at log time. Hence the
    `citation_health` block: it reports how many sources nothing references, which is
    the number a practice can actually act on.

    Purely local: no cortex calls, so it works for any practice (tenant or AI) with
    just `--project-id`. Dry-run unless `apply`.
    """
    cur = db.conn.cursor()

    # PRACTICE-scoped, not single-project_id scoped. The session DB lives at
    # {project_path}/.empirica/sessions/sessions.db — one DB per practice — so every
    # row in it is this practice's by construction, while its project_id DRIFTS over
    # the practice's life (measured on empirica: 63 sources across 4 ids, 3 of them
    # absent from registry.yaml). Filtering to the one canonical id would hide the
    # drifted rows from gardening — and would disagree with the daemon, which reads
    # the same set practice-scoped. Run this from within the practice whose DB you
    # want to garden.
    valid_sources: set[str] = set()
    try:
        cur.execute("SELECT id FROM epistemic_sources")
        valid_sources = {r[0] for r in cur.fetchall()}
    except sqlite3.OperationalError as e:
        return {"ok": False, "error": f"epistemic_sources unreadable: {e}"}

    existing: set[tuple[str, str]] = set()
    try:
        cur.execute("SELECT from_id, to_id FROM artifact_edges WHERE relation = 'sourced_from'")
        existing = {(r[0], r[1]) for r in cur.fetchall()}
    except sqlite3.OperationalError:
        pass  # pre-edges schema — everything is "to create"

    to_create: list[dict] = []
    already: int = 0
    dangling: list[dict] = []
    scanned: int = 0

    for label, table in _CITATION_TABLES:
        try:
            cur.execute(
                # `table` interpolates only from the fixed _CITATION_TABLES literal.
                # Practice-scoped (no project_id filter) for the same reason as the
                # source read above — citing artifacts drift across ids too.
                f"SELECT id, source_refs FROM {table} "
                f"WHERE source_refs IS NOT NULL AND source_refs NOT IN ('', '[]', 'null')"
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            continue  # table or column absent on this project's schema — normal drift
        for artifact_id, refs in rows:
            scanned += 1
            for sid in _parse_source_refs(refs):
                if sid not in valid_sources:
                    # Never fabricate an edge to a source that isn't there — that
                    # would plant dangling edges the graph would have to carry.
                    dangling.append({"artifact_id": artifact_id, "type": label, "missing_source_id": sid})
                    continue
                if (artifact_id, sid) in existing:
                    already += 1
                    continue
                to_create.append({"artifact_id": artifact_id, "type": label, "source_id": sid})

    created = 0
    write_failures: list[dict] = []
    if apply and to_create:
        for edge in to_create:
            try:
                db.conn.execute(
                    "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, 'sourced_from')",
                    (edge["artifact_id"], edge["source_id"]),
                )
                created += 1
            except sqlite3.OperationalError as e:
                # Surface in the receipt rather than a debug log — a partial backfill
                # that reports itself as clean is the failure mode worth avoiding.
                write_failures.append({**edge, "error": str(e)})
        db.conn.commit()

    # Citation health — the number that actually matters. A source nothing references
    # is what `sanctify` calls a zombie; a practice with many is under-citing, which no
    # backfill can fix.
    #
    # Scored over ACTIVE sources only. An archived source is retired: nothing should
    # reference it, so counting it as "uncited" would inflate the number gardening
    # asks the practice to act on. (Backfill itself still writes edges to archived
    # sources — the citation really happened; it just isn't a live gap.)
    cited: set[str] = set()
    try:
        cur.execute(
            "SELECT DISTINCT to_id FROM artifact_edges WHERE relation = 'sourced_from'",
        )
        cited = {r[0] for r in cur.fetchall()}
    except sqlite3.OperationalError:
        pass
    if apply:
        cited |= {e["source_id"] for e in to_create}

    active_sources = valid_sources
    archived_count = 0
    try:
        cur.execute("SELECT id FROM epistemic_sources WHERE COALESCE(archived, 0) = 0")
        active_sources = {r[0] for r in cur.fetchall()}
        archived_count = len(valid_sources) - len(active_sources)
    except sqlite3.OperationalError:
        pass  # pre-archive schema — every source counts as active
    uncited = len(active_sources - cited)

    return {
        "ok": True,
        "dry_run": not apply,
        "mode": "backfill-citations",
        "project_id": project_id,
        "artifacts_with_source_refs": scanned,
        "edges_already_present": already,
        "edges_to_create": len(to_create) if not apply else 0,
        "edges_created": created,
        "write_failures": write_failures,
        "dangling_refs": dangling,
        "citation_health": {
            "sources_active": len(active_sources),
            "sources_archived": archived_count,
            "sources_cited": len(active_sources & cited),
            "sources_uncited": uncited,
            "note": (
                "Uncited sources are invisible as 'citing artifacts' and count as "
                "zombies to `sanctify`. Backfill only recovers legacy source_refs; "
                "closing the gap means citing sources at log time (--source / "
                "sourced_from in log-artifacts)."
            ),
        },
    }


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


def _set_cortex_uuid_alias(db, local_uuid: str, cortex_uuid: str) -> dict:
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
            # Keyed on id alone: the source came from a practice-scoped read, so a
            # project_id filter here would silently match zero rows for any source
            # sitting under a drifted id — the write half of the under-read.
            "UPDATE epistemic_sources SET cortex_uuid = ? WHERE id = ?",
            (cortex_uuid, local_uuid),
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
            # id-only, as above. A PK swap that no-ops because the row sits under a
            # drifted project_id would still cascade every REFERENCE to the new id,
            # leaving them pointing at a row that was never renamed.
            "UPDATE epistemic_sources SET id = ? WHERE id = ?",
            (cortex_uuid, local_uuid),
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
    local_uuid: str,
    cortex_uuid: str,
) -> int:
    """Rewrite source_refs JSON arrays on findings that cite the old id.

    Practice-scoped, and this one is a data-integrity matter rather than a
    visibility one: findings drift across project_ids exactly as sources do, so a
    `project_id` filter here would rename the source and leave every finding under
    a drifted id still citing the OLD uuid — dangling refs created by the repair.
    The cascade must cover every citation in the practice or it must not run.
    """
    cursor.execute(
        "SELECT id, source_refs FROM project_findings WHERE source_refs LIKE ?",
        (f"%{local_uuid}%",),
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


def _render_citation_human(p: dict) -> str:
    if not p.get("ok"):
        return f"sources-reconcile --backfill-citations — FAILED: {p.get('error')}"
    h = p.get("citation_health", {})
    lines = [
        f"sources-reconcile --backfill-citations — {'DRY RUN' if p['dry_run'] else 'APPLIED'}",
        f"  Artifacts w/ source_refs: {p['artifacts_with_source_refs']}",
        f"  Edges already present:    {p['edges_already_present']}",
    ]
    if p["dry_run"]:
        lines.append(f"  Edges to create:          {p['edges_to_create']}")
        if p["edges_to_create"]:
            lines.append("  Run with --apply to write them.")
    else:
        lines.append(f"  Edges created:            {p['edges_created']}")
    for f in p.get("write_failures", [])[:5]:
        lines.append(f"    ! write failed {str(f.get('artifact_id'))[:8]} → {f.get('error')}")
    for d in p.get("dangling_refs", [])[:5]:
        lines.append(
            f"    - dangling ref {str(d.get('artifact_id'))[:8]} → missing source {str(d.get('missing_source_id'))[:8]}"
        )
    lines += [
        "  Citation health (active sources):",
        f"    active:          {h.get('sources_active', 0)}  (archived, not scored: {h.get('sources_archived', 0)})",
        f"    cited:           {h.get('sources_cited', 0)}",
        f"    UNCITED:         {h.get('sources_uncited', 0)}",
    ]
    if h.get("sources_uncited"):
        lines.append("    ^ nothing references these; backfill can't fix that — cite sources at log time")
    return "\n".join(lines)


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
