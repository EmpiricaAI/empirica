#!/usr/bin/env python3
"""SessionStart hook: arm a Monitor to bridge systemd loop fires into this session.

The Phase 1b wake-from-idle bridge for canonical loops scheduled by
systemd-user timers (goal f718156c). The systemd service ExecStart
(`empirica loop tick`) appends one JSON line per fire to
`~/.empirica/loop_fires.log`. This hook tells the running Claude to
arm a persistent Monitor that tails the log and reacts to each event.

Output: hookSpecificOutput.additionalContext (string) — markdown
instructions telling Claude exactly which Monitor to arm and how to
react to fire events. Empty output when no enabled loops exist (or
systemd isn't available, or the instance can't be resolved).

Non-blocking — any failure path emits empty output so a missing
systemd doesn't break SessionStart.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Plugin script — empirica package on sys.path via plugin bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    from empirica.core.cockpit.canonical_loops import canonical_loop_by_name
    from empirica.core.loop_scheduler import list_active_loops_for_instance
    from empirica.core.loop_scheduler.persistent_listener import is_listener_running
    from empirica.utils.session_resolver import InstanceResolver
except Exception:
    # Empirica package broken / not installed → emit empty output, exit clean.
    print(json.dumps({}))
    sys.exit(0)


def _has_active_listener_intent(instance_id: str) -> bool:
    """True iff a previous session armed a listener for this instance.

    `~/.empirica/listener_active_<instance>_<name>.json` is written by
    `empirica listener arm` and persists on disk across CC restarts.
    The Monitor task that file referenced died with the harness
    session, but the file is durable proof the user wanted a listener
    here. Treat that as a third wake-source signal alongside
    canonical loops and the persistent OS service — without it, a CC
    restart leaves the user deaf until they re-arm manually
    (extension's prop_72polrcugnbwxmpl3dxxvio6rq repro'd this twice
    in one day).

    Never raises; returns False on any glob failure.
    """
    try:
        d = Path.home() / ".empirica"
        if not d.is_dir():
            return False
        prefix = f"listener_active_{instance_id}_"
        return any(d.glob(f"{prefix}*.json"))
    except Exception:
        return False


def _has_live_log_tail(instance_id: str) -> bool:
    """True iff a live (non-orphan) loop_fires tail-Monitor for this ai_id is
    already running — a monitor is already bridging wakes into an active session.

    SessionStart re-fires on every compaction / new-session-init, and the AI
    can't reliably dedup across a compaction (the prior Monitor's task id is
    gone). Without this check the hook re-emits arm instructions and each arm
    stacks another tail that delivers every wake event an extra time. If a live
    tail already exists, the hook skips re-arming.

    Orphan tails (ppid == 1, dead parent) do NOT count — they deliver nowhere
    and are handled by `listener gc`; only a live-parent tail means an active
    session is already covered. Never raises.
    """
    try:
        from empirica.core.cockpit.listener_processes import (
            _ai_id_from_listener_cmdline,
            walk_listener_processes,
        )

        for p in walk_listener_processes():
            if p.get("kind") != "log_tail" or p.get("ppid") == 1:
                continue
            if _ai_id_from_listener_cmdline(p.get("cmdline", "")) == instance_id:
                return True
    except Exception:
        pass
    return False


def _build_reaction_table(loop_names: list[str]) -> str:
    """Markdown table mapping each active loop → its body skill."""
    rows = []
    for name in loop_names:
        entry = canonical_loop_by_name(name) or {}
        body_skill = entry.get("body_skill") or name
        rows.append(f"| `{name}` | `/{body_skill}` |")
    if not rows:
        return ""
    return "| Loop name | Body skill to invoke on fire |\n|---|---|\n" + "\n".join(rows)


def _query_listener_on(instance_id: str) -> dict | None:
    """Delegate to `empirica listener on --output json` for the canonical
    arming payload. Returns the parsed JSON or None on any failure.

    Phase 2 of prop_oxrhoehv4 — single source of truth: the CLI handler
    owns the persistent-service short-circuit detection + Monitor command
    rendering. This hook just renders the JSON response as markdown.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "empirica",
                "listener",
                "on",
                "--ai-id",
                instance_id,
                "--instance",
                instance_id,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _build_monitor_block_from_cli(payload: dict | None, instance_id: str) -> str:
    """Render the Monitor-arming markdown block from `empirica listener on`'s
    JSON response. Falls back to the canonical default command when the CLI
    is unavailable.

    Status semantics from the CLI (cockpit_commands.handle_listener_on_command):

      * `awaiting_arm` — standalone mode (no persistent service). Monitor
        command is `empirica loop listen --instance <ai_id>` — this session
        holds the ntfy stream itself.
      * `persistent_service_tail_session` — persistent OS service is up.
        Monitor command is a `tail -F loop_fires.log | grep instance_id`
        log-tailer that bridges the persistent service's writes into this
        session WITHOUT duplicating the ntfy curl subscription. Closes the
        Phase-3 in-session wake-delivery gap (without this, the session is
        deaf because the persistent service writes to disk but doesn't
        deliver to the running Claude).

    Both statuses carry `next_step.args` with the right command — this
    function just renders whatever the CLI returned.
    """
    # Use the CLI-provided next_step.args when available
    ns = (payload or {}).get("next_step") or {}
    monitor_args = ns.get("args") if isinstance(ns, dict) else None
    status = (payload or {}).get("status")
    # Standalone fallback wraps the listener spawn in a while-true supervisor
    # loop — the listener's design assumes a relauncher (systemd/launchd) on
    # clean exits (SIGTERM during reconnect, ListenerUpgraded on pip-version
    # drift), but Claude Code's Monitor isn't one. Wrapping here gives the
    # same auto-relaunch semantics on hosts without an OS service. sleep 3
    # keeps a crash-loop from pinning CPU; reconnect/backoff is handled
    # internally by the listener loop itself. (cortex prop_6kevxb63 finding.)
    _standalone_supervised = f"while true; do empirica loop listen --instance {instance_id}; sleep 3; done"
    if monitor_args:
        description = monitor_args.get("description", f"Cortex orchestration push listener for {instance_id}")
        command = monitor_args.get("command", _standalone_supervised)
        persistent = monitor_args.get("persistent", True)
    else:
        # Fallback when CLI is unavailable — preserve pre-Phase-2 behavior
        description = f"Cortex orchestration push listener for {instance_id}"
        command = _standalone_supervised
        persistent = True
    after_arm = ns.get("after_arm") if isinstance(ns, dict) else None
    after_arm_hint = (
        f"\n\nAfter arming, run `{after_arm}` (replace `<monitor_task_id>` "
        f"with the actual id returned by Monitor) so `empirica listener off` "
        f"knows what to TaskStop later."
        if after_arm
        else ""
    )

    # Mode-specific explainer so the reader knows WHY this Monitor shape
    # is the one being arrowed, not the other.
    if status == "persistent_service_tail_session":
        mode_explainer = (
            "The persistent OS listener service is already running for this "
            "ai_id (systemd-user / launchd). The Monitor below is a LOG-TAIL "
            "on `~/.empirica/loop_fires.log` — it bridges the persistent "
            "service's writes into this session WITHOUT spawning a duplicate "
            "ntfy subscriber. The persistent service handles ntfy + catch-up; "
            "the tail-Monitor handles in-session wake delivery."
        )
    else:
        mode_explainer = (
            "No persistent service detected for this ai_id — the Monitor "
            "below runs `empirica loop listen` directly, which holds the "
            "ntfy stream + catches up via content_poll on initial start, "
            "on each push arrival, AND on disconnect + reconnect. No "
            "periodic timer needed — push-primary, poll-on-reconnect-only "
            '("epistemic email for the AI age" — David, 2026-05-15).'
        )

    return (
        f"**Arm this Monitor at session start:**\n\n"
        f"```python\n"
        f"Monitor(\n"
        f'    description="{description}",\n'
        f'    command="{command}",\n'
        f"    persistent={persistent},\n"
        f")\n"
        f"```\n\n"
        f"{mode_explainer}{after_arm_hint}"
    )


