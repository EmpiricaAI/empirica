"""
Artifact Log Commands - Noetic artifact logging (findings, unknowns, dead-ends, etc.)

Split from project_commands.py for maintainability.
"""

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error
from .project_commands import get_workspace_db_path


def _suggest_links_safe(project_id: str | None, text: str, exclude_id: str) -> list[dict]:
    """Wrapper that never raises — returns [] on any failure path."""
    if not project_id or not text or not exclude_id:
        return []
    try:
        from empirica.core.bootstrap import suggest_links_for_artifact

        return suggest_links_for_artifact(project_id, text, exclude_id)
    except Exception as e:
        logger.debug(f"_suggest_links_safe: {e}")
        return []


logger = logging.getLogger(__name__)


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_URL_RE = re.compile(r"https?://\S+", re.I)


def _is_uuid(s: str) -> bool:
    """Check if a string looks like a UUID."""
    return bool(_UUID_RE.match(s))


# ── Source-provenance nudge (content-aware) ──
#
# Prior nudge surfaces (CHECK reminder, POSTFLIGHT retrospective, system prompt)
# all proved ineffective — adoption check on 2026-05-11 returned 0/50 of
# decisions and 0/50 of findings with source_refs populated. The pattern of
# prospective generic nudges fails because the nudge fires far from the
# moment of artifact creation and doesn't cite anything specific.
#
# This nudge fires AT the moment of *-log invocation, when the artifact text
# itself shows external citation (currently: URLs) but no source flag is
# provided. The warning names the detected pattern and suggests the exact
# remediation. Non-blocking — the artifact still gets logged.


def _detect_external_citations(text: str | None) -> list[str]:
    """Return short descriptions of external citations detected in `text`.

    Currently detects HTTP(S) URLs. Returns up to 3 detected patterns;
    returns empty list when text is None/empty or contains nothing flagged.

    URL detection is conservative — only proper protocol prefixes count.
    Cross-repo file path detection is intentionally out of scope for v1
    (too much false-positive noise from stack traces, log paths, etc).
    """
    if not text:
        return []
    found: list[str] = []
    for match in _URL_RE.findall(text)[:3]:
        # Strip common trailing punctuation that's almost never part of a URL
        cleaned = match.rstrip(".,;:)]'\"")
        found.append(f"URL {cleaned}")
    return found


def _has_explicit_source(args) -> bool:
    """True if the user provided any explicit provenance flag.

    Counts: --source (source_ids), --evidence (evidence_refs), or any
    --epistemic-source other than 'intuition'. The intuition tag is a
    declaration of "no external source", so it counts as honest absence
    rather than provenance.
    """
    if getattr(args, "source_ids", None):
        return True
    if getattr(args, "evidence_refs", None):
        return True
    epistemic_source = getattr(args, "epistemic_source", None)
    return bool(epistemic_source) and epistemic_source != "intuition"


def _warn_unsourced_citations_if_needed(args, *texts: str | None) -> None:
    """Emit a stderr nudge when artifact text shows external citation but
    no provenance flag is set. Non-blocking; the artifact still logs.

    Suppressed when --output json (machine consumers don't want stderr
    noise interleaved) and when EMPIRICA_SUPPRESS_PROVENANCE_NUDGE=1.
    """
    if getattr(args, "output", "human") == "json":
        return
    import os

    if os.environ.get("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE"):
        return
    if _has_explicit_source(args):
        return

    citations: list[str] = []
    for text in texts:
        citations.extend(_detect_external_citations(text))
        if len(citations) >= 3:
            citations = citations[:3]
            break

    if not citations:
        return

    summary = "; ".join(citations)
    print(
        f"⚠ source-provenance: external citation detected ({summary}) but no "
        f"--source provided. Either:\n"
        f'   1. `empirica source-add --title "..." --url "..." --noetic` then '
        f"re-log with `--source <id>`\n"
        f"   2. Tag intent with `--epistemic-source search` to record that you "
        f"retrieved external material this session\n"
        f"   3. Pass `EMPIRICA_SUPPRESS_PROVENANCE_NUDGE=1` if this artifact "
        f"genuinely has no external origin (citation is illustrative).",
        file=sys.stderr,
    )


# ── Edge declaration (inline graph linkage) ──

# Maps artifact type → (refs/notes namespace, sql table, id column, data column)
_ARTIFACT_EDGE_TARGETS = {
    "finding": ("findings", "project_findings", "id", "finding_data"),
    "unknown": ("unknowns", "project_unknowns", "id", "unknown_data"),
    "dead_end": ("dead_ends", "project_dead_ends", "id", "dead_end_data"),
    "mistake": ("mistakes", "mistakes_made", "id", "mistake_data"),
    "assumption": ("assumptions", "assumptions", "id", "data"),
    "decision": ("decisions", "decisions", "id", "data"),
}


def _collect_edges_from_args(args, evidence_relation: str | None = None) -> list[dict]:
    """Build [{to, relation}] from --edge / --related-to / --evidence-from.

    --edge ID:RELATION canonical, --related-to ID defaults relation="related",
    --evidence-from ID (decision-only) defaults relation=evidence_relation.
    """
    edges: list[dict] = []
    for raw in getattr(args, "edges_raw", None) or []:
        raw = str(raw).strip()
        if not raw:
            continue
        if ":" in raw:
            to_id, rel = raw.split(":", 1)
            edges.append({"to": to_id.strip(), "relation": rel.strip() or "related"})
        else:
            edges.append({"to": raw, "relation": "related"})
    for to_id in getattr(args, "related_to_ids", None) or []:
        if to_id:
            edges.append({"to": str(to_id).strip(), "relation": "related"})
    if evidence_relation:
        for to_id in getattr(args, "evidence_from_ids", None) or []:
            if to_id:
                edges.append({"to": str(to_id).strip(), "relation": evidence_relation})
    return edges


def _resolve_edge_target(db, to_id: str) -> tuple[str | None, str | None]:
    """Resolve an edge ``to`` endpoint to a full artifact id, or explain why not.

    Returns ``(resolved_id, None)`` on success; ``(None, reason)`` when the
    endpoint matches no existing artifact (dangling) or a prefix is ambiguous.

    - **Exact** id match against any artifact table (or ``goals``) → use as-is.
    - Otherwise, if ``to_id`` is a hex-ish prefix (≥6 chars), **unique-prefix
      resolve** to the full id — prefixes are the natural paste form, so a short
      id becomes usable rather than a literal dangling row. Ambiguous (>1 match)
      → refuse rather than guess.

    This closes the inline ``--related-to`` / ``--edge`` path's endpoint gap: it
    used to store the raw ``to`` (a prefix or a non-existent UUID) verbatim, the
    same silent-success class #268 fixed on the graph-batch path. Best-effort:
    a query error on one table is skipped.
    """
    if not db.conn or not to_id:
        return None, "empty endpoint"
    cursor = db.conn.cursor()
    lookups = [(t[1], t[2]) for t in _ARTIFACT_EDGE_TARGETS.values()]  # (table, id_col)
    lookups.append(("goals", "id"))

    # 1. Exact match — the common case (AIs paste full UUIDs).
    for table, id_col in lookups:
        try:
            cursor.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ? LIMIT 1", (to_id,))
            if cursor.fetchone():
                return to_id, None
        except Exception:
            continue

    # 2. Unique-prefix resolution — only for hex-ish ids, so junk can't match.
    import re as _re

    if len(to_id) >= 6 and _re.fullmatch(r"[0-9a-fA-F-]{6,}", to_id):
        matches: list[str] = []
        for table, id_col in lookups:
            try:
                cursor.execute(f"SELECT {id_col} FROM {table} WHERE {id_col} LIKE ?", (to_id + "%",))
                matches.extend(r[0] for r in cursor.fetchall())
                if len(matches) > 1:
                    break
            except Exception:
                continue
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, f"ambiguous prefix '{to_id}' ({len(matches)}+ artifacts match)"

    return None, f"no artifact matches '{to_id}'"


