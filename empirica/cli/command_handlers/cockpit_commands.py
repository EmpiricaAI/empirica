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
import sys
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
    instance = getattr(args, 'instance', None)
    if instance:
        return instance
    if fallback_to_current:
        return get_instance_id()
    return None


def _emit(args, payload: dict[str, Any], human_summary: str) -> int:
    """Emit JSON or human output based on --output."""
    fmt = getattr(args, 'output', 'human')
    if fmt == 'json':
        sys.stdout.write(_json.dumps(payload, indent=2, sort_keys=False) + '\n')
    else:
        sys.stdout.write(human_summary + '\n')
    return 0 if payload.get('ok', True) else 1


# ─── empirica sentinel ──────────────────────────────────────────────────────

def handle_sentinel_pause_command(args) -> int:
    instance_id = _resolve_instance_id(args, fallback_to_current=True)
    reason = getattr(args, 'reason', None)
    status = pause_sentinel(instance_id, reason=reason)
    payload = {
        'ok': True,
        'paused': status.paused,
        'instance_id': status.instance_id,
        'scope': status.scope,
        'since': status.since,
        'reason': status.reason,
    }
    target = status.instance_id or 'global'
    summary = f'Sentinel paused for {target}'
    if status.reason:
        summary += f' (reason: {status.reason})'
    return _emit(args, payload, summary)


def handle_sentinel_resume_command(args) -> int:
    instance_id = _resolve_instance_id(args, fallback_to_current=True)
    status = resume_sentinel(instance_id)
    payload = {
        'ok': True,
        'paused': status.paused,
        'instance_id': status.instance_id,
        'scope': status.scope,
    }
    target = instance_id or 'global'
    if status.paused:
        # The instance pause was removed but a global pause still applies.
        summary = (
            f'Sentinel resume requested for {target}, '
            f'but global pause is still in effect (scope={status.scope})'
        )
    else:
        summary = f'Sentinel resumed for {target}'
    return _emit(args, payload, summary)


def handle_sentinel_status_command_cockpit(args) -> int:
    """`empirica sentinel status` — distinct from existing `sentinel-status`."""
    instance_id = _resolve_instance_id(args, fallback_to_current=True)
    status = sentinel_status(instance_id)
    payload = {
        'ok': True,
        'paused': status.paused,
        'instance_id': status.instance_id,
        'scope': status.scope,
        'since': status.since,
        'reason': status.reason,
    }
    target = status.instance_id or 'global'
    if status.paused:
        summary = f'Sentinel PAUSED for {target} (scope={status.scope})'
        if status.since:
            summary += f' since {status.since}'
        if status.reason:
            summary += f' — {status.reason}'
    else:
        summary = f'Sentinel ON for {target}'
    return _emit(args, payload, summary)


# ─── empirica loop ──────────────────────────────────────────────────────────


class InstanceIdRequiredError(ValueError):
    """Raised when an `empirica loop`/`listener` verb requires an instance_id
    but none was resolvable (no --instance flag, no current-process detection).

    Inherits from ValueError (not BaseException like SystemExit) so callers
    using `except Exception` — including the TUI and background loops —
    catch it cleanly without crashing the host process. Same hazard pattern
    that motivated the resolve_project_id → ProjectNotFoundError migration
    (1.8.18).
    """


def _require_instance_id(args) -> str:
    instance_id = _resolve_instance_id(args, fallback_to_current=True)
    if not instance_id:
        raise InstanceIdRequiredError(
            'error: no instance_id available. Set EMPIRICA_INSTANCE_ID '
            'or pass --instance ID explicitly.'
        )
    return instance_id


def handle_loop_register_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    try:
        entry = registry.register(
            name=args.name,
            kind=args.kind,
            cron=getattr(args, 'cron', None),
            interval=getattr(args, 'interval', None),
            description=getattr(args, 'description', '') or '',
            backoff_policy=getattr(args, 'backoff', None),
            base_interval=getattr(args, 'base_interval', None),
            max_interval=getattr(args, 'max_interval', None),
        )
    except ValueError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
    }
    summary = f'Loop registered: {entry.name} ({entry.kind})'
    if entry.backoff.policy == 'exponential':
        from empirica.core.cockpit.loop_registry import format_duration
        summary += (
            f' [backoff exponential, base={format_duration(entry.backoff.base_interval_seconds)}, '
            f'max={format_duration(entry.backoff.max_interval_seconds)}]'
        )
    return _emit(args, payload, summary)


