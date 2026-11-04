"""`release.py --prepare` runs the nightly's two jobs on the release commit, so one run covers tests, nightly and CI (David, 2026-11-04).

The Nightly workflow runs what per-push CI deliberately does not: the tests marked `integration`, and the whole suite with an empty HOME and a system-only
PATH (the only detector for a test that measures the box). It had been red every night since it was added (8 runs, 2026-10-28 to 2026-11-04) and the
release did not look, because `--prepare` trusts a green develop CI and skips the suite. The gate runs both jobs locally, always, so a red nightly blocks
the cut instead of waiting to be noticed.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "scripts" / "release.py"


def _mod():
    spec = importlib.util.spec_from_file_location("release_script_nightly", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manager(tmp_path: Path, **kw):
    rm = _mod().ReleaseManager(**kw)
    rm.repo_root = tmp_path
    return rm


def _marker(cmd):
    """The value of pytest's -m: the second -m, since `python -m pytest` has the first."""
    return cmd[cmd.index("pytest") + 1 :][cmd[cmd.index("pytest") + 1 :].index("-m") + 1]


def _fake_run(codes: list[int]):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(
            cmd, codes[len(calls) - 1] if len(calls) <= len(codes) else 0, stdout="", stderr=""
        )

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_both_nightly_jobs_run_and_a_pass_returns_true(tmp_path):
    run = _fake_run([0, 0])
    assert _manager(tmp_path).run_nightly_checks(run=run) is True
    assert len(run.calls) == 2


def test_the_integration_job_selects_the_integration_marker(tmp_path):
    run = _fake_run([0, 0])
    _manager(tmp_path).run_nightly_checks(run=run)
    cmd, _ = run.calls[0]
    assert cmd[:3] == [sys.executable, "-m", "pytest"] and _marker(cmd) == "integration"


def test_the_clean_environment_job_runs_with_an_empty_home_and_a_system_only_path(tmp_path):
    run = _fake_run([0, 0])
    _manager(tmp_path).run_nightly_checks(run=run)
    cmd, kw = run.calls[1]
    assert _marker(cmd) == "not integration"
    env = kw["env"]
    assert set(env) == {"HOME", "PATH"} and env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] and Path(env["HOME"]).name != ""
    assert cmd[0] == sys.executable  # absolute: the toolchain is deliberately not on that PATH


def test_a_failing_integration_job_fails_the_gate_and_the_clean_job_still_runs(tmp_path):
    """Both jobs are reported, so one run shows every red thing instead of the first."""
    run = _fake_run([1, 0])
    assert _manager(tmp_path).run_nightly_checks(run=run) is False
    assert len(run.calls) == 2


def test_a_failing_clean_environment_job_fails_the_gate(tmp_path):
    assert _manager(tmp_path).run_nightly_checks(run=_fake_run([0, 1])) is False


def test_a_timeout_is_a_failure_not_a_pass(tmp_path):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    assert _manager(tmp_path).run_nightly_checks(run=run) is False


def test_dry_run_runs_nothing_and_passes(tmp_path):
    run = _fake_run([1, 1])
    assert _manager(tmp_path, dry_run=True).run_nightly_checks(run=run) is True
    assert run.calls == []


def test_prepare_calls_the_gate_unless_it_was_skipped():
    """The gate is wired into --prepare and is not behind the develop-CI-is-green skip: that skip is exactly what hid the red nightly."""
    src = RELEASE_PY.read_text()
    prepare = src[src.index("def run_prepare") :]
    assert "run_nightly_checks()" in prepare and "skip_nightly" in prepare
    assert prepare.index("run_nightly_checks()") < prepare.index("_develop_ci_green()")
