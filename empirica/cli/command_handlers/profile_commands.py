"""
Profile Commands - Epistemic profile management

Commands:
- profile-sync: Full sync pipeline (fetch → import → optional Qdrant rebuild)
- profile-prune: Transparent artifact pruning with git notes receipts
- profile-status: Unified profile status view
- profile-import: Import epistemic artifacts from AI conversation transcripts
"""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _get_workspace_root() -> str:
    """Get workspace root from active context, git root, or cwd."""
    import os

    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        context_project = R.project_path()
        if context_project:
            return context_project
    except Exception:
        pass
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def _load_sync_config() -> dict[str, Any]:
    """Load sync config — reuses sync_commands logic."""
    from .sync_commands import _load_sync_config as load_config

    return load_config()


def _resolve_notes_remote(sync_config: dict[str, Any], explicit: str | None = None) -> str | None:
    """The notes remote, or None — the same resolution the sync verbs use.

    Shared deliberately: this file used to resolve `notes_remote -> remote -> "forgejo"`
    while `sync-push` read `remote` alone, so `sync-config notes_remote X` retargeted
    profile-sync and not sync-push. One key, two verbs, two destinations.
    """
    from empirica.core.sync_remotes import NOTES, resolve

    return resolve(NOTES, sync_config, explicit)


def _notes_remote_refusal() -> dict[str, Any]:
    """Refusal payload naming the configured git remotes and the exact fix."""
    from empirica.core.sync_remotes import NOTES, refusal

    return refusal(NOTES)


def _fetch_notes(remote: str) -> dict[str, Any]:
    """Fetch all empirica git notes refs from remote.

    Returns dict with 'ok', 'fetched', 'errors' keys.
    """
    results: dict[str, Any] = {"ok": True, "fetched": {}, "errors": []}

    # Fetch all empirica notes at once
    refspecs = [
        "refs/notes/empirica/*:refs/notes/empirica/*",
        "refs/notes/breadcrumbs:refs/notes/breadcrumbs",
        "refs/notes/empirica-precompact:refs/notes/empirica-precompact",
    ]

    for refspec in refspecs:
        try:
            result = subprocess.run(
                ["git", "fetch", remote, refspec], capture_output=True, text=True, timeout=60, cwd=_get_workspace_root()
            )
            ref_name = refspec.split(":")[0].replace("refs/notes/", "")
            if result.returncode == 0:
                results["fetched"][ref_name] = True
            else:
                if "no matching refs" not in (result.stderr or "").lower():
                    results["errors"].append(f"{ref_name}: {result.stderr.strip()}")
                results["fetched"][ref_name] = False
        except subprocess.TimeoutExpired:
            results["errors"].append(f"Fetch timed out for {refspec}")
            results["ok"] = False
        except Exception as e:
            results["errors"].append(str(e))
            results["ok"] = False

    if results["errors"]:
        results["ok"] = False

    return results


def _push_notes(remote: str) -> dict[str, Any]:
    """Push all empirica git notes refs to remote."""
    results: dict[str, Any] = {"ok": True, "pushed": {}, "errors": []}

    refspecs = [
        "refs/notes/empirica/*:refs/notes/empirica/*",
        "refs/notes/breadcrumbs:refs/notes/breadcrumbs",
        "refs/notes/empirica-precompact:refs/notes/empirica-precompact",
    ]

    for refspec in refspecs:
        try:
            result = subprocess.run(
                ["git", "push", remote, refspec], capture_output=True, text=True, timeout=60, cwd=_get_workspace_root()
            )
            ref_name = refspec.split(":")[0].replace("refs/notes/", "")
            if result.returncode == 0:
                results["pushed"][ref_name] = True
            else:
                if "no matching refs" not in (result.stderr or "").lower():
                    results["errors"].append(f"{ref_name}: {result.stderr.strip()}")
                results["pushed"][ref_name] = False
        except subprocess.TimeoutExpired:
            results["errors"].append(f"Push timed out for {refspec}")
            results["ok"] = False
        except Exception as e:
            results["errors"].append(str(e))
            results["ok"] = False

    if results["errors"]:
        results["ok"] = False

    return results


