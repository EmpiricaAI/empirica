"""Cross-cutting helpers used by all three workflow phases (preflight/check/postflight).

Includes retrospective counters since _build_retrospective is called from BOTH
check (praxic reminders) and postflight (closing summary). Kept here so neither
phase module owns the other."""

from __future__ import annotations

import json
import logging
import os

from empirica.config.path_resolver import resolve_session_db_path
from empirica.core.canonical.empirica_git.sentinel_hooks import SentinelHooks
from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import parse_json_safely, run_empirica_subprocess

logger = logging.getLogger(__name__)

# Module-level helpers are imported by _workflow_preflight/_workflow_check/
# _workflow_postflight (cross-module). Declared in __all__ so pyright's
# unused-function check accepts them as exported, not dead code.
__all__ = [
    "_auto_bootstrap",
    "_build_noetic_guidance",
    "_build_retrospective",
    "_build_voice_guidance",
    "_build_weave_guidance",
    "_check_bootstrap_status",
    "_extract_all_vectors",
    "_extract_numeric_value",
    "_get_db_for_session",
    "_invoke_sentinel_hook",
    "_parse_workflow_input",
    "_remap_trajectory_summary",
    "_resolve_and_validate_session",
    "_retro_count_artifacts",
    "_retro_count_edges",
    "_retro_count_sources",
    "_soft_run",
    "_weave_enforcement_block",
]

# Investigation-heavy work_types where noetic_batch is most useful.
# Action-pure types (release, comms) and short-form types (data) skip the hint.
_NOETIC_BATCH_WORK_TYPES = frozenset(
    {
        "code",
        "research",
        "debug",
        "audit",
        "docs",
        "infra",
        "config",
        "design",
    }
)


def _remap_trajectory_summary(calibration_summary):
    """Remap Bayesian calibration_summary keys to learning trajectory language.

    The BayesianBeliefManager uses calibration terms (overestimates/underestimates)
    but these represent learning patterns, not accuracy corrections.
    Remap to make the distinction clear in PREFLIGHT output.
    """
    if not calibration_summary:
        return None
    return {
        "typically_increases": calibration_summary.get("underestimates", []),
        "typically_decreases": calibration_summary.get("overestimates", []),
        "stable": calibration_summary.get("well_calibrated", []),
    }


def _get_db_for_session(session_id: str):
    """
    Get SessionDatabase for a specific session_id.

    Resolves the session to its correct project database, allowing
    CLI commands to work correctly even when CWD is different from
    the session's project.

    Args:
        session_id: The session UUID

    Returns:
        SessionDatabase instance connected to the correct project's DB
    """
    from empirica.data.session_database import SessionDatabase

    db_path = resolve_session_db_path(session_id)
    if db_path:
        return SessionDatabase(db_path=str(db_path))
    else:
        # Fallback to CWD-based detection (legacy behavior)
        return SessionDatabase()


def _check_bootstrap_status(session_id: str) -> dict:
    """
    Check if project-bootstrap has been run for this session.

    Returns:
        {
            "has_bootstrap": bool,
            "project_id": str or None,
            "session_exists": bool
        }
    """
    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()

        # Check if session exists and has project_id
        cursor.execute(
            """
            SELECT session_id, project_id FROM sessions
            WHERE session_id = ?
        """,
            (session_id,),
        )
        row = cursor.fetchone()
        db.close()

        if not row:
            return {"has_bootstrap": False, "project_id": None, "session_exists": False}

        project_id = row[1] if row else None
        return {"has_bootstrap": project_id is not None, "project_id": project_id, "session_exists": True}
    except Exception as e:
        return {"has_bootstrap": False, "project_id": None, "session_exists": False, "error": str(e)}


