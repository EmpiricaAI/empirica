"""Rules that live in two places agree on a shared corpus (goal 3fcde812, task 7: copy parity).

A hook runs as a standalone script and cannot always import core, so some rules exist twice. The code said each pair must
agree; nothing checked it. Each test feeds the same inputs to both copies and requires the same answer, so a change to one
copy fails here until the other follows. The redaction parity test is the model.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"


def _load(name: str, alias: str):
    spec = _ilu.spec_from_file_location(alias, HOOKS / name)
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load("sentinel-gate.py", "sentinel_gate_parity")


@pytest.fixture(scope="module")
def router():
    return _load("tool-router.py", "tool_router_parity")


# ── the transaction file: sentinel-gate.py and empirica/utils/session_resolver.py ─────────────────────────────────────

_SCENARIOS = {
    "the claude session outranks the empirica session": [
        ("a", {"claude_session_id": "cc", "session_id": "other", "status": "open", "updated_at": 1}),
        ("b", {"claude_session_id": "x", "session_id": "s", "status": "open", "updated_at": 9}),
    ],
    "the most recent outranks the open one (5fb3398cf)": [
        ("a", {"claude_session_id": "cc", "status": "open", "updated_at": 1}),
        ("b", {"claude_session_id": "cc", "status": "closed", "updated_at": 5}),
    ],
    "open breaks a tie on time": [
        ("a", {"claude_session_id": "cc", "status": "closed", "updated_at": 3}),
        ("b", {"claude_session_id": "cc", "status": "open", "updated_at": 3}),
    ],
    "a file with no updated_at loses to one with it": [
        ("a", {"claude_session_id": "cc", "status": "open"}),
        ("b", {"claude_session_id": "cc", "status": "open", "updated_at": 2}),
    ],
    "an unrelated file is never chosen": [
        ("a", {"claude_session_id": "someone-else", "session_id": "nope", "status": "open", "updated_at": 9}),
    ],
}


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_both_transaction_scans_pick_the_same_file(gate, tmp_path, scenario):
    from empirica.utils import session_resolver

    for suffix, data in _SCENARIOS[scenario]:
        (tmp_path / f"active_transaction_{suffix}.json").write_text(json.dumps(data))
    args = (tmp_path, "_missing", "s", "cc")  # the exact-suffix file does not exist: both fall through to the scan
    core = session_resolver._find_transaction_file(*args)
    hook = gate._find_transaction_file(*args)
    assert (core and core.name) == (hook and hook.name), scenario


# ── the proportionality budget's state file: sentinel-gate.py and tool-router.py ──────────────────────────────────────


@pytest.mark.parametrize("session_id", ["abc-123", "a/b\\c", "x y%z", "", None, "ü-ñ", "../../etc"])
def test_both_hooks_key_the_proportionality_budget_alike(gate, router, session_id):
    assert gate._proportionality_state_path(session_id) == router._proportionality_state_path(session_id)


# ── the Sentinel pause file: sentinel-gate.py and empirica/core/cockpit/sentinel_pause.py ───────────────────────────


@pytest.mark.parametrize("instance_id", ["tmux_6", "x/y", "a%b", "%2/3%", "plain"])
def test_the_cockpit_pauses_the_file_the_gate_reads(gate, monkeypatch, instance_id):
    from empirica.core.cockpit import sentinel_pause

    monkeypatch.setattr(gate, "get_instance_id", lambda: instance_id)
    assert gate.get_pause_file_path().name == sentinel_pause.pause_file_path(instance_id).name


def test_with_no_instance_both_use_the_global_pause_file(gate, monkeypatch):
    from empirica.core.cockpit import sentinel_pause

    monkeypatch.setattr(gate, "get_instance_id", lambda: None)
    assert gate.get_pause_file_path().name == sentinel_pause.pause_file_path(None).name