def _import_notes_to_sqlite() -> dict[str, Any]:
    """Import git notes artifacts into SQLite using ProfileImporter (idempotent)."""
    try:
        from empirica.core.canonical.empirica_git.profile_import import ProfileImporter
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        try:
            importer = ProfileImporter(workspace_root=_get_workspace_root())
            stats = importer.import_all(db)
            return {
                "ok": True,
                "stats": stats,
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Import failed: {e}")
        return {
            "ok": False,
            "error": str(e),
        }


def _rebuild_qdrant() -> dict[str, Any]:
    """Rebuild Qdrant semantic index from SQLite."""
    try:
        from empirica.core.qdrant.rebuild import rebuild_qdrant_from_db

        result = rebuild_qdrant_from_db()
        return {"ok": True, "result": result}
    except ImportError:
        return {"ok": False, "error": "Qdrant not available (qdrant-client not installed)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _print_sync_pretty(result, import_result, remote, import_only, do_push, do_qdrant):
    """Print human-readable sync result."""
    summary = result.get("summary", {})
    if result["ok"]:
        print("✅ Profile sync complete")
        if not import_only:
            print(f"   Fetched from: {remote}")
        print(f"   Imported: {summary.get('imported', 0)} new artifacts")
        print(f"   Skipped: {summary.get('skipped', 0)} (already in SQLite)")
        print(f"   Total in notes: {summary.get('total_notes', 0)}")
        stats = import_result.get("stats", {})
        for artifact_type in ["findings", "unknowns", "dead_ends", "mistakes", "goals"]:
            type_stats = stats.get(artifact_type, {})
            if type_stats.get("total", 0) > 0:
                print(f"     {artifact_type}: {type_stats['imported']} new / {type_stats['total']} total")
        if do_push:
            print(f"   Pushed to: {remote}")
        if do_qdrant:
            qdrant_ok = result.get("qdrant", {}).get("ok", False)
            print(f"   Qdrant: {'rebuilt' if qdrant_ok else 'failed'}")
    else:
        print(f"❌ Profile sync failed: {result.get('error', 'Unknown error')}")


def handle_profile_sync_command(args):
    """Handle profile-sync command — full sync pipeline."""
    try:
        sync_config = _load_sync_config()
        # No literal tail. This used to end in `"forgejo"`, a remote most seats do not
        # have — so on those seats every fetch failed against a name nobody chose, and
        # the error blamed git rather than the missing configuration.
        remote = _resolve_notes_remote(sync_config, getattr(args, "remote", None))
        output_format = getattr(args, "output", "json")
        do_push = getattr(args, "push", False)
        do_qdrant = getattr(args, "qdrant", False)
        import_only = getattr(args, "import_only", False)
        force = getattr(args, "force", False)

        if not sync_config.get("enabled", True) and not force and not import_only:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Sync is disabled in config",
                        "hint": "Run 'empirica sync-config enabled true' to enable, or use --force",
                    },
                    indent=2,
                )
            )
            return 1

        # A remote is needed for fetch and for push, and for nothing else — so
        # `--import-only` still works on a seat that has chosen no destination.
        if not remote and (not import_only or do_push):
            print(json.dumps(_notes_remote_refusal(), indent=2))
            return 1

        result: dict[str, Any] = {"ok": True, "pipeline": []}

        if not import_only:
            fetch_result = _fetch_notes(remote)
            result["fetch"] = fetch_result
            result["pipeline"].append("fetch")
            if not fetch_result["ok"]:
                result["ok"] = False
                result["error"] = "Fetch failed"
                print(json.dumps(result, indent=2, default=str))
                return 1

        import_result = _import_notes_to_sqlite()
        result["import"] = import_result
        result["pipeline"].append("import")
        if not import_result["ok"]:
            result["ok"] = False
            result["error"] = "Import failed"

        if do_push and result["ok"]:
            push_result = _push_notes(remote)
            result["push"] = push_result
            result["pipeline"].append("push")
            if not push_result["ok"]:
                result["push_warning"] = "Push had errors but import succeeded"

        if do_qdrant and result["ok"]:
            qdrant_result = _rebuild_qdrant()
            result["qdrant"] = qdrant_result
            result["pipeline"].append("qdrant")
            if not qdrant_result["ok"]:
                result["qdrant_warning"] = "Qdrant rebuild failed but import succeeded"

        summary = import_result.get("stats", {}).get("_summary", {})
        result["summary"] = {
            "imported": summary.get("imported", 0),
            "skipped": summary.get("skipped", 0),
            "total_notes": summary.get("total", 0),
        }

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_sync_pretty(result, import_result, remote, import_only, do_push, do_qdrant)

        return 0 if result["ok"] else 1

    except Exception as e:
        handle_cli_error(e, "Profile sync", getattr(args, "verbose", False))
        return 1


