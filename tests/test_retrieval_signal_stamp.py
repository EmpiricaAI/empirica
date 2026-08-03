"""Retrieval must leave a trace, so relevance has an axis that is not age.

Proposed by empirica-outreach from a sweep of 636 unresolved findings, and the
evidence is that **age is the wrong axis**: their most valuable artifact was
from May — its symptom string matched a live log line and produced a root cause
— while one written four days earlier was wrong on arrival. Any TTL or
age-decay model destroys the first and keeps the second.

`project_dead_ends.last_revisited_at` already existed and is written by
GARDENING, not retrieval: on outreach it is populated on 173 of 186 rows, and
those are exactly the 173 a sweep had just walked. It records the gardener, not
the readers — the opposite of a relevance signal.

The stamp is a WRITE inside a READ path, which is where it is easy to get
silently wrong. It was: the first version never committed, and the caller
closes its connection without committing, so it recorded nothing. Verified by
counting stamped rows across a real bootstrap — 0 before, 0 after — which is
why these tests assert persistence rather than just the call.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.core.bootstrap.circles import _stamp_retrieval


@pytest.fixture
def cur():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT, "
        "last_retrieved_at REAL, retrieval_count INTEGER DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO project_findings (id, finding) VALUES (?, ?)",
        [("f1", "one"), ("f2", "two"), ("f3", "never surfaced")],
    )
    conn.commit()
    return conn.cursor()


def test_surfacing_stamps_the_time(cur):
    before = time.time()
    _stamp_retrieval(cur, ["f1", "f2"])

    rows = dict(cur.execute("SELECT id, last_retrieved_at FROM project_findings").fetchall())
    assert rows["f1"] >= before
    assert rows["f2"] >= before
    assert rows["f3"] is None, "an artifact that was not surfaced must stay unstamped"


def test_the_write_actually_persists(cur):
    """The bug the first version had: no commit inside a read path.

    The caller closes its connection without committing, so an uncommitted
    UPDATE is discarded and the stamp records nothing — while every in-memory
    assertion still passes.
    """
    _stamp_retrieval(cur, ["f1"])
    conn = cur.connection

    fresh = conn.execute("SELECT last_retrieved_at FROM project_findings WHERE id='f1'").fetchone()[0]
    assert fresh is not None
    assert not conn.in_transaction, "the stamp must commit, not leave an open transaction"


def test_repeated_retrieval_increments_the_count(cur):
    """Frequency is the signal. One retrieval and twenty are different states."""
    for _ in range(3):
        _stamp_retrieval(cur, ["f1"])

    count = cur.execute("SELECT retrieval_count FROM project_findings WHERE id='f1'").fetchone()[0]
    assert count == 3


def test_never_retrieved_stays_null_not_zero_time(cur):
    """NULL means 'never surfaced since the column existed' — a real state.

    Backfilling it to a timestamp would invent retrieval history and poison the
    exact signal the column exists to provide.
    """
    _stamp_retrieval(cur, ["f1"])

    assert cur.execute("SELECT last_retrieved_at FROM project_findings WHERE id='f3'").fetchone()[0] is None


def test_an_empty_batch_is_a_no_op(cur):
    _stamp_retrieval(cur, [])

    stamped = cur.execute("SELECT COUNT(*) FROM project_findings WHERE last_retrieved_at IS NOT NULL").fetchone()[0]
    assert stamped == 0


def test_a_failure_never_breaks_the_bootstrap(cur):
    """Bookkeeping must not take down the read it rides on."""
    cur.execute("DROP TABLE project_findings")

    _stamp_retrieval(cur, ["f1"])  # must not raise
