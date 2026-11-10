"""Due dates on goals, unknowns and assumptions (prop_bnviyv2epvg27kgmqycwlflshm).

``due_at`` holds an ISO date. When the date has come and the item is still open, it is *due*: bootstrap and the
SessionStart context list it under Due until the practitioner answers it one of three ways: do it, snooze it (a new
date, through ``update-artifacts``), or drop it (complete, resolve or falsify it with the existing verbs).

A condition trigger ("after release X") is not here yet: a date is the part that can fire on its own.
"""

from __future__ import annotations

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
        except sqlite3.OperationalError:
            continue  # a database older than the column: nothing can be due there yet
        rows += [{"type": kind, "id": r[0], "text": r[1], "due_at": r[2]} for r in found]
    rows.sort(key=lambda r: (r["due_at"], r["type"], r["id"]))
    shown = rows[: max(limit, 0)]
    if shown and len(rows) > len(shown):
        shown[-1]["more"] = len(rows) - len(shown)
    return shown
