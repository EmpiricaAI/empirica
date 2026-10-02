"""Cockpit launcher config — ``~/.empirica/cockpit/config.yaml``.

User-editable file declaring the canonical layout: tmux session name,
attach behaviour, project list (one tmux window per project), optional
status windows, and on-abnormal-exit policy.

Sensible defaults: most users don't need to touch this file. First
``empirica cockpit launch`` run with no config writes a minimal one
based on detected projects (any directory under ``~/empirical-ai/`` with
a ``.empirica/`` folder).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".empirica" / "cockpit" / "config.yaml"
DEFAULT_PROJECTS_ROOT = Path.home() / "empirical-ai"


@dataclass
class ProjectSpec:
    """One tmux window in the cockpit — typically one project."""

    name: str
    path: str
    launch: str = "claude"  # command to run in this window
    kind: str = "code"  # placeholder for future split / pane semantics
    # EMPIRICA_INSTANCE_ID for this project's panes. None = derive from the
    # name when the launch program is claude or a shell; "" = never bind.
    instance_id: str | None = None
    # Command `cockpit refresh` runs to bring a dead pane back. None = the
    # launch command with --continue, when the launch program is claude.
    resume: str | None = None


@dataclass
class StatusWindow:
    """An always-on observability window (monitor, log tail, etc.)."""

    name: str
    command: str


@dataclass
class PaneSpec:
    """One pane within a group (one tmux session).

    Exactly one of ``project_ref`` / ``inline_command`` is set:
      - ``project_ref``: name lookup into ``LauncherConfig.projects``;
        the pane runs that project's ``launch`` command in ``path``.
      - ``inline_command``: raw shell command to run in the pane (e.g.
        ``empirica cockpit`` for the cockpit pane).
    """

    project_ref: str | None = None
    inline_command: str | None = None
    label: str | None = None  # optional human-readable pane title
    instance_id: str | None = None  # overrides the project's; "" = never bind


@dataclass
class GroupSpec:
    """One terminal window (alacritty or ghostty, per ``surface``) = one
    tmux session with N panes (default 2).

    Each group becomes a separate terminal window with its own
    WM_CLASS/app-id so KDE/wmctrl can target it individually (Meta+1..N
    for taskbar slots once pinned).
    """

    name: str
    panes: list[PaneSpec] = field(default_factory=list)
    split: str = "horizontal"  # 'horizontal' (left/right) | 'vertical' (top/bottom)


@dataclass
class LauncherConfig:
    """Loaded cockpit config.

    Two layout modes (mutually compatible — groups wins when present):

    1. **Legacy (single session)**: ``projects`` + ``status_windows`` →
       one tmux session named ``session_name`` with one window per
       project. Single ``tmux attach`` in the launching terminal.

    2. **Groups (multi-window)**: ``groups`` → one tmux session per
       group, one terminal window per group (alacritty or ghostty, per
       ``surface``), panes per group. Each group's window gets a unique
       WM_CLASS/app-id for keyboard-shortcut window switching (Meta+1..N
       in KDE).
    """

    session_name: str = "cockpit"
    attach_on_launch: bool = True
    projects: list[ProjectSpec] = field(default_factory=list)
    status_windows: list[StatusWindow] = field(default_factory=list)
    groups: list[GroupSpec] = field(default_factory=list)
    surface: str = "tmux"  # 'tmux' (legacy single attach) | 'alacritty' | 'ghostty' (groups mode)
    alacritty_args: list[str] = field(default_factory=list)
    warn_on_abnormal_exit: bool = True
    auto_prune_dead: bool = False
    notify_on_abnormal_exit: bool = True
    mouse: bool = True  # click a window name to switch, click a pane to focus

    def project_names(self) -> list[str]:
        return [p.name for p in self.projects]

    def project_by_name(self, name: str) -> ProjectSpec | None:
        for p in self.projects:
            if p.name == name:
                return p
        return None

    def is_groups_mode(self) -> bool:
        return bool(self.groups)


def _builtin_default(projects: list[ProjectSpec] | None = None) -> LauncherConfig:
    """Sensible defaults for first-run config."""
    return LauncherConfig(
        session_name="cockpit",
        attach_on_launch=True,
        projects=projects or [],
        status_windows=[
            StatusWindow(
                name="monitor",
                command="watch -n 2 empirica status --all --pretty",
            ),
        ],
        warn_on_abnormal_exit=True,
        auto_prune_dead=False,
        notify_on_abnormal_exit=True,
    )


def _default_project_roots() -> list[Path]:
    """Where first-launch discovery looks: ``~/empirical-ai`` and ``~/empirica``.

    ``provision-practice`` creates practices under ``~/empirica`` unless told otherwise, so a
    practice provisioned with the defaults must be found here too; before this a default-provisioned
    practice never appeared in an auto-generated cockpit. Resolved per call, not at import, so it
    follows the current HOME.
    """
    return [Path.home() / "empirical-ai", Path.home() / "empirica"]


def detect_projects(projects_root: Path | None = None) -> list[ProjectSpec]:
    """Discover candidate projects under ``~/empirical-ai/`` and ``~/empirica/``.

    A directory qualifies if it has a ``.empirica/`` subdirectory. The launch command defaults to
    ``claude``. An explicit ``projects_root`` searches only that directory. A name found under
    both default roots is listed once, from the first.
    """
    roots = [projects_root] if projects_root is not None else _default_project_roots()
    discovered: list[ProjectSpec] = []
    names: set[str] = set()
    resolved: set[Path] = set()
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
            entries = sorted(root.iterdir())
        except OSError:
            continue  # a root we cannot read finds nothing; it must not take the launcher down
        for entry in entries:
            if not entry.is_dir() or entry.name in names or entry.resolve() in resolved:
                continue  # same name, or the same directory under another name (a symlink alias)
            if (entry / ".empirica").is_dir():
                names.add(entry.name)
                resolved.add(entry.resolve())
                discovered.append(
                    ProjectSpec(
                        name=entry.name,
                        path=str(entry.resolve()),
                        launch="claude",
                        kind="code",
                    )
                )
    return discovered


def _optional_str(entry: dict, key: str) -> str | None:
    """A key that may be absent (None), explicitly empty (""), or set.

    Absent and empty mean different things for ``instance_id``: absent derives
    one, empty says this pane must not be bound.
    """
    if key not in entry or entry[key] is None:
        return None
    value = entry[key]
    if value is False:
        return ""
    return str(value).strip()


def _serialize_project(p: ProjectSpec) -> dict[str, Any]:
    out: dict[str, Any] = {"name": p.name, "path": p.path, "launch": p.launch, "kind": p.kind}
    if p.instance_id is not None:
        out["instance_id"] = p.instance_id
    if p.resume is not None:
        out["resume"] = p.resume
    return out


def _as_list(value: object) -> list:
    """A YAML section as a list; anything else (a scalar, a mapping) is no entries, and is REPORTED by
    `dropped_entries`. `projects: 5` used to raise out of load_config, and so out of `status`."""
    return value if isinstance(value, list) else []


def _parse_groups(raw_groups: list) -> list[GroupSpec]:
    """Parse the optional ``groups:`` section of the launcher YAML."""
    groups: list[GroupSpec] = []
    for entry in raw_groups:
        if not isinstance(entry, dict):
            continue
        gname = entry.get("name")
        if not gname:
            continue
        panes: list[PaneSpec] = []
        for pane in _as_list(entry.get("panes")):
            if not isinstance(pane, dict):
                continue
            project_ref = pane.get("project")
            inline = pane.get("command")
            if not project_ref and not inline:
                continue
            panes.append(
                PaneSpec(
                    project_ref=str(project_ref) if project_ref else None,
                    inline_command=str(inline) if inline else None,
                    label=str(pane.get("label")) if pane.get("label") else None,
                    instance_id=_optional_str(pane, "instance_id"),
                )
            )
        if not panes:
            continue
        groups.append(
            GroupSpec(
                name=str(gname),
                panes=panes,
                split=str(entry.get("split") or "horizontal"),
            )
        )
    return groups


def load_config(path: Path | None = None) -> LauncherConfig:
    """Load cockpit config from disk. Returns built-in defaults when
    the file doesn't exist (caller decides whether to write the default).
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return _builtin_default()
    try:
        import yaml

        with config_path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception:
        return _builtin_default()

    if not isinstance(raw, dict):
        return _builtin_default()

    projects = []
    for entry in _as_list(raw.get("projects")):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        path = entry.get("path")
        if not name or not path:
            continue
        projects.append(
            ProjectSpec(
                name=str(name),
                path=str(Path(str(path)).expanduser()),  # tmux does not expand ~ in -c
                launch=str(entry.get("launch") or "claude"),
                kind=str(entry.get("kind") or "code"),
                instance_id=_optional_str(entry, "instance_id"),
                resume=_optional_str(entry, "resume"),
            )
        )

    status_windows = []
    for entry in _as_list(raw.get("status_windows")):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        command = entry.get("command")
        if not name or not command:
            continue
        status_windows.append(StatusWindow(name=str(name), command=str(command)))

    groups = _parse_groups(_as_list(raw.get("groups")))

    abnormal = raw.get("on_abnormal_exit")
    abnormal = abnormal if isinstance(abnormal, dict) else {}
    surface = str(raw.get("surface") or ("alacritty" if groups else "tmux"))
    alacritty_args = [str(a) for a in _as_list(raw.get("alacritty_args")) if a]

    return LauncherConfig(
        session_name=str(raw.get("session_name") or "cockpit"),
        attach_on_launch=bool(raw.get("attach_on_launch", True)),
        projects=projects,
        status_windows=status_windows,
        groups=groups,
        surface=surface,
        alacritty_args=alacritty_args,
        warn_on_abnormal_exit=bool(abnormal.get("warn", True)),
        auto_prune_dead=bool(abnormal.get("auto_prune_dead", False)),
        notify_on_abnormal_exit=bool(abnormal.get("notify", True)),
        mouse=bool(raw.get("mouse", True)),
    )


