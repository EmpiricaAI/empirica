#!/usr/bin/env python3
"""UserPromptSubmit hook: Track context shifts in epistemic transactions.

Classifies user prompts as:
- SOLICITED: AI asked a question (AskUserQuestion) → user responded
- UNSOLICITED: User initiated a new prompt without being asked

Uses a purely structural signal — no keyword matching:
- sentinel-gate.py sets pending_user_response=True when AskUserQuestion fires
- This hook checks that flag on each UserPromptSubmit event

Counts are stored in the hook_counters file (separate from transaction lifecycle).
POSTFLIGHT reads counters for calibration; next-PREFLIGHT uses them for divergence.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add lib folder to path for shared modules
_lib_path = Path(__file__).parent.parent / "lib"
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

from project_resolver import _get_instance_suffix, get_instance_id  # noqa: E402 — after sys.path setup


def _find_transaction_file(claude_session_id: "str | None" = None) -> "Path | None":
    """Find the active transaction file using the same priority as sentinel-gate."""
    instance_id = get_instance_id()
    suffix = _get_instance_suffix()

    # Try 1: active_work file for project_path
    if claude_session_id:
        aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if aw_file.exists():
            try:
                with open(aw_file) as f:
                    pp = json.load(f).get("project_path")
                if pp:
                    candidate = Path(pp) / ".empirica" / f"active_transaction{suffix}.json"
                    if candidate.exists():
                        return candidate
            except Exception:
                pass

    # Try 2: instance_projects mapping
    if instance_id:
        ip_file = Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json"
        if ip_file.exists():
            try:
                with open(ip_file) as f:
                    pp = json.load(f).get("project_path")
                if pp:
                    candidate = Path(pp) / ".empirica" / f"active_transaction{suffix}.json"
                    if candidate.exists():
                        return candidate
            except Exception:
                pass

    # Try 3: global fallback
    candidate = Path.home() / ".empirica" / f"active_transaction{suffix}.json"
    if candidate.exists():
        return candidate

    return None


def _build_context_usage_output(claude_session_id):
    """Read context window usage from statusline state file.

    Returns a hook output dict with context percentage info, or empty dict.

    The number is labeled unambiguously as USED (it grows toward 100%) —
    practitioners have repeatedly misread a bare "context: 42%" as
    remaining and rushed/deferred work off a miscalibrated budget feeling.
    At high usage the message carries the lossless-compaction reminder so
    self-assessment tracks the actual recovery model, not compaction fear.
    """
    import time as _time

    try:
        suffix = _get_instance_suffix()
        state_file = Path.home() / ".empirica" / f"context_usage{suffix}.json"
        if not state_file.exists():
            return {}

        state = json.loads(state_file.read_text())
        used_pct = state.get("used_percentage", 0)
        state_age = _time.time() - state.get("timestamp", 0)

        if state_age >= 60 or used_pct <= 0:
            return {}

        ctx_msg = f"context: {int(used_pct)}% used"

        # High usage: compaction may be near. State the abundance model
        # affirmatively — the % is informational, not a deadline — so the
        # practitioner keeps working at full quality instead of rushing.
        if used_pct > 80:
            ctx_msg += (
                " | this is a buffer reading, not a deadline. If compaction "
                "comes it's lossless: your logged goals/artifacts/git-notes "
                "persist and bootstrap re-grounds you. Keep working at full "
                "quality and keep logging — the log is the durable copy."
            )

        # At >85% context, advise auto-switching to CWD project
        # if CWD differs from active transaction project
        if used_pct > 85:
            ctx_msg = _maybe_append_project_switch_hint(ctx_msg, claude_session_id)

        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx_msg}}
    except Exception:
        return {}


def _maybe_append_project_switch_hint(ctx_msg, claude_session_id):
    """Append a project-switch hint to ctx_msg if CWD differs from transaction project."""
    try:
        cwd = str(Path.cwd().resolve())
        tx_file = _find_transaction_file(claude_session_id)
        if tx_file:
            tx_data = json.loads(Path(tx_file).read_text())
            tx_project = tx_data.get("project_path", "")
            if tx_project and str(Path(tx_project).resolve()) != cwd:
                ctx_msg += (
                    f" | CWD project differs from transaction project"
                    f" — project-switch to {Path(cwd).name} when convenient"
                    f" to keep artifacts attributed correctly"
                )
    except Exception:
        pass
    return ctx_msg


def _append_to_output(output, message):
    """Append a message to the hookSpecificOutput additionalContext, or create it."""
    existing = output.get("hookSpecificOutput", {}).get("additionalContext")
    if existing:
        output["hookSpecificOutput"]["additionalContext"] += f" | {message}"
    else:
        output.setdefault("hookSpecificOutput", {})
        output["hookSpecificOutput"]["hookEventName"] = "UserPromptSubmit"
        output["hookSpecificOutput"]["additionalContext"] = message


def _build_no_transaction_nudge(hook_input):
    """Build a skill nudge message when no active transaction exists."""
    skill_nudge = "no active transaction — load /empirica-constitution for orientation before PREFLIGHT"

    user_prompt = hook_input.get("prompt", "").lower()
    complex_work_signals = [
        "plan",
        "implement",
        "spec",
        "transaction",
        "transactions",
        "preflight",
        "artifacts",
        "epistemic",
        "break this down",
        "how should i approach",
        "decompose",
        "multi-step",
        "complex",
    ]
    if any(signal in user_prompt for signal in complex_work_signals):
        skill_nudge += " | complex work detected — consider /epistemic-transaction for structured decomposition"

    return skill_nudge


def _atomic_write_counters(counters, counters_path):
    """Atomically write counters dict to counters_path."""
    fd, tmp = tempfile.mkstemp(dir=str(counters_path.parent))
    try:
        with os.fdopen(fd, "w") as tf:
            json.dump(counters, tf, indent=2)
        os.replace(tmp, str(counters_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _get_reminder_turns(tx_path):
    """Read configurable artifact reminder turn threshold from project config."""
    reminder_turns = 15
    try:
        import yaml

        for cfg_path in [
            tx_path.parent.parent / ".empirica-project" / "PROJECT_CONFIG.yaml",
            tx_path.parent.parent / "PROJECT_CONFIG.yaml",
        ]:
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                reminder_turns = int(cfg.get("transaction", {}).get("log_artifacts_reminder_turns", reminder_turns))
                break
    except Exception:
        pass
    return reminder_turns


def _check_artifact_reminder(tx, tx_path, counters, counters_path, output):
    """Check if an artifact reminder should be emitted for this transaction.

    Mutates output and counters in-place if a reminder is needed.
    """
    tool_calls = counters.get("tool_call_count", 0)
    reminder_turns = _get_reminder_turns(tx_path)

    if tool_calls < reminder_turns or counters.get("artifact_reminded"):
        return

    try:
        tx_id = tx.get("transaction_id")
        session_id = tx.get("session_id")
        if not (tx_id and session_id):
            return

        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()
        total = 0
        for table in (
            "project_findings",
            "project_unknowns",
            "project_dead_ends",
            "mistakes_made",
            "assumptions",
            "decisions",
        ):
            try:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE session_id = ? AND transaction_id = ?", (session_id, tx_id)
                )
                total += cursor.fetchone()[0]
            except Exception:
                pass
        db.close()

        if total != 0:
            return

        goal_hint = _get_goal_hint(tx_id)
        reminder = (
            f"Artifact reminder: {tool_calls} tool calls, 0 artifacts logged "
            f"in this transaction.{goal_hint} "
            f"Commands: finding-log, decision-log, assumption-log, unknown-log"
        )
        _append_to_output(output, reminder)

        counters["artifact_reminded"] = True
        _atomic_write_counters(counters, counters_path)
    except Exception:
        pass


def _get_goal_hint(tx_id):
    """Get a goal-aware hint for the artifact reminder."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
        from empirica.data.session_database import SessionDatabase

        cursor2 = SessionDatabase().conn.cursor()
        cursor2.execute(
            """
            SELECT objective, scope FROM goals
            WHERE transaction_id = ? AND status = 'in_progress'
            LIMIT 1
        """,
            (tx_id,),
        )
        row = cursor2.fetchone()
        if row:
            scope = json.loads(row[1]) if row[1] else {}
            breadth = scope.get("breadth", 0.3)
            if breadth >= 0.5:
                return f" Working on '{row[0][:50]}...' (breadth {breadth}) — decisions and assumptions are likely worth capturing."
            return f" Working on '{row[0][:50]}...' — at minimum log a finding."
    except Exception:
        pass
    return ""


