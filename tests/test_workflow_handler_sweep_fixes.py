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
