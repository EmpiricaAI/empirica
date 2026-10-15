"""``empirica doctor`` reports a pre-compact capture that keeps degrading or failing.

The hook records one JSON line per run in ``~/.empirica/precompact.log``. A bootstrap that failed on every compaction left a line
each time and nothing read them: ecodex-lab's log holds eight ``bootstrap_failed`` entries with an empty ``stderr``, the pattern
behind #415, and the user found out only because compaction was blocked. The check reads the log for THIS project, never fails,
and warns only when the most recent runs are all bad, so one slow bootstrap does not raise an alarm.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from empirica.cli.command_handlers import doctor

NOW = datetime(2026, 10, 15, 12, 0)
HERE = "/work/practice"
OTHER = "/work/other"


def _line(minutes_ago: int, outcome: str, cwd: str = HERE, **fields) -> str:
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return json.dumps({"ts": ts, "cwd": cwd, "outcome": outcome, **fields})


def _ok(minutes_ago: int, cwd: str = HERE) -> list[str]:
    return [_line(minutes_ago, "snapshot_written", cwd, status="complete")]


def _degraded(minutes_ago: int, error: str = "project-bootstrap failed: boom", cwd: str = HERE) -> list[str]:
    return [
        _line(minutes_ago, "bootstrap_failed", cwd, error=error),
        _line(minutes_ago, "snapshot_written", cwd, status="degraded"),
    ]


def _old_failure(minutes_ago: int, cwd: str = HERE) -> list[str]:
    """What a pre-1.14.11 hook logged: the failure, an empty stderr, and no snapshot (it exited 2)."""
    return [_line(minutes_ago, "bootstrap_failed", cwd, rc=1, stderr="")]


@pytest.fixture
def log(tmp_path: Path):
    path = tmp_path / "precompact.log"

    def write(*groups: list[str]) -> Path:
        path.write_text("\n".join(line for g in groups for line in g) + "\n")
        return path

    return write


def _check(path: Path, cwd: str = HERE):
    return doctor.check_precompact_health(Path(cwd), log_path=path, now=NOW.timestamp())


def test_runs_that_all_succeeded_pass(log):
    check = _check(log(_ok(300), _ok(200), _ok(100)))
    assert check.status == doctor.PASS and "3" in check.detail


def test_three_degraded_runs_in_a_row_warn_and_say_why(log):
    check = _check(
        log(_ok(500), _degraded(300), _degraded(200, "project-bootstrap failed: no project"), _degraded(100))
    )
    assert check.status == doctor.WARN
    assert "3 pre-compact runs in a row" in check.detail and "boom" in check.detail
    assert "project-bootstrap" in (check.hint or "")


def test_a_degraded_run_counts_once_not_twice(log):
    check = _check(log(_ok(300), _ok(200), _degraded(100)))
    assert check.status == doctor.PASS and "latest run degraded" in check.detail


def test_old_format_failures_with_empty_stderr_are_recognised(log):
    check = _check(log(_old_failure(300), _old_failure(200), _old_failure(100)))
    assert check.status == doctor.WARN and "no message recorded" in check.detail


def test_one_or_two_bad_runs_do_not_alarm(log):
    check = _check(log(_ok(400), _ok(300), _degraded(200), _degraded(100)))
    assert check.status == doctor.PASS and "latest run degraded" in check.detail and "2 in a row" in check.detail


def test_a_recovered_hook_passes(log):
    check = _check(log(_degraded(400), _degraded(300), _degraded(200), _ok(100)))
    assert check.status == doctor.PASS and "latest run degraded" not in check.detail


def test_other_projects_failures_are_not_this_projects(log):
    check = _check(
        log(_ok(300), _ok(200), _degraded(100, cwd=OTHER), _degraded(90, cwd=OTHER), _degraded(80, cwd=OTHER))
    )
    assert check.status == doctor.PASS


def test_failures_that_are_errors_count_as_bad(log):
    bad = [_line(m, "error", error="OSError: disk full") for m in (300, 200, 100)]
    check = _check(log(_ok(400), bad))
    assert check.status == doctor.WARN and "disk full" in check.detail


def test_intentional_skips_are_neither_good_nor_bad(log):
    skips = [_line(m, "no_empirica_session") for m in (300, 200, 100)]
    check = _check(log(_ok(500), skips))
    assert check.status == doctor.PASS


def test_a_log_with_no_line_for_this_project_is_a_skip_not_a_pass(log):
    check = _check(log(_ok(100, cwd=OTHER)))
    assert check.status == doctor.SKIP and "no pre-compact run" in check.detail


def test_no_log_at_all_is_a_skip(tmp_path):
    assert _check(tmp_path / "missing.log").status == doctor.SKIP


def test_garbage_lines_are_skipped(log, tmp_path):
    path = log(_ok(300), _ok(200))
    path.write_text(path.read_text() + "not json\n\n{broken\n" + "\n".join(_degraded(100)) + "\n")
    assert _check(path).status == doctor.PASS


def test_only_the_tail_of_a_huge_log_is_read(log):
    filler = [_line(5000 + i, "snapshot_written", OTHER, status="complete") for i in range(6000)]
    path = log(filler, _degraded(300), _degraded(200), _degraded(100))
    assert path.stat().st_size > 512 * 1024
    assert _check(path).status == doctor.WARN


def test_the_age_of_the_latest_run_is_reported(log):
    check = _check(log(_ok(60 * 49)))
    assert check.status == doctor.PASS and "2d" in check.detail.replace(" ", "")
