"""Cockpit parsers — sentinel, loop, status subcommand groups.

Naming choice: the new group commands use a *space* between verb and action
(`empirica sentinel pause`, `empirica loop register`) — not the hyphenated
`sentinel-status` convention used by older CLI surfaces. This is the
proposal's surface, and groups read more naturally for users learning the
cockpit. The legacy `sentinel-status` (no space, single command) coexists
unchanged in sentinel_parsers.py.
"""

from __future__ import annotations

from empirica.core.cockpit.loop_registry import VALID_BACKOFF, VALID_KIND, VALID_RESULT, VALID_STATUS

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
    """Register sentinel/loop/listener/instance subcommand groups + top-level status + tui."""
    _add_sentinel_group(subparsers)
    _add_loop_group(subparsers)
    _add_listener_group(subparsers)
    _add_instance_group(subparsers)
    _add_status_command(subparsers)
    _add_tui_command(subparsers)


def _add_tui_command(subparsers):
    tui = subparsers.add_parser(
        'tui',
        help='Launch the interactive cockpit (Textual app — clickable controls)',
    )
    tui.add_argument('--include-dead', action='store_true',
                      help='Show instances whose Claude process is dead '
                           '(diagnostic — toggle in-app with D)')


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

    prune = instance_subs.add_parser(
        'prune',
        help='Bulk forget every instance that fails the liveness check',
    )
    prune.add_argument('--dry-run', action='store_true',
                       help='Show which instances would be removed without removing them')
    _add_output(prune)


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
    register.add_argument('--backoff', choices=VALID_BACKOFF,
                          help='Backoff policy when empty fires accumulate (default: none)')
    register.add_argument('--base-interval',
                          help='Backoff floor — used after a found/fail fire (default: 15m)')
    register.add_argument('--max-interval',
                          help='Backoff ceiling — cap on stretched interval (default: 4h)')
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
    heartbeat.add_argument('--result', choices=VALID_RESULT,
                            help='Signal: found (new work), empty (no work), '
                                 'fail (errored), paused (body short-circuited). '
                                 'Defaults from --status if omitted.')
    heartbeat.add_argument('--message', help='Optional summary message for this fire')
    heartbeat.add_argument('--next-scheduled-job-id',
                            help='Opaque scheduler job id for the next fire — '
                                 'pause uses it to cancel future fires '
                                 '(PROPOSAL_LOOP_SELF_SCHEDULING)')
    heartbeat.add_argument('--scheduler-kind',
                            choices=('cron-create', 'systemd-user', 'system-cron',
                                     'at-queue', 'unknown'),
                            help='Which scheduler installed the next fire')
    _add_instance(heartbeat)
    _add_output(heartbeat)

    should_fire = loop_subs.add_parser('should-fire',
        help='Exit 0 if loop body should run this fire, exit 1 if backoff says skip')
    should_fire.add_argument('name', help='Loop name')
    _add_instance(should_fire)
    _add_output(should_fire)

    poke = loop_subs.add_parser('poke',
        help='Manual escape hatch — zero the streak, clear next_fire_threshold')
    poke.add_argument('name', help='Loop name')
    _add_instance(poke)
    _add_output(poke)

    # PROPOSAL_LOOP_SELF_SCHEDULING — body owns the schedule.
    schedule_next = loop_subs.add_parser(
        'schedule-next',
        help='Compute the next-fire timestamp + cron expression. '
             'Body uses this to install the next one-shot fire.',
    )
    schedule_next.add_argument('name', help='Loop name')
    _add_instance(schedule_next)
    _add_output(schedule_next)

    fire = loop_subs.add_parser(
        'fire',
        help='Manually trigger one fire of the loop body. Bootstraps '
             'after resume on Claude Code (CronCreate-mode only emits a hint).',
    )
    fire.add_argument('name', help='Loop name')
    _add_instance(fire)
    _add_output(fire)

    install = loop_subs.add_parser(
        'install-request',
        help='Cockpit→Claude install: register loop + queue a pending '
             'install request the target Claude picks up via UserPromptSubmit '
             'and installs via /loop / CronCreate.',
    )
    install.add_argument('--name', required=True, help='Loop name')
    install.add_argument('--interval', required=True,
                          help='Base interval (e.g. "15m"). Acts as the cadence '
                               'after a found fire and the floor for backoff.')
    install.add_argument('--description', help='One-line description')
    install.add_argument('--base-interval',
                          help='Backoff floor (default: same as --interval)')
    install.add_argument('--max-interval', default='4h',
                          help='Backoff ceiling (default: 4h)')
    _add_instance(install)
    _add_output(install)

    list_p = loop_subs.add_parser('list', help='List all loops registered for an instance')
    _add_instance(list_p)
    _add_output(list_p)

    status_p = loop_subs.add_parser('status', help='Show status for a single loop')
    status_p.add_argument('name', help='Loop name')
    _add_instance(status_p)
    _add_output(status_p)