def _write_prune_receipt(artifact_id: str, artifact_type: str, reason: str, artifact_summary: str) -> bool:
    """Write an immutable prune receipt to git notes.

    Receipt is stored at refs/notes/empirica/prune_receipts/<artifact_id>
    """
    receipt = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "reason": reason,
        "artifact_summary": artifact_summary,
        "pruned_at": datetime.now(timezone.utc).isoformat(),
        "pruned_by": "empirica-profile-prune",
    }

    workspace = _get_workspace_root()
    ref = f"empirica/prune_receipts/{artifact_id}"

    try:
        # Write receipt as a git note on HEAD
        receipt_json = json.dumps(receipt, indent=2)
        result = subprocess.run(
            ["git", "notes", f"--ref={ref}", "add", "-f", "-m", receipt_json, "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=workspace,
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"Failed to write prune receipt for {artifact_id}: {e}")
        return False


def _prune_artifact(db, artifact_id: str, artifact_type: str, reason: str) -> dict[str, Any]:
    """Remove a single artifact from SQLite and write prune receipt."""
    # THIRD private copy of the type map, now removed. It knew five types and was
    # missing `assumption`, `decision` and `source` — so pruning silently refused
    # three artifact types the registry declares, and refused them with the same
    # message a typo gets.
    from empirica.data.artifact_fields import DELETABLE_TYPES, NON_DELETABLE_REASON, artifact_table

    if artifact_type in NON_DELETABLE_REASON and artifact_type not in DELETABLE_TYPES:
        # Pruning is a deletion, so the same policy applies: a source is archived,
        # never destroyed, and saying so beats an unknown-type error.
        return {"ok": False, "error": f"'{artifact_type}' cannot be pruned: {NON_DELETABLE_REASON[artifact_type]}"}

    resolved = artifact_table(artifact_type)
    if resolved is None or artifact_type not in DELETABLE_TYPES:
        return {"ok": False, "error": f"Unknown artifact type: {artifact_type}"}
    table, _id_col, _ = resolved

    # Fetch artifact summary before deletion
    cursor = db.conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (artifact_id,))
        row = cursor.fetchone()
        if not row:
            return {"ok": False, "error": f"Artifact {artifact_id} not found in {table}"}

        # Build summary from row
        columns = [desc[0] for desc in cursor.description]
        artifact_data = dict(zip(columns, row))
        # Use a representative field for summary
        summary_field = {
            "finding": "finding",
            "unknown": "unknown",
            "dead_end": "approach",
            "mistake": "mistake",
            "goal": "objective" if "objective" in columns else "id",
        }.get(artifact_type, "id")
        artifact_summary = str(artifact_data.get(summary_field, artifact_id))[:200]

        # Write prune receipt BEFORE deletion (so we never lose the audit trail)
        receipt_written = _write_prune_receipt(artifact_id, artifact_type, reason, artifact_summary)

        # Delete from SQLite
        cursor.execute(f"DELETE FROM {table} WHERE id = ?", (artifact_id,))
        db.conn.commit()

        return {
            "ok": True,
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "summary": artifact_summary,
            "receipt_written": receipt_written,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _prune_find_low_confidence_imports(cursor, age_cutoff):
    """Find low-confidence transcript-imported artifacts for pruning."""
    candidates = []
    confidence_threshold = 0.6
    for table, artifact_type, text_col, data_col in [
        ("project_findings", "finding", "finding", "finding_data"),
        ("project_unknowns", "unknown", "unknown", "unknown_data"),
        ("project_dead_ends", "dead_end", "approach", "dead_end_data"),
        ("mistakes_made", "mistake", "mistake", "mistake_data"),
    ]:
        try:
            query = f"""
                SELECT id, {text_col}, {data_col} FROM {table}
                WHERE {data_col} LIKE '%"extraction_confidence"%'
            """
            params = []
            if age_cutoff:
                query += " AND created_timestamp < ?"
                params.append(age_cutoff)
            cursor.execute(query, params)
            for row in cursor.fetchall():
                try:
                    data = json.loads(row[2]) if row[2] else {}
                    conf = data.get("extraction_confidence", 1.0)
                    if conf < confidence_threshold:
                        candidates.append(
                            {
                                "id": row[0],
                                "type": artifact_type,
                                "summary": str(row[1])[:200],
                                "reason": f"Low confidence transcript import ({conf:.2f} < {confidence_threshold}) (low-confidence-imports rule)",
                            }
                        )
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass
    return candidates


def _prune_find_candidates(cursor, rule, age_cutoff):
    """Find prune candidates for a given rule. Returns list of candidate dicts."""
    now = time.time()
    candidates = []

    if rule == "stale-resolved-unknowns":
        query = "SELECT id, unknown, resolved_timestamp FROM project_unknowns WHERE is_resolved = 1"
        params = []
        if age_cutoff:
            query += " AND resolved_timestamp < ?"
            params.append(age_cutoff)
        cursor.execute(query, params)
        for row in cursor.fetchall():
            candidates.append(
                {
                    "id": row[0],
                    "type": "unknown",
                    "summary": str(row[1])[:200],
                    "reason": "Resolved unknown (stale-resolved-unknowns rule)",
                }
            )

    elif rule == "low-impact-findings":
        query = "SELECT id, finding, impact FROM project_findings WHERE impact < 0.3"
        params = []
        if age_cutoff:
            query += " AND created_timestamp < ?"
            params.append(age_cutoff)
        cursor.execute(query, params)
        for row in cursor.fetchall():
            candidates.append(
                {
                    "id": row[0],
                    "type": "finding",
                    "summary": str(row[1])[:200],
                    "reason": f"Low impact ({row[2]}) finding (low-impact-findings rule)",
                }
            )

    elif rule == "old-dead-ends":
        threshold = age_cutoff or (now - 90 * 86400)
        cursor.execute("SELECT id, approach FROM project_dead_ends WHERE created_timestamp < ?", (threshold,))
        for row in cursor.fetchall():
            candidates.append(
                {
                    "id": row[0],
                    "type": "dead_end",
                    "summary": str(row[1])[:200],
                    "reason": "Old dead end (old-dead-ends rule)",
                }
            )

    elif rule == "test-transactions":
        cursor.execute("""
            SELECT pf.id, pf.finding, pf.session_id
            FROM project_findings pf
            JOIN sessions s ON pf.session_id = s.session_id
            WHERE s.ai_id = 'test' OR s.ai_id LIKE 'test-%'
        """)
        for row in cursor.fetchall():
            candidates.append(
                {
                    "id": row[0],
                    "type": "finding",
                    "summary": str(row[1])[:200],
                    "reason": "Test session artifact (test-transactions rule)",
                }
            )

    elif rule == "low-confidence-imports":
        candidates = _prune_find_low_confidence_imports(cursor, age_cutoff)

    return candidates


def _apply_prune_rule(db, rule: str, older_than_days: int | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Apply a mechanical pruning rule.

    Returns dict with 'ok', 'count', and 'pruned'/'candidates' keys.
    """
    if rule == "falsified-assumptions":
        return {"ok": True, "candidates": [], "note": "Assumptions table not yet supported for pruning"}

    cursor = db.conn.cursor()
    age_cutoff = (time.time() - (older_than_days * 86400)) if older_than_days else None
    candidates = _prune_find_candidates(cursor, rule, age_cutoff)

    if dry_run:
        return {"ok": True, "dry_run": True, "candidates": candidates, "count": len(candidates)}

    pruned = []
    errors = []
    for candidate in candidates:
        result = _prune_artifact(db, candidate["id"], candidate["type"], candidate["reason"])
        if result["ok"]:
            pruned.append(result)
        else:
            errors.append(result)

    return {"ok": len(errors) == 0, "pruned": pruned, "errors": errors, "count": len(pruned)}


def handle_profile_prune_command(args):
    """Handle profile-prune command — transparent artifact pruning."""
    try:
        rule = getattr(args, "rule", None)
        artifact_id = getattr(args, "artifact_id", None)
        artifact_type = getattr(args, "artifact_type", None)
        reason = getattr(args, "reason", None)
        older_than = getattr(args, "older_than", None)
        dry_run = getattr(args, "dry_run", False)
        output_format = getattr(args, "output", "json")

        scope = getattr(args, "scope", None)

        # Memory scope: prune stale CC memory files
        if scope == "memory":
            from empirica.core.memory_manager import demote_stale_memories

            stale_days = int(older_than) if older_than else 30
            archived = demote_stale_memories(stale_days=stale_days, dry_run=dry_run)
            result = {
                "ok": True,
                "scope": "memory",
                "dry_run": dry_run,
                "archived": archived,
                "count": len(archived),
                "stale_days": stale_days,
            }
            if output_format == "json":
                print(json.dumps(result, indent=2))
            else:
                if archived:
                    action = "Would archive" if dry_run else "Archived"
                    print(f"{action} {len(archived)} stale memory files (>{stale_days} days):")
                    for f in archived:
                        print(f"  {f}")
                else:
                    print(f"No stale promoted memory files older than {stale_days} days.")
            return 0

        if not rule and not artifact_id:
            result = {
                "ok": False,
                "error": "Specify --rule for rule-based pruning, --artifact-id for specific artifact, or --scope memory for CC memory files",
                "available_rules": [
                    "stale-resolved-unknowns",
                    "test-transactions",
                    "low-impact-findings",
                    "old-dead-ends",
                ],
                "available_scopes": ["memory"],
            }
            print(json.dumps(result, indent=2))
            return 1

        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        try:
            if artifact_id:
                if not artifact_type:
                    result = {"ok": False, "error": "--artifact-type required with --artifact-id"}
                    print(json.dumps(result, indent=2))
                    return 1

                if dry_run:
                    result = {
                        "ok": True,
                        "dry_run": True,
                        "would_prune": {
                            "artifact_id": artifact_id,
                            "artifact_type": artifact_type,
                            "reason": reason or "Manual prune",
                        },
                    }
                else:
                    result = _prune_artifact(db, artifact_id, artifact_type, reason or "Manual prune")
            else:
                assert rule is not None  # noqa: S101 — guaranteed by earlier check
                result = _apply_prune_rule(db, rule, older_than, dry_run)
        finally:
            db.close()

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            if dry_run:
                count = result.get("count", 0)
                candidates = result.get("candidates", [])
                print(f"🔍 Dry run: {count} artifacts would be pruned")
                for c in candidates[:20]:
                    print(f"   [{c['type']}] {c['id'][:12]}... — {c['summary'][:60]}")
                if count > 20:
                    print(f"   ... and {count - 20} more")
            elif result.get("ok"):
                count = result.get("count", 1)
                print(f"✅ Pruned {count} artifact(s) with receipts in git notes")
            else:
                print(f"❌ Prune failed: {result.get('error', 'Unknown error')}")

        return 0 if result.get("ok") else 1

    except Exception as e:
        handle_cli_error(e, "Profile prune", getattr(args, "verbose", False))
        return 1


def _get_artifact_counts() -> dict:
    """Get artifact counts from SQLite."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    counts = {}
    try:
        cursor = db.conn.cursor()
        for name, table in {
            "findings": "project_findings",
            "unknowns": "project_unknowns",
            "dead_ends": "project_dead_ends",
            "mistakes": "mistakes_made",
            "goals": "goals",
        }.items():
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[name] = cursor.fetchone()[0]
            except Exception:
                counts[name] = -1
        for key, query in [
            ("unknowns_resolved", "SELECT COUNT(*) FROM project_unknowns WHERE is_resolved = 1"),
            ("sessions", "SELECT COUNT(*) FROM sessions"),
            (
                "snapshots",
                "SELECT COUNT(*) FROM epistemic_snapshots WHERE snapshot_type IN ('preflight', 'postflight')",
            ),
        ]:
            try:
                cursor.execute(query)
                counts[key] = cursor.fetchone()[0]
            except Exception:
                counts[key] = 0
    finally:
        db.close()
    return counts


def _get_correction_record() -> dict:
    """How often has this practice recorded that it was WRONG, versus merely old?

    `resolution_kind` (migration 061) was queryable from the day it shipped and
    reported NOWHERE a practitioner looks — read in exactly one place, the
    source-outcome attribution. A field that only the code reads is one step from
    the free-text `resolution` it replaced, which failed for the same reason:
    nobody could see the aggregate, so nobody noticed it was degenerate.

    What made the original defect visible was a single number — 1267 stale against
    1 wrong across 1268 resolutions. This surfaces that number by default.

    Returned separately from `_get_artifact_counts` rather than nested inside it:
    that dict is summed with ``sum(v for v in ... if isinstance(v, int))`` and
    iterated with ``count > 0``, so a nested dict would silently drop from the
    total and raise on the comparison.
    """
    from empirica.data.resolution_kind import RESOLUTION_KINDS
    from empirica.data.session_database import SessionDatabase

    rec: dict = {"resolved": 0, "by_kind": {}, "unclassified": 0, "linked_supersessions": 0}
    db = SessionDatabase()
    try:
        cur = db.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_findings WHERE is_resolved = 1")
        rec["resolved"] = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(resolution_kind, ''), COUNT(*) FROM project_findings WHERE is_resolved = 1 GROUP BY 1"
        )
        for kind, n in cur.fetchall():
            if kind in RESOLUTION_KINDS:
                rec["by_kind"][kind] = n
            else:
                rec["unclassified"] += n
        # Supersession that is LINKED, not merely narrated. 1034 resolutions said
        # "superseded" in prose here while this count was 0 across 4199 rows.
        cur.execute("SELECT COUNT(*) FROM project_findings WHERE superseded_by IS NOT NULL AND superseded_by != ''")
        rec["linked_supersessions"] = cur.fetchone()[0]
    except Exception:
        # Pre-061 DBs have no resolution_kind. Report nothing rather than zeros —
        # "column absent" and "practice never retracted" are different states and
        # printing 0 for both is the conflation this whole surface exists to end.
        return {}
    finally:
        db.close()
    return rec


#: Below this many resolutions, zero retractions is unremarkable — a young practice
#: legitimately has not been wrong yet. Above it, zero stops being plausible and
#: starts being evidence the vocabulary is not reaching the practitioner. Floored
#: deliberately so the note is rare: a prompt that fires on every run is one the
#: reader learns to skip, which is how the POSTFLIGHT breadth nudge lost its force.
_RETRACTION_PLAUSIBILITY_FLOOR = 50


def _get_ceremony_record() -> dict:
    """How many CHECKs carried nothing — the ceremony metric.

    A peer measured **47% of 728 CHECKs submitted within 30s of their PREFLIGHT**.
    That is what the gate-as-unlock instinct produces at scale: a CHECK filed to
    get through rather than to certify.

    A short gap is NOT itself a fault — grounding often happens before the window
    opens, since noetic work is ungated. What makes a CHECK ceremonial is carrying
    nothing: no claims declared AND no artifacts logged since PREFLIGHT. So the
    conjunction is the metric, never the clock alone. Reporting on duration would
    repeat the rush guard's own defect — teaching "wait longer" instead of
    "certify or skip".

    Requires PREFLIGHT snapshots, which only began recording 2026-07-31, so the
    numbers here start from that date rather than covering history. Said plainly
    in the output: a metric whose coverage is unstated reads as complete.
    """
    from empirica.data.session_database import SessionDatabase

    rec: dict = {"checks": 0, "with_claims": 0, "ceremonial": 0, "since": None}
    db = SessionDatabase()
    try:
        cur = db.conn.cursor()
        cur.execute("SELECT MIN(timestamp) FROM epistemic_snapshots WHERE cascade_phase = 'PREFLIGHT'")
        row = cur.fetchone()
        if not row or not row[0]:
            return {}  # No PREFLIGHT rows yet — say nothing rather than report zeros.
        rec["since"] = row[0]

        cur.execute(
            "SELECT COUNT(*) FROM epistemic_snapshots WHERE cascade_phase = 'CHECK' AND timestamp >= ?",
            (rec["since"],),
        )
        rec["checks"] = cur.fetchone()[0]

        # Scoped to the SAME window as the CHECK count. Counting claims over all
        # time against CHECKs since a start date would put two different windows
        # side by side in one block and invite the reader to divide them — the
        # numbers would look like a ratio and mean nothing.
        cur.execute(
            "SELECT COUNT(DISTINCT transaction_id) FROM transaction_claims "
            "WHERE grounding IS NOT NULL AND declared_timestamp >= ?",
            (rec["since"],),
        )
        rec["with_claims"] = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(DISTINCT transaction_id) FROM transaction_claims "
            "WHERE grounding IN ('read','ran') AND declared_timestamp >= ?",
            (rec["since"],),
        )
        rec["grounded"] = cur.fetchone()[0]
    except Exception:
        return {}
    finally:
        db.close()
    return rec


