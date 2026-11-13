"""The tool router treats a task notification as what it is: not a person's prompt (goal 3fcde812, corpus replay).

Replayed over this seat's 12 transcripts (2026-11-13): 18,591 user-role messages were task notifications, more than the 14,723
human prompts, and the agent matcher recommended an agent on 1,431 of them. Each also ran the prompt-relevance search, the
pushback pointer, and reset the probe-first budget a human prompt had armed.
"""

from __future__ import annotations

import importlib.util as _ilu
import io
import json
import sys
from pathlib import Path

NOTIFICATION = (
    "<task-notification>\n<task-id>bx9g35unq</task-id>\n<summary>Monitor event</summary>\n"
    "<event>[Monitor expired after 30m with no events delivered. Re-arm it; check the security of the latency path.]</event>\n"
    "</task-notification>"
)


def _load_hook():
    hook_path = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/tool-router.py"
    spec = _ilu.spec_from_file_location("tool_router_notif", hook_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, monkeypatch, capsys, prompt, session_id="s-notif"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": session_id})))
    monkeypatch.setattr(mod, "get_active_session_vectors", lambda: (None, {}))
    monkeypatch.setattr(mod, "_build_prompt_relevance_block", lambda *a: "")
    mod.main()
    return json.loads(capsys.readouterr().out)


def _context(out: dict) -> str:
    return json.dumps(out)


def test_a_task_notification_gets_no_routing_and_leaves_the_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    mod = _load_hook()
    armed = mod._proportionality_state_path("s-notif")
    armed.parent.mkdir(parents=True)
    armed.write_text('{"remaining": 3}')
    out = _run(mod, monkeypatch, capsys, NOTIFICATION)
    assert "epistemic-routing" not in _context(out) and "epp-check" not in _context(out)
    assert armed.read_text() == '{"remaining": 3}'  # a notification is not the person speaking: the budget stands
    out = _run(
        mod, monkeypatch, capsys, "[SYSTEM NOTIFICATION - NOT USER INPUT]\nThis is an automated background-task event."
    )
    assert "epp-check" not in _context(out)


def test_a_prompt_that_quotes_a_notification_is_still_a_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    mod = _load_hook()
    out = _run(mod, monkeypatch, capsys, "Why did the security review miss this? It printed <task-notification> lines.")
    assert "epp-check" in _context(out)  # the near miss: mentioned mid-text, written by a person
