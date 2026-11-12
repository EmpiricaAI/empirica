"""Closing sessions in the workspace registry (``global_sessions``), and reconciling the rows that were never closed (goal 119e2972).

A session is registered in ``~/.empirica/workspace/workspace.db`` when it is created, and nothing closed it: every path that ends a
session wrote ``end_time`` to the practice's own database only. By 2026-10-29 the registry held 10,870 active rows against 1,053
closed ones, the closed ones all from before April 2026. Readers take the most recent *active* row, and ``projects-unregister
--local`` counted the dead rows as references.

:func:`close_global_sessions` is called wherever a session ends. :func:`plan_reconcile` / :func:`apply_reconcile` close the
backlog (``doctor --reconcile-sessions``): an active row is closed when its session ended in its practice's database, when the
session is absent from every database it names, or when its practice is gone from disk. A database that cannot be read is
*unknown* and its rows stay active: unknown is not ended.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def workspace_db_path() -> Path:
    return Path.home() / ".empirica" / "workspace" / "workspace.db"


def close_global_sessions(session_ids: Iterable[str], *, closed_at: float | None = None) -> int:
    """Mark these sessions closed in the registry. Returns how many rows changed; never raises (a session still ends when the
    registry cannot be written, and the warning says so)."""
    ids = [s for s in session_ids if s]
    path = workspace_db_path()
    if not ids or not path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            from empirica.data.repositories.workspace_db import ensure_global_sessions_closed_at

            ensure_global_sessions_closed_at(conn)
            cur = conn.executemany(
                "UPDATE global_sessions SET status = 'closed', closed_at = ? WHERE session_id = ? AND status = 'active'",
                [(closed_at or time.time(), s) for s in ids],
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning("global session registry not updated for %d ended session(s): %s", len(ids), e)
        return 0


def _ended_at(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()  # ended, at a time we cannot read: closed now


def _local_state(db: Path, session_id: str) -> tuple[str, float | None]:
    """('ended', when) | ('live', None) | ('absent', None) for one practice database; raises sqlite3.Error when unreadable."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        for table in ("sessions", "subagent_sessions"):
            try:
                row = conn.execute(f"SELECT end_time FROM {table} WHERE session_id = ?", (session_id,)).fetchone()
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    continue
                raise
            if row is not None:
                return ("ended", _ended_at(row[0])) if row[0] else ("live", None)
        return "absent", None
    finally:
        conn.close()


def plan_reconcile(conn: sqlite3.Connection) -> dict:
    """What a reconcile would close, and why. Reads only."""
    paths = dict(conn.execute("SELECT id, trajectory_path FROM global_projects").fetchall())
    rows = conn.execute(
        "SELECT session_id, origin_project_id, current_project_id FROM global_sessions WHERE status = 'active'"
    ).fetchall()
    close: list[dict] = []
    counts = {"active": len(rows), "ended": 0, "absent": 0, "practice_gone": 0, "live": 0, "unknown": 0}
    unknown_dbs: dict[str, str] = {}
    for session_id, origin, current in rows:
        project_ids = [p for p in dict.fromkeys((current, origin)) if p]
        dbs, gone = [], 0
        for pid in project_ids:
            traj = paths.get(pid)
            if not traj or not Path(traj).exists():
                gone += 1
                continue
            dbs.append(Path(traj) / "sessions" / "sessions.db")
        states = []
        for db in dbs:
            if not db.exists():
                states.append(("absent", None))
                continue
            try:
                states.append(_local_state(db, session_id))
            except sqlite3.Error as e:
                unknown_dbs[str(db)] = str(e)
                states.append(("unknown", None))
        ended = [when for state, when in states if state == "ended"]
        if any(state == "live" for state, _ in states):
            counts["live"] += 1
        elif ended:
            counts["ended"] += 1
            close.append({"session_id": session_id, "reason": "ended", "closed_at": max(w or 0 for w in ended) or None})
        elif any(state == "unknown" for state, _ in states):
            counts["unknown"] += 1
        elif states:
            counts["absent"] += 1
            close.append({"session_id": session_id, "reason": "absent", "closed_at": None})
        else:  # no project id, or every practice it names is gone from disk
            counts["practice_gone"] += 1
            close.append({"session_id": session_id, "reason": "practice_gone", "closed_at": None})
    return {"counts": counts, "close": close, "unknown_dbs": unknown_dbs}


def apply_reconcile(conn: sqlite3.Connection, plan: dict) -> int:
    """Close what the plan names, in one transaction, keeping a row active that was closed or reopened since the plan."""
    from empirica.data.repositories.workspace_db import ensure_global_sessions_closed_at

    ensure_global_sessions_closed_at(conn)
    now = time.time()
    with conn:
        cur = conn.executemany(
            "UPDATE global_sessions SET status = 'closed', closed_at = ? WHERE session_id = ? AND status = 'active'",
            [(item["closed_at"] or now, item["session_id"]) for item in plan["close"]],
        )
    return cur.rowcount