def _build_orphan_warning() -> str:
    """Markdown warning when orphan listener processes have accumulated.

    Surfaces the buildup (cortex's orphan-accumulation report) before this
    session arms yet another listener. Deliberately does NOT auto-reap —
    killing processes is the user's call via `empirica listener gc --apply`.
    Threshold of 3 keeps the common one-stale-process case quiet. Never
    raises; any failure renders as no warning.
    """
    try:
        from empirica.core.cockpit.listener_processes import (
            walk_orphan_listener_processes,
        )

        orphan_count = len(walk_orphan_listener_processes())
    except Exception:
        return ""
    if orphan_count <= 3:
        return ""
    return (
        f"\n⚠️ **{orphan_count} orphan listener processes detected** — "
        f"listener subprocesses from dead sessions are accumulating. "
        f"Run `empirica listener gc` to review and "
        f"`empirica listener gc --apply` to reap them.\n"
    )


def _build_additional_context(
    instance_id: str,
    loop_names: list[str],
    listener_running: bool = False,
    has_prior_intent: bool = False,
) -> str:
    # T8 (goal f718156c): switched from `tail -F loop_fires.log` to
    # `empirica loop listen` — the listener is the push-primary wake
    # mechanism. It holds an ntfy stream to cortex, emits one stdout
    # line per ECO-decided proposal event.
    #
    # Phase 2 of prop_oxrhoehv4 (2026-05-21): delegate to
    # `empirica listener on --output json` for the canonical short-circuit
    # detection + Monitor command rendering. The CLI is the single source
    # of truth; this hook just renders the JSON as markdown. `listener_running`
    # arg kept for backwards-compat with callers; CLI delegation supersedes.
    payload = _query_listener_on(instance_id)
    if payload is None and listener_running:
        # CLI unavailable but caller already detected persistent service —
        # synthesize a tail-session payload so the renderer arms a log-tailer.
        payload = {"status": "persistent_service_tail_session"}
    monitor_block = _build_monitor_block_from_cli(payload, instance_id)

    # Wake source language adapts: loops / service / prior-intent / mix.
    if loop_names and listener_running:
        wake_source = (
            f"This instance (`{instance_id}`) has canonical loops registered AND a persistent listener service running."
        )
    elif loop_names:
        wake_source = (
            f"This instance (`{instance_id}`) has canonical loops registered. "
            f"The push-primary wake mechanism is `empirica loop listen` — it "
            f"holds an authenticated ntfy stream to Cortex and emits one "
            f"stdout line per ECO-decided proposal event (real wake) or "
            f"AI-to-AI completion ack."
        )
    elif listener_running:
        # Persistent-service-only case (no canonical loops). This was the
        # empirica-AI deafness: persistent service writes to loop_fires.log,
        # session had no Monitor to read it.
        wake_source = (
            f"This instance (`{instance_id}`) has a persistent listener "
            f"service running (systemd-user / launchd) but no canonical "
            f"loops registered. Without a session-side Monitor on "
            f"`~/.empirica/loop_fires.log`, wake events written by the "
            f"persistent service would not reach this session."
        )
    elif has_prior_intent:
        # Prior-intent-only case: a previous CC session armed a listener
        # for this instance (~/.empirica/listener_active_<instance>_*.json
        # is on disk) but the Monitor died with that session, and there's
        # no canonical loop or persistent OS service to re-trigger arming.
        # The active file is durable proof of intent — re-arm so this
        # session matches the user's last-known setup.
        wake_source = (
            f"This instance (`{instance_id}`) has a prior-armed listener on "
            f"record (`~/.empirica/listener_active_{instance_id}_*.json` "
            f"persists across CC restarts), but the Monitor that file "
            f"referenced died with the previous session. Re-arm the Monitor "
            f"so mesh wake events reach this session — without it, events "
            f"sit in `~/.empirica/loop_fires.log` unread until next manual "
            f"rearm (fixed for prop_72polrcugnbwxmpl3dxxvio6rq)."
        )
    else:
        # All three signals false — caller shouldn't have called us. Bail
        # gracefully with a non-empty but inert wake-source line so the
        # downstream renderer has something coherent to wrap.
        wake_source = f"This instance (`{instance_id}`) has no detected wake source."

    # Reaction table only renders meaningfully when there are loops.
    reaction_section = (
        f"### Active loops + their body skills\n\n{_build_reaction_table(loop_names)}\n" if loop_names else ""
    )

    return f"""\
## 📬 Empirica orchestration listener — arm at session start

{wake_source}

### REQUIRED: load mesh skills before your first transaction

Because this session will receive AI-mesh wake events, BOTH messaging
skills MUST be loaded before your first PREFLIGHT:

- `/cortex-mailbox-poll` — receive side: per-direction × per-status
  reaction protocol for incoming `proposal_event` notifications
- `/cortex-mailbox-send` — send side: how to emit proposals, the
  collab vs ECO-gated flavor split, target ai_id verification, AND
  the completion-ack handshake (without which the source AI never
  knows you delivered)

Loading them only when an event arrives is too late — the send-side
handshake guidance is needed BEFORE you act, not after. Load both now.

{reaction_section}{monitor_block}

**Reaction protocol** — when an event arrives (one JSON line in the chat):

1. Read `direction` field:
   - `inbox` → ECO-decided proposal targeting you (act per status)
   - `outbox` → ack/refinement on a proposal you emitted (informational)
2. **If mid-transaction:** log a goal `"Process <direction>/<status>: <proposal_id>"`
   and pick up at next natural break (EWM pattern).
3. **If idle:** follow the per-direction × per-status reaction protocol
   in the already-loaded `/cortex-mailbox-poll` skill (act on accepted,
   refine on changed, acknowledge on completed). When completing work a
   peer asked of you, ack via the `/cortex-mailbox-send` skill's
   completion-handshake guidance — without it, the source AI never sees
   the work landed.

**ECO-gated autonomy property:** every event you act on traces back to an
ECO actor decision (`eco_actor` field). The listener filters `eco_review`
proposals server-side — they never cross the wake boundary. Even if the
ntfy stream were compromised, your status re-verification against Cortex
by `proposal_id` is the auth boundary.

If you do not arm this Monitor, events will accumulate at Cortex but no
work will trigger in this session.

**Arm exactly once per ai_id — replace, don't stack.** Before arming, if you
already armed a loop_fires Monitor for `{instance_id}` earlier in THIS session,
do NOT arm a second one: TaskStop the prior Monitor first (or skip arming
entirely). Duplicate Monitors tailing the same log deliver every wake event
N times — once per stacked tail. Harnesses that dedupe identical Monitor
commands make a re-arm a no-op; those that don't (and every re-fired
SessionStart) accumulate duplicates unless you reap first. `empirica listener
off` TaskStops the recorded Monitor; re-arm, then `empirica listener arm
<new_monitor_task_id>` to replace the record.
{_build_orphan_warning()}"""


