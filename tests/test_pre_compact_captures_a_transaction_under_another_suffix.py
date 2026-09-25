"""pre-compact snapshots the transaction the Sentinel would find, with its counters.

It looked only under its own instance suffix, so a transaction PREFLIGHT had
opened from another terminal context (another X11 window, a tmux pane number
that had rotated) was missed: the snapshot carried no transaction, and
post-compact had nothing to restore.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "pre-compact.py"
)


@pytest.fixture
def hook(monkeypatch):
    lib = str(_HOOK.parent.parent / "lib")
    monkeypatch.syspath_prepend(lib)
    import project_resolver

    # This hook process sits in a different X11 window from the PREFLIGHT shell.
    monkeypatch.setattr(project_resolver, "_get_instance_suffix", lambda: "_x11_67108867")
    spec = importlib.util.spec_from_file_location("pre_compact_under_test", _HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _project(tmp_path):
    emp = tmp_path / ".empirica"
    emp.mkdir()
    (emp / "active_transaction_x11_68431093.json").write_text(
        json.dumps({"status": "open", "transaction_id": "tx-1", "session_id": "sess-1"})
    )
    (emp / "hook_counters_x11_68431093.json").write_text(json.dumps({"tool_call_count": 7}))
    return tmp_path


def test_a_transaction_under_another_suffix_is_snapshotted_with_its_counters(hook, tmp_path):
    tx, counters = hook._capture_transaction_state(_project(tmp_path), "sess-1")
    assert tx and tx["transaction_id"] == "tx-1"
    assert counters == {"tool_call_count": 7}, "the counters beside the transaction, not at this hook's suffix"


def test_another_sessions_transaction_is_not_taken(hook, tmp_path):
    """Positive control: the fallback is scoped by session, not a free-for-all."""
    tx, counters = hook._capture_transaction_state(_project(tmp_path), "some-other-session")
    assert tx is None and counters is None