def _refresh_presence(claude_session_id):
    """Best-effort per-turn presence refresh: re-stamp last_heartbeat=now so the
    persistent-service heartbeat emitter keeps forwarding a NON-STALE record to
    cortex.

    session-init writes presence once at session start; without a per-turn
    refresh the record goes stale after the active window (180s) and a live
    session stops emitting — the mesh's busy/free/blocked active-state signal
    goes dark on any session older than 3 minutes. `practitioner write` resolves
    ai_id / location / empirica-session / active-transaction from the running
    context, so only --session is needed. Fire-and-forget (detached Popen) so it
    never adds latency to — or fails — the user turn.

    We also re-supply --session-pid (this hook is spawned by Claude Code, so
    getppid() is CC): it makes the per-turn refresh self-healing for the daemon's
    liveness anchor — any session that emits a prompt gets its session_pid set or
    corrected, even one created before the anchor existed. (write_presence also
    preserves an existing anchor across rewrites, so this is belt-and-suspenders.)
    A prompt submission also means the practitioner is active again, so this
    naturally clears any status=blocked the AskUserQuestion gate stamped.
    """
    if not claude_session_id:
        return
    try:
        import subprocess

        subprocess.Popen(
            [
                "empirica",
                "practitioner",
                "write",
                "--session",
                claude_session_id,
                "--session-pid",
                str(os.getppid()),
                "--output",
                "json",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main():
    hook_input = json.loads(sys.stdin.read() or "{}")
    claude_session_id = hook_input.get("session_id")

    # Keep practitioner presence fresh each turn (the mesh active-state signal) —
    # best-effort, non-blocking. See _refresh_presence.
    _refresh_presence(claude_session_id)

    # Context window monitoring
    output = _build_context_usage_output(claude_session_id)

    tx_path = _find_transaction_file(claude_session_id)
    if not tx_path:
        # No active transaction — suggest constitution skill for orientation
        _append_to_output(output, _build_no_transaction_nudge(hook_input))
        print(json.dumps(output))
        return

    try:
        with open(tx_path) as f:
            tx = json.load(f)

        if tx.get("status") != "open":
            print(json.dumps({}))
            return

        # READ-MODIFY-WRITE the hook counters file (hook-owned, no race with POSTFLIGHT)
        suffix = _get_instance_suffix()
        counters_path = tx_path.parent / f"hook_counters{suffix}.json"
        counters = {}
        if counters_path.exists():
            try:
                with open(counters_path) as f:
                    counters = json.load(f)
            except Exception:
                counters = {}

        was_solicited = counters.pop("pending_user_response", False)
        if was_solicited:
            counters["solicited_prompt_count"] = counters.get("solicited_prompt_count", 0) + 1
        else:
            counters["unsolicited_prompt_count"] = counters.get("unsolicited_prompt_count", 0) + 1

        _atomic_write_counters(counters, counters_path)

        # Artifact reminder check
        _check_artifact_reminder(tx, tx_path, counters, counters_path, output)

    except Exception:
        pass

    print(json.dumps(output))


if __name__ == "__main__":
    main()
