"""Tests for the calibration-config overlay resolver + sparse-override store.

Covers the settable schema, the layering resolver (base → preset → global →
practice), weight normalization, PATCH validation, and the dedicated
.empirica/calibration.yaml store (roundtrip, reset-to-default, file cleanup).
"""

from __future__ import annotations

from empirica.core import calibration_config as cc

# ── schema + defaults ─────────────────────────────────────────────────────────


def test_schema_has_weights_and_thresholds():
    groups = {f.group for f in cc.SCHEMA}
    assert groups == {"weights", "thresholds"}
    assert cc.WEIGHT_KEYS == ("foundation", "comprehension", "execution", "engagement")


def test_default_config_matches_spec_defaults():
    d = cc.default_config()
    assert d["weights"] == {"foundation": 0.35, "comprehension": 0.25, "execution": 0.25, "engagement": 0.15}
    assert d["thresholds"]["engagement_gate"] == 0.60
    assert d["thresholds"]["uncertainty_trigger"] == 0.40


def test_schema_json_is_serializable_field_specs():
    rows = cc.schema_json()
    assert len(rows) == len(cc.SCHEMA)
    gate = next(r for r in rows if r["key"] == "engagement_gate")
    assert gate["is_gate"] is True and gate["group"] == "thresholds" and gate["default"] == 0.60


# ── resolver ──────────────────────────────────────────────────────────────────


def test_resolve_no_overrides_is_defaults_all_sourced_default():
    r = cc.resolve()
    assert r["weights"]["foundation"] == 0.35  # already sums to 1.0 → unchanged
    assert r["preset"] is None
    assert r["overridden"] == []
    assert set(r["sources"].values()) == {"default"}


def test_resolve_global_preset_applies_persona_values():
    r = cc.resolve(global_override={"preset": "security"})
    # security template: engagement_gate 0.70, foundation weight 0.40
    assert r["preset"] == "security"
    assert r["thresholds"]["engagement_gate"] == 0.70
    assert r["sources"]["thresholds.engagement_gate"] == "preset:security"
    # weights renormalized but foundation still the largest
    assert r["weights"]["foundation"] == max(r["weights"].values())


def test_practice_override_wins_over_global_preset():
    r = cc.resolve(
        global_override={"preset": "security"},  # engagement_gate 0.70
        practice_override={"thresholds": {"engagement_gate": 0.5}},
    )
    assert r["thresholds"]["engagement_gate"] == 0.5
    assert r["sources"]["thresholds.engagement_gate"] == "practice"
    assert "thresholds.engagement_gate" in r["overridden"]


def test_resolve_clamps_out_of_range_and_ignores_unknown_keys():
    r = cc.resolve(
        practice_override={
            "thresholds": {"engagement_gate": 5.0, "bogus": 0.9},  # 5.0 clamps to 1.0; bogus ignored
        },
        normalize=False,
    )
    assert r["thresholds"]["engagement_gate"] == 1.0
    assert "bogus" not in r["thresholds"]


def test_normalize_weights_sums_to_one():
    out = cc.normalize_weights({"foundation": 2, "comprehension": 1, "execution": 1, "engagement": 0})
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["foundation"] == 0.5


def test_normalize_weights_zero_sum_falls_back_equal():
    out = cc.normalize_weights(dict.fromkeys(cc.WEIGHT_KEYS, 0.0))
    assert all(abs(v - 0.25) < 1e-9 for v in out.values())


# ── PATCH validation ──────────────────────────────────────────────────────────


def test_validate_patch_clamps_and_accepts_reset_none():
    clean, errors = cc.validate_patch({"thresholds": {"engagement_gate": 2.0, "signal_quality_min": None}})
    assert errors == []
    assert clean["thresholds"]["engagement_gate"] == 1.0
    assert clean["thresholds"]["signal_quality_min"] is None  # reset signal preserved


def test_validate_patch_rejects_unknown_key_and_preset():
    _clean, errors = cc.validate_patch({"weights": {"nope": 0.5}, "preset": "not-a-persona"})
    assert any("unknown weights key" in e for e in errors)
    assert any("unknown preset" in e for e in errors)


def test_validate_patch_rejects_non_number():
    _clean, errors = cc.validate_patch({"thresholds": {"engagement_gate": "high"}})
    assert any("must be a number" in e for e in errors)


def test_validate_patch_accepts_null_preset_reset():
    clean, errors = cc.validate_patch({"preset": None})
    assert errors == [] and clean["preset"] is None


# ── store roundtrip ───────────────────────────────────────────────────────────


def test_apply_patch_roundtrip_and_read(tmp_path):
    cc.apply_patch(tmp_path, {"thresholds": {"engagement_gate": 0.7}, "preset": "security"})
    ov = cc.read_override(tmp_path)
    assert ov["thresholds"]["engagement_gate"] == 0.7
    assert ov["preset"] == "security"
    assert cc.override_path(tmp_path).exists()


def test_read_override_absent_is_empty(tmp_path):
    assert cc.read_override(tmp_path) == {}


def test_apply_patch_none_resets_key_and_cleans_empty_file(tmp_path):
    cc.apply_patch(tmp_path, {"thresholds": {"engagement_gate": 0.7}})
    assert cc.read_override(tmp_path)["thresholds"]["engagement_gate"] == 0.7
    # reset the only key → block empties → file removed
    cc.apply_patch(tmp_path, {"thresholds": {"engagement_gate": None}})
    assert cc.read_override(tmp_path) == {}
    assert not cc.override_path(tmp_path).exists()


def test_apply_patch_clear_preset(tmp_path):
    cc.apply_patch(tmp_path, {"preset": "ux"})
    assert cc.read_override(tmp_path)["preset"] == "ux"
    cc.apply_patch(tmp_path, {"preset": None})
    assert "preset" not in cc.read_override(tmp_path)


def test_store_then_resolve_end_to_end(tmp_path):
    cc.apply_patch(tmp_path, {"thresholds": {"engagement_gate": 0.8}})
    r = cc.resolve(practice_override=cc.read_override(tmp_path))
    assert r["thresholds"]["engagement_gate"] == 0.8
    assert "thresholds.engagement_gate" in r["overridden"]
