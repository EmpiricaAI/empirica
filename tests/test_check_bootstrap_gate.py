"""CHECK's bootstrap gate: when it re-grounds the project, and that an unassessed vector cannot crash it (goal c69effce, slice 3).

`_check_bootstrap_gate` runs first in `check-submit`, on the RAW payload, and decides whether to run `project-bootstrap` before the CHECK
is judged: always when no bootstrap exists, else when `context` is under 0.50 or `uncertainty` over 0.60. The mutation pass over the
CHECK handler (finding 67545968) killed none of 22 sampled mutants of it. Reading it for the pins also found a defect: `vectors.get("context", 0.7)`
returns None when the practitioner sent `"context": null`, and `None < 0.5` raises, so a CHECK that left one vector unassessed errored out
instead of returning a verdict. An unassessed vector is "did not assess" everywhere else (the sentinel's `_assessed`, the monitor,
the diminishing-returns detector); here it now behaves like an absent key and triggers nothing.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers import _workflow_check as wc


@pytest.fixture
def gate(monkeypatch, capsys):
    """Run the gate with the bootstrap status and the bootstrap itself faked; returns (run, calls)."""
    calls: list[str] = []

    def run(vectors, has_bootstrap=True, bootstrap_ok=True):
        monkeypatch.setattr(
            wc, "_check_bootstrap_status", lambda _sid: {"has_bootstrap": has_bootstrap, "marker": "status"}
        )

        def fake_bootstrap(sid):
            calls.append(sid)
            return {"ok": True, "project_id": "p-1"} if bootstrap_ok else {"ok": False, "error": "db locked"}

        monkeypatch.setattr(wc, "_auto_bootstrap", fake_bootstrap)
        status, result = wc._check_bootstrap_gate("sess-1", vectors)
        return status, result, capsys.readouterr().err

    return run, calls


def test_a_bootstrapped_project_with_ordinary_vectors_is_left_alone(gate):
    run, calls = gate
    status, result, err = run({"context": 0.7, "uncertainty": 0.3})
    assert status == {"has_bootstrap": True, "marker": "status"}, "the status is returned as read"
    assert result is None and calls == [] and err == ""


def test_no_bootstrap_at_all_always_runs_one(gate):
    run, calls = gate
    _, result, err = run({"context": 0.9, "uncertainty": 0.1}, has_bootstrap=False)
    assert calls == ["sess-1"] and result == {"ok": True, "project_id": "p-1"}
    assert "initial bootstrap" in err and "project_id=p-1" in err


def test_a_failed_bootstrap_is_reported_and_check_goes_on(gate):
    run, calls = gate
    _, result, err = run({}, has_bootstrap=False, bootstrap_ok=False)
    assert result == {"ok": False, "error": "db locked"} and calls == ["sess-1"]
    assert "db locked" in err and "hollow" in err


@pytest.mark.parametrize(("context", "fires"), [(0.49, True), (0.0, True), (0.50, False), (0.51, False), (1.0, False)])
def test_context_under_one_half_regrounds_and_exactly_one_half_does_not(gate, context, fires):
    run, calls = gate
    _, result, err = run({"context": context, "uncertainty": 0.3})
    assert bool(calls) is fires and (result is not None) is fires
    if fires:
        assert f"low context ({context:.2f} < 0.50)" in err


@pytest.mark.parametrize(
    ("uncertainty", "fires"), [(0.61, True), (1.0, True), (0.60, False), (0.59, False), (0.0, False)]
)
def test_uncertainty_over_point_six_regrounds_and_exactly_point_six_does_not(gate, uncertainty, fires):
    run, calls = gate
    _, _, err = run({"context": 0.7, "uncertainty": uncertainty})
    assert bool(calls) is fires
    if fires:
        assert f"high uncertainty ({uncertainty:.2f} > 0.60)" in err


def test_the_reasons_are_tried_in_order_initial_then_context_then_uncertainty(gate):
    run, _ = gate
    assert "initial bootstrap" in run({"context": 0.1, "uncertainty": 0.9}, has_bootstrap=False)[2]
    err = run({"context": 0.1, "uncertainty": 0.9})[2]
    assert "low context" in err and "high uncertainty" not in err, "one run, one stated reason"


def test_absent_vectors_trigger_nothing(gate):
    run, calls = gate
    assert run({"know": 0.9})[1] is None and run({})[1] is None and calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"vectors": {"context": 0.2}},
        json.dumps({"context": 0.2}),
        json.dumps({"vectors": {"context": 0.2}}),
    ],
)
def test_a_wrapped_or_stringified_payload_is_read_the_same_way(gate, payload):
    run, calls = gate
    run(payload)
    assert calls == ["sess-1"]


@pytest.mark.parametrize("payload", [None, [0.1, 0.9], "not json at all", 7])
def test_a_payload_that_is_not_a_vector_dict_falls_back_to_defaults_and_does_not_crash(gate, payload):
    run, calls = gate
    run(payload)
    assert calls == []


@pytest.mark.parametrize(
    "vectors",
    [
        {"context": None, "uncertainty": 0.3},
        {"context": 0.7, "uncertainty": None},
        {"context": None, "uncertainty": None},
        {"vectors": {"context": None}},
    ],
)
def test_an_unassessed_vector_is_treated_like_an_absent_one_not_a_crash(gate, vectors):
    run, calls = gate
    _, result, _ = run(vectors)
    assert result is None and calls == []


def test_an_unassessed_vector_still_lets_the_other_one_fire(gate):
    run, calls = gate
    run({"context": None, "uncertainty": 0.9})
    assert calls == ["sess-1"]
    calls.clear()
    run({"context": 0.2, "uncertainty": None})
    assert calls == ["sess-1"]


@pytest.mark.parametrize("junk", ["0.2", True, [], {}])
def test_a_value_that_is_not_a_number_is_ignored_rather_than_compared(gate, junk):
    """A string "0.2" used to compare against 0.5 and raise; a bool is not a measurement of context."""
    run, calls = gate
    _, result, _ = run({"context": junk, "uncertainty": junk})
    assert result is None and calls == []
