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


def test_grounded_verification_uses_the_projects_own_type_for_its_domain(tmp_path, monkeypatch):
    """#6: the type was read from a sessions column that does not exist, so every practice was graded as 'default'."""
    from empirica.cli.command_handlers import _workflow_postflight as wp
    from empirica.core.post_test import collector, grounded_calibration, phase_boundary
    from empirica.core.project_types import calibration_domain

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("name: r\ntype: research\n")
    seen: dict = {}

    class _DB:
        def get_session(self, sid):
            return {"project_id": "P"}

        def close(self):
            pass

    monkeypatch.setattr(wp, "_get_db_for_session", lambda sid: _DB())
    monkeypatch.setattr(wp, "_stamp_practitioner_model", lambda db, tx: None)
    monkeypatch.setattr(collector.EvidenceProfile, "resolve", staticmethod(lambda **k: None))
    monkeypatch.setattr(phase_boundary, "detect_phase_boundary", lambda *a, **k: None)
    monkeypatch.setattr(grounded_calibration, "run_grounded_verification", lambda **k: seen.update(k) or None)
    wp._run_grounded_verification("S", {}, {}, None, "code", "tx", project_path=str(tmp_path))
    assert seen["domain"] == calibration_domain("research") != "default"


def test_the_storage_goal_is_the_one_bound_to_the_closed_transaction(db):
    """#6: the goal came from a sessions column that does not exist, so it was always None."""
    from empirica.cli.command_handlers._workflow_postflight import _postflight_goal_for_session

    db.conn.execute(
        "INSERT INTO reflexes (session_id, phase, round, timestamp, transaction_id) VALUES ('S','POSTFLIGHT',1,?,'tx-9')",
        (time.time(),),
    )
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, transaction_id) "
        "VALUES ('g-9','S','o','{}',?,'{}','tx-9')",
        (time.time(),),
    )
    db.conn.commit()
    assert _postflight_goal_for_session(db, "S") == "g-9"
    assert _postflight_goal_for_session(db, "OTHER") is None


def test_a_failing_goal_criteria_stage_becomes_a_postflight_warning(monkeypatch):
    """#9: the stage caught its own exception and returned None, so _soft_run never recorded a warning."""
    from empirica.cli.command_handlers._workflow_postflight import _run_postflight_goal_criteria
    from empirica.cli.command_handlers._workflow_shared import _soft_run
    from empirica.core.post_test import criterion_evaluators

    def boom(*a, **k):
        raise RuntimeError("criteria store unreadable")

    monkeypatch.setattr(criterion_evaluators, "evaluate_goal_criteria", boom)
    warnings: list = []
    _soft_run("goal_criteria", warnings, _run_postflight_goal_criteria, "S", "tx", None)
    assert [w.get("stage") for w in warnings] == ["goal_criteria"]


def test_a_failing_grounded_verification_becomes_a_postflight_warning(monkeypatch, tmp_path):
    from empirica.cli.command_handlers import _workflow_postflight as wp
    from empirica.cli.command_handlers._workflow_shared import _soft_run

    monkeypatch.setenv("HOME", str(tmp_path))  # the crash log goes under Path.home()

    def boom(sid):
        raise RuntimeError("db gone")

    monkeypatch.setattr(wp, "_get_db_for_session", boom)
    warnings: list = []
    _soft_run("grounded_verification", warnings, wp._run_grounded_verification, "S", {}, {}, None, "code", "tx")
    assert [w.get("stage") for w in warnings] == ["grounded_verification"]