def _auto_bootstrap(session_id: str) -> dict:
    """
    Auto-run project-bootstrap for a session.

    Returns:
        {"ok": bool, "project_id": str, "message": str}
    """
    try:
        result = run_empirica_subprocess(
            ["empirica", "project-bootstrap", "--session-id", session_id, "--output", "json"], timeout=30
        )

        if result.returncode == 0:
            try:
                output = json.loads(result.stdout)
                return {"ok": True, "project_id": output.get("project_id"), "message": "Auto-bootstrap completed"}
            except json.JSONDecodeError:
                return {"ok": True, "project_id": None, "message": "Bootstrap ran (non-JSON output)"}
        else:
            return {"ok": False, "error": result.stderr[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _parse_workflow_input(args, phase: str):
    """Parse and validate workflow input from config file, stdin, or CLI flags.

    Shared across PREFLIGHT, CHECK, and POSTFLIGHT handlers.
    Returns (config_data, output_format) where config_data is parsed JSON
    or None if using legacy CLI flags.
    """
    import sys

    config_data = None

    # AI-FIRST MODE: Check if config file provided or stdin piped
    if hasattr(args, "config") and args.config:
        if args.config == "-":
            config_data = parse_json_safely(sys.stdin.read())
        else:
            if not os.path.exists(args.config):
                print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
                sys.exit(1)
            with open(args.config) as f:
                config_data = parse_json_safely(f.read())
    elif not sys.stdin.isatty():
        config_data = parse_json_safely(sys.stdin.read())

    if config_data:
        # Merge CLI session_id as fallback
        if not config_data.get("session_id") and getattr(args, "session_id", None):
            config_data["session_id"] = args.session_id
        # Auto-resolve session_id from active session
        if not config_data.get("session_id"):
            try:
                auto_sid = R.session_id()
                if auto_sid:
                    config_data["session_id"] = auto_sid
                    logger.debug(f"{phase}: Auto-derived session_id: {auto_sid[:8]}...")
            except Exception:
                pass
        return config_data, "json"

    return None, getattr(args, "output", "json")


def _resolve_and_validate_session(session_id: str, phase: str) -> str:
    """Resolve partial session IDs to full UUIDs with consistent error handling.

    Shared across PREFLIGHT, CHECK, and POSTFLIGHT.
    Returns the resolved session_id or exits with error JSON.
    """
    import sys

    try:
        return R.resolve_session(session_id)
    except ValueError as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Invalid session_id: {e}",
                    "hint": "Use full UUID, partial UUID (8+ chars), or 'latest'",
                }
            )
        )
        sys.exit(1)


def _invoke_sentinel_hook(phase: str, session_id: str, checkpoint_data: dict):
    """Invoke Sentinel post-checkpoint hook if enabled.

    Returns SentinelDecision or None.
    """
    if SentinelHooks.is_enabled():
        return SentinelHooks.post_checkpoint_hook(
            session_id=session_id, ai_id=None, phase=phase, checkpoint_data=checkpoint_data
        )
    return None


def _build_noetic_guidance(work_type: str | None) -> dict | None:
    """Surface the noetic-batch schema when the work_type benefits from it.

    Returns a dict with tool name, CLI form, schema, and hint — or None
    when the work_type isn't investigation-prone (release, comms).
    """
    if not work_type or work_type not in _NOETIC_BATCH_WORK_TYPES:
        return None
    return {
        "tool": "mcp__empirica__noetic_batch",
        "cli": "empirica noetic-batch -",
        "schema": {
            "intent": "<one-line investigation goal>",
            "reads": [{"path": "<file>", "lines": "<optional 'N-M'>"}],
            "greps": [
                {
                    "pattern": "<regex>",
                    "glob": "<optional path glob>",
                    "context": "<optional 0-5>",
                    "max_matches": "<optional ≤500>",
                }
            ],
            "globs": ["<pattern>", {"pattern": "<...>", "root": "<optional dir>"}],
            "investigate": [{"query": "<...>", "scope": "session|project|global", "limit": "<optional ≤20>"}],
        },
        "hint": (
            "Use ONLY when batching ≥3 investigation operations together — the "
            "value is one merged result for your conversation, fewer round-trips. "
            "Individual Read/Grep/Glob are noetic anywhere (any phase) — use them "
            "freely. noetic-batch is NOT a Sentinel bypass; calling it once for a "
            "single read is misuse."
        ),
        "skip_if": (
            "Fewer than 3 investigation operations. Use Read/Grep/Glob/investigate "
            "directly — they're already noetic and don't need batching."
        ),
    }


