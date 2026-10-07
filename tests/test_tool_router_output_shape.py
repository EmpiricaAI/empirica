"""tool-router delivers its text where the host harness reads it: hookSpecificOutput.additionalContext on Claude Code.

Phase-1 sweep finding H3 (hooks, high): the router put routing advice, the EPP pointer, the probe-first block and the AAP hedges under a
top-level 'context' key; every sibling UserPromptSubmit hook uses hookSpecificOutput.additionalContext. Measured on the box that
reproduced it: 244 hook-context blocks reached one session's transcript, none from the router, though it runs on every prompt.
Other harnesses (EMPIRICA_HARNESS=ecodex) keep the legacy shape. The hook runs as a real subprocess under an isolated HOME.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROUTER = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/tool-router.py"
HYPOTHESIS = "I think the loader is wrong, can you check quickly whether the config path is the problem here please"


def _run(tmp_path, prompt, harness=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    if harness:
        env["EMPIRICA_HARNESS"] = harness
    out = subprocess.run(
        [sys.executable, str(ROUTER)],
        input=json.dumps({"prompt": prompt, "session_id": "shape-sess"}),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-400:]
    return json.loads(out.stdout)


def test_claude_code_gets_its_text_under_hook_specific_output(tmp_path):
    out = _run(tmp_path, HYPOTHESIS)
    assert out["continue"] is True and "context" not in out
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "Hypothesis" in hso["additionalContext"] and len(hso["additionalContext"]) > 500


@pytest.mark.parametrize("harness", ["claude-code", "CLAUDE-CODE", " claude-code "])
def test_the_default_and_spelling_variants_are_claude_code(tmp_path, harness):
    assert "hookSpecificOutput" in _run(tmp_path, HYPOTHESIS, harness)


@pytest.mark.parametrize("harness", ["ecodex", "codex"])
def test_other_harnesses_keep_the_legacy_top_level_context(tmp_path, harness):
    out = _run(tmp_path, HYPOTHESIS, harness)
    assert "hookSpecificOutput" not in out and out["continue"] is True and "Hypothesis" in out["context"]


@pytest.mark.parametrize("prompt", ["yes", "/compact", "ok go"])
def test_a_prompt_with_nothing_to_add_is_a_bare_continue_in_every_harness(tmp_path, prompt):
    assert _run(tmp_path, prompt) == {"continue": True}
    assert _run(tmp_path, prompt, "ecodex") == {"continue": True}


def test_the_router_and_a_sibling_hook_now_use_the_same_shape(tmp_path):
    """The sibling's shape is read from its source so the two cannot drift apart again unseen."""
    sibling = (ROUTER.parent / "context-shift-tracker.py").read_text()
    assert '"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext"' in sibling
    out = _run(tmp_path, HYPOTHESIS)
    assert set(out["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
