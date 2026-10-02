"""A newcomer's SessionStart / post-compact must not take over a LIVE owner's instance pointer.

2026-09-29, 10-01 and 10-02: a claude in empirica-nle carrying `EMPIRICA_INSTANCE_ID=empirica` rewrote
`instance_projects/empirica.json` (core's) at its own SessionStart, and core's commands then landed
beside the wrong transaction file. The guard that existed looked for an open transaction in the
NEWCOMER's project directory, where the owner's transaction cannot be, and only when it was open.

What marks an owner worth protecting is that it is alive. Claude Code keeps ~/.claude/sessions/<pid>.json
(sessionId, pid) for every running claude, so that is what these tests build, under tmp_path with HOME
pinned. The "live claude" is a real process whose executable is named `claude`.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"
sys.path.insert(0, str(LIB))
import instance_clash as ic  # noqa: E402


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "sessions").mkdir(parents=True)
    (home / ".empirica" / "instance_projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "shared-id")
    core = tmp_path / "core"
    nle = tmp_path / "nle"
    for p in (core, nle):
        (p / ".empirica").mkdir(parents=True)
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\nsleep 60\n")
    exe.chmod(0o755)
    procs: list[subprocess.Popen] = []

    def live_claude(session_id: str) -> int:
        proc = subprocess.Popen([str(exe), "60"])
        procs.append(proc)
        time.sleep(0.15)
        (home / ".claude" / "sessions" / f"{proc.pid}.json").write_text(
            json.dumps({"pid": proc.pid, "sessionId": session_id})
        )
        return proc.pid

    def pointer(project: Path, session_id: str | None) -> None:
        (home / ".empirica" / "instance_projects" / "shared-id.json").write_text(
            json.dumps({"project_path": str(project), "claude_session_id": session_id, "empirica_session_id": "e1"})
        )

    def read_pointer() -> dict:
        return json.loads((home / ".empirica" / "instance_projects" / "shared-id.json").read_text())

    yield type(
        "W",
        (),
        {
            "home": home,
            "core": core,
            "nle": nle,
            "live": staticmethod(live_claude),
            "pointer": staticmethod(pointer),
            "read": staticmethod(read_pointer),
        },
    )
    for p in procs:
        p.kill()
        p.wait()


def _load(name: str, filename: str):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


# ── the helper ───────────────────────────────────────────────────────────────


def test_a_live_owner_in_another_project_is_a_foreign_owner(world):
    """POSITIVE CONTROL: the real shape."""
    pid = world.live("owner-session")

    owner = ic.foreign_live_owner(
        {"project_path": str(world.core), "claude_session_id": "owner-session"}, "newcomer", str(world.nle)
    )

    assert owner and owner["pid"] == pid and owner["claude_session_id"] == "owner-session"


def test_a_dead_owner_is_not_protected(world):
    (world.home / ".claude" / "sessions" / "99999999.json").write_text(
        json.dumps({"pid": 99999999, "sessionId": "gone"})
    )

    assert (
        ic.foreign_live_owner({"project_path": str(world.core), "claude_session_id": "gone"}, "me", str(world.nle))
        is None
    )


def test_the_same_project_is_a_resume_not_a_clash(world):
    world.live("owner-session")

    assert (
        ic.foreign_live_owner(
            {"project_path": str(world.core), "claude_session_id": "owner-session"}, "me", str(world.core)
        )
        is None
    )


def test_the_same_session_is_not_a_foreign_owner_of_itself(world):
    world.live("same")

    assert (
        ic.foreign_live_owner({"project_path": str(world.core), "claude_session_id": "same"}, "same", str(world.nle))
        is None
    )


@pytest.mark.parametrize(
    "existing",
    [None, [], {}, {"project_path": "x"}, {"claude_session_id": "a"}, {"claude_session_id": 5, "project_path": "x"}],
)
def test_a_pointer_without_an_owner_session_never_blocks(world, existing):
    world.live("owner-session")

    assert ic.foreign_live_owner(existing, "me", str(world.nle)) is None


def test_a_session_file_whose_pid_is_not_a_claude_is_not_a_live_owner(world, tmp_path):
    """pid reuse: the number is alive but it is some other program."""
    other = subprocess.Popen(["sleep", "60"])
    try:
        (world.home / ".claude" / "sessions" / f"{other.pid}.json").write_text(
            json.dumps({"pid": other.pid, "sessionId": "recycled"})
        )
        assert ic.live_claude_pid("recycled") is None
    finally:
        other.kill()
        other.wait()


def test_the_notice_names_the_owner_and_the_way_out(world):
    text = ic.clash_notice(
        "shared-id", {"claude_session_id": "abcdef123456", "project_path": str(world.core), "pid": 42}, str(world.nle)
    )

    assert "not taking over `shared-id`" in text and "`core`" in text and "abcdef12" in text and "pid 42" in text
    assert "unset EMPIRICA_INSTANCE_ID" in text and "EMPIRICA_INSTANCE_ID=nle claude --continue" in text


# ── the two hooks ────────────────────────────────────────────────────────────


def test_session_init_leaves_a_live_owners_pointer_alone_and_says_so(world, monkeypatch):
    """Fails on the old hook, which overwrote the pointer with the newcomer's project."""
    world.live("owner-session")
    world.pointer(world.core, "owner-session")
    hook = _load("si_clash", "session-init.py")

    ok = hook._write_instance_projects(str(world.nle), "newcomer", "e2")

    assert ok is True
    assert world.read()["project_path"] == str(world.core) and world.read()["claude_session_id"] == "owner-session"
    assert "INSTANCE ID CLASH" in hook._INSTANCE_CLASH_NOTICE
    # the newcomer's own session-keyed record is still written, so its own commands resolve by session id
    mine = json.loads((world.home / ".empirica" / "active_work_newcomer.json").read_text())
    assert mine["project_path"] == str(world.nle)


def test_session_init_still_takes_over_a_dead_owners_pointer(world):
    """CONTROL: a dead owner is exactly the stale-pointer case the overwrite exists for."""
    world.pointer(world.core, "dead-session")
    hook = _load("si_dead", "session-init.py")

    hook._write_instance_projects(str(world.nle), "newcomer", "e2")

    assert world.read()["project_path"] == str(world.nle) and hook._INSTANCE_CLASH_NOTICE == ""


def test_session_init_still_writes_when_there_is_no_pointer_yet(world):
    hook = _load("si_new", "session-init.py")

    hook._write_instance_projects(str(world.nle), "newcomer", "e2")

    assert world.read()["claude_session_id"] == "newcomer"


def test_post_compact_leaves_a_live_owners_pointer_alone(world):
    world.live("owner-session")
    world.pointer(world.core, "owner-session")
    hook = _load("pc_clash", "post-compact.py")

    assert hook._write_active_work_for_new_conversation("newcomer", str(world.nle), "e2", "shared-id") is True

    assert world.read()["project_path"] == str(world.core)
    assert json.loads((world.home / ".empirica" / "active_work_newcomer.json").read_text())["project_path"] == str(
        world.nle
    )


def test_post_compact_still_takes_over_a_dead_owners_pointer(world):
    world.pointer(world.core, "dead-session")
    hook = _load("pc_dead", "post-compact.py")

    hook._write_active_work_for_new_conversation("newcomer", str(world.nle), "e2", "shared-id")

    assert world.read()["project_path"] == str(world.nle)