def handle_loop_unregister_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    removed = registry.unregister(args.name)
    payload = {'ok': True, 'instance_id': instance_id, 'removed': removed, 'name': args.name}
    summary = (
        f'Loop unregistered: {args.name}'
        if removed
        else f'Loop {args.name} was not registered (no-op)'
    )
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
                status=entry.last_status or 'ok',
                result=entry.last_result,
                message=entry.last_message,
                next_scheduled_job_id='',
            )
            # CronCreate-mode: surface a pending uninstall request so the
            # owning Claude instance picks it up via UserPromptSubmit hook
            # and calls CronDelete from inside that CC session. The empirica
            # CLI can't call CronDelete itself.
            if scheduler_kind == 'cron-create':
                pending = write_uninstall_pending(
                    instance_id=instance_id,
                    name=args.name,
                    job_id=cancelled_job_id,
                    scheduler_kind=scheduler_kind,
                    requested_by=get_instance_id(),
                    reason='manual pause',
                )
                uninstall_pending_path = str(pending)

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        'paused': paused,
        'cancelled_job_id': cancelled_job_id,
        'scheduler_kind': scheduler_kind,
        'uninstall_pending_path': uninstall_pending_path,
    }
    summary = f'Loop paused: {args.name}'
    if cancelled_job_id:
        summary += f' · cleared next_job={cancelled_job_id}'
        if scheduler_kind == 'cron-create':
            if uninstall_pending_path:
                summary += (
                    ' · queued CronDelete request for owning instance '
                    '(picked up via UserPromptSubmit; body pause-check is the backstop)'
                )
            else:
                summary += (
                    ' (CronCreate: body pause-check is the backstop; '
                    'next fire will exit silently)'
                )
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
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        'paused': paused,
        'scheduler_kind': scheduler_kind,
    }
    summary = f'Loop resumed: {args.name}'
    if scheduler_kind == 'cron-create':
        summary += (
            f' · re-issue via /loop or run `empirica loop fire {args.name}`'
        )
    return _emit(args, payload, summary)


def handle_loop_set_interval_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    try:
        entry = registry.set_interval(args.name, args.interval)
    except (KeyError, ValueError) as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
    }
    return _emit(args, payload, f'Loop interval set: {args.name} → {args.interval}')


