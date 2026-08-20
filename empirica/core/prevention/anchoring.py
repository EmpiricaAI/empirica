"""Whether a prevention verdict was measured at all — the precondition for reporting a rate.

A rate is a claim about a population. When that population's verdicts come from a
predicate that cannot discriminate, the honest output is not a smaller number and
not a caveat beside the number: it is **no number**. A caveat loses to the figure
it sits next to, every time.

Two anchoring failures are known, both measured rather than theorised:

1. **The session-wide failure predicate.** ``detection.py`` resolves an exposure
   to ``failed`` when its ``goal_id`` is NULL by asking *was any mistake or
   dead-end logged in this session after exposure* — not the warned-about
   failure, not on the same subject. For a practitioner logging mistakes at a
   normal rate that is almost always true, so ``failed`` is near-guaranteed by
   construction. Measured: 204 events, all NULL-goal, 202 ``failed``, and
   ``prevented`` recorded zero times ever.

2. **``subject_key`` holds the session.** ``wiring.py`` emits at PREFLIGHT,
   before any goal exists, and sets ``subject_key`` to ``f"session:{id}"``. So
   scoping a predicate "to the subject" is scoping to the session under a better
   name. Measured: 204 distinct ``pattern_key`` values against **one** distinct
   ``subject_key``; a peer's independent sample collapsed 167 patterns onto 3.

The instrument built as the anti-Goodhart anchor is itself unanchored. This
module is what stops it publishing a number until it is not.

**Read-only, and fail-CLOSED.** Every other module in this package fails open,
which is right for them — a measurement path that breaks should degrade to empty
rather than take a caller down. This one is a gate, and a gate that fails open
publishes exactly the number it exists to withhold.
"""

from __future__ import annotations

#: A ``subject_key`` of this shape is the session, not a subject.
SESSION_SUBJECT_PREFIX = "session:"

#: Outcomes that constitute a verdict. ``exposed`` is an open window and
#: ``unmeasurable`` (P3) is an explicit refusal — neither is a resolved verdict.
RESOLVED_OUTCOMES = ("prevented", "failed")


def anchoring_verdict(rows: list[dict]) -> dict:
    """Can a rate be computed from ``rows``? Returns the verdict and the reason.

    Returns ``{anchored, reason, resolved, subjectless, distinct_subjects,
    distinct_patterns}``. ``reason`` is present exactly when ``anchored`` is
    False and names which precondition failed, so a reader gets a diagnosis
    rather than a silence.
    """
    default = {
        "anchored": False,
        "reason": "anchoring could not be evaluated",
        "resolved": 0,
        "subjectless": 0,
        "distinct_subjects": 0,
        "distinct_patterns": 0,
    }
    try:
        resolved = [r for r in (rows or []) if (r.get("outcome") or "") in RESOLVED_OUTCOMES]
        subjects = {r.get("subject_key") for r in resolved if r.get("subject_key")}
        patterns = {r.get("pattern_key") for r in resolved if r.get("pattern_key")}
        # Subjectless = no bound goal AND the session placeholder in subject_key.
        # Both halves of the same defect; either alone leaves a usable anchor.
        subjectless = sum(
            1
            for r in resolved
            if not r.get("goal_id") and str(r.get("subject_key") or "").startswith(SESSION_SUBJECT_PREFIX)
        )
        out = {
            "anchored": True,
            "reason": None,
            "resolved": len(resolved),
            "subjectless": subjectless,
            "distinct_subjects": len(subjects),
            "distinct_patterns": len(patterns),
        }
        if not resolved:
            out.update(anchored=False, reason="no resolved events — nothing to compute a rate over")
        elif subjectless:
            out.update(
                anchored=False,
                reason=(
                    f"{subjectless} of {len(resolved)} resolved events have no bound subject "
                    "(goal_id NULL and subject_key is the session), so their verdict came from a "
                    "session-wide predicate that cannot discriminate"
                ),
            )
        elif len(patterns) > 1 and len(subjects) <= 1:
            # Many patterns adjudicated against one subject means every pattern
            # was judged on the same failures.
            out.update(
                anchored=False,
                reason=(
                    f"{len(patterns)} distinct patterns resolve against {len(subjects)} distinct "
                    "subject(s) — the predicate does not discriminate between them"
                ),
            )
        return out
    except Exception as e:  # fail CLOSED — a gate that cannot evaluate must withhold
        return {**default, "reason": f"anchoring could not be evaluated: {type(e).__name__}: {e}"}


def strip_unanchored_rates(aggregate: dict) -> dict:
    """Return ``aggregate`` with every ``*_rate`` key REMOVED, not nulled.

    Absence and null say different things and the difference is the whole point:
    ``None`` reads as *measured, and the answer is nothing*, while an absent key
    reads as *not measured*. Counts are left alone — they are observations about
    what was recorded and stay true whatever the predicate did.
    """
    if not isinstance(aggregate, dict):
        return aggregate
    return {k: v for k, v in aggregate.items() if not k.endswith("_rate")}
