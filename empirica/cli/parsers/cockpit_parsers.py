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
_OUTPUT_CHOICES = ("human", "json")


def _add_output(parser):
    parser.add_argument("--output", choices=_OUTPUT_CHOICES, default="human", help="Output format (default: human)")


def _add_instance(parser):
    parser.add_argument(
        "--instance", metavar="ID", help="Target instance_id (default: auto-detect from current process)"
    )


def _add_sentinel_target(parser):
    """Target selection for sentinel pause/resume/status verbs.

    Beyond a raw runtime --instance, accepts a practice ai_id (resolved to its
    live runtime instance), --session <claude_session_id>, or --all (fan out
    across a practice's live instances). No-match / ambiguous resolution fails
    loud — practitioner-identity phase ①
    (docs/architecture/instance_isolation/PRACTITIONER_IDENTITY.md §6).
    """
    parser.add_argument(
        "--instance",
        metavar="ID",
        help="Target instance_id OR a practice ai_id (resolved to its live runtime instance; "
        "no-match or ambiguous resolution fails loud)",
    )
    parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="Target the live instance running this claude_session_id",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fan out across ALL live instances of the resolved practice "
        "(required when an ai_id maps to >1 live instance)",
    )


def add_cockpit_parsers(subparsers):
    """Register sentinel/loop/listener/instance/practitioner groups + top-level status + tui."""
    _add_sentinel_group(subparsers)
    _add_loop_group(subparsers)
    _add_listener_group(subparsers)
    _add_instance_group(subparsers)
    _add_practitioner_group(subparsers)
    _add_status_command(subparsers)
    _add_tui_command(subparsers)


def _add_practitioner_group(subparsers):
    """`empirica practitioner <write|clear|list>` — practitioner-presence control.

    Presence is keyed on the durable claude_session_id (--session). write/clear
    are the lifecycle the session hooks shell out to; list is the resolver
    surface (a practice's live practitioners).
    """
    root = subparsers.add_parser(
        "practitioner",
        help="Practitioner presence: write/clear/list (keyed on claude_session_id)",
    )
    subs = root.add_subparsers(dest="practitioner_action", metavar="action")

    write = subs.add_parser("write", help="Register/heartbeat this practitioner's presence")
    write.add_argument("--session", required=True, help="claude_session_id (the durable practitioner key)")
    write.add_argument("--status", default="active", help="active | idle | paused | blocked (default: active)")
    write.add_argument("--pending-question", dest="pending_question", help="Blocked-reason (emit-and-park signal)")
    write.add_argument(
        "--session-pid",
        dest="session_pid",
        type=int,
        help="Claude Code parent PID (os.getppid() at session-init) — the daemon's liveness anchor",
    )
    write.add_argument("--ai-id", dest="ai_id", help="Practice ai_id (default: resolve from project context)")
    write.add_argument("--location", help="Location/instance_id (default: resolve from current process)")
    write.add_argument("--empirica-session", dest="empirica_session", help="Empirica session id (default: resolve)")
    write.add_argument(
        "--active-transaction", dest="active_transaction", help="Active transaction id (default: resolve)"
    )
    write.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    write.add_argument("--verbose", action="store_true", help="Verbose output")

    clear = subs.add_parser("clear", help="Clear this practitioner's presence (session-end)")
    clear.add_argument("--session", required=True, help="claude_session_id")
    clear.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    clear.add_argument("--verbose", action="store_true", help="Verbose output")

    plist = subs.add_parser("list", help="List live practitioners (optionally scoped to a practice)")
    plist.add_argument("--practice", help="Scope to a practice ai_id")
    plist.add_argument(
        "--include-stale", dest="include_stale", action="store_true", help="Include stale (no recent heartbeat)"
    )
    plist.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    plist.add_argument("--verbose", action="store_true", help="Verbose output")

    hb = subs.add_parser("heartbeat", help="Push local presence to cortex's /v1/practitioners/heartbeat")
    hb.add_argument("--session", help="claude_session_id to emit (default: all local non-stale practitioners)")
    hb.add_argument(
        "--include-stale", dest="include_stale", action="store_true", help="Include stale records when emitting all"
    )
    hb.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
    hb.add_argument("--verbose", action="store_true", help="Verbose output")


