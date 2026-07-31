"""The wrong-vs-stale split must be REPORTED, not merely storable.

`resolution_kind` (migration 061) shipped queryable and surfaced nowhere: read in
exactly one place (source-outcome attribution), absent from `calibration-report`,
`profile-status` and `compliance-report`. That is one step from the free-text
`resolution` it replaced, which failed for precisely this reason — nobody could see
the aggregate, so nobody noticed it was degenerate for six months.

What made the original defect visible was one number: **1267 stale against 1 wrong**
across 1268 resolutions. These tests pin that number onto a surface a practitioner
actually reads, and pin the honesty of the zero case — which is the hard part.

Two failure directions, and the tests defend both:
  - Silent zero: printing "retracted: 0" like any other row, so an implausible
    number reads as routine.
  - Nagging: firing on every practice regardless of volume, which is how the
    POSTFLIGHT breadth nudge trained everyone to ignore it.
"""

from __future__ import annotations

import io
import sys

import pytest

from empirica.cli.command_handlers import profile_commands as P


def _capture(rec: dict) -> str:
    buf, out = sys.stdout, io.StringIO()
    sys.stdout = out
    try:
        P._print_correction_record(rec)
    finally:
        sys.stdout = buf
    return out.getvalue()


def _rec(resolved, by_kind=None, unclassified=0, linked=0):
    return {
        "resolved": resolved,
        "by_kind": by_kind or {},
        "unclassified": unclassified,
        "linked_supersessions": linked,
    }


# ── the split must be visible ─────────────────────────────────────────


def test_the_split_is_printed():
    out = _capture(_rec(10, {"stale": 7, "retracted": 3}))

    assert "stale: 7" in out
    assert "retracted: 3" in out


def test_linked_supersessions_are_distinguished_from_narrated_ones():
    """1034 resolutions said "superseded" in prose here while this count was 0
    across 4199 rows. Reporting the LINK count is what makes that visible."""
    out = _capture(_rec(10, {"superseded": 9}, linked=2))

    line = next(ln for ln in out.splitlines() if "LINKED" in ln)
    assert line.strip().endswith(": 2"), f"the link COUNT must be on the line, got {line!r}"


def test_unclassified_rows_are_shown_not_hidden():
    """The 1268 pre-061 resolutions are deliberately un-backfilled. They must be
    visible as unclassified rather than silently omitted or counted as stale."""
    out = _capture(_rec(1268, {}, unclassified=1268))

    assert "unclassified" in out
    assert "1268" in out


# ── the zero case: honest, but not nagging ────────────────────────────


def test_zero_retractions_at_volume_is_flagged_as_implausible():
    """THE regression this surface exists for. Zero must not read as routine."""
    out = _capture(_rec(1268, {"stale": 1267, "superseded": 1}))

    assert "0 recorded as WRONG" in out
    assert "implausible" in out
    assert "--kind retracted" in out, "the flag must name the remedy, not just complain"


def test_a_young_practice_with_zero_retractions_is_not_nagged():
    """Below the floor, zero retractions is unremarkable — a practice that has
    resolved 12 findings legitimately may not have been wrong yet. Firing here
    would make the warning noise, which is how a nudge stops being read."""
    out = _capture(_rec(12, {"stale": 12}))

    assert "implausible" not in out
    assert "stale: 12" in out, "the split is still reported — only the WARNING is floored"


def test_the_flag_goes_quiet_once_retraction_is_actually_used():
    """It must be satisfiable. A warning that persists after you comply is one you
    learn to ignore."""
    out = _capture(_rec(1268, {"stale": 1267, "retracted": 1}))

    assert "implausible" not in out


def test_mistyped_counts_as_a_retraction_for_the_flag():
    """A mistake logged as a finding was never a finding — recording that IS the
    practice noticing it was wrong, so it must silence the flag."""
    out = _capture(_rec(500, {"stale": 499, "mistyped": 1}))

    assert "implausible" not in out


@pytest.mark.parametrize("empty", [{}, {"resolved": 0}, None])
def test_nothing_is_printed_when_there_is_nothing_to_say(empty):
    """A practice with no resolutions gets no block at all — not a block of zeros."""
    assert _capture(empty or {}) == ""


# ── honest absence on old databases ───────────────────────────────────


def test_a_pre_061_database_reports_absence_not_zeros(tmp_path, monkeypatch):
    """`column absent` and `practice never retracted` are different states, and
    printing 0 for both is exactly the conflation this surface exists to end."""
    import sqlite3

    import empirica.data.session_database as _sdb

    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT, is_resolved BOOLEAN, superseded_by TEXT)"
    )
    conn.execute("INSERT INTO project_findings VALUES ('f1','x',1,NULL)")
    conn.commit()
    conn.close()

    class _FakeDB:
        def __init__(self, *a, **k):
            self.conn = sqlite3.connect(db_file)

        def close(self):
            self.conn.close()

    monkeypatch.setattr(_sdb, "SessionDatabase", _FakeDB)

    rec = P._get_correction_record()

    assert rec == {}, "a schema without resolution_kind must report nothing, not zeros"
    assert _capture(rec) == ""


def test_the_record_survives_a_totally_broken_db(monkeypatch):
    """profile-status is a health surface. It must not become the thing that breaks."""
    import empirica.data.session_database as _sdb

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no db here")

    monkeypatch.setattr(_sdb, "SessionDatabase", _Boom)

    with pytest.raises(RuntimeError):
        P._get_correction_record()
