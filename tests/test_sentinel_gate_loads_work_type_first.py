"""The gate reads the transaction's work_type BEFORE the noetic firewall classifies the command (gate B#6).

`_current_work_type` was assigned only inside the authorization pipeline, after the firewall had already run, so the
firewall judged every Bash command with work_type None: the infra/config/debug/remote-ops expansions never applied
there and the "set work_type=remote-ops" nudge fired on transactions that had set it. Existing tests monkeypatched the
global that production did not set; these drive the hook's main() with a real transaction file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GATE = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "sentinel-gate.py"
)
SID = "cc-session-wt"


def _run(tmp_path, work_type, command):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    (home / ".empirica").mkdir(parents=True)
    (project / ".empirica").mkdir(parents=True)
    (home / ".empirica" / f"active_work_{SID}.json").write_text(
        json.dumps({"project_path": str(project), "empirica_session_id": "es-wt"})
    )
    (project / ".empirica" / "active_transaction.json").write_text(
        json.dumps(
            {
                "status": "open",
                "transaction_id": "tx-wt",
                "session_id": "es-wt",
                "claude_session_id": SID,
                "preflight_timestamp": 1.0,
                "avg_turns": 10,
                "work_type": work_type,
            }
        )
    )
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": os.pathsep.join(sys.path)}
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": SID}
    out = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )
    assert out.stdout, out.stderr
    return json.loads(out.stdout)["hookSpecificOutput"]


def _context(hso):
    return hso.get("additionalContext", "")


def test_a_remote_ops_transaction_is_not_nudged_to_set_remote_ops(tmp_path):
    hso = _run(tmp_path, "remote-ops", "ssh host 'ls /tmp'")
    assert hso["permissionDecision"] == "allow"
    assert "REMOTE-OPS" not in _context(hso)


def test_a_code_transaction_running_ssh_is_still_nudged(tmp_path):
    hso = _run(tmp_path, "code", "ssh host 'ls /tmp'")
    assert "REMOTE-OPS" in _context(hso) and "'code'" in _context(hso)


def test_the_instance_suffix_is_resolved_once_per_process(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("gate_suffix_once", GATE)
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    calls = []

    import empirica.utils.session_resolver as sr

    with monkeypatch.context() as m:
        m.setattr(sr.InstanceResolver, "instance_suffix", staticmethod(lambda: calls.append(1) or "_x"))
        m.setattr(gate, "_INSTANCE_SUFFIX", None, raising=False)
        assert [gate._instance_suffix() for _ in range(5)] == ["_x"] * 5
    assert len(calls) == 1
