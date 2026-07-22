"""Cockpit CLI command handlers — sentinel, loop, status subcommands.

Wires the empirica.core.cockpit module into the argparse-driven CLI. Each
handler is small on purpose — the work lives in core/cockpit.

Conventions:
- Every handler returns 0 on success, non-zero on error.
- --output json prints a single JSON object with "ok" boolean.
- --output human is for humans; matches the proposal's --pretty look.
- instance_id resolution: --instance flag > current-process detection
  (TMUX_PANE etc.) > None (which targets the global Sentinel pause).
"""

from __future__ import annotations

import json as _json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from empirica.core.cockpit import (
    LoopRegistry,
    aggregate_all,
    aggregate_instance_state,
    discover_dead_instances,
    forget_instance,
    get_label,
    is_loop_paused,
    kill_instance,
    pause_sentinel,
    rebind_instance,
    render_json,
    render_pretty,
    resume_sentinel,
    sentinel_status,
    set_label,
    set_loop_paused,
)
from empirica.core.cockpit.listener_install_request import (
    write_pending as write_listener_install_pending,
)
from empirica.core.cockpit.listener_registry import (
    ListenerRegistry,
    is_listener_paused,
    listener_active_path,
    set_listener_paused,
)
from empirica.core.cockpit.listener_uninstall_request import (
    write_pending as write_listener_uninstall_pending,
)
from empirica.core.cockpit.loop_install_request import (
    DEFAULT_SCHEDULER_KIND,
    write_pending,
)
from empirica.core.cockpit.loop_registry import VALID_KIND, VALID_STATUS
from empirica.core.cockpit.loop_uninstall_request import (
    write_pending as write_uninstall_pending,
)
from empirica.core.cockpit.notify_dispatcher_view import build_notify_dispatcher_block
from empirica.utils.session_resolver import get_instance_id

logger = logging.getLogger(__name__)


def _resolve_instance_id(args, fallback_to_current: bool = True) -> str | None:
    """Pick the instance to operate on.

    Priority: explicit --instance flag > current process's instance > None.
    None means "global" for sentinel; required for loop ops.
    """
    instance = getattr(args, "instance", None)
    if instance:
        return instance
    if fallback_to_current:
        return get_instance_id()
    return None


class SentinelResolveError(ValueError):
    """A sentinel target could not be resolved to a live runtime instance, or
    resolved ambiguously (a practice ai_id mapping to >1 live instance with no
    --all/--session selector).

    This is the loud-fail invariant for practitioner-identity phase ①
    (docs/architecture/instance_isolation/PRACTITIONER_IDENTITY.md §6). It
    replaces the silent pause-miss class: `sentinel pause --instance <ai_id>`
    used to write sentinel_paused_<ai_id> while the gate — keyed on the runtime
    instance_id (tmux_N) — never read it, so the pauser saw success but nothing
    paused. Now a no-match / ambiguous target is an explicit error.

    Inherits ValueError (not SystemExit) so `except Exception` callers catch it
    cleanly. Same hazard pattern as InstanceIdRequiredError below.
    """


def _resolve_sentinel_targets(args) -> list[str | None]:
    """Resolve sentinel pause/resume/status target(s) from args.

    Returns the list of instance_ids the verb should act on:
      - [None]          → no selector: current process / global (legacy default)
      - [instance_id]   → one resolved live runtime instance
      - [id1, id2, ...] → --all fan-out across a practice's live instances

    Resolution order:
      0. --global → [None] (the single global pause file the gate reads for ALL
         instances). Broadest scope — wins over any narrower selector.
      1. No --instance / --session / --all → [get_instance_id()] (may be None →
         global), preserving the pre-① default.
      2. --session <claude_session_id>     → the live instance running it.
      3. --instance <X> where X is a live runtime instance_id → [X] (back-compat
         passthrough — the common autonomy/cockpit path).
      4. --instance <X> where X is a practice ai_id:
           1 live instance  → [that instance_id]   (the ① fix: ai_id → runtime)
           0 live instances → SentinelResolveError  (the silent-miss class)
           N>1 live         → SentinelResolveError unless --all (decision-2:
                              no silent fan-out — require --session/--all)

    Raises SentinelResolveError on no-match or unresolved ambiguity (loud-fail).
    """
    requested = getattr(args, "instance", None)
    session = getattr(args, "session", None)
    want_all = bool(getattr(args, "all", False))
    want_global = bool(getattr(args, "global_scope", False))

    # --global forces the single global pause file (~/.empirica/sentinel_paused),
    # which the gate reads for EVERY instance. It is the broadest scope, so it
    # wins over any narrower selector — `empirica off --global` means "pause
    # everything" regardless of the current instance / --instance / --all.
    if want_global:
        return [None]

    if not requested and not session and not want_all:
        return [get_instance_id()]  # may be None → global pause (unchanged)

    # Snapshot live instances once. Each carries {instance_id, ai_id, session_id}.
    try:
        live = aggregate_all(include_dead=False).get("instances", [])
    except Exception:  # discovery failure must not silently mis-target
        live = []

    def _fmt(insts: list[dict[str, Any]]) -> str:
        return ", ".join(f"{i.get('ai_id') or '?'}/{i.get('instance_id') or '?'}" for i in insts) or "(none)"

    # --session wins: resolve to the live instance running that conversation.
    if session:
        matches = [i["instance_id"] for i in live if i.get("session_id") == session]
        if not matches:
            raise SentinelResolveError(f"no live instance for session '{session}'. Live: {_fmt(live)}")
        return matches

    # Direct runtime instance_id match → passthrough (back-compat fast path).
    if requested and any(i.get("instance_id") == requested for i in live):
        return [requested]

    # --all with no --instance → every live instance (whole-fleet quarantine).
    if want_all and not requested:
        ids = [i["instance_id"] for i in live]
        if not ids:
            raise SentinelResolveError("no live instances to target with --all")
        return ids

    # Practice (ai_id) resolution.
    practice = [i["instance_id"] for i in live if i.get("ai_id") == requested]
    if not practice:
        # 0 → loud error (the silent pause-miss class being killed).
        raise SentinelResolveError(
            f"no live runtime instance for '{requested}' (not a live instance_id, and no live "
            f"instance has ai_id '{requested}'). Live: {_fmt(live)}"
        )
    if len(practice) == 1:
        return practice  # the fix: ai_id → its single live runtime instance
    if want_all:
        return practice  # deliberate whole-practice fan-out (decision-2)
    raise SentinelResolveError(
        f"'{requested}' resolves to {len(practice)} live instances ({practice}). "
        f"Pass --instance <runtime-id>, --session <claude_session_id>, or --all."
    )


def _emit_sentinel_error(args, message: str) -> int:
    """Loud-fail emit for an unresolved/ambiguous sentinel target (exit 1)."""
    return _emit(args, {"ok": False, "error": message}, f"error: {message}")


def _emit(args, payload: dict[str, Any], human_summary: str) -> int:
    """Emit JSON or human output based on --output."""
    fmt = getattr(args, "output", "human")
    if fmt == "json":
        sys.stdout.write(_json.dumps(payload, indent=2, sort_keys=False) + "\n")
    else:
        sys.stdout.write(human_summary + "\n")
    return 0 if payload.get("ok", True) else 1


# ─── empirica sentinel ──────────────────────────────────────────────────────


def _emit_sentinel_state_event(event: str, status, *, _emit_fn=None) -> None:
    """Best-effort: report a Sentinel gating-state change to cortex's system-event
    surface (immune-system Req 1; autonomy ruling prop_72y3dqeb).

    A silent pause that disarms gating is the most observable-worthy event in the
    system — the fleet once ran ungated ~50min undetected. REPORTS flow UP here
    (any practice, about itself, on /v1/system/event which it's entitled to);
    guardians re-emit DIRECTIVES on the source-gated guardian_action channel after
    judgment. ``emit_path=cli_verb`` marks this as verb-emitted so consumers can
    tell it apart from the raw-file-touch blind spot the guardian's filesystem
    watch covers.

    NEVER raises, NEVER blocks — an emit failure must not affect gating.
    """
    try:
        import time as _time

        from empirica.cli.command_handlers.system_event import emit_system_event
        from empirica.utils.session_resolver import InstanceResolver

        emit = _emit_fn or emit_system_event
        try:
            practice = InstanceResolver.ai_id()  # best-available; canonical when resolvable
        except Exception:
            practice = None
        envelope = {
            "event": event,  # sentinel_pause | sentinel_resume
            "scope": getattr(status, "scope", None),
            "practice_ai_id": practice,
            "instance_id": getattr(status, "instance_id", None),
            "actor": "cli",
            "created_at": _time.time(),
            "reason": getattr(status, "reason", None),
            "emit_path": "cli_verb",
        }
        emit(envelope)
    except Exception as e:
        logging.getLogger(__name__).debug("sentinel state-event emit failed (non-fatal): %s", e)


def handle_sentinel_pause_command(args) -> int:
    try:
        targets = _resolve_sentinel_targets(args)
    except SentinelResolveError as e:
        return _emit_sentinel_error(args, str(e))
    reason = getattr(args, "reason", None)
    statuses = [pause_sentinel(t, reason=reason) for t in targets]
    for _st in statuses:
        _emit_sentinel_state_event("sentinel_pause", _st)
    if len(statuses) == 1:
        status = statuses[0]
        payload = {
            "ok": True,
            "paused": status.paused,
            "instance_id": status.instance_id,
            "scope": status.scope,
            "since": status.since,
            "reason": status.reason,
        }
        target = status.instance_id or "global"
        summary = f"Sentinel paused for {target}"
        if status.reason:
            summary += f" (reason: {status.reason})"
        return _emit(args, payload, summary)
    payload = {
        "ok": True,
        "paused_count": len(statuses),
        "instances": [{"instance_id": s.instance_id, "paused": s.paused, "scope": s.scope} for s in statuses],
    }
    ids = ", ".join(s.instance_id or "global" for s in statuses)
    return _emit(args, payload, f"Sentinel paused for {len(statuses)} instances: {ids}")


def handle_sentinel_resume_command(args) -> int:
    try:
        targets = _resolve_sentinel_targets(args)
    except SentinelResolveError as e:
        return _emit_sentinel_error(args, str(e))
    results = [(t, resume_sentinel(t)) for t in targets]
    for _, _st in results:
        _emit_sentinel_state_event("sentinel_resume", _st)
    if len(results) == 1:
        instance_id, status = results[0]
        payload = {
            "ok": True,
            "paused": status.paused,
            "instance_id": status.instance_id,
            "scope": status.scope,
        }
        target = instance_id or "global"
        if status.paused:
            # The instance pause was removed but a global pause still applies.
            summary = (
                f"Sentinel resume requested for {target}, but global pause is still in effect (scope={status.scope})"
            )
        else:
            summary = f"Sentinel resumed for {target}"
        return _emit(args, payload, summary)
    payload = {
        "ok": True,
        "resumed_count": len(results),
        "instances": [{"instance_id": s.instance_id, "paused": s.paused, "scope": s.scope} for _, s in results],
    }
    ids = ", ".join((iid or "global") for iid, _ in results)
    return _emit(args, payload, f"Sentinel resume requested for {len(results)} instances: {ids}")


