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
import re
import shlex
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

    identities = assign_identities(config, config.session_name)

    # Initial window — first project, or first status window if no projects.
    if config.projects:
        first = config.projects[0]
        result = _open_pane(
            ["new-session", "-d", "-s", config.session_name, "-n", first.name],
            PaneSpec(project_ref=first.name),
            f"{first.name}/0",
            config,
            identities,
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

    _configure_session(config, config.session_name)

    # Additional project windows
    for project in remaining_projects:
        result = _open_pane(
            ["new-window", "-t", config.session_name, "-n", project.name],
            PaneSpec(project_ref=project.name),
            f"{project.name}/0",
            config,
            identities,
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
    error: str | None = (
        None  # top-level error (e.g. tmux missing, terminal spawn failed); per-window errors live on each result
    )

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
        if not os.path.isdir(os.path.expanduser(proj.path)):
            # tmux would fall back to another directory and start the practitioner
            # there. A seat is often launched before every practice is provisioned,
            # so say what is missing and leave a shell instead.
            missing = shlex.quote(f"[empirica] {proj.name} not provisioned yet: {proj.path} does not exist")
            return None, f"echo {missing} && bash"
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


# ─── Practitioner identity ─────────────────────────────────────────────────
#
# A pane's EMPIRICA_INSTANCE_ID is what keeps a relaunched claude the same
# practitioner rather than a generic tmux_N label. It rides two carriers:
#   - `-e EMPIRICA_INSTANCE_ID=<id>` on the pane's creation, so the pane's
#     process (and everything started from a shell in it) inherits it;
#   - the pane option @empirica_instance_id, which outlives any process in the
#     pane and is what `refresh` and mesh-support's claude() wrapper read back.
# Ids are slot-shaped ([a-z][a-z0-9_-]*), which session_resolver treats as an
# intentional override of TMUX_PANE rather than a misconfiguration.

_SHELLS = frozenset({"bash", "zsh", "sh", "fish", "dash", "ksh"})
_SLOT_CHARS = re.compile(r"[^a-z0-9_-]+")


def _launch_program(command: str) -> str:
    """Basename of the program a launch command runs (``claude``, ``bash``...)."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return os.path.basename(parts[0]) if parts else ""


def slot_id(name: str) -> str:
    """A slot-shaped instance id from a project name."""
    slug = _SLOT_CHARS.sub("-", name.strip().lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"p-{slug}" if slug else "pane"
    return slug


def _wanted_identity(pane: PaneSpec, config: LauncherConfig) -> str | None:
    """The instance id this pane should carry, before de-duplication.

    An explicit ``instance_id`` wins ("" means never bind). Otherwise a
    project pane is bound when its launch program is claude or a shell, and
    anything else is left alone: ecodex, for one, puts its own UUID into
    EMPIRICA_INSTANCE_ID, and a preset value would override it.
    """
    if pane.instance_id is not None:
        return slot_id(pane.instance_id) if pane.instance_id else None
    if not pane.project_ref:
        return None
    proj = config.project_by_name(pane.project_ref)
    if proj is None:
        return None
    if proj.instance_id is not None:
        return slot_id(proj.instance_id) if proj.instance_id else None
    program = _launch_program(proj.launch)
    if program == "claude" or program in _SHELLS:
        return slot_id(proj.name)
    return None


def _ids_live_elsewhere(session_name: str) -> set[str]:
    """Instance ids already carried by panes in OTHER tmux sessions."""
    result = _tmux("list-panes", "-a", "-F", "#{session_name}\t#{@empirica_instance_id}")
    if result.returncode != 0:
        return set()
    taken = set()
    for line in result.stdout.splitlines():
        sess, _, iid = line.partition("\t")
        if iid and sess != session_name:
            taken.add(iid)
    return taken


def assign_identities(config: LauncherConfig, session_name: str) -> dict[str, str]:
    """``{pane key: instance id}`` for every pane that should be bound.

    Unique within the config (a second pane of the same project gets ``-2``)
    and against ids live in other tmux sessions (suffixed with this session's
    name), because two panes sharing an id would share transaction files.
    """
    taken = _ids_live_elsewhere(session_name)
    assigned: dict[str, str] = {}
    for key, pane in _pane_keys(config):
        wanted = _wanted_identity(pane, config)
        if not wanted:
            continue
        candidate = wanted
        if candidate in taken:
            candidate = f"{wanted}-{slot_id(session_name)}"
        n = 2
        base = candidate
        while candidate in taken:
            candidate = f"{base}-{n}"
            n += 1
        taken.add(candidate)
        assigned[key] = candidate
    return assigned


def _pane_keys(config: LauncherConfig) -> list[tuple[str, PaneSpec]]:
    """Stable ``<group>/<index>`` keys for every configured pane, in order.

    Stored on each pane as @empirica_pane so a re-launch can tell WHICH
    configured panes survive, not just how many.
    """
    keys: list[tuple[str, PaneSpec]] = []
    if config.groups:
        for group in config.groups:
            for i, pane in enumerate(group.panes):
                keys.append((f"{group.name}/{i}", pane))
    else:
        for proj in config.projects:
            keys.append((f"{proj.name}/0", PaneSpec(project_ref=proj.name)))
    return keys


def _identity_args(instance_id: str | None) -> list[str]:
    return ["-e", f"EMPIRICA_INSTANCE_ID={instance_id}"] if instance_id else []


def _stamp_pane(pane_id: str, key: str, instance_id: str | None, remain: bool) -> None:
    """Record which configured pane this is, and its identity, on the pane itself."""
    if not pane_id:
        return
    _tmux("set-option", "-p", "-t", pane_id, "@empirica_pane", key)
    if instance_id:
        _tmux("set-option", "-p", "-t", pane_id, "@empirica_instance_id", instance_id)
    if remain:
        # A pane whose claude exits stays in place as a dead pane, so
        # `cockpit refresh` can respawn it where it was instead of the layout
        # silently losing a practitioner.
        _tmux("set-option", "-p", "-t", pane_id, "remain-on-exit", "on")


def _runs_claude(pane: PaneSpec, config: LauncherConfig) -> bool:
    if not pane.project_ref:
        return False
    proj = config.project_by_name(pane.project_ref)
    return proj is not None and _launch_program(proj.launch) == "claude"


def _configure_session(config: LauncherConfig, session_name: str) -> None:
    """Session-wide UX: with mouse on, window names in the status line are
    clickable tabs and a click focuses a pane — no prefix keys needed."""
    if config.mouse:
        _tmux("set-option", "-t", session_name, "mouse", "on")


def _open_pane(base: list[str], pane: PaneSpec, key: str, config: LauncherConfig, identities: dict[str, str]):
    """Run one pane-creating tmux command (new-session / new-window /
    split-window) for ``pane``, bound and stamped. Returns the tmux result."""
    cwd, cmd = _resolve_pane(pane, config)
    iid = identities.get(key)
    args = [*base, "-P", "-F", "#{pane_id}"]
    if cwd:
        args += ["-c", cwd]
    args += _identity_args(iid)
    args.append(cmd)
    result = _tmux(*args)
    if result.returncode == 0:
        pane_id = result.stdout.strip()
        _set_pane_title(pane_id, _pane_title(pane))
        _stamp_pane(pane_id, key, iid, remain=bool(iid) and _runs_claude(pane, config))
    return result


def _create_group_window(
    group: GroupSpec,
    config: LauncherConfig,
    session_name: str,
    is_first_group: bool,
    identities: dict[str, str] | None = None,
) -> tuple[bool, int, str | None]:
    """Create ONE WINDOW (named after the group) inside the shared
    per-monitor session, with all the group's panes split into it.

    One terminal per *monitor* (= per config), not per group — every
    group becomes a clickable window within that single session
    (native tmux status-line window-switching), not a separate
    terminal process.

    Returns ``(created, panes_created, error)``. Idempotent — if the
    window already exists, adds back only the configured panes that are
    missing, identified by the @empirica_pane key each pane carries, and
    leaves live processes alone. A window from before the keys existed
    falls back to counting panes.

    Window target is ``session:group-name`` (by name, not index) so
    this works regardless of ``base-index`` 0 vs 1.
    """
    identities = identities or {}
    window_target = f"{session_name}:{group.name}"
    split_flag = "-h" if group.split == "horizontal" else "-v"
    layout = "even-horizontal" if group.split == "horizontal" else "even-vertical"
    keyed = [(f"{group.name}/{i}", pane) for i, pane in enumerate(group.panes)]

    listing = _tmux("list-panes", "-t", window_target, "-F", "#{pane_id}\t#{@empirica_pane}")
    if listing.returncode == 0:
        rows = [line.split("\t") for line in listing.stdout.splitlines() if line.strip()]
        present = {r[1] for r in rows if len(r) > 1 and r[1]}
        existing = len(rows)
        if present:
            missing = [(k, p) for k, p in keyed if k not in present]
        else:
            missing = keyed[existing:]  # unstamped window from an older launch
        for key, pane in missing:
            if (
                _open_pane(["split-window", "-t", window_target, split_flag], pane, key, config, identities).returncode
                == 0
            ):
                existing += 1
        if missing:
            _tmux("select-layout", "-t", window_target, layout)
        return False, existing, None

    if not group.panes:
        return False, 0, f"group {group.name!r} has no panes"

    first_key, first = keyed[0]
    if is_first_group:
        base = ["new-session", "-d", "-s", session_name, "-n", group.name]
        verb = "new-session"
    else:
        base = ["new-window", "-t", session_name, "-n", group.name]
        verb = "new-window"
    result = _open_pane(base, first, first_key, config, identities)
    if result.returncode != 0:
        return False, 0, f"tmux {verb} failed: {result.stderr.strip() or result.stdout.strip()}"
    if is_first_group:
        _configure_session(config, session_name)

    panes_created = 1
    for key, pane in keyed[1:]:
        if _open_pane(["split-window", "-t", window_target, split_flag], pane, key, config, identities).returncode == 0:
            panes_created += 1

    # Even out pane sizes so a 2-pane horizontal split is 50/50.
    _tmux("select-layout", "-t", window_target, layout)

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
    identities = assign_identities(config, session_name)
    for i, group in enumerate(config.groups):
        is_first_group = not session_existed_before and i == 0
        created, pane_count, err = _create_group_window(group, config, session_name, is_first_group, identities)
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


# ─── Refresh: bring dead practitioners back in place ───────────────────────


@dataclass
class RefreshResult:
    """What ``refresh_cockpit`` did, pane by pane."""

    session_name: str
    respawned: list[dict] = field(default_factory=list)  # {key, pane_id, instance_id, command, error}
    alive: list[str] = field(default_factory=list)  # keys whose claude is still running
    missing: list[str] = field(default_factory=list)  # configured claude panes absent from the session
    unkeyed: int = 0  # untracked panes that might be a configured claude pane (blocks `missing`)
    adopted: list[str] = field(default_factory=list)  # older panes matched and stamped this run
    error: str | None = None


def resume_command(proj) -> str:
    """The command that brings ``proj``'s pane back with its conversation.

    An explicit ``resume`` wins. For claude launches, ``--continue`` resumes
    the most recent conversation in the pane's directory without a prompt; a
    bare ``--resume`` would open the session picker instead, so it is swapped.
    ``--resume <id>`` names a conversation already and is kept. Anything that
    is not claude is re-run as launched.
    """
    if proj.resume:
        return proj.resume
    if _launch_program(proj.launch) != "claude":
        return proj.launch
    try:
        tokens = shlex.split(proj.launch)
    except ValueError:
        return proj.launch
    if any(t in ("--continue", "-c") for t in tokens):
        return proj.launch
    for i, t in enumerate(tokens):
        if t in ("--resume", "-r"):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt and not nxt.startswith("-"):
                return proj.launch
            tokens[i] = "--continue"
            return shlex.join(tokens)
    return shlex.join([*tokens, "--continue"])


def _adopt(
    rows: list[dict], specs: dict[str, PaneSpec], config: LauncherConfig, identities: dict[str, str]
) -> list[str]:
    """Stamp untracked panes that can be matched to a configured pane without guessing.

    A pane from a launch that predates @empirica_pane is matched when its window
    is the group's window AND its working directory is the project's directory
    AND exactly one untracked configured pane fits both. Anything ambiguous stays
    untracked: a pane stamped with the wrong key would later be respawned as a
    different practitioner. The running process keeps its environment; the
    identity takes effect the next time the pane is respawned.
    """
    seen = {r["key"] for r in rows if r["key"]}
    adopted: list[str] = []
    for row in rows:
        if row["key"]:
            continue
        candidates = []
        for key, pane in specs.items():
            if key in seen or key.rsplit("/", 1)[0] != row["window"] or not pane.project_ref:
                continue
            proj = config.project_by_name(pane.project_ref)
            if proj and os.path.realpath(proj.path) == os.path.realpath(row["path"] or "/nonexistent"):
                candidates.append(key)
        if len(candidates) != 1:
            continue
        key = candidates[0]
        iid = identities.get(key)
        _stamp_pane(row["pane_id"], key, iid, remain=bool(iid) and _runs_claude(specs[key], config))
        row["key"], row["iid"] = key, iid or ""
        seen.add(key)
        adopted.append(key)
    return adopted


def refresh_cockpit(config: LauncherConfig) -> RefreshResult:
    """Respawn, in place, every claude pane whose claude has exited.

    A pane counts as dead when tmux reports it dead (remain-on-exit keeps it
    in the layout) or when a shell is in its foreground where claude should
    be. It is respawned in the same position, in its project directory, with
    the same EMPIRICA_INSTANCE_ID and ``resume_command``. Live panes, and panes
    that do not run claude, are never touched. Configured panes that no longer
    exist at all are reported, not recreated: ``cockpit launch`` adds them back.
    Panes from an older launch are adopted first when they match unambiguously.
    """
    session_name = config.session_name
    out = RefreshResult(session_name=session_name)
    if not tmux_available():
        out.error = "tmux binary not found on PATH"
        return out
    if not cockpit_session_exists(session_name):
        out.error = f"cockpit session {session_name!r} is not running — use `empirica cockpit launch`"
        return out

    fmt = (
        "#{pane_id}\t#{pane_dead}\t#{pane_current_command}\t#{@empirica_pane}\t"
        "#{@empirica_instance_id}\t#{window_name}\t#{pane_current_path}"
    )
    listing = _tmux("list-panes", "-s", "-t", session_name, "-F", fmt)
    if listing.returncode != 0:
        out.error = f"tmux list-panes failed: {listing.stderr.strip()}"
        return out
    names = ("pane_id", "dead", "current", "key", "iid", "window", "path")
    rows = [dict(zip(names, line.split("\t"), strict=False)) for line in listing.stdout.splitlines() if line]
    rows = [r for r in rows if len(r) == len(names)]

    specs = dict(_pane_keys(config))
    out.adopted = _adopt(rows, specs, config, assign_identities(config, session_name))
    seen = {r["key"] for r in rows if r["key"]}
    for row in rows:
        key = row["key"]
        if not key:
            # Only an untracked pane that COULD be a configured claude pane makes
            # absence undecidable; a spare shell in a fully matched window cannot.
            if any(
                k not in seen and k.rsplit("/", 1)[0] == row["window"] and _runs_claude(p, config)
                for k, p in specs.items()
            ):
                out.unkeyed += 1
            continue
        pane = specs.get(key)
        if pane is None or not _runs_claude(pane, config):
            continue
        if row["dead"] != "1" and row["current"] not in _SHELLS:
            out.alive.append(key)
            continue
        proj = config.project_by_name(pane.project_ref or "")
        if proj is None:
            continue
        command = resume_command(proj)
        iid = row["iid"] or None
        args = ["respawn-pane", "-k", "-t", row["pane_id"], "-c", proj.path, *_identity_args(iid), command]
        result = _tmux(*args)
        out.respawned.append(
            {
                "key": key,
                "pane_id": row["pane_id"],
                "instance_id": iid,
                "command": command,
                "error": None if result.returncode == 0 else (result.stderr.strip() or "respawn-pane failed"),
            }
        )

    # With untracked panes left, absence cannot be told from "not adopted".
    if not out.unkeyed:
        out.missing = [k for k, p in specs.items() if k not in seen and _runs_claude(p, config)]
    return out
