"""Tests for the content-aware source-provenance nudge.

Background: prior nudge surfaces (CHECK reminder, POSTFLIGHT retrospective,
system prompt) all proved ineffective — adoption check on 2026-05-11
returned 0/50 of decisions and 0/50 of findings with source_refs populated.
This nudge fires AT the moment of *-log invocation, when the artifact text
itself shows external citation but no source flag is provided.

Covers:
  - Detector: URL detection (with/without trailing punct), empty input
  - has_explicit_source: source_ids, evidence_refs, epistemic_source variants
  - warn helper: silent when no citation, silent when source set, silent on
    json output, silent when env var set; emits when citation + no source
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

from empirica.cli.command_handlers.artifact_log_commands import (
    _detect_external_citations,
    _has_explicit_source,
    _warn_unsourced_citations_if_needed,
)

# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


def test_detect_url_https():
    out = _detect_external_citations("Per https://example.com/spec the answer is X")
    assert len(out) == 1
    assert "https://example.com/spec" in out[0]


def test_detect_url_http():
    out = _detect_external_citations("Posted at http://blog.example.org/post-1")
    assert len(out) == 1
    assert "http://blog.example.org/post-1" in out[0]


def test_detect_url_strips_trailing_punct():
    out = _detect_external_citations("See https://example.com/x.")
    assert "https://example.com/x" in out[0]
    assert not out[0].endswith(".")


def test_detect_multiple_urls_capped_at_three():
    text = " ".join(f"https://ex{i}.com" for i in range(5))
    out = _detect_external_citations(text)
    assert len(out) == 3


def test_detect_no_citations_in_plain_text():
    out = _detect_external_citations("This finding has no external links")
    assert out == []


def test_detect_handles_none_and_empty():
    assert _detect_external_citations(None) == []
    assert _detect_external_citations("") == []


def test_detect_does_not_match_bare_domain():
    """example.com (no protocol) should not trigger — too noisy in plain text."""
    out = _detect_external_citations("see example.com for details")
    assert out == []


# ---------------------------------------------------------------------------
# has_explicit_source
# ---------------------------------------------------------------------------


def test_has_source_true_when_source_ids_set():
    args = SimpleNamespace(source_ids=["abc-123"])
    assert _has_explicit_source(args) is True


def test_has_source_true_when_evidence_refs_set():
    args = SimpleNamespace(evidence_refs=["finding-id"])
    assert _has_explicit_source(args) is True


def test_has_source_true_when_epistemic_source_search():
    args = SimpleNamespace(epistemic_source="search")
    assert _has_explicit_source(args) is True


def test_has_source_true_when_epistemic_source_mixed():
    args = SimpleNamespace(epistemic_source="mixed")
    assert _has_explicit_source(args) is True


def test_has_source_false_when_epistemic_source_intuition():
    """intuition is an honest declaration of 'no external source', not provenance."""
    args = SimpleNamespace(epistemic_source="intuition")
    assert _has_explicit_source(args) is False


def test_has_source_false_when_all_unset():
    args = SimpleNamespace()
    assert _has_explicit_source(args) is False


def test_has_source_false_when_source_ids_empty_list():
    args = SimpleNamespace(source_ids=[])
    assert _has_explicit_source(args) is False


# ---------------------------------------------------------------------------
# Warn helper
# ---------------------------------------------------------------------------


def _capture_stderr_for(args, *texts):
    buf = io.StringIO()
    with patch("sys.stderr", buf):
        _warn_unsourced_citations_if_needed(args, *texts)
    return buf.getvalue()


def test_warn_emits_when_url_detected_and_no_source(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human")
    out = _capture_stderr_for(args, "See https://example.com/spec for details")
    assert "source-provenance" in out
    assert "https://example.com/spec" in out


def test_warn_silent_when_no_citation(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human")
    out = _capture_stderr_for(args, "plain finding text with no urls")
    assert out == ""


def test_warn_silent_when_source_provided(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human", source_ids=["abc-123"])
    out = _capture_stderr_for(args, "Per https://example.com/spec...")
    assert out == ""


def test_warn_silent_when_evidence_provided(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human", evidence_refs=["finding-uuid"])
    out = _capture_stderr_for(args, "Per https://example.com/spec...")
    assert out == ""


def test_warn_silent_when_epistemic_source_search(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human", epistemic_source="search")
    out = _capture_stderr_for(args, "Per https://example.com/spec...")
    assert out == ""


def test_warn_emits_when_epistemic_source_intuition(monkeypatch):
    """intuition is honest 'no source', so the nudge should still fire if a
    URL is in the text — that's a contradiction the AI should resolve."""
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human", epistemic_source="intuition")
    out = _capture_stderr_for(args, "Per https://example.com/spec...")
    assert "source-provenance" in out


def test_warn_silent_when_output_json(monkeypatch):
    """JSON consumers (programmatic callers) shouldn't get stderr noise."""
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="json")
    out = _capture_stderr_for(args, "Per https://example.com/spec...")
    assert out == ""


def test_warn_silent_when_env_suppress_set(monkeypatch):
    monkeypatch.setenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", "1")
    args = SimpleNamespace(output="human")
    out = _capture_stderr_for(args, "Per https://example.com/spec...")
    assert out == ""


def test_warn_combines_multiple_text_fields(monkeypatch):
    """For decisions/dead-ends with multiple text fields, citations from any
    field should be detected and reported."""
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human")
    out = _capture_stderr_for(
        args,
        "plain choice text",
        "rationale: see https://example.com/decision-doc for context",
    )
    assert "https://example.com/decision-doc" in out


def test_warn_message_includes_actionable_remediation(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SUPPRESS_PROVENANCE_NUDGE", raising=False)
    args = SimpleNamespace(output="human")
    out = _capture_stderr_for(args, "see https://example.com/x")
    # Should include all three remediation paths
    assert "source-add" in out
    assert "--epistemic-source" in out
    assert "EMPIRICA_SUPPRESS_PROVENANCE_NUDGE" in out
