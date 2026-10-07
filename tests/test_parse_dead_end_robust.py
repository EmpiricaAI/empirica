"""Test _parse_dead_end robustness with various text formats.

The original code had an off-by-one in a guard: it checked for "Why failed:" but split
on "Why failed: " (with space). If the text had "Why failed:" without a space after,
it would raise IndexError. The helper should never raise and always return valid
approach/why_failed strings.
"""

from __future__ import annotations

import empirica.core.qdrant.pattern_retrieval as pr


def test_parse_dead_end_standard_format():
    """Standard format: DEAD END: <approach> Why failed: <reason>"""
    item = {"text": "DEAD END: tried a shortcut Why failed: it broke the build"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == "tried a shortcut"
    assert result["why_failed"] == "it broke the build"


def test_parse_dead_end_with_em_dash():
    """Format with em-dash separator before Why failed:"""
    item = {"text": "DEAD END: approach — Why failed: reason"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == "approach"
    assert result["why_failed"] == "reason"


def test_parse_dead_end_with_hyphen():
    """Format with hyphen separator before Why failed:"""
    item = {"text": "DEAD END: approach - Why failed: reason"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == "approach"
    assert result["why_failed"] == "reason"


def test_parse_dead_end_extra_whitespace():
    """Extra whitespace around the Why failed separator"""
    item = {"text": "DEAD END: approach   Why failed:   reason"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == "approach"
    assert result["why_failed"] == "reason"


def test_parse_dead_end_alt_prefix_dead_end_approach():
    """Dead end approach: prefix variant"""
    item = {"text": "Dead end approach: tried something Why failed: it failed"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == "tried something"
    assert result["why_failed"] == "it failed"


def test_parse_dead_end_no_why_failed_returns_empty_reason():
    """If Why failed: is missing, return empty why_failed instead of raising"""
    item = {"text": "DEAD END: some approach"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == "some approach"
    assert result["why_failed"] == ""  # Never raises, returns empty


def test_parse_dead_end_why_failed_without_space_after():
    """If Why failed: has no space after (malformed), still doesn't raise"""
    item = {"text": "DEAD END: approach Why failed:reason"}
    result = pr._parse_dead_end(item)
    # Should parse without raising - captures both or returns safely
    assert isinstance(result["approach"], str)
    assert isinstance(result["why_failed"], str)


def test_parse_dead_end_uses_text_full_over_text():
    """Prefers text_full if available"""
    item = {"text_full": "full", "text": "fallback"}
    result = pr._parse_dead_end(item)
    assert "full" in result["approach"]


def test_parse_dead_end_empty_item():
    """Empty or missing text returns empty strings"""
    assert pr._parse_dead_end({}) == {"approach": "", "why_failed": ""}
    assert pr._parse_dead_end({"text": ""}) == {"approach": "", "why_failed": ""}
    assert pr._parse_dead_end({"text": None}) == {"approach": "", "why_failed": ""}


def test_parse_dead_end_only_why_failed():
    """Edge case: only Why failed: in text"""
    item = {"text": "DEAD END: Why failed: reason"}
    result = pr._parse_dead_end(item)
    assert result["approach"] == ""
    assert result["why_failed"] == "reason"


def test_retrieve_task_patterns_never_raises_on_malformed_dead_ends(monkeypatch):
    """PREFLIGHT: malformed dead-end text is parsed robustly, never raises"""
    monkeypatch.setattr(pr, "_retrieval_available", lambda: True)
    monkeypatch.setattr(
        pr,
        "_search_memory_by_type",
        lambda *args, **kwargs: (
            [
                {"text": "DEAD END: attempt1 Why failed: reason1", "score": 0.9},
                {"text": "DEAD END: attempt2", "score": 0.8},  # missing Why failed:
                {"text": "DEAD END: attempt3Why failed:nospace", "score": 0.7},  # malformed
            ]
            if args[2] == "dead_end"
            else []
        ),
    )
    result = pr.retrieve_task_patterns("proj", "test task", vectors=None)
    dead_ends = result.get("dead_ends", [])
    assert len(dead_ends) == 3  # all three parsed, none raised
    assert dead_ends[0]["approach"] == "attempt1"
    assert dead_ends[0]["why_failed"] == "reason1"
    assert dead_ends[1]["approach"] == "attempt2"
    assert dead_ends[1]["why_failed"] == ""  # missing reason is empty, not error
    # third one may vary but must exist and not raise
    assert isinstance(dead_ends[2]["approach"], str)
    assert isinstance(dead_ends[2]["why_failed"], str)


def test_check_against_patterns_never_raises_on_malformed_dead_ends(monkeypatch):
    """CHECK: malformed dead-end text is parsed robustly, never raises"""
    monkeypatch.setattr(pr, "_retrieval_available", lambda: True)
    monkeypatch.setattr(
        pr,
        "_search_memory_by_type",
        lambda *args, **kwargs: (
            [
                {"text": "DEAD END: attempt1 Why failed: reason1", "score": 0.9},
                {"text": "DEAD END: attempt2", "score": 0.8},  # missing Why failed:
            ]
            if args[2] == "dead_end"
            else []
        ),
    )
    result = pr.check_against_patterns("proj", current_approach="test", vectors=None)
    matches = result.get("dead_end_matches", [])
    assert len(matches) == 2  # both parsed
    assert matches[0]["approach"] == "attempt1"
    assert matches[0]["why_failed"] == "reason1"
    assert matches[1]["approach"] == "attempt2"
    assert matches[1]["why_failed"] == ""  # empty, not error
