"""Tests for the cockpit launcher (v1).

Per docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md. Covers config parsing,
state-file mtime semantics, and abnormal-exit detection. Tmux
integration is exercised via the existing integration suites — these
tests focus on the launcher's own logic.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from empirica.core.cockpit.launcher import (
    config as launcher_config,
)
from empirica.core.cockpit.launcher import (
    detection,
    state,
)


@pytest.fixture
def tmp_cockpit_dir(tmp_path, monkeypatch):
    """Redirect ~/.empirica/cockpit/ to a tmp dir + reload module path constants."""
    cockpit_dir = tmp_path / "cockpit"
    cockpit_dir.mkdir()
    monkeypatch.setattr(state, "COCKPIT_DIR", cockpit_dir)
    monkeypatch.setattr(state, "LAST_SESSION_START_PATH", cockpit_dir / "last_session_start")
    monkeypatch.setattr(state, "LAST_CLEAN_SHUTDOWN_PATH", cockpit_dir / "last_clean_shutdown")
    monkeypatch.setattr(state, "LOCK_PATH", cockpit_dir / "active.lock")
    return cockpit_dir


# ─── State helpers ───────────────────────────────────────────────────────


def test_write_session_start_creates_file(tmp_cockpit_dir):
    path = state.write_session_start()
    assert path.exists()
    assert int(path.read_text()) > 0


def test_write_clean_shutdown_clears_lock(tmp_cockpit_dir):
    state.write_lock(pid=12345)
    assert state.LOCK_PATH.exists()
    state.write_clean_shutdown()
    assert state.LAST_CLEAN_SHUTDOWN_PATH.exists()
    assert not state.LOCK_PATH.exists()


def test_pid_alive_for_self():
    assert state.pid_alive(os.getpid()) is True


def test_pid_alive_for_dead_pid():
    assert state.pid_alive(99999999) is False


def test_pid_alive_rejects_init():
    assert state.pid_alive(1) is False


def test_cockpit_status_snapshot(tmp_cockpit_dir):
    state.write_session_start()
    snap = state.cockpit_status()
    assert snap.last_session_start is not None
    assert snap.last_clean_shutdown is None
    assert snap.lock_pid is None
    assert snap.lock_alive is False


# ─── Abnormal-exit detection ─────────────────────────────────────────────


def test_no_session_history_returns_none(tmp_cockpit_dir):
    """Fresh install — never launched."""
    assert detection.detect_abnormal_exit() is None


def test_clean_shutdown_returns_none(tmp_cockpit_dir):
    """Last shutdown was clean (clean ≥ start)."""
    state.write_session_start()
    time.sleep(0.05)
    state.write_clean_shutdown()
    assert detection.detect_abnormal_exit() is None


def test_running_session_returns_already_running(tmp_cockpit_dir):
    """Start > clean AND lock PID alive → SessionAlreadyRunning."""
    state.write_session_start()
    state.write_lock(pid=os.getpid())  # current pid is alive
    result = detection.detect_abnormal_exit()
    assert isinstance(result, detection.SessionAlreadyRunning)
    assert result.pid == os.getpid()


def test_abnormal_exit_when_lock_pid_dead(tmp_cockpit_dir):
    """Start > clean AND lock PID dead → AbnormalExit."""
    state.write_session_start()
    state.write_lock(pid=99999999)  # dead pid
    result = detection.detect_abnormal_exit()
    assert isinstance(result, detection.AbnormalExit)
    assert result.started_at is not None
    assert result.likely_cause in ("reboot", "unknown")


def test_abnormal_exit_when_no_lock(tmp_cockpit_dir):
    """Start > clean AND no lock file at all → AbnormalExit (process died
    before writing the lock, or after the lock was cleared by a partial
    cleanup)."""
    state.write_session_start()
    # No write_lock call
    result = detection.detect_abnormal_exit()
    assert isinstance(result, detection.AbnormalExit)


# ─── Config parsing ──────────────────────────────────────────────────────


def test_load_config_returns_defaults_when_missing(tmp_path):
    config = launcher_config.load_config(path=tmp_path / "nope.yaml")
    assert config.session_name == "cockpit"
    assert config.attach_on_launch is True
    assert config.projects == []


def test_load_config_parses_full_yaml(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
session_name: my-cockpit
attach_on_launch: false
projects:
  - name: alpha
    path: /tmp/alpha
    launch: claude
  - name: beta
    path: /tmp/beta
    launch: claude --dangerously-skip-permissions
status_windows:
  - name: monitor
    command: watch empirica status
on_abnormal_exit:
  warn: false
  auto_prune_dead: true
  notify: false
""")
    config = launcher_config.load_config(path=yaml_path)
    assert config.session_name == "my-cockpit"
    assert config.attach_on_launch is False
    assert len(config.projects) == 2
    assert config.projects[0].name == "alpha"
    assert config.projects[1].launch == "claude --dangerously-skip-permissions"
    assert len(config.status_windows) == 1
    assert config.status_windows[0].name == "monitor"
    assert config.warn_on_abnormal_exit is False
    assert config.auto_prune_dead is True


