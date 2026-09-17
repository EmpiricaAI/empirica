"""`grounding` says HOW I know. It says nothing about OVER WHAT, or HOW MANY.

David-directed 2026-09-17: *"better scope management and measurement thereof would
help then, let core know along with everything else you are doing."*

Relayed by empirica-cortex with the argument for why the obvious lever is wrong.
David had offered to raise the confidence gate; **it would have caught nothing.**
Their week's failures were all high confidence on claims that were TRUE and whose
verdicts all adjudicated `held`:

  · "the status filter answers typos with a zero" — ran, held, never asked WHO
    currently sends invalid values → 400'd a live pane
  · "source_user_id is unspoofable" — read, held, never asked HOW MANY THINGS IT
    NAMES → collapsed 7 practices into one outbox

The gate asks *how sure am I*; the defect was *is that the right question*.

**Then refined by empirica-mesh-support before this shipped**, which is why there
are two fields. Scope alone would have caught none of their three cases, because
they would have written the scope they INTENDED and believed they had measured —
and two of the three measured *nothing at all*:

  claimed                             what the command actually walked
  "5,783 artifacts across practices"  a SQLite count; artifacts are git notes
  "every practice has 0 note refs"    a glob that cannot cross path components,
                                      matching 0 of 34,890 real refs
  "no repo exists on the forge"       the notes-remote config key; 17 existed

`count` is what makes those visible. *scope: "all 21 practices", count: 0* against
a store holding 34,890 refs is absurd on sight.
"""

from __future__ import annotations

import uuid

import pytest

from empirica.core import claims as C
from empirica.data.session_database import SessionDatabase

SESSION = str(uuid.uuid4())
TX = str(uuid.uuid4())


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


# ─── the count normaliser: None and 0 must not collapse ───────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (1, 1), (34890, 34890), ("5", 5), (None, None), ("", None), ("many", None), (-3, None)],
)
def test_count_normalisation(raw, expected):
    assert C._normalize_count(raw) == expected


def test_true_is_not_a_count():
    """`bool` is an `int` in Python, so `count: True` would store 1.

    A claim declaring a boolean has not measured a population, and storing 1 would
    invent a measurement of one thing.
    """
    assert C._normalize_count(True) is None
    assert C._normalize_count(False) is None


def test_zero_and_absent_stay_distinct():
    """The whole signal lives in this distinction.

    None means "not a measurement over a population". 0 means "I measured and
    found nothing" — and 0 is the interesting one, because it is most often the
    answer of an enumerator that walked the wrong thing.
    """
    assert C._normalize_count(None) is None
    assert C._normalize_count(0) == 0


# ─── the suspicion detector ───────────────────────────────────────────


def test_a_zero_count_over_a_named_population_is_suspect():
    """mesh-support's glob case, as an assertion: 0 refs across 21 practices."""
    why = C.scope_is_suspect("all 21 practices", 0)

    assert why and "walk the wrong thing" in why
    assert "measured nothing" in why


def test_a_scope_with_no_count_is_flagged_more_softly():
    why = C.scope_is_suspect("orchestration_proposals", None)

    assert why and "how many did it return" in why


def test_a_coherent_scope_and_count_is_not_flagged():
    """Positive control.

    A detector that fired on every scoped claim would make the field a tax rather
    than a signal, and this module's stated design is report-first — two mechanisms
    here already died of over-firing.
    """
    assert C.scope_is_suspect("the 330 rows I sampled", 330) is None
    assert C.scope_is_suspect("one file: cli_core.py", 1) is None


def test_an_unscoped_claim_is_not_itself_suspect():
    """No scope is an omission, reported separately. It is not a contradiction.

    Conflating them would put every unscoped claim — including ones where a scope
    is meaningless, like a statement about a single file — into the loud channel.
    """
    assert C.scope_is_suspect(None, None) is None
    assert C.scope_is_suspect(None, 0) is None


# ─── persistence ──────────────────────────────────────────────────────


