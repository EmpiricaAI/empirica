"""Practitioner presence — the per-practitioner liveness + control substrate.

A *practitioner* is a Claude conversation occupying a *practice*. Its durable
identity is the ``claude_session_id`` (it survives compaction and already
anchors ``active_work_*``), NOT the empirica ``session_id`` — that one rotates
per compact window because it is the *measurement-cycle* container (it tracks
transactions). Presence is therefore keyed on ``claude_session_id`` so a single
practitioner stays continuous across compactions; ``empirica_session_id`` and
``active_transaction_id`` ride along as the churning measurement-cycle
attributes.

Storage is one JSON file per practitioner at
``~/.empirica/practitioner_presence_<claude_session_id>.json`` — matching the
existing ``active_work_*`` / control-plane file pattern (lock-free, atomic
replace, high-churn-friendly for 30–120s heartbeats). The resolver globs these
files → "practice → its active practitioner(s) → location + gate state".

This is the local substrate that session-init writes (B2b), the heartbeat emitter
pushes to cortex's ``POST /v1/practitioners/heartbeat`` (B2c), and autonomy's
watch-sweep reads as a deterministic liveness sensor (via ``pending_question``:
blocked-on-question vs idle vs working).

See docs/architecture/instance_isolation/PRACTITIONER_IDENTITY.md §5.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / ".empirica"
_PREFIX = "practitioner_presence_"

# A practitioner is considered stale (likely gone) after this long with no
# heartbeat. ~3× the default 60s cadence — past the realtime (30s) / default
# (60s) bands with margin; the spec's "stale-after-2N → unreachable" rule.
DEFAULT_STALE_AFTER_S = 180.0

# Status-aware staleness: a session BLOCKED on a user question (or paused) is
# alive but deliberately quiet — no UserPromptSubmit fires to refresh it, so the
# per-turn hook can't keep it warm and the active window would mark it dead even
# though it's just waiting. The daemon's refresh_live_presence() re-stamps any
# alive-PID session each tick (the PRIMARY keep-alive); this longer window is the
# FALLBACK grace for records the daemon can't PID-verify (no session_pid, or the
# gap between PID-death and the next tick). Tunable via the env var.
_DEFAULT_BLOCKED_STALE_S = 1800.0


def _blocked_stale_after() -> float:
    """Stale window for blocked/paused sessions (env-tunable, default 30min)."""
    raw = os.environ.get("EMPIRICA_PRESENCE_BLOCKED_STALE_S")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_BLOCKED_STALE_S


def _stale_after_for_status(status: str | None) -> float:
    """Per-status stale threshold. Blocked + paused get the longer grace window;
    everything else uses the default active cadence-derived window."""
    if status in ("blocked", "paused"):
        return _blocked_stale_after()
    return DEFAULT_STALE_AFTER_S


VALID_STATUS = ("active", "idle", "paused", "blocked")


def _safe(text: str) -> str:
    """Filesystem-safe suffix (same sanitization as the cockpit pause files)."""
    return text.replace("/", "-").replace("%", "").replace("..", "")


def presence_path(claude_session_id: str) -> Path:
    return EMPIRICA_DIR / f"{_PREFIX}{_safe(claude_session_id)}.json"


def write_presence(
    claude_session_id: str,
    *,
    practice_ai_id: str,
    location: str | None = None,
    status: str = "active",
    pending_question: str | None = None,
    active_transaction_id: str | None = None,
    empirica_session_id: str | None = None,
    practitioner_id: str | None = None,
    session_pid: int | None = None,
) -> dict[str, Any]:
    """Upsert the practitioner's presence record (stamps ``last_heartbeat``=now).

    Idempotent register + heartbeat in one call. ``status`` must be a valid
    state. The stable key is ``claude_session_id``; ``empirica_session_id`` +
    ``active_transaction_id`` are the churning measurement-cycle attributes.

    ``session_pid`` is the Claude Code parent PID — captured at session-init,
    where ``os.getppid()`` reliably resolves to it. It is the liveness anchor the
    daemon's :func:`refresh_live_presence` probes to keep an alive-but-quiet
    session (e.g. one blocked on a user question) non-stale. Writers that don't
    re-supply it pass ``None`` — an existing record's ``session_pid`` is then
    PRESERVED rather than clobbered, so the anchor survives high-churn rewrites
    (the per-turn refresh, a daemon touch).
    """
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status {status!r} — must be one of {VALID_STATUS}")
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    # Preserve the liveness anchor across rewrites that don't re-supply it.
    if session_pid is None:
        prior = read_presence(claude_session_id)
        if prior is not None and isinstance(prior.get("session_pid"), int):
            session_pid = prior["session_pid"]
    record: dict[str, Any] = {
        "claude_session_id": claude_session_id,
        # nullable seam — becomes user_id × practice_id × harness_class when the
        # durable cross-session id lands (spec §5 / open decision 1).
        "practitioner_id": practitioner_id,
        "practice_ai_id": practice_ai_id,
        "location": location,
        "status": status,
        "pending_question": pending_question,
        "active_transaction_id": active_transaction_id,
        "empirica_session_id": empirica_session_id,
        "session_pid": session_pid,
        "last_heartbeat": time.time(),
    }
    path = presence_path(claude_session_id)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    tmp.replace(path)  # atomic
    return record


def read_presence(claude_session_id: str) -> dict[str, Any] | None:
    path = presence_path(claude_session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_presence(claude_session_id: str) -> bool:
    """Remove the practitioner's presence (session-end). True if a file was removed."""
    try:
        presence_path(claude_session_id).unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def _all_presence_files() -> list[Path]:
    if not EMPIRICA_DIR.exists():
        return []
    return sorted(EMPIRICA_DIR.glob(f"{_PREFIX}*.json"))


