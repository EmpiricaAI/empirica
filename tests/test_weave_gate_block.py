"""_weave_gate_block — report-only artifact-graph gate verdict.

Gated Artifact-Graph map, work-stream 1 foundation (goal 43471346). Reports the
gate mode (env EMPIRICA_ARTIFACT_GRAPH_GATE, default nudge) + a connectivity
verdict, but NEVER blocks in this build (`enforced: False`) — the soft/hard
blocking is a deliberate follow-up.
"""

from __future__ import annotations

from empirica.cli.command_handlers._workflow_shared import _weave_gate_block


def test_connected_verdict_default_nudge(monkeypatch):
    monkeypatch.delenv("EMPIRICA_ARTIFACT_GRAPH_GATE", raising=False)
    g = _weave_gate_block(total_artifacts=3, edges_count=3)
    assert g is not None
    assert g["mode"] == "nudge"
    assert g["verdict"] == "connected"
    assert g["enforced"] is False


def test_partial_and_disconnected(monkeypatch):
    monkeypatch.delenv("EMPIRICA_ARTIFACT_GRAPH_GATE", raising=False)
    assert _weave_gate_block(3, 1)["verdict"] == "partial"
    assert _weave_gate_block(3, 0)["verdict"] == "disconnected"


def test_off_returns_none(monkeypatch):
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_GATE", "off")
    assert _weave_gate_block(3, 3) is None


def test_no_artifacts_returns_none(monkeypatch):
    monkeypatch.delenv("EMPIRICA_ARTIFACT_GRAPH_GATE", raising=False)
    assert _weave_gate_block(0, 0) is None


def test_invalid_mode_falls_back_to_nudge(monkeypatch):
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_GATE", "bogus")
    assert _weave_gate_block(1, 1)["mode"] == "nudge"


def test_never_enforces_even_in_hard_mode(monkeypatch):
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_GATE", "hard")
    g = _weave_gate_block(2, 0)
    assert g["mode"] == "hard"
    assert g["enforced"] is False  # report-only build — blocking is a follow-up
