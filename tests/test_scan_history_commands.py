"""Tests for empirica scan-history / scan-show / scan-diff verbs (Phase 3 T5a)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import scan_commands as sc


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.empirica to a tmp dir; seed a couple of scans."""
    home = tmp_path / '.empirica'
    home.mkdir()
    (home / 'scans').mkdir()

    def _fake_home():
        return home

    monkeypatch.setattr(sc, '_empirica_home', _fake_home)
    return home


def _seed_scan(home, scan_id: str, project_id: str, processes: list[str],
               listeners: list[tuple[str, int]], started_at: str = '2026-05-02T10:00:00Z'):
    """Write a snapshot to scans/<id>.json and append the history line."""
    snapshot_dict = {
        'scan_id': scan_id,
        'started_at': started_at,
        'finished_at': started_at,
        'host': 'test-host',
        'snapshot': {
            'processes': [{'name': p} for p in processes],
            'network': {
                'listening_ports': [{'host': h, 'port': p} for h, p in listeners],
            },
            'coverage': {'processes': {'attempted': 10, 'succeeded': 10, 'ratio': 1.0}},
        },
        'errors': [],
    }
    (home / 'scans' / f'{scan_id}.json').write_text(
        json.dumps(snapshot_dict), encoding='utf-8',
    )
    history_line = {
        'scan_id': scan_id,
        'started_at': started_at,
        'finished_at': started_at,
        'host': 'test-host',
        'coverage': snapshot_dict['snapshot']['coverage'],
        'errors': 0,
    }
    history_path = home / f'scan_history_{project_id}.jsonl'
    with history_path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(history_line) + '\n')
    return snapshot_dict


# ─── scan-history ─────────────────────────────────────────────────────────


def test_history_empty_returns_empty_list(fake_home, capsys):
    args = SimpleNamespace(project_id='proj-aaaa', output='json', limit=20)
    rc = sc.handle_scan_history_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is True
    assert out['count'] == 0


def test_history_returns_newest_first(fake_home, capsys):
    _seed_scan(fake_home, 'aaaa1111-1111-1111-1111-111111111111',
               'proj-1', ['svc-a'], [('127.0.0.1', 8080)],
               started_at='2026-04-01T10:00:00Z')
    _seed_scan(fake_home, 'bbbb2222-2222-2222-2222-222222222222',
               'proj-1', ['svc-a', 'svc-b'], [('127.0.0.1', 8080)],
               started_at='2026-05-01T10:00:00Z')
    args = SimpleNamespace(project_id='proj-1', output='json', limit=20)
    rc = sc.handle_scan_history_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['count'] == 2
    # Newest first
    assert out['entries'][0]['scan_id'].startswith('bbbb')
    assert out['entries'][1]['scan_id'].startswith('aaaa')


def test_history_limit_caps_results(fake_home, capsys):
    for i in range(5):
        _seed_scan(fake_home, f'{i:08d}-aaaa-bbbb-cccc-dddddddddddd',
                   'proj-1', ['svc'], [('127.0.0.1', 9000)],
                   started_at=f'2026-05-0{i+1}T10:00:00Z')
    args = SimpleNamespace(project_id='proj-1', output='json', limit=2)
    sc.handle_scan_history_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out['count'] == 2
    # Newest two
    assert out['entries'][0]['scan_id'].startswith('00000004')
    assert out['entries'][1]['scan_id'].startswith('00000003')


def test_history_no_project_id_errors(fake_home, monkeypatch, capsys):
    args = SimpleNamespace(project_id=None, output='json', limit=20)
    # Force resolver to return None — monkeypatch so it auto-reverts
    # at end of test (direct assignment leaks across tests).
    monkeypatch.setattr(sc, '_resolve_project_id', lambda _a: None)
    rc = sc.handle_scan_history_command(args)
    assert rc == 1


# ─── scan-show ────────────────────────────────────────────────────────────


