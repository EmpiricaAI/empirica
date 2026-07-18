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
        exposed = db.conn.execute(
            "SELECT id, goal_id, subtask_id, exposed_at, acknowledged, window_s "
            "FROM prevention_events WHERE session_id = ? AND outcome = 'exposed'",
            (session_id,),
        ).fetchall()
        if not exposed:
            return 0

        updated = 0
        for row_id, goal_id, subtask_id, exposed_at, acknowledged, window_s in exposed:
            since = exposed_at or 0
            # Causal order: only failures logged AFTER the exposure count.
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

            if failure:
                outcome = "failed"  # exposed, but the warned-about failure still landed
            elif acknowledged and window_s is not None and (now - since) >= window_s:
                outcome = "prevented"  # window elapsed, acknowledged, no failure = prevention
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
