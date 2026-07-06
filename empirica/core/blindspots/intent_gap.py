"""Intent-gap blindspot detection — the least-noisy signal.

A blindspot is *absent + unacknowledged + inferred*. The strongest, lowest-noise
inference is against **stated intent**: a goal/task the practice declared it would
address, that has no covering artifact AND no acknowledging unknown. You said
you'd assess X; nothing shows you did; you never flagged X as open.

Pure function over ``GoalDataRepository.get_goal_tree(session_id)`` — no DB, no
embeddings, unit-testable. The per-subtask ``findings`` / ``unknowns`` /
``dead_ends`` lists ARE the mask:

- a **finding** means it's covered → not a blindspot
- an **unknown** means it's acknowledged → a known-unknown, not a blindspot
- a **dead_end** means it was attempted → not an untouched gap

so a candidate is an *open* subtask, under a *non-terminal* goal, with all three
empty.
"""

from __future__ import annotations

# Terminal states — no intent-gap possible (the work is done or abandoned).
_TERMINAL_GOAL_STATUS = frozenset({"completed", "complete", "done", "abandoned", "cancelled", "stale"})
_TERMINAL_TASK_STATUS = frozenset({"completed", "complete", "done", "cancelled", "abandoned"})


def detect_intent_gaps(goal_tree: list[dict] | None) -> list[dict]:
    """Return intent-gap blindspot candidates from a session's goal tree.

    ``goal_tree`` — the output of ``GoalDataRepository.get_goal_tree(session_id)``:
    a list of goal dicts, each with a nested ``subtasks`` list whose items carry
    ``description`` / ``status`` / ``findings`` / ``unknowns`` / ``dead_ends``.

    A candidate is an open subtask (non-terminal status) under a non-terminal goal
    whose findings, unknowns, and dead_ends are all empty — stated intent with no
    coverage, no acknowledgment, and no attempt. Conservative by design: any of the
    three lists being non-empty masks the subtask out.
    """
    candidates: list[dict] = []
    for goal in goal_tree or []:
        if (goal.get("status") or "").strip().lower() in _TERMINAL_GOAL_STATUS:
            continue
        objective = goal.get("objective") or ""
        for st in goal.get("subtasks") or []:
            if (st.get("status") or "").strip().lower() in _TERMINAL_TASK_STATUS:
                continue
            # The mask: covered (finding) / acknowledged (unknown) / attempted (dead_end).
            if st.get("findings") or st.get("unknowns") or st.get("dead_ends"):
                continue
            candidates.append(
                {
                    "kind": "intent_gap",
                    "goal_id": goal.get("goal_id"),
                    "objective": objective,
                    "subtask_id": st.get("subtask_id"),
                    "intent": st.get("description") or "",
                    "reason": (
                        "stated task with no covering finding, no acknowledging unknown, "
                        "and no attempt — you may be proceeding as if it's handled"
                    ),
                }
            )
    return candidates
