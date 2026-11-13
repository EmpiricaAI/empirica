"""A task notification is not a context shift (goal 3fcde812 follow-on to the tool-router corpus replay).

Every user-role message incremented the solicited or unsolicited prompt count that POSTFLIGHT reports as context shifts and
PREFLIGHT echoes ("N unsolicited context shift(s)"), and marked the turn plan-shaped from its text. On this seat notifications
outnumber human prompts. Presence and the context gauge still run: a notification wakes the AI, which is then busy.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

_HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/context-shift-tracker.py"


def _load():
    spec = importlib.util.spec_from_file_location("context_shift_tracker_notif", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(hook, monkeypatch, capsys, tmp_path, prompt):
    tx = tmp_path / "active_transaction_x.json"
    tx.write_text(json.dumps({"status": "open", "transaction_id": "t1"}))
    marked, refreshed = [], []
    monkeypatch.setattr(hook, "_find_transaction_file", lambda sid=None: tx)
    monkeypatch.setattr(hook, "_mark_plan_shaped", lambda p: marked.append(p))
    monkeypatch.setattr(hook, "_refresh_presence", lambda sid: refreshed.append(sid))
    monkeypatch.setattr(hook, "_build_context_usage_output", lambda sid: {})
    monkeypatch.setattr(hook, "_check_artifact_reminder", lambda *a: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": "s1"})))
    hook.main()
    capsys.readouterr()
    from empirica.utils.session_resolver import hook_counters_path_for_transaction

    path = hook_counters_path_for_transaction(tx)
    counters = json.loads(path.read_text()) if path.exists() else {}
    return counters, marked, refreshed


def test_a_notification_is_not_counted_or_marked_but_keeps_presence(tmp_path, monkeypatch, capsys):
    hook = _load()
    counters, marked, refreshed = _run(
        hook, monkeypatch, capsys, tmp_path, "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"
    )
    assert counters.get("unsolicited_prompt_count", 0) == 0 and marked == []
    assert refreshed == ["s1"]


def test_a_person_prompt_is_still_counted(tmp_path, monkeypatch, capsys):
    hook = _load()
    counters, marked, _ = _run(hook, monkeypatch, capsys, tmp_path, "Please also look at <task-notification> handling.")
    assert counters.get("unsolicited_prompt_count") == 1 and marked


def test_both_prompt_hooks_recognise_the_same_notification_starts():
    """The tool router holds its own copy (hooks load no shared module for this): the two must not drift."""
    router = importlib.util.spec_from_file_location("router_for_parity", _HOOK.with_name("tool-router.py"))
    mod = importlib.util.module_from_spec(router)
    router.loader.exec_module(mod)
    assert _load()._NOTIFICATION_STARTS == mod._NOTIFICATION_STARTS
