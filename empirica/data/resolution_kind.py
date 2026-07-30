"""Closed vocabulary for *why* a finding was resolved.

Migration 057 gave findings ``is_resolved`` plus a free-text ``resolution``.
Free text cannot be queried — and, more importantly, cannot be *offered*. What
the surface does not name, the practitioner does not reach for.

Measured 2026-07-30 on the empirica practice: of 1268 resolved findings, **1267
resolve as stale/superseded/snapshot and exactly 1 as an error**. A true error
rate of 1-in-4199 over six months is not plausible, so errors were not being
expressed rather than not occurring. The rebuttal "we simply had not gardened
yet" does not apply — this practice HAS gardened 1268 findings. Gardening itself
was staleness-only, because staleness was the only available word.

The distinction that carries the weight is ``stale`` vs ``retracted``:
*it aged* and *it was never true* are different epistemic events, and collapsing
them means a practice cannot tell ageing from error in its own history.
"""

from typing import Literal

ResolutionKind = Literal["stale", "superseded", "retracted", "mistyped"]

#: Ordered by how often they should legitimately fire — ``stale`` is the common
#: case, ``mistyped`` the rare one.
RESOLUTION_KINDS: tuple[str, ...] = ("stale", "superseded", "retracted", "mistyped")

#: One line each, surfaced in ``--help`` so the choice is made at the point of
#: resolving rather than looked up.
RESOLUTION_KIND_HELP: dict[str, str] = {
    "stale": "was true when written, has since aged out",
    "superseded": "replaced by a NAMED newer artifact (use with --superseded-by)",
    "retracted": "was FALSE when written — a genuine error, not ageing",
    "mistyped": "belongs to a different artifact type (e.g. a mistake logged as a finding)",
}


def normalize_resolution_kind(value: str | None) -> str | None:
    """Return a valid kind, or ``None`` for unknown/missing values.

    ``None`` is the legitimate default: it means "not classified". As with
    :mod:`empirica.data.epistemic_source`, coercing an unrecognised value to a
    tag would silently misclassify — and here the misclassification that matters
    most is exactly the one being measured, ``retracted`` recorded as ``stale``.
    Callers that need to reject bad input should validate against
    :data:`RESOLUTION_KINDS` rather than rely on this returning ``None``.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in RESOLUTION_KINDS:
        return v
    return None


def is_retraction(kind: str | None) -> bool:
    """True when the kind asserts the finding was WRONG, not merely old.

    ``mistyped`` counts: a mistake recorded as a finding was never a finding, so
    the row's claim to be an observation was false from the start. Used by
    calibration surfaces that need "how often does this practice discover it was
    wrong?" — a number that read as zero for six months because the vocabulary
    had no way to say it.
    """
    return normalize_resolution_kind(kind) in ("retracted", "mistyped")
