"""source-review — record a human/AI verdict on a source (source-lifecycle REVIEW half).

Completes David's check/update/review triad: sources-check DETECTS, source-update
RE-FETCHES, source-review JUDGES. The verb appends a 'reviewed' event to
lifecycle_audit_log and stamps last_reviewed_at + review_verdict. These cover the
pure verdict/event helpers; the handler's DB path is covered by a live smoke in
the PR.
"""

from __future__ import annotations

from empirica.cli.command_handlers.sources_review_commands import (
    _NEXT_ACTION,
    _VALID_VERDICTS,
    _build_review_event,
)


def test_valid_verdicts_are_the_four_lifecycle_states():
    assert set(_VALID_VERDICTS) == {"valid", "stale", "superseded", "irrelevant"}


def test_every_verdict_has_a_next_action_entry():
    # Every verdict must be routable — no verdict without a defined next step.
    for v in _VALID_VERDICTS:
        assert v in _NEXT_ACTION


def test_valid_verdict_routes_to_no_action():
    assert _NEXT_ACTION["valid"] is None


def test_stale_routes_to_source_update():
    assert "source-update" in _NEXT_ACTION["stale"]


def test_superseded_and_irrelevant_route_to_archive():
    assert "source-archive" in _NEXT_ACTION["superseded"]
    assert "source-archive" in _NEXT_ACTION["irrelevant"]


def test_build_review_event_shape():
    ev = _build_review_event("stale", note="outdated", reviewer="empirica", at=123.0)
    assert ev == {
        "event": "reviewed",
        "at": 123.0,
        "verdict": "stale",
        "note": "outdated",
        "reviewer": "empirica",
    }


def test_build_review_event_allows_null_note_and_reviewer():
    ev = _build_review_event("valid", note=None, reviewer=None, at=1.0)
    assert ev["event"] == "reviewed"
    assert ev["note"] is None and ev["reviewer"] is None
    assert ev["verdict"] == "valid"
