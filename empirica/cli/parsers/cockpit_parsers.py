"""Cockpit parsers — sentinel, loop, status subcommand groups.

Naming choice: the new group commands use a *space* between verb and action
(`empirica sentinel pause`, `empirica loop register`) — not the hyphenated
`sentinel-status` convention used by older CLI surfaces. This is the
proposal's surface, and groups read more naturally for users learning the
cockpit. The legacy `sentinel-status` (no space, single command) coexists
unchanged in sentinel_parsers.py.
"""

from __future__ import annotations

from empirica.core.cockpit.loop_registry import VALID_KIND, VALID_STATUS

# Add an _output flag to every leaf parser using this helper.
_OUTPUT_CHOICES = ('human', 'json')


def _add_output(parser):
    parser.add_argument(
        '--output', choices=_OUTPUT_CHOICES, default='human',
        help='Output format (default: human)'
    )


def _add_instance(parser):
    parser.add_argument(
        '--instance', metavar='ID',
        help='Target instance_id (default: auto-detect from current process)'
    )


def add_cockpit_parsers(subparsers):
    """Register sentinel/loop/instance subcommand groups + top-level status + tui."""
    _add_sentinel_group(subparsers)
    _add_loop_group(subparsers)
    _add_instance_group(subparsers)
    _add_status_command(subparsers)
    _add_tui_command(subparsers)


def _add_tui_command(subparsers):
    subparsers.add_parser(
        'tui',
        help='Launch the interactive cockpit (Textual app — clickable controls)',
    )


def _add_instance_group(subparsers):
    instance_root = subparsers.add_parser(
        'instance',
        help='Instance lifecycle: kill, forget, label (the destructive control plane)',
    )
    instance_subs = instance_root.add_subparsers(
        dest='instance_action', metavar='action'
    )

    kill = instance_subs.add_parser(
        'kill',
        help='Terminate an instance (tmux kill-pane for tmux_*, SIGTERM for others)',
    )
    kill.add_argument('instance_id', help='Target instance_id')
    kill.add_argument('--force', action='store_true',
                      help='Use SIGKILL instead of SIGTERM (non-tmux only)')
    kill.add_argument('--yes', '-y', action='store_true',
                      help='Bypass safety check when targeting current instance')
    _add_output(kill)

    forget = instance_subs.add_parser(
        'forget',
        help='Remove all per-instance state files from ~/.empirica/ (cleanup for dead instances)',
    )
    forget.add_argument('instance_id', help='Target instance_id')
    forget.add_argument('--yes', '-y', action='store_true',
                        help='Bypass safety check when targeting current instance')
    _add_output(forget)

    label = instance_subs.add_parser(
        'label',
        help='Set/show/clear the human-readable label for an instance',
    )
    label.add_argument('instance_id', help='Target instance_id')
    label.add_argument('label', nargs='?',
                       help='New label (omit to show current value)')
    label.add_argument('--clear', action='store_true',
                       help='Clear the manual label (revert to project basename)')
    _add_output(label)


def _add_sentinel_group(subparsers):
    sentinel_root = subparsers.add_parser(
        'sentinel',
        help='Sentinel pause/resume/status (per-instance noetic firewall control)',
    )
    sentinel_subs = sentinel_root.add_subparsers(
        dest='sentinel_action', metavar='action'
    )

    pause = sentinel_subs.add_parser('pause', help='Pause Sentinel for an instance')
    pause.add_argument('--reason', help='Optional human-readable reason for the pause')
    _add_instance(pause)
    _add_output(pause)

    resume = sentinel_subs.add_parser('resume', help='Resume Sentinel for an instance')
    _add_instance(resume)
    _add_output(resume)

    status = sentinel_subs.add_parser('status', help='Show Sentinel pause state')
    _add_instance(status)
    _add_output(status)


def _add_loop_group(subparsers):
    loop_root = subparsers.add_parser(
        'loop',
        help='Loop registry: register, pause, heartbeat per-instance scheduled work',
    )
    loop_subs = loop_root.add_subparsers(dest='loop_action', metavar='action')

    register = loop_subs.add_parser('register', help='Register a loop (idempotent)')
    register.add_argument('--name', required=True, help='Loop name (alphanumeric, dot, dash, underscore)')
    register.add_argument('--kind', required=True, choices=VALID_KIND,
                          help='Loop kind: cron | interval | monitor')
    register.add_argument('--cron', help='Cron expression (for kind=cron)')
    register.add_argument('--interval', help='Interval like "5m", "30s", "2h" (for kind=interval)')
    register.add_argument('--description', help='Optional human-readable description')
    _add_instance(register)
    _add_output(register)

    unregister = loop_subs.add_parser('unregister', help='Remove a loop from the registry')
    unregister.add_argument('name', help='Loop name')
    _add_instance(unregister)
    _add_output(unregister)

    pause = loop_subs.add_parser('pause', help='Pause a loop (writes pause sidecar)')
    pause.add_argument('name', help='Loop name')
    _add_instance(pause)
    _add_output(pause)

    resume = loop_subs.add_parser('resume', help='Resume a loop (removes pause sidecar)')
    resume.add_argument('name', help='Loop name')
    _add_instance(resume)
    _add_output(resume)

    set_iv = loop_subs.add_parser('set-interval', help='Update a registered loop interval')
    set_iv.add_argument('name', help='Loop name')
    set_iv.add_argument('interval', help='New interval (e.g. "5m")')
    _add_instance(set_iv)
    _add_output(set_iv)

    heartbeat = loop_subs.add_parser('heartbeat', help='Record a loop fire (call after each run)')
    heartbeat.add_argument('name', help='Loop name')
    heartbeat.add_argument('--status', choices=VALID_STATUS, default='ok',
                            help='Run status (default: ok)')
    heartbeat.add_argument('--message', help='Optional summary message for this fire')
    _add_instance(heartbeat)
    _add_output(heartbeat)

    list_p = loop_subs.add_parser('list', help='List all loops registered for an instance')
    _add_instance(list_p)
    _add_output(list_p)

    status_p = loop_subs.add_parser('status', help='Show status for a single loop')
    status_p.add_argument('name', help='Loop name')
    _add_instance(status_p)
    _add_output(status_p)


def _add_status_command(subparsers):
    status = subparsers.add_parser(
        'status',
        help='Cockpit overview — per-instance phase, Sentinel, loops, transactions',
    )
    status.add_argument('--all', action='store_true', help='Show every discoverable instance')
    status.add_argument('--instance', metavar='ID',
                         help='Limit to a single instance')
    fmt = status.add_mutually_exclusive_group()
    fmt.add_argument('--pretty', action='store_true',
                     help='ANSI colored layout (default for TTY)')
    fmt.add_argument('--json', action='store_true', dest='json',
                     help='Machine-readable JSON output (default for pipes)')
    status.add_argument('--output', choices=_OUTPUT_CHOICES,
                         help='Explicit output format (overrides --pretty/--json)')