def handle_loop_heartbeat_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    try:
        entry = registry.heartbeat(
            name=args.name,
            status=args.status,
            result=getattr(args, 'result', None),
            message=getattr(args, 'message', None),
            next_scheduled_job_id=getattr(args, 'next_scheduled_job_id', None),
            scheduler_kind=getattr(args, 'scheduler_kind', None),
        )
    except ValueError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
        'paused': is_loop_paused(instance_id, entry.name),
    }
    summary = f'Loop heartbeat: {entry.name} → {entry.last_status}/{entry.last_result}'
    if entry.last_message:
        summary += f' ({entry.last_message})'
    if entry.backoff.policy == 'exponential':
        from empirica.core.cockpit.loop_registry import format_duration
        summary += f' · streak={entry.backoff.empty_streak} next≥{format_duration(entry.backoff.current_interval_seconds())}'
    if entry.scheduling.next_scheduled_job_id:
        summary += f' · next_job={entry.scheduling.next_scheduled_job_id}'
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
            {'ok': False, 'error': f'loop {args.name!r} not registered'},
            f'error: loop {args.name!r} not registered',
        )
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        **plan.to_dict(),
    }
    summary = (
        f'next fire: {plan.fire_at.isoformat()} '
        f'({plan.cron_one_shot}) — {plan.reason}'
    )
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
    target_instance = getattr(args, 'instance', None)
    if not target_instance:
        return _emit(
            args,
            {'ok': False, 'error': '--instance required (target instance to install in)'},
            'error: --instance required',
        )

    name = args.name
    interval = args.interval
    description = getattr(args, 'description', '') or ''
    # Fallback chain: explicit --base-interval > --interval > '15m' default.
    # Same fallback applies to interval itself when absent: project.yaml
    # entries with `kind: cron` + `cron: "..."` legitimately omit interval
    # (the cron expression is the schedule), but the loop-cron prompt
    # template substitutes interval into backoff config — a None there
    # writes the literal string 'None' into the prompt and produces a
    # malformed `--interval "None"` flag in the body's register call.
    base_interval = getattr(args, 'base_interval', None) or interval or '15m'
    max_interval = getattr(args, 'max_interval', None) or '4h'
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
            kind='cron',
            interval=interval,
            description=description,
            backoff_policy='exponential',
            base_interval=base_interval,
            max_interval=max_interval,
        )
    except ValueError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    # Stamp scheduler_kind so heartbeat fields don't drift later.
    registry.heartbeat(
        name=name,
        status=entry.last_status or 'ok',
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
    )

    payload = {
        'ok': True,
        'instance_id': target_instance,
        'name': name,
        'interval': interval,
        'pending_request_path': str(pending),
        'requested_by': requested_by,
        'scheduler_kind': DEFAULT_SCHEDULER_KIND,
        'next_step': (
            f'Target Claude in {target_instance} will see the install request '
            f'on its next prompt and run /loop to call CronCreate'
        ),
    }
    summary = (
        f'Install request queued for {name} in {target_instance} '
        f'({interval}) — surfaces on next UserPromptSubmit'
    )
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
            {'ok': False, 'error': f'loop {args.name!r} not registered'},
            f'error: loop {args.name!r} not registered',
        )
    plan = registry.schedule_next(args.name)
    payload: dict[str, Any] = {
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        'scheduler_kind': entry.scheduling.scheduler_kind,
        'paused': is_loop_paused(instance_id, args.name),
    }
    if plan is not None:
        payload.update(plan.to_dict())
    scheduler_kind = entry.scheduling.scheduler_kind or 'unknown'
    if scheduler_kind == 'cron-create':
        payload['hint'] = (
            f"empirica CLI can't call CronCreate directly. Re-issue via "
            f"/loop or run: CronCreate(cron='{plan.cron_one_shot}', "
            f"recurring=false, prompt='<loop body template>')"
            if plan
            else 'no schedule plan — register loop first'
        )
        summary = (
            f'fire requested for {args.name} — install '
            f"`{plan.cron_one_shot}`" if plan else f'fire requested for {args.name}'
        )
    else:
        summary = (
            f'fire requested for {args.name} ({scheduler_kind}) — '
            f"`{plan.cron_one_shot}` at {plan.fire_at.isoformat()}"
            if plan
            else f'fire requested for {args.name} ({scheduler_kind})'
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
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        'should_fire': should,
        'reason': reason,
    }
    summary = f'{"FIRE" if should else "SKIP"} ({reason})'
    _emit(args, payload, summary)
    return 0 if should else 1


def handle_loop_poke_command(args) -> int:
    """Manual escape hatch — zero the streak, clear the next_fire_threshold."""
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    entry = registry.poke(args.name)
    if entry is None:
        payload = {'ok': False, 'error': f'loop not registered: {args.name}',
                   'instance_id': instance_id, 'name': args.name}
        return _emit(args, payload, payload['error'])
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
    }
    return _emit(args, payload, f'Loop poked: {args.name} (streak cleared, next fire allowed)')


def handle_loop_list_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    loops = registry.list_loops()

    payload_loops = []
    for entry in loops:
        d = entry.to_dict()
        d['name'] = entry.name
        d['paused'] = is_loop_paused(instance_id, entry.name)
        payload_loops.append(d)

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'count': len(loops),
        'loops': payload_loops,
    }

    if not loops:
        summary = f'No loops registered for {instance_id}'
    else:
        rows = [f'  {l["name"]:<20} {l["kind"]:<8} paused={l["paused"]}' for l in payload_loops]
        summary = f'Loops registered for {instance_id}:\n' + '\n'.join(rows)

    return _emit(args, payload, summary)


def handle_loop_status_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = LoopRegistry(instance_id)
    entry = registry.get(args.name)
    if entry is None:
        payload = {'ok': False, 'error': f'loop not registered: {args.name}',
                   'instance_id': instance_id, 'name': args.name, 'paused': False}
        return _emit(args, payload, payload['error'])

    paused = is_loop_paused(instance_id, args.name)
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
        'paused': paused,
    }
    summary = (
        f'{entry.name}: kind={entry.kind} paused={paused} '
        f'last_run={entry.last_run} last_status={entry.last_status}'
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
            description=getattr(args, 'description', '') or '',
            on_wake_template=getattr(args, 'on_wake', '') or '',
        )
    except ValueError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'listener': {'name': entry.name, **entry.to_dict()},
    }
    summary = f'Listener registered: {entry.name} (topic={entry.topic})'
    return _emit(args, payload, summary)