def test_a_cortex_sync_that_did_not_happen_becomes_a_postflight_warning(monkeypatch):
    import urllib.error
    import urllib.request

    from empirica.cli.command_handlers import _workflow_postflight as wp
    from empirica.cli.command_handlers._workflow_shared import _soft_run
    from empirica.core.auth import cortex_oauth

    monkeypatch.setattr(cortex_oauth, "cortex_bearer", lambda loader: {"url": "https://cortex.invalid", "bearer": "t"})

    def offline(*a, **k):
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    monkeypatch.setattr(wp, "_get_db_for_session", lambda sid: (_ for _ in ()).throw(RuntimeError("no db")))
    warnings: list = []
    _soft_run("cortex_sync", warnings, wp._run_postflight_cortex_sync, "S", "r", None)
    assert [w.get("stage") for w in warnings] == ["cortex_sync"]


def test_postflight_readers_see_the_summarys_score_and_the_feedback_stays_off(monkeypatch):
    """#4/#21: readers asked for calibration_score, which the summary keeps as _internal_calibration_score."""
    from empirica.cli.command_handlers import _workflow_postflight as wp

    summary = {"_internal_calibration_score": 0.12, "gaps": {}, "grounded_coverage": 0.5}
    assert wp._summary_score(summary) == 0.12
    calls: list = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(a))
    wp._pipeline_cortex_cache_feedback("S", {}, summary)  # a low score, but the opt-in feedback stays off
    assert calls == []


def test_a_latest_postflight_without_a_transaction_binds_no_older_goal(db):
    """1.17.9 sweep: the subquery skipped a NULL-transaction POSTFLIGHT and bound the previous transaction's goal."""
    from empirica.cli.command_handlers._workflow_postflight import _postflight_goal_for_session

    now = time.time()
    db.conn.execute(
        "INSERT INTO reflexes (session_id, phase, round, timestamp, transaction_id) VALUES ('S','POSTFLIGHT',1,?,'tx-old')",
        (now - 100,),
    )
    db.conn.execute(
        "INSERT INTO reflexes (session_id, phase, round, timestamp, transaction_id) VALUES ('S','POSTFLIGHT',1,?,NULL)",
        (now,),
    )
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, transaction_id) VALUES ('g-old','S','o','{}',?,'{}','tx-old')",
        (now - 100,),
    )
    db.conn.commit()
    assert _postflight_goal_for_session(db, "S") is None


def test_a_check_submit_whose_save_fails_exits_1(monkeypatch):
    from empirica.cli.command_handlers import _workflow_check as wc

    monkeypatch.setattr(wc.R, "transaction_id", staticmethod(lambda *a, **k: "tx-1"))
    patches = {
        "_check_parse_inputs": lambda args: {"session_id": "S", "vectors": {"know": 0.9, "uncertainty": 0.1}, "decision": None,
                                             "reasoning": "r", "cycle": 1, "output_format": "json", "claims": [], "falsifiers": []},
        "_check_bootstrap_gate": lambda sid, v: ({}, {}),
        "_check_get_round_and_history": lambda sid, args, transaction_id=None: (1, []),
        "_check_load_dynamic_thresholds": lambda sid: (None, 0.35, {}),
        "_check_detect_diminishing_returns": lambda *a: {"detected": False},
        "_check_gate_decision": lambda *a, **k: ("proceed", "proceed", None, None),
        "_check_run_blindspot_scan": lambda result, decision, *a: decision,
        "_check_apply_weave_enforce": lambda result, decision, *a: decision,
        "_check_store_and_publish": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
        "_check_format_output": lambda *a, **k: None,
    }  # fmt: skip
    for name, fn in patches.items():
        monkeypatch.setattr(wc, name, fn)
    assert wc.handle_check_submit_command(argparse.Namespace()) == 1


def test_edges_to_a_local_source_are_recognised_for_dropping(db):
    from empirica.cli.command_handlers._workflow_postflight import _cortex_local_source_ids

    for sid, vis in (("src-local", "local"), ("src-shared", "shared")):
        db.conn.execute(
            "INSERT INTO epistemic_sources (id, project_id, source_type, title, discovered_at, visibility) VALUES (?,?,?,?,?,?)",
            (sid, "P", "doc", sid, time.time(), vis),
        )
    db.conn.commit()
    assert _cortex_local_source_ids(db, {"src-local", "src-shared", "finding-1"}) == {"src-local"}


