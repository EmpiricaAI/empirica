"""Tests for the context budget on the bootstrap global-learnings injection.

`_query_global_learnings` runs at EVERY project-bootstrap and injects up to 5
cross-project results into the AI's session-start context. #187 budgeted the
pattern-retrieval injection but missed this sibling site; these tests cover the
follow-up trim — per-item text truncation via the shared
``context_budget.truncate_text`` (single source of truth, same EMPIRICA_PATTERN_*
knobs + escape hatch as #187).
"""

from __future__ import annotations

from empirica.cli.command_handlers import project_bootstrap as pb
from empirica.core import context_budget as cb
from empirica.core.qdrant import vector_store

# --- shared truncate_text (single source of truth) -------------------------


def test_truncate_text_passes_short_through():
    assert cb.truncate_text("short", 280) == "short"


def test_truncate_text_truncates_long_at_word_boundary_with_marker():
    text = "word " * 200  # 1000 chars
    out = cb.truncate_text(text, 50)
    assert len(out) < len(text)
    assert out.endswith("chars)")
    assert "…" in out
    assert " …" not in out  # head rstripped — no dangling space before the marker


def test_truncate_text_nonstring_passthrough():
    assert cb.truncate_text(None, 280) is None  # type: ignore[arg-type]
    assert cb.truncate_text(12345, 280) == 12345  # type: ignore[arg-type]


# --- bootstrap injection budgeting -----------------------------------------


def _patch_search(monkeypatch, results):
    def fake_search_global(*_args, **_kwargs):
        return results

    monkeypatch.setattr(vector_store, "search_global", fake_search_global)


def test_query_global_learnings_truncates_item_text(monkeypatch):
    monkeypatch.delenv("EMPIRICA_PATTERN_BUDGET_OFF", raising=False)
    monkeypatch.setenv("EMPIRICA_PATTERN_MAX_ITEM_CHARS", "40")
    long_text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    _patch_search(monkeypatch, [{"text": long_text, "score": 0.9}])

    out = pb._query_global_learnings("some task")

    assert out is not None
    item = out["results"][0]
    assert len(item["text"]) < len(long_text)
    assert item["text"].endswith("chars)")


def test_query_global_learnings_budget_off_keeps_full_text(monkeypatch):
    monkeypatch.setenv("EMPIRICA_PATTERN_BUDGET_OFF", "1")
    monkeypatch.setenv("EMPIRICA_PATTERN_MAX_ITEM_CHARS", "40")
    long_text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    _patch_search(monkeypatch, [{"text": long_text, "score": 0.9}])

    out = pb._query_global_learnings("some task")

    assert out is not None
    assert out["results"][0]["text"] == long_text  # untrimmed


def test_query_global_learnings_none_when_empty(monkeypatch):
    _patch_search(monkeypatch, [])
    assert pb._query_global_learnings("some task") is None


def test_query_global_learnings_tolerates_missing_text_field(monkeypatch):
    monkeypatch.delenv("EMPIRICA_PATTERN_BUDGET_OFF", raising=False)
    _patch_search(monkeypatch, [{"score": 0.9}, {"text": "ok", "score": 0.8}])
    out = pb._query_global_learnings("some task")
    assert out is not None
    assert out["count"] == 2
