"""When the Sentinel cannot import empirica it still allows, but it says so.

It used to allow with suppressOutput, so a seat whose hook interpreter lacked
empirica had its Sentinel off with no trace anywhere. Reproduced 2026-09-25 on a
simulated pipx-only seat. The first call in a Claude session now carries the
reason to the user and the model, later ones stay quiet, a marker records it for
doctor, and a working gate clears the marker.

The gate runs in a subprocess under this interpreter with `empirica` blocked
from importing, not under /usr/bin/python3, whose contents depend on the box.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "sentinel-gate.py"
)
_BLOCKED = f"import runpy, sys; sys.modules['empirica'] = None; runpy.run_path({str(GATE)!r}, run_name='__main__')"


def _gate(home: Path, session: str, blocked: bool = True) -> dict:
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/tmp/x.py"},
        "session_id": session,
    }
    argv = [sys.executable, "-c", _BLOCKED] if blocked else [sys.executable, str(GATE)]
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    r = subprocess.run(argv, input=json.dumps(event), capture_output=True, text=True, env=env, timeout=60, check=False)
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".empirica").mkdir()
    return tmp_path


def test_the_first_call_in_a_session_is_visible_and_still_allows(home):
    out = _gate(home, "s1")
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow", "a broken install must not lock the session out"
    assert "Sentinel is OFF" in out.get("systemMessage", "")
    assert "Sentinel is OFF" in out["hookSpecificOutput"].get("additionalContext", "")
    marker = json.loads((home / ".empirica" / "sentinel_unavailable.json").read_text())
    assert marker["claude_session_id"] == "s1"


def test_later_calls_in_the_same_session_stay_quiet(home):
    _gate(home, "s1")
    out = _gate(home, "s1")
    assert "systemMessage" not in out and out.get("suppressOutput") is True


def test_a_new_session_is_told_again(home):
    _gate(home, "s1")
    assert "systemMessage" in _gate(home, "s2")


def test_a_working_gate_clears_the_marker(home):
    """Positive control: the marker reports a CURRENT failure, not a past one."""
    _gate(home, "s1")
    assert (home / ".empirica" / "sentinel_unavailable.json").exists()
    _gate(home, "s1", blocked=False)
    assert not (home / ".empirica" / "sentinel_unavailable.json").exists()