def _print_ceremony_record(rec: dict) -> None:
    """Render the ceremony metric, or stay silent when there is nothing to say."""
    if not rec or not rec.get("checks"):
        return
    print("\n  CHECK record — all figures since PREFLIGHT snapshots began:")
    print(f"    CHECKs submitted: {rec['checks']}")
    print(f"    transactions declaring claims: {rec.get('with_claims', 0)}")
    print(f"      of which grounded (read/ran, certifies): {rec.get('grounded', 0)}")
    if not rec.get("with_claims"):
        print(
            "    ⚠️  No transaction has declared load-bearing claims. A CHECK that\n"
            "        names nothing certifies nothing — and if you were already\n"
            "        grounded, declare claims at PREFLIGHT and skip CHECK instead."
        )


def _get_git_notes_counts(workspace) -> dict:
    """Get artifact counts from git notes (canonical source)."""
    counts = {}
    for ref_name in [
        "empirica/findings",
        "empirica/unknowns",
        "empirica/dead_ends",
        "empirica/mistakes",
        "empirica/goals",
        "empirica/sessions",
    ]:
        try:
            result = subprocess.run(
                ["git", "for-each-ref", f"refs/notes/{ref_name}/"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=workspace,
            )
            if result.returncode == 0:
                counts[ref_name.split("/")[-1]] = len([l for l in result.stdout.strip().split("\n") if l.strip()])
        except Exception:
            pass
    return counts


def _check_sync_available(workspace, remote) -> bool:
    """Check if git remote is available for sync. No remote chosen → not available."""
    if not remote:
        return False
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote], capture_output=True, text=True, timeout=5, cwd=workspace
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_calibration_summary(workspace) -> dict:
    """Read calibration summary from .breadcrumbs.yaml."""
    calibration = {}
    try:
        from pathlib import Path

        import yaml

        bc_path = Path(workspace) / ".breadcrumbs.yaml"
        if bc_path.exists():
            with open(bc_path) as f:
                bc = yaml.safe_load(f) or {}
            cal = bc.get("calibration", {})
            if cal:
                calibration["observations"] = cal.get("observations", 0)
            gcal = bc.get("grounded_calibration", {})
            if gcal:
                calibration["grounded_observations"] = gcal.get("total_observations", 0)
                calibration["grounded_score"] = gcal.get("latest_score")
                calibration["grounded_coverage"] = gcal.get("latest_coverage")
    except Exception:
        pass
    return calibration