def _build_weave_guidance() -> dict:
    """Surface the log-artifacts weave schema at the CHECK gate.

    Gated Artifact-Graph map, work-stream 2 (schema-injection). At CHECK→proceed
    the AI is entering the praxic phase and will weave its artifacts into a
    connected sub-graph; giving it the node-type + relation vocabulary here — the
    exact shape AIs fumble (recurring "unknown relation" errors) — makes weaving
    cheap enough that the eventual hard gate doesn't hurt. Structural
    artifact→goal edges are written automatically (work-stream 3); this is for
    the SEMANTIC edges the AI must assert. Best-effort; returns {} on failure.
    """
    try:
        from .graph_commands import NODE_REQUIRED_FIELDS, VALID_RELATIONS
    except Exception:
        return {}
    return {
        "tool": "empirica log-artifacts -   (or mcp__empirica__log_artifacts)",
        "node_types": sorted(NODE_REQUIRED_FIELDS.keys()),
        "relations": sorted(VALID_RELATIONS),
        "node_required_fields": dict(NODE_REQUIRED_FIELDS),
        "shape": {
            "nodes": [{"ref": "<local id e.g. f1>", "type": "<node_type>", "data": {"<required field>": "..."}}],
            "edges": [{"from": "<ref|uuid>", "to": "<ref|uuid>", "relation": "<relation>"}],
        },
        "hint": (
            "Weave, don't just log: connect this transaction's artifacts into a "
            "sub-graph in ONE log-artifacts call. Structural artifact→goal edges "
            "are written for you — assert only the SEMANTIC edges "
            "(evidence / grounded_by / caused_by / resolves / invalidates). Use "
            "ONLY the relations listed above; unknown relations are rejected."
        ),
    }


def _build_voice_guidance(work_type: str | None, voice: str | None) -> dict | None:
    """Surface the voice profile when work_type=comms or --voice was set.

    Loads the profile via the same resolver as `empirica voice apply` —
    project-local .empirica/voice/ overrides ~/.empirica/voice/. Output
    block mirrors voice_commands.handle_voice_apply's payload shape so
    the AI can treat it as if it had run `voice apply` directly.

    Resolution policy:
      • voice='<name>' explicit  → load that profile (any work_type)
      • work_type='comms' alone  → no auto-load (no opinionated default)
      • neither                  → return None

    Choosing not to auto-pick a profile when work_type=comms is set
    without --voice keeps the surface explicit and avoids the wrong-voice
    bug at scale (multi-user sessions, project-shared voice profiles).
    The voice_guidance block in that case nudges the AI toward
    `empirica voice list` so the right profile gets named.
    """
    if not voice:
        # work_type=comms without explicit voice → nudge, don't auto-pick
        if work_type == "comms":
            return {
                "hint": (
                    "work_type=comms — consider naming a voice profile via "
                    "the 'voice' field in PREFLIGHT (or --voice flag). Run "
                    "'empirica voice list' to see available profiles."
                ),
                "profile": None,
            }
        return None

    # Resolve and load the profile
    try:
        import yaml

        from empirica.cli.command_handlers.voice_commands import _resolve_profile_path

        path = _resolve_profile_path(voice)
        if path is None:
            return {
                "hint": f"voice profile {voice!r} not found (no .yaml in project or global voice dirs).",
                "profile": None,
                "error": "profile_not_found",
            }
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        return {
            "hint": f"voice profile load failed: {type(e).__name__}: {e}",
            "profile": None,
            "error": "load_failed",
        }

    # Default register: if work_type=comms, prefer email register; otherwise natural
    natural = data.get("natural_register", "unspecified")
    register_key = "email" if work_type == "comms" else None
    platform_conf = (data.get("platforms") or {}).get(register_key or "") or {}
    effective_register = platform_conf.get("register") or natural

    return {
        "profile": data.get("name", voice),
        "profile_path": str(path),
        "register_effective": effective_register,
        "depth": platform_conf.get("depth", "medium"),
        "framing": platform_conf.get("framing", "unspecified"),
        "tendencies_foreground": data.get("tendencies") or [],
        "anti_patterns_suppress": data.get("anti_patterns") or [],
        "natural_register_fallback": natural,
        "hint": (
            "Apply these tendencies and avoid the anti-patterns when drafting "
            "in this register. The guidance is descriptive of the source "
            "voice, not aspirational — match what the person actually does."
        ),
    }