def _add_listener_group(subparsers):
    """Event-listener subcommands per PROPOSAL_EVENT_LISTENER.md.

    Listeners are sister concept to loops but event-driven (held HTTP
    connection via ntfy/SSE → Monitor wake), not periodic. Pause must
    mechanically kill the Monitor + held connection (handled by the
    listener body via the install-request analog, item 4).
    """
    listener_root = subparsers.add_parser(
        'listener',
        help='Event listener registry: register, pause, resume per-instance event-driven work',
    )
    listener_subs = listener_root.add_subparsers(dest='listener_action', metavar='action')

    register = listener_subs.add_parser('register', help='Register a listener (idempotent)')
    register.add_argument('--name', required=True,
                          help='Listener name (alphanumeric, dot, dash, underscore)')
    register.add_argument('--topic', required=True,
                          help='Topic URL: <scheme>:<rest>. V1: ntfy:<channel>. '
                               'Future: sse:<url>, websocket:<url>, gmail:<query>, whatsapp:<num>')
    register.add_argument('--description', help='Optional human-readable description')
    register.add_argument('--on-wake',
                          help='Prompt template the listener body replays on each wake. '
                               'Empty = use the default from the inbox-listener skill.')
    _add_instance(register)
    _add_output(register)

    unregister = listener_subs.add_parser('unregister',
        help='Remove a listener from the registry (also clears pause/active state)')
    unregister.add_argument('name', help='Listener name')
    _add_instance(unregister)
    _add_output(unregister)

    pause = listener_subs.add_parser('pause',
        help='Pause a listener — sets pause flag (mechanical kill of Monitor + curl '
             'requires the install-request analog, item 4 of PROPOSAL_EVENT_LISTENER)')
    pause.add_argument('name', help='Listener name')
    _add_instance(pause)
    _add_output(pause)

    resume = listener_subs.add_parser('resume',
        help='Resume a listener (clears pause flag; bootstrap arming via the wake template)')
    resume.add_argument('name', help='Listener name')
    _add_instance(resume)
    _add_output(resume)

    record_wake = listener_subs.add_parser('record-wake',
        help='Record a wake fire (call after the listener body processes a message)')
    record_wake.add_argument('name', help='Listener name')
    record_wake.add_argument('--message',
                              help='Optional summary message for this wake')
    _add_instance(record_wake)
    _add_output(record_wake)

    fire = listener_subs.add_parser('fire',
        help='Manually trigger one wake of the listener body (testing).')
    fire.add_argument('name', help='Listener name')
    _add_instance(fire)
    _add_output(fire)

    list_p = listener_subs.add_parser('list',
        help='List all listeners registered for an instance')
    _add_instance(list_p)
    _add_output(list_p)

    status_p = listener_subs.add_parser('status',
        help='Show status for a single listener')
    status_p.add_argument('name', help='Listener name')
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
    status.add_argument('--include-dead', action='store_true',
                         help='Show instances whose Claude process is dead '
                              '(diagnostic — by default only live instances are listed)')
    fmt = status.add_mutually_exclusive_group()
    fmt.add_argument('--pretty', action='store_true',
                     help='ANSI colored layout (default for TTY)')
    fmt.add_argument('--json', action='store_true', dest='json',
                     help='Machine-readable JSON output (default for pipes)')
    status.add_argument('--output', choices=_OUTPUT_CHOICES,
                         help='Explicit output format (overrides --pretty/--json)')
