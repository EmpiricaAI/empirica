"""A codex pane resumes its recorded session only when that session's transcript was started in the pane's directory
(goal e5b1a12e), as Claude panes already are: a stale or foreign record falls back to `resume --last`."""

from __future__ import annotations

import json

from empirica.core.cockpit.launcher import resume_ids

_SID = "019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b"


def _setup(tmp_path, monkeypatch, *, transcript_cwd, project):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / ".empirica" / "pane_sessions").mkdir(parents=True)
    (tmp_path / ".empirica" / "pane_sessions" / "17.json").write_text(
        json.dumps({"harness": "codex", "session_id": _SID, "project_path": str(project), "tmux_server_start": "99"})
    )
    if transcript_cwd is not None:
        day = tmp_path / ".codex" / "sessions" / "2026" / "11" / "17"
        day.mkdir(parents=True)
        meta = {"type": "session_meta", "payload": {"cwd": str(transcript_cwd), "id": _SID}}
        (day / f"rollout-2026-11-17T01-00-00-{_SID}.jsonl").write_text(json.dumps(meta) + "\n")


def test_a_session_started_in_the_panes_directory_is_resumed(tmp_path, monkeypatch):
    project = tmp_path / "practice"
    project.mkdir()
    _setup(tmp_path, monkeypatch, transcript_cwd=project, project=project)
    assert resume_ids.codex_session_id("%17", str(project), "99") == _SID


def test_a_session_started_elsewhere_is_not(tmp_path, monkeypatch):
    project = tmp_path / "practice"
    project.mkdir()
    _setup(tmp_path, monkeypatch, transcript_cwd=tmp_path / "other", project=project)
    assert resume_ids.codex_session_id("%17", str(project), "99") is None


def test_a_session_with_no_transcript_is_not(tmp_path, monkeypatch):
    project = tmp_path / "practice"
    project.mkdir()
    _setup(tmp_path, monkeypatch, transcript_cwd=None, project=project)
    assert resume_ids.codex_session_id("%17", str(project), "99") is None
