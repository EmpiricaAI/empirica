"""The prose collector no longer grounds `change` or `uncertainty` on counts of logged things, and counts the transaction.

cortex (prop_y5gjiksahzhc5l3ih6yr3talay), confirmed in the code and ruled on by David (2026-10-04):

* `finding_production` = min(1, findings/10) supported [do, change]: ten findings read as `change` 1.0 whether or not
  anything changed, so the insight said change was underestimated by 0.65 on a transaction that changed nothing.
* `assumption_logging` = min(1, assumptions/3) supported [uncertainty, know]: three logged assumptions read as
  `uncertainty` 1.0, so honest logging could never shrink the gap. The mapping pointed the wrong way.
* Both counted the whole SESSION, while unknown_resolution_rate beside them counts the transaction, so one long session
  saturated every transaction in it.

The counts stay as evidence for `do` and `know` (producing findings is work done; logging assumptions is honesty about
what is known), they are scoped to the transaction, and each observation says which scope it used so stored history can
be split at this change.
"""

from __future__ import annotations

import pytest

from empirica.core.post_test.prose_collector import ProseEvidenceCollector
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def world(tmp_path):
    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="a", project_id=pid)
    yield type("W", (), {"db": db, "pid": pid, "sid": sid})
    db.close()


def _collector(world, tx):
    return ProseEvidenceCollector(session_id=world.sid, project_id=world.pid, db=world.db, transaction_id=tx)


def _item(items, name):
    found = [i for i in items if i.metric_name == name]
    assert len(found) == 1, f"{name}: {[i.metric_name for i in items]}"
    return found[0]


def _log_findings(world, tx, n):
    for i in range(n):
        world.db.log_finding(world.pid, world.sid, f"{tx} finding {i} with a few words in it", transaction_id=tx)


def _log_assumptions(world, tx, n):
    for i in range(n):
        world.db.log_assumption(world.pid, world.sid, f"{tx} assumption {i}", transaction_id=tx)


# ── findings → do, not change ───────────────────────────────────────────────


def test_findings_are_counted_in_the_transaction_not_the_session(world):
    _log_findings(world, "tx-A", 12)
    _log_findings(world, "tx-B", 2)

    item = _item(_collector(world, "tx-B")._collect_document_metrics(), "finding_production")

    assert item.raw_value["findings_logged"] == 2
    assert item.value == pytest.approx(0.2)
    assert item.raw_value["scope"] == "transaction"


def test_finding_production_no_longer_supports_change(world):
    _log_findings(world, "tx-A", 12)

    item = _item(_collector(world, "tx-A")._collect_document_metrics(), "finding_production")

    assert "change" not in item.supports_vectors and "do" in item.supports_vectors


def test_a_transaction_that_logged_no_findings_emits_none_however_long_the_session(world):
    _log_findings(world, "tx-A", 56)

    items = _collector(world, "tx-B")._collect_document_metrics()

    assert [i for i in items if i.metric_name == "finding_production"] == []


def test_without_a_transaction_the_session_is_the_only_window_and_says_so(world):
    _log_findings(world, "tx-A", 3)

    item = _item(_collector(world, None)._collect_document_metrics(), "finding_production")

    assert item.raw_value["findings_logged"] == 3 and item.raw_value["scope"] == "session"


# ── assumptions → know, not uncertainty ─────────────────────────────────────


def test_assumptions_are_counted_in_the_transaction_not_the_session(world):
    _log_assumptions(world, "tx-A", 5)
    _log_assumptions(world, "tx-B", 1)

    item = _item(_collector(world, "tx-B")._collect_action_verification(), "assumption_logging")

    assert item.raw_value["assumptions_logged"] == 1
    assert item.value == pytest.approx(1 / 3)
    assert item.raw_value["scope"] == "transaction"


def test_assumption_logging_no_longer_supports_uncertainty(world):
    """More assumptions acknowledged is not evidence of MORE uncertainty: the old mapping read three of them as 1.0."""
    _log_assumptions(world, "tx-A", 5)

    item = _item(_collector(world, "tx-A")._collect_action_verification(), "assumption_logging")

    assert "uncertainty" not in item.supports_vectors and "know" in item.supports_vectors


def test_a_transaction_that_logged_no_assumptions_emits_none(world):
    _log_assumptions(world, "tx-A", 9)

    items = _collector(world, "tx-B")._collect_action_verification()

    assert [i for i in items if i.metric_name == "assumption_logging"] == []


def test_no_prose_observation_still_grounds_uncertainty_or_change_from_a_count(world):
    """The whole point, across every observation the two methods can emit."""
    _log_findings(world, "tx-A", 30)
    _log_assumptions(world, "tx-A", 30)
    collector = _collector(world, "tx-A")

    items = collector._collect_document_metrics() + collector._collect_action_verification()

    counts = [i for i in items if i.metric_name in ("finding_production", "assumption_logging")]
    assert len(counts) == 2
    assert all(not ({"uncertainty", "change"} & set(i.supports_vectors)) for i in counts)


def test_a_database_old_enough_to_lack_transaction_id_falls_back_to_the_session_instead_of_raising(world):
    """Counting the session beats an OperationalError that loses every observation in the method."""
    world.db.conn.execute("DROP TABLE assumptions")
    world.db.conn.execute("CREATE TABLE assumptions (id TEXT PRIMARY KEY, session_id TEXT)")
    world.db.conn.execute("INSERT INTO assumptions VALUES ('a1', ?)", (world.sid,))
    world.db.conn.commit()

    item = _item(_collector(world, "tx-A")._collect_action_verification(), "assumption_logging")

    assert item.raw_value["scope"] == "session" and item.raw_value["assumptions_logged"] == 1


# ── decisions: the same class, ruled the same way (David, 2026-10-04) ───────


def _log_decisions(world, tx, n):
    for i in range(n):
        world.db.log_decision(world.pid, world.sid, f"{tx} choice {i}", f"{tx} rationale {i}", transaction_id=tx)


def test_decisions_are_counted_in_the_transaction_not_the_session(world):
    _log_decisions(world, "tx-A", 5)
    _log_decisions(world, "tx-B", 1)

    item = _item(_collector(world, "tx-B")._collect_action_verification(), "decision_documentation")

    assert item.raw_value["decisions_logged"] == 1
    assert item.value == pytest.approx(1 / 3)
    assert item.raw_value["scope"] == "transaction"


def test_a_transaction_that_logged_no_decisions_emits_none(world):
    _log_decisions(world, "tx-A", 9)

    items = _collector(world, "tx-B")._collect_action_verification()

    assert [i for i in items if i.metric_name == "decision_documentation"] == []


def test_decision_documentation_keeps_its_vectors(world):
    """Control: the ruling changed the window, not what it supports (context, signal)."""
    _log_decisions(world, "tx-A", 3)

    item = _item(_collector(world, "tx-A")._collect_action_verification(), "decision_documentation")

    assert item.supports_vectors == ["context", "signal"]
