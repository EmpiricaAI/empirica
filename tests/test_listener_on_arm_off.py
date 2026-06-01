"""Tests for `empirica listener on/arm/off` (prop_oxrhoehv4zeelbi2pjvjv2wfum).

AI-ergonomic facade — 3 new verbs that collapse the multi-step in-session
arming protocol to single tool calls. The 9 power-user verbs stay
untouched.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import patch

from empirica.cli.command_handlers.cockpit_commands import (
    _resolve_canonical_ai_id,
    handle_listener_arm_command,
    handle_listener_off_command,
    handle_listener_on_command,
)


def _make_args(**overrides):
    defaults = {
        'ai_id': None,
        'name': None,
        'topic': None,
        'instance': 'test_instance',
        'task_id': None,
        'output': 'json',
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


# ─── _resolve_canonical_ai_id ─────────────────────────────────────────


def test_resolve_canonical_ai_id_uses_explicit_flag():
    args = _make_args(ai_id='explicit-ai')
    assert _resolve_canonical_ai_id(args) == 'explicit-ai'


def test_resolve_canonical_ai_id_returns_none_when_unresolvable():
    args = _make_args(ai_id=None)
    with patch('empirica.utils.session_resolver.InstanceResolver.ai_id',
               return_value=None):
        assert _resolve_canonical_ai_id(args) is None


def test_resolve_canonical_ai_id_falls_back_to_resolver():
    args = _make_args(ai_id=None)
    with patch('empirica.utils.session_resolver.InstanceResolver.ai_id',
               return_value='resolved-ai'):
        assert _resolve_canonical_ai_id(args) == 'resolved-ai'


# ─── on: short-circuit on persistent service ──────────────────────────


def test_on_persistent_service_returns_tail_monitor(tmp_path, monkeypatch, capsys):
    """When persistent service is up, `on` returns a tail-Monitor command
    that bridges loop_fires.log into this session — NOT 'no Monitor needed'
    (which was the Phase-3 wake-delivery gap).

    The tail-Monitor doesn't spawn a duplicate ntfy curl; it just tails
    the log the persistent service writes to.
    """
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='empirica')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=True,
    ):
        rc = handle_listener_on_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is True
    assert out['status'] == 'persistent_service_tail_session'
    ns = out['next_step']
    assert ns is not None, 'next_step must include a Monitor command — the persistent service alone does not deliver wakes into the session'
    assert ns['tool'] == 'Monitor'
    cmd = ns['args']['command']
    assert 'tail -F' in cmd
    assert 'loop_fires.log' in cmd
    # Filter scopes the tail to events for this ai_id
    assert '"instance_id": "empirica"' in cmd
    # Crucially, no duplicate ntfy subscriber
    assert 'empirica loop listen' not in cmd
    assert 'curl' not in cmd
    assert ns['args']['persistent'] is True
    assert 'after_arm' in ns


def test_on_persistent_service_writes_tail_mode_state_file(tmp_path, monkeypatch):
    """Tail-mode state file is written with mode='tail' so `listener off`
    can distinguish the cleanup path later."""
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='empirica')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=True,
    ):
        handle_listener_on_command(args)
    # Default instance from _make_args is 'test_instance'
    state_file = tmp_path / '.empirica' / 'listener_active_test_instance_empirica-inbox.json'
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state['mode'] == 'tail'
    assert state['ai_id'] == 'empirica'
    assert state['monitor_task_id'] is None  # filled by `arm`


def test_on_arms_in_session_when_no_persistent_service(tmp_path, monkeypatch):
    """No persistent service → register + write state file + emit Monitor next_step."""
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='myai')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        rc = handle_listener_on_command(args)
    assert rc == 0


def test_on_emits_monitor_next_step_with_correct_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='myai')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        handle_listener_on_command(args)
    out = json.loads(capsys.readouterr().out)
    ns = out['next_step']
    assert ns['tool'] == 'Monitor'
    # Standalone Monitor wraps the listener spawn in a supervisor loop —
    # the listener's design assumes a relauncher (systemd/launchd) on its
    # intentional clean exits (SIGTERM during reconnect, ListenerUpgraded
    # on drift); Claude Code's Monitor isn't a supervisor, so the wrapper
    # provides those semantics by default. cf. cockpit_commands.py L1647+
    # + cortex prop_6kevxb63 (the SIGTERM-during-reconnect finding).
    assert ns['args']['command'] == (
        'while true; do empirica loop listen --instance myai; sleep 3; done'
    )
    assert ns['args']['persistent'] is True
    assert 'after_arm' in ns
    assert 'empirica listener arm' in ns['after_arm']


def test_on_default_name_is_ai_id_inbox(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='cortex')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        handle_listener_on_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out['name'] == 'cortex-inbox'


def test_on_default_topic_includes_canonical_ntfy_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='cortex')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ), patch(
        'empirica.core.cockpit.notification_channels.resolve_orchestration_events_topic',
        return_value='ntfy:empirica-orchestration-events?tags=cortex',
    ):
        handle_listener_on_command(args)
    out = json.loads(capsys.readouterr().out)
    # Default topic is the per-org-prefixed canonical wake topic (NOT the
    # deprecated bare 'orchestration-events', which has no ACL grant).
    assert out['topic'] == 'ntfy:empirica-orchestration-events?tags=cortex'


def test_on_writes_placeholder_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='myai')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        handle_listener_on_command(args)
    state_file = tmp_path / '.empirica' / 'listener_active_test_instance_myai-inbox.json'
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data['monitor_task_id'] is None  # placeholder
    assert data['ai_id'] == 'myai'
    assert data['mode'] == 'standalone'  # distinguishes from tail-mode


def test_on_errors_when_ai_id_unresolved(capsys):
    args = _make_args(ai_id=None)
    with patch('empirica.utils.session_resolver.InstanceResolver.ai_id',
               return_value=None):
        rc = handle_listener_on_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is False
    assert 'ai_id unresolved' in out['error']


# ─── arm: state file update ───────────────────────────────────────────


def test_arm_replaces_placeholder_task_id(tmp_path, monkeypatch, capsys):
    """arm <task_id> updates the state file's monitor_task_id field."""
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    # Set up placeholder via `on`
    args_on = _make_args(ai_id='myai')
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        handle_listener_on_command(args_on)
    capsys.readouterr()  # drain
    # Now arm
    args_arm = _make_args(ai_id='myai', task_id='tk_abc123')
    rc = handle_listener_arm_command(args_arm)
    assert rc == 0
    state_file = tmp_path / '.empirica' / 'listener_active_test_instance_myai-inbox.json'
    data = json.loads(state_file.read_text())
    assert data['monitor_task_id'] == 'tk_abc123'


