"""A verdict that could not be applied must SAY so, not be counted as untested.

Reported by mesh-support (prop_tr6icuvm) after it cost them three transactions
and two wrong diagnoses.

`adjudicate()` dropped unusable entries with a bare `continue`. The claims they
targeted then fell through to the bulk sweep and POSTFLIGHT reported "declared at
CHECK, never adjudicated — acted on, never checked" about claims the practitioner
HAD adjudicated, with evidence, in the payload.

That is the worst thing this mechanism can say falsely, because it is precisely
the signal it exists to produce. A wrong "you didn't verify" teaches the
practitioner to distrust a discipline that was working.

It also hid from diagnosis: mesh-support's two attempts blamed claim-index
bookkeeping — self-consistent and wrong, because the entries were dropped before
matching was ever attempted (they sent `adjudication` where the contract wants
`verdict`). A defect that survives two good-faith diagnoses from its own error
message is doing work to conceal itself.
"""

from __future__ import annotations

import sqlite3

from empirica.core import claims


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE transaction_claims ("
            " id TEXT, session_id TEXT, transaction_id TEXT, claim_index INT,"
            " claim TEXT, grounding TEXT, ref TEXT, verdict TEXT, verdict_evidence TEXT,"
            " adjudicated_timestamp REAL, created_timestamp REAL)"
        )
        self.conn.commit()

    def declare(self, n=2):
        for i in range(1, n + 1):
            self.conn.execute(
                "INSERT INTO transaction_claims VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,?)",
                (f"claim-id-{i:04d}-aaaa", "S1", "T1", i, f"claim {i}", "read", 1.0),
            )
        self.conn.commit()


def _adj(db, adjudications):
    return claims.adjudicate(db, session_id="S1", transaction_id="T1", adjudications=adjudications)


# ─── the reported defect ──────────────────────────────────────────────────


def test_wrong_key_names_are_reported_not_swallowed():
    """mesh-support's exact payload: `adjudication`/`note` instead of `verdict`/`evidence`."""
    db = _DB()
    db.declare(2)
    out = _adj(db, [{"claim": "claim 1", "adjudication": "held", "note": "checked it"}])

    assert out["adjudication"]["unmatched"] == 1
    assert out["adjudication"]["applied"] == 0
    assert out["adjudication"]["entries"][0]["reason"] == "missing_verdict"


def test_the_specific_confusion_is_named():
    """`keys_seen` covers the general case; naming the known confusion collapses
    three sessions of diagnosis into one glance."""
    db = _DB()
    db.declare(1)
    out = _adj(db, [{"claim": "claim 1", "adjudication": "held", "note": "x"}])

    confusions = out["adjudication"]["entries"][0]["likely_key_confusion"]
    assert "adjudication -> verdict" in confusions
    assert "note -> evidence" in confusions


def test_the_warning_connects_drops_to_the_untested_count():
    """The two facts are useless apart: the drop explains the untested number,
    and the untested number is what the drop will otherwise be misread as."""
    db = _DB()
    db.declare(2)
    out = _adj(db, [{"claim": "claim 1", "adjudication": "held"}])

    assert out["untested"] == 2
    assert "untested" in out["adjudication_warning"]


def test_unrecognized_verdict_is_distinguished_from_a_missing_one():
    """Different corrections: "you sent none" vs "you sent one I don't know"."""
    db = _DB()
    db.declare(1)
    out = _adj(db, [{"index": 1, "verdict": "probably-fine"}])

    assert out["adjudication"]["entries"][0]["reason"] == "unrecognized_verdict"


def test_index_pointing_at_nothing_is_reported():
    db = _DB()
    db.declare(2)
    out = _adj(db, [{"index": 99, "verdict": "held"}])

    assert out["adjudication"]["entries"][0]["reason"] == "no_matching_claim"


def test_a_clean_payload_reports_no_adjudication_block():
    """The block must be ABSENT when nothing was dropped — a warning that is
    always present is one nobody reads."""
    db = _DB()
    db.declare(2)
    out = _adj(db, [{"index": 1, "verdict": "held"}, {"index": 2, "verdict": "refuted"}])

    assert "adjudication" not in out
    assert "adjudication_warning" not in out
    assert out["held"] == 1 and out["refuted"] == 1 and out["untested"] == 0


def test_partial_application_counts_both_sides():
    db = _DB()
    db.declare(2)
    out = _adj(db, [{"index": 1, "verdict": "held"}, {"index": 2, "adjudication": "held"}])

    assert out["adjudication"]["applied"] == 1
    assert out["adjudication"]["unmatched"] == 1
    assert out["held"] == 1


# ─── the nested drop, found while reading the consumer ────────────────────


def test_the_no_claims_warning_reaches_the_retrospective():
    """adjudicate() reports `dropped_adjudications` when verdicts arrive with no
    claims to attach them to — and `_retro_adjudicate_claims` returned early on
    `declared == 0`, discarding the one branch written to prevent that silence.

    The guard was meant for the genuinely-empty case, so it must test emptiness,
    not `declared`.
    """
    from empirica.cli.command_handlers import _workflow_shared as ws

    db = _DB()  # no claims declared
    retro: dict = {}
    ws._retro_adjudicate_claims(db, "S1", "T1", [{"index": 1, "verdict": "held"}], retro)

    assert retro.get("claims", {}).get("dropped_adjudications") == 1


def test_genuinely_empty_stays_quiet():
    """The early return still has a job: nothing declared AND nothing submitted
    must add no noise to the retrospective."""
    from empirica.cli.command_handlers import _workflow_shared as ws

    db = _DB()
    retro: dict = {}
    ws._retro_adjudicate_claims(db, "S1", "T1", [], retro)

    assert retro == {}


def test_the_warning_is_promoted_for_declared_claims():
    from empirica.cli.command_handlers import _workflow_shared as ws

    db = _DB()
    db.declare(2)
    retro: dict = {}
    ws._retro_adjudicate_claims(db, "S1", "T1", [{"claim": "c1", "adjudication": "held"}], retro)

    assert "claim_adjudication_warning" in retro
