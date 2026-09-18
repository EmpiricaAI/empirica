"""Claim scope enforcement (David-directed 2026-09-18, via empirica-autonomy).

The failure class: a TRUE claim applied past the population it was measured
over. It adjudicates `held`, so no confidence gate can see it. Scope and count
existed since 071 but were advisory, and a count written the way people say it
("4 sites") was silently stored as NULL - after which the summary asked "how
many did it return?" of a claim that had said.
"""

from __future__ import annotations

import sqlite3

from empirica.core import claims


def test_counts_written_as_phrases_keep_their_number():
    assert claims._normalize_count("4 sites") == 4
    assert claims._normalize_count("46 of 46") == 46
    assert claims._normalize_count("0 readers; 3 comment mentions") == 0
    assert claims._normalize_count(12) == 12
    assert claims._normalize_count("several") is None
    assert claims._normalize_count(None) is None


def test_certifies_read_always_ran_only_with_scope_and_count():
    assert claims.certifies({"grounding": "read"})
    assert claims.certifies({"grounding": "ran", "scope": "tx claims since 071", "measured_count": 48})
    assert claims.certifies({"grounding": "ran", "scope": "a glob over 21 practices", "measured_count": 0})
    assert not claims.certifies({"grounding": "ran"})
    assert not claims.certifies({"grounding": "ran", "scope": "x"})
    assert not claims.certifies({"grounding": "ran", "scope": "  ", "measured_count": 3})
    assert not claims.certifies({"grounding": "retrieved", "scope": "x", "measured_count": 1})
    assert not claims.certifies({"grounding": "assumed"})


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE transaction_claims (id TEXT, session_id TEXT, transaction_id TEXT, "
            "claim_index INT, claim TEXT, grounding TEXT, ref TEXT, verdict TEXT, verdict_evidence TEXT, "
            "declared_timestamp REAL, adjudicated_timestamp REAL, scope TEXT, measured_count INTEGER)"
        )


def test_an_unparseable_count_is_reported_not_dropped():
    stored = claims.declare(
        _DB(),
        session_id="s",
        transaction_id="t",
        claims=[{"claim": "the sweep found them all", "grounding": "ran", "scope": "all hooks", "count": "several"}],
    )
    assert stored[0]["measured_count"] is None
    assert stored[0]["count_ignored"] == "several"
    summary = claims.summarize_for_check(stored)
    assert summary["count_ignored"] == [{"index": stored[0]["index"], "given": "several"}]
    assert summary["uncertified_ran"] == [stored[0]["index"]]


def test_a_phrase_count_is_stored_and_certifies():
    stored = claims.declare(
        _DB(),
        session_id="s",
        transaction_id="t",
        claims=[
            {
                "claim": "46 of 46 recent goals carry a tx id",
                "grounding": "ran",
                "scope": "goals, 7d",
                "count": "46 of 46",
            }
        ],
    )
    assert stored[0]["measured_count"] == 46
    assert "count_ignored" not in stored[0]
    assert claims.certifies(stored[0])
    assert "uncertified_ran" not in claims.summarize_for_check(stored)
