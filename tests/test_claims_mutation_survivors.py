"""Tests for the mutants of empirica/core/claims.py that the existing suite could not kill (goal c8e5c548, task R4).

A hand-rolled AST mutation pass over claims.py (215 mutants: flipped comparisons, swapped operators, negated conditions, constants + 1) was run
against the ten claims test files in a throwaway worktree. 189 were killed; 26 survived. Each test below is named for the behaviour a
survivor showed to be unpinned, and each one fails if that mutant is applied. Survivors that are equivalent mutants (the `except` path of
`int(row[0]) + 1 if row and row[0] is not None`, the `maxsplit` of an already-unique split, `held or 0` for a group that cannot have NULL
held with a decided count) are not tested: there is no behaviour to pin.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core import claims as C
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def _declare(db, session_id, transaction_id, *claims):
    items = [{"claim": text, "grounding": grounding, "ref": "x"} for text, grounding in claims]
    return C.declare(db, session_id=session_id, transaction_id=transaction_id, claims=items)


# ── counts ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_count_of_zero_is_a_count_not_a_missing_one():
    """The docstring's own point: 0 is 'I measured and found nothing' and must stay distinct from None. (`0 <= n` -> `0 < n`)"""
    assert C._normalize_count(0) == 0
    assert C._normalize_count("0 readers") == 0


def test_the_count_ceiling_is_inclusive_and_exact():
    """(`<= _MAX_COUNT` -> `<`, and `2**53` -> a larger constant)"""
    assert C._normalize_count(2**53) == 2**53
    assert C._normalize_count(2**53 + 1) is None
    assert C._normalize_count(-1) is None


# ── persistence and numbering ───────────────────────────────────────────────────────────────────────────────────


def test_declared_claims_are_committed_not_just_visible_to_the_connection_that_wrote_them(tmp_path):
    """(`if stored: commit` -> `if not stored`). Another connection only sees committed rows."""
    path = str(tmp_path / "durable.db")
    d = SessionDatabase(db_path=path)
    try:
        _declare(d, "s1", "t1", ("a claim", "read"))
        other = sqlite3.connect(path)
        try:
            assert other.execute("SELECT COUNT(*) FROM transaction_claims").fetchone()[0] == 1
        finally:
            other.close()
    finally:
        d.close()


def test_claim_numbering_restarts_in_each_transaction_of_the_same_session(db):
    """(`if transaction_id:` negated in _next_claim_index)"""
    _declare(db, "s1", "txA", ("a1", "read"), ("a2", "ran"))
    _declare(db, "s1", "txB", ("b1", "read"), ("b2", "read"))
    assert sorted(c["claim_index"] for c in C._all_claims(db, "s1", "txB")) == [1, 2]


def test_a_transaction_only_sees_its_own_claims(db):
    """(`if transaction_id:` negated in _query_claims)"""
    _declare(db, "s1", "txA", ("a1", "read"))
    _declare(db, "s1", "txB", ("b1", "read"), ("b2", "ran"))
    assert [c["claim"] for c in C._all_claims(db, "s1", "txA")] == ["a1"]
    assert len(C._all_claims(db, "s1", "txB")) == 2


def test_the_numbering_falls_back_to_one_when_the_query_fails():
    """(`return 1` in the except -> 2). Fail-soft by design: numbering must not be able to fail a CHECK."""

    class Broken:
        class conn:
            @staticmethod
            def execute(*_a, **_k):
                raise sqlite3.OperationalError("boom")

    assert C._next_claim_index(Broken(), "s", "t") == 1


def test_open_claims_are_only_those_without_a_verdict(db):
    """(`only_open=True` -> False in _open_claims)"""
    stored = _declare(db, "s1", "t1", ("decided", "read"), ("undecided", "read"))
    db.conn.execute("UPDATE transaction_claims SET verdict = 'held' WHERE id = ?", (stored[0]["id"],))
    db.conn.commit()
    assert [c["claim"] for c in C._open_claims(db, "s1", "t1")] == ["undecided"]


# ── adjudication echoes ─────────────────────────────────────────────────────────────────────────────────────────


def test_verdicts_with_nothing_declared_echo_zeros_and_say_they_were_dropped(db):
    """(constants in the empty-block dict: 0 -> 1)"""
    out = C.adjudicate(db, session_id="s1", transaction_id="t1", adjudications=[{"index": 1, "verdict": "held"}])
    assert (out["declared"], out["held"], out["refuted"], out["untested"]) == (0, 0, 0, 0)
    assert out["gaps"] == []


