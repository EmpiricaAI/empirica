"""Tmux command wrappers for the cockpit launcher.

Subprocess shell-outs to the system ``tmux`` binary. Idempotent —
``launch_cockpit`` attaches to an existing session if one is already
running with the configured ``session_name``.

Two layout modes:

- ``launch_cockpit`` (legacy): one tmux session, N windows, single attach.
- ``launch_groups``: N tmux sessions (one per group), one alacritty
  window per session, panes per group. Each alacritty gets a unique
  ``WM_CLASS=empirica-<group>`` for KDE/wmctrl-friendly window
  switching (Meta+1..N once pinned).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from empirica.core.cockpit.launcher.config import (
    GroupSpec,
    LauncherConfig,
    PaneSpec,
)
from empirica.core.cockpit.launcher.state import (
    write_clean_shutdown,
    write_lock,
    write_session_start,
)


@dataclass
class LaunchResult:
    """Returned by ``launch_cockpit``. Lets the caller decide whether
    to attach interactively or print a summary."""
    session_name: str
    created: bool          # True if a new session was created; False if attached to existing
    windows_created: list[str]
    status_windows_created: list[str]
    error: str | None = None


def _tmux(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run a tmux command, capturing output. Doesn't raise on non-zero
    by default — callers inspect ``returncode`` and ``stderr``."""
    return subprocess.run(
        ['tmux', *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=10,
    )


def tmux_available() -> bool:
    """True iff the ``tmux`` binary is on PATH."""
    return shutil.which('tmux') is not None


def cockpit_session_exists(session_name: str) -> bool:
    """Check whether a tmux session with the given name is running."""
    if not tmux_available():
        return False
    result = _tmux('has-session', '-t', session_name)
    return result.returncode == 0


def launch_cockpit(
    config: LauncherConfig,
    attach: bool | None = None,
) -> LaunchResult:
    """Bring up the canonical layout per ``config``. Idempotent —
    attaches to an existing session if one already exists.

    Args:
        config: Loaded launcher config.
        attach: Override config.attach_on_launch when set.

    Returns:
        ``LaunchResult`` with what was created and an optional error.
        The caller does the actual attach (subprocess.run with
        ``tmux attach`` taking over stdin/stdout) — this function
        only sets up the layout.
    """
    if not tmux_available():
        return LaunchResult(
            session_name=config.session_name,
            created=False,
            windows_created=[],
            status_windows_created=[],
            error='tmux binary not found on PATH',
        )

    # Idempotent: if the session exists, just record we're attaching.
    if cockpit_session_exists(config.session_name):
        return LaunchResult(
            session_name=config.session_name,
            created=False,
            windows_created=[],
            status_windows_created=[],
        )

    # Create the session with the first project as the initial window so
    # tmux doesn't open an extra empty window we'd have to close.
    if not config.projects and not config.status_windows:
        return LaunchResult(
            session_name=config.session_name,
            created=False,
            windows_created=[],
            status_windows_created=[],
            error='config has no projects and no status windows — nothing to launch',
        )

    write_session_start()

    windows_created: list[str] = []
    status_windows_created: list[str] = []

    # Initial window — first project, or first status window if no projects.
    if config.projects:
        first = config.projects[0]
        result = _tmux(
            'new-session', '-d',
            '-s', config.session_name,
            '-n', first.name,
            '-c', first.path,
            first.launch,
        )
        if result.returncode != 0:
            return LaunchResult(
                session_name=config.session_name,
                created=False,
                windows_created=[],
                status_windows_created=[],
                error=f'tmux new-session failed: {result.stderr.strip() or result.stdout.strip()}',
            )
        windows_created.append(first.name)
        remaining_projects = config.projects[1:]
    else:
        # No projects — bootstrap with the first status window.
        first_status = config.status_windows[0]
        result = _tmux(
            'new-session', '-d',
            '-s', config.session_name,
            '-n', first_status.name,
            first_status.command,
        )
        if result.returncode != 0:
            return LaunchResult(
                session_name=config.session_name,
                created=False,
                windows_created=[],
                status_windows_created=[],
                error=f'tmux new-session failed: {result.stderr.strip() or result.stdout.strip()}',
            )
        status_windows_created.append(first_status.name)
        remaining_projects = []

    # Additional project windows
    for project in remaining_projects:
        result = _tmux(
            'new-window',
            '-t', config.session_name,
            '-n', project.name,
            '-c', project.path,
            project.launch,
        )
        if result.returncode == 0:
            windows_created.append(project.name)

    # Status windows (skip the first if we already used it as the bootstrap)
    if config.projects:
        status_iter = config.status_windows
    else:
        status_iter = config.status_windows[1:]
    for status in status_iter:
        result = _tmux(
            'new-window',
            '-t', config.session_name,
            '-n', status.name,
            status.command,
        )
        if result.returncode == 0:
            status_windows_created.append(status.name)

    # Lock file — records that the cockpit is now active.
    write_lock()

    return LaunchResult(
        session_name=config.session_name,
        created=True,
        windows_created=windows_created,
        status_windows_created=status_windows_created,
    )


def cockpit_kill(session_name: str = 'cockpit') -> tuple[bool, str | None]:
    """Destroy the tmux session and write the clean-shutdown marker.

    Returns ``(success, error_message)``. Returns ``(True, None)`` even
    if the session didn't exist (idempotent).
    """
    if not tmux_available():
        return False, 'tmux binary not found on PATH'

    if cockpit_session_exists(session_name):
        result = _tmux('kill-session', '-t', session_name)
        if result.returncode != 0:
            return False, f'tmux kill-session failed: {result.stderr.strip()}'

    # Clean shutdown marker even when the session didn't exist —
    # the operator's intent was to have the cockpit gone.
    write_clean_shutdown()
    return True, None


# ─── Groups mode (one alacritty window per group, panes per group) ─────


@dataclass
class GroupLaunchResult:
    """Per-group bring-up result."""
    group_name: str
    tmux_session: str
    created: bool                 # True = new tmux session created; False = adopted existing
    panes_created: int            # 1 (initial) + N splits = total pane count actually in session
    alacritty_pid: int | None     # PID of the spawned alacritty, or None if spawn failed
    error: str | None = None


@dataclass
class GroupsLaunchResult:
    """Aggregate result for ``launch_groups``."""
    groups: list[GroupLaunchResult] = field(default_factory=list)
    error: str | None = None      # top-level error (e.g. tmux missing); per-group errors live on each result

    def all_ok(self) -> bool:
        return self.error is None and all(g.error is None for g in self.groups)


def alacritty_available() -> bool:
    """True iff ``alacritty`` is on PATH."""
    return shutil.which('alacritty') is not None


def _group_session_name(group_name: str) -> str:
    """Tmux session name for a group. Prefixed to namespace from ad-hoc sessions."""
    return f'empirica-{group_name}'


def _resolve_pane(pane: PaneSpec, config: LauncherConfig) -> tuple[str | None, str]:
    """Return (cwd, command) for a pane spec.

    cwd is None for inline_command panes (run in the user's home/cwd —
    cockpit etc. don't care about pwd).
    """
    if pane.project_ref:
        proj = config.project_by_name(pane.project_ref)
        if proj is None:
            # Reference to non-existent project — surface as a no-op pane
            # with bash so the operator can see something is wrong rather
            # than the whole session failing.
            return None, f'echo "[empirica] unknown project: {pane.project_ref}" && bash'
        return proj.path, proj.launch
    return None, pane.inline_command or 'bash'


def _create_group_session(group: GroupSpec, config: LauncherConfig) -> tuple[bool, int, str | None]:
    """Create a tmux session for a group with all its panes.

    Returns ``(created, panes_created, error)``. Idempotent — if the
    session already exists, returns ``(False, <existing pane count>, None)``
    so the caller can still spawn alacritty for the existing session
    (the abnormal-exit recovery path).
    """
    session_name = _group_session_name(group.name)

    if cockpit_session_exists(session_name):
        # Adopt mode: count panes in the first window so the result is honest.
        result = _tmux('list-panes', '-t', f'{session_name}:0', '-F', '#{pane_id}')
        existing = len([line for line in result.stdout.splitlines() if line.strip()])
        return False, existing, None

    if not group.panes:
        return False, 0, f'group {group.name!r} has no panes'

    # First pane = initial window
    first = group.panes[0]
    cwd, cmd = _resolve_pane(first, config)
    args = ['new-session', '-d', '-s', session_name, '-n', group.name]
    if cwd:
        args += ['-c', cwd]
    args.append(cmd)
    result = _tmux(*args)
    if result.returncode != 0:
        return False, 0, f'tmux new-session failed: {result.stderr.strip() or result.stdout.strip()}'

    panes_created = 1

    # Subsequent panes = splits
    split_flag = '-h' if group.split == 'horizontal' else '-v'
    for pane in group.panes[1:]:
        cwd, cmd = _resolve_pane(pane, config)
        split_args = ['split-window', '-t', f'{session_name}:0', split_flag]
        if cwd:
            split_args += ['-c', cwd]
        split_args.append(cmd)
        sresult = _tmux(*split_args)
        if sresult.returncode == 0:
            panes_created += 1

    # Even out pane sizes so a 2-pane horizontal split is 50/50
    _tmux('select-layout', '-t', f'{session_name}:0',
          'even-horizontal' if group.split == 'horizontal' else 'even-vertical')

    return True, panes_created, None


def _spawn_alacritty(group_name: str, session_name: str, extra_args: list[str]) -> tuple[int | None, str | None]:
    """Fork an alacritty window attaching to the given tmux session.

    Returns ``(pid, error)``. The alacritty detaches from the parent
    process (setsid) so closing the launching shell doesn't kill the
    cockpit windows.
    """
    if not alacritty_available():
        return None, 'alacritty binary not found on PATH'

    wm_class = f'empirica-{group_name}'
    title = f'Empirica · {group_name}'

    cmd = [
        'alacritty',
        '--class', wm_class,
        '--title', title,
        *extra_args,
        '-e', 'tmux', 'attach-session', '-t', session_name,
    ]

    try:
        # start_new_session detaches from our process group — closing this
        # terminal won't SIGHUP the cockpit alacritty windows.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )
        return proc.pid, None
    except OSError as exc:
        return None, f'alacritty spawn failed: {exc}'


def launch_groups(config: LauncherConfig) -> GroupsLaunchResult:
    """Bring up the canonical groups layout: one alacritty per group,
    each running its own tmux session with the configured panes.

    Idempotent per-group — if a group's tmux session already exists,
    re-spawns alacritty for it without touching the running panes.
    This is the abnormal-exit recovery path: after a hibernate-detach,
    re-running ``empirica cockpit launch`` re-wraps the surviving tmux
    sessions in fresh alacritty windows without losing claude state.
    """
    if not tmux_available():
        return GroupsLaunchResult(error='tmux binary not found on PATH')

    if not config.groups:
        return GroupsLaunchResult(error='config has no groups — nothing to launch')

    write_session_start()

    results: list[GroupLaunchResult] = []
    for group in config.groups:
        session_name = _group_session_name(group.name)
        created, pane_count, err = _create_group_session(group, config)
        if err:
            results.append(GroupLaunchResult(
                group_name=group.name,
                tmux_session=session_name,
                created=False,
                panes_created=0,
                alacritty_pid=None,
                error=err,
            ))
            continue

        pid, alacritty_err = _spawn_alacritty(
            group_name=group.name,
            session_name=session_name,
            extra_args=config.alacritty_args,
        )
        results.append(GroupLaunchResult(
            group_name=group.name,
            tmux_session=session_name,
            created=created,
            panes_created=pane_count,
            alacritty_pid=pid,
            error=alacritty_err,
        ))

    write_lock()
    return GroupsLaunchResult(groups=results)
