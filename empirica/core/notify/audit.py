"""Always-on dispatcher audit log — `~/.empirica/notify-dispatcher.jsonl`.

Records one metadata-only line per emit attempt (success, failure,
fallback) so the cockpit can render dispatcher activity faithfully
regardless of which backend the event was routed to.

Why a separate file from notify.log:
  notify.log is the LOG BACKEND'S destination (a place users may also
  tail). Events routed to ntfy / stdout never appear in it. The cockpit
  needs every emit attempt — that's what this file is.

Schema (one JSON object per line):
  ts                 ISO-8601 UTC timestamp
  source             event.source (e.g. "loop:metrics", "hook:postflight")
  severity           "info" | "warning" | "critical"
  topic              resolved topic (or null)
  resolved_backend   the backend the dispatcher picked
  fell_back          true if the resolved backend wasn't usable
  fallback_reason    why we fell back (or null)
  ok                 emit succeeded
  response_code      HTTP code for ntfy etc. (or null)
  detail             short human string from the backend
  project_id         optional — emitting project's id (for cross-project views)

Metadata-only by design. NO title/message/rationale/tags. Content stays
in destinations; this file is for telemetry. Keeps the file small,
avoids leaking notification content into a debug file.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AUDIT_PATH = Path.home() / ".empirica" / "notify-dispatcher.jsonl"
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB — same convention as log backend
KEEP_ROTATIONS = 5


def _maybe_rotate(path: Path) -> None:
    """Rotate path → path.1 → .2 → ... when it exceeds MAX_SIZE_BYTES.

    Best-effort. Failures are swallowed — telemetry rotation should
    never block emit.
    """
    try:
        if not path.exists():
            return
        if path.stat().st_size <= MAX_SIZE_BYTES:
            return
        for i in range(KEEP_ROTATIONS - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            dst = path.with_suffix(path.suffix + f".{i + 1}")
            if src.exists():
                src.replace(dst)
        path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def append_audit(
    *,
    source: str | None,
    severity: str,
    topic: str | None,
    resolved_backend: str,
    fell_back: bool,
    fallback_reason: str | None,
    ok: bool,
    response_code: int | None,
    detail: str,
    project_id: str | None = None,
    path: Path | None = None,
) -> None:
    """Write one audit row. Best-effort — telemetry never blocks emit."""
    p = path or AUDIT_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        _maybe_rotate(p)
        row = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "source": source,
            "severity": severity,
            "topic": topic,
            "resolved_backend": resolved_backend,
            "fell_back": fell_back,
            "fallback_reason": fallback_reason,
            "ok": ok,
            "response_code": response_code,
            "detail": detail,
            "project_id": project_id,
        }
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def read_recent(limit: int = 5, path: Path | None = None) -> list[dict[str, Any]]:
    """Return the last `limit` rows in chronological order (oldest first
    of the slice — easier for the cockpit to render top-down).

    Tolerant to malformed lines. Reads only the tail-needed bytes.
    """
    p = path or AUDIT_PATH
    if not p.exists():
        return []

    out: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return list(out)


def last_failure(path: Path | None = None) -> dict[str, Any] | None:
    """Most recent row where ok==False. Used for the failed-emit banner."""
    p = path or AUDIT_PATH
    if not p.exists():
        return None
    last: dict[str, Any] | None = None
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if row.get("ok") is False:
                    last = row
    except OSError:
        return None
    return last


#: A backend failure younger than this is "recent": the cockpit banners it and
#: doctor reports it. One number, one home — the view used to own it.
FAILURE_WINDOW_SECONDS = 3600


def failure_within_window(
    failure_row: dict[str, Any] | None,
    now: datetime,
    window_seconds: int = FAILURE_WINDOW_SECONDS,
) -> dict[str, Any] | None:
    """The failure row with its age attached if it is younger than the window,
    else None. Lives beside the audit readers rather than in the cockpit view
    so a non-visual consumer (doctor) can ask the same question the banner
    asks, without importing a drawing module or re-deriving the rule."""
    if not failure_row:
        return None
    ts_raw = failure_row.get("ts")
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return None
    age = (now - ts).total_seconds()
    if age > window_seconds:
        return None
    out = dict(failure_row)
    out["age_seconds"] = int(age)
    return out


def fell_back_count(window_hours: float = 24.0, path: Path | None = None) -> int:
    """Count audit rows where fell_back==True within the last N hours."""
    p = path or AUDIT_PATH
    if not p.exists():
        return 0
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
    count = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not row.get("fell_back"):
                    continue
                ts_raw = row.get("ts")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
                if ts >= cutoff:
                    count += 1
    except OSError:
        return 0
    return count


def emit_count(window_hours: float = 24.0, path: Path | None = None) -> int:
    """Total audit rows in the last N hours. Renders 24h activity total."""
    p = path or AUDIT_PATH
    if not p.exists():
        return 0
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
    count = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts_raw = row.get("ts")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
                if ts >= cutoff:
                    count += 1
    except OSError:
        return 0
    return count


def last_emit_by_source(
    sources: list[str],
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """For each source string, return the most recent audit row matching it.

    Used by the cockpit to annotate loops table rows with their last
    notify. Single pass over the file; sources are exact matches against
    the audit row's `source` field.
    """
    p = path or AUDIT_PATH
    if not p.exists() or not sources:
        return {}
    wanted = set(sources)
    latest: dict[str, dict[str, Any]] = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                src = row.get("source")
                if src in wanted:
                    latest[src] = row
    except OSError:
        return {}
    return latest


__all__ = [
    "AUDIT_PATH",
    "append_audit",
    "emit_count",
    "fell_back_count",
    "last_emit_by_source",
    "last_failure",
    "read_recent",
]
