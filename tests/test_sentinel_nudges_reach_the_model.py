"""Sentinel's nudges ride additionalContext, the channel the model reads.

Measured 2026-09-18 (headless `claude -p`, project-only settings, one token per
channel, a positive control proving the hook ran): on PreToolUse "allow",
Claude Code discards permissionDecisionReason before the model sees it, while
additionalContext in the same response arrives. Sentinel put every nudge in
the reason, so none had ever reached the model.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def gate():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    try:
        spec = importlib.util.spec_from_file_location("sentinel_gate_nudges", _HOOK_DIR / "sentinel-gate.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(str(_HOOK_DIR.parent / "lib"))


def test_an_allow_with_a_nudge_carries_it_in_additional_context(gate, capsys, monkeypatch):
    monkeypatch.setattr(gate, "_goalless_nudge", "no goal attached to this transaction")
    gate.respond("allow", "Safe read")
    hso = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert "no goal attached" in hso["additionalContext"]


def test_a_quiet_allow_adds_nothing_and_stays_suppressed(gate, capsys, monkeypatch):
    for name in (
        "_autonomy_nudge",
        "_goalless_nudge",
        "_remote_ops_nudge",
        "_worktype_nudge",
        "_file_relevance_nudge",
    ):
        monkeypatch.setattr(gate, name, "")
    gate.respond("allow", "Safe read")
    out = json.loads(capsys.readouterr().out)
    assert "additionalContext" not in out["hookSpecificOutput"]
    assert out.get("suppressOutput") is True
