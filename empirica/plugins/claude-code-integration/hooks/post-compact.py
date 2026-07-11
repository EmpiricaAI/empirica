#!/usr/bin/env python3
"""
Empirica PostCompact Hook - Phase-Aware Re-grounding

Compaction is routine and lossless under empirica discipline: the
conversation gets summarized, while the knowledge that matters lives in
the durable layer (goals, artifacts, breadcrumbs, git notes) and is
re-loaded here via bootstrap. This hook detects the transaction phase
state and routes appropriately:

1. If old session is COMPLETE (has POSTFLIGHT) → New session + PREFLIGHT
2. If old session is INCOMPLETE (mid-work) → CHECK gate on old session

Key insight: Compact can happen at ANY point in the transaction cycle.
The re-grounding action depends on WHERE in the cycle compact occurred.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Import shared utilities from plugin lib
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from project_resolver import _get_instance_suffix, find_project_root, get_instance_id

# Import epistemic summarizer for confidence-weighted context
try:
    from epistemic_summarizer import format_epistemic_focus

    EPISTEMIC_SUMMARIZER_AVAILABLE = True
except ImportError:
    EPISTEMIC_SUMMARIZER_AVAILABLE = False


def _temporal_trail_section(project_path: str | Path | None) -> str:
    """Build a one-line trail pointing at the commit-context CLI.

    Counts artifact notes under refs/notes/empirica/<type>/ for the
    first-class types only. Skips silently on any failure.
    """
    try:
        cwd = Path(project_path) if project_path else Path.cwd()
        if not (cwd / ".git").exists():
            return ""
        types = ("findings", "decisions", "dead_ends", "mistakes", "unknowns", "assumptions", "goals", "cascades")
        total = 0
        for t in types:
            r = subprocess.run(
                ["git", "for-each-ref", "--format=%(refname)", f"refs/notes/empirica/{t}/"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                total += sum(1 for line in r.stdout.split("\n") if line.strip())
        if total == 0:
            return ""
        return (
            f"\n**Temporal trail:** {total:,} artifact git notes anchored to commits. "
            f"Query: `empirica commit-context <sha>` or "
            f"`empirica commit-context --range HEAD~10..HEAD` or "
            f"`empirica commit-context --since <date>`.\n"
        )
    except Exception:
        return ""


def _write_active_transaction_for_new_conversation(
    active_transaction: dict,
    project_path: str,
    instance_id: str | None = None,
    claude_session_id: str | None = None,
) -> bool:
    """
    Write active_transaction file for the NEW conversation after compaction.

    This is CRITICAL: without this file, Sentinel and statusline cannot find
    the current transaction's state. They fall back to querying without
    transaction_id filter, potentially picking up wrong CHECK data from
    older transactions.

    The pre-compact hook captures the transaction in the snapshot.
    This function writes it back to the filesystem for the new Claude process.
    """
    if not active_transaction:
        return False

    try:
        suffix = _get_instance_suffix()

        if project_path:
            tx_file = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
        else:
            tx_file = Path.home() / ".empirica" / f"active_transaction{suffix}.json"

        tx_file.parent.mkdir(parents=True, exist_ok=True)

        # Update timestamp but preserve original transaction data. Carry the
        # DURABLE claude_session_id (B3) so the firewall can resolve this
        # transaction even though the ephemeral instance suffix / empirica
        # session_id may have rotated across the compaction boundary. Prefer the
        # caller's value; fall back to whatever the snapshot captured.
        tx_data = {
            "transaction_id": active_transaction.get("transaction_id"),
            "session_id": active_transaction.get("session_id"),
            "claude_session_id": claude_session_id or active_transaction.get("claude_session_id"),
            "preflight_timestamp": active_transaction.get("preflight_timestamp"),
            "status": active_transaction.get("status", "open"),
            "project_path": project_path,
            "updated_at": datetime.now().timestamp(),
        }

        with open(tx_file, "w") as f:
            json.dump(tx_data, f, indent=2)

        return True
    except Exception:
        return False


def _write_active_work_for_new_conversation(
    claude_session_id: str, project_path: str, empirica_session_id: str, instance_id: str | None = None
) -> bool:
    """
    Write active_work file for the NEW conversation after compaction.

    This is CRITICAL: without this file, all subsequent CLI commands in the
    new conversation (project-bootstrap, finding-log, goals-create, etc.)
    will fail to resolve the correct project and fall through to CWD-based
    resolution, which poisons context with the wrong project.

    The pre-compact hook writes compact_handoff for project resolution,
    but this function writes active_work for CLI command resolution.

    NOTE: Even if claude_session_id is null, we still write instance_projects
    because instance_id-based isolation still works. The active_work file
    requires claude_session_id as its key, so that's skipped if null.
    """
    try:
        folder_name = Path(project_path).name if project_path else None

        # Write active_work file only if we have claude_session_id (it's the filename key)
        if claude_session_id:
            active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
            work_data = {
                "project_path": project_path,
                "folder_name": folder_name,
                "claude_session_id": claude_session_id,
                "empirica_session_id": empirica_session_id,
                "source": "post-compact",
                "timestamp": datetime.now().isoformat(),
                "timestamp_epoch": datetime.now().timestamp(),
            }
            with open(active_work_file, "w") as f:
                json.dump(work_data, f, indent=2)
            os.chmod(active_work_file, 0o600)

        # ALWAYS update instance_projects - instance_id isolation works even without claude_session_id
        # This is the primary isolation mechanism for multi-pane tmux setups
        if instance_id:
            instance_file = Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json"
            instance_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            instance_data = {
                "project_path": project_path,
                "claude_session_id": claude_session_id,  # May be null, that's OK
                "empirica_session_id": empirica_session_id,
                "timestamp": datetime.now().isoformat(),
            }
            with open(instance_file, "w") as f:
                json.dump(instance_data, f, indent=2)
            os.chmod(instance_file, 0o600)

        return True
    except Exception:
        return False


def _load_calibration_from_breadcrumbs_yaml() -> str:
    """Load calibration biases from .breadcrumbs.yaml for post-compact injection.

    Previously handled by session-start.sh bash script.
    Returns formatted calibration text or empty string.
    """
    git_root = None
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        git_root = result.stdout.strip()
    except Exception:
        pass

    config_path = None
    if Path(".breadcrumbs.yaml").exists():
        config_path = Path(".breadcrumbs.yaml")
    elif git_root and Path(git_root, ".breadcrumbs.yaml").exists():
        config_path = Path(git_root, ".breadcrumbs.yaml")

    if not config_path:
        return ""

    try:
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        calibration = config.get("calibration")
        if not calibration:
            return ""

        # Format calibration for prompt injection
        lines = ["### Calibration Biases (from .breadcrumbs.yaml)"]
        if isinstance(calibration, dict):
            for key, value in calibration.items():
                if isinstance(value, dict):
                    lines.append(f"**{key}:**")
                    for k, v in value.items():
                        lines.append(f"  {k}: {v}")
                else:
                    lines.append(f"  {key}: {value}")
        return "\n".join(lines)
    except Exception:
        return ""


def _resolve_project_and_setup(claude_session_id: str) -> tuple:
    """Resolve practice root, set CWD, and compute instance_id.

    Practice (project/ai_id) resolves from the practitioner cache
    (active_work/instance_projects) and — when the harness declares CWD is the
    verified practice via ``EMPIRICA_CWD_RELIABLE=true`` — from the filesystem
    (CWD/git-root) too. This makes session-boundary resolution vendor-agnostic (a
    fresh practitioner with an empty cache still resolves, e.g. codex/ecodex)
    without re-opening the cross-project-bleed bug (KNOWN_ISSUES 11.10): in a
    multiplexer where CWD is the empirica *launch* dir rather than the user's
    project, the flag is unset, so the CWD/git fallback stays off and we never
    grab the wrong practice.

    If nothing resolves, exits 0 — a session-boundary hook with no resolvable
    practice is a no-op, not a failure (matches pre-compact.py). Returns
    (project_root, instance_id) on success.
    """
    # Harness-declared CWD reliability (Karson's #91 contract). session-init sets
    # EMPIRICA_CWD_RELIABLE=true after chdir'ing to the verified practice; vendor
    # harnesses (ecodex/codex) set it likewise. Absent it, CWD is untrusted (could
    # be the launch dir) → filesystem fallback stays off.
    cwd_reliable = os.environ.get("EMPIRICA_CWD_RELIABLE", "").lower() == "true"
    project_root = find_project_root(
        claude_session_id,
        check_compact_handoff=True,
        allow_cwd_fallback=cwd_reliable,
        allow_git_root=cwd_reliable,
    )
    if project_root is None:
        # Graceful no-op (not exit 1): a boundary hook that can't resolve a
        # practice has nothing to do — surface a stderr diagnostic and skip.
        print(
            f"post-compact: no resolvable empirica practice "
            f"(session={claude_session_id}, cwd_reliable={cwd_reliable}) — skipping",
            file=sys.stderr,
        )
        print(json.dumps({}))
        sys.exit(0)
    os.chdir(project_root)
    instance_id = get_instance_id()
    sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
    return project_root, instance_id


def _try_memory_swap(claude_session_id: str) -> None:
    """Attempt memory swap for cross-project CWD mismatch. Non-fatal."""
    try:
        from empirica.utils.memory_swap import maybe_swap_for_active_transaction

        swap_result = maybe_swap_for_active_transaction(claude_session_id=claude_session_id)
        if swap_result.get("action") == "swapped":
            print(f"{swap_result.get('message', '')}", file=sys.stderr)
    except Exception as e:
        print(f"memory swap failed (non-fatal): {e}", file=sys.stderr)


def _extract_pre_snapshot_data(pre_snapshot: dict) -> tuple:
    """Extract vectors, reasoning, transaction, and counters from pre-compact snapshot.

    Returns (pre_vectors, pre_reasoning, active_transaction, hook_counters).
    """
    if not pre_snapshot:
        return {}, None, None, None
    pre_vectors = pre_snapshot.get("checkpoint", {}) or (pre_snapshot.get("live_state") or {}).get("vectors", {})
    pre_reasoning = (pre_snapshot.get("live_state") or {}).get("reasoning")
    active_transaction = pre_snapshot.get("active_transaction")
    hook_counters = pre_snapshot.get("hook_counters")
    return pre_vectors, pre_reasoning, active_transaction, hook_counters


def _enrich_dynamic_context(dynamic_context: dict, active_transaction: dict, pre_snapshot: dict) -> None:
    """Inject transaction, last_task, git_context, and calibration into dynamic_context."""
    if active_transaction:
        dynamic_context["active_transaction"] = active_transaction
    if pre_snapshot:
        dynamic_context["last_task"] = pre_snapshot.get("last_task", "")
        dynamic_context["git_context"] = pre_snapshot.get("git_context", {})
    calibration_text = _load_calibration_from_breadcrumbs_yaml()
    if calibration_text:
        dynamic_context["calibration_biases"] = calibration_text


def _lookup_project_id_from_trajectory(project_root: Path) -> str | None:
    """Resolve canonical project_id from workspace.db using trajectory_path.

    The trajectory_path is the project's `.empirica` directory — that's
    the unique key in workspace.db's global_projects table. Used at
    session boundaries (post-compact, session-init) where cwd is reliable.

    Returns project_id or None if the project isn't registered.
    """
    try:
        import sqlite3

        ws_db = Path.home() / ".empirica" / "workspace" / "workspace.db"
        if not ws_db.exists():
            return None
        trajectory = str(Path(project_root) / ".empirica")
        conn = sqlite3.connect(str(ws_db))
        try:
            cursor = conn.execute("SELECT id FROM global_projects WHERE trajectory_path = ?", (trajectory,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _auto_heal_session(tx_session_id: str, ai_id: str, project_id: str, instance_id: str, project_root: Path) -> None:
    """Auto-heal missing session + stale project_id in project DB.

    Two-stage heal at session boundaries (post-compact, session-init):
      1. Session-row existence (KNOWN_ISSUES 11.24 — cross-project resume)
      2. Session-row project_id correctness (extends 11.24 to project_id grain)

    Stage 2 catches the ghost-project_id pattern: session was originally
    created with a wrong/stale project_id (cross-project --resume,
    ambiguous folder_name match, tmux pane reuse without project-switch),
    and that wrong id rides forever even when file-context recovers.

    Non-fatal — every step logs to stderr and continues on error.
    """
    try:
        from empirica.data.session_database import SessionDatabase
        from empirica.utils.session_resolver import _validate_session_in_db

        # Stage 1: ensure session row exists in project-local DB
        if not _validate_session_in_db(tx_session_id, project_path=str(project_root)):
            db = SessionDatabase()
            healed = db.ensure_session_exists(
                session_id=tx_session_id,
                ai_id=ai_id,
                project_id=project_id,
                instance_id=instance_id,
            )
            db.close()
            if healed:
                print(f"post-compact: auto-healed missing session {tx_session_id[:8]} in project DB", file=sys.stderr)

        # Stage 2: validate session.project_id against workspace.db canonical
        # cwd is reliable at session boundaries — workspace.db lookup grounds
        # the heal in registered project trajectory_path, never folder_name.
        expected_project_id = _lookup_project_id_from_trajectory(project_root)
        if expected_project_id:
            db = SessionDatabase()
            try:
                status = db.heal_session_project_id(
                    session_id=tx_session_id,
                    expected_project_id=expected_project_id,
                )
                if status == "healed":
                    print(
                        f"post-compact: healed session {tx_session_id[:8]} project_id "
                        f"-> {expected_project_id[:8]} (ghost-project_id pattern)",
                        file=sys.stderr,
                    )
            finally:
                db.close()
        else:
            print(
                f"post-compact: skipped project_id heal — {project_root}/.empirica "
                "not registered in workspace.db (no trustworthy expected_project_id)",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"post-compact: auto-heal skipped ({type(e).__name__}: {e})", file=sys.stderr)


def _restore_hook_counters(hook_counters: dict, project_root: Path) -> None:
    """Restore hook counters file from pre-compact snapshot. Non-fatal."""
    if not hook_counters:
        return
    try:
        suffix = _get_instance_suffix()
        counters_file = Path(str(project_root)) / ".empirica" / f"hook_counters{suffix}.json"
        with open(counters_file, "w") as f:
            json.dump(hook_counters, f, indent=2)
    except Exception:
        pass


def _handle_open_transaction(
    active_transaction: dict,
    pre_vectors: dict,
    dynamic_context: dict,
    claude_session_id: str,
    empirica_session: str,
    ai_id: str,
    instance_id: str,
    project_root: Path,
    hook_counters: dict,
) -> tuple:
    """Handle CONTINUE_TRANSACTION path: open transaction survives compaction.

    Returns (recovery_prompt, action_required, empirica_session).
    """
    recovery_prompt = _generate_transaction_continue_prompt(
        pre_vectors=pre_vectors, dynamic_context=dynamic_context, active_transaction=active_transaction
    )
    tx_session_id = active_transaction.get("session_id") or empirica_session

    project_id = dynamic_context.get("session_context", {}).get("project_id")
    _auto_heal_session(tx_session_id, ai_id, project_id, instance_id, project_root)

    _write_active_work_for_new_conversation(
        claude_session_id=claude_session_id,
        project_path=str(project_root),
        empirica_session_id=tx_session_id,
        instance_id=instance_id,
    )
    _write_active_transaction_for_new_conversation(
        active_transaction=active_transaction,
        project_path=str(project_root),
        instance_id=instance_id,
        claude_session_id=claude_session_id,
    )
    _restore_hook_counters(hook_counters, project_root)
    return recovery_prompt, "CONTINUE_TRANSACTION", tx_session_id


def _handle_complete_session(
    pre_vectors: dict,
    dynamic_context: dict,
    empirica_session: str,
    ai_id: str,
    claude_session_id: str,
    instance_id: str,
    project_root: Path,
) -> tuple:
    """Handle NEW_SESSION_PREFLIGHT path: previous session was complete.

    Returns (recovery_prompt, action_required, empirica_session, session_bootstrap).
    """
    project_id = dynamic_context.get("session_context", {}).get("project_id")
    session_bootstrap = _create_session_and_bootstrap(ai_id, project_id)

    recovery_prompt = _generate_new_session_prompt(
        pre_vectors=pre_vectors,
        dynamic_context=dynamic_context,
        old_session_id=empirica_session,
        ai_id=ai_id,
        session_bootstrap=session_bootstrap,
    )

    if session_bootstrap.get("session_id"):
        empirica_session = session_bootstrap["session_id"]

    # Defense-in-depth: validate the freshly-created session's project_id
    # against workspace.db canonical. Fresh sessions usually get the right
    # binding from session-create's resolution chain, but if folder_name
    # ambiguity or stale TTY context misfired, this catches it before any
    # work attaches to the wrong project.
    _auto_heal_session(empirica_session, ai_id, project_id, instance_id, project_root)

    _write_active_work_for_new_conversation(
        claude_session_id=claude_session_id,
        project_path=str(project_root),
        empirica_session_id=empirica_session,
        instance_id=instance_id,
    )
    return recovery_prompt, "NEW_SESSION_PREFLIGHT", empirica_session, session_bootstrap


def _handle_incomplete_session(
    pre_vectors: dict,
    pre_reasoning: str,
    dynamic_context: dict,
    empirica_session: str,
    claude_session_id: str,
    instance_id: str,
    project_root: Path,
) -> tuple:
    """Handle CHECK_GATE path: session incomplete, need CHECK to continue.

    Returns (recovery_prompt, action_required).
    """
    recovery_prompt = _generate_check_prompt(
        pre_vectors=pre_vectors, pre_reasoning=pre_reasoning, dynamic_context=dynamic_context
    )
    _write_active_work_for_new_conversation(
        claude_session_id=claude_session_id,
        project_path=str(project_root),
        empirica_session_id=empirica_session,
        instance_id=instance_id,
    )
    return recovery_prompt, "CHECK_GATE"


def _build_output_payload(
    recovery_prompt: str,
    empirica_session: str,
    action_required: str,
    phase_state: dict,
    pre_vectors: dict,
    pre_reasoning: str,
    pre_snapshot: dict,
    potential_drift: dict,
    session_bootstrap: dict,
) -> dict:
    """Build the injection payload using Claude Code's hook format."""
    return {
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": recovery_prompt},
        "empirica_session_id": empirica_session,
        "action_required": action_required,
        "phase_state": phase_state,
        "pre_compact_state": {
            "vectors": pre_vectors,
            "reasoning": pre_reasoning,
            "timestamp": pre_snapshot.get("timestamp") if pre_snapshot else None,
        },
        "potential_drift_warning": potential_drift,
        "session_bootstrap": session_bootstrap,
    }


