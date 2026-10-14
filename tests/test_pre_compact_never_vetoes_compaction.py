"""pre-compact must never veto compaction (issue #415).

The hook turned a failing ``project-bootstrap`` into exit code 2, so every retry of a manual or automatic compaction hit
the same failure and Claude Code printed "Compaction blocked by PreCompact hook". Context capture is optional work: when
it fails the hook saves what it still can (a degraded snapshot), says so, and exits 0. The CLI's diagnostic goes to
stdout, so a stderr-only log hid the reason; the hook also called bootstrap without the project id it already knew, which
fails in a practice with no active-instance mapping and no ``origin`` remote.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
_PROJECT_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def hook():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("pre_compact_never_vetoes", _HOOK_DIR / "pre-compact.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def practice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A practice with a project.yaml and nothing else: no git, no remote, no active-instance mapping."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "practice"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(f"name: P\nai_id: probe\nproject_id: {_PROJECT_UUID}\n")
    monkeypatch.chdir(root)
    return root


def _stub_the_rest(hook, monkeypatch):
    monkeypatch.setattr(hook, "_run_context_budget_triage", lambda *_a, **_k: {})
    monkeypatch.setattr(hook, "_capture_transaction_state", lambda *_a, **_k: (None, {}))
    monkeypatch.setattr(hook, "_assess_fresh_vectors", lambda *_a, **_k: ({"know": 0.8}, None))
    monkeypatch.setattr(hook, "_write_unified_breadcrumbs_note", lambda **_k: True)
    monkeypatch.setenv("EMPIRICA_AI_ID", "probe")


def _run(hook, practice, **kw):
    with pytest.raises(SystemExit) as exit_info:
        hook._main_guarded(
            recovered=None,
            trigger="auto",
            empirica_session="sess1234",
            last_task="finish the thing",
            git_context={},
            project_root=str(practice),
            claude_session_id="cc-1",
            **kw,
        )
    return exit_info.value.code


def _snapshot(practice: Path) -> dict:
    files = sorted((practice / ".empirica" / "ref-docs").glob("pre_summary_*.json"))
    assert files, "no snapshot was written"
    return json.loads(files[-1].read_text())


def _bootstrap_json() -> str:
    return json.dumps({"breadcrumbs": {"session_id": "sess1234", "findings": [{}], "goals": []}})


def _fake_run(responder):
    def _run(cmd, *args, **kwargs):
        if "project-bootstrap" in cmd:
            return responder(cmd, kwargs)
        raise AssertionError(f"unexpected subprocess in the hook body: {cmd}")

    return _run


def _completed(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def test_a_failing_bootstrap_degrades_the_snapshot_and_lets_compaction_proceed(hook, practice, monkeypatch, capsys):
    _stub_the_rest(hook, monkeypatch)
    diagnostic = json.dumps({"ok": False, "error": "Not in a git repository or no remote 'origin' configured"})
    monkeypatch.setattr(hook.subprocess, "run", _fake_run(lambda c, k: _completed(1, diagnostic, "")))

    code = _run(hook, practice)

    assert code == 0, "a context-capture failure must not veto compaction"
    out = capsys.readouterr().out
    assert "stopReason" not in out and "systemMessage" in json.loads(out)
    snap = _snapshot(practice)
    assert snap["capture_status"] == "degraded"
    assert "no remote 'origin'" in snap["bootstrap_error"], (
        "the CLI prints its diagnostic on stdout; it must reach the log"
    )
    assert snap["last_task"] == "finish the thing"
    assert snap["vectors_canonical"] == {"know": 0.8}, "what could still be captured is kept"


def test_a_bootstrap_timeout_degrades_and_exits_zero(hook, practice, monkeypatch, capsys):
    _stub_the_rest(hook, monkeypatch)

    def _slow(cmd, kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(hook.subprocess, "run", _fake_run(_slow))

    assert _run(hook, practice) == 0
    assert "stopReason" not in capsys.readouterr().out
    snap = _snapshot(practice)
    assert snap["capture_status"] == "degraded" and "timed out" in snap["bootstrap_error"]


def test_unparseable_bootstrap_output_degrades_and_exits_zero(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    monkeypatch.setattr(hook.subprocess, "run", _fake_run(lambda c, k: _completed(0, "not json at all")))

    assert _run(hook, practice) == 0
    snap = _snapshot(practice)
    assert snap["capture_status"] == "degraded" and snap["bootstrap_error"]


def test_a_bootstrap_that_reports_ok_false_with_rc_zero_is_degraded_not_complete(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    refusal = json.dumps({"ok": False, "error": "no project"})
    monkeypatch.setattr(hook.subprocess, "run", _fake_run(lambda c, k: _completed(0, refusal)))

    assert _run(hook, practice) == 0
    assert _snapshot(practice)["capture_status"] == "degraded"


def test_an_unexpected_error_in_the_hook_body_still_exits_zero(hook, practice, monkeypatch, capsys):
    _stub_the_rest(hook, monkeypatch)
    monkeypatch.setattr(hook.subprocess, "run", _fake_run(lambda c, k: _completed(0, _bootstrap_json())))

    def _boom(*_a, **_k):
        raise RuntimeError("guidance exploded")

    monkeypatch.setattr(hook, "_build_compact_guidance", _boom)

    assert _run(hook, practice) == 0
    out = json.loads(capsys.readouterr().out)
    assert "stopReason" not in out and "guidance exploded" in out["systemMessage"]


def test_a_successful_capture_is_marked_complete(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    monkeypatch.setattr(hook.subprocess, "run", _fake_run(lambda c, k: _completed(0, _bootstrap_json())))

    assert _run(hook, practice) == 0
    snap = _snapshot(practice)
    assert snap["capture_status"] == "complete" and snap["bootstrap_error"] is None
    assert snap["breadcrumbs_summary"]["findings_count"] == 1


def test_bootstrap_is_bound_to_the_practice_the_hook_resolved(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    seen = {}

    def _capture(cmd, kwargs):
        seen["cmd"] = list(cmd)
        return _completed(0, _bootstrap_json())

    monkeypatch.setattr(hook.subprocess, "run", _fake_run(_capture))

    _run(hook, practice)

    cmd = seen["cmd"]
    assert cmd[cmd.index("--project-id") + 1] == _PROJECT_UUID
    assert cmd[cmd.index("--session-id") + 1] == "sess1234"


def test_a_practice_without_a_project_id_still_bootstraps_by_the_old_route(hook, practice, monkeypatch):
    (practice / ".empirica" / "project.yaml").write_text("name: P\nai_id: probe\n")
    _stub_the_rest(hook, monkeypatch)
    seen = {}

    def _capture(cmd, kwargs):
        seen["cmd"] = list(cmd)
        return _completed(0, _bootstrap_json())

    monkeypatch.setattr(hook.subprocess, "run", _fake_run(_capture))

    assert _run(hook, practice) == 0
    assert "--project-id" not in seen["cmd"]


def test_the_subprocess_timeout_is_capped_by_the_shared_budget(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    seen = {}

    def _capture(cmd, kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _completed(0, _bootstrap_json())

    monkeypatch.setattr(hook.subprocess, "run", _fake_run(_capture))
    now = hook.time.monotonic()

    _run(hook, practice, deadline=now + 6)

    assert 0 < seen["timeout"] <= 5, "bootstrap was given more time than the hook had left"


def test_an_expired_budget_skips_bootstrap_instead_of_running_past_the_harness_kill(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    calls = []
    monkeypatch.setattr(
        hook.subprocess, "run", _fake_run(lambda c, k: calls.append(c) or _completed(0, _bootstrap_json()))
    )

    assert _run(hook, practice, deadline=hook.time.monotonic() - 1) == 0
    assert calls == [], "bootstrap ran with no budget left"
    snap = _snapshot(practice)
    assert snap["capture_status"] == "degraded" and "budget" in snap["bootstrap_error"]


def test_no_capture_path_can_exit_with_the_veto_code():
    source = (_HOOK_DIR / "pre-compact.py").read_text()
    assert "sys.exit(2)" not in source
    assert '"stopReason"' not in source


# --- found by the independent review of 1.14.11 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yaml_text", "expected"),
    [
        (f"project_id: {_PROJECT_UUID}\n", _PROJECT_UUID),
        (f'project_id: "{_PROJECT_UUID}"  # the id\n', _PROJECT_UUID),
        (f"project_id: '{_PROJECT_UUID}'\r\n", _PROJECT_UUID),
        ("project_id:\nai_id: proj\n", None),
        ("project_id: null\n", None),
        ("project_id: ~\n", None),
        ("project_id:\n  sub: 1\n", None),
        ("project_id: &x abc\n", None),
        ('project_id: "a b"\n', None),
        ("  project_id: nested\n", None),
        ("name: P\n", None),
    ],
)
def test_the_project_id_is_read_only_when_the_line_really_holds_one(hook, tmp_path, yaml_text, expected):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(yaml_text)
    assert hook._practice_project_id(tmp_path) == expected


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", '"str"'])
def test_input_the_hook_cannot_read_still_does_not_veto(tmp_path, stdin):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    done = subprocess.run(
        [sys.executable, str(_HOOK_DIR / "pre-compact.py")],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-300:]
    json.loads(done.stdout or "{}")


def test_a_failing_stderr_banner_cannot_corrupt_the_json_on_stdout(hook, practice, monkeypatch, capsys):
    _stub_the_rest(hook, monkeypatch)
    monkeypatch.setattr(hook.subprocess, "run", _fake_run(lambda c, k: _completed(0, _bootstrap_json())))

    class _Broken:
        def write(self, *_a):
            raise BrokenPipeError("reader went away")

        def flush(self):
            raise BrokenPipeError("reader went away")

    monkeypatch.setattr(sys, "stderr", _Broken())

    assert _run(hook, practice) == 0
    out = capsys.readouterr().out
    assert len(json.loads(out)) == 1 and "systemMessage" in json.loads(out)


def test_the_budget_counts_from_process_start_not_from_the_guard(hook, practice, monkeypatch):
    _stub_the_rest(hook, monkeypatch)
    calls = []
    monkeypatch.setattr(
        hook.subprocess, "run", _fake_run(lambda c, k: calls.append(c) or _completed(0, _bootstrap_json()))
    )
    monkeypatch.setattr(hook, "_PROCESS_START", hook.time.monotonic() - 26)  # 26 s went on steps before the guard

    assert _run(hook, practice) == 0
    assert calls == [], "bootstrap ran although the hook had already used its whole budget before the guard"
    assert "budget" in _snapshot(practice)["bootstrap_error"]
