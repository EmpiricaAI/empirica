"""CHECK's know threshold under a non-default cascade profile (sweep item R1, goal b78396ad; reproduced at HEAD on 2026-10-25).

cascade_styles.yaml defines `ready_know_threshold` only in the `default` profile and calls `ready_confidence_threshold` its legacy alias.
Every other profile (exploratory, rigorous, speed, expert, learning) carries the know bar under the legacy name only, so the lookup of the
new name missed, fell back to a hardcoded 0.70, and the verdict's `basis` said it came from "cascade profile 'rigorous'" while the value
was the default's. The gate itself decides on uncertainty alone; the know bar is reported and fed to the dynamic thresholds, so what was
wrong is the number the verdict states and builds on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from empirica.cli.command_handlers._workflow_check import _profile_base_thresholds

STYLES = Path(__file__).resolve().parents[1] / "empirica" / "config" / "mco" / "cascade_styles.yaml"


class _Loader:
    """A loader over a plain dict, keyed the way ThresholdLoader.get is (dotted path)."""

    def __init__(self, cascade: dict):
        self.cascade = cascade

    def get(self, key, default=None):
        node = {"cascade": self.cascade}
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def test_a_profile_that_only_has_the_legacy_know_key_uses_it_instead_of_the_hardcoded_default():
    loader = _Loader({"ready_confidence_threshold": 0.85, "ready_uncertainty_threshold": 0.2})
    assert _profile_base_thresholds(loader) == {"ready_know_threshold": 0.85, "ready_uncertainty_threshold": 0.2}


def test_the_new_key_wins_when_a_profile_defines_both():
    loader = _Loader(
        {"ready_know_threshold": 0.7, "ready_confidence_threshold": 0.7, "ready_uncertainty_threshold": 0.35}
    )
    assert _profile_base_thresholds(loader)["ready_know_threshold"] == 0.7
    assert (
        _profile_base_thresholds(_Loader({"ready_know_threshold": 0.9, "ready_confidence_threshold": 0.5}))[
            "ready_know_threshold"
        ]
        == 0.9
    )


def test_a_profile_with_neither_key_falls_back_to_the_documented_default():
    assert _profile_base_thresholds(_Loader({})) == {"ready_know_threshold": 0.70, "ready_uncertainty_threshold": 0.35}


def _profiles():
    data = yaml.safe_load(STYLES.read_text())
    return {name: (p.get("cascade") or {}) for name, p in (data.get("profiles") or {}).items()}


@pytest.mark.parametrize("name", sorted(_profiles()))
def test_every_shipped_profile_resolves_to_the_know_bar_it_actually_declares(name):
    """Not the fallback: the value a profile declares (under either name) is the value CHECK reports."""
    cascade = _profiles()[name]
    declared = cascade.get("ready_know_threshold", cascade.get("ready_confidence_threshold"))
    assert declared is not None, f"profile {name} declares no know bar under either name"
    assert _profile_base_thresholds(_Loader(cascade))["ready_know_threshold"] == declared


def test_rigorous_and_exploratory_are_not_both_the_default_bar():
    """The control that the walk above is not vacuous: the two profiles the sweep named differ from 0.70 and from each other."""
    profiles = _profiles()
    bars = {
        n: _profile_base_thresholds(_Loader(profiles[n]))["ready_know_threshold"] for n in ("rigorous", "exploratory")
    }
    assert bars["rigorous"] == 0.85 and bars["exploratory"] == 0.60


# ── sweep 2: the other readers of the same bar use the same lookup ──


def test_the_dynamic_threshold_baseline_reads_the_legacy_know_key_too(monkeypatch):
    from empirica.config import threshold_loader
    from empirica.core.post_test.dynamic_thresholds import _load_calibration_config

    loader = _Loader({"ready_confidence_threshold": 0.85, "ready_uncertainty_threshold": 0.2})
    monkeypatch.setattr(threshold_loader.ThresholdLoader, "get_instance", classmethod(lambda cls: loader))
    assert _load_calibration_config()["baselines"]["ready_know_threshold"] == 0.85


def test_the_monitor_hooks_readiness_bar_reads_the_legacy_know_key_too(monkeypatch):
    from empirica.config import threshold_loader
    from empirica.core.canonical.empirica_git.sentinel_hooks import _load_readiness_thresholds

    monkeypatch.delenv("EMPIRICA_KNOW_THRESHOLD", raising=False)
    monkeypatch.delenv("EMPIRICA_UNCERTAINTY_THRESHOLD", raising=False)
    loader = _Loader({"ready_confidence_threshold": 0.85, "ready_uncertainty_threshold": 0.2})
    monkeypatch.setattr(threshold_loader, "get_threshold_config", lambda: loader)
    assert _load_readiness_thresholds()["min_know"] == 0.85


def test_the_shared_lookup_is_the_one_the_check_handler_uses():
    from empirica.config.threshold_loader import ready_know_bar

    assert ready_know_bar(_Loader({"ready_confidence_threshold": 0.6}), 0.7) == 0.6
    assert ready_know_bar(_Loader({"ready_know_threshold": 0.9, "ready_confidence_threshold": 0.6}), 0.7) == 0.9
    assert ready_know_bar(_Loader({}), 0.7) == 0.7