def _cleanup_compact_handoff(instance_id: str) -> None:
    """Clean up compact handoff file -- consumed, no longer needed."""
    try:
        if instance_id:
            handoff_suffix = _get_instance_suffix()
            handoff_file = Path.home() / ".empirica" / f"compact_handoff{handoff_suffix}.json"
            if handoff_file.exists():
                handoff_file.unlink()
    except Exception:
        pass


def main():
    """Post-compact recovery orchestrator.

    Routes recovery based on transaction/phase state:
    open transaction -> continue, complete session -> new PREFLIGHT,
    incomplete session -> CHECK gate.
    """
    hook_input = json.loads(sys.stdin.read())
    claude_session_id = hook_input.get("session_id")

    # Stage 1: Resolve project and setup environment
    project_root, instance_id = _resolve_project_and_setup(claude_session_id)
    _try_memory_swap(claude_session_id)

    # Stage 2: Find active session
    empirica_session = _get_empirica_session(claude_session_id=claude_session_id)
    if not empirica_session:
        print(json.dumps({"ok": True, "skipped": True, "reason": "No active Empirica session"}))
        sys.exit(0)

    # Precedence: explicit env override > canonical resolver (project.yaml →
    # basename) > legacy 'claude-code'. The wrong practice here re-anchors the
    # post-compact context to the wrong calibration trajectory.
    ai_id = os.getenv("EMPIRICA_AI_ID")
    if not ai_id:
        try:
            from empirica.utils.session_resolver import InstanceResolver as R

            ai_id = R.ai_id()
        except Exception:
            ai_id = None
    ai_id = ai_id or "claude-code"

    # Stage 3: Load state
    phase_state = _get_session_phase_state(empirica_session)
    pre_snapshot = _load_pre_snapshot()
    pre_vectors, pre_reasoning, active_transaction, hook_counters = _extract_pre_snapshot_data(pre_snapshot)

    dynamic_context = _load_dynamic_context(empirica_session, ai_id, pre_snapshot)
    _enrich_dynamic_context(dynamic_context, active_transaction, pre_snapshot)

    # Stage 4: Route based on phase state
    session_bootstrap = None

    if active_transaction and active_transaction.get("status") == "open":
        recovery_prompt, action_required, empirica_session = _handle_open_transaction(
            active_transaction,
            pre_vectors,
            dynamic_context,
            claude_session_id,
            empirica_session,
            ai_id,
            instance_id,
            project_root,
            hook_counters,
        )
    elif phase_state.get("is_complete"):
        recovery_prompt, action_required, empirica_session, session_bootstrap = _handle_complete_session(
            pre_vectors, dynamic_context, empirica_session, ai_id, claude_session_id, instance_id, project_root
        )
    else:
        recovery_prompt, action_required = _handle_incomplete_session(
            pre_vectors, pre_reasoning, dynamic_context, empirica_session, claude_session_id, instance_id, project_root
        )

    # Stage 5: Build output and emit
    potential_drift = _calculate_potential_drift(pre_vectors)
    output = _build_output_payload(
        recovery_prompt,
        empirica_session,
        action_required,
        phase_state,
        pre_vectors,
        pre_reasoning,
        pre_snapshot,
        potential_drift,
        session_bootstrap,
    )

    _cleanup_compact_handoff(instance_id)
    print(json.dumps(output), file=sys.stdout)
    _print_user_message(pre_vectors, dynamic_context, potential_drift, phase_state, ai_id, session_bootstrap)
    sys.exit(0)


