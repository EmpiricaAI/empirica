"""Due dates on goals, unknowns and assumptions (prop_bnviyv2epvg27kgmqycwlflshm).

``due_at`` holds an ISO date. When the date has come and the item is still open, it is *due*: bootstrap and the
SessionStart context list it under Due until the practitioner answers it one of three ways: do it, snooze it (a new
date, through ``update-artifacts``), or drop it (complete, resolve or falsify it with the existing verbs).

A condition trigger ("after release X") is not here yet: a date is the part that can fire on its own.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, timedelta

#: artifact type -> (table, the predicate that makes a row still open)
DUE_TABLES: dict[str, tuple[str, str]] = {
    "goal": (
        "goals",
        "status NOT IN ('completed', 'abandoned') AND COALESCE(is_completed, 0) = 0 AND COALESCE(archived, 0) = 0",
    ),
    "unknown": ("project_unknowns", "COALESCE(is_resolved, 0) = 0"),
    "assumption": ("assumptions", "COALESCE(status, 'unverified') = 'unverified'"),
}
_TEXT_COLUMN = {"goal": "objective", "unknown": "unknown", "assumption": "assumption"}
_OFFSET = re.compile(r"^\+(\d{1,4})([dw])$")
FORMAT_HELP = "a date YYYY-MM-DD, or an offset from today: +7d, +2w"
logger = logging.getLogger(__name__)


class DueDateError(ValueError):
    """A due date that cannot be read, or an artifact type that cannot carry one."""


def parse_due(text: str, *, today: date | None = None) -> str:
    """The ISO date ``text`` names: ``YYYY-MM-DD`` as given, or ``+Nd``/``+Nw`` counted from ``today``."""
    value = (text or "").strip()
    offset = _OFFSET.match(value)
    if offset:
        n, unit = int(offset.group(1)), offset.group(2)
        return ((today or date.today()) + timedelta(days=n * (7 if unit == "w" else 1))).isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DueDateError(f"due date {value!r} is not {FORMAT_HELP}")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as e:
        raise DueDateError(f"due date {value!r} is not {FORMAT_HELP}") from e


def set_due(conn: sqlite3.Connection, artifact_type: str, artifact_id: str, due_at: str | None) -> bool:
    """Write ``due_at`` (None clears it). True when a row was updated."""
    if artifact_type not in DUE_TABLES:
        raise DueDateError(f"{artifact_type} cannot carry a due date: only {', '.join(DUE_TABLES)}")
    table = DUE_TABLES[artifact_type][0]
    cur = conn.execute(f"UPDATE {table} SET due_at = ? WHERE id = ?", (due_at, artifact_id))
    conn.commit()
    return cur.rowcount > 0


def due_items(conn: sqlite3.Connection, project_id: str, *, today: date | None = None, limit: int = 5) -> list[dict]:
    """Open items of ``project_id`` whose date has come, oldest date first. The last one carries ``more`` when the cap
    left items out, so a short list never reads as the whole list."""
    cutoff = (today or date.today()).isoformat()
    rows: list[dict] = []
    for kind, (table, still_open) in DUE_TABLES.items():
        try:
            found = conn.execute(
                f"SELECT id, {_TEXT_COLUMN[kind]}, due_at FROM {table} "
                f"WHERE project_id = ? AND due_at IS NOT NULL AND due_at <= ? AND {still_open}",
                (project_id, cutoff),
            ).fetchall()
        except sqlite3.OperationalError as e:
            if "no such column" not in str(e) and "no such table" not in str(e):
                logger.warning("due items not read from %s: %s", table, e)  # locked or damaged: not "nothing due"
            continue  # a database older than the column: nothing can be due there yet
        rows += [{"type": kind, "id": r[0], "text": r[1], "due_at": r[2]} for r in found]
    rows.sort(key=lambda r: (r["due_at"], r["type"], r["id"]))
    shown = rows[: max(limit, 0)]
    if shown and len(rows) > len(shown):
        shown[-1]["more"] = len(rows) - len(shown)
    return shown


def missing_due_hint(artifact_type: str) -> str:
    """What a create/log response says when a planned goal or an unknown carries no date: nothing will bring it back."""
    what = "This planned goal" if artifact_type == "goal" else f"This {artifact_type}"
    return (
        f"{what} has no due date, so nothing will bring it back up. If it should return on a date, set one: "
        f"update-artifacts with due_at, or --due next time ({FORMAT_HELP})."
    )


def due_option(config_data: object, args: object) -> str | None:
    """The ISO date a create/log call asked for (``due`` in its JSON config, else ``--due``), or None. Raises
    :class:`DueDateError` on a value it cannot read, before anything is written."""
    raw = config_data.get("due") if isinstance(config_data, dict) else None
    raw = raw or getattr(args, "due", None)
    return parse_due(str(raw)) if raw else None


def stamp_due(conn: sqlite3.Connection, artifact_type: str, artifact_id: str, due_at: str | None) -> dict:
    """Write ``due_at`` when one was given, and say what the row now carries.

    A log call can return an existing row (an unknown logged twice is matched to the first, resolved or not): a date is not
    written onto a closed row, and a row that already has a date reports it instead of a hint that it has none (1.15.0
    sweep, D2). Returns ``{"due_at": ...}``, ``{"due_note": ...}`` or ``{}``.
    """
    table, still_open = DUE_TABLES[artifact_type]
    try:
        row = conn.execute(f"SELECT due_at, ({still_open}) FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None and not row[1]:
        return (
            {"due_note": f"this matches a closed {artifact_type} ({artifact_id[:8]}): no due date set"}
            if due_at
            else {}
        )
    if due_at:
        set_due(conn, artifact_type, artifact_id, due_at)
        return {"due_at": due_at}
    return {"due_at": row[0]} if row is not None and row[0] else {}


def due_fields(artifact_type: str, stamped: dict | str | None, *, hint: bool) -> dict:
    """What a create/log response says about the date: what stamp_due reported, or (when ``hint`` and the row has no date)
    how to set one. A plain date string is accepted for callers that wrote the date themselves."""
    if isinstance(stamped, str):
        stamped = {"due_at": stamped}
    stamped = stamped or {}
    if stamped:
        return stamped
    return {"due_hint": missing_due_hint(artifact_type)} if hint else {}


ANSWERS = "answer each: do it, snooze it (update-artifacts with a new due_at), or drop it (complete or resolve it)"


def due_lines(items: list[dict]) -> list[str]:
    """One line per due item, plus a count of what the cap left out. Shared by bootstrap and the SessionStart context."""
    lines = [f"{i['type']} {str(i['id'])[:8]} ({i['due_at']}): {str(i['text'])[:100]}" for i in items]
    more = items[-1].get("more") if items else None
    if more:
        lines.append(f"... and {more} more due")
    return lines
