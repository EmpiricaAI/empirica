"""An item we cannot date must not outrank one created this second.

`calculate_weight` is `impact * type_confidence * recency`, with a 24-hour
half-life — so an 8-month-old artifact should score ~0. Measured behaviour
before this fix:

    today               0.81
    8 months old        0.00
    MISSING timestamp   0.81   <-- identical to today, forever

The chain was `item.get("created_timestamp") or ... or time.time()`, so an
absent field became NOW and pinned the item at maximum recency permanently. The
recency term was therefore inert for every item whose fetch omitted the column
— and `project-bootstrap` returns `goals` with no timestamp field at all.

That is why the same high-impact artifacts appeared in EPISTEMIC FOCUS every
session regardless of age. David asked why 8-month-old artifacts were being
treated as pertinent; this is half the answer. The other half is architectural
and unfixed here: the ranker takes no task_context and runs no semantic match,
so it cannot be about the work in progress at all.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "epistemic_summarizer.py"
)

_EIGHT_MONTHS_AGO = time.time() - (240 * 24 * 3600)


@pytest.fixture(scope="module")
def es():
    spec = importlib.util.spec_from_file_location("epistemic_summarizer", _HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules["epistemic_summarizer"] = module
    spec.loader.exec_module(module)
    return module


def test_a_missing_timestamp_does_not_score_as_new(es):
    """THE BUG. These two were identical at 0.81."""
    fresh = es.calculate_weight({"finding": "x", "impact": 0.9, "created_timestamp": time.time()}, "finding")
    undateable = es.calculate_weight({"finding": "x", "impact": 0.9}, "finding")

    assert undateable < fresh, "an undateable item must not tie with one created this second"
    assert undateable < fresh / 4, "and must not be merely slightly lower"


def test_an_undateable_item_still_outranks_nothing(es):
    """Neutral, not punitive — a missing column should degrade, not invert."""
    undateable = es.calculate_weight({"finding": "x", "impact": 0.9}, "finding")

    assert undateable > 0.0


def test_age_still_decays_normally(es):
    fresh = es.calculate_weight({"finding": "x", "impact": 0.9, "created_timestamp": time.time()}, "finding")
    old = es.calculate_weight({"finding": "x", "impact": 0.9, "created_timestamp": _EIGHT_MONTHS_AGO}, "finding")

    assert old == 0.0, "24h half-life means 8 months is effectively zero"
    assert fresh > 0.5


def test_a_malformed_timestamp_is_treated_as_undateable_not_as_now(es):
    """The except branch used to assign time.time(), with the same effect."""
    fresh = es.calculate_weight({"finding": "x", "impact": 0.9, "created_timestamp": time.time()}, "finding")
    junk = es.calculate_weight({"finding": "x", "impact": 0.9, "created_timestamp": "31/12/2025"}, "finding")

    assert junk < fresh


def test_parseable_string_timestamps_still_work(es):
    """Real rows carry ISO strings; they must date correctly, not fall through."""
    iso_old = es.calculate_weight(
        {"finding": "x", "impact": 0.9, "created_timestamp": "2025-12-31 18:52:10"}, "finding"
    )

    assert iso_old == 0.0, "a parseable old date must decay, not hit the neutral floor"


def test_an_old_high_impact_item_cannot_outrank_a_fresh_low_impact_one(es):
    """The ordering property that actually matters for injected context.

    A stale finding with impact 0.95 was outranking everything logged today.
    """
    stale_important = es.calculate_weight(
        {"finding": "x", "impact": 0.95, "created_timestamp": _EIGHT_MONTHS_AGO}, "finding"
    )
    fresh_minor = es.calculate_weight({"finding": "y", "impact": 0.3, "created_timestamp": time.time()}, "finding")

    assert fresh_minor > stale_important


def test_goals_from_bootstrap_have_no_timestamp_field(es):
    """Pins the real-world trigger, so a future fix to the FETCH is noticed.

    project-bootstrap returns goals with keys that contain no date at all, so
    every goal took the missing-timestamp path. If the fetch is fixed to include
    one, this test should be updated deliberately rather than silently.
    """
    goal_shaped = {"id": "g1", "objective": "do the thing", "status": "in_progress", "subtask_count": 0}

    assert not any("time" in k or "date" in k for k in goal_shaped)
    assert es.calculate_weight(goal_shaped, "goal") == pytest.approx(es.NEUTRAL_RECENCY * 0.5 * 0.75, abs=0.02)
