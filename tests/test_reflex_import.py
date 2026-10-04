"""`rebuild --reflexes-only`: restore reflex rows from the session-phase git notes, idempotently.

ecodex-lab (prop_toajlquc4jag5gun7ygb4646ee, routed by cowork prop_cdouoslqtvdxzatjdwtzgnftoe) lost its reflex rows and found
there was no way back: `rebuild --from-notes` restores artifacts but no reflexes, and `store_vectors` stamps the current time and
fills every missing vector with 0.5. The importer keeps what the note says: the original timestamp, only the vectors present,
transaction id, reasoning and meta, and skips an identity (session_id, phase, round) that already has a row.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import types
from datetime import datetime, timezone

import pytest

from empirica.core.canonical.reflex_import import import_reflexes
from empirica.data.session_database import SessionDatabase

V13 = {
    "engagement": 0.9, "know": 0.8, "do": 0.7, "context": 0.6, "clarity": 0.5, "coherence": 0.4, "signal": 0.3,
    "density": 0.2, "state": 0.1, "change": 0.15, "completion": 0.25, "impact": 0.35, "uncertainty": 0.45,
}  # fmt: skip
V7 = {k: V13[k] for k in ("know", "uncertainty", "context", "clarity", "coherence", "signal", "do")}
STAMP = "2026-10-03T16:52:03.822486+00:00"
EPOCH = datetime(2026, 10, 3, 16, 52, 3, 822486, tzinfo=timezone.utc).timestamp()


def _git(repo, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="a", project_id=pid)
    return types.SimpleNamespace(repo=repo, db=db, pid=pid, sid=sid, tmp=tmp_path)


def _note(
    world,
    phase,
    rnd,
    *,
    vectors=None,
    meta=None,
    session_id=None,
    stamp=STAMP,
    body=None,
    round_in_body=None,
    commit="HEAD",
):
    sid = session_id or world.sid
    payload = body or {
        "session_id": sid,
        "phase": phase,
        "round": round_in_body if round_in_body is not None else rnd,
        "timestamp": stamp,
        "vectors": V13 if vectors is None else vectors,
        "meta": meta if meta is not None else {"transaction_id": f"tx-{rnd}", "reasoning": f"why {phase} {rnd}"},
        "epistemic_tags": {},
    }
    text = payload if isinstance(payload, str) else json.dumps(payload)
    _git(world.repo, "notes", f"--ref=empirica/session/{sid}/{phase}/{rnd}", "add", "-f", "-m", text, commit)


def _rows(world):
    world.db.conn.row_factory = sqlite3.Row
    return [dict(r) for r in world.db.conn.execute("SELECT * FROM reflexes ORDER BY phase, round")]


def _run(world, apply=False):
    return import_reflexes(world.db.conn, str(world.repo), apply=apply)


def test_a_preview_reports_what_it_would_import_and_writes_nothing(world):
    _note(world, "PREFLIGHT", 1)
    _note(world, "POSTFLIGHT", 1)

    out = _run(world)

    assert out["ok"] and out["applied"] is False and out["importable"] == 2 and out["imported"] == 0
    assert _rows(world) == []


def test_apply_restores_the_original_timestamp_transaction_reasoning_and_vectors(world):
    _note(world, "PREFLIGHT", 1, meta={"transaction_id": "tx-A", "reasoning": "the why", "task_context": "ctx"})

    out = _run(world, apply=True)
    (row,) = _rows(world)

    assert out["imported"] == 1 and out["by_phase"] == {"PREFLIGHT": 1}
    assert row["timestamp"] == pytest.approx(EPOCH)
    assert row["transaction_id"] == "tx-A" and row["reasoning"] == "the why" and row["project_id"] == world.pid
    assert {k: row[k] for k in V13} == V13
    data = json.loads(row["reflex_data"])
    assert (
        data["task_context"] == "ctx"
        and data["vectors"] == V13
        and data["round"] == 1
        and data["transaction_id"] == "tx-A"
    )


def test_a_vector_the_note_does_not_carry_stays_null_not_half(world):
    """A CHECK note often has 7 of 13. store_vectors would write 0.5 for the rest; that is an invented measurement."""
    _note(world, "CHECK", 1, vectors=V7, meta={"transaction_id": "tx-1", "decision": "proceed", "confidence": 0.7})

    _run(world, apply=True)
    (row,) = _rows(world)

    assert row["know"] == V7["know"]
    assert [row[k] for k in V13 if k not in V7] == [None] * 6
    assert json.loads(row["reflex_data"])["vectors"] == V7 and json.loads(row["reflex_data"])["decision"] == "proceed"


def test_the_hazard_is_real_store_vectors_fills_missing_vectors_with_half(world):
    """Control for the test above: the other route does invent values, so the NULLs are a choice and not an accident."""
    world.db.store_vectors(world.sid, "CHECK", V7, transaction_id="x")
    world.db.conn.row_factory = sqlite3.Row

    row = dict(world.db.conn.execute("SELECT * FROM reflexes").fetchone())

    assert row["impact"] == 0.5 and row["engagement"] == 0.5


def test_a_second_apply_imports_nothing(world):
    _note(world, "PREFLIGHT", 1)
    _note(world, "POSTFLIGHT", 1)
    _run(world, apply=True)

    again = _run(world, apply=True)

    assert again["imported"] == 0 and again["importable"] == 0 and again["already_present"] == 2
    assert len(_rows(world)) == 2


def test_an_identity_that_already_has_a_row_is_left_untouched(world):
    world.db.store_vectors(
        world.sid, "PREFLIGHT", {"know": 0.11}, round_num=1, reasoning="existing", transaction_id="live"
    )
    _note(world, "PREFLIGHT", 1, meta={"transaction_id": "from-note", "reasoning": "note"})

    out = _run(world, apply=True)
    (row,) = _rows(world)

    assert out["already_present"] == 1 and out["imported"] == 0
    assert row["reasoning"] == "existing" and row["transaction_id"] == "live" and row["know"] == 0.11


def test_a_session_with_no_row_is_skipped_and_named_not_imported(world):
    _note(world, "PREFLIGHT", 1)
    _note(world, "PREFLIGHT", 1, session_id="orphan-session")

    out = _run(world, apply=True)

    assert out["imported"] == 1 and out["skipped_no_session"] == 1 and out["sessions_missing"] == ["orphan-session"]
    assert {r["session_id"] for r in _rows(world)} == {world.sid}


def test_a_note_that_disagrees_with_its_ref_name_is_not_imported(world):
    _note(world, "PREFLIGHT", 1, round_in_body=7)

    out = _run(world, apply=True)

    assert out["unreadable_notes"] == 1 and out["imported"] == 0 and _rows(world) == []


def test_an_unreadable_note_costs_that_note_only(world):
    _note(world, "PREFLIGHT", 1, body="this is not json {")
    _note(world, "POSTFLIGHT", 1)

    out = _run(world, apply=True)

    assert (
        out["unreadable_notes"] == 1 and out["imported"] == 1 and [r["phase"] for r in _rows(world)] == ["POSTFLIGHT"]
    )


def test_a_note_with_no_usable_timestamp_is_not_imported_with_the_current_time(world):
    _note(world, "PREFLIGHT", 1, stamp="yesterday")

    assert _run(world, apply=True)["unreadable_notes"] == 1 and _rows(world) == []


def test_a_z_suffixed_and_a_numeric_timestamp_both_parse(world):
    _note(world, "PREFLIGHT", 1, stamp="2026-10-03T16:52:03Z")
    _note(world, "PREFLIGHT", 2, stamp=1791000000.5)

    _run(world, apply=True)
    stamps = sorted(r["timestamp"] for r in _rows(world))

    assert stamps[0] == pytest.approx(1791000000.5) or stamps[1] == pytest.approx(1791000000.5)
    assert len(stamps) == 2


def test_the_apply_is_all_or_nothing(world):
    _note(world, "PREFLIGHT", 1)
    _note(world, "PREFLIGHT", 2)
    world.db.conn.execute(
        "CREATE TRIGGER stop BEFORE INSERT ON reflexes WHEN NEW.round = 2 BEGIN SELECT RAISE(ABORT, 'stop'); END"
    )
    world.db.conn.commit()

    with pytest.raises(sqlite3.DatabaseError):
        _run(world, apply=True)

    assert _rows(world) == [], "the first row must not survive the second one failing"


def test_refs_that_are_not_session_phase_notes_are_counted_not_guessed(world):
    _git(world.repo, "notes", "--ref=empirica/session/sid/BOGUS/1", "add", "-f", "-m", "{}", "HEAD")
    _note(world, "PREFLIGHT", 1)

    out = _run(world)

    assert out["unparsed_refs"] == 1 and out["importable"] == 1


# ── wiring ──────────────────────────────────────────────────────────────────


def _handler(world, monkeypatch, capsys, **kw):
    from empirica.cli.command_handlers import sync_commands

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(world.tmp / "sessions.db"))
    monkeypatch.setattr("empirica.config.path_resolver.get_git_root", lambda: world.repo)
    monkeypatch.setattr(sync_commands, "_rebuild_from_notes", lambda: pytest.fail("the full rebuild must not run"))
    world.db.close()
    args = types.SimpleNamespace(
        output="json", from_notes=True, qdrant=False, qdrant_only=False, reflexes_only=True, apply=False, **kw
    )
    rc = sync_commands.handle_rebuild_command(args)
    return rc, json.loads(capsys.readouterr().out), args


def test_the_flag_previews_by_default_and_never_runs_the_full_rebuild(world, monkeypatch, capsys):
    _note(world, "PREFLIGHT", 1)

    rc, out, _ = _handler(world, monkeypatch, capsys)

    assert rc == 0 and out["ok"] and out["reflexes"]["applied"] is False and out["reflexes"]["importable"] == 1


def test_the_flag_applies_only_with_apply(world, monkeypatch, capsys):
    from empirica.cli.command_handlers import sync_commands

    _note(world, "PREFLIGHT", 1)
    _, _, args = _handler(world, monkeypatch, capsys)
    args.apply = True

    assert sync_commands.handle_rebuild_command(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reflexes"]["imported"] == 1


def test_the_flags_are_on_the_parser():
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "rebuild" in a.choices)
    dests = {a.dest for a in sub.choices["rebuild"]._actions}

    assert {"reflexes_only", "apply"} <= dests


def test_a_note_with_no_vectors_is_not_restored_as_a_row_of_nulls(world):
    _note(world, "CHECK", 1, vectors={}, meta={"transaction_id": "t"})

    out = _run(world, apply=True)

    assert out["skipped_no_vectors"] == 1 and out["imported"] == 0 and _rows(world) == []


def test_the_phantom_auto_checkpoint_check_is_not_resurrected(world):
    """delete-artifacts purged these rows on purpose; the notes outlive the purge, so the importer must not undo it."""
    _note(world, "CHECK", 1, vectors=dict.fromkeys(V7, 0.5), meta={"transaction_id": "t", "auto_checkpoint": True})

    out = _run(world, apply=True)

    assert out["skipped_phantom"] == 1 and out["imported"] == 0 and _rows(world) == []


def test_an_auto_checkpoint_note_with_real_vectors_is_still_restored(world):
    """Control: the phantom skip is the signature, not the flag alone."""
    _note(world, "CHECK", 1, vectors=V7, meta={"transaction_id": "t", "auto_checkpoint": True})

    assert _run(world, apply=True)["imported"] == 1


def test_a_ref_that_holds_notes_for_two_transactions_restores_both(world):
    """The writer reuses <PHASE>/<round> across transactions; reading only the first note lost the others."""
    _note(world, "CHECK", 1, meta={"transaction_id": "tx-A", "decision": "proceed"})
    _git(world.repo, "commit", "-q", "--allow-empty", "-m", "second")
    _note(world, "CHECK", 1, meta={"transaction_id": "tx-B", "decision": "investigate"})

    out = _run(world, apply=True)

    assert out["imported"] == 2 and out["multi_note_refs"] == 1
    assert sorted(r["transaction_id"] for r in _rows(world)) == ["tx-A", "tx-B"]


def test_an_unreadable_note_beside_a_good_one_in_the_same_ref_costs_only_itself(world):
    _note(world, "CHECK", 1, meta={"transaction_id": "tx-A"})
    _git(world.repo, "commit", "-q", "--allow-empty", "-m", "second")
    _note(world, "CHECK", 1, body="not json {")

    out = _run(world, apply=True)

    assert out["imported"] == 1 and out["unreadable_notes"] == 1


def test_apply_without_reflexes_only_is_refused_not_ignored(world, monkeypatch, capsys):
    from empirica.cli.command_handlers import sync_commands

    monkeypatch.setattr(sync_commands, "_rebuild_from_notes", lambda: pytest.fail("the default rebuild must not run"))
    args = types.SimpleNamespace(
        output="json", from_notes=True, qdrant=False, qdrant_only=False, reflexes_only=False, apply=True
    )

    assert sync_commands.handle_rebuild_command(args) == 1
    assert "--apply only applies with --reflexes-only" in json.loads(capsys.readouterr().out)["error"]
