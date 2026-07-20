"""Tmux command wrappers for the cockpit launcher.

Subprocess shell-outs to the system ``tmux`` binary. Idempotent —
``launch_cockpit`` attaches to an existing session if one is already
running with the configured ``session_name``.

Two layout modes:

- ``launch_cockpit`` (legacy): one tmux session, N windows, single attach.
- ``launch_groups``: N tmux sessions (one per group), one terminal
  window per session (alacritty or ghostty — see ``config.surface``),
  panes per group. Each window gets a unique WM_CLASS/app-id for
  KDE/wmctrl-friendly window switching (Meta+1..N once pinned):
  ``empirica-<group>`` for alacritty, ``com.empirica.cockpit.<group>``
  for ghostty (GTK app-ids must be reverse-domain-name).
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
    created: bool  # True if a new session was created; False if attached to existing
    windows_created: list[str]
    status_windows_created: list[str]
    error: str | None = None


def _tmux(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run a tmux command, capturing output. Doesn't raise on non-zero
    by default — callers inspect ``returncode`` and ``stderr``."""
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=10,
    )


def tmux_available() -> bool:
    """True iff the ``tmux`` binary is on PATH."""
    return shutil.which("tmux") is not None


def cockpit_session_exists(session_name: str) -> bool:
    """Check whether a tmux session with the given name is running."""
    if not tmux_available():
        return False
    result = _tmux("has-session", "-t", session_name)
    return result.returncode == 0


def launch_cockpit(config: LauncherConfig) -> LaunchResult:
    """Bring up the canonical layout per ``config``. Idempotent —
    attaches to an existing session if one already exists.

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
            error="tmux binary not found on PATH",
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
            error="config has no projects and no status windows — nothing to launch",
        )

    write_session_start()

    windows_created: list[str] = []
    status_windows_created: list[str] = []

    # Initial window — first project, or first status window if no projects.
    if config.projects:
        first = config.projects[0]
        result = _tmux(
            "new-session",
            "-d",
            "-s",
            config.session_name,
            "-n",
            first.name,
            "-c",
            first.path,
            first.launch,
        )
        if result.returncode != 0:
            return LaunchResult(
                session_name=config.session_name,
                created=False,
                windows_created=[],
                status_windows_created=[],
                error=f"tmux new-session failed: {result.stderr.strip() or result.stdout.strip()}",
            )
        windows_created.append(first.name)
        remaining_projects = config.projects[1:]
    else:
        # No projects — bootstrap with the first status window.
        first_status = config.status_windows[0]
        result = _tmux(
            "new-session",
            "-d",
            "-s",
            config.session_name,
            "-n",
            first_status.name,
            first_status.command,
        )
        if result.returncode != 0:
            return LaunchResult(
                session_name=config.session_name,
                created=False,
                windows_created=[],
                status_windows_created=[],
                error=f"tmux new-session failed: {result.stderr.strip() or result.stdout.strip()}",
            )
        status_windows_created.append(first_status.name)
        remaining_projects = []

    # Additional project windows
    for project in remaining_projects:
        result = _tmux(
            "new-window",
            "-t",
            config.session_name,
            "-n",
            project.name,
            "-c",
            project.path,
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
            "new-window",
            "-t",
            config.session_name,
            "-n",
            status.name,
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


def cockpit_kill(session_name: str = "cockpit") -> tuple[bool, str | None]:
    """Destroy the tmux session and write the clean-shutdown marker.

    Returns ``(success, error_message)``. Returns ``(True, None)`` even
    if the session didn't exist (idempotent).
    """
    if not tmux_available():
        return False, "tmux binary not found on PATH"

    if cockpit_session_exists(session_name):
        result = _tmux("kill-session", "-t", session_name)
        if result.returncode != 0:
            return False, f"tmux kill-session failed: {result.stderr.strip()}"

    # Clean shutdown marker even when the session didn't exist —
    # the operator's intent was to have the cockpit gone.
    write_clean_shutdown()
    return True, None


# ─── Groups mode (one terminal window per MONITOR/config, one tmux window
#     per group within it, panes per group) ─────────────────────────────


@dataclass
class GroupLaunchResult:
    """Per-group (= per-window) bring-up result. Terminal spawn is
    tracked once at the ``GroupsLaunchResult`` level, not per group —
    one terminal hosts every group's window."""

    group_name: str
    tmux_session: str
    created: bool  # True = new tmux window created; False = adopted existing
    panes_created: int  # 1 (initial) + N splits = total pane count actually in the window
    error: str | None = None


