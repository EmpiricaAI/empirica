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


#: A note stamped within this of its row describes the same event. Beyond it, a
#: note dated LATER than its row was written by a process that stamped `now` for
#: an artifact it did not create.
_SAME_EVENT_SECONDS = 60


def _classify(note_value: str | None, row_value: str | None) -> str | None:
    """`fabricated`, `missing`, `write_lag`, or None when they agree."""
    if not row_value:
        return None
    if not note_value:
        return "missing"
    if note_value == row_value:
        return None
    try:
        note_t = datetime.fromisoformat(note_value)
        row_t = datetime.fromisoformat(row_value)
    except (TypeError, ValueError):
        return "fabricated"
    delta = (note_t - row_t).total_seconds()
    if abs(delta) <= _SAME_EVENT_SECONDS:
        return "write_lag"
    return "fabricated" if delta > 0 else "write_lag"


def _note_payload(cat, kind: str, artifact_id: str) -> dict | None:
    blob = cat.first_note_blob(f"refs/notes/empirica/{kind}/{artifact_id}")
    if blob is None:
        return None
    try:
        payload = json.loads(blob.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _note_targets(ref_short: str) -> list[tuple[str, str]]:
    """(blob, annotated commit) pairs for a note ref, in `git notes list` order."""
    out = subprocess.run(["git", "notes", f"--ref={ref_short}", "list"], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        return []
    pairs = []
    for line in out.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def _rewrite_note_in_place(ref_short: str, commit: str, payload: dict) -> bool:
    """Write `payload` onto the commit the note ALREADY annotates.

    The stores annotate `HEAD`, which is correct for a new artifact and wrong for
    a rewrite: it leaves the old note on its original commit and adds a second one
    on HEAD, so the ref holds two payloads and a reader takes whichever comes
    first in tree order. Repairing in place is the whole difference.
    """
    done = subprocess.run(
        ["git", "notes", f"--ref={ref_short}", "add", "-f", "-F", "-", commit],
        input=json.dumps(payload, indent=2),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return done.returncode == 0


def _apply_repair(ref_short, targets, head, payload, want_created, want_resolved, report) -> bool:
    """Collapse a ref to one note on its own commit, with the row's timestamps.

    An earlier run of this repair wrote to HEAD instead of the note's own commit,
    leaving two payloads under one ref where a reader takes whichever comes first
    in tree order. The HEAD copy is dropped and the original kept.
    """
    keep = [t for t in targets if t[1] != head] or targets
    target_commit = keep[0][1]
    for _blob, commit in targets:
        if commit != target_commit:
            subprocess.run(
                ["git", "notes", f"--ref={ref_short}", "remove", commit],
                capture_output=True,
                text=True,
                timeout=10,
            )
            report["duplicate_notes_removed"] += 1
    fixed = dict(payload)
    if want_created:
        fixed["created_at"] = want_created
    if want_resolved:
        fixed["resolved_at"] = want_resolved
    return _rewrite_note_in_place(ref_short, target_commit, fixed)


def _repair_timestamps(conn, apply: bool) -> dict:
    """Rewrite notes whose timestamps disagree with the row that owns them.

    The first backfill let the stores stamp `now`, so a note written for an
    already-resolved artifact recorded the backfill instant as its resolution
    date. Notes are canonical and `rebuild` imports them back, so those dates
    would have overwritten the true ones SQLite still holds.

    Only the note's timestamp fields are touched; everything else in the payload
    is left exactly as it was, and the note stays on its own commit.
    """
    from empirica.core.canonical.empirica_git.goal_store import GitCatFileBatch

    report: dict = {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "checked": {"findings": 0, "unknowns": 0},
        # Three outcomes, kept apart on purpose. `fabricated` is a note dated
        # materially LATER than the row it describes — a writer that stamped
        # `now` for an old artifact. `missing` is a resolved row whose note never
        # learned the date. `write_lag` is the note being stamped milliseconds
        # after the row, which is the same event and is left alone: repairing it
        # would rewrite thousands of notes for a 20 ms difference.
        "fabricated": {"findings": 0, "unknowns": 0},
        "missing": {"findings": 0, "unknowns": 0},
        "write_lag_ignored": {"findings": 0, "unknowns": 0},
        "rewritten": {"findings": 0, "unknowns": 0},
        "duplicate_notes_removed": 0,
        "failed": [],
        "examples": [],
    }
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    with GitCatFileBatch(".") as cat:
        for kind, table in (("findings", "project_findings"), ("unknowns", "project_unknowns")):
            for r in conn.execute(f"SELECT * FROM {table}"):
                ref_short = f"empirica/{kind}/{r['id']}"
                payload = _note_payload(cat, kind, r["id"])
                if payload is None:
                    continue
                report["checked"][kind] += 1
                want_created = _iso(r["created_timestamp"])
                want_resolved = _iso(r["resolved_timestamp"]) if r["is_resolved"] else None
                verdicts = {
                    _classify(payload.get("created_at"), want_created),
                    _classify(payload.get("resolved_at"), want_resolved),
                }
                targets = _note_targets(ref_short)
                duplicated = len(targets) > 1
                if "fabricated" in verdicts:
                    report["fabricated"][kind] += 1
                elif "missing" in verdicts:
                    report["missing"][kind] += 1
                elif "write_lag" in verdicts:
                    report["write_lag_ignored"][kind] += 1
                    if not duplicated:
                        continue
                elif not duplicated:
                    continue
                if len(report["examples"]) < 5:
                    report["examples"].append(
                        {
                            "id": r["id"],
                            "note_resolved_at": payload.get("resolved_at"),
                            "row_resolved_at": want_resolved,
                            "notes_on_commits": len(targets),
                        }
                    )
                if not apply or not targets:
                    continue
                if _apply_repair(ref_short, targets, head, payload, want_created, want_resolved, report):
                    report["rewritten"][kind] += 1
                elif len(report["failed"]) < 10:
                    report["failed"].append(r["id"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=".empirica/sessions/sessions.db")
    ap.add_argument("--apply", action="store_true", help="write the notes; without it, only report")
    ap.add_argument("--limit", type=int, default=0, help="stop after N writes (0 = all)")
    ap.add_argument(
        "--repair-timestamps",
        action="store_true",
        help=(
            "Rewrite notes whose created_at/resolved_at disagree with the row's own "
            "timestamps. The first run of this script stamped `now` for both, so every "
            "note it wrote for an already-resolved artifact dates that resolution to the "
            "backfill instant. Dry run unless --apply."
        ),
    )
    args = ap.parse_args()

    if not Path(args.db).is_file():
        print(json.dumps({"ok": False, "error": f"no database at {args.db}"}))
        return 1
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    if args.repair_timestamps:
        print(json.dumps(_repair_timestamps(conn, args.apply), indent=2))
        return 0

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
            # The row's OWN resolution time. Without it the store stamped `now`,
            # so every note this wrote for an already-resolved artifact dated the
            # resolution to the backfill instant — and `rebuild` imports notes
            # back over SQLite, so the repair would have destroyed the true dates.
            resolved_at=_iso(r["resolved_timestamp"]) if r["is_resolved"] else None,
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
            created_at=_iso(r["created_timestamp"]),
            resolved_at=_iso(r["resolved_timestamp"]) if r["is_resolved"] else None,
        )
        (written if ok else failed)["unknowns"] += 1
        if not ok:
            failed_ids["unknowns"].append(r["id"])
        writes += 1
    print(json.dumps(report, indent=2))
    return 0 if not any(failed.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
