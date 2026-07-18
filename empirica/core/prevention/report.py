"""Prevention measurement report — the read-only measurement surface (S5).

Composes :func:`read_prevention_events` + :func:`aggregate_prevention_events`
(per ``outcome_family``) into one measurement view, plus the exposed/shadow-arm
split (EXP-SHADOW control).

**Emits RAW exposed-arm rates only — NOT the causal ATE.** The average treatment
effect (``Pr(failure | not exposed) − Pr(failure | exposed)``) needs the
EXP-SHADOW control arm and research's causal model; that is deliberately *not*
computed here (spec §2, §6). ``prevention_rate`` below is the exposed-arm
numerator, not the treatment effect.

A composable core function, not a CLI verb — the daemon / extension / a future
report verb can call it without adding CLI surface. Fail-open: returns an
empty-but-valid report on any error.
"""

from __future__ import annotations

from .persist import aggregate_prevention_events, read_prevention_events

_ATE_DISCLAIMER = (
    "Raw exposed-arm rates only — NOT the causal ATE. The treatment effect needs "
    "the EXP-SHADOW control arm and research's causal model; prevention_rate here "
    "is the exposed-arm numerator, not the effect size."
)


def _empty_report(session_id: str | None) -> dict:
    return {
        "session_id": session_id,
        "total_events": 0,
        "exposed_arm": 0,
        "shadow_arm": 0,
        "families": [],
        "by_family": {},
        "overall": aggregate_prevention_events([]),
        "disclaimer": _ATE_DISCLAIMER,
    }


def prevention_report(db, session_id: str | None = None) -> dict:
    """Read-only measurement view of prevention_events.

    Returns::

        {session_id, total_events, exposed_arm, shadow_arm, families,
         by_family: {<family>: <aggregate>}, overall: <aggregate>, disclaimer}

    where each ``<aggregate>`` carries ``prevention_rate`` +
    ``beneficiary_independent[_rate]`` (see :func:`aggregate_prevention_events`).
    ``shadow_arm`` counts EXP-SHADOW control-arm rows; ``exposed_arm`` the rest.
    Fail-open.
    """
    try:
        rows = read_prevention_events(db, session_id)
        families = sorted({(r.get("outcome_family") or "prevention") for r in rows})
        shadow_arm = sum(1 for r in rows if r.get("shadow"))
        return {
            "session_id": session_id,
            "total_events": len(rows),
            "exposed_arm": len(rows) - shadow_arm,
            "shadow_arm": shadow_arm,
            "families": families,
            "by_family": {fam: aggregate_prevention_events(rows, family=fam) for fam in families},
            "overall": aggregate_prevention_events(rows),
            "disclaimer": _ATE_DISCLAIMER,
        }
    except Exception:
        return _empty_report(session_id)
