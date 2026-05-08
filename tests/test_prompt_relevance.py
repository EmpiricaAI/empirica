"""Tests for prompt_relevance — Item 5 (UserPromptSubmit prompt-relevance).

Covers:
- get_prompt_relevant_artifacts: arg guards, length floor, delegates to suggest_links
- format_prompt_relevance_context: empty-list short-circuit, XML wrapping,
  per-item formatting, singular/plural label
- build_prompt_relevance_context: missing project_id, end-to-end with mocked search
"""

from __future__ import annotations

from unittest.mock import patch

from empirica.core.bootstrap.prompt_relevance import (
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TOP_K,
    MIN_PROMPT_LENGTH,
    build_prompt_relevance_context,
    format_prompt_relevance_context,
    get_prompt_relevant_artifacts,
)

# ── Defaults ───────────────────────────────────────────────────────────


def test_default_top_k_is_3():
    """Hot-path budget — keep top-K small."""
    assert DEFAULT_TOP_K == 3


def test_default_threshold_aligns_with_circle_3():
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.65


def test_min_prompt_length_filters_trivial_inputs():
    assert MIN_PROMPT_LENGTH >= 10


# ── get_prompt_relevant_artifacts arg guards ──────────────────────────


def test_returns_empty_for_blank_project_id():
    assert get_prompt_relevant_artifacts("", "what's going on with the project?") == []


def test_returns_empty_for_blank_prompt():
    assert get_prompt_relevant_artifacts("proj-1", "") == []


def test_returns_empty_for_short_prompt():
    """Under MIN_PROMPT_LENGTH the embedding is too sparse to be useful."""
    assert get_prompt_relevant_artifacts("proj-1", "short") == []


def test_delegates_to_suggest_links_for_artifact():
    """Internally we reuse the same Qdrant search machinery as Item 6."""
    fake_results = [{"id": "x", "type": "finding", "summary": "y", "similarity_score": 0.8}]
    with patch(
        "empirica.core.bootstrap.prompt_relevance.suggest_links_for_artifact",
        return_value=fake_results,
    ) as suggest:
        out = get_prompt_relevant_artifacts(
            "proj-1", "a prompt over the length floor", top_k=2,
        )
    assert out == fake_results
    suggest.assert_called_once()
    call = suggest.call_args
    assert call.args[0] == "proj-1"
    assert call.args[1] == "a prompt over the length floor"
    assert call.kwargs["exclude_id"] == ""
    assert call.kwargs["top_k"] == 2


# ── format_prompt_relevance_context ────────────────────────────────────


def test_format_returns_empty_for_no_artifacts():
    assert format_prompt_relevance_context([]) == ""


def test_format_wraps_in_prior_context_tag():
    artifacts = [{"id": "a", "type": "finding", "summary": "x", "similarity_score": 0.9}]
    out = format_prompt_relevance_context(artifacts)
    assert out.startswith("<prior-context>")
    assert out.endswith("</prior-context>")


def test_format_uses_singular_for_count_one():
    artifacts = [{"id": "a", "type": "finding", "summary": "x", "similarity_score": 0.9}]
    out = format_prompt_relevance_context(artifacts)
    assert "1 item from prior" in out
    assert "1 items from prior" not in out


def test_format_uses_plural_for_multiple():
    artifacts = [
        {"id": "a", "type": "finding", "summary": "x", "similarity_score": 0.9},
        {"id": "b", "type": "decision", "summary": "y", "similarity_score": 0.8},
    ]
    out = format_prompt_relevance_context(artifacts)
    assert "2 items from prior" in out


def test_format_includes_type_score_summary_per_item():
    artifacts = [
        {"id": "a", "type": "dead_end", "summary": "tried passport.js", "similarity_score": 0.72},
    ]
    out = format_prompt_relevance_context(artifacts)
    # dead_end → dead-end for human readability
    assert "[dead-end 0.72]" in out
    assert "tried passport.js" in out


def test_format_truncates_summary_to_140_chars():
    artifacts = [
        {"id": "a", "type": "finding", "summary": "x" * 200, "similarity_score": 0.9},
    ]
    out = format_prompt_relevance_context(artifacts)
    # 140-char summary should appear, 200-char input should not
    assert "x" * 140 in out
    assert "x" * 141 not in out


def test_format_handles_missing_score_gracefully():
    artifacts = [
        {"id": "a", "type": "finding", "summary": "no score", "similarity_score": None},
    ]
    out = format_prompt_relevance_context(artifacts)
    assert "[finding]" in out  # no score → no number after type
    assert "no score" in out


def test_format_includes_followup_hint():
    """The block should tell the AI how to drill deeper or anchor edges."""
    artifacts = [{"id": "a", "type": "finding", "summary": "x", "similarity_score": 0.9}]
    out = format_prompt_relevance_context(artifacts)
    assert "project-search" in out
    assert "--related-to" in out


# ── build_prompt_relevance_context end-to-end ──────────────────────────


def test_build_returns_empty_for_missing_project_id():
    assert build_prompt_relevance_context(None, "a substantive prompt here") == ""
    assert build_prompt_relevance_context("", "a substantive prompt here") == ""


def test_build_returns_empty_when_no_matches():
    with patch(
        "empirica.core.bootstrap.prompt_relevance.suggest_links_for_artifact",
        return_value=[],
    ):
        assert build_prompt_relevance_context("proj-1", "a substantive prompt here") == ""


def test_build_wraps_results_in_block():
    fake = [{"id": "x", "type": "finding", "summary": "important", "similarity_score": 0.81}]
    with patch(
        "empirica.core.bootstrap.prompt_relevance.suggest_links_for_artifact",
        return_value=fake,
    ):
        out = build_prompt_relevance_context("proj-1", "a substantive prompt here")
    assert "<prior-context>" in out
    assert "important" in out


def test_build_passes_project_path_to_search():
    """The prompt-relevance helper must thread project_path through so the
    legacy reverse-hash fallback in suggest_links_for_artifact can resolve
    pre-payload-fix Qdrant points."""
    fake = []
    with patch(
        "empirica.core.bootstrap.prompt_relevance.suggest_links_for_artifact",
        return_value=fake,
    ) as suggest:
        build_prompt_relevance_context(
            "proj-1", "a substantive prompt here", project_path="/tmp/proj",
        )
    assert suggest.call_args.kwargs["project_path"] == "/tmp/proj"