def _persist_edges(artifact_type: str, artifact_id: str, edges: list[dict]) -> int:
    """Persist edges to SQLite data column AND patch git note JSON. Non-fatal.

    Returns count successfully wired. Order: SQLite first (graph_commands._store_edge),
    then git-note patch via read-modify-write. Idempotent — re-running with the same
    edges is a no-op since they're keyed by (artifact_id, to_id, relation) in JSON.

    Each ``to`` endpoint is resolved via ``_resolve_edge_target`` first: a prefix
    is resolved to its full UUID, and a dangling / ambiguous endpoint is SKIPPED
    (logged, not counted, and not written to either SQLite or the git note) —
    "accepted must mean applied-or-loudly-failed", the inline-flag twin of #268.
    """
    if not edges or artifact_type not in _ARTIFACT_EDGE_TARGETS:
        return 0
    wired = 0
    wired_edges: list[dict] = []  # only successfully-resolved edges reach the git note
    # 1. SQLite
    try:
        from empirica.cli.command_handlers.graph_commands import _store_edge
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        try:
            for edge in edges:
                resolved, reason = _resolve_edge_target(db, edge["to"])
                if resolved is None:
                    logger.warning(f"inline edge skipped {artifact_id}->{edge['to']} ({edge['relation']}): {reason}")
                    continue
                try:
                    _store_edge(db, artifact_id, resolved, edge["relation"])
                    wired += 1
                    wired_edges.append({"to": resolved, "relation": edge["relation"]})
                except Exception as e:
                    logger.warning(f"SQLite edge persist failed: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"_persist_edges SQLite phase failed: {e}")
        return 0
    # 2. Git note: read existing, merge the WIRED edges into <type>_data, rewrite.
    if wired_edges:
        try:
            _patch_git_note_with_edges(artifact_type, artifact_id, wired_edges)
        except Exception as e:
            logger.warning(f"_persist_edges git-note phase failed: {e}")
    return wired


def _patch_git_note_with_edges(artifact_type: str, artifact_id: str, edges: list[dict]) -> None:
    """Read note for artifact, merge edges into <type>_data, write back.

    Uses `git notes` plumbing rather than the per-type Git*Store classes, so this
    is uniform across all artifact types.
    """
    import subprocess

    from empirica.config.path_resolver import get_git_root

    namespace, _table, _id_col, _data_col = _ARTIFACT_EDGE_TARGETS[artifact_type]
    nested_key = f"{artifact_type}_data"
    workspace = get_git_root()
    if not workspace:
        return
    short_ref = f"empirica/{namespace}/{artifact_id}"

    # 1. Find annotated commit
    list_proc = subprocess.run(
        ["git", "notes", f"--ref={short_ref}", "list"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if list_proc.returncode != 0 or not list_proc.stdout.strip():
        return  # No git note to patch (e.g., GitFindingStore failed earlier)
    parts = list_proc.stdout.strip().split("\n")[0].split()
    if len(parts) < 2:
        return
    commit_sha = parts[1]

    # 2. Read note content
    show_proc = subprocess.run(
        ["git", "notes", f"--ref={short_ref}", "show", commit_sha],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if show_proc.returncode != 0:
        return
    try:
        payload = json.loads(show_proc.stdout)
    except json.JSONDecodeError:
        return

    # 3. Merge edges
    nested = payload.get(nested_key)
    if not isinstance(nested, dict):
        nested = {}
    existing_edges = nested.get("edges") or []
    if not isinstance(existing_edges, list):
        existing_edges = []
    seen = {(e.get("to"), e.get("relation")) for e in existing_edges if isinstance(e, dict)}
    for edge in edges:
        key = (edge["to"], edge["relation"])
        if key not in seen:
            existing_edges.append({"to": edge["to"], "relation": edge["relation"]})
            seen.add(key)
    nested["edges"] = existing_edges
    payload[nested_key] = nested

    # 4. Write note back (-f overwrites)
    new_json = json.dumps(payload, indent=2)
    subprocess.run(
        ["git", "notes", f"--ref={short_ref}", "add", "-f", "-m", new_json, commit_sha],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _parse_config_input(args):
    """Parse config from stdin, file, or None. Shared across all artifact handlers."""
    import os
    import sys

    from empirica.cli.cli_utils import parse_json_safely

    config_data = None
    if hasattr(args, "config") and args.config:
        if args.config == "-":
            config_data = parse_json_safely(sys.stdin.read())
        else:
            if not os.path.exists(args.config):
                print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
                sys.exit(1)
            with open(args.config) as f:
                config_data = parse_json_safely(f.read())
    return config_data


def _extract_scalar_fields(config_data, args):
    """Extract common scalar fields from config dict or CLI args.

    Also runs the visibility ladders-into-agreement check (write-time
    advisory). If ``--visibility shared/public`` is requested but the local
    mesh-sharing-agreement mirror has no agreement at the required layer,
    the visibility is downgraded with a stderr warning. Fails-open when the
    mirror is unbootstrapped — cortex enforces authoritatively on the
    consumer side. See ``empirica/core/visibility.py``.
    """
    output_format = "json" if config_data else getattr(args, "output", "json")
    session_id = (config_data or {}).get("session_id") or getattr(args, "session_id", None)
    project_id = (config_data or {}).get("project_id") or getattr(args, "project_id", None)
    goal_id = (config_data or {}).get("goal_id") or getattr(args, "goal_id", None)
    # CLI flag is --task-id (args.task_id); JSON config keeps 'subtask_id' as the
    # internal artifact field name. Both routes land in the same internal variable.
    subtask_id = (config_data or {}).get("subtask_id") or getattr(args, "task_id", None)
    impact = (config_data or {}).get("impact") or getattr(args, "impact", None)
    intended_visibility = (config_data or {}).get("visibility") or getattr(args, "visibility", None)

    from empirica.core.visibility import resolve_visibility_with_agreement

    visibility, warning = resolve_visibility_with_agreement(intended_visibility)
    if warning:
        print(warning, file=sys.stderr)

    return output_format, session_id, project_id, goal_id, subtask_id, impact, visibility


def _resolve_session_for_artifact(session_id, project_id):
    """Auto-derive session_id and detect cross-project writes.

    Returns (session_id, is_cross_project).
    """
    if not session_id:
        session_id = R.session_id()

    is_cross_project = False
    if project_id:
        try:
            current_path = R.project_path()
            current_project_id = R.project_id_from_db(current_path) if current_path else None
            is_cross_project = current_project_id is None or project_id != current_project_id
        except Exception:
            is_cross_project = True

    if not session_id and is_cross_project:
        session_id = "cross-project"

    return session_id, is_cross_project


def _validate_artifact_required_fields(config_data, args, session_id, required_fields):
    """Validate that session_id and all required fields are present.

    Prints an error and exits if validation fails.
    """
    import sys

    if not required_fields:
        return

    missing = [f for f in required_fields if not ((config_data or {}).get(f) or getattr(args, f, None))]
    if not session_id or missing:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Missing required: {', '.join(['session_id'] + missing) if not session_id else ', '.join(missing)}",
                    "hint": "Either run PREFLIGHT first, or provide --session-id explicitly",
                }
            )
        )
        sys.exit(1)


def _resolve_subject_for_artifact(config_data, args):
    """Resolve artifact subject from config, args, or project config."""
    subject = (config_data or {}).get("subject") or getattr(args, "subject", None)
    if subject is None:
        try:
            from empirica.config.project_config_loader import get_current_subject

            subject = get_current_subject()
        except Exception:
            pass
    return subject


def _resolve_project_id_for_artifact(project_id, session_id, db):
    """Resolve project_id via cascading fallbacks after DB is known.

    Falls back through: session lookup -> R.context() -> project_resolver -> hash.
    """
    if not project_id and session_id:
        try:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row["project_id"]:
                project_id = row["project_id"]
        except Exception:
            pass

    if not project_id:
        try:
            ctx = R.context()
            if ctx and ctx.get("project_id"):
                project_id = ctx["project_id"]
        except Exception:
            pass

    if not project_id:
        try:
            from empirica.cli.utils.project_resolver import resolve_project_id

            project_id = resolve_project_id(project_id, db)
        except Exception:
            pass

    if not project_id and session_id:
        import hashlib

        project_id = hashlib.md5(f"session-{session_id}".encode()).hexdigest()

    return project_id


def _resolve_goal_for_artifact(goal_id, session_id, db):
    """Auto-link to the most recent open goal for this session."""
    if not goal_id and session_id:
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT id FROM goals WHERE session_id = ? AND is_completed = 0 ORDER BY created_timestamp DESC LIMIT 1",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                goal_id = row["id"] if hasattr(row, "keys") else row[0]
        except Exception:
            pass
    return goal_id


def _resolve_transaction_id_for_artifact():
    """Resolve the current transaction ID, returning None on failure."""
    try:
        return R.transaction_id()
    except Exception:
        return None


def _resolve_ai_id_for_artifact(session_id, db):
    """Look up ai_id from the session record, defaulting to 'claude-code'."""
    ai_id = "claude-code"
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            val = row["ai_id"] if hasattr(row, "keys") else row[0]
            if val:
                ai_id = val
    except Exception:
        pass
    return ai_id


def _resolve_entity_defaults(entity_type, entity_id, project_id):
    """Apply defaults: entity_type falls back to 'project', entity_id to project_id."""
    resolved_entity_type = entity_type or "project"
    resolved_entity_id = entity_id or (project_id if resolved_entity_type == "project" else None)
    return resolved_entity_type, resolved_entity_id


def _resolve_artifact_context(config_data, args, required_fields=None) -> dict[str, Any]:
    """Resolve common context needed by all artifact handlers.

    Consolidates session resolution, project resolution, entity params,
    transaction ID, goal auto-link, ai_id lookup, and subject detection
    into a single call. Each handler previously did this independently.

    Returns dict with: session_id, project_id, goal_id, transaction_id,
    entity_type, entity_id, via, ai_id, subject, output_format, db.
    Caller is responsible for closing db.
    """
    output_format, session_id, project_id, goal_id, subtask_id, impact, visibility = _extract_scalar_fields(
        config_data, args
    )
    entity_type, entity_id, via = _extract_entity_params(config_data, args)
    session_id, is_cross_project = _resolve_session_for_artifact(session_id, project_id)
    _validate_artifact_required_fields(config_data, args, session_id, required_fields)
    subject = _resolve_subject_for_artifact(config_data, args)
    db, project_id = _resolve_db_for_artifact(project_id)
    project_id = _resolve_project_id_for_artifact(project_id, session_id, db)
    goal_id = _resolve_goal_for_artifact(goal_id, session_id, db)
    transaction_id = _resolve_transaction_id_for_artifact()
    ai_id = _resolve_ai_id_for_artifact(session_id, db)
    resolved_entity_type, resolved_entity_id = _resolve_entity_defaults(entity_type, entity_id, project_id)

    return {
        "session_id": session_id,
        "project_id": project_id,
        "goal_id": goal_id,
        "subtask_id": subtask_id,
        "impact": impact,
        "subject": subject,
        "output_format": output_format,
        "entity_type": resolved_entity_type,
        "entity_id": resolved_entity_id,
        "via": via,
        "transaction_id": transaction_id,
        "ai_id": ai_id,
        "db": db,
        "is_cross_project": is_cross_project,
        "visibility": visibility,
    }


def _resolve_db_for_artifact(project_id: str | None):
    """Resolve the correct SessionDatabase for artifact writing.

    If project_id is a project name (not UUID), attempts cross-project
    write by resolving the target project's DB. Falls back to local DB.

    Returns (db, resolved_project_id) tuple.
    """
    from empirica.data.session_database import SessionDatabase

    if project_id and not _is_uuid(project_id):
        cross_db = _get_db_for_project(project_id)
        if cross_db:
            # Resolve the name to UUID in the target DB
            resolved = cross_db.resolve_project_id(project_id)
            logger.info(f"Cross-project write: targeting '{project_id}' → {resolved[:8] if resolved else '?'}...")
            return cross_db, resolved
        else:
            logger.warning(f"Could not resolve project '{project_id}' for cross-project write, using local DB")

    return SessionDatabase(), project_id


def _get_db_for_project(project_name_or_id: str):
    """Get SessionDatabase for a specific project by name or UUID.

    Resolves project → trajectory_path (from workspace.db) → sessions.db.
    Used for cross-project artifact writing without project-switch.

    Args:
        project_name_or_id: Project name (e.g., "empirica-cortex") or UUID

    Returns:
        SessionDatabase instance connected to the target project's DB,
        or None if the project can't be resolved.
    """
    import sqlite3

    from empirica.data.session_database import SessionDatabase

    workspace_db = get_workspace_db_path()
    if not workspace_db.exists():
        return None

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try by name first, then by UUID
        cursor.execute(
            "SELECT trajectory_path FROM global_projects WHERE name = ? OR id = ?",
            (project_name_or_id, project_name_or_id),
        )
        row = cursor.fetchone()
        conn.close()

        if not row or not row["trajectory_path"]:
            return None

        trajectory_path = row["trajectory_path"]
        # trajectory_path may point to .empirica/ dir or project root
        if trajectory_path.endswith(".empirica"):
            db_path = Path(trajectory_path) / "sessions" / "sessions.db"
        else:
            db_path = Path(trajectory_path) / ".empirica" / "sessions" / "sessions.db"

        if not db_path.exists():
            logger.warning(f"Cross-project DB not found: {db_path}")
            return None

        return SessionDatabase(db_path=str(db_path))

    except Exception as e:
        logger.warning(f"Failed to resolve project DB for '{project_name_or_id}': {e}")
        return None


def _create_entity_artifact_link(
    artifact_type: str,
    artifact_id: str,
    entity_type: str,
    entity_id: str,
    project_path: str | None = None,
    discovered_via: str | None = None,
    transaction_id: str | None = None,
    engagement_id: str | None = None,
):
    """Create cross-reference in workspace.db entity_artifacts table.

    Called after artifact insert when entity_type is not 'project'.
    Links artifacts in sessions.db to entities (org, contact, engagement)
    in workspace.db for cross-entity discovery.
    """
    if not entity_type or entity_type == "project":
        return  # No cross-link needed for project-scoped artifacts

    import time
    import uuid

    workspace_db = get_workspace_db_path()
    if not workspace_db.exists():
        logger.debug("Workspace DB not found, skipping entity_artifacts link")
        return

    # Resolve artifact_source (trajectory_path for this project)
    if not project_path:
        try:
            project_path = R.project_path()
        except Exception:
            pass

    # artifact_source = trajectory_path (.empirica dir), NOT full sessions.db path
    # EntityArtifactStore._populate_content() appends /sessions/sessions.db
    artifact_source = str(Path(project_path) / ".empirica") if project_path else None

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.execute(
            """
            INSERT OR IGNORE INTO entity_artifacts (
                id, artifact_type, artifact_id, artifact_source,
                entity_type, entity_id, relationship, relevance,
                discovered_via, engagement_id, transaction_id,
                created_at, created_by_ai
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                str(uuid.uuid4()),
                artifact_type,
                artifact_id,
                artifact_source,
                entity_type,
                entity_id,
                "about",  # default relationship
                1.0,
                discovered_via,
                engagement_id,
                transaction_id,
                time.time(),
                "claude-code",
            ),
        )
        conn.commit()
        conn.close()
        logger.info(f"🔗 Entity artifact linked: {artifact_type} → {entity_type}/{entity_id[:8]}...")
    except Exception as e:
        logger.debug(f"Entity artifact link failed (non-fatal): {e}")


def _extract_entity_params(config_data, args):
    """Extract entity_type, entity_id, via from config or CLI args.

    Falls back to active engagement if no entity explicitly specified.
    Returns (entity_type, entity_id, via) tuple.
    """
    if config_data:
        entity_type = config_data.get("entity_type")
        entity_id = config_data.get("entity_id")
        via = config_data.get("via")
    else:
        entity_type = getattr(args, "entity_type", None)
        entity_id = getattr(args, "entity_id", None)
        via = getattr(args, "via", None)

    # Auto-inherit from active engagement if no entity explicitly specified
    if not entity_type or entity_type == "project":
        try:
            active_eng = R.engagement()
            if active_eng:
                entity_type = "engagement"
                entity_id = active_eng
        except Exception:
            pass

    return entity_type, entity_id, via


def handle_engagement_focus_command(args):
    """Handle engagement-focus command — set active engagement for auto-linking."""
    try:
        from empirica.utils.session_resolver import set_active_engagement

        if getattr(args, "clear", False):
            # Clear engagement by setting to None
            tx_data = R.transaction_read()
            if tx_data and tx_data.get("active_engagement"):
                import os
                import tempfile
                from pathlib import Path

                tx_data.pop("active_engagement", None)
                tx_data["updated_at"] = __import__("time").time()

                # Find the transaction file path (same logic as set_active_engagement)
                suffix = R.instance_suffix()
                project_path = R.project_path()
                if project_path:
                    tx_path = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
                else:
                    tx_path = Path.home() / ".empirica" / f"active_transaction{suffix}.json"

                tmp_fd, tmp_path = tempfile.mkstemp(dir=str(tx_path.parent))
                try:
                    with os.fdopen(tmp_fd, "w") as tmp_f:
                        json.dump(tx_data, tmp_f, indent=2)
                    os.replace(tmp_path, str(tx_path))
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

                print(json.dumps({"ok": True, "action": "cleared"}))
            else:
                print(json.dumps({"ok": True, "action": "no_engagement_set"}))
            return

        engagement_id = getattr(args, "engagement_id", None)
        if not engagement_id:
            print(json.dumps({"ok": False, "error": "engagement_id required"}))
            return

        ok = set_active_engagement(engagement_id)
        if ok:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "engagement_id": engagement_id,
                        "message": f"Engagement focused: {engagement_id}. All artifacts will auto-link.",
                    }
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No active transaction. Run PREFLIGHT first.",
                    }
                )
            )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


def _store_finding_git_notes(
    finding_id, project_id, session_id, ai_id, finding, impact, goal_id, subtask_id, subject
) -> bool:
    """Store finding in git notes (canonical source). Non-fatal on failure."""
    try:
        from empirica.core.canonical.empirica_git.finding_store import GitFindingStore

        stored = GitFindingStore().store_finding(
            finding_id=finding_id,
            project_id=project_id,
            session_id=session_id,
            ai_id=ai_id,
            finding=finding,
            impact=impact,
            goal_id=goal_id,
            subtask_id=subtask_id,
            subject=subject,
        )
        if stored:
            logger.info(f"✓ Finding {finding_id[:8]} stored in git notes")
        return stored
    except Exception as e:
        logger.warning(f"Git notes storage failed: {e}")
        return False


def _embed_finding_qdrant(project_id, finding_id, finding, session_id, goal_id, subtask_id, subject, impact) -> bool:
    """Auto-embed finding to Qdrant for semantic search. Non-fatal on failure."""
    if not (project_id and finding_id):
        return False
    try:
        from datetime import datetime

        from empirica.core.qdrant.vector_store import embed_single_memory_item

        return embed_single_memory_item(
            project_id=project_id,
            item_id=finding_id,
            text=finding,
            item_type="finding",
            session_id=session_id,
            goal_id=goal_id,
            subtask_id=subtask_id,
            subject=subject,
            impact=impact,
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        logger.warning(f"Auto-embed failed: {e}")
        return False


def _ingest_finding_eidetic(project_id, finding_id, finding, subject, impact, session_id):
    """Add finding to eidetic layer for confidence tracking. Returns 'created'|'confirmed'|None."""
    if not (project_id and finding_id):
        return None
    try:
        import hashlib

        from empirica.core.qdrant.vector_store import confirm_eidetic_fact, embed_eidetic

        content_hash = hashlib.md5(finding.encode()).hexdigest()
        if confirm_eidetic_fact(project_id, content_hash, session_id):
            return "confirmed"
        if embed_eidetic(
            project_id=project_id,
            fact_id=finding_id,
            content=finding,
            fact_type="fact",
            domain=subject,
            confidence=0.5 + ((impact or 0.5) * 0.2),
            confirmation_count=1,
            source_sessions=[session_id] if session_id else [],
            source_findings=[finding_id],
            tags=[subject] if subject else [],
        ):
            return "created"
    except Exception as e:
        logger.warning(f"Eidetic ingestion failed: {e}")
    return None


def _decay_related_lessons(finding, subject, project_id) -> list:
    """Immune-system glue for finding-log → lesson decay. DISABLED (see below)."""
    # DISABLED 2026-05-28 (converged w/ cortex, decay thread prop_j7y7f4): the
    # underlying LessonStorageManager.decay_related_lessons fires on keyword
    # overlap (>=2 shared keywords), not actual contradiction — so a CONFIRMATORY
    # finding decayed the lesson it confirmed (autoimmune erosion of the knowledge
    # it should reinforce). Stop the erosion now; the machinery in storage.py stays
    # intact and gets re-wired here gated on a real opposition predicate (goal
    # 98055360 P2). Until then finding-log must NOT auto-decay lessons.
    return []


def _decay_eidetic_by_finding(project_id, finding, subject) -> int:
    """Immune-system glue for finding-log → eidetic decay. DISABLED (see below)."""
    # DISABLED 2026-05-28 (converged w/ cortex, decay thread prop_j7y7f4):
    # qdrant.decay.decay_eidetic_by_finding decays on cosine similarity >= 0.85
    # with no opposition check — a confirmatory finding decayed the fact it
    # confirmed (inverse of confirm->raise). Stop the erosion now; the machinery
    # in qdrant/decay.py stays intact and gets re-wired here gated on a real
    # opposition predicate (goal 98055360 P2). Until then finding-log must NOT
    # auto-decay eidetic facts.
    return 0


def handle_finding_log_command(args):
    """Handle finding-log command - AI-first with config file support"""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=["finding"])
        db = ctx["db"]

        # Extract finding-specific fields
        finding = (config_data or {}).get("finding") or getattr(args, "finding", None)
        # Optional rich markdown body (renders in extension + skill surfaces)
        description = (config_data or {}).get("description") or getattr(args, "description", None)

        # Show project context (quiet mode)
        if ctx["output_format"] != "json":
            from empirica.cli.cli_utils import print_project_context

            print_project_context(quiet=True)

        # Extract source IDs (from --source flags or config)
        source_ids = list((config_data or {}).get("source_ids") or getattr(args, "source_ids", None) or [])
        # Inline citation: --cite creates the source AND links it in one call (no
        # separate source-add step). Prepend the fresh id so it links like any --source.
        cite_title = (config_data or {}).get("cite_title") or getattr(args, "cite_title", None)
        if cite_title:
            try:
                new_sid = db.breadcrumbs.create_source(
                    project_id=ctx["project_id"],
                    session_id=ctx["session_id"],
                    title=cite_title,
                    url=(config_data or {}).get("cite_url") or getattr(args, "cite_url", None),
                    source_type=(config_data or {}).get("cite_type") or getattr(args, "cite_type", None) or "reference",
                    visibility=ctx["visibility"],
                    transaction_id=ctx["transaction_id"],
                )
                source_ids.insert(0, new_sid)
            except Exception as e:
                logger.debug(f"inline --cite create_source failed (non-fatal): {e}")
        # Source-aware Sentinel substrate: optional intuition|search|mixed tag
        epistemic_source = (config_data or {}).get("epistemic_source") or getattr(args, "epistemic_source", None)

        # Content-aware provenance nudge (non-blocking)
        _warn_unsourced_citations_if_needed(args, finding)

        # Store to SQLite (durable)
        finding_id = db.log_finding(
            project_id=ctx["project_id"],
            session_id=ctx["session_id"],
            finding=finding,
            goal_id=ctx["goal_id"],
            subtask_id=ctx["subtask_id"],
            subject=ctx["subject"],
            impact=ctx["impact"],
            transaction_id=ctx["transaction_id"],
            entity_type=ctx["entity_type"],
            entity_id=ctx["entity_id"],
            source_ids=source_ids,
            visibility=ctx["visibility"],
            epistemic_source=epistemic_source,
            description=description,
        )

        # Entity cross-link
        if ctx["via"] and ctx["entity_type"] != "project" and ctx["entity_id"]:
            _create_entity_artifact_link(
                artifact_type="finding",
                artifact_id=finding_id,
                entity_type=ctx["entity_type"],
                entity_id=ctx["entity_id"],
                discovered_via=ctx["via"],
                transaction_id=ctx["transaction_id"],
            )

        # Aliases for readability in unique logic below
        project_id = ctx["project_id"]
        session_id = ctx["session_id"]
        ai_id = ctx["ai_id"]
        goal_id = ctx["goal_id"]
        subtask_id = ctx["subtask_id"]
        subject = ctx["subject"]
        impact = ctx["impact"]
        output_format = ctx["output_format"]
        entity_type = ctx["entity_type"]
        entity_id = ctx["entity_id"]
        via = ctx["via"]

        db.close()
        db = None  # Prevent double-close in finally

        # Multi-layer storage: git notes → Qdrant → eidetic → immune system
        git_stored = _store_finding_git_notes(
            finding_id, project_id, session_id, ai_id, finding, impact, goal_id, subtask_id, subject
        )
        embedded = _embed_finding_qdrant(
            project_id, finding_id, finding, session_id, goal_id, subtask_id, subject, impact
        )
        eidetic_result = _ingest_finding_eidetic(project_id, finding_id, finding, subject, impact, session_id)
        decayed_lessons = _decay_related_lessons(finding, subject, project_id)
        eidetic_decayed = _decay_eidetic_by_finding(project_id, finding, subject)

        # Inline edges (--edge / --related-to)
        edges_declared = _collect_edges_from_args(args)
        edges_wired = _persist_edges("finding", finding_id, edges_declared) if edges_declared else 0

        suggested_links = _suggest_links_safe(project_id, finding, finding_id)

        result = {
            "ok": True,
            "finding_id": finding_id,
            "edges_wired": edges_wired,
            "project_id": project_id if project_id else None,
            "session_id": session_id,
            "entity_type": entity_type or "project",
            "entity_id": entity_id,
            "via": via,
            "source_ids": source_ids,
            "git_stored": git_stored,  # Git notes for sync
            "embedded": embedded,
            "eidetic": eidetic_result,  # "created" | "confirmed" | None
            "immune_decay": decayed_lessons if decayed_lessons else None,  # Lessons affected by this finding
            "eidetic_decayed": eidetic_decayed if eidetic_decayed else None,
            "suggested_links": suggested_links,
            "message": "Finding logged to project scope",
        }

        # Format output (AI-first = JSON by default)
        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output (legacy)
            print("✅ Finding logged successfully")
            print(f"   Finding ID: {finding_id}")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print("   📝 Stored in git notes for sync")
            if embedded:
                print("   🔍 Auto-embedded for semantic search")
            if decayed_lessons:
                print(f"   🛡️ IMMUNE: Decayed {len(decayed_lessons)} related lesson(s)")
                for dl in decayed_lessons:
                    print(f"      - {dl['name']}: {dl['previous_confidence']:.2f} → {dl['new_confidence']:.2f}")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Finding log", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_unknown_log_command(args):
    """Handle unknown-log command - AI-first with config file support"""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=["unknown"])
        db = ctx["db"]

        # Extract unknown-specific fields
        unknown = (config_data or {}).get("unknown") or getattr(args, "unknown", None)
        description = (config_data or {}).get("description") or getattr(args, "description", None)
        epistemic_source = (config_data or {}).get("epistemic_source") or getattr(args, "epistemic_source", None)

        # Show project context (quiet mode)
        if ctx["output_format"] != "json":
            from empirica.cli.cli_utils import print_project_context

            print_project_context(quiet=True)

        # Content-aware provenance nudge (non-blocking)
        _warn_unsourced_citations_if_needed(args, unknown)

        # Store to SQLite (durable)
        unknown_id = db.log_unknown(
            project_id=ctx["project_id"],
            session_id=ctx["session_id"],
            unknown=unknown,
            goal_id=ctx["goal_id"],
            subtask_id=ctx["subtask_id"],
            subject=ctx["subject"],
            impact=ctx["impact"],
            transaction_id=ctx["transaction_id"],
            entity_type=ctx["entity_type"],
            entity_id=ctx["entity_id"],
            visibility=ctx["visibility"],
            epistemic_source=epistemic_source,
            description=description,
        )

        # Entity cross-link
        if ctx["via"] and ctx["entity_type"] != "project" and ctx["entity_id"]:
            _create_entity_artifact_link(
                artifact_type="unknown",
                artifact_id=unknown_id,
                entity_type=ctx["entity_type"],
                entity_id=ctx["entity_id"],
                discovered_via=ctx["via"],
                transaction_id=ctx["transaction_id"],
            )

        # Aliases for readability in unique logic below
        project_id = ctx["project_id"]
        session_id = ctx["session_id"]
        ai_id = ctx["ai_id"]
        goal_id = ctx["goal_id"]
        subtask_id = ctx["subtask_id"]
        subject = ctx["subject"]
        impact = ctx["impact"]
        output_format = ctx["output_format"]

        db.close()
        db = None  # Prevent double-close in finally

        # GIT NOTES: Store unknown in git notes for sync (canonical source)
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore

            git_store = GitUnknownStore()

            git_stored = git_store.store_unknown(
                unknown_id=unknown_id,
                project_id=project_id,
                session_id=session_id,
                ai_id=ai_id,
                unknown=unknown,
                goal_id=goal_id,
                subtask_id=subtask_id,
            )
            if git_stored:
                logger.info(f"✓ Unknown {unknown_id[:8]} stored in git notes")
        except Exception as git_err:
            # Non-fatal - log but continue
            logger.warning(f"Git notes storage failed: {git_err}")

        # AUTO-EMBED: Add unknown to Qdrant for semantic search
        embedded = False
        if project_id and unknown_id:
            try:
                from datetime import datetime

                from empirica.core.qdrant.vector_store import embed_single_memory_item

                embedded = embed_single_memory_item(
                    project_id=project_id,
                    item_id=unknown_id,
                    text=unknown,
                    item_type="unknown",
                    session_id=session_id,
                    goal_id=goal_id,
                    subtask_id=subtask_id,
                    subject=subject,
                    impact=impact,
                    is_resolved=False,
                    timestamp=datetime.now().isoformat(),
                )
            except Exception as embed_err:
                # Non-fatal - log but continue
                logger.warning(f"Auto-embed failed: {embed_err}")

        # Inline edges (--edge / --related-to)
        edges_declared = _collect_edges_from_args(args)
        edges_wired = _persist_edges("unknown", unknown_id, edges_declared) if edges_declared else 0

        suggested_links = _suggest_links_safe(project_id, unknown, unknown_id)

        result = {
            "ok": True,
            "unknown_id": unknown_id,
            "edges_wired": edges_wired,
            "project_id": project_id if project_id else None,
            "session_id": session_id,
            "entity_type": ctx["entity_type"],
            "entity_id": ctx["entity_id"],
            "via": ctx["via"],
            "git_stored": git_stored,  # Git notes for sync
            "embedded": embedded,
            "suggested_links": suggested_links,
            "message": "Unknown logged to project scope",
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Unknown logged successfully")
            print(f"   Unknown ID: {unknown_id}")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print("   📝 Stored in git notes for sync")
            if embedded:
                print("   🔍 Auto-embedded for semantic search")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Unknown log", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_unknown_resolve_command(args):
    """Handle unknown-resolve command"""
    try:
        from empirica.data.session_database import SessionDatabase

        unknown_id = getattr(args, "unknown_id", None)
        resolved_by = getattr(args, "resolved_by", None)
        resolution_finding_id = getattr(args, "resolution_finding_id", None)
        output_format = getattr(args, "output", "json")

        if not unknown_id or not resolved_by:
            result = {"ok": False, "error": "unknown_id and resolved_by are required"}
            print(json.dumps(result))
            return 1

        # Resolve the unknown
        db = SessionDatabase()
        db.resolve_unknown(
            unknown_id=unknown_id,
            resolved_by=resolved_by,
            resolution_finding_id=resolution_finding_id,
        )
        db.close()

        # Format output
        result = {
            "ok": True,
            "unknown_id": unknown_id,
            "resolved_by": resolved_by,
            "resolution_finding_id": resolution_finding_id,
            "message": "Unknown resolved successfully",
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Unknown resolved successfully")
            print(f"   Unknown ID: {unknown_id[:8]}...")
            print(f"   Resolved by: {resolved_by}")
            if resolution_finding_id:
                print(f"   Linked finding: {resolution_finding_id[:8]}...")

        return 0

    except Exception as e:
        handle_cli_error(e, "Unknown resolve", getattr(args, "verbose", False))
        return 1


def handle_finding_resolve_command(args):
    """Handle finding-resolve command (#307 — the prune primitive).

    Marks a finding resolved/superseded: kept for history, dropped from live
    retrieval. Findings are the fruit that must be pluckable — recency-decay only
    knows 'old', never 'superseded'.
    """
    try:
        from empirica.data.session_database import SessionDatabase

        finding_id = getattr(args, "finding_id", None)
        resolution = getattr(args, "resolution", None)
        superseded_by = getattr(args, "superseded_by", None)
        output_format = getattr(args, "output", "json")

        if not finding_id or not resolution:
            print(json.dumps({"ok": False, "error": "finding_id and --resolution are required"}))
            return 1

        db = SessionDatabase()
        updated = db.resolve_finding(finding_id, resolution, superseded_by=superseded_by)
        db.close()

        if not updated:
            print(json.dumps({"ok": False, "error": f"no finding matched {finding_id!r} (full id or 8+ char prefix)"}))
            return 1

        result = {
            "ok": True,
            "finding_id": finding_id,
            "resolution": resolution,
            "superseded_by": superseded_by,
            "message": "Finding resolved (kept for history, dropped from live retrieval)",
        }
        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Finding resolved: {finding_id[:8]}... ({resolution})")
            if superseded_by:
                print(f"   superseded by: {superseded_by[:8]}...")
        return 0

    except Exception as e:
        handle_cli_error(e, "Finding resolve", getattr(args, "verbose", False))
        return 1


def _resolve_project_id_from_context(cursor, session_id, project_id):
    """Auto-derive project_id from session_id or active context."""
    if project_id:
        return project_id

    if session_id:
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]

    try:
        context = R.context()
        ctx_session = context.get("empirica_session_id")
        if ctx_session:
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (ctx_session,))
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass

    return None


def _print_unknowns_pretty(unknowns, status_desc, filter_desc):
    """Print unknowns list in human-readable format."""
    print(f"{'=' * 70}")
    print(f"❓ UNKNOWNS ({status_desc.upper()}) - {len(unknowns)} found [{filter_desc}]")
    print(f"{'=' * 70}")
    print()

    if not unknowns:
        print("   (No unknowns found)")
    else:
        for i, u in enumerate(unknowns, 1):
            status_emoji = "✅" if u["is_resolved"] else "❓"
            impact_str = f" [impact={u['impact']:.1f}]" if u["impact"] else ""
            print(f"{status_emoji} {i}. {u['unknown'][:75]}")
            resolved_info = f" | Resolved: {u['resolved_by'][:30]}" if u["resolved_by"] else ""
            goal_info = f" | Goal: {u['goal_id'][:8]}" if u["goal_id"] else ""
            print(f"   ID: {u['id'][:8]}...{impact_str}{goal_info}{resolved_info}")
            print()


def handle_unknown_list_command(args):
    """Handle unknown-list command - list project unknowns with optional filters.

    Unknowns are PROJECT-SCOPED. Auto-derives project_id from active context.
    """
    try:
        from empirica.data.session_database import SessionDatabase

        session_id = getattr(args, "session_id", None)
        project_id = getattr(args, "project_id", None)
        show_resolved = getattr(args, "resolved", False)
        show_all = getattr(args, "show_all", False)
        subject = getattr(args, "subject", None)
        limit = getattr(args, "limit", 30)
        output_format = getattr(args, "output", "human")

        db = SessionDatabase()
        cursor = db.conn.cursor()

        project_id = _resolve_project_id_from_context(cursor, session_id, project_id)

        # Build query
        query = """
            SELECT id, unknown, is_resolved, resolved_by, impact, subject,
                   created_timestamp, resolved_timestamp, goal_id
            FROM project_unknowns
            WHERE 1=1
        """
        params = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        if not show_all:
            if show_resolved:
                query += " AND is_resolved = 1"
            else:
                query += " AND is_resolved = 0"

        if subject:
            query += " AND subject = ?"
            params.append(subject)

        query += " ORDER BY created_timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        unknowns = [
            {
                "id": row[0],
                "unknown": row[1],
                "is_resolved": bool(row[2]),
                "resolved_by": row[3],
                "impact": row[4],
                "subject": row[5],
                "created_at": row[6],
                "resolved_at": row[7],
                "goal_id": row[8],
            }
            for row in rows
        ]

        db.close()

        filters_applied = []
        if project_id:
            filters_applied.append(f"project={project_id[:8]}...")
        if subject:
            filters_applied.append(f"subject={subject}")
        filter_desc = ", ".join(filters_applied) if filters_applied else "all"
        status_desc = "all" if show_all else ("resolved" if show_resolved else "open")

        result = {
            "ok": True,
            "unknowns_count": len(unknowns),
            "unknowns": unknowns,
            "filters": {"project_id": project_id, "status": status_desc, "subject": subject},
        }

        if output_format == "json":
            return result

        _print_unknowns_pretty(unknowns, status_desc, filter_desc)
        return None

    except Exception as e:
        handle_cli_error(e, "Unknown list", getattr(args, "verbose", False))
        return 1


def handle_deadend_log_command(args):
    """Handle deadend-log command - AI-first with config file support"""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=["approach", "why_failed"])
        db = ctx["db"]

        # Extract deadend-specific fields
        approach = (config_data or {}).get("approach") or getattr(args, "approach", None)
        why_failed = (config_data or {}).get("why_failed") or getattr(args, "why_failed", None)
        description = (config_data or {}).get("description") or getattr(args, "description", None)
        epistemic_source = (config_data or {}).get("epistemic_source") or getattr(args, "epistemic_source", None)

        # Content-aware provenance nudge (non-blocking)
        _warn_unsourced_citations_if_needed(args, approach, why_failed)

        # Store to SQLite (durable)
        dead_end_id = db.log_dead_end(
            project_id=ctx["project_id"],
            session_id=ctx["session_id"],
            approach=approach,
            why_failed=why_failed,
            goal_id=ctx["goal_id"],
            subtask_id=ctx["subtask_id"],
            subject=ctx["subject"],
            impact=ctx["impact"],
            transaction_id=ctx["transaction_id"],
            entity_type=ctx["entity_type"],
            entity_id=ctx["entity_id"],
            visibility=ctx["visibility"],
            epistemic_source=epistemic_source,
            description=description,
        )

        # Entity cross-link
        if ctx["via"] and ctx["entity_type"] != "project" and ctx["entity_id"]:
            _create_entity_artifact_link(
                artifact_type="dead_end",
                artifact_id=dead_end_id,
                entity_type=ctx["entity_type"],
                entity_id=ctx["entity_id"],
                discovered_via=ctx["via"],
                transaction_id=ctx["transaction_id"],
            )

        # Aliases for readability in unique logic below
        project_id = ctx["project_id"]
        session_id = ctx["session_id"]
        ai_id = ctx["ai_id"]
        goal_id = ctx["goal_id"]
        subtask_id = ctx["subtask_id"]
        output_format = ctx["output_format"]

        db.close()
        db = None  # Prevent double-close in finally

        # GIT NOTES: Store dead end in git notes for sync (canonical source)
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.dead_end_store import GitDeadEndStore

            git_store = GitDeadEndStore()

            git_stored = git_store.store_dead_end(
                dead_end_id=dead_end_id,
                project_id=project_id,
                session_id=session_id,
                ai_id=ai_id,
                approach=approach,
                why_failed=why_failed,
                goal_id=goal_id,
                subtask_id=subtask_id,
            )
            if git_stored:
                logger.info(f"✓ Dead end {dead_end_id[:8]} stored in git notes")
        except Exception as git_err:
            # Non-fatal - log but continue
            logger.warning(f"Git notes storage failed: {git_err}")

        # AUTO-EMBED: Add dead-end to Qdrant for semantic search
        # Without this, dead-ends are invisible to pattern_retrieval.py at CHECK
        embedded = False
        if project_id and dead_end_id:
            try:
                from datetime import datetime

                from empirica.core.qdrant.vector_store import embed_single_memory_item

                text = f"DEAD END: {approach} — Why failed: {why_failed}"
                embedded = embed_single_memory_item(
                    project_id=project_id,
                    item_id=dead_end_id,
                    text=text,
                    item_type="dead_end",
                    session_id=session_id,
                    goal_id=goal_id,
                    timestamp=datetime.now().isoformat(),
                )
            except Exception as embed_err:
                logger.warning(f"Auto-embed failed: {embed_err}")

        # Inline edges (--edge / --related-to)
        edges_declared = _collect_edges_from_args(args)
        edges_wired = _persist_edges("dead_end", dead_end_id, edges_declared) if edges_declared else 0

        suggested_links = _suggest_links_safe(project_id, f"{approach} — Why failed: {why_failed}", dead_end_id)

        result = {
            "ok": True,
            "dead_end_id": dead_end_id,
            "edges_wired": edges_wired,
            "project_id": project_id if project_id else None,
            "session_id": session_id,
            "entity_type": ctx["entity_type"],
            "entity_id": ctx["entity_id"],
            "via": ctx["via"],
            "git_stored": git_stored,
            "embedded": embedded,
            "suggested_links": suggested_links,
            "message": "Dead end logged to project scope",
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Dead end logged successfully")
            print(f"   Dead End ID: {dead_end_id[:8]}...")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print("   📝 Stored in git notes for sync")
            if embedded:
                print("   🔍 Auto-embedded for semantic search")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Dead end log", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_assumption_log_command(args):
    """Handle assumption-log command — log unverified assumptions."""
    db = None
    try:
        import time

        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=["assumption"])
        db = ctx["db"]

        # Extract assumption-specific fields
        assumption = (config_data or {}).get("assumption") or getattr(args, "assumption", None)
        # Use .get's default as the fallback chain (NOT `or`): a truthy default
        # like 0.5 would make `x or args.confidence` short-circuit and silently
        # drop the CLI flag. This form also preserves an explicit 0.0.
        confidence = (config_data or {}).get("confidence", getattr(args, "confidence", 0.5))
        domain = (config_data or {}).get("domain") or getattr(args, "domain", None)
        description = (config_data or {}).get("description") or getattr(args, "description", None)
        epistemic_source = (config_data or {}).get("epistemic_source") or getattr(args, "epistemic_source", None)

        # Content-aware provenance nudge (non-blocking)
        _warn_unsourced_citations_if_needed(args, assumption)

        # Store to SQLite (durable)
        assumption_id = db.log_assumption(
            project_id=ctx["project_id"],
            session_id=ctx["session_id"],
            assumption=assumption,
            confidence=confidence,
            domain=domain,
            goal_id=ctx["goal_id"],
            transaction_id=ctx["transaction_id"],
            entity_type=ctx["entity_type"],
            entity_id=ctx["entity_id"],
            visibility=ctx["visibility"],
            epistemic_source=epistemic_source,
            description=description,
        )

        # GIT NOTES: Store in git notes for sync
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore

            git_stored = GitAssumptionStore().store_assumption(
                assumption_id=assumption_id,
                project_id=ctx["project_id"],
                session_id=ctx["session_id"],
                ai_id=ctx["ai_id"],
                assumption=assumption,
                confidence=confidence,
                domain=domain,
                goal_id=ctx["goal_id"],
            )
        except Exception as e:
            logger.debug(f"Git notes storage failed (non-fatal): {e}")

        # Store to Qdrant (semantic search)
        embedded = False
        try:
            from empirica.core.qdrant.vector_store import _check_qdrant_available, embed_assumption

            if _check_qdrant_available():
                embed_assumption(
                    project_id=ctx["project_id"],
                    assumption_id=assumption_id,
                    assumption=assumption,
                    confidence=confidence,
                    status="unverified",
                    entity_type=ctx["entity_type"],
                    entity_id=ctx["entity_id"],
                    session_id=ctx["session_id"],
                    transaction_id=ctx["transaction_id"],
                    domain=domain,
                    timestamp=time.time(),
                )
                embedded = True
        except Exception as e:
            logger.debug(f"Qdrant embed failed (non-fatal): {e}")

        # Entity cross-link
        if ctx["via"] and ctx["entity_type"] != "project" and ctx["entity_id"]:
            _create_entity_artifact_link(
                artifact_type="assumption",
                artifact_id=assumption_id,
                entity_type=ctx["entity_type"],
                entity_id=ctx["entity_id"],
                discovered_via=ctx["via"],
                transaction_id=ctx["transaction_id"],
            )

        # Inline edges (--edge / --related-to)
        edges_declared = _collect_edges_from_args(args)
        edges_wired = _persist_edges("assumption", assumption_id, edges_declared) if edges_declared else 0

        suggested_links = _suggest_links_safe(ctx["project_id"], assumption, assumption_id)

        result = {
            "ok": True,
            "assumption_id": assumption_id,
            "edges_wired": edges_wired,
            "project_id": ctx["project_id"],
            "entity_type": ctx["entity_type"],
            "entity_id": ctx["entity_id"],
            "assumption": assumption,
            "confidence": confidence,
            "status": "unverified",
            "embedded": embedded,
            "git_stored": git_stored,
            "suggested_links": suggested_links,
            "message": "Assumption logged",
        }

        if ctx["output_format"] == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"Assumption logged: {assumption_id[:8]}...")
            print(f"   Confidence: {confidence}")
            if embedded:
                print("   Stored in Qdrant")

        return 0

    except Exception as e:
        handle_cli_error(e, "Assumption log", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


def _parse_decision_alternatives(value) -> list:
    """Parse the --alternatives flag (JSON list, comma-string, or pre-parsed list)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return [a.strip() for a in value.split(",") if a.strip()]
    return []


def _decision_persist_git(decision_id, ctx, choice, rationale, alternatives_json, confidence, reversibility) -> bool:
    """Store decision in git notes for sync. Non-fatal on failure."""
    try:
        from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore

        return GitDecisionStore().store_decision(
            decision_id=decision_id,
            project_id=ctx["project_id"],
            session_id=ctx["session_id"],
            ai_id=ctx["ai_id"],
            choice=choice,
            rationale=rationale,
            alternatives=alternatives_json,
            confidence=confidence,
            reversibility=reversibility,
            goal_id=ctx["goal_id"],
        )
    except Exception as e:
        logger.debug(f"Git notes storage failed (non-fatal): {e}")
        return False


def _decision_persist_qdrant(decision_id, ctx, choice, rationale, alternatives_list, confidence, reversibility) -> bool:
    """Embed decision in Qdrant for semantic search. Non-fatal on failure."""
    import time

    try:
        from empirica.core.qdrant.vector_store import _check_qdrant_available, embed_decision

        if not _check_qdrant_available():
            return False
        embed_decision(
            project_id=ctx["project_id"],
            decision_id=decision_id,
            choice=choice,
            alternatives=json.dumps(alternatives_list),
            rationale=rationale,
            confidence_at_decision=confidence,
            reversibility=reversibility,
            entity_type=ctx["entity_type"],
            entity_id=ctx["entity_id"],
            session_id=ctx["session_id"],
            transaction_id=ctx["transaction_id"],
            timestamp=time.time(),
        )
        return True
    except Exception as e:
        logger.debug(f"Qdrant embed failed (non-fatal): {e}")
        return False


def handle_decision_log_command(args):
    """Handle decision-log command — log decisions with alternatives."""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=["choice"])
        db = ctx["db"]

        # Extract decision-specific fields
        cfg = config_data or {}
        choice = cfg.get("choice") or getattr(args, "choice", None)
        rationale = cfg.get("rationale", "") or getattr(args, "rationale", "")
        alternatives_list = _parse_decision_alternatives(
            cfg.get("alternatives", "") or getattr(args, "alternatives", "")
        )
        confidence = cfg.get("confidence", 0.7) if config_data else getattr(args, "confidence", 0.7)
        reversibility = cfg.get("reversibility", "exploratory") or getattr(args, "reversibility", "exploratory")
        evidence_refs = cfg.get("evidence_refs") or getattr(args, "evidence_refs", None)
        epistemic_source = cfg.get("epistemic_source") or getattr(args, "epistemic_source", None)
        description = cfg.get("description") or getattr(args, "description", None)
        alternatives_json = json.dumps(alternatives_list) if alternatives_list else None

        # Content-aware provenance nudge (non-blocking)
        _warn_unsourced_citations_if_needed(args, choice, rationale)

        # Store to SQLite (durable)
        decision_id = db.log_decision(
            project_id=ctx["project_id"],
            session_id=ctx["session_id"],
            choice=choice,
            rationale=rationale,
            alternatives=alternatives_json,
            confidence=confidence,
            reversibility=reversibility,
            goal_id=ctx["goal_id"],
            transaction_id=ctx["transaction_id"],
            entity_type=ctx["entity_type"],
            entity_id=ctx["entity_id"],
            evidence_refs=evidence_refs,
            visibility=ctx["visibility"],
            epistemic_source=epistemic_source,
            description=description,
        )

        git_stored = _decision_persist_git(
            decision_id,
            ctx,
            choice,
            rationale,
            alternatives_json,
            confidence,
            reversibility,
        )
        embedded = _decision_persist_qdrant(
            decision_id,
            ctx,
            choice,
            rationale,
            alternatives_list,
            confidence,
            reversibility,
        )

        # Entity cross-link
        if ctx["via"] and ctx["entity_type"] != "project" and ctx["entity_id"]:
            _create_entity_artifact_link(
                artifact_type="decision",
                artifact_id=decision_id,
                entity_type=ctx["entity_type"],
                entity_id=ctx["entity_id"],
                discovered_via=ctx["via"],
                transaction_id=ctx["transaction_id"],
            )

        # Inline edges (--edge / --related-to / --evidence-from)
        edges_declared = _collect_edges_from_args(args, evidence_relation="evidence")
        edges_wired = _persist_edges("decision", decision_id, edges_declared) if edges_declared else 0

        suggested_links = _suggest_links_safe(ctx["project_id"], f"{choice}. Rationale: {rationale}", decision_id)

        result = {
            "ok": True,
            "decision_id": decision_id,
            "edges_wired": edges_wired,
            "project_id": ctx["project_id"],
            "entity_type": ctx["entity_type"],
            "entity_id": ctx["entity_id"],
            "choice": choice,
            "alternatives": alternatives_list,
            "rationale": rationale,
            "confidence": confidence,
            "reversibility": reversibility,
            "evidence_refs": evidence_refs,
            "embedded": embedded,
            "git_stored": git_stored,
            "suggested_links": suggested_links,
            "message": "Decision logged",
        }

        if ctx["output_format"] == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"Decision logged: {decision_id[:8]}...")
            print(f"   Choice: {choice}")
            print(f"   Alternatives: {', '.join(alternatives_list) if alternatives_list else 'none'}")
            print(f"   Reversibility: {reversibility}")
            if embedded:
                print("   Stored in Qdrant")

        return 0

    except Exception as e:
        handle_cli_error(e, "Decision log", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


# handle_refdoc_add_command removed in goal 3d6aeb08 Phase 2.
# The CLI surface is gone; use `empirica source-add` instead.
# The Python API (SessionDatabase.add_reference_doc /
# BreadcrumbRepository.add_reference_doc) is still in place for
# internal callers and routes to epistemic_sources WHERE
# source_type='pointer' since migration 046.


def _source_persist_git_and_qdrant(
    source_id, project_id, session_id, title, source_type, source_url, doc_path, description, confidence, direction
):
    """Persist source to git notes and Qdrant (both non-fatal)."""
    import time

    git_stored = False
    try:
        from empirica.core.canonical.empirica_git.source_store import GitSourceStore

        git_stored = GitSourceStore().store_source(
            source_id=source_id,
            project_id=project_id,
            session_id=session_id,
            title=title,
            source_type=source_type,
            source_url=source_url,
            doc_path=doc_path,
            description=description,
            confidence=confidence,
            direction=direction,
        )
    except Exception as e:
        logger.debug(f"Source git notes failed (non-fatal): {e}")

    embedded = False
    if project_id and source_id:
        try:
            from empirica.core.qdrant.vector_store import embed_single_memory_item

            text = f"SOURCE ({direction}): {title}"
            if description:
                text += f" — {description}"
            if source_url or doc_path:
                text += f" [{source_url or doc_path}]"
            embedded = embed_single_memory_item(
                project_id=project_id,
                item_id=source_id,
                text=text,
                item_type="source",
                session_id=session_id,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        except Exception as e:
            logger.debug(f"Source Qdrant embed failed (non-fatal): {e}")

    return git_stored, embedded


def _compute_content_identity(doc_path: str | None) -> dict:
    """Best-effort content identity for a file-backed source.

    Returns {canonical_path, content_hash, size_bytes, mime_type} — all
    None when doc_path is absent, unresolvable, or unreadable. Never
    raises: source-add must not fail because the file moved.

    content_hash is algorithm-prefixed ('sha256:<hex>') — the shared
    catalogue dedupe + reconcile key, so the format must match what the
    catalogue stores.
    """
    identity: dict = {
        "canonical_path": None,
        "content_hash": None,
        "size_bytes": None,
        "mime_type": None,
    }
    if not doc_path:
        return identity
    try:
        import hashlib
        import mimetypes
        from pathlib import Path

        p = Path(doc_path).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()
        identity["canonical_path"] = str(p)
        if p.is_file():
            data = p.read_bytes()
            identity["content_hash"] = f"sha256:{hashlib.sha256(data).hexdigest()}"
            identity["size_bytes"] = len(data)
            identity["mime_type"] = mimetypes.guess_type(p.name)[0]
    except Exception:
        pass
    return identity


def handle_source_add_command(args):
    """Handle source-add command — entity-agnostic epistemic source logging.

    Sources are bidirectional:
      --noetic: evidence IN (source_used — informed knowledge)
      --praxic: output OUT (source_created — produced by action)
    """
    try:
        import time
        import uuid

        from empirica.data.session_database import SessionDatabase
        from empirica.data.visibility import normalize_visibility

        title = args.title
        description = getattr(args, "description", None)
        source_type = getattr(args, "source_type", "document")
        # --media is --path plus a cortex blob push (producer half of
        # media-bearing sources). When given, it IS the doc_path (content
        # identity is computed from it), and media_push below uploads the bytes.
        media_path = getattr(args, "media", None)
        doc_path = media_path or getattr(args, "path", None)
        source_url = getattr(args, "url", None)
        confidence = getattr(args, "confidence", 0.7)
        direction = "noetic" if getattr(args, "noetic", False) else "praxic"
        # Normalize visibility once at the CLI boundary so the INSERT carries
        # the canonical tier. None → 'shared' (the default + safe invariant);
        # bogus values also collapse to 'shared' rather than silently promoting
        # to 'public' (see data/visibility.py).
        visibility = normalize_visibility(getattr(args, "visibility", None))
        session_id = getattr(args, "session_id", None)
        project_id = getattr(args, "project_id", None)
        output_format = getattr(args, "output", "human")
        entity_type = getattr(args, "entity_type", None)
        entity_id = getattr(args, "entity_id", None)
        via = getattr(args, "via", None)

        if not session_id:
            session_id = R.session_id()
        if not session_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No active transaction and --session-id not provided",
                        "hint": "Either run PREFLIGHT first, or provide --session-id explicitly",
                    }
                )
            )
            return 1

        db = SessionDatabase()

        if not project_id:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                project_id = row["project_id"] if isinstance(row, dict) else row[0]

        if not project_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Could not resolve project_id",
                        "hint": "Provide --project-id or ensure active session has a project",
                    }
                )
            )
            db.close()
            return 1

        transaction_id = None
        try:
            from empirica.utils.session_resolver import read_active_transaction_full

            tx = read_active_transaction_full()
            if tx:
                transaction_id = tx.get("transaction_id")
        except Exception:
            pass

        source_id = str(uuid.uuid4())
        metadata = {
            "direction": direction,
            "doc_path": doc_path,
            "source_url": source_url,
            "transaction_id": transaction_id,
        }
        resolved_entity_type = entity_type or "project"
        resolved_entity_id = entity_id or (project_id if resolved_entity_type == "project" else None)
        identity = _compute_content_identity(doc_path)

        db.conn.execute(
            """
            INSERT INTO epistemic_sources (
                id, project_id, session_id, source_type, source_url,
                title, description, confidence, epistemic_layer,
                discovered_by_ai, discovered_at, source_metadata,
                entity_type, entity_id, visibility,
                content_hash, size_bytes, canonical_path, mime_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                source_id,
                project_id,
                session_id,
                source_type,
                source_url or doc_path,
                title,
                description,
                confidence,
                direction,
                "claude-code",
                time.time(),
                json.dumps(metadata),
                resolved_entity_type,
                resolved_entity_id,
                visibility,
                identity["content_hash"],
                identity["size_bytes"],
                identity["canonical_path"],
                identity["mime_type"],
            ),
        )
        db.conn.commit()

        if entity_type and entity_type != "project" and entity_id:
            _create_entity_artifact_link(
                artifact_type="source",
                artifact_id=source_id,
                entity_type=entity_type,
                entity_id=entity_id,
                discovered_via=via,
                transaction_id=transaction_id,
            )

        # Refdoc back-compat dual-write removed in goal 3d6aeb08 Phase 2.
        # Pre-Phase-1: this wrote a parallel row into project_reference_docs
        # for any source with a doc_path, so legacy refdoc consumers could
        # see it. Post-Phase-1: add_reference_doc routes to epistemic_sources
        # too, which made this a double-insert (one row with the user's
        # source_type, one duplicate with source_type='pointer'). Drop it.

        db.close()

        git_stored, embedded = _source_persist_git_and_qdrant(
            source_id,
            project_id,
            session_id,
            title,
            source_type,
            source_url,
            doc_path,
            description,
            confidence,
            direction,
        )

        # Producer half of media-bearing sources: when --media is given, push
        # the blob to cortex (upsert-on-body) so peers can source-get it. The
        # source_id IS adopted as the catalogue uuid, so we stamp cortex_uuid
        # locally to mark the row cortex-registered (daemon resolves id OR
        # cortex_uuid). Best-effort: a push failure leaves the local source
        # intact and reports the error — it never fails the whole add.
        media_push = _upload_media_source(args, media_path, source_id, identity, title, project_id, visibility)
        # --media exists to upload the blob — if that fails, the command failed,
        # even though the local source row was created. Surface it loudly
        # (ok:false + non-zero exit) rather than a silent ok:true footnote.
        media_failed = _media_upload_failed(media_path, media_push)

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": not media_failed,
                        "source_id": source_id,
                        "project_id": project_id,
                        "session_id": session_id,
                        "transaction_id": transaction_id,
                        "direction": direction,
                        "title": title,
                        "visibility": visibility,
                        "git_stored": git_stored,
                        "embedded": embedded,
                        "media_push": media_push,
                        "error": (media_push or {}).get("error") if media_failed else None,
                        "message": (
                            f"Source row created but media upload FAILED: {(media_push or {}).get('error')}"
                            if media_failed
                            else f"Source added ({direction})"
                        ),
                    },
                    indent=2,
                )
            )
        else:
            direction_emoji = "📥" if direction == "noetic" else "📤"
            print(f"{direction_emoji} Source added ({direction})")
            print(f"   Source ID: {source_id[:12]}...")
            print(f"   Title: {title}")
            print(f"   Type: {source_type}")
            print(f"   Direction: {direction} ({'evidence IN' if direction == 'noetic' else 'output OUT'})")
            print(f"   Visibility: {visibility}")
            if doc_path:
                print(f"   Path: {doc_path}")
            if source_url:
                print(f"   URL: {source_url}")
            _print_media_push_status(media_push, visibility)

        return 1 if media_failed else 0

    except Exception as e:
        handle_cli_error(e, "Source add", getattr(args, "verbose", False))
        return None


def _normalize_hash(h: str | None) -> str | None:
    """Strip an optional `algo:` prefix and lowercase, so a cortex-returned
    `content_hash` compares equal regardless of whether it carries the
    `sha256:` prefix empirica's _compute_content_identity uses."""
    if not h:
        return None
    h = h.strip().lower()
    if ":" in h:
        h = h.split(":", 1)[1]
    return h


def _fetch_source_raw(cortex_url: str, api_key: str, source_id: str, timeout: float = 30.0):
    """GET /v1/sources/{id}/raw — the consumer side of media-bearing sources.

    Returns (status_code, body) where body is the parsed JSON dict on 2xx
    (shape: {ok, source_id, content_hash, body_hash, mime, size_bytes,
    encoding:'base64', content, retained_at}) or the parsed error dict on an
    HTTP error. Returns (None, {'error': ...}) on a network/transport failure.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{cortex_url}/v1/sources/{source_id}/raw",
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            # The REST /raw endpoint serves the RAW BODY BYTES + an X-Body-Hash
            # header (verified: image/png, 762KB). Only when it hands back an
            # explicit JSON envelope do we parse the base64 {content,...} shape
            # (the MCP-tool serving path). Branch on Content-Type so we never
            # UTF-8/JSON-decode raw bytes (that crashed on the PNG magic 0x89).
            if "application/json" in ctype:
                try:
                    return resp.status, (json.loads(data.decode("utf-8")) if data else {})
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass  # mislabeled — fall through to raw handling
            return resp.status, {
                "raw_bytes": data,
                "mime": resp.headers.get("Content-Type"),
                "body_hash": resp.headers.get("X-Body-Hash") or resp.headers.get("x-body-hash"),
                "size_bytes": len(data),
            }
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": f"HTTP {e.code}"}
        return e.code, body
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def _upsert_media_to_cortex(
    cortex_url: str,
    api_key: str,
    source_id: str,
    content: bytes,
    mime: str | None,
    title: str,
    project_id: str | None,
    visibility: str,
    timeout: float = 60.0,
):
    """POST /v1/sources/{id}/body — the producer side of media-bearing sources.

    Cortex upserts-on-missing (commit d76ae9c9): a single atomic call registers
    the source in the catalogue (adopting our local uuid as the catalogue uuid)
    AND retains the blob. Image MIMEs auto-retain. The X-Source-Visibility
    header is load-bearing for cross-tenant reads — it MUST be shared/public or
    cortex's get_raw visibility gate rejects the peer fetch.

    Returns a status dict (never raises). On success: {'pushed': True, ...cortex
    response fields}. On failure: {'pushed': False, 'error': ...}.
    """
    import urllib.error
    import urllib.request

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": mime or "application/octet-stream",
        "X-Source-Visibility": visibility,
    }
    # HTTP headers are latin-1; a unicode title would raise at send time. Only
    # forward an ASCII-safe title — otherwise let cortex default to media-<hash>.
    if title and title.isascii():
        headers["X-Source-Title"] = title
    if project_id:
        headers["X-Project-Id"] = project_id

    req = urllib.request.Request(
        f"{cortex_url}/v1/sources/{source_id}/body",
        data=content,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            result = {"pushed": True, "status": resp.status}
            if isinstance(body, dict):
                result.update(body)
            return result
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            err = body.get("error", f"HTTP {e.code}")
        except Exception:
            err = f"HTTP {e.code}"
        return {"pushed": False, "status": e.code, "error": err}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"pushed": False, "error": f"{type(e).__name__}: {e}"}


def _media_upload_failed(media_path, media_push) -> bool:
    """True when --media was requested but the blob upload did not succeed.

    Used to make the failure LOUD (ok:false + non-zero exit) instead of a
    silent ok:true — the whole point of --media is the upload, so a source
    row without its blob is a failed command, not a success with a footnote.
    """
    return bool(media_path) and media_push is not None and not media_push.get("pushed")


def _print_media_push_status(media_push, visibility):
    """Human-readable one-liner for a --media upload result (no-op if None)."""
    if media_push is None:
        return
    if media_push.get("pushed"):
        size = media_push.get("size_bytes", "?")
        print(f"   📤 Media uploaded to cortex ({size} bytes, visibility={visibility})")
        if visibility == "local":
            print("   ⚠ visibility=local — peers in other tenants can't source-get it; use --visibility shared")
    else:
        print(f"   ⚠ Media upload failed: {media_push.get('error', 'unknown')}")


def _upload_media_source(args, media_path, source_id, identity, title, project_id, visibility):
    """Push a --media source's blob to cortex + stamp cortex_uuid locally.

    Returns None when no --media was given. Otherwise best-effort: returns a
    status dict, never raises. A failure here leaves the local source row intact
    (it's already committed) and surfaces the error — the media upload is an
    enrichment, not a precondition for the source. On success, stamps
    cortex_uuid = source_id (cortex adopts our uuid as the catalogue uuid via
    upsert-on-body), so the daemon resolves id OR cortex_uuid.
    """
    if not media_path:
        return None

    import os

    from empirica.data.session_database import SessionDatabase

    canonical = identity.get("canonical_path")
    if not (canonical and os.path.isfile(canonical)):
        return {"pushed": False, "error": f"media file unreadable: {media_path}"}

    from empirica.cli.command_handlers.projects_commands import _resolve_cortex_config

    cortex_url, api_key = _resolve_cortex_config(args)
    if not (cortex_url and api_key):
        return {"pushed": False, "error": "cortex not configured (media blob not uploaded)"}

    try:
        with open(canonical, "rb") as fh:
            content = fh.read()
        result = _upsert_media_to_cortex(
            cortex_url, api_key, source_id, content, identity.get("mime_type"), title, project_id, visibility
        )
        if result.get("pushed"):
            mdb = SessionDatabase()
            mdb.conn.execute(
                "UPDATE epistemic_sources SET cortex_uuid = ? WHERE id = ?",
                (source_id, source_id),
            )
            mdb.conn.commit()
            mdb.close()
        return result
    except Exception as e:
        return {"pushed": False, "error": f"{type(e).__name__}: {e}"}


def handle_source_get_command(args):
    """Handle source-get — download a media-bearing source's retained bytes.

    The consumer half of cross-tenant media transfer (SER ser_a92b3a05): fetch
    a source's blob from cortex via GET /v1/sources/{id}/raw, verify the
    SHA-256 content hash, and write the bytes to --out. Cross-tenant fetch is
    permitted by cortex's visibility×tenant/org gate (source visibility must
    allow the caller); a per-fetch access-log entry is written cortex-side.

    Producer counterpart: `source-add --media` (pending a cortex source-
    registration endpoint — POST /body requires the source to pre-exist in the
    catalogue).
    """
    import base64
    import hashlib
    from pathlib import Path

    from empirica.cli.command_handlers.projects_commands import _resolve_cortex_config

    try:
        source_id = args.id
        out_path = args.out
        output_format = getattr(args, "output", "human")

        cortex_url, api_key = _resolve_cortex_config(args)
        if not (cortex_url and api_key):
            msg = {
                "ok": False,
                "error": "Cortex not configured",
                "hint": "Set cortex.url + cortex.api_key in ~/.empirica/credentials.yaml (or pass --cortex-url/--api-key)",
            }
            print(json.dumps(msg) if output_format == "json" else f"❌ {msg['error']} — {msg['hint']}")
            return 1

        status, body = _fetch_source_raw(cortex_url, api_key, source_id)
        # Accept either shape: raw-bytes response (REST /raw — the common path)
        # or a base64 JSON envelope (MCP-tool serving path).
        has_payload = isinstance(body, dict) and ("raw_bytes" in body or "content" in body)
        if status != 200 or not has_payload:
            err = (body or {}).get("error", f"HTTP {status}")
            hint = ""
            if err == "not_retained":
                hint = " — source exists but has no retained body (producer must POST /body)"
            elif err == "source_not_found":
                hint = " — no such source, or your tenant/visibility doesn't permit access"
            payload = {"ok": False, "error": err, "source_id": source_id, "status": status}
            print(json.dumps(payload) if output_format == "json" else f"❌ source-get failed: {err}{hint}")
            return 1

        # Decode + verify before writing — a hash mismatch means corruption or
        # a wrong-source response; never write unverified bytes to --out.
        # Prefer body_hash: it is always sha256 of the RAW bytes. content_hash
        # only equals the raw-bytes hash on the media-upsert path — for a
        # text-extracted source it hashes the flattened text, so verifying raw
        # bytes against it would spuriously fail. Fall back to content_hash
        # when body_hash is absent (older serve paths).
        content = body["raw_bytes"] if "raw_bytes" in body else base64.b64decode(body["content"])
        expected = _normalize_hash(body.get("body_hash") or body.get("content_hash"))
        actual = hashlib.sha256(content).hexdigest()
        if expected and expected != actual:
            payload = {
                "ok": False,
                "error": "hash_mismatch",
                "source_id": source_id,
                "expected": expected,
                "actual": actual,
            }
            print(
                json.dumps(payload)
                if output_format == "json"
                else f"❌ hash mismatch — expected {expected[:16]}…, got {actual[:16]}… (not written)"
            )
            return 1

        out = Path(out_path).expanduser()
        if not out.is_absolute():
            out = Path.cwd() / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "source_id": source_id,
                        "out": str(out),
                        "size_bytes": len(content),
                        "mime": body.get("mime"),
                        "content_hash": body.get("content_hash"),
                        "hash_verified": bool(expected),
                    },
                    indent=2,
                )
            )
        else:
            verified = "✓ hash verified" if expected else "⚠ no hash to verify against"
            print(f"📥 Source fetched → {out}")
            print(f"   {len(content)} bytes · {body.get('mime') or 'application/octet-stream'} · {verified}")
        return 0

    except Exception as e:
        handle_cli_error(e, "Source get", getattr(args, "verbose", False))
        return None


def _query_epistemic_sources(db, project_id, source_type_filter, direction_filter, include_archived=False):
    """Query epistemic_sources and legacy refdocs, returning combined list.

    include_archived defaults False — archived sources are hidden by default
    in line with SOURCES_LIFECYCLE_SPEC's read-side default. Pass True for
    forensics views that need to surface terminated rows.
    """
    sources = []
    try:
        # archived/archive_reason/archived_at are LEFT-OUT-protected via
        # COALESCE in case the DB hasn't been migrated past 044 yet (older
        # installs reading newer code).
        query = """
            SELECT id, source_type, title, description, confidence,
                   epistemic_layer, source_url, discovered_at, source_metadata,
                   COALESCE(archived, 0) AS archived,
                   archive_reason, archive_target_id, archived_at
            FROM epistemic_sources
            WHERE project_id = ?
        """
        params = [project_id]
        if not include_archived:
            query += " AND COALESCE(archived, 0) = 0"
        if source_type_filter:
            query += " AND source_type = ?"
            params.append(source_type_filter)
        if direction_filter != "all":
            query += " AND epistemic_layer = ?"
            params.append(direction_filter)
        query += " ORDER BY discovered_at DESC"

        cursor = db.conn.cursor()
        cursor.execute(query, params)
        for row in cursor.fetchall():
            r = (
                dict(row)
                if hasattr(row, "keys")
                else {
                    "id": row[0],
                    "source_type": row[1],
                    "title": row[2],
                    "description": row[3],
                    "confidence": row[4],
                    "direction": row[5],
                    "url": row[6],
                    "discovered_at": row[7],
                    "metadata": row[8],
                    "archived": bool(row[9]) if len(row) > 9 else False,
                    "archive_reason": row[10] if len(row) > 10 else None,
                    "archive_target_id": row[11] if len(row) > 11 else None,
                    "archived_at": row[12] if len(row) > 12 else None,
                }
            )
            r["source"] = "epistemic_sources"
            sources.append(r)
    except Exception as e:
        logger.debug(f"epistemic_sources query failed (table may not exist): {e}")

    try:
        refdocs = db.get_project_reference_docs(project_id)
        for rd in refdocs:
            doc_path = rd.get("doc_path", "")
            if any(s.get("url") == doc_path or s.get("source_url") == doc_path for s in sources):
                continue
            sources.append(
                {
                    "id": rd.get("id", ""),
                    "source_type": rd.get("doc_type", "document"),
                    "title": doc_path.split("/")[-1] if doc_path else "unknown",
                    "description": rd.get("description", ""),
                    "confidence": None,
                    "direction": "noetic",
                    "url": doc_path,
                    "discovered_at": None,
                    "source": "refdoc_legacy",
                }
            )
    except Exception as e:
        logger.debug(f"refdoc query failed: {e}")

    return sources


def _print_sources_pretty(sources):
    """Print human-readable sources list."""
    print(f"\n📚 Epistemic Sources ({len(sources)} total)")
    print("=" * 60)
    for s in sources:
        direction = s.get("direction") or s.get("epistemic_layer", "?")
        emoji = "📥" if direction == "noetic" else "📤"
        conf = f" [{s['confidence']:.1f}]" if s.get("confidence") else ""
        source_tag = f" ({s['source']})" if s.get("source") == "refdoc_legacy" else ""
        print(f"  {emoji} {s.get('title', '?')}{conf}{source_tag}")
        print(f"     Type: {s.get('source_type', '?')} | Direction: {direction}")
        url = s.get("url") or s.get("source_url", "")
        if url:
            print(f"     Path: {url}")
        desc = s.get("description", "")
        if desc:
            print(f"     Desc: {desc[:80]}")
        print()


_VALID_ARCHIVE_REASONS = ("user_deleted", "file_missing", "url_unreachable", "superseded")


def _hard_delete_source_chunks(project_id: str, source_id: str) -> int:
    """Best-effort hard-delete of Qdrant chunks for an archived source.

    Per SOURCES_LIFECYCLE_SPEC: chunks (layer B) are derived data; safe to
    drop on archive because they're regenerable from the original. Returns
    number of points deleted (0 if Qdrant unavailable or none found).
    """
    try:
        from empirica.core.qdrant.collections import _docs_collection
        from empirica.core.qdrant.connection import _get_qdrant_client
    except ImportError:
        return 0
    client = _get_qdrant_client()
    if client is None:
        return 0
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        coll = _docs_collection(project_id)
        if not client.collection_exists(coll):
            return 0
        # Match either source_id payload key directly, or the source's URL
        # if chunks were keyed by url.
        flt = Filter(
            should=[
                FieldCondition(key="source_id", match=MatchValue(value=source_id)),
            ]
        )
        result = client.delete(collection_name=coll, points_selector=flt)
        return getattr(result, "deleted", 0) or 0
    except Exception as e:
        logger.debug(f"_hard_delete_source_chunks: qdrant delete failed: {e}")
        return 0


def _hard_delete_source_memory_embed(project_id: str, source_id: str) -> int:
    """Best-effort delete of the source's metadata embed from the memory collection.

    A source is embedded at add-time into ``_memory_collection`` as an
    ``item_type='source'`` point (payload ``artifact_id=source_id``, ``type='source'``)
    so it is discoverable via ``sources-map`` / cross-project semantic search. On
    archive that embed must go too — otherwise the archived source stays
    discoverable even though it is soft-deleted. This is DISTINCT from
    ``_hard_delete_source_chunks``, which clears content chunks in
    ``_docs_collection`` (a different collection that CLI-added sources don't
    populate). Returns points deleted (0 if Qdrant unavailable or none found).
    """
    try:
        from empirica.core.qdrant.collections import _memory_collection
        from empirica.core.qdrant.connection import _get_qdrant_client
    except ImportError:
        return 0
    client = _get_qdrant_client()
    if client is None:
        return 0
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        coll = _memory_collection(project_id)
        if not client.collection_exists(coll):
            return 0
        flt = Filter(
            must=[
                FieldCondition(key="artifact_id", match=MatchValue(value=source_id)),
                FieldCondition(key="type", match=MatchValue(value="source")),
            ]
        )
        result = client.delete(collection_name=coll, points_selector=flt)
        return getattr(result, "deleted", 0) or 0
    except Exception as e:
        logger.debug(f"_hard_delete_source_memory_embed: qdrant delete failed: {e}")
        return 0


def _push_source_archive_to_cortex(full_id: str, reason: str, target_id: str | None) -> dict | None:
    """Best-effort `DELETE /v1/sources/{id}` to Cortex (Phase 1.5).

    Returns a small status dict that the caller embeds in the JSON response
    so the user can see whether the remote side was notified. Network or HTTP
    failures NEVER fail the local archive — they're logged and reported.

    No-op when Cortex creds (env vars or ~/.empirica/credentials.yaml)
    are unset. Resolved via the centralized CredentialsLoader.
    """
    import urllib.error
    import urllib.request

    from empirica.config.credentials_loader import get_credentials_loader

    cfg = get_credentials_loader().get_cortex_config()
    url = cfg.get("url")
    key = cfg.get("api_key")
    if not url or not key:
        return None
    body = json.dumps({"reason": reason, "target_id": target_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/v1/sources/{full_id}",
        data=body,
        method="DELETE",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return {"synced": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"synced": False, "status": e.code, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"synced": False, "status": 0, "error": f"{type(e).__name__}: {e}"}


def handle_source_archive_command(args):
    """Handle source-archive command — soft-delete a source (lifecycle Phase 1).

    Mutates the local epistemic_sources row in place: sets archived=1 plus
    archive_reason / archive_target_id / archived_at, appends an audit-log
    event. Hard-deletes corresponding Qdrant chunks (regenerable from the
    original if needed). Edges from findings to this source are NEVER
    touched — the audit chain is preserved.

    Per SOURCES_LIFECYCLE_SPEC §8 Empirica-Core CLI parity. Empirica is the
    authoritative store; downstream Cortex projects from this state.

    Phase 1.5 (v1.9.6+): when CORTEX_REMOTE_URL + CORTEX_API_KEY are set,
    also `DELETE /v1/sources/{id}` on Cortex so the remote authoritative
    side reflects the same archived state. Best-effort — the local archive
    succeeds regardless of remote outcome.
    """
    db = None
    try:
        import time

        from empirica.data.session_database import SessionDatabase

        source_id = args.source_id
        reason = args.reason
        target_id = getattr(args, "target_id", None)
        output_format = getattr(args, "output", "human")

        if reason not in _VALID_ARCHIVE_REASONS:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": (f"Invalid --reason '{reason}'. Must be one of: {', '.join(_VALID_ARCHIVE_REASONS)}"),
                    }
                )
            )
            return 1
        if reason == "superseded" and not target_id:
            print(
                json.dumps(
                    {"ok": False, "error": "--reason superseded requires --target-id (the replacement source UUID)"}
                )
            )
            return 1

        db = SessionDatabase()
        cur = db.conn.cursor()

        # Resolve full source_id from prefix (matches log-artifacts UX)
        cur.execute(
            "SELECT id, project_id, title, archived, lifecycle_audit_log "
            "FROM epistemic_sources WHERE id = ? OR id LIKE ? LIMIT 2",
            (source_id, f"{source_id}%"),
        )
        rows = cur.fetchall()
        if not rows:
            print(json.dumps({"ok": False, "error": f"Source not found: {source_id}"}))
            return 1
        if len(rows) > 1:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"Source ID '{source_id}' is ambiguous — matches multiple rows. Use full UUID.",
                    }
                )
            )
            return 1

        full_id, project_id, title, already_archived, audit_log_json = rows[0]

        # Idempotent: re-archive returns 200 with the existing state
        if already_archived:
            existing_log = json.loads(audit_log_json) if audit_log_json else []
            if output_format == "json":
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "source_id": full_id,
                            "archived": True,
                            "already_archived": True,
                            "audit_log": existing_log,
                            "message": "Source already archived (idempotent)",
                        },
                        indent=2,
                    )
                )
            else:
                print(f"⚠ Source {full_id[:8]}... already archived ({len(existing_log)} log entries)")
            return 0

        # Append audit-log event
        existing_log = json.loads(audit_log_json) if audit_log_json else []
        now = time.time()
        existing_log.append(
            {
                "event": "archived",
                "reason": reason,
                "target_id": target_id,
                "timestamp": now,
                "by": "claude-code",  # Could resolve from session.ai_id; keeping simple
            }
        )

        # Mutate in place
        cur.execute(
            "UPDATE epistemic_sources SET "
            "archived = 1, archive_reason = ?, archive_target_id = ?, "
            "archived_at = ?, lifecycle_audit_log = ? "
            "WHERE id = ?",
            (reason, target_id, now, json.dumps(existing_log), full_id),
        )
        db.conn.commit()

        # Hard-delete chunks (best-effort; non-fatal)
        chunks_deleted = _hard_delete_source_chunks(project_id, full_id)
        # Remove the source's metadata embed from the memory collection too —
        # otherwise the archived source stays discoverable via sources-map / search.
        memory_deleted = _hard_delete_source_memory_embed(project_id, full_id)

        # Optional Cortex sync (Phase 1.5) — no-op when env vars unset
        cortex_status = _push_source_archive_to_cortex(full_id, reason, target_id)

        result = {
            "ok": True,
            "source_id": full_id,
            "title": title,
            "archived": True,
            "archive_reason": reason,
            "archive_target_id": target_id,
            "archived_at": now,
            "chunks_deleted": chunks_deleted,
            "memory_embed_deleted": memory_deleted,
            "audit_log": existing_log,
            "message": "Source archived (soft-delete; edges + citing artifacts preserved)",
        }
        if cortex_status is not None:
            result["cortex"] = cortex_status
        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Archived source {full_id[:8]}... — {title}")
            print(f"   Reason: {reason}" + (f" → target {target_id[:8]}..." if target_id else ""))
            if chunks_deleted:
                print(f"   Cleared {chunks_deleted} Qdrant chunks (regenerable from original)")
            if cortex_status:
                if cortex_status.get("synced"):
                    print(f"   ☁ Cortex notified (HTTP {cortex_status['status']})")
                else:
                    print(
                        f"   ⚠ Cortex sync failed: {cortex_status.get('error', 'unknown')} (local archive still succeeded)"
                    )
            print("   Edges + citing findings/decisions untouched (audit chain preserved)")
        return 0

    except Exception as e:
        handle_cli_error(e, "Source archive", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_sources_map_command(args):
    """Handle sources-map command — cross-mesh source discoverability view.

    The Maven-POM-for-knowledge view (goal 74d35435): show me what
    canonical reference material I own + what's discoverable across
    other practices' Qdrant collections. v1 is a read-only assembly
    over existing data — no new schema, no new embedding pipeline,
    no cortex dependency. v2 candidates: incoming-reference graph
    (who cites my sources), citation-weighted-by-calibration ranking,
    cortex-side mesh aggregation endpoint.

    Output structure:
      - owned: sources in MY project's epistemic_sources table
      - discoverable (--global only): sources from OTHER projects'
        per-project Qdrant collections, filtered to item_type='source'
    """
    db = None
    try:
        from empirica.data.session_database import SessionDatabase

        project_id = getattr(args, "project_id", None)
        include_global = getattr(args, "include_global", False)
        query_text = getattr(args, "query", None) or ""
        source_type_filter = getattr(args, "source_type", None)
        limit = getattr(args, "limit", 20)
        output_format = getattr(args, "output", "human")

        db = SessionDatabase()

        if not project_id:
            try:
                project_path = R.project_path()
                if project_path:
                    project_id = R.project_id_from_db(project_path)
            except Exception:
                pass

        if not project_id:
            print(json.dumps({"ok": False, "error": "Could not resolve project_id"}))
            return 1

        owned_sources = _query_epistemic_sources(
            db,
            project_id,
            source_type_filter,
            "all",
            include_archived=False,
        )

        discoverable_sources = []
        if include_global:
            discoverable_sources = _query_cross_mesh_sources(
                project_id=project_id,
                query_text=query_text,
                source_type_filter=source_type_filter,
                limit=limit,
            )

        payload = {
            "ok": True,
            "project_id": project_id,
            "owned": {
                "count": len(owned_sources),
                "sources": owned_sources,
            },
            "discoverable": {
                "count": len(discoverable_sources),
                "sources": discoverable_sources,
                "scope": "cross-mesh" if include_global else "skipped (--global not set)",
            },
        }

        if output_format == "json":
            print(json.dumps(payload, indent=2))
        else:
            _print_sources_map_pretty(payload)
        return 0

    except Exception as e:
        handle_cli_error(e, "Sources map", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


def _query_cross_mesh_sources(
    project_id: str,
    query_text: str = "",
    source_type_filter: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Walk other projects' Qdrant collections for type='source' items.

    Filters out the current project (you already see those via owned).
    Returns dicts with project_id provenance so consumers know who owns
    each source. Falls back to empty list if Qdrant is unavailable —
    discoverability is a nice-to-have, not a hard dependency.
    """
    try:
        from empirica.core.qdrant.global_sync import search_cross_project
    except Exception:
        return []

    # search_cross_project requires a query — if caller didn't provide
    # one, use a neutral semantic anchor that still produces results.
    # Empty string would fail embedding; a generic anchor surfaces a
    # broad slice ordered by relevance to "source", which is the closest
    # behaviour to "list recent sources" we can get without redesigning
    # the underlying API.
    effective_query = query_text.strip() or "epistemic source"

    try:
        raw = search_cross_project(
            query_text=effective_query,
            exclude_project_id=project_id,
            limit=limit,
        )
    except Exception:
        return []

    out: list[dict] = []
    for r in raw:
        payload = r if isinstance(r, dict) else {}
        # search_cross_project may not natively filter by type, so we
        # post-filter on the payload's `type` field that
        # embed_single_memory_item writes.
        if payload.get("type") != "source":
            continue
        if source_type_filter and payload.get("source_type") != source_type_filter:
            continue
        out.append(
            {
                "source_id": payload.get("item_id") or payload.get("id"),
                "project_id": payload.get("project_id"),
                "text": payload.get("text"),
                "score": payload.get("score"),
                "collection_type": payload.get("collection_type"),
            }
        )
    return out


def _print_sources_map_pretty(payload: dict) -> None:
    """Human-readable rendering of the sources-map response."""
    owned = payload.get("owned", {})
    disc = payload.get("discoverable", {})
    print(f"📍 Sources map for project {payload.get('project_id', '?')[:12]}…")
    print()
    print(f"  Owned (locally): {owned.get('count', 0)}")
    for s in owned.get("sources", [])[:10]:
        title = s.get("title") or s.get("text") or "?"
        sid = (s.get("id") or "")[:12]
        stype = s.get("source_type") or ""
        layer = s.get("epistemic_layer") or ""
        print(f"    • {sid}… [{stype}/{layer}] {title[:60]}")
    if owned.get("count", 0) > 10:
        print(f"    … +{owned['count'] - 10} more")
    print()
    print(f"  Discoverable across mesh: {disc.get('count', 0)} ({disc.get('scope', '')})")
    for s in disc.get("sources", [])[:10]:
        pid = (s.get("project_id") or "?")[:12]
        sid = (s.get("source_id") or "")[:12]
        text = (s.get("text") or "")[:60]
        score = s.get("score")
        print(f"    • {sid}… owned-by={pid}… {text}{f' (score={score:.2f})' if score is not None else ''}")
    if disc.get("count", 0) > 10:
        print(f"    … +{disc['count'] - 10} more")


def handle_source_list_command(args):
    """Handle source-list command — list epistemic sources for a project."""
    db = None
    try:
        from empirica.data.session_database import SessionDatabase

        project_id = getattr(args, "project_id", None)
        source_type_filter = getattr(args, "source_type", None)
        direction_filter = getattr(args, "direction", "all")
        include_archived = getattr(args, "include_archived", False)
        output_format = getattr(args, "output", "human")

        db = SessionDatabase()

        if not project_id:
            try:
                project_path = R.project_path()
                if project_path:
                    project_id = R.project_id_from_db(project_path)
            except Exception:
                pass

        if not project_id:
            print(json.dumps({"ok": False, "error": "Could not resolve project_id"}))
            return 1

        sources = _query_epistemic_sources(
            db,
            project_id,
            source_type_filter,
            direction_filter,
            include_archived=include_archived,
        )

        if output_format == "json":
            print(
                json.dumps({"ok": True, "project_id": project_id, "count": len(sources), "sources": sources}, indent=2)
            )
        else:
            _print_sources_pretty(sources)

        return 0

    except Exception as e:
        handle_cli_error(e, "Source list", getattr(args, "verbose", False))
        return None
    finally:
        if db is not None:
            db.close()


# =============================================================================
# Mistake Commands (consolidated from mistake_commands.py)
# =============================================================================


def _mistake_persist_git_and_qdrant(
    mistake_id, project_id, session_id, ai_id, mistake, why_wrong, prevention, cost_estimate, root_cause_vector, goal_id
):
    """Persist mistake to git notes and Qdrant (both non-fatal)."""
    git_stored = False
    try:
        from empirica.core.canonical.empirica_git.mistake_store import GitMistakeStore

        git_stored = GitMistakeStore().store_mistake(
            mistake_id=mistake_id,
            project_id=project_id,
            session_id=session_id,
            ai_id=ai_id,
            mistake=mistake,
            why_wrong=why_wrong,
            prevention=prevention,
            cost_estimate=cost_estimate,
            root_cause_vector=root_cause_vector,
            goal_id=goal_id,
        )
    except Exception:
        pass

    embedded = False
    if project_id and mistake_id:
        try:
            from datetime import datetime

            from empirica.core.qdrant.vector_store import embed_single_memory_item

            embedded = embed_single_memory_item(
                project_id=project_id,
                item_id=mistake_id,
                text=f"MISTAKE: {mistake} Prevention: {prevention or 'none specified'}",
                item_type="mistake",
                session_id=session_id,
                goal_id=goal_id,
                timestamp=datetime.now().isoformat(),
            )
        except Exception:
            pass

    return git_stored, embedded


def handle_mistake_log_command(args):
    """Handle mistake-log command"""
    try:
        from empirica.data.session_database import SessionDatabase

        project_id = getattr(args, "project_id", None)
        session_id = getattr(args, "session_id", None)
        mistake = args.mistake
        why_wrong = args.why_wrong
        cost_estimate = getattr(args, "cost_estimate", None)
        root_cause_vector = getattr(args, "root_cause_vector", None)
        prevention = getattr(args, "prevention", None)
        description = getattr(args, "description", None)
        goal_id = getattr(args, "goal_id", None)
        output_format = getattr(args, "output", "json")
        entity_type = getattr(args, "entity_type", None)
        entity_id = getattr(args, "entity_id", None)
        via = getattr(args, "via", None)
        visibility = getattr(args, "visibility", None)
        epistemic_source = getattr(args, "epistemic_source", None)

        # Content-aware provenance nudge (non-blocking)
        _warn_unsourced_citations_if_needed(args, mistake, why_wrong, prevention)

        if not session_id:
            session_id = R.session_id()
        if not session_id:
            print(json.dumps({"ok": False, "error": "No active transaction and --session-id not provided"}))
            return

        db = SessionDatabase()
        if not project_id and session_id:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row["project_id"]:
                project_id = row["project_id"]

        transaction_id = None
        try:
            transaction_id = R.transaction_id()
        except Exception:
            pass

        mistake_id = db.log_mistake(
            session_id=session_id,
            mistake=mistake,
            why_wrong=why_wrong,
            cost_estimate=cost_estimate,
            root_cause_vector=root_cause_vector,
            prevention=prevention,
            goal_id=goal_id,
            project_id=project_id,
            transaction_id=transaction_id,
            entity_type=entity_type,
            entity_id=entity_id,
            visibility=visibility,
            epistemic_source=epistemic_source,
            description=description,
        )

        if entity_type and entity_type != "project" and entity_id:
            try:
                _create_entity_artifact_link(
                    artifact_type="mistake",
                    artifact_id=mistake_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    discovered_via=via,
                    transaction_id=transaction_id,
                )
            except Exception:
                pass

        ai_id = "claude-code"
        try:
            cursor = db.conn.cursor()
            cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row["ai_id"]:
                ai_id = row["ai_id"]
        except Exception:
            pass

        db.close()

        git_stored, embedded = _mistake_persist_git_and_qdrant(
            mistake_id,
            project_id,
            session_id,
            ai_id,
            mistake,
            why_wrong,
            prevention,
            cost_estimate,
            root_cause_vector,
            goal_id,
        )

        # Inline edges (--edge / --related-to)
        edges_declared = _collect_edges_from_args(args)
        edges_wired = _persist_edges("mistake", mistake_id, edges_declared) if edges_declared else 0

        suggested_links = _suggest_links_safe(project_id, f"{mistake} — {why_wrong}", mistake_id)

        result = {
            "ok": True,
            "mistake_id": mistake_id,
            "session_id": session_id,
            "project_id": project_id,
            "git_stored": git_stored,
            "embedded": embedded,
            "edges_wired": edges_wired,
            "suggested_links": suggested_links,
            "message": "Mistake logged to project scope",
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Mistake logged successfully")
            print(f"   Mistake ID: {mistake_id[:8]}...")
            print(f"   Session: {session_id[:8]}...")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print("   📝 Stored in git notes for sync")
            if embedded:
                print("   🔍 Auto-embedded for semantic search")

        return None
    except Exception as e:
        from ..cli_utils import handle_cli_error

        handle_cli_error(e, "Mistake log", getattr(args, "verbose", False))
        return None


def handle_mistake_query_command(args):
    """Handle mistake-query command"""
    try:
        from empirica.data.session_database import SessionDatabase

        session_id = getattr(args, "session_id", None)
        goal_id = getattr(args, "goal_id", None)
        limit = getattr(args, "limit", 10)

        db = SessionDatabase()
        mistakes = db.get_mistakes(session_id=session_id, goal_id=goal_id, limit=limit)
        db.close()

        if hasattr(args, "output") and args.output == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mistakes_count": len(mistakes),
                        "mistakes": [
                            {
                                "mistake_id": m["id"],
                                "mistake": m["mistake"],
                                "why_wrong": m["why_wrong"],
                                "prevention": m["prevention"],
                            }
                            for m in mistakes
                        ],
                    },
                    indent=2,
                )
            )
        else:
            print(f"📋 Found {len(mistakes)} mistake(s):")
            for i, m in enumerate(mistakes, 1):
                print(f"\n{i}. {m['mistake'][:60]}...")
                print(f"   Why wrong: {m['why_wrong'][:60]}...")
                if m.get("prevention"):
                    print(f"   Prevention: {m['prevention'][:60]}...")

        return None
    except Exception as e:
        from ..cli_utils import handle_cli_error

        handle_cli_error(e, "Mistake query", getattr(args, "verbose", False))
        return None
