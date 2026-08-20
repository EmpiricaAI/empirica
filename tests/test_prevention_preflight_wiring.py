"""PREFLIGHT exposure emission — the producer half the pipeline never had.

Until wiring.py, `emit_prevention_exposure` had zero production call sites and
the live DB held zero rows ever: a wired oracle over an unwired emitter (the
existence-vs-function retraction, 2026-08-12). These tests prove rows are
born at pattern-surfacing time and that the oracle can advance them down BOTH
paths — `prevented` and `failed` — which is the row proof ecodex's EXP-SHADOW
pilot is blocked on.

Same in-memory harness as test_prevention_oracle: no ambient DB, no box.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.prevention import apply_prevention_detection
from empirica.core.prevention.wiring import EXPOSURE_CLASSES, emit_preflight_exposures
from empirica.data.migrations.migrations import (
    migration_058_prevention_events,
    migration_059_prevention_outcome_family,
)


def _db():
    conn = sqlite3.connect(":memory:")
    migration_058_prevention_events(conn.cursor())
    migration_059_prevention_outcome_family(conn.cursor())
    conn.execute(
        "CREATE TABLE mistakes_made (id INTEGER PRIMARY KEY, session_id TEXT, goal_id TEXT, created_timestamp REAL)"
    )
    conn.execute(
        "CREATE TABLE session_dead_ends (id INTEGER PRIMARY KEY, session_id TEXT, "
        "goal_id TEXT, subtask_id TEXT, created_timestamp REAL)"
    )
    conn.commit()
    return types.SimpleNamespace(conn=conn)


PATTERNS = {
    "dead_ends": [{"id": "de1", "approach": "the doomed approach"}],
    "prior_mistakes": [{"mistake": "shipped without negative control"}],  # no id → content hash
    "lessons": [{"lesson_id": "les1", "name": "survival matrix"}],
    "relevant_findings": [{"id": "f1"}],  # knowledge, NOT an exposure class
    "retrieved_from": {"project_id": "p"},
}


def _rows(db):
    return db.conn.execute(
        "SELECT pattern_key, subject_key, acknowledged, outcome FROM prevention_events ORDER BY pattern_key"
    ).fetchall()


def test_surfaced_antipatterns_become_exposed_rows():
    db = _db()
    written = emit_preflight_exposures(db, "sess1", "tx1", PATTERNS)
    assert written == 3, "one row per anti-pattern class item; findings are knowledge, not exposures"
    rows = _rows(db)
    assert all(r[3] == "exposed" for r in rows)
    assert all(r[2] == 1 for r in rows), "injection into context = acknowledged (v1 co-design semantics)"
    assert all(r[1] == "session:sess1" for r in rows)
    classes = {r[0].split(":")[0] for r in rows}
    assert classes == set(EXPOSURE_CLASSES)


def test_reemission_dedupes_within_session():
    db = _db()
    assert emit_preflight_exposures(db, "sess1", "tx1", PATTERNS) == 3
    assert emit_preflight_exposures(db, "sess1", "tx2", PATTERNS) == 0, "re-surfacing is retrieval, not a new exposure"
    # A different session is a different subject — emits again.
    assert emit_preflight_exposures(db, "sess2", "tx3", PATTERNS) == 3


def test_a_session_mistake_no_longer_fails_a_subjectless_treatment_row():
    """This test used to assert the exact defect, and its docstring said why.

    It reasoned that NULL-goal rows must match session-scoped, because under a
    strict goal_id match they could never fail — "a thumb on the experiment's
    scale". The reasoning was right and the remedy was the heavier thumb: for a
    practitioner logging mistakes at a normal rate, "any failure in this
    session" is almost always true. Measured across 218 live events, every one
    NULL-goal: 215 resolved `failed` and `prevented` was recorded zero times
    ever. The predicate was reading artifact volume as recurrence.

    Both thumbs are answered by declining the verdict rather than choosing one.
    """
    db = _db()
    emit_preflight_exposures(db, "sess1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    db.conn.execute(
        "INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess1', NULL, ?)",
        (exposed_at + 60,),
    )
    db.conn.commit()

    # The window must elapse first, and that ordering is deliberate: a
    # subjectless row must not be declared unmeasurable while a subject could
    # still arrive. goals-create binds mid-transaction, so verdict-before-window
    # would foreclose the very binding that makes the row measurable.
    window_s = db.conn.execute("SELECT MIN(window_s) FROM prevention_events").fetchone()[0]
    assert apply_prevention_detection(db, "sess1", now=exposed_at + 120) == 0, "inside the window, nothing resolves"

    advanced = apply_prevention_detection(db, "sess1", now=exposed_at + window_s + 1)
    assert advanced == 3
    outcomes = {r[0] for r in db.conn.execute("SELECT outcome FROM prevention_events").fetchall()}
    assert outcomes == {"unmeasurable"}, "an unrelated session mistake is not this exposure's failure"


def test_the_failed_path_works_on_the_bound_subject():
    """The precondition again: a failure ON THE SUBJECT still fails the row."""
    db = _db()
    emit_preflight_exposures(db, "sess1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    db.conn.execute("UPDATE prevention_events SET goal_id = 'goal_A', subject_key = 'goal:goal_A'")
    db.conn.execute(
        "INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess1', 'goal_A', ?)",
        (exposed_at + 60,),
    )
    db.conn.commit()

    assert apply_prevention_detection(db, "sess1", now=exposed_at + 120) == 3
    outcomes = {r[0] for r in db.conn.execute("SELECT outcome FROM prevention_events").fetchall()}
    assert outcomes == {"failed"}


def test_a_subjectless_treatment_row_is_unmeasurable_not_prevented():
    """These rows used to resolve `prevented` on an absence nobody scoped for.

    This test previously asserted exactly that, and it was the OTHER thumb on
    the experiment's scale: with no bound subject there was no place to look for
    the warned-about failure, so "no failure found" carried no information. An
    absence you never scoped for is not evidence of prevention.

    The treatment arm now declines the verdict instead of guessing either way.
    """
    db = _db()
    emit_preflight_exposures(db, "sess1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    window_s = db.conn.execute("SELECT MIN(window_s) FROM prevention_events").fetchone()[0]

    advanced = apply_prevention_detection(db, "sess1", now=exposed_at + window_s + 1)
    assert advanced == 3
    outcomes = {r[0] for r in db.conn.execute("SELECT outcome FROM prevention_events").fetchall()}
    assert outcomes == {"unmeasurable"}


def test_the_prevented_path_works_once_a_subject_is_bound():
    """The precondition, asserted alongside — or the test above just records a loss.

    A guard that says "this no longer resolves" without showing what restores it
    reads as a capability removed. Binding a subject is what `goals-create` does
    via `bind_prevention_subjects`, and it makes the prevented path reachable
    again on a real scope.
    """
    db = _db()
    emit_preflight_exposures(db, "sess1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    window_s = db.conn.execute("SELECT MIN(window_s) FROM prevention_events").fetchone()[0]
    db.conn.execute("UPDATE prevention_events SET goal_id = 'goal_A', subject_key = 'goal:goal_A'")
    db.conn.commit()

    assert apply_prevention_detection(db, "sess1", now=exposed_at + window_s + 1) == 3
    outcomes = {r[0] for r in db.conn.execute("SELECT outcome FROM prevention_events").fetchall()}
    assert outcomes == {"prevented"}, "with a subject, no failure on it IS evidence"


def test_window_still_open_stays_exposed():
    """Absence is not yet evidence — inside the window nothing advances."""
    db = _db()
    emit_preflight_exposures(db, "sess1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    assert apply_prevention_detection(db, "sess1", now=exposed_at + 60) == 0
    outcomes = {r[0] for r in db.conn.execute("SELECT outcome FROM prevention_events").fetchall()}
    assert outcomes == {"exposed"}


def test_goal_scoped_rows_keep_goal_matching():
    """The NULL-goal session-scope branch must NOT widen goal-bound rows: a
    mistake on a DIFFERENT goal does not fail a goal-scoped exposure."""
    db = _db()
    db.conn.execute(
        "INSERT INTO prevention_events "
        "(session_id, transaction_id, created_timestamp, pattern_key, subject_key, "
        "goal_id, exposed_at, acknowledged, outcome, outcome_family, window_s) "
        "VALUES ('sess1', 'tx', 1000.0, 'P', 'subj', 'goal_A', 1000.0, 1, 'exposed', 'prevention', 3600)"
    )
    db.conn.execute(
        "INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess1', 'goal_B', 1060.0)"
    )
    db.conn.commit()
    apply_prevention_detection(db, "sess1", now=1200.0)
    outcome = db.conn.execute("SELECT outcome FROM prevention_events").fetchone()[0]
    assert outcome == "exposed", "a goal_B mistake must not fail a goal_A exposure"


def test_emission_is_fail_open():
    """A broken DB must never take down PREFLIGHT."""
    broken = types.SimpleNamespace(conn=None)
    assert emit_preflight_exposures(broken, "s", "t", PATTERNS) == 0
    assert emit_preflight_exposures(_db(), "s", "t", None) == 0
    assert emit_preflight_exposures(_db(), "s", "t", {"dead_ends": "not-a-list"}) == 0


# ─── EXP-SHADOW control arm (spec §6, ecodex prop_ualvtcxj) ─────────────


def test_shadow_mode_records_hidden_rows_and_suppresses_delivery(monkeypatch):
    """Control arm: rows exist (shadow=true, acknowledged=false — nothing was
    delivered) and the anti-pattern classes are stripped from what reaches the
    AI, while knowledge classes pass through untouched."""
    from empirica.core.prevention.wiring import suppress_exposure_classes

    db = _db()
    monkeypatch.setenv("EMPIRICA_PREVENTION_SHADOW", "1")
    patterns = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in PATTERNS.items()}
    written = emit_preflight_exposures(db, "ctl1", "tx1", patterns)
    assert written == 3
    rows = db.conn.execute("SELECT shadow, acknowledged FROM prevention_events").fetchall()
    assert all(r == (1, 0) for r in rows), "shadow rows are recorded and never acknowledged"

    suppressed = suppress_exposure_classes(patterns)
    assert all(suppressed[cls] == [] for cls in EXPOSURE_CLASSES)
    assert suppressed["relevant_findings"], "the experiment withholds WARNINGS, not knowledge"


def test_shadow_rows_close_at_window_without_acknowledgement(monkeypatch):
    """The base-rate cell: a shadow row with no failure closes at window
    elapse even though acknowledged is structurally false — under the
    acknowledged-only rule the control arm could never produce its
    no-failure measurement and the ATE would have no denominator."""
    db = _db()
    monkeypatch.setenv("EMPIRICA_PREVENTION_SHADOW", "1")
    emit_preflight_exposures(db, "ctl1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    window_s = db.conn.execute("SELECT MIN(window_s) FROM prevention_events").fetchone()[0]

    assert apply_prevention_detection(db, "ctl1", now=exposed_at + window_s + 1) == 3
    rows = db.conn.execute("SELECT outcome, shadow FROM prevention_events").fetchall()
    assert all(r == ("prevented", 1) for r in rows), "no-failure-within-W, disambiguated by the shadow flag"


def test_shadow_rows_fail_like_treatment_rows(monkeypatch):
    """Failure incidence must be measurable identically in both arms."""
    db = _db()
    monkeypatch.setenv("EMPIRICA_PREVENTION_SHADOW", "1")
    emit_preflight_exposures(db, "ctl1", "tx1", PATTERNS)
    exposed_at = db.conn.execute("SELECT MIN(exposed_at) FROM prevention_events").fetchone()[0]
    db.conn.execute(
        "INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('ctl1', NULL, ?)",
        (exposed_at + 60,),
    )
    db.conn.commit()
    assert apply_prevention_detection(db, "ctl1", now=exposed_at + 120) == 3
    outcomes = {r[0] for r in db.conn.execute("SELECT outcome FROM prevention_events").fetchall()}
    assert outcomes == {"failed"}
