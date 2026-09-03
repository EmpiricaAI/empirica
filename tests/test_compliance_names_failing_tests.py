"""A gate that counts failures and does not name them is a number you cannot act on.

`compliance-report`'s `tests` check emitted `passed_count` / `failed_count` /
`skipped_count` and nothing else. On 2026-09-03 it blocked a release cut with
`failed_count: 1`, and identifying which test it was took a full manual re-run of a
~6,900-test suite — **while the id had been in the pytest output the parser was
reading and discarding.**

The counting half was never the defect. The failure was already known to the gate at
the moment it reported, and the report threw it away.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.compliance_report_commands import (
    _failed_test_ids,
    _parse_pytest_result,
    _print_human_report,
)

SUMMARY = """\
FAILED tests/test_alpha.py::test_one - AssertionError: expected 3
FAILED tests/test_beta.py::TestB::test_two - sqlite3.OperationalError: database is locked
ERROR tests/test_gamma.py::test_three
1 failed, 6866 passed, 10 skipped in 227.87s
"""


def _raw(output: str, passed: bool = False) -> dict:
    return {"stdout": output, "stderr": "", "passed": passed, "duration_seconds": 1.0}


def test_the_failing_ids_reach_the_receipt():
    """THE regression. Same input the parser already had; it now keeps the names."""
    result = _parse_pytest_result(_raw(SUMMARY))

    assert result["failed_tests"] == [
        "tests/test_alpha.py::test_one",
        "tests/test_beta.py::TestB::test_two",
        "tests/test_gamma.py::test_three",
    ]


def test_counts_are_unchanged():
    """POSITIVE CONTROL on what already worked. Adding the names must not disturb the
    numbers the gate's pass/fail verdict is computed from."""
    result = _parse_pytest_result(_raw(SUMMARY))

    assert (result["passed_count"], result["failed_count"], result["skipped_count"]) == (6866, 1, 10)
    assert result["status"] == "fail"


def test_a_clean_run_names_nothing():
    """NEGATIVE CONTROL. A parser that always produced ids — or produced a stray empty
    string — would put phantom failures in a passing receipt."""
    result = _parse_pytest_result(_raw("6867 passed, 10 skipped in 655.55s", passed=True))

    assert result["failed_tests"] == []
    assert result["status"] == "pass"


def test_errors_are_named_too():
    """A collection ERROR is a failure the reader has to act on, and pytest prints it
    under a different prefix. Naming only FAILED would drop exactly the class where
    the test never ran at all."""
    assert _failed_test_ids("ERROR tests/test_x.py::test_y\n") == ["tests/test_x.py::test_y"]


def test_duplicates_collapse():
    """pytest prints a failure in the progress line and again in the summary; the
    receipt should name it once."""
    dup = "FAILED tests/a.py::t - x\nFAILED tests/a.py::t - x\n"

    assert _failed_test_ids(dup) == ["tests/a.py::t"]


def test_truncation_says_it_truncated():
    """A cut list that does not announce the cut is the silent-truncation defect one
    layer down — the reader would take 25 names for the whole set."""
    many = "".join(f"FAILED tests/t{i}.py::test - boom\n" for i in range(40))

    ids = _failed_test_ids(many, limit=25)

    assert len(ids) == 26
    assert "truncated at 25" in ids[-1]
    assert "15 more" in ids[-1]


def test_the_names_reach_the_human_tail(capsys):
    """The tail is how long CLI output actually gets read. Ids that exist only in the
    JSON leave the person piping to `tail` exactly where they started."""
    _print_human_report(
        {
            "timestamp": "2026-09-03T00:00:00Z",
            "project_root": "/tmp/x",
            "regulatory_frameworks": ["EU AI Act (2024/1689)"],
            "overall": {"status": "non_compliant", "score": 0.5, "checks_passed": 1, "checks_total": 2},
            "checks": [
                {"check": "lint", "status": "pass"},
                {"check": "tests", "status": "fail", "failed_tests": ["tests/test_beta.py::TestB::test_two"]},
            ],
        }
    )
    tail = "\n".join(capsys.readouterr().out.strip().splitlines()[-6:])

    assert "FAILED: tests" in tail
    assert "tests/test_beta.py::TestB::test_two" in tail


@pytest.mark.parametrize("junk", ["", "no failures here", "1 failed in 3s"])
def test_unparseable_output_yields_no_phantom_names(junk):
    """Introspection that cannot find an id must return nothing rather than a
    plausible-looking one — a wrong test name costs more than an absent one."""
    assert _failed_test_ids(junk) == []
