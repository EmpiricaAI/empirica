"""Relevance is first-class: the focus block must depend on the task.

Before this, `format_epistemic_focus` took no task_context and ranked global
top-N by `impact * type_confidence * recency`. Two sessions doing entirely
unrelated work received identical artifacts, and the block could not be about
the work in progress even in principle.

Two design choices are asserted here because both are load-bearing and both
could reasonably have gone the other way:

1. **Additive blend, not multiplicative.** A multiplicative relevance term
   zeroes out anything the query does not match, which destroys the case that
   matters most — a dead-end from months ago about exactly this task SHOULD
   surface. The objection was never to age; it was to ancient IRRELEVANT
   artifacts crowding out today's work.

2. **Degradation is announced.** A block silently ranked on recency while the
   reader assumes it is task-matched is worse than one that admits it: they
   would trust it for a question it never answered.
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

_NOW = time.time()
_OLD = _NOW - (240 * 24 * 3600)


@pytest.fixture(scope="module")
def es():
    spec = importlib.util.spec_from_file_location("epistemic_summarizer_rel", _HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules["epistemic_summarizer_rel"] = module
    spec.loader.exec_module(module)
    return module


def test_a_relevant_old_artifact_beats_an_irrelevant_fresh_one(es):
    """THE POINT. A dead-end from months ago about THIS task must surface."""
    old_relevant = es.calculate_weight(
        {"approach": "x", "impact": 0.8, "created_timestamp": _OLD}, "dead_end", relevance=0.95
    )
    fresh_irrelevant = es.calculate_weight(
        {"finding": "y", "impact": 0.9, "created_timestamp": _NOW}, "finding", relevance=0.05
    )

    assert old_relevant > fresh_irrelevant


def test_an_irrelevant_ancient_artifact_ranks_near_zero(es):
    """David's actual complaint: ancient AND irrelevant crowding out the work."""
    assert (
        es.calculate_weight({"finding": "x", "impact": 0.9, "created_timestamp": _OLD}, "finding", relevance=0.05) < 0.1
    )


def test_relevance_does_not_zero_out_an_unmatched_artifact(es):
    """Additive, not multiplicative — relevance 0 must not annihilate the item.

    A multiplicative term would make every artifact the query misses invisible,
    including ones missed because the query was poorly worded.
    """
    unmatched_fresh = es.calculate_weight(
        {"finding": "x", "impact": 0.9, "created_timestamp": _NOW}, "finding", relevance=0.0
    )

    assert unmatched_fresh > 0.0


def test_the_block_differs_for_different_tasks(es, monkeypatch):
    """The property that was FALSE before: output depended only on the store.

    Same artifacts, two task contexts, two different orderings.
    """
    items = [
        {"id": "a", "finding": "how the sentinel gates sed", "impact": 0.7, "created_timestamp": _OLD},
        {"id": "b", "finding": "how mailbox polling filters status", "impact": 0.7, "created_timestamp": _OLD},
    ]

    def fake_fetch(project_id, task_context, limit=25):
        if task_context and "sentinel" in task_context:
            return {"a": 0.95, "b": 0.05}, None
        return {"a": 0.05, "b": 0.95}, None

    monkeypatch.setattr(es, "fetch_relevance", fake_fetch)

    sentinel_block = es.format_epistemic_focus(
        findings=items, unknowns=[], dead_ends=[], goals=[], task_context="sentinel gating", project_id="p"
    )
    mailbox_block = es.format_epistemic_focus(
        findings=items, unknowns=[], dead_ends=[], goals=[], task_context="mailbox status filter", project_id="p"
    )

    assert sentinel_block != mailbox_block, "the block must depend on the task"
    assert sentinel_block.index("sentinel gates sed") < sentinel_block.index("mailbox polling")
    assert mailbox_block.index("mailbox polling") < mailbox_block.index("sentinel gates sed")


def test_missing_task_context_is_announced(es):
    """Silent degradation here would be the worst kind — it shapes attention."""
    block = es.format_epistemic_focus(
        findings=[{"id": "a", "finding": "x", "impact": 0.7, "created_timestamp": _NOW}],
        unknowns=[],
        dead_ends=[],
        goals=[],
    )

    assert "⚠️" in block
    assert "no task_context" in block
    assert "Confidence-Ranked" in block, "the header must not claim relevance ranking it did not do"


def test_a_relevance_ranked_block_says_so(es, monkeypatch):
    monkeypatch.setattr(es, "fetch_relevance", lambda p, t, limit=25: ({"a": 0.9}, None))

    block = es.format_epistemic_focus(
        findings=[{"id": "a", "finding": "x", "impact": 0.7, "created_timestamp": _NOW}],
        unknowns=[],
        dead_ends=[],
        goals=[],
        task_context="something",
        project_id="p",
    )

    assert "Relevance-Ranked" in block
    assert "⚠️" not in block


def test_qdrant_failure_degrades_loudly_not_silently(es, monkeypatch):
    """An empty semantic result is indistinguishable from 'nothing is relevant'."""

    def boom(*a, **k):
        raise ConnectionError("qdrant unreachable")

    monkeypatch.setattr(es, "fetch_relevance", es.fetch_relevance)
    import empirica.core.qdrant.epistemics_store as store

    monkeypatch.setattr(store, "search_epistemics", boom)

    scores, note = es.fetch_relevance("p", "some task")

    assert scores is None
    assert note and "unavailable" in note


def test_empty_semantic_result_is_also_a_degradation(es, monkeypatch):
    import empirica.core.qdrant.epistemics_store as store

    monkeypatch.setattr(store, "search_epistemics", lambda *a, **k: [])

    scores, note = es.fetch_relevance("p", "some task")

    assert scores is None
    assert note and "returned nothing" in note
