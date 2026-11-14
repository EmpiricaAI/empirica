"""Restarting a claude pane whose practice has no conversation starts claude fresh (prop_vc67kjtw4vfj3b5uavfqyjsycu, bug 1).

refresh added --continue to every claude launch it could not name a session for. In a directory with no conversation,
claude prints "No conversation found to continue" and exits 1, so R and A turned a dead pane into another dead pane
(empirica-research in cockpit-tv:lab, twice). David: the point of refresh is to get rid of dead panes. When Claude Code's
transcript folder for the directory exists and holds no conversation, the plain launch runs instead.
"""

from __future__ import annotations

import json

import pytest

from empirica.core.cockpit.launcher import tmux as T
from empirica.core.cockpit.launcher.config import ProjectSpec


def _proj(path, launch="claude --continue", resume=None):
    return ProjectSpec(name="p", path=str(path), launch=launch, resume=resume)


@pytest.mark.parametrize(
    ("launch", "fresh"),
    [
        ("claude --continue", "claude"),
        ("claude -c --dangerously-skip-permissions", "claude --dangerously-skip-permissions"),
        ("claude --resume", "claude"),
        ("claude", "claude"),
    ],
)
def test_no_conversation_runs_the_launch_without_continue(tmp_path, launch, fresh):
    assert T.resume_command(_proj(tmp_path, launch), None, has_conversation=False) == fresh


def test_a_conversation_or_an_unknown_answer_still_continues(tmp_path):
    assert T.resume_command(_proj(tmp_path), None, has_conversation=True) == "claude --continue"
    assert T.resume_command(_proj(tmp_path), None) == "claude --continue"  # not known: as before


def test_a_named_session_and_an_explicit_resume_line_still_win(tmp_path):
    assert T.resume_command(_proj(tmp_path), "abc", has_conversation=False) == "claude --resume abc"
    assert T.resume_command(_proj(tmp_path, resume="claude -c"), None, has_conversation=False) == "claude -c"


def test_a_chained_launch_is_left_alone(tmp_path):
    launch = "claude --continue; exec bash"
    assert T.resume_command(_proj(tmp_path, launch), None, has_conversation=False) == launch


def test_conversation_lookup_reads_claude_codes_transcript_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "work" / "p"
    work.mkdir(parents=True)
    assert T._claude_has_conversation(str(work)) is None  # no ~/.claude/projects at all: unknown
    folder = tmp_path / "home" / ".claude" / "projects" / str(work).replace("/", "-")
    (tmp_path / "home" / ".claude" / "projects").mkdir(parents=True)
    assert T._claude_has_conversation(str(work)) is False  # Claude Code in use, never here
    folder.mkdir()
    assert T._claude_has_conversation(str(work)) is False  # folder, no transcript
    (folder / "s1.jsonl").write_text(json.dumps({"type": "user"}) + "\n")
    assert T._claude_has_conversation(str(work)) is True


def test_a_conversation_filed_under_another_folder_name_is_found_by_its_cwd(tmp_path, monkeypatch):
    # resume_ids trusts the cwd a transcript records, not the folder name; so does this.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "w"
    work.mkdir()
    other = tmp_path / "home" / ".claude" / "projects" / "-renamed"
    other.mkdir(parents=True)
    (other / "s.jsonl").write_text(json.dumps({"type": "user", "cwd": str(work)}) + "\n")
    assert T._claude_has_conversation(str(work)) is True
    (other / "s.jsonl").write_text(json.dumps({"type": "user", "cwd": "/elsewhere"}) + "\n")
    assert T._claude_has_conversation(str(work)) is False
    (other / "s.jsonl").write_text("not json\n")
    assert T._claude_has_conversation(str(work)) is None  # unreadable: not evidence of no conversation


def test_refresh_respawns_a_dead_pane_with_no_conversation_fresh(tmp_path, monkeypatch):
    import subprocess

    from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".claude" / "projects").mkdir(parents=True)
    work = tmp_path / "research"
    work.mkdir()
    cfg = LauncherConfig(
        session_name="t",
        projects=[ProjectSpec(name="research", path=str(work), launch="claude --continue")],
        groups=[GroupSpec(name="lab", panes=[PaneSpec(project_ref="research")])],
    )
    calls = []

    def fake(*args, check=False):
        calls.append(args)
        out = f"%1\t1\tclaude\tlab/0\t\tlab\t{work}\t" if args and args[0] == "list-panes" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(T, "_tmux", fake)
    monkeypatch.setattr(T, "tmux_available", lambda: True)
    monkeypatch.setattr(T, "cockpit_session_exists", lambda _n: True)
    monkeypatch.setattr(T, "strip_session_identity", lambda _n: None)
    monkeypatch.setattr(T, "last_session_id", lambda *_a: None)

    result = T.refresh_cockpit(cfg)

    assert [r["command"] for r in result.respawned] == ["claude"]
