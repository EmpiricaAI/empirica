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


# ── the outcome half ──────────────────────────────────────────────────────────────────────────────────────────────────

HOOKS = GATE.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".empirica").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.mark.parametrize(
    "relative, expected",
    [
        ("proj/src/app.py", "project_write"),
        ("proj/.empirica/project.yaml", "empirica_state"),
        ("home/.empirica/cockpit/config.yaml", "empirica_state"),
        ("home/.claude/settings.json", "config_write"),
        ("home/.codex/config.toml", "config_write"),
        ("home/.config/tmux/tmux.conf", "config_write"),
        ("home/.bashrc", "config_write"),
        ("elsewhere/notes.txt", "outside_write"),
    ],
)
def test_a_file_write_is_classed_by_where_it_lands(tmp_path, home, relative, expected):
    assert dr.path_effect(str(tmp_path / relative), str(tmp_path / "proj")) == expected


def test_a_file_write_outside_any_practice_is_still_classed(tmp_path, home):
    assert dr.path_effect(str(tmp_path / "x" / "a.txt"), None) == "outside_write"


def test_the_practice_comes_from_the_session_then_the_working_directory(tmp_path, home):
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("project_id: p\n")
    (home / ".empirica" / "active_work_s1.json").write_text(json.dumps({"project_path": str(proj)}))

    assert dr.project_path_for("s1", None) == str(proj)
    assert dr.project_path_for("other", str(proj / "src")) is None  # a subdirectory is not the practice root
    assert dr.project_path_for("other", str(proj)) == str(proj)
    assert dr.project_path_for(None, str(tmp_path)) is None


def _post(tmp_path, home, hook: str, payload: dict):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)}
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps({"session_id": "s", "tool_use_id": "toolu_9", "cwd": str(work), **payload}),
        capture_output=True,
        text=True,
        cwd=work,
        env=env,
        timeout=60,
    )
    path = home / ".empirica" / "decisions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_a_successful_edit_records_its_outcome_and_effect(tmp_path, home):
    target = home / ".claude" / "settings.json"
    lines = _post(
        tmp_path,
        home,
        "entity-extractor.py",
        {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(target)}},
    )

    assert len(lines) == 1
    rec = lines[0]
    assert rec["event"] == "post" and rec["tool_use_id"] == "toolu_9"
    assert rec["outcome"] == "success" and rec["effect_class"] == "config_write" and rec["observed"] is True


def test_a_successful_shell_call_records_its_outcome_and_says_the_effect_is_not_observed(tmp_path, home):
    lines = _post(
        tmp_path,
        home,
        "truncation-legibility.py",
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin develop"},
            "tool_response": {"stdout": ""},
        },
    )

    assert [(r["outcome"], r["effect_class"], r["observed"]) for r in lines] == [("success", None, False)]
    assert "git push" not in json.dumps(lines)


@pytest.mark.parametrize("interrupt, outcome", [(False, "failure"), (True, "interrupted")])
def test_a_failed_or_interrupted_call_records_that(tmp_path, home, interrupt, outcome):
    lines = _post(
        tmp_path,
        home,
        "tool-failure.py",
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
            "error": "permission denied",
            "is_interrupt": interrupt,
        },
    )

    assert [(r["outcome"], r["effect_class"]) for r in lines] == [(outcome, None)]


def test_the_three_hooks_carry_the_same_loader():
    """The loader is copied into each hook (a new file under hooks/lib does not reach codex seats); copies must not drift."""
    import re

    pattern = re.compile(r"^def _decision_record\(\):\n(?:(?:    .*|)\n)+", re.M)
    copies = {name: pattern.search((HOOKS / name).read_text()) for name in dr.OUTCOME_HOOKS}
    assert all(copies.values()), [n for n, m in copies.items() if not m]
    assert len({m.group(0) for m in copies.values() if m}) == 1
