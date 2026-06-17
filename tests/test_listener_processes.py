"""Tests for listener subprocess orphan detection + reaping."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from empirica.core.cockpit.listener_processes import (
    reap_processes,
    walk_listener_processes,
    walk_orphan_listener_processes,
)

_PS_OUTPUT = """\
    1     0 /sbin/init
  900     1 /usr/lib/systemd/systemd --user
  901   900 empirica loop listen --instance empirica-extension
 1200     1 sh -c while true; do empirica loop listen --instance empirica-outreach; sleep 3; done
 1201  1200 empirica loop listen --instance empirica-outreach
 1300     1 tail -F -n 0 /home/u/.empirica/loop_fires.log
 1400  4321 tail -F -n 0 /home/u/.empirica/loop_fires.log
 1500     1 vim notes.txt
 1600     1 empirica loop listen --instance empirica
"""


def _patched_ps(output: str = _PS_OUTPUT):
    return patch(
        'empirica.core.cockpit.listener_processes.subprocess.run',
        return_value=SimpleNamespace(stdout=output),
    )


# ─── walk_listener_processes ──────────────────────────────────────────


def test_walk_matches_loop_listen_and_log_tail_only():
    with _patched_ps():
        procs = walk_listener_processes()
    kinds = {(p['pid'], p['kind']) for p in procs}
    assert (901, 'loop_listen') in kinds
    assert (1200, 'loop_listen') in kinds  # supervisor shell matches too
    assert (1201, 'loop_listen') in kinds
    assert (1300, 'log_tail') in kinds
    assert (1400, 'log_tail') in kinds
    assert (1600, 'loop_listen') in kinds
    # Unrelated processes never match
    assert all(pid not in {1, 900, 1500} for pid, _ in kinds)


def test_walk_returns_empty_when_ps_unavailable():
    with patch(
        'empirica.core.cockpit.listener_processes.subprocess.run',
        side_effect=OSError('no ps'),
    ):
        assert walk_listener_processes() == []


# ─── walk_orphan_listener_processes ───────────────────────────────────


def test_orphan_walk_flags_ppid_1_only():
    with _patched_ps():
        orphans = walk_orphan_listener_processes()
    pids = {p['pid'] for p in orphans}
    # systemd-user child (901) and live-parent tail (1400) are NOT orphans;
    # the supervisor's child (1201) has a live parent (the shell) either way.
    assert pids == {1200, 1300, 1600}


def test_orphan_walk_scopes_to_ai_id():
    with _patched_ps():
        orphans = walk_orphan_listener_processes('empirica')
    assert [p['pid'] for p in orphans] == [1600]


def test_orphan_walk_ai_id_matches_log_tail_grep_filter():
    ps = (
        ' 2000     1 tail -F -n 0 /home/u/.empirica/loop_fires.log\n'
        ' 2001     1 grep -E --line-buffered "instance_id": "empirica-pilot"\n'
    )
    # The grep stage carries the ai_id marker; the bare tail does not.
    with _patched_ps(ps):
        orphans = walk_orphan_listener_processes('empirica-pilot')
    assert orphans == []  # grep line lacks loop_fires.log marker; tail lacks ai_id


# ─── reap_processes ───────────────────────────────────────────────────


def test_reap_dry_run_annotates_without_killing():
    procs = [{'pid': 99999999, 'ppid': 1, 'kind': 'loop_listen', 'cmdline': 'x'}]
    with patch('empirica.core.cockpit.listener_processes.os.kill') as kill:
        out = reap_processes(procs, apply=False)
    kill.assert_not_called()
    assert out[0]['removed'] is False


def test_reap_apply_term_then_gone_counts_removed():
    procs = [{'pid': 4242, 'ppid': 1, 'kind': 'loop_listen', 'cmdline': 'x'}]

    calls = []

    def fake_kill(pid, sig):
        calls.append(sig)
        if sig == 0:
            raise ProcessLookupError  # gone after TERM

    with patch('empirica.core.cockpit.listener_processes.os.kill', fake_kill):
        out = reap_processes(procs, apply=True)
    assert out[0]['removed'] is True
    assert calls[0] == signal.SIGTERM
    assert signal.SIGKILL not in calls


def test_reap_apply_escalates_to_kill():
    procs = [{'pid': 4242, 'ppid': 1, 'kind': 'loop_listen', 'cmdline': 'x'}]

    def fake_kill(pid, sig):
        if sig in (signal.SIGTERM, 0):
            return None  # survives TERM, still alive on probe

    with patch(
        'empirica.core.cockpit.listener_processes.os.kill', fake_kill,
    ), patch('empirica.core.cockpit.listener_processes.time.sleep'):
        out = reap_processes(procs, apply=True, term_grace_sec=0.0)
    assert out[0]['removed'] is True


def test_reap_already_dead_counts_removed():
    procs = [{'pid': 4242, 'ppid': 1, 'kind': 'log_tail', 'cmdline': 'x'}]
    with patch(
        'empirica.core.cockpit.listener_processes.os.kill',
        side_effect=ProcessLookupError,
    ):
        out = reap_processes(procs, apply=True)
    assert out[0]['removed'] is True


def test_reap_permission_error_recorded():
    procs = [{'pid': 4242, 'ppid': 1, 'kind': 'log_tail', 'cmdline': 'x'}]
    with patch(
        'empirica.core.cockpit.listener_processes.os.kill',
        side_effect=PermissionError('nope'),
    ):
        out = reap_processes(procs, apply=True)
    assert out[0]['removed'] is False
    assert 'nope' in out[0]['error']


def test_walk_excludes_own_pid():
    ps = f'{os.getpid()}     1 empirica loop listen --instance self\n'
    with _patched_ps(ps):
        assert walk_listener_processes() == []


# ─── gc + off integration (handler level) ─────────────────────────────


def _make_args(**overrides):
    defaults = {
        'instance': 'test_instance', 'ai_id': 'myai', 'name': None,
        'output': 'json', 'apply': False, 'age_days': 7,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_gc_reports_orphan_processes(tmp_path, monkeypatch, capsys):
    from empirica.cli.command_handlers.cockpit_commands import (
        handle_listener_gc_command,
    )
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    (tmp_path / '.empirica').mkdir()
    orphan = {'pid': 777, 'ppid': 1, 'kind': 'loop_listen', 'cmdline': 'empirica loop listen --instance x'}
    with patch(
        'empirica.core.cockpit.listener_processes.walk_listener_processes',
        return_value=[orphan],
    ):
        rc = handle_listener_gc_command(_make_args())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['orphan_process_count'] == 1
    assert out['orphan_processes'][0]['pid'] == 777
    assert out['orphan_processes'][0]['removed'] is False  # dry run


def test_off_reaps_orphans_and_removes_state_file(tmp_path, monkeypatch, capsys):
    from empirica.cli.command_handlers.cockpit_commands import (
        handle_listener_off_command,
    )
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('empirica.core.cockpit.listener_registry.EMPIRICA_DIR',
                        tmp_path / '.empirica')
    (tmp_path / '.empirica').mkdir()
    state = tmp_path / '.empirica' / 'listener_active_test_instance_myai-inbox.json'
    state.write_text(json.dumps({'monitor_task_id': 'tk_1', 'ai_id': 'myai'}))

    orphan = {'pid': 778, 'ppid': 1, 'kind': 'loop_listen',
              'cmdline': 'empirica loop listen --instance myai'}
    with patch(
        'empirica.core.cockpit.listener_processes.walk_listener_processes',
        return_value=[orphan],
    ), patch(
        'empirica.core.cockpit.listener_processes.os.kill',
        side_effect=ProcessLookupError,
    ), patch(
        'empirica.cli.command_handlers.cockpit_commands._resolve_canonical_ai_id',
        return_value='myai',
    ):
        rc = handle_listener_off_command(_make_args())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['state_file_removed'] is True
    assert not state.exists()
    assert out['reaped_orphans'][0]['pid'] == 778
    assert out['reaped_orphans'][0]['removed'] is True
    # next_step protocol unchanged
    assert out['next_step']['tool'] == 'TaskStop'


# ─── launchd-supervised exclusion (prop_vicjvq4: gc reaped live workers) ──

_IS_RUNNING = "empirica.core.loop_scheduler.persistent_listener.is_listener_running"


def test_ai_id_from_listener_cmdline():
    from empirica.core.cockpit.listener_processes import _ai_id_from_listener_cmdline as f
    assert f("empirica loop listen --instance empirica-outreach") == "empirica-outreach"
    assert f('tail -F x | grep \'"instance_id": "empirica-pilot"\'') == "empirica-pilot"
    assert f("random unrelated command") is None


def test_macos_excludes_launchd_backed_loop_listen_but_keeps_log_tail(monkeypatch):
    """On macOS, launchd reparents live workers to PID 1 — they must NOT be
    reaped when the service is up. A log_tail bridge stays a real orphan."""
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch(_IS_RUNNING, return_value=True), _patched_ps():
        orphans = walk_orphan_listener_processes()
    assert all(o["kind"] != "loop_listen" for o in orphans)   # live workers spared
    assert any(o["kind"] == "log_tail" for o in orphans)      # session bridge still orphan


def test_macos_reaps_unbacked_loop_listen(monkeypatch):
    """macOS but no loaded launchd service for the ai_id → genuine orphan."""
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch(_IS_RUNNING, return_value=False), _patched_ps():
        orphans = walk_orphan_listener_processes()
    assert any(o["kind"] == "loop_listen" for o in orphans)


def test_linux_unchanged_ppid1_is_orphan(monkeypatch):
    """systemd-user supervision never reparents to PID 1, so PID 1 genuinely
    means orphaned — the launchd exclusion must not run on linux."""
    monkeypatch.setattr(sys, "platform", "linux")
    with _patched_ps():
        orphans = walk_orphan_listener_processes()
    assert any(o["kind"] == "loop_listen" for o in orphans)
