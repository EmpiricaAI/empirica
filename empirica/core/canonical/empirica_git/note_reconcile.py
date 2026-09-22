"""Repair historical notes/sqlite divergence, and let `doctor` report it.

The write path is correct from 697bd613 forward. Everything gardened BEFORE that
left notes and sqlite disagreeing, and because `rebuild --qdrant` imports notes
back INTO sqlite, that backlog is a set of pending reverts rather than stale
copies.

Two divergences, mirroring the two write-path halves:

    notes-only     sqlite deleted the artifact; the note is still active  -> ARCHIVE
    unstamped      sqlite resolved it; the note never learned             -> STAMP

**Dedupe by notes ROOT, not by working directory.** One practice here has 22
worktrees sharing a single notes history; reconciling per-worktree would process
the same store 22 times, and a run that quietly skips 21 paths is indistinguishable
from one that had 1 path to begin with. So the collapse is REPORTED, not silent.

Dry-run is the default and the plan names every artifact id. The receipt is the
point: this rewrites history-adjacent state, and an operator who cannot read what
moved cannot disagree with it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from empirica.core.canonical.empirica_git.goal_store import GitCatFileBatch
from empirica.core.canonical.empirica_git.note_lifecycle import (
    ACTIVE_PREFIX,
    archive_note,
    stamp_resolution,
)

logger = logging.getLogger(__name__)

#: sqlite table + id column + note namespace, per artifact type.
_TYPES = {
    "findings": ("project_findings", "id"),
    "unknowns": ("project_unknowns", "id"),
    "dead_ends": ("project_dead_ends", "id"),
    "decisions": ("decisions", "id"),
    "assumptions": ("assumptions", "id"),
}


def notes_root(project_path: str) -> str | None:
    """The git common dir — the store notes actually live in.

    A worktree has its own `.git` FILE pointing at a shared common dir, so
    `rev-parse --git-common-dir` collapses N worktrees onto the one store they
    share. Keying on the working directory instead reconciles the same notes
    history once per worktree.
    """
    r = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return str(Path(r.stdout.strip()).resolve())


def _active_note_ids(project_path: str, namespace: str) -> set[str]:
    r = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", f"{ACTIVE_PREFIX}/{namespace}/"],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if r.returncode != 0:
        return set()
    return {ref.rsplit("/", 1)[-1] for ref in r.stdout.split() if ref.strip()}


def _notes_lacking_resolution(project_path: str, namespace: str, ids: set[str]) -> set[str]:
    """Of these ids, the ones whose active note does not say it is resolved.

    Reads each note's payload through one `git cat-file --batch`. A note carries
    the resolution as `is_resolved` (the stamp, and the finding store) or as
    `resolved` (the unknown store's own resolve). A note that cannot be read or
    parsed counts as lacking it: the stamp is idempotent, so over-including costs
    one no-op rewrite, while under-including would report a divergence as fixed.
    """
    lacking: set[str] = set()
    with GitCatFileBatch(project_path) as cat:
        for aid in ids:
            blob = cat.first_note_blob(f"{ACTIVE_PREFIX}/{namespace}/{aid}")
            try:
                payload = json.loads(blob.decode()) if blob is not None else None
            except (ValueError, UnicodeDecodeError):
                payload = None
            if not isinstance(payload, dict) or not (payload.get("is_resolved") or payload.get("resolved")):
                lacking.add(aid)
    return lacking


def plan(db, project_path: str, project_id: str | None = None) -> dict:
    """What reconciliation WOULD do. Pure read — nothing moves.

    Returns per-type `{orphaned: [...], unstamped: [...]}` plus totals, so a
    zero is a measured zero rather than an absent key.
    """
    out: dict = {"notes_root": notes_root(project_path), "types": {}, "orphaned_total": 0, "unstamped_total": 0}
    cur = db.conn.cursor()
    for namespace, (table, idcol) in _TYPES.items():
        note_ids = _active_note_ids(project_path, namespace)
        try:
            rows = cur.execute(f"SELECT {idcol}, * FROM {table}").fetchall()
        except Exception as e:
            out["types"][namespace] = {"error": f"{type(e).__name__}: {e}", "orphaned": [], "unstamped": []}
            continue
        live = {str(r[0]) for r in rows}
        resolved = set()
        cols = {d[0] for d in cur.description} if cur.description else set()
        if "is_resolved" in cols:
            try:
                resolved = {
                    str(r[0]) for r in cur.execute(f"SELECT {idcol} FROM {table} WHERE is_resolved = 1").fetchall()
                }
            except Exception:
                resolved = set()

        orphaned = sorted(note_ids - live)
        # A note for a RESOLVED artifact that is still active is correct — that is
        # the design. The refs cannot say whether the note carries the stamp, so the
        # payload is read. Counting `resolved & note_ids` instead reported every
        # resolved artifact as "never received", 2221 on core, and the count could
        # not fall after a repair, so the check stayed WARN forever.
        unstamped = sorted(_notes_lacking_resolution(project_path, namespace, resolved & note_ids))
        out["types"][namespace] = {"orphaned": orphaned, "unstamped": unstamped}
        out["orphaned_total"] += len(orphaned)
        out["unstamped_total"] += len(unstamped)
    return out


def apply(db, project_path: str, the_plan: dict) -> dict:
    """Execute a plan. Reports what LANDED, separately from what was attempted —
    a count of rows found and a count of refs moved are different facts."""
    cur = db.conn.cursor()
    receipt: dict = {"archived": 0, "stamped": 0, "failed": [], "notes_root": the_plan.get("notes_root")}
    for namespace, entry in the_plan.get("types", {}).items():
        for aid in entry.get("orphaned", []):
            r = archive_note(namespace, aid, project_path)
            if r["archived"]:
                receipt["archived"] += 1
            else:
                receipt["failed"].append(f"{namespace}/{aid}: {r['reason']}")
        table, idcol = _TYPES[namespace]
        for aid in entry.get("unstamped", []):
            try:
                row = cur.execute(
                    f"SELECT is_resolved, resolution, resolution_kind FROM {table} WHERE {idcol} = ?",
                    (aid,),
                ).fetchone()
            except Exception:
                row = None
            fields = {"is_resolved": True}
            if row is not None:
                fields.update({"resolution": row[1], "resolution_kind": row[2]})
            r = stamp_resolution(namespace, aid, fields, project_path)
            if r["stamped"]:
                receipt["stamped"] += 1
            elif r["reason"] != "not_present":
                receipt["failed"].append(f"{namespace}/{aid}: {r['reason']}")
    return receipt
