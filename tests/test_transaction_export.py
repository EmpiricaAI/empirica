"""`grounding-export --transactions`: per-transaction history with structure only, no text.

cowork (prop_fmymqnc5zze2nbclxoyfue45yq, prop_jqdsaiptp5eddb63baxvhozrzq) asked for a transaction export that can leave a
practice without a content review: empirica-mesh-support correctly refused to hand-shape one because goal objectives and
artifact titles carry people's names. David chose it 2026-10-04.

The contract is structural, so the tests hold it structurally: a marker is planted in EVERY free-text field the store has
(reasoning, evidence, finding/unknown/dead-end text, goal objective, reflex_data prose) and must appear nowhere in the output,
and a walker checks every string that does appear is a bounded identifier-shaped token.
"""

from __future__ import annotations

import json
import re
import time
import types

import pytest

from empirica.core.transaction_export import export_transactions
from empirica.data.session_database import SessionDatabase

MARK = "ZQXMARKER"
VEC13 = {
    "engagement": 0.9,
    "know": 0.8,
    "do": 0.7,
    "context": 0.6,
    "clarity": 0.5,
    "coherence": 0.4,
    "signal": 0.3,
    "density": 0.2,
    "state": 0.1,
    "change": 0.15,
    "completion": 0.25,
    "impact": 0.35,
    "uncertainty": 0.45,
}
NOW = time.time()


@pytest.fixture
def world(tmp_path):
    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name=f"{MARK}-project")
    sid = db.create_session(ai_id="a", project_id=pid)
    other = db.create_session(ai_id="other", project_id=pid)
    yield types.SimpleNamespace(db=db, pid=pid, sid=sid, other=other)
    try:
        db.close()
    except Exception:  # a test may close it to hand the file to the CLI handler
        pass


def _stamp(db, row_id, ts):
    db.conn.execute("UPDATE reflexes SET timestamp = ? WHERE id = ?", (ts, row_id))
    db.conn.commit()


def _tx(world, tx, start, *, checks=1, post=True, session=None):
    """One transaction: PREFLIGHT, n CHECKs and a POSTFLIGHT, every free-text field carrying the marker."""
    sid = session or world.sid
    db = world.db
    r = db.store_vectors(
        sid,
        "PREFLIGHT",
        VEC13,
        reasoning=f"{MARK} reasoning",
        transaction_id=tx,
        metadata={"prompt": f"{MARK} prompt", "task_context": f"{MARK} ctx"},
    )
    _stamp(db, r, start)
    for n in range(checks):
        r = db.store_vectors(
            sid,
            "CHECK",
            {**VEC13, "know": 0.8 + n / 100},
            reasoning=f"{MARK} check",
            transaction_id=tx,
            metadata={
                "decision": "proceed" if n == checks - 1 else "investigate",
                "confidence": 0.7,
                "cycle": n + 1,
                "gaps": [f"{MARK} gap"],
            },
        )
        _stamp(db, r, start + 10 + n)
    if post:
        r = db.store_vectors(
            sid,
            "POSTFLIGHT",
            {**VEC13, "completion": 1.0},
            reasoning=f"{MARK} post",
            transaction_id=tx,
            metadata={
                "work_type": "code",
                "tool_call_count": 12,
                "internal_consistency": "good",
                "postflight_confidence": 0.9,
                "task_summary": f"{MARK} summary",
                "retrospective": f"{MARK} retro",
                "git_commit_sha": "ab12cd34",
                "git_notes_ref": "empirica/session/0a21af76-6609-4d5d-9fd1-44e07551f7aa/postflight/7a4676a8-422a-4dec-99f7-099a820aa4f4",
            },
        )
        _stamp(db, r, start + 100)


