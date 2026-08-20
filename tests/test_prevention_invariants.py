"""Invariants that would have caught both oracle defects, years earlier than we did.

Both anchoring failures were visible in **one SQL query each**, and had been for
months. Nobody ran them because nothing asked:

    SELECT COUNT(DISTINCT pattern_key), COUNT(DISTINCT subject_key) ...   -> 204, 1
    SELECT outcome, COUNT(*) ... GROUP BY outcome                          -> failed 215, exposed 3

Neither was subtle. Both were unasked. That is the argument for an invariant
rather than for more diligence: a measurement table with no constraint on its own
discriminating power will hold a degenerate distribution indefinitely, and every
consumer will read it as a result.

Each invariant here is asserted against a synthetic pre-fix population as a
NEGATIVE CONTROL. A guard that has never fired is not a guard — the same rule
that made the AST precedence test and the cold-roundtrip test trustworthy.
"""

from __future__ import annotations

import pytest

from empirica.core.prevention.anchoring import anchoring_verdict

DEGENERATE_SHARE = 0.95


def _rows(n, *, outcome="failed", distinct_subjects=1, bound=False):
    out = []
    for i in range(n):
        subj = f"goal:g{i % distinct_subjects}" if bound else f"session:s{i % distinct_subjects}"
        out.append(
            {
                "outcome": outcome,
                "pattern_key": f"p{i}",
                "subject_key": subj,
                "goal_id": f"g{i % distinct_subjects}" if bound else None,
            }
        )
    return out


# ── invariant 1: subjects must grow with patterns ────────────────────────────


def test_patterns_must_not_collapse_onto_one_subject():
    """204 patterns on 1 subject means every pattern was judged on the same failures."""
    pre_fix = _rows(204, distinct_subjects=1, bound=True)
    v = anchoring_verdict(pre_fix)
    assert v["anchored"] is False, "NEGATIVE CONTROL: the pre-fix shape must fail this"
    assert v["distinct_patterns"] == 204
    assert v["distinct_subjects"] == 1


def test_the_same_population_passes_once_subjects_discriminate():
    post_fix = _rows(204, distinct_subjects=204, bound=True)
    v = anchoring_verdict(post_fix)
    assert v["anchored"] is True


def test_a_single_pattern_on_a_single_subject_is_not_degenerate():
    """One pattern legitimately has one subject — the invariant must not fire on it.

    An invariant that fires on a healthy shape gets disabled, and then the
    unhealthy shape passes too.
    """
    v = anchoring_verdict(_rows(1, distinct_subjects=1, bound=True))
    assert v["anchored"] is True


# ── invariant 2: a near-uniform verdict distribution is a suspect ─────────────


def _dominant_share(rows):
    resolved = [r for r in rows if r["outcome"] in ("prevented", "failed")]
    if not resolved:
        return 0.0
    counts: dict[str, int] = {}
    for r in resolved:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return max(counts.values()) / len(resolved)


def test_the_pre_fix_outcome_distribution_is_degenerate():
    """NEGATIVE CONTROL: 215 failed / 0 prevented is 100% one value.

    This is the check that stopped me citing 202/204 to two peers as evidence
    for a hypothesis I already held. A near-uniform value in a measurement table
    is a suspect, not a result.
    """
    pre_fix = _rows(215, outcome="failed", distinct_subjects=1)
    assert _dominant_share(pre_fix) > DEGENERATE_SHARE


def test_a_mixed_distribution_is_not_degenerate():
    mixed = _rows(60, outcome="failed", distinct_subjects=60, bound=True) + _rows(
        40, outcome="prevented", distinct_subjects=40, bound=True
    )
    assert _dominant_share(mixed) <= DEGENERATE_SHARE


@pytest.mark.parametrize("share", [1.0, 0.99, 0.96])
def test_the_threshold_is_where_it_claims_to_be(share):
    """Named so a future tuner sees the tradeoff before loosening it."""
    n = 200
    dominant = int(n * share)
    rows = _rows(dominant, outcome="failed", distinct_subjects=n, bound=True) + _rows(
        n - dominant, outcome="prevented", distinct_subjects=n, bound=True
    )
    assert _dominant_share(rows) > DEGENERATE_SHARE


# ── invariant 3: unmeasurable never enters a denominator ─────────────────────


def test_unmeasurable_is_not_resolved_and_so_cannot_inflate_a_rate():
    """The third verdict exists to keep unjudged rows OUT of rate arithmetic.

    If `unmeasurable` were ever folded into `resolved`, rows the predicate
    explicitly declined to judge would land in the denominator of a rate about
    judgements — reintroducing the defect the verdict was added to remove.
    """
    from empirica.core.prevention.persist import aggregate_prevention_events

    rows = [
        {"outcome": "unmeasurable", "outcome_family": "prevention"},
        {"outcome": "unmeasurable", "outcome_family": "prevention"},
        {"outcome": "prevented", "outcome_family": "prevention"},
    ]
    agg = aggregate_prevention_events(rows)
    assert agg["by_outcome"]["unmeasurable"] == 2, "counted as an observation"
    assert agg["prevention_rate"] == 1.0, "1 prevented of 1 RESOLVED — the two unmeasurable are excluded"


def test_an_all_unmeasurable_population_yields_no_rate():
    from empirica.core.prevention.persist import aggregate_prevention_events

    agg = aggregate_prevention_events([{"outcome": "unmeasurable"} for _ in range(9)])
    assert agg["prevention_rate"] is None
    assert anchoring_verdict([{"outcome": "unmeasurable"} for _ in range(9)])["anchored"] is False
