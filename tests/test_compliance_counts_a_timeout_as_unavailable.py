"""A check that timed out is unavailable, not failed, in the summary too.

ecodex (prop_cb32uv7kevf2fmhxhb3wme73fa), empirica 1.14.1: secret_scan returned
{"passed": false, "error": "timeout (180s)", "status": "unavailable"} and the
summary read checks_failed 2, checks_unavailable 0, score 0.8571. The runner
reported a timeout as a failure, the parser stamped "unavailable" over it
without clearing `passed`, and the summary counted by `passed` alone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from empirica.cli.command_handlers import compliance_report_commands as crc


def test_a_check_stamped_unavailable_is_counted_as_unavailable():
    results = [
        {"check": "lint", "passed": True, "status": "pass"},
        {"check": "complexity", "passed": False, "status": "fail"},
        {"check": "secret_scan", "passed": False, "status": "unavailable", "error": "timeout (180s)"},
    ]
    overall = crc._compute_overall_status(results)
    assert overall["checks_unavailable"] == 1
    assert overall["checks_failed"] == 1
    assert overall["checks_passed"] == 1
    assert overall["score"] == 0.5, "scored over the checks that actually ran"


def test_the_runner_reports_a_timeout_as_could_not_answer(monkeypatch):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="scan", timeout=1)

    monkeypatch.setattr(subprocess, "run", slow)
    raw = crc._run_check("secret_scan", ["scan"], Path("."), timeout=1)
    assert raw["passed"] is None and "timeout" in raw["error"]


def test_a_genuine_failure_still_counts_as_failed():
    """Positive control: unavailability does not swallow real failures."""
    overall = crc._compute_overall_status([{"check": "lint", "passed": False, "status": "fail"}])
    assert overall["checks_failed"] == 1 and overall["status"] == "non_compliant"