def _get_transcript_import_stats() -> dict:
    """Get transcript import statistics from SQLite."""
    import_stats: dict[str, Any] = {"total": 0, "by_source": {}, "by_type": {}}
    try:
        from empirica.data.session_database import SessionDatabase as _DB2

        db2 = _DB2()
        try:
            c2 = db2.conn.cursor()
            for table, artifact_type, data_col in [
                ("project_findings", "findings", "finding_data"),
                ("project_unknowns", "unknowns", "unknown_data"),
                ("project_dead_ends", "dead_ends", "dead_end_data"),
                ("mistakes_made", "mistakes", "mistake_data"),
            ]:
                try:
                    c2.execute(f"""
                        SELECT {data_col} FROM {table}
                        WHERE {data_col} LIKE '%"extraction_confidence"%'
                    """)
                    for row in c2.fetchall():
                        try:
                            data = json.loads(row[0]) if row[0] else {}
                            source = data.get("source", "unknown")
                            import_stats["total"] += 1
                            import_stats["by_source"][source] = import_stats["by_source"].get(source, 0) + 1
                            import_stats["by_type"][artifact_type] = import_stats["by_type"].get(artifact_type, 0) + 1
                        except (json.JSONDecodeError, TypeError):
                            pass
                except Exception:
                    pass
        finally:
            db2.close()
    except Exception:
        pass
    return import_stats


