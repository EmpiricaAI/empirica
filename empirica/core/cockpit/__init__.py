"""Empirica Cockpit — multi-instance state visibility and per-instance controls.

This module is the core for the `empirica sentinel`, `empirica loop`, and
`empirica status` CLI subcommands. It is intentionally narrow:

- sentinel_pause: wraps the existing ~/.empirica/sentinel_paused_{instance_id}
  pause file. Does NOT reinvent — defers to the same path the Sentinel hook
  already reads.
- loop_registry: per-instance ~/.empirica/loops_{instance_id}.json registry.
  Provides register/pause/resume/heartbeat/list/status/unregister.
- instance_state: state-file scan to discover instances and aggregate
  transaction/sentinel/loop state into one dict per instance.
- render: pretty (ANSI) and machine-readable JSON renderers for `status`.

State-file discovery, no process scanning, no tmux dependency.
"""

from empirica.core.cockpit.enrichment import (
    NotificationItem,
    NotificationSummary,
    OpenGoal,
    RecentAction,
    StatuslineSummary,
    calculate_confidence,
    clear_notifications,
    context_usage,
    is_asking,
    notification_summary,
    notifications_for_project,
    notifications_list,
    notifications_total,
    open_goals_list,
    recent_actions,
    statusline_summary,
)
from empirica.core.cockpit.instance_actions import (
    ForgetResult,
    KillResult,
    StopResult,
    forget_instance,
    get_label,
    kill_instance,
    set_label,
    stop_instance,
)
from empirica.core.cockpit.instance_state import (
    aggregate_all,
    aggregate_instance_state,
    discover_dead_instances,
    discover_instances,
)
from empirica.core.cockpit.listener_registry import (
    ListenerEntry,
    ListenerRegistry,
    is_listener_paused,
    set_listener_paused,
)
from empirica.core.cockpit.liveness import LivenessResult, is_alive
from empirica.core.cockpit.loop_install_request import (
    LoopInstallRequest,
    consume_pending,
    list_pending,
    pending_path,
    render_loop_cron_prompt,
    write_pending,
)
from empirica.core.cockpit.loop_registry import (
    LoopRegistry,
    is_loop_paused,
    set_loop_paused,
)
from empirica.core.cockpit.loop_uninstall_request import (
    LoopUninstallRequest,
)
from empirica.core.cockpit.render import render_json, render_pretty
from empirica.core.cockpit.sentinel_pause import (
    SentinelPauseStatus,
    pause_sentinel,
    resume_sentinel,
    sentinel_status,
)

__all__ = [
    'ForgetResult',
    'KillResult',
    'ListenerEntry',
    'ListenerRegistry',
    'LivenessResult',
    'LoopInstallRequest',
    'LoopRegistry',
    'LoopUninstallRequest',
    'NotificationItem',
    'NotificationSummary',
    'OpenGoal',
    'RecentAction',
    'SentinelPauseStatus',
    'StatuslineSummary',
    'StopResult',
    'aggregate_all',
    'aggregate_instance_state',
    'calculate_confidence',
    'clear_notifications',
    'consume_pending',
    'context_usage',
    'discover_dead_instances',
    'discover_instances',
    'forget_instance',
    'get_label',
    'is_alive',
    'is_asking',
    'is_listener_paused',
    'is_loop_paused',
    'kill_instance',
    'list_pending',
    'notification_summary',
    'notifications_for_project',
    'notifications_list',
    'notifications_total',
    'open_goals_list',
    'pause_sentinel',
    'pending_path',
    'recent_actions',
    'render_json',
    'render_loop_cron_prompt',
    'render_pretty',
    'resume_sentinel',
    'sentinel_status',
    'set_label',
    'set_listener_paused',
    'set_loop_paused',
    'statusline_summary',
    'stop_instance',
    'write_pending',
]
