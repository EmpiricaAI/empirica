"""Tests for the injection measure-view served source (B1 persist + B2 daemon).

The extension's injection-observability panel has NO cortex/MCP path — the daemon
GET /profile/status is its ONLY served source (prop_o4g6sag). PREFLIGHT persists
the 6-field ``_context_budget`` measure-view into ``active_transaction.json`` (B1);
the daemon reads it back for /profile/status (B2).
"""

from __future__ import annotations

import json

import empirica.api.daemon_project as dp
import empirica.api.serve_app as serve
import empirica.cli.command_handlers._workflow_preflight as wp

_BUDGET = {
    "injected_per_category": {"relevant_findings": 3},
    "injected_total": 3,
    "cap_per_category": None,
    "cap_total": None,
    "capped_per_category": 0,
    "capped_total": 0,
}


def _tx_file(root, suffix="", status="open", budget=None):
    emp = root / ".empirica"
    emp.mkdir(parents=True, exist_ok=True)
    d = {"transaction_id": "t1", "status": status}
    if budget is not None:
        d["injection_budget"] = budget
    p = emp / f"active_transaction{suffix}.json"
    p.write_text(json.dumps(d))
    return p


# ── B1: PREFLIGHT persists the measure-view ───────────────────────────────────


def test_persist_writes_injection_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.R, "instance_suffix", lambda: "")
    p = _tx_file(tmp_path)  # existing tx file, no budget yet
    patterns = {"_context_budget": _BUDGET, "relevant_findings": [1, 2, 3]}
    wp._preflight_persist_pattern_count(patterns, str(tmp_path))
    written = json.loads(p.read_text())
    assert written["injection_budget"] == _BUDGET
    assert written["preflight_pattern_count"] == 3  # _context_budget dict not counted


def test_persist_omits_budget_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.R, "instance_suffix", lambda: "")
    p = _tx_file(tmp_path)
    wp._preflight_persist_pattern_count({"relevant_findings": [1]}, str(tmp_path))
    assert "injection_budget" not in json.loads(p.read_text())


# ── B2: daemon reads the persisted measure-view ───────────────────────────────


def test_daemon_reads_persisted_budget(tmp_path, monkeypatch):
    _tx_file(tmp_path, budget=_BUDGET)
    monkeypatch.setattr(dp, "get_cached_daemon_project", lambda *a, **k: {"project_path": str(tmp_path)})
    assert serve._read_active_injection_budget() == _BUDGET


def test_daemon_prefers_open_over_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "get_cached_daemon_project", lambda *a, **k: {"project_path": str(tmp_path)})
    _tx_file(tmp_path, suffix="_a", status="closed", budget={**_BUDGET, "injected_total": 99})
    _tx_file(tmp_path, suffix="_b", status="open", budget=_BUDGET)
    assert serve._read_active_injection_budget()["injected_total"] == 3  # open wins


def test_daemon_falls_back_to_closed_when_no_open(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "get_cached_daemon_project", lambda *a, **k: {"project_path": str(tmp_path)})
    _tx_file(tmp_path, status="closed", budget=_BUDGET)
    assert serve._read_active_injection_budget() == _BUDGET


def test_daemon_honest_none_when_no_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "get_cached_daemon_project", lambda *a, **k: {"project_path": str(tmp_path)})
    _tx_file(tmp_path)  # tx file exists but carries no injection_budget
    assert serve._read_active_injection_budget() is None


def test_daemon_honest_none_when_no_project(monkeypatch):
    monkeypatch.setattr(dp, "get_cached_daemon_project", lambda *a, **k: {})
    assert serve._read_active_injection_budget() is None