def main() -> int:
    # Use ai_id (project basename) for timer-name lookup, not the ephemeral
    # tmux pane id. Timers are named `empirica-loop-<ai_id>-<loop>.timer`
    # so they survive tmux restarts; the hook must query with the same
    # stable id the installer used. Falls back to instance_id only for
    # legacy/unconfigured environments. (David, 2026-05-16)
    try:
        instance_id = InstanceResolver.ai_id() or InstanceResolver.instance_id()
    except Exception:
        instance_id = None
    if not instance_id:
        print(json.dumps({}))
        return 0

    try:
        loops = list_active_loops_for_instance(instance_id)
    except Exception:
        loops = []

    # Check persistent service. If it's running, this session needs a
    # tail-Monitor on loop_fires.log to receive wakes — even when no
    # canonical loops are registered (the empirica-AI case: persistent
    # service holds the ntfy stream, but the running Claude is deaf
    # without an in-band Monitor reading the log). Pre-Phase-3, the
    # hook only fired when loops were registered, which was the wrong
    # condition — wake bridging is needed wherever there's a wake source.
    listener_running = False
    try:
        listener_running = is_listener_running(instance_id)
    except Exception:
        listener_running = False

    # Third wake-source signal: a `listener_active_<instance>_*.json`
    # file on disk is proof the user previously armed a listener for
    # this instance. CC Monitor tasks die with the harness session, so
    # after a restart that file is the only evidence of intent. Without
    # this signal in the bail check, restart leaves the user deaf until
    # they re-arm manually (extension's prop_72polrcugnbwxmpl3dxxvio6rq
    # repro). Per extension's option (b): emit the arm block whenever
    # ANY wake source is implied; AI is the dedup point for double-arm.
    has_prior_intent = _has_active_listener_intent(instance_id)

    # Bail only when there's NO wake source at all — no loops + no
    # service + no record of prior intent.
    if not loops and not listener_running and not has_prior_intent:
        print(json.dumps({}))
        return 0

    # A live tail-Monitor for this ai_id already bridging wakes → do NOT re-emit
    # arm instructions. SessionStart re-fires on compaction / new-session-init;
    # re-arming would stack a duplicate tail that delivers every event twice.
    if _has_live_log_tail(instance_id):
        already = (
            f"## 📬 Mesh listener already armed\n\n"
            f"A live tail-Monitor for `{instance_id}` is already bridging wake events "
            f"into this session — SessionStart re-fired (compaction / new-session-init), "
            f"but re-arming would stack a duplicate tail that delivers every event twice. "
            f"**No action needed — do not arm another Monitor.**\n\n"
            f"If you ever see duplicate wake deliveries, reap same-ai_id duplicates with "
            f"`empirica listener gc --apply`."
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": already,
                    },
                }
            )
        )
        return 0

    additional = _build_additional_context(
        instance_id,
        loops,
        listener_running,
        has_prior_intent,
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": additional,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
