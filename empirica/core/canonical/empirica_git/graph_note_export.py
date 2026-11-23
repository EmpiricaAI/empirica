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
import math
import os
import re
import threading
import time
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
_TIERS = ("shared", "public", "local")


def _visibility_shard(artifact_id: str) -> str:
    """The shard an id's tag lives in: its first two characters, lowercased (256 shards for UUIDs). An id whose start
    cannot name a git ref ('../x', '.h', 'a:b') goes to the shard 'other', or its note failed at every POSTFLIGHT."""
    head = str(artifact_id)[:2].lower()
    return head if re.fullmatch(r"[0-9a-z]{2}", head) else "other"


def machine_name() -> str:
    """This machine's namespace for its visibility notes: the hostname, ref-safe, plus a short hash of /etc/machine-id (or of
    the hostname) so two machines with the same hostname do not share refs (goal 221679b1)."""
    import hashlib
    import socket

    host = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().lower()).strip("-")[:40] or "host"
    try:
        machine_id = Path("/etc/machine-id").read_text().strip()
    except OSError:
        machine_id = ""
    return f"{host}-{hashlib.sha256((machine_id or host).encode()).hexdigest()[:6]}"


#: Where a checkout keeps its visibility writer id: ``.empirica/`` is not versioned, so each checkout has its own.
WRITER_FILE = "visibility_writer.json"
_WRITER_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


def _checkout_key(root: str) -> str:
    """What binds a writer id to one checkout: the inode of its writer file. Moving or renaming the checkout, or the
    machine, keeps it; a copy (cp -r, rsync) makes a new file with a new inode, so the copy gets a new id and two checkouts
    never write the same refs. Binding to the path stranded a moved checkout's writer (goal 38510fdd, second review)."""
    try:
        return f"inode:{(Path(root) / '.empirica' / WRITER_FILE).stat().st_ino}"
    except OSError:
        return ""


