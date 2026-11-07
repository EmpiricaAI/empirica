"""Whether a person approved a call at the harness's prompt is inferred, never observed, and says so.

ecodex read codex's source (prop_2zqpvjdjwfbghgbi6f2rjtghgm): its PermissionRequest hook fires when codex asks for approval and
carries tool_name, tool_input, permission_mode and turn_id, but no tool_use_id, and never receives the answer. So the hook
writes a permission_request line, and the answer is inferred from what follows: a post line for the same input in the same
turn means it was approved; none, or a failure, means it was denied or aborted. On Claude Code the gate answers allow or deny
itself, so its prompt rarely appears.
"""

from __future__ import annotations

import json

from empirica.core import decision_record as dr

TOOL_INPUT = {"command": "git push origin develop"}


def test_a_permission_request_line_carries_the_join_keys_and_no_input(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    hook_input = {"tool_name": "Bash", "tool_input": TOOL_INPUT, "turn_id": "turn-1", "permission_mode": "default"}

    assert dr.record_permission_request(hook_input) is True
    line = (tmp_path / ".empirica" / "decisions.jsonl").read_text().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "permission_request"
    assert rec["input_sha256"] == dr.input_hash(TOOL_INPUT) and rec["turn_id"] == "turn-1"
    assert rec["permission_mode"] == "default"
    assert "git push" not in line


def test_the_decision_and_post_lines_carry_the_same_join_keys():
    pre = dr.build(tool_name="Bash", tool_input=TOOL_INPUT, decision="allow", rule="pipeline", turn_id="turn-1")
    post = dr.build_outcome({"tool_name": "Bash", "tool_input": TOOL_INPUT, "turn_id": "turn-1"}, "success", None)

    assert pre["turn_id"] == post["turn_id"] == "turn-1"
    assert pre["input_sha256"] == post["input_sha256"] == dr.input_hash(TOOL_INPUT)


def _lines(*events):
    h = dr.input_hash(TOOL_INPUT)
    out = []
    for event, extra in events:
        out.append({"event": event, "input_sha256": h, "turn_id": "turn-1", **extra})
    return out


def test_asked_then_ran_is_approved_inferred():
    lines = _lines(("permission_request", {}), ("post", {"outcome": "success"}))

    assert dr.infer_overrides(lines) == [
        {"turn_id": "turn-1", "input_sha256": dr.input_hash(TOOL_INPUT), "override": "approved", "inferred": True}
    ]


def test_asked_and_nothing_followed_is_denied_or_aborted_inferred():
    assert dr.infer_overrides(_lines(("permission_request", {})))[0]["override"] == "denied_or_aborted"


def test_asked_then_failed_is_denied_or_aborted_inferred():
    lines = _lines(("permission_request", {}), ("post", {"outcome": "failure"}))

    assert dr.infer_overrides(lines)[0]["override"] == "denied_or_aborted"


def test_a_post_in_another_turn_does_not_answer_the_request():
    lines = _lines(("permission_request", {}))
    lines.append(
        {"event": "post", "input_sha256": dr.input_hash(TOOL_INPUT), "turn_id": "turn-2", "outcome": "success"}
    )

    assert dr.infer_overrides(lines)[0]["override"] == "denied_or_aborted"


def test_no_request_means_no_override_to_infer():
    assert dr.infer_overrides(_lines(("post", {"outcome": "success"}))) == []
