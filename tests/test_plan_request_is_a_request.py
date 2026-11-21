"""A prompt is planned work when it asks for planning, not when it uses Empirica's vocabulary (goal b55139fd).

David, 2026-11-21: "I still don't see the trivial tasks not requiring a transaction, in other practices". In discipline mode
a plan-shaped prompt gets no out-of-loop budget, and the classifier marked a prompt plan-shaped when it contained
transaction, preflight, artifact or epistemic: words every prompt in an Empirica practice uses. Over 14 days of typed prompts
on one machine, 29% were marked, 19% only because of those words. That message itself was marked, for "transaction".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/context-shift-tracker.py"
_spec = importlib.util.spec_from_file_location("_t_context_shift_tracker", _HOOK)
tracker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tracker)


@pytest.mark.parametrize(
    "prompt",
    [
        "Sentinel fix is important, I still don't see the trivial tasks not requiring a transaction, in other practices",
        "log the epistemic artifacts from this session",
        "is preflight slow today?",
        "close the transaction and commit",
        "fix the typo in the README",
        "this is a complex one, but just rename the flag",
    ],
)
def test_framework_vocabulary_is_not_a_plan_request(prompt):
    assert not tracker._PLAN_REQUEST.search(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "plan the next three transactions",
        "implement the visibility redesign",
        "write a spec for the listener",
        "break this down into steps",
        "how should I approach the migration?",
        "decompose this into tasks",
        "this is multi-step work",
    ],
)
def test_requests_for_planned_work_still_are(prompt):
    assert tracker._PLAN_REQUEST.search(prompt)


def test_the_nudge_list_still_suggests_structured_planning_for_framework_words():
    assert tracker._COMPLEX_WORK.search("open a transaction for this")
