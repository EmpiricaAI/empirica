"""source-update — re-fetch a source, recompute its content identity, refresh it.

The ACT half of David's source-lifecycle gap: ``sources-check`` DETECTS a stale /
broken source; ``source-update`` RE-FETCHES it (local ``canonical_path`` first,
else an http(s) ``source_url``), recomputes the content identity written by
migration 050 (``content_hash`` / ``size_bytes`` / ``mime_type``), and appends an
``updated`` event to ``lifecycle_audit_log`` (migration 044). A failed re-fetch
updates NOTHING — an existing content_hash is never wiped by an unreachable
source. Mirrors the source-archive handler's prefix-resolve + audit-append shape.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

from empirica.cli.cli_utils import handle_cli_error

# Don't hash unbounded blobs — cap the re-fetch. A source over the cap keeps its
# existing identity + reports the cap (rather than silently truncating a hash).
_MAX_FETCH_BYTES = 25 * 1024 * 1024  # 25 MiB


def _fetch_content(
    source_url: str | None, canonical_path: str | None, timeout: float = 10.0
) -> tuple[bytes | None, str | None]:
    """Re-fetch a source's raw bytes. Returns ``(content, error)``.

    Prefers a local ``canonical_path`` (fast, offline); falls back to an http(s)
    ``source_url``. ``(None, reason)`` on any failure — the caller must NOT touch
    the stored hash when the fetch fails.
    """
    for candidate in (canonical_path, source_url):
        if candidate and not candidate.startswith(("http://", "https://")):
            p = Path(candidate.replace("file://", ""))
            try:
                if p.is_file():
                    data = p.read_bytes()
                    if len(data) > _MAX_FETCH_BYTES:
                        return None, f"file exceeds {_MAX_FETCH_BYTES}-byte cap"
                    return data, None
            except OSError as e:
                return None, f"file read failed: {e}"
    if source_url and source_url.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(source_url, method="GET", headers={"User-Agent": "empirica-source-update"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(_MAX_FETCH_BYTES + 1)
            if len(data) > _MAX_FETCH_BYTES:
                return None, f"content exceeds {_MAX_FETCH_BYTES}-byte cap"
            return data, None
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, ValueError) as e:
            return None, f"fetch failed: {e}"
    return None, "no fetchable source_url or canonical_path"


def _guess_mime(source_url: str | None, canonical_path: str | None) -> str | None:
    for c in (canonical_path, source_url):
        if c:
            mt, _ = mimetypes.guess_type(c)
            if mt:
                return mt
    return None


def _content_hash(data: bytes) -> str:
    """sha256-prefixed, matching migration 050's content_hash convention."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def handle_source_update_command(args):
    """source-update --source-id <id> — re-fetch + recompute content identity + audit."""
    db = None
    try:
        from empirica.data.session_database import SessionDatabase

        source_id = args.source_id
        output_format = getattr(args, "output", "human")

        db = SessionDatabase()
        cur = db.conn.cursor()
        # Resolve full id from a prefix (matches source-archive / log-artifacts UX).
        cur.execute(
            "SELECT id, title, source_url, canonical_path, content_hash, lifecycle_audit_log "
            "FROM epistemic_sources WHERE id = ? OR id LIKE ? LIMIT 2",
            (source_id, f"{source_id}%"),
        )
        rows = cur.fetchall()
        if not rows:
            print(json.dumps({"ok": False, "error": f"Source not found: {source_id}"}))
            return 1
        if len(rows) > 1:
            print(json.dumps({"ok": False, "error": f"Source ID '{source_id}' is ambiguous — use the full UUID."}))
            return 1
        full_id, title, source_url, canonical_path, old_hash, audit_json = rows[0]

        content, err = _fetch_content(source_url, canonical_path)
        if content is None:
            # Never wipe an existing hash on a failed re-fetch.
            print(json.dumps({"ok": False, "source_id": full_id, "error": err, "action": "unchanged_unreachable"}))
            return 1

        new_hash = _content_hash(content)
        new_size = len(content)
        mime = _guess_mime(source_url, canonical_path)
        changed = new_hash != old_hash

        audit = json.loads(audit_json) if audit_json else []
        if not isinstance(audit, list):
            audit = []
        audit.append(
            {
                "event": "updated",
                "at": time.time(),
                "old_content_hash": old_hash,
                "new_content_hash": new_hash,
                "changed": changed,
            }
        )
        cur.execute(
            "UPDATE epistemic_sources SET content_hash = ?, size_bytes = ?, "
            "mime_type = COALESCE(?, mime_type), lifecycle_audit_log = ? WHERE id = ?",
            (new_hash, new_size, mime, json.dumps(audit), full_id),
        )
        db.conn.commit()

        result = {
            "ok": True,
            "source_id": full_id,
            "title": title,
            "changed": changed,
            "old_content_hash": old_hash,
            "new_content_hash": new_hash,
            "size_bytes": new_size,
            "action": "updated" if changed else "refreshed_unchanged",
        }
        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            verb = "content CHANGED" if changed else "unchanged"
            print(f"source-update: {full_id[:8]} '{title}' — {verb} ({new_hash[:19]}…, {new_size} bytes)")
        return 0
    except Exception as e:
        handle_cli_error(e, "Source update", getattr(args, "verbose", False))
        return 1
    finally:
        if db:
            db.close()