def _get_empirica_session(claude_session_id: str | None = None):
    """
    Find the active Empirica session using priority chain.

    Priority:
    0. active_work file's empirica_session_id (set by project-switch, instance-aware)
    1. Database query for latest active session (fallback)
    """
    # Priority 0: Check active_work file (authoritative for this Claude instance)
    if claude_session_id:
        try:
            active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
            if active_work_file.exists():
                with open(active_work_file) as f:
                    work_data = json.load(f)
                empirica_session_id = work_data.get("empirica_session_id")
                if empirica_session_id:
                    return empirica_session_id
        except Exception:
            pass

    # Priority 1: Database query (fallback)
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        # Canonical ai_id first (project.yaml → basename), then legacy
        # 'claude-code', then None wildcard. Prepend the resolved id ONLY when
        # truthy — a leading None would short-circuit to the wildcard.
        resolved = R.ai_id()
        ai_patterns = ([resolved] if resolved else []) + ["claude-code", None]
        for ai_pattern in ai_patterns:
            try:
                return R.latest_session_id(ai_id=ai_pattern, active_only=True)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _get_session_phase_state(session_id: str) -> dict:
    """
    Detect the transaction phase state of a session.

    Returns:
        {
            "has_preflight": bool,
            "has_postflight": bool,
            "last_phase": str or None,
            "is_complete": bool  # True if session has POSTFLIGHT
        }
    """
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Get all phases for this session
        cursor.execute(
            """
            SELECT phase, timestamp
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
        """,
            (session_id,),
        )
        rows = cursor.fetchall()
        db.close()

        if not rows:
            return {"has_preflight": False, "has_postflight": False, "last_phase": None, "is_complete": False}

        phases = [r[0] for r in rows]
        last_phase = phases[0] if phases else None

        # Session is "complete" if the LAST phase was POSTFLIGHT
        # (not just if it ever had a POSTFLIGHT - could be in cycle 2+)
        is_complete = last_phase == "POSTFLIGHT"

        return {
            "has_preflight": "PREFLIGHT" in phases,
            "has_postflight": "POSTFLIGHT" in phases,
            "last_phase": last_phase,
            "is_complete": is_complete,
        }
    except Exception as e:
        return {
            "has_preflight": False,
            "has_postflight": False,
            "last_phase": None,
            "is_complete": False,
            "error": str(e),
        }


