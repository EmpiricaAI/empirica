"""One answer to "is this transaction open": the reflexes table.

Three surfaces disagreed about one transaction (goal 6656f101): the
SessionStart post-compact block said ACTIVE, the Sentinel said closed, and
PREFLIGHT warned it had been unclosed for 47 days. Two of them were reading a
pointer file that a 48-day-old pre-compact snapshot kept overwriting; the
POSTFLIGHT row had been in the table since July. These tests pin the
predicate and the two readers that now consult it before trusting a cache.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

from empirica.data.session_database import SessionDatabase
from empirica.utils.session_resolver import transaction_open_in_db

HOOKS = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(p))
    db = SessionDatabase(db_path=str(p))
    cur = db.conn.cursor()
    cur.execute(
        "INSERT INTO sessions (session_id, ai_id, project_id, start_time, components_loaded) VALUES ('s1','ai','p1',?, '[]')",
        (time.time(),),
    )
    db.conn.commit()
    db.close()
    return str(p)


def _add(db_path, tx, phase, ts):
    db = SessionDatabase(db_path=db_path)
    db.conn.execute(
        "INSERT INTO reflexes (session_id, phase, timestamp, transaction_id, reflex_data) VALUES ('s1', ?, ?, ?, '{}')",
        (phase, ts, tx),
    )
    db.conn.commit()
    db.close()


# ─── the predicate ────────────────────────────────────────────────────────


def test_preflight_without_postflight_is_open(db_path):
    _add(db_path, "tx-a", "PREFLIGHT", 1000.0)
    _add(db_path, "tx-a", "CHECK", 1010.0)
    assert transaction_open_in_db("tx-a", db_path) is True


def test_postflight_after_preflight_is_closed(db_path):
    _add(db_path, "tx-a", "PREFLIGHT", 1000.0)
    _add(db_path, "tx-a", "POSTFLIGHT", 1500.0)
    assert transaction_open_in_db("tx-a", db_path) is False


def test_the_47e5a91c_shape_is_closed(db_path):
    """PREFLIGHT, CHECK, POSTFLIGHT — then a CHECK three weeks later, written by
    a post-compact that restored the stale snapshot. Still closed."""
    _add(db_path, "tx-a", "PREFLIGHT", 1785584701.0)
    _add(db_path, "tx-a", "CHECK", 1785584803.0)
    _add(db_path, "tx-a", "POSTFLIGHT", 1785591019.0)
    _add(db_path, "tx-a", "CHECK", 1787493527.0)
    assert transaction_open_in_db("tx-a", db_path) is False


def test_unknown_transaction_is_not_open(db_path):
    assert transaction_open_in_db("never-seen", db_path) is False


def test_no_id_or_unreadable_db_is_none(tmp_path):
    assert transaction_open_in_db(None, str(tmp_path / "x.db")) is None
    assert transaction_open_in_db("tx-a", str(tmp_path / "not-a-dir" / "x.db")) is None


# ─── preflight's unclosed warning consults the table ──────────────────────


def test_preflight_does_not_warn_about_a_pointer_the_table_says_is_closed(db_path, monkeypatch):
    from empirica.cli.command_handlers import _workflow_preflight as wp

    _add(db_path, "tx-old", "PREFLIGHT", 1000.0)
    _add(db_path, "tx-old", "POSTFLIGHT", 2000.0)
    monkeypatch.setattr(
        wp.R,
        "transaction_read",
        staticmethod(lambda *a, **k: {"transaction_id": "tx-old", "status": "open", "preflight_timestamp": 1000.0}),
    )
    assert wp._preflight_check_unclosed_transaction() is None


def test_preflight_still_warns_when_the_table_agrees_it_is_open(db_path, monkeypatch):
    from empirica.cli.command_handlers import _workflow_preflight as wp

    _add(db_path, "tx-open", "PREFLIGHT", time.time() - 600)
    monkeypatch.setattr(
        wp.R,
        "transaction_read",
        staticmethod(
            lambda *a, **k: {"transaction_id": "tx-open", "status": "open", "preflight_timestamp": time.time() - 600}
        ),
    )
    warning = wp._preflight_check_unclosed_transaction()
    assert warning is not None
    assert str(warning["previous_transaction_id"]).startswith("tx-open")
    assert 9 <= warning["age_minutes"] <= 11


def test_preflight_still_warns_when_the_table_cannot_answer(monkeypatch, tmp_path):
    """None from the predicate is not 'closed' — the warning stays, as before."""
    from empirica.cli.command_handlers import _workflow_preflight as wp

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "missing" / "x.db"))
    monkeypatch.setattr(
        wp.R,
        "transaction_read",
        staticmethod(lambda *a, **k: {"transaction_id": "tx-?", "status": "open", "preflight_timestamp": 1.0}),
    )
    assert wp._preflight_check_unclosed_transaction() is not None


# ─── post-compact refuses a closed transaction and reports a stale snapshot ─


@pytest.fixture
def post_compact():
    spec = importlib.util.spec_from_file_location("post_compact_test", HOOKS / "post-compact.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


NOW = 1789727806.0  # 2026-09-18T12:36 local on the box that measured this


def _snap(ts: str, tx_id="47e5a91c-b46", status="open"):
    return {
        "timestamp": ts,
        "active_transaction": {"transaction_id": tx_id, "status": status, "preflight_timestamp": 1785584701.0},
    }


def test_closed_in_db_is_not_restored_and_says_so(post_compact):
    snap = _snap("2026-09-18T12-30-00")
    tx, note = post_compact._validate_snapshot_transaction(
        snap, snap["active_transaction"], is_open_in_db=lambda _id: False, now=NOW
    )
    assert tx is None
    assert "CLOSED in the reflexes table" in note
    assert "NOT restored" in note


def test_open_in_db_and_fresh_snapshot_is_restored_silently(post_compact):
    snap = _snap("2026-09-18T12-30-00")
    tx, note = post_compact._validate_snapshot_transaction(
        snap, snap["active_transaction"], is_open_in_db=lambda _id: True, now=NOW
    )
    assert tx == snap["active_transaction"]
    assert note is None


def test_unanswerable_db_keeps_the_snapshot_verdict(post_compact):
    snap = _snap("2026-09-18T12-30-00")
    tx, note = post_compact._validate_snapshot_transaction(
        snap, snap["active_transaction"], is_open_in_db=lambda _id: None, now=NOW
    )
    assert tx == snap["active_transaction"]
    assert note is None


def test_a_48_day_old_snapshot_is_named_as_stale(post_compact):
    """The specimen: newest snapshot 2026-08-01, compaction 2026-09-18. The
    note must say pre-compact wrote nothing, and the closed transaction must
    not come back."""
    snap = _snap("2026-08-01T14-58-43")
    tx, note = post_compact._validate_snapshot_transaction(
        snap, snap["active_transaction"], is_open_in_db=lambda _id: False, now=NOW
    )
    assert tx is None
    assert "day(s) old" in note
    assert "pre-compact.py exited before its snapshot" in note
    assert "NOT restored" in note


def test_no_snapshot_is_quiet(post_compact):
    assert post_compact._validate_snapshot_transaction(None, None, is_open_in_db=lambda _id: None, now=NOW) == (
        None,
        None,
    )


# ─── pre-compact leaves a trace on every exit ─────────────────────────────


def test_pre_compact_trace_appends_one_json_line(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    spec = importlib.util.spec_from_file_location("pre_compact_test", HOOKS / "pre-compact.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    mod._trace("no_empirica_session", trigger="auto", claude_session_id="cc-1")
    mod._trace("snapshot_written", path="/x/pre_summary_1.json")

    lines = (home / ".empirica" / "precompact.log").read_text().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(ln) for ln in lines)
    assert first["outcome"] == "no_empirica_session" and first["claude_session_id"] == "cc-1"
    assert second["outcome"] == "snapshot_written" and second["path"].endswith("pre_summary_1.json")
    assert "ts" in first and "cwd" in first


# ─── pre-compact reads the transcript tail, not the file ──────────────────


@pytest.fixture
def pre_compact(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    spec = importlib.util.spec_from_file_location("pre_compact_tail_test", HOOKS / "pre-compact.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _user(text):
    return json.dumps({"type": "user", "message": {"content": text}})


def _tool_result():
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 50_000}]}})


def test_last_task_comes_from_the_tail_of_a_large_transcript(pre_compact, tmp_path):
    """A transcript far larger than the tail window: the answer is still the
    last human message, and only the tail is read. The whole-file read this
    replaces cost 27.9 s on a 1.94 GB transcript and was killed by the harness
    before the snapshot write, every compaction, for 48 days."""
    path = tmp_path / "t.jsonl"
    filler = [_user(f"old task {i}") for i in range(200)] + [_tool_result() for _ in range(200)]
    body = "\n".join(filler) + "\n" + _user("the real last task") + "\n" + _tool_result() + "\n"
    path.write_text(body)
    assert path.stat().st_size > pre_compact.TRANSCRIPT_TAIL_BYTES

    reads: list[int] = []
    real_open = open

    def counting_open(p, mode="r", *a, **k):
        f = real_open(p, mode, *a, **k)
        if "b" in mode:
            orig = f.read

            def read(n=-1):
                data = orig(n)
                reads.append(len(data))
                return data

            f.read = read
        return f

    import builtins

    original = builtins.open
    builtins.open = counting_open
    try:
        out = pre_compact._extract_last_task(str(path))
    finally:
        builtins.open = original

    assert out == "the real last task"
    assert sum(reads) <= pre_compact.TRANSCRIPT_TAIL_BYTES


def test_small_transcript_is_read_whole(pre_compact, tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(_user("first") + "\n" + _user("second") + "\n")
    assert pre_compact._extract_last_task(str(path)) == "second"


def test_cut_first_line_of_the_tail_is_dropped_not_parsed(pre_compact, tmp_path, monkeypatch):
    monkeypatch.setattr(pre_compact, "TRANSCRIPT_TAIL_BYTES", 80)
    path = tmp_path / "t.jsonl"
    path.write_text(_user("a" * 100) + "\n" + _user("tail task") + "\n")
    lines = pre_compact._read_transcript_tail(str(path), tail_bytes=80)
    assert all(json.loads(ln) for ln in lines)  # every returned line is a complete record
    assert pre_compact._extract_last_task(str(path)) == "tail task"


# ─── post-compact's CRITICAL writers report a failed write ────────────────


def test_failed_pointer_write_is_reported_on_stderr(post_compact, tmp_path, monkeypatch, capsys):
    """Both writers' docstrings say CRITICAL and every caller discards the
    return; a failed write used to leave the new conversation without its
    pointers and nothing saying so."""
    monkeypatch.setattr(post_compact, "_get_instance_suffix", lambda: "_tmux_1")
    ro = tmp_path / "proj"
    ro.mkdir()
    (ro / ".empirica").write_text("not a directory")  # the mkdir/open below must fail
    ok = post_compact._write_active_transaction_for_new_conversation(
        {"transaction_id": "tx", "status": "open"}, str(ro), "inst", "cc"
    )
    assert ok is False
    assert "could NOT write the transaction pointer" in capsys.readouterr().err


def test_failed_active_work_write_is_reported_on_stderr(post_compact, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".empirica").write_text("not a directory")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    ok = post_compact._write_active_work_for_new_conversation("cc-1", str(tmp_path), "sess", "inst")
    assert ok is False
    assert "could NOT write active_work" in capsys.readouterr().err
