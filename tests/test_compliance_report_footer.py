"""The verdict was header-only, so `compliance-report | tail` showed no verdict.

A practitioner read `tail -45`, saw only passing rows, reported "all 14 controls
pass", and tagged a release on it — while lint and complexity were failing above the
fold and the first line said FAIL. The last thing printed was a frameworks banner
that LOOKS like a summary and carries no outcome.

Tail is how long CLI output actually gets read. A verdict reachable only by scrolling
up is one most readers never see, and the reader most likely to pipe to tail is the
one automating a gate.

**Every test here asserts the FAILING case renders**, because a footer that is only
correct when everything passes is the defect it was written to remove.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.compliance_report_commands import _print_human_report


def _report(checks: list[dict], status: str, passed: int) -> dict:
    return {
        "timestamp": "2026-09-02T00:00:00Z",
        "project_root": "/tmp/x",
        "regulatory_frameworks": ["EU AI Act (2024/1689)"],
        "overall": {
            "status": status,
            "score": passed / len(checks),
            "checks_passed": passed,
            "checks_total": len(checks),
        },
        "checks": checks,
    }


def _tail(capsys, n: int = 8) -> str:
    """Read only what a `| tail` reader would see. The whole point is that the head
    is not available to them."""
    return "\n".join(capsys.readouterr().out.strip().splitlines()[-n:])


def test_a_failing_report_says_so_in_the_tail(capsys):
    """THE regression. Passing rows last, failure earlier — exactly the shape that
    fooled a reader into tagging a release."""
    _print_human_report(
        _report(
            [
                {"check": "lint", "status": "fail"},
                {"check": "complexity", "status": "fail"},
                {"check": "type_safety", "status": "pass"},
                {"check": "dep_audit", "status": "pass"},
            ],
            "non_compliant",
            2,
        )
    )
    tail = _tail(capsys)

    assert "FAIL" in tail, "the tail must carry the verdict"
    assert "lint" in tail and "complexity" in tail, "and NAME what failed"


def test_failures_are_named_not_counted(capsys):
    """A count sends the reader back up to scan for which ones, through output that
    interleaves failures with passes. The tail alone should answer 'what failed'."""
    _print_human_report(
        _report([{"check": "secret_scan", "status": "fail"}, {"check": "lint", "status": "pass"}], "non_compliant", 1)
    )
    tail = _tail(capsys)

    assert "secret_scan" in tail


def test_unavailable_is_reported_separately_from_passed(capsys):
    """A skipped check cannot fail, so folding it into the pass count is how an
    exemption reports clean forever. The reader needs 'checked and good' told apart
    from 'not checked'."""
    _print_human_report(
        _report(
            [{"check": "pytest", "status": "unavailable"}, {"check": "lint", "status": "pass"}],
            "compliant_with_gaps",
            1,
        )
    )
    tail = _tail(capsys)

    assert "UNAVAILABLE" in tail
    assert "pytest" in tail


def test_a_clean_report_still_says_pass(capsys):
    """POSITIVE CONTROL. A footer that only rendered on failure would satisfy every
    assertion above while telling a passing run nothing."""
    _print_human_report(_report([{"check": "lint", "status": "pass"}], "fully_compliant", 1))
    tail = _tail(capsys)

    assert "PASS" in tail
    assert "100%" in tail


@pytest.mark.parametrize("status", ["fully_compliant", "compliant_with_gaps", "non_compliant"])
def test_every_status_reaches_the_tail(capsys, status):
    """No status may render a footer without a verdict — including one nobody
    anticipated, since the icon map has a '?' fallback and a silent '?' is the same
    unreadable tail this fixes."""
    _print_human_report(_report([{"check": "lint", "status": "pass"}], status, 1))
    assert "RESULT:" in _tail(capsys)