def _serialize(config: LauncherConfig) -> dict[str, Any]:
    out: dict[str, Any] = {
        "session_name": config.session_name,
        "attach_on_launch": config.attach_on_launch,
        "surface": config.surface,
        "mouse": config.mouse,
        "projects": [_serialize_project(p) for p in config.projects],
        "status_windows": [{"name": w.name, "command": w.command} for w in config.status_windows],
        "on_abnormal_exit": {
            "warn": config.warn_on_abnormal_exit,
            "auto_prune_dead": config.auto_prune_dead,
            "notify": config.notify_on_abnormal_exit,
        },
    }
    if config.groups:
        out["groups"] = [
            {
                "name": g.name,
                "split": g.split,
                "panes": [
                    {
                        k: v
                        for k, v in {
                            "project": p.project_ref,
                            "command": p.inline_command,
                            "label": p.label,
                            "instance_id": p.instance_id,
                        }.items()
                        if v is not None
                    }
                    for p in g.panes
                ],
            }
            for g in config.groups
        ]
    if config.alacritty_args:
        out["alacritty_args"] = config.alacritty_args
    return out


_PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ProfileError(ValueError):
    """A profile could not be created or extended without risking the user's file."""


def config_file_state(path: Path | None = None) -> str:
    """What ``load_config`` found at ``path``: ``ok``, ``missing``, ``unreadable`` or ``not-a-mapping``.

    ``load_config`` answers all three non-ok cases with the built-in defaults, which is right for
    launching and invisible to a status check: a profile with a YAML typo would report the
    defaults as if they were the profile.
    """
    import yaml

    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return "missing"
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return "unreadable"
    return "ok" if raw is None or isinstance(raw, dict) else "not-a-mapping"


