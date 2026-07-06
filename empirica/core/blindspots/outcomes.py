"""Blindspot outcome resolution — the POSTFLIGHT learning half (T4).

Closes the loop the CHECK advisory opened: a surfaced blindspot is advanced to
``acknowledged`` (its flagged task got engaged) or ``dismissed`` (the goal closed
with the task still bare — you proceeded past the nudge). That makes
``blindspot-report``'s acknowledge/dismiss rates real, which is what tunes how
loud the detector earns the right to be.

``regretted`` — the training label (a *dismissed* blindspot that later became a
mistake/dead-end) — gets its mechanism here (``mark_blindspot_regretted``); wiring
the automatic trigger from the mistake/dead-end log is the documented next step.

All fail-open — the blindspot machinery must never affect POSTFLIGHT.
"""

from __future__ import annotations

import time

from .intent_gap import _TERMINAL_GOAL_STATUS

_ENGAGED_TASK_STATUS = frozenset({"completed", "complete", "done"})


def resolve_blindspot_outcomes(db, session_id: str) -> int:
    """Advance this session's ``surfaced`` blindspots at POSTFLIGHT.

    For each subtask with a surfaced blindspot, re-read the goal tree:
    - **engaged** (the subtask now has a finding, unknown, dead_end, or a complete
      status) → ``acknowledged`` — the practitioner did something with the flagged
      task (the nudge, or the work, addressed it).
    - else, if the **parent goal is terminal** but the subtask stayed bare →
      ``dismissed`` — the goal closed while the flagged gap was ignored.
    - else → stay ``surfaced`` (work may still be in-flight; don't dismiss early).

    Fail-open: returns the number of rows updated, or 0 on any error.
    """
    try:
        cur = db.conn.execute(
            "SELECT DISTINCT subtask_id FROM blindspot_events WHERE session_id = ? AND outcome = 'surfaced'",
            (session_id,),
        )
        surfaced = {row[0] for row in cur.fetchall()}
        if not surfaced:
            return 0

        # subtask_id -> (engaged, goal_terminal)
        state: dict = {}
        for goal in db.goals.get_goal_tree(session_id) or []:
            gterm = (goal.get("status") or "").strip().lower() in _TERMINAL_GOAL_STATUS
            for st in goal.get("subtasks") or []:
                engaged = (
                    bool(st.get("findings") or st.get("unknowns") or st.get("dead_ends"))
                    or (st.get("status") or "").strip().lower() in _ENGAGED_TASK_STATUS
                )
                state[st.get("subtask_id")] = (engaged, gterm)

        now = time.time()
        updated = 0
        for sid in surfaced:
            engaged, gterm = state.get(sid, (False, False))
            outcome = "acknowledged" if engaged else ("dismissed" if gterm else None)
            if outcome is None:
                continue
            db.conn.execute(
                "UPDATE blindspot_events SET outcome = ?, resolved_timestamp = ? "
                "WHERE session_id = ? AND subtask_id = ? AND outcome = 'surfaced'",
                (outcome, now, session_id, sid),
            )
            updated += 1
        if updated:
            db.conn.commit()
        return updated
    except Exception:
        return 0


def mark_blindspot_regretted(db, session_id: str, subtask_id: str) -> int:
    """Flip a ``dismissed`` blindspot to ``regretted`` — the training label.

    Called when a mistake or dead-end lands on a subtask that had a dismissed
    blindspot (we warned, it was ignored, and the gap bit). Fail-open; returns
    rows updated. The automatic trigger from the mistake/dead-end log is the
    documented follow-up — this is the mechanism it will call.
    """
    try:
        cur = db.conn.execute(
            "UPDATE blindspot_events SET outcome = 'regretted', resolved_timestamp = ? "
            "WHERE session_id = ? AND subtask_id = ? AND outcome = 'dismissed'",
            (time.time(), session_id, subtask_id),
        )
        db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception:
        return 0
