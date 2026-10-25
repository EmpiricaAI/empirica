"""The CHECK / PREFLIGHT / POSTFLIGHT Sentinel integration was unreachable and is deleted (sweep item C3, David's ruling 2026-10-25).

`SentinelHooks._enabled` defaults False and was only ever set by a monitor command, inside its own process. The three transaction verbs run
in separate processes, so their `_invoke_sentinel_hook` always returned None and the override branch, the result block and the always-null
`sentinel` key never ran in production. Enabling it would have changed CHECK decisions (the default evaluator can flip proceed to
investigate) against the 2026-04-07 design that the gate reads uncertainty alone, so it was removed rather than switched on.

What stays: the SentinelHooks class itself, because `empirica monitor` uses it live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

HANDLERS = Path(__file__).resolve().parents[1] / "empirica" / "cli" / "command_handlers"
TRANSACTION_VERBS = ("_workflow_check.py", "_workflow_preflight.py", "_workflow_postflight.py", "_workflow_shared.py")


@pytest.mark.parametrize("name", TRANSACTION_VERBS)
def test_the_transaction_verbs_no_longer_reach_for_the_sentinel_hooks(name):
    source = (HANDLERS / name).read_text()
    for token in ("_invoke_sentinel_hook", "SentinelHooks", "SentinelDecision", "_check_apply_sentinel"):
        assert token not in source, f"{name} still mentions {token}"


def test_the_dead_entry_point_is_gone_from_the_shared_module():
    import importlib

    shared = importlib.import_module("empirica.cli.command_handlers._workflow_shared")
    assert not hasattr(shared, "_invoke_sentinel_hook")
    assert "_invoke_sentinel_hook" not in shared.__all__
    assert hasattr(shared, "_get_db_for_session"), "control: the module itself imported and kept its live helpers"


def test_the_monitor_command_still_has_the_class_it_really_uses():
    """Control: the deletion is of the unreachable integration, not of the module a live command depends on."""
    from empirica.core.canonical.empirica_git.sentinel_hooks import SentinelHooks, auto_enable_sentinel

    assert callable(auto_enable_sentinel) and hasattr(SentinelHooks, "turtle_check")
    assert "auto_enable_sentinel" in (HANDLERS / "monitor_commands.py").read_text()


@pytest.mark.parametrize(
    ("know", "uncertainty", "expected"),
    [(0.05, 0.10, "proceed"), (0.99, 0.90, "investigate")],
)
def test_the_check_gate_decides_on_uncertainty_alone(know, uncertainty, expected):
    from empirica.cli.command_handlers._workflow_check import _check_gate_decision

    vectors = {"know": know, "uncertainty": uncertainty}
    _, computed, _, _ = _check_gate_decision(vectors, 0.35, {"recommend_proceed": False}, 1, "investigate")
    assert computed == expected, "know is reported, not gated on"