def handle_sentinel_status_command_cockpit(args) -> int:
    """`empirica sentinel status` — distinct from existing `sentinel-status`."""
    try:
        targets = _resolve_sentinel_targets(args)
    except SentinelResolveError as e:
        return _emit_sentinel_error(args, str(e))
    statuses = [sentinel_status(t) for t in targets]
    if len(statuses) == 1:
        status = statuses[0]
        payload = {
            "ok": True,
            "paused": status.paused,
            "instance_id": status.instance_id,
            "scope": status.scope,
            "since": status.since,
            "reason": status.reason,
        }
        target = status.instance_id or "global"
        if status.paused:
            summary = f"Sentinel PAUSED for {target} (scope={status.scope})"
            if status.since:
                summary += f" since {status.since}"
            if status.reason:
                summary += f" — {status.reason}"
        else:
            summary = f"Sentinel ON for {target}"
        return _emit(args, payload, summary)
    payload = {
        "ok": True,
        "instances": [
            {
                "instance_id": s.instance_id,
                "paused": s.paused,
                "scope": s.scope,
                "since": s.since,
                "reason": s.reason,
            }
            for s in statuses
        ],
    }
    parts = [f"{(s.instance_id or 'global')}={'PAUSED' if s.paused else 'ON'}" for s in statuses]
    return _emit(args, payload, "Sentinel status — " + ", ".join(parts))


# ─── empirica loop ──────────────────────────────────────────────────────────


class InstanceIdRequiredError(ValueError):
    """Raised when an `empirica loop`/`listener` verb requires an instance_id
    but none was resolvable (no --instance flag, no current-process detection).

    Inherits from ValueError (not BaseException like SystemExit) so callers
    using `except Exception` — including the TUI and background loops —
    catch it cleanly without crashing the host process. Same hazard pattern
    that motivated the resolve_project_id → ProjectNotFoundError migration
    (1.9.6).
    """


def _require_instance_id(args) -> str:
    instance_id = _resolve_instance_id(args, fallback_to_current=True)
    if not instance_id:
        raise InstanceIdRequiredError(
            "error: no instance_id available. Set EMPIRICA_INSTANCE_ID or pass --instance ID explicitly."
        )
    return instance_id


def handle_loop_register_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    try:
        entry = registry.register(
            name=args.name,
            kind=args.kind,
            cron=getattr(args, "cron", None),
            interval=getattr(args, "interval", None),
            description=getattr(args, "description", "") or "",
            backoff_policy=getattr(args, "backoff", None),
            base_interval=getattr(args, "base_interval", None),
            max_interval=getattr(args, "max_interval", None),
        )
    except ValueError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "loop": {"name": entry.name, **entry.to_dict()},
    }
    summary = f"Loop registered: {entry.name} ({entry.kind})"
    if entry.backoff.policy == "exponential":
        from empirica.core.cockpit.loop_registry import format_duration

        summary += (
            f" [backoff exponential, base={format_duration(entry.backoff.base_interval_seconds)}, "
            f"max={format_duration(entry.backoff.max_interval_seconds)}]"
        )
    return _emit(args, payload, summary)


def handle_loop_unregister_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    removed = registry.unregister(args.name)
    payload = {"ok": True, "instance_id": instance_id, "removed": removed, "name": args.name}
    summary = f"Loop unregistered: {args.name}" if removed else f"Loop {args.name} was not registered (no-op)"
    return _emit(args, payload, summary)


def handle_loop_pause_command(args) -> int:
    """Pause a loop. Per PROPOSAL_LOOP_SELF_SCHEDULING this also clears
    the next_scheduled_job_id from the registry — pause must mean the
    scheduler is silent, not "body filters every fire."

    Cancellation is scheduler-specific and best-effort:
      - cron-create: the empirica CLI can't call CronDelete (it's a
        Claude Code tool). Registry surfaces the job_id; the body's
        pause check at the next fire is the backstop.
      - systemd-user / at-queue: out-of-process cancellation requires
        a follow-up shell call (not invoked from this handler).

    The body's pause check at start-of-fire remains the source of truth:
    if pause flag exists, body exits without scheduling next fire and
    the loop dies cleanly after at most one more silent fire.
    """
    instance_id = _require_instance_id(args)
    paused = set_loop_paused(instance_id, args.name, paused=True)

    registry = LoopRegistry(instance_id)
    entry = registry.get(args.name)
    cancelled_job_id: str | None = None
    scheduler_kind: str | None = None
    uninstall_pending_path: str | None = None
    if entry is not None:
        cancelled_job_id = entry.scheduling.next_scheduled_job_id
        scheduler_kind = entry.scheduling.scheduler_kind
        if cancelled_job_id:
            # Clear from registry — caller may also need to do scheduler-
            # specific cancellation (CronDelete / systemctl stop / atrm).
            registry.heartbeat(
                name=args.name,
                status=entry.last_status or "ok",
                result=entry.last_result,
                message=entry.last_message,
                next_scheduled_job_id="",
            )
            # CronCreate-mode: surface a pending uninstall request so the
            # owning Claude instance picks it up via UserPromptSubmit hook
            # and calls CronDelete from inside that CC session. The empirica
            # CLI can't call CronDelete itself.
            if scheduler_kind == "cron-create":
                pending = write_uninstall_pending(
                    instance_id=instance_id,
                    name=args.name,
                    job_id=cancelled_job_id,
                    scheduler_kind=scheduler_kind,
                    requested_by=get_instance_id(),
                    reason="manual pause",
                )
                uninstall_pending_path = str(pending)

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "paused": paused,
        "cancelled_job_id": cancelled_job_id,
        "scheduler_kind": scheduler_kind,
        "uninstall_pending_path": uninstall_pending_path,
    }
    summary = f"Loop paused: {args.name}"
    if cancelled_job_id:
        summary += f" · cleared next_job={cancelled_job_id}"
        if scheduler_kind == "cron-create":
            if uninstall_pending_path:
                summary += (
                    " · queued CronDelete request for owning instance "
                    "(picked up via UserPromptSubmit; body pause-check is the backstop)"
                )
            else:
                summary += " (CronCreate: body pause-check is the backstop; next fire will exit silently)"
    return _emit(args, payload, summary)


def handle_loop_resume_command(args) -> int:
    """Resume a loop. Per PROPOSAL_LOOP_SELF_SCHEDULING the empirica CLI
    can't reinstall a CronCreate one-shot directly — surface a hint so
    the user knows to re-issue via /loop or trigger one fire manually.
    """
    instance_id = _require_instance_id(args)
    paused = set_loop_paused(instance_id, args.name, paused=False)
    registry = LoopRegistry(instance_id)
    entry = registry.get(args.name)
    scheduler_kind = entry.scheduling.scheduler_kind if entry else None
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "paused": paused,
        "scheduler_kind": scheduler_kind,
    }
    summary = f"Loop resumed: {args.name}"
    if scheduler_kind == "cron-create":
        summary += f" · re-issue via /loop or run `empirica loop fire {args.name}`"
    return _emit(args, payload, summary)


def handle_loop_set_interval_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    try:
        entry = registry.set_interval(args.name, args.interval)
    except (KeyError, ValueError) as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "loop": {"name": entry.name, **entry.to_dict()},
    }
    return _emit(args, payload, f"Loop interval set: {args.name} → {args.interval}")


def handle_loop_heartbeat_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    try:
        entry = registry.heartbeat(
            name=args.name,
            status=args.status,
            result=getattr(args, "result", None),
            message=getattr(args, "message", None),
            next_scheduled_job_id=getattr(args, "next_scheduled_job_id", None),
            scheduler_kind=getattr(args, "scheduler_kind", None),
        )
    except ValueError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "loop": {"name": entry.name, **entry.to_dict()},
        "paused": is_loop_paused(instance_id, entry.name),
    }
    summary = f"Loop heartbeat: {entry.name} → {entry.last_status}/{entry.last_result}"
    if entry.last_message:
        summary += f" ({entry.last_message})"
    if entry.backoff.policy == "exponential":
        from empirica.core.cockpit.loop_registry import format_duration

        summary += (
            f" · streak={entry.backoff.empty_streak} next≥{format_duration(entry.backoff.current_interval_seconds())}"
        )
    if entry.scheduling.next_scheduled_job_id:
        summary += f" · next_job={entry.scheduling.next_scheduled_job_id}"
    return _emit(args, payload, summary)


def handle_loop_schedule_next_command(args) -> int:
    """Compute the next-fire timestamp + cron expression for a self-scheduling loop.

    Per PROPOSAL_LOOP_SELF_SCHEDULING — body owns the schedule. After
    each fire (and after pause check passes), the body calls this to
    learn when to install the next one-shot.
    """
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    plan = registry.schedule_next(args.name)
    if plan is None:
        return _emit(
            args,
            {"ok": False, "error": f"loop {args.name!r} not registered"},
            f"error: loop {args.name!r} not registered",
        )
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        **plan.to_dict(),
    }
    summary = f"next fire: {plan.fire_at.isoformat()} ({plan.cron_one_shot}) — {plan.reason}"
    return _emit(args, payload, summary)