def _dropped_groups(raw: dict) -> list[str]:
    out: list[str] = []
    for i, g in enumerate(_as_list(raw.get("groups")), start=1):
        if not isinstance(g, dict):
            out.append(f"groups[{i}] is ignored: not a mapping")
            continue
        if not g.get("name"):
            out.append(f"groups[{i}] is ignored: has no name")
            continue
        label = f"group {g.get('name')!r}"
        if g.get("panes") not in (None, []) and not isinstance(g.get("panes"), list):
            out.append(f"{label} panes is ignored: expected a list, found {type(g['panes']).__name__}")
        usable = 0
        for j, pane in enumerate(_as_list(g.get("panes")), start=1):
            if not isinstance(pane, dict):
                out.append(f"{label} pane {j} is ignored: not a mapping")
            elif not pane.get("project") and not pane.get("command"):
                out.append(f"{label} pane {j} is ignored: names neither a project nor a command")
            else:
                usable += 1
        if not usable:
            out.append(f"{label} is ignored: it has no usable pane, so its window will not exist")
    return out


def dropped_entries(raw: dict) -> list[str]:
    """What ``load_config`` silently leaves out of the file, as errors.

    The loader skips an entry that is not a mapping, a project without a name or path, a window
    without a name or command, a group without a name, a pane naming neither a project nor a
    command, a group left with no pane, and any section that is not a list. Skipping is right for
    launching a hand-edited file; it is also why a typo in one pane just made the window vanish, and
    a status check on the loaded config cannot see what was never loaded.
    """
    out: list[str] = []
    for section in ("projects", "status_windows", "groups", "alacritty_args"):
        value = raw.get(section)
        if value not in (None, []) and not isinstance(value, list):
            out.append(f"{section} is ignored: expected a list, found {type(value).__name__}")
    for i, e in enumerate(_as_list(raw.get("projects")), start=1):
        if not isinstance(e, dict):
            out.append(f"projects[{i}] is ignored: not a mapping")
        elif not e.get("name") or not e.get("path"):
            out.append(f"projects[{i}] is ignored: needs both name and path")
    for i, e in enumerate(_as_list(raw.get("status_windows")), start=1):
        if not isinstance(e, dict):
            out.append(f"status_windows[{i}] is ignored: not a mapping")
        elif not e.get("name") or not e.get("command"):
            out.append(f"status_windows[{i}] is ignored: needs both name and command")
    return out + _dropped_groups(raw)


