"""Cockpit compliance panel — surfaces last `empirica compliance-report` result.

Compliance is project-scoped (it audits a project's source tree, not an
instance's transaction state). The compliance-report command writes its
JSON output to `~/.empirica/last_compliance_<project_id>.json` on every
run; this module reads that file and produces a small render-friendly
summary that the cockpit aggregator includes per-instance (since multiple
instances of the same project share the same compliance state).

State file shape: identical to the JSON produced by
`empirica compliance-report --output json`. Owned by compliance-report
on write; read-only here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / '.empirica'

# How long a compliance result stays "fresh" before the cockpit shades it
# grey. After this it's still readable but visibly stale.
FRESH_WINDOW_S = 24 * 60 * 60  # 24h


def _safe_suffix(text: str) -> str:
    return text.replace('/', '-').replace('%', '')


def last_compliance_path(project_id: str | None) -> Path | None:
    """Path to the persisted last-compliance JSON for a given project_id.
    Returns None when project_id is empty (caller skips the read)."""
    if not project_id:
        return None
    return EMPIRICA_DIR / f'last_compliance_{_safe_suffix(project_id)}.json'


def _project_id_from_path(project_path: str | None) -> str | None:
    """Read project_id from <project>/.empirica/project.yaml. Non-raising —
    returns None on missing file / malformed yaml / missing key."""
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


def write_last_compliance(project_id: str, report: dict[str, Any]) -> Path | None:
    """Persist the latest compliance-report output for cockpit consumption.
    Called from handle_compliance_report_command on every run. Idempotent
    (overwrites the previous result). Returns the written path, or None if
    project_id is empty (skip silently)."""
    path = last_compliance_path(project_id)
    if path is None:
        return None
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        '_persisted_at': datetime.now(tz=UTC).isoformat(),
        '_project_id': project_id,
        **report,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    return path


def read_compliance_summary(project_path: str | None) -> dict[str, Any] | None:
    """Return a render-friendly summary of the project's last compliance
    check, or None when no persisted result exists. The cockpit aggregator
    embeds this per-instance.

    Output shape:
        {
            'status': 'pass' | 'fail',
            'score': 0.0..1.0,            # checks_passed / checks_total
            'checks_passed': int,
            'checks_total': int,
            'failed_checks': ['lint', 'complexity', ...],   # short labels
            'passed_check_names': ['tests', 'dep_audit', ...],  # short labels
            'persisted_at': iso str,
            'age_seconds': float,         # since persisted_at
            'fresh': bool,                # age < FRESH_WINDOW_S
            'project_id': str,
        }
    """
    project_id = _project_id_from_path(project_path)
    path = last_compliance_path(project_id)
    if path is None or not path.exists():
        return None

    try:
        with open(path, encoding='utf-8') as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    persisted_at_str = report.get('_persisted_at') or report.get('timestamp', '')
    age_seconds: float | None = None
    fresh = False
    try:
        if persisted_at_str:
            persisted = datetime.fromisoformat(
                persisted_at_str.replace('Z', '+00:00')
            )
            age_seconds = max(
                0.0,
                datetime.now(tz=UTC).timestamp() - persisted.timestamp(),
            )
            fresh = age_seconds < FRESH_WINDOW_S
    except (ValueError, TypeError):
        pass

    overall = report.get('overall') or {}
    failed_checks: list[str] = []
    passed_check_names: list[str] = []
    for check in report.get('checks', []) or []:
        if not isinstance(check, dict):
            continue
        label = str(check.get('check') or check.get('name') or '?')
        if check.get('passed', True):
            passed_check_names.append(label)
        else:
            failed_checks.append(label)

    return {
        'status': overall.get('status', 'unknown'),
        'score': float(overall.get('score', 0.0) or 0.0),
        'checks_passed': int(overall.get('checks_passed', 0) or 0),
        'checks_total': int(overall.get('checks_total', 0) or 0),
        'failed_checks': failed_checks,
        'passed_check_names': passed_check_names,
        'persisted_at': persisted_at_str,
        'age_seconds': age_seconds,
        'fresh': fresh,
        'project_id': project_id or '',
    }


__all__ = [
    'EMPIRICA_DIR',
    'FRESH_WINDOW_S',
    'last_compliance_path',
    'read_compliance_summary',
    'write_last_compliance',
]
