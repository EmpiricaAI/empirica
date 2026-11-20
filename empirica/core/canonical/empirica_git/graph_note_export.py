"""Links between artifacts, falsifiers and lessons into the git notes, so a rebuild stops losing them (goal 966808c0).

These lived in SQLite (and lessons in their own store) only. David chose (2026-11-12) one exporter run at each POSTFLIGHT,
in the detached storage pipeline, and in full by ``doctor --reconcile-notes``, over hooking every writer: it covers all of
them, stays off the POSTFLIGHT response path, and leaves the same window the rest of a transaction has.

Links: one note per source artifact, ``empirica/edges/<from_id>``, listing every edge from it. Incremental: a watermark in
``.empirica/notes_export.json`` (the newest edge time and source id exported) says where the last run stopped, so a run
writes only the sources with an edge newer than that, at most ``limit`` of them; the next run resumes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import uuid
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


_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _watermark_lock(root: str):
    """One writer at a time on the watermark file: a thread lock, and an inter-process flock where fcntl exists (on
    Windows the file lock is skipped, as the credentials refresh lock does)."""
    with _THREAD_LOCK:
        try:
            import fcntl
        except ImportError:
            yield
            return
        try:
            directory = Path(root) / ".empirica"
            directory.mkdir(parents=True, exist_ok=True)
            fh = open(directory / f".{WATERMARK_FILE}.lock", "a")  # noqa: SIM115 — held for the with-block
        except OSError as e:
            logger.warning("notes export watermark lock unavailable, writing unserialised: %s", e)
            yield
            return
        try:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
            except OSError as e:  # ENOLCK on NFS mounted nolock, some FUSE/SMB: best effort, as above (goal 33ffc93c)
                logger.warning("notes export watermark lock unavailable, writing unserialised: %s", e)
            yield
        finally:
            fh.close()  # releases the flock


def _update_watermarks(root: str, change) -> None:
    """Read, change and write the watermark file under the lock: a mark another writer adds meanwhile is never lost, and
    each writer has its own temp file, so two writers cannot corrupt it (goal 11d666e6)."""
    path = Path(root) / ".empirica" / WATERMARK_FILE
    with _watermark_lock(root):
        marks = change(_watermarks(root))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(marks, indent=2))
            tmp.replace(path)
        except OSError as e:
            logger.warning("notes export watermark not saved: %s", e)


def _save_watermark(root: str, kind: str, value) -> None:
    _update_watermarks(root, lambda marks: {**marks, kind: value})


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
    token = (
        uuid.uuid4().hex
    )  # a fresh token per mark: an export clears a source only if nobody marked it again meanwhile
    _update_watermarks(root, lambda marks: {**marks, "edges_dirty": {**_dirty(marks), **dict.fromkeys(ids, token)}})


def _dirty(marks: dict) -> dict:
    """The dirty sources as ``{from_id: token}``. The older form, a plain list, reads as tokens of None."""
    value = marks.get("edges_dirty") or {}
    if isinstance(value, list):
        return dict.fromkeys(value)
    return dict(value) if isinstance(value, dict) else {}


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
    snapshot = _dirty(marks)
    dirty = set(snapshot)
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
    if snapshot or done:
        # Clear only what was written from a mark this run read: a source marked again after its note was built (a link
        # deleted meanwhile) carries a new token and stays for the next run (1.16.2 sweep B7).
        # One locked read-and-write, so a mark added between the read and the write is kept too (goal 11d666e6).
        def _clear(current_marks: dict) -> dict:
            current = _dirty(current_marks)
            kept = {i: t for i, t in current.items() if not (i in done and t == snapshot.get(i))}
            return {**current_marks, "edges_dirty": kept}

        _update_watermarks(root, _clear)
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


VISIBILITY_PREFIX = "empirica/visibility"
#: The tables whose rows carry a visibility tag a rebuild must restore (the per-artifact notes carry none).
VISIBILITY_TABLES = (
    "project_findings",
    "project_unknowns",
    "project_dead_ends",
    "mistakes_made",
    "assumptions",
    "decisions",
    "epistemic_sources",
    "goals",
)
_TIERS = ("shared", "public")


def _visibility_shard(artifact_id: str) -> str:
    """The shard an id's tag lives in: its first two characters, lowercased (256 shards for UUIDs). An id whose start
    cannot name a git ref ('../x', '.h', 'a:b') goes to the shard 'other', or its note failed at every POSTFLIGHT."""
    head = str(artifact_id)[:2].lower()
    return head if re.fullmatch(r"[0-9a-z]{2}", head) else "other"


def _existing_visibility_shards(root: str) -> set[str]:
    """The shard notes already in the repository, so a full export also rewrites the ones that have emptied."""
    refs = _git(["for-each-ref", "--format=%(refname)", f"refs/notes/{VISIBILITY_PREFIX}/"], root)
    return {r.rsplit("/", 1)[-1] for r in (refs.stdout.split() if refs and refs.returncode == 0 else [])}


def _visibility_shards(conn) -> dict[str, dict[str, str]]:
    """{shard: {id: tier}} for every non-local row; local is the default a rebuild already restores, so it is not written."""
    shards: dict[str, dict[str, str]] = {}
    for table in VISIBILITY_TABLES:
        try:
            rows = conn.execute(
                f"SELECT id, visibility FROM {table} WHERE visibility IN ('shared', 'public')"
            ).fetchall()
        except Exception:  # a table or column this store lacks
            continue
        for artifact_id, tier in rows:
            if artifact_id:
                shards.setdefault(_visibility_shard(artifact_id), {})[str(artifact_id)] = tier
    return shards


def _held_ids(conn) -> set[str]:
    """Every artifact id this database holds, whatever its tag."""
    held: set[str] = set()
    for table in VISIBILITY_TABLES:
        try:
            held.update(str(r[0]) for r in conn.execute(f"SELECT id FROM {table}").fetchall() if r[0])
        except Exception:
            continue
    return held


def _read_shard(root: str, shard: str) -> dict[str, str]:
    """The tiers a shard note holds now ({} when there is none)."""
    ref = f"{VISIBILITY_PREFIX}/{shard}"
    listed = _git(["notes", f"--ref={ref}", "list"], root)
    first = (listed.stdout.splitlines() or [""])[0].split() if listed and listed.returncode == 0 else []
    if len(first) < 2:
        return {}
    shown = _git(["notes", f"--ref={ref}", "show", first[1]], root)
    try:
        payload = json.loads(shown.stdout) if shown and shown.returncode == 0 else {}
    except ValueError:
        return {}
    tiers = payload.get("tiers") if isinstance(payload, dict) else None
    return {str(k): v for k, v in tiers.items() if v in _TIERS} if isinstance(tiers, dict) else {}


def export_visibility_notes(conn, root: str, *, full: bool = False) -> dict:
    """Write each visibility shard whose tags in this database changed since the last export (every shard when ``full`` or
    when there is no watermark, as on a fresh clone), so a rebuild restores shared and public tags (goal ecea0fd4).

    A shard is written as a merge: entries for ids this database does not hold are kept (another machine exported them,
    and a clone that has not rebuilt yet must not empty them), entries for ids it holds take its tags, so a tag moved back
    to local drops out (1.17.14 sweep, both passes). The watermark records this database's own view, so an unchanged shard
    is skipped without reading its note. ``{"written", "failed", "shards"}``."""
    import hashlib

    shards = _visibility_shards(conn)
    previous = dict(_watermarks(root).get("visibility") or {})
    everything = full or not previous
    candidates = set(shards) | set(previous) | (_existing_visibility_shards(root) if everything else set())
    held = _held_ids(conn) if candidates else set()
    current: dict[str, str] = {}
    written = failed = 0
    for shard in sorted(candidates):
        mine = shards.get(shard, {})
        digest = hashlib.sha256(json.dumps(mine, sort_keys=True).encode()).hexdigest()[:16]
        current[shard] = digest
        if not everything and previous.get(shard) == digest:
            continue
        note = _read_shard(root, shard)
        merged = {i: t for i, t in note.items() if i not in held} | mine
        if merged == note:
            continue  # nothing to change in the note itself
        if _write_note(root, f"{VISIBILITY_PREFIX}/{shard}", {"tiers": merged}):
            written += 1
        else:
            failed += 1
            current[shard] = previous.get(shard, "")  # retried next time
    _save_watermark(root, "visibility", {k: v for k, v in current.items() if v})
    return {"written": written, "failed": failed, "shards": len(shards)}


def discover_visibility_notes(root: str) -> dict[str, str]:
    """{id: tier} from every shard note, for the rebuild; a tier that does not exist is ignored."""
    out: dict[str, str] = {}
    for payload in _discover(root, VISIBILITY_PREFIX):
        for artifact_id, tier in (payload.get("tiers") or {}).items():
            if tier in _TIERS:
                out[str(artifact_id)] = tier
    return out


def export_graph_notes(conn, root: str, *, full: bool = False) -> dict:
    """Links, falsifiers and lessons into notes: incremental and bounded at each POSTFLIGHT, everything when ``full``
    (doctor --reconcile-notes --apply). Never raises; each part reports its own counts."""
    out: dict = {}
    for name, run in (
        ("edges", lambda: export_edge_notes(conn, root, full=full)),
        ("falsifiers", lambda: export_falsifier_notes(conn, root, full=full)),
        ("lessons", lambda: export_lesson_notes(conn, root, full=full)),
        ("visibility", lambda: export_visibility_notes(conn, root, full=full)),
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
        "visibility_tagged": sum(len(t) for t in _visibility_shards(conn).values()),
    }