def _add_tui_command(subparsers):
    tui = subparsers.add_parser(
        "tui",
        help="Launch the interactive cockpit (Textual app — clickable controls)",
    )
    tui.add_argument(
        "--include-dead",
        action="store_true",
        help="Show instances whose Claude process is dead (diagnostic — toggle in-app with D)",
    )


def _add_instance_group(subparsers):
    instance_root = subparsers.add_parser(
        "instance",
        help="Instance lifecycle: kill, forget, label (the destructive control plane)",
    )
    instance_subs = instance_root.add_subparsers(dest="instance_action", metavar="action")

    kill = instance_subs.add_parser(
        "kill",
        help="Terminate an instance (tmux kill-pane for tmux_*, SIGTERM for others)",
    )
    kill.add_argument("instance_id", help="Target instance_id")
    kill.add_argument("--force", action="store_true", help="Use SIGKILL instead of SIGTERM (non-tmux only)")
    kill.add_argument("--yes", "-y", action="store_true", help="Bypass safety check when targeting current instance")
    _add_output(kill)

    forget = instance_subs.add_parser(
        "forget",
        help="Remove all per-instance state files from ~/.empirica/ (cleanup for dead instances)",
    )
    forget.add_argument("instance_id", help="Target instance_id")
    forget.add_argument("--yes", "-y", action="store_true", help="Bypass safety check when targeting current instance")
    _add_output(forget)

    label = instance_subs.add_parser(
        "label",
        help="Set/show/clear the human-readable label for an instance",
    )
    label.add_argument("instance_id", help="Target instance_id")
    label.add_argument("label", nargs="?", help="New label (omit to show current value)")
    label.add_argument("--clear", action="store_true", help="Clear the manual label (revert to project basename)")
    _add_output(label)

    prune = instance_subs.add_parser(
        "prune",
        help="Bulk forget every instance that fails the liveness check",
    )
    prune.add_argument(
        "--dry-run", action="store_true", help="Show which instances would be removed without removing them"
    )
    _add_output(prune)


def _add_sentinel_group(subparsers):
    sentinel_root = subparsers.add_parser(
        "sentinel",
        help="Sentinel pause/resume/status (per-instance noetic firewall control)",
    )
    sentinel_subs = sentinel_root.add_subparsers(dest="sentinel_action", metavar="action")

    pause = sentinel_subs.add_parser("pause", help="Pause Sentinel for an instance")
    pause.add_argument("--reason", help="Optional human-readable reason for the pause")
    _add_sentinel_target(pause)
    _add_output(pause)

    resume = sentinel_subs.add_parser("resume", help="Resume Sentinel for an instance")
    _add_sentinel_target(resume)
    _add_output(resume)

    status = sentinel_subs.add_parser("status", help="Show Sentinel pause state")
    _add_sentinel_target(status)
    _add_output(status)