def _load_pre_snapshot():
    """Load the most recent pre-compact snapshot"""
    try:
        ref_docs_dir = Path.cwd() / ".empirica" / "ref-docs"
        snapshots = sorted(ref_docs_dir.glob("pre_summary_*.json"), reverse=True)
        if snapshots:
            with open(snapshots[0]) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _load_dynamic_context(session_id: str, ai_id: str, pre_snapshot: dict) -> dict:
    """
    Load DYNAMIC context - only what's relevant for re-grounding.

    NOT everything that ever was - just:
    1. Active goals (what was being worked on)
    2. Recent findings from THIS session (last learnings)
    3. Unresolved unknowns (open questions)
    4. Critical dead ends (mistakes to avoid)
    """
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Get the session's project_id
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        project_id = row[0] if row else None

        context: dict[str, Any] = {
            "active_goals": [],
            "recent_findings": [],
            "open_unknowns": [],
            "critical_dead_ends": [],
            "session_context": {},
        }

        if not project_id:
            db.close()
            return context

        # 1. Active goals (incomplete, high priority)
        cursor.execute(
            """
            SELECT id, objective, status, scope, created_timestamp
            FROM goals
            WHERE project_id = ? AND status IN ('active', 'in_progress', 'blocked')
            ORDER BY created_timestamp DESC LIMIT 3
        """,
            (project_id,),
        )
        for row in cursor.fetchall():
            context["active_goals"].append(
                {"id": row[0], "objective": row[1], "status": row[2], "scope": row[3], "created_timestamp": row[4]}
            )

        # 1b. Load subtasks for each active goal (for continuity across sessions)
        context["pending_subtasks"] = []
        for goal in context["active_goals"]:
            cursor.execute(
                """
                SELECT id, description, status, epistemic_importance, created_timestamp
                FROM subtasks
                WHERE goal_id = ? AND status != 'completed'
                ORDER BY
                    CASE epistemic_importance
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        ELSE 4
                    END,
                    created_timestamp DESC
                LIMIT 5
            """,
                (goal["id"],),
            )
            subtasks = []
            for st_row in cursor.fetchall():
                subtask = {
                    "id": st_row[0],
                    "description": st_row[1],
                    "status": st_row[2],
                    "importance": st_row[3],
                    "created_timestamp": st_row[4],
                    "goal_id": goal["id"],
                    "goal_objective": goal["objective"][:50],  # Truncate for context
                }
                subtasks.append(subtask)
                # Also add to flat list for epistemic ranking
                context["pending_subtasks"].append(subtask)
            goal["subtasks"] = subtasks

        # 2. Recent findings (broader retrieval — epistemic_summarizer handles ranking)
        cursor.execute(
            """
            SELECT finding, impact, created_timestamp
            FROM project_findings
            WHERE project_id = ?
            ORDER BY created_timestamp DESC LIMIT 10
        """,
            (project_id,),
        )
        for row in cursor.fetchall():
            context["recent_findings"].append(
                {"finding": row[0], "impact": row[1], "when": str(row[2])[:19] if row[2] else None}
            )

        # 3. Unresolved unknowns (open questions you need to address)
        cursor.execute(
            """
            SELECT unknown, impact, created_timestamp
            FROM project_unknowns
            WHERE project_id = ? AND is_resolved = 0
            ORDER BY impact DESC, created_timestamp DESC LIMIT 10
        """,
            (project_id,),
        )
        for row in cursor.fetchall():
            context["open_unknowns"].append({"unknown": row[0], "impact": row[1]})

        # 4. Critical dead ends (mistakes to avoid)
        cursor.execute(
            """
            SELECT approach, why_failed, created_timestamp
            FROM project_dead_ends
            WHERE project_id = ?
            ORDER BY created_timestamp DESC LIMIT 5
        """,
            (project_id,),
        )
        for row in cursor.fetchall():
            context["critical_dead_ends"].append(
                {"approach": row[0], "why_failed": row[1], "created_timestamp": row[2] if len(row) > 2 else None}
            )

        # 5. Session context (what was happening)
        context["session_context"] = {"session_id": session_id, "ai_id": ai_id, "project_id": project_id}

        db.close()
        return context

    except Exception as e:
        return {
            "error": str(e),
            "active_goals": [],
            "recent_findings": [],
            "open_unknowns": [],
            "critical_dead_ends": [],
        }


