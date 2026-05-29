"""Tests for the POSTFLIGHT pipeline restructure (#95 Issue 3).

Three behavioral guarantees we ship:

  1. Pre-validation (stage 0) failure returns early — NO state mutation.
     Loop stays open, AI can fix context (project-switch) and retry.

  2. _soft_run wraps a stage so any exception becomes a warning, not a
     failure. Successful stages still return their value.

  3. Hard-mutation stages (3-4: close transaction, write reflex) are
     unaffected — they remain hard. The fix only changes how downstream
     stages 5-7 fail.

The full pipeline integration is exercised by existing live POSTFLIGHT
tests; these target the new helpers directly so the test surface is
hermetic and fast.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.workflow_commands import (
    _soft_run,
    _validate_postflight_preconditions,
)

# ─── _validate_postflight_preconditions ─────────────────────────────────────


class TestValidatePostflightPreconditions:
    def test_returns_true_when_session_has_project_id(self):
        # Mock SessionDatabase to return a row with project_id
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = ('valid-project-uuid',)

            ok, err = _validate_postflight_preconditions('session-001')
            assert ok is True
            assert err is None

    def test_returns_false_when_session_row_missing(self):
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = None

            ok, err = _validate_postflight_preconditions('missing-session-uuid')
            assert ok is False
            assert err is not None
            assert 'not found' in err

    def test_returns_false_when_project_id_is_null(self):
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = (None,)

            ok, err = _validate_postflight_preconditions('session-002')
            assert ok is False
            assert 'no project_id' in err
            assert 'project-switch' in err  # actionable hint

    def test_returns_false_when_project_id_is_empty_string(self):
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = ('',)

            ok, err = _validate_postflight_preconditions('session-003')
            assert ok is False
            assert 'no project_id' in err

    def test_fails_open_when_db_unavailable(self):
        # If validation itself can't run, fail-open (return ok=True with a
        # diagnostic message). Downstream soft-run wrappers handle their
        # own errors anyway. The fix should never make POSTFLIGHT *less*
        # available than it was before.
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db_cls.side_effect = OSError("DB inaccessible")

            ok, err = _validate_postflight_preconditions('session-004')
            assert ok is True  # fail-open
            assert err is not None
            assert 'skipped' in err


# ─── _soft_run ───────────────────────────────────────────────────────────────


class TestSoftRun:
    def test_returns_function_value_on_success(self):
        warnings = []
        result = _soft_run("bus_publish", warnings, lambda: 42)
        assert result == 42
        assert warnings == []

    def test_passes_args_and_kwargs_through(self):
        warnings = []
        def fn(a, b, *, c):
            return a + b + c
        result = _soft_run("test_stage", warnings, fn, 1, 2, c=3)
        assert result == 6
        assert warnings == []

    def test_catches_exception_and_appends_warning(self):
        warnings = []
        def boom():
            raise ValueError("explicit failure")

        result = _soft_run("compliance_check", warnings, boom)
        assert result is None
        assert len(warnings) == 1
        w = warnings[0]
        assert w["stage"] == "compliance_check"
        assert w["error_type"] == "ValueError"
        assert w["error"] == "explicit failure"

    def test_multiple_failures_accumulate(self):
        warnings = []
        _soft_run("stage_a", warnings, lambda: 1 / 0)
        _soft_run("stage_b", warnings, lambda: int("not a number"))
        _soft_run("stage_c", warnings, lambda: "fine")  # success

        assert len(warnings) == 2
        assert warnings[0]["stage"] == "stage_a"
        assert warnings[0]["error_type"] == "ZeroDivisionError"
        assert warnings[1]["stage"] == "stage_b"
        assert warnings[1]["error_type"] == "ValueError"

    def test_handles_keyboard_interrupt_does_not_swallow(self):
        # KeyboardInterrupt is a user signal — must propagate, not be
        # absorbed as a warning. SystemExit IS caught (separate test
        # below) because library functions sometimes use sys.exit; KI
        # comes from the user.
        warnings = []
        def raises_ki():
            raise KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            _soft_run("bus_publish", warnings, raises_ki)
        assert warnings == []  # no warning recorded — exception escaped

    def test_catches_system_exit_from_library(self):
        # Some helpers in cli.utils.project_resolver and elsewhere call
        # sys.exit(1) on miss. SystemExit derives from BaseException
        # (not Exception), so without explicit handling it would walk
        # straight through every `except Exception` above and kill
        # POSTFLIGHT. See #95 (pschwinger) for the repro.
        warnings = []
        def lib_calls_sys_exit():
            import sys
            sys.exit(1)
        result = _soft_run("cortex_sync", warnings, lib_calls_sys_exit)
        assert result is None
        assert len(warnings) == 1
        w = warnings[0]
        assert w["stage"] == "cortex_sync"
        assert w["error_type"] == "SystemExit"
        assert "sys.exit" in w["error"]

    def test_catches_system_exit_with_string_code(self):
        # sys.exit("error message") is also valid Python. Must capture
        # the code in the warning regardless of type.
        warnings = []
        def lib_exit_string():
            import sys
            sys.exit("project not found")
        result = _soft_run("cortex_sync", warnings, lib_exit_string)
        assert result is None
        assert "project not found" in warnings[0]["error"]

    def test_warning_dict_shape_is_serializable(self):
        # Warnings end up in result['warnings'] which is JSON-serialized.
        # Make sure no funky types leak in.
        import json
        warnings = []
        _soft_run("bus_publish", warnings, lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        # Should serialize cleanly
        json.dumps(warnings)


# ─── _cortex_resolve_project_id (architectural fix) ─────────────────────────


class TestCortexResolveProjectId:
    """Cortex sync now reads project_id from session row, not project.yaml.

    Before: read Path.cwd()/.empirica/project.yaml, routed through
    resolve_project_id() which sys.exit(1)'s on miss → SystemExit walked
    through every wrapper, killed POSTFLIGHT (#95 root cause).

    After: SELECT project_id FROM sessions WHERE session_id = ?. DB is
    canonical, T5's pre-validation guarantees the row + project_id exist
    by the time this runs.
    """

    def test_returns_project_id_from_session_row(self):
        from unittest.mock import patch

        from empirica.cli.command_handlers.workflow_commands import _cortex_resolve_project_id

        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = ('eea1ca87-real-project-uuid',)

            result = _cortex_resolve_project_id('session-001')
            assert result == 'eea1ca87-real-project-uuid'

    def test_returns_empty_string_on_missing_session(self):
        from unittest.mock import patch

        from empirica.cli.command_handlers.workflow_commands import _cortex_resolve_project_id

        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = None

            result = _cortex_resolve_project_id('missing-session')
            assert result == ""

    def test_returns_empty_string_on_null_project_id(self):
        from unittest.mock import patch

        from empirica.cli.command_handlers.workflow_commands import _cortex_resolve_project_id

        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = (None,)

            result = _cortex_resolve_project_id('session-002')
            assert result == ""

    def test_returns_empty_string_on_empty_session_id(self):
        from empirica.cli.command_handlers.workflow_commands import _cortex_resolve_project_id
        # No DB query attempted — short-circuit on empty input.
        assert _cortex_resolve_project_id("") == ""
        assert _cortex_resolve_project_id(None) == ""  # type: ignore[arg-type]

    def test_does_not_read_project_yaml_or_call_resolve_project_id(self):
        # Architectural test: the function must NOT touch the YAML file
        # or the resolve_project_id helper. If a future refactor accidentally
        # reintroduces either, this test fails.
        from unittest.mock import patch

        from empirica.cli.command_handlers.workflow_commands import _cortex_resolve_project_id

        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls, \
             patch('builtins.open') as mock_open, \
             patch('empirica.cli.utils.project_resolver.resolve_project_id') as mock_rpi:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = ('canonical-uuid',)

            result = _cortex_resolve_project_id('session-003')
            assert result == 'canonical-uuid'
            # No YAML read, no resolve_project_id call — the failure modes
            # from #95 are structurally impossible.
            mock_open.assert_not_called()
            mock_rpi.assert_not_called()

    def test_db_failure_returns_empty_string_not_raises(self):
        # Cortex sync is non-fatal — if DB is unavailable, return empty
        # string and let the caller skip. Never raise to caller.
        from unittest.mock import patch

        from empirica.cli.command_handlers.workflow_commands import _cortex_resolve_project_id

        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db_cls.side_effect = OSError("DB locked")
            result = _cortex_resolve_project_id('session-004')
            assert result == ""


# ─── _cortex_extract_transaction_graph (full-set graph sync) ────────────────
# David-directed full-set /v1/sync: the sender builds a {nodes,edges} graph
# covering the whole artifact set + edges, mirroring the log-artifacts node
# schema so Cortex's process_artifact_graph ingests it directly.


class TestCortexExtractTransactionGraph:
    def _build_db_with_tx(self, tmp_path):
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase(db_path=str(tmp_path / "graph.db"))
        return db

    def _patch_tx(self, monkeypatch, db, tx_id):
        from empirica.cli.command_handlers import _workflow_postflight as wp
        monkeypatch.setattr(wp.R, "transaction_read",
                            lambda *a, **k: {"transaction_id": tx_id})
        monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: db)
        return wp

    def test_graph_covers_full_set_with_goal_and_artifact_edges(self, tmp_path, monkeypatch):
        db = self._build_db_with_tx(tmp_path)
        PID, SID, TX, GID = "proj", "sess", "tx-graph-1", "goal-xyz"
        fid = db.log_finding(PID, SID, "a real finding", impact=0.8,
                             goal_id=GID, transaction_id=TX)
        did = db.log_decision(PID, SID, choice="chose X", rationale="grounded",
                              goal_id=GID, transaction_id=TX)
        # an inter-artifact edge
        from empirica.cli.command_handlers.graph_commands import _store_edge
        _store_edge(db, fid, did, "supports")

        wp = self._patch_tx(monkeypatch, db, TX)
        graph = wp._cortex_extract_transaction_graph(SID)

        # full set: both node types present, keyed by their real UUIDs
        types = {n["type"] for n in graph["nodes"]}
        assert {"finding", "decision"} <= types
        refs = {n["ref"] for n in graph["nodes"]}
        assert fid in refs and did in refs

        # node data matches the log-artifacts per-type field convention
        fnode = next(n for n in graph["nodes"] if n["ref"] == fid)
        assert fnode["data"]["finding"] == "a real finding"
        assert fnode["data"]["impact"] == 0.8
        dnode = next(n for n in graph["nodes"] if n["ref"] == did)
        assert dnode["data"]["choice"] == "chose X"
        assert dnode["data"]["rationale"] == "grounded"

        # per-artifact goal edges + the canonical artifact_edges edge
        goal_edges = [e for e in graph["edges"] if e["relation"] == "addresses_goal"]
        assert {e["from"] for e in goal_edges} == {fid, did}
        assert all(e["to"] == GID for e in goal_edges)
        assert any(e["relation"] == "supports" and e["from"] == fid and e["to"] == did
                   for e in graph["edges"])

    def test_graph_empty_when_no_artifacts_in_transaction(self, tmp_path, monkeypatch):
        db = self._build_db_with_tx(tmp_path)
        wp = self._patch_tx(monkeypatch, db, "tx-with-nothing")
        assert wp._cortex_extract_transaction_graph("sess") == {}

    def test_graph_empty_when_no_open_transaction(self, tmp_path, monkeypatch):
        from empirica.cli.command_handlers import _workflow_postflight as wp
        monkeypatch.setattr(wp.R, "transaction_read", lambda *a, **k: None)
        assert wp._cortex_extract_transaction_graph("sess") == {}

    def test_goal_edge_omitted_when_artifact_has_no_goal(self, tmp_path, monkeypatch):
        db = self._build_db_with_tx(tmp_path)
        PID, SID, TX = "proj", "sess", "tx-nogoal"
        db.log_finding(PID, SID, "goalless finding", impact=0.5, transaction_id=TX)
        wp = self._patch_tx(monkeypatch, db, TX)
        graph = wp._cortex_extract_transaction_graph(SID)
        assert len(graph["nodes"]) == 1
        assert [e for e in graph["edges"] if e["relation"] == "addresses_goal"] == []
