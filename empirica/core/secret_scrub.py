"""Remove credentials that were stored before write-time redaction existed.

``redact_for_storage`` (core/redaction.py) keeps new credentials out of artifact text. This module removes the old ones from the
three places an artifact's text lives, which is why it has three scrubbers: the SQLite row, the git note (the canonical log: a
rebuild from notes would otherwise bring the secret back) and the Qdrant payload (retrieved into later sessions and eligible for
promotion to ``global_learnings``).

Rules that keep it safe to run:

* dry-run unless ``apply=True``; the report says what WOULD change;
* idempotent: a second run finds nothing;
* only this project's data. Another practice's collections are never touched; the shared collections (``global_learnings``,
  ``epistemic_events``) only when ``include_global`` is asked for;
* only text that :func:`redact_for_storage` would change, and only with the strong credential shapes, so prose is left alone;
* it reports an unreachable store instead of raising.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from empirica.core.redaction import redact_for_storage

#: The tables that hold artifact text. Nothing else in the database is rewritten.
ARTIFACT_TABLES = (
    "project_findings",
    "project_unknowns",
    "project_dead_ends",
    "mistakes_made",
    "assumptions",
    "decisions",
    "goals",
    "subtasks",
)

#: Collections shared across practices; scrubbed only on request.
GLOBAL_COLLECTIONS = ("global_learnings", "epistemic_events")


def scrub_text(text: str) -> str:
    """``text`` with credentials removed, and still valid JSON if it was JSON.

    The raw redactor works on characters, so a rule that is a little too greedy (the flag rule once took the closing quote and
    comma after its value) turns a git note or a ``*_data`` column into text nothing can parse, and a rebuild or a reader that
    does ``json.loads(...) except: {}`` then loses the artifact. A JSON document is therefore scrubbed through its strings and
    re-encoded; text that is not JSON, and a JSON scalar, goes through the plain redactor. A document with nothing to remove is
    returned byte for byte, not re-dumped.
    """
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            document = json.loads(text)
        except ValueError:
            return redact_for_storage(text)
        clean = _scrubbed(document)
        if clean == document:
            return text
        return json.dumps(clean, indent=2 if "\n" in text else None, ensure_ascii=False)
    return redact_for_storage(text)


def _scrubbed(value: Any) -> Any:
    """``value`` with every string inside it scrubbed (dicts and lists are walked)."""
    if isinstance(value, str):
        return redact_for_storage(value)
    if isinstance(value, dict):
        return {k: _scrubbed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrubbed(v) for v in value]
    return value


# --- SQLite -------------------------------------------------------------------------------------------------------------


def scrub_sqlite(conn: sqlite3.Connection, apply: bool = False) -> dict:
    """Scrub the text columns of the artifact tables. Returns ``{"rows": {table: n}, "applied": bool}``."""
    rows: dict[str, int] = {}
    for table in ARTIFACT_TABLES:
        try:
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.Error:
            continue
        columns = [r[1] for r in info if (r[2] or "").upper() in ("TEXT", "")]
        if not columns:
            continue
        changed = 0
        select = f"SELECT rowid, {', '.join(columns)} FROM {table}"
        for row in conn.execute(select).fetchall():
            updates = {}
            for name, value in zip(columns, row[1:], strict=True):
                if isinstance(value, str):
                    clean = scrub_text(value)
                    if clean != value:
                        updates[name] = clean
            if not updates:
                continue
            changed += 1
            if apply:
                assignments = ", ".join(f"{name} = ?" for name in updates)
                conn.execute(f"UPDATE {table} SET {assignments} WHERE rowid = ?", (*updates.values(), row[0]))
        if changed:
            rows[table] = changed
    if apply:
        conn.commit()
    return {"rows": rows, "applied": apply}


# --- Qdrant -------------------------------------------------------------------------------------------------------------


class QdrantLike(Protocol):
    def list_collections(self) -> list[str]: ...

    def scroll(self, collection: str) -> Iterator[tuple[Any, dict]]: ...

    def set_payload(self, collection: str, point_id: Any, payload: dict) -> None: ...


class QdrantHttp:
    """The three calls the scrub needs, over Qdrant's REST API (no client library required)."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, path: str, body: dict | None = None, method: str | None = None) -> dict:
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"content-type": "application/json"},
            method=method or ("POST" if data else "GET"),
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def list_collections(self) -> list[str]:
        return [c["name"] for c in self._call("/collections")["result"]["collections"]]

    def scroll(self, collection: str) -> Iterator[tuple[Any, dict]]:
        offset = None
        while True:
            body: dict[str, Any] = {"limit": 256, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            result = self._call(f"/collections/{collection}/points/scroll", body)["result"]
            for point in result["points"]:
                yield point["id"], point.get("payload") or {}
            offset = result.get("next_page_offset")
            if offset is None:
                return

    def set_payload(self, collection: str, point_id: Any, payload: dict) -> None:
        # POST merges the given keys into the point's payload; PUT would REPLACE the whole payload and erase every other field.
        self._call(
            f"/collections/{collection}/points/payload", {"payload": payload, "points": [point_id]}, method="POST"
        )


def _project_collections(names: list[str], project_id: str) -> list[str]:
    prefix = f"project_{project_id}_"
    return [n for n in names if n.startswith(prefix)]


def scrub_qdrant(client: QdrantLike, project_id: str, include_global: bool = False, apply: bool = False) -> dict:
    """Scrub the payloads of this project's collections (and the shared ones if asked). Returns
    ``{"points": {collection: n}, "applied": bool}``, plus ``"error"`` if the store could not be read."""
    points: dict[str, int] = {}
    try:
        names = client.list_collections()
        targets = _project_collections(names, project_id)
        if include_global:
            targets += [n for n in GLOBAL_COLLECTIONS if n in names]
        for collection in targets:
            changed = 0
            for point_id, payload in list(client.scroll(collection)):
                clean = _scrubbed(payload)
                delta = {k: v for k, v in clean.items() if v != payload.get(k)}
                if not delta:
                    continue
                changed += 1
                if apply:
                    client.set_payload(collection, point_id, delta)
            if changed:
                points[collection] = changed
    except Exception as exc:  # an unreachable store is a result, not a crash
        return {"points": points, "applied": apply, "error": f"{type(exc).__name__}: {exc}"}
    return {"points": points, "applied": apply}


# --- git notes ----------------------------------------------------------------------------------------------------------


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, input=stdin, capture_output=True, text=True, check=True, timeout=60
    ).stdout


def scrub_notes(repo: Path, apply: bool = False) -> dict:
    """Scrub the ``refs/notes/empirica/*`` notes of ``repo``. Returns ``{"notes": n, "applied": bool}``."""
    count = 0
    try:
        refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/notes/empirica").split()
        for ref in refs:
            notes_ref = ref.removeprefix("refs/notes/")
            for line in _git(repo, "notes", f"--ref={notes_ref}", "list").splitlines():
                parts = line.split()
                if len(parts) != 2:
                    continue
                note_sha, object_sha = parts
                text = _git(repo, "cat-file", "-p", note_sha)
                clean = scrub_text(text)
                if clean == text:
                    continue
                count += 1
                if apply:
                    _git(repo, "notes", f"--ref={notes_ref}", "add", "-f", "-F", "-", object_sha, stdin=clean)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"notes": count, "applied": apply, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return {"notes": count, "applied": apply}