def _verification(world, tx, phase, session=None):
    world.db.conn.execute(
        "INSERT INTO grounded_verifications (verification_id, session_id, ai_id, self_assessed_vectors, grounded_vectors, "
        "calibration_gaps, grounded_coverage, overall_calibration_score, evidence_count, grounded_rationale, domain, "
        "transaction_id, phase, practitioner_model, compliance_status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"v-{tx}-{phase}",
            session or world.sid,
            "a",
            json.dumps({"know": 0.8, "do": 0.7}),
            json.dumps(
                {
                    "know": {"value": 0.6, "confidence": 0.7, "evidence_count": 4, "source": "artifacts"},
                    "do": {"value": 0.9, "confidence": 0.8, "evidence_count": 2, "source": "git"},
                }
            ),
            json.dumps({"know": 0.2, "do": -0.2}),
            0.5,
            0.12,
            6,
            f"{MARK} rationale",
            f"{MARK}-domain",
            tx,
            phase,
            "claude-sonnet-5-5",
            "complete",
            NOW,
        ),
    )
    world.db.conn.commit()


def _goal(world, gid, tx, status="completed"):
    world.db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, completed_timestamp, goal_data, status, "
        "project_id, transaction_id, description) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (gid, world.sid, f"{MARK} objective", "{}", NOW - 50, NOW - 5, "{}", status, world.pid, tx, f"{MARK} body"),
    )
    world.db.conn.commit()


def _export(world, **kw):
    return export_transactions(world.db.conn, "a", **kw)


# ── shape ───────────────────────────────────────────────────────────────────


def test_a_transaction_carries_its_phases_in_order_with_all_13_vectors(world):
    _tx(world, "tx1", NOW - 1000, checks=2)

    (t,) = _export(world)["transactions"]

    assert t["transaction_id"] == "tx1" and t["session_id"] == world.sid and t["ai_id"] == "a"
    assert t["preflight"]["vectors"] == VEC13
    assert [c["cycle"] for c in t["checks"]] == [1, 2] and [c["decision"] for c in t["checks"]] == [
        "investigate",
        "proceed",
    ]
    assert t["postflight"]["vectors"]["completion"] == 1.0
    assert t["postflight"]["work_type"] == "code" and t["postflight"]["tool_call_count"] == 12
    assert (
        t["postflight"]["git_notes_ref"].startswith("empirica/session/")
        and t["postflight"]["git_commit_sha"] == "ab12cd34"
    )
    assert t["preflight"]["timestamp"] < t["checks"][0]["timestamp"] < t["postflight"]["timestamp"]


def test_a_transaction_that_has_not_closed_has_no_postflight_not_a_missing_key_error(world):
    _tx(world, "open", NOW - 100, post=False, checks=0)

    (t,) = _export(world)["transactions"]

    assert t["postflight"] is None and t["checks"] == [] and t["preflight"] is not None


def test_only_the_requested_practice_is_exported(world):
    _tx(world, "mine", NOW - 1000)
    _tx(world, "theirs", NOW - 900, session=world.other)

    assert [t["transaction_id"] for t in _export(world)["transactions"]] == ["mine"]


def test_newest_first_with_since_and_limit_and_a_truthful_total(world):
    for i in range(5):
        _tx(world, f"tx{i}", NOW - (5 - i) * 1000)

    out = _export(world, limit=2)
    assert [t["transaction_id"] for t in out["transactions"]] == ["tx4", "tx3"]
    assert out["total_matching"] == 5 and out["truncated"] is True and out["returned"] == 2

    since = _export(world, since=NOW - 3500)
    assert [t["transaction_id"] for t in since["transactions"]] == ["tx4", "tx3", "tx2"]
    assert since["total_matching"] == 3 and since["truncated"] is False


def test_rows_with_no_transaction_id_are_counted_not_emitted(world):
    _tx(world, "tx1", NOW - 1000)
    world.db.store_vectors(world.sid, "PREFLIGHT", VEC13)  # a legacy row: no transaction id

    out = _export(world)

    assert [t["transaction_id"] for t in out["transactions"]] == ["tx1"]
    assert out["skipped_without_transaction_id"] == 1


