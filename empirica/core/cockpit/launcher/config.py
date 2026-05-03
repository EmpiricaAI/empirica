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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / '.empirica' / 'cockpit' / 'config.yaml'
DEFAULT_PROJECTS_ROOT = Path.home() / 'empirical-ai'


@dataclass
class ProjectSpec:
    """One tmux window in the cockpit — typically one project."""
    name: str
    path: str
    launch: str = 'claude'   # command to run in this window
    kind: str = 'code'       # placeholder for future split / pane semantics


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
    label: str | None = None     # optional human-readable pane title


@dataclass
class GroupSpec:
    """One alacritty window = one tmux session with N panes (default 2).

    Each group becomes a separate alacritty window with its own
    ``WM_CLASS=empirica-<name>`` so KDE/wmctrl can target it individually
    (Meta+1..N for taskbar slots once pinned).
    """
    name: str
    panes: list[PaneSpec] = field(default_factory=list)
    split: str = 'horizontal'    # 'horizontal' (left/right) | 'vertical' (top/bottom)


@dataclass
class LauncherConfig:
    """Loaded cockpit config.

    Two layout modes (mutually compatible — groups wins when present):

    1. **Legacy (single session)**: ``projects`` + ``status_windows`` →
       one tmux session named ``session_name`` with one window per
       project. Single ``tmux attach`` in the launching terminal.

    2. **Groups (multi-window)**: ``groups`` → one tmux session per
       group, one alacritty window per group, panes per group. Each
       group's alacritty gets a unique WM_CLASS for keyboard-shortcut
       window switching (Meta+1..N in KDE).
    """
    session_name: str = 'cockpit'
    attach_on_launch: bool = True
    projects: list[ProjectSpec] = field(default_factory=list)
    status_windows: list[StatusWindow] = field(default_factory=list)
    groups: list[GroupSpec] = field(default_factory=list)
    surface: str = 'tmux'        # 'tmux' (legacy single attach) | 'alacritty' (groups mode)
    alacritty_args: list[str] = field(default_factory=list)
    warn_on_abnormal_exit: bool = True
    auto_prune_dead: bool = False
    notify_on_abnormal_exit: bool = True

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
        session_name='cockpit',
        attach_on_launch=True,
        projects=projects or [],
        status_windows=[
            StatusWindow(
                name='monitor',
                command='watch -n 2 empirica status --all --pretty',
            ),
        ],
        warn_on_abnormal_exit=True,
        auto_prune_dead=False,
        notify_on_abnormal_exit=True,
    )


def detect_projects(projects_root: Path | None = None) -> list[ProjectSpec]:
    """Discover candidate projects under ``~/empirical-ai/``.

    A directory qualifies if it has a ``.empirica/`` subdirectory.
    The launch command defaults to ``claude``.
    """
    root = projects_root or DEFAULT_PROJECTS_ROOT
    if not root.exists() or not root.is_dir():
        return []
    discovered: list[ProjectSpec] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / '.empirica').is_dir():
            discovered.append(ProjectSpec(
                name=entry.name,
                path=str(entry.resolve()),
                launch='claude',
                kind='code',
            ))
    return discovered


def _parse_groups(raw_groups: list) -> list[GroupSpec]:
    """Parse the optional ``groups:`` section of the launcher YAML."""
    groups: list[GroupSpec] = []
    for entry in raw_groups:
        if not isinstance(entry, dict):
            continue
        gname = entry.get('name')
        if not gname:
            continue
        panes: list[PaneSpec] = []
        for pane in entry.get('panes') or []:
            if not isinstance(pane, dict):
                continue
            project_ref = pane.get('project')
            inline = pane.get('command')
            if not project_ref and not inline:
                continue
            panes.append(PaneSpec(
                project_ref=str(project_ref) if project_ref else None,
                inline_command=str(inline) if inline else None,
                label=str(pane.get('label')) if pane.get('label') else None,
            ))
        if not panes:
            continue
        groups.append(GroupSpec(
            name=str(gname),
            panes=panes,
            split=str(entry.get('split') or 'horizontal'),
        ))
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
        with config_path.open(encoding='utf-8') as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception:
        return _builtin_default()

    if not isinstance(raw, dict):
        return _builtin_default()

    projects = []
    for entry in raw.get('projects') or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        path = entry.get('path')
        if not name or not path:
            continue
        projects.append(ProjectSpec(
            name=str(name),
            path=str(path),
            launch=str(entry.get('launch') or 'claude'),
            kind=str(entry.get('kind') or 'code'),
        ))

    status_windows = []
    for entry in raw.get('status_windows') or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        command = entry.get('command')
        if not name or not command:
            continue
        status_windows.append(StatusWindow(name=str(name), command=str(command)))

    groups = _parse_groups(raw.get('groups') or [])

    abnormal = raw.get('on_abnormal_exit') or {}
    surface = str(raw.get('surface') or ('alacritty' if groups else 'tmux'))
    alacritty_args_raw = raw.get('alacritty_args') or []
    alacritty_args = [str(a) for a in alacritty_args_raw if a]

    return LauncherConfig(
        session_name=str(raw.get('session_name') or 'cockpit'),
        attach_on_launch=bool(raw.get('attach_on_launch', True)),
        projects=projects,
        status_windows=status_windows,
        groups=groups,
        surface=surface,
        alacritty_args=alacritty_args,
        warn_on_abnormal_exit=bool(abnormal.get('warn', True)),
        auto_prune_dead=bool(abnormal.get('auto_prune_dead', False)),
        notify_on_abnormal_exit=bool(abnormal.get('notify', True)),
    )


def _serialize(config: LauncherConfig) -> dict[str, Any]:
    out: dict[str, Any] = {
        'session_name': config.session_name,
        'attach_on_launch': config.attach_on_launch,
        'surface': config.surface,
        'projects': [
            {'name': p.name, 'path': p.path, 'launch': p.launch, 'kind': p.kind}
            for p in config.projects
        ],
        'status_windows': [
            {'name': w.name, 'command': w.command}
            for w in config.status_windows
        ],
        'on_abnormal_exit': {
            'warn': config.warn_on_abnormal_exit,
            'auto_prune_dead': config.auto_prune_dead,
            'notify': config.notify_on_abnormal_exit,
        },
    }
    if config.groups:
        out['groups'] = [
            {
                'name': g.name,
                'split': g.split,
                'panes': [
                    {
                        k: v for k, v in {
                            'project': p.project_ref,
                            'command': p.inline_command,
                            'label': p.label,
                        }.items() if v is not None
                    }
                    for p in g.panes
                ],
            }
            for g in config.groups
        ]
    if config.alacritty_args:
        out['alacritty_args'] = config.alacritty_args
    return out


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
    with config_path.open('w', encoding='utf-8') as fh:
        yaml.safe_dump(_serialize(config), fh, default_flow_style=False, sort_keys=False)
    return config_path
