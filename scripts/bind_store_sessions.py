#!/usr/bin/env python3
"""Bind sessions that carry no project_id to the project their STORE belongs to.

`sessions.db` lives inside a project's own `.empirica/`, so a row's location
already determines its project (David, 2026-09-23). A NULL `project_id` in
core's store still means core — it is a missing stamp, not an unknown.

That is why this is a repair and not a guess: the id comes from the
`project.yaml` sitting beside the database, which is the checkout's canonical
identity, and never from the cwd, a registry, or an inference about which of
several practices it might have been.

Measured on core when this was written: 525 sessions with no project_id, of
which 506 carry `ai_id = 'test-ai'` and are suite leakage that
`prune_orphan_reflexes.py` covers. The remaining 19 are real, and 16 of them
have no finding, goal or reflex from which a project could otherwise be
inferred — so without this they stay unbound permanently, and anything that
inherits scope from the session row is born unscoped under them.

Test rows are left alone: binding them would fold suite noise into the
practice's own counts, which is the defect the pruning work exists to undo.

Dry run by default. `--apply` takes an online backup first and prints its path.
Run from the project root.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _store_project_id(db_path: Path) -> tuple[str | None, str]:
    """(project_id, where it came from) for the store at `db_path`.

    `.empirica/sessions/sessions.db` → `.empirica/project.yaml`.
    """
    cfg = db_path.resolve().parent.parent / "project.yaml"
    if not cfg.is_file():
        return None, f"no project.yaml beside the store (looked at {cfg})"
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text()) or {}
    except Exception as exc:
        return None, f"project.yaml unreadable: {type(exc).__name__}: {exc}"
    pid = data.get("project_id")
    return (str(pid), str(cfg)) if pid else (None, f"project.yaml carries no project_id ({cfg})")


def _stampable_tables(conn) -> list[str]:
    """Tables carrying BOTH a project_id and a session_id, so a row can be bound
    through the session that owns it. `projects` is excluded: its `id` is the
    project, not a reference to one."""
    out = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if name == "projects":
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()}
        if {"project_id", "session_id"} <= cols:
            out.append(name)
    return out


def _free_backup_path(db_path: Path) -> Path:
    """A backup name nothing else is using.

    The stamp is per-SECOND, and the upgrade guide tells operators to run these
    scripts back to back on one store — so two runs finishing in the same second
    wrote the same filename, and the survivor was the POST-prune copy. A backup
    that can be silently overwritten by the next step is not a rollback.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    n = 2
    while candidate.exists():
        candidate = db_path.with_name(f"{db_path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=".empirica/sessions/sessions.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(json.dumps({"ok": False, "error": f"no database at {db_path}"}))
        return 1
    project_id, source = _store_project_id(db_path)
    if not project_id:
        print(json.dumps({"ok": False, "error": source}))
        return 1

    conn = sqlite3.connect(str(db_path))
    where = "project_id IS NULL AND (ai_id IS NULL OR ai_id != 'test-ai')"
    rows = conn.execute(f"SELECT ai_id, count(*) FROM sessions WHERE {where} GROUP BY ai_id").fetchall()
    unbound = conn.execute(f"SELECT count(*) FROM sessions WHERE {where}").fetchone()[0]
    test_rows = conn.execute("SELECT count(*) FROM sessions WHERE project_id IS NULL AND ai_id = 'test-ai'").fetchone()[
        0
    ]
    unstamped_artifacts = {
        t: n
        for t in _stampable_tables(conn)
        for (n,) in [
            conn.execute(
                f"SELECT count(*) FROM {t} WHERE project_id IS NULL AND session_id IN (SELECT session_id FROM sessions)"
            ).fetchone()
        ]
        if n
    }
    report: dict = {
        "ok": True,
        "unstamped_artifacts": unstamped_artifacts,
        "mode": "apply" if args.apply else "dry-run",
        "store": str(db_path),
        "project_id": project_id,
        "project_id_source": source,
        "unbound_sessions": unbound,
        "by_ai_id": dict(rows),
        "left_alone_test_sessions": test_rows,
    }
    if not args.apply:
        print(json.dumps(report, indent=2))
        return 0

    backup = _free_backup_path(db_path)
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    report["backup"] = str(backup)

    with conn:
        report["bound"] = conn.execute(f"UPDATE sessions SET project_id = ? WHERE {where}", (project_id,)).rowcount
        # Binding the session alone left its ARTIFACTS unstamped — 1697 reflexes
        # on core had a bound session and a NULL project_id of their own, so a
        # per-project count still missed them while the report said "remaining:
        # 0". Same rule as above and no wider: the row is stamped only when its
        # session is one this store knows.
        stamped: dict[str, int] = {}
        for table in _stampable_tables(conn):
            n = conn.execute(
                f"UPDATE {table} SET project_id = ? WHERE project_id IS NULL"
                " AND session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)",
                (project_id, project_id),
            ).rowcount
            if n:
                stamped[table] = n
    report["artifacts_stamped"] = stamped
    report["remaining"] = conn.execute(f"SELECT count(*) FROM sessions WHERE {where}").fetchone()[0]
    print(json.dumps(report, indent=2))
    return 0 if report["remaining"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