def validate_file(path: Path | None = None) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for the config at ``path``: what the loader dropped, then what the
    loaded config cannot do. A file that cannot be read yields no per-entry findings; the caller
    reports that through ``config_file_state``."""
    import yaml

    config_path = path or DEFAULT_CONFIG_PATH
    errors: list[str] = []
    if config_file_state(config_path) == "ok":
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            errors = dropped_entries(raw)
    more_errors, warnings = validate_config(load_config(config_path))
    return [*errors, *more_errors], warnings


def validate_config(config: LauncherConfig) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for a config, without touching tmux.

    An error is something launch cannot honour: a pane naming a project that is not listed, a
    pane that names both or neither of ``project_ref`` / ``inline_command``, a group with no
    panes, or two groups or projects sharing a name (tmux window names and ``project_by_name``
    both resolve by name, so the second would be unreachable). Launch turns the first of these
    into a bash placeholder and carries on, which is why this exists: the mistake showed only
    after the windows were built. A warning is something launch will degrade, not refuse: a
    project directory that does not exist yet.
    """
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for proj in config.projects:
        if proj.name in seen:
            errors.append(f"project {proj.name!r} is listed more than once")
        seen.add(proj.name)
    known = {p.name for p in config.projects}
    groups_seen: set[str] = set()
    referenced: set[str] = set()
    for group in config.groups:
        if group.name in groups_seen:
            errors.append(f"group {group.name!r} appears more than once (its window name would collide)")
        groups_seen.add(group.name)
        if "." in group.name:
            errors.append(
                f"group {group.name!r} contains a '.', which tmux reads as a pane separator, so its window cannot be "
                "addressed; rename the group (the project name can stay)"
            )
        if not group.panes:
            errors.append(f"group {group.name!r} has no panes")
        for i, pane in enumerate(group.panes, start=1):
            where = f"group {group.name!r} pane {i}"
            if pane.project_ref and pane.inline_command:
                errors.append(f"{where} sets both project and command; use one")
            elif pane.project_ref:
                referenced.add(pane.project_ref)
                if pane.project_ref not in known:
                    errors.append(f"{where} names project {pane.project_ref!r}, which is not listed under projects")
            elif not pane.inline_command:
                errors.append(f"{where} names neither a project nor a command")
    for proj in config.projects:
        if (not config.groups or proj.name in referenced) and not os.path.isdir(os.path.expanduser(proj.path)):
            warnings.append(f"project {proj.name!r}: {proj.path} does not exist yet (launch leaves a placeholder pane)")
    return errors, warnings


