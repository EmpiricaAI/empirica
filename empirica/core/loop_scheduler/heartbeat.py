"""Heartbeat emitter for empirica loop listen persistent service.

Posts liveness signals to Cortex so the extension's GET /v1/listeners
aggregation can render accurate per-ai_id presence.

Closes prop_5rlp6tkclvhhhdqjs5nhcmnvni (extension scope addendum to
prop_oxrhoehv4) — sequenced behind cortex's POST /v1/listeners/heartbeat
endpoint.

Architecture choice (option-b from prop_hs55f5px Q4):
emission point is the **persistent OS service** (this module), NOT the
in-session Monitor that extension originally scoped in prop_5rlp6tk.
Reasons:
  - Machine-anchored — survives session boundaries; cortex's
    GET /v1/listeners sees accurate liveness regardless of whether
    a Claude session is currently open.
  - Reflects the default install (setup-claude-code Stage 6.7
    installs the persistent service for the user's ai_id).
  - Cortex unblocks immediately — they can ship the heartbeat
    endpoint against a real emitter without waiting for extension's
    bigger CLI-refactor (prop_oxrhoehv4) to land.

The in-session emission point (extension's original scoping) can be
added later as a `mode: 'session'` companion if cortex's aggregation
needs to distinguish "service alive" vs "session attached" — for now
the persistent emission is the truthful signal.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

_HEARTBEAT_ENDPOINT_PATH = "/v1/listeners/heartbeat"
_DEFAULT_INTERVAL_SEC = 45.0
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
        # OAuth-first with api_key fallback (empirica auth login) — the
        # resolver never returns a stale token and never raises past here.
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
        return creds.get("url"), creds.get("bearer")
    except Exception:
        return None, None


class HeartbeatEmitter:
    """Background heartbeat emitter for `empirica loop listen`.

    Posts liveness signals to Cortex's /v1/listeners/heartbeat every
    `interval_sec`. Runs in a daemon thread; never crashes the
    listener on failure (logs warnings + continues).

    Payload (per prop_5rlp6tk spec):
        {"ai_id": <listener's --instance arg>,
         "instance_id": socket.gethostname(),  # distinguishes multi-machine deploys
         "capabilities": []}

    Cortex TTL ~2min; emit every 45s by default → 2-3 misses tolerated
    before cortex marks the ai_id offline.

    Lifecycle:
        emitter = HeartbeatEmitter(ai_id="empirica")
        emitter.start()           # spawn the daemon thread
        ...                       # listener body runs
        emitter.stop(timeout=2)   # signal + join

    Test injection (all optional, kwargs only):
        _post_fn: replaces HTTP POST — pass a Mock that records calls.
        _resolve_creds_fn: replaces credentials resolution — return
            (url, key) directly without touching disk.
    """

    def __init__(
        self,
        ai_id: str,
        *,
        instance_id: str | None = None,
        interval_sec: float = _DEFAULT_INTERVAL_SEC,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        capabilities: list | None = None,
        _post_fn: Callable[[str, bytes, dict, float], int] = _default_post,
        _resolve_creds_fn: Callable[[], tuple] = _default_resolve_creds,
    ):
        self.ai_id = ai_id
        self.instance_id = instance_id or socket.gethostname() or "unknown-host"
        self.interval_sec = interval_sec
        self.timeout_sec = timeout_sec
        self.capabilities = list(capabilities or [])
        self._post_fn = _post_fn
        self._resolve_creds_fn = _resolve_creds_fn
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the heartbeat thread. Idempotent — no-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"heartbeat-{self.ai_id}",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal stop + join. Idempotent."""
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None

    def emit_once(self) -> int:
        """Send one heartbeat synchronously.

        Returns the HTTP status code (200 on success, 4xx/5xx on cortex
        error, -1 on network failure, 0 when skipped because cortex
        creds aren't configured).
        """
        url, api_key = self._resolve_creds_fn()
        if not url or not api_key:
            return 0  # SKIP — cortex not configured
        endpoint = f"{url.rstrip('/')}{_HEARTBEAT_ENDPOINT_PATH}"
        body = json.dumps(
            {
                "ai_id": self.ai_id,
                "instance_id": self.instance_id,
                "capabilities": self.capabilities,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return self._post_fn(endpoint, body, headers, self.timeout_sec)

    def _loop(self) -> None:
        """Background loop. wait-on-event so stop() interrupts immediately."""
        while not self._stop_event.is_set():
            try:
                self.emit_once()
            except Exception as e:
                logger.warning("heartbeat: emit failed: %s: %s", type(e).__name__, e)
            # Interruptible sleep — stop() sets the event and wait returns immediately
            self._stop_event.wait(self.interval_sec)
