"""The no-transaction nudge adds "complex work detected" when the prompt asks for planned, multi-step work.

It matched substrings, so "inspect", "respect", "special", "explanation" and "airplane" all read as complex work: a nudge
that fires on ordinary words trains the reader to skip it, the one beside it included. Words now match whole (goal 546e7c33).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/context-shift-tracker.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("context_shift_tracker", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _complex(hook, prompt: str) -> bool:
    return "complex work detected" in hook._build_no_transaction_nudge({"prompt": prompt})


@pytest.mark.parametrize(
    "prompt",
    [
        "inspect the log and tell me what failed",
        "respect the special case for empty input",
        "the explanation in the README is wrong",
        "rename airplane_mode to flight_mode",
        "is the plant watering cron still running?",
        "what does the speculative branch do",
        "check the respect of the spec_version field",  # spec_version is an identifier, not a spec
    ],
)
def test_ordinary_words_that_contain_a_signal_do_not_fire(hook, prompt):
    assert not _complex(hook, prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "plan this release",
        "Implement the cockpit resume",
        "planning the next three transactions",
        "write a spec for the visual post-test",
        "break this down into steps",
        "How should I approach the migration?",
        "this is a multi-step change",
        "log the epistemic artifacts",
    ],
)
def test_requests_for_planned_work_still_fire(hook, prompt):
    assert _complex(hook, prompt)
