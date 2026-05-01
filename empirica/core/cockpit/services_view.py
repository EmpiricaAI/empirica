"""Cockpit services panel — surfaces last `empirica scan` snapshot.

The scanner is project-scoped (its read-surface lives in the project's
`.empirica/project.yaml`). The `empirica scan --save` and
`empirica scan --explain` paths both write the latest snapshot to
``~/.empirica/last_scan_<project_id>.json``; this module reads that
file and produces a small render-friendly summary the cockpit
aggregator embeds per-instance (since multiple instances of the same
project share the same scanner state).

State file shape: identical to the JSON produced by
``empirica scan --output json --save``. Owned by the scanner on write;
read-only here.

Mirrors the compliance_view.py shape so the cockpit can render
Compliance + Services with consistent ergonomics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / '.empirica'

# A scanner snapshot stays "fresh" for 24h; after that the cockpit
# shades it grey but the value is still readable.
FRESH_WINDOW_S = 24 * 60 * 60


def _safe_suffix(text: str) -> str:
    return text.replace('/', '-').replace('%', '')


def last_scan_path(project_id: str | None) -> Path | None:
    """Path to the persisted last-scan JSON for a given project_id.
    Returns None when project_id is empty (caller skips the read)."""
    if not project_id:
        return None
    return EMPIRICA_DIR / f'last_scan_{_safe_suffix(project_id)}.json'


def _project_id_from_path(project_path: str | None) -> str | None:
    """Read project_id from <project>/.empirica/project.yaml. Non-raising —
    returns None on missing file / malformed yaml / missing key.
    Same logic as compliance_view._project_id_from_path; duplicated to
    keep the two views independent."""
    if not project_path:
        return None
    yaml_path = Path(project_path) / '.empirica' / 'project.yaml'
    if not yaml_path.exists():
        return None
    try:
        import yaml
        with open(yaml_path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get('project_id')
    return str(pid) if pid else None


def read_services_summary(project_path: str | None) -> dict[str, Any] | None:
    """Return a render-friendly summary of the project's last scan, or
    None when no persisted snapshot exists.

    Output shape:
        {
            'scan_id': str,
            'started_at_iso': str,
            'age_seconds': float | None,
            'fresh': bool,                 # age < FRESH_WINDOW_S
            'host': str,
            'process_count': int,           # snapshot.processes length
            'listening_ports_count': int,
            'mcp_servers_count': int,
            'plugin_manifests_count': int,
            'cron_entries_count': int,
            'integrity_ratio': float,        # processes coverage ratio
            'env_var_names_count': int,      # interesting matches
            'errors_count': int,             # collector errors during scan
            'project_id': str,
        }

    Phase 2 T2 surfaces only the deterministic Phase 1 metrics. When
    Phase 2's auditor judgments land in the artifact tables, this
    summary will gain ``findings_count`` / ``assumptions_count`` /
    ``unknowns_count`` derived from rows filtered by audit-transaction
    marker.
    """
    project_id = _project_id_from_path(project_path)
    path = last_scan_path(project_id)
    if path is None or not path.exists():
        return None

    try:
        with open(path, encoding='utf-8') as f:
            scan = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    started_at = scan.get('started_at')
    started_at_iso = ''
    age_seconds: float | None = None
    fresh = False
    if isinstance(started_at, (int, float)):
        try:
            started_at_iso = datetime.fromtimestamp(
                started_at, tz=UTC,
            ).isoformat(timespec='seconds')
            age_seconds = max(
                0.0,
                datetime.now(tz=UTC).timestamp() - float(started_at),
            )
            fresh = age_seconds < FRESH_WINDOW_S
        except (ValueError, OSError):
            pass

    snap = scan.get('snapshot') or {}
    processes = snap.get('processes') or []
    network = snap.get('network') or {}
    scheduled = snap.get('scheduled') or {}
    filesystem = snap.get('filesystem') or {}
    process_env = snap.get('process_env') or {}
    coverage = snap.get('coverage') or {}
    proc_cov = coverage.get('processes') or {}

    return {
        'scan_id': scan.get('scan_id', ''),
        'started_at_iso': started_at_iso,
        'age_seconds': age_seconds,
        'fresh': fresh,
        'host': scan.get('host', ''),
        'process_count': len(processes),
        'listening_ports_count': len(network.get('listening_ports') or []),
        'mcp_servers_count': len(filesystem.get('mcp_registered_servers') or []),
        'plugin_manifests_count': len(filesystem.get('plugin_manifest_paths') or []),
        'cron_entries_count': len(scheduled.get('cron_entries') or []),
        'integrity_ratio': float(proc_cov.get('ratio', 0.0) or 0.0),
        'env_var_names_count': len(process_env.get('var_names_only') or []),
        'errors_count': len(scan.get('errors') or []),
        'project_id': project_id or '',
    }


__all__ = [
    'EMPIRICA_DIR',
    'FRESH_WINDOW_S',
    'last_scan_path',
    'read_services_summary',
]
