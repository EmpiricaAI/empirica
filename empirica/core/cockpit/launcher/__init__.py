"""Cockpit launcher — single-command multi-instance bring-up.

Per ``docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md``. Separates **layout**
(tmux windows, working directories, commands — what this module
restores) from **state** (Claude session_ids, conversation history —
not restorable). The launcher's value is removing layout-restoration
friction; conversation continuity is what ``/compact`` + Empirica
artifacts already handle.

Public API:
  - ``launch_cockpit(config, attach=True)`` — bring up the canonical
    layout, idempotent attach when session already exists
  - ``cockpit_status()`` — read current cockpit state without attaching
  - ``cockpit_kill(prune=False)`` — destroy session + write clean
    shutdown marker
  - ``detect_abnormal_exit()`` — compare last_session_start vs
    last_clean_shutdown, return None or AbnormalExit
  - ``write_clean_shutdown()`` — mark a clean detach/kill
  - ``write_session_start()`` — mark a launch
"""

from __future__ import annotations

from empirica.core.cockpit.launcher.config import (
    DEFAULT_CONFIG_PATH,
    GroupSpec,
    LauncherConfig,
    PaneSpec,
    ProjectSpec,
    StatusWindow,
    load_config,
    write_default_config,
)
from empirica.core.cockpit.launcher.detection import (
    AbnormalExit,
    SessionAlreadyRunning,
    detect_abnormal_exit,
)
from empirica.core.cockpit.launcher.state import (
    COCKPIT_DIR,
    LAST_CLEAN_SHUTDOWN_PATH,
    LAST_SESSION_START_PATH,
    LOCK_PATH,
    cockpit_status,
    write_clean_shutdown,
    write_session_start,
)
from empirica.core.cockpit.launcher.tmux import (
    GroupLaunchResult,
    GroupsLaunchResult,
    LaunchResult,
    alacritty_available,
    cockpit_kill,
    cockpit_session_exists,
    launch_cockpit,
    launch_groups,
)

__all__ = [
    'COCKPIT_DIR',
    'DEFAULT_CONFIG_PATH',
    'LAST_CLEAN_SHUTDOWN_PATH',
    'LAST_SESSION_START_PATH',
    'LOCK_PATH',
    'AbnormalExit',
    'GroupLaunchResult',
    'GroupSpec',
    'GroupsLaunchResult',
    'LaunchResult',
    'LauncherConfig',
    'PaneSpec',
    'ProjectSpec',
    'SessionAlreadyRunning',
    'StatusWindow',
    'alacritty_available',
    'cockpit_kill',
    'cockpit_session_exists',
    'cockpit_status',
    'detect_abnormal_exit',
    'launch_cockpit',
    'launch_groups',
    'load_config',
    'write_clean_shutdown',
    'write_default_config',
    'write_session_start',
]