def handle_listener_unregister_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    removed = registry.unregister(args.name)
    payload = {
        'ok': True, 'instance_id': instance_id,
        'removed': removed, 'name': args.name,
    }
    summary = (
        f'Listener unregistered: {args.name}'
        if removed
        else f'Listener {args.name} was not registered (no-op)'
    )
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
            with open(active_path, encoding='utf-8') as f:
                active_data = _json.load(f)
            monitor_task_id = active_data.get('monitor_task_id') or None
            raw_pid = active_data.get('curl_pid')
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
            reason='manual pause',
        )
        uninstall_pending_path = str(pending)

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        'paused': paused,
        'monitor_task_id': monitor_task_id,
        'curl_pid': curl_pid,
        'uninstall_pending_path': uninstall_pending_path,
    }

    summary = f'Listener paused: {args.name}'
    if uninstall_pending_path:
        summary += (
            f' · queued TaskStop request for Monitor {monitor_task_id} '
            '(picked up via UserPromptSubmit; body pause-check is the backstop)'
        )
    elif active_path.exists():
        summary += (
            ' (active file present but missing monitor_task_id — '
            'body pause-check is the only backstop)'
        )
    else:
        summary += ' (no active runtime — listener was already disarmed)'

    return _emit(args, payload, summary)


def handle_listener_resume_command(args) -> int:
    instance_id = _require_instance_id(args)
    paused = set_listener_paused(instance_id, args.name, paused=False)
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'name': args.name,
        'paused': paused,
    }
    summary = (
        f'Listener resumed: {args.name} '
        '(re-arm via the inbox-listener skill or run `empirica listener fire`)'
    )
    return _emit(args, payload, summary)


def handle_listener_record_wake_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    try:
        entry = registry.record_wake(
            name=args.name,
            message=getattr(args, 'message', None),
        )
    except KeyError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'listener': {'name': entry.name, **entry.to_dict()},
        'paused': is_listener_paused(instance_id, entry.name),
    }
    summary = (
        f'Listener wake: {entry.name} → count={entry.wake_count} '
        f'last_at={entry.last_wake_at}'
    )
    if entry.last_message:
        summary += f' ({entry.last_message})'
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
            {'ok': False, 'error': f'listener not registered: {args.name}'},
            f'error: listener not registered: {args.name}',
        )
    entry = registry.record_wake(args.name, message='manual fire')
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'listener': {'name': entry.name, **entry.to_dict()},
    }
    summary = (
        f'Listener fired: {entry.name} (V1: counted only — wake injection lands in item 4)'
    )
    return _emit(args, payload, summary)


def handle_listener_list_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    listeners = registry.list_listeners()

    payload_listeners = []
    for entry in listeners:
        d = entry.to_dict()
        d['name'] = entry.name
        d['paused'] = is_listener_paused(instance_id, entry.name)
        payload_listeners.append(d)

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'count': len(listeners),
        'listeners': payload_listeners,
    }

    if not listeners:
        summary = f'No listeners registered for {instance_id}'
    else:
        rows = [
            f'  {item["name"]:<20} {item["topic"]:<35} paused={item["paused"]} '
            f'wakes={item["wake_count"]}'
            for item in payload_listeners
        ]
        summary = f'Listeners registered for {instance_id}:\n' + '\n'.join(rows)

    return _emit(args, payload, summary)


