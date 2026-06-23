"""Regression: the sentinel firewall must never gate its own escape hatch.

Two David-flagged bugs (ecodex prop_3dih), both in sentinel-gate.py:

1) CRITICAL deadlock — a rushed assessment (short noetic + 0 artifacts) made
   the rush-guard deny EVERY praxic Bash call, INCLUDING the postflight /
   check-submit / doctor needed to clear it. The safe-command escape lived only
   in the no-CHECK-row branch; the has-CHECK-row (rush) branch denied
   unconditionally. Fix: hoist the recovery escape to the TOP of
   _validate_check_record so recovery verbs always pass, in any state.

2) `empirica doctor` was not allow-listed at all → blocked when needed most.

This locks both so the deadlock can't return.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent.parent / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("sentinel_gate_under_test", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sg = _load_hook()


# --- #2: doctor/diagnose are recovery verbs and must be allow-listed --------- #
@pytest.mark.parametrize("cmd", ["empirica doctor", "empirica diagnose"])
def test_diagnostic_recovery_verbs_allow_listed(cmd):
    assert sg.is_safe_empirica_command(cmd) is True


# --- #1: recovery verbs escape the rush-guard regardless of CHECK state ------ #
# The escape is hoisted ABOVE any cursor use, so passing cursor=None proves the
# command short-circuits to allow (None) without ever reaching the rush/deny
# logic. A genuinely praxic command would instead touch the cursor (and here
# raise), so "returns None cleanly" == "escaped the gate".
@pytest.mark.parametrize(
    "command",
    [
        "empirica postflight-submit -",
        "empirica check-submit -",
        "empirica doctor",
        "empirica finding-log --finding x",
        'empirica note "x"',
    ],
)
def test_recovery_verb_escapes_rush_guard(command):
    result = sg._validate_check_record(
        None,
        "sess",
        None,
        0,
        tool_input={"command": command},
        tool_name="Bash",
    )
    assert result is None, f"{command!r} should escape the gate (allow), got {result!r}"


# --- self-heal verbs must escape the rush-guard (self-gated-fix gap) ---------- #
# On a box with a stale hook, the very command that fixes the deadlock
# (setup-claude-code / plugin-sync) must NOT itself be rush-blocked, or there is
# no escape without a manual sentinel-pause.
@pytest.mark.parametrize(
    "cmd",
    [
        "empirica setup-claude-code",
        "empirica setup-claude-code --force",
        "empirica plugin-sync",
        "empirica plugin-version",
    ],
)
def test_self_heal_verbs_allow_listed(cmd):
    assert sg.is_safe_empirica_command(cmd) is True


@pytest.mark.parametrize("command", ["empirica setup-claude-code --force", "empirica plugin-sync"])
def test_self_heal_verb_escapes_rush_guard(command):
    result = sg._validate_check_record(
        None,
        "sess",
        None,
        0,
        tool_input={"command": command},
        tool_name="Bash",
    )
    assert result is None, f"{command!r} (self-heal) must escape the gate, got {result!r}"


def test_noetic_tool_escapes_rush_guard():
    result = sg._validate_check_record(
        None,
        "sess",
        None,
        0,
        tool_input={},
        tool_name="Read",
    )
    assert result is None


def test_non_recovery_bash_is_not_escaped_by_the_hatch():
    # A non-safe praxic command must NOT short-circuit via the escape — it falls
    # through to the real logic, which calls cursor.execute(); with cursor=None
    # that raises AttributeError. If the escape were over-broad it would instead
    # return None. So the raise proves the command reached the real gate.
    with pytest.raises(AttributeError):
        sg._validate_check_record(
            None,
            "sess",
            "tx1",
            0,
            tool_input={"command": "rm -rf /tmp/whatever"},
            tool_name="Bash",
        )


# --- #3: worktree-aware subagent signal -------------------------------------- #
def test_linked_worktree_detected_by_git_file(tmp_path, monkeypatch):
    # Linked worktree: .git is a FILE.
    (tmp_path / ".git").write_text("gitdir: /repo/.git/worktrees/wt1\n")
    monkeypatch.chdir(tmp_path)
    assert sg._in_linked_git_worktree() is True


def test_main_checkout_not_flagged_as_worktree(tmp_path, monkeypatch):
    # Main checkout: .git is a DIRECTORY.
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert sg._in_linked_git_worktree() is False
