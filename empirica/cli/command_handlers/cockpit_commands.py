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
from empirica.core.cockpit.loop_registry import VALID_KIND, VALID_STATUS
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

def _require_instance_id(args) -> str:
    instance_id = _resolve_instance_id(args, fallback_to_current=True)
    if not instance_id:
        raise SystemExit(
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
        )
    except ValueError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
    }
    summary = f'Loop registered: {entry.name} ({entry.kind})'
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
    instance_id = _require_instance_id(args)
    paused = set_loop_paused(instance_id, args.name, paused=True)
    payload = {'ok': True, 'instance_id': instance_id, 'name': args.name, 'paused': paused}
    return _emit(args, payload, f'Loop paused: {args.name}')


def handle_loop_resume_command(args) -> int:
    instance_id = _require_instance_id(args)
    paused = set_loop_paused(instance_id, args.name, paused=False)
    payload = {'ok': True, 'instance_id': instance_id, 'name': args.name, 'paused': paused}
    return _emit(args, payload, f'Loop resumed: {args.name}')


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
            message=getattr(args, 'message', None),
        )
    except ValueError as e:
        return _emit(args, {'ok': False, 'error': str(e)}, f'error: {e}')

    payload = {
        'ok': True,
        'instance_id': instance_id,
        'loop': {'name': entry.name, **entry.to_dict()},
        'paused': is_loop_paused(instance_id, entry.name),
    }
    summary = f'Loop heartbeat: {entry.name} → {entry.last_status}'
    if entry.last_message:
        summary += f' ({entry.last_message})'
    return _emit(args, payload, summary)


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


# ─── empirica status ────────────────────────────────────────────────────────

def handle_tui_command(_args) -> int:
    """Launch the Textual cockpit TUI."""
    try:
        from empirica.cli.tui import run_tui
    except ImportError as e:
        sys.stdout.write(
            f'error: TUI requires the textual package — {e}\n'
            'install with: pip install textual\n'
        )
        return 2
    return run_tui()


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

    if explicit_instance:
        payload = {
            'generated_at': aggregate_all()['generated_at'],
            'instances': [aggregate_instance_state(explicit_instance)],
            'summary': {
                'instances': 1,
                'loops_registered': 0,
                'loops_paused': 0,
                'active_tx': 0,
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
        payload = aggregate_all()
        all_mode = True
    else:
        current = get_instance_id()
        if current:
            payload = {
                'generated_at': aggregate_all()['generated_at'],
                'instances': [aggregate_instance_state(current)],
                'summary': {
                    'instances': 1,
                    'loops_registered': 0,
                    'loops_paused': 0,
                    'active_tx': 0,
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
            payload = aggregate_all()
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


_INSTANCE_DISPATCH = {
    'kill': handle_instance_kill_command,
    'forget': handle_instance_forget_command,
    'label': handle_instance_label_command,
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
    'list': handle_loop_list_command,
    'status': handle_loop_status_command,
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
    return handler(args) or 0


# Keep loaders happy — these names are the canonical export surface.
__all__ = [
    'VALID_KIND',
    'VALID_STATUS',
    'handle_instance_forget_command',
    'handle_instance_group_command',
    'handle_instance_kill_command',
    'handle_instance_label_command',
    'handle_loop_group_command',
    'handle_loop_heartbeat_command',
    'handle_loop_list_command',
    'handle_loop_pause_command',
    'handle_loop_register_command',
    'handle_loop_resume_command',
    'handle_loop_set_interval_command',
    'handle_loop_status_command',
    'handle_loop_unregister_command',
    'handle_sentinel_group_command',
    'handle_sentinel_pause_command',
    'handle_sentinel_resume_command',
    'handle_sentinel_status_command_cockpit',
    'handle_status_command',
    'handle_tui_command',
]