def handle_loop_install_request_command(args) -> int:
    """Cockpit→Claude install path: register loop in target's registry and
    drop a pending install request that the target instance's
    UserPromptSubmit hook surfaces as a system-reminder. The target Claude
    sees the reminder, runs `/loop` with the embedded prompt template,
    and CronCreate fires from inside that CC session.

    The cockpit runs `empirica loop install-request --instance <ID> --name X
    --interval 15m` to make this happen — no manual /loop paste needed.
    """
    target_instance = getattr(args, "instance", None)
    if not target_instance:
        return _emit(
            args,
            {"ok": False, "error": "--instance required (target instance to install in)"},
            "error: --instance required",
        )

    name = args.name
    interval = args.interval
    description = getattr(args, "description", "") or ""
    # Optional body_skill: if a paired skill exists with this loop's
    # name in the canonical catalog (or explicitly via --body-skill),
    # render_loop_cron_prompt uses its `## Cron Prompt Template` section
    # as the full prompt — no `[... your actual work ...]` placeholder.
    body_skill = getattr(args, "body_skill", None)
    if not body_skill:
        # Auto-resolve from canonical catalog by loop name
        try:
            from empirica.core.cockpit.canonical_loops import canonical_loop_by_name

            entry = canonical_loop_by_name(name)
            if entry and entry.get("body_skill"):
                body_skill = entry["body_skill"]
        except Exception:
            pass  # canonical lookup is best-effort
    # Fallback chain: explicit --base-interval > --interval > '15m' default.
    # Same fallback applies to interval itself when absent: project.yaml
    # entries with `kind: cron` + `cron: "..."` legitimately omit interval
    # (the cron expression is the schedule), but the loop-cron prompt
    # template substitutes interval into backoff config — a None there
    # writes the literal string 'None' into the prompt and produces a
    # malformed `--interval "None"` flag in the body's register call.
    base_interval = getattr(args, "base_interval", None) or interval or "15m"
    max_interval = getattr(args, "max_interval", None) or "4h"
    # If interval wasn't supplied (cron-only loop), use the resolved
    # base_interval so the rendered prompt template is well-formed.
    if not interval:
        interval = base_interval

    # Register in the target's registry first so the loop is visible in the
    # cockpit immediately — even before the target Claude installs CronCreate.
    registry = LoopRegistry(target_instance)
    try:
        entry = registry.register(
            name=name,
            kind="cron",
            interval=interval,
            description=description,
            backoff_policy="exponential",
            base_interval=base_interval,
            max_interval=max_interval,
        )
    except ValueError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")

    # Stamp scheduler_kind so heartbeat fields don't drift later.
    registry.heartbeat(
        name=name,
        status=entry.last_status or "ok",
        result=entry.last_result,
        message=entry.last_message,
        scheduler_kind=DEFAULT_SCHEDULER_KIND,
    )

    # Resolve the cockpit's own instance_id (best-effort) so the receiver
    # can show 'requested by tmux_X' in the system-reminder.
    requested_by: str | None = None
    try:
        from empirica.utils.session_resolver import get_instance_id

        requested_by = get_instance_id()
    except Exception:
        requested_by = None

    pending = write_pending(
        instance_id=target_instance,
        name=name,
        interval=interval,
        description=description,
        scheduler_kind=DEFAULT_SCHEDULER_KIND,
        requested_by=requested_by,
        base_interval=base_interval,
        max_interval=max_interval,
        body_skill=body_skill,
    )

    payload = {
        "ok": True,
        "instance_id": target_instance,
        "name": name,
        "interval": interval,
        "pending_request_path": str(pending),
        "requested_by": requested_by,
        "scheduler_kind": DEFAULT_SCHEDULER_KIND,
        "next_step": (
            f"Target Claude in {target_instance} will see the install request "
            f"on its next prompt and run /loop to call CronCreate"
        ),
    }
    summary = f"Install request queued for {name} in {target_instance} ({interval}) — surfaces on next UserPromptSubmit"
    return _emit(args, payload, summary)


def handle_loop_fire_command(args) -> int:
    """Manual fire — bootstrap after resume, test the body, or bypass backoff.

    For CronCreate-mode loops this can't actually invoke the loop body
    (the empirica CLI doesn't have CronCreate access). Instead it
    reports the cron expression the body would install AND the prompt
    template the user should re-issue via /loop. For loops with no
    cron template captured, just emits the schedule plan so the caller
    knows what to install.
    """
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    entry = registry.get(args.name)
    if entry is None:
        return _emit(
            args,
            {"ok": False, "error": f"loop {args.name!r} not registered"},
            f"error: loop {args.name!r} not registered",
        )
    plan = registry.schedule_next(args.name)
    payload: dict[str, Any] = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "scheduler_kind": entry.scheduling.scheduler_kind,
        "paused": is_loop_paused(instance_id, args.name),
    }
    if plan is not None:
        payload.update(plan.to_dict())
    scheduler_kind = entry.scheduling.scheduler_kind or "unknown"
    if scheduler_kind == "cron-create":
        payload["hint"] = (
            f"empirica CLI can't call CronCreate directly. Re-issue via "
            f"/loop or run: CronCreate(cron='{plan.cron_one_shot}', "
            f"recurring=false, prompt='<loop body template>')"
            if plan
            else "no schedule plan — register loop first"
        )
        summary = (
            f"fire requested for {args.name} — install `{plan.cron_one_shot}`"
            if plan
            else f"fire requested for {args.name}"
        )
    else:
        summary = (
            f"fire requested for {args.name} ({scheduler_kind}) — `{plan.cron_one_shot}` at {plan.fire_at.isoformat()}"
            if plan
            else f"fire requested for {args.name} ({scheduler_kind})"
        )
    return _emit(args, payload, summary)


def handle_loop_should_fire_command(args) -> int:
    """Exit 0 if loop body should fire this cron tick, exit 1 if backoff says skip.

    Loop scripts use this between the pause check and the actual work:

      if ! empirica loop should-fire poll-name; then exit 0; fi

    JSON output also includes the reason for traceability.
    """
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    should, reason = registry.should_fire(args.name)
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "should_fire": should,
        "reason": reason,
    }
    summary = f"{'FIRE' if should else 'SKIP'} ({reason})"
    _emit(args, payload, summary)
    return 0 if should else 1


def handle_loop_poke_command(args) -> int:
    """Manual escape hatch — zero the streak, clear the next_fire_threshold."""
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    entry = registry.poke(args.name)
    if entry is None:
        payload = {
            "ok": False,
            "error": f"loop not registered: {args.name}",
            "instance_id": instance_id,
            "name": args.name,
        }
        return _emit(args, payload, payload["error"])
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "loop": {"name": entry.name, **entry.to_dict()},
    }
    return _emit(args, payload, f"Loop poked: {args.name} (streak cleared, next fire allowed)")


def handle_loop_list_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    loops = registry.list_loops()

    payload_loops = []
    for entry in loops:
        d = entry.to_dict()
        d["name"] = entry.name
        d["paused"] = is_loop_paused(instance_id, entry.name)
        payload_loops.append(d)

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "count": len(loops),
        "loops": payload_loops,
    }

    if not loops:
        summary = f"No loops registered for {instance_id}"
    else:
        rows = [f"  {l['name']:<20} {l['kind']:<8} paused={l['paused']}" for l in payload_loops]
        summary = f"Loops registered for {instance_id}:\n" + "\n".join(rows)

    return _emit(args, payload, summary)


def handle_loop_status_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    entry = registry.get(args.name)
    if entry is None:
        payload = {
            "ok": False,
            "error": f"loop not registered: {args.name}",
            "instance_id": instance_id,
            "name": args.name,
            "paused": False,
        }
        return _emit(args, payload, payload["error"])

    paused = is_loop_paused(instance_id, args.name)
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "loop": {"name": entry.name, **entry.to_dict()},
        "paused": paused,
    }
    summary = (
        f"{entry.name}: kind={entry.kind} paused={paused} last_run={entry.last_run} last_status={entry.last_status}"
    )
    return _emit(args, payload, summary)


# ─── systemd-user scheduler (Phase 1a — goal f718156c) ──────────────────────
#
# Replaces /loop's in-session CronCreate with an OS-level timer that fires
# regardless of Claude's state. The wake-from-idle bridge into a running
# session is the Monitor tool (armed at SessionStart — Phase 1b).
#
# True pause becomes `systemctl --user disable --now` — atomic, no Claude
# cooperation needed. AFK token cost drops to zero while disabled.


def handle_loop_enable_command(args) -> int:
    """Install systemd-user timer + register loop in registry.

    Combines two things the old CronCreate path required separate steps for:
    (1) writing/starting the timer (via systemctl), (2) creating a registry
    entry so the TUI/cockpit sees the loop. Registry entry is stamped with
    scheduler_kind='systemd' so subsequent toggles route via systemctl, not
    via the file-flag pause path.

    Resolves the empirica binary to an absolute path via shutil.which()
    before baking it into the service ExecStart. systemd-user environments
    don't inherit shell PATH, so bare `empirica` fails silently when the
    binary lives in `~/.local/bin` (pipx) or a venv. Smoke-tested 2026-05-15.
    """
    import shutil as _sh

    from empirica.core.loop_scheduler import (
        LoopSchedulerUnavailable,
        get_loop_scheduler,
    )

    instance_id = _require_instance_id(args)
    empirica_bin = _sh.which("empirica")
    if not empirica_bin:
        return _emit(
            args,
            {
                "ok": False,
                "error": "Could not resolve absolute path to 'empirica' via shutil.which(). "
                "Install via pipx (`pipx install empirica`) or activate the venv "
                "before enabling a loop — otherwise the scheduler fires but the "
                "service/agent can't find the binary.",
            },
            "enable failed: empirica binary not on PATH",
        )
    # T10: get_loop_scheduler picks systemd-user (Linux/WSL2) or launchd
    # (macOS). Same API surface across backends; handler stays portable.
    # Resolve a direct-exec body_command for pure-CLI canonical loops
    # (body_kind="cli", e.g. message-cleanup → runs on the timer autonomously).
    # Every other loop — including claude-react / monitor / listener loops like
    # cortex-mailbox-poll — passes body_command=None and gets the tick-only
    # ExecStart, so the timer can never run it autonomously. Wake-on-event stays
    # the sole trigger for those (David 2026-07-22).
    from empirica.core.cockpit.canonical_loops import canonical_loop_by_name

    _entry = canonical_loop_by_name(args.name)
    body_command = _entry.get("body_command") if (_entry and _entry.get("body_kind") == "cli") else None
    try:
        sched = get_loop_scheduler(empirica_bin=empirica_bin)
        paths = sched.enable(instance_id, args.name, args.interval, body_command=body_command)
    except LoopSchedulerUnavailable as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"no scheduler available: {e}")
    except Exception as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"enable failed: {e}")

    # Register in the cockpit's loop registry (idempotent — register catches
    # the duplicate case). Stamp scheduler_kind='systemd' via heartbeat so
    # later toggles know to route through systemctl rather than the legacy
    # pause sidecar.
    registry = LoopRegistry(instance_id)
    description = getattr(args, "description", "") or ""
    try:
        entry = registry.register(
            name=args.name,
            kind="interval",
            interval=args.interval,
            description=description,
        )
        registry.heartbeat(
            name=args.name,
            status=entry.last_status or "ok",
            result=entry.last_result,
            message=entry.last_message,
            scheduler_kind="systemd-user",
        )
    except ValueError:
        # Already registered — refresh scheduler_kind only.
        try:
            existing = registry.get(args.name)
            registry.heartbeat(
                name=args.name,
                status=existing.last_status if existing else "ok",
                result=existing.last_result if existing else None,
                message=existing.last_message if existing else None,
                scheduler_kind="systemd-user",
            )
        except Exception:
            pass  # registry stamp is best-effort; systemd state is source of truth

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "interval": args.interval,
        "scheduler_kind": "systemd",
        "timer_path": str(paths.timer),
        "service_path": str(paths.service),
    }
    return _emit(args, payload, f"enabled {args.name} (every {args.interval}) — timer + registry stamped systemd")


def handle_loop_disable_command(args) -> int:
    """Stop+remove OS scheduler entry (systemd timer or launchd agent).
    Registry entry stays (toggle off shouldn't forget the loop) — use
    `loop unregister` to fully forget."""
    from empirica.core.loop_scheduler import (
        LoopSchedulerUnavailable,
        get_loop_scheduler,
    )

    instance_id = _require_instance_id(args)
    try:
        sched = get_loop_scheduler()
        removed = sched.disable(instance_id, args.name)
    except LoopSchedulerUnavailable as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"no scheduler available: {e}")
    payload = {"ok": True, "instance_id": instance_id, "name": args.name, "removed": removed}
    summary = f"disabled {args.name} (scheduler entry removed)" if removed else f"{args.name} was not enabled — no-op"
    return _emit(args, payload, summary)


