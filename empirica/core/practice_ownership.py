"""Which practice owns a session — asked when the local store does not know it.

A transaction opened from one practice's checkout against another practice's
session writes into the wrong store. It happened: a peer cd'd into core's
checkout and left an orphan PREFLIGHT row here (unknown 4ca7fcc7). David's ask,
relayed by mesh-support in prop_337a3aqsffem7a7ozcbruc3vba, is to stop it.

A miss in the local store is two different situations, and only one is an error:

- the session is real and lives in ANOTHER registered practice — refuse, because
  the write would land in someone else's store;
- the session exists nowhere — warn, as PREFLIGHT already does. That is the
  first transaction of a session created outside the CLI, which is legitimate.

This module answers only the discriminating question, and it is a pure read of
other practices' stores. Every path is a parameter so a test can build its own
registry rather than reading the developer's.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Practices are registered here, one `project` row each, with the checkout's
#: `.empirica` directory in `metadata.trajectory_path`.
DEFAULT_WORKSPACE_DB = Path.home() / ".empirica" / "workspace" / "workspace.db"


def registered_practices(workspace_db: Path | None = None) -> list[dict[str, Any]]:
    """Every registered practice as {name, project_id, db_path}, stores that exist.

    Returns [] when the registry is absent or unreadable: on a registry we cannot
    read, the honest answer is "no evidence this belongs to anyone else", which
    leaves PREFLIGHT's existing warning in place rather than refusing the work.
    """
    path = Path(workspace_db) if workspace_db else DEFAULT_WORKSPACE_DB
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT entity_id, display_name, metadata FROM entity_registry WHERE entity_type = 'project'"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("practice registry unreadable (%s): %s", path, exc)
        return []
    for project_id, name, metadata in rows:
        try:
            meta = json.loads(metadata) if metadata else {}
        except (ValueError, TypeError):
            meta = {}
        trajectory = meta.get("trajectory_path")
        if not trajectory:
            continue
        db_path = Path(trajectory) / "sessions" / "sessions.db"
        if db_path.is_file():
            out.append({"name": name, "project_id": project_id, "db_path": db_path})
    return out


def find_owning_practice(
    session_id: str,
    *,
    workspace_db: Path | None = None,
    exclude_db: Path | None = None,
) -> dict[str, Any] | None:
    """The practice whose store holds this session, or None.

    `exclude_db` is the store already asked — the local one — so a session found
    only there is not reported as belonging elsewhere. Compared by resolved path,
    since the registry and the caller reach the same file by different routes
    (a symlinked checkout, a relative cwd).
    """
    if not session_id:
        return None
    skip = None
    if exclude_db is not None:
        try:
            skip = Path(exclude_db).resolve()
        except OSError:
            skip = Path(exclude_db)
    for practice in registered_practices(workspace_db):
        db_path: Path = practice["db_path"]
        try:
            if skip is not None and db_path.resolve() == skip:
                continue
        except OSError:
            pass
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                found = conn.execute("SELECT 1 FROM sessions WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            # A locked or older store is not evidence either way. Skip it and say
            # so in the log; refusing on a read failure would block real work.
            logger.debug("session lookup skipped for %s: %s", db_path, exc)
            continue
        if found:
            return {"name": practice["name"], "project_id": practice["project_id"], "db_path": str(db_path)}
    return None