def _add_loop_group(subparsers):
    loop_root = subparsers.add_parser(
        "loop",
        help="Loop registry: register, pause, heartbeat per-instance scheduled work",
    )
    loop_subs = loop_root.add_subparsers(dest="loop_action", metavar="action")

    register = loop_subs.add_parser("register", help="Register a loop (idempotent)")
    register.add_argument("--name", required=True, help="Loop name (alphanumeric, dot, dash, underscore)")
    register.add_argument("--kind", required=True, choices=VALID_KIND, help="Loop kind: cron | interval | monitor")
    register.add_argument("--cron", help="Cron expression (for kind=cron)")
    register.add_argument("--interval", help='Interval like "5m", "30s", "2h" (for kind=interval)')
    register.add_argument("--description", help="Optional human-readable description")
    register.add_argument(
        "--backoff", choices=VALID_BACKOFF, help="Backoff policy when empty fires accumulate (default: none)"
    )
    register.add_argument("--base-interval", help="Backoff floor — used after a found/fail fire (default: 15m)")
    register.add_argument("--max-interval", help="Backoff ceiling — cap on stretched interval (default: 4h)")
    _add_instance(register)
    _add_output(register)

    unregister = loop_subs.add_parser("unregister", help="Remove a loop from the registry")
    unregister.add_argument("name", help="Loop name")
    _add_instance(unregister)
    _add_output(unregister)

    pause = loop_subs.add_parser("pause", help="Pause a loop (writes pause sidecar)")
    pause.add_argument("name", help="Loop name")
    _add_instance(pause)
    _add_output(pause)

    resume = loop_subs.add_parser("resume", help="Resume a loop (removes pause sidecar)")
    resume.add_argument("name", help="Loop name")
    _add_instance(resume)
    _add_output(resume)

    set_iv = loop_subs.add_parser("set-interval", help="Update a registered loop interval")
    set_iv.add_argument("name", help="Loop name")
    set_iv.add_argument("interval", help='New interval (e.g. "5m")')
    _add_instance(set_iv)
    _add_output(set_iv)

    heartbeat = loop_subs.add_parser("heartbeat", help="Record a loop fire (call after each run)")
    heartbeat.add_argument("name", help="Loop name")
    heartbeat.add_argument("--status", choices=VALID_STATUS, default="ok", help="Run status (default: ok)")
    heartbeat.add_argument(
        "--result",
        choices=VALID_RESULT,
        help="Signal: found (new work), empty (no work), "
        "fail (errored), paused (body short-circuited). "
        "Defaults from --status if omitted.",
    )
    heartbeat.add_argument("--message", help="Optional summary message for this fire")
    heartbeat.add_argument(
        "--next-scheduled-job-id",
        help="Opaque scheduler job id for the next fire — "
        "pause uses it to cancel future fires "
        "(PROPOSAL_LOOP_SELF_SCHEDULING)",
    )
    heartbeat.add_argument(
        "--scheduler-kind",
        choices=("cron-create", "systemd-user", "system-cron", "at-queue", "unknown"),
        help="Which scheduler installed the next fire",
    )
    _add_instance(heartbeat)
    _add_output(heartbeat)

    should_fire = loop_subs.add_parser(
        "should-fire", help="Exit 0 if loop body should run this fire, exit 1 if backoff says skip"
    )
    should_fire.add_argument("name", help="Loop name")
    _add_instance(should_fire)
    _add_output(should_fire)

    poke = loop_subs.add_parser("poke", help="Manual escape hatch — zero the streak, clear next_fire_threshold")
    poke.add_argument("name", help="Loop name")
    _add_instance(poke)
    _add_output(poke)

    # PROPOSAL_LOOP_SELF_SCHEDULING — body owns the schedule.
    schedule_next = loop_subs.add_parser(
        "schedule-next",
        help="Compute the next-fire timestamp + cron expression. Body uses this to install the next one-shot fire.",
    )
    schedule_next.add_argument("name", help="Loop name")
    _add_instance(schedule_next)
    _add_output(schedule_next)

    fire = loop_subs.add_parser(
        "fire",
        help="Manually trigger one fire of the loop body. Bootstraps "
        "after resume on Claude Code (CronCreate-mode only emits a hint).",
    )
    fire.add_argument("name", help="Loop name")
    _add_instance(fire)
    _add_output(fire)

    install = loop_subs.add_parser(
        "install-request",
        help="Cockpit→Claude install: register loop + queue a pending "
        "install request the target Claude picks up via UserPromptSubmit "
        "and installs via /loop / CronCreate.",
    )
    install.add_argument("--name", required=True, help="Loop name")
    install.add_argument(
        "--interval",
        required=True,
        help='Base interval (e.g. "15m"). Acts as the cadence after a found fire and the floor for backoff.',
    )
    install.add_argument("--description", help="One-line description")
    install.add_argument("--base-interval", help="Backoff floor (default: same as --interval)")
    install.add_argument("--max-interval", default="4h", help="Backoff ceiling (default: 4h)")
    install.add_argument(
        "--body-skill",
        help="Optional: paired skill name whose `## Cron Prompt Template` "
        "section becomes the install request prompt_template. Auto-resolved "
        "from canonical_loops.CANONICAL_LOOPS by loop name when not given.",
    )
    _add_instance(install)
    _add_output(install)

    list_p = loop_subs.add_parser("list", help="List all loops registered for an instance")
    _add_instance(list_p)
    _add_output(list_p)

    status_p = loop_subs.add_parser("status", help="Show status for a single loop")
    status_p.add_argument("name", help="Loop name")
    _add_instance(status_p)
    _add_output(status_p)

    # ── systemd-user scheduler (Phase 1a — goal f718156c) ─────────────────
    # Replaces /loop's CronCreate as the firing mechanism. systemd timer
    # appends to ~/.empirica/loop_fires.log; the SessionStart Monitor (Phase
    # 1b) bridges new lines into the running Claude session. True external
    # on/off via systemctl — no Claude cooperation needed to pause.
    enable = loop_subs.add_parser(
        "enable",
        help="Install + start a systemd-user timer for this loop (Phase 1a — "
        "wake-from-idle bridge via Monitor armed at SessionStart).",
    )
    enable.add_argument("name", help="Loop name")
    enable.add_argument("--interval", required=True, help="systemd time spec: 30s | 5min | 1h")
    _add_instance(enable)
    _add_output(enable)

    disable = loop_subs.add_parser(
        "disable",
        help="Stop + remove the systemd-user timer for this loop. Idempotent — no error if the loop was never enabled.",
    )
    disable.add_argument("name", help="Loop name")
    _add_instance(disable)
    _add_output(disable)

    systemd_status = loop_subs.add_parser(
        "systemd-status",
        help="Query systemctl for the timer state (is-active, is-enabled, "
        "last/next trigger). Separate from `status` which inspects "
        "the in-DB registry + pause sidecar.",
    )
    systemd_status.add_argument("name", help="Loop name")
    _add_instance(systemd_status)
    _add_output(systemd_status)

    tick = loop_subs.add_parser(
        "tick",
        help="ExecStart target for systemd-user .service units. Appends one "
        "JSON event to ~/.empirica/loop_fires.log (Monitor bridge input). "
        "Internal — but callable manually for testing or manual fire.",
    )
    tick.add_argument("instance_id", help="Instance identifier")
    tick.add_argument("name", help="Loop name")

    listen = loop_subs.add_parser(
        "listen",
        help="Long-running ntfy listener — push-primary wake mechanism. "
        "Holds an HTTP stream to cortex ntfy topic, prints one JSON "
        "event line to stdout per ECO-decided proposal change. "
        "Runs forever; SessionStart hook arms a Monitor on its stdout. "
        "On disconnect: runs one catch-up content_poll, reconnects.",
    )
    _add_instance(listen)
    listen.add_argument(
        "--loop-name",
        default="cortex-mailbox-poll",
        help="Canonical loop name to attribute events to (default: cortex-mailbox-poll)",
    )

    # ── Persistent listener service (cortex prop_flrtxxn32japbazq) ────────
    # OS-detected install of `empirica loop listen` as a system-level service
    # (systemd-user on Linux, launchd on macOS). Replaces the Monitor-only
    # architecture where the listener died with the Claude session.
    listen_install = loop_subs.add_parser(
        "listen-install",
        help="Install the persistent listener service for an ai_id. "
        "Auto-detects OS (systemd-user / launchd). The service runs "
        "`empirica loop listen --instance <ai_id>` with auto-restart, "
        "so wake events arrive even when no Claude session is open.",
    )
    listen_install.add_argument("--ai-id", help="AI identifier (default: project basename via project.yaml)")
    _add_output(listen_install)

    listen_uninstall = loop_subs.add_parser(
        "listen-uninstall",
        help="Stop + remove the persistent listener service. Idempotent.",
    )
    listen_uninstall.add_argument("--ai-id", help="AI identifier (default: project basename)")
    _add_output(listen_uninstall)

    listen_status = loop_subs.add_parser(
        "listen-status",
        help="Inspect the persistent listener service state (installed, active, unit path, log path).",
    )
    listen_status.add_argument("--ai-id", help="AI identifier (default: project basename)")
    _add_output(listen_status)