def handle_loop_systemd_status_command(args) -> int:
    """Query the OS scheduler for the loop's state. Verb name kept as
    'systemd-status' for back-compat — it works on launchd too via the
    portable scheduler interface."""
    from empirica.core.loop_scheduler import (
        LoopSchedulerUnavailable,
        get_loop_scheduler,
    )

    instance_id = _require_instance_id(args)
    try:
        sched = get_loop_scheduler()
        st = sched.status(instance_id, args.name)
    except LoopSchedulerUnavailable as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"no scheduler available: {e}")
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "active": st.active,
        "enabled": st.enabled,
        "last_trigger": st.last_trigger,
        "next_trigger": st.next_trigger,
    }
    summary = (
        f"{args.name}: active={st.active} enabled={st.enabled} "
        f"last={st.last_trigger or '—'} next={st.next_trigger or '—'}"
    )
    return _emit(args, payload, summary)


def handle_loop_tick_command(args) -> int:
    """systemd-user service ExecStart target. Appends one JSON event to the
    fires log; the Monitor bridge tails this and relays into the running
    Claude session. Must succeed even on systems without systemd (the log
    write is the contract; the timer mechanism is separate).

    Ghost-fire guard: a stale/ghost unit — an old
    ``com.empirica.loop.<placeholder>.*`` or a loop whose registry entry was
    removed without its timer — must not keep appending wake events. Refuse a
    placeholder instance id or a loop not in the instance's registry (clean
    skip, exit 0 — the scheduler unit itself should be removed, but a ghost
    tick must not pollute the fires log meanwhile)."""
    from empirica.core.loop_scheduler import SystemdLoopScheduler
    from empirica.core.loop_scheduler.launchd import is_placeholder_instance

    if is_placeholder_instance(args.instance_id):
        return _emit(
            args,
            {"ok": True, "skipped": "placeholder_instance", "instance_id": args.instance_id, "name": args.name},
            f"tick skipped: placeholder instance {args.instance_id!r} (ghost unit — remove it)",
        )
    try:
        if LoopRegistry(args.instance_id).get(args.name) is None:
            return _emit(
                args,
                {"ok": True, "skipped": "not_registered", "instance_id": args.instance_id, "name": args.name},
                f"tick skipped: {args.instance_id}/{args.name} not in registry (ghost unit — remove it)",
            )
    except Exception:
        pass  # registry read is best-effort — never block a legit tick on a read hiccup

    try:
        path = SystemdLoopScheduler.tick(args.instance_id, args.name)
    except Exception as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"tick failed: {e}")
    return _emit(
        args,
        {"ok": True, "log_path": str(path), "instance_id": args.instance_id, "name": args.name},
        f"tick: {args.instance_id}/{args.name}",
    )


def handle_loop_listen_command(args) -> int:
    """Long-running ntfy listener — push-primary wake mechanism.

    Holds an authenticated stream to cortex's ntfy topic. Each message
    arrival triggers a content_poll catch-up that emits one stdout line
    per ECO-decided proposal event. SessionStart hook arms Monitor on
    this command's stdout — each line becomes a Monitor wake event in
    the running Claude session.

    Runs forever (or until SIGTERM). Exit code 0 on clean stop, nonzero
    on configuration errors (missing ntfy creds → exit 2 so systemd /
    Monitor lifecycle surfaces the problem clearly).
    """
    from empirica.core.loop_scheduler import run_listener

    instance_id = _require_instance_id(args)
    loop_name = getattr(args, "loop_name", None) or "cortex-mailbox-poll"
    return run_listener(instance_id, loop_name)


def _resolve_listener_ai_id(args) -> str:
    """Use --ai-id if provided, else derive from project.yaml basename.

    Falls back to `_require_instance_id` resolution for parity with the
    other loop subcommands.
    """
    if getattr(args, "ai_id", None):
        return args.ai_id
    # Fall back to project basename / instance id
    return _require_instance_id(args)


def handle_loop_listen_install_command(args) -> int:
    """Install the persistent listener service for an ai_id.

    OS-detected (systemd-user / launchd). Closes prop_flrtxxn32japbazq —
    listener stays alive even when no Claude session is open, so wake
    events arrive in real time.
    """
    from empirica.core.loop_scheduler.persistent_listener import (
        ListenerServiceUnavailable,
        PersistentListenerService,
    )

    ai_id = _resolve_listener_ai_id(args)
    service = PersistentListenerService()
    try:
        unit_path = service.install(ai_id)
    except ListenerServiceUnavailable as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")
    except subprocess.CalledProcessError as e:
        return _emit(args, {"ok": False, "error": f"install failed: {e}"}, f"error: {e}")

    status = service.status(ai_id)
    payload = {
        "ok": True,
        "ai_id": ai_id,
        "backend": service.backend,
        "unit_path": str(unit_path),
        "log_path": status.log_path,
        "active": status.active,
    }
    summary = (
        f"Installed persistent listener ({service.backend}) for {ai_id}\n"
        f"  unit: {unit_path}\n"
        f"  log:  {status.log_path}\n"
        f"  active: {status.active}"
    )
    return _emit(args, payload, summary)


def handle_loop_listen_uninstall_command(args) -> int:
    """Stop + remove the persistent listener service. Idempotent."""
    from empirica.core.loop_scheduler.persistent_listener import (
        PersistentListenerService,
    )

    ai_id = _resolve_listener_ai_id(args)
    service = PersistentListenerService()
    removed = service.uninstall(ai_id)
    payload = {
        "ok": True,
        "ai_id": ai_id,
        "backend": service.backend,
        "removed": removed,
    }
    summary = f"Uninstalled persistent listener for {ai_id}" if removed else f"No listener service for {ai_id} (no-op)"
    return _emit(args, payload, summary)


def handle_loop_listen_status_command(args) -> int:
    """Inspect the persistent listener service state."""
    from empirica.core.loop_scheduler.persistent_listener import (
        PersistentListenerService,
    )

    ai_id = _resolve_listener_ai_id(args)
    status = PersistentListenerService().status(ai_id)
    payload = {
        "ok": True,
        "ai_id": ai_id,
        "backend": status.backend,
        "installed": status.installed,
        "active": status.active,
        "unit_path": status.unit_path,
        "log_path": status.log_path,
    }
    if status.backend == "unavailable":
        summary = (
            f"Listener service: unavailable on this host ({sys.platform}). "
            f"Linux/WSL2 needs systemd-user, macOS needs launchctl."
        )
    elif not status.installed:
        summary = f"Listener service ({status.backend}) for {ai_id}: NOT installed"
    else:
        state = "active" if status.active else "INSTALLED but inactive"
        summary = (
            f"Listener service ({status.backend}) for {ai_id}: {state}\n"
            f"  unit: {status.unit_path}\n"
            f"  log:  {status.log_path}"
        )
    return _emit(args, payload, summary)


# ─── empirica listener ──────────────────────────────────────────────────────
#
# Sister concept to `empirica loop` but event-driven (PROPOSAL_EVENT_LISTENER).
# Listeners hold an open subscription (ntfy/SSE/WebSocket) and wake when an
# event arrives — no periodic firing. The registry surface mirrors loop's
# (register/pause/resume/list/status/unregister) plus listener-specific
# verbs (record-wake, fire). Mechanical Monitor-kill on pause is deferred
# to item 4 (the install-request analog with runtime metadata).


def handle_listener_register_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    try:
        entry = registry.register(
            name=args.name,
            topic=args.topic,
            description=getattr(args, "description", "") or "",
            on_wake_template=getattr(args, "on_wake", "") or "",
        )
    except ValueError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "listener": {"name": entry.name, **entry.to_dict()},
    }
    summary = f"Listener registered: {entry.name} (topic={entry.topic})"
    return _emit(args, payload, summary)


def handle_listener_unregister_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    removed = registry.unregister(args.name)
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "removed": removed,
        "name": args.name,
    }
    summary = f"Listener unregistered: {args.name}" if removed else f"Listener {args.name} was not registered (no-op)"
    return _emit(args, payload, summary)


def handle_listener_pause_command(args) -> int:
    """Pause a listener — mechanical-via-pickup-hook.

    Writes the pause sidecar (advisory layer for body short-circuit at
    next wake) AND, when the listener is armed (active runtime file
    present with monitor_task_id + curl_pid), writes a pending uninstall
    request that the owning instance's UserPromptSubmit hook surfaces
    on next prompt asking Claude to TaskStop the Monitor and kill the
    held curl.

    The body's pause check at next wake is the backstop if Claude
    doesn't run TaskStop/kill in time.
    """
    instance_id = _require_instance_id(args)
    paused = set_listener_paused(instance_id, args.name, paused=True)

    uninstall_pending_path: str | None = None
    monitor_task_id: str | None = None
    curl_pid: int | None = None

    active_path = listener_active_path(instance_id, args.name)
    if active_path.exists():
        try:
            with open(active_path, encoding="utf-8") as f:
                active_data = _json.load(f)
            monitor_task_id = active_data.get("monitor_task_id") or None
            raw_pid = active_data.get("curl_pid")
            curl_pid = int(raw_pid) if raw_pid is not None else None
        except (OSError, ValueError, _json.JSONDecodeError):
            # Corrupt active file — pause flag is set; the body backstop
            # still works. Skip the pending-uninstall write.
            pass

    if monitor_task_id:
        pending = write_listener_uninstall_pending(
            instance_id=instance_id,
            name=args.name,
            monitor_task_id=monitor_task_id,
            curl_pid=curl_pid,
            requested_by=get_instance_id(),
            reason="manual pause",
        )
        uninstall_pending_path = str(pending)

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "paused": paused,
        "monitor_task_id": monitor_task_id,
        "curl_pid": curl_pid,
        "uninstall_pending_path": uninstall_pending_path,
    }

    summary = f"Listener paused: {args.name}"
    if uninstall_pending_path:
        summary += (
            f" · queued TaskStop request for Monitor {monitor_task_id} "
            "(picked up via UserPromptSubmit; body pause-check is the backstop)"
        )
    elif active_path.exists():
        summary += " (active file present but missing monitor_task_id — body pause-check is the only backstop)"
    else:
        summary += " (no active runtime — listener was already disarmed)"

    return _emit(args, payload, summary)


def handle_listener_resume_command(args) -> int:
    instance_id = _require_instance_id(args)
    paused = set_listener_paused(instance_id, args.name, paused=False)
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": args.name,
        "paused": paused,
    }
    summary = f"Listener resumed: {args.name} (re-arm via the inbox-listener skill or run `empirica listener fire`)"
    return _emit(args, payload, summary)


def handle_listener_record_wake_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    try:
        entry = registry.record_wake(
            name=args.name,
            message=getattr(args, "message", None),
        )
    except KeyError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "listener": {"name": entry.name, **entry.to_dict()},
        "paused": is_listener_paused(instance_id, entry.name),
    }
    summary = f"Listener wake: {entry.name} → count={entry.wake_count} last_at={entry.last_wake_at}"
    if entry.last_message:
        summary += f" ({entry.last_message})"
    return _emit(args, payload, summary)