def _extract_numeric_value(value):
    """
    Extract numeric value from vector data.

    Handles multiple formats:
    - Simple float: 0.85
    - Nested dict: {"score": 0.85, "rationale": "...", "evidence": "..."}
    - String numbers: "0.85"

    Returns:
        float or None if value cannot be extracted
    """
    if isinstance(value, (int, float)):
        return float(value)
    elif isinstance(value, dict):
        # Extract 'score' key if present
        if "score" in value:
            return float(value["score"])
        # Extract 'value' key as fallback
        if "value" in value:
            return float(value["value"])
        # Try to find any numeric value in nested structure
        for _k, v in value.items():
            if isinstance(v, (int, float)):
                return float(v)
            elif isinstance(v, str) and v.replace(".", "").replace("-", "").isdigit():
                try:
                    return float(v)
                except ValueError:
                    continue
        # Try to convert entire dict to float if it looks like a single number
        for v in value.values():
            if isinstance(v, (int, float)):
                return float(v)
    elif isinstance(value, str):
        # Try to convert string to float
        try:
            return float(value)
        except ValueError:
            pass
    return None


def _extract_all_vectors(vectors):
    """
    Extract all numeric values from vectors dict, handling nested structures.
    Flattens nested dicts to extract individual vector values.

    Args:
        vectors: Dict containing vector data (simple or nested)

    Returns:
        Dict with all vector names mapped to numeric values

    Example:
        Input: {"engagement": 0.85, "foundation": {"know": 0.75, "do": 0.80}}
        Output: {"engagement": 0.85, "know": 0.75, "do": 0.80}
    """
    extracted = {}

    for key, value in vectors.items():
        if isinstance(value, dict):
            # Nested structure - recursively extract all sub-vectors
            for nested_key, nested_value in value.items():
                numeric_value = _extract_numeric_value(nested_value)
                if numeric_value is not None:
                    extracted[nested_key] = numeric_value
                else:
                    # Fallback to default if extraction fails
                    extracted[nested_key] = 0.5
        else:
            # Simple value - extract directly
            numeric_value = _extract_numeric_value(value)
            if numeric_value is not None:
                extracted[key] = numeric_value
            else:
                # Fallback to default if extraction fails
                extracted[key] = 0.5

    return extracted


def _retro_count_artifacts(cursor, session_id, transaction_id):
    """Count artifact types logged in this transaction. Returns dict."""
    artifact_counts = {}
    all_tables = [
        ("project_findings", "findings"),
        ("project_unknowns", "unknowns"),
        ("project_dead_ends", "dead_ends"),
        ("mistakes_made", "mistakes"),
        ("assumptions", "assumptions"),
        ("decisions", "decisions"),
    ]
    for table, label in all_tables:
        try:
            if transaction_id:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE session_id = ? AND transaction_id = ?",
                    (session_id, transaction_id),
                )
            else:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,))
            artifact_counts[label] = cursor.fetchone()[0]
        except Exception:
            artifact_counts[label] = 0
    return artifact_counts


def _retro_count_sources(cursor, session_id: str, transaction_id: str | None) -> int:
    """Count artifacts in this transaction that declare at least one source_ref.

    Sources are tracked via source_refs column (JSON list of source IDs from
    source-add). 0% adoption today (per goal d290bc3c) — this helper drives
    the nudge that surfaces when artifacts skip --source.
    """
    by_table = (
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "assumptions",
        "decisions",
    )
    total = 0
    for table in by_table:
        try:
            sql = (
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE session_id = ? "
                f"AND COALESCE(source_refs, '') NOT IN ('', '[]', 'null')"
            )
            params: tuple = (session_id,)
            if transaction_id:
                sql += " AND transaction_id = ?"
                params = (session_id, transaction_id)
            cursor.execute(sql, params)
            total += cursor.fetchone()[0]
        except Exception:
            # Column missing on some tables; ignore silently.
            pass
    return total


