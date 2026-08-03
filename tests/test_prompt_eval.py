"""The eval must detect a real behavioural shift and refuse to dress up noise.

Built because the prompt-trim programme stalled on grepping: three practitioners
produced three different counts of which obligations were exposed from the same
files. A grep answers "did the words survive". The question is "did the
behaviour survive", and string matching cannot reach it.

The load-bearing metric is the calibration gap — a prompt that stops keeping
beliefs honest widens `self_assessed - grounded` while every audited phrase
survives intact.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prompt_eval.py"


def _load():
    spec = importlib.util.spec_from_file_location("prompt_eval", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["prompt_eval"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ev():
    return _load()


def _ts(day: str) -> float:
    return dt.datetime.strptime(day, "%Y-%m-%d").timestamp()


def _build(
    path: Path, *, window_day: str, preflights: int, checks: int, findings: int, gap: float
) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE reflexes (id INTEGER PRIMARY KEY, phase TEXT, timestamp REAL)")
    conn.execute("CREATE TABLE calibration_trajectory (point_id INTEGER PRIMARY KEY, gap REAL, timestamp REAL)")
    for t in (
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "decisions",
        "assumptions",
    ):
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, created_timestamp REAL)")
    base = _ts(window_day) + 3600
    for i in range(preflights):
        conn.execute("INSERT INTO reflexes (phase, timestamp) VALUES ('PREFLIGHT', ?)", (base + i,))
    for i in range(checks):
        conn.execute("INSERT INTO reflexes (phase, timestamp) VALUES ('CHECK', ?)", (base + i,))
    for i in range(findings):
        conn.execute("INSERT INTO project_findings (created_timestamp) VALUES (?)", (base + i,))
    for i in range(20):
        conn.execute("INSERT INTO calibration_trajectory (gap, timestamp) VALUES (?, ?)", (gap, base + i))
    conn.commit()
    return conn


def test_a_real_degradation_is_visible(ev, tmp_path):
    """Fewer artifacts per transaction and a wider gap must both show up."""
    db = tmp_path / "shift.db"
    conn = _build(db, window_day="2026-01-01", preflights=20, checks=18, findings=60, gap=0.05)
    base = _ts("2026-02-01") + 3600
    for i in range(20):
        conn.execute("INSERT INTO reflexes (phase, timestamp) VALUES ('PREFLIGHT', ?)", (base + i,))
    for i in range(4):  # CHECK discipline collapses
        conn.execute("INSERT INTO reflexes (phase, timestamp) VALUES ('CHECK', ?)", (base + i,))
    for i in range(6):  # logging collapses
        conn.execute("INSERT INTO project_findings (created_timestamp) VALUES (?)", (base + i,))
    for i in range(20):  # beliefs drift from evidence
        conn.execute("INSERT INTO calibration_trajectory (gap, timestamp) VALUES (?, ?)", (0.40, base + i))
    conn.commit()

    before = ev.measure(conn, *ev._parse_window("2026-01-01..2026-01-02"))
    after = ev.measure(conn, *ev._parse_window("2026-02-01..2026-02-02"))

    assert before["artifacts_per_transaction"] == pytest.approx(3.0)
    assert after["artifacts_per_transaction"] == pytest.approx(0.3)
    assert before["check_rate"] > 0.8 and after["check_rate"] < 0.3
    assert after["mean_abs_calibration_gap"] > before["mean_abs_calibration_gap"] * 5


def test_identical_behaviour_produces_no_delta(ev, tmp_path):
    """The negative control — the instrument must not manufacture a signal."""
    db = tmp_path / "flat.db"
    conn = _build(db, window_day="2026-01-01", preflights=20, checks=18, findings=60, gap=0.10)
    base = _ts("2026-02-01") + 3600
    for i in range(20):
        conn.execute("INSERT INTO reflexes (phase, timestamp) VALUES ('PREFLIGHT', ?)", (base + i,))
    for i in range(18):
        conn.execute("INSERT INTO reflexes (phase, timestamp) VALUES ('CHECK', ?)", (base + i,))
    for i in range(60):
        conn.execute("INSERT INTO project_findings (created_timestamp) VALUES (?)", (base + i,))
    for i in range(20):
        conn.execute("INSERT INTO calibration_trajectory (gap, timestamp) VALUES (?, ?)", (0.10, base + i))
    conn.commit()

    before = ev.measure(conn, *ev._parse_window("2026-01-01..2026-01-02"))
    after = ev.measure(conn, *ev._parse_window("2026-02-01..2026-02-02"))

    assert before["artifacts_per_transaction"] == after["artifacts_per_transaction"]
    assert before["check_rate"] == after["check_rate"]
    assert before["mean_abs_calibration_gap"] == after["mean_abs_calibration_gap"]


def test_windows_do_not_bleed(ev, tmp_path):
    """An off-by-one on the window boundary would attribute work to the wrong side."""
    db = tmp_path / "edge.db"
    conn = _build(db, window_day="2026-01-01", preflights=5, checks=5, findings=5, gap=0.1)

    inside = ev.measure(conn, *ev._parse_window("2026-01-01..2026-01-01"))
    outside = ev.measure(conn, *ev._parse_window("2026-01-02..2026-01-03"))

    assert inside["preflights"] == 5, "a single-day window must include that whole day"
    assert outside["preflights"] == 0


def test_it_refuses_to_measure_mixed_type_timestamps(ev, tmp_path):
    """The failure that motivated the precondition, as an assertion.

    A single TEXT row in a numeric column sorts above every number and falls
    outside every numeric window. Found live: 13 legacy rows dated 2025-12-31
    were returned as the "most recent" findings by every recency query, so the
    context injected into each session was eight months stale — silently.

    A windowing tool that ran anyway would exclude them from every window and
    report the result as measurement.
    """
    db = tmp_path / "dirty.db"
    conn = _build(db, window_day="2026-01-01", preflights=5, checks=5, findings=5, gap=0.1)
    conn.execute("INSERT INTO project_findings (created_timestamp) VALUES ('2025-12-31 18:52:10')")
    conn.commit()

    with pytest.raises(SystemExit) as exc:
        ev.assert_timestamps_are_comparable(conn)

    assert "project_findings" in str(exc.value)
    assert "wrong, not approximate" in str(exc.value)


def test_clean_timestamps_pass_the_precondition(ev, tmp_path):
    db = tmp_path / "clean.db"
    conn = _build(db, window_day="2026-01-01", preflights=5, checks=5, findings=5, gap=0.1)

    ev.assert_timestamps_are_comparable(conn)  # must not raise


def test_a_thin_window_is_flagged_rather_than_reported(ev, tmp_path, capsys):
    """Below the floor the tool must say so, not print a ratio that reads as evidence."""
    db = tmp_path / "thin.db"
    conn = _build(db, window_day="2026-01-01", preflights=2, checks=2, findings=4, gap=0.1)

    ev._report_window("thin", ev.measure(conn, *ev._parse_window("2026-01-01..2026-01-02")))
    out = capsys.readouterr().out

    assert "too few to support a claim" in out