@dataclass
class GroupsLaunchResult:
    """Aggregate result for ``launch_groups``. One terminal per config
    (= per monitor), hosting every group as a window in one session."""

    groups: list[GroupLaunchResult] = field(default_factory=list)
    session_name: str = ""
    terminal_pid: int | None = None  # PID of the ONE spawned terminal, or None if spawn failed/skipped
    terminal_skipped: bool = False  # True when an existing client was found and we skipped spawning a duplicate
    error: str | None = None  # top-level error (e.g. tmux missing, terminal spawn failed); per-window errors live on each result

    def all_ok(self) -> bool:
        return self.error is None and all(g.error is None for g in self.groups)


def alacritty_available() -> bool:
    """True iff ``alacritty`` is on PATH."""
    return shutil.which("alacritty") is not None


def ghostty_available() -> bool:
    """True iff ``ghostty`` is on PATH."""
    return shutil.which("ghostty") is not None


def _session_has_attached_client(session_name: str) -> bool:
    """True iff the given tmux session has at least one client attached.

    Used by ``launch_groups`` to skip spawning a duplicate alacritty
    window when a previous launch's window is still alive. The check is
    pure tmux state — no wmctrl/Wayland window enumeration needed (which
    is unreliable on KDE Wayland anyway).
    """
    if not tmux_available():
        return False
    result = _tmux("list-clients", "-t", session_name, "-F", "#{client_pid}")
    if result.returncode != 0:
        return False
    return any(line.strip() for line in result.stdout.splitlines())


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
    return None, pane.inline_command or "bash"


def _pane_title(pane: PaneSpec) -> str:
    """Display title for a pane — project name for project panes, the
    configured ``label`` (or a generic fallback) for inline-command
    panes. Set via ``select-pane -T`` and shown by ``pane-border-status``
    so each pane is identifiable (and clickable-to-focus, via tmux's
    built-in mouse handling) without opening it first."""
    if pane.project_ref:
        return pane.project_ref
    return pane.label or "shell"


def _set_pane_title(pane_id: str, title: str) -> None:
    """Best-effort — a failed title-set shouldn't fail the whole launch."""
    if pane_id:
        _tmux("select-pane", "-t", pane_id, "-T", title)


def _create_group_window(
    group: GroupSpec, config: LauncherConfig, session_name: str, is_first_group: bool
) -> tuple[bool, int, str | None]:
    """Create ONE WINDOW (named after the group) inside the shared
    per-monitor session, with all the group's panes split into it.

    One terminal per *monitor* (= per config), not per group — every
    group becomes a clickable window within that single session
    (native tmux status-line window-switching), not a separate
    terminal process. This is what actually delivers "titled clickable
    windows/panes in one cockpit per monitor" rather than N terminal
    windows per monitor.

    Returns ``(created, panes_created, error)``. Idempotent — if the
    window already exists, augments to the configured pane count by
    splitting in the missing panes (preserving any live processes in
    existing panes). This is the abnormal-exit / re-launch path.

    Window target is ``session:group-name`` (by name, not index) so
    this works regardless of ``base-index`` 0 vs 1.
    """
    window_target = f"{session_name}:{group.name}"
    split_flag = "-h" if group.split == "horizontal" else "-v"

    window_exists = _tmux("list-panes", "-t", window_target, "-F", "#{pane_id}").returncode == 0

    if window_exists:
        # Adopt path: count existing panes in this specific window.
        result = _tmux("list-panes", "-t", window_target, "-F", "#{pane_id}")
        existing = len([line for line in result.stdout.splitlines() if line.strip()])
        configured = len(group.panes)
        if existing < configured:
            for pane in group.panes[existing:]:
                cwd, cmd = _resolve_pane(pane, config)
                split_args = ["split-window", "-t", window_target, split_flag, "-P", "-F", "#{pane_id}"]
                if cwd:
                    split_args += ["-c", cwd]
                split_args.append(cmd)
                sresult = _tmux(*split_args)
                if sresult.returncode == 0:
                    existing += 1
                    _set_pane_title(sresult.stdout.strip(), _pane_title(pane))
            _tmux(
                "select-layout",
                "-t",
                window_target,
                "even-horizontal" if group.split == "horizontal" else "even-vertical",
            )
        return False, existing, None

    if not group.panes:
        return False, 0, f"group {group.name!r} has no panes"

    first = group.panes[0]
    cwd, cmd = _resolve_pane(first, config)

    if is_first_group:
        # First group bootstraps the session itself.
        args = ["new-session", "-d", "-s", session_name, "-n", group.name, "-P", "-F", "#{pane_id}"]
        if cwd:
            args += ["-c", cwd]
        args.append(cmd)
        result = _tmux(*args)
        if result.returncode != 0:
            return False, 0, f"tmux new-session failed: {result.stderr.strip() or result.stdout.strip()}"
    else:
        args = ["new-window", "-t", session_name, "-n", group.name, "-P", "-F", "#{pane_id}"]
        if cwd:
            args += ["-c", cwd]
        args.append(cmd)
        result = _tmux(*args)
        if result.returncode != 0:
            return False, 0, f"tmux new-window failed: {result.stderr.strip() or result.stdout.strip()}"

    panes_created = 1
    _set_pane_title(result.stdout.strip(), _pane_title(first))

    for pane in group.panes[1:]:
        cwd, cmd = _resolve_pane(pane, config)
        split_args = ["split-window", "-t", window_target, split_flag, "-P", "-F", "#{pane_id}"]
        if cwd:
            split_args += ["-c", cwd]
        split_args.append(cmd)
        sresult = _tmux(*split_args)
        if sresult.returncode == 0:
            panes_created += 1
            _set_pane_title(sresult.stdout.strip(), _pane_title(pane))

    # Even out pane sizes so a 2-pane horizontal split is 50/50.
    _tmux(
        "select-layout", "-t", window_target, "even-horizontal" if group.split == "horizontal" else "even-vertical"
    )

    return True, panes_created, None


