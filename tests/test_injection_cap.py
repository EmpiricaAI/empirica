"""Tests for the user-configurable artifact-injection cap (pattern_retrieval).

Caps how many items per category (and in total) get injected into the
PREFLIGHT/CHECK teaser. Default is uncapped (preserves prior behaviour); tunable
via ``.empirica/config.yaml`` ``artifact_injection`` block or
``EMPIRICA_MAX_ARTIFACTS_*`` env vars (env wins).
"""

from __future__ import annotations

from empirica.core.qdrant.pattern_retrieval import (
    _apply_injection_caps,
    _resolve_injection_caps,
)


def _sample():
    return {
        "related_goals": [{"n": i} for i in range(5)],
        "relevant_findings": [{"n": i} for i in range(4)],
        "dead_ends": [{"n": i} for i in range(2)],
        "_context_budget": {"deduped": 0},  # non-category dict — must stay untouched
        "scalar": 3,
    }


# ── cap application ───────────────────────────────────────────────────────────


def test_no_caps_is_noop():
    r = _sample()
    assert _apply_injection_caps(r, {"max_per_category": None, "max_total": None}) == (0, 0)
    assert len(r["related_goals"]) == 5


def test_max_per_category_truncates_each_list():
    r = _sample()
    pc, total = _apply_injection_caps(r, {"max_per_category": 3, "max_total": None})
    assert len(r["related_goals"]) == 3  # 5 -> 3
    assert len(r["relevant_findings"]) == 3  # 4 -> 3
    assert len(r["dead_ends"]) == 2  # under cap, unchanged
    assert pc == (5 - 3) + (4 - 3)
    assert total == 0


def test_keeps_highest_ranked_prefix():
    r = _sample()
    _apply_injection_caps(r, {"max_per_category": 2, "max_total": None})
    assert r["related_goals"] == [{"n": 0}, {"n": 1}]  # top-2 kept, tail dropped


def test_max_total_trims_largest_first():
    r = _sample()  # 5 + 4 + 2 = 11
    pc, total = _apply_injection_caps(r, {"max_per_category": None, "max_total": 6})
    remaining = len(r["related_goals"]) + len(r["relevant_findings"]) + len(r["dead_ends"])
    assert remaining == 6
    assert total == 5
    assert pc == 0


def test_non_category_keys_untouched():
    r = _sample()
    _apply_injection_caps(r, {"max_per_category": 1, "max_total": None})
    assert r["_context_budget"] == {"deduped": 0}
    assert r["scalar"] == 3


# ── config/env resolution (precedence: env > config.yaml > default) ───────────


def _patch_cfg(monkeypatch, cfg):
    import empirica.config.path_resolver as pr

    monkeypatch.setattr(pr, "load_empirica_config", lambda: cfg)


def test_resolve_default_uncapped(monkeypatch):
    monkeypatch.delenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", raising=False)
    monkeypatch.delenv("EMPIRICA_MAX_ARTIFACTS_TOTAL", raising=False)
    _patch_cfg(monkeypatch, {})
    assert _resolve_injection_caps() == {"max_per_category": None, "max_total": None}


def test_resolve_env_override(monkeypatch):
    monkeypatch.setenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", "4")
    _patch_cfg(monkeypatch, {})
    assert _resolve_injection_caps()["max_per_category"] == 4


def test_resolve_config_yaml_block(monkeypatch):
    monkeypatch.delenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", raising=False)
    monkeypatch.delenv("EMPIRICA_MAX_ARTIFACTS_TOTAL", raising=False)
    _patch_cfg(monkeypatch, {"artifact_injection": {"max_per_category": 2, "max_total": 10}})
    assert _resolve_injection_caps() == {"max_per_category": 2, "max_total": 10}


def test_resolve_env_wins_over_config(monkeypatch):
    monkeypatch.setenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", "7")
    _patch_cfg(monkeypatch, {"artifact_injection": {"max_per_category": 2}})
    assert _resolve_injection_caps()["max_per_category"] == 7


def test_resolve_bad_value_ignored(monkeypatch):
    monkeypatch.setenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", "notanint")
    _patch_cfg(monkeypatch, {})
    assert _resolve_injection_caps()["max_per_category"] is None
