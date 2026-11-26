"""'empirica visibility list' and 'visibility show' only read, so the Sentinel lets them run (over-gating, 2026-11-26)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
_spec = importlib.util.spec_from_file_location("sentinel_gate_vis", _PATH)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


@pytest.mark.parametrize(
    "cmd",
    ["empirica visibility list", "empirica visibility list --tier shared --limit 5", "empirica visibility show abc123"],
)
def test_visibility_reads_are_reads(cmd):
    assert gate.empirica_command_only_reads(cmd)


@pytest.mark.parametrize(
    "cmd", ["empirica visibility", "empirica visibility listx", "empirica visibility set abc shared"]
)
def test_near_misses_are_not(cmd):
    assert not gate.empirica_command_only_reads(cmd)
