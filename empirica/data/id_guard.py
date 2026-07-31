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


def is_blank_id(value: str | None) -> bool:
    """True when ``value`` cannot address a row.

    ``None``, ``""``, and whitespace-only all qualify. Whitespace matters in
    practice — an id piped out of a shell lookup arrives with a trailing newline,
    and ``"\\n"`` interpolates into ``LIKE '\\n%'``, which matches nothing and so
    fails safe by accident rather than by design. Treat it as blank so the refusal
    is deliberate either way.
    """
    return value is None or not str(value).strip()
