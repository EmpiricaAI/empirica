"""CHECK stores the gate it computed, and the export carries it (goal ae8b676e, cowork prop_brbkfst3 item 4).

_workflow_check computed the threshold, its base and source, and the computed decision for every CHECK and printed them
under metacog, but stored none, so nothing afterwards could tell what the gate had applied. Not retroactive.
"""

from __future__ import annotations

import json
import types


def test_the_gate_reaches_the_checkpoint_metadata(monkeypatch):
    import empirica.core.canonical.git_enhanced_reflex_logger as grl
    from empirica.cli.command_handlers import _workflow_check as wc

    captured = {}

    class FakeLogger:
        def __init__(self, **k):
            pass

        def add_checkpoint(self, **k):
            captured.update(k["metadata"])
            return "cp-1"

    monkeypatch.setattr(grl, "GitEnhancedReflexLogger", FakeLogger)
    monkeypatch.setattr(wc, "R", types.SimpleNamespace(transaction_id=lambda: "tx-1"))
    gate = {
        "threshold_used": 0.4,
        "base_threshold": 0.35,
        "threshold_source": "dynamic",
        "computed_decision": "investigate",
        "decision_overridden": True,
    }
    wc._check_store_and_publish("s", 1, {"know": 0.8}, "proceed", "why", 1, gate=gate)
    assert {k: captured[k] for k in gate} == gate


def test_the_export_carries_the_gate_fields():
    from empirica.core.transaction_export import _reflex, _Safe

    row = {
        "phase": "CHECK",
        "timestamp": 5.0,
        "reflex_data": json.dumps(
            {
                "decision": "proceed",
                "threshold_used": 0.4,
                "base_threshold": 0.35,
                "threshold_source": "dynamic",
                "computed_decision": "investigate",
                "decision_overridden": True,
                "threshold_source_extra": "x",
            }
        ),
    }
    row.update(dict.fromkeys(__import__("empirica.core.transaction_export", fromlist=["VECTORS"]).VECTORS))
    out = _reflex(row, _Safe())
    assert out["threshold_used"] == 0.4 and out["base_threshold"] == 0.35
    assert out["threshold_source"] == "dynamic" and out["computed_decision"] == "investigate"
    assert out["decision_overridden"] is True and "threshold_source_extra" not in out
