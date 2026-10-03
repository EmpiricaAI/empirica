"""The hooks under a harness that is not Claude Code, and outside any project.

ecodex (prop_irvujsxusnamtbvmpf6j3ulpe4, prop_dke2es3rhff4plrgfliiqldf24, prop_75i57lp7fzfprbbfftqs2fzdqu):

* Outside a git repo and any empirica project, `get_empirica_root` raises ValueError and the outer
  crash handler allowed the call, printing SENTINEL_CRASH. David ruled that allowing is by design;
  the defect was that it read as a crash, and under EMPIRICA_SENTINEL_FAIL_CLOSED it became a deny.
* `_respond_unavailable` told every harness to re-run `empirica setup-claude-code`, which repairs
  settings only Claude Code reads.
* session-init printed `Deploy gaps: verdict unreadable` at every codex session start, because the
  harness never vendored the cache module and the detector describes a Claude Code deploy anyway.

Everything is built under tmp_path; the gate runs as a subprocess with a scratch HOME and no git.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
HOOKS = REPO / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


def _load(name: str, filename: str):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _run_gate(tmp_path: Path, *, project: bool = False, under_home: bool = False, **env_over: str):
    home = tmp_path / "home"
    work = home / "work" if under_home else tmp_path / "work"
    home.mkdir()
    work.mkdir(parents=True)
    if under_home:
        (home / ".empirica").mkdir()  # the global store every home has: not a project
    if project:
        (work / ".empirica").mkdir()
        (work / ".empirica" / "project.yaml").write_text("project_id: p\nai_id: p\n")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), **env_over}
    hook_input = {
        "hook_event_name": "PreToolUse",
        "session_id": "00000000-0000-0000-0000-00000000abcd",
        "tool_name": "Write",
        "tool_input": {"file_path": str(work / "probe.txt"), "content": "x"},
        "cwd": str(work),
    }
    return subprocess.run(
        [sys.executable, str(HOOKS / "sentinel-gate.py")],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        cwd=work,
        env=env,
        timeout=60,
    )


def _decision(proc) -> dict:
    return json.loads(proc.stdout.strip().splitlines()[-1])["hookSpecificOutput"]


# ── no project ──────────────────────────────────────────────────────────────


def test_no_project_is_a_stated_not_applicable_allow_not_a_crash(tmp_path):
    proc = _run_gate(tmp_path)

    out = _decision(proc)
    assert proc.returncode == 0
    assert out["permissionDecision"] == "allow"
    assert "not applicable" in out["permissionDecisionReason"]
    assert "SENTINEL_CRASH" not in proc.stderr


def test_no_project_stays_an_allow_under_fail_closed(tmp_path):
    """Fail-closed hardens a real crash. A run with no project is not a crash."""
    proc = _run_gate(tmp_path, EMPIRICA_SENTINEL_FAIL_CLOSED="1")

    assert proc.returncode == 0
    assert _decision(proc)["permissionDecision"] == "allow"


def test_a_real_project_in_a_directory_with_no_git_is_not_called_not_applicable(tmp_path):
    """The no-project allow is for the absence of a project. A directory holding .empirica/project.yaml
    but no git repo is a project whose root could not be resolved: that is a crash to report, not nothing
    to measure (broccoli, 2026-10-03: it was allowed, and it overrode fail-closed)."""
    proc = _run_gate(tmp_path, project=True)

    assert "not applicable" not in _decision(proc)["permissionDecisionReason"]


def test_a_real_project_in_a_directory_with_no_git_still_denies_under_fail_closed(tmp_path):
    proc = _run_gate(tmp_path, project=True, EMPIRICA_SENTINEL_FAIL_CLOSED="1")

    assert proc.returncode == 2 and _decision(proc)["permissionDecision"] == "deny"


def test_a_directory_under_home_is_not_a_project_because_home_has_an_empirica_dir(tmp_path):
    """Positive control for the project test: ~/.empirica is the global store, not a project."""
    proc = _run_gate(tmp_path, under_home=True)

    assert "not applicable" in _decision(proc)["permissionDecisionReason"]


# ── the repair hint ─────────────────────────────────────────────────────────


@pytest.fixture
def gate(tmp_path, monkeypatch):
    mod = _load("sentinel_gate_unavailable", "sentinel-gate.py")
    monkeypatch.setattr(mod, "_UNAVAILABLE_MARKER", tmp_path / "unavailable.json")
    return mod


def _unavailable_reason(gate, capsys) -> str:
    gate._respond_unavailable("No module named 'empirica'", "sid")
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_claude_code_is_still_told_to_rerun_setup(gate, capsys, monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)

    assert "empirica setup-claude-code" in _unavailable_reason(gate, capsys)


def test_codex_is_not_sent_to_a_claude_code_repair(gate, capsys, monkeypatch):
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")

    reason = _unavailable_reason(gate, capsys)

    assert "setup-claude-code" not in reason
    assert "on PATH" in reason and "empirica diagnose --frontend ecodex" in reason


def test_an_unknown_harness_gets_the_path_advice_without_a_diagnose_frontend_it_lacks(gate, capsys, monkeypatch):
    monkeypatch.setenv("EMPIRICA_HARNESS", "someother")

    reason = _unavailable_reason(gate, capsys)

    assert "setup-claude-code" not in reason and "on PATH" in reason and "--frontend" not in reason


# ── the deploy-gap block ────────────────────────────────────────────────────


@pytest.fixture
def init_with_cache(monkeypatch):
    """session-init with a stand-in `deploy_gap_cache` that returns a recognisable block."""
    fake = type(sys)("deploy_gap_cache")
    fake.session_start_block = lambda _root: "DEPLOY-GAP-BLOCK"
    monkeypatch.setitem(sys.modules, "deploy_gap_cache", fake)
    return _load("session_init_deploy_gap", "session-init.py")


def test_claude_code_still_gets_the_deploy_gap_block(init_with_cache, monkeypatch, tmp_path):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)

    assert init_with_cache._deploy_gap_block(tmp_path) == "DEPLOY-GAP-BLOCK"


def test_codex_gets_no_deploy_gap_block_and_no_unreadable_line(init_with_cache, monkeypatch, tmp_path):
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    # The failure ecodex would have hit: the module was never vendored.
    monkeypatch.setitem(sys.modules, "deploy_gap_cache", None)

    assert init_with_cache._deploy_gap_block(tmp_path) == ""


def test_the_real_session_init_refuses_to_run_the_detector_off_claude_code(monkeypatch, tmp_path):
    """Not the stand-in: the shipped module is never entered under codex."""
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")
    mod = _load("session_init_real_cache", "session-init.py")
    import deploy_gap_cache as real

    monkeypatch.setattr(real, "session_start_block", lambda _root: pytest.fail("detector entered under codex"))

    assert mod._deploy_gap_block(tmp_path) == ""
