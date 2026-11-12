"""Links between artifacts, falsifiers and lessons into the git notes, so a rebuild stops losing them (goal 966808c0).

These lived in SQLite (and lessons in their own store) only. David chose (2026-11-12) one exporter run at each POSTFLIGHT,
in the detached storage pipeline, and in full by ``doctor --reconcile-notes``, over hooking every writer: it covers all of
them, stays off the POSTFLIGHT response path, and leaves the same window the rest of a transaction has.

Links: one note per source artifact, ``empirica/edges/<from_id>``, listing every edge from it. Incremental: a watermark in
``.empirica/notes_export.json`` (the newest edge time and source id exported) says where the last run stopped, so a run
writes only the sources with an edge newer than that, at most ``limit`` of them; the next run resumes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from empirica.core.canonical.empirica_git.note_lifecycle import _git, note_target

logger = logging.getLogger(__name__)

EDGE_PREFIX = "empirica/edges"
WATERMARK_FILE = "notes_export.json"
#: Per POSTFLIGHT run: a first run on a practice with thousands of sources backfills over several runs instead of one.
DEFAULT_LIMIT = 200


def _watermarks(root: str) -> dict:
    try:
        return json.loads((Path(root) / ".empirica" / WATERMARK_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _save_watermark(root: str, kind: str, value) -> None:
    path = Path(root) / ".empirica" / WATERMARK_FILE
    marks = _watermarks(root)
    marks[kind] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(marks, indent=2))
        tmp.replace(path)
    except OSError as e:
        logger.warning("notes export watermark not saved (%s): %s", kind, e)


def _write_note(root: str, ref: str, payload: dict) -> bool:
    try:
        target = note_target(ref, root)
    except Exception as e:  # no HEAD yet: nothing to attach a note to
        logger.debug("note %s not written: %s", ref, e)
        return False
    done = _git(["notes", f"--ref={ref}", "add", "-f", "-F", "-", target], root, input=json.dumps(payload, indent=2))
    return bool(done and done.returncode == 0)


def export_edge_notes(conn, root: str, *, limit: int | None = DEFAULT_LIMIT, full: bool = False) -> dict:
    """Write the edge notes of every source artifact with an edge newer than the watermark (all of them when ``full``).
    Returns ``{"written", "failed", "remaining"}``; ``remaining`` is True when ``limit`` stopped the run early."""
    mark = None if full else _watermarks(root).get("edges")
    since_ts, since_id = (mark or ["", ""])[:2]
    rows = conn.execute(
        "SELECT from_id, MAX(created_at) AS newest FROM artifact_edges GROUP BY from_id "
        "HAVING newest > ? OR (newest = ? AND from_id > ?) ORDER BY newest, from_id",
        (since_ts, since_ts, since_id),
    ).fetchall()
    batch = rows if (full or limit is None) else rows[:limit]
    written = failed = 0
    last = None
    for from_id, newest in batch:
        edges = conn.execute(
            "SELECT to_id, relation, created_at, metadata FROM artifact_edges WHERE from_id = ? ORDER BY created_at, to_id",
            (from_id,),
        ).fetchall()
        payload = {
            "from_id": from_id,
            "edges": [{"to_id": t, "relation": r, "created_at": c, "metadata": m} for t, r, c, m in edges],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        if _write_note(root, f"{EDGE_PREFIX}/{from_id}", payload):
            written += 1
            last = [newest, from_id]
        else:
            failed += 1
            break  # resume from here next run rather than skip past a source whose note was not written
    if last is not None:
        _save_watermark(root, "edges", last)
    return {"written": written, "failed": failed, "remaining": len(batch) < len(rows) or failed > 0}


def discover_edge_notes(root: str) -> list[dict]:
    """Every edge note's payload, for the rebuild."""
    return [p for p in _discover(root, EDGE_PREFIX) if isinstance(p.get("edges"), list)]


FALSIFIER_PREFIX = "empirica/falsifiers"


def export_falsifier_notes(conn, root: str, *, full: bool = False) -> dict:
    """Write the note of every falsifier registered or adjudicated since the watermark (all of them when ``full``): the whole
    row, so a rebuild brings it back with its verdict. ``{"written", "failed"}``."""
    since = 0.0 if full else float(_watermarks(root).get("falsifiers") or 0.0)
    cur = conn.execute(
        "SELECT *, MAX(COALESCE(adjudicated_at, 0), COALESCE(registered_at, 0)) AS changed FROM falsifiers "
        "WHERE MAX(COALESCE(adjudicated_at, 0), COALESCE(registered_at, 0)) > ? ORDER BY changed",
        (since,),
    )
    names = [d[0] for d in cur.description]
    written = failed = 0
    newest = since
    for row in cur.fetchall():
        record = dict(zip(names, row, strict=True))
        changed = record.pop("changed") or 0.0
        if _write_note(root, f"{FALSIFIER_PREFIX}/{record['id']}", {"falsifier": record}):
            written += 1
            newest = max(newest, float(changed))
        else:
            failed += 1
            break
    if written:
        _save_watermark(root, "falsifiers", newest)
    return {"written": written, "failed": failed}


def discover_falsifier_notes(root: str) -> list[dict]:
    """Every falsifier note's row, for the rebuild."""
    return [p["falsifier"] for p in _discover(root, FALSIFIER_PREFIX) if isinstance(p.get("falsifier"), dict)]


def _discover(root: str, prefix: str) -> list[dict]:
    refs = _git(["for-each-ref", "--format=%(refname)", f"refs/notes/{prefix}/"], root)
    out = []
    for full in refs.stdout.split() if refs and refs.returncode == 0 else []:
        short = full.removeprefix("refs/notes/")
        listed = _git(["notes", f"--ref={short}", "list"], root)
        first = (listed.stdout.splitlines() or [""])[0].split() if listed and listed.returncode == 0 else []
        if len(first) < 2:
            continue
        shown = _git(["notes", f"--ref={short}", "show", first[1]], root)
        try:
            payload = json.loads(shown.stdout) if shown and shown.returncode == 0 else None
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            out.append(payload)
    return out
