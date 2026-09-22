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


class UnresolvableFindingRef(ValueError):
    """A `superseded_by` value that names no single artifact."""


# A supersession may cross types: a finding that was really a mistake is
# resolved `mistyped` and points at the mistake. cortex held exactly that row,
# and a findings-only lookup refused it as dangling (2026-09-21).
_ARTIFACT_TABLES = (
    "project_findings",
    "mistakes_made",
    "project_unknowns",
    "project_dead_ends",
    "assumptions",
    "decisions",
    "lessons",
)


def canonical_finding_id(execute, value) -> str | None:
    """Turn what a practitioner typed into the full id of one artifact, or refuse.

    `execute` is any `(sql, params) -> cursor` callable: a connection's execute, or
    a repository's `_execute`, which also handles the PostgreSQL placeholder dialect.

    Every list view and receipt prints ids as eight characters, so that is the
    value in hand when someone reaches for `--superseded-by`. The column used to
    store it verbatim: a pointer built from the tool's own output dangled (two of
    a peer's four dangling pointers, measured 2026-09-21). A full id that matches
    nothing was stored the same way, and so was a `log-artifacts` batch ref such
    as `f_unit` reused in a later `resolve-artifacts` call.

    Exact id wins, in any artifact table. Otherwise an 8+ character prefix that
    matches exactly one artifact across all tables is expanded. Anything else
    raises: no match, several matches, or a prefix too short to be an identity.
    Empty stays None.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for table in _ARTIFACT_TABLES:
        row = _fetch(execute, f"SELECT id FROM {table} WHERE id = ?", (text,))
        if row:
            return row[0][0]
    if len(text) < 8:
        raise UnresolvableFindingRef(f"superseded_by {text!r} is too short to identify an artifact (8+ characters)")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    matches: list[str] = []
    for table in _ARTIFACT_TABLES:
        rows = _fetch(execute, f"SELECT id FROM {table} WHERE id LIKE ? ESCAPE '\\' LIMIT 3", (escaped + "%",))
        matches.extend(r[0] for r in rows)
        if len(matches) > 1:
            break
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise UnresolvableFindingRef(f"superseded_by {text!r} matches no artifact in this store")
    raise UnresolvableFindingRef(f"superseded_by {text!r} matches more than one artifact; give the full id")


def _fetch(execute, sql: str, params: tuple) -> list:
    """Rows, or none when the table is absent on an older store."""
    try:
        return list(execute(sql, params).fetchall())
    except Exception:
        return []


def one_artifact_id(execute, table: str, value: str) -> str:
    """The single row in `table` that `value` names, expanded to its full id.

    `finding-resolve` and `unknown-resolve` ran UPDATE ... WHERE id LIKE prefix%
    with no check on how many rows matched, so an ambiguous prefix resolved every
    artifact sharing it, silently. Exact match wins; an 8+ character prefix must
    match exactly one row. Raises UnresolvableFindingRef otherwise.
    """
    if table not in _ARTIFACT_TABLES:
        raise ValueError(f"not an artifact table: {table}")
    text = str(value or "").strip()
    if not text:
        raise UnresolvableFindingRef("no artifact id given")
    row = _fetch(execute, f"SELECT id FROM {table} WHERE id = ?", (text,))
    if row:
        return row[0][0]
    if len(text) < 8:
        raise UnresolvableFindingRef(f"{text!r} is too short to identify an artifact (8+ characters)")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = _fetch(execute, f"SELECT id FROM {table} WHERE id LIKE ? ESCAPE '\\' LIMIT 3", (escaped + "%",))
    if len(rows) == 1:
        return rows[0][0]
    if not rows:
        raise UnresolvableFindingRef(f"no {table} row matches {text!r}")
    raise UnresolvableFindingRef(f"{text!r} matches more than one {table} row; give the full id")