def _detect_notes_sqlite_drift(artifact_counts, notes_counts):
    """Detect drift between git notes and SQLite artifact counts."""
    drift = {}
    for artifact_type in ["findings", "unknowns", "dead_ends", "mistakes", "goals"]:
        notes_count = notes_counts.get(artifact_type, 0)
        sqlite_count = artifact_counts.get(artifact_type, 0)
        if notes_count > 0 and sqlite_count >= 0:
            diff = notes_count - sqlite_count
            if diff != 0:
                drift[artifact_type] = {"notes": notes_count, "sqlite": sqlite_count, "delta": diff}
    return drift


def _print_correction_record(rec: dict) -> None:
    """Render the wrong-vs-stale split, and say so when zero stops being plausible."""
    if not rec or not rec.get("resolved"):
        return

    from empirica.data.resolution_kind import is_retraction

    resolved = rec["resolved"]
    by_kind = rec.get("by_kind", {})
    retracted = sum(n for k, n in by_kind.items() if is_retraction(k))
    unclassified = rec.get("unclassified", 0)

    print(f"\n  Correction record ({resolved} findings resolved):")
    for kind in ("stale", "superseded", "retracted", "mistyped"):
        if by_kind.get(kind):
            print(f"    {kind}: {by_kind[kind]}")
    if unclassified:
        print(f"    unclassified (pre-061 or omitted --kind): {unclassified}")
    print(f"    supersessions LINKED (not just narrated): {rec.get('linked_supersessions', 0)}")

    if retracted == 0 and resolved >= _RETRACTION_PLAUSIBILITY_FLOOR:
        print(
            f"    ⚠️  {resolved} resolutions, 0 recorded as WRONG. A true error rate near zero\n"
            "        is implausible at this volume — more likely retraction is not being\n"
            "        reached for. Use: finding-resolve <id> --kind retracted"
        )


def _print_profile_status_pretty(
    artifact_counts,
    notes_counts,
    import_stats,
    drift,
    sync_config,
    remote,
    sync_available,
    calibration,
    correction=None,
    ceremony=None,
):
    """Print human-readable profile status output."""
    print("📊 Epistemic Profile Status")
    print("=" * 50)

    total_sqlite = sum(v for v in artifact_counts.values() if isinstance(v, int) and v > 0)
    total_notes = sum(notes_counts.values())
    print(f"\n  Artifacts (SQLite): {total_sqlite}")
    for name, count in artifact_counts.items():
        if count > 0 and name not in ("unknowns_resolved", "sessions", "snapshots"):
            print(f"    {name}: {count}")
    if artifact_counts.get("unknowns_resolved", 0) > 0:
        print(f"    unknowns (resolved): {artifact_counts['unknowns_resolved']}")
    print(f"    sessions: {artifact_counts.get('sessions', 0)}")
    print(f"    snapshots: {artifact_counts.get('snapshots', 0)}")

    _print_correction_record(correction or {})
    _print_ceremony_record(ceremony or {})

    print(f"\n  Artifacts (Git Notes): {total_notes}")
    for name, count in notes_counts.items():
        if count > 0:
            print(f"    {name}: {count}")

    if import_stats["total"] > 0:
        print(f"\n  Transcript Imports: {import_stats['total']}")
        for src, count in import_stats["by_source"].items():
            print(f"    source={src}: {count}")
        for atype, count in import_stats["by_type"].items():
            print(f"    {atype}: {count}")

    if drift:
        print("\n  ⚠️  Drift detected (notes - sqlite):")
        for artifact_type, info in drift.items():
            print(f"    {artifact_type}: {info['delta']:+d} (notes={info['notes']}, sqlite={info['sqlite']})")
        print("    Run 'empirica profile-sync --import-only' to reconcile")

    print(
        f"\n  Sync: {'enabled' if sync_config.get('enabled') else 'disabled'}, "
        f"remote={remote} ({'configured' if sync_available else 'NOT configured'})"
    )

    if calibration:
        print("\n  Calibration:")
        if "observations" in calibration:
            print(f"    Self-referential observations: {calibration['observations']}")
        if "grounded_score" in calibration:
            print(f"    Grounded score: {calibration.get('grounded_score', 'N/A')}")
            print(f"    Grounded coverage: {calibration.get('grounded_coverage', 'N/A')}")


def handle_profile_status_command(args):
    """Handle profile-status command — unified profile view."""
    try:
        output_format = getattr(args, "output", "json")
        sync_config = _load_sync_config()
        # Status REPORTS rather than refusing, but `remote: null` and
        # `remote: forgejo, available: false` are different facts and must not render
        # the same: the first is a destination nobody chose, the second is one that no
        # longer matches the repo.
        remote = _resolve_notes_remote(sync_config, getattr(args, "remote", None))

        artifact_counts = _get_artifact_counts()
        correction = _get_correction_record()
        ceremony = _get_ceremony_record()
        workspace = _get_workspace_root()
        notes_counts = _get_git_notes_counts(workspace)
        sync_available = _check_sync_available(workspace, remote)
        calibration = _get_calibration_summary(workspace)
        import_stats = _get_transcript_import_stats()
        drift = _detect_notes_sqlite_drift(artifact_counts, notes_counts)

        result = {
            "ok": True,
            "artifacts": {"sqlite": artifact_counts, "git_notes": notes_counts},
            "correction_record": correction or None,
            "check_record": ceremony or None,
            "transcript_imports": import_stats if import_stats["total"] > 0 else None,
            "drift": drift if drift else None,
            "sync": {"remote": remote, "available": sync_available, "enabled": sync_config.get("enabled", True)},
            "calibration": calibration if calibration else None,
        }

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_profile_status_pretty(
                artifact_counts,
                notes_counts,
                import_stats,
                drift,
                sync_config,
                remote,
                sync_available,
                calibration,
                correction=correction,
                ceremony=ceremony,
            )

        return 0

    except Exception as e:
        handle_cli_error(e, "Profile status", getattr(args, "verbose", False))
        return 1