def test_detect_projects_finds_empirica_dirs(tmp_path):
    """A directory qualifies if it has a .empirica/ subdir."""
    root = tmp_path / "projects"
    root.mkdir()
    (root / "has-empirica").mkdir()
    (root / "has-empirica" / ".empirica").mkdir()
    (root / "no-empirica").mkdir()
    (root / "also-empirica").mkdir()
    (root / "also-empirica" / ".empirica").mkdir()

    found = launcher_config.detect_projects(projects_root=root)
    names = sorted(p.name for p in found)
    assert names == ["also-empirica", "has-empirica"]


def test_detect_projects_handles_missing_root():
    found = launcher_config.detect_projects(projects_root=Path("/nonexistent/path"))
    assert found == []


def test_write_default_config_round_trips(tmp_path):
    config_path = tmp_path / "cockpit" / "config.yaml"
    written = launcher_config.write_default_config(
        path=config_path,
        projects_root=Path("/nonexistent"),  # no projects detected
    )
    assert written == config_path
    assert config_path.exists()
    # Should re-load cleanly
    reloaded = launcher_config.load_config(path=config_path)
    assert reloaded.session_name == "cockpit"
    assert reloaded.status_windows  # has the default monitor window


# ─── Groups mode (alacritty surface) ─────────────────────────────────────


def test_groups_mode_parses_full_yaml(tmp_path):
    """Groups + panes + surface override + alacritty_args round-trip."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
session_name: my-cockpit
projects:
  - name: empirica
    path: /home/user/empirica
  - name: outreach
    path: /home/user/outreach
groups:
  - name: main
    panes:
      - {project: empirica}
      - {project: outreach}
  - name: cockpit
    split: vertical
    panes:
      - {project: empirica, label: empirica-extra}
      - {command: empirica cockpit, label: cockpit-tui}
surface: alacritty
alacritty_args: ['--option', 'font.size=11']
""")
    config = launcher_config.load_config(path=yaml_path)
    assert config.is_groups_mode()
    assert config.surface == "alacritty"
    assert config.alacritty_args == ["--option", "font.size=11"]
    assert len(config.groups) == 2

    main = config.groups[0]
    assert main.name == "main"
    assert main.split == "horizontal"  # default
    assert len(main.panes) == 2
    assert main.panes[0].project_ref == "empirica"
    assert main.panes[1].project_ref == "outreach"

    cockpit = config.groups[1]
    assert cockpit.split == "vertical"
    assert cockpit.panes[1].inline_command == "empirica cockpit"
    assert cockpit.panes[1].label == "cockpit-tui"

    # Project lookup by name
    assert config.project_by_name("empirica").path == "/home/user/empirica"
    assert config.project_by_name("does-not-exist") is None


def test_groups_mode_back_compat_no_groups(tmp_path):
    """Without ``groups:``, surface defaults to 'tmux' (legacy)."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
session_name: legacy
projects:
  - name: alpha
    path: /tmp/alpha
""")
    config = launcher_config.load_config(path=yaml_path)
    assert not config.is_groups_mode()
    assert config.surface == "tmux"
    assert config.groups == []


def test_groups_mode_pane_filtering(tmp_path):
    """Empty/malformed panes are dropped silently."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("""
groups:
  - name: g1
    panes:
      - {project: real}
      - {}                        # no project / no command — drop
      - "string-not-dict"         # malformed — drop
      - {project: another}
  - name: g2
    panes: []                     # empty — group dropped
  - {}                            # no name — drop
""")
    config = launcher_config.load_config(path=yaml_path)
    assert len(config.groups) == 1
    assert config.groups[0].name == "g1"
    assert len(config.groups[0].panes) == 2


def test_groups_mode_serialize_round_trip(tmp_path):
    """Write a groups-mode config, reload, verify equivalence."""
    config_path = tmp_path / "cockpit" / "config.yaml"
    config_path.parent.mkdir(parents=True)

    # Build manually then write via internal serializer
    cfg = launcher_config.LauncherConfig(
        session_name="test",
        surface="alacritty",
        projects=[launcher_config.ProjectSpec(name="p1", path="/tmp/p1")],
        groups=[
            launcher_config.GroupSpec(
                name="g1",
                panes=[
                    launcher_config.PaneSpec(project_ref="p1"),
                    launcher_config.PaneSpec(inline_command="echo hello"),
                ],
                split="horizontal",
            )
        ],
    )
    import yaml as _yaml

    config_path.write_text(_yaml.safe_dump(launcher_config._serialize(cfg), sort_keys=False))

    reloaded = launcher_config.load_config(path=config_path)
    assert reloaded.surface == "alacritty"
    assert len(reloaded.groups) == 1
    g = reloaded.groups[0]
    assert g.name == "g1"
    assert g.panes[0].project_ref == "p1"
    assert g.panes[1].inline_command == "echo hello"


def test_alacritty_available_check():
    """Smoke check — alacritty is present on the dev machine and CI image
    respectively, but the function shouldn't raise."""
    from empirica.core.cockpit.launcher import alacritty_available

    result = alacritty_available()
    assert isinstance(result, bool)


