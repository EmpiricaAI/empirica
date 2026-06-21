"""Retrospective soft-gate (Piece 2, Part C) — the breather predicate.

The gate fires at PREFLIGHT when the PREVIOUS transaction made substantive
praxic tool calls but logged ZERO epistemic artifacts on a non-mechanical
work_type — real work with nothing recorded, invisible to grounded calibration.

It is deliberately narrow (not generic PREFLIGHT nagging), SOFT (a response
field, never a hard block), env-toggleable, and clearable via
`retrospective_reason`. These tests lock that behavior down.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers._workflow_preflight import (
    _feedback_compute_retrospective_gate,
)


def _pf_meta(work_type="code", praxic_calls=5, artifact_counts=None):
    """Build a previous-POSTFLIGHT reflex_data blob (pf_meta) for the gate."""
    if artifact_counts is None:
        artifact_counts = {
            "findings": 0,
            "decisions": 0,
            "unknowns": 0,
            "dead_ends": 0,
            "mistakes": 0,
            "assumptions": 0,
        }
    return {
        "work_type": work_type,
        "phase_tool_counts": {"praxic_tool_calls": praxic_calls},
        "retrospective": {"artifact_counts": artifact_counts},
    }


@pytest.fixture(autouse=True)
def _gate_enabled(monkeypatch):
    # Default-on so each test controls the toggle explicitly.
    monkeypatch.setenv("EMPIRICA_RETROSPECTIVE_GATE", "true")


def test_fires_on_real_work_zero_artifacts():
    gate = _feedback_compute_retrospective_gate(_pf_meta(), None)
    assert gate is not None
    assert gate["soft"] is True
    assert gate["acknowledged"] is False
    assert "0 epistemic artifacts" in gate["trigger"]
    assert "breather" in gate


def test_cleared_by_retrospective_reason():
    gate = _feedback_compute_retrospective_gate(_pf_meta(), "pure mechanical rename, no decisions to record")
    assert gate is not None
    assert gate["acknowledged"] is True
    assert gate["retrospective_reason"] == "pure mechanical rename, no decisions to record"
    assert gate["breather"] == "Acknowledged — proceeding. Reason recorded."


def test_no_fire_when_artifacts_logged():
    meta = _pf_meta(artifact_counts={"findings": 2, "decisions": 1})
    assert _feedback_compute_retrospective_gate(meta, None) is None


def test_no_fire_when_no_praxic_activity():
    # Noetic-only transaction (investigation) — zero artifacts is not the
    # high-signal pattern when no praxic work happened.
    assert _feedback_compute_retrospective_gate(_pf_meta(praxic_calls=0), None) is None


def test_exempt_mechanical_work_type_release():
    assert _feedback_compute_retrospective_gate(_pf_meta(work_type="release"), None) is None


def test_no_fire_on_unknown_work_type():
    # work_type=None can't be judged (older POSTFLIGHTs before Part B).
    assert _feedback_compute_retrospective_gate(_pf_meta(work_type=None), None) is None


def test_env_toggle_disables(monkeypatch):
    monkeypatch.setenv("EMPIRICA_RETROSPECTIVE_GATE", "false")
    assert _feedback_compute_retrospective_gate(_pf_meta(), None) is None


def test_zero_artifacts_zero_notes_is_strong():
    gate = _feedback_compute_retrospective_gate(_pf_meta(), None, untriaged_note_count=0)
    assert gate is not None
    assert gate["severity"] == "strong"
    assert gate["untriaged_note_count"] == 0
    assert "finding-log" in gate["breather"]  # "log something" wording


def test_notes_present_softens_to_promote():
    gate = _feedback_compute_retrospective_gate(_pf_meta(), None, untriaged_note_count=3)
    assert gate is not None
    assert gate["severity"] == "soft"
    assert gate["untriaged_note_count"] == 3
    assert "Promote" in gate["breather"]  # "promote your notes" wording


def test_no_pf_meta_is_safe():
    assert _feedback_compute_retrospective_gate(None, None) is None


def test_missing_subkeys_dont_crash():
    # A malformed/partial pf_meta must degrade to "no gate", never raise.
    assert _feedback_compute_retrospective_gate({"work_type": "code"}, None) is None
    assert (
        _feedback_compute_retrospective_gate({"work_type": "code", "phase_tool_counts": {"praxic_tool_calls": 3}}, None)
        is not None
    )  # no retrospective → artifact_total 0 → fires
