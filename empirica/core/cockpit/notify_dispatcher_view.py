"""Cockpit view onto the notify dispatcher.

Bundles audit-log telemetry + current backend status into one dict
the cockpit can render. Single source of truth so the TUI and the
JSON `--output json` view agree.

The audit log is global (one file at `~/.empirica/notify-dispatcher.jsonl`)
because notify config is global. Per-instance scoping is achieved by
filtering the `recent` list to events whose `source` matches loops
registered to that instance — the cockpit's instance_state does that
join.

Out of scope here: routing-rule preview, hypothetical-event matcher,
topic subscription introspection. Just makes the existing data
legible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from empirica.core.notify.audit import (
    FAILURE_WINDOW_SECONDS,
    emit_count,
    failure_within_window,
    fell_back_count,
    last_emit_by_source,
    last_failure,
    read_recent,
)
from empirica.core.notify.backends import backends_status_snapshot
from empirica.core.notify.config import NotifyConfig, load_config

# How long after a failure the cockpit header should banner it.
FAILURE_BANNER_WINDOW_SECONDS = FAILURE_WINDOW_SECONDS  # kept as the view's name for its callers


_EMPTY_BLOCK: dict[str, Any] = {
    "default_backend": None,
    "backends": [],
    "recent": [],
    "last_failure": None,
    "banner_failure": None,
    "fell_back_count_24h": 0,
    "emit_count_24h": 0,
}


def build_notify_dispatcher_block(
    config: NotifyConfig | None = None,
    recent_limit: int = 5,
) -> dict[str, Any]:
    """Return the cockpit-facing dispatcher dict.

    Shape:
      default_backend       str
      backends              list[{name, configured, is_default, ...}]
      recent                list[audit rows] (oldest of slice first)
      last_failure          audit row or None  (most recent ok==False ever)
      banner_failure        audit row or None  (last_failure within 1h)
      fell_back_count_24h   int
      emit_count_24h        int

    Telemetry must never break the cockpit — any read/parse failure
    yields an empty block, never an exception.
    """
    try:
        cfg = config if config is not None else load_config()
        now = datetime.now(tz=timezone.utc)
        failure = last_failure()
        return {
            "default_backend": cfg.default_backend,
            "backends": backends_status_snapshot(cfg),
            "recent": read_recent(limit=recent_limit),
            "last_failure": failure,
            "banner_failure": failure_within_window(failure, now, FAILURE_BANNER_WINDOW_SECONDS),
            "fell_back_count_24h": fell_back_count(window_hours=24.0),
            "emit_count_24h": emit_count(window_hours=24.0),
        }
    except Exception:
        return dict(_EMPTY_BLOCK)


def annotate_loops_with_last_notify(
    loops_dict: dict[str, dict[str, Any]],
    audit_path: Any = None,
) -> None:
    """Mutate `loops_dict` in place, adding a `last_notify` field per loop.

    Match key: source == "loop:{name}". Loops without a matching audit
    row get last_notify=None.

    `audit_path`: optional override for tests.
    """
    if not loops_dict:
        return
    sources = [f"loop:{name}" for name in loops_dict]
    by_source = last_emit_by_source(sources, path=audit_path)
    for name, loop in loops_dict.items():
        row = by_source.get(f"loop:{name}")
        loop["last_notify"] = (
            {
                "ts": row.get("ts"),
                "resolved_backend": row.get("resolved_backend"),
                "topic": row.get("topic"),
                "fell_back": bool(row.get("fell_back")),
                "ok": bool(row.get("ok")),
            }
            if row
            else None
        )


__all__ = [
    "FAILURE_BANNER_WINDOW_SECONDS",
    "annotate_loops_with_last_notify",
    "build_notify_dispatcher_block",
]
