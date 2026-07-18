"""Recurrence oracle — ground-truth on whether a warned-about failure actually recurred.

Leg B of ``docs/architecture/PREVENTION_MEASUREMENT_SPEC.md``. The anti-Goodhart
anchor: "no mistake was *logged*" is gameable; "the failure did not *recur* under
continued exposure" requires manufacturing real downstream work under permissioned
identity, which is empirica's existing ungameable-behavioral fight.

Read-only. Fail-open — returns a null verdict on any error, never raises. This
module only *witnesses recurrence*; it never asserts prevention from absence (that
is the caller's observation-window judgement, per spec §5).
"""

from __future__ import annotations


def _rows(db, sql: str, params: tuple) -> list:
    try:
        return db.conn.execute(sql, params).fetchall()
    except Exception:
        return []


def recurrence_verdict(db, pattern_key: str, subject_key: str, since_ts: float = 0.0) -> dict:
    """Did anti-pattern ``pattern_key``'s failure recur on ``subject_key`` after ``since_ts``?

    Ground-truth signals, strongest first (spec §5):
      1. a NEW failure event (mistake / dead-end) on the subject's goal or subtask,
         logged after ``since_ts`` — hard recurrence (confidence 0.9);
      2. a prevention_event already resolved to ``failed`` / ``recurred`` after
         ``since_ts`` (0.8);
      3. a regret flip (a dismissed blindspot that became a mistake/dead-end) on the
         same subtask (0.6).

    The prevention_events rows for ``(pattern_key, subject_key)`` are the bridge:
    failures carry no ``pattern_key``, so the subject's ``goal_id`` / ``subtask_id``
    (recorded at exposure) are the join keys into the failure tables.

    Returns a verdict dict::

        {pattern_key, subject_key, first_occurrence_at, exposures, preventions,
         recurrences: [{kind, at, confidence}], recurred, latency_s, confidence}

    ``confidence`` is the strongest recurrence signal's confidence (0.0 if none).
    ``latency_s`` is the time from ``since_ts`` to the earliest recurrence, or None.
    """
    default = {
        "pattern_key": pattern_key,
        "subject_key": subject_key,
        "first_occurrence_at": None,
        "exposures": 0,
        "preventions": 0,
        "recurrences": [],
        "recurred": False,
        "latency_s": None,
        "confidence": 0.0,
    }
    try:
        events = _rows(
            db,
            "SELECT goal_id, subtask_id, exposed_at, outcome, outcome_at "
            "FROM prevention_events WHERE pattern_key = ? AND subject_key = ?",
            (pattern_key, subject_key),
        )
        if not events:
            return default

        goal_ids = {e[0] for e in events if e[0]}
        subtask_ids = {e[1] for e in events if e[1]}
        exposed_ats = [e[2] for e in events if e[2] is not None]
        first_occurrence_at = min(exposed_ats) if exposed_ats else None
        exposures = len(events)
        preventions = sum(1 for e in events if e[3] == "prevented")

        seen: set = set()
        recurrences: list[dict] = []

        def _add(kind: str, at, confidence: float) -> None:
            key = (kind, at)
            if key not in seen:
                seen.add(key)
                recurrences.append({"kind": kind, "at": at, "confidence": confidence})

        # (2) prevention_events already resolved to a miss after since_ts.
        for ev in events:
            outcome, outcome_at = ev[3], ev[4]
            if outcome in ("failed", "recurred") and (outcome_at or 0) > since_ts:
                _add(outcome, outcome_at, 0.8)

        # (1) hard recurrence: a new failure on the subject's goal/subtask after since_ts.
        for gid in goal_ids:
            for (at,) in _rows(
                db,
                "SELECT created_timestamp FROM mistakes_made WHERE goal_id = ? AND created_timestamp > ?",
                (gid, since_ts),
            ):
                _add("mistake", at, 0.9)
            for (at,) in _rows(
                db,
                "SELECT created_timestamp FROM session_dead_ends WHERE goal_id = ? AND created_timestamp > ?",
                (gid, since_ts),
            ):
                _add("dead_end", at, 0.9)
        for sid in subtask_ids:
            for (at,) in _rows(
                db,
                "SELECT created_timestamp FROM session_dead_ends WHERE subtask_id = ? AND created_timestamp > ?",
                (sid, since_ts),
            ):
                _add("dead_end", at, 0.9)
            # (3) regret flip on the same subtask.
            for (at,) in _rows(
                db,
                "SELECT resolved_timestamp FROM blindspot_events "
                "WHERE outcome = 'regretted' AND subtask_id = ? "
                "AND (resolved_timestamp IS NULL OR resolved_timestamp > ?)",
                (sid, since_ts),
            ):
                _add("regret", at, 0.6)

        recurred = bool(recurrences)
        rec_ats = [r["at"] for r in recurrences if r.get("at") is not None]
        latency_s = (min(rec_ats) - since_ts) if (recurred and rec_ats) else None
        confidence = max((r["confidence"] for r in recurrences), default=0.0)
        return {
            "pattern_key": pattern_key,
            "subject_key": subject_key,
            "first_occurrence_at": first_occurrence_at,
            "exposures": exposures,
            "preventions": preventions,
            "recurrences": recurrences,
            "recurred": recurred,
            "latency_s": latency_s,
            "confidence": round(confidence, 2),
        }
    except Exception:
        return default
