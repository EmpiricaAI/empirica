"""goals_touched (many-to-many transaction to goal) and the opt-in --content flag on `grounding-export --transactions`.

cowork (prop_brbkfst3nbcrpibih3v726iudi), the observatory pivoted to goals:

1. A goal links to the one transaction it was created or activated in (goals.transaction_id), so a goal worked across N
   transactions showed one. A goal is in play in a transaction by the Sentinel's own definition: created there, OR an artifact
   logged there carries its goal_id, OR a task of it was created or completed between PREFLIGHT and POSTFLIGHT.
2. David's ruling: an opt-in flag that adds goal objectives and artifact title lines for a view inside the owner's own tenant. The
   default stays content-free; the envelope says `content_scope: tenant` and `do_not_share: true`; credentials in the text are
   redacted; and it is refused for any practice but this store's own.
"""

from __future__ import annotations

import json
import time
import types
import uuid

import pytest

from empirica.core.transaction_export import export_transactions
from empirica.data.session_database import SessionDatabase

NOW = time.time()
SECRET = "ctx_empirica_adm_0123456789abcdef0123456789abcdef"  # noqa: S105  (a fake, to prove redaction)
NAME = "Georg Fechter"


def U(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))


@pytest.fixture
def world(tmp_path):
    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="a", project_id=pid)
    other = db.create_session(ai_id="other", project_id=pid)
    yield types.SimpleNamespace(db=db, pid=pid, sid=sid, other=other)
    try:
        db.close()
    except Exception:
        pass


def _tx(world, name, start, *, end=True, session=None):
    sid = session or world.sid
    tx = U(name)
    pre = world.db.store_vectors(sid, "PREFLIGHT", {"know": 0.5}, transaction_id=tx)
    world.db.conn.execute("UPDATE reflexes SET timestamp = ? WHERE id = ?", (start, pre))
    if end:
        post = world.db.store_vectors(sid, "POSTFLIGHT", {"know": 0.6}, transaction_id=tx)
        world.db.conn.execute("UPDATE reflexes SET timestamp = ? WHERE id = ?", (start + 100, post))
    world.db.conn.commit()
    return tx


def _goal(world, name, tx=None, session=None, objective=None):
    gid = U(name)
    world.db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status, project_id, transaction_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            gid,
            session or world.sid,
            objective or f"objective of {name}",
            "{}",
            NOW - 5000,
            "{}",
            "in_progress",
            world.pid,
            tx,
        ),
    )
    world.db.conn.commit()
    return gid


def _task(world, goal_id, created=None, completed=None, created_raw=None):
    world.db.conn.execute(
        "INSERT INTO subtasks (id, goal_id, description, status, created_timestamp, completed_timestamp, subtask_data) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            goal_id,
            f"task text {NAME}",
            "pending",
            created_raw if created_raw is not None else created,
            completed,
            "{}",
        ),
    )
    world.db.conn.commit()


def _export(world, **kw):
    return export_transactions(world.db.conn, "a", **kw)


def _touched(rec):
    return {g["id"]: sorted(g["via"]) for g in rec["goals_touched"]}


# ── goals_touched ───────────────────────────────────────────────────────────


def test_a_goal_created_in_the_transaction_is_touched_via_created(world):
    tx = _tx(world, "t1", NOW - 1000)
    gid = _goal(world, "g1", tx)

    (rec,) = _export(world)["transactions"]

    assert _touched(rec) == {gid: ["created"]}


def test_a_goal_named_by_an_artifact_logged_in_the_transaction_is_touched_via_artifact(world):
    tx = _tx(world, "t1", NOW - 1000)
    gid = _goal(world, "old-goal", None)
    world.db.log_finding(world.pid, world.sid, "a finding", transaction_id=tx, goal_id=gid)

    (rec,) = _export(world)["transactions"]

    assert _touched(rec) == {gid: ["artifact"]}


