"""The breadth nudge must be satisfiable — it never was.

`_signal_artifact_breadth` read ``summary["artifacts"]``, which is the evidence
*source* dict whose values are per-metric sub-dicts, not numbers. Its filter was
``isinstance(v, (int, float)) and v > 0``, so it matched nothing, always. The
narrow warning therefore fired on EVERY transaction and the positive branch
("Good artifact breadth") was unreachable code.

Reported by cortex as "the retrospective gate repeats identically and I learned to
discount it" (SER item 4). It was not repetition — **the predicate could not be
satisfied**, on any input the collector actually produces. A check that cannot pass
is training, not feedback, and what it trains is dismissal of every signal beside
it. Confirmed live: a transaction logging findings, mistakes, assumptions AND
decisions still got "Narrow artifact breadth — consider logging decisions,
assumptions, or dead-ends", naming two types it had just logged and cannot observe.

These tests are written against the SHAPE THE COLLECTOR REALLY EMITS, copied from a
live POSTFLIGHT response. Testing against an invented flat dict is what let the
original defect survive: on a flat dict the old code worked fine.
"""

from __future__ import annotations

import pytest

from empirica.core.post_test.mapper import _signal_artifact_breadth


def _artifacts(findings=0, mistakes=0, dead_ends=0.0, unknowns=0.0, include_unknowns=True):
    """The real evidence-source shape — nested per-metric dicts, not flat counts."""
    art = {
        "mistake_ratio": {"mistakes": mistakes, "findings": findings},
        "productive_exploration_ratio": {"findings_weighted": float(findings), "dead_ends_weighted": dead_ends},
        "project_epistemic_depth": {"prior_artifacts": 5973},
        "session_accumulated_context": {"completed_transactions": 1},
    }
    if include_unknowns:
        art["unknown_resolution_ratio"] = {"total_weighted": unknowns, "resolved_weighted": 0.0}
    return {"artifacts": art}


# ── THE regression ────────────────────────────────────────────────────


def test_a_broad_transaction_is_not_called_narrow():
    """The exact live payload that triggered this fix: findings + mistakes present,
    and the old code still said "Narrow"."""
    out = _signal_artifact_breadth(_artifacts(findings=7, mistakes=1, dead_ends=0.0, unknowns=0.3))

    assert out is None or "Narrow" not in out, f"a multi-type transaction must not read as narrow, got {out!r}"


def test_the_positive_branch_is_reachable_at_all():
    """`Good artifact breadth` was unreachable code — nothing could produce it."""
    out = _signal_artifact_breadth(_artifacts(findings=5, mistakes=2, dead_ends=3.0, unknowns=1.0))

    assert out is not None and "Good artifact breadth" in out


def test_a_genuinely_narrow_transaction_is_still_flagged():
    """The fix must not swing to silence — one type only is a real signal."""
    out = _signal_artifact_breadth(_artifacts(findings=6))

    assert out is not None and "Narrow" in out
    assert "findings" in out, "it should name what it DID see"


def test_a_transaction_with_nothing_logged_is_flagged():
    out = _signal_artifact_breadth(_artifacts())

    assert out is not None and "Narrow" in out


# ── it must not recommend what it cannot verify ───────────────────────


def test_it_does_not_advise_logging_types_it_cannot_observe():
    """The old text told you to log decisions and assumptions, while the collector
    emits no metric for either — so complying could not silence it. That is what
    makes a nudge unsatisfiable, and unsatisfiable nudges get tuned out."""
    out = _signal_artifact_breadth(_artifacts(findings=6))

    assert out is not None
    head = out.split("(assumptions")[0]
    assert "consider logging decisions" not in head
    assert "not observable" in out, "the blind spot must be disclosed, not hidden"


def test_it_reports_how_much_it_could_actually_see():
    """A reader must be able to tell a real narrow result from a partial view."""
    out = _signal_artifact_breadth(_artifacts(findings=6))

    assert "measurable here" in out


# ── unmeasurable must not read as narrow ──────────────────────────────


def test_no_artifacts_source_is_silence_not_a_verdict():
    assert _signal_artifact_breadth({}) is None
    assert _signal_artifact_breadth({"artifacts": {}}) is None


def test_an_artifacts_source_with_no_countable_metrics_is_silence():
    """If the collector shape changes again and none of the known metrics are
    present, the signal must go quiet rather than assert a deficiency it never
    measured — which is the exact failure being fixed."""
    out = _signal_artifact_breadth({"artifacts": {"something_unrecognised": {"x": 1}}})

    assert out is None


@pytest.mark.parametrize("junk", [{"mistake_ratio": None}, {"mistake_ratio": 5}, {"mistake_ratio": "nope"}])
def test_malformed_metric_entries_do_not_raise(junk):
    """This runs inside POSTFLIGHT. It must never be the thing that breaks."""
    assert _signal_artifact_breadth({"artifacts": junk}) is None
