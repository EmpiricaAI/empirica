"""Pins for the CHECK handler's drift calculation and its readiness-threshold cascade (goal c69effce, slice 2).

The mutation pass over `_workflow_check.py` (finding 67545968) let most mutants of these two functions survive: 7 of 33 died on the existing tests
before this file. `_check_cmd_compute_drift` feeds the drift number shown to the practitioner; `_check_load_dynamic_thresholds` decides the
know/uncertainty bar CHECK holds a practitioner to, from a fixed order of overrides (default, cascade profile, calibration.yaml, Brier-dynamic),
each stage failing back to the one before it. Nothing pinned the order, the defaults, or the fall-backs.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from empirica.cli.command_handlers import _workflow_check as wc

# ── drift ───────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("checkpoints", [[], None])
def test_with_no_checkpoint_there_is_no_drift_and_every_numeric_key_has_a_zero_delta(checkpoints):
    baseline = {"know": 0.6, "clarity": 0.9, "note": "text", "uncertainty": 0.3}
    current, drift, deltas = wc._check_cmd_compute_drift(baseline, checkpoints)
    assert current == baseline
    assert drift == 0.0
    assert deltas == {"know": 0.0, "clarity": 0.0, "uncertainty": 0.0}, (
        "non-numeric values carry no delta; clarity does though it is not a drift key"
    )


def test_drift_is_the_mean_absolute_change_over_the_five_drift_keys_present_on_both_sides():
    baseline = {"know": 0.50, "uncertainty": 0.40, "engagement": 0.90, "impact": 0.50, "completion": 0.00}
    latest = {"know": 0.80, "uncertainty": 0.10, "engagement": 0.85, "impact": 0.50, "completion": 0.50}
    current, drift, deltas = wc._check_cmd_compute_drift(baseline, [{"vectors": latest}])
    assert current == latest
    assert deltas == pytest.approx(
        {"know": 0.30, "uncertainty": -0.30, "engagement": -0.05, "impact": 0.0, "completion": 0.50}
    )
    assert drift == pytest.approx((0.30 + 0.30 + 0.05 + 0.0 + 0.50) / 5), (
        "signs cancel nowhere: drift averages magnitudes"
    )


def test_the_delta_keeps_its_sign_current_minus_baseline():
    _, _, deltas = wc._check_cmd_compute_drift({"know": 0.9}, [{"vectors": {"know": 0.4}}])
    assert deltas == pytest.approx({"know": -0.5})


def test_only_the_newest_checkpoint_counts():
    older = {"vectors": {"know": 0.0}}
    newest = {"vectors": {"know": 0.7}}
    current, drift, _ = wc._check_cmd_compute_drift({"know": 0.5}, [newest, older])
    assert current == {"know": 0.7} and drift == pytest.approx(0.2)


def test_a_key_missing_on_either_side_is_left_out_and_does_not_dilute_the_mean():
    baseline = {"know": 0.5, "uncertainty": 0.4, "impact": 0.5}  # engagement and completion absent here
    latest = {"know": 0.7, "engagement": 0.9, "impact": 0.5}  # uncertainty absent here
    _, drift, deltas = wc._check_cmd_compute_drift(baseline, [{"vectors": latest}])
    assert set(deltas) == {"know", "impact"}
    assert drift == pytest.approx((0.2 + 0.0) / 2), "two keys compared, so the denominator is two"


def test_a_vector_that_is_not_a_drift_key_never_moves_the_drift():
    _, drift, deltas = wc._check_cmd_compute_drift({"clarity": 0.1}, [{"vectors": {"clarity": 0.9}}])
    assert deltas == {} and drift == 0.0


def test_a_checkpoint_without_vectors_means_nothing_to_compare():
    current, drift, deltas = wc._check_cmd_compute_drift({"know": 0.5}, [{"other": 1}])
    assert (current, drift, deltas) == ({}, 0.0, {})


# ── the threshold cascade ───────────────────────────────────────────────────────────────────────────────────────


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE sessions (session_id TEXT, ai_id TEXT)")
        self.conn.execute("INSERT INTO sessions VALUES ('s', 'practice')")

    def close(self):
        self.conn.close()


DYNAMIC: dict[str, Any] = {
    "source": "dynamic",
    "noetic": {
        "brier_score": 0.12,
        "ready_know_threshold": 0.81,
        "ready_uncertainty_threshold": 0.22,
        "brier_reliability": 0.03,
        "brier_resolution": 0.07,
        "threshold_inflation": 0.04,
        "transactions_analyzed": 14,
        "basis": "practitioner",
        "practitioner_points": 9,
    },
}


@pytest.fixture
def cascade(monkeypatch):
    """Run the cascade with the transaction, store, calibration config and Brier computation all faked."""
    import empirica.config.threshold_loader as tl
    import empirica.core.calibration_config as cc
    import empirica.core.post_test.dynamic_thresholds as dt
    import empirica.utils.practitioner_model as pm

    calls: dict = {"loader": 0}

    def run(profile=None, override=None, computed=None, compute_raises=False):
        def fake_compute(ai_id, db, base_thresholds=None, practitioner_model=None):
            calls["base"] = base_thresholds
            if compute_raises:
                raise RuntimeError("no history")
            return computed if computed is not None else {"source": "static"}

        monkeypatch.setattr(dt, "compute_dynamic_thresholds", fake_compute)
        monkeypatch.setattr(pm, "practitioner_model_now", lambda *_a, **_k: "model-x")
        monkeypatch.setattr(cc, "override_thresholds", lambda _p: override if override is not None else {})
        monkeypatch.setattr(wc, "_get_db_for_session", lambda _sid: _DB())
        monkeypatch.setattr(wc.R, "transaction_id", staticmethod(lambda: "tx"))
        monkeypatch.setattr(wc.R, "transaction_read", staticmethod(lambda: {"cascade_profile": profile}))
        monkeypatch.setattr(wc.R, "project_path", staticmethod(lambda: None))
        real = tl.ThresholdLoader.get_instance

        def counting():
            calls["loader"] += 1
            return real()

        monkeypatch.setattr(tl.ThresholdLoader, "get_instance", staticmethod(counting))
        return wc._check_load_dynamic_thresholds("s"), calls

    return run


def test_with_nothing_configured_the_bar_is_point_seven_know_and_point_three_five_uncertainty(cascade):
    (know, unc, info), calls = cascade()
    assert (know, unc) == (0.70, 0.35)
    assert info["basis"]["base_source"] == "default" and info["basis"]["threshold_source"] == "static"
    assert calls["base"] is None, "no profile and no override: the Brier stage gets no base to start from"


@pytest.mark.parametrize("profile", [None, "default"])
def test_the_default_profile_never_consults_the_profile_loader(cascade, profile):
    _, calls = cascade(profile=profile)
    assert calls["loader"] == 0


def test_a_named_profile_does_consult_it_and_sets_the_base(cascade):
    (_, unc, info), calls = cascade(profile="rigorous")
    assert calls["loader"] == 1
    assert unc == pytest.approx(0.20)
    assert info["basis"]["base_source"] == "cascade profile 'rigorous'"
    assert calls["base"]["ready_uncertainty_threshold"] == pytest.approx(0.20)


def test_a_calibration_yaml_value_replaces_the_default_and_is_handed_to_the_brier_stage_as_its_base(cascade):
    (know, unc, info), calls = cascade(override={"ready_uncertainty": 0.28})
    assert (know, unc) == (0.70, 0.28)
    assert info["basis"]["base_source"] == "calibration.yaml ready_uncertainty"
    assert info["basis"]["base_threshold"] == 0.28
    assert calls["base"] == {"ready_uncertainty_threshold": 0.28}


def test_calibration_yaml_wins_over_a_cascade_profile(cascade):
    (_, unc, info), calls = cascade(profile="rigorous", override={"ready_uncertainty": 0.28})
    assert unc == 0.28 and info["basis"]["base_source"] == "calibration.yaml ready_uncertainty"
    assert calls["base"]["ready_uncertainty_threshold"] == 0.28


def test_a_calibration_yaml_without_the_key_changes_nothing(cascade):
    (_, unc, info), calls = cascade(override={"something_else": 1})
    assert unc == 0.35 and info["basis"]["base_source"] == "default" and calls["base"] is None


def test_a_dynamic_result_replaces_both_thresholds_and_reports_every_figure_behind_it(cascade):
    (know, unc, info), _ = cascade(computed=DYNAMIC)
    assert (know, unc) == (0.81, 0.22)
    n = DYNAMIC["noetic"]
    assert {k: info[k] for k in ("source", "brier_score", "brier_reliability", "brier_resolution")} == {
        "source": "dynamic",
        "brier_score": n["brier_score"],
        "brier_reliability": n["brier_reliability"],
        "brier_resolution": n["brier_resolution"],
    }
    assert info["know_threshold"] == 0.81 and info["uncertainty_threshold"] == 0.22
    assert info["threshold_inflation"] == 0.04 and info["transactions_analyzed"] == 14
    assert info["basis"]["threshold_source"] == "dynamic" and info["basis"]["uncertainty_threshold"] == 0.22
    assert info["basis"]["calibration_basis"] == "practitioner" and info["basis"]["practitioner_points"] == 9


def test_dynamic_wins_over_a_calibration_yaml_base_which_it_only_tightens_from(cascade):
    (_, unc, info), calls = cascade(override={"ready_uncertainty": 0.28}, computed=DYNAMIC)
    assert (
        unc == 0.22
        and info["basis"]["base_threshold"] == 0.28
        and calls["base"] == {"ready_uncertainty_threshold": 0.28}
    )


def test_a_dynamic_source_without_a_brier_score_is_not_used(cascade):
    noetic = {**DYNAMIC["noetic"], "brier_score": None}
    (know, unc, info), _ = cascade(computed={"source": "dynamic", "noetic": noetic})
    assert (know, unc) == (0.70, 0.35) and info["basis"]["threshold_source"] == "static"


def test_a_static_source_is_ignored_even_if_it_carries_noetic_figures(cascade):
    (know, unc, info), _ = cascade(computed={"source": "static", "noetic": DYNAMIC["noetic"]})
    assert (know, unc) == (0.70, 0.35) and info["basis"]["threshold_source"] == "static"
    assert "brier_score" not in info


def test_a_failing_brier_stage_falls_back_to_the_stage_before_it_not_to_the_defaults(cascade):
    (know, unc, info), _ = cascade(override={"ready_uncertainty": 0.28}, compute_raises=True)
    assert (know, unc) == (0.70, 0.28), "the calibration.yaml base survives the failure"
    assert info["basis"]["threshold_source"] == "static"
