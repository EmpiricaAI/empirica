#!/usr/bin/env python3
"""Mark a cockpit pane waiting on its person, and tell them (goal dfa8b484). PermissionRequest and Notification hook.

* PermissionRequest (a permission dialog is about to be shown): the pane's waiting mark becomes `approve <tool>`, which the cockpit bar draws on
  the practice tab and under the pane. AskUserQuestion is marked by ruling-shape, which already runs for it.
* Notification of type permission_prompt, elicitation_dialog or agent_needs_input (Claude Code sends permission_prompt when nobody has typed
  for about six seconds after the dialog appeared): a desktop notification, only for a pane that is marked waiting, so a session outside a
  cockpit is never notified by this hook.

The mark is cleared by the hooks that run on the pane's next tool, prompt or turn end (pane_waiting.clear_if_waiting). This hook never
decides: it prints nothing and always exits 0, so the dialog is shown exactly as it would be without it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

NOTIFY_TYPES = frozenset({"permission_prompt", "elicitation_dialog", "agent_needs_input"})


def _helpers():
    spec = importlib.util.spec_from_file_location("_empirica_pane_waiting", Path(__file__).with_name("pane_waiting.py"))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handle(payload: dict, pw) -> str:
    """What was done, for tests: 'marked', 'notified', or ''."""
    pane = pw.current_pane()
    if pane is None:
        return ""
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        return "marked" if pw.set_waiting(pane, pw.label_for(payload.get("tool_name"))) else ""
    if event == "Notification" and payload.get("notification_type") in NOTIFY_TYPES:
        label = pw.waiting_label(pane)
        if label is None:
            return ""
        practice = Path(str(payload.get("cwd") or "")).name or "a practice"
        return "notified" if pw.notify(f"{practice}: {label}", str(payload.get("message") or "")) else ""
    return ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        pw = _helpers()
        if isinstance(payload, dict) and pw is not None:
            handle(payload, pw)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