def profile_path(profile: str) -> Path:
    """``~/.empirica/cockpit/config-NAME.yaml``. The name becomes a file name, so it is
    checked here, not trusted: no separators, no dots, no leading dash."""
    if not _PROFILE_NAME.fullmatch(profile or ""):
        raise ProfileError(f"profile name {profile!r} must be lowercase letters, digits, - or _")
    return Path.home() / ".empirica" / "cockpit" / f"config-{profile}.yaml"


def add_practice_to_profile(profile: str, name: str, path: str) -> tuple[Path, bool]:
    """Add one practice to a cockpit profile, creating the profile if it has none.

    Returns ``(file, changed)``. Idempotent: a practice already listed changes
    nothing. A new profile gets a ``monitor`` window running the TUI, then one
    window per practice (surface: tmux, so the layout opens where ``launch`` is run).

    An existing file is read strictly. ``load_config`` returns defaults for
    anything unreadable, which is right for launching and wrong here: writing
    those defaults back would replace a profile the user edited. So an unreadable
    or non-mapping file is refused, and a modified one is backed up first (the
    round trip drops YAML comments).
    """
    import yaml

    target = profile_path(profile)
    if target.exists():
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProfileError(f"{target} is not readable YAML ({exc}); fix or remove it, nothing was changed") from exc
        if not isinstance(raw, dict):
            raise ProfileError(f"{target} is not a YAML mapping; fix or remove it, nothing was changed")
        config = load_config(target)
    else:
        config = LauncherConfig(
            session_name=f"cockpit-{profile}",
            attach_on_launch=True,
            surface="tmux",
            groups=[GroupSpec(name="monitor", panes=[PaneSpec(inline_command="empirica tui", label="tui")])],
        )
    wanted = Path(path).expanduser()
    existing = config.project_by_name(name)
    if existing is not None:
        if os.path.realpath(os.path.expanduser(existing.path)) != os.path.realpath(wanted):
            # Idempotent means "same practice, same place". A different path under the same name is
            # a conflict the user has to settle; reporting "already there" would leave the stale path.
            raise ProfileError(
                f"{name!r} is already in {target} at {existing.path}, not {wanted}; "
                "edit or remove that entry, nothing was changed"
            )
        return target, False
    group_name = name.replace(".", "-")  # tmux reads '.' in a window target as a pane separator
    if any(g.name == group_name for g in config.groups):
        raise ProfileError(
            f"a window named {name!r} already exists in {target} (the default TUI window is 'monitor'); "
            "provision under another name, nothing was changed"
        )
    config.projects.append(ProjectSpec(name=name, path=str(wanted), launch="claude"))
    if config.is_groups_mode():
        config.groups.append(GroupSpec(name=group_name, panes=[PaneSpec(project_ref=name)]))
    # else: a projects-only profile opens one window per project on its own. Adding a group would
    # flip it to groups mode, where only the groups are windows and the existing projects vanish.

    header = (
        f"# Cockpit profile '{profile}': written by `empirica provision-practice --cockpit-profile {profile}`.\n"
        f"# Launch: empirica cockpit launch --profile {profile}    Guide: docs/guides/COCKPIT.md\n"
    )
    body = header + yaml.safe_dump(_serialize(config), default_flow_style=False, sort_keys=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_suffix(".yaml.bak")
        if target.exists() and not backup.exists():
            # The FIRST backup is the user's own version; a later run must not overwrite it with ours.
            backup.write_bytes(target.read_bytes())
        tmp = target.with_suffix(".yaml.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        raise ProfileError(f"could not write {target} ({exc}); nothing was changed") from exc
    return target, True


def write_default_config(
    path: Path | None = None,
    projects_root: Path | None = None,
) -> Path:
    """Write a default cockpit config.yaml based on detected projects.

    Returns the path written. Creates parent dirs if needed. Caller
    is responsible for confirming with the user before overwriting an
    existing file (this function does NOT check).
    """
    config_path = path or DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)

    projects = detect_projects(projects_root=projects_root)
    config = _builtin_default(projects=projects)

    import yaml

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(_serialize(config), fh, default_flow_style=False, sort_keys=False)
    return config_path