def test_note_skipped_derives_the_submitted_count_when_the_caller_gave_none():
    """(`out.get('declared', 0) + len(skipped)`: `+` -> `-`, `is not None` -> `is None`, default 0 -> 1)"""
    out = {"declared": 2}
    C._note_skipped(out, [{"position": 3}], None)
    assert out["submitted"] == 3
    empty: dict = {}
    C._note_skipped(empty, [{"position": 1}], None)
    assert empty["submitted"] == 1, "no declared key: the default is 0"
    given: dict = {"declared": 2}
    C._note_skipped(given, [{"position": 3}], 0)
    assert given["submitted"] == 0, "an explicit 0 is a count, not a missing one"


def test_nothing_stored_but_something_skipped_echoes_a_zero_declaration():
    """(`'declared': 0` -> 1 in the skipped-only echo)"""
    out = C.summarize_for_check([], [{"position": 1, "reason": "no claim text"}])
    assert out is not None
    assert out["declared"] == 0 and out["claims"] == [] and out["weakly_grounded"] == 0


def test_unlabelled_claims_are_counted_and_labelled_ones_do_not_add_the_key():
    """(`grounding is None` -> `is not None`, and the `if ungrounded:` guard negated)"""
    unlabelled = {"id": "c1", "claim": "x", "grounding": None}
    labelled = {"id": "c2", "claim": "y", "grounding": "read", "ref": "r"}
    mixed = C.summarize_for_check([unlabelled, labelled])
    assert mixed is not None and mixed["unlabelled"] == 1
    only_labelled = C.summarize_for_check([labelled])
    assert only_labelled is not None and "unlabelled" not in only_labelled


# ── hold rates and the confidence figure ────────────────────────────────────────────────────────────────────────


def _seed_verdicts(db, grounding, held, refuted):
    rows = [(grounding, "held")] * held + [(grounding, "refuted")] * refuted
    for i, (g, verdict) in enumerate(rows):
        db.conn.execute(
            "INSERT INTO transaction_claims "
            "(session_id, transaction_id, claim_index, claim, grounding, verdict, declared_timestamp) "
            "VALUES ('s', ?, ?, 'c', ?, ?, 0.0)",
            (f"t{i}", i + 1, g, verdict),
        )
    db.conn.commit()


def test_the_rate_is_rounded_to_three_places(db):
    """(`round(..., 3)` -> 4). 7 of 7 held at the read prior .974: (7 + 20*.974)/27 = 0.98074..."""
    _seed_verdicts(db, "read", held=7, refuted=0)
    assert C.hold_rates(db)["read"]["rate"] == 0.981


def test_prior_weight_is_rounded_to_three_places(db):
    """(`round(prior_weight, 3)` -> 4). 20/27 = 0.7407..."""
    _seed_verdicts(db, "read", held=7, refuted=0)
    assert C.hold_rates(db)["read"]["prior_weight"] == 0.741


def test_a_practice_becomes_the_source_exactly_at_a_quarter_prior_weight(db):
    """(`prior_weight <= PRACTICE_MAX_PRIOR_WEIGHT` -> `<`). MIN_DECIDED / (decided + MIN_DECIDED) is exactly 0.25 at 60 decided."""
    _seed_verdicts(db, "ran", held=59, refuted=1)  # 60 decided
    assert C.hold_rates(db)["ran"]["prior_weight"] == 0.25
    assert C.hold_rates(db)["ran"]["source"] == "practice"


def test_one_fewer_decided_claim_is_still_blended(db):
    """Control for the boundary above."""
    _seed_verdicts(db, "retrieved", held=58, refuted=1)  # 59 decided
    assert C.hold_rates(db)["retrieved"]["source"] == "blended"


def test_a_grounding_missing_from_the_mapping_falls_back_to_its_prior_with_zero_decided():
    """(`'decided': 0` -> 1 in _rate_of)"""
    entry = C._rate_of({}, "read")
    assert entry["decided"] == 0 and entry["source"] == "prior"
    assert entry["rate"] == C.PRIOR_HOLD_RATES["read"]


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ({"practice"}, "come from this practice's own"),
        ({"blended"}, "partly this practice's own"),
        ({"practice", "blended"}, "partly this practice's own"),
        ({"prior"}, "too few verdicts"),
    ],
)
def test_the_assumption_text_says_where_the_rates_came_from(sources, expected):
    """(`'practice' in sources or 'blended' in sources` -> `and`: a blended-only set read as the prior's wording)"""
    assert expected in C._assumption_text(sources)


def test_the_confidence_value_is_rounded_to_three_places(db):
    """(`round(value, 3)` -> 4). Two `read` claims at the .974 prior: .974 * .974 = 0.948676"""
    stored = [
        {"id": "a", "claim": "x", "grounding": "read", "ref": "r"},
        {"id": "b", "claim": "y", "grounding": "read", "ref": "r"},
    ]
    figure = C.claim_confidence(stored, C.hold_rates(db))
    assert figure is not None and figure["value"] == 0.949
