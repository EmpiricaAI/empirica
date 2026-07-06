"""Artifact-graph weave-enforce at the CHECK gate (map work-stream 1 enforce-half).

The gate BLOCKS the noetic→praxic transition when strictness is in the enforce
band (≥0.70) and the transaction's artifacts are below the connectivity floor.
Enforce is now the ecosystem default (strictness 0.75); a practice dials DOWN to
make it a no-op. And it's fail-open: a measurement error never blocks CHECK (the
P1 lesson — gating machinery must not brick the loop it gates).
"""

from __future__ import annotations

from empirica.cli.command_handlers._workflow_check import _check_apply_weave_enforce

_BLOCK_MOD = "empirica.cli.command_handlers._workflow_shared._weave_enforcement_block"


def test_dormant_when_not_enforced(monkeypatch):
    monkeypatch.setattr(_BLOCK_MOD, lambda sid, tx: {"enforced": False, "note": "report-only"})
    result = {"decision": "proceed"}
    d = _check_apply_weave_enforce(result, "proceed", "s1", "tx1")
    assert d == "proceed"  # dormant — no override
    assert result["weave_gate"]["enforced"] is False  # still attached (report)
    assert "weave_enforce" not in result


def test_blocks_proceed_when_enforced(monkeypatch):
    monkeypatch.setattr(_BLOCK_MOD, lambda sid, tx: {"enforced": True, "note": "MUST weave more"})
    result = {"decision": "proceed"}
    d = _check_apply_weave_enforce(result, "proceed", "s1", "tx1")
    assert d == "investigate"  # blocked
    assert result["decision"] == "investigate"  # result updated too
    assert result["weave_enforce"]["blocked"] is True
    assert "MUST weave" in result["weave_enforce"]["reason"]


def test_investigate_unchanged_when_enforced(monkeypatch):
    # enforce only overrides a proceed — an already-investigate decision is untouched.
    monkeypatch.setattr(_BLOCK_MOD, lambda sid, tx: {"enforced": True, "note": "x"})
    result = {"decision": "investigate"}
    d = _check_apply_weave_enforce(result, "investigate", "s1", "tx1")
    assert d == "investigate"


def test_none_block_leaves_everything_alone(monkeypatch):
    monkeypatch.setattr(_BLOCK_MOD, lambda sid, tx: None)  # no artifacts / silent
    result = {"decision": "proceed"}
    d = _check_apply_weave_enforce(result, "proceed", "s1", "tx1")
    assert d == "proceed"
    assert "weave_gate" not in result


def test_measurement_failure_is_fail_open(monkeypatch):
    def boom(sid, tx):
        raise RuntimeError("db locked")

    monkeypatch.setattr(_BLOCK_MOD, boom)
    result = {"decision": "proceed"}
    d = _check_apply_weave_enforce(result, "proceed", "s1", "tx1")
    assert d == "proceed"  # fail-open — a counting error never blocks CHECK


# ── _weave_enforcement_block (the DB-reading wrapper) ────────────────────


def test_enforcement_block_none_on_db_error(monkeypatch):
    from empirica.cli.command_handlers import _workflow_shared as ws

    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ws._weave_enforcement_block("s1", "tx1") is None


def test_enforcement_block_none_when_no_artifacts(monkeypatch):
    import types

    from empirica.cli.command_handlers import _workflow_shared as ws

    fake_db = types.SimpleNamespace(conn=types.SimpleNamespace(cursor=lambda: None))
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: fake_db)
    monkeypatch.setattr(ws, "_retro_count_artifacts", lambda cur, sid, tx: {"finding": 0})
    assert ws._weave_enforcement_block("s1", "tx1") is None  # 0 artifacts → None
