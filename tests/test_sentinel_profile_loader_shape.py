"""The shipped Sentinel profile YAMLs nest `thresholds:` and `audit:`; the loader read top-level keys only.

A profile loaded from a shipped file therefore got the defaults (uncertainty_trigger 0.5, confidence_to_proceed
0.75, audit off, 90-day retention) with ok reported: a HIPAA profile with its strict values silently ignored.
Found by the 2026-10-05 deep sweep (sentinel unit, pilot 1 and pilot 2, and the docs rewrite of
SENTINEL_ARCHITECTURE). David: fix the loader.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from empirica.core.sentinel.orchestrator import DomainProfile

PROFILES = sorted((Path(__file__).resolve().parents[1] / "empirica/core/sentinel/profiles").glob("*.yaml"))


def test_there_are_shipped_profiles_to_check():
    assert PROFILES, "no shipped profiles found: the parametrised test below would pass vacuously"


@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_every_shipped_profile_keeps_its_yaml_values_when_loaded(path):
    raw = yaml.safe_load(path.read_text())
    profile = DomainProfile.from_dict(raw)
    thresholds = raw.get("thresholds", {})
    audit = raw.get("audit", {})
    if "uncertainty_trigger" in thresholds:
        assert profile.uncertainty_trigger == thresholds["uncertainty_trigger"]
    if "confidence_to_proceed" in thresholds:
        assert profile.confidence_to_proceed == thresholds["confidence_to_proceed"]
    if "retention_days" in audit:
        assert profile.audit_retention_days == audit["retention_days"]
    if "log_all_actions" in audit:
        assert profile.audit_all_actions == audit["log_all_actions"]
    assert len(profile.gates) == len(raw.get("gates", []))


def test_the_check_can_fail_healthcare_differs_from_the_defaults():
    """Control: the values under test are not the defaults, so a loader that ignores the nesting fails above."""
    raw = yaml.safe_load((PROFILES[0].parent / "healthcare.yaml").read_text())
    defaults = DomainProfile.from_dict({"name": "x"})
    assert raw["thresholds"]["uncertainty_trigger"] != defaults.uncertainty_trigger
    assert raw["audit"]["retention_days"] != defaults.audit_retention_days


def test_the_flat_form_still_loads():
    profile = DomainProfile.from_dict(
        {"name": "flat", "uncertainty_trigger": 0.2, "confidence_to_proceed": 0.9, "audit_retention_days": 30}
    )
    assert (profile.uncertainty_trigger, profile.confidence_to_proceed, profile.audit_retention_days) == (0.2, 0.9, 30)


def test_a_flat_key_wins_over_the_nested_one():
    profile = DomainProfile.from_dict(
        {"name": "both", "uncertainty_trigger": 0.2, "thresholds": {"uncertainty_trigger": 0.4}}
    )
    assert profile.uncertainty_trigger == 0.2
