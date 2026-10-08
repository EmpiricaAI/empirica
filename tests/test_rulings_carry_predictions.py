"""Rulings waiting on the user carry the practitioner's predicted answer.

Two mechanical nudges (goal bc7aef96), both advisory, never blocking:
- goals-create warns when a ruling goal's body has no `Predicted:` line;
- the AskUserQuestion hook reminds when a single-select question offers no
  option marked "(Recommended)".
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from empirica.cli.command_handlers.goal_commands import _ruling_prediction_warning

HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "ruling-shape.py"
)


def test_a_ruling_goal_without_a_prediction_warns():
    assert _ruling_prediction_warning("DECISION NEEDED: loosen the pin", "why it matters") is not None
    assert _ruling_prediction_warning("DECISION NEEDED: loosen the pin", None) is not None


def test_a_ruling_goal_with_a_prediction_is_quiet():
    body = "## Why\n...\n**Predicted:** loosen to >=X,<2 — the guard test covers the symbols."
    assert _ruling_prediction_warning("DECISION NEEDED: loosen the pin", body) is None


def test_ordinary_goals_are_never_warned():
    assert _ruling_prediction_warning("Fix the sources-map count", None) is None


def _run_hook(payload: dict) -> str:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, timeout=10
    ).stdout


def _ask(options, multi=False):
    return {
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "q?", "header": "Pin", "multiSelect": multi, "options": options}]},
    }


def test_hook_reminds_when_no_option_is_recommended():
    out = _run_hook(_ask([{"label": "A", "description": ""}, {"label": "B", "description": ""}]))
    hso = json.loads(out)["hookSpecificOutput"]
    assert "Pin" in hso["additionalContext"] and "Recommended" in hso["additionalContext"]
    # Must not decide: "allow" can run AskUserQuestion without showing it.
    assert "permissionDecision" not in hso


def test_hook_is_silent_when_the_prediction_is_marked():
    assert _run_hook(_ask([{"label": "A (Recommended)", "description": ""}, {"label": "B", "description": ""}])) == ""


def test_hook_is_silent_for_multi_select_and_other_tools_and_bad_input():
    assert _run_hook(_ask([{"label": "A"}, {"label": "B"}], multi=True)) == ""
    assert _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == ""
    bad = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, timeout=10)
    assert bad.returncode == 0 and bad.stdout == ""


def test_hook_is_stdlib_only():
    """Hooks run outside the package; importing empirica would break on a bare interpreter."""
    spec = importlib.util.spec_from_file_location("ruling_shape", HOOK)
    assert spec and spec.loader
    src = HOOK.read_text(encoding="utf-8")
    assert "import empirica" not in src and "from empirica" not in src


# ---- the question reaches the gate's counter branch (gate-A#12) -------------------------------------
#
# sentinel-gate is registered for Edit|Write and Bash only, so its AskUserQuestion branch (the
# pending_user_response flag context-shift-tracker reads, and the blocked-presence stamp) never ran.
# ruling-shape IS registered for AskUserQuestion and hands the payload to the gate.


def _isolated_project(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    (home / ".empirica").mkdir(parents=True)
    (project / ".empirica").mkdir(parents=True)
    sid = "cc-session-1"
    (home / ".empirica" / f"active_work_{sid}.json").write_text(
        json.dumps({"project_path": str(project), "empirica_session_id": "es-1"})
    )
    (project / ".empirica" / "active_transaction.json").write_text(
        json.dumps(
            {
                "status": "open",
                "transaction_id": "tx-1",
                "preflight_timestamp": 1.0,
                "avg_turns": 10,
                "claude_session_id": sid,  # the gate finds the transaction by this key when the tty suffix differs
            }
        )
    )
    return home, project, sid


def _run_in(hook, payload, home, tmp_path):
    import os

    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": os.pathsep.join(sys.path)}
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        cwd=str(tmp_path),
    )


def _counters(project):
    files = list((project / ".empirica").glob("hook_counters*.json"))
    return json.loads(files[0].read_text()) if files else {}


def test_an_ask_user_question_sets_the_pending_response_flag(tmp_path):
    home, project, sid = _isolated_project(tmp_path)
    payload = {**_ask([{"label": "A (Recommended)", "description": ""}]), "session_id": sid}
    _run_in(HOOK, payload, home, tmp_path)
    assert _counters(project).get("pending_user_response") is True


def test_another_tool_through_the_hook_leaves_the_flag_alone(tmp_path):
    home, project, sid = _isolated_project(tmp_path)
    _run_in(HOOK, {"tool_name": "Read", "tool_input": {}, "session_id": sid}, home, tmp_path)
    assert "pending_user_response" not in _counters(project)


def test_the_reminder_still_arrives_when_the_gate_is_not_beside_the_hook(tmp_path):
    lone = tmp_path / "lone" / "ruling-shape.py"
    lone.parent.mkdir()
    lone.write_text(HOOK.read_text())
    home, _project, sid = _isolated_project(tmp_path)
    out = _run_in(lone, {**_ask([{"label": "A", "description": ""}]), "session_id": sid}, home, tmp_path)
    assert out.returncode == 0
    assert "Recommended" in json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
