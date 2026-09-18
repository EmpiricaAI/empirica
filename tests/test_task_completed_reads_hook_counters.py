"""task-completed reads the tool-call count from the hook counters file.

The counters were split out of the transaction file (single writer per file).
This hook kept reading tool_call_count from the transaction data, got 0, and
so its ">3 tool calls -> request POSTFLIGHT" branch could never run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "task-completed.py"
)


def _mod():
    spec = importlib.util.spec_from_file_location("task_completed_hook", _HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_count_comes_from_the_counters_file_beside_the_transaction(tmp_path):
    mod = _mod()
    tx_file = tmp_path / "active_transaction_tmux_3.json"
    tx_file.write_text(json.dumps({"status": "open", "transaction_id": "t"}))
    (tmp_path / "hook_counters_tmux_3.json").write_text(json.dumps({"tool_call_count": 12}))

    tx = mod._with_hook_counters(json.loads(tx_file.read_text()), tx_file, "_tmux_3")
    assert tx["tool_call_count"] == 12


def test_no_counters_file_means_no_calls_yet(tmp_path):
    mod = _mod()
    tx_file = tmp_path / "active_transaction_tmux_3.json"
    tx = mod._with_hook_counters({"status": "open"}, tx_file, "_tmux_3")
    assert tx.get("tool_call_count", 0) == 0