def handle_listener_fire_command(args) -> int:
    """Manually trigger a wake — V1 just records-wake, doesn't actually
    inject a wake into the listener body. The actual wake injection
    happens in item 4 (the install-request analog) where the listener
    body knows how to be poked. This verb is a placeholder for that
    flow plus a working "I want to count one fire" affordance for tests.
    """
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    if registry.get(args.name) is None:
        return _emit(
            args,
            {"ok": False, "error": f"listener not registered: {args.name}"},
            f"error: listener not registered: {args.name}",
        )
    entry = registry.record_wake(args.name, message="manual fire")
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "listener": {"name": entry.name, **entry.to_dict()},
    }
    summary = f"Listener fired: {entry.name} (V1: counted only — wake injection lands in item 4)"
    return _emit(args, payload, summary)


def handle_listener_list_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    listeners = registry.list_listeners()

    payload_listeners = []
    for entry in listeners:
        d = entry.to_dict()
        d["name"] = entry.name
        d["paused"] = is_listener_paused(instance_id, entry.name)
        payload_listeners.append(d)

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "count": len(listeners),
        "listeners": payload_listeners,
    }

    if not listeners:
        summary = f"No listeners registered for {instance_id}"
    else:
        rows = [
            f"  {item['name']:<20} {item['topic']:<35} paused={item['paused']} wakes={item['wake_count']}"
            for item in payload_listeners
        ]
        summary = f"Listeners registered for {instance_id}:\n" + "\n".join(rows)

    return _emit(args, payload, summary)


def handle_listener_status_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    entry = registry.get(args.name)
    if entry is None:
        payload = {
            "ok": False,
            "error": f"listener not registered: {args.name}",
            "instance_id": instance_id,
            "name": args.name,
            "paused": False,
        }
        return _emit(args, payload, payload["error"])

    paused = is_listener_paused(instance_id, args.name)
    payload = {
        "ok": True,
        "instance_id": instance_id,
        "listener": {"name": entry.name, **entry.to_dict()},
        "paused": paused,
    }
    summary = (
        f"{entry.name}: topic={entry.topic} paused={paused} wakes={entry.wake_count} last_wake_at={entry.last_wake_at}"
    )
    return _emit(args, payload, summary)


def handle_listener_install_request_command(args) -> int:
    """Cockpit→Claude install path for listeners. Symmetric to
    handle_loop_install_request_command. Registers the listener in the
    target's registry and drops a pending install request that the
    target instance's UserPromptSubmit hook surfaces as a system-reminder.
    The target Claude sees the reminder, runs `/inbox-listener` with
    the embedded prompt template, arms the curl + Monitor, and writes
    the listener_active_*.json runtime metadata.
    """
    target_instance = getattr(args, "instance", None)
    if not target_instance:
        return _emit(
            args,
            {"ok": False, "error": "--instance required (target instance to install in)"},
            "error: --instance required",
        )

    name = args.name
    topic = args.topic
    description = getattr(args, "description", "") or ""
    on_wake = getattr(args, "on_wake", "") or ""

    # Register first so the listener is visible in the cockpit immediately
    # — even before the target Claude arms the curl + Monitor.
    registry = ListenerRegistry(target_instance)
    try:
        entry = registry.register(
            name=name,
            topic=topic,
            description=description,
            on_wake_template=on_wake,
        )
    except ValueError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")

    pending_path_obj = write_listener_install_pending(
        instance_id=target_instance,
        name=name,
        topic=topic,
        description=description,
        on_wake_template=on_wake,
        requested_by=get_instance_id(),
    )

    payload = {
        "ok": True,
        "instance_id": target_instance,
        "listener": {"name": entry.name, **entry.to_dict()},
        "pending_path": str(pending_path_obj),
    }
    summary = (
        f"Listener install requested: {name} (topic={topic}) → {target_instance} "
        "· pending file written; owning Claude will pick it up via UserPromptSubmit"
    )
    return _emit(args, payload, summary)


# ─── empirica status ────────────────────────────────────────────────────────


def handle_tui_command(args) -> int:
    """Launch the Textual cockpit TUI."""
    try:
        from empirica.cli.tui import run_tui
    except ImportError as e:
        sys.stdout.write(f'error: TUI requires the textual package — {e}\ninstall with: pip install "empirica[tui]"\n')
        return 2
    return run_tui(include_dead=bool(getattr(args, "include_dead", False)))


def handle_status_command(args) -> int:
    """Top-level cockpit overview command.

    Modes:
      --all           every discoverable instance
      --instance ID   single specified instance
      (default)       current instance (auto-detected) or all if no instance
      --json          machine-readable output
      --pretty        ANSI colored output (default for --output human)
    """
    fmt = getattr(args, "output", None)
    json_mode = getattr(args, "json", False) or fmt == "json"
    pretty_mode = getattr(args, "pretty", False) or (fmt == "human" and not json_mode)
    if not json_mode and not pretty_mode:
        # Default: pretty when stdout is a TTY, json otherwise (for piping).
        pretty_mode = sys.stdout.isatty()
        json_mode = not pretty_mode

    explicit_instance = getattr(args, "instance", None)
    show_all = getattr(args, "all", False)
    include_dead = bool(getattr(args, "include_dead", False))

    if explicit_instance:
        payload = {
            "generated_at": aggregate_all()["generated_at"],
            "instances": [aggregate_instance_state(explicit_instance)],
            "summary": {
                "instances": 1,
                "loops_registered": 0,
                "loops_paused": 0,
                "active_tx": 0,
                "notify_dispatcher": build_notify_dispatcher_block(),
            },
        }
        # Refresh summary from the single instance.
        instance: dict[str, Any] = payload["instances"][0]
        loops: dict[str, Any] = instance.get("loops", {})
        payload["summary"]["loops_registered"] = len(loops)
        payload["summary"]["loops_paused"] = sum(1 for v in loops.values() if v.get("paused"))
        payload["summary"]["active_tx"] = sum(
            1 for inst in payload["instances"] if inst["phase"] in ("noetic", "praxic")
        )
        all_mode = False
    elif show_all:
        payload = aggregate_all(include_dead=include_dead)
        all_mode = True
    else:
        current = get_instance_id()
        if current:
            payload = {
                "generated_at": aggregate_all(include_dead=True)["generated_at"],
                "instances": [aggregate_instance_state(current)],
                "summary": {
                    "instances": 1,
                    "loops_registered": 0,
                    "loops_paused": 0,
                    "active_tx": 0,
                    "notify_dispatcher": build_notify_dispatcher_block(),
                },
            }
            instance: dict[str, Any] = payload["instances"][0]
            loops: dict[str, Any] = instance.get("loops", {})
            payload["summary"]["loops_registered"] = len(loops)
            payload["summary"]["loops_paused"] = sum(1 for v in loops.values() if v.get("paused"))
            payload["summary"]["active_tx"] = sum(
                1 for inst in payload["instances"] if inst["phase"] in ("noetic", "praxic")
            )
            all_mode = False
        else:
            payload = aggregate_all(include_dead=include_dead)
            all_mode = True

    if json_mode:
        sys.stdout.write(render_json(payload) + "\n")
    else:
        sys.stdout.write(render_pretty(payload, all_instances=all_mode) + "\n")
    return 0


# ─── empirica instance ─────────────────────────────────────────────────────


def handle_instance_kill_command(args) -> int:
    instance_id = args.instance_id
    force = bool(getattr(args, "force", False))
    yes = bool(getattr(args, "yes", False))

    # Defensive: don't let a stray command kill the very Claude that runs it.
    current = get_instance_id()
    if instance_id == current and not yes:
        payload = {
            "ok": False,
            "error": "refusing to kill the current instance — pass --yes to override",
            "instance_id": instance_id,
        }
        return _emit(args, payload, payload["error"])

    result = kill_instance(instance_id, force=force)
    payload = {
        "ok": result.success,
        "instance_id": result.instance_id,
        "method": result.method,
        "pid": result.pid,
        "detail": result.detail,
    }
    summary = (
        f"Killed {instance_id} ({result.method}): {result.detail}"
        if result.success
        else f"Kill failed for {instance_id}: {result.detail}"
    )
    return _emit(args, payload, summary)


def handle_instance_forget_command(args) -> int:
    instance_id = args.instance_id
    yes = bool(getattr(args, "yes", False))

    current = get_instance_id()
    if instance_id == current and not yes:
        payload = {
            "ok": False,
            "error": "refusing to forget the current instance — pass --yes to override",
            "instance_id": instance_id,
        }
        return _emit(args, payload, payload["error"])

    result = forget_instance(instance_id)
    payload = {
        "ok": True,
        "instance_id": result.instance_id,
        "removed": result.removed,
        "skipped": result.skipped,
        "count": len(result.removed),
    }
    if not result.removed and not result.skipped:
        summary = f"Nothing to forget for {instance_id} — no state files found"
    else:
        summary = f"Forgot {instance_id}: removed {len(result.removed)} files"
        if result.skipped:
            summary += f" ({len(result.skipped)} skipped)"
    return _emit(args, payload, summary)


def handle_instance_label_command(args) -> int:
    instance_id = args.instance_id
    label = getattr(args, "label", None)
    clear = bool(getattr(args, "clear", False))

    if clear:
        set_label(instance_id, None)
        payload = {"ok": True, "instance_id": instance_id, "label": None, "cleared": True}
        return _emit(args, payload, f"Label cleared for {instance_id}")

    if label is None:
        existing = get_label(instance_id)
        payload = {"ok": True, "instance_id": instance_id, "label": existing}
        return _emit(args, payload, f"{instance_id}: {existing or '(no manual label)'}")

    new_label = set_label(instance_id, label)
    payload = {"ok": True, "instance_id": instance_id, "label": new_label, "cleared": False}
    return _emit(args, payload, f"Label set for {instance_id}: {new_label}")


def handle_instance_prune_command(args) -> int:
    """Bulk forget every instance that fails the liveness check.

    Skips the current instance (it's running this code, by definition alive).
    With --dry-run, prints what would be removed without removing anything.
    """
    dry_run = bool(getattr(args, "dry_run", False))
    dead = discover_dead_instances()

    if not dead:
        payload = {"ok": True, "pruned": [], "dry_run": dry_run, "count": 0}
        return _emit(args, payload, "No dead instances to prune")

    pruned: list[dict[str, Any]] = []
    for iid in dead:
        if dry_run:
            pruned.append({"instance_id": iid, "removed_count": None, "dry_run": True})
            continue
        result = forget_instance(iid)
        pruned.append(
            {
                "instance_id": iid,
                "removed_count": len(result.removed),
                "skipped_count": len(result.skipped),
            }
        )

    payload = {"ok": True, "pruned": pruned, "dry_run": dry_run, "count": len(pruned)}
    if dry_run:
        names = ", ".join(d["instance_id"] for d in pruned)
        summary = f"[DRY RUN] would prune {len(pruned)} dead instances: {names}"
    else:
        total_files = sum(p.get("removed_count", 0) or 0 for p in pruned)
        summary = f"Pruned {len(pruned)} dead instances ({total_files} state files removed)"
    return _emit(args, payload, summary)