def test_an_unreadable_criteria_store_becomes_a_postflight_warning(monkeypatch):
    """1.17.9 sweep: evaluate_goal_criteria itself swallowed the load failure, so the stage's re-raise never fired."""
    from empirica.cli.command_handlers._workflow_postflight import _run_postflight_goal_criteria
    from empirica.cli.command_handlers._workflow_shared import _soft_run
    from empirica.core.goals import repository

    def boom(self, sid):
        raise RuntimeError("criteria table unreadable")

    monkeypatch.setattr(repository.GoalRepository, "list_active_criteria_for_session", boom)
    monkeypatch.setattr(repository.GoalRepository, "__init__", lambda self, **k: None)
    warnings: list = []
    _soft_run("goal_criteria", warnings, _run_postflight_goal_criteria, "S", "tx", None)
    assert [w.get("stage") for w in warnings] == ["goal_criteria"]


def test_the_ai_facing_postflight_result_carries_no_per_vector_gaps():
    """1.17.9 sweep: the summary now carries gaps for its readers; the AI's copy must not (Goodhart rule)."""
    from empirica.cli.command_handlers import _workflow_postflight as wp

    gv = {"_internal_calibration_score": 0.2, "gaps": {"know": 0.3}, "evidence_summary": "e", "grounded_coverage": 0.5}
    from empirica.core.post_test.grounded_calibration import ai_facing_summary

    out = ai_facing_summary(gv)
    assert "gaps" not in out and "_internal_calibration_score" not in out and out["evidence_summary"] == "e"
    assert "ai_facing_summary(grounded_verification)" in __import__("inspect").getsource(wp)


def test_a_failure_inside_grounded_verification_reaches_the_postflight_warnings(monkeypatch, tmp_path):
    """1.17.9 sweep: run_grounded_verification caught everything and returned None, below the stage's re-raise."""
    from empirica.cli.command_handlers import _workflow_postflight as wp
    from empirica.cli.command_handlers._workflow_shared import _soft_run
    from empirica.core.post_test import collector, grounded_calibration, phase_boundary

    monkeypatch.setenv("HOME", str(tmp_path))

    class _DB:
        def get_session(self, sid):
            return {"project_id": "P"}

        def close(self):
            pass

    monkeypatch.setattr(wp, "_get_db_for_session", lambda sid: _DB())
    monkeypatch.setattr(collector.EvidenceProfile, "resolve", staticmethod(lambda **k: None))
    monkeypatch.setattr(phase_boundary, "detect_phase_boundary", lambda *a, **k: None)
    monkeypatch.setattr(
        grounded_calibration,
        "PostTestCollector",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("collector broke")),
    )
    warnings: list = []
    _soft_run(
        "grounded_verification",
        warnings,
        wp._run_grounded_verification,
        "S",
        {},
        {},
        None,
        "code",
        "tx",
        project_path=str(tmp_path),
    )
    assert [w.get("stage") for w in warnings] == ["grounded_verification"]


def test_episodic_memory_carries_no_calibration_numbers():
    """Second review pass: once the summary had gaps, the narrative put per-vector divergence into retrievable memory."""
    from empirica.cli.command_handlers._workflow_postflight import _build_episodic_narrative

    gv = {"evidence_count": 5, "_internal_calibration_score": 0.4, "grounded_coverage": 0.6, "gaps": {"know": 0.3}}
    text = _build_episodic_narrative("did the work", {}, gv)
    assert text == "did the work" and "gap" not in text and "score" not in text


def test_edges_to_a_local_artifact_are_recognised_for_dropping(db):
    """Second review pass: a shared finding's edge to a local finding carried the local id off the machine."""
    from empirica.cli.command_handlers._workflow_postflight import _cortex_local_source_ids

    fid = db.log_finding("P", "S", "a private observation", visibility="local")
    assert fid in _cortex_local_source_ids(db, {fid, "other"})
