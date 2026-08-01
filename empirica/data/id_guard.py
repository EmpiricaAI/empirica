"""Blank ids must never reach a prefix matcher.

Every id-resolving path in this codebase does prefix matching by interpolating the
caller's id into a LIKE pattern::

    "SELECT id FROM goals WHERE id LIKE ?", (f"{goal_id}%",)

That is correct for a real prefix and catastrophic for an empty one: ``LIKE '%'``
matches every row, and the resolver then takes the first or most-recent match and
writes to it. The caller gets a success back, because from inside the resolver the
write *did* succeed — just not on the row anyone asked for.

The failure is quiet by construction: a script that derives an id from a lookup
gets ``""`` when the lookup finds nothing, passes it straight to a verb, and is
told the operation worked. Cortex hit this on ``goals-reopen`` (prop_fm4ultb5) and
concluded it was a harmless no-op; it was a write to an arbitrary bystander.

So the rule is: **an id that resolves to nothing is never a valid target for any
verb.** Guard at the point where a blank would become ``LIKE '%'``, not at each
call site — call sites are where the guard gets forgotten.
"""

from __future__ import annotations

# Shortest accepted partial id. Eight characters is what the list verbs print,
# so it is the shortest fragment a caller could legitimately have copied. Below
# that a prefix identifies nothing — `LIKE '1%'` spans every id starting with a
# digit — so anything shorter is a typo rather than a query.
MIN_ID_PREFIX = 8


def resolve_id_prefix(cursor, table: str, id_col: str, raw_id: str | None) -> tuple[str | None, str | None]:
    """Resolve a full or partial id to exactly one full id.

    Returns ``(full_id, None)`` on success, or ``(None, error_message)``.

    This exists because guarding blankness alone turned out not to be enough. A
    one-character id walks straight past :func:`is_blank_id` and still becomes a
    matcher spanning hundreds of rows, and the callers then either take the first
    match or — worse, in the batch verbs — issue ``UPDATE ... WHERE id LIKE`` with
    no LIMIT and mutate all of them.

    Three refusals, in order:

    1. **blank** — would become ``LIKE '%'`` and match everything
    2. **shorter than MIN_ID_PREFIX** — identifies nothing (full UUIDs, which
       contain dashes, are exempt from the length rule)
    3. **ambiguous** — more than one row matches, and picking one by recency or
       row order is a coin flip returned as a result

    Resolving up front and then addressing rows by exact id is what makes the
    operation single-row: it is not a check bolted onto a prefix match, it
    replaces the prefix match at the point of mutation.
    """
    if is_blank_id(raw_id):
        return None, "id is empty — refusing, an empty id matches every row"

    candidate = str(raw_id).strip()

    # Wording deliberately matches what update-artifacts already returned, so the
    # existing CLI error contract is preserved — callers and tests key on these
    # phrases, and changing them would be a gratuitous break.
    if len(candidate) < MIN_ID_PREFIX and "-" not in candidate:
        return None, f"id {candidate!r} is shorter than {MIN_ID_PREFIX} characters — refusing to prefix-match"

    cursor.execute(f"SELECT {id_col} FROM {table} WHERE {id_col} LIKE ?", (f"{candidate}%",))
    rows = cursor.fetchall()

    if not rows:
        return None, f"not found — no row matches id {candidate!r}"

    if len(rows) > 1:
        sample = ", ".join(str(r[0])[:8] for r in rows[:3])
        suffix = ", …" if len(rows) > 3 else ""
        return None, f"id {candidate!r} is ambiguous — matches {len(rows)} rows ({sample}{suffix}); use a longer prefix"

    return str(rows[0][0]), None


def is_blank_id(value: str | None) -> bool:
    """True when ``value`` cannot address a row.

    ``None``, ``""``, and whitespace-only all qualify. Whitespace matters in
    practice — an id piped out of a shell lookup arrives with a trailing newline,
    and ``"\\n"`` interpolates into ``LIKE '\\n%'``, which matches nothing and so
    fails safe by accident rather than by design. Treat it as blank so the refusal
    is deliberate either way.
    """
    return value is None or not str(value).strip()