def _run_cli_json(cmd: list, timeout: int = 15) -> dict | None:
    """Run an empirica CLI command and return parsed JSON output.

    Returns parsed JSON dict on success, None on failure.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    return None


def _fetch_optional_context(project_id: str, result: dict) -> None:
    """Fetch optional context (memory, related goals, stale goals) from Qdrant.

    All fetches are non-fatal -- failures are silently ignored.
    """
    if not project_id:
        return

    # Memory context from Qdrant
    memory = _run_cli_json(
        [
            "empirica",
            "project-search",
            "--project-id",
            project_id,
            "--task",
            "current context and recent work",
            "--output",
            "json",
        ]
    )
    if memory:
        result["memory_context"] = memory

    # Semantic search for related goals
    goals = _run_cli_json(
        [
            "empirica",
            "goals-search",
            "current work in progress",
            "--project-id",
            project_id,
            "--status",
            "in_progress",
            "--limit",
            "5",
            "--output",
            "json",
        ]
    )
    if goals and goals.get("results"):
        result["related_goals"] = goals["results"]

    # Stale goals (marked during pre-compact)
    stale = _run_cli_json(["empirica", "goals-get-stale", "--project-id", project_id, "--output", "json"], timeout=10)
    if stale and stale.get("stale_goals"):
        result["stale_goals"] = stale["stale_goals"]


def _create_session_and_bootstrap(ai_id: str, project_id: str | None = None) -> dict:
    """Create a new session AND run project-bootstrap in one step.

    Orchestrates: session creation, bootstrap, and optional Qdrant context fetches.

    Returns dict with session_id, bootstrap_output, memory_context, error.
    """
    result = {"session_id": None, "bootstrap_output": None, "memory_context": None, "error": None}

    try:
        # Step 1: Create new session
        create_output = _run_cli_json(["empirica", "session-create", "--ai-id", ai_id, "--output", "json"])
        if not create_output:
            result["error"] = "session-create failed or returned invalid JSON"
            return result

        new_session_id = create_output.get("session_id")
        if not new_session_id:
            result["error"] = "session-create returned no session_id"
            return result
        result["session_id"] = new_session_id

        # Step 2: Run project-bootstrap
        bootstrap = _run_cli_json(
            ["empirica", "project-bootstrap", "--session-id", new_session_id, "--output", "json"], timeout=30
        )
        if bootstrap:
            result["bootstrap_output"] = bootstrap

        # Steps 3-5: Optional context fetches (non-fatal)
        _fetch_optional_context(project_id, result)

    except subprocess.TimeoutExpired:
        result["error"] = "Command timed out"
    except Exception as e:
        result["error"] = str(e)

    return result


def _generate_new_session_prompt(
    pre_vectors: dict, dynamic_context: dict, old_session_id: str, ai_id: str, session_bootstrap: dict | None = None
) -> str:
    """
    Generate prompt for NEW session + PREFLIGHT when old session was complete.

    This is the correct path when compact happens AFTER POSTFLIGHT - the old
    session is done, we need a fresh start with proper baseline.

    If session_bootstrap is provided, the session was already created and bootstrapped
    by the hook - AI just needs to do PREFLIGHT with the loaded context.
    """
    pre_know = pre_vectors.get("know", "N/A")
    pre_unc = pre_vectors.get("uncertainty", "N/A")

    # Determine session_id for retrieval guidance
    new_session_id = None
    if session_bootstrap and session_bootstrap.get("session_id"):
        new_session_id = session_bootstrap["session_id"]

    # Use epistemic summarizer for confidence-weighted ranking (no chronological fallback)
    if EPISTEMIC_SUMMARIZER_AVAILABLE:
        epistemic_focus = format_epistemic_focus(
            findings=dynamic_context.get("recent_findings", []),
            unknowns=dynamic_context.get("open_unknowns", []),
            dead_ends=dynamic_context.get("critical_dead_ends", []),
            goals=dynamic_context.get("active_goals", []),
            subtasks=dynamic_context.get("pending_subtasks", []),
            max_items=5,
            session_id=new_session_id,
        )
    else:
        # Fallback to legacy formatting if summarizer not available
        goals_text = _format_goals(dynamic_context)
        findings_text = _format_findings(dynamic_context)
        unknowns_text = _format_unknowns(dynamic_context)
        epistemic_focus = f"""**Active Goals:**
{goals_text}

