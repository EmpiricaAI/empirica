"""Persistence + aggregation for blindspot events (migration 053).

Instrument-before-surface: ``persist_blindspot_candidates`` records surfaced
candidates **fail-open** (a persistence error must never affect CHECK/POSTFLIGHT);
``aggregate_blindspot_events`` powers the ``blindspot-report`` telemetry. The
outcome starts ``surfaced`` and is advanced later — ``acknowledged`` when the
practitioner logs an unknown for it, ``dismissed`` when ignored, ``regretted``
when a dismissed one later becomes a mistake or dead-end (the T4 regret loop).
"""

from __future__ import annotations

import time

_EVENT_COLS = (
    "session_id",
    "transaction_id",
    "created_timestamp",
    "kind",
    "goal_id",
    "subtask_id",
    "intent",
    "surfaced_at",
    "outcome",
    "resolved_timestamp",
)


def persist_blindspot_candidates(db, session_id, transaction_id, candidates, surfaced_at) -> int:
    """Record each candidate as a ``blindspot_events`` row (outcome=``surfaced``).

    FAIL-OPEN — returns the number persisted, or 0 on any error (including a
    missing table on an un-migrated DB). Never raises: the blindspot machinery
    must not brick the loop it observes.
    """
    try:
        now = time.time()
        rows = [
            (
                session_id,
                transaction_id,
                now,
                c.get("kind"),
                c.get("goal_id"),
                c.get("subtask_id"),
                c.get("intent"),
                surfaced_at,
                "surfaced",
            )
            for c in (candidates or [])
        ]
        if not rows:
            return 0
        db.conn.executemany(
            "INSERT INTO blindspot_events "
            "(session_id, transaction_id, created_timestamp, kind, goal_id, subtask_id, "
            "intent, surfaced_at, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        db.conn.commit()
        return len(rows)
    except Exception:
        return 0


def read_blindspot_events(db, session_id: str | None = None) -> list[dict]:
    """Read blindspot_events (optionally scoped to a session). [] on any error."""
    try:
        sql = f"SELECT {', '.join(_EVENT_COLS)} FROM blindspot_events"
        params: tuple = ()
        if session_id:
            sql += " WHERE session_id = ?"
            params = (session_id,)
        cur = db.conn.execute(sql, params)
        return [dict(zip(_EVENT_COLS, row)) for row in cur.fetchall()]
    except Exception:
        return []


def aggregate_blindspot_events(rows: list[dict]) -> dict:
    """Aggregate into telemetry: totals, by-outcome, by-kind, acknowledge/regret rate.

    - **acknowledge-rate** — of surfaced, how many were acted on (an ``unknown``
      logged / investigated). High = the nudge is useful.
    - **regret-rate** — of surfaced, how many were dismissed and *then* became a
      mistake/dead-end. High = we were right and got ignored (surface louder).
    - low acknowledge + low regret = crying wolf (surface quieter / raise the bar).
    """
    by_outcome: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in rows or []:
        by_outcome[r.get("outcome") or "surfaced"] = by_outcome.get(r.get("outcome") or "surfaced", 0) + 1
        by_kind[r.get("kind") or "unknown"] = by_kind.get(r.get("kind") or "unknown", 0) + 1
    total = len(rows or [])
    return {
        "total": total,
        "by_outcome": by_outcome,
        "by_kind": by_kind,
        "acknowledge_rate": round(by_outcome.get("acknowledged", 0) / total, 3) if total else None,
        "dismiss_rate": round(by_outcome.get("dismissed", 0) / total, 3) if total else None,
        "regret_rate": round(by_outcome.get("regretted", 0) / total, 3) if total else None,
    }