def handle_instance_rebind_command(args) -> int:
    """Re-stamp an instance's captured pid from its live process.

    Refreshes a resumed/manually-restarted instance's record (pid + start time)
    by matching the live claude process on EMPIRICA_INSTANCE_ID — without
    running a transaction, and re-registering rather than deleting.
    """
    instance_id = args.instance_id
    result = rebind_instance(instance_id)
    payload = {
        "ok": result.success,
        "instance_id": result.instance_id,
        "pid": result.pid,
        "detail": result.detail,
    }
    summary = (
        f"Rebound {instance_id}: {result.detail}"
        if result.success
        else f"Rebind failed for {instance_id}: {result.detail}"
    )
    return _emit(args, payload, summary)


_INSTANCE_DISPATCH = {
    "kill": handle_instance_kill_command,
    "forget": handle_instance_forget_command,
    "label": handle_instance_label_command,
    "prune": handle_instance_prune_command,
    "rebind": handle_instance_rebind_command,
}


def handle_instance_group_command(args) -> int:
    action = getattr(args, "instance_action", None)
    if not action:
        sys.stdout.write("usage: empirica instance <kill|forget|label|prune|rebind> <instance_id> [args...]\n")
        return 2
    handler = _INSTANCE_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f"error: unknown instance action: {action}\n")
        return 2
    return handler(args) or 0


# ─── group dispatchers (mapped from cli_core 'sentinel'/'loop' commands) ────

_SENTINEL_DISPATCH = {
    "pause": handle_sentinel_pause_command,
    "resume": handle_sentinel_resume_command,
    "status": handle_sentinel_status_command_cockpit,
}

_LOOP_DISPATCH = {
    "register": handle_loop_register_command,
    "unregister": handle_loop_unregister_command,
    "pause": handle_loop_pause_command,
    "resume": handle_loop_resume_command,
    "set-interval": handle_loop_set_interval_command,
    "heartbeat": handle_loop_heartbeat_command,
    "should-fire": handle_loop_should_fire_command,
    "poke": handle_loop_poke_command,
    "schedule-next": handle_loop_schedule_next_command,
    "fire": handle_loop_fire_command,
    "install-request": handle_loop_install_request_command,
    "list": handle_loop_list_command,
    "status": handle_loop_status_command,
    # systemd-user scheduler (Phase 1a)
    "enable": handle_loop_enable_command,
    "disable": handle_loop_disable_command,
    "systemd-status": handle_loop_systemd_status_command,
    "tick": handle_loop_tick_command,
    "listen": handle_loop_listen_command,
    # Persistent listener service (prop_flrtxxn32japbazq, 2026-05-18)
    "listen-install": handle_loop_listen_install_command,
    "listen-uninstall": handle_loop_listen_uninstall_command,
    "listen-status": handle_loop_listen_status_command,
}


# ─── AI-ergonomic on/arm/off facade (prop_oxrhoehv4) ───────────────────────
# Three new verbs that collapse the multi-step in-session arming protocol
# to single tool calls. The 9 verbs above stay as power-user primitives.


def _resolve_canonical_ai_id(args) -> str | None:
    """Resolve ai_id for the canonical mesh listener (soft — returns None on failure).

    Priority (cwd-anchored, mirrors session-init.py:_resolve_ai_id_for_session):
      1. --ai-id flag (explicit caller intent)
      2. EMPIRICA_AI_ID env var (explicit override — codex/Kimi/ecodex-lab
         pattern that launches with a declared practitioner)
      3. <cwd>/.empirica/project.yaml `ai_id` field (declared practitioner)
      4. basename(cwd) [strict-canonical, empirica- prefix KEPT per 1.11.x]
      5. InstanceResolver.ai_id() — TTY/session-bound fallback (last resort
         because it can return the GLOBAL active-instance pointer, which is
         wrong when the caller is in a different practice's cwd)
      6. None

    Closes ecodex prop_sdjcbttkcneptjatmvsc5tmkbq + parent prop_3pptt:
    a practitioner running in its own practice dir (e.g. cwd=
    ~/empirical-ai/ecodex-lab) was getting the session-bound resolver's
    answer (`ecodex`) instead of `ecodex-lab`. Steps 2–4 close that gap.

    Distinct from `_resolve_listener_ai_id` (used by `loop listen-install`)
    which falls back to instance_id and raises if no instance.
    """
    explicit = getattr(args, "ai_id", None)
    if explicit:
        return explicit

    import os as _os

    env_override = _os.environ.get("EMPIRICA_AI_ID", "").strip()
    if env_override:
        return env_override

    try:
        import yaml as _yaml

        proj_yaml = Path.cwd() / ".empirica" / "project.yaml"
        if proj_yaml.exists():
            cfg = _yaml.safe_load(proj_yaml.read_text()) or {}
            declared = cfg.get("ai_id")
            if declared:
                return str(declared)
    except Exception:
        pass

    try:
        basename = Path.cwd().name
        if basename:
            return basename
    except Exception:
        pass

    try:
        from empirica.utils.session_resolver import InstanceResolver

        ai_id = InstanceResolver.ai_id()
        if ai_id:
            return ai_id
    except Exception:
        pass
    return None


def handle_listener_on_command(args) -> int:
    """`empirica listener on` — arm the canonical mesh listener for ai_id.

    Auto-resolves name (default `<ai_id>-inbox`), topic (queried from
    cortex `/v1/users/me/notification-channels` — falls back to legacy
    `ntfy:orchestration-events?tags=<ai_id>` if cortex is unreachable
    or the endpoint isn't shipped), and instance_id.

    Two arming modes, picked automatically by detecting whether the
    persistent OS service is running for `ai_id`:

    1. **Persistent-service-tail mode** — persistent service IS running.
       Returns a log-tail Monitor command (`tail -F loop_fires.log`
       filtered to this ai_id). The persistent service writes each
       ECO-decided event to the shared fires log; tailing it delivers
       wakes into THIS session without spawning a duplicate ntfy curl
       subscriber (which is what cortex/the docs explicitly warn
       against — duplicate subscribers double-count wake traffic and
       confuse the `GET /v1/listeners` aggregation).

    2. **Standalone mode** — no persistent service. Returns a
       `empirica loop listen` Monitor command (the session itself
       holds the ntfy stream + handles catch-up on reconnect).

    Both paths write `listener_active_*.json` with a placeholder
    monitor_task_id and return `after_arm: empirica listener arm <task_id>`
    so `listener off` can later TaskStop the right Monitor.
    """
    ai_id = _resolve_canonical_ai_id(args)
    if not ai_id:
        return _emit(
            args,
            {
                "ok": False,
                "error": "ai_id unresolved — pass --ai-id or set ai_id in .empirica/project.yaml",
            },
            "error: ai_id unresolved",
        )

    name = getattr(args, "name", None) or f"{ai_id}-inbox"
    topic = getattr(args, "topic", None)
    if not topic:
        from empirica.core.cockpit.notification_channels import (
            resolve_orchestration_events_topic,
        )

        try:
            topic = resolve_orchestration_events_topic(ai_id)
        except RuntimeError as e:
            # Resolver refuses to fall back to the dead bare topic when
            # cortex is unreachable — surface a clean error rather than
            # registering a listener that will 403 on every poll.
            return _emit(
                args,
                {
                    "ok": False,
                    "error": str(e),
                },
                f"error: {e}",
            )

    # Detect persistent service. If present, pick the tail-Monitor mode;
    # otherwise the standalone-Monitor mode further down. Both paths share
    # the same active-state-file write at the end of the function.
    persistent_active = False
    try:
        from empirica.core.loop_scheduler.persistent_listener import is_listener_running

        persistent_active = is_listener_running(ai_id)
    except Exception:
        # is_listener_running is defensive; on any failure fall through
        # to the standalone-Monitor path. The body never blocks.
        persistent_active = False

    # Register the listener in the per-instance registry (idempotent)
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    try:
        entry = registry.register(
            name=name,
            topic=topic,
            description=f"Canonical mesh listener for ai_id={ai_id}",
            on_wake_template="",
        )
    except ValueError as e:
        return _emit(args, {"ok": False, "error": str(e)}, f"error: {e}")

    # The listener_active_*.json placeholder is written AFTER the Monitor
    # description is computed below, so the marker can carry monitor_description
    # — the deterministic teardown handle `off` falls back to when the optional
    # `arm` step (which records monitor_task_id) was skipped.
    active_path = listener_active_path(instance_id, name)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    import time as _time

    # Pick the Monitor command based on detection above.
    #
    # Tail mode: persistent OS service is already holding the ntfy stream
    # and writing each ECO-decided event to ~/.empirica/loop_fires.log.
    # The session-level Monitor is a `tail -F` on that log filtered to
    # this ai_id — delivers wakes in-band without spawning a duplicate
    # ntfy subscriber. The persistent service subscribed with
    # `?tags=<ai_id>` server-side, so its writes to loop_fires.log carry
    # `instance_id: "<ai_id>"` and are already pre-filtered to events
    # relevant to us.
    #
    # Standalone mode: no persistent service. The Monitor runs
    # `empirica loop listen` itself — holds the ntfy stream + handles
    # catch-up on reconnect.
    if persistent_active:
        log_path = Path.home() / ".empirica" / "loop_fires.log"
        # Wake on every proposal_event for this ai_id. The listener writes
        # `"instance_id": "<exact-project-basename>"` (e.g.
        # `empirica-extension`). The trailing `"` anchor keeps the match
        # scoped to the field, no over-matching.
        #
        # Strict-canonical match. The transition-compat `(empirica-)?`
        # form (accepting both stripped + canonical) shipped in 1.11.6
        # and was retired here in 1.11.8 — session-init's
        # _heal_project_yaml_ai_id_at_init (shipped 1.11.7) runs BEFORE
        # the Monitor arms, so any session new enough to reach this code
        # path already saw its project.yaml migrated to canonical form.
        grep_filter = f'"instance_id": "{ai_id}"'
        monitor_cmd = f"tail -F -n 0 {log_path} 2>/dev/null | grep -E --line-buffered '{grep_filter}'"
        description = f"Cortex orchestration log tail for {ai_id} (persistent-service mode)"
        status = "persistent_service_tail_session"
        mode_note = (
            f"persistent service active for ai_id={ai_id} — arming a log-tail Monitor (no duplicate ntfy subscriber)"
        )
    else:
        # Standalone mode: this Monitor IS the listener process — no OS
        # supervisor (systemd/launchd) is relaunching it. The listener itself
        # exits cleanly on a few paths (SIGTERM during reconnect, ListenerUpgraded
        # on pip-version drift, etc.) and its design DOCSTRINGS those exits as
        # supervisor-relaunched. Without a supervisor, those clean exits look
        # like silent death from the Monitor's perspective.
        #
        # Wrap in a while-true loop so the listener auto-relaunches after any
        # exit. `sleep 3` keeps a crash-loop from pinning CPU; the listener
        # itself does ntfy stream reconnect with backoff internally, so this
        # only fires on a process-level exit (signal / drift / crash) — which
        # is the original supervisor case the design assumed. Found by cortex
        # (prop_6kevxb63: SIGTERM during reconnect under Claude Code Monitor,
        # exit-144 wrapper encoding masked the underlying sig 15).
        monitor_cmd = f"while true; do empirica loop listen --instance {ai_id}; sleep 3; done"
        description = f"Cortex orchestration push listener for {ai_id} (supervised)"
        status = "awaiting_arm"
        mode_note = (
            "standalone Monitor — wrapped in supervisor loop "
            "(matches listener's relaunch-on-clean-exit design intent; "
            "no systemd/launchd needed)"
        )

    placeholder = {
        "monitor_task_id": None,  # filled by `empirica listener arm <task_id>`
        "curl_pid": None,
        "armed_at": _time.time(),
        "ai_id": ai_id,
        "name": name,
        "topic": entry.topic,
        "mode": "tail" if persistent_active else "standalone",
        # The exact Monitor description. `off` uses this to recover the
        # TaskStop handle (TaskList → match description → TaskStop) when the
        # optional `arm` step was skipped and monitor_task_id is still null.
        "monitor_description": description,
    }
    active_path.write_text(_json.dumps(placeholder, indent=2), encoding="utf-8")

    payload = {
        "ok": True,
        "ai_id": ai_id,
        "instance_id": instance_id,
        "name": name,
        "topic": entry.topic,
        "state_file": str(active_path),
        "status": status,
        "next_step": {
            "tool": "Monitor",
            "args": {
                "description": description,
                "command": monitor_cmd,
                "persistent": True,
                "timeout_ms": 3600000,
            },
            "after_arm": f"empirica listener arm <monitor_task_id> --name {name}",
        },
    }
    summary = (
        f'Listener "{name}" registered for ai_id={ai_id} (topic={entry.topic}, '
        f'{mode_note}). Arm Monitor with command "{monitor_cmd}" then run '
        f"`empirica listener arm <task_id> --name {name}`."
    )
    return _emit(args, payload, summary)