def _add_listener_group(subparsers):
    """Event-listener subcommands per PROPOSAL_EVENT_LISTENER.md.

    Listeners are sister concept to loops but event-driven (held HTTP
    connection via ntfy/SSE → Monitor wake), not periodic. Pause must
    mechanically kill the Monitor + held connection (handled by the
    listener body via the install-request analog, item 4).
    """
    listener_root = subparsers.add_parser(
        "listener",
        help="Event listener registry: register, pause, resume per-instance event-driven work",
    )
    listener_subs = listener_root.add_subparsers(dest="listener_action", metavar="action")

    register = listener_subs.add_parser("register", help="Register a listener (idempotent)")
    register.add_argument("--name", required=True, help="Listener name (alphanumeric, dot, dash, underscore)")
    register.add_argument(
        "--topic",
        required=True,
        help="Topic URL: <scheme>:<rest>. V1: ntfy:<channel>. "
        "Future: sse:<url>, websocket:<url>, gmail:<query>, whatsapp:<num>",
    )
    register.add_argument("--description", help="Optional human-readable description")
    register.add_argument(
        "--on-wake",
        help="Prompt template the listener body replays on each wake. "
        "Empty = use the default from the inbox-listener skill.",
    )
    _add_instance(register)
    _add_output(register)

    unregister = listener_subs.add_parser(
        "unregister", help="Remove a listener from the registry (also clears pause/active state)"
    )
    unregister.add_argument("name", help="Listener name")
    _add_instance(unregister)
    _add_output(unregister)

    pause = listener_subs.add_parser(
        "pause",
        help="Pause a listener — sets pause flag (mechanical kill of Monitor + curl "
        "requires the install-request analog, item 4 of PROPOSAL_EVENT_LISTENER)",
    )
    pause.add_argument("name", help="Listener name")
    _add_instance(pause)
    _add_output(pause)

    resume = listener_subs.add_parser(
        "resume", help="Resume a listener (clears pause flag; bootstrap arming via the wake template)"
    )
    resume.add_argument("name", help="Listener name")
    _add_instance(resume)
    _add_output(resume)

    record_wake = listener_subs.add_parser(
        "record-wake", help="Record a wake fire (call after the listener body processes a message)"
    )
    record_wake.add_argument("name", help="Listener name")
    record_wake.add_argument("--message", help="Optional summary message for this wake")
    _add_instance(record_wake)
    _add_output(record_wake)

    fire = listener_subs.add_parser("fire", help="Manually trigger one wake of the listener body (testing).")
    fire.add_argument("name", help="Listener name")
    _add_instance(fire)
    _add_output(fire)

    install = listener_subs.add_parser(
        "install-request",
        help="Cockpit→Claude install: register listener + queue a pending "
        "install request the target Claude picks up via UserPromptSubmit "
        "and arms via /inbox-listener (curl + Monitor).",
    )
    install.add_argument("--name", required=True, help="Listener name")
    install.add_argument("--topic", required=True, help="Topic URL: <scheme>:<rest>. V1: ntfy:<channel>.")
    install.add_argument("--description", help="One-line description")
    install.add_argument(
        "--on-wake", help="Prompt template the listener body replays on each wake (empty = inbox-listener default)."
    )
    _add_instance(install)
    _add_output(install)

    list_p = listener_subs.add_parser("list", help="List all listeners registered for an instance")
    _add_instance(list_p)
    _add_output(list_p)

    status_p = listener_subs.add_parser("status", help="Show status for a single listener")
    status_p.add_argument("name", help="Listener name")
    _add_instance(status_p)
    _add_output(status_p)

    # ─── AI-ergonomic on/arm/off facade (prop_oxrhoehv4) ─────────────────
    # 3 verbs that collapse the multi-step in-session arming protocol to
    # single tool calls. Power-user verbs above stay untouched.

    on_p = listener_subs.add_parser(
        "on", help="Arm the canonical mesh listener for ai_id (short-circuits when persistent OS service is running)"
    )
    on_p.add_argument("--ai-id", help="AI identifier (default: project basename via .empirica/project.yaml)")
    on_p.add_argument("--name", help="Listener name (default: <ai_id>-inbox)")
    on_p.add_argument("--topic", help="ntfy topic (default: ntfy:orchestration-events?tags=<ai_id>)")
    _add_instance(on_p)
    _add_output(on_p)

    arm_p = listener_subs.add_parser("arm", help="Record the Monitor task_id post-arm (chained after `on` + Monitor)")
    arm_p.add_argument("task_id", help="Monitor task id (from the Monitor tool response)")
    arm_p.add_argument("--name", help="Listener name (default: <ai_id>-inbox)")
    arm_p.add_argument("--ai-id", help="AI identifier (default: project basename via .empirica/project.yaml)")
    _add_instance(arm_p)
    _add_output(arm_p)

    off_p = listener_subs.add_parser(
        "off",
        help="Tear down the canonical mesh listener — reaps orphan listener "
        "processes for the ai_id, deletes the state file, and emits "
        "TaskStop + `unregister` next_step JSON",
    )
    off_p.add_argument("--name", help="Listener name (default: <ai_id>-inbox)")
    off_p.add_argument("--ai-id", help="AI identifier (default: project basename via .empirica/project.yaml)")
    _add_instance(off_p)
    _add_output(off_p)

    gc_p = listener_subs.add_parser(
        "gc",
        help="Garbage-collect stale ~/.empirica/listener_active_*.json files "
        "AND orphaned listener processes (parent session dead). "
        "Dry-run by default; pass --apply to actually remove.",
    )
    gc_p.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove the stale files + reap orphan processes (default: dry-run shows what would be removed)",
    )
    gc_p.add_argument(
        "--age-days",
        type=int,
        default=7,
        help="Age threshold in days for the stale criterion (default: 7). Files older than this with no recent wake activity are pruned.",
    )
    _add_output(gc_p)


def _add_status_command(subparsers):
    status = subparsers.add_parser(
        "status",
        help="Cockpit overview — per-instance phase, Sentinel, loops, transactions",
    )
    status.add_argument("--all", action="store_true", help="Show every discoverable instance")
    status.add_argument("--instance", metavar="ID", help="Limit to a single instance")
    status.add_argument(
        "--include-dead",
        action="store_true",
        help="Show instances whose Claude process is dead (diagnostic — by default only live instances are listed)",
    )
    fmt = status.add_mutually_exclusive_group()
    fmt.add_argument("--pretty", action="store_true", help="ANSI colored layout (default for TTY)")
    fmt.add_argument(
        "--json", action="store_true", dest="json", help="Machine-readable JSON output (default for pipes)"
    )
    status.add_argument("--output", choices=_OUTPUT_CHOICES, help="Explicit output format (overrides --pretty/--json)")
