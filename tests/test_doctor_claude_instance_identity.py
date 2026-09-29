"""doctor compares each live claude's EMPIRICA_INSTANCE_ID with its own project's ai_id.

2026-09-29: a claude in empirica-nle ran as `empirica`, took over core's transaction file and mapped
core's instance to its own project, while `check_tmux_session_identity` read PASS. The id sat in a
long-lived shell's own environment; a restart inside that shell re-inherits it. Only the process
knows which id it really runs with.

The logic is tested on injected process lists so nothing here measures the machine running the
suite; the real gatherer is tested only against a process this test spawns itself.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from empirica.cli.command_handlers.doctor import (
    PASS,
    SKIP,
    WARN,
    _claude_processes,
    _project_ai_id,
    check_claude_instance_identity,
)


def _project(root: Path, ai_id: str) -> Path:
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(f"ai_id: {ai_id}\n")
    return root


def _p(pid: int, cwd: Path, iid: str | None) -> dict:
    return {"pid": pid, "cwd": str(cwd), "instance_id": iid}


def test_a_claude_running_as_another_practice_is_flagged_with_the_way_out(tmp_path):
    """POSITIVE CONTROL: the real shape. core's id in empirica-nle's directory."""
    core = _project(tmp_path / "empirica", "empirica")
    nle = _project(tmp_path / "empirica-nle", "empirica-nle")

    c = check_claude_instance_identity([_p(1, core, "empirica"), _p(2, nle, "empirica")])

    assert c.status == WARN
    assert c.data["mismatched"] == [
        {"pid": 2, "instance_id": "empirica", "project": "empirica-nle", "ai_id": "empirica-nle"}
    ]
    assert "2 claude process(es) read: 1 agree" in c.detail and "pid 2 is 'empirica' in empirica-nle" in c.detail
    assert "unset EMPIRICA_INSTANCE_ID" in c.hint and "inherits the wrong id again" in c.hint


def test_agreement_passes_and_says_what_it_read(tmp_path):
    a = _project(tmp_path / "a", "a")
    b = _project(tmp_path / "b", "b")

    c = check_claude_instance_identity([_p(1, a, "a"), _p(2, b, "b")])

    assert c.status == PASS and "2 agree" in c.detail


def test_an_id_equal_to_the_directory_name_is_accepted(tmp_path):
    """The cockpit names an id after the project, which is the directory."""
    d = _project(tmp_path / "dirname", "some-ai-id")

    assert check_claude_instance_identity([_p(1, d, "dirname")]).status == PASS


def test_a_prefix_of_the_ai_id_is_not_agreement(tmp_path):
    """`empirica` is a prefix of `empirica-nle`; that is exactly the bug, not a match."""
    nle = _project(tmp_path / "empirica-nle", "empirica-nle")

    assert check_claude_instance_identity([_p(1, nle, "empirica")]).status == WARN


def test_unbound_sessions_are_counted_but_not_warned(tmp_path):
    d = _project(tmp_path / "d", "d")

    c = check_claude_instance_identity([_p(1, d, None), _p(2, d, "d")])

    assert c.status == PASS and "1 unbound" in c.detail


def test_a_uuid_id_is_deliberate_and_a_claude_outside_any_project_has_nothing_to_compare(tmp_path):
    """ecodex binds its thread id (a UUIDv7) on purpose."""
    d = _project(tmp_path / "ecodex", "ecodex")
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()

    c = check_claude_instance_identity([_p(1, d, "019e35bb-2015-7523-b2d7-b310fb68a07e"), _p(2, nowhere, "whatever")])

    assert c.status == PASS and "2 skipped" in c.detail


def test_a_session_in_a_subdirectory_resolves_to_its_project_root(tmp_path):
    root = _project(tmp_path / "proj", "proj")
    sub = root / "src" / "deep"
    sub.mkdir(parents=True)

    assert _project_ai_id(str(sub)) == (root, "proj")
    assert check_claude_instance_identity([_p(1, sub, "other")]).status == WARN


def test_unreadable_sessions_are_counted_and_stop_a_clean_pass(tmp_path):
    """A PASS that skipped what it could not read would overstate what was checked."""
    d = _project(tmp_path / "d", "d")

    c = check_claude_instance_identity([_p(1, d, "d")], unreadable=2)

    assert c.status == WARN and "2 unreadable" in c.detail


def test_no_claude_at_all_is_a_skip_not_a_pass():
    assert check_claude_instance_identity([]).status == SKIP


# ── the real gatherer, against a process this test owns ─────────────────────


@pytest.fixture
def fake_claude(tmp_path):
    """A process whose executable is named `claude`, in a project directory, carrying an id."""
    # A script, not a renamed binary: coreutils is multi-call and dispatches on argv[0], so a copied
    # `sleep` named claude exits at once. The script keeps the process name `claude` while it waits
    # (no `exec`, or the name would become `sleep`).
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\nsleep 60\n")
    exe.chmod(0o755)
    proj = _project(tmp_path / "proj", "proj")
    proc = subprocess.Popen([str(exe), "60"], cwd=proj, env={**os.environ, "EMPIRICA_INSTANCE_ID": "somebody-else"})
    time.sleep(0.2)
    yield proc, proj
    proc.kill()
    proc.wait()


def test_the_real_gatherer_reads_a_live_processes_cwd_and_id(fake_claude):
    proc, proj = fake_claude

    found, _ = _claude_processes()
    mine = [p for p in found if p["pid"] == proc.pid]

    assert mine and mine[0]["instance_id"] == "somebody-else"
    assert Path(mine[0]["cwd"]).resolve() == proj.resolve()
    # and the check, given exactly that process, flags it
    assert check_claude_instance_identity(mine).status == WARN
