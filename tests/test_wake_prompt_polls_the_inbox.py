"""A wake turn tells the woken session to read its inbox (goal 87772ac4).

The wake sends a bare Space + Enter into an idle pane, which submits an empty prompt. An empty prompt carries no instruction, so
without help the session would take the turn, see nothing, and go back to sleep with its mail unread. tool-router already runs on
every prompt; on a blank prompt it now polls the practice's inbox and injects the same "Pending mesh messages" block SessionStart
shows, plus a reminder that the Monitor may have expired.

Two properties are tested as black-box behaviour of the real hook, run as a subprocess with a fake `empirica` on PATH:

* the poll happens ONLY for a blank prompt: the router runs on every prompt, so a poll on any other would be a per-prompt cost;
* a poll that fails, or an empty inbox, adds nothing: a wake turn must never be made worse by the check.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROUTER = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/tool-router.py"


def _prop(i):
    return {
        "id": f"prop_wake{i}",
        "source_claude": "empirica.david.ecodex",
        "status": "accepted",
        "type": "collab_brief",
        "title": f"Wake message {i}",
        "summary": f"body {i}",
    }


@pytest.fixture
def box(tmp_path):
    """An isolated HOME, a project with an ai_id, and a fake `empirica` that records each call and prints a canned poll."""
    project = tmp_path / "wakeproj"
    (project / ".empirica").mkdir(parents=True)
    (project / ".empirica" / "project.yaml").write_text("ai_id: wakeproj\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"

    def install(proposals=None, exit_code=0):
        payload = json.dumps(
            {"ok": True, "count": len(proposals or []), "matched": len(proposals or []), "proposals": proposals or []}
        )
        script = bindir / "empirica"
        script.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\ncat <<'EOF'\n{payload}\nEOF\nexit {exit_code}\n")
        script.chmod(0o755)

    def run(prompt, harness=None):
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
        if harness:
            env["EMPIRICA_HARNESS"] = harness
        out = subprocess.run(
            [sys.executable, str(ROUTER)],
            input=json.dumps({"prompt": prompt, "session_id": "wake-sess"}),
            env=env,
            cwd=project,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert out.returncode == 0, out.stderr[-400:]
        return json.loads(out.stdout)

    install.run = run  # type: ignore[attr-defined]
    install.calls = lambda: calls.read_text().splitlines() if calls.exists() else []  # type: ignore[attr-defined]
    return install


def _context(out):
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize("blank", ["", " ", "  \n ", "[wake]", "  [wake] \n"])
def test_a_wake_prompt_injects_the_pending_inbox(box, blank):
    box([_prop(1), _prop(2)])
    ctx = _context(box.run(blank))
    assert "Wake turn" in ctx
    assert "Pending mesh messages (2)" in ctx and "prop_wake1" in ctx and "prop_wake2" in ctx
    assert "re-arm" in ctx.lower(), "the wake turn is also the moment to re-arm an expired Monitor"
    assert any("mailbox poll" in c and "wakeproj" in c for c in box.calls()), box.calls()


@pytest.mark.parametrize(
    "prompt",
    [
        "yes",
        "go",
        "/compact",
        "please look at the failing test in the cockpit",
        "[wake] and also fix the tests",
        "wake",
        "[WAKE]x",
    ],
)
def test_any_other_prompt_never_polls_the_inbox(box, prompt):
    box([_prop(1)])
    out = box.run(prompt)
    assert "Pending mesh messages" not in _context(out) and "Wake turn" not in _context(out)
    assert box.calls() == [], "the router runs on every prompt: no poll outside a wake"


def test_a_wake_with_an_empty_inbox_adds_nothing(box):
    box([])
    assert box.run(" ") == {"continue": True}


def test_a_failing_poll_adds_nothing(box):
    box([_prop(1)], exit_code=1)
    assert box.run(" ") == {"continue": True}


def test_other_harnesses_keep_their_own_output_shape(box):
    box([_prop(1)])
    out = box.run(" ", harness="ecodex")
    assert "Pending mesh messages (1)" in out.get("context", "") and "hookSpecificOutput" not in out


def test_the_control_the_fake_empirica_is_actually_called(box):
    """A poll that never ran would also add nothing; the other tests only mean something if the fake is reachable."""
    box([_prop(1)])
    assert "Pending mesh messages" in _context(box.run(" "))
    assert len(box.calls()) == 1


def test_the_router_and_the_wake_agree_on_the_marker():
    """The hook is a standalone script and keeps its own copy of the marker; the two must not drift apart."""
    import importlib.util

    from empirica.core.cockpit.instance_actions import WAKE_MARKER

    spec = importlib.util.spec_from_file_location("tool_router", ROUTER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._WAKE_MARKER == WAKE_MARKER


def test_the_wake_text_names_the_actions_and_says_to_stop(box):
    """Live resume trial, 2026-10-24: the woken outreach session did the two things asked and then kept going for 59 s (git status
    on another repo, a finding, a full status report). A wake is meant to be cheap, so the block spells out the actions (mesh-support
    asked for this too: a resumed pane may not still hold the arm text) and ends the turn."""
    box([_prop(1)])
    ctx = _context(box.run("[wake]"))
    assert "empirica listener on --dry-run" in ctx and "empirica listener arm" in ctx, (
        "the arm steps are named, not left to session-start text"
    )
    assert "empirica mailbox poll" in ctx
    low = ctx.lower()
    assert "then stop" in low, "the turn must end after the asked actions"
    assert "status report" in low, "an unasked status report was the main waste"
