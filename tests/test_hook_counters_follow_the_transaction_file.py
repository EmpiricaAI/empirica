"""The hook counters live beside the transaction file that was FOUND, under its suffix.

Transaction lookup falls back to suffix-mismatched files: a hook whose WINDOWID
differs from the shell that ran PREFLIGHT, or a tmux pane number rotated across
compaction. Every caller then built the counters path from its OWN instance
suffix, so the pair split. Measured on an X11 seat (mesh-support):
active_transaction_x11_68431093.json beside hook_counters_x11_67108867.json.
Anything keyed on the pair (the tool-call count, the autonomy nudge,
task-completed's POSTFLIGHT prompt) then read the wrong file or none.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from empirica.utils.session_resolver import hook_counters_path_for_transaction

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.mark.parametrize(
    ("tx_name", "counters_name"),
    [
        ("active_transaction_x11_68431093.json", "hook_counters_x11_68431093.json"),
        ("active_transaction_tmux_6.json", "hook_counters_tmux_6.json"),
        ("active_transaction.json", "hook_counters.json"),
    ],
)
def test_the_counters_name_is_read_off_the_transaction_file(tmp_path, tx_name, counters_name):
    assert hook_counters_path_for_transaction(tmp_path / tx_name) == tmp_path / counters_name


@pytest.fixture
def gate():
    lib = str(_HOOK_DIR.parent / "lib")
    sys.path.insert(0, lib)
    try:
        spec = importlib.util.spec_from_file_location("sentinel_gate_counters", _HOOK_DIR / "sentinel-gate.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(lib)


def test_the_gate_counts_beside_a_transaction_found_under_another_suffix(gate, tmp_path, monkeypatch):
    """The writer, not just the path helper: the file the gate increments."""
    empirica_dir = tmp_path / ".empirica"
    empirica_dir.mkdir()
    tx_path = empirica_dir / "active_transaction_x11_68431093.json"
    tx_path.write_text(json.dumps({"status": "open", "transaction_id": "tx-1", "preflight_timestamp": 1.0}))

    from empirica.utils.session_resolver import InstanceResolver

    # This process sits in a different X11 window from the shell that ran PREFLIGHT.
    monkeypatch.setattr(InstanceResolver, "instance_suffix", staticmethod(lambda: "_x11_67108867"))
    monkeypatch.setattr(gate, "_resolve_empirica_session_id", lambda _cid: None)
    monkeypatch.setattr(gate, "_locate_transaction_file", lambda *_a: tx_path)

    count, _avg = gate._try_increment_tool_count(None, "Read", {"file_path": "x"})

    assert count == 1
    beside = empirica_dir / "hook_counters_x11_68431093.json"
    assert beside.exists(), "the count must land beside the transaction it belongs to"
    assert json.loads(beside.read_text())["tool_call_count"] == 1
    assert not (empirica_dir / "hook_counters_x11_67108867.json").exists(), "the process suffix must not split the pair"
