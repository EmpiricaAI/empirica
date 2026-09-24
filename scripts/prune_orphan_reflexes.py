#!/usr/bin/env python3
"""Remove reflex rows the test suite left in the live store.

Until aada19519 pinned EMPIRICA_SESSION_DB for the whole suite, tests wrote
reflexes into the developer's real sessions.db under project ids that no
`projects` row names. They sit in the denominator of any reflex count that does
not filter by project id, and they have already produced one false outage report.

A row is removed only when it is PROVABLY a test row:

- its project id has no `projects` row, and
- its session is one of the suite's fixed session ids (TEST_SESSION_IDS), or a
  session whose `sessions` row says `ai_id = 'test-ai'`.

An unregistered project id is not enough by itself. On core, real sessions
(ai_id claude-code, January to May 2026) wrote reflexes under unregistered ids
too; those are practice history filed under the wrong id and are kept. So are
rows whose session has no `sessions` row and no test id, because nothing shows
whether they were tests or real sessions that lost their row. Both kept groups
are counted in the report.

No CLI verb deletes reflexes (delete-artifacts covers graph artifacts only), so
this is a reviewed script rather than ad-hoc SQL. Dry run by default. `--apply`
takes an online backup first and prints its path. Select one id with
`--project-id` (a registered id is refused) or every unregistered id with
`--all-unregistered`. Run from the project root.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

#: Session ids the test suite used literally.
TEST_SESSION_IDS = ("test-session", "test-git-state-session", "other-session", "test-cli-create")

#: A row is a test row when its session is one of those fixed ids AND no real
#: session owns that id, or when the session's own row says ai_id test-ai.
#: Matching on the id alone meant that on any store where a real session happened
#: to carry one of them, its reflexes were selected for deletion while the report
#: still said "provable test rows" — and the selected and kept buckets overlapped.
#: "None has a sessions row on core" was an assertion about one box, not a check.
_TEST_ROW = (
    "((session_id IN ({ids}) AND session_id NOT IN (SELECT session_id FROM sessions))"
    " OR session_id IN (SELECT session_id FROM sessions WHERE ai_id = 'test-ai'))"
).format(ids=",".join("?" * len(TEST_SESSION_IDS)))
#: NULL-safe on BOTH sides, deliberately. `project_id NOT IN (SELECT id FROM
#: projects)` evaluates to NULL — never TRUE — for a row whose project_id is
#: NULL, so 1713 of core's 9378 reflexes were invisible to the scope, to both
#: kept buckets, and to the partition check, which then reported a complete
#: partition over 62% of the rows it claimed to cover. One NULL in `projects.id`
#: would have made the predicate NULL for every row and selected nothing, still
#: reporting clean.
#:
#: A NULL project_id is "unstamped", not "foreign": a store belongs to one
#: project, so such a row is this practice's (see bind_store_sessions.py). It is
#: in scope here only so the counts are honest — selection still requires the row
#: to be provably a test row.
_UNREGISTERED = "(project_id IS NULL OR NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = reflexes.project_id))"


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
    ap.add_argument(
        "--db",
        default=os.environ.get("EMPIRICA_SESSION_DB") or ".empirica/sessions/sessions.db",
        help="store to operate on (default: $EMPIRICA_SESSION_DB, else the project-local one)",
    )
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project-id", help="one unregistered project id")
    scope.add_argument(
        "--all-unregistered",
        action="store_true",
        help="every row whose project is unregistered, or unstamped (NULL)",
    )
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(json.dumps({"ok": False, "error": f"no database at {db_path}"}))
        return 1
    conn = sqlite3.connect(str(db_path))

    if args.project_id:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (args.project_id,)).fetchone():
            print(json.dumps({"ok": False, "error": f"{args.project_id} is a registered project; refusing"}))
            return 1
        scope_sql, scope_params = "project_id = ?", (args.project_id,)
    else:
        scope_sql, scope_params = _UNREGISTERED, ()

    target = f"{scope_sql} AND {_UNREGISTERED} AND {_TEST_ROW}"
    params = (*scope_params, *TEST_SESSION_IDS)

    def count(where: str, p: tuple) -> int:
        return conn.execute(f"SELECT count(*) FROM reflexes WHERE {where}", p).fetchone()[0]

    in_scope = count(f"{scope_sql} AND {_UNREGISTERED}", scope_params)
    selected = count(target, params)
    kept_real = count(
        f"{scope_sql} AND {_UNREGISTERED} AND session_id IN (SELECT session_id FROM sessions WHERE ai_id != 'test-ai')",
        scope_params,
    )
    report: dict = {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "scope": args.project_id or "all unregistered project ids",
        "rows_in_scope": in_scope,
        "selected_test_rows": selected,
        "kept_real_sessions": kept_real,
        "kept_unattributable": in_scope - selected - kept_real,
        # The three buckets must partition the scope. A negative remainder means
        # they overlapped, which is a defect in the predicate, not a number.
        "buckets_partition": (in_scope - selected - kept_real) >= 0,
        # Capped at 10, and the cap is stated: a list that silently ends at ten
        # reads as the whole set.
        "by_project": dict(
            conn.execute(
                f"SELECT project_id, count(*) FROM reflexes WHERE {target} GROUP BY 1 ORDER BY 2 DESC LIMIT 10", params
            ).fetchall()
        ),
        "by_project_total": conn.execute(
            f"SELECT count(DISTINCT project_id) FROM reflexes WHERE {target}", params
        ).fetchone()[0],
    }
    if not args.apply:
        print(json.dumps(report, indent=2))
        return 0

    backup = _free_backup_path(db_path)
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    report["backup"] = str(backup)

    with conn:
        report["deleted"] = conn.execute(f"DELETE FROM reflexes WHERE {target}", params).rowcount
    report["remaining_selected"] = count(target, params)
    print(json.dumps(report, indent=2))
    return 0 if report["remaining_selected"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