**Recent Findings (high-impact learnings):**
{findings_text}

**Open Unknowns (unresolved questions):**
{unknowns_text}"""

    # Include last task and calibration if available
    last_task = dynamic_context.get("last_task", "")
    last_task_section = f"\n**Last task:** {last_task}\n" if last_task else ""

    calibration = dynamic_context.get("calibration_biases", "")
    calibration_section = f"\n{calibration}\n" if calibration else ""
    temporal_trail = _temporal_trail_section(dynamic_context.get("project_path"))

    # If hook already created session and ran bootstrap, use that
    if new_session_id:
        memory_text = _format_memory_context(session_bootstrap.get("memory_context"))

        return f"""
## POST-COMPACT — new session `{new_session_id}`, PREFLIGHT required

Compaction is routine + lossless — the durable layer carries what matters; the data below re-grounds you (prev session {old_session_id[:8]}... was COMPLETE).

**Pre-compact (historical):** know={pre_know} · uncertainty={pre_unc}
{last_task_section}{temporal_trail}
{epistemic_focus}{calibration_section}

### Memory
{memory_text}

→ Run PREFLIGHT for `{new_session_id}`, calibrated to your state *after* this context.
"""

    # Fallback: hook couldn't create the session, AI runs the sequence itself.
    return f"""
## POST-COMPACT — new session required, then PREFLIGHT

Compaction is routine + lossless — the durable layer carries what matters; the data below re-grounds you (prev session {old_session_id[:8]}... was COMPLETE, so a fresh session + PREFLIGHT baseline is needed).

**Pre-compact (historical):** know={pre_know} · uncertainty={pre_unc}
{last_task_section}{temporal_trail}
{epistemic_focus}{calibration_section}

