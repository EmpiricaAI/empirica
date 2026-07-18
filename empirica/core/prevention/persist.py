"""Persistence + aggregation for ``prevention_events`` (migration 058).

The positive-polarity sibling of ``blindspots/persist.py``: emit an EXPOSURE when
a known anti-pattern prior is surfaced on a subject, then let the POSTFLIGHT
detection pass (``detection.py``) advance it to ``prevented`` or ``failed``.

Per ``docs/architecture/PREVENTION_MEASUREMENT_SPEC.md`` (Leg A). Fail-open — a
persistence error must never affect the CHECK/POSTFLIGHT loop it observes.
"""

from __future__ import annotations

import time

# 30-day observation window (spec §5). Absence of a failure inside a shorter
# window is NOT prevention evidence. Per-pattern overridable; research Q1 may
# replace the fixed window with a hazard model.
DEFAULT_WINDOW_S = 30 * 24 * 3600

_EVENT_COLS = (
    "id",
    "session_id",
    "transaction_id",
    "created_timestamp",
    "pattern_key",
    "subject_key",
    "goal_id",
    "subtask_id",
    "author_practice",
    "beneficiary_practice",
    "exposed_at",
    "acknowledged",
    "shadow",
    "outcome",
    "outcome_at",
    "window_s",
    "provenance_ref",
    "outcome_family",
)


def emit_prevention_exposure(
    db,
    session_id,
    transaction_id,
    *,
    pattern_key,
    subject_key,
    goal_id=None,
    subtask_id=None,
    author_practice=None,
    beneficiary_practice=None,
    acknowledged=False,
    shadow=False,
    outcome_family="prevention",
    window_s=DEFAULT_WINDOW_S,
    provenance_ref=None,
    exposed_at=None,
) -> int:
    """Record one exposure of anti-pattern ``pattern_key`` on ``subject_key``.

    Row lands with ``outcome='exposed'``; the POSTFLIGHT detection pass advances
    it. ``shadow=True`` marks an EXP-SHADOW control-arm non-exposure.
    ``author_practice != beneficiary_practice`` is the beneficiary-independence
    (anti-collusion) signal.

    FAIL-OPEN — returns 1 on success, 0 on any error (including a missing table on
    an un-migrated DB). Never raises: the prevention machinery must not brick the
    loop it observes.
    """
    try:
        now = time.time()
        db.conn.execute(
            "INSERT INTO prevention_events "
            "(session_id, transaction_id, created_timestamp, pattern_key, subject_key, "
            "goal_id, subtask_id, author_practice, beneficiary_practice, exposed_at, "
            "acknowledged, shadow, outcome, outcome_family, window_s, provenance_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                transaction_id,
                now,
                pattern_key,
                subject_key,
                goal_id,
                subtask_id,
                author_practice,
                beneficiary_practice,
                exposed_at if exposed_at is not None else now,
                1 if acknowledged else 0,
                1 if shadow else 0,
                "exposed",
                outcome_family,
                window_s,
                provenance_ref,
            ),
        )
        db.conn.commit()
        return 1
    except Exception:
        return 0


def emit_fabrication_exposure(db, session_id, transaction_id, *, pattern_key, subject_key, **kwargs) -> int:
    """Emit a fabrication-class exposure (``outcome_family='fabrication'``).

    The 2nd outcome family (David's fabrication-detection-floor) — same emission
    surface as :func:`emit_prevention_exposure`. NOTE: fabrication rows are
    deliberately NOT resolved by the POSTFLIGHT prevention detector — its
    mistake/dead-end signal is the wrong one for a fabrication. They await a
    distinct grounding/verification oracle (deferred, spec §6 Q4). Fail-open.
    """
    return emit_prevention_exposure(
        db,
        session_id,
        transaction_id,
        pattern_key=pattern_key,
        subject_key=subject_key,
        outcome_family="fabrication",
        **kwargs,
    )


def read_prevention_events(db, session_id: str | None = None) -> list[dict]:
    """Read ``prevention_events`` (optionally session-scoped). ``[]`` on any error."""
    try:
        sql = f"SELECT {', '.join(_EVENT_COLS)} FROM prevention_events"
        params: tuple = ()
        if session_id:
            sql += " WHERE session_id = ?"
            params = (session_id,)
        cur = db.conn.execute(sql, params)
        return [dict(zip(_EVENT_COLS, row)) for row in cur.fetchall()]
    except Exception:
        return []


def aggregate_prevention_events(rows: list[dict], family: str | None = None) -> dict:
    """Aggregate into telemetry: totals, by-outcome, prevention rate, beneficiary-independent split.

    - **family** — when given, only rows of that ``outcome_family`` are aggregated
      (rows with no family default to ``'prevention'``). Lets callers split the
      prevention family from the fabrication-incidence family cleanly.
    - **prevention_rate** — of *resolved* events (prevented + failed), how many
      prevented. This is a raw rate, NOT the causal ATE (that is research's, and
      needs the shadow/control arm) — it is the exposed-arm numerator only.
    - **beneficiary_independent** — preventions where author_practice !=
      beneficiary_practice: the anti-collusion signal the currency is backed by.
      Endogenous (within-practice) preventions are discounted downstream.
    """
    if family is not None:
        rows = [r for r in (rows or []) if (r.get("outcome_family") or "prevention") == family]
    by_outcome: dict[str, int] = {}
    prevented_bi = 0
    prevented_total = 0
    for r in rows or []:
        oc = r.get("outcome") or "exposed"
        by_outcome[oc] = by_outcome.get(oc, 0) + 1
        if oc == "prevented":
            prevented_total += 1
            a, b = r.get("author_practice"), r.get("beneficiary_practice")
            if a and b and a != b:
                prevented_bi += 1
    resolved = by_outcome.get("prevented", 0) + by_outcome.get("failed", 0)
    total = len(rows or [])
    return {
        "total": total,
        "by_outcome": by_outcome,
        "prevention_rate": round(by_outcome.get("prevented", 0) / resolved, 3) if resolved else None,
        "beneficiary_independent": prevented_bi,
        "beneficiary_independent_rate": round(prevented_bi / prevented_total, 3) if prevented_total else None,
    }
