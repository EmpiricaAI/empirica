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