def handle_listener_status_command(args) -> int:
    instance_id = _require_instance_id(args)
    registry = ListenerRegistry(instance_id)
    entry = registry.get(args.name)
    if entry is None:
        payload = {
            'ok': False, 'error': f'listener not registered: {args.name}',
            'instance_id': instance_id, 'name': args.name, 'paused': False,
        }
        return _emit(args, payload, payload['error'])

    paused = is_listener_paused(instance_id, args.name)
    payload = {
        'ok': True,
        'instance_id': instance_id,
        'listener': {'name': entry.name, **entry.to_dict()},
        'paused': paused,
    }
    summary = (
        f'{entry.name}: topic={entry.topic} paused={paused} '
        f'wakes={entry.wake_count} last_wake_at={entry.last_wake_at}'
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
    target_instance = getattr(args, 'instance', None)
    if not target_instance:
        return _emit(
            args,
            {'ok': False, 'error': '--instance required (target instance to install in)'},
            'error: --instance required',
        )

    name = args.name
    topic = args.topic
    description = getattr(args, 'description', '') or ''
    on_wake = getattr(args, 'on_wake', '') or ''

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
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    pending_path_obj = write_listener_install_pending(
        instance_id=target_instance,
        name=name,
        topic=topic,
        description=description,
        on_wake_template=on_wake,
        requested_by=get_instance_id(),
    )

    payload = {
        'ok': True,
        'instance_id': target_instance,
        'listener': {'name': entry.name, **entry.to_dict()},
        'pending_path': str(pending_path_obj),
    }
    summary = (
        f'Listener install requested: {name} (topic={topic}) → {target_instance} '
        '· pending file written; owning Claude will pick it up via UserPromptSubmit'
    )
    return _emit(args, payload, summary)


# ─── empirica status ────────────────────────────────────────────────────────

def handle_tui_command(args) -> int:
    """Launch the Textual cockpit TUI."""
    try:
        from empirica.cli.tui import run_tui
    except ImportError as e:
        sys.stdout.write(
            f'error: TUI requires the textual package — {e}\n'
            'install with: pip install textual\n'
        )
        return 2
    return run_tui(include_dead=bool(getattr(args, 'include_dead', False)))


def handle_status_command(args) -> int:
    """Top-level cockpit overview command.

    Modes:
      --all           every discoverable instance
      --instance ID   single specified instance
      (default)       current instance (auto-detected) or all if no instance
      --json          machine-readable output
      --pretty        ANSI colored output (default for --output human)
    """
    fmt = getattr(args, 'output', None)
    json_mode = getattr(args, 'json', False) or fmt == 'json'
    pretty_mode = getattr(args, 'pretty', False) or (fmt == 'human' and not json_mode)
    if not json_mode and not pretty_mode:
        # Default: pretty when stdout is a TTY, json otherwise (for piping).
        pretty_mode = sys.stdout.isatty()
        json_mode = not pretty_mode

    explicit_instance = getattr(args, 'instance', None)
    show_all = getattr(args, 'all', False)
    include_dead = bool(getattr(args, 'include_dead', False))

    if explicit_instance:
        payload = {
            'generated_at': aggregate_all()['generated_at'],
            'instances': [aggregate_instance_state(explicit_instance)],
            'summary': {
                'instances': 1,
                'loops_registered': 0,
                'loops_paused': 0,
                'active_tx': 0,
                'notify_dispatcher': build_notify_dispatcher_block(),
            },
        }
        # Refresh summary from the single instance.
        loops = payload['instances'][0].get('loops', {})
        payload['summary']['loops_registered'] = len(loops)
        payload['summary']['loops_paused'] = sum(
            1 for v in loops.values() if v.get('paused')
        )
        payload['summary']['active_tx'] = sum(
            1 for inst in payload['instances'] if inst['phase'] in ('noetic', 'praxic')
        )
        all_mode = False
    elif show_all:
        payload = aggregate_all(include_dead=include_dead)
        all_mode = True
    else:
        current = get_instance_id()
        if current:
            payload = {
                'generated_at': aggregate_all(include_dead=True)['generated_at'],
                'instances': [aggregate_instance_state(current)],
                'summary': {
                    'instances': 1,
                    'loops_registered': 0,
                    'loops_paused': 0,
                    'active_tx': 0,
                    'notify_dispatcher': build_notify_dispatcher_block(),
                },
            }
            loops = payload['instances'][0].get('loops', {})
            payload['summary']['loops_registered'] = len(loops)
            payload['summary']['loops_paused'] = sum(
                1 for v in loops.values() if v.get('paused')
            )
            payload['summary']['active_tx'] = sum(
                1 for inst in payload['instances'] if inst['phase'] in ('noetic', 'praxic')
            )
            all_mode = False
        else:
            payload = aggregate_all(include_dead=include_dead)
            all_mode = True

    if json_mode:
        sys.stdout.write(render_json(payload) + '\n')
    else:
        sys.stdout.write(render_pretty(payload, all_instances=all_mode) + '\n')
    return 0


# ─── empirica instance ─────────────────────────────────────────────────────

def handle_instance_kill_command(args) -> int:
    instance_id = args.instance_id
    force = bool(getattr(args, 'force', False))
    yes = bool(getattr(args, 'yes', False))

    # Defensive: don't let a stray command kill the very Claude that runs it.
    current = get_instance_id()
    if instance_id == current and not yes:
        payload = {
            'ok': False,
            'error': 'refusing to kill the current instance — pass --yes to override',
            'instance_id': instance_id,
        }
        return _emit(args, payload, payload['error'])

    result = kill_instance(instance_id, force=force)
    payload = {
        'ok': result.success,
        'instance_id': result.instance_id,
        'method': result.method,
        'pid': result.pid,
        'detail': result.detail,
    }
    summary = (
        f'Killed {instance_id} ({result.method}): {result.detail}'
        if result.success
        else f'Kill failed for {instance_id}: {result.detail}'
    )
    return _emit(args, payload, summary)


def handle_instance_forget_command(args) -> int:
    instance_id = args.instance_id
    yes = bool(getattr(args, 'yes', False))

    current = get_instance_id()
    if instance_id == current and not yes:
        payload = {
            'ok': False,
            'error': 'refusing to forget the current instance — pass --yes to override',
            'instance_id': instance_id,
        }
        return _emit(args, payload, payload['error'])

    result = forget_instance(instance_id)
    payload = {
        'ok': True,
        'instance_id': result.instance_id,
        'removed': result.removed,
        'skipped': result.skipped,
        'count': len(result.removed),
    }
    if not result.removed and not result.skipped:
        summary = f'Nothing to forget for {instance_id} — no state files found'
    else:
        summary = f'Forgot {instance_id}: removed {len(result.removed)} files'
        if result.skipped:
            summary += f' ({len(result.skipped)} skipped)'
    return _emit(args, payload, summary)


def handle_instance_label_command(args) -> int:
    instance_id = args.instance_id
    label = getattr(args, 'label', None)
    clear = bool(getattr(args, 'clear', False))

    if clear:
        set_label(instance_id, None)
        payload = {'ok': True, 'instance_id': instance_id, 'label': None, 'cleared': True}
        return _emit(args, payload, f'Label cleared for {instance_id}')

    if label is None:
        existing = get_label(instance_id)
        payload = {'ok': True, 'instance_id': instance_id, 'label': existing}
        return _emit(args, payload, f'{instance_id}: {existing or "(no manual label)"}')

    new_label = set_label(instance_id, label)
    payload = {'ok': True, 'instance_id': instance_id, 'label': new_label, 'cleared': False}
    return _emit(args, payload, f'Label set for {instance_id}: {new_label}')


def handle_instance_prune_command(args) -> int:
    """Bulk forget every instance that fails the liveness check.

    Skips the current instance (it's running this code, by definition alive).
    With --dry-run, prints what would be removed without removing anything.
    """
    dry_run = bool(getattr(args, 'dry_run', False))
    dead = discover_dead_instances()

    if not dead:
        payload = {'ok': True, 'pruned': [], 'dry_run': dry_run, 'count': 0}
        return _emit(args, payload, 'No dead instances to prune')

    pruned: list[dict[str, Any]] = []
    for iid in dead:
        if dry_run:
            pruned.append({'instance_id': iid, 'removed_count': None, 'dry_run': True})
            continue
        result = forget_instance(iid)
        pruned.append({
            'instance_id': iid,
            'removed_count': len(result.removed),
            'skipped_count': len(result.skipped),
        })

    payload = {'ok': True, 'pruned': pruned, 'dry_run': dry_run, 'count': len(pruned)}
    if dry_run:
        names = ', '.join(d['instance_id'] for d in pruned)
        summary = f'[DRY RUN] would prune {len(pruned)} dead instances: {names}'
    else:
        total_files = sum(p.get('removed_count', 0) or 0 for p in pruned)
        summary = f'Pruned {len(pruned)} dead instances ({total_files} state files removed)'
    return _emit(args, payload, summary)


_INSTANCE_DISPATCH = {
    'kill': handle_instance_kill_command,
    'forget': handle_instance_forget_command,
    'label': handle_instance_label_command,
    'prune': handle_instance_prune_command,
}


def handle_instance_group_command(args) -> int:
    action = getattr(args, 'instance_action', None)
    if not action:
        sys.stdout.write(
            'usage: empirica instance <kill|forget|label> <instance_id> [args...]\n'
        )
        return 2
    handler = _INSTANCE_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f'error: unknown instance action: {action}\n')
        return 2
    return handler(args) or 0


