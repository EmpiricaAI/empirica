"""Tests for the lean-by-default context budget in pattern_retrieval.

Covers the three mechanisms added to keep PREFLIGHT/CHECK/bootstrap pattern
blocks from ballooning the AI's context window:
- B1 truncate long item text (_truncate_text / _truncate_sections)
- B2 cross-section dedup by content-hash (_dedup_sections)
- B3 total-char budget eviction (_enforce_total_budget) + adaptive cap
and the EMPIRICA_PATTERN_BUDGET_OFF / apply_budget=False escape hatch.
"""

from __future__ import annotations

from empirica.core.qdrant import pattern_retrieval as pr

# --- B2 dedup --------------------------------------------------------------


def test_dedup_drops_cross_section_duplicate():
    """A finding whose text recurs as an eidetic fact is dropped from the
    later section (earlier section wins by insertion order)."""
    result = {
        "relevant_findings": [{"finding": "shared text", "score": 0.9}],
        "eidetic_facts": [{"content": "shared text", "score": 0.8}],
    }
    dropped = pr._dedup_sections(result)
    assert dropped == 1
    assert len(result["relevant_findings"]) == 1
    assert result["eidetic_facts"] == []


def test_dedup_normalizes_whitespace_and_case():
    result = {
        "relevant_findings": [{"finding": "Shared   TEXT"}],
        "eidetic_facts": [{"content": "shared text"}],
    }
    assert pr._dedup_sections(result) == 1
    assert result["eidetic_facts"] == []


def test_dedup_keeps_distinct_items():
    result = {
        "relevant_findings": [{"finding": "alpha"}],
        "eidetic_facts": [{"content": "beta"}],
    }
    assert pr._dedup_sections(result) == 0
    assert len(result["eidetic_facts"]) == 1


# --- B1 truncate -----------------------------------------------------------


def test_truncate_long_text_adds_overflow_marker():
    out = pr._truncate_text("word " * 200, 50)
    assert out.endswith("chars)")
    assert len(out) < 80


def test_truncate_leaves_short_text_unchanged():
    assert pr._truncate_text("short", 280) == "short"


def test_truncate_sections_only_hits_mapped_fields():
    result = {"relevant_findings": [{"finding": "x" * 600, "impact": 0.5}]}
    pr._truncate_sections(result, 100)
    assert str(result["relevant_findings"][0]["finding"]).endswith("chars)")
    # numeric fields untouched
    assert result["relevant_findings"][0]["impact"] == 0.5


# --- B3 budget -------------------------------------------------------------


def test_enforce_budget_drops_lowest_ranked_first():
    result = {
        "related_goals": [
            {"objective": "g" * 100, "effective_score": 0.1},
            {"objective": "g" * 100, "effective_score": 0.9},
        ]
    }
    dropped = pr._enforce_total_budget(result, max_total=120)
    assert dropped == 1
    # the higher-ranked item survives
    assert result["related_goals"][0]["effective_score"] == 0.9


def test_enforce_budget_protects_triad_top_item():
    result = {"relevant_findings": [{"finding": "f" * 10000, "score": 0.1}]}
    # Even far over budget, the single protected finding is never evicted.
    dropped = pr._enforce_total_budget(result, max_total=10)
    assert dropped == 0
    assert len(result["relevant_findings"]) == 1


def test_result_chars_ignores_non_item_keys():
    result = {
        "time_gap": {"note": "x" * 1000},
        "relevant_findings": [{"finding": "abc"}],
    }
    # time_gap is a scalar/metadata key, not counted
    assert pr._result_chars(result) == 3


# --- escape hatch + integration -------------------------------------------


def test_apply_budget_false_returns_full():
    result = {"relevant_findings": [{"finding": "z" * 500, "score": 0.5}]}
    out = pr._apply_context_budget(result, apply_budget=False)
    assert len(out["relevant_findings"][0]["finding"]) == 500
    assert "_context_budget" not in out


def test_env_escape_hatch(monkeypatch):
    monkeypatch.setenv("EMPIRICA_PATTERN_BUDGET_OFF", "1")
    result = {"relevant_findings": [{"finding": "z" * 500}]}
    out = pr._apply_context_budget(result)
    assert len(out["relevant_findings"][0]["finding"]) == 500


