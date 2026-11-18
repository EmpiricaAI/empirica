"""The cockpit's Events toggle resumes a systemd loop that `loop disable` turned off.

The toggle decided pause-vs-resume from the sidecar `paused` flag, which
`loop disable` / `loop enable` never touch (they flip the systemd timer). A
disabled systemd loop therefore read as running, and every press disabled it
again: the toggle could never turn it back on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from empirica.cli.tui import cockpit_app
from empirica.cli.tui.cockpit_app import CockpitApp


def _app_with(inst, monkeypatch):
    app = CockpitApp()
    monkeypatch.setattr(app, "_require_selected", lambda: inst)
    monkeypatch.setattr(app, "_log_status", lambda msg: None)
    monkeypatch.setattr(app, "refresh_payload", lambda: None)
    enable, disable = MagicMock(), MagicMock()
    monkeypatch.setattr(cockpit_app, "handle_loop_enable_command", enable)
    monkeypatch.setattr(cockpit_app, "handle_loop_disable_command", disable)
    return app, enable, disable


def _inst(systemd_active: bool):
    return {
        "instance_id": "tmux_1",
        "ai_id": "empirica",
        "project_path": "/nonexistent",
        "loops": {
            "cortex-mailbox-poll": {
                "scheduling": {"scheduler_kind": "systemd-user"},
                "systemd_active": systemd_active,
                "paused": False,
                "interval": "30s",
            }
        },
        "listeners": {},
    }


def test_disabled_systemd_loop_is_enabled_by_the_toggle(monkeypatch):
    app, enable, disable = _app_with(_inst(systemd_active=False), monkeypatch)
    app.action_toggle_events()
    enable.assert_called_once()
    disable.assert_not_called()


def test_active_systemd_loop_is_disabled_by_the_toggle(monkeypatch):
    app, enable, disable = _app_with(_inst(systemd_active=True), monkeypatch)
    app.action_toggle_events()
    disable.assert_called_once()
    enable.assert_not_called()
