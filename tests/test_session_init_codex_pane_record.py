"""session-init writes ecodex's pane resume record (ecodex prop_bwg6k4wwprgc3jwtnq4fvw443a).

ecodex vendors these hooks verbatim, so the write lives here, gated on the codex harness. The reader is
resume_ids.codex_session_id (98aeecdba): the cockpit resumes a codex pane by its own session, not `resume --last`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

HOOK_PATH = (
    Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
)
_spec = importlib.util.spec_from_file_location("session_init_codex_pane", HOOK_PATH)
assert _spec is not None and _spec.loader is not None
session_init = importlib.util.module_from_spec(_spec)
sys.modules["session_init_codex_pane"] = session_init
_spec.loader.exec_module(session_init)

_SID = "019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b"


def _env(monkeypatch, tmp_path, harness="codex", pane="%17"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_HARNESS", harness)
    monkeypatch.setenv("TMUX_PANE", pane)
    monkeypatch.setattr(
        session_init.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="1794840000\n")
    )


def test_a_codex_session_writes_the_record_the_launcher_reads(tmp_path, monkeypatch):
    from empirica.core.cockpit.launcher.resume_ids import codex_session_id

    _env(monkeypatch, tmp_path)
    project = tmp_path / "practice"
    project.mkdir()
    assert session_init._write_codex_pane_record(project, _SID) is True
    record = json.loads((tmp_path / ".empirica" / "pane_sessions" / "17.json").read_text())
    assert record == {
        "harness": "codex",
        "session_id": _SID,
        "project_path": str(project),
        "tmux_server_start": "1794840000",
    }
    assert codex_session_id("%17", str(project), "1794840000") == _SID  # round-trips through the real reader


def test_a_claude_session_writes_nothing(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, harness="claude-code")
    assert session_init._write_codex_pane_record(tmp_path, _SID) is False
    assert not (tmp_path / ".empirica" / "pane_sessions").exists()


def test_outside_tmux_or_with_a_malformed_pane_nothing_is_written(tmp_path, monkeypatch):
    for pane in ("", "17", "%17/../x"):
        _env(monkeypatch, tmp_path, pane=pane)
        assert session_init._write_codex_pane_record(tmp_path, _SID) is False
    assert not (tmp_path / ".empirica" / "pane_sessions").exists()