def test_apply_budget_records_legible_note():
    result = {
        "relevant_findings": [{"finding": "dup", "score": 0.9}],
        "eidetic_facts": [{"content": "dup", "score": 0.8}],
    }
    out = pr._apply_context_budget(result)
    assert out["_context_budget"]["deduped"] == 1
    assert "investigate" in out["_context_budget"]["note"]


def test_apply_budget_leaves_time_gap_untouched():
    result = {
        "relevant_findings": [{"finding": "a"}],
        "time_gap": {"gap_category": "extended_away"},
    }
    out = pr._apply_context_budget(result)
    assert out["time_gap"] == {"gap_category": "extended_away"}


# --- adaptive limit cap ----------------------------------------------------


def test_adaptive_limits_capped_at_max_per_section():
    # Worst case (max uncertainty, zero know/context) would balloon without cap.
    limits = pr._compute_adaptive_limits({"uncertainty": 1.0, "know": 0.0, "context": 0.0}, 3)
    assert all(v <= pr.MAX_PER_SECTION for v in limits.values())


def test_adaptive_limits_none_vectors_uses_base():
    limits = pr._compute_adaptive_limits(None, 3)
    assert all(v == 3 for v in limits.values())


# --- injection measure-view (6-field, 'measure before you tune') -----------


def _isolate_caps(monkeypatch):
    """Neutralize any repo-local config.yaml + env caps so the default is uncapped."""
    import empirica.config.path_resolver as prcfg

    monkeypatch.setattr(prcfg, "load_empirica_config", lambda: {})
    monkeypatch.delenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", raising=False)
    monkeypatch.delenv("EMPIRICA_MAX_ARTIFACTS_TOTAL", raising=False)


def test_measure_view_surfaces_volume_when_uncapped(monkeypatch):
    # The core of the volume amendment: even uncapped (the default everywhere),
    # _context_budget carries the injected volume so status/panel aren't blank.
    _isolate_caps(monkeypatch)
    result = {"relevant_findings": [{"finding": f"f{i}"} for i in range(3)]}
    b = pr._apply_context_budget(result)["_context_budget"]
    assert b["injected_per_category"] == {"relevant_findings": 3}
    assert b["injected_total"] == 3
    assert b["cap_per_category"] is None and b["cap_total"] is None
    assert b["capped_per_category"] == 0 and b["capped_total"] == 0
    # nothing trimmed → no dedup/elision note keys
    assert "note" not in b and "deduped" not in b


def test_measure_view_absent_when_no_volume_and_uncapped(monkeypatch):
    _isolate_caps(monkeypatch)
    result = {"time_gap": {"gap_category": "x"}}  # no injectable category items
    assert "_context_budget" not in pr._apply_context_budget(result)


def test_measure_view_present_when_cap_configured_even_without_volume(monkeypatch):
    # 'OR a cap is configured' branch — the operator set a ceiling; surface it.
    _isolate_caps(monkeypatch)
    monkeypatch.setenv("EMPIRICA_MAX_ARTIFACTS_TOTAL", "10")
    b = pr._apply_context_budget({"time_gap": {"x": 1}})["_context_budget"]
    assert b["cap_total"] == 10 and b["injected_total"] == 0


def test_measure_view_reflects_cap_and_drops(monkeypatch):
    _isolate_caps(monkeypatch)
    monkeypatch.setenv("EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY", "2")
    result = {"relevant_findings": [{"finding": f"f{i}"} for i in range(5)]}
    b = pr._apply_context_budget(result)["_context_budget"]
    assert b["cap_per_category"] == 2
    assert b["capped_per_category"] == 3  # 5 -> 2, three dropped
    assert b["injected_total"] == 2


def test_measure_view_coexists_with_trim_note(monkeypatch):
    # When both trimming AND volume happen, the block carries measure + note.
    _isolate_caps(monkeypatch)
    result = {
        "relevant_findings": [{"finding": "dup", "score": 0.9}],
        "eidetic_facts": [{"content": "dup", "score": 0.8}],
    }
    b = pr._apply_context_budget(result)["_context_budget"]
    assert b["deduped"] == 1 and "investigate" in b["note"]
    assert b["injected_total"] == 1  # one survivor after dedup