def _spawn_alacritty(group_name: str, session_name: str, extra_args: list[str]) -> tuple[int | None, str | None]:
    """Fork an alacritty window attaching to the given tmux session.

    Returns ``(pid, error)``. The alacritty detaches from the parent
    process (setsid) so closing the launching shell doesn't kill the
    cockpit windows.
    """
    if not alacritty_available():
        return None, "alacritty binary not found on PATH"

    wm_class = f"empirica-{group_name}"
    title = f"Empirica · {group_name}"

    cmd = [
        "alacritty",
        "--class",
        wm_class,
        "--title",
        title,
        *extra_args,
        "-e",
        "tmux",
        "attach-session",
        "-t",
        session_name,
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
        return None, f"alacritty spawn failed: {exc}"


def _spawn_ghostty(group_name: str, session_name: str, extra_args: list[str]) -> tuple[int | None, str | None]:
    """Fork a ghostty window attaching to the given tmux session.

    Same shape as ``_spawn_alacritty`` — see that docstring. Ghostty's
    ``--class`` must be a reverse-domain-name (GTK app-id rules), so we
    can't reuse the bare ``empirica-<group>`` value alacritty accepts;
    ``com.empirica.cockpit.<group>`` satisfies GTK while keeping the
    per-group uniqueness KDE's Meta+1..N switching needs.
    """
    if not ghostty_available():
        return None, "ghostty binary not found on PATH"

    app_id = f"com.empirica.cockpit.{group_name}"
    title = f"Empirica · {group_name}"

    cmd = [
        "ghostty",
        f"--class={app_id}",
        f"--title={title}",
        *extra_args,
        "-e",
        "tmux",
        "attach-session",
        "-t",
        session_name,
    ]

    try:
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
        return None, f"ghostty spawn failed: {exc}"


def launch_groups(config: LauncherConfig) -> GroupsLaunchResult:
    """Bring up the canonical groups layout: ONE terminal window (per
    ``config.surface`` — alacritty or ghostty) for the whole config,
    hosting ``config.session_name`` with one tmux window per group
    (native status-line click-to-switch) and each group's panes split
    inside its window.

    Idempotent — if the session already exists, augments any windows
    missing panes without touching live processes, and skips spawning a
    duplicate terminal if one is already attached. This is the
    abnormal-exit recovery path: after a hibernate-detach, re-running
    ``empirica cockpit launch`` re-wraps the surviving tmux session in a
    fresh terminal window without losing claude state.
    """
    if not tmux_available():
        return GroupsLaunchResult(error="tmux binary not found on PATH")

    if not config.groups:
        return GroupsLaunchResult(error="config has no groups — nothing to launch")

    session_name = config.session_name
    write_session_start()

    results: list[GroupLaunchResult] = []
    session_existed_before = cockpit_session_exists(session_name)
    for i, group in enumerate(config.groups):
        is_first_group = not session_existed_before and i == 0
        created, pane_count, err = _create_group_window(group, config, session_name, is_first_group)
        results.append(
            GroupLaunchResult(
                group_name=group.name,
                tmux_session=f"{session_name}:{group.name}",
                created=created,
                panes_created=pane_count,
                error=err,
            )
        )

    if any(g.error for g in results):
        return GroupsLaunchResult(groups=results, session_name=session_name, error=None)

    # Dedup: if the session already has a client attached (= a terminal
    # window from a prior launch is still alive), don't spawn a
    # duplicate. Re-launching becomes idempotent at the window level,
    # not just the session level.
    if _session_has_attached_client(session_name):
        write_lock()
        return GroupsLaunchResult(groups=results, session_name=session_name, terminal_skipped=True)

    spawn_fn = _spawn_ghostty if config.surface == "ghostty" else _spawn_alacritty
    pid, spawn_err = spawn_fn(
        group_name=session_name,
        session_name=session_name,
        extra_args=config.alacritty_args,
    )

    write_lock()
    return GroupsLaunchResult(groups=results, session_name=session_name, terminal_pid=pid, error=spawn_err)