def current_writer(root: str) -> str | None:
    """This checkout's visibility writer id, or None before its first export or when the file was made for another
    checkout (goal 38510fdd)."""
    try:
        data = json.loads((Path(root) / ".empirica" / WRITER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    writer = data.get("writer") if isinstance(data, dict) else None
    if not (isinstance(writer, str) and _WRITER_RE.fullmatch(writer)) or data.get("checkout") != _checkout_key(root):
        return None
    return writer


#: Where a checkout keeps its visibility baseline: what it last learned the notes say, {id: [tier, changed_at]}, written
#: by every rebuild and every export. The export compares the database with it, so only a tag changed here since is
#: stamped now, and the rebuild leaves a row with an unexported change here alone (goal 38510fdd review).
BASELINE_FILE = "visibility_baseline.json"


REBUILD_MARKER = "visibility_rebuild.pending"


@contextlib.contextmanager
def _baseline_lock(root: str):
    """Serialise the baseline's read-modify-write across processes: a background POSTFLIGHT export and a rebuild can run
    at once (goal 38510fdd, second review). POSIX only; elsewhere it does not lock."""
    path = Path(root) / ".empirica" / "visibility_baseline.lock"
    try:
        import fcntl

        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a")
    except (ImportError, OSError):
        yield
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def rebuild_started(root: str) -> None:
    """Mark a rebuild as under way: rows come back untagged before the visibility step sets them, and an export in
    between would read every share as withdrawn. apply_visibility_notes clears it (goal 38510fdd, second review)."""
    path = Path(root) / ".empirica" / REBUILD_MARKER
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")


def _rebuild_pending(root: str) -> bool:
    return (Path(root) / ".empirica" / REBUILD_MARKER).exists()


def load_baseline(root: str) -> dict[str, list]:
    try:
        data = json.loads((Path(root) / ".empirica" / BASELINE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _entries({"tiers": data}) if isinstance(data, dict) else {}


def save_baseline(root: str, baseline: dict[str, list]) -> None:
    path = Path(root) / ".empirica" / BASELINE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(baseline, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def writer_id(root: str) -> str:
    """The namespace this checkout writes its visibility notes under: the hostname for readability plus a random suffix,
    made once and kept in ``.empirica/`` (goal 38510fdd). Per checkout, not per host: 1.17.15 named it from the hostname,
    so two clones on one host wrote the same refs and a renamed machine could not override its own older entries."""
    import secrets
    import socket

    existing = current_writer(root)
    if existing:
        return existing
    host = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().lower()).strip("-")[:40] or "host"
    writer = f"{host}-{secrets.token_hex(4)}"
    path = Path(root) / ".empirica" / WRITER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"writer": writer}) + "\n", encoding="utf-8")
    # Rewritten in place, so the inode recorded is this file's own.
    path.write_text(json.dumps({"writer": writer, "checkout": _checkout_key(root)}) + "\n", encoding="utf-8")
    return writer


def _visibility_refs(root: str) -> dict[str, list[str]]:
    """{shard: [namespace, ...]} for every visibility note ref; the 1.17.14 flat layout's namespace is ""."""
    prefix = f"refs/notes/{VISIBILITY_PREFIX}/"
    refs = _git(["for-each-ref", "--format=%(refname)", prefix], root)
    out: dict[str, list[str]] = {}
    for ref in refs.stdout.split() if refs and refs.returncode == 0 else []:
        if not ref.startswith(prefix):
            continue
        namespace, _, shard = ref[len(prefix) :].rpartition("/")
        out.setdefault(shard, []).append(namespace)
    return out


def _existing_visibility_shards(root: str, machine: str) -> set[str]:
    """The shard notes this machine has already written, so a full export also rewrites the ones that have emptied."""
    refs = _git(["for-each-ref", "--format=%(refname)", f"refs/notes/{VISIBILITY_PREFIX}/{machine}/"], root)
    return {r.rsplit("/", 1)[-1] for r in (refs.stdout.split() if refs and refs.returncode == 0 else [])}


def _visibility_shards(conn) -> dict[str, dict[str, str]]:
    """{shard: {id: tier}} for every non-local row of this database."""
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


def _entries(payload) -> dict[str, list]:
    """A note's tiers as {id: [tier, changed_at]}; the 1.17.14 flat layout ({id: tier}) reads as changed_at 0 (oldest)."""
    tiers = payload.get("tiers") if isinstance(payload, dict) else None
    out: dict[str, list] = {}
    for artifact_id, value in (tiers or {}).items() if isinstance(tiers, dict) else []:
        tier, when = (
            (value, 0.0)
            if isinstance(value, str)
            else (list(value) + [0.0])[:2]
            if isinstance(value, list) and value
            else (None, 0.0)
        )
        if tier in _TIERS:
            out[str(artifact_id)] = [tier, _sane_time(when)]
    return out


def _sane_time(when) -> float:
    """A changed_at as a time, or 0.0 (oldest) when it is not one: not a number, not finite, negative, or more than a day
    ahead of this clock. A NaN or far-future time used to win every comparison and lock the tag (1.17.15 sweep)."""
    try:
        value = float(when)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and 0.0 <= value <= time.time() + 86400 else 0.0


def _read_shard(root: str, path: str) -> dict[str, list]:
    """The entries a note under empirica/visibility/<path> holds now ({} when there is none)."""
    ref = f"{VISIBILITY_PREFIX}/{path}"
    listed = _git(["notes", f"--ref={ref}", "list"], root)
    first = (listed.stdout.splitlines() or [""])[0].split() if listed and listed.returncode == 0 else []
    if len(first) < 2:
        return {}
    shown = _git(["notes", f"--ref={ref}", "show", first[1]], root)
    try:
        return _entries(json.loads(shown.stdout) if shown and shown.returncode == 0 else {})
    except ValueError:
        return {}


#: The export record's value for a shard whose write failed, ``retry:<failures>``: never a real digest, so the shard is
#: rewritten next time. Up to _RETRY_FULL failures it forces a full export (the only kind that recomputes the 1.17.14
#: overrides); after that the shard is retried on its own, so a write that always fails does not make every POSTFLIGHT a
#: full export (1.17.16 sweep: 514 git calls, about 4.5 s, on 256 shards).
_RETRY = "retry:"
_RETRY_FULL = 3


def _failures(value) -> int:
    try:
        return int(str(value)[len(_RETRY) :]) if str(value).startswith(_RETRY) else 0
    except ValueError:
        return 1


def export_visibility_notes(conn, root: str, *, full: bool = False) -> dict:
    """Write this machine's visibility shard notes (goals ecea0fd4, 221679b1): empirica/visibility/<machine>/<shard>, each
    entry [tier, changed_at] for the ids this database holds as shared or public, and a tag moved back to local kept as
    local, so it can win. Only this machine writes its namespace, so syncing machines never write the same ref (1.17.14
    shared one set of refs, and two machines refused each other's sync). A shard is rewritten when this database's tags in
    it changed since the last export (every shard of this machine when ``full`` or with no watermark). ``{"written",
    "failed", "shards"}``."""
    if _rebuild_pending(root):
        return {"written": 0, "failed": 0, "shards": 0, "skipped": "a rebuild is under way"}
    with _baseline_lock(root):
        return _export_visibility_notes(conn, root, full=full)


def _export_visibility_notes(conn, root: str, *, full: bool = False) -> dict:
    import hashlib

    machine = writer_id(root)
    shards = _visibility_shards(conn)
    baseline = load_baseline(root)
    # Per machine: 1.17.14 kept one record for its flat layout, and matching it skipped this machine's first export,
    # so a later withdrawal had no entry to override (1.17.15 second pass). A new name starts with a full export.
    record = f"visibility:{machine}"
    marks = _watermarks(root)
    previous = dict(marks.get(record) or {})
    # An empty record is not a first export (1.17.15 second pass); a write that failed is retried by a full export, the only
    # one that recomputes the 1.17.14 overrides (goal c29b6931).
    everything = full or record not in marks or any(0 < _failures(v) <= _RETRY_FULL for v in previous.values())
    candidates = set(shards) | set(previous) | (_existing_visibility_shards(root, machine) if everything else set())
    # Shards where the database's tag differs from the baseline: a tag adopted by a rebuild and changed here since (a
    # withdrawal of an adopted share leaves nothing shared in the shard, and this writer may never have written it).
    tagged = {i: t for tiers in shards.values() for i, t in tiers.items()}
    differs = {_visibility_shard(i) for i, (t, _w) in baseline.items() if tagged.get(i, "local") != t}
    candidates |= differs
    held: set[str] | None = None
    # This checkout's older namespaces: 1.17.14's flat notes (one shared set of refs) and the hostname namespace 1.17.15
    # and 1.17.16 wrote, when its export record is here. A full export overrides their shared or public entries for ids this
    # database holds as local, while one of them is still the winner: they were this checkout's own tags, and a share
    # withdrawn while the export was off or under another name is in no current note (1.17.15 second pass, goal 38510fdd).
    # Another writer's entries are never overridden: a row merely never shared here is not a withdrawal.
    refs = _visibility_refs(root)
    legacy_local = _owned_legacy_overrides(conn, root, refs, machine, marks, shards) if everything else {}
    candidates |= set(legacy_local)
    current: dict[str, str] = {}
    written = failed = 0
    now = time.time()
    for shard in sorted(candidates):
        mine = shards.get(shard, {})
        digest = hashlib.sha256(json.dumps(mine, sort_keys=True).encode()).hexdigest()[:16]
        current[shard] = digest
        # Unchanged since the last export, unless a rebuild moved the baseline in between (shared, local by a rebuild,
        # shared again reads as the same digest).
        if not everything and previous.get(shard) == digest and shard not in differs:
            continue
        note = _read_shard(root, f"{machine}/{shard}")
        others = [ns for ns in refs.get(shard, []) if ns != machine]
        if held is None:
            held = _held_ids(conn)
        merged = _stamp_tags(
            mine,
            note,
            {i: e for i, e in baseline.items() if _visibility_shard(i) == shard},
            held,
            lambda shard=shard, others=others: _shard_winners(root, shard, others),
            now,
        )
        for artifact_id in legacy_local.get(shard, ()):
            if artifact_id not in merged:
                merged[artifact_id] = ["local", now]
        if merged == note:
            baseline.update(merged)
            continue
        if _write_note(root, f"{VISIBILITY_PREFIX}/{machine}/{shard}", {"machine": machine, "tiers": merged}):
            baseline.update(merged)  # only what reached the notes: a failed write keeps the change pending
            written += 1
        else:
            failed += 1
            current[shard] = f"{_RETRY}{_failures(previous.get(shard)) + 1}"  # retried next time (goal c29b6931)
    _save_watermark(root, record, {k: v for k, v in current.items() if v})
    save_baseline(root, baseline)
    return {"written": written, "failed": failed, "shards": len(shards)}


def _owned_legacy_overrides(
    conn, root: str, refs: dict, machine: str, marks: dict, shards: dict
) -> dict[str, set[str]]:
    """{shard: ids} a full export writes as local: entries in this checkout's older namespaces (1.17.14's flat notes, and
    the hostname namespace 1.17.15 and 1.17.16 wrote, when its export record is here) that are still the winner, shared or
    public, for ids this database holds as local. They were this checkout's own tags, and a share withdrawn while the export
    was off or under another name is in no current note (1.17.15 second pass, goal 38510fdd). Another writer's entries are
    never overridden: a row merely never shared here is not a withdrawal."""
    old_name = machine_name()
    owned = {""} | ({old_name} if old_name != machine and f"visibility:{old_name}" in marks else set())
    if not any(owned & set(namespaces) for namespaces in refs.values()):
        return {}
    held = _held_ids(conn)
    shared_here = {i for tiers in shards.values() for i in tiers}
    out: dict[str, set[str]] = {}
    for shard, namespaces in refs.items():
        for artifact_id, (tier, _when, source) in _shard_winners(root, shard, namespaces).items():
            if source in owned and tier != "local" and artifact_id in held and artifact_id not in shared_here:
                out.setdefault(shard, set()).add(artifact_id)
    return out


def _stamp_tags(
    mine: dict[str, str], note: dict[str, list], baseline: dict[str, list], held: set[str], elsewhere, now: float
) -> dict[str, list]:
    """This checkout's entries for one shard, [tier, changed_at] (goal 38510fdd). For each id this checkout has a tag for
    (shared or public in the database, or in its baseline or its own note), with the database's tier (local when it holds
    the row untagged): when the baseline knows the id, it decides: the same tier keeps the baseline's entry and its time
    (including a withdrawal copied from another writer), any other tier was changed here, now (a re-share after a rebuild
    brought a withdrawal). Without a baseline entry, this writer's own entry with that tier is kept; else another writer's
    latest entry with that tier (adopted before this checkout had a baseline); else the tier was changed here, now. A row this checkout holds local and never had a tag for is not written: it is not a withdrawal. An id this
    database does not hold keeps its entry. ``elsewhere`` reads the other writers' entries, only when needed."""
    others: dict | None = None
    merged: dict[str, list] = {}
    for artifact_id in set(mine) | set(baseline) | set(note):
        tier = mine.get(artifact_id) or ("local" if artifact_id in held else None)
        if tier is None:
            if artifact_id in note:
                merged[artifact_id] = note[artifact_id]
            continue
        known = baseline.get(artifact_id)
        if known:  # what this checkout last learned decides: same tier, keep its time; any other, changed here
            merged[artifact_id] = known if known[0] == tier else [tier, now]
            continue
        own = note.get(artifact_id)
        if own and own[0] == tier:
            merged[artifact_id] = own
            continue
        if tier == "local" and not own:
            continue
        if others is None:
            others = elsewhere()
        win = others.get(artifact_id)
        merged[artifact_id] = [tier, win[1]] if win and win[0] == tier else [tier, now]
    return merged


def _shard_winners(root: str, shard: str, namespaces: list[str]) -> dict[str, tuple[str, float, str]]:
    """{id: (tier, changed_at, namespace)}: the latest entry per id in one shard across the given namespaces."""
    best: dict[str, tuple[str, float, str]] = {}
    for namespace in namespaces:
        for artifact_id, (tier, when) in _read_shard(root, f"{namespace}/{shard}" if namespace else shard).items():
            if artifact_id not in best or _beats((tier, when), best[artifact_id]):
                best[artifact_id] = (tier, when, namespace)
    return best


def _winning_entries(root: str) -> dict[str, list]:
    """{id: [tier, changed_at]}: the latest entry per id across every machine's notes and the 1.17.14 flat layout."""
    best: dict[str, list] = {}
    for payload in _discover(root, VISIBILITY_PREFIX):
        for artifact_id, entry in _entries(payload).items():
            if artifact_id not in best or _beats(entry, best[artifact_id]):
                best[artifact_id] = entry
    return best


def _beats(entry, best) -> bool:
    """The later entry wins; on a tie a withdrawal wins, so no order of reading lets a share outlast an equally recent
    withdrawal (goal 38510fdd review: two entries at time 0 were settled by the writer ids' order)."""
    return entry[1] > best[1] or (entry[1] == best[1] and entry[0] == "local" and best[0] != "local")


def apply_visibility_notes(conn, root: str, *, held_before: set[str] | None = None) -> int:
    """Set each artifact's tag from the notes (the latest entry per id across writers) and record it in this checkout's
    baseline; return how many shares and public tags were restored. A row that existed before this rebuild and whose tag
    differs from the baseline was changed here and not yet exported: it is left alone, so a pull before POSTFLIGHT does
    not undo a withdrawal (goal 38510fdd review). Rows this rebuild created (``held_before`` lacks them) always take the
    notes' tag."""
    with _baseline_lock(root):
        restored = _apply_visibility_notes(conn, root, held_before or set())
    with contextlib.suppress(OSError):
        (Path(root) / ".empirica" / REBUILD_MARKER).unlink()
    return restored


def _own_entries(root: str) -> dict[str, list]:
    """This checkout's own exported entries (its writer namespace, and the hostname namespace 1.17.15-16 wrote): what the
    baseline would hold, for a checkout that has none yet or lost it (goal 38510fdd, second review)."""
    namespaces = [w for w in (current_writer(root), machine_name()) if w]
    own: dict[str, list] = {}
    for shard, found in _visibility_refs(root).items():
        for namespace in namespaces:
            if namespace in found:
                for artifact_id, entry in _read_shard(root, f"{namespace}/{shard}").items():
                    if artifact_id not in own or entry[1] > own[artifact_id][1]:
                        own[artifact_id] = entry
    return own


def _apply_visibility_notes(conn, root: str, held_before: set[str]) -> int:
    baseline = load_baseline(root)
    if not baseline and held_before:
        baseline = _own_entries(root)
    restored = 0
    for artifact_id, (tier, when) in _winning_entries(root).items():
        for table in VISIBILITY_TABLES:
            try:
                row = conn.execute(f"SELECT visibility FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
            except Exception:
                continue
            if row is None:
                continue
            current = row[0] or "local"
            known = baseline.get(artifact_id)
            if artifact_id in held_before and known and current != known[0]:
                break  # changed here since this checkout last synced
            conn.execute(f"UPDATE {table} SET visibility = ? WHERE id = ?", (tier, artifact_id))
            baseline[artifact_id] = [tier, when]
            restored += tier != "local"
            break
    conn.commit()
    save_baseline(root, baseline)
    return restored


def discover_visibility_notes(root: str) -> dict[str, str]:
    """{id: tier} for the rebuild: across every machine's notes (and the 1.17.14 flat layout, as oldest), the entry with
    the latest changed_at wins, so a move back to local on one machine outranks an older shared tag on another."""
    return {artifact_id: entry[0] for artifact_id, entry in _winning_entries(root).items()}


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
    if visibility_notes_enabled():
        try:
            out["visibility"] = export_visibility_notes(conn, root, full=full)
        except Exception as e:
            logger.warning("notes export (visibility) failed: %s", e)
            out["visibility"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def visibility_notes_enabled() -> bool:
    """Off by default (EMPIRICA_VISIBILITY_NOTES=1 turns it on) until the multi-machine design (goal 38510fdd: writer per
    checkout, baseline, adopted times, own-refs push and fetch) has had a clean independent review and David's word."""
    return os.environ.get("EMPIRICA_VISIBILITY_NOTES", "").strip() == "1"


def plan_graph_notes(conn, root: str) -> dict:
    """What a full export would write: counts only (doctor's dry run)."""
    return {
        "edge_sources": conn.execute("SELECT COUNT(DISTINCT from_id) FROM artifact_edges").fetchone()[0],
        "falsifiers": conn.execute("SELECT COUNT(*) FROM falsifiers").fetchone()[0],
        "lessons": conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0],
        "visibility_tagged": sum(len(t) for t in _visibility_shards(conn).values()),
    }
