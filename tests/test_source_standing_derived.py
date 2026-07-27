"""Phase 4 — a source's standing is DERIVED from evidence, never stored.

A stored score drifts from the evidence that produced it. Equally important: every
metric must be able to say "unknown", because unreviewed is not good and uncited is
not inaccurate — collapsing absence of evidence into a number is how a metric starts
lying.
"""

from __future__ import annotations

import pytest

from empirica.core.sources.sanctify import derive_standing

NOW = 1_000_000.0
DAY = 86400.0


def _ev(outcome, implicated=False):
    return {"event": "source_outcome", "outcome": outcome, "implicated": implicated}


def test_no_evidence_yields_unknown_not_zero():
    """The load-bearing case: a brand-new source must not look BAD."""
    st = derive_standing([], citation_count=0, last_reviewed_at=None, now=NOW)
    assert st["accuracy"] is None
    assert st["stability"] is None
    assert set(st["unknown"]) == {"accuracy", "stability"}
    assert st["never_reviewed"] is True
    assert st["relevance"]["status"] == "uncited"


def test_confirmed_outcomes_raise_accuracy():
    st = derive_standing([_ev("confirmed"), _ev("confirmed")], 2, NOW - DAY, NOW)
    assert st["accuracy"] == 1.0
    assert st["accuracy_basis"] == {"confirmed": 2, "implicated_failures": 0}


def test_undeclared_failure_does_not_touch_accuracy():
    """The attribution rule, enforced at the metric: an artifact can fail because the
    REASONING was wrong. Only a declared implication may count against the source."""
    st = derive_standing([_ev("confirmed"), _ev("invalidated", implicated=False)], 2, NOW, NOW)
    assert st["accuracy"] == 1.0, "an unattributed failure must not slander the source"
    assert st["accuracy_basis"]["implicated_failures"] == 0


def test_declared_failure_lowers_accuracy():
    st = derive_standing([_ev("confirmed"), _ev("invalidated", implicated=True)], 2, NOW, NOW)
    assert st["accuracy"] == 0.5
    assert st["accuracy_basis"] == {"confirmed": 1, "implicated_failures": 1}


def test_stability_is_separate_from_accuracy():
    """A moving domain is not a wrong source — superseded artifacts lower stability
    but must not be read as inaccuracy."""
    st = derive_standing([_ev("confirmed"), _ev("superseded")], 2, NOW, NOW)
    assert st["stability"] == 0.5
    assert st["accuracy"] == 1.0, "supersession is not a declared failure"


@pytest.mark.parametrize(
    ("reviewed_at", "overdue", "never"),
    [(None, False, True), (NOW - 10 * DAY, False, False), (NOW - 200 * DAY, True, False)],
)
def test_review_age_distinguishes_never_from_stale(reviewed_at, overdue, never):
    st = derive_standing([], 1, reviewed_at, NOW)
    assert st["review_overdue"] is overdue
    assert st["never_reviewed"] is never


def test_non_outcome_log_entries_are_ignored():
    """The log also carries `repointed` and archive events — they must not be read as
    outcomes."""
    st = derive_standing([{"event": "repointed"}, {"event": "archived"}, _ev("confirmed")], 1, NOW, NOW)
    assert st["relevance"]["outcomes_observed"] == 1
    assert st["accuracy"] == 1.0


# ── Phase 5: blindspot propagation ────────────────────────────────────


def test_blindspot_stands_when_premises_hold():
    from empirica.core.sources.sanctify import assess_blindspot_inputs

    r = assess_blindspot_inputs(["a", "b", "c"], invalidated_ids=set())
    assert r["verdict"] == "stands"
    assert r["ratio"] == 0.0


def test_blindspot_flags_stale_inputs_but_never_auto_invalidates():
    """A blindspot can stay TRUE even when a supporting finding was wrong, and
    silently deleting an unknown-unknown is the worst failure direction — the whole
    point is that nobody was looking there. So: flag, re-derive, decide."""
    from empirica.core.sources.sanctify import assess_blindspot_inputs

    r = assess_blindspot_inputs(["a", "b"], invalidated_ids={"a", "b"})
    assert r["verdict"] == "stale_inputs", "flagged"
    assert r["verdict"] != "invalidated", "never auto-invalidated"
    assert "re-derive" in r["recommendation"]


def test_blindspot_without_recorded_premises_is_visibly_unfalsifiable():
    """Pre-migration blindspots recorded no inputs. That is not 'fine' — it is
    unfalsifiable for a different reason and must be surfaced, not treated as sound."""
    from empirica.core.sources.sanctify import assess_blindspot_inputs

    r = assess_blindspot_inputs([], invalidated_ids={"x"})
    assert r["verdict"] == "unknown_provenance"
    assert r["ratio"] is None
    assert "re-scan" in r["recommendation"]


def test_partial_invalidation_below_threshold_still_stands():
    from empirica.core.sources.sanctify import assess_blindspot_inputs

    r = assess_blindspot_inputs(["a", "b", "c", "d"], invalidated_ids={"a"})
    assert r["verdict"] == "stands"
    assert r["invalidated_inputs"] == 1
