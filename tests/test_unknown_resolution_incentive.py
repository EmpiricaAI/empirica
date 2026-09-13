"""Logging an unknown must not lower your score.

`unknown_resolution_rate` counted every unknown in the session, unfloored and
ungated, into `do`, `completion` and `impact`. So banking a question you could not
yet answer emitted a hard 0.0 into three vectors — while the system prompt calls a
session that reports uncertainty with no unknown artifacts behind it an
unsupported claim. The instrument punished the behaviour the practice mandates,
and structurally: an unknown logged late in a session cannot be resolved inside
it, so diligence could not avoid the zero.

Traced by empirica-mesh-support (2026-09-13) from the impact-zero question left
open by migration 070.

The fix is NOT a floor. A floor keeps the incentive and hides it behind a
friendlier number — which is what `issue_resolution_ratio`'s 0.2 does to its own
zero. These tests hold the two properties that actually remove it:

  * an unknown opened in this transaction changes nothing, and
  * no closure means no evidence item, rather than an item asserting zero.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.post_test.prose_collector import ProseEvidenceCollector


class _Db:
    def __init__(self, conn):
        self.conn = conn


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, session_id TEXT, "
        "transaction_id TEXT, is_resolved INTEGER DEFAULT 0)"
    )
    # Every table `_collect_action_verification` reads, so a missing one shows up
    # as a fixture gap rather than as an OperationalError mid-assertion.
    c.execute("CREATE TABLE assumptions (id TEXT PRIMARY KEY, session_id TEXT)")
    c.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, session_id TEXT)")
    return c


def _add(conn, uid, tx, resolved=0):
    conn.execute("INSERT INTO project_unknowns VALUES (?, 's1', ?, ?)", (uid, tx, resolved))


def _rate_item(conn, transaction_id=None):
    collector = ProseEvidenceCollector(session_id="s1", db=_Db(conn), transaction_id=transaction_id)
    items = collector._collect_action_verification()
    matches = [i for i in items if i.metric_name == "unknown_resolution_rate"]
    return matches[0] if matches else None


def test_logging_an_unknown_and_not_resolving_it_emits_nothing(conn):
    """The reported defect, stated as a test. This used to be a 0.0 in three vectors."""
    _add(conn, "u1", "tx_now")
    _add(conn, "u2", "tx_now")

    assert _rate_item(conn, transaction_id="tx_now") is None


def test_banking_uncertainty_cannot_lower_an_existing_rate(conn):
    """Incentive-neutral, not merely softened: the number must be IDENTICAL."""
    _add(conn, "old1", "tx_old", resolved=1)
    _add(conn, "old2", "tx_old", resolved=0)

    before = _rate_item(conn, transaction_id="tx_now")

    _add(conn, "new1", "tx_now")  # the act the practice mandates
    _add(conn, "new2", "tx_now")
    after = _rate_item(conn, transaction_id="tx_now")

    assert before is not None and after is not None
    assert after.value == before.value == 0.5
    assert after.raw_value["total"] == 2, "the transaction's own unknowns stayed out of the denominator"


def test_closing_standing_debt_is_what_the_metric_measures(conn):
    _add(conn, "old1", "tx_old", resolved=1)
    _add(conn, "old2", "tx_old", resolved=1)
    _add(conn, "old3", "tx_old", resolved=0)

    item = _rate_item(conn, transaction_id="tx_now")

    assert item is not None
    assert item.value == pytest.approx(2 / 3)
    assert item.raw_value["scope"] == "standing"
    assert item.raw_value["excludes_current_transaction"] is True


def test_no_closure_is_no_signal_not_a_zero(conn):
    """A transaction that was about something else must not read as zero impact."""
    _add(conn, "old1", "tx_old", resolved=0)
    _add(conn, "old2", "tx_old", resolved=0)

    assert _rate_item(conn, transaction_id="tx_now") is None


def test_without_a_transaction_id_the_scope_says_so(conn):
    """The fallback may still emit — it may not emit anonymously."""
    _add(conn, "u1", None, resolved=1)
    _add(conn, "u2", None, resolved=0)

    item = _rate_item(conn)

    assert item is not None
    assert item.raw_value["scope"] == "session"
    assert item.raw_value["excludes_current_transaction"] is False


def test_the_vectors_it_feeds_are_unchanged(conn):
    """A positive control on the blast radius: this fix changes WHEN it speaks, not what it speaks to.

    If these three ever drift, the change stopped being about the incentive and
    became a redefinition of what closure grounds — which is a different decision
    and needs its own argument.
    """
    _add(conn, "old1", "tx_old", resolved=1)

    item = _rate_item(conn, transaction_id="tx_now")

    assert item is not None
    assert item.supports_vectors == ["do", "completion", "impact"]
