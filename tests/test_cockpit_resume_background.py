"""Refresh brings back a pane whose Claude session was sent to the background (mesh-support prop_3yoa6zfsffasdejpj5wnah4mcq).

In Claude Code 2.1.325 Ctrl+Z hands the session to the background daemon and the foreground claude exits; every later
`--continue` then exits 1 ("Your most recent conversation is running in the background"). `claude attach <job id>` (the
short `id` of `claude agents --json`) brings it back.
"""

from __future__ import annotations

from types import SimpleNamespace

from empirica.core.cockpit.launcher.resume_ids import background_job

_DIR = "/home/u/practice"
_SID = "901761c7-1111-4222-8333-444455556666"


def _job(kind="background", cwd=_DIR, sid=_SID, jid="901761c7"):
    return {"id": jid, "kind": kind, "cwd": cwd, "sessionId": sid}


def test_the_panes_own_session_in_the_background_is_attached():
    assert background_job(_DIR, _SID, [_job()]) == "901761c7"


def test_without_a_known_session_one_background_job_in_the_directory_is_attached():
    assert background_job(_DIR, None, [_job()]) == "901761c7"


def test_two_background_jobs_in_one_directory_are_not_guessed_between():
    assert background_job(_DIR, None, [_job(), _job(sid="aaaa", jid="aaaa0000")]) is None


def test_a_session_still_live_in_a_terminal_is_not_attached():
    assert background_job(_DIR, _SID, [_job(), {**_job(kind="interactive"), "pid": 1}]) is None


def test_another_directory_or_session_is_not_attached():
    assert background_job(_DIR, _SID, [_job(cwd="/home/u/other")]) is None
    assert background_job(_DIR, _SID, [_job(sid="other-session")]) is None


def test_a_malformed_listing_attaches_nothing():
    assert background_job(_DIR, _SID, [{"kind": "background"}, "x", None]) is None
    assert background_job(_DIR, _SID, [_job(jid="9; rm -rf /")]) is None  # the id goes into a shell command


def test_refresh_runs_attach_for_a_backgrounded_claude_pane():
    from empirica.core.cockpit.launcher.tmux import _background_attach_command

    proj = SimpleNamespace(launch="claude --dangerously-skip-permissions --continue", path=_DIR, resume=None)
    assert _background_attach_command(proj, _SID, [_job()]) == "claude attach 901761c7"
    codex = SimpleNamespace(launch="ecodex", path=_DIR, resume=None)
    assert _background_attach_command(codex, _SID, [_job()]) is None