def list_presence(
    practice_ai_id: str | None = None,
    *,
    include_stale: bool = False,
    stale_after: float | None = None,
) -> list[dict[str, Any]]:
    """Resolve practitioners → presence records.

    With ``practice_ai_id`` set, scopes to that practice's practitioners (the
    "practice → its active practitioner(s)" resolver). Each returned record gets
    a derived ``stale`` flag (``last_heartbeat`` older than the threshold, or
    missing); stale records are excluded unless ``include_stale``.

    The threshold is STATUS-AWARE by default (``stale_after=None``): blocked /
    paused sessions get the longer :func:`_blocked_stale_after` grace, everything
    else the default active window. Pass an explicit ``stale_after`` to force a
    single flat threshold for all records.
    """
    now = time.time()
    out: list[dict[str, Any]] = []
    for path in _all_presence_files():
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if practice_ai_id is not None and rec.get("practice_ai_id") != practice_ai_id:
            continue
        threshold = stale_after if stale_after is not None else _stale_after_for_status(rec.get("status"))
        last = rec.get("last_heartbeat")
        age = (now - last) if isinstance(last, (int, float)) else None
        rec["stale"] = age is None or age > threshold
        if rec["stale"] and not include_stale:
            continue
        out.append(rec)
    return out


def _pid_alive(pid: int) -> bool:
    """True if the process is still alive (signal-0 probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user — still alive
    except OSError:
        return False
    return True


def refresh_live_presence(*, now: float | None = None) -> dict[str, int]:
    """Daemon liveness re-stamp — keep alive-but-quiet sessions non-stale.

    For each presence record carrying a live ``session_pid`` (the Claude Code
    parent), bump ``last_heartbeat`` to now. This is the FIX for the
    blocked-while-idle gap: a session blocked on a user question stops firing
    UserPromptSubmit, so the per-turn refresh can't keep it warm and it would go
    stale after the active window — even though it's alive and waiting. The
    persistent service calls this each tick BEFORE listing/forwarding, so an
    alive session of ANY status (active, idle, blocked, paused) keeps emitting to
    cortex as long as its process lives.

    Records with no ``session_pid`` (legacy / pre-PID writers) or a dead PID are
    left untouched — they fall through to the status-aware TTL. Returns counts:
    ``{"refreshed", "alive", "dead", "no_pid"}``.
    """
    now = time.time() if now is None else now
    counts = {"refreshed": 0, "alive": 0, "dead": 0, "no_pid": 0}
    for path in _all_presence_files():
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = rec.get("session_pid")
        if not isinstance(pid, int):
            counts["no_pid"] += 1
            continue
        if not _pid_alive(pid):
            counts["dead"] += 1
            continue
        counts["alive"] += 1
        rec["last_heartbeat"] = now
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            tmp.replace(path)  # atomic
            counts["refreshed"] += 1
        except OSError:
            pass
    return counts


def resolve_practitioners(practice_ai_id: str, *, include_stale: bool = False) -> list[dict[str, Any]]:
    """Control-verb resolver: a practice's live practitioners.

    Returns the presence records (claude_session_id + location + status +
    active_transaction_id + pending_question) for ``practice_ai_id``. Used by
    sentinel pause/resume/status to address a practitioner of a practice, and by
    autonomy's watch-sweep as a liveness sensor.
    """
    return list_presence(practice_ai_id, include_stale=include_stale)
