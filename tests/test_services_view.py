"""Phase 2 T2 tests for the cockpit services panel.

Mirrors the test_compliance_view.py shape (the services view is the
parallel reader for ``last_scan_<project_id>.json``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from empirica.core.cockpit import services_view


@pytest.fixture
def empirica_home(tmp_path, monkeypatch):
    """Redirect EMPIRICA_DIR + the module's resolver to tmp_path."""
    monkeypatch.setattr(services_view, 'EMPIRICA_DIR', tmp_path)
    return tmp_path


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    proj = tmp_path / 'project'
    (proj / '.empirica').mkdir(parents=True)
    (proj / '.empirica' / 'project.yaml').write_text(
        "project_id: scanner-test\nname: scanner-test\n"
    )
    return proj


def _write_scan(empirica_home: Path, project_id: str, scan: dict) -> Path:
    path = empirica_home / f'last_scan_{project_id}.json'
    path.write_text(json.dumps(scan))
    return path


# ── Path resolution ────────────────────────────────────────────────────


class TestLastScanPath:
    def test_returns_none_for_empty_project_id(self):
        assert services_view.last_scan_path(None) is None
        assert services_view.last_scan_path('') is None

    def test_returns_path_for_valid_id(self, empirica_home):
        path = services_view.last_scan_path('foo')
        assert path == empirica_home / 'last_scan_foo.json'

    def test_safe_suffix_strips_problem_chars(self, empirica_home):
        path = services_view.last_scan_path('foo/bar%baz')
        assert path is not None
        assert '/' not in path.name
        assert '%' not in path.name


# ── read_services_summary ──────────────────────────────────────────────


class TestReadServicesSummary:
    def test_returns_none_when_no_project_path(self, empirica_home):
        assert services_view.read_services_summary(None) is None

    def test_returns_none_when_no_project_yaml(self, empirica_home, tmp_path):
        empty_dir = tmp_path / 'no-yaml'
        empty_dir.mkdir()
        assert services_view.read_services_summary(str(empty_dir)) is None

    def test_returns_none_when_no_scan_file(self, empirica_home, project_dir):
        # Project yaml exists but no last_scan_*.json
        assert services_view.read_services_summary(str(project_dir)) is None

    def test_returns_summary_with_full_shape(self, empirica_home, project_dir):
        import time

        now = time.time()
        scan = {
            'scan_id': 'abc-123',
            'started_at': now - 60,
            'finished_at': now,
            'host': 'test-host',
            'platform': 'Linux x',
            'errors': [],
            'snapshot': {
                'processes': [{'pid': 1}, {'pid': 2}, {'pid': 3}],
                'network': {'connections': [], 'listening_ports': [80, 443]},
                'scheduled': {'cron_entries': [{'line': '* * * * * x'}]},
                'process_env': {'var_names_only': ['ANTHROPIC_API_KEY']},
                'filesystem': {
                    'plugin_manifest_paths': ['/p/a', '/p/b'],
                    'mcp_registered_servers': [{'name': 'foo'}],
                },
                'coverage': {
                    'processes': {
                        'attempted': 3,
                        'succeeded': 3,
                        'ratio': 1.0,
                    },
                },
            },
        }
        _write_scan(empirica_home, 'scanner-test', scan)

        summary = services_view.read_services_summary(str(project_dir))
        assert summary is not None
        assert summary['scan_id'] == 'abc-123'
        assert summary['host'] == 'test-host'
        assert summary['process_count'] == 3
        assert summary['listening_ports_count'] == 2
        assert summary['mcp_servers_count'] == 1
        assert summary['plugin_manifests_count'] == 2
        assert summary['cron_entries_count'] == 1
        assert summary['env_var_names_count'] == 1
        assert summary['integrity_ratio'] == 1.0
        assert summary['errors_count'] == 0
        assert summary['project_id'] == 'scanner-test'
        # Fresh because the scan was 60s ago, well within FRESH_WINDOW_S
        assert summary['fresh'] is True
        assert summary['age_seconds'] is not None
        assert summary['age_seconds'] >= 60.0

    def test_stale_when_old(self, empirica_home, project_dir):
        scan = {
            'scan_id': 'old',
            # Older than the fresh window
            'started_at': 0,
            'host': 'h',
            'snapshot': {'processes': [], 'coverage': {'processes': {'ratio': 1.0}}},
            'errors': [],
        }
        _write_scan(empirica_home, 'scanner-test', scan)

        summary = services_view.read_services_summary(str(project_dir))
        assert summary is not None
        assert summary['fresh'] is False

    def test_handles_missing_keys_gracefully(self, empirica_home, project_dir):
        # Minimal scan — exercise the .get fallbacks
        _write_scan(empirica_home, 'scanner-test', {})

        summary = services_view.read_services_summary(str(project_dir))
        assert summary is not None
        assert summary['scan_id'] == ''
        assert summary['process_count'] == 0
        assert summary['integrity_ratio'] == 0.0

    def test_handles_corrupt_json(self, empirica_home, project_dir):
        path = empirica_home / 'last_scan_scanner-test.json'
        path.write_text('not json {')
        assert services_view.read_services_summary(str(project_dir)) is None