def _retro_count_edges(cursor, session_id: str, transaction_id: str | None) -> int:
    """Count artifacts in this transaction that have ≥1 edge in the canonical
    ``artifact_edges`` table (migration 041) — i.e. appear as an edge's ``from_id``.

    Reads ``artifact_edges``, the source BOTH ``log-artifacts`` and the POSTFLIGHT
    auto-edge writer persist to — NOT the legacy inline ``<type>_data.edges`` JSON,
    which neither actually writes, so it chronically reported a false ``0`` even
    when edges existed. Best-effort: a pre-041 DB with no ``artifact_edges``
    degrades to 0 (each table query raises and is skipped).
    """
    by_table = (
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "assumptions",
        "decisions",
    )
    total = 0
    for table in by_table:
        try:
            sql = (
                f"SELECT COUNT(*) FROM {table} t "
                "WHERE t.session_id = ? "
                "AND EXISTS (SELECT 1 FROM artifact_edges e WHERE e.from_id = t.id)"
            )
            params: tuple = (session_id,)
            if transaction_id:
                sql += " AND t.transaction_id = ?"
                params = (session_id, transaction_id)
            cursor.execute(sql, params)
            total += cursor.fetchone()[0]
        except Exception:
            # Table/column absent (old DB) or pre-041 (no artifact_edges); skip.
            pass
    return total


# --- Artifact-graph gate: three orthogonal scalar dimensions -----------------
# The extension's Sentinel config owns these as SLIDERS; the env vars are the
# transport into the CLI. This dict is the SINGLE source of truth for both the
# default values and the env-var names — deliberately not split across call
# sites (the engagement_gate's 12-site duplicated-default mess is the anti-
# pattern this avoids). Defaults keep a fresh install report-only + forgiving:
# the gate never blocks until a human dials strictness up.
_GATE_SCALARS = {
    # key                  (default, env var)
    "strictness": (0.25, "EMPIRICA_ARTIFACT_GRAPH_STRICTNESS"),
    "connectivity_floor": (0.50, "EMPIRICA_ARTIFACT_GRAPH_FLOOR"),
    "patience": (0.80, "EMPIRICA_ARTIFACT_GRAPH_PATIENCE"),
}


def _resolve_gate_scalars() -> dict:
    """Resolve the gate's three scalar dimensions from env (Sentinel sliders).

    - **strictness** — response intensity (drives ``_gate_response_for``).
    - **connectivity_floor** — fraction of artifacts that must carry ≥1 edge to
      count as satisfied.
    - **patience** — adaptive forgiveness (consecutive-miss escalation; consumed
      by the follow-up adaptive-enforcement work-stream, surfaced here so the
      extension can render it).

    Each is clamped to ``[0.0, 1.0]``; an absent or unparseable env value falls
    back to its default. Never raises — a bad slider value must not break the
    retrospective.
    """
    out: dict = {}
    for key, (default, env) in _GATE_SCALARS.items():
        raw = os.environ.get(env)
        if raw is None:
            out[key] = default
            continue
        try:
            out[key] = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            out[key] = default
    return out


def _gate_response_for(strictness: float) -> str:
    """Map the strictness scalar to a response band (the ONLY place this lives).

    A single monotonic ladder — quieter below, louder above:

    - ``silent`` (<0.05) — gate computes nothing, returns None (fully dialed down).
    - ``report`` (<0.40) — verdict attached, no pressure (default band).
    - ``warn``   (<0.70) — verdict + explicit "should weave more" language.
    - ``enforce`` (≥0.70) — verdict + would-block signal. Blocking itself is a
      follow-up work-stream; this build still returns ``enforced: False``.
    """
    if strictness < 0.05:
        return "silent"
    if strictness < 0.40:
        return "report"
    if strictness < 0.70:
        return "warn"
    return "enforce"


