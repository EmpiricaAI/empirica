"""_weave_gate_block — scalar-driven artifact-graph gate verdict.

Gated Artifact-Graph map, work-stream 1 foundation (goal 43471346). Three
orthogonal 0.0–1.0 dimensions — strictness / connectivity_floor / patience —
resolved from env (the extension's Sentinel sliders) / project.yaml / default.
ENFORCE-BY-DEFAULT (ecosystem-wide, from 1.12.15): the default strictness 0.75
lands in the `enforce` band, so `enforced` is True below the floor; a practice
opts DOWN to report-only by dialing strictness < 0.70.
"""

from __future__ import annotations

from empirica.cli.command_handlers._workflow_shared import (
    _gate_response_for,
    _resolve_gate_scalars,
    _weave_gate_block,
)

_ENV = (
    "EMPIRICA_ARTIFACT_GRAPH_STRICTNESS",
    "EMPIRICA_ARTIFACT_GRAPH_FLOOR",
    "EMPIRICA_ARTIFACT_GRAPH_PATIENCE",
)


def _clear(monkeypatch):
    for e in _ENV:
        monkeypatch.delenv(e, raising=False)


def test_default_scalars_are_enforce_and_forgiving(monkeypatch):
    _clear(monkeypatch)
    # Ecosystem-wide enforce-by-default: strictness in the enforce band (>=0.70),
    # floor kept forgiving (0.34) so weaving one edge per few artifacts satisfies.
    assert _resolve_gate_scalars() == {
        "strictness": 0.75,
        "connectivity_floor": 0.34,
        "patience": 0.80,
    }


def test_scalars_env_driven_and_clamped(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.9")
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_FLOOR", "1.5")  # clamps to 1.0
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_PATIENCE", "-3")  # clamps to 0.0
    s = _resolve_gate_scalars()
    assert s["strictness"] == 0.9
    assert s["connectivity_floor"] == 1.0
    assert s["patience"] == 0.0


def test_bad_env_value_falls_back_to_default(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "not-a-number")
    assert _resolve_gate_scalars()["strictness"] == 0.75


def test_response_bands_ladder():
    assert _gate_response_for(0.0) == "silent"
    assert _gate_response_for(0.04) == "silent"
    assert _gate_response_for(0.05) == "report"
    assert _gate_response_for(0.25) == "report"
    assert _gate_response_for(0.40) == "warn"
    assert _gate_response_for(0.69) == "warn"
    assert _gate_response_for(0.70) == "enforce"
    assert _gate_response_for(1.0) == "enforce"


def test_connected_verdict_at_default(monkeypatch):
    _clear(monkeypatch)
    g = _weave_gate_block(total_artifacts=3, edges_count=3)
    assert g is not None
    assert g["response"] == "enforce"  # default strictness 0.75 → enforce band
    assert g["verdict"] == "connected"
    assert g["connected_ratio"] == 1.0
    assert g["satisfied"] is True
    assert g["enforced"] is False  # satisfied → no block even in enforce band


def test_partial_and_disconnected(monkeypatch):
    _clear(monkeypatch)
    partial = _weave_gate_block(4, 1)
    assert partial["verdict"] == "partial"
    assert partial["satisfied"] is False  # 0.25 ratio < 0.34 floor
    assert _weave_gate_block(3, 0)["verdict"] == "disconnected"


def test_connectivity_floor_governs_satisfied(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_FLOOR", "0.25")
    # 1/4 connected = 0.25 ratio, now meets a 0.25 floor
    assert _weave_gate_block(4, 1)["satisfied"] is True


def test_silent_strictness_returns_none(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.0")
    assert _weave_gate_block(3, 3) is None


def test_no_artifacts_returns_none(monkeypatch):
    _clear(monkeypatch)
    assert _weave_gate_block(0, 0) is None


def test_enforces_at_enforce_band_below_floor(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.9")
    g = _weave_gate_block(2, 0)  # 0% connected, below the 34% floor
    assert g["response"] == "enforce"
    assert g["enforced"] is True  # enforce band + below floor → blocks


def test_report_and_warn_bands_never_enforce(monkeypatch):
    _clear(monkeypatch)
    # report band (0.25, an opt-down) below floor → dormant, never blocks
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.25")
    assert _weave_gate_block(2, 0)["enforced"] is False
    # warn band (0.5) below floor → still report-only, no block
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.5")
    g = _weave_gate_block(2, 0)
    assert g["response"] == "warn"
    assert g["enforced"] is False


def test_enforce_band_satisfied_does_not_enforce(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.9")
    g = _weave_gate_block(2, 2)  # fully connected → satisfied → no block even at enforce
    assert g["response"] == "enforce"
    assert g["satisfied"] is True
    assert g["enforced"] is False
