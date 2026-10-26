"""What the post-test collector accepts as test evidence, and how it says so when there is none (goal 561c1aea).

The collector used to read one pytest-shaped file from four fixed paths, trust it regardless of age, mistake `htmlcov/status.json` (a coverage
index) for a test report, and answer "nothing found" with the same silence whether a project had no tests, no report, or a report from last month.
Now it reads the runner-agnostic `.empirica/test_report.json` first, then legacy pytest files, then JUnit XML (the one format nearly every runner
emits), ignores anything older than the transaction window, and records WHY it found nothing in `bundle.source_notes`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from empirica.core.post_test.collector import EvidenceBundle, EvidenceProfile, PostTestCollector

T0 = 1_000_000.0  # the transaction window opens here


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".empirica").mkdir()
    return tmp_path


def _collector(root: Path, **kw) -> PostTestCollector:
    c = PostTestCollector("s", preflight_timestamp=kw.pop("preflight_timestamp", T0), **kw)
    c._project_root = str(root)
    return c


def _neutral(root: Path, passed=8, failed=2, created=T0 + 100, **extra) -> Path:
    path = root / ".empirica" / "test_report.json"
    path.write_text(
        json.dumps({"runner": "go", "summary": {"passed": passed, "failed": failed}, "created": created, **extra})
    )
    return path


def _junit(root: Path, rel: str, body: str, mtime: float | None = T0 + 100) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


SUITE = '<testsuite name="a" tests="{t}" failures="{f}" errors="{e}" skipped="{s}"/>'


def _rate(items):
    (item,) = [i for i in items if i.metric_name == "test_pass_rate"]
    return item


# ── the neutral file ────────────────────────────────────────────────────────────────────────────────────────────


def test_the_neutral_report_becomes_a_pass_rate_and_keeps_the_runner(root):
    _neutral(root, passed=8, failed=2)
    item = _rate(_collector(root)._collect_test_results())
    assert item.value == pytest.approx(0.8)
    assert item.raw_value["passed"] == 8 and item.raw_value["total"] == 10
    assert item.raw_value["runner"] == "go"


def test_the_neutral_report_wins_over_a_legacy_pytest_file(root):
    _neutral(root, passed=5, failed=5)
    (root / "pytest_report.json").write_text(json.dumps({"summary": {"passed": 10, "failed": 0}}))
    assert _rate(_collector(root)._collect_test_results()).value == pytest.approx(0.5)


def test_a_legacy_pytest_report_without_a_timestamp_still_counts_when_the_file_is_fresh(root):
    legacy = root / "pytest_report.json"
    legacy.write_text(json.dumps({"summary": {"passed": 3, "failed": 1}}))
    os.utime(legacy, (T0 + 50, T0 + 50))
    assert _rate(_collector(root)._collect_test_results()).value == pytest.approx(0.75)


def test_a_coverage_index_is_not_mistaken_for_a_test_report(root):
    """htmlcov/status.json was listed as a pytest report path; it holds no pass counts and parsed as zeros."""
    (root / "htmlcov").mkdir()
    (root / "htmlcov" / "status.json").write_text(json.dumps({"files": {}, "totals": {"percent_covered": 91.0}}))
    c = _collector(root)
    assert [i for i in c._collect_test_results() if i.metric_name == "test_pass_rate"] == []


# ── age ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_report_from_before_the_transaction_is_ignored_and_the_note_says_stale(root):
    _neutral(root, created=T0 - 3600)
    c = _collector(root)
    assert c._collect_test_results() == []
    assert c._source_notes["pytest"] == "stale_test_report"


def test_a_report_made_inside_the_window_counts(root):
    _neutral(root, created=T0 + 1)
    assert _rate(_collector(root)._collect_test_results()).value == pytest.approx(0.8)


def test_a_praxic_window_opens_at_check_not_at_preflight(root):
    """Tests run while investigating say nothing about what was built after CHECK."""
    _neutral(root, created=T0 + 100)
    c = _collector(root, phase="praxic", check_timestamp=T0 + 500)
    assert c._collect_test_results() == []
    assert c._source_notes["pytest"] == "stale_test_report"


def test_without_any_window_a_report_cannot_be_judged_old_and_is_accepted(root):
    _neutral(root, created=1.0)
    c = PostTestCollector("s")
    c._project_root = str(root)
    assert _rate(c._collect_test_results()).value == pytest.approx(0.8)


def test_a_report_without_created_falls_back_to_the_file_mtime(root):
    path = _neutral(root)
    data = json.loads(path.read_text())
    del data["created"]
    path.write_text(json.dumps(data))
    os.utime(path, (T0 - 10, T0 - 10))
    c = _collector(root)
    assert c._collect_test_results() == [] and c._source_notes["pytest"] == "stale_test_report"


# ── saying why there is nothing ─────────────────────────────────────────────────────────────────────────────────


def test_no_report_anywhere_is_noted_as_such(root):
    c = _collector(root)
    assert c._collect_test_results() == []
    assert c._source_notes["pytest"] == "no_test_report"


def test_a_report_that_ran_zero_tests_is_not_evidence_and_says_so(root):
    _neutral(root, passed=0, failed=0)
    c = _collector(root)
    assert c._collect_test_results() == []
    assert c._source_notes["pytest"] == "empty_test_report"


def test_a_malformed_report_is_treated_as_absent_not_as_a_crash(root):
    (root / ".empirica" / "test_report.json").write_text("{not json")
    c = _collector(root)
    assert c._collect_test_results() == []
    assert c._source_notes["pytest"] == "no_test_report"


def test_a_second_call_does_not_inherit_the_first_calls_note(root):
    c = _collector(root)
    c._collect_test_results()
    assert c._source_notes["pytest"] == "no_test_report"
    _neutral(root)
    assert c._collect_test_results() and "pytest" not in c._source_notes


def test_collect_all_surfaces_the_note_only_for_a_source_that_came_back_empty(root, monkeypatch):
    c = _collector(root)
    monkeypatch.setattr(c, "_resolve_profile", lambda: EvidenceProfile.CODE)
    for name in (
        "_collect_artifact_metrics",
        "_collect_noetic_metrics",
        "_collect_sentinel_metrics",
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
        "_collect_git_metrics",
        "_collect_code_quality_metrics",
    ):
        monkeypatch.setattr(c, name, lambda: [])
    bundle = c.collect_all()
    assert "pytest" in bundle.sources_empty and bundle.source_notes == {"pytest": "no_test_report"}
    _neutral(root)
    bundle = c.collect_all()
    assert "pytest" in bundle.sources_available and bundle.source_notes == {}, "a source with signal needs no excuse"


def test_the_bundle_defaults_to_no_notes():
    assert EvidenceBundle(session_id="s").source_notes == {}


def test_both_calibration_payloads_carry_the_notes():
    import inspect

    from empirica.core.post_test import grounded_calibration as gc

    assert inspect.getsource(gc).count('"source_notes"') >= 2, "the no-evidence and the graded payloads both report why"


# ── JUnit XML: the format every runner already writes ───────────────────────────────────────────────────────────


def test_a_surefire_report_is_read_without_any_configuration(root):
    _junit(root, "target/surefire-reports/TEST-a.xml", SUITE.format(t=10, f=1, e=1, s=2))
    item = _rate(_collector(root)._collect_test_results())
    # 10 tests: 2 skipped did not run, 1 failure + 1 error failed, so 6 passed of 8 that ran
    assert item.raw_value == {"passed": 6, "failed": 2, "total": 8, "runner": "junit"}
    assert item.value == pytest.approx(0.75)


def test_several_files_are_summed_not_picked_between(root):
    _junit(root, "build/test-results/test/TEST-a.xml", SUITE.format(t=4, f=0, e=0, s=0))
    _junit(root, "build/test-results/test/TEST-b.xml", SUITE.format(t=6, f=3, e=0, s=0))
    item = _rate(_collector(root)._collect_test_results())
    assert item.raw_value["passed"] == 7 and item.raw_value["failed"] == 3


def test_a_testsuites_wrapper_is_summed_over_its_suites_not_double_counted(root):
    body = f"<testsuites tests='99'>{SUITE.format(t=3, f=0, e=0, s=0)}{SUITE.format(t=2, f=1, e=0, s=0)}</testsuites>"
    _junit(root, "junit.xml", body)
    item = _rate(_collector(root)._collect_test_results())
    assert (item.raw_value["passed"], item.raw_value["failed"]) == (4, 1), "the wrapper's own total is not trusted"


def test_a_suite_without_counts_is_counted_from_its_cases(root):
    body = '<testsuite name="x"><testcase name="a"/><testcase name="b"><failure/></testcase><testcase name="c"><skipped/></testcase></testsuite>'
    _junit(root, "test-results/x.xml", body)
    item = _rate(_collector(root)._collect_test_results())
    assert (item.raw_value["passed"], item.raw_value["failed"]) == (1, 1)


def test_a_junit_file_from_before_the_window_is_left_out_while_a_fresh_one_counts(root):
    _junit(root, "target/surefire-reports/TEST-old.xml", SUITE.format(t=50, f=50, e=0, s=0), mtime=T0 - 5000)
    _junit(root, "target/surefire-reports/TEST-new.xml", SUITE.format(t=2, f=0, e=0, s=0))
    item = _rate(_collector(root)._collect_test_results())
    assert item.raw_value["total"] == 2


def test_only_stale_junit_files_is_reported_stale(root):
    _junit(root, "junit.xml", SUITE.format(t=5, f=0, e=0, s=0), mtime=T0 - 5000)
    c = _collector(root)
    assert c._collect_test_results() == [] and c._source_notes["pytest"] == "stale_test_report"


def test_malformed_junit_is_skipped_and_a_good_neighbour_still_counts(root):
    _junit(root, "test-results/bad.xml", "<testsuite><oops")
    _junit(root, "test-results/good.xml", SUITE.format(t=2, f=0, e=0, s=0))
    assert _rate(_collector(root)._collect_test_results()).raw_value["total"] == 2


def test_the_neutral_file_beats_junit_when_both_exist(root):
    _neutral(root, passed=1, failed=1)
    _junit(root, "junit.xml", SUITE.format(t=100, f=0, e=0, s=0))
    assert _rate(_collector(root)._collect_test_results()).raw_value["runner"] == "go"


def test_a_pile_of_junit_files_is_bounded(root):
    for i in range(300):
        _junit(root, f"test-results/{i:03}.xml", SUITE.format(t=1, f=0, e=0, s=0))
    item = _rate(_collector(root)._collect_test_results())
    assert item.raw_value["total"] <= 200, (
        "an unbounded glob on a hot path is a latency regression waiting for a big repo"
    )


# ── which projects count as code ────────────────────────────────────────────────────────────────────────────────


def _profile(files, root):
    c = _collector(root)
    c._get_session_changed_files = lambda: files  # type: ignore[method-assign]
    return c._resolve_profile()


@pytest.mark.parametrize(
    "files",
    [
        ["main.go"],
        ["src/lib.rs", "Cargo.toml"],
        ["A.java"],
        ["App.kt"],
        ["svc.ts"],
        ["index.js"],
        ["x.c", "x.h"],
        ["a.cpp"],
        ["P.cs"],
        ["a.rb"],
        ["a.php"],
        ["main.py"],
    ],
)
def test_source_in_any_mainstream_language_is_code(files, root):
    assert _profile(files, root) == EvidenceProfile.CODE


def test_markdown_and_config_alone_are_still_prose(root):
    assert _profile(["README.md", "config.yaml"], root) == EvidenceProfile.PROSE


def test_component_files_are_web_and_a_script_beside_them_does_not_make_it_hybrid(root):
    assert _profile(["Page.tsx"], root) == EvidenceProfile.WEB
    assert _profile(["Page.astro", "util.ts"], root) == EvidenceProfile.WEB, (
        "a web project's own scripts are web assets"
    )


def test_real_code_beside_web_files_is_hybrid(root):
    assert _profile(["Page.tsx", "server.go"], root) == EvidenceProfile.HYBRID
    assert _profile(["Page.astro", "tool.py"], root) == EvidenceProfile.HYBRID


def test_an_empty_change_set_is_still_insufficient(root):
    assert _profile([], root) == EvidenceProfile.INSUFFICIENT
