"""Tests for resolve_hygiene_policy — per-practice artifact-hygiene policy (WS2).

Mirrors the #253 scalar-resolver discipline: defaults are the single source of
truth, project.yaml overrides are clamped/validated, and a bad policy never
raises (falls back to defaults).
"""

from __future__ import annotations

import yaml

from empirica.config.hygiene_policy import (
    HYGIENE_POLICY_DEFAULTS,
    resolve_hygiene_policy,
)


def _write_policy(tmp_path, block):
    empdir = tmp_path / ".empirica"
    empdir.mkdir(parents=True, exist_ok=True)
    (empdir / "project.yaml").write_text(yaml.dump({"project_id": "p", "hygiene_policy": block}), encoding="utf-8")
    return tmp_path


def test_defaults_when_no_project_yaml(tmp_path):
    assert resolve_hygiene_policy(tmp_path) == HYGIENE_POLICY_DEFAULTS


def test_full_override(tmp_path):
    root = _write_policy(
        tmp_path,
        {
            "source_staleness_days": 7,
            "unknown_triage_days": 60,
            "goal_auto_close": "surface_only",
            "auto_delete": "off",
            "dedup": "fuzzy",
        },
    )
    p = resolve_hygiene_policy(root)
    assert p["source_staleness_days"] == 7
    assert p["unknown_triage_days"] == 60
    assert p["goal_auto_close"] == "surface_only"
    assert p["auto_delete"] == "off"
    assert p["dedup"] == "fuzzy"


def test_partial_override_keeps_other_defaults(tmp_path):
    root = _write_policy(tmp_path, {"source_staleness_days": 90})
    p = resolve_hygiene_policy(root)
    assert p["source_staleness_days"] == 90
    assert p["unknown_triage_days"] == HYGIENE_POLICY_DEFAULTS["unknown_triage_days"]
    assert p["goal_auto_close"] == HYGIENE_POLICY_DEFAULTS["goal_auto_close"]


def test_negative_int_clamps_to_default(tmp_path):
    root = _write_policy(tmp_path, {"source_staleness_days": -5})
    assert resolve_hygiene_policy(root)["source_staleness_days"] == HYGIENE_POLICY_DEFAULTS["source_staleness_days"]


def test_bad_int_falls_back(tmp_path):
    root = _write_policy(tmp_path, {"source_staleness_days": "soon"})
    assert resolve_hygiene_policy(root)["source_staleness_days"] == HYGIENE_POLICY_DEFAULTS["source_staleness_days"]


def test_unknown_enum_falls_back(tmp_path):
    root = _write_policy(tmp_path, {"goal_auto_close": "yolo", "dedup": "aggressive"})
    p = resolve_hygiene_policy(root)
    assert p["goal_auto_close"] == HYGIENE_POLICY_DEFAULTS["goal_auto_close"]
    assert p["dedup"] == HYGIENE_POLICY_DEFAULTS["dedup"]


def test_unknown_keys_ignored(tmp_path):
    root = _write_policy(tmp_path, {"source_staleness_days": 10, "bogus_field": 99})
    p = resolve_hygiene_policy(root)
    assert p["source_staleness_days"] == 10
    assert "bogus_field" not in p


def test_malformed_yaml_returns_defaults(tmp_path):
    empdir = tmp_path / ".empirica"
    empdir.mkdir(parents=True)
    (empdir / "project.yaml").write_text("hygiene_policy: [not: a: mapping", encoding="utf-8")
    assert resolve_hygiene_policy(tmp_path) == HYGIENE_POLICY_DEFAULTS


def test_non_dict_block_returns_defaults(tmp_path):
    root = _write_policy(tmp_path, "not-a-dict")
    assert resolve_hygiene_policy(root) == HYGIENE_POLICY_DEFAULTS