def test_a_goal_whose_task_was_created_or_completed_inside_the_window_is_touched_via_task(world):
    _tx(world, "t1", NOW - 1000)
    created_in, completed_in = _goal(world, "created-in"), _goal(world, "completed-in")
    _task(world, created_in, created=NOW - 950)
    _task(world, completed_in, created=NOW - 9000, completed=NOW - 920)

    (rec,) = _export(world)["transactions"]

    assert _touched(rec) == {created_in: ["task"], completed_in: ["task"]}


def test_a_task_outside_the_window_does_not_touch_the_goal(world):
    _tx(world, "t1", NOW - 1000)
    gid = _goal(world, "elsewhere")
    _task(world, gid, created=NOW - 5000, completed=NOW - 4000)  # before PREFLIGHT
    _task(world, gid, created=NOW - 500)  # after POSTFLIGHT (the window is start..start+100)

    (rec,) = _export(world)["transactions"]

    assert rec["goals_touched"] == []


def test_an_open_transaction_has_no_upper_bound_so_a_task_made_since_counts(world):
    _tx(world, "open", NOW - 1000, end=False)
    gid = _goal(world, "live")
    _task(world, gid, created=NOW - 10)

    (rec,) = _export(world)["transactions"]

    assert _touched(rec) == {gid: ["task"]}


def test_all_three_ways_to_one_goal_are_one_entry_with_every_via(world):
    tx = _tx(world, "t1", NOW - 1000)
    gid = _goal(world, "g1", tx)
    world.db.log_finding(world.pid, world.sid, "f", transaction_id=tx, goal_id=gid)
    _task(world, gid, created=NOW - 950)

    (rec,) = _export(world)["transactions"]

    assert _touched(rec) == {gid: ["artifact", "created", "task"]}


def test_a_legacy_text_timestamp_on_a_task_is_not_read_as_inside_every_window(world):
    """SQLite ranks any TEXT above any number; a bare >= would put a '2025-12-31 18:24:03' task inside every window."""
    _tx(world, "t1", NOW - 1000)
    gid = _goal(world, "legacy")
    _task(world, gid, created_raw="2025-12-31 18:24:03")

    (rec,) = _export(world)["transactions"]

    assert rec["goals_touched"] == []


def test_another_practices_goal_is_never_touched(world):
    _tx(world, "t1", NOW - 1000)
    theirs = _goal(world, "theirs", session=world.other)
    _task(world, theirs, created=NOW - 950)

    (rec,) = _export(world)["transactions"]

    assert rec["goals_touched"] == []


def test_a_goal_id_that_is_a_word_is_dropped_from_goals_touched(world):
    tx = _tx(world, "t1", NOW - 1000)
    world.db.log_finding(world.pid, world.sid, "f", transaction_id=tx, goal_id="Client-Acme-Renewal")

    (rec,) = _export(world)["transactions"]

    assert rec["goals_touched"] == [] and "Acme" not in json.dumps(rec)


# ── --content ───────────────────────────────────────────────────────────────


def test_the_default_is_content_free_and_says_so(world):
    tx = _tx(world, "t1", NOW - 1000)
    _goal(world, "g1", tx, objective=f"talk to {NAME}")
    world.db.log_finding(world.pid, world.sid, f"{NAME} said yes", transaction_id=tx)

    out = _export(world)

    assert out["content_scope"] == "none" and "do_not_share" not in out
    assert NAME not in json.dumps(out)


def test_content_adds_goal_objectives_and_artifact_text_and_marks_the_envelope(world):
    tx = _tx(world, "t1", NOW - 1000)
    _goal(world, "g1", tx, objective=f"talk to {NAME}")
    world.db.log_finding(world.pid, world.sid, f"{NAME} said yes", transaction_id=tx)
    world.db.log_unknown(world.pid, world.sid, "what next?", transaction_id=tx)

    out = _export(world, content=True)
    (rec,) = out["transactions"]

    assert out["content_scope"] == "tenant" and out["do_not_share"] is True
    assert rec["goals"][0]["objective"] == f"talk to {NAME}"
    assert {a["type"]: a["text"] for a in rec["artifacts"]} == {"finding": f"{NAME} said yes", "unknown": "what next?"}