def handle_listener_arm_command(args) -> int:
    """`empirica listener arm <task_id>` — record the Monitor task_id post-arm.

    Replaces the `monitor_task_id: null` placeholder in
    `listener_active_<instance>_<name>.json` with the real Monitor
    task id returned by Claude Code's Monitor tool. After this, `off`
    knows what to TaskStop.
    """
    task_id = getattr(args, "task_id", None)
    if not task_id:
        return _emit(
            args,
            {
                "ok": False,
                "error": "task_id required (positional)",
            },
            "error: task_id required",
        )

    instance_id = _require_instance_id(args)
    ai_id = _resolve_canonical_ai_id(args)
    name = getattr(args, "name", None) or (f"{ai_id}-inbox" if ai_id else None)
    if not name:
        return _emit(
            args,
            {
                "ok": False,
                "error": "name unresolved — pass --name or --ai-id",
            },
            "error: name unresolved",
        )

    active_path = listener_active_path(instance_id, name)
    if not active_path.exists():
        return _emit(
            args,
            {
                "ok": False,
                "error": (f"no active state file at {active_path} — run `empirica listener on` first"),
            },
            "error: no active state file (run `empirica listener on` first)",
        )

    try:
        with open(active_path, encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        return _emit(
            args,
            {
                "ok": False,
                "error": f"state file unreadable: {e}",
            },
            f"error: state file unreadable: {e}",
        )

    data["monitor_task_id"] = task_id
    import time as _time

    data["armed_at"] = _time.time()
    active_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": name,
        "monitor_task_id": task_id,
        "state_file": str(active_path),
    }
    summary = f'Listener "{name}" armed: monitor_task_id={task_id}'
    return _emit(args, payload, summary)


def handle_listener_off_command(args) -> int:
    """`empirica listener off` — tear down the canonical mesh listener.

    Reads `listener_active_<instance>_<name>.json` for the Monitor
    task_id, then emits structured JSON instructing Claude to
    `TaskStop(task_id)` followed by `empirica listener unregister <name>`.

    The 2-step protocol mirrors `on`'s structured next_step shape so
    the AI can chain mechanical actions without bespoke knowledge.

    Teardown does NOT rely on the arming session being alive: any
    orphaned listener processes for this ai_id (parent session dead,
    reparented to PID 1) are reaped directly (TERM → KILL), and the
    state file is deleted regardless of kill outcome. The TaskStop
    next_step still covers the live-session Monitor, which only the
    harness can stop; `unregister` clears the registry entry and is
    tolerant of the already-deleted state file.
    """
    from empirica.core.cockpit.listener_processes import (
        reap_processes,
        walk_orphan_listener_processes,
    )

    instance_id = _require_instance_id(args)
    ai_id = _resolve_canonical_ai_id(args)
    name = getattr(args, "name", None) or (f"{ai_id}-inbox" if ai_id else None)
    if not name:
        return _emit(
            args,
            {
                "ok": False,
                "error": "name unresolved — pass --name or --ai-id",
            },
            "error: name unresolved",
        )

    reaped = reap_processes(
        walk_orphan_listener_processes(ai_id) if ai_id else [],
        apply=True,
    )

    active_path = listener_active_path(instance_id, name)
    if not active_path.exists():
        # Already off (or never armed). Emit unregister-only next_step
        # so the caller can still clean up the registry entry if one exists.
        payload = {
            "ok": True,
            "instance_id": instance_id,
            "name": name,
            "status": "not_armed",
            "reaped_orphans": reaped,
            "next_step": {
                "tool": None,
                "after_stop": f"empirica listener unregister {name}",
            },
        }
        summary = (
            f'Listener "{name}" not armed (no state file). '
            f"Run `empirica listener unregister {name}` to clear the registry entry."
        )
        return _emit(args, payload, summary)

    try:
        with open(active_path, encoding="utf-8") as f:
            data = _json.load(f)
        monitor_task_id = data.get("monitor_task_id")
        monitor_description = data.get("monitor_description")
    except (OSError, _json.JSONDecodeError) as e:
        return _emit(
            args,
            {
                "ok": False,
                "error": f"state file unreadable: {e}",
            },
            f"error: state file unreadable: {e}",
        )

    # Delete the state file now — teardown must not depend on the caller
    # following through with unregister (whose unlink tolerates absence).
    try:
        active_path.unlink()
        state_file_removed = True
    except OSError:
        state_file_removed = False

    # Teardown handle resolution, in order of precision:
    #   1. monitor_task_id (recorded by `arm`)        → TaskStop directly
    #   2. monitor_description (recorded by `on`)      → TaskList → match → TaskStop
    #   3. neither (pre-fix marker / standalone)       → unregister-only
    # Case 2 closes the silent gap: a live in-session Monitor whose `arm`
    # step was skipped is neither TaskStop-able by id nor reap-able (it is
    # not a PID-1 orphan while its session lives), so without the description
    # fallback `off` would report "never armed" while the Monitor kept running.
    if monitor_task_id:
        next_step = {
            "tool": "TaskStop",
            "args": {"task_id": monitor_task_id},
            "after_stop": f"empirica listener unregister {name}",
        }
    elif monitor_description:
        next_step = {
            "tool": "TaskList",
            "match_description": monitor_description,
            "then": f"TaskStop the matching Monitor task, then run `empirica listener unregister {name}`",
            "after_stop": f"empirica listener unregister {name}",
        }
    else:
        next_step = {
            "tool": None,
            "after_stop": f"empirica listener unregister {name}",
        }

    payload = {
        "ok": True,
        "instance_id": instance_id,
        "name": name,
        "monitor_task_id": monitor_task_id,
        "monitor_description": monitor_description,
        "state_file": str(active_path),
        "state_file_removed": state_file_removed,
        "reaped_orphans": reaped,
        "next_step": next_step,
    }
    reap_note = f" Reaped {len(reaped)} orphan process(es)." if reaped else ""
    if monitor_task_id:
        summary = (
            f'Listener "{name}" — TaskStop({monitor_task_id}), '
            f"then run `empirica listener unregister {name}`.{reap_note}"
        )
    elif monitor_description:
        summary = (
            f'Listener "{name}" was armed but its monitor_task_id was never '
            f"recorded (`arm` step skipped). Run TaskList, TaskStop the Monitor "
            f'matching description "{monitor_description}", then '
            f"`empirica listener unregister {name}`.{reap_note}"
        )
    else:
        summary = (
            f'Listener "{name}" has no recorded Monitor handle (pre-fix marker). '
            f"Run `empirica listener unregister {name}` to clean up; if a tail "
            f"Monitor is still live, TaskList + TaskStop it manually.{reap_note}"
        )
    return _emit(args, payload, summary)


def _gc_installed_service_ai_ids(home: Path) -> set[str]:
    """Inventory of empirica-listener-* services on disk (systemd-user + launchd).

    Used by the no_service_or_health prune criterion — read once rather than
    per-file so a fleet GC stays fast.
    """
    ids: set[str] = set()
    systemd_dir = home / ".config" / "systemd" / "user"
    launchd_dir = home / "Library" / "LaunchAgents"
    try:
        for unit in systemd_dir.glob("empirica-listener-*.service"):
            ids.add(unit.stem.removeprefix("empirica-listener-"))
    except OSError:
        pass
    try:
        for plist in launchd_dir.glob("com.empirica.listener.*.plist"):
            ids.add(plist.stem.removeprefix("com.empirica.listener."))
    except OSError:
        pass
    return ids


def _gc_legacy_topic_reason(topic: str) -> str | None:
    """Classify a listener_active topic against the retired-topic patterns.

    Returns a human-readable reason string for prune, or None if the topic
    matches a current per-tenant shape.
    """
    if not topic:
        return None
    stripped = topic.replace("ntfy:", "", 1).split("?", 1)[0]
    if stripped == "orchestration-events":
        return "legacy_topic: bare orchestration-events (retired)"
    # Per-org pattern like `empirica-orchestration-events` has 2 dashes;
    # per-tenant `empirica-orchestration-events-david` has 3+. Below 3
    # dashes → pre-T16/T17 per-org form.
    if "orchestration-events" in stripped and stripped.count("-") < 3:
        return f"legacy_topic: pre-tenant per-org form {stripped!r}"
    return None


def _gc_no_service_or_health_reason(
    ai_id: str,
    empirica_dir: Path,
    installed_ids: set[str],
    now: float,
) -> str | None:
    """Return prune reason if neither a persistent service nor a fresh
    health marker is present for this ai_id."""
    if not ai_id or ai_id in installed_ids:
        return None
    health_file = empirica_dir / f"listener_health_{ai_id}.json"
    if health_file.exists():
        try:
            if now - health_file.stat().st_mtime <= 300:  # 5 min
                return None
        except OSError:
            pass
    return f"no_service_or_health: no empirica-listener-{ai_id}.service and no recent health marker"


