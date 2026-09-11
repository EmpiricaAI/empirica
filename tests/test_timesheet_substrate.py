"""The four timesheet-substrate wirings: columns that exist get written, keys that
are accepted get stored, and attachment holds across session boundaries.

empirica-autonomy measured four gaps (prop_764q2pdzgzfo5hzn4v6xxopywm), each the
same shape: schema or interface reserved space for a value the write path never
delivered, and nothing rendered the absence visible.

A. ``cascades.completed_at`` / ``duration_ms``: 888 of 888 NULL. PREFLIGHT
   inserts the row, POSTFLIGHT read its id and closed nothing.
B. ``work_type``: validated at PREFLIGHT, used to weight calibration, stored
   nowhere — migration 069 adds the column, the PREFLIGHT insert now writes it.
C. Goal auto-attach: 53% of findings logged inside a transaction carried no
   goal_id. The resolver scoped to ``session_id`` only, and a goal created in a
   PRIOR session — the normal shape for multi-session work — never matched.
D. ``engagement_id``: goals-create accepted it and it was populated 0 of 186,
   because the only path was a separate command after work started. PREFLIGHT
   now accepts it and the window's goals inherit it.

FIXTURES: built via ``tests.schema_shapes`` from the REAL schema + the REAL
migration registry — never hand-rolled.
"""

from __future__ import annotations

import json
import sqlite3
import time
import typing

import pytest

from empirica.cli.command_handlers._workflow_postflight import (
    _cascade_elapsed_ms,
    _postflight_close_cascade_row,
)
from empirica.cli.command_handlers.artifact_log_commands import _resolve_goal_for_artifact
from empirica.cli.command_handlers.goal_commands import _inherit_engagement_from_transaction
from empirica.cli.validation import PreflightInput, safe_validate
from tests.schema_shapes import build_db


@pytest.fixture
def db_path(tmp_path):
    return build_db(tmp_path / "sessions.db", "current")


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    yield c
    c.close()


class _DB:
    """Duck-typed SessionDatabase carrying just .conn, as the closers use it."""

    def __init__(self, conn):
        self.conn = conn


def _insert_cascade(conn, cascade_id, session_id, started_at, completed_at=None):
    conn.execute(
        "INSERT INTO cascades (cascade_id, session_id, task, started_at, completed_at) VALUES (?, ?, ?, ?, ?)",
        (cascade_id, session_id, "PREFLIGHT assessment", started_at, completed_at),
    )
    conn.commit()


def _insert_goal(conn, goal_id, session_id, status="in_progress", **cols):
    fields = {
        "id": goal_id,
        "session_id": session_id,
        "objective": f"goal {goal_id}",
        "scope": "{}",
        "created_timestamp": cols.pop("created_timestamp", time.time()),
        "goal_data": "{}",
        "status": status,
        "is_completed": cols.pop("is_completed", 0),
    }
    fields.update(cols)
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    conn.execute(f"INSERT INTO goals ({names}) VALUES ({marks})", tuple(fields.values()))
    conn.commit()


# ─── Item A: POSTFLIGHT closes the cascade row ───────────────────────────────