→ `session-create --ai-id {ai_id}` → `project-bootstrap --session-id <new>` → PREFLIGHT, calibrated to your state *after* bootstrap.
"""


def _format_memory_context(memory_context: dict) -> str:
    """Format memory context from Qdrant search for prompt."""
    if not memory_context:
        return "  (No memory context available - Qdrant may not be running)"

    results = memory_context.get("results", {})
    if not results:
        return "  (No relevant memories found)"

    lines = []

    # Handle both old format (list) and new format (dict with docs/memory keys)
    if isinstance(results, dict):
        # New format: {"docs": [...], "memory": [...]}
        all_results = []
        for key in ["memory", "docs", "eidetic", "episodic"]:
            if key in results and isinstance(results[key], list):
                all_results.extend(results[key])
        results = all_results

    if not results:
        return "  (No relevant memories found)"

    for r in results[:5]:  # Top 5 memories
        if not isinstance(r, dict):
            continue
        content = r.get("content", r.get("text", ""))[:150]
        score = r.get("score", 0)
        lines.append(f"  - [{score:.2f}] {content}...")

    return "\n".join(lines) if lines else "  (No memories)"


def _format_goals(dynamic_context: dict) -> str:
    """Format goals for prompt, including semantic search results and stale goals."""
    lines = []

    # Stale goals (marked during pre-compact) - show first as they need attention
    if dynamic_context.get("stale_goals"):
        lines.append("  **⚠️ STALE (context lost during compaction - re-evaluate before continuing):**")
        for g in dynamic_context["stale_goals"]:
            obj = g.get("objective", "Unknown")
            reason = g.get("stale_reason", "memory_compact")
            lines.append(f"  - ⚠️ {obj[:80]}... (stale: {reason})")
            lines.append(f"       Refresh with: empirica goals-refresh --goal-id {g['goal_id']}")
        lines.append("  ")  # separator

    # Active goals from project-bootstrap
    if dynamic_context.get("active_goals"):
        for g in dynamic_context["active_goals"]:
            lines.append(f"  - {g['objective']} ({g['status']})")

    # Related goals from semantic search (goals-search)
    if dynamic_context.get("related_goals"):
        if lines:
            lines.append("  ")  # separator
            lines.append("  **Semantically related (from Qdrant):**")
        for g in dynamic_context["related_goals"]:
            obj = g.get("objective") or g.get("description", "Unknown")
            status = g.get("status", "unknown")
            score = g.get("score", 0)
            goal_type = g.get("type", "goal")
            lines.append(f"  - [{goal_type}] {obj[:80]} ({status}, score={score:.2f})")

    return "\n".join(lines) if lines else "  (No active goals)"


def _format_findings(dynamic_context: dict) -> str:
    """Format findings for prompt."""
    if dynamic_context.get("recent_findings"):
        return "\n".join(
            [
                f"  - {f['finding'][:100]}..." if len(f["finding"]) > 100 else f"  - {f['finding']}"
                for f in dynamic_context["recent_findings"]
            ]
        )
    return "  (No recent findings)"


def _format_unknowns(dynamic_context: dict) -> str:
    """Format unknowns for prompt."""
    if dynamic_context.get("open_unknowns"):
        return "\n".join(
            [
                f"  - {u['unknown'][:100]}..." if len(u["unknown"]) > 100 else f"  - {u['unknown']}"
                for u in dynamic_context["open_unknowns"]
            ]
        )
    return "  (No open unknowns)"


def _format_dead_ends(dynamic_context: dict) -> str:
    """Format dead ends for prompt."""
    if dynamic_context.get("critical_dead_ends"):
        return "\n".join([f"  - {d['approach']}: {d['why_failed']}" for d in dynamic_context["critical_dead_ends"]])
    return "  (None recorded)"


def _generate_transaction_continue_prompt(pre_vectors: dict, dynamic_context: dict, active_transaction: dict) -> str:
    """
    Generate a simple continuation prompt when there's an open transaction.

    When a transaction is open, the AI should just continue - no new PREFLIGHT or CHECK needed.
    The transaction file on disk is the source of truth.
    """
    pre_know = pre_vectors.get("know", "N/A")
    pre_unc = pre_vectors.get("uncertainty", "N/A")
    session_id = dynamic_context.get("session_context", {}).get("session_id", "unknown")

    tx_id = active_transaction.get("transaction_id", "unknown")[:8]
    tx_session = active_transaction.get("session_id", "unknown")[:8]
    tx_project = active_transaction.get("project_path", "unknown")
    if isinstance(tx_project, str) and "/" in tx_project:
        tx_project = tx_project.split("/")[-1]

    # Use epistemic summarizer for focus section if available
    if EPISTEMIC_SUMMARIZER_AVAILABLE:
        epistemic_focus = format_epistemic_focus(
            findings=dynamic_context.get("recent_findings", []),
            unknowns=dynamic_context.get("open_unknowns", []),
            dead_ends=dynamic_context.get("critical_dead_ends", []),
            goals=dynamic_context.get("active_goals", []),
            subtasks=dynamic_context.get("pending_subtasks", []),
            max_items=5,
            session_id=session_id if session_id != "unknown" else None,
        )
    else:
        epistemic_focus = "*No breadcrumbs loaded.*"

    # Include last task and calibration if available
    last_task = dynamic_context.get("last_task", "")
    last_task_section = f"\n**Last task:** {last_task}\n" if last_task else ""

    calibration = dynamic_context.get("calibration_biases", "")
    calibration_section = f"\n{calibration}\n" if calibration else ""
    temporal_trail = _temporal_trail_section(dynamic_context.get("project_path"))

    return f"""## TRANSACTION CONTINUES — compaction was a non-event

Your **transaction is still open**, so the measurement window carries across — no new PREFLIGHT/CHECK; just resume.

**⚡ ACTIVE:** tx {tx_id}... · session {tx_session}... · {tx_project} · pre-compact know={pre_know} unc={pre_unc}
{last_task_section}{temporal_trail}
## EPISTEMIC FOCUS
{epistemic_focus}
{calibration_section}
→ Continue; close with POSTFLIGHT when the task's done.
"""


def _generate_check_prompt(pre_vectors: dict, pre_reasoning: str, dynamic_context: dict) -> str:
    """
    Generate a CHECK gate prompt for post-compact validation.

    CHECK is correct when session is INCOMPLETE (no POSTFLIGHT yet) -
    we're continuing work and need to validate readiness.
    """
    pre_know = pre_vectors.get("know", "N/A")
    pre_unc = pre_vectors.get("uncertainty", "N/A")
    session_id = dynamic_context.get("session_context", {}).get("session_id", "unknown")

    # Format active transaction context (if exists)
    tx_context = ""
    project_folder = "unknown"  # Default for bootstrap command
    active_tx = dynamic_context.get("active_transaction")
    if active_tx:
        tx_id = active_tx.get("transaction_id", "unknown")[:8]
        tx_status = active_tx.get("status", "unknown")
        tx_project = active_tx.get("project_path", "unknown")
        if isinstance(tx_project, str) and "/" in tx_project:
            project_folder = tx_project.split("/")[-1]  # Just folder name for commands
            tx_project = project_folder
        tx_context = f"""
**⚡ ACTIVE TRANSACTION (preserved across compact):**
   Transaction: {tx_id}... | Status: {tx_status} | Project: {tx_project}
   → This transaction is still open. Continue work within it or close with POSTFLIGHT.
"""
    else:
        # Try to get project folder from session_context
        project_id = dynamic_context.get("session_context", {}).get("project_id", "")
        if project_id:
            project_folder = project_id  # May be UUID or folder name

    # Use epistemic summarizer for confidence-weighted ranking (no chronological fallback)
    if EPISTEMIC_SUMMARIZER_AVAILABLE:
        epistemic_focus = format_epistemic_focus(
            findings=dynamic_context.get("recent_findings", []),
            unknowns=dynamic_context.get("open_unknowns", []),
            dead_ends=dynamic_context.get("critical_dead_ends", []),
            goals=dynamic_context.get("active_goals", []),
            subtasks=dynamic_context.get("pending_subtasks", []),
            max_items=5,
            session_id=session_id if session_id != "unknown" else None,
        )
    else:
        # Fallback to legacy formatting if summarizer not available
        goals_text = _format_goals(dynamic_context)
        findings_text = _format_findings(dynamic_context)
        unknowns_text = _format_unknowns(dynamic_context)
        dead_ends_text = _format_dead_ends(dynamic_context)
        epistemic_focus = f"""**Active Goals:**
{goals_text}