def _gc_stale_reason(
    armed_at: float,
    last_wake_at: float,
    now: float,
    age_threshold_sec: float,
    age_days: int,
) -> str | None:
    """Return prune reason if the active file's arm/wake history is older
    than the age threshold."""
    armed_age_sec = (now - armed_at) if armed_at else float("inf")
    if armed_age_sec <= age_threshold_sec:
        return None
    if last_wake_at != 0 and (now - last_wake_at) <= age_threshold_sec:
        return None
    return f"stale: armed {int(armed_age_sec // 86400)}d ago, no recent wake activity (threshold {age_days}d)"


def _gc_evaluate_file(
    active_file: Path,
    empirica_dir: Path,
    installed_ids: set[str],
    now: float,
    age_threshold_sec: float,
    age_days: int,
    apply: bool,
) -> tuple[dict, bool]:
    """Evaluate one listener_active file against the three criteria.

    Returns (entry, was_pruned). `entry` is the JSON-shape dict for the
    payload; `was_pruned` distinguishes pruned vs kept buckets.
    """
    try:
        data = _json.loads(active_file.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as e:
        # Unreadable → always-safe prune candidate.
        entry: dict = {
            "file": str(active_file),
            "reasons": [f"unreadable: {e}"],
            "removed": False,
        }
        if apply:
            try:
                active_file.unlink()
                entry["removed"] = True
            except OSError as rm_err:
                entry["error"] = str(rm_err)
        return entry, True

    ai_id = data.get("ai_id") or ""
    topic = data.get("topic") or ""
    armed_at = float(data.get("armed_at") or 0)
    last_wake_at = float(data.get("last_wake_at") or 0)

    reasons: list[str] = []
    for reason in (
        _gc_legacy_topic_reason(topic),
        _gc_no_service_or_health_reason(ai_id, empirica_dir, installed_ids, now),
        _gc_stale_reason(armed_at, last_wake_at, now, age_threshold_sec, age_days),
    ):
        if reason:
            reasons.append(reason)

    if not reasons:
        return {
            "file": str(active_file),
            "ai_id": ai_id,
            "topic": topic,
            "armed_at": armed_at,
        }, False

    entry = {
        "file": str(active_file),
        "ai_id": ai_id,
        "topic": topic,
        "armed_at": armed_at,
        "reasons": reasons,
        "removed": False,
    }
    if apply:
        try:
            active_file.unlink()
            entry["removed"] = True
        except OSError as e:
            entry["error"] = str(e)
    return entry, True


def _gc_render_summary(
    pruned: list[dict], kept: list[dict], apply: bool, age_days: int, orphan_procs: list[dict] | None = None
) -> str:
    """Build the human-readable summary string for GC results."""
    orphan_procs = orphan_procs or []
    lines = [
        f"listener gc — {'APPLIED' if apply else 'DRY RUN'} (age threshold: {age_days}d)",
        f"  Pruned: {len(pruned)}",
        f"  Kept:   {len(kept)}",
        f"  Orphan processes: {len(orphan_procs)}",
    ]
    if pruned:
        lines.append("")
        for entry in pruned[:20]:
            tag = (
                "✓ removed"
                if entry.get("removed")
                else ("(would remove)" if not apply else f"! error: {entry.get('error')}")
            )
            reasons = "; ".join(entry.get("reasons", []))
            lines.append(f"  - {Path(entry['file']).name}  {tag}\n      reasons: {reasons}")
        if len(pruned) > 20:
            lines.append(f"  … and {len(pruned) - 20} more")
    if orphan_procs:
        lines.append("")
        for proc in orphan_procs[:20]:
            tag = (
                "✓ reaped"
                if proc.get("removed")
                else ("(would reap)" if not apply else f"! error: {proc.get('error')}")
            )
            lines.append(f"  - pid {proc['pid']} [{proc['kind']}]  {tag}\n      cmd: {proc['cmdline'][:100]}")
        if len(orphan_procs) > 20:
            lines.append(f"  … and {len(orphan_procs) - 20} more")
    if not apply and (pruned or orphan_procs):
        lines.append("")
        lines.append("  Run with --apply to actually remove.")
    return "\n".join(lines)


def handle_listener_gc_command(args) -> int:
    """Garbage-collect stale ~/.empirica/listener_active_*.json files.

    Three prune criteria, OR'd:

      1. **legacy_topic** — file's `topic` field references the retired
         bare `orchestration-events` (no `<org>-` prefix) or a per-org
         pre-T16/T17 topic (no `-<tenant>-` segment); cortex stopped
         emitting to those topics, so any listener still pinned to them
         receives zero traffic.

      2. **no_service_or_health** — the corresponding persistent
         `empirica-listener-<ai_id>.service` (or launchd plist) does
         NOT exist on disk AND no recent (< 5 min) `listener_health_<ai_id>.json`
         marker is on disk. Together these mean nothing is keeping the
         active file relevant.

      3. **stale** — file's `armed_at` is older than `--age-days N`
         (default 7) AND no `last_wake_at` OR `last_wake_at` is also
         older than that threshold.

    Monitor task id liveness is **not** checked — Monitor ids are
    Claude-session-scoped, so cross-session liveness can't be
    determined from a CLI invocation. The age / topic / service
    checks cover the practical fleet-wide cleanup case.

    Beyond the state files, a PROCESS pass walks `ps` for orphaned
    listener subprocesses — `empirica loop listen` workers (and their
    supervisor shells) plus `tail -F loop_fires.log` session bridges
    whose parent Claude Code session died (reparented to PID 1).
    These outlive their session, accumulate across restarts, and were
    previously only killable via manual pkill. systemd-user service
    children are never flagged (their parent is the user manager, not
    PID 1). Container PID-1 entrypoints legitimately parent listeners
    — the dry-run default keeps that environment report-only.

    Dry-run by default. Pass `--apply` to actually remove files AND
    reap orphan processes (TERM, KILL after 3s). Per-item decision
    rationale is included in both the JSON payload and the human
    render so audits can see why each was flagged.

    Closes goal d75f2b7c (extension's listener_active GC ask); process
    reaping per cortex's orphan-accumulation report.
    """
    import time as _time

    from empirica.core.cockpit.listener_processes import (
        reap_processes,
        walk_orphan_listener_processes,
    )

    apply = bool(getattr(args, "apply", False))
    age_days = int(getattr(args, "age_days", 7))

    home = Path.home()
    empirica_dir = home / ".empirica"
    if not empirica_dir.is_dir():
        return _emit(
            args,
            {"ok": True, "dry_run": not apply, "pruned": [], "kept": []},
            "No ~/.empirica/ directory — nothing to GC.",
        )

    age_threshold_sec = age_days * 24 * 60 * 60
    now = _time.time()
    installed_ids = _gc_installed_service_ai_ids(home)

    pruned: list[dict] = []
    kept: list[dict] = []
    for active_file in sorted(empirica_dir.glob("listener_active_*.json")):
        entry, was_pruned = _gc_evaluate_file(
            active_file,
            empirica_dir,
            installed_ids,
            now,
            age_threshold_sec,
            age_days,
            apply,
        )
        (pruned if was_pruned else kept).append(entry)

    orphan_procs = reap_processes(walk_orphan_listener_processes(), apply)

    payload = {
        "ok": True,
        "dry_run": not apply,
        "age_days": age_days,
        "pruned_count": len(pruned),
        "kept_count": len(kept),
        "pruned": pruned,
        "kept": kept,
        "orphan_process_count": len(orphan_procs),
        "orphan_processes": orphan_procs,
    }
    return _emit(
        args,
        payload,
        _gc_render_summary(
            pruned,
            kept,
            apply,
            age_days,
            orphan_procs,
        ),
    )


_LISTENER_DISPATCH = {
    "register": handle_listener_register_command,
    "unregister": handle_listener_unregister_command,
    "pause": handle_listener_pause_command,
    "resume": handle_listener_resume_command,
    "record-wake": handle_listener_record_wake_command,
    "fire": handle_listener_fire_command,
    "install-request": handle_listener_install_request_command,
    "list": handle_listener_list_command,
    "status": handle_listener_status_command,
    # AI-ergonomic facade (prop_oxrhoehv4)
    "on": handle_listener_on_command,
    "arm": handle_listener_arm_command,
    "off": handle_listener_off_command,
    "gc": handle_listener_gc_command,
}


def handle_sentinel_group_command(args) -> int:
    action = getattr(args, "sentinel_action", None)
    if not action:
        sys.stdout.write("usage: empirica sentinel <pause|resume|status> [--instance ID] [--reason TEXT]\n")
        return 2
    handler = _SENTINEL_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f"error: unknown sentinel action: {action}\n")
        return 2
    return handler(args) or 0


def handle_loop_group_command(args) -> int:
    action = getattr(args, "loop_action", None)
    if not action:
        sys.stdout.write(
            "usage: empirica loop <register|unregister|pause|resume|set-interval|heartbeat|list|status> [args...]\n"
        )
        return 2
    handler = _LOOP_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f"error: unknown loop action: {action}\n")
        return 2
    try:
        return handler(args) or 0
    except InstanceIdRequiredError as e:
        sys.stdout.write(f"{e}\n")
        return 2


def handle_listener_group_command(args) -> int:
    action = getattr(args, "listener_action", None)
    if not action:
        sys.stdout.write(
            "usage: empirica listener <on|arm|off|register|unregister|pause|resume|"
            "record-wake|fire|install-request|list|status> [args...]\n"
        )
        return 2
    handler = _LISTENER_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f"error: unknown listener action: {action}\n")
        return 2
    try:
        return handler(args) or 0
    except InstanceIdRequiredError as e:
        sys.stdout.write(f"{e}\n")
        return 2


# Keep loaders happy — these names are the canonical export surface.
__all__ = [
    "VALID_KIND",
    "VALID_STATUS",
    "handle_instance_forget_command",
    "handle_instance_group_command",
    "handle_instance_kill_command",
    "handle_instance_label_command",
    "handle_instance_rebind_command",
    "handle_listener_arm_command",
    "handle_listener_fire_command",
    "handle_listener_group_command",
    "handle_listener_install_request_command",
    "handle_listener_list_command",
    "handle_listener_off_command",
    "handle_listener_on_command",
    "handle_listener_pause_command",
    "handle_listener_record_wake_command",
    "handle_listener_register_command",
    "handle_listener_resume_command",
    "handle_listener_status_command",
    "handle_listener_unregister_command",
    "handle_loop_disable_command",
    "handle_loop_enable_command",
    "handle_loop_group_command",
    "handle_loop_heartbeat_command",
    "handle_loop_list_command",
    "handle_loop_listen_command",
    "handle_loop_pause_command",
    "handle_loop_poke_command",
    "handle_loop_register_command",
    "handle_loop_resume_command",
    "handle_loop_set_interval_command",
    "handle_loop_should_fire_command",
    "handle_loop_status_command",
    "handle_loop_systemd_status_command",
    "handle_loop_tick_command",
    "handle_loop_unregister_command",
    "handle_sentinel_group_command",
    "handle_sentinel_pause_command",
    "handle_sentinel_resume_command",
    "handle_sentinel_status_command_cockpit",
    "handle_status_command",
    "handle_tui_command",
]
