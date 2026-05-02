"""Tests for `empirica services-audit` (Phase 3 T5b/T5c)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import scan_commands as sc


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.empirica to a tmp dir."""
    home = tmp_path / '.empirica'
    home.mkdir()
    (home / 'scans').mkdir()

    def _fake_home():
        return home

    monkeypatch.setattr(sc, '_empirica_home', _fake_home)
    return home


def _make_snapshot(scan_id: str, processes: list[str],
                    listeners: list[tuple[str, int]] | None = None) -> dict:
    return {
        'scan_id': scan_id,
        'started_at': '2026-05-02T10:00:00Z',
        'finished_at': '2026-05-02T10:00:01Z',
        'host': 'test-host',
        'snapshot': {
            'processes': [{'name': p} for p in processes],
            'network': {
                'listening_ports': [
                    {'host': h, 'port': p} for h, p in (listeners or [])
                ],
            },
            'coverage': {'processes': {'attempted': 5, 'succeeded': 5, 'ratio': 1.0}},
        },
        'errors': [],
    }


@pytest.fixture
def patch_collect_snapshot(monkeypatch):
    """Provide a fake collect_snapshot that returns a programmable result."""
    state = {'next': None}

    class _FakeSnapshot:
        def __init__(self, payload):
            self._payload = payload

        def to_dict(self):
            return self._payload

    def _fake_collect():
        if state['next'] is None:
            return _FakeSnapshot(_make_snapshot('default-scan-id', ['svc-default']))
        return _FakeSnapshot(state['next'])

    # The handler imports collect_snapshot lazily — patch at the source.
    import empirica.core.scanner as scanner
    monkeypatch.setattr(scanner, 'collect_snapshot', _fake_collect)
    return state


def _seed_history(home, project_id: str, snapshot: dict) -> None:
    """Write a snapshot to scans/<id>.json + append to history."""
    sid = snapshot['scan_id']
    (home / 'scans' / f'{sid}.json').write_text(json.dumps(snapshot), encoding='utf-8')
    history_line = {
        'scan_id': sid,
        'started_at': snapshot['started_at'],
        'finished_at': snapshot.get('finished_at'),
        'host': snapshot['host'],
        'coverage': snapshot['snapshot']['coverage'],
        'errors': 0,
    }
    history_path = home / f'scan_history_{project_id}.jsonl'
    with history_path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(history_line) + '\n')


def test_audit_first_run_returns_empty_no_prior(fake_home, patch_collect_snapshot, capsys):
    """First fire — no prior to diff against. Result should be 'empty',
    no notification."""
    patch_collect_snapshot['next'] = _make_snapshot('aaaa1111-1111-1111-1111-111111111111',
                                                    ['svc-a'])
    args = SimpleNamespace(project_id='proj-1', output='json', no_notify=False)
    rc = sc.handle_services_audit_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is True
    assert out['result'] == 'empty'
    assert out['prior_scan_id'] is None
    assert out['notify']['emitted'] is False


def test_audit_no_change_returns_empty(fake_home, patch_collect_snapshot, capsys):
    """Prior snapshot exists, current matches → empty, no notify."""
    prior = _make_snapshot('aaaa1111-1111-1111-1111-111111111111', ['svc-a'])
    _seed_history(fake_home, 'proj-1', prior)
    patch_collect_snapshot['next'] = _make_snapshot('bbbb2222-2222-2222-2222-222222222222',
                                                    ['svc-a'])
    args = SimpleNamespace(project_id='proj-1', output='json', no_notify=False)
    rc = sc.handle_services_audit_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['result'] == 'empty'
    assert out['prior_scan_id'].startswith('aaaa')
    assert out['novelty']['processes_added'] == []


def test_audit_novelty_returns_found_and_attempts_notify(
    fake_home, patch_collect_snapshot, capsys, monkeypatch,
):
    """Novel process appears → result=found, notify dispatched."""
    prior = _make_snapshot('aaaa1111-1111-1111-1111-111111111111', ['svc-a'])
    _seed_history(fake_home, 'proj-1', prior)
    patch_collect_snapshot['next'] = _make_snapshot(
        'bbbb2222-2222-2222-2222-222222222222', ['svc-a', 'svc-NEW'],
    )

    captured: dict = {}

    def _fake_dispatch(event, config, project_id=None):
        captured['event'] = event
        captured['project_id'] = project_id
        from empirica.core.notify.dispatcher import DispatchResult
        from empirica.core.notify.event import EmitResult
        return DispatchResult(
            resolved_backend='stdout', resolved_topic=None,
            fell_back=False, fallback_reason=None,
            emit_result=EmitResult(backend='stdout', ok=True, detail='ok'),
        )

    import empirica.core.notify.dispatcher as disp
    monkeypatch.setattr(disp, 'dispatch', _fake_dispatch)

    args = SimpleNamespace(project_id='proj-1', output='json', no_notify=False)
    rc = sc.handle_services_audit_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['result'] == 'found'
    assert out['novelty']['processes_added'] == ['svc-NEW']
    assert out['notify']['emitted'] is True
    assert out['notify']['backend'] == 'stdout'
    assert captured['project_id'] == 'proj-1'
    assert captured['event'].severity == 'warning'


def test_audit_no_notify_flag_skips_dispatch(
    fake_home, patch_collect_snapshot, capsys, monkeypatch,
):
    """--no-notify suppresses the dispatcher even when novelty present."""
    prior = _make_snapshot('aaaa1111-1111-1111-1111-111111111111', ['svc-a'])
    _seed_history(fake_home, 'proj-1', prior)
    patch_collect_snapshot['next'] = _make_snapshot(
        'bbbb2222-2222-2222-2222-222222222222', ['svc-a', 'svc-NEW'],
    )

    called = {'count': 0}

    def _fake_dispatch(*_a, **_kw):
        called['count'] += 1

    import empirica.core.notify.dispatcher as disp
    monkeypatch.setattr(disp, 'dispatch', _fake_dispatch)

    args = SimpleNamespace(project_id='proj-1', output='json', no_notify=True)
    sc.handle_services_audit_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out['result'] == 'found'
    assert out['notify']['emitted'] is False
    assert called['count'] == 0


def test_audit_no_project_id_returns_fail(monkeypatch, capsys):
    """No project context resolved → result=fail, exit 1."""
    monkeypatch.setattr(sc, '_resolve_project_id', lambda _a: None)
    args = SimpleNamespace(project_id=None, output='json', no_notify=False)
    rc = sc.handle_services_audit_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is False
    assert out['result'] == 'fail'
