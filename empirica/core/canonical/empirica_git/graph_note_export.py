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


def mark_edges_dirty(conn, from_ids) -> None:
    """Record sources whose links were deleted or rewritten, so the next export rewrites their notes from what is committed.

    The export only reads sources that still have a link, ordered by when it was made, so a deleted link (and an id swapped in
    place, which keeps its timestamp) stayed in its note and came back on a rebuild (goal b29f37b0). Callers mark after the
    change; the note is rebuilt from the committed rows at export, so a rolled-back delete leaves the note as it was. Best
    effort: a database outside a practice, or a file that cannot be written, marks nothing."""
    ids = {i for i in from_ids if i}
    if not ids:
        return
    try:
        from empirica.core.canonical.empirica_git.goal_note_state import _project_root

        root = _project_root(conn)
    except Exception:
        root = None
    if not root:
        return
    _save_watermark(root, "edges_dirty", sorted(set(_watermarks(root).get("edges_dirty") or []) | ids))


def edge_sources_touching(conn, artifact_id: str) -> set[str]:
    """The sources whose notes change when every link from or to ``artifact_id`` goes: itself and whatever links to it."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT from_id FROM artifact_edges WHERE from_id = ? OR to_id = ?", (artifact_id, artifact_id)
        ).fetchall()
    except Exception:
        return set()
    return {r[0] for r in rows}


def _edge_payload(conn, from_id: str) -> dict:
    edges = conn.execute(
        "SELECT to_id, relation, created_at, metadata FROM artifact_edges WHERE from_id = ? ORDER BY created_at, to_id",
        (from_id,),
    ).fetchall()
    return {
        "from_id": from_id,
        # Empty when every link went: the note says so, and a rebuild restores nothing for it.
        "edges": [{"to_id": t, "relation": r, "created_at": c, "metadata": m} for t, r, c, m in edges],
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def _noted_sources(root: str) -> set[str]:
    refs = _git(["for-each-ref", "--format=%(refname)", f"refs/notes/{EDGE_PREFIX}/"], root)
    prefix = f"refs/notes/{EDGE_PREFIX}/"
    return {r.removeprefix(prefix) for r in (refs.stdout.split() if refs and refs.returncode == 0 else [])}


def export_edge_notes(conn, root: str, *, limit: int | None = DEFAULT_LIMIT, full: bool = False) -> dict:
    """Write the edge notes of every source artifact with an edge newer than the watermark (all of them when ``full``), and of
    every source marked dirty since the last run. Returns ``{"written", "failed", "remaining"}``; ``remaining`` is True when
    ``limit`` stopped the run early.

    The watermark is the newest edge time exported and the sources already exported in that second: a link written later in
    the same second, from a source that sorts first, used to be skipped (one-second timestamps)."""
    marks = _watermarks(root)
    written = failed = 0

    # Sources whose links were deleted or rewritten: their notes are rewritten from the committed rows, all of them.
    dirty = set(marks.get("edges_dirty") or [])
    if full:
        dirty |= _noted_sources(root) - {r[0] for r in conn.execute("SELECT DISTINCT from_id FROM artifact_edges")}
    done = set()
    for from_id in sorted(dirty):
        if _write_note(root, f"{EDGE_PREFIX}/{from_id}", _edge_payload(conn, from_id)):
            written += 1
            done.add(from_id)
        else:
            failed += 1
            break
    if marks.get("edges_dirty") or done:
        _save_watermark(root, "edges_dirty", sorted(set(_watermarks(root).get("edges_dirty") or []) - done))
    if failed:
        return {"written": written, "failed": failed, "remaining": True}

    mark = None if full else marks.get("edges")
    if isinstance(
        mark, list
    ):  # the older [time, id] form: re-read its second rather than skip what sorts before the id
        mark = {"ts": mark[0], "ids": []}
    since_ts = (mark or {}).get("ts") or ""
    seen = set((mark or {}).get("ids") or [])
    rows = [
        (from_id, newest)
        for from_id, newest in conn.execute(
            "SELECT from_id, MAX(created_at) AS newest FROM artifact_edges GROUP BY from_id "
            "HAVING newest >= ? ORDER BY newest, from_id",
            (since_ts,),
        ).fetchall()
        if not (newest == since_ts and from_id in seen) and from_id not in done
    ]
    batch = rows if (full or limit is None) else rows[:limit]
    last_ts, last_ids = since_ts, set(seen)
    for from_id, newest in batch:
        if _write_note(root, f"{EDGE_PREFIX}/{from_id}", _edge_payload(conn, from_id)):
            written += 1
            if newest != last_ts:
                last_ts, last_ids = newest, set()
            last_ids.add(from_id)
        else:
            failed += 1
            break  # resume from here next run rather than skip past a source whose note was not written
    if last_ts != since_ts or last_ids != seen:
        _save_watermark(root, "edges", {"ts": last_ts, "ids": sorted(last_ids)})
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


LESSON_PREFIX = "empirica/lessons"


def export_lesson_notes(conn, root: str, *, full: bool = False) -> dict:
    """Write the note of every lesson updated since the watermark (all of them when ``full``): its full content from the
    cold YAML under .empirica/lessons (which git ignores) when the file exists, else the record the lessons table keeps,
    which the store reads as its fallback. ``{"written", "failed"}``."""
    import yaml

    folder = Path(root) / ".empirica" / "lessons"
    since = 0.0 if full else float(_watermarks(root).get("lessons") or 0.0)
    rows = conn.execute(
        "SELECT id, lesson_data, MAX(COALESCE(updated_timestamp, 0), COALESCE(created_timestamp, 0)) AS changed "
        "FROM lessons WHERE MAX(COALESCE(updated_timestamp, 0), COALESCE(created_timestamp, 0)) > ? ORDER BY changed",
        (since,),
    ).fetchall()
    written = failed = 0
    newest = since
    for lesson_id, stored, changed in rows:
        data = None
        path = folder / f"{lesson_id}.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else json.loads(stored or "null")
        except (OSError, ValueError, yaml.YAMLError) as e:
            logger.warning("lesson %s not exported: %s", lesson_id, e)
        if not isinstance(data, dict):
            # Could not be read: stop here, so the watermark does not move past it and the next run retries (1.16.1 sweep).
            failed += 1
            break
        data.setdefault("id", lesson_id)
        if _write_note(root, f"{LESSON_PREFIX}/{lesson_id}", {"lesson": data}):
            written += 1
            newest = max(newest, float(changed or 0.0))
        else:
            failed += 1
            break
    if written:
        _save_watermark(root, "lessons", newest)
    return {"written": written, "failed": failed}


def discover_lesson_notes(root: str) -> list[dict]:
    """Every lesson note's full lesson, for the rebuild."""
    return [p["lesson"] for p in _discover(root, LESSON_PREFIX) if isinstance(p.get("lesson"), dict)]


def export_graph_notes(conn, root: str, *, full: bool = False) -> dict:
    """Links, falsifiers and lessons into notes: incremental and bounded at each POSTFLIGHT, everything when ``full``
    (doctor --reconcile-notes --apply). Never raises; each part reports its own counts."""
    out: dict = {}
    for name, run in (
        ("edges", lambda: export_edge_notes(conn, root, full=full)),
        ("falsifiers", lambda: export_falsifier_notes(conn, root, full=full)),
        ("lessons", lambda: export_lesson_notes(conn, root, full=full)),
    ):
        try:
            out[name] = run()
        except Exception as e:  # one part failing must not stop the others
            logger.warning("notes export (%s) failed: %s", name, e)
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def plan_graph_notes(conn, root: str) -> dict:
    """What a full export would write: counts only (doctor's dry run)."""
    return {
        "edge_sources": conn.execute("SELECT COUNT(DISTINCT from_id) FROM artifact_edges").fetchone()[0],
        "falsifiers": conn.execute("SELECT COUNT(*) FROM falsifiers").fetchone()[0],
        "lessons": conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0],
    }