class TestCascadeClose:
    def test_open_cascade_gets_completed_at_and_duration(self, conn, monkeypatch):
        started = time.time() - 90.0
        _insert_cascade(conn, "c1", "s1", started)
        monkeypatch.setattr(
            "empirica.cli.command_handlers._workflow_postflight._get_db_for_session",
            lambda _sid: _DB(conn),
        )

        closed = _postflight_close_cascade_row("s1")

        assert closed == 1
        row = conn.execute(
            "SELECT completed_at, duration_ms, postflight_completed FROM cascades WHERE cascade_id = 'c1'"
        ).fetchone()
        assert row[0] is not None
        # ~90s elapsed; wide tolerance, but NULL or 0 would both fail
        assert 80_000 < row[1] < 200_000
        assert row[2] == 1

    def test_other_sessions_and_already_closed_rows_untouched(self, conn, monkeypatch):
        _insert_cascade(conn, "mine-open", "s1", time.time() - 5)
        _insert_cascade(conn, "theirs-open", "s2", time.time() - 5)
        _insert_cascade(conn, "mine-closed", "s1", time.time() - 500, completed_at=123.0)
        monkeypatch.setattr(
            "empirica.cli.command_handlers._workflow_postflight._get_db_for_session",
            lambda _sid: _DB(conn),
        )

        closed = _postflight_close_cascade_row("s1")

        assert closed == 1
        assert conn.execute("SELECT completed_at FROM cascades WHERE cascade_id = 'theirs-open'").fetchone()[0] is None
        assert conn.execute("SELECT completed_at FROM cascades WHERE cascade_id = 'mine-closed'").fetchone()[0] == 123.0

    def test_orphaned_open_rows_are_swept_together(self, conn, monkeypatch):
        """A row left open by a died-mid-flight POSTFLIGHT closes on the NEXT one."""
        _insert_cascade(conn, "orphan", "s1", time.time() - 3600)
        _insert_cascade(conn, "current", "s1", time.time() - 10)
        monkeypatch.setattr(
            "empirica.cli.command_handlers._workflow_postflight._get_db_for_session",
            lambda _sid: _DB(conn),
        )

        assert _postflight_close_cascade_row("s1") == 2
        open_left = conn.execute(
            "SELECT COUNT(*) FROM cascades WHERE session_id = 's1' AND completed_at IS NULL"
        ).fetchone()[0]
        assert open_left == 0

    def test_elapsed_accepts_both_started_at_formats(self):
        """PREFLIGHT writes epoch floats; CascadeRepository writes ISO strings.

        Both live in the same column, so the duration computation must accept
        both — and say None, not raise, for garbage."""
        now = time.time()
        assert _cascade_elapsed_ms(now - 1.5, now) == 1500

        from datetime import datetime, timezone

        iso = datetime.fromtimestamp(now - 2.0, tz=timezone.utc).isoformat()
        iso_ms = _cascade_elapsed_ms(iso, now)
        assert iso_ms is not None and 1900 <= iso_ms <= 2100

        assert _cascade_elapsed_ms("not-a-time", now) is None
        assert _cascade_elapsed_ms(None, now) is None


# ─── Item B: work_type lands on the cascade row ──────────────────────────────


class TestWorkTypeColumn:
    def test_migration_069_provides_the_column(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cascades)")}
        assert "work_type" in cols

    def test_base_schema_lacks_it_so_the_migration_is_load_bearing(self, tmp_path):
        base = build_db(tmp_path / "base.db", "base")
        c = sqlite3.connect(base)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(cascades)")}
        finally:
            c.close()
        assert "work_type" not in cols

    def test_column_accepts_the_preflight_shaped_insert(self, conn):
        """The exact INSERT the PREFLIGHT handler now runs, values included."""
        conn.execute(
            "INSERT INTO cascades (cascade_id, session_id, task, started_at, work_type) VALUES (?, ?, ?, ?, ?)",
            ("c-wt", "s1", "PREFLIGHT assessment", time.time(), "code"),
        )
        assert conn.execute("SELECT work_type FROM cascades WHERE cascade_id = 'c-wt'").fetchone()[0] == "code"


# ─── Item C: goal auto-attach across session boundaries ──────────────────────


