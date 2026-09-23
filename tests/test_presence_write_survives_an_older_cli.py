"""The presence write must survive a CLI older than the plugin.

The plugin directory is user-global and shared by every practice on a box, while
each practice upgrades its own `empirica` install — so a plugin NEWER than the
CLI is routine, not exotic. session-init passes `--record-build` (1.14); an
older CLI exits 2 on the unknown flag, and with the result unread the whole
presence write was lost, taking `session_pid` with it. That anchor is what keeps
a live-but-quiet session visible, so a session would have read as dead to the
fleet over a build field it did not need.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

HOOK_PATH = (
    Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
)
_spec = importlib.util.spec_from_file_location("session_init_for_presence_test", HOOK_PATH)
assert _spec is not None and _spec.loader is not None
session_init = importlib.util.module_from_spec(_spec)
sys.modules["session_init_for_presence_test"] = session_init
_spec.loader.exec_module(session_init)


class _Result:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _calls(monkeypatch, responder):
    seen: list[list[str]] = []

    def fake_run(cmd, **_kw):
        seen.append(list(cmd))
        return responder(list(cmd))

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_a_current_cli_is_asked_for_the_build(monkeypatch):
    seen = _calls(monkeypatch, lambda _cmd: _Result())
    session_init._write_practitioner_presence("cc-1", "empirica", "sess-1")
    assert len(seen) == 1
    assert "--record-build" in seen[0]
    assert "--session-pid" in seen[0], "the liveness anchor rides the same call"


def test_an_older_cli_gets_the_write_without_the_flag(monkeypatch):
    def responder(cmd):
        if "--record-build" in cmd:
            return _Result(2, "error: unrecognized arguments: --record-build")
        return _Result()

    seen = _calls(monkeypatch, responder)
    session_init._write_practitioner_presence("cc-1", "empirica", "sess-1")
    assert len(seen) == 2, "the write is retried without the flag"
    assert "--record-build" not in seen[1]
    assert "--session-pid" in seen[1], "the anchor survives the retry — that is the point"


def test_a_real_failure_is_reported_and_not_retried(monkeypatch, capsys):
    """Positive control for the retry: only the unknown-flag case retries."""
    seen = _calls(monkeypatch, lambda _cmd: _Result(1, "database is locked"))
    session_init._write_practitioner_presence("cc-1", "empirica", "sess-1")
    assert len(seen) == 1
    assert "database is locked" in capsys.readouterr().err


def test_a_missing_cli_does_not_raise(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise FileNotFoundError("empirica")

    monkeypatch.setattr(subprocess, "run", boom)
    session_init._write_practitioner_presence("cc-1", "empirica", "sess-1")
    assert "presence write skipped" in capsys.readouterr().err
