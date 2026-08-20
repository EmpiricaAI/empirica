"""Prevention detection at POSTFLIGHT — the positive mirror of ``apply_blindspot_regret``.

For each ``exposed`` prevention_event, ask the causal-ordering question the regret
loop asks, with opposite polarity:

  - a same-subject **mistake / dead-end logged AFTER** ``exposed_at`` → ``failed``
    (a *measured miss*; kept, NOT discarded — it is the exposed-arm failure the
    causal ATE needs).
  - else, once the observation window ``W`` has fully elapsed with no such failure
    **and** the prior was acknowledged → ``prevented`` (the measured prevention).
  - else → stay ``exposed`` (window still open; absence is not yet evidence — §5).

The ``created_timestamp > exposed_at`` guard enforces the causal order (the failure
came after the exposure), exactly mirroring ``apply_blindspot_regret``.

Fail-open: the prevention machinery must never affect POSTFLIGHT.
"""

from __future__ import annotations

import time


def apply_prevention_detection(db, session_id: str, *, now: float | None = None) -> int:
    """Advance this session's ``exposed`` prevention_events at POSTFLIGHT.

    ``now`` is injectable so tests can simulate an elapsed observation window
    (production passes wall-clock). Returns the number of rows advanced, or 0 on
    any error (including absent tables on a partial DB).
    """
    try:
        now = now if now is not None else time.time()
        # Only the 'prevention' family is resolved here: its failure signal is a
        # mistake / dead-end. The 'fabrication' family has a different failure
        # signal (a detected fabrication) and MUST NOT be resolved by this pass,
        # or a fabrication exposure would get a FALSE 'prevented' verdict from the
        # mere absence of a mistake. Those rows await a distinct oracle (spec §6 Q4).
        exposed = db.conn.execute(
            "SELECT id, goal_id, subtask_id, exposed_at, acknowledged, window_s, shadow "
            "FROM prevention_events WHERE session_id = ? AND outcome = 'exposed' "
            "AND (outcome_family = 'prevention' OR outcome_family IS NULL)",
            (session_id,),
        ).fetchall()
        if not exposed:
            return 0

        updated = 0
        for row_id, goal_id, subtask_id, exposed_at, acknowledged, window_s, shadow in exposed:
            since = exposed_at or 0
            # Causal order: only failures logged AFTER the exposure count.
            #
            # SUBJECT-SCOPED ONLY. The predecessor here matched NULL-goal rows
            # against ANY mistake or dead-end in the session, on the reasoning
            # that `goal_id = NULL` matches nothing in SQL so PREFLIGHT
            # exposures would otherwise be structurally unable to fail — "a
            # thumb on the experiment's scale". The concern was right and the
            # remedy was the heavier thumb: for a practitioner logging mistakes
            # at a normal rate, "any failure in this session" is almost always
            # true. Measured across 218 events, every one NULL-goal: 215
            # resolved `failed` and `prevented` was recorded zero times ever.
            # The predicate was reading artifact volume as recurrence.
            #
            # Both directions are answered instead by deferring the verdict.
            # goals-create binds a subject the moment one exists
            # (`bind_prevention_subjects`), and an exposure that never acquires
            # one resolves `unmeasurable` below rather than falling into either
            # biased branch. A subjectless row is not evidence of prevention and
            # not evidence of failure; it is a row we cannot adjudicate, and
            # saying so is the only honest option available.
            if shadow:
                # CONTROL ARM asks a different question and so needs a different
                # scope. A treatment row asks "did the warned-about failure recur
                # ON THIS SUBJECT" — subject-scoped by definition. A shadow row
                # was never delivered; it measures the BASE RATE of failure in a
                # session like this one, absent exposure, and incidence is a
                # session-level quantity. Scoping it to a subject would make the
                # control arm structurally unable to observe the thing it exists
                # to observe, and the ATE needs both cells measured the same way.
                #
                # This distinction is not in the plan that produced this change;
                # the existing wiring tests caught it when the subject-scoping
                # rewrite silently turned every shadow row `unmeasurable`.
                failure = db.conn.execute(
                    "SELECT 1 FROM mistakes_made WHERE session_id = ? AND created_timestamp > ? LIMIT 1",
                    (session_id, since),
                ).fetchone()
                if not failure:
                    failure = db.conn.execute(
                        "SELECT 1 FROM session_dead_ends WHERE session_id = ? AND created_timestamp > ? LIMIT 1",
                        (session_id, since),
                    ).fetchone()
            elif goal_id is None:
                failure = None
            else:
                failure = db.conn.execute(
                    "SELECT 1 FROM mistakes_made WHERE session_id = ? AND goal_id = ? AND created_timestamp > ? LIMIT 1",
                    (session_id, goal_id, since),
                ).fetchone()
                if not failure:
                    failure = db.conn.execute(
                        "SELECT 1 FROM session_dead_ends WHERE session_id = ? "
                        "AND (goal_id = ? OR subtask_id = ?) AND created_timestamp > ? LIMIT 1",
                        (session_id, goal_id, subtask_id, since),
                    ).fetchone()

            window_elapsed = window_s is not None and (now - since) >= window_s
            if goal_id is None and not shadow and window_elapsed:
                # Never bound, window closed: no subject was ever available to
                # adjudicate against. Recording this as its own outcome is what
                # keeps it out of every rate rather than silently inflating one.
                outcome = "unmeasurable"
            elif failure:
                outcome = "failed"  # exposed, but the warned-about failure still landed
            elif (acknowledged or shadow) and window_elapsed:
                # Shadow (control-arm) rows were never delivered, so acknowledged
                # is structurally false — they close at window elapse regardless.
                # On a shadow row 'prevented' reads as "no failure within W,
                # UNEXPOSED": the base-rate cell, disambiguated by the shadow
                # flag the analysis conditions on (spec §6 no-control-arm row).
                outcome = "prevented"
            else:
                continue  # window still open — absence is not yet evidence (§5)

            db.conn.execute(
                "UPDATE prevention_events SET outcome = ?, outcome_at = ? WHERE id = ? AND outcome = 'exposed'",
                (outcome, now, row_id),
            )
            updated += 1

        if updated:
            db.conn.commit()
        return updated
    except Exception:
        return 0
