"""`delete-artifacts` can remove the phantom CHECK rows the old auto-checkpoint wrote.

Before 46aec0d66, check-submit's auto-checkpoint wrote a second CHECK-phase reflex row with every vector 0.5, no
reasoning and `reflex_data.auto_checkpoint` true. They live only in SQLite `reflexes` (the git notes and epistemic
snapshots hold the real vectors), and nothing deleted reflex rows; David ruled out raw SQL and chose to extend
delete-artifacts rather than add a verb (ecodex, prop_oq2bd32ci5efrfovccocism63u: 43 rows in 11 practices).

The section is `{"reflexes": {"phantom_checks": true}}`. It previews unless --apply, matches the signature and only
the signature, and acts on the invoking practice's store.
"""

from __future__ import annotations

import json
import sqlite3
import types

import pytest

from empirica.cli.command_handlers.graph_commands import handle_delete_artifacts_command
from empirica.data.session_database import SessionDatabase

REAL = {"know": 0.8, "uncertainty": 0.6, "context": 0.75, "engagement": 0.9}


@pytest.fixture
def store(tmp_path, monkeypatch):
    dbp = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(dbp))
    db = SessionDatabase(db_path=str(dbp))
    sid = db.create_session(ai_id="a")
    ids = {
        "real_check": db.store_vectors(sid, "CHECK", REAL, reasoning="the real CHECK", transaction_id="tx1"),
        "phantom": db.store_vectors(sid, "CHECK", {}, metadata={"auto_checkpoint": True, "reason": "risky"}),
        "phantom_2": db.store_vectors(sid, "CHECK", {}, metadata={"auto_checkpoint": True}),
        "manual_checkpoint": db.store_vectors(
            sid, "CHECK", REAL, metadata={"auto_checkpoint": False, "note": "by hand"}
        ),
        "auto_with_reasoning": db.store_vectors(
            sid, "CHECK", REAL, metadata={"auto_checkpoint": True}, reasoning="someone wrote a reason"
        ),
        "auto_preflight": db.store_vectors(sid, "PREFLIGHT", {}, metadata={"auto_checkpoint": True}),
        "plain_check_no_reasoning": db.store_vectors(sid, "CHECK", REAL),
        "not_json": db.store_vectors(sid, "CHECK", {}, metadata={"auto_checkpoint": True}),
    }
    db.conn.execute("UPDATE reflexes SET reflex_data = 'not json {' WHERE id = ?", (ids["not_json"],))
    db.conn.commit()
    db.close()
    return dbp, sid, ids


def _surviving(dbp) -> set[int]:
    con = sqlite3.connect(dbp)
    try:
        return {r[0] for r in con.execute("SELECT id FROM reflexes")}
    finally:
        con.close()


def _run(tmp_path, capsys, payload: dict, *, apply: bool = False) -> dict:
    cfg = tmp_path / "payload.json"
    cfg.write_text(json.dumps(payload))
    rc = handle_delete_artifacts_command(types.SimpleNamespace(config=str(cfg), apply=apply, schema=False))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    return out


def test_it_previews_by_default_and_names_each_row(store, tmp_path, capsys):
    dbp, sid, ids = store
    before = _surviving(dbp)

    out = _run(tmp_path, capsys, {"reflexes": {"phantom_checks": True}})

    assert out["dry_run"] is True and _surviving(dbp) == before, "nothing may be deleted without --apply"
    would = [i for i in out["items"] if i.get("type") == "reflex"]
    assert {i["id"] for i in would} == {ids["phantom"], ids["phantom_2"]}
    assert all(i["action"] == "would_delete" and i["session_id"] == sid and i["at"] for i in would)


def test_apply_deletes_exactly_the_phantoms(store, tmp_path, capsys):
    dbp, _, ids = store

    out = _run(tmp_path, capsys, {"reflexes": {"phantom_checks": True}}, apply=True)

    gone = {ids["phantom"], ids["phantom_2"]}
    assert _surviving(dbp) == set(ids.values()) - gone
    assert out["reflexes_removed"] == 2 and out["dry_run"] is False


@pytest.mark.parametrize(
    "keep",
    [
        "real_check",
        "manual_checkpoint",
        "auto_with_reasoning",
        "auto_preflight",
        "plain_check_no_reasoning",
        "not_json",
    ],
)
def test_every_neighbouring_row_survives_an_apply(store, tmp_path, capsys, keep):
    """Each is one condition short of the signature: a real CHECK, a manual checkpoint, one with reasoning, another
    phase, a CHECK with no reasoning but no auto flag, and a row whose reflex_data is not JSON at all."""
    dbp, _, ids = store

    _run(tmp_path, capsys, {"reflexes": {"phantom_checks": True}}, apply=True)

    assert ids[keep] in _surviving(dbp)


def test_a_row_with_unparseable_reflex_data_does_not_abort_the_query(store, tmp_path, capsys):
    """json_extract on malformed JSON raises; one bad row must not make the whole purge fail."""
    out = _run(tmp_path, capsys, {"reflexes": {"phantom_checks": True}})

    assert out["errors"] == [] and out["ok"] is True


def test_a_second_apply_finds_nothing(store, tmp_path, capsys):
    _run(tmp_path, capsys, {"reflexes": {"phantom_checks": True}}, apply=True)

    out = _run(tmp_path, capsys, {"reflexes": {"phantom_checks": True}}, apply=True)

    assert out["reflexes_removed"] == 0


def test_the_flag_off_does_nothing(store, tmp_path, capsys):
    dbp, _, _ = store
    before = _surviving(dbp)

    out = _run(tmp_path, capsys, {"reflexes": {"phantom_checks": False}}, apply=True)

    assert _surviving(dbp) == before and out["reflexes_removed"] == 0


def test_an_unknown_key_inside_reflexes_is_refused_not_ignored(store, tmp_path, capsys):
    """Same rule as the top level: a silently dropped switch reads exactly like a successful preview."""
    dbp, _, _ = store
    before = _surviving(dbp)
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({"reflexes": {"phantom_check": True}}))  # singular: a typo

    rc = handle_delete_artifacts_command(types.SimpleNamespace(config=str(cfg), apply=True, schema=False))

    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and "phantom_check" in json.dumps(out)
    assert _surviving(dbp) == before


def test_a_payload_with_nothing_to_do_is_still_refused_as_before(store, tmp_path, capsys):
    dbp, _, _ = store
    before = _surviving(dbp)
    cfg = tmp_path / "empty.json"
    cfg.write_text(json.dumps({"deletions": [], "reason": "nothing"}))

    rc = handle_delete_artifacts_command(types.SimpleNamespace(config=str(cfg), apply=True, schema=False))

    assert rc == 1 and json.loads(capsys.readouterr().out)["ok"] is False
    assert _surviving(dbp) == before


def test_the_schema_documents_the_section(capsys):
    handle_delete_artifacts_command(types.SimpleNamespace(config=None, apply=False, schema=True))

    assert "phantom_checks" in capsys.readouterr().out
