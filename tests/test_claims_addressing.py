"""Claims must be addressable, and must not misreport their own coverage.

Two defects, both making the mechanism lie about itself in the direction that
erodes trust in a signal that was otherwise working.

**Index collision.** Claims can be declared at PREFLIGHT *and* at CHECK. `declare`
enumerated from 1 on every call, so both sets held an index 1 inside one
transaction, and `adjudicate` keys its lookup on a dict — one row silently won, the
other became unaddressable, got forced to `untested`, and was reported as a gap.
Three transactions in a single session reported phantom gaps for claims that had in
fact been verified.

**Dropped verdicts.** Submitting adjudications when nothing was declared returned a
zeroed block and discarded them. That is the precise mirror of the forcing rule this
module exists to enforce: the rule stops "declared but never checked" from looking
like "nothing declared", and this branch let "adjudicated but never declared" look
like "submitted nothing".
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from empirica.core import claims as C

SESSION = str(uuid.uuid4())
TX = str(uuid.uuid4())


class _DB:
    def __init__(self, conn):
        self.conn = conn


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE transaction_claims (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            transaction_id TEXT,
            claim_index INTEGER NOT NULL,
            claim TEXT NOT NULL,
            grounding TEXT,
            ref TEXT,
            verdict TEXT,
            verdict_evidence TEXT,
            declared_timestamp REAL,
            adjudicated_timestamp REAL
        )
        """
    )
    conn.commit()
    return _DB(conn)


def _declare(db, texts):
    return C.declare(
        db,
        session_id=SESSION,
        transaction_id=TX,
        claims=[{"claim": t, "grounding": "read"} for t in texts],
    )


# ── index collision ───────────────────────────────────────────────────


def test_a_second_declaration_continues_the_index_rather_than_restarting(db):
    """POSITIVE CONTROL — the PREFLIGHT/CHECK collision."""
    first = _declare(db, ["preflight A", "preflight B"])
    second = _declare(db, ["check A", "check B", "check C"])

    assert [c["index"] for c in first] == [1, 2]
    assert [c["index"] for c in second] == [3, 4, 5], "CHECK claims restarted at 1 and collided with PREFLIGHT's"
    assert len({c["index"] for c in first + second}) == 5, "indices must be unique within a transaction"


def test_every_declared_claim_is_addressable_by_its_index(db):
    """The consequence that actually bit: a colliding claim could never be
    adjudicated, so it was forced to untested and reported as a gap."""
    _declare(db, ["preflight A", "preflight B"])
    _declare(db, ["check A"])

    result = C.adjudicate(
        db,
        session_id=SESSION,
        transaction_id=TX,
        adjudications=[{"index": i, "verdict": "held"} for i in (1, 2, 3)],
    )

    assert result["held"] == 3
    assert result["untested"] == 0, f"a declared claim was unreachable by index: {result['gaps']}"


def test_negative_control_a_genuinely_unadjudicated_claim_is_still_a_gap(db):
    """The forcing rule must survive the fix — otherwise this could pass by making
    everything held."""
    _declare(db, ["one", "two"])

    result = C.adjudicate(db, session_id=SESSION, transaction_id=TX, adjudications=[{"index": 1, "verdict": "held"}])

    assert result["held"] == 1
    assert result["untested"] == 1
    assert len(result["gaps"]) == 1


# ── dropped verdicts ──────────────────────────────────────────────────


def test_verdicts_with_nothing_declared_are_reported_not_swallowed(db):
    """POSITIVE CONTROL — previously returned a silent zero block."""
    result = C.adjudicate(
        db,
        session_id=SESSION,
        transaction_id=TX,
        adjudications=[{"index": 1, "verdict": "held"}, {"index": 2, "verdict": "refuted"}],
    )

    assert result["declared"] == 0
    assert result["dropped_adjudications"] == 2
    assert "no claims were declared" in result["note"]


def test_negative_control_submitting_nothing_stays_quiet(db):
    """An empty POSTFLIGHT must not grow a warning — that would make the signal
    fire on the common case and train people to ignore it."""
    result = C.adjudicate(db, session_id=SESSION, transaction_id=TX, adjudications=[])

    assert result["declared"] == 0
    assert "dropped_adjudications" not in result
    assert "note" not in result


def test_malformed_verdicts_do_not_count_as_dropped(db):
    """Only real verdicts were lost. Counting junk would inflate the warning."""
    result = C.adjudicate(
        db,
        session_id=SESSION,
        transaction_id=TX,
        adjudications=[{"index": 1, "verdict": "banana"}, "not-a-dict"],
    )

    assert "dropped_adjudications" not in result