def _print_import_dry_run(
    all_results, totals, total_artifacts, source, sessions_scanned, min_confidence, output_format
):
    """Print dry-run import report."""
    report = {
        "ok": True,
        "dry_run": True,
        "source": source,
        "sessions_scanned": sessions_scanned,
        "artifacts_found": totals,
        "total": total_artifacts,
        "min_confidence": min_confidence,
    }
    samples = {}
    for r in all_results:
        if r.findings and "finding" not in samples:
            samples["finding"] = {
                "text": r.findings[0].finding,
                "confidence": r.findings[0].confidence,
                "impact": r.findings[0].impact,
            }
        if r.decisions and "decision" not in samples:
            samples["decision"] = {"choice": r.decisions[0].choice, "confidence": r.decisions[0].confidence}
        if r.dead_ends and "dead_end" not in samples:
            samples["dead_end"] = {"approach": r.dead_ends[0].approach, "confidence": r.dead_ends[0].confidence}
    if samples:
        report["samples"] = samples
    if output_format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(f"\n📋 Dry run: {total_artifacts} artifacts found from {sessions_scanned} session(s)")
        for atype, count in totals.items():
            if count > 0:
                print(f"   {atype}: {count}")
        if samples:
            print("\n   Sample artifacts:")
            for atype, sample in samples.items():
                text = sample.get("text", sample.get("choice", sample.get("approach", "")))
                print(f"   [{atype}] {text[:80]}... (confidence={sample['confidence']})")
    return 0


def _import_from_claude_code(
    SessionIndex,
    TranscriptParser,
    ArtifactExtractor,
    project_name,
    session_id_filter,
    include_sidechains,
    min_confidence,
    dry_run,
    output_format,
):
    """Import artifacts from Claude Code transcripts. Returns (results, count) or int exit code."""
    session_index = SessionIndex()
    parser = TranscriptParser()
    sessions = (
        session_index.get_sessions(project_name) if project_name else session_index.get_all_sessions(min_messages=3)
    )
    if session_id_filter:
        sessions = [s for s in sessions if s.session_id == session_id_filter]
    if not sessions:
        msg = f"No sessions found{f' in project {project_name!r}' if project_name else ''}"
        if output_format == "json":
            print(json.dumps({"ok": True, "message": msg, "sessions_scanned": 0, "artifacts": 0}))
        else:
            print(f"ℹ️  {msg}")
        return 0

    if output_format == "text" and not dry_run:
        print(f"🔍 Scanning {len(sessions)} session(s)...")

    all_results = []
    sessions_scanned = 0
    for session_meta in sessions:
        if not session_meta.full_path:
            continue
        sessions_scanned += 1
        records = parser.parse_session(session_meta.full_path)
        if not records:
            continue
        turns = list(parser.iter_conversation_turns(records, include_sidechains=include_sidechains))
        if not turns:
            continue
        extractor = ArtifactExtractor(min_confidence=min_confidence)
        result = extractor.extract_all(turns, source="claude-code", session_id=session_meta.session_id)
        if result.total_artifacts > 0:
            all_results.append(result)
            if output_format == "text" and not dry_run:
                summary = result.summary()
                parts = [
                    f"{k}={summary[k]}"
                    for k in ["findings", "decisions", "dead_ends", "mistakes", "unknowns"]
                    if summary[k] > 0
                ]
                if parts:
                    print(f"   {session_meta.summary or session_meta.session_id[:12]}: {', '.join(parts)}")
    return all_results, sessions_scanned


def _import_from_claude_ai(ClaudeAIParser, ArtifactExtractor, file_path, min_confidence, dry_run, output_format):
    """Import artifacts from Claude.ai export. Returns (results, count) or int exit code."""
    if not file_path:
        if output_format == "json":
            print(json.dumps({"ok": False, "error": "--file required for --source claude-ai"}))
        else:
            print("❌ --file required for --source claude-ai")
        return 1

    ai_parser = ClaudeAIParser()
    turns, metadata = ai_parser.parse_export(file_path)
    if not turns:
        msg = f"No conversations found in {file_path}"
        if output_format == "json":
            print(json.dumps({"ok": True, "message": msg, "sessions_scanned": 0, "artifacts": 0}))
        else:
            print(f"ℹ️  {msg}")
        return 0

    sessions_scanned = metadata.get("conversation_count", 1)
    if output_format == "text" and not dry_run:
        print(f"🔍 Processing {len(turns)} conversation turns from Claude.ai export...")

    extractor = ArtifactExtractor(min_confidence=min_confidence)
    result = extractor.extract_all(turns, source="claude-ai")
    all_results = [result] if result.total_artifacts > 0 else []
    return all_results, sessions_scanned


