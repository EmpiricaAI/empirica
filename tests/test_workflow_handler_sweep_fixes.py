"""Workflow-handler defects from 0f68032f, re-verified at HEAD 2026-11-18 and fixed failing-test-first (goal 03916ee7)."""

from __future__ import annotations

import argparse
import time

import pytest

from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def _check_row(db, sid, tx, know=0.6):
    db.conn.execute(
        "INSERT INTO reflexes (session_id, phase, round, timestamp, know, transaction_id) VALUES (?, 'CHECK', 1, ?, ?, ?)",
        (sid, time.time(), know, tx),
    )
    db.conn.commit()


def test_the_check_round_counts_this_transaction_not_the_session(db, monkeypatch):
    """#3: max-rounds and plateau overrides fired on the first CHECK of a later transaction."""
    from empirica.cli.command_handlers import _workflow_check as wc

    for _ in range(5):
        _check_row(db, "S", "tx-A")
    monkeypatch.setattr(wc, "_get_db_for_session", lambda sid: SessionDatabase(db_path=db.db_path))
    assert wc._check_get_round_and_history("S", argparse.Namespace(), transaction_id="tx-B") == (1, [])
    round_num, history = wc._check_get_round_and_history("S", argparse.Namespace(), transaction_id="tx-A")
    assert round_num == 6 and len(history) == 3


def _goal(db, gid, sid, objective):
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, is_completed) VALUES (?,?,?,?,?,?,0)",
        (gid, sid, objective, "{}", time.time(), "{}"),
    )
    db.conn.commit()


def test_deferred_proposal_goals_in_the_prompt_convention_are_noticed(db):
    """#13: the system prompt names such goals 'Process inbox/<status>: <id>'; only 'Process proposal prop_%' matched."""
    from empirica.cli.command_handlers._workflow_shared import _maybe_add_deferred_proposals_note

    db.conn.execute(
        "INSERT INTO sessions (session_id, ai_id, start_time, components_loaded, project_id) VALUES ('S','x',?,0,'P')",
        (time.time(),),
    )
    _goal(db, "g1", "S", "Process inbox/accepted: prop_abc123")
    _goal(db, "g2", "S", "Process inboxes later")  # near miss: no slash
    retro: dict = {}
    _maybe_add_deferred_proposals_note(db.conn.cursor(), "S", retro)
    assert retro.get("deferred_proposals_count") == 1


def test_local_sources_are_not_sent_to_cortex(db):
    """#8: the artifact queries excluded visibility='local'; the source query did not."""
    from empirica.cli.command_handlers._workflow_postflight import _cortex_graph_source_nodes

    for sid, vis in (("src-local", "local"), ("src-shared", "shared"), ("src-legacy", None)):
        db.conn.execute(
            "INSERT INTO epistemic_sources (id, project_id, source_type, title, discovered_at, visibility) VALUES (?,?,?,?,?,?)",
            (sid, "P", "doc", sid, time.time(), vis),
        )
    db.conn.commit()
    nodes = _cortex_graph_source_nodes(db, ["src-local", "src-shared", "src-legacy"], set())
    sent = {n.get("id") or n.get("source_id") or n.get("ref") for n in nodes}
    assert "src-local" not in sent and {"src-shared", "src-legacy"} <= sent


@pytest.mark.parametrize("fmt", ["human", "json"])
def test_a_postflight_that_fails_validation_exits_1_and_says_why(monkeypatch, capsys, fmt):
    """#5: pre-validation failure printed only in JSON mode and returned None, so the CLI exited 0."""
    from empirica.cli.command_handlers import _workflow_postflight as wp

    monkeypatch.setattr(
        wp,
        "_parse_postflight_input",
        lambda args: {"session_id": "S", "vectors": {}, "reasoning": "", "output_format": fmt},
    )
    monkeypatch.setattr(wp, "_validate_postflight_preconditions", lambda sid: (False, "session S not found"))
    assert wp.handle_postflight_submit_command(argparse.Namespace()) == 1
    assert "session S not found" in capsys.readouterr().out


def test_the_preflight_session_comes_from_this_transaction_not_another_instance(tmp_path, monkeypatch):
    """#7: the lookup took the first active_work_*.json with a PREFLIGHT, from any instance on the machine."""
    import json

    from empirica.cli.command_handlers import _workflow_postflight as wp

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(wp.Path, "home", lambda: tmp_path)
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "active_work_foreign.json").write_text(json.dumps({"empirica_session_id": "FOREIGN"}))
    monkeypatch.setattr(wp.R, "transaction_read", staticmethod(lambda *a, **k: None))
    assert wp._postflight_resolve_preflight_session("CURRENT") == "CURRENT"
    monkeypatch.setattr(
        wp.R, "transaction_read", staticmethod(lambda *a, **k: {"session_id": "OPENED", "status": "open"})
    )
    assert wp._postflight_resolve_preflight_session("CURRENT") == "OPENED"