def test_handle_groups_launch_no_alacritty(tmp_cockpit_dir, capsys, monkeypatch):
    """If alacritty isn't on PATH, the groups handler errors out cleanly
    and points the operator at the legacy tmux surface fallback."""
    from empirica.cli.command_handlers.cockpit_launcher_commands import _handle_groups_launch
    from empirica.core.cockpit.launcher import (
        GroupSpec,
        LauncherConfig,
        PaneSpec,
        ProjectSpec,
    )
    from empirica.core.cockpit.launcher import tmux as launcher_tmux

    monkeypatch.setattr(launcher_tmux, "alacritty_available", lambda: False)
    # Also patch the re-export the handler imports through
    from empirica.cli.command_handlers import cockpit_launcher_commands as cmds

    monkeypatch.setattr(cmds, "alacritty_available", lambda: False)

    config = LauncherConfig(
        groups=[GroupSpec(name="g1", panes=[PaneSpec(project_ref="p1")])],
        projects=[ProjectSpec(name="p1", path="/tmp/p1")],
        surface="alacritty",
    )
    rc = _handle_groups_launch(config, output="json", quiet=True)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "alacritty not found" in out["error"].lower()


def test_ghostty_available_check():
    """Smoke check — shouldn't raise regardless of whether ghostty is
    actually installed on the machine running the test."""
    from empirica.core.cockpit.launcher import ghostty_available

    result = ghostty_available()
    assert isinstance(result, bool)


def test_handle_groups_launch_no_ghostty(tmp_cockpit_dir, capsys, monkeypatch):
    """Mirrors test_handle_groups_launch_no_alacritty for the ghostty
    surface — must check ghostty_available(), not alacritty_available()."""
    from empirica.cli.command_handlers.cockpit_launcher_commands import _handle_groups_launch
    from empirica.core.cockpit.launcher import (
        GroupSpec,
        LauncherConfig,
        PaneSpec,
        ProjectSpec,
    )
    from empirica.cli.command_handlers import cockpit_launcher_commands as cmds

    monkeypatch.setattr(cmds, "ghostty_available", lambda: False)

    config = LauncherConfig(
        groups=[GroupSpec(name="g1", panes=[PaneSpec(project_ref="p1")])],
        projects=[ProjectSpec(name="p1", path="/tmp/p1")],
        surface="ghostty",
    )
    rc = _handle_groups_launch(config, output="json", quiet=True)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "ghostty not found" in out["error"].lower()


def test_wm_class_for_surface():
    """Ghostty needs reverse-domain-name app-ids (GTK rule); alacritty
    accepts a bare string. Must match what _spawn_ghostty/_spawn_alacritty
    actually pass — this is display-only, not the source of truth."""
    from empirica.cli.command_handlers.cockpit_launcher_commands import _wm_class_for

    assert _wm_class_for("ghostty", "core") == "com.empirica.cockpit.core"
    assert _wm_class_for("alacritty", "core") == "empirica-core"
    assert _wm_class_for("tmux", "core") == "empirica-core"  # falls back to alacritty-style


# ─── CLI handlers (no-tmux paths) ────────────────────────────────────────


def test_status_handler_returns_clean_state(tmp_cockpit_dir, capsys, monkeypatch):
    """`empirica cockpit status` works on a fresh machine (no state files,
    no tmux session)."""
    from empirica.core.cockpit.launcher import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", tmp_cockpit_dir / "config.yaml")

    from empirica.cli.command_handlers.cockpit_launcher_commands import (
        handle_cockpit_status_command,
    )

    args = SimpleNamespace(config=None, output="json")
    rc = handle_cockpit_status_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["session_live"] is False
    assert out["last_session_start"] == "never"


# NOTE: removed test_detach_handler_writes_clean_marker.
# It exercised handle_cockpit_detach_command which subshells
# `tmux detach-client` with no target — under any TMUX-leak in CI / a
# user setup it detached the dev's live tmux client. Mocking the
# subprocess made it safe but added maintenance + future-drift risk.
# The handler's actual logic (marker write + JSON wrapper) is trivial;
# the marker-write itself is already covered by
# test_write_clean_shutdown_clears_lock above. Better to have zero tests
# that can ever shell tmux than to defend a mock forever.
