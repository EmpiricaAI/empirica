#!/usr/bin/env python3
"""Remove reflex rows left in the live store by the test suite, one project id at a time.

Until aada19519 pinned EMPIRICA_SESSION_DB for the whole suite, tests wrote
reflexes into the developer's real sessions.db under a project id that no
`projects` row names. On core that was 9308 rows under 489d07a11e939ff9. They sit
in the denominator of any reflex count that does not filter by project id, and
they have already produced one false outage report.

No CLI verb deletes reflexes (delete-artifacts covers graph artifacts only), so
this is a reviewed script rather than ad-hoc SQL:

- dry run by default; `--apply` deletes;
- `--project-id` is required and named explicitly, and a project id that has a
  `projects` row is refused, so real work cannot be selected by mistake;
- `--apply` takes an online backup of the database first and prints its path.

Run from the project root.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=".empirica/sessions/sessions.db")
    ap.add_argument("--project-id", required=True, help="the unregistered project id whose reflexes to remove")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(json.dumps({"ok": False, "error": f"no database at {db_path}"}))
        return 1
    conn = sqlite3.connect(str(db_path))
    registered = conn.execute("SELECT 1 FROM projects WHERE id = ?", (args.project_id,)).fetchone()
    if registered:
        print(json.dumps({"ok": False, "error": f"{args.project_id} is a registered project; refusing"}))
        return 1

    where = "project_id = ?"
    rows = conn.execute(
        f"SELECT count(*), count(DISTINCT session_id) FROM reflexes WHERE {where}", (args.project_id,)
    ).fetchone()
    by_session = conn.execute(
        f"SELECT session_id, count(*) FROM reflexes WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
        (args.project_id,),
    ).fetchall()
    report: dict = {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "project_id": args.project_id,
        "reflexes": rows[0],
        "sessions": rows[1],
        "top_sessions": dict(by_session),
    }
    if not args.apply:
        print(json.dumps(report, indent=2))
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    report["backup"] = str(backup)

    with conn:
        deleted = conn.execute(f"DELETE FROM reflexes WHERE {where}", (args.project_id,)).rowcount
    remaining = conn.execute(f"SELECT count(*) FROM reflexes WHERE {where}", (args.project_id,)).fetchone()[0]
    report["deleted"] = deleted
    report["remaining"] = remaining
    print(json.dumps(report, indent=2))
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
