"""Reviewer findings on the 1.14.6 hook changes (broccoli sweep, standard x changed).

Each was reproduced by an independent reviewer; each test here fails against the code the reviewer read.
Built under tmp_path with HOME pinned. The Linux-only ones drive real processes and /proc.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"
sys.path.insert(0, str(LIB))
import instance_clash as ic  # noqa: E402

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="drives real processes through /proc")


def _load(name: str, filename: str):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


# ── the clash notice reaches the model on the resume and adoption paths ─────


@pytest.fixture
def init(monkeypatch):
    mod = _load("si_review_1146", "session-init.py")
    monkeypatch.setattr(mod, "_write_instance_projects", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "_bootstrap_for_existing_session", lambda *_a: True)
    monkeypatch.setattr(mod, "_write_practitioner_presence", lambda *_a: None)
    return mod


def _context(capsys) -> str:
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


def test_the_resume_path_tells_the_model_it_was_refused_the_pointer(init, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(init, "_detect_existing_session", lambda *_a: {"session_id": "s1", "source": "x"})
    monkeypatch.setattr(init, "_INSTANCE_CLASH_NOTICE", "## INSTANCE ID CLASH: refused")

    with pytest.raises(SystemExit):
        init._handle_resume_path("claude-sid", tmp_path, "ai")

    assert "INSTANCE ID CLASH" in _context(capsys)


def test_the_adoption_path_tells_the_model_it_was_refused_the_pointer(init, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        init, "_detect_existing_session", lambda *_a: {"session_id": "s1", "source": "orphaned_transaction"}
    )
    monkeypatch.setattr(init, "_INSTANCE_CLASH_NOTICE", "## INSTANCE ID CLASH: refused")

    with pytest.raises(SystemExit):
        init._handle_orphan_adoption("claude-sid", tmp_path)

    assert "INSTANCE ID CLASH" in _context(capsys)


def test_without_a_clash_the_resume_context_is_unchanged(init, monkeypatch, capsys, tmp_path):
    """Control: no notice recorded, none shown."""
    monkeypatch.setattr(init, "_detect_existing_session", lambda *_a: {"session_id": "s1", "source": "x"})
    monkeypatch.setattr(init, "_INSTANCE_CLASH_NOTICE", "")

    with pytest.raises(SystemExit):
        init._handle_resume_path("claude-sid", tmp_path, "ai")

    ctx = _context(capsys)
    assert "CLASH" not in ctx and ctx.lstrip().startswith("## Session Resumed")


# ── liveness: who counts as a live claude ───────────────────────────────────


@pytest.fixture
def world(tmp_path, monkeypatch):
    import subprocess

    home = tmp_path / "home"
    (home / ".claude" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\nsleep 60\n")
    exe.chmod(0o755)
    procs: list[subprocess.Popen] = []

    def live() -> int:
        p = subprocess.Popen([str(exe), "60"])
        procs.append(p)
        time.sleep(0.15)
        return p.pid

    def session_file(pid: int, session_id: str, **extra) -> None:
        (home / ".claude" / "sessions" / f"{pid}.json").write_text(
            json.dumps({"pid": pid, "sessionId": session_id, **extra})
        )

    yield type(
        "W", (), {"claude_dir": home / ".claude", "live": staticmethod(live), "session": staticmethod(session_file)}
    )
    for p in procs:
        p.kill()
        p.wait()


@linux_only
def test_a_pid_that_is_not_a_claude_is_not_a_live_owner_even_when_it_is_not_ours(world):
    """pid 1 exists and is owned by someone else: kill(1, 0) raises PermissionError, which used to read as 'alive'."""
    world.session(1, "owner")

    assert ic.live_claude_pid("owner", world.claude_dir) is None


@pytest.mark.parametrize("pid", [-1, 0])
def test_a_non_positive_pid_is_never_alive(pid):
    assert ic._alive(pid) is False


@linux_only
def test_a_recycled_pid_now_running_another_claude_is_not_the_owner(world):
    """The session file's own cwd says where its claude ran; a different live claude elsewhere is not it."""
    pid = world.live()
    world.session(pid, "dead-session", cwd="/somewhere/else/entirely")

    assert ic.live_claude_pid("dead-session", world.claude_dir) is None


@linux_only
def test_the_same_claude_in_the_recorded_directory_is_the_owner(world):
    """Positive control for the cwd rule."""
    pid = world.live()
    world.session(pid, "owner", cwd=os.getcwd())

    assert ic.live_claude_pid("owner", world.claude_dir) == pid


@linux_only
def test_a_session_file_with_no_cwd_still_matches_a_live_claude(world):
    """Older or odd session files carry no cwd; that must not turn every owner into a stranger."""
    pid = world.live()
    world.session(pid, "owner")

    assert ic.live_claude_pid("owner", world.claude_dir) == pid


def test_windows_is_never_signalled(monkeypatch):
    """`os.kill(pid, 0)` on Windows calls TerminateProcess: the guard would kill the owner it protects."""
    called = []
    monkeypatch.setattr(ic.sys, "platform", "win32")
    monkeypatch.setattr(ic.os, "kill", lambda *a: called.append(a))

    assert ic._alive(os.getpid()) is False
    assert called == []


# ── EMPIRICA_HARNESS is case-insensitive everywhere ─────────────────────────


@pytest.mark.parametrize("value", ["Claude-Code", " CLAUDE-CODE ", "claude-code"])
def test_a_differently_cased_claude_code_is_still_claude_code(monkeypatch, capsys, tmp_path, value):
    monkeypatch.setenv("EMPIRICA_HARNESS", value)
    router = _load("tr_case", "tool-router.py")
    init = _load("si_case", "session-init.py")
    gate = _load("gate_case", "sentinel-gate.py")
    monkeypatch.setattr(gate, "_UNAVAILABLE_MARKER", tmp_path / "m.json")

    assert router._harness() == "claude-code" and init._harness() == "claude-code"
    gate._respond_unavailable("No module", "sid")
    assert "setup-claude-code" in capsys.readouterr().out


def test_a_differently_cased_codex_gets_the_codex_text(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("EMPIRICA_HARNESS", "CODEX")
    gate = _load("gate_case2", "sentinel-gate.py")
    monkeypatch.setattr(gate, "_UNAVAILABLE_MARKER", tmp_path / "m.json")

    gate._respond_unavailable("No module", "sid")

    out = capsys.readouterr().out
    assert "setup-claude-code" not in out and "diagnose --frontend ecodex" in out


# ── monitor: the whole input must be a list request ─────────────────────────


@pytest.fixture(scope="module")
def gate_mod():
    return _load("gate_monitor_1146", "sentinel-gate.py")


def test_monitor_list_with_extra_fields_is_not_a_plain_list(gate_mod):
    assert gate_mod._noetic_firewall_check("monitor", {"action": "list", "command": "touch x"}, {}) is None


def test_monitor_list_alone_is_still_a_read(gate_mod):
    assert gate_mod._noetic_firewall_check("monitor", {"action": "list"}, {}) is not None
