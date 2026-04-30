"""Tests for ListenerRegistry — sister to LoopRegistry, event-driven.

Covers item 2 of PROPOSAL_EVENT_LISTENER.md: the registry + CLI surface
for event listeners. State files mirror loops_<instance>.json /
loop_paused_<instance>_<name>.

Item 4 — mechanical Monitor-kill on pause via install-request analog —
is deferred. Pause for V1 is an advisory flag; the listener body's
pause check at next wake is the backstop.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from empirica.core.cockpit import (
    ListenerEntry,
    ListenerRegistry,
    is_listener_paused,
    set_listener_paused,
)
from empirica.core.cockpit import listener_registry as lr_mod
from empirica.core.cockpit.listener_registry import (
    VALID_TOPIC_SCHEMES,
    listener_active_path,
    listener_pause_path,
    registry_path,
)


@pytest.fixture
def empirica_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lr_mod, 'EMPIRICA_DIR', tmp_path)
    return tmp_path


# ─── path helpers ──────────────────────────────────────────────────────────


class TestPaths:
    def test_registry_path(self, empirica_dir):
        p = registry_path('tmux_3')
        assert p == empirica_dir / 'listeners_tmux_3.json'

    def test_pause_path(self, empirica_dir):
        p = listener_pause_path('tmux_3', 'inbox')
        assert p == empirica_dir / 'listener_paused_tmux_3_inbox'

    def test_active_path(self, empirica_dir):
        p = listener_active_path('tmux_3', 'inbox')
        assert p == empirica_dir / 'listener_active_tmux_3_inbox.json'

    def test_path_sanitization(self, empirica_dir):
        p = registry_path('tmux/3')
        assert '/' not in p.name


# ─── topic validation ──────────────────────────────────────────────────────


class TestTopicValidation:
    def test_ntfy_accepted(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        entry = reg.register(name='foo', topic='ntfy:my-channel')
        assert entry.topic == 'ntfy:my-channel'

    def test_all_listed_schemes_accepted(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        for i, scheme in enumerate(VALID_TOPIC_SCHEMES):
            reg.register(name=f'foo{i}', topic=f'{scheme}:rest')

    def test_no_scheme_rejected(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        with pytest.raises(ValueError, match="expected '<scheme>:<rest>'"):
            reg.register(name='foo', topic='no-colon')

    def test_unsupported_scheme_rejected(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        with pytest.raises(ValueError, match='Unsupported topic scheme'):
            reg.register(name='foo', topic='ftp:host')

    def test_empty_topic_rejected(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        with pytest.raises(ValueError, match='topic required'):
            reg.register(name='foo', topic='')


# ─── name validation ───────────────────────────────────────────────────────


class TestNameValidation:
    def test_valid_names(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        for name in ('foo', 'foo-bar', 'foo_bar', 'foo.bar', 'F00x'):
            reg.register(name=name, topic='ntfy:t')

    def test_starts_with_punct_rejected(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        with pytest.raises(ValueError, match='Invalid listener name'):
            reg.register(name='-foo', topic='ntfy:t')

    def test_too_long_rejected(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        with pytest.raises(ValueError, match='Invalid listener name'):
            reg.register(name='x' * 65, topic='ntfy:t')


# ─── register/get/list/unregister ──────────────────────────────────────────


class TestRegister:
    def test_basic_register(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        entry = reg.register(
            name='inbox', topic='ntfy:foo',
            description='cortex inbox', on_wake_template='Run /process',
        )
        assert entry.name == 'inbox'
        assert entry.topic == 'ntfy:foo'
        assert entry.description == 'cortex inbox'
        assert entry.on_wake_template == 'Run /process'
        assert entry.wake_count == 0
        assert entry.last_wake_at is None
        assert entry.registered_at  # non-empty timestamp

    def test_idempotent_preserves_history(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='inbox', topic='ntfy:foo')
        reg.record_wake('inbox', message='wake 1')
        reg.record_wake('inbox', message='wake 2')

        # Re-register with new declarative fields
        entry = reg.register(
            name='inbox', topic='ntfy:bar',  # changed topic
            description='updated', on_wake_template='New template',
        )
        # History preserved
        assert entry.wake_count == 2
        assert entry.last_message == 'wake 2'
        # Declarative updated
        assert entry.topic == 'ntfy:bar'
        assert entry.description == 'updated'
        assert entry.on_wake_template == 'New template'

    def test_persists_to_disk(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='foo', topic='ntfy:a')
        # New instance — read from disk
        reg2 = ListenerRegistry('tmux_test')
        got = reg2.get('foo')
        assert got is not None
        assert got.topic == 'ntfy:a'


class TestList:
    def test_empty(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        assert reg.list_listeners() == []

    def test_multiple_listeners(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='a', topic='ntfy:1')
        reg.register(name='b', topic='ntfy:2')
        reg.register(name='c', topic='ntfy:3')
        names = sorted(e.name for e in reg.list_listeners())
        assert names == ['a', 'b', 'c']

    def test_isolation_per_instance(self, empirica_dir):
        reg1 = ListenerRegistry('tmux_1')
        reg2 = ListenerRegistry('tmux_2')
        reg1.register(name='foo', topic='ntfy:a')
        reg2.register(name='bar', topic='ntfy:b')
        assert [e.name for e in reg1.list_listeners()] == ['foo']
        assert [e.name for e in reg2.list_listeners()] == ['bar']


class TestUnregister:
    def test_removes_entry(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='foo', topic='ntfy:a')
        assert reg.unregister('foo') is True
        assert reg.get('foo') is None

    def test_returns_false_when_absent(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        assert reg.unregister('never-registered') is False

    def test_clears_pause_sidecar(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='foo', topic='ntfy:a')
        set_listener_paused('tmux_test', 'foo', True)
        assert is_listener_paused('tmux_test', 'foo')

        reg.unregister('foo')
        assert not is_listener_paused('tmux_test', 'foo')

    def test_clears_active_runtime_file(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='foo', topic='ntfy:a')
        # Simulate a runtime active file (would normally be written by
        # the listener body when arming the Monitor).
        active = listener_active_path('tmux_test', 'foo')
        active.write_text(json.dumps({'monitor_task_id': 'tk_1', 'curl_pid': 12345}))
        assert active.exists()

        reg.unregister('foo')
        assert not active.exists()


# ─── pause/resume ──────────────────────────────────────────────────────────


class TestPauseResume:
    def test_pause_writes_sidecar(self, empirica_dir):
        set_listener_paused('tmux_test', 'foo', True)
        assert is_listener_paused('tmux_test', 'foo')
        assert listener_pause_path('tmux_test', 'foo').exists()

    def test_resume_clears_sidecar(self, empirica_dir):
        set_listener_paused('tmux_test', 'foo', True)
        set_listener_paused('tmux_test', 'foo', False)
        assert not is_listener_paused('tmux_test', 'foo')

    def test_resume_idempotent_when_not_paused(self, empirica_dir):
        # Should not raise even if no pause file exists
        set_listener_paused('tmux_test', 'foo', False)
        assert not is_listener_paused('tmux_test', 'foo')


# ─── record_wake ───────────────────────────────────────────────────────────


class TestRecordWake:
    def test_increments_count_and_sets_message(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='foo', topic='ntfy:a')

        e1 = reg.record_wake('foo', message='hello')
        assert e1.wake_count == 1
        assert e1.last_message == 'hello'
        assert e1.last_wake_at is not None

        e2 = reg.record_wake('foo', message='world')
        assert e2.wake_count == 2
        assert e2.last_message == 'world'

    def test_wake_without_message(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        reg.register(name='foo', topic='ntfy:a')
        entry = reg.record_wake('foo')
        assert entry.wake_count == 1
        assert entry.last_message is None

    def test_wake_unknown_listener_raises(self, empirica_dir):
        reg = ListenerRegistry('tmux_test')
        with pytest.raises(KeyError, match=r'Listener .* not registered'):
            reg.record_wake('never-registered')


# ─── round-trip serialization ──────────────────────────────────────────────


class TestRoundTrip:
    def test_to_from_dict(self):
        e = ListenerEntry(
            name='foo',
            topic='ntfy:bar',
            description='d',
            on_wake_template='t',
            registered_at='2026-04-30T09:00:00+00:00',
            last_wake_at='2026-04-30T09:30:00+00:00',
            last_message='msg',
            wake_count=5,
        )
        d = e.to_dict()
        e2 = ListenerEntry.from_dict('foo', d)
        assert e2.topic == 'ntfy:bar'
        assert e2.description == 'd'
        assert e2.on_wake_template == 't'
        assert e2.last_wake_at == '2026-04-30T09:30:00+00:00'
        assert e2.last_message == 'msg'
        assert e2.wake_count == 5

    def test_from_dict_handles_missing_history_fields(self):
        # Minimal payload — old schema or fresh register
        e = ListenerEntry.from_dict('foo', {'topic': 'ntfy:t'})
        assert e.wake_count == 0
        assert e.last_wake_at is None
        assert e.last_message is None


# ─── command handler integration ───────────────────────────────────────────


def _ns(**kwargs):
    """Build an argparse-ish Namespace for handler tests."""
    defaults = {'output': 'json', 'instance': 'tmux_test'}
    defaults.update(kwargs)
    return Namespace(**defaults)


class TestCommandHandlers:
    def test_register_then_status_then_unregister(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_register_command,
            handle_listener_status_command,
            handle_listener_unregister_command,
        )

        rc = handle_listener_register_command(_ns(
            name='foo', topic='ntfy:t', description='', on_wake='',
        ))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is True
        assert out['listener']['topic'] == 'ntfy:t'

        rc = handle_listener_status_command(_ns(name='foo'))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is True
        assert out['listener']['name'] == 'foo'

        rc = handle_listener_unregister_command(_ns(name='foo'))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is True
        assert out['removed'] is True

    def test_register_invalid_topic(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_register_command,
        )
        rc = handle_listener_register_command(_ns(
            name='foo', topic='not-a-url', description='', on_wake='',
        ))
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is False
        assert 'Invalid topic' in out['error']

    def test_status_for_unknown_listener(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_status_command,
        )
        rc = handle_listener_status_command(_ns(name='nope'))
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is False
        assert 'not registered' in out['error']

    def test_pause_resume_cycle(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_pause_command,
            handle_listener_register_command,
            handle_listener_resume_command,
        )

        handle_listener_register_command(_ns(
            name='foo', topic='ntfy:t', description='', on_wake='',
        ))
        capsys.readouterr()  # drain

        handle_listener_pause_command(_ns(name='foo'))
        out = json.loads(capsys.readouterr().out)
        assert out['paused'] is True

        handle_listener_resume_command(_ns(name='foo'))
        out = json.loads(capsys.readouterr().out)
        assert out['paused'] is False

    def test_record_wake_increments(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_record_wake_command,
            handle_listener_register_command,
        )

        handle_listener_register_command(_ns(
            name='foo', topic='ntfy:t', description='', on_wake='',
        ))
        capsys.readouterr()

        handle_listener_record_wake_command(_ns(name='foo', message='hello'))
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is True
        assert out['listener']['wake_count'] == 1
        assert out['listener']['last_message'] == 'hello'

        handle_listener_record_wake_command(_ns(name='foo', message='again'))
        out = json.loads(capsys.readouterr().out)
        assert out['listener']['wake_count'] == 2

    def test_fire_increments_with_manual_marker(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_fire_command,
            handle_listener_register_command,
        )

        handle_listener_register_command(_ns(
            name='foo', topic='ntfy:t', description='', on_wake='',
        ))
        capsys.readouterr()

        rc = handle_listener_fire_command(_ns(name='foo'))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is True
        assert out['listener']['wake_count'] == 1
        assert out['listener']['last_message'] == 'manual fire'

    def test_fire_unknown_listener(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_fire_command,
        )
        rc = handle_listener_fire_command(_ns(name='nope'))
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out['ok'] is False
        assert 'not registered' in out['error']

    def test_list_empty_then_populated(self, empirica_dir, capsys):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_listener_list_command,
            handle_listener_register_command,
        )

        rc = handle_listener_list_command(_ns())
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out['count'] == 0
        assert out['listeners'] == []

        handle_listener_register_command(_ns(
            name='a', topic='ntfy:1', description='', on_wake='',
        ))
        handle_listener_register_command(_ns(
            name='b', topic='ntfy:2', description='', on_wake='',
        ))
        capsys.readouterr()

        handle_listener_list_command(_ns())
        out = json.loads(capsys.readouterr().out)
        assert out['count'] == 2