# ─── group dispatchers (mapped from cli_core 'sentinel'/'loop' commands) ────

_SENTINEL_DISPATCH = {
    'pause': handle_sentinel_pause_command,
    'resume': handle_sentinel_resume_command,
    'status': handle_sentinel_status_command_cockpit,
}

_LOOP_DISPATCH = {
    'register': handle_loop_register_command,
    'unregister': handle_loop_unregister_command,
    'pause': handle_loop_pause_command,
    'resume': handle_loop_resume_command,
    'set-interval': handle_loop_set_interval_command,
    'heartbeat': handle_loop_heartbeat_command,
    'should-fire': handle_loop_should_fire_command,
    'poke': handle_loop_poke_command,
    'schedule-next': handle_loop_schedule_next_command,
    'fire': handle_loop_fire_command,
    'install-request': handle_loop_install_request_command,
    'list': handle_loop_list_command,
    'status': handle_loop_status_command,
}


_LISTENER_DISPATCH = {
    'register': handle_listener_register_command,
    'unregister': handle_listener_unregister_command,
    'pause': handle_listener_pause_command,
    'resume': handle_listener_resume_command,
    'record-wake': handle_listener_record_wake_command,
    'fire': handle_listener_fire_command,
    'install-request': handle_listener_install_request_command,
    'list': handle_listener_list_command,
    'status': handle_listener_status_command,
}


