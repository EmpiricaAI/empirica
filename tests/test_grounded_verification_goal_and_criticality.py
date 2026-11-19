"""A grounded verification names the goal it graded and the stakes the transaction declared (goal dffe317d).

Measured 2026-11-19: goal_id and criticality were empty in all 4198 grounded rows of core's own practice, though PREFLIGHT
names goal_ids and accepts criticality. Cortex's expertise export needs both (to join to goals and to gate on stakes); the
goal's work kind then joins through goal_id.
"""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers._workflow_postflight import _verification_goal_and_criticality


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE goals (id TEXT, transaction_id TEXT, created_timestamp REAL)")
    conn.executemany(
        "INSERT INTO goals VALUES (?, ?, ?)",
        [("aaaa1111-0000-0000-0000-000000000001", "tx-1", 1.0), ("bbbb2222-0000-0000-0000-000000000002", "tx-1", 2.0)],
    )
    return conn


def test_the_first_goal_preflight_named_wins_and_a_prefix_resolves():
    tx = {"goal_ids": ["aaaa1111"], "criticality": "high"}
    assert _verification_goal_and_criticality(tx, _db(), "tx-9") == ("aaaa1111-0000-0000-0000-000000000001", "high")


def test_without_named_goals_the_newest_goal_created_in_the_transaction():
    # No declared criticality in the transaction file is the medium default (PREFLIGHT stores only a departure from it).
    assert _verification_goal_and_criticality({}, _db(), "tx-1") == ("bbbb2222-0000-0000-0000-000000000002", "medium")


def test_nothing_known_is_none_never_a_guess():
    assert _verification_goal_and_criticality({"goal_ids": ["ffff"]}, _db(), "tx-9") == (None, "medium")


def test_store_verification_writes_the_criticality_it_is_given(tmp_path):
    from types import SimpleNamespace

    from empirica.core.post_test.grounded_calibration import GroundedCalibrationManager

    conn = sqlite3.connect(str(tmp_path / "s.db"))
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    manager = GroundedCalibrationManager(db)
    assessment = SimpleNamespace(
        grounded={}, self_assessed={}, calibration_gaps={}, grounded_coverage=0.5, overall_calibration_score=0.1
    )
    bundle = SimpleNamespace(items=[], sources_available=[], sources_failed=[])
    vid = manager.store_verification("s1", assessment, bundle, goal_id="g1", criticality="high", transaction_id="tx")
    row = db.conn.execute(
        "SELECT goal_id, criticality FROM grounded_verifications WHERE verification_id = ?", (vid,)
    ).fetchone()
    assert tuple(row) == ("g1", "high")
    conn.close()
    db.close()


def test_postflight_captures_goal_ids_and_criticality_before_it_closes_the_transaction(tmp_path, monkeypatch):
    """The verification runs after the transaction file is closed, so close must carry both (the first wiring read the
    closed file and stamped nothing)."""
    import json

    from empirica.cli.command_handlers import _workflow_postflight as wp

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "active_transaction_x.json").write_text(
        json.dumps({"transaction_id": "tx-1", "status": "open", "goal_ids": ["g1"], "criticality": "high"})
    )
    monkeypatch.setattr(wp.R, "transaction_write", lambda **k: None, raising=False)
    result = {"transaction_id": None, "tool_call_count": 0, "avg_turns": 0, "phase_tool_counts": None, "tool_trace": [],
              "edited_files": [], "work_context": None, "work_type": None}  # fmt: skip
    wp._postflight_close_and_capture_counters(result, str(tmp_path), "_x")
    assert result["goal_ids"] == ["g1"] and result["criticality"] == "high"


import pytest  # noqa: E402


@pytest.mark.parametrize("named", ["%", "", "aaaa_111", " "])  # case is folded by SQLite LIKE: ids are lowercase hex
def test_wildcards_case_and_blanks_never_resolve_a_goal(named):
    """1.17.13 sweep: an unescaped LIKE resolved '%', '' and 'aaaa_111' to a goal they do not spell."""
    assert _verification_goal_and_criticality({"goal_ids": [named]}, _db(), "tx-9")[0] is None


def test_an_ambiguous_prefix_resolves_nothing_and_an_exact_id_wins_over_a_longer_one():
    conn = _db()
    conn.execute("INSERT INTO goals VALUES ('aaaa1111-0000-0000-0000-000000000001-x', 'tx-2', 3.0)")
    assert _verification_goal_and_criticality({"goal_ids": ["aaaa"]}, conn, "tx-9")[0] is None
    exact = "aaaa1111-0000-0000-0000-000000000001"
    assert _verification_goal_and_criticality({"goal_ids": [exact]}, conn, "tx-9")[0] == exact


def test_goal_ids_given_as_a_string_are_not_read_a_character_at_a_time():
    assert _verification_goal_and_criticality({"goal_ids": "a-not-a-list"}, _db(), "tx-9")[0] is None


def test_profile_import_marks_every_imported_row_local():
    """1.17.13 sweep: goals, findings, unknowns, dead ends and mistakes took the table's 'shared' default on import."""
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "empirica/core/canonical/empirica_git/profile_import.py"
    ).read_text()
    for table in ("project_findings", "project_unknowns", "project_dead_ends", "mistakes_made", "goals"):
        assert re.search(rf'self\._mark_local\(cursor, "{table}",', src), table
    for table in ("decisions", "assumptions"):  # these insert 'local' directly
        insert = re.search(rf"INSERT (?:OR IGNORE )?INTO {table}\b(.*?)VALUES \((.*?)\)", src, re.S)
        assert insert and "visibility" in insert.group(1) and "'local'" in insert.group(2), table


def test_mark_local_sets_the_row_and_tolerates_a_table_without_the_column():
    import sqlite3

    from empirica.core.canonical.empirica_git.profile_import import ProfileImporter

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE goals (id TEXT, visibility TEXT DEFAULT 'shared')")
    conn.execute("CREATE TABLE old (id TEXT)")
    conn.execute("INSERT INTO goals (id) VALUES ('g')")
    importer = ProfileImporter.__new__(ProfileImporter)
    importer._mark_local(conn.cursor(), "goals", "g")
    importer._mark_local(conn.cursor(), "old", "g")  # no column: logged, not raised
    assert conn.execute("SELECT visibility FROM goals").fetchone()[0] == "local"
