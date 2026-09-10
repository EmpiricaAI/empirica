"""PREFLIGHT retrieval must not outlive the window its callers wait in.

`retrieve_task_patterns` runs a dozen sequential network calls after the
transaction row has already committed, so a degraded backend does not fail
preflight — it makes preflight LIE to whoever wrapped it in a timeout.
Measured: MCP reporting failure on an open transaction at age 128s (win32
install, issues.md #1), and a 120s+ stall reproduced twice locally the same
week, intermittent, every component fast when probed alone.

The budget converts that class from "stall until someone's timeout misfires"
into "partial injection that says it is partial": past the wall-clock deadline,
remaining phases are SKIPPED and named in `_retrieval_budget` — the no-silent-
caps rule applied to retrieval itself.
"""

from __future__ import annotations

from empirica.core.qdrant import pattern_retrieval as pr


def _stub_retrieval(monkeypatch):
    """Make retrieval 'available' with instant, empty searches — the budget is
    the subject here, not Qdrant, and a test that needs a live vector store
    measures the box instead of the code."""
    monkeypatch.setattr(pr, "_retrieval_available", lambda: True)
    monkeypatch.setattr(pr, "_search_memory_by_type", lambda *a, **k: [])
    monkeypatch.setattr(pr, "_apply_recency_rerank", lambda raw, *a, **k: raw)


def test_exhausted_budget_skips_and_NAMES_the_phases(monkeypatch):
    _stub_retrieval(monkeypatch)
    monkeypatch.setenv("EMPIRICA_RETRIEVAL_BUDGET_S", "0")
    r = pr.retrieve_task_patterns("proj-x", "budget probe", include_decisions=True)
    budget = r.get("_retrieval_budget")
    assert budget, "budget exhaustion left no trace — a partial injection that reads as complete"
    assert "relevant_findings" in budget["skipped"]
    assert "enrichment" in budget["skipped"]
    assert "SKIPPED" in budget["note"]


def test_normal_budget_leaves_no_key(monkeypatch):
    """The block appears only when something was actually skipped — present on
    every response it would be noise, and noise trains dismissal."""
    _stub_retrieval(monkeypatch)
    monkeypatch.setenv("EMPIRICA_RETRIEVAL_BUDGET_S", "30")
    r = pr.retrieve_task_patterns("proj-x", "budget probe")
    assert "_retrieval_budget" not in r


def test_early_phases_still_run_on_a_tight_budget(monkeypatch):
    """The deadline gates BETWEEN phases; it must not turn into an up-front
    all-or-nothing switch. What completed before exhaustion is kept."""
    _stub_retrieval(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(pr, "_search_memory_by_type", lambda pid, q, mtype, *a, **k: calls.append(mtype) or [])
    monkeypatch.setenv("EMPIRICA_RETRIEVAL_BUDGET_S", "0")
    pr.retrieve_task_patterns("proj-x", "budget probe")
    assert "lesson" in calls, "phases before the first checkpoint must still run"
    assert "finding" not in calls, "the gated phase ran despite an exhausted budget"