def test_arm_errors_when_no_state_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    args = _make_args(ai_id='myai', task_id='tk_xyz')
    rc = handle_listener_arm_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert 'no active state file' in out['error']


def test_arm_errors_without_task_id(capsys):
    args = _make_args(ai_id='myai', task_id=None)
    rc = handle_listener_arm_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert 'task_id required' in out['error']


def test_arm_errors_when_name_unresolved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    args = _make_args(ai_id=None, name=None, task_id='tk_x')
    with patch('empirica.utils.session_resolver.InstanceResolver.ai_id',
               return_value=None):
        rc = handle_listener_arm_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert 'name unresolved' in out['error']


# ─── off: TaskStop + unregister next_step ─────────────────────────────


def test_off_emits_task_stop_when_armed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    # Arm a listener via on + arm
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        handle_listener_on_command(_make_args(ai_id='myai'))
    handle_listener_arm_command(_make_args(ai_id='myai', task_id='tk_999'))
    capsys.readouterr()
    # Now off
    rc = handle_listener_off_command(_make_args(ai_id='myai'))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ns = out['next_step']
    assert ns['tool'] == 'TaskStop'
    assert ns['args']['task_id'] == 'tk_999'
    assert 'empirica listener unregister myai-inbox' in ns['after_stop']


def test_off_handles_no_state_file_gracefully(tmp_path, monkeypatch, capsys):
    """No state file means not_armed — emit unregister-only next_step."""
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    args = _make_args(ai_id='myai')
    rc = handle_listener_off_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'not_armed'
    assert 'empirica listener unregister' in out['next_step']['after_stop']


def test_off_handles_placeholder_task_id(tmp_path, monkeypatch, capsys):
    """on without arm → state file has null task_id → off emits unregister-only."""
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        handle_listener_on_command(_make_args(ai_id='myai'))
    capsys.readouterr()
    rc = handle_listener_off_command(_make_args(ai_id='myai'))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # No TaskStop emitted because task_id is null
    assert out['next_step']['tool'] is None
    assert 'unregister' in out['next_step']['after_stop']


def test_off_errors_when_name_unresolved(capsys):
    args = _make_args(ai_id=None, name=None)
    with patch('empirica.utils.session_resolver.InstanceResolver.ai_id',
               return_value=None):
        rc = handle_listener_off_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert 'name unresolved' in out['error']


# ─── End-to-end flow ──────────────────────────────────────────────────


def test_on_arm_off_full_cycle(tmp_path, monkeypatch, capsys):
    """Full lifecycle: on → arm → off should produce coherent state transitions."""
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    state_file = tmp_path / '.empirica' / 'listener_active_test_instance_myai-inbox.json'

    # 1. on
    with patch(
        'empirica.core.loop_scheduler.persistent_listener.is_listener_running',
        return_value=False,
    ):
        rc1 = handle_listener_on_command(_make_args(ai_id='myai'))
    assert rc1 == 0
    assert state_file.exists()
    assert json.loads(state_file.read_text())['monitor_task_id'] is None
    capsys.readouterr()

    # 2. arm
    rc2 = handle_listener_arm_command(_make_args(ai_id='myai', task_id='tk_777'))
    assert rc2 == 0
    assert json.loads(state_file.read_text())['monitor_task_id'] == 'tk_777'
    capsys.readouterr()

    # 3. off — emits TaskStop(tk_777) + after_stop=unregister
    rc3 = handle_listener_off_command(_make_args(ai_id='myai'))
    assert rc3 == 0
    out = json.loads(capsys.readouterr().out)
    assert out['next_step']['tool'] == 'TaskStop'
    assert out['next_step']['args']['task_id'] == 'tk_777'
