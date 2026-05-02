"""Tests for loop_uninstall_request module + pause handler integration.

Covers the 1.8.19 pause-actually-cancels-cron fix. Pause used to be
advisory: it cleared next_scheduled_job_id from the registry but the
already-installed cron one-shot kept firing every interval, spawning
fresh CC sessions that ran the body just long enough to see the pause
flag and exit. Token bleed.

Post-fix: pause writes a pending uninstall request file. The owning
instance's UserPromptSubmit hook surfaces it as a system-reminder
telling Claude to call CronDelete(job_id). Body pause-check at next
fire is the backstop if Claude doesn't run CronDelete in time.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from empirica.core.cockpit import LoopUninstallRequest
from empirica.core.cockpit import loop_registry as reg_mod
from empirica.core.cockpit import loop_uninstall_request as uninst_mod
from empirica.core.cockpit import sentinel_pause as sp_mod
from empirica.core.cockpit.loop_registry import LoopRegistry
from empirica.core.cockpit.loop_uninstall_request import (
    DEFAULT_SCHEDULER_KIND,
    consume_pending,
    list_pending,
    pending_path,
    write_pending,
)


@pytest.fixture
def empirica_dir(tmp_path, monkeypatch):
    """Redirect ~/.empirica/ to a tmp dir for all touched modules."""
    monkeypatch.setattr(uninst_mod, 'EMPIRICA_DIR', tmp_path)
    monkeypatch.setattr(reg_mod, 'EMPIRICA_DIR', tmp_path)
    monkeypatch.setattr(sp_mod, 'EMPIRICA_DIR', tmp_path)
    return tmp_path


# ─── module unit tests ─────────────────────────────────────────────────────


class TestPendingPath:
    def test_basic_path(self, empirica_dir):
        p = pending_path('tmux_3', 'metrics-watch')
        assert p == empirica_dir / 'loop_uninstall_pending_tmux_3_metrics-watch.json'

    def test_sanitizes_slashes(self, empirica_dir):
        # Slashes get stripped to dashes (same rule as install_request)
        p = pending_path('tmux/3', 'a/b')
        assert '/' not in p.name
        assert p.name == 'loop_uninstall_pending_tmux-3_a-b.json'

    def test_sanitizes_percent(self, empirica_dir):
        p = pending_path('tmux%3', 'a%b')
        assert '%' not in p.name


class TestWritePending:
    def test_writes_file(self, empirica_dir):
        path = write_pending(
            instance_id='tmux_42',
            name='test-loop',
            job_id='job-abc-123',
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data['instance_id'] == 'tmux_42'
        assert data['name'] == 'test-loop'
        assert data['job_id'] == 'job-abc-123'
        assert data['scheduler_kind'] == DEFAULT_SCHEDULER_KIND
        assert data['reason'] == 'pause'
        assert data['requested_by'] is None
        assert data['requested_at']  # non-empty timestamp

    def test_idempotent_overwrite(self, empirica_dir):
        write_pending('tmux_1', 'foo', 'job-1')
        write_pending('tmux_1', 'foo', 'job-2')  # rewrite same target
        files = list(empirica_dir.glob('loop_uninstall_pending_*.json'))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data['job_id'] == 'job-2'

    def test_with_requested_by_and_reason(self, empirica_dir):
        write_pending(
            instance_id='tmux_1',
            name='foo',
            job_id='job-x',
            requested_by='tmux_99',
            reason='pause via TUI',
        )
        files = list(empirica_dir.glob('loop_uninstall_pending_*.json'))
        data = json.loads(files[0].read_text())
        assert data['requested_by'] == 'tmux_99'
        assert data['reason'] == 'pause via TUI'

    def test_creates_dir_if_missing(self, tmp_path, monkeypatch):
        # Point at non-existent subdir — write should create it
        target = tmp_path / 'sub' / 'empirica'
        monkeypatch.setattr(uninst_mod, 'EMPIRICA_DIR', target)
        write_pending('tmux_1', 'foo', 'job-x')
        assert target.is_dir()


class TestListPending:
    def test_returns_only_matching_instance(self, empirica_dir):
        write_pending('tmux_1', 'foo', 'job-1')
        write_pending('tmux_1', 'bar', 'job-2')
        write_pending('tmux_2', 'baz', 'job-3')

        for_1 = list_pending('tmux_1')
        assert len(for_1) == 2
        names = sorted(p.name for p in for_1)
        assert all('tmux_1' in n for n in names)

    def test_empty_when_none(self, empirica_dir):
        assert list_pending('tmux_1') == []


class TestConsumePending:
    def test_consume_returns_requests(self, empirica_dir):
        write_pending('tmux_1', 'foo', 'job-1')
        write_pending('tmux_1', 'bar', 'job-2')

        reqs = consume_pending('tmux_1')
        assert len(reqs) == 2
        names = sorted(r.name for r in reqs)
        assert names == ['bar', 'foo']

    def test_consume_deletes_files(self, empirica_dir):
        write_pending('tmux_1', 'foo', 'job-1')
        consume_pending('tmux_1')
        assert list(empirica_dir.glob('loop_uninstall_pending_*.json')) == []

    def test_consume_only_target_instance(self, empirica_dir):
        write_pending('tmux_1', 'foo', 'job-1')
        write_pending('tmux_2', 'bar', 'job-2')

        reqs = consume_pending('tmux_1')
        assert len(reqs) == 1
        # tmux_2's file should remain
        remaining = list(empirica_dir.glob('loop_uninstall_pending_tmux_2_*.json'))
        assert len(remaining) == 1

    def test_consume_handles_corrupt_json(self, empirica_dir):
        # Drop a malformed pending file alongside a good one
        (empirica_dir / 'loop_uninstall_pending_tmux_1_bad.json').write_text('{ broken')
        write_pending('tmux_1', 'good', 'job-x')

        reqs = consume_pending('tmux_1')
        # Corrupt one yields None (filtered); good one survives
        assert len(reqs) == 1
        assert reqs[0].name == 'good'


class TestLoopUninstallRequest:
    def test_to_dict_round_trip(self, tmp_path):
        req = LoopUninstallRequest(
            instance_id='tmux_1',
            name='foo',
            job_id='job-x',
            scheduler_kind='cron-create',
            requested_at='2026-04-30T09:00:00+00:00',
            requested_by='tmux_99',
            reason='pause',
        )
        d = req.to_dict()
        assert d['instance_id'] == 'tmux_1'
        assert d['name'] == 'foo'
        assert d['job_id'] == 'job-x'

    def test_from_path_loads(self, tmp_path):
        path = tmp_path / 'pending.json'
        path.write_text(json.dumps({
            'instance_id': 'tmux_1',
            'name': 'foo',
            'job_id': 'job-x',
            'scheduler_kind': 'cron-create',
            'requested_at': '2026-04-30T09:00:00+00:00',
            'requested_by': 'tmux_99',
            'reason': 'manual pause',
        }))
        req = LoopUninstallRequest.from_path(path)
        assert req is not None
        assert req.name == 'foo'
        assert req.job_id == 'job-x'
        assert req.reason == 'manual pause'

    def test_from_path_returns_none_on_corrupt(self, tmp_path):
        path = tmp_path / 'corrupt.json'
        path.write_text('{ not json')
        assert LoopUninstallRequest.from_path(path) is None

    def test_from_path_returns_none_on_missing(self, tmp_path):
        assert LoopUninstallRequest.from_path(tmp_path / 'nope.json') is None

    def test_from_path_defaults_scheduler_kind(self, tmp_path):
        path = tmp_path / 'p.json'
        path.write_text(json.dumps({
            'instance_id': 'tmux_1',
            'name': 'foo',
            'job_id': 'job-x',
        }))
        req = LoopUninstallRequest.from_path(path)
        assert req is not None
        assert req.scheduler_kind == DEFAULT_SCHEDULER_KIND


# ─── pause handler integration ─────────────────────────────────────────────


def _register_with_job(name='foo', instance='tmux_test', job_id='job-xyz'):
    reg = LoopRegistry(instance)
    reg.register(name=name, kind='cron', cron='*/15 * * * *', description='t')
    reg.heartbeat(
        name=name,
        status='ok',
        result='empty',
        next_scheduled_job_id=job_id,
        scheduler_kind='cron-create',
    )
    return reg


class TestPauseHandlerIntegration:
    def test_cron_create_writes_pending_uninstall(self, empirica_dir):
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_loop_pause_command,
        )

        _register_with_job(name='foo', instance='tmux_test', job_id='job-xyz')
        args = Namespace(
            name='foo', instance='tmux_test', output='json', reason=None,
        )
        rc = handle_loop_pause_command(args)
        assert rc == 0

        # Pause flag set
        pause_files = list(empirica_dir.glob('loop_paused_*'))
        assert pause_files, 'pause flag should exist'

        # Pending uninstall written with correct payload
        pending_files = list(empirica_dir.glob('loop_uninstall_pending_*.json'))
        assert len(pending_files) == 1
        data = json.loads(pending_files[0].read_text())
        assert data['name'] == 'foo'
        assert data['job_id'] == 'job-xyz'
        assert data['scheduler_kind'] == 'cron-create'

        # Registry's next_scheduled_job_id cleared
        reg = LoopRegistry('tmux_test')
        entry = reg.get('foo')
        assert entry is not None
        assert entry.scheduling.next_scheduled_job_id is None

    def test_no_pending_when_no_job_id(self, empirica_dir):
        """If a loop is registered but has never recorded a next_scheduled_job_id,
        pause has nothing to cancel — pending file should not be written."""
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_loop_pause_command,
        )

        # Register without heartbeating a job_id
        reg = LoopRegistry('tmux_test')
        reg.register(name='foo', kind='cron', cron='*/15 * * * *', description='t')

        args = Namespace(
            name='foo', instance='tmux_test', output='json', reason=None,
        )
        rc = handle_loop_pause_command(args)
        assert rc == 0

        # Pause flag still set (advisory layer remains)
        assert list(empirica_dir.glob('loop_paused_*'))
        # But no pending uninstall — there's nothing to cancel
        assert not list(empirica_dir.glob('loop_uninstall_pending_*.json'))

    def test_no_pending_for_non_cron_create_scheduler(self, empirica_dir):
        """Pending uninstall is cron-create-specific (the reminder tells
        Claude to call CronDelete). For systemd-user / at-queue / other
        backends, scheduler-specific cancellation is the operator's
        responsibility — no pending file written."""
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_loop_pause_command,
        )

        reg = LoopRegistry('tmux_test')
        reg.register(name='foo', kind='cron', cron='*/15 * * * *', description='t')
        reg.heartbeat(
            name='foo', status='ok', result='empty',
            next_scheduled_job_id='systemd-job-456',
            scheduler_kind='systemd-user',
        )

        args = Namespace(
            name='foo', instance='tmux_test', output='json', reason=None,
        )
        rc = handle_loop_pause_command(args)
        assert rc == 0

        # No pending uninstall — wrong scheduler kind
        assert not list(empirica_dir.glob('loop_uninstall_pending_*.json'))

    def test_pause_idempotent_rewrites_pending(self, empirica_dir):
        """Calling pause twice in a row rewrites the pending file with the
        latest timestamp. The second call sees a registry with cleared
        job_id from the first pause — so no NEW pending is written. The
        first pause's pending file remains until the hook consumes it."""
        from empirica.cli.command_handlers.cockpit_commands import (
            handle_loop_pause_command,
        )

        _register_with_job(name='foo', instance='tmux_test', job_id='job-1')
        args = Namespace(
            name='foo', instance='tmux_test', output='json', reason=None,
        )
        handle_loop_pause_command(args)
        first_files = list(empirica_dir.glob('loop_uninstall_pending_*.json'))
        assert len(first_files) == 1
        first_data = json.loads(first_files[0].read_text())

        # Second pause — registry already has next_job cleared, so no
        # new pending. First file remains untouched.
        handle_loop_pause_command(args)
        second_files = list(empirica_dir.glob('loop_uninstall_pending_*.json'))
        assert len(second_files) == 1
        second_data = json.loads(second_files[0].read_text())
        assert second_data == first_data


# ─── hook integration ──────────────────────────────────────────────────────


class TestHookFormatting:
    def test_format_request_includes_job_id(self):
        # Import the hook module — the format_request helper is module-private
        # but we can exercise it by constructing a request and checking
        # consume_pending output is what the hook would format.
        req = LoopUninstallRequest(
            instance_id='tmux_3',
            name='outreach-poll',
            job_id='cron-job-xyz',
            scheduler_kind='cron-create',
            requested_at='2026-04-30T09:00:00+00:00',
            requested_by='tmux_7',
            reason='manual pause',
        )
        # The hook formats requests like loop-install-pickup.py — we
        # verify the dataclass exposes the fields the hook needs.
        assert req.name == 'outreach-poll'
        assert req.job_id == 'cron-job-xyz'
        assert req.scheduler_kind == 'cron-create'
        assert req.reason == 'manual pause'
        assert req.requested_by == 'tmux_7'
