"""Practitioner-presence heartbeat emitter — pushes local presence to cortex.

Companion to the listener ``HeartbeatEmitter`` (which posts ai_id/machine-level
liveness to ``/v1/listeners/heartbeat``). This emitter posts the richer
PER-PRACTITIONER presence — one row per live ``claude_session`` — to cortex's
``POST /v1/practitioners/heartbeat`` so the mesh sees per-session liveness plus
gate state (status, pending_question, active_transaction_id).

Source of truth is the LOCAL presence store (``empirica.core.practitioner_presence``
— file-per-practitioner keyed on the durable ``claude_session_id``). The session
hooks write/clear those files (B2b); this emitter forwards the non-stale ones to
cortex on a cadence so cortex's TTL reflects the live practitioners. A session
that ends clears its file → the emitter stops forwarding → cortex's staleness
window marks it offline.

cortex contract (``transport_handlers_practitioners.py``)::

    POST /v1/practitioners/heartbeat
    auth: Bearer api_key — the key's tenant.user_id is the authoritative writer.
    body: {machine, session_id  [both required, non-empty],
           status, location, active_transaction_id, practitioner_id,
           pending_question, blocked_at, blocked_reason}

``ai_id`` is OMITTED deliberately. It's an optional cross-check that cortex
resolves strict-canonically (``<org>.<tenant>.<project>``); a bare basename
returns None → the handler 403s. The field is never stored in the presence row
anyway (it's keyed user_id × machine × session_id), and the api_key already
identifies the writing user — so omitting it is both correct and robust.

mesh_mode-driven cadence (cortex's "30s/60s/120s by
``practitioner_registrations.mesh_mode``") is a cortex-side registration field,
not locally readable — so this emitter uses a fixed default interval (60s, ~3×
margin under cortex's ~180s staleness window). Coupling the cadence to the live
loop band is a follow-on.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_HEARTBEAT_ENDPOINT_PATH = "/v1/practitioners/heartbeat"
_DEFAULT_INTERVAL_SEC = 60.0
_DEFAULT_TIMEOUT_SEC = 5.0


def _default_post(url: str, body: bytes, headers: dict, timeout: float) -> int:
    """HTTP POST returning status code (or -1 on network error). Defensive — never raises."""
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def _default_resolve_creds() -> tuple[str | None, str | None]:
    """Resolve Cortex URL + api_key from credentials_loader. Defensive — returns (None, None) on any failure."""
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        cfg = get_credentials_loader().get_cortex_config()
        return cfg.get("url"), cfg.get("api_key")
    except Exception:
        return None, None


def _default_get(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    """HTTP GET → parsed JSON dict. Raises on error (callers wrap defensively)."""
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Cached slug → canonical-3-form map (bootstrap-once; the canonical id is stable
# for the process lifetime — it only changes on org/tenant/project rename, which
# would require a restart anyway). Only a NON-empty fetch is cached, so a
# transient failure retries on the next heartbeat.
_CANONICAL_MAP: dict[str, str] | None = None


def _reset_canonical_cache() -> None:
    """Test seam — clear the cached canonical map."""
    global _CANONICAL_MAP
    _CANONICAL_MAP = None


def _fetch_canonical_map(
    resolve_creds_fn: Callable[[], tuple],
    get_fn: Callable[[str, str, float], dict],
    timeout: float,
) -> dict[str, str]:
    """slug → ai_id_canonical from cortex's GET /v1/users/me/projects.

    cortex composes ai_id_canonical server-side (the strict-canonical 3-form), so
    the emitter never assembles the string itself. Defensive — {} on any failure.
    """
    url, api_key = resolve_creds_fn()
    if not url or not api_key:
        return {}
    try:
        body = get_fn(f"{url.rstrip('/')}/v1/users/me/projects", api_key, timeout)
        out: dict[str, str] = {}
        for proj in body.get("projects") or []:
            slug, canonical = proj.get("slug"), proj.get("ai_id_canonical")
            if slug and canonical:
                out[slug] = canonical
        return out
    except Exception:
        return {}


def resolve_canonical_ai_id(
    practice_basename: str | None,
    *,
    resolve_creds_fn: Callable[[], tuple] = _default_resolve_creds,
    get_fn: Callable[[str, str, float], dict] = _default_get,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> str | None:
    """Resolve a practice basename (e.g. 'empirica') → its canonical 3-form.

    ai_id is the PRACTICE ANCHOR cortex stores as practice_id; a bare basename
    fails cortex's strict-canonical resolver, so the emitter must send the
    canonical 3-form. Returns None when unresolvable → the emitter omits ai_id
    (graceful NULL practice_id, the back-compat path).
    """
    global _CANONICAL_MAP
    if not practice_basename:
        return None
    if _CANONICAL_MAP is None:
        fetched = _fetch_canonical_map(resolve_creds_fn, get_fn, timeout)
        if fetched:  # cache only a successful (non-empty) fetch
            _CANONICAL_MAP = fetched
        return fetched.get(practice_basename)
    return _CANONICAL_MAP.get(practice_basename)


def _practitioner_body(
    record: dict[str, Any], *, machine: str, canonical_ai_id: str | None = None
) -> dict[str, Any] | None:
    """Map a local presence record → cortex heartbeat body. None if unmappable.

    ``machine`` and ``session_id`` are cortex-required and non-empty; a record
    without a ``claude_session_id`` cannot be emitted and returns None.
    ``ai_id`` is the canonical 3-form (the practice anchor cortex resolves to
    practice_id) — included only when resolvable; omitted otherwise (cortex then
    leaves practice_id NULL, the back-compat path).
    """
    session_id = (record.get("claude_session_id") or "").strip()
    if not session_id or not machine:
        return None
    status = record.get("status") or "active"
    body: dict[str, Any] = {
        "machine": machine,
        "session_id": session_id,
        "status": status,
        "location": record.get("location") or "",
        "active_transaction_id": record.get("active_transaction_id"),
        "practitioner_id": record.get("practitioner_id"),
        "pending_question": record.get("pending_question"),
    }
    if canonical_ai_id:
        body["ai_id"] = canonical_ai_id
    # When blocked, surface the reason in cortex's blocked_reason column too.
    if status == "blocked" and record.get("pending_question"):
        body["blocked_reason"] = record["pending_question"]
    return body


def emit_practitioner_heartbeat(
    record: dict[str, Any],
    *,
    machine: str | None = None,
    post_fn: Callable[[str, bytes, dict, float], int] = _default_post,
    resolve_creds_fn: Callable[[], tuple] = _default_resolve_creds,
    get_fn: Callable[[str, str, float], dict] = _default_get,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> int:
    """Emit one local presence record to cortex's practitioners/heartbeat.

    Resolves the practice basename → canonical 3-form (so cortex populates
    practice_id) and sends it as ``ai_id``; omits ai_id when unresolvable.

    Returns the HTTP status code: 200 on success, 4xx/5xx on cortex error, -1 on
    network failure, 0 when skipped (cortex creds unconfigured, or the record is
    unmappable — e.g. no claude_session_id).
    """
    url, api_key = resolve_creds_fn()
    if not url or not api_key:
        return 0  # SKIP — cortex not configured
    canonical = resolve_canonical_ai_id(
        record.get("practice_ai_id"), resolve_creds_fn=resolve_creds_fn, get_fn=get_fn, timeout=timeout
    )
    body = _practitioner_body(
        record, machine=machine or socket.gethostname() or "unknown-host", canonical_ai_id=canonical
    )
    if body is None:
        return 0  # SKIP — unmappable record
    endpoint = f"{url.rstrip('/')}{_HEARTBEAT_ENDPOINT_PATH}"
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return post_fn(endpoint, payload, headers, timeout)


class PractitionerHeartbeatEmitter:
    """Background emitter: forwards local practitioner presence → cortex.

    On each tick, reads the LOCAL non-stale presence records (the
    file-per-practitioner store) and posts each to cortex's
    ``/v1/practitioners/heartbeat``. Runs in a daemon thread; never crashes the
    listener on failure (logs warnings + continues), mirroring the listener
    ``HeartbeatEmitter`` lifecycle.

    Lifecycle::

        emitter = PractitionerHeartbeatEmitter()
        emitter.start()           # spawn the daemon thread
        ...                       # listener body runs
        emitter.stop(timeout=2)   # signal + join

    Test injection (all optional, kwargs only):
        _post_fn: replaces HTTP POST — pass a Mock that records calls.
        _resolve_creds_fn: replaces credentials resolution — return (url, key).
        _list_fn: replaces the local-presence read — return a list of records.
    """

    def __init__(
        self,
        *,
        machine: str | None = None,
        interval_sec: float = _DEFAULT_INTERVAL_SEC,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        _post_fn: Callable[[str, bytes, dict, float], int] = _default_post,
        _resolve_creds_fn: Callable[[], tuple] = _default_resolve_creds,
        _get_fn: Callable[[str, str, float], dict] = _default_get,
        _list_fn: Callable[[], list] | None = None,
    ):
        self.machine = machine or socket.gethostname() or "unknown-host"
        self.interval_sec = interval_sec
        self.timeout_sec = timeout_sec
        self._post_fn = _post_fn
        self._resolve_creds_fn = _resolve_creds_fn
        self._get_fn = _get_fn
        self._list_fn = _list_fn or self._default_list
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _default_list() -> list[dict]:
        """Read local non-stale practitioner presence records."""
        from empirica.core.practitioner_presence import list_presence

        return list_presence(include_stale=False)

    def start(self) -> None:
        """Start the emitter thread. Idempotent — no-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="practitioner-heartbeat",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal stop + join. Idempotent."""
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None

    def emit_once(self) -> dict[str, int]:
        """Emit every local non-stale practitioner once.

        Returns ``{claude_session_id: status_code}`` per emitted practitioner.
        Never raises — a per-record failure records -1 and continues.
        """
        results: dict[str, int] = {}
        try:
            records = self._list_fn()
        except Exception as e:
            logger.warning("practitioner-heartbeat: list failed: %s: %s", type(e).__name__, e)
            return results
        for rec in records:
            sid = rec.get("claude_session_id") or "?"
            try:
                results[sid] = emit_practitioner_heartbeat(
                    rec,
                    machine=self.machine,
                    post_fn=self._post_fn,
                    resolve_creds_fn=self._resolve_creds_fn,
                    get_fn=self._get_fn,
                    timeout=self.timeout_sec,
                )
            except Exception as e:
                logger.warning("practitioner-heartbeat: emit failed for %s: %s", sid, e)
                results[sid] = -1
        return results

    def _loop(self) -> None:
        """Background loop. wait-on-event so stop() interrupts immediately."""
        while not self._stop_event.is_set():
            try:
                self.emit_once()
            except Exception as e:
                logger.warning("practitioner-heartbeat: tick failed: %s: %s", type(e).__name__, e)
            # Interruptible sleep — stop() sets the event and wait returns immediately.
            self._stop_event.wait(self.interval_sec)