def test_the_check_probe_writes_no_check_and_declares_nothing(monkeypatch, capsys):
    """#2: `check` is documented as a probe that does not submit the gate decision, but it wrote a CHECK reflex with the
    transaction id (which the Sentinel accepts as the CHECK) and declared claims and falsifiers for the window."""
    from empirica.cli.command_handlers import _workflow_check as wc
    from empirica.core.canonical import git_enhanced_reflex_logger as gl

    written: list = []

    class _Logger:
        def __init__(self, **kw):
            pass

        def list_checkpoints(self, limit=1):
            return []

        def add_checkpoint(self, **kw):
            written.append(kw)
            return "ckpt"

    class _DB:
        def get_preflight_vectors(self, sid):
            return {"vectors": {"know": 0.7, "uncertainty": 0.3}}

        def close(self):
            pass

    declared: list = []
    monkeypatch.setattr(gl, "GitEnhancedReflexLogger", _Logger)
    monkeypatch.setattr(wc, "_get_db_for_session", lambda sid: _DB())
    monkeypatch.setattr(
        wc,
        "_check_cmd_parse_inputs",
        lambda args: {"session_id": "S", "explicit_confidence": None, "config_data": {}, "cycle": 1,
                      "claims": [{"claim": "x", "grounding": "read"}], "falsifiers": [], "output_format": "json"},
    )  # fmt: skip
    monkeypatch.setattr(wc, "_check_cmd_compute_drift", lambda b, c: ({"know": 0.7, "uncertainty": 0.3}, 0.0, {}))
    monkeypatch.setattr(wc, "_check_cmd_load_evidence", lambda db, sid: ([], [], "P"))
    monkeypatch.setattr(wc, "_check_patterns_for_warnings", lambda *a, **k: None)
    monkeypatch.setattr(wc, "_check_declare_claims", lambda *a, **k: declared.append(a) or {})
    monkeypatch.setattr(wc, "register_window_falsifiers", lambda *a, **k: declared.append(a) or {})
    wc.handle_check_command(argparse.Namespace())
    assert written == [] and declared == []


def test_a_check_override_is_what_gets_stored(monkeypatch):
    """#1: the weave-enforce and blindspot overrides changed only the response's decision; the stored CHECK row (which the
    Sentinel reads) still said proceed."""
    from empirica.cli.command_handlers import _workflow_check as wc

    stored: list = []

    def _store(session_id, round_num, vectors, decision, reasoning, cycle, gate=None):
        stored.append(decision)
        return "ckpt", "tx-1", []

    def _weave(result, decision, session_id, transaction_id):
        result["weave_enforce"] = {"enforced": True}
        return "investigate"

    monkeypatch.setattr(wc.R, "transaction_id", staticmethod(lambda *a, **k: "tx-1"))
    patches = {
        "_check_parse_inputs": lambda args: {"session_id": "S", "vectors": {"know": 0.9, "uncertainty": 0.1},
                                             "reasoning": "r", "cycle": 1, "output_format": "json", "claims": [], "decision": None,
                                             "falsifiers": []},
        "_check_bootstrap_gate": lambda sid, v: ({}, {}),
        "_check_get_round_and_history": lambda sid, args, transaction_id=None: (1, []),
        "_check_load_dynamic_thresholds": lambda sid: (None, 0.35, {}),
        "_check_detect_diminishing_returns": lambda *a: {"detected": False},
        "_check_gate_decision": lambda *a, **k: ("proceed", "proceed", None, None),
        "_check_store_and_publish": _store,
        "_check_auto_checkpoint": lambda *a, **k: None,
        "_check_create_snapshot": lambda *a, **k: None,
        "_check_declare_claims": lambda *a, **k: {},
        "register_window_falsifiers": lambda *a, **k: {},
        "_check_run_blindspot_scan": lambda result, decision, *a: decision,
        "_check_apply_weave_enforce": _weave,
        "_check_surface_blindspots": lambda *a, **k: None,
        "_check_enrich_context": lambda *a, **k: None,
        "_check_format_output": lambda *a, **k: None,
    }  # fmt: skip
    for name, fn in patches.items():
        monkeypatch.setattr(wc, name, fn)
    wc.handle_check_submit_command(argparse.Namespace())
    assert stored == ["investigate"]
