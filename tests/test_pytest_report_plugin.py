"""The pytest plugin that leaves a summary for the post-test collector (goal 561c1aea, David's ruling 2026-10-26).

`PostTestCollector._collect_test_results` read a pytest-json-report-shaped file from four fixed paths and nothing in core ever wrote one: no
`verification_evidence` row on any practice was sourced from pytest, and the absence was silent. The plugin writes `.empirica/test_report.json`
(`summary.passed/failed/total`, `created`, `head`, `invocation`) when a pytest session finishes, but ONLY when the pytest rootdir holds an
`.empirica/` directory, so it is inert in every other project that happens to share the machine, and it is registered as a `pytest11` entry
point so a practice needs no configuration. It never fails a test run. Each test runs a real pytest in a throwaway project.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "empirica.utils.pytest_report_plugin"

SUITE = """
def test_one(): assert True
def test_two(): assert 1 + 1 == 2
def test_three_fails(): assert False
import pytest
@pytest.mark.skip(reason="no")
def test_skipped(): pass
"""


def _project(tmp_path: Path, *, empirica: bool, body: str = SUITE) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "test_sample.py").write_text(body)
    if empirica:
        (project / ".empirica").mkdir()
    return project


def _run_pytest(project: Path, *extra: str) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(project.parent), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        # `no:empirica` blocks the entry-point copy: with the install refreshed, loading the same module under two names raises in pluggy
        [sys.executable, "-m", "pytest", "-p", "no:empirica", "-p", PLUGIN, "-p", "no:cacheprovider", "-q", *extra],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _report(project: Path) -> dict:
    return json.loads((project / ".empirica" / "test_report.json").read_text())


def test_a_finished_session_leaves_the_counts_where_the_collector_reads_them(tmp_path):
    project = _project(tmp_path, empirica=True)
    before = time.time()
    result = _run_pytest(project)
    assert result.returncode == 1, result.stdout[-400:]  # one test fails on purpose
    report = _report(project)
    assert report["summary"] == {"passed": 2, "failed": 1, "skipped": 1, "total": 3, "collected": 4}
    assert before - 1 <= report["created"] <= time.time() + 1
    assert report["exitcode"] == 1
    assert report["runner"] == "pytest", "the file is runner-agnostic; the writer names itself"
    assert isinstance(report["invocation"], list)


def test_total_excludes_skipped_so_a_skip_does_not_lower_the_pass_rate(tmp_path):
    """The collector computes passed / total; a skipped test did not run and is not evidence either way."""
    project = _project(
        tmp_path, empirica=True, body="import pytest\ndef test_a(): pass\n@pytest.mark.skip\ndef test_b(): pass\n"
    )
    _run_pytest(project)
    summary = _report(project)["summary"]
    assert (summary["passed"], summary["failed"], summary["total"]) == (1, 0, 1)


def test_the_plugin_is_inert_outside_an_empirica_project(tmp_path):
    project = _project(tmp_path, empirica=False)
    _run_pytest(project)
    assert not list(project.rglob("test_report.json")), "no .empirica/ in the rootdir: nothing is written anywhere"
    assert not (project / ".empirica").exists()


def test_a_collection_error_counts_as_a_failure(tmp_path):
    project = _project(tmp_path, empirica=True, body="import definitely_not_a_module\ndef test_a(): pass\n")
    result = _run_pytest(project)
    assert result.returncode != 0
    summary = _report(project)["summary"]
    assert summary["failed"] >= 1 and summary["passed"] == 0


def test_under_xdist_only_the_controller_writes_and_it_counts_the_whole_run(tmp_path):
    pytest.importorskip("xdist")
    body = "".join(f"def test_{i}(): assert True\n" for i in range(6))
    project = _project(tmp_path, empirica=True, body=body)
    result = _run_pytest(project, "-n", "2")
    assert result.returncode == 0, result.stdout[-400:]
    summary = _report(project)["summary"]
    assert (summary["passed"], summary["failed"], summary["total"]) == (6, 0, 6), (
        "a worker's partial count must not win"
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_report_location_never_fails_or_alters_the_test_run(tmp_path):
    project = _project(tmp_path, empirica=True, body="def test_a(): pass\n")
    (project / ".empirica").chmod(0o555)
    try:
        result = _run_pytest(project)
    finally:
        (project / ".empirica").chmod(0o755)
    assert result.returncode == 0 and "INTERNALERROR" not in result.stdout + result.stderr


def test_a_second_run_replaces_the_report_and_leaves_no_temp_file_behind(tmp_path):
    project = _project(tmp_path, empirica=True, body="def test_a(): pass\n")
    _run_pytest(project)
    first = _report(project)["created"]
    time.sleep(0.05)
    _run_pytest(project)
    assert _report(project)["created"] > first
    assert sorted(p.name for p in (project / ".empirica").iterdir()) == ["test_report.json"]


def test_the_plugin_is_registered_as_a_pytest11_entry_point():
    """Pins the zero-config registration; it only takes effect on an editable install after `pip install -e . --no-deps` is re-run."""
    meta = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert meta["project"]["entry-points"]["pytest11"]["empirica"] == PLUGIN


# ── scope: what the report covers ───────────────────────────────────────────────────────────────────────────────
# A -k or single-file rerun overwrites the report and, with no record of its selection, reads as the whole suite (ecodex, nextest).


def test_a_run_with_no_selection_declares_a_full_run(tmp_path):
    project = _project(tmp_path, empirica=True)
    _run_pytest(project)
    assert _report(project)["scope"] == "full run"


def test_a_keyword_selection_is_declared_partial_and_names_the_expression(tmp_path):
    project = _project(tmp_path, empirica=True)
    _run_pytest(project, "-k", "test_one")
    scope = _report(project)["scope"]
    assert scope.startswith("partial:") and "-k test_one" in scope


def test_a_marker_selection_is_declared_partial(tmp_path):
    project = _project(tmp_path, empirica=True)
    _run_pytest(project, "-m", "not slow")
    assert "-m not slow" in _report(project)["scope"]


def test_naming_a_file_is_declared_partial_and_names_it(tmp_path):
    project = _project(tmp_path, empirica=True)
    _run_pytest(project, "test_sample.py::test_two")
    scope = _report(project)["scope"]
    assert scope.startswith("partial:") and "test_sample.py::test_two" in scope


def test_options_that_do_not_select_tests_leave_it_a_full_run(tmp_path):
    project = _project(tmp_path, empirica=True)
    _run_pytest(project, "-x", "--tb=short", "-W", "ignore")
    assert _report(project)["scope"] == "full run"


def test_a_very_long_selection_is_truncated_so_the_report_stays_small(tmp_path):
    project = _project(tmp_path, empirica=True)
    _run_pytest(project, "-k", " or ".join(f"test_{i}" for i in range(400)))
    assert len(_report(project)["scope"]) <= 200