# ── self versus grounded ────────────────────────────────────────────────────


def test_grounded_rows_carry_self_grounded_and_gap_per_vector(world):
    _tx(world, "tx1", NOW - 1000)
    _verification(world, "tx1", "praxic")

    (v,) = _export(world)["transactions"][0]["grounded"]

    assert (
        v["phase"] == "praxic"
        and v["practitioner_model"] == "claude-sonnet-5-5"
        and v["compliance_status"] == "complete"
    )
    assert v["vectors"]["know"] == {
        "self": 0.8,
        "grounded": 0.6,
        "gap": 0.2,
        "confidence": 0.7,
        "evidence_count": 4,
        "source": "artifacts",
    }
    assert v["vectors"]["do"]["gap"] == -0.2
    assert v["grounded_coverage"] == 0.5 and v["overall_calibration_score"] == 0.12


def test_a_transaction_with_no_verification_has_an_empty_grounded_list(world):
    _tx(world, "tx1", NOW - 1000)

    assert _export(world)["transactions"][0]["grounded"] == []


# ── links ───────────────────────────────────────────────────────────────────


def test_linked_goals_and_artifacts_are_ids_with_types_only(world):
    _tx(world, "tx1", NOW - 1000)
    _tx(world, "tx2", NOW - 500)
    _goal(world, "goal-1", "tx1")
    world.db.log_finding(world.pid, world.sid, f"{MARK} finding", transaction_id="tx1", goal_id="goal-1")
    world.db.log_unknown(world.pid, world.sid, f"{MARK} unknown", transaction_id="tx1")
    world.db.log_dead_end(world.pid, world.sid, f"{MARK} approach", f"{MARK} why", transaction_id="tx1")
    world.db.log_finding(world.pid, world.sid, f"{MARK} elsewhere", transaction_id="tx2")

    by_id = {t["transaction_id"]: t for t in _export(world)["transactions"]}
    t1 = by_id["tx1"]

    assert t1["goals"] == [
        {
            "id": "goal-1",
            "status": "completed",
            "created_timestamp": pytest.approx(NOW - 50),
            "completed_timestamp": pytest.approx(NOW - 5),
        }
    ]
    assert sorted(a["type"] for a in t1["artifacts"]) == ["dead_end", "finding", "unknown"]
    assert all(set(a) <= {"id", "type", "goal_id"} for a in t1["artifacts"])
    assert next(a for a in t1["artifacts"] if a["type"] == "finding")["goal_id"] == "goal-1"
    assert [a["type"] for a in by_id["tx2"]["artifacts"]] == ["finding"], (
        "an artifact belongs to its own transaction only"
    )


# ── the contract: structure only ────────────────────────────────────────────


def _everything(world):
    _tx(world, "tx1", NOW - 1000, checks=2)
    _verification(world, "tx1", "praxic")
    _goal(world, "goal-1", "tx1")
    world.db.log_finding(world.pid, world.sid, f"{MARK} finding", transaction_id="tx1", goal_id="goal-1")
    world.db.log_unknown(world.pid, world.sid, f"{MARK} unknown", transaction_id="tx1")
    return _export(world)


def test_no_free_text_field_of_the_store_appears_in_the_output(world):
    """The leak control: the marker is in reasoning, prompts, gaps, summaries, rationale, domain, objective and body."""
    out = _everything(world)

    assert "tx1" in json.dumps(out), "positive control: the output is not empty"
    assert MARK not in json.dumps(out)


_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")  # no spaces: a sentence cannot match
_BANNED_KEYS = {
    "reasoning",
    "evidence",
    "objective",
    "title",
    "description",
    "finding",
    "unknown",
    "approach",
    "task_summary",
    "retrospective",
    "prompt",
    "task_context",
    "gaps",
    "grounded_rationale",
    "domain",
    "note",
    "text",
    "body",
}


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in _BANNED_KEYS, f"{path}/{k}: a text field name in the output"
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def test_every_leaf_is_a_number_a_bool_null_or_a_bounded_identifier_shaped_string(world):
    leaves = list(_walk(_everything(world)))

    assert leaves
    for path, value in leaves:
        if isinstance(value, str):
            assert _SAFE.fullmatch(value), f"{path}: {value!r} is not an identifier-shaped token"
        else:
            assert value is None or isinstance(value, (int, float, bool)), f"{path}: {type(value).__name__}"


