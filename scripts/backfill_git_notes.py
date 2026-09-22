#!/usr/bin/env python3
"""Write a git note for every finding and unknown SQLite has and the notes do not.

Git notes are the canonical log; `rebuild` imports them back and drops what is
absent. `log-artifacts` wrote SQLite only from 2026-04-23 until 58f50cd30, so on
core 2435 of 4981 findings had no note, and every resolution of those findings
had nothing to mirror into. This adds the missing notes, carrying the row's own
timestamp and resolution state. It never touches an artifact that already has a
note, and it never deletes.

Dry run by default. `--apply` writes. Run from the project root.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _noted(kind: str) -> set[str]:
    out = subprocess.run(
        ["git", "for-each-ref", f"refs/notes/empirica/{kind}/", "--format=%(refname:lstrip=4)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.split())


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat() if ts else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=".empirica/sessions/sessions.db")
    ap.add_argument("--apply", action="store_true", help="write the notes; without it, only report")
    ap.add_argument("--limit", type=int, default=0, help="stop after N writes (0 = all)")
    args = ap.parse_args()

    if not Path(args.db).is_file():
        print(json.dumps({"ok": False, "error": f"no database at {args.db}"}))
        return 1
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    findings = [
        r
        for r in conn.execute(
            "SELECT f.*, s.ai_id FROM project_findings f LEFT JOIN sessions s ON s.session_id = f.session_id"
        )
        if r["id"] not in _noted("findings")
    ]
    unknowns = [
        r
        for r in conn.execute(
            "SELECT u.*, s.ai_id FROM project_unknowns u LEFT JOIN sessions s ON s.session_id = u.session_id"
        )
        if r["id"] not in _noted("unknowns")
    ]
    written = {"findings": 0, "unknowns": 0}
    failed = {"findings": 0, "unknowns": 0}
    # A count says something failed; the ids say which, so the row can be found
    # without re-deriving the whole missing set.
    failed_ids: dict[str, list[str]] = {"findings": [], "unknowns": []}
    report: dict = {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "findings_missing": len(findings),
        "findings_missing_resolved": sum(1 for r in findings if r["is_resolved"]),
        "unknowns_missing": len(unknowns),
        "unknowns_missing_resolved": sum(1 for r in unknowns if r["is_resolved"]),
        "written": written,
        "failed": failed,
        "failed_ids": failed_ids,
    }
    if not args.apply:
        print(json.dumps(report, indent=2))
        return 0

    from empirica.core.canonical.empirica_git.finding_store import GitFindingStore
    from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore

    fstore, ustore = GitFindingStore(), GitUnknownStore()
    writes = 0
    for r in findings:
        if args.limit and writes >= args.limit:
            break
        data = None
        try:
            data = json.loads(r["finding_data"]) if r["finding_data"] else None
        except ValueError:
            pass
        ok = fstore.store_finding(
            finding_id=r["id"],
            project_id=r["project_id"],
            session_id=r["session_id"],
            ai_id=r["ai_id"] or "unknown",
            finding=r["finding"],
            impact=r["impact"],
            goal_id=r["goal_id"],
            subtask_id=r["subtask_id"],
            subject=r["subject"],
            finding_data=data,
            is_resolved=bool(r["is_resolved"]),
            resolution=r["resolution"],
            superseded_by=r["superseded_by"],
            resolution_kind=r["resolution_kind"],
            created_at=_iso(r["created_timestamp"]),
        )
        (written if ok else failed)["findings"] += 1
        if not ok:
            failed_ids["findings"].append(r["id"])
        writes += 1
    for r in unknowns:
        if args.limit and writes >= args.limit:
            break
        ok = ustore.store_unknown(
            unknown_id=r["id"],
            project_id=r["project_id"],
            session_id=r["session_id"],
            ai_id=r["ai_id"] or "unknown",
            unknown=r["unknown"],
            goal_id=r["goal_id"],
            subtask_id=r["subtask_id"],
            resolved=bool(r["is_resolved"]),
            resolved_by=r["resolved_by"],
        )
        (written if ok else failed)["unknowns"] += 1
        if not ok:
            failed_ids["unknowns"].append(r["id"])
        writes += 1
    print(json.dumps(report, indent=2))
    return 0 if not any(failed.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