def _weave_gate_block(total_artifacts: int, edges_count: int) -> dict | None:
    """Artifact-graph gate verdict — scalar-driven (map work-stream 1 foundation).

    Reports a connectivity verdict from the transaction's (now-accurate) edge
    count vs artifact count, shaped by the three scalar dimensions resolved from
    the extension's Sentinel sliders (``_resolve_gate_scalars``). ``satisfied``
    is measured against ``connectivity_floor``; the loudness of the ``note`` is
    scaled by the ``strictness``-derived response band.

    **Enforcement is strictness-driven.** ``enforced`` is True ONLY at the
    ``enforce`` band (strictness ≥ 0.70) AND below the connectivity floor — at
    every lower band it stays ``False`` (report-only), so the default (strictness
    0.25) never blocks. A practice opts into enforcement by dialing strictness up;
    the consumer (the CHECK gate) blocks the noetic→praxic transition when
    ``enforced``. Returns None when strictness dials the gate fully ``silent``
    (<0.05) or there are no artifacts.
    """
    scalars = _resolve_gate_scalars()
    response = _gate_response_for(scalars["strictness"])
    if response == "silent" or total_artifacts < 1:
        return None
    connected = min(edges_count, total_artifacts)
    connected_ratio = connected / total_artifacts if total_artifacts else 0.0
    satisfied = connected_ratio >= scalars["connectivity_floor"]
    # The block signal: only the enforce band, only below the floor. Everything
    # else is report-only — the whole point of the ramp (#253 scalar surface).
    enforced = response == "enforce" and not satisfied
    if connected >= total_artifacts:
        verdict = "connected"
    elif connected > 0:
        verdict = "partial"
    else:
        verdict = "disconnected"
    pct = round(connected_ratio * 100)
    floor_pct = round(scalars["connectivity_floor"] * 100)
    mode = "ENFORCED — blocks" if enforced else f"{response}, report-only"
    if satisfied:
        note = (
            f"artifact-graph gate [{mode}]: "
            f"{connected}/{total_artifacts} artifacts connected ({pct}%) — "
            f"meets the {floor_pct}% floor."
        )
    else:
        lead = (
            "MUST weave more (transition blocked)"
            if enforced
            else ("SHOULD weave more" if response == "warn" else "consider weaving")
        )
        tail = "" if enforced else " Blocking activates only at strictness ≥ 0.70."
        note = (
            f"artifact-graph gate [{mode}]: "
            f"{connected}/{total_artifacts} artifacts connected ({pct}%), below the "
            f"{floor_pct}% floor — {lead}. Structural goal-edges are automatic; add "
            "semantic edges (log-artifacts nodes+edges, or --related-to / --edge on "
            f"any *-log) to raise connectivity.{tail}"
        )
    return {
        "scalars": scalars,
        "response": response,
        "verdict": verdict,
        "connected_ratio": round(connected_ratio, 3),
        "connected_artifacts": connected,
        "total_artifacts": total_artifacts,
        "satisfied": satisfied,
        "enforced": enforced,  # strictness-driven: True only at enforce band + below floor
        "note": note,
    }


def _weave_enforcement_block(session_id: str, transaction_id: str | None) -> dict | None:
    """Compute the artifact-graph weave-gate for THIS transaction, for the CHECK
    gate's enforce-half (map work-stream 1). Reuses the same artifact/edge
    counters the POSTFLIGHT retrospective uses. Returns the gate block (carrying
    the ``enforced`` flag) or None. Best-effort — any measurement error returns
    None, so a counting failure never blocks CHECK.
    """
    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()
        counts = _retro_count_artifacts(cursor, session_id, transaction_id)
        total_artifacts = sum(counts.values()) if isinstance(counts, dict) else int(counts)
        if total_artifacts < 1:
            return None
        edges = _retro_count_edges(cursor, session_id, transaction_id)
        return _weave_gate_block(total_artifacts, edges)
    except Exception:
        return None


def _maybe_add_weave_gate(cursor, session_id, transaction_id, retro: dict, total_artifacts: int) -> None:
    """Attach the report-only artifact-graph gate verdict to the retrospective.

    Kept out of ``_build_retrospective`` to hold that function's complexity down
    (mirrors ``_maybe_add_untriaged_notes``). Reuses the already-computed edge
    count when present, else counts once. Best-effort; no-op on any failure.
    """
    if total_artifacts < 1:
        return
    try:
        edges = retro.get("edges_with_artifacts")
        if edges is None:
            edges = _retro_count_edges(cursor, session_id, transaction_id)
        gate = _weave_gate_block(total_artifacts, edges)
        if gate:
            retro["weave_gate"] = gate
    except Exception:
        pass


