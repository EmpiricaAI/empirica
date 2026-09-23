#!/usr/bin/env python3
"""Remove the rows the test suite wrote into a live store.

Until aada19519 pinned EMPIRICA_SESSION_DB for the whole suite, tests wrote into
the developer's real `sessions.db`. `prune_orphan_reflexes.py` removed the
reflexes; this removes the rest, and it is the same rule throughout: a row goes
only when something in the row itself says it is a fixture, never because it
looks unused.

Measured on core when this was written:

    sessions              506   ai_id = 'test-ai'
    projects              614   fixture names (test-…, Test …, "Test project")
    auto_captured_issues  260   under a test session
    attention_budgets     114   under a test session
    goals                   2   under a test session

A project row is only removed once nothing references it: every table carrying a
`project_id` is counted first, and an id with any referent is kept and reported.
Deleting a referenced project would leave rows pointing at nothing, which is the
shape this whole cleanup exists to undo.

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

#: Session ids the suite used literally, none of which a real session owns.
TEST_SESSION_IDS = ("test-session", "test-git-state-session", "other-session", "test-cli-create")

#: A session row is a fixture when its own ai_id says so.
_TEST_SESSION = "(ai_id = 'test-ai' OR session_id IN ({ids}))".format(ids=",".join("?" * len(TEST_SESSION_IDS)))

#: A project row is a fixture when its own name or description says so. Matched
#: on the row, never on "nothing points at it": an unreferenced real project is
#: a quiet project, not a test one.
_TEST_PROJECT = (
    "(name LIKE 'test-%' OR name LIKE 'Test %' OR name LIKE 'TestCase%' OR name LIKE '%-test-%'"
    " OR description LIKE 'Test project%')"
)


def _tables_with(conn, column: str) -> list[str]:
    out = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()}
        if column in cols:
            out.append(name)
    return out


def _workspace_strays(conn) -> list[tuple[str, str, str]]:
    """Registry project rows that are provably a test's leftovers.

    Three conditions together, never one alone: the row is a `project`, its
    `trajectory_path` no longer exists on disk, and that path is under a
    temporary directory. A missing path alone would match a checkout on an
    unmounted disk; a temp path alone would match a deliberate scratch project.
    Rows with any referent elsewhere in the store are excluded by the caller.
    """
    out = []
    try:
        rows = conn.execute(
            "SELECT entity_id, display_name, json_extract(metadata, '$.trajectory_path')"
            " FROM entity_registry WHERE entity_type = 'project'"
        ).fetchall()
    except Exception:
        return []
    for entity_id, name, path in rows:
        if not path or Path(path).exists():
            continue
        if not str(path).startswith(("/tmp/", "/var/tmp/", "/private/var/folders/")):
            continue
        out.append((entity_id, name, path))
    return out


def _referenced_in_workspace(conn, entity_id: str) -> list[str]:
    hits = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if table == "entity_registry":
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col in ("entity_id", "related_entity_id", "group_id"):
            if col in cols:
                n = conn.execute(f"SELECT count(*) FROM {table} WHERE {col} = ?", (entity_id,)).fetchone()[0]
                if n:
                    hits.append(f"{table}.{col}")
    return hits


def _prune_workspace(path: Path, apply: bool) -> dict:
    """Remove registry rows for projects that only ever existed in a test's tmp dir."""
    if not path.is_file():
        return {"ok": False, "error": f"no workspace store at {path}"}
    conn = sqlite3.connect(str(path))
    strays = [(e, n, p) for e, n, p in _workspace_strays(conn) if not _referenced_in_workspace(conn, e)]
    report = {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "store": str(path),
        "strays": [{"entity_id": e, "name": n, "path": p} for e, n, p in strays],
    }
    if not apply or not strays:
        return report
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    report["backup"] = str(backup)
    with conn:
        report["deleted"] = conn.executemany(
            "DELETE FROM entity_registry WHERE entity_id = ?", [(e,) for e, _n, _p in strays]
        ).rowcount
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=".empirica/sessions/sessions.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--workspace",
        nargs="?",
        const=str(Path.home() / ".empirica" / "workspace" / "workspace.db"),
        help="Instead of the session store, prune registry rows for projects whose path is a vanished temp dir",
    )
    args = ap.parse_args()

    if args.workspace:
        print(json.dumps(_prune_workspace(Path(args.workspace), args.apply), indent=2))
        return 0

    db_path = Path(args.db)
    if not db_path.is_file():
        print(json.dumps({"ok": False, "error": f"no database at {db_path}"}))
        return 1
    conn = sqlite3.connect(str(db_path))

    # The four literal ids own no `sessions` row — that is why the suite's rows
    # under them were invisible to a sessions-table query, and why 114
    # attention_budgets and 2 goals survived the first pass. They are included
    # by name, whether or not a row exists for them.
    test_sessions = sorted(
        {r[0] for r in conn.execute(f"SELECT session_id FROM sessions WHERE {_TEST_SESSION}", TEST_SESSION_IDS)}
        | set(TEST_SESSION_IDS)
    )
    test_projects = [r[0] for r in conn.execute(f"SELECT id FROM projects WHERE {_TEST_PROJECT}")]

    # A project id with any referent stays, whatever its name says.
    referenced: dict[str, list[str]] = {}
    if test_projects:
        marks = ",".join("?" * len(test_projects))
        for table in _tables_with(conn, "project_id"):
            if table == "projects":
                continue
            rows = conn.execute(
                f"SELECT DISTINCT project_id FROM {table} WHERE project_id IN ({marks})", test_projects
            ).fetchall()
            for (pid,) in rows:
                referenced.setdefault(pid, []).append(table)
    removable_projects = [p for p in test_projects if p not in referenced]

    plan: dict[str, int] = {}
    session_tables = _tables_with(conn, "session_id")
    if test_sessions:
        marks = ",".join("?" * len(test_sessions))
        for table in session_tables:
            n = conn.execute(f"SELECT count(*) FROM {table} WHERE session_id IN ({marks})", test_sessions).fetchone()[0]
            if n:
                plan[table] = n
    plan["projects"] = len(removable_projects)

    report: dict = {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "store": str(db_path),
        "test_sessions": len(test_sessions),
        "test_projects": len(test_projects),
        "kept_referenced_projects": dict(list(referenced.items())[:10]),
        "kept_referenced_project_count": len(referenced),
        "rows_by_table": plan,
        "rows_total": sum(plan.values()),
    }
    if not args.apply:
        print(json.dumps(report, indent=2))
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    report["backup"] = str(backup)

    deleted: dict[str, int] = {}
    with conn:
        if test_sessions:
            marks = ",".join("?" * len(test_sessions))
            for table in plan:
                if table == "projects":
                    continue
                deleted[table] = conn.execute(
                    f"DELETE FROM {table} WHERE session_id IN ({marks})", test_sessions
                ).rowcount
        if removable_projects:
            marks = ",".join("?" * len(removable_projects))
            deleted["projects"] = conn.execute(
                f"DELETE FROM projects WHERE id IN ({marks})", removable_projects
            ).rowcount
    report["deleted"] = deleted
    report["deleted_total"] = sum(deleted.values())
    remaining = conn.execute(f"SELECT count(*) FROM sessions WHERE {_TEST_SESSION}", TEST_SESSION_IDS).fetchone()[0]
    report["remaining_test_sessions"] = remaining
    print(json.dumps(report, indent=2))
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
