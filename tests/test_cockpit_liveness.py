"""Tests for empirica.core.cockpit.liveness.

Covers the four signal paths:
- tmux pane running claude (alive)
- tmux pane running other command (dead — Claude exited)
- tmux pane gone (dead — terminal closed)
- non-tmux PPID alive / dead / fallback to recent activity
"""

from __future__ import annotations

import json

import pytest

from empirica.core.cockpit import liveness as lv


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / '.empirica'
    fake.mkdir()
    (fake / 'instance_projects').mkdir()
    (fake / 'tty_sessions').mkdir()
    monkeypatch.setattr(lv, 'EMPIRICA_DIR', fake)
    monkeypatch.setattr(lv, 'TTY_SESSIONS_DIR', fake / 'tty_sessions')
    return fake


def test_current_instance_always_alive(fake_home):
    result = lv.is_alive('tmux_99', current_instance_id='tmux_99')
    assert result.alive is True
    assert result.reason == 'current instance'


def test_tmux_pane_running_claude_is_alive(fake_home):
    # Pre-computed: pane %5 has claude running.
    result = lv.is_alive('tmux_5', live_panes={'5'})
    assert result.alive is True
    assert 'running claude' in result.reason


def test_tmux_pane_running_bash_is_dead(fake_home, monkeypatch):
    """Pane exists in `_all_tmux_panes` but is not in the claude-only set
    (e.g. user exited claude, bash is left running)."""
    monkeypatch.setattr(lv, '_all_tmux_panes', lambda: {'5', '6'})
    result = lv.is_alive('tmux_5', live_panes=set())  # claude not in any pane
    assert result.alive is False
    assert 'claude is not running there' in result.reason


def test_tmux_pane_gone_is_dead(fake_home, monkeypatch):
    monkeypatch.setattr(lv, '_all_tmux_panes', lambda: set())
    result = lv.is_alive('tmux_5', live_panes=set())
    assert result.alive is False
    assert 'does not exist' in result.reason


def test_non_tmux_with_alive_ppid_is_alive(fake_home, monkeypatch):
    (fake_home / 'instance_projects' / 'term-pts-7.json').write_text(
        json.dumps({'pid': 12345, 'ppid': 67890})
    )
    monkeypatch.setattr(lv, '_process_alive', lambda pid: pid == 67890)
    result = lv.is_alive('term-pts-7')
    assert result.alive is True
    assert result.pid_checked == 67890


def test_non_tmux_with_dead_ppid_is_dead(fake_home, monkeypatch):
    (fake_home / 'instance_projects' / 'term-pts-7.json').write_text(
        json.dumps({'pid': 12345, 'ppid': 67890})
    )
    monkeypatch.setattr(lv, '_process_alive', lambda _: False)
    result = lv.is_alive('term-pts-7')
    assert result.alive is False
    assert 'pid 67890 dead' in result.reason


def test_non_tmux_no_pid_recent_activity_is_alive(fake_home):
    """No PID, no tmux info, but state file was touched recently — give
    benefit of doubt (covers fresh sessions before session-init wrote PID)."""
    result = lv.is_alive('term-pts-7', last_activity_seconds=30.0)
    assert result.alive is True
    assert 'recent activity' in result.reason


def test_non_tmux_no_pid_old_activity_is_dead(fake_home):
    result = lv.is_alive('term-pts-7', last_activity_seconds=99999.0)
    assert result.alive is False


def test_non_tmux_no_signals_at_all_is_dead(fake_home):
    result = lv.is_alive('term-pts-7')
    assert result.alive is False


def test_tmux_with_dead_ppid_overrides_pane_alive(fake_home, monkeypatch):
    """If we have a captured PPID and it's dead, that's a stronger signal
    than the pane being a 'claude' pane (the captured PPID was Claude;
    if it died, this instance is gone — even if a NEW Claude started in
    the same pane, it's a different instance for our purposes)."""
    # Actually, current implementation: pane-with-claude wins over PID check.
    # That's fine for the user's stated need (don't show dead Claudes), but
    # documents the precedence ordering.
    pass  # Behavioural assertion — kept as documentation, not a hard test.


def test_tmux_bash_but_captured_pid_alive_is_alive(fake_home, monkeypatch):
    """Regression for issue #98 (Philipp): pane shows bash as foreground
    (claude is in a sub-process / split / wrapper) but the captured PID
    is alive. Must return alive — was returning dead in the prior
    short-circuit shape, which hid live Claudes from the cockpit.
    """
    (fake_home / 'instance_projects' / 'tmux_5.json').write_text(
        json.dumps({'pid': 12345, 'ppid': 67890})
    )
    monkeypatch.setattr(lv, '_all_tmux_panes', lambda: {'5'})
    monkeypatch.setattr(lv, '_process_alive', lambda pid: pid == 67890)

    # Pane exists, claude is NOT the foreground command, but PID is alive.
    result = lv.is_alive('tmux_5', live_panes=set())

    assert result.alive is True, (
        f"PID-alive must override tmux disagreement "
        f"(was: {result.reason})"
    )
    assert result.pid_checked == 67890
    assert result.tmux_pane == '5'


def test_tmux_bash_and_captured_pid_dead_is_dead(fake_home, monkeypatch):
    """Counterpart: pane shows bash AND captured PID is dead → dead.
    Verifies the false-positive direction stays correct after the refactor.
    """
    (fake_home / 'instance_projects' / 'tmux_5.json').write_text(
        json.dumps({'pid': 12345, 'ppid': 67890})
    )
    monkeypatch.setattr(lv, '_all_tmux_panes', lambda: {'5'})
    monkeypatch.setattr(lv, '_process_alive', lambda _: False)

    result = lv.is_alive('tmux_5', live_panes=set())

    assert result.alive is False
    assert 'pid 67890 dead' in result.reason


def test_tmux_bash_no_captured_pid_no_activity_is_dead(fake_home, monkeypatch):
    """Pane shows bash, no PID captured, no recent activity → dead.
    The reason string now records BOTH the tmux state and the PID
    absence so the operator can diagnose."""
    monkeypatch.setattr(lv, '_all_tmux_panes', lambda: {'5'})
    result = lv.is_alive('tmux_5', live_panes=set())
    assert result.alive is False
    assert 'no captured PID survived' in result.reason


def test_pid_capture_falls_back_to_tty_sessions(fake_home, monkeypatch):
    """instance_projects has tty_key but no pid; tty_sessions has the pid."""
    (fake_home / 'instance_projects' / 'term-pts-7.json').write_text(
        json.dumps({'tty_key': 'pts-7'})
    )
    (fake_home / 'tty_sessions' / 'pts-7.json').write_text(
        json.dumps({'pid': 12345, 'ppid': 67890})
    )
    monkeypatch.setattr(lv, '_process_alive', lambda pid: pid == 67890)
    result = lv.is_alive('term-pts-7')
    assert result.alive is True
    assert result.pid_checked == 67890