def test_a_prose_value_in_a_whitelisted_reflex_field_is_dropped_not_passed_through(world):
    """The whitelist names keys; the value check is what stops prose arriving under a key we trust."""
    r = world.db.store_vectors(
        world.sid,
        "POSTFLIGHT",
        VEC13,
        transaction_id="tx1",
        metadata={
            "work_type": f"{MARK} a sentence, with punctuation. And more words than a work type has.",
            "internal_consistency": "good",
            "tool_call_count": f"{MARK}",
        },
    )
    _stamp(world.db, r, NOW - 10)
    world.db.store_vectors(world.sid, "PREFLIGHT", VEC13, transaction_id="tx1")

    post = _export(world)["transactions"][0]["postflight"]

    assert "work_type" not in post and "tool_call_count" not in post
    assert post["internal_consistency"] == "good"
    assert MARK not in json.dumps(post)


def test_a_malformed_reflex_data_row_does_not_break_the_export(world):
    _tx(world, "tx1", NOW - 1000)
    world.db.conn.execute("UPDATE reflexes SET reflex_data = 'not json {' WHERE phase = 'POSTFLIGHT'")
    world.db.conn.commit()

    (t,) = _export(world)["transactions"]

    assert t["postflight"]["vectors"]["completion"] == 1.0 and "work_type" not in t["postflight"]


def test_a_phantom_auto_checkpoint_check_is_flagged(world):
    _tx(world, "tx1", NOW - 1000, checks=0)
    r = world.db.store_vectors(world.sid, "CHECK", {}, transaction_id="tx1", metadata={"auto_checkpoint": True})
    _stamp(world.db, r, NOW - 900)

    (t,) = _export(world)["transactions"]

    assert t["checks"][0]["auto_checkpoint"] is True


# ── wiring ──────────────────────────────────────────────────────────────────


def _handler(world, monkeypatch, capsys, **kw):
    from empirica.cli.command_handlers.monitor_commands import handle_grounding_export_command

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(world.db.db_path))
    args = types.SimpleNamespace(ai_id="a", output="json", transactions=False, since=None, limit=None, **kw)
    return handle_grounding_export_command(args), capsys


def test_the_flag_adds_the_transactions_and_the_default_output_is_unchanged(world, monkeypatch, capsys):
    _tx(world, "tx1", NOW - 1000)
    world.db.close()

    rc, _ = _handler(world, monkeypatch, capsys)
    default = json.loads(capsys.readouterr().out)
    assert rc == 0 and "transactions" not in default

    from empirica.cli.command_handlers.monitor_commands import handle_grounding_export_command

    args = types.SimpleNamespace(ai_id="a", output="json", transactions=True, since=None, limit=10)
    assert handle_grounding_export_command(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["schema"] == "empirica.transaction_export.v1"
    assert out["transactions"][0]["transaction_id"] == "tx1"
    assert MARK not in json.dumps(out)


def test_a_bad_since_is_a_clear_error_not_an_empty_export(world, monkeypatch, capsys):
    from empirica.cli.command_handlers.monitor_commands import handle_grounding_export_command

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(world.db.db_path))
    args = types.SimpleNamespace(ai_id="a", output="json", transactions=True, since="last tuesday", limit=None)

    assert handle_grounding_export_command(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "since" in out["error"]


def test_the_flags_are_on_the_parser():
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "grounding-export" in a.choices)
    dests = {a.dest for a in sub.choices["grounding-export"]._actions}

    assert {"transactions", "since", "limit"} <= dests