def handle_sentinel_group_command(args) -> int:
    action = getattr(args, 'sentinel_action', None)
    if not action:
        sys.stdout.write(
            'usage: empirica sentinel <pause|resume|status> [--instance ID] [--reason TEXT]\n'
        )
        return 2
    handler = _SENTINEL_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f'error: unknown sentinel action: {action}\n')
        return 2
    return handler(args) or 0


def handle_loop_group_command(args) -> int:
    action = getattr(args, 'loop_action', None)
    if not action:
        sys.stdout.write(
            'usage: empirica loop <register|unregister|pause|resume|set-interval|'
            'heartbeat|list|status> [args...]\n'
        )
        return 2
    handler = _LOOP_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f'error: unknown loop action: {action}\n')
        return 2
    try:
        return handler(args) or 0
    except InstanceIdRequiredError as e:
        sys.stdout.write(f'{e}\n')
        return 2


def handle_listener_group_command(args) -> int:
    action = getattr(args, 'listener_action', None)
    if not action:
        sys.stdout.write(
            'usage: empirica listener <register|unregister|pause|resume|'
            'record-wake|fire|install-request|list|status> [args...]\n'
        )
        return 2
    handler = _LISTENER_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f'error: unknown listener action: {action}\n')
        return 2
    try:
        return handler(args) or 0
    except InstanceIdRequiredError as e:
        sys.stdout.write(f'{e}\n')
        return 2


# Keep loaders happy — these names are the canonical export surface.
__all__ = [
    'VALID_KIND',
    'VALID_STATUS',
    'handle_instance_forget_command',
    'handle_instance_group_command',
    'handle_instance_kill_command',
    'handle_instance_label_command',
    'handle_listener_fire_command',
    'handle_listener_group_command',
    'handle_listener_install_request_command',
    'handle_listener_list_command',
    'handle_listener_pause_command',
    'handle_listener_record_wake_command',
    'handle_listener_register_command',
    'handle_listener_resume_command',
    'handle_listener_status_command',
    'handle_listener_unregister_command',
    'handle_loop_group_command',
    'handle_loop_heartbeat_command',
    'handle_loop_list_command',
    'handle_loop_pause_command',
    'handle_loop_poke_command',
    'handle_loop_register_command',
    'handle_loop_resume_command',
    'handle_loop_set_interval_command',
    'handle_loop_should_fire_command',
    'handle_loop_status_command',
    'handle_loop_unregister_command',
    'handle_sentinel_group_command',
    'handle_sentinel_pause_command',
    'handle_sentinel_resume_command',
    'handle_sentinel_status_command_cockpit',
    'handle_status_command',
    'handle_tui_command',
]
