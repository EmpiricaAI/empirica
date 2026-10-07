"""The proportionality budget resets on every new user prompt, and the block never claims enforcement that did not happen.

Phase-1 sweep finding H4 (high), reproduced on this box's own state: tool-router armed ~/.empirica/state/proportionality_<sid>.json when
a prompt carried a hypothesis marker, and nothing removed it. sentinel-gate counted every Read/Grep/Glob against it for up to an hour
and denied past the limit with a message promising "the next user prompt resets the budget": only another hypothesis-bearing prompt did,
so unrelated later prompts (and short ones like "yes") were denied survey tools. Also: the block said "the Sentinel firewall is now
armed" even when arming was skipped (empty session id, unwritable state). Both hooks are loaded as modules; HOME is a tmp dir.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"
HYPOTHESIS = "I think it might be the config path, quick check please"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"{filename} not importable here: {exc}")
    return mod


@pytest.fixture(scope="module")
def tr():
    return _load("tool_router_budget_reset", "tool-router.py")


@pytest.fixture(scope="module")
def sg():
    return _load("sentinel_gate_budget_reset", "sentinel-gate.py")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _state(tr, sid):
    return tr._proportionality_state_path(sid)


def _count(tr, sid):
    return json.loads(_state(tr, sid).read_text())["tool_count"]


def test_arming_writes_a_zeroed_budget_and_says_so(tr):
    assert tr._arm_proportionality_budget("s1") is True
    assert _count(tr, "s1") == 0


def test_arming_with_no_session_id_writes_nothing_and_says_so(tr, home):
    assert tr._arm_proportionality_budget("") is False
    assert not (home / ".empirica").exists()


def test_arming_into_an_unwritable_state_dir_says_so(tr, home):
    (home / ".empirica").mkdir()
    (home / ".empirica" / "state").write_text("a file where the directory should be")
    assert tr._arm_proportionality_budget("s1") is False


def test_a_prompt_without_a_hypothesis_removes_the_armed_budget(tr):
    tr._arm_proportionality_budget("s1")
    assert tr._sync_proportionality_budget("s1", None) is False
    assert not _state(tr, "s1").exists()


def test_a_new_hypothesis_prompt_resets_the_count_to_zero(tr):
    tr._arm_proportionality_budget("s1")
    data = json.loads(_state(tr, "s1").read_text())
    data["tool_count"] = 4
    _state(tr, "s1").write_text(json.dumps(data))
    assert tr._sync_proportionality_budget("s1", "BLOCK") is True
    assert _count(tr, "s1") == 0


def test_the_reset_touches_only_this_sessions_budget(tr):
    tr._arm_proportionality_budget("s1")
    tr._arm_proportionality_budget("other")
    tr._sync_proportionality_budget("s1", None)
    assert not _state(tr, "s1").exists() and _state(tr, "other").exists()


def test_the_block_claims_enforcement_only_when_a_budget_is_armed(tr):
    block = tr.build_investigation_proportionality_check(HYPOTHESIS)
    assert block and "firewall is now armed" in block
    assert tr._state_the_budget_truthfully(block, True) == block
    unarmed = tr._state_the_budget_truthfully(block, False)
    assert "firewall is now armed" not in unarmed and "DENIED" not in unarmed and "advice only" in unarmed
    assert "NAME the hypothesis" in unarmed  # the advice itself is kept


@pytest.mark.parametrize("prompt", ["yes", "go", "/compact", "ok do it"])
def test_main_resets_the_budget_on_short_prompts_and_slash_commands_too(tr, monkeypatch, capsys, prompt):
    """The early return for short prompts sat above the reset, so 'yes' left the old budget counting."""
    tr._arm_proportionality_budget("s1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": "s1"})))
    tr.main()
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert not _state(tr, "s1").exists()


def test_end_to_end_the_gate_denies_past_the_limit_and_the_next_prompt_lifts_it(tr, sg):
    tr._arm_proportionality_budget("s1")
    results = [sg._check_proportionality_budget({"session_id": "s1"}, "Read") for _ in range(6)]
    assert results[:5] == [None] * 5 and results[5] and "budget exceeded" in results[5]
    assert sg._check_proportionality_budget({"session_id": "s1"}, "Grep")  # still denied within the same turn
    tr._sync_proportionality_budget("s1", None)  # the next user prompt, no hypothesis
    assert sg._check_proportionality_budget({"session_id": "s1"}, "Read") is None


def test_the_gate_ignores_tools_outside_the_budget(tr, sg):
    tr._arm_proportionality_budget("s1")
    assert all(sg._check_proportionality_budget({"session_id": "s1"}, "Bash") is None for _ in range(9))


def test_budget_files_of_ended_sessions_are_pruned_and_live_ones_are_not(tr):
    import os
    import time

    tr._arm_proportionality_budget("old")
    tr._arm_proportionality_budget("recent")
    tr._arm_proportionality_budget("current")
    long_ago = time.time() - 2 * 3600
    os.utime(_state(tr, "old"), (long_ago, long_ago))
    tr._sync_proportionality_budget("current", None)
    assert not _state(tr, "old").exists()  # untouched for 2h: gone
    assert _state(tr, "recent").exists()  # another live session: kept
    assert not _state(tr, "current").exists()  # this prompt's reset