def test_show_returns_snapshot_by_full_id(fake_home, capsys):
    sid = 'aaaa1111-1111-1111-1111-111111111111'
    _seed_scan(fake_home, sid, 'proj-1', ['svc-a'], [('127.0.0.1', 8080)])
    args = SimpleNamespace(scan_id=sid, project_id='proj-1', output='json')
    rc = sc.handle_scan_show_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] is True
    assert out['snapshot']['scan_id'] == sid


def test_show_resolves_prefix(fake_home, capsys):
    """First 8 chars should be enough — operators don't paste full UUIDs."""
    sid = 'aaaa1111-1111-1111-1111-111111111111'
    _seed_scan(fake_home, sid, 'proj-1', ['svc-a'], [('127.0.0.1', 8080)])
    args = SimpleNamespace(scan_id='aaaa1111', project_id='proj-1', output='json')
    rc = sc.handle_scan_show_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['snapshot']['scan_id'] == sid


def test_show_missing_returns_error(fake_home, capsys):
    args = SimpleNamespace(scan_id='deadbeef', project_id='proj-1', output='json')
    rc = sc.handle_scan_show_command(args)
    assert rc == 1


# ─── scan-diff ────────────────────────────────────────────────────────────


def test_diff_detects_added_and_removed_processes(fake_home, capsys):
    a = _seed_scan(fake_home, 'aaaa1111-1111-1111-1111-111111111111',
                   'proj-1', ['svc-a', 'svc-b'], [],
                   started_at='2026-04-01T10:00:00Z')
    b = _seed_scan(fake_home, 'bbbb2222-2222-2222-2222-222222222222',
                   'proj-1', ['svc-b', 'svc-c'], [],
                   started_at='2026-05-01T10:00:00Z')
    args = SimpleNamespace(scan_id_a=a['scan_id'], scan_id_b=b['scan_id'],
                            project_id='proj-1', output='json')
    rc = sc.handle_scan_diff_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['processes']['added'] == ['svc-c']
    assert out['processes']['removed'] == ['svc-a']
    # No changes recorded (svc-b count is 1 in both)
    assert out['processes']['changed'] == []


def test_diff_detects_listening_port_changes(fake_home, capsys):
    a = _seed_scan(fake_home, 'aaaa1111-1111-1111-1111-111111111111',
                   'proj-1', ['svc'], [('127.0.0.1', 8080)],
                   started_at='2026-04-01T10:00:00Z')
    b = _seed_scan(fake_home, 'bbbb2222-2222-2222-2222-222222222222',
                   'proj-1', ['svc'], [('127.0.0.1', 9090)],
                   started_at='2026-05-01T10:00:00Z')
    args = SimpleNamespace(scan_id_a=a['scan_id'], scan_id_b=b['scan_id'],
                            project_id='proj-1', output='json')
    rc = sc.handle_scan_diff_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['listeners']['added'] == ['127.0.0.1:9090']
    assert out['listeners']['removed'] == ['127.0.0.1:8080']


def test_diff_detects_process_count_change(fake_home, capsys):
    """Same process appearing more times → 'changed' entry."""
    a = _seed_scan(fake_home, 'aaaa1111-1111-1111-1111-111111111111',
                   'proj-1', ['svc-a'], [],
                   started_at='2026-04-01T10:00:00Z')
    b = _seed_scan(fake_home, 'bbbb2222-2222-2222-2222-222222222222',
                   'proj-1', ['svc-a', 'svc-a', 'svc-a'], [],
                   started_at='2026-05-01T10:00:00Z')
    args = SimpleNamespace(scan_id_a=a['scan_id'], scan_id_b=b['scan_id'],
                            project_id='proj-1', output='json')
    sc.handle_scan_diff_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out['processes']['changed'] == [{'name': 'svc-a', 'before': 1, 'after': 3}]


def test_diff_missing_either_snapshot_errors(fake_home, capsys):
    args = SimpleNamespace(scan_id_a='aaaaaaaa', scan_id_b='bbbbbbbb',
                            project_id='proj-1', output='json')
    rc = sc.handle_scan_diff_command(args)
    assert rc == 1
