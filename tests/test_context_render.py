"""The bootstrap contract renders under a token budget at session start (goal 2e686e79, ecodex's contract)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "lib")
)

import context_render as cr

LONG = "word " * 80


def _history(n: int = 400) -> dict:
    ids = [f"{i:08x}-0000-0000-0000-000000000000" for i in range(n)]
    return {
        "goals": [{"id": i, "objective": f"goal {i} {LONG}", "status": "in_progress"} for i in ids],
        "findings": [{"id": i, "finding": f"finding {i} {LONG}", "impact": 0.7} for i in ids],
        "unknowns": [{"id": i, "unknown": f"unknown {i} {LONG}"} for i in ids],
        "decisions": [{"id": i, "choice": f"decision {i} {LONG}"} for i in ids],
        "assumptions": [{"id": i, "assumption": f"assumption {i} {LONG}", "confidence": 0.6} for i in ids],
        "mistakes_to_avoid": [{"mistake": f"mistake {i} {LONG}", "prevention": f"prevent {i} {LONG}"} for i in ids],
        "dead_ends": [{"id": i, "approach": f"approach {i} {LONG}", "why_failed": "it broke\nmore"} for i in ids],
        "artifact_edges": [
            {"from": i, "to": ids[0], "from_type": "finding", "to_type": "decision", "relation": "evidence"}
            for i in ids
        ],
        "coverage": {
            k: {"shown": n, "total": n * 3, "truncated": True, "source": "sqlite"}
            for k in (
                "findings",
                "unknowns",
                "decisions",
                "assumptions",
                "dead_ends",
                "mistakes_to_avoid",
                "artifact_edges",
            )
        },
        "blindspot_status": {"surfaced": 3, "acknowledged": 9, "note": "CHECK-time scan telemetry"},
    }


def test_a_large_history_stays_under_the_budget_and_every_category_is_accounted_for():
    out = cr.render(_history())
    assert cr.tokens(out) <= 2500
    assert cr.tokens(out) <= cr.DEFAULT_TOKENS + 60  # the coverage line's final numbers may differ by a few bytes
    for heading in (
        "Goals",
        "Findings",
        "Unknowns",
        "Decisions",
        "Assumptions",
        "Mistakes to avoid",
        "Dead ends",
        "Links",
    ):
        assert f"**{heading}:**" in out  # one selection pass: every category gets a turn before any takes a second
    coverage = next(line for line in out.splitlines() if line.startswith("Coverage:"))
    assert "findings" in coverage and "of 1200" in coverage


def test_ids_prevention_and_blindspots_are_shown():
    out = cr.render(_history(3), budget=4000)
    assert "[00000000]" in out and "→ prevent 0" in out and "Blindspots: 3 surfaced, 9 acknowledged" in out


def test_none_logged_not_returned_and_unavailable_read_differently():
    out = cr.render(
        {
            "findings": [],
            "coverage": {
                "findings": {"shown": 0, "total": 0, "truncated": False, "source": "sqlite"},
                "decisions": {"source": "unavailable"},
            },
            "decisions": [],
        }
    )
    coverage = next(line for line in out.splitlines() if line.startswith("Coverage:"))
    assert "findings: none" in coverage
    assert "decisions: unavailable" in coverage
    assert "unknowns: not returned by this core" in coverage


def test_the_budget_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("EMPIRICA_HOOK_CONTEXT_TOKENS", "400")
    assert cr.tokens(cr.render(_history())) <= 460
    monkeypatch.setenv("EMPIRICA_HOOK_CONTEXT_TOKENS", "not a number")
    assert cr.budget_from_env() == cr.DEFAULT_TOKENS


def test_no_bootstrap_says_so():
    assert "did not answer" in cr.render(None)


def test_session_start_renders_the_contract(monkeypatch):
    import importlib.util

    hooks = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "hooks"
    spec = importlib.util.spec_from_file_location("session_init_under_test", hooks / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    history = _history(5)
    out = mod.format_context(
        {"goals": history["goals"][:3], "findings": [], "unknowns": [], "due": [], "breadcrumbs": history}
    )
    assert "**Decisions:**" in out and "Coverage:" in out and "[00000000]" in out


def test_only_limits_the_item_sections_but_coverage_names_every_category():
    out = cr.render(_history(5), budget=4000, only={"decisions", "assumptions"})
    assert "**Decisions:**" in out and "**Findings:**" not in out
    assert "findings 0 of 15" in next(line for line in out.splitlines() if line.startswith("Coverage:"))


def test_post_compact_adds_the_contract_sections_its_focus_lacks():
    import importlib.util

    hooks = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "hooks"
    spec = importlib.util.spec_from_file_location("post_compact_under_test", hooks / "post-compact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod._contract_section({"breadcrumbs": _history(5)})
    assert "**Decisions:**" in out and "**Mistakes to avoid:**" in out and "Coverage:" in out
    assert "**Findings:**" not in out
    assert mod._contract_section(None) == ""