**Recent Findings (high-impact learnings):**
{findings_text}

**Open Unknowns (unresolved questions):**
{unknowns_text}

**Dead Ends (approaches that failed):**
{dead_ends_text}"""

    # Include last task and calibration if available
    last_task = dynamic_context.get("last_task", "")
    last_task_section = f"\n**Last task:** {last_task}\n" if last_task else ""

    calibration = dynamic_context.get("calibration_biases", "")
    calibration_section = f"\n{calibration}\n" if calibration else ""
    temporal_trail = _temporal_trail_section(dynamic_context.get("project_path"))

    prompt = f"""
## POST-COMPACT — CHECK to resume (session incomplete)

Compaction is routine + lossless — the durable layer carries what matters; the data below re-grounds you. Your pre-compact vectors (know={pre_know} · unc={pre_unc}) are stale — re-ground, then CHECK with fresh, honestly-lower values.
{tx_context}{last_task_section}{temporal_trail}
{epistemic_focus}{calibration_section}

→ Re-ground if needed (`project-bootstrap --session-id {session_id}` / `project-search`), then CHECK (proceed = continue; investigate = load more first).
"""
    return prompt


def _calculate_potential_drift(pre_vectors: dict) -> dict:
    """
    Calculate what drift WOULD look like if we naively kept pre-compact vectors.
    This shows why re-assessment is necessary.
    """
    if not pre_vectors:
        return {"warning": "No pre-compact vectors to compare"}

    # Post-compact, honest assessment would typically show:
    # - Lower know (lost detailed context)
    # - Higher uncertainty (less confident)
    # - Similar or lower context (depends on evidence loaded)

    pre_know = pre_vectors.get("know", 0.5)
    pre_unc = pre_vectors.get("uncertainty", 0.5)

    return {
        "pre_compact": {"know": pre_know, "uncertainty": pre_unc},
        "expected_honest_post_compact": {
            "know": max(0.3, pre_know - 0.2),  # Typically drops
            "uncertainty": min(0.8, pre_unc + 0.2),  # Typically rises
        },
        "message": "If your post-compact know equals pre-compact, you may be overestimating",
    }


def _print_user_message(
    pre_vectors: dict,
    dynamic_context: dict,
    potential_drift: dict,
    phase_state: dict | None = None,
    ai_id: str = "claude-code",
    session_bootstrap: dict | None = None,
):
    """Print user-visible summary to stderr"""
    pre_know = pre_vectors.get("know", "N/A")
    pre_unc = pre_vectors.get("uncertainty", "N/A")

    goals_count = len(dynamic_context.get("active_goals", []))
    findings_count = len(dynamic_context.get("recent_findings", []))
    unknowns_count = len(dynamic_context.get("open_unknowns", []))

    is_complete = phase_state.get("is_complete", False) if phase_state else False
    last_phase = phase_state.get("last_phase", "unknown") if phase_state else "unknown"

    if is_complete:
        # Session was complete - check if hook created new session
        if session_bootstrap and session_bootstrap.get("session_id"):
            new_session_id = session_bootstrap["session_id"]
            print(
                f"""
🔄 Empirica: Post-Compact Recovery (Session Complete)

📊 Previous Session State:
   Last Phase: {last_phase} (COMPLETE)
   Pre-compact vectors (NOW INVALID): know={pre_know}, uncertainty={pre_unc}

✅ NEW SESSION CREATED: {new_session_id}
✅ Project context bootstrapped automatically

📚 Dynamic Context Loaded:
   Active Goals: {goals_count}
   Recent Findings: {findings_count}
   Open Unknowns: {unknowns_count}

🎯 ACTION REQUIRED:
   Run PREFLIGHT with your ACTUAL knowledge state (after reading loaded context):
   empirica preflight-submit --session-id {new_session_id}

💡 TIP: Your PREFLIGHT should reflect knowledge AFTER reading the bootstrap context.
   This makes the PREFLIGHT→POSTFLIGHT delta meaningful.
""",
                file=sys.stderr,
            )
        else:
            # Fallback: Hook couldn't create session
            print(
                f"""
🔄 Empirica: Post-Compact Recovery (Session Complete)

📊 Previous Session State:
   Last Phase: {last_phase} (COMPLETE)
   Pre-compact vectors (NOW INVALID): know={pre_know}, uncertainty={pre_unc}

⚠️  Previous session had POSTFLIGHT - it's COMPLETE.
   You need a NEW session with fresh PREFLIGHT baseline.

📚 Dynamic Context Available:
   Active Goals: {goals_count}
   Recent Findings: {findings_count}
   Open Unknowns: {unknowns_count}

🎯 ACTION REQUIRED:
   1. Create new session: empirica session-create --ai-id {ai_id}
   2. Load context: empirica project-bootstrap --session-id <NEW_ID>
   3. Run PREFLIGHT: empirica preflight-submit (AFTER loading context!)
""",
                file=sys.stderr,
            )
    else:
        # Session incomplete - need CHECK to continue
        print(
            f"""
🔄 Empirica: Post-Compact CHECK Gate (Session Incomplete)

📊 Pre-Compact State (NOW INVALID):
   Last Phase: {last_phase}
   know={pre_know}, uncertainty={pre_unc}

⚠️  These vectors reflected FULL context knowledge.
   You now have only a summary. Session is INCOMPLETE.

📚 Dynamic Context Loaded:
   Active Goals: {goals_count}
   Recent Findings: {findings_count}
   Open Unknowns: {unknowns_count}

🎯 ACTION REQUIRED:
   1. Load context: empirica project-bootstrap --session-id <ID>
   2. Run CHECK: empirica check-submit (with honest assessment)
   3. Follow decision: "proceed" or "investigate"
""",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
