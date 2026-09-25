"""subagent-stop's counters write cleans up and then lets an interrupt through.

The write's cleanup handler caught BaseException and returned False, so a
KeyboardInterrupt or SystemExit during the write became a quiet "not added",
which the hook then reported as "(parent tx not found)".
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
    / "subagent-stop.py"
)


@pytest.fixture
def hook(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(_HOOK.parent.parent / "lib"))
    spec = importlib.util.spec_from_file_location("subagent_stop_under_test", _HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from empirica.utils.session_resolver import InstanceResolver

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "active_transaction_t.json").write_text(json.dumps({"status": "open"}))
    monkeypatch.setattr(InstanceResolver, "instance_suffix", staticmethod(lambda: "_t"))
    monkeypatch.setattr(InstanceResolver, "project_path", staticmethod(lambda *_a, **_k: str(tmp_path)))
    return mod


def test_an_ordinary_write_error_is_false_and_leaves_no_temp_file(hook, tmp_path, monkeypatch):
    import os

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    assert hook.add_delegated_work_to_parent(3) is False
    assert not [p for p in (tmp_path / ".empirica").iterdir() if p.name.startswith("tmp")]


def test_an_interrupt_during_the_write_propagates(hook, monkeypatch):
    import os

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        hook.add_delegated_work_to_parent(3)


def test_a_normal_write_counts_the_delegated_calls(hook, tmp_path):
    """Positive control: the path under test really writes."""
    assert hook.add_delegated_work_to_parent(3) is True
    counters = json.loads((tmp_path / ".empirica" / "hook_counters_t.json").read_text())
    assert counters["delegated_tool_calls"] == 3
