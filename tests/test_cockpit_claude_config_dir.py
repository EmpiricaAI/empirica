"""The cockpit's Claude Code lookups honour CLAUDE_CONFIG_DIR (1.16.3 sweep A6, goal 9b5ebd62).

Claude Code keeps its transcripts under ``$CLAUDE_CONFIG_DIR/projects`` when that variable is set, else under
``~/.claude/projects``. The conversation lookup (tmux._claude_has_conversation) and the resume-by-id lookup
(resume_ids.last_session_id) read ``~/.claude/projects`` directly, so on a box with the variable set they saw no
conversation at all. Both now go through one helper.
"""

from __future__ import annotations

import json

from empirica.core.cockpit.launcher import resume_ids
from empirica.core.cockpit.launcher import tmux as T

SID = "0f3c9a1e-1111-4222-8333-444455556666"


def test_the_helper_prefers_the_variable_and_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert resume_ids.claude_config_dir() == tmp_path / "home" / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert resume_ids.claude_config_dir() == tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")  # empty is unset, not the working directory
    assert resume_ids.claude_config_dir() == tmp_path / "home" / ".claude"


def _world(tmp_path, monkeypatch):
    home, cfg, work = tmp_path / "home", tmp_path / "cfg", tmp_path / "work"
    (home / ".empirica" / "instance_projects").mkdir(parents=True)
    work.mkdir()
    folder = cfg / "projects" / "-some-folder"
    folder.mkdir(parents=True)
    (folder / f"{SID}.jsonl").write_text(json.dumps({"type": "user", "cwd": str(work), "sessionId": SID}) + "\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return home, work


def test_the_conversation_lookup_reads_the_configured_dir(tmp_path, monkeypatch):
    _home, work = _world(tmp_path, monkeypatch)
    assert T._claude_has_conversation(str(work)) is True


def test_the_resume_lookup_reads_the_configured_dir(tmp_path, monkeypatch):
    home, work = _world(tmp_path, monkeypatch)
    (home / ".empirica" / "instance_projects" / "core.json").write_text(
        json.dumps({"project_path": str(work), "claude_session_id": SID})
    )
    assert resume_ids.last_session_id("core", str(work)) == SID
