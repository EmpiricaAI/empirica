"""Listener liveness probe — silent-zombie defeater.

Bitten twice in production: the listener subprocess sits alive, its curl
ntfy stream silently drops (no exception, no exit), AND the held HTTP
catch-up call (poll_and_diff) hangs without ever returning. The existing
curl watchdog (listener.py:626-662) is curl-stream-bound — it only runs
inside the while-True stream loop and resets on each stdout line, so it
cannot:

  - cover the initial _emit_catchup_events at startup (cortex's reported
    95-min stall happened here)
  - help when the main thread is hung INSIDE _emit_catchup_events (the
    HTTP request is blocked, terminating curl doesn't unblock it)

This module runs a SEPARATE daemon thread that owns its own probe HTTP
call to cortex's roster endpoint (the same lightweight bearer-authenticated
GET that `mesh diagnose --cortex` uses). On N consecutive misses past the
threshold, it writes a degraded health marker AND calls os._exit(exit_code)
— a hard exit that bypasses Python cleanup so the supervisor restarts even
when other threads are hung in syscalls.

The same probe writes the positive-liveness marker
(~/.empirica/listener_health_<ai_id>.json) on every success. This
decouples `mesh status`'s health view from the catch-up cycle —
quiet-but-healthy listeners stay green even when no ntfy events arrive,
because the probe keeps refreshing the marker independent of catch-up.

Env overrides:
  EMPIRICA_LIVENESS_PROBE_INTERVAL_SEC      (default 60)
  EMPIRICA_LIVENESS_PROBE_FAIL_THRESHOLD_SEC (default 240 = 4 probe windows)
  EMPIRICA_LIVENESS_PROBE_DISABLE           (set non-empty to disable entirely)
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 60.0
_DEFAULT_FAIL_THRESHOLD_SEC = 240.0
_PROBE_HTTP_TIMEOUT_SEC = 8.0


def _listener_health_path(ai_id: str) -> Path:
    return Path.home() / ".empirica" / f"listener_health_{ai_id}.json"


def _probe_cortex_roster(cortex_url: str, api_key: str) -> int:
    """Bearer-authenticated GET to /v1/users/me/roster. Returns HTTP status code.

    Reuses the diagnose --cortex probe shape: small JSON response, proves
    both reachability and auth. Raises on connection / SSL errors so the
    caller can count the failure.
    """
    req = urllib.request.Request(
        f"{cortex_url.rstrip('/')}/v1/users/me/roster",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=_PROBE_HTTP_TIMEOUT_SEC, context=ctx) as resp:
        resp.read(64)  # drain a bit to keep the connection clean
        return resp.status


class LivenessProbe:
    """Daemon thread that probes cortex periodically + hard-exits on staleness.

    Lifecycle:
      probe = LivenessProbe(ai_id="empirica")
      probe.start()        # spawns daemon thread, idempotent
      ...                  # listener runs
      probe.stop()         # signals thread to exit, idempotent

    Hard-exit behavior: when the most-recent successful probe is older than
    fail_threshold_sec, the thread writes a degraded health marker and
    calls os._exit(exit_code). The exit is intentional and uncatchable —
    the point is supervisor restart.

    Test injection points: pass _probe_fn / _exit_fn / _sleep / _now to
    swap in fakes without monkeypatching the module.
    """

    def __init__(
        self,
        ai_id: str,
        *,
        loop_name: str = "cortex-mailbox-poll",
        interval_sec: float | None = None,
        fail_threshold_sec: float | None = None,
        exit_code: int = 2,
        _probe_fn: Callable[[str, str], int] | None = None,
        _exit_fn: Callable[[int], Any] | None = None,
        _sleep: Callable[[float], None] | None = None,
        _now: Callable[[], float] | None = None,
        _cortex_loader: Callable[[], dict[str, Any] | None] | None = None,
        _err_stream: Any = None,
    ) -> None:
        self.ai_id = ai_id
        self.loop_name = loop_name
        self.interval_sec = float(
            interval_sec
            if interval_sec is not None
            else os.environ.get("EMPIRICA_LIVENESS_PROBE_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC)
        )
        self.fail_threshold_sec = float(
            fail_threshold_sec
            if fail_threshold_sec is not None
            else os.environ.get("EMPIRICA_LIVENESS_PROBE_FAIL_THRESHOLD_SEC", _DEFAULT_FAIL_THRESHOLD_SEC)
        )
        self.exit_code = exit_code
        self._probe_fn = _probe_fn or _probe_cortex_roster
        self._exit_fn = _exit_fn or os._exit
        self._sleep = _sleep
        self._now = _now or time.time
        self._cortex_loader = _cortex_loader or _default_cortex_loader
        self._err_stream = _err_stream
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_ok_at: float | None = None
        self._consecutive_failures = 0

    @property
    def is_enabled(self) -> bool:
        return not os.environ.get("EMPIRICA_LIVENESS_PROBE_DISABLE")

    def start(self) -> None:
        if not self.is_enabled:
            self._log("liveness probe disabled via EMPIRICA_LIVENESS_PROBE_DISABLE")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"liveness-probe-{self.ai_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        cortex = self._cortex_loader()
        if not cortex or not cortex.get("url") or not cortex.get("api_key"):
            self._log("liveness probe inactive — no cortex credentials")
            return
        cortex_url = cortex["url"]
        api_key = cortex["api_key"]
        self._last_ok_at = self._now()
        self._log(
            f"liveness probe armed: interval={self.interval_sec:.0f}s "
            f"fail_threshold={self.fail_threshold_sec:.0f}s "
            f"target={cortex_url}/v1/users/me/roster"
        )
        while not self._stop_evt.is_set():
            self._do_probe(cortex_url, api_key)
            self._check_staleness()
            self._wait()

    def _do_probe(self, cortex_url: str, api_key: str) -> None:
        try:
            status = self._probe_fn(cortex_url, api_key)
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            self._consecutive_failures += 1
            self._log(f"liveness probe miss ({self._consecutive_failures} consecutive): {type(e).__name__}: {e}")
            return
        if 200 <= status < 300:
            self._last_ok_at = self._now()
            self._consecutive_failures = 0
            self._write_health_marker(status="ok")
        else:
            self._consecutive_failures += 1
            self._log(f"liveness probe miss ({self._consecutive_failures} consecutive): HTTP {status}")

    def _check_staleness(self) -> None:
        if self._last_ok_at is None:
            return
        age = self._now() - self._last_ok_at
        if age <= self.fail_threshold_sec:
            return
        self._log(
            f"LIVENESS PROBE STALE — no successful probe for {age:.0f}s "
            f"(threshold {self.fail_threshold_sec:.0f}s). Writing degraded marker "
            f"and exiting with code {self.exit_code} for supervisor restart."
        )
        self._write_health_marker(
            status="degraded",
            reason=f"liveness_probe_stale_{int(age)}s",
        )
        self._exit_fn(self.exit_code)

    def _wait(self) -> None:
        if self._sleep is not None:
            self._sleep(self.interval_sec)
            return
        self._stop_evt.wait(self.interval_sec)

    def _write_health_marker(self, *, status: str, reason: str | None = None) -> None:
        path = _listener_health_path(self.ai_id)
        body: dict[str, Any] = {
            "instance_id": self.ai_id,
            "loop": self.loop_name,
            "status": status,
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "source": "liveness_probe",
        }
        if reason:
            body["reason"] = reason
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        except OSError as e:
            logger.debug(f"liveness probe marker write failed (non-fatal): {e}")

    def _log(self, msg: str) -> None:
        if self._err_stream is not None:
            try:
                self._err_stream.write(f"{msg}\n")
                self._err_stream.flush()
            except OSError:
                pass
        else:
            logger.info(msg)


def _default_cortex_loader() -> dict[str, Any] | None:
    try:
        # OAuth-first with api_key fallback (empirica auth login); shape kept
        # {url, api_key} for the existing consumer below.
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
        if not creds.get("bearer"):
            return None
        return {"url": creds.get("url"), "api_key": creds.get("bearer")}
    except Exception as e:
        logger.warning(f"liveness probe cortex loader failed: {e}")
        return None
