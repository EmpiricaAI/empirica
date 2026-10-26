"""POSTFLIGHT's test signal fires on the evidence the collector actually produces (goal 0b644a20).

`mapper._signal_test_results` read `summary["pytest"]["failed_count"]` and `["passed_count"]`. The collector stores the test evidence as
`summary["pytest"]["test_pass_rate"] = {passed, failed, total, runner}`, so the "Test regressions" and "Tests passing" signals could not appear on any
real evidence: ten failing tests of a hundred produced no signal at all. It stayed invisible because no pytest evidence was persisted before the
pytest plugin (goal 561c1aea). These tests start from a report file and run the real collector into the real summary, so the shape between the
two cannot drift again unseen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from empirica.core.post_test.collector import EvidenceBundle, PostTestCollector
from empirica.core.post_test.mapper import _signal_test_results, summarize_evidence


def _bundle(tmp_path: Path, passed: int, failed: int) -> EvidenceBundle:
    (tmp_path / ".empirica").mkdir(exist_ok=True)
    (tmp_path / ".empirica" / "test_report.json").write_text(
        json.dumps({"runner": "go", "summary": {"passed": passed, "failed": failed}, "created": 2.0})
    )
    collector = PostTestCollector("s", preflight_timestamp=1.0)
    collector._project_root = str(tmp_path)
    return EvidenceBundle(session_id="s", items=collector._collect_test_results())


def _signals(bundle: EvidenceBundle, work_type: str | None = None) -> list[str]:
    return summarize_evidence(bundle, work_type)["signals"]


def test_failures_in_the_collected_evidence_become_a_regression_signal(tmp_path):
    assert "Test regressions: 10 tests failing" in _signals(_bundle(tmp_path, passed=90, failed=10))


def test_a_clean_run_becomes_a_passing_signal_with_its_count(tmp_path):
    assert "Tests passing (12101)" in _signals(_bundle(tmp_path, passed=12101, failed=0))


def test_one_failure_outweighs_any_number_of_passes(tmp_path):
    signals = _signals(_bundle(tmp_path, passed=5000, failed=1))
    assert "Test regressions: 1 tests failing" in signals
    assert not any(s.startswith("Tests passing") for s in signals)


def test_a_junit_file_gives_the_signal_too_and_says_what_it_cannot_know(tmp_path):
    """The signal reads the collector's contract, not pytest; JUnit cannot say what it covers, so the signal does."""
    (tmp_path / "junit.xml").write_text('<testsuite tests="4" failures="1" errors="0" skipped="0"/>')
    collector = PostTestCollector("s")
    collector._project_root = str(tmp_path)
    bundle = EvidenceBundle(session_id="s", items=collector._collect_test_results())
    assert "Test regressions: 1 tests failing (scope unknown)" in _signals(bundle)


def test_no_test_evidence_gives_no_test_signal(tmp_path):
    collector = PostTestCollector("s", preflight_timestamp=1.0)
    collector._project_root = str(tmp_path)
    assert not any(
        s.startswith("Test") for s in _signals(EvidenceBundle(session_id="s", items=collector._collect_test_results()))
    )


def test_a_run_that_executed_nothing_gives_no_test_signal(tmp_path):
    assert not any(s.startswith("Test") for s in _signals(_bundle(tmp_path, passed=0, failed=0)))


def test_a_work_type_that_is_blind_to_pytest_gets_no_test_signal(tmp_path):
    """summarize_evidence drops sources with zero relevance for the work type before signals are made."""
    from empirica.core.post_test.mapper import WORK_TYPE_RELEVANCE

    blind = next(wt for wt, rel in WORK_TYPE_RELEVANCE.items() if rel.get("pytest") == 0.0)
    assert not any(s.startswith("Test") for s in _signals(_bundle(tmp_path, passed=1, failed=9), blind))


@pytest.mark.parametrize(
    "summary",
    [
        {"pytest": {"test_pass_rate": 0.8}},
        {"pytest": {"test_pass_rate": {"passed": "many", "failed": None}}},
        {"pytest": {"test_coverage_percent": {"percent": 91}}},
        {"pytest": {}},
        {},
    ],
)
def test_evidence_that_is_not_a_pass_count_never_crashes_the_signal(summary):
    assert _signal_test_results(summary) is None


# ── scope in the signal ─────────────────────────────────────────────────────────────────────────────────────────


def _scoped_bundle(tmp_path: Path, passed: int, failed: int, scope) -> EvidenceBundle:
    (tmp_path / ".empirica").mkdir(exist_ok=True)
    (tmp_path / ".empirica" / "test_report.json").write_text(
        json.dumps(
            {"runner": "pytest", "summary": {"passed": passed, "failed": failed}, "created": 2.0, "scope": scope}
        )
    )
    collector = PostTestCollector("s", preflight_timestamp=1.0)
    collector._project_root = str(tmp_path)
    return EvidenceBundle(session_id="s", items=collector._collect_test_results())


def test_a_declared_scope_is_named_in_the_passing_signal(tmp_path):
    assert "Tests passing (62, scope: partial: -k plugin)" in _signals(
        _scoped_bundle(tmp_path, 62, 0, "partial: -k plugin")
    )


def test_a_declared_scope_is_named_in_the_regression_signal(tmp_path):
    assert "Test regressions: 2 tests failing (scope: full run)" in _signals(_scoped_bundle(tmp_path, 8, 2, "full run"))


def test_a_junit_report_says_its_scope_is_unknown_in_both_directions(tmp_path):
    (tmp_path / "junit.xml").write_text('<testsuite tests="62" failures="0" errors="0" skipped="0"/>')
    collector = PostTestCollector("s")
    collector._project_root = str(tmp_path)
    ok = EvidenceBundle(session_id="s", items=collector._collect_test_results())
    assert "Tests passing (62, scope unknown)" in _signals(ok)
    (tmp_path / "junit.xml").write_text('<testsuite tests="62" failures="3" errors="0" skipped="0"/>')
    bad = EvidenceBundle(session_id="s", items=collector._collect_test_results())
    assert "Test regressions: 3 tests failing (scope unknown)" in _signals(bad)
