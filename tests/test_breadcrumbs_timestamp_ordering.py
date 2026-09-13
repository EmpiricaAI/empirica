"""Recency ordering must survive a mixed-storage timestamp column.

`get_project_findings` sorts by `created_timestamp DESC`. That column is REAL
epoch on older rows and can be TEXT ISO on newer ones — `project_unknowns`
already carries both — so the ORDER BY normalises before comparing.

The normalisation it used was wrong in a way that reads as right:

    '2026-09-13 01:15:52' GLOB '[0-9]*'   ->  1      (an ISO date starts with a digit)
    CAST('2026-09-13 01:15:52' AS REAL)   ->  2026.0

so the numeric branch swallowed every ISO row and filed it under January 1970 —
which in a DESC recency sort puts the NEWEST findings LAST. No existing test
caught it because `project_findings.created_timestamp` happens to be REAL on all
4,803 rows here; the defect was one ISO write away from going live, and the same
idiom had already been copied into the calibration collector.

So this test builds the mixture the live table does not have. It is the only
place the broken branch is reachable, which is exactly why it needs to exist.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories.breadcrumbs import BreadcrumbRepository

#: Epoch seconds, and the SAME instants as ISO text. Written both ways on
#: purpose: a correct ORDER BY interleaves them by time, a broken one segregates
#: them by storage class.
_T = {
    "oldest": 1_788_000_000.0,  # REAL
    "older": "2026-09-01 12:00:00",  # TEXT  (~1788609600)
    "newer": 1_789_000_000.0,  # REAL
    "newest": "2026-09-13 01:15:52",  # TEXT  (~1789262152)
}


@pytest.fixture
def repo(tmp_path):
    """A findings table under tmp_path — never the developer's real database."""
    db = sqlite3.connect(tmp_path / "b.db")
    db.row_factory = sqlite3.Row  # the repository builds dicts from rows
    db.execute("""
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, session_id TEXT, goal_id TEXT, subtask_id TEXT,
            finding TEXT, created_timestamp, finding_data TEXT, subject TEXT,
            impact REAL, project_id TEXT, is_resolved INTEGER DEFAULT 0
        )
    """)
    for name, ts in _T.items():
        db.execute(
            "INSERT INTO project_findings (id, finding, created_timestamp, subject, project_id) "
            "VALUES (?, ?, ?, 'topic', 'p1')",
            (name, f"finding {name}", ts),
        )
    db.commit()

    r = BreadcrumbRepository.__new__(BreadcrumbRepository)
    r.conn = db
    return r


def _order(repo, **kw) -> list[str]:
    return [f["id"] for f in repo.get_project_findings("p1", depth="complete", **kw)]


def test_newest_first_across_both_storage_classes(repo):
    """The failing case: under the old idiom both TEXT rows sorted as 1970."""
    assert _order(repo) == ["newest", "newer", "older", "oldest"]


def test_the_subject_filtered_query_orders_the_same_way(repo):
    """A second query carries the same clause — fixing one and not the other is the classic miss."""
    assert _order(repo, subject="topic") == ["newest", "newer", "older", "oldest"]


def test_text_rows_are_not_segregated_from_real_rows(repo):
    """The signature of the bug, stated independently of the exact expected order.

    Broken, the two TEXT rows land together at one end regardless of their real
    instants. This asserts interleaving, so it still fails if someone reintroduces
    storage-class sorting under a different formula.
    """
    order = _order(repo)
    text_positions = sorted(order.index(n) for n in ("older", "newest"))
    assert text_positions != [0, 1] and text_positions != [2, 3], (
        f"TEXT rows clustered at one end ({order}) — they are being compared as a storage class, not as times"
    )


def test_a_purely_numeric_string_is_read_as_epoch_not_as_a_date(repo):
    """`'1789300000'` is a timestamp stored as TEXT, not an ISO date.

    strftime would return NULL for it and drop the row to the bottom, so the
    numeric-looking-TEXT branch has to exist alongside the typeof() check.
    """
    repo.conn.execute(
        "INSERT INTO project_findings (id, finding, created_timestamp, subject, project_id) "
        "VALUES ('numeric_text', 'f', '1789300000', 'topic', 'p1')"
    )
    repo.conn.commit()

    assert _order(repo)[0] == "numeric_text", "a numeric string later than every other row must sort first"
