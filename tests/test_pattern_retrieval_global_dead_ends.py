"""Global dead ends in PREFLIGHT carry the real approach, why_failed and source project.

Found by the 2026-10-06 pipeline sweep (U2): the retrieval read approach/why_failed/project_name off a global hit, but a hit carries
`text` and `project_id` (see global_sync._global_hit), so every peer dead end arrived as one blob in `approach`, an empty
`why_failed` and the constant "other project". The haiku drafts for this swapped the local callers' parser for one that stopped
stripping prefixes and were rated risky; the parser is now shared and the third embed format ("Approach: ...") is handled too.
"""

from __future__ import annotations

import pytest

from empirica.core.qdrant import pattern_retrieval as pr


def _enrich(monkeypatch, hits):
    monkeypatch.setattr("empirica.core.qdrant.vector_store.search_global_dead_ends", lambda query, limit=5: hits)
    result: dict = {}
    pr._enrich_memory_types(result, "p", "a task", {"global_dead_ends": 3, "eidetic": 0, "episodic": 0}, False, False)
    return result.get("global_dead_ends")


@pytest.mark.parametrize(
    "text",
    [
        "Approach: reuse the cache\nWhy failed: it is per-process",  # sync_high_impact_to_global
        "Dead end approach: reuse the cache\nWhy failed: it is per-process",  # the lesson/dead-end sync path
        "DEAD END: reuse the cache Why failed: it is per-process",  # project-embed / rebuild
        "DEAD END: reuse the cache — Why failed: it is per-process",  # the live log path
    ],
)
def test_each_embed_format_is_split_into_approach_and_reason(monkeypatch, text):
    out = _enrich(monkeypatch, [{"text": text, "project_id": "proj-9", "score": 0.7}])
    assert out == [
        {"approach": "reuse the cache", "why_failed": "it is per-process", "project": "proj-9", "score": 0.7}
    ]


def test_text_full_wins_over_a_truncated_preview(monkeypatch):
    long_reason = "r" * 700
    hit = {
        "text": ("Approach: x\nWhy failed: " + long_reason)[:500],
        "text_full": "Approach: x\nWhy failed: " + long_reason,
        "project_id": "p",
        "score": 0.5,
    }
    out = _enrich(monkeypatch, [hit])
    assert out and out[0]["why_failed"] == long_reason


def test_explicit_fields_still_win_when_a_hit_has_them(monkeypatch):
    out = _enrich(
        monkeypatch, [{"approach": "A", "why_failed": "B", "project_name": "named", "text": "ignored", "score": 0.1}]
    )
    assert out == [{"approach": "A", "why_failed": "B", "project": "named", "score": 0.1}]


def test_a_hit_with_no_text_and_no_project_degrades_without_raising(monkeypatch):
    assert _enrich(monkeypatch, [{"score": 0.2}]) == [
        {"approach": "", "why_failed": "", "project": "other project", "score": 0.2}
    ]