def test_scope_and_count_round_trip(db):
    stored = C.declare(
        db,
        session_id=SESSION,
        transaction_id=TX,
        claims=[
            {"claim": "every practice has 0 note refs", "grounding": "ran", "scope": "all 21 practices", "count": 0}
        ],
    )

    assert stored[0]["scope"] == "all 21 practices"
    assert stored[0]["measured_count"] == 0

    row = db.conn.execute(
        "SELECT scope, measured_count FROM transaction_claims WHERE id = ?", (stored[0]["id"],)
    ).fetchone()
    assert row[0] == "all 21 practices"
    assert row[1] == 0, "0 must persist as 0, not as NULL — the distinction is the signal"


def test_a_claim_without_scope_still_persists(db):
    """Advisory, not required. A claim is worth more recorded than rejected."""
    stored = C.declare(db, session_id=SESSION, transaction_id=TX, claims=[{"claim": "x", "grounding": "read"}])

    assert stored[0]["scope"] is None
    assert stored[0]["measured_count"] is None


def test_measured_count_is_accepted_under_either_key(db):
    """`count` is what a practitioner types; `measured_count` is the column."""
    stored = C.declare(
        db,
        session_id=SESSION,
        transaction_id=TX,
        claims=[{"claim": "a", "grounding": "ran", "scope": "s", "measured_count": 7}],
    )

    assert stored[0]["measured_count"] == 7


# ─── the CHECK echo, where it can still change behaviour ──────────────


def test_check_reports_unscoped_claims():
    out = C.summarize_for_check(
        [
            {"index": 1, "claim": "a", "grounding": "ran", "scope": "x", "measured_count": 5},
            {"index": 2, "claim": "b", "grounding": "read"},
        ]
    )

    assert out["unscoped"] == 1
    assert "OVER WHAT" in out["scope_note"]
    assert "adjudicate `held`" in out["scope_note"], "the note must say why a confidence gate misses this"


def test_check_surfaces_the_suspect_pairing_with_its_index():
    """The index is the practitioner's addressing space — a reason with no index
    cannot be acted on when three claims are declared."""
    out = C.summarize_for_check(
        [{"index": 1, "claim": "a", "grounding": "ran", "scope": "all 21 practices", "measured_count": 0}]
    )

    assert out["suspect_scopes"][0]["index"] == 1
    assert "walk the wrong thing" in out["suspect_scopes"][0]["why"]
    assert "34,890" in out["suspect_scope_note"], "the note carries the measured case, not a generality"


def test_fully_scoped_claims_produce_no_scope_noise():
    """Positive control for both notes at once."""
    out = C.summarize_for_check(
        [{"index": 1, "claim": "a", "grounding": "ran", "scope": "the 330 rows", "measured_count": 330}]
    )

    assert "unscoped" not in out
    assert "scope_note" not in out
    assert "suspect_scopes" not in out


def test_the_postflight_read_path_returns_the_new_columns(db):
    """A projection that omits a column its own consumer reads is the shape where
    every test asserts on the behaviour the field enables and none reads it back
    off the row — the module's own comment says so about `verdict_evidence`."""
    C.declare(
        db,
        session_id=SESSION,
        transaction_id=TX,
        claims=[{"claim": "a", "grounding": "ran", "scope": "pop", "count": 3}],
    )

    rows = C._query_claims(db, SESSION, TX, only_open=True)

    assert rows[0]["scope"] == "pop"
    assert rows[0]["measured_count"] == 3


def test_transaction_claims_is_migratable():
    """Migration 071 failed on every install until this was fixed.

    `add_column_if_missing` guards on an allowlist that did not contain
    `transaction_claims`, so the ALTER raised "Invalid table name" — which reads as
    a bad migration rather than an incomplete allowlist. The allowlist was also TWO
    hand-maintained copies, so adding the table to one would have half-worked.
    """
    from empirica.data.migrations.migration_runner import MIGRATABLE_TABLES

    assert "transaction_claims" in MIGRATABLE_TABLES
