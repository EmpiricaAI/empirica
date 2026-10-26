"""The POSTFLIGHT response carries the reply shape, because that is the moment the reply gets written.

/reporting-discipline was loaded 3 times against ~4,970 POSTFLIGHTs across the seat transcripts: its trigger ("before writing a
reply that reports finished work") asks the model to notice a moment it cannot see from inside, which is the drift the skill is
about. The POSTFLIGHT response is the one place that moment is guaranteed to pass through, so the reminder rides there. A static
string: no I/O on the hot path.
"""

from __future__ import annotations

import pytest

import empirica.cli.command_handlers._workflow_postflight as wp


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(wp, "_postflight_update_memory_hot_cache", lambda *a, **k: None)


def _build(**kw):
    args = {
        "session_id": "s",
        "internal_consistency": "good",
        "deltas": {},
        "trajectory_issues": [],
        "grounded_verification": None,
        "compliance_result": None,
        "compliance_error": None,
        "postflight_grounded_vectors": None,
        "postflight_grounded_rationale": None,
        "vectors": {},
        "resolved_project_path": None,
    }
    args.update(kw)
    return wp._build_postflight_result(**args)


def test_the_response_carries_the_reply_shape():
    shape = _build()["reply_shape"]
    for needle in ("Done", "Next", "Waiting", "predicted answer", "/reporting-discipline"):
        assert needle in shape, f"reply_shape lost {needle!r}"


def test_it_is_present_with_and_without_grounded_verification():
    with_gv = _build(grounded_verification={"evidence_summary": {"x": 1}, "_internal_y": 2})
    assert with_gv["reply_shape"] == _build()["reply_shape"]
    assert "_internal_y" not in (with_gv["calibration"] or {}), "the existing stripping is untouched"


def test_it_is_one_line_so_it_costs_little_per_postflight():
    assert "\n" not in _build()["reply_shape"] and len(_build()["reply_shape"]) < 260


def test_the_existing_keys_are_all_still_there():
    result = _build()
    for key in (
        "ok",
        "session_id",
        "internal_consistency",
        "evidence_summary",
        "deltas",
        "calibration",
    ):
        assert key in result