def test_content_text_is_truncated_to_one_bounded_line(world):
    tx = _tx(world, "t1", NOW - 1000)
    world.db.log_finding(world.pid, world.sid, "first line\nsecond line " + "x" * 900, transaction_id=tx)

    (rec,) = _export(world, content=True)["transactions"]
    text = rec["artifacts"][0]["text"]

    assert "\n" not in text and len(text) <= 300 and text.startswith("first line second line")


def test_a_credential_in_the_content_text_is_redacted_and_counted(world):
    """The nine key-holding dead-ends this session were exactly this: a credential in an artifact's text."""
    tx = _tx(world, "t1", NOW - 1000)
    world.db.log_finding(world.pid, world.sid, f"calling cortex with api_key {SECRET} failed", transaction_id=tx)
    _goal(world, "g1", tx, objective=f"use token {SECRET}")

    out = _export(world, content=True)

    assert SECRET not in json.dumps(out) and "ctx_empirica_adm_0123" not in json.dumps(out)
    assert out["content_redactions"] >= 2


def test_structure_only_fields_are_unchanged_by_the_flag(world):
    """Control: --content adds text, it must not loosen the id and vocabulary checks of everything else."""
    tx = _tx(world, "t1", NOW - 1000)
    world.db.log_finding(world.pid, world.sid, "f", transaction_id=tx, goal_id="Client-Acme-Renewal")

    out = _export(world, content=True)

    assert "Client-Acme-Renewal" not in json.dumps(out) and out["dropped_unsafe_values"] >= 1


# ── wiring ──────────────────────────────────────────────────────────────────


def _handler(world, monkeypatch, capsys, own="a", **kw):
    from empirica.cli.command_handlers.monitor_commands import handle_grounding_export_command

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(world.db.db_path))
    monkeypatch.setattr("empirica.core.transaction_export.own_ai_id", lambda: own)
    fields = {
        "ai_id": "a",
        "output": "json",
        "transactions": True,
        "since": None,
        "limit": None,
        "content": False,
        **kw,
    }
    args = types.SimpleNamespace(**fields)
    rc = handle_grounding_export_command(args)
    return rc, json.loads(capsys.readouterr().out)


def test_the_flag_runs_for_this_practices_own_store(world, monkeypatch, capsys):
    _tx(world, "t1", NOW - 1000)
    world.db.close()

    rc, out = _handler(world, monkeypatch, capsys, content=True)

    assert rc == 0 and out["content_scope"] == "tenant"


def test_the_flag_is_refused_for_any_other_practice(world, monkeypatch, capsys):
    _tx(world, "t1", NOW - 1000)
    world.db.close()

    rc, out = _handler(world, monkeypatch, capsys, own="somebody-else", content=True)

    assert rc == 1 and out["ok"] is False and "own" in out["error"] and "--content" in out["error"]


def test_the_flag_without_transactions_is_an_error_not_a_silent_no_op(world, monkeypatch, capsys):
    from empirica.cli.command_handlers.monitor_commands import handle_grounding_export_command

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(world.db.db_path))
    args = types.SimpleNamespace(ai_id="a", output="json", transactions=False, since=None, limit=None, content=True)

    assert handle_grounding_export_command(args) == 1
    assert "--content" in json.loads(capsys.readouterr().out)["error"]


def test_the_parser_has_the_flag():
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "grounding-export" in a.choices)
    action = next(a for a in sub.choices["grounding-export"]._actions if a.dest == "content")

    assert action.default is False and "tenant" in action.help and "do_not_share" in action.help