def _build_retrospective(session_id: str, transaction_id: str | None) -> dict:
    """Build retrospective feedback: artifact breadth, commit discipline, completion hints.

    Returns dict with artifact_counts, optional breadth_note, commit_warning, completion_hint.
    Non-fatal -- returns empty dict on any error.
    """
    import subprocess as _sp

    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()

        artifact_counts = _retro_count_artifacts(cursor, session_id, transaction_id)
        retro: dict = {"artifact_counts": artifact_counts}

        types_used = [k for k, v in artifact_counts.items() if v > 0]
        types_missing = [k for k, v in artifact_counts.items() if v == 0]

        if len(types_used) <= 1 and sum(artifact_counts.values()) > 0:
            retro["breadth_note"] = (
                f"Only {', '.join(types_used) or 'no'} artifacts logged. "
                f"Missing: {', '.join(types_missing)}. "
                "Unlogged artifact types are ungrounded prediction domains — "
                "were there assumptions, decisions, dead-ends, or mistakes worth capturing?"
            )

        # Edge density nudge — surfaces when artifacts exist but no edges declared.
        total_artifacts = sum(artifact_counts.values())
        if total_artifacts >= 2:
            try:
                edges_count = _retro_count_edges(cursor, session_id, transaction_id)
                retro["edges_with_artifacts"] = edges_count
                if edges_count == 0:
                    retro["edge_density_note"] = (
                        f"{total_artifacts} artifacts logged with 0 declared edges. "
                        "Anchor them in the graph: --related-to <id> on any *-log command, "
                        "or --edge ID:RELATION for typed links. Unlinked artifacts are "
                        "invisible to the commit-context walker."
                    )
            except Exception:
                pass

        # Sources discipline nudge — surfaces when artifacts exist but no source_refs declared.
        if total_artifacts >= 2:
            try:
                sources_count = _retro_count_sources(cursor, session_id, transaction_id)
                retro["artifacts_with_sources"] = sources_count
                if sources_count == 0:
                    retro["sources_discipline_note"] = (
                        f"{total_artifacts} artifacts logged with 0 source_refs. "
                        "Where did the evidence come from? Use --source <id> on any *-log "
                        "command, or `empirica source-add` to register the source first. "
                        "Sourced artifacts get full provenance trail in audit + compliance."
                    )
            except Exception:
                pass

        # Artifact-graph gate verdict (report-only foundation, map work-stream 1).
        _maybe_add_weave_gate(cursor, session_id, transaction_id, retro, total_artifacts)

        try:
            _gr = _sp.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
            if _gr.returncode == 0 and _gr.stdout.strip():
                retro["commit_warning"] = (
                    "Uncommitted changes detected. Grounded calibration for change/state/do "
                    "will be based on committed work only — uncommitted edits are invisible."
                )
        except Exception:
            pass

        try:
            # Table is `goals`, not `project_goals` (pre-existing typo that
            # silently dropped this hint for the entire history of the file).
            # Column is `transaction_id` (set at activation; no separate
            # completed_transaction_id exists in schema).
            if transaction_id:
                cursor.execute(
                    "SELECT COUNT(*) FROM goals WHERE is_completed = 1 AND transaction_id = ?",
                    (transaction_id,),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM goals WHERE is_completed = 1 AND session_id = ?",
                    (session_id,),
                )
            goals_completed = cursor.fetchone()[0]
            if goals_completed > 0:
                retro["completion_hint"] = (
                    f"{goals_completed} goal(s) completed in this transaction — "
                    "completion for this transaction should be near 1.0."
                )
        except Exception:
            pass

        _maybe_add_deferred_proposals_note(cursor, session_id, retro)
        _maybe_add_untriaged_notes(cursor, session_id, transaction_id, retro)

        db.close()
        return retro
    except Exception as e:
        logger.debug(f"Retrospective feedback failed (non-fatal): {e}")
        return {}


def _maybe_add_untriaged_notes(cursor, session_id: str, transaction_id, retro: dict) -> None:
    """Surface scratchpad notes-to-self for triage at the retrospective.

    The POSTFLIGHT review moment: EVERY untriaged note for the PROJECT is
    surfaced (not just this transaction's) so the "capture now, classify later"
    backlog reliably resurfaces and gets promoted/cleared. Scoped by project_id
    (durable across transaction/session rotation — the transaction/session
    scoping used to strand cross-transaction notes); falls back to session_id
    when the session has no project. Metadata-only, non-fatal. Tolerates the
    table not existing on older DBs.
    """
    try:
        project_id = None
        try:
            prow = cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            project_id = prow[0] if prow else None
        except Exception:
            project_id = None  # sessions table absent (older DB) → session-scope fallback
        if project_id:
            rows = cursor.execute(
                "SELECT text, tag FROM notes WHERE project_id = ? AND triaged = 0 ORDER BY created_at",
                (project_id,),
            ).fetchall()
        else:
            rows = cursor.execute(
                "SELECT text, tag FROM notes WHERE session_id = ? AND triaged = 0 ORDER BY created_at",
                (session_id,),
            ).fetchall()
        if not rows:
            return
        retro["untriaged_notes"] = [{"text": r[0], "tag": r[1]} for r in rows]
        retro["untriaged_notes_hint"] = (
            f"{len(rows)} untriaged note(s)-to-self for this project. "
            "Promote any worth keeping to a finding/decision/goal, then "
            "`empirica note --clear`."
        )
    except Exception:
        pass  # notes table may not exist on older DBs — non-fatal