class TestGoalAutoAttach:
    def test_explicit_goal_id_always_wins(self, conn):
        assert _resolve_goal_for_artifact("explicit", "s1", _DB(conn)) == "explicit"

    def test_transaction_tier_beats_session_recency(self, conn):
        """The goal bound to the CURRENT transaction wins over a newer session goal."""
        _insert_goal(conn, "g-tx", "s-old", created_timestamp=100.0, transaction_id="tx-now")
        _insert_goal(conn, "g-newer", "s1", created_timestamp=200.0)

        got = _resolve_goal_for_artifact(None, "s1", _DB(conn), transaction_id="tx-now")
        assert got == "g-tx"

    def test_session_tier_unchanged(self, conn):
        _insert_goal(conn, "g-sess", "s1")
        assert _resolve_goal_for_artifact(None, "s1", _DB(conn)) == "g-sess"

    def test_cross_session_goal_attaches_via_project_tier(self, conn):
        """The 53% case: goal created in a PRIOR session, artifact logged in a
        new one. Session-only resolution returned None here — the fix's whole
        point is that this now attaches."""
        _insert_goal(conn, "g-prior", "session-A", project_id="proj-1")

        got = _resolve_goal_for_artifact(None, "session-B", _DB(conn), transaction_id="tx-B", project_id="proj-1")
        assert got == "g-prior"

    def test_project_tier_skips_planned_archived_and_completed(self, conn):
        _insert_goal(conn, "g-planned", "session-A", status="planned", project_id="proj-1")
        _insert_goal(conn, "g-archived", "session-A", project_id="proj-1", archived=1)
        _insert_goal(conn, "g-done", "session-A", project_id="proj-1", is_completed=1)

        got = _resolve_goal_for_artifact(None, "session-B", _DB(conn), project_id="proj-1")
        assert got is None

    def test_nothing_matches_returns_none_not_wrong(self, conn):
        _insert_goal(conn, "g-other-proj", "session-A", project_id="proj-OTHER")
        assert _resolve_goal_for_artifact(None, "session-B", _DB(conn), project_id="proj-1") is None


# ─── Item D: engagement_id accepted at PREFLIGHT, inherited by goals ─────────


class TestEngagementAtPreflight:
    _BASE: typing.ClassVar[dict] = {"session_id": "s1", "vectors": {"know": 0.5, "uncertainty": 0.5}}

    def test_preflight_input_accepts_engagement_id(self):
        validated, error = safe_validate({**self._BASE, "engagement_id": "e-acme-pilot"}, PreflightInput)
        assert error is None
        assert validated.engagement_id == "e-acme-pilot"

    def test_unknown_keys_still_rejected(self):
        _validated, error = safe_validate({**self._BASE, "engagment_id": "typo"}, PreflightInput)
        assert error is not None

    def test_enrichment_writes_engagement_id_to_transaction_file(self, tmp_path, monkeypatch):
        from empirica.cli.command_handlers import _workflow_preflight as wp

        (tmp_path / ".empirica").mkdir()
        tx_file = tmp_path / ".empirica" / "active_transaction.json"
        tx_file.write_text(json.dumps({"transaction_id": "t1", "status": "open"}))
        monkeypatch.setattr(wp.R, "instance_suffix", staticmethod(lambda: ""))

        parsed = {
            "work_context": None,
            "work_type": None,
            "domain": None,
            "criticality": None,
            "predicted_check_outcomes": None,
            "task_context": "",
            "engagement_id": "e-acme-pilot",
        }
        wp._preflight_enrich_transaction_file(str(tmp_path), parsed)

        assert json.loads(tx_file.read_text())["engagement_id"] == "e-acme-pilot"

    def test_goal_inherits_from_open_transaction_only(self, monkeypatch):
        from empirica.cli.command_handlers import goal_commands as gc

        monkeypatch.setattr(
            gc.R, "transaction_read", staticmethod(lambda: {"status": "open", "engagement_id": "e-acme"})
        )
        assert _inherit_engagement_from_transaction() == "e-acme"

        monkeypatch.setattr(
            gc.R, "transaction_read", staticmethod(lambda: {"status": "closed", "engagement_id": "e-acme"})
        )
        assert _inherit_engagement_from_transaction() is None

        monkeypatch.setattr(gc.R, "transaction_read", staticmethod(lambda: None))
        assert _inherit_engagement_from_transaction() is None

    def test_schema_flag_exposes_the_field(self, capsys):
        """`preflight-submit --schema` did not exist when autonomy probed for it;
        now it prints the accepted shape, engagement_id included."""
        from argparse import Namespace

        from empirica.cli.command_handlers._workflow_preflight import handle_preflight_submit_command

        rc = handle_preflight_submit_command(Namespace(schema=True))
        assert rc == 0
        schema = json.loads(capsys.readouterr().out)
        assert "engagement_id" in schema["properties"]
        assert "work_type" in schema["properties"]
