"""Mistake retrieval — logged mistakes surface at PREFLIGHT + CHECK.

The audit found mistakes were embedded (type='mistake') but never retrieved — the
attention-nudge orphan (dead_ends surfaced, mistakes didn't). These pin the fix:
retrieve_task_patterns returns `prior_mistakes`, check_against_patterns returns
`mistake_matches`, both parsed from the embedded "{mistake} Prevention: {prevention}"
text exactly like dead_ends parse "DEAD END: ... Why failed: ...".
"""

from __future__ import annotations

import empirica.core.qdrant.pattern_retrieval as pr


def test_adaptive_limits_include_mistakes():
    assert "mistakes" in pr._compute_adaptive_limits(None, 3)  # no-vectors path
    limits = pr._compute_adaptive_limits({"know": 0.3, "uncertainty": 0.7, "context": 0.5}, 3)
    assert "mistakes" in limits and limits["mistakes"] >= 1  # vectors path, scaled by know_gap


def _fake_search(mistake_items):
    def _f(project_id, query_text, memory_type, limit=3, min_score=0.5):
        return mistake_items if memory_type == "mistake" else []

    return _f


def test_retrieve_task_patterns_surfaces_prior_mistakes(monkeypatch):
    monkeypatch.setattr(pr, "get_qdrant_url", lambda: "http://fake")
    monkeypatch.setattr(
        pr,
        "_search_memory_by_type",
        _fake_search([{"text": "Ran narrow ruff Prevention: run full-tree ruff before push", "score": 0.9}]),
    )
    result = pr.retrieve_task_patterns("proj", "pushing a PR", vectors={"know": 0.5, "uncertainty": 0.5})
    pm = result.get("prior_mistakes")
    assert pm and len(pm) == 1
    assert pm[0]["mistake"] == "Ran narrow ruff"
    assert pm[0]["prevention"] == "run full-tree ruff before push"


def test_check_against_patterns_surfaces_mistake_matches(monkeypatch):
    monkeypatch.setattr(pr, "get_qdrant_url", lambda: "http://fake")
    monkeypatch.setattr(
        pr,
        "_search_memory_by_type",
        _fake_search([{"text": "Skipped CI check Prevention: always check CI after push", "score": 0.85}]),
    )
    w = pr.check_against_patterns("proj", current_approach="push without checking CI", vectors=None)
    assert w["mistake_matches"] and len(w["mistake_matches"]) == 1
    assert w["mistake_matches"][0]["mistake"] == "Skipped CI check"
    assert w["mistake_matches"][0]["prevention"] == "always check CI after push"
    assert w["has_warnings"] is True  # mistake_matches alone flips has_warnings


def test_absent_mistakes_are_empty_lists_not_missing(monkeypatch):
    monkeypatch.setattr(pr, "get_qdrant_url", lambda: "http://fake")
    monkeypatch.setattr(pr, "_search_memory_by_type", _fake_search([]))
    assert pr.retrieve_task_patterns("proj", "some task", vectors=None).get("prior_mistakes") == []
    assert pr.check_against_patterns("proj", current_approach="x", vectors=None)["mistake_matches"] == []


def test_no_qdrant_still_returns_prior_mistakes_key(monkeypatch):
    monkeypatch.setattr(pr, "get_qdrant_url", lambda: None)  # qdrant unavailable
    assert "prior_mistakes" in pr.retrieve_task_patterns("proj", "task")