def _store_extracted_artifacts(all_results):
    """Store extracted artifacts into SQLite. Returns dict of stored counts per type."""
    import uuid as uuid_mod

    from empirica.data.session_database import SessionDatabase
    from empirica.utils.session_resolver import InstanceResolver

    db = SessionDatabase()
    project_id = InstanceResolver.project_id_from_db(InstanceResolver.project_path())
    stored = {"findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0}

    try:
        cursor = db.conn.cursor()
        now_ts = datetime.now(timezone.utc).timestamp()

        for result in all_results:
            for finding in result.findings:
                try:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO project_findings
                        (id, project_id, session_id, finding, created_timestamp,
                         finding_data, impact)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            str(uuid_mod.uuid4()),
                            project_id,
                            result.session_id,
                            finding.finding,
                            now_ts,
                            json.dumps(
                                {
                                    "finding": finding.finding,
                                    "source": result.source,
                                    "extraction_confidence": finding.confidence,
                                    "source_turn": finding.source_turn,
                                }
                            ),
                            finding.impact,
                        ),
                    )
                    if cursor.rowcount > 0:
                        stored["findings"] += 1
                except Exception as e:
                    logger.debug(f"Failed to store finding: {e}")

            for unknown in result.unknowns:
                try:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO project_unknowns
                        (id, project_id, session_id, unknown, is_resolved,
                         created_timestamp, unknown_data, impact)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            str(uuid_mod.uuid4()),
                            project_id,
                            result.session_id,
                            unknown.unknown,
                            False,
                            now_ts,
                            json.dumps(
                                {
                                    "unknown": unknown.unknown,
                                    "source": result.source,
                                    "extraction_confidence": unknown.confidence,
                                }
                            ),
                            0.5,
                        ),
                    )
                    if cursor.rowcount > 0:
                        stored["unknowns"] += 1
                except Exception as e:
                    logger.debug(f"Failed to store unknown: {e}")

            for dead_end in result.dead_ends:
                try:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO project_dead_ends
                        (id, project_id, session_id, approach, why_failed,
                         created_timestamp, dead_end_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            str(uuid_mod.uuid4()),
                            project_id,
                            result.session_id,
                            dead_end.approach,
                            dead_end.why_failed,
                            now_ts,
                            json.dumps({"source": result.source, "extraction_confidence": dead_end.confidence}),
                        ),
                    )
                    if cursor.rowcount > 0:
                        stored["dead_ends"] += 1
                except Exception as e:
                    logger.debug(f"Failed to store dead end: {e}")

            for mistake in result.mistakes:
                try:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO mistakes_made
                        (id, project_id, session_id, mistake, why_wrong,
                         prevention, created_timestamp, mistake_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            str(uuid_mod.uuid4()),
                            project_id,
                            result.session_id,
                            mistake.mistake,
                            mistake.why_wrong,
                            mistake.prevention,
                            now_ts,
                            json.dumps({"source": result.source, "extraction_confidence": mistake.confidence}),
                        ),
                    )
                    if cursor.rowcount > 0:
                        stored["mistakes"] += 1
                except Exception as e:
                    logger.debug(f"Failed to store mistake: {e}")

        db.conn.commit()
    finally:
        db.close()

    return stored


def handle_profile_import_command(args):
    """Handle profile-import command — mine AI transcripts for epistemic artifacts."""
    try:
        source = getattr(args, "source", "claude-code")
        project_name = getattr(args, "project", None)
        file_path = getattr(args, "file", None)
        session_id_filter = getattr(args, "session", None)
        min_confidence = getattr(args, "min_confidence", 0.5)
        dry_run = getattr(args, "dry_run", False)
        include_sidechains = getattr(args, "include_sidechains", False)
        output_format = getattr(args, "output", "text")

        from empirica.core.canonical.artifact_extractor import ArtifactExtractor
        from empirica.core.canonical.transcript_parser import ClaudeAIParser, SessionIndex, TranscriptParser

        all_results = []
        sessions_scanned = 0

        if source == "claude-code":
            result = _import_from_claude_code(
                SessionIndex,
                TranscriptParser,
                ArtifactExtractor,
                project_name,
                session_id_filter,
                include_sidechains,
                min_confidence,
                dry_run,
                output_format,
            )
            if isinstance(result, int):
                return result  # Early exit (no sessions found)
            all_results, sessions_scanned = result

        elif source == "claude-ai":
            result = _import_from_claude_ai(
                ClaudeAIParser, ArtifactExtractor, file_path, min_confidence, dry_run, output_format
            )
            if isinstance(result, int):
                return result
            all_results, sessions_scanned = result

        # Aggregate results
        totals = {
            "findings": sum(len(r.findings) for r in all_results),
            "decisions": sum(len(r.decisions) for r in all_results),
            "dead_ends": sum(len(r.dead_ends) for r in all_results),
            "mistakes": sum(len(r.mistakes) for r in all_results),
            "unknowns": sum(len(r.unknowns) for r in all_results),
        }
        total_artifacts = sum(totals.values())

        if dry_run:
            return _print_import_dry_run(
                all_results, totals, total_artifacts, source, sessions_scanned, min_confidence, output_format
            )

        # Actually store artifacts
        if total_artifacts == 0:
            msg = f"No artifacts found above confidence {min_confidence}"
            if output_format == "json":
                print(json.dumps({"ok": True, "message": msg, "sessions_scanned": sessions_scanned, "artifacts": 0}))
            else:
                print(f"ℹ️  {msg}")
            return 0

        stored = _store_extracted_artifacts(all_results)
        total_stored = sum(stored.values())
        report = {
            "ok": True,
            "source": source,
            "sessions_scanned": sessions_scanned,
            "artifacts_found": total_artifacts,
            "artifacts_stored": stored,
            "total_stored": total_stored,
            "duplicates_skipped": total_artifacts - total_stored,
        }

        if output_format == "json":
            print(json.dumps(report, indent=2))
        else:
            print(f"\n✅ Imported {total_stored} artifacts from {sessions_scanned} session(s)")
            for artifact_type, count in stored.items():
                if count > 0:
                    print(f"   {artifact_type}: {count}")
            skipped = total_artifacts - total_stored
            if skipped > 0:
                print(f"   ({skipped} duplicates skipped)")

        return 0

    except Exception as e:
        handle_cli_error(e, "Profile import", getattr(args, "verbose", False))
        return 1
