"""Every PreToolUse decision the Sentinel makes is one line in the practice's decisions.jsonl.

Asked for by ecodex (prop_35xy4qkfgvcvph6rqpbzpbueey): the labels a local decision model learns from, and the 1.15 audit trail.
The line names the decision, the stage that made it, the mode and vocabulary it was made in, and what the call was, without the
call's text: a command or a file body can hold a secret, so the input is kept only as a hash. Writing it is one append, with no
database or network on the tool path. It is not tamper-proof against the agent's own shell, and says so in the module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from empirica.core import decision_record as dr

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "sentinel-gate.py"


# ── the record ────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_record_carries_no_raw_input():
    command = "export TOKEN=" + "ghp_" + "a" * 36 + " && git push"  # built, so no scanner reads it as a real token
    rec = dr.build(
        tool_name="Bash",
        tool_input={"command": command},
        decision="deny",
        rule="pipeline",
        tool_use_id="toolu_1",
        claude_session_id="s",
        transaction_id="t",
        tx_status="open",
        harness="claude-code",
    )

    line = json.dumps(rec)
    assert "ghp_" not in line and "git push" not in line
    assert rec["input_sha256"] == dr.input_hash({"command": command})
    assert len(rec["input_sha256"]) == 64


def test_the_record_names_mode_vocabulary_and_what_it_cannot_see():
    rec = dr.build(tool_name="Edit", tool_input={"file_path": "/x"}, decision="allow", rule="noetic-firewall")

    assert rec["mode"] == "strict"
    assert rec["vocabulary"] == dr.VOCABULARY_VERSION
    assert rec["effect_class"] is None and rec["human_override"] is None
    assert rec["command_class"] == "Edit"


@pytest.mark.parametrize(
    "command, expected",
    [
        ("git status", "git"),
        ("/usr/bin/git push origin develop", "git"),
        ("FOO=1 BAR=2 pytest -q", "pytest"),
        ("sudo rm -rf build", "rm"),
        ("cd /tmp && ls", "cd"),
        ("", "Bash"),
        ("echo 'unterminated", "echo"),
    ],
)
def test_the_command_class_is_the_program_not_the_command(command, expected):
    assert dr.command_class("Bash", {"command": command}) == expected


def test_append_writes_one_line_per_call_and_creates_the_directory(tmp_path):
    path = tmp_path / "p" / ".empirica" / "decisions.jsonl"
    for i in range(3):
        assert dr.append({"n": i}, path) is True

    assert [json.loads(line)["n"] for line in path.read_text().splitlines()] == [0, 1, 2]


def test_a_full_file_rolls_over_to_one_backup(tmp_path):
    path = tmp_path / "decisions.jsonl"
    dr.append({"n": 0}, path, max_bytes=5)
    dr.append({"n": 1}, path, max_bytes=5)  # the 8-byte file is over the cap: it moves to .1 first

    assert json.loads(path.read_text()) == {"n": 1}
    assert json.loads(path.with_suffix(".jsonl.1").read_text()) == {"n": 0}


def test_an_unwritable_place_is_reported_as_false_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")

    assert dr.append({"n": 0}, blocker / "decisions.jsonl") is False


def test_the_path_is_the_practice_or_the_seat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert dr.record_path(str(tmp_path / "proj")) == tmp_path / "proj" / ".empirica" / "decisions.jsonl"
    assert dr.record_path(None) == tmp_path / "home" / ".empirica" / "decisions.jsonl"


@pytest.mark.parametrize("value, on", [(None, True), ("1", True), ("0", False), ("off", False), ("false", False)])
def test_it_can_be_turned_off(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("EMPIRICA_DECISION_RECORD", raising=False)
    else:
        monkeypatch.setenv("EMPIRICA_DECISION_RECORD", value)

    assert dr.enabled() is on


# ── the gate writes it ────────────────────────────────────────────────────────────────────────────────────────────────


def _run_gate(tmp_path: Path, tool_input: dict, tool_name: str = "Bash", **env_over: str):
    home = tmp_path / "home"
    work = tmp_path / "work"
    (home / ".empirica").mkdir(parents=True)
    work.mkdir()
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), **env_over}
    hook_input = {
        "hook_event_name": "PreToolUse",
        "session_id": "00000000-0000-0000-0000-00000000abcd",
        "tool_use_id": "toolu_probe",
        "permission_mode": "acceptEdits",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(work),
    }
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        cwd=work,
        env=env,
        timeout=60,
    )
    return proc, home / ".empirica" / "decisions.jsonl"


def test_the_gate_records_its_decision(tmp_path):
    proc, path = _run_gate(tmp_path, {"command": "git status --short"})

    decision = json.loads(proc.stdout.strip().splitlines()[-1])["hookSpecificOutput"]["permissionDecision"]
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["decision"] == decision
    assert rec["tool"] == "Bash" and rec["command_class"] == "git"
    assert rec["tool_use_id"] == "toolu_probe"
    assert rec["permission_mode"] == "acceptEdits"  # the harness's mode, as it sent it
    assert rec["rule"]
    assert "status --short" not in lines[0]


def test_the_gate_records_nothing_when_turned_off(tmp_path):
    _, path = _run_gate(tmp_path, {"command": "git status"}, EMPIRICA_DECISION_RECORD="0")

    assert not path.exists()