def _maybe_add_deferred_proposals_note(cursor, session_id: str, retro: dict) -> None:
    """Surface open goals derived from peer-AI proposals.

    Convention (per cortex-mailbox-poll skill): defer goals MUST be created
    with objective = "Process proposal prop_<id>: <title>". The query
    matches that exact prefix so planning goals that mention prop_* in
    their description (proposal references, doc filenames like
    PROPOSAL_*.md, etc.) don't get false-positive-flagged as defer goals.

    Scoped to the current project. Mutates `retro` in-place; non-fatal
    on any error.

    Earlier shape used `LIKE '%prop_%' OR description LIKE '%prop_%'`
    which was over-broad — surfaced 16 false positives in the first
    transaction that fired it (planning goals from weeks prior). Tightened
    to convention-prefix only 2026-05-17.
    """
    try:
        cursor.execute(
            """
            SELECT g.id, g.objective FROM goals g
            JOIN sessions s ON g.session_id = s.session_id
            WHERE g.is_completed = 0
              AND s.project_id = (
                SELECT project_id FROM sessions WHERE session_id = ?
              )
              AND g.objective LIKE 'Process proposal prop_%'
            ORDER BY g.created_timestamp DESC
        """,
            (session_id,),
        )
        deferred = cursor.fetchall()
        if not deferred:
            return
        listing = "\n".join(f"  - {gid[:8]}: {obj[:90]}" for gid, obj in deferred[:10])
        more = f"\n  ... + {len(deferred) - 10} more" if len(deferred) > 10 else ""
        retro["deferred_proposals_note"] = (
            f"{len(deferred)} proposal-derived goal(s) still open in this project. "
            "These came in from peer AIs and were deferred during in-flight "
            "work. Action or ack them now — without follow-through the source "
            "AI's outbox stays visibly stalled (the half-handshake bug class).\n"
            f"{listing}{more}"
        )
        retro["deferred_proposals_count"] = len(deferred)
    except Exception:
        pass


def _soft_run(stage_name: str, warnings: list, fn, *args, **kwargs):
    """Run a downstream POSTFLIGHT stage; collect failures as warnings.

    Stages 5-7 (bus, beliefs, storage, compliance, cortex sync) are
    informational — they enrich the result but their failures must not
    erase the reflex that already landed in stages 3-4. Pre-fix, an
    exception in any of them surfaced as exit-code-1 with persisted=false
    even though the loop had actually closed (ghost-success bug).

    Post-fix: catch, log, accumulate into warnings[].

    SystemExit is caught explicitly. Some library helpers (like
    `cli.utils.project_resolver.resolve_project_id`) call `sys.exit(1)`
    on miss instead of raising — and SystemExit derives from
    BaseException, not Exception. Without explicit handling, those
    sys.exit calls would walk straight through every `except Exception`
    above us and kill POSTFLIGHT, defeating the soft-stage contract.
    See #95 (pschwinger) for the repro.

    KeyboardInterrupt is intentionally NOT caught — it's a user signal
    to stop, and we should let it propagate through the whole call stack.
    """
    try:
        return fn(*args, **kwargs)
    except SystemExit as e:
        # Library code that called sys.exit(N) — treat as a soft failure
        # equivalent to a normal exception. Do NOT exit the process.
        warnings.append(
            {
                "stage": stage_name,
                "error_type": "SystemExit",
                "error": f"library called sys.exit({e.code!r})",
            }
        )
        logger.warning(f"POSTFLIGHT {stage_name} soft-failed: SystemExit({e.code!r})")
        return None
    except Exception as e:
        warnings.append(
            {
                "stage": stage_name,
                "error_type": type(e).__name__,
                "error": str(e),
            }
        )
        logger.warning(f"POSTFLIGHT {stage_name} soft-failed: {type(e).__name__}: {e}")
        return None
