"""Logging an unknown must not lower your score — and neither must having logged one.

`unknown_resolution_rate` counted every unknown in the session, unfloored and
ungated, into `do`, `completion` and `impact`. So banking a question you could not
yet answer emitted a hard 0.0 into three vectors, while the system prompt calls a
session reporting uncertainty with no unknown artifacts behind it an unsupported
claim. The instrument punished the behaviour the practice mandates, structurally:
an unknown logged late in a session cannot be resolved inside it.

The first fix excluded the transaction's own unknowns and kept `resolved /
standing`. That removed the penalty for banking uncertainty and left a penalty for
HAVING banked it — closing 3 of 200 scores 0.015 where closing 3 of 3 scores 1.000,
sixty-six-fold on identical work, decided by inherited backlog. On this project
(505 unknowns, 475 resolved) it sat near 0.94 every transaction regardless of what
was done, which is a constant wearing an observation's clothes.

So the metric is now the COUNT of standing unknowns closed inside the window,
saturating at three — the constant the assumption block in the same file already
uses for "enough of this act to count fully".

Neither fix is a floor. A floor preserves an incentive and hides it behind a
friendlier number, which is what `issue_resolution_ratio`'s 0.2 does to its own
zero. Traced by empirica-mesh-support over two rounds, 2026-09-13.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.post_test.prose_collector import ProseEvidenceCollector

WINDOW_START = 1_789_000_000.0
IN_WINDOW = WINDOW_START + 60
BEFORE_WINDOW = WINDOW_START - 86_400


class _Db:
    def __init__(self, conn):
        self.conn = conn


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    # Every table `_collect_action_verification` reads, so a missing one shows up
    # as a fixture gap rather than as an OperationalError mid-assertion.
    c.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, session_id TEXT, "
        "transaction_id TEXT, is_resolved INTEGER DEFAULT 0, resolved_timestamp)"
    )
    c.execute("CREATE TABLE assumptions (id TEXT PRIMARY KEY, session_id TEXT)")
    c.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, session_id TEXT)")
    return c


def _add(conn, uid, tx, resolved_at=None):
    conn.execute(
        "INSERT INTO project_unknowns VALUES (?, 's1', ?, ?, ?)",
        (uid, tx, 1 if resolved_at is not None else 0, resolved_at),
    )


def _item(conn, transaction_id="tx_now", preflight_timestamp=WINDOW_START):
    collector = ProseEvidenceCollector(
        session_id="s1",
        db=_Db(conn),
        transaction_id=transaction_id,
        preflight_timestamp=preflight_timestamp,
    )
    matches = [i for i in collector._collect_action_verification() if i.metric_name == "unknown_resolution_rate"]
    return matches[0] if matches else None


def test_logging_an_unknown_and_not_resolving_it_emits_nothing(conn):
    """The originally reported defect. This used to be a 0.0 in three vectors."""
    _add(conn, "u1", "tx_now")
    _add(conn, "u2", "tx_now")

    assert _item(conn) is None


def test_banking_uncertainty_cannot_move_the_number(conn):
    """Incentive-NEUTRAL, not merely softened: the value must be identical."""
    _add(conn, "old1", "tx_old", resolved_at=IN_WINDOW)

    before = _item(conn)
    _add(conn, "new1", "tx_now")  # the act the practice mandates
    _add(conn, "new2", "tx_now")
    after = _item(conn)

    assert before is not None and after is not None
    assert after.value == before.value


def test_identical_work_scores_identically_whatever_the_backlog(conn):
    """mesh-support's objection, as a test: 3 of 200 and 3 of 3 must agree.

    Under the old ratio these were 0.015 and 1.000 — a sixty-six-fold difference
    decided by debt inherited from earlier sessions rather than by work done.
    """
    big = sqlite3.connect(":memory:")
    for c in (conn, big):
        c.execute(
            "CREATE TABLE IF NOT EXISTS project_unknowns (id TEXT PRIMARY KEY, session_id TEXT, "
            "transaction_id TEXT, is_resolved INTEGER DEFAULT 0, resolved_timestamp)"
        )
        c.execute("CREATE TABLE IF NOT EXISTS assumptions (id TEXT PRIMARY KEY, session_id TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, session_id TEXT)")

    for i in range(3):  # three closures in each, in-window
        _add(conn, f"c{i}", "tx_old", resolved_at=IN_WINDOW)
        _add(big, f"c{i}", "tx_old", resolved_at=IN_WINDOW)
    for i in range(197):  # …but one practice carries a large standing backlog
        _add(big, f"debt{i}", "tx_old")

    small_item, big_item = _item(conn), _item(big)

    assert small_item is not None and big_item is not None
    assert small_item.value == big_item.value == 1.0
    assert big_item.raw_value["standing"] == 200
    assert big_item.raw_value["closed_in_window"] == 3


def test_closure_before_the_window_is_not_this_transactions_work(conn):
    """Otherwise the gate fires on history and the metric inflates instead of penalising."""
    _add(conn, "old1", "tx_old", resolved_at=BEFORE_WINDOW)
    _add(conn, "old2", "tx_old", resolved_at=BEFORE_WINDOW)

    assert _item(conn) is None


def test_iso_text_timestamps_are_not_all_counted_as_in_window(conn):
    """resolved_timestamp is mixed storage and SQLite orders TEXT above REAL.

    A bare `>= epoch` comparison matches every TEXT row whatever its real time, so
    an old ISO-stamped resolution would count as in-window. That failure is
    invisible — it inflates rather than errors — so it gets its own test.
    """
    _add(conn, "iso_old", "tx_old", resolved_at="2020-01-01 00:00:00")

    assert _item(conn) is None, "an ISO timestamp from 2020 is not inside this window"

    _add(conn, "iso_new", "tx_old", resolved_at="2026-09-13 01:15:52")
    item = _item(conn, preflight_timestamp=1_789_000_000.0)
    assert item is not None, "a genuinely recent ISO timestamp must still count"
    assert item.raw_value["closed_in_window"] == 1, "only the recent one, not both"


def test_the_score_saturates_rather_than_scaling_with_backlog(conn):
    for i in range(2):
        _add(conn, f"c{i}", "tx_old", resolved_at=IN_WINDOW)
    assert _item(conn).value == pytest.approx(2 / 3)

    for i in range(5):
        _add(conn, f"more{i}", "tx_old", resolved_at=IN_WINDOW)
    assert _item(conn).value == 1.0, "saturates at three; more closures do not overflow"


def test_no_window_start_means_no_observation(conn):
    """Cumulative closure under a work-done label would be the dishonest fallback."""
    _add(conn, "old1", "tx_old", resolved_at=IN_WINDOW)

    assert _item(conn, preflight_timestamp=None) is None


def test_the_vectors_it_feeds_are_unchanged(conn):
    """Positive control on blast radius: this changes WHEN it speaks, not what it grounds.

    If these drift, the change stopped being about the incentive and became a
    redefinition of what closure grounds — a separate decision needing its own
    argument.
    """
    _add(conn, "old1", "tx_old", resolved_at=IN_WINDOW)

    assert _item(conn).supports_vectors == ["do", "completion", "impact"]
