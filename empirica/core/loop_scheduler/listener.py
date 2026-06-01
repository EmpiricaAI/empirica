"""ntfy listener — push-primary wake mechanism for canonical loops.

Holds an authenticated HTTP stream to cortex's ntfy topic. Each message
arrival is a wake signal — the listener doesn't TRUST the message content
itself (defense in depth + ECO-gated autonomy property). Instead, on every
ntfy event arrival the listener triggers a catch-up content_poll which
fetches the authoritative ECO-decided state from Cortex via the existing
T6/T7 path and emits content events.

Why this shape:

  1. **Push-primary**: zero idle cost when nothing's happening; ~100ms
     wake latency when a real event lands. No periodic timer firing in
     the background ("nag not nudge" — David, 2026-05-15).

  2. **Reconnect-triggers-catch-up**: when the held connection drops
     (network blip, laptop sleep, server restart, ntfy bug), the
     listener reconnects and immediately runs ONE catch-up poll. Any
     events missed during the drop window are captured by the poll's
     diff-against-last-seen logic. No events leak.

  3. **ntfy as wake-pinger, not content source**: the listener treats
     ntfy messages as opaque wake signals. Authoritative content comes
     from the Cortex inbox/outbox catch-up. This means even if ntfy is
     compromised, the AI's reaction is bounded by what Cortex actually
     returns — preserving the ECO-gated authorization boundary.

Each event line written to stdout is one Monitor event in the running
Claude session (the SessionStart hook arms Monitor on this command's
stdout). Lines are JSON, matching ProposalEvent's wire shape so the
existing reaction protocol handles them uniformly.

Failure modes handled:

  - Connection drop / network blip → reconnect with exponential backoff
    (1s → 2s → 4s → 8s → max 60s). Catch-up poll on every reconnect.
  - Auth failure (401/403) → log error to stderr, sleep 5min, retry
    (creds may rotate; don't pin to a permanent failure).
  - ntfy keepalive messages → silently ignored (they're proof-of-life,
    not content).
  - SIGTERM / SIGINT → exit cleanly so systemd / Monitor lifecycle
    knows the listener stopped intentionally.
"""

from __future__ import annotations

import base64
import importlib.metadata
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Backoff caps — bounded reconnect storm protection.
_RECONNECT_BASE_SEC = 1.0
_RECONNECT_MAX_SEC = 60.0
_AUTH_FAIL_BACKOFF_SEC = 300.0  # 5 min — auth issues rarely self-fix in seconds

# ntfy rate-limit (HTTP 429) backoff. Hammering ntfy with reconnects when
# already rate-limited just re-trips the limit indefinitely. Apply a long
# backoff so the limit window expires before we retry. Catch-up poll
# (which hits cortex's HTTP inbox, not ntfy) continues at its own cadence
# during this window — events still flow, just via the pull path.
_RATE_LIMIT_BACKOFF_SEC = float(
    os.environ.get("EMPIRICA_LISTENER_RATE_LIMIT_BACKOFF_SEC", "1800")  # 30 min
)
# Within the rate-limit backoff, run catch-up poll at this cadence so
# events keep flowing via the pull path.
_RATE_LIMIT_CATCHUP_INTERVAL_SEC = 300.0  # 5 min

# Curl-zombie watchdog. ntfy sends 'keepalive' frames every ~45s by default;
# any longer silence means the held TCP connection is dead. The curl
# subprocess can't tell — it's blocked in a read() syscall on a socket the
# OS hasn't reaped yet. We force-terminate it so the outer reconnect loop
# kicks in. Default threshold (120s) is well above ntfy's keepalive cadence
# but short enough that "no fires in N minutes" doesn't compound. Override
# with EMPIRICA_LISTENER_STALE_THRESHOLD_SEC.
_STALE_THRESHOLD_SEC = float(
    os.environ.get("EMPIRICA_LISTENER_STALE_THRESHOLD_SEC", "120")
)
_WATCHDOG_CHECK_INTERVAL_SEC = 15.0


class ListenerStopped(Exception):
    """Raised on SIGTERM/SIGINT so the main loop can exit cleanly."""


class ListenerUpgraded(Exception):
    """Raised when in-process empirica version differs from the installed
    dist-info — pip upgrade landed under a running listener. Caller exits
    cleanly with code 0 so systemd Restart=always / launchd KeepAlive=true
    relaunches the service against the new code on disk."""


def _check_version_drift() -> tuple[str, str] | None:
    """Return (in_process_version, installed_version) on drift, None otherwise.

    `empirica.__version__` is frozen at import time. `importlib.metadata.version`
    re-reads the dist-info every call — pip overwrites that file on upgrade.
    A mismatch means a pip upgrade happened under the running listener and
    the in-memory code is stale.

    Returns None on any error (missing dist-info, import failure) — drift
    check is best-effort, must never crash the listener.

    Opt-out via EMPIRICA_LISTENER_NO_DRIFT_EXIT: the upgrade-exit assumes a
    supervisor (systemd Restart=always / launchd KeepAlive) will relaunch
    against the new code. Under a bare/non-supervised Monitor (e.g. a native
    harness holding the stream, no relauncher) the self-exit just kills the
    listener permanently, so honor the bypass and report no drift.
    """
    if os.environ.get("EMPIRICA_LISTENER_NO_DRIFT_EXIT"):
        return None
    try:
        from empirica import __version__ as in_process
        installed = importlib.metadata.version("empirica")
        if in_process != installed:
            return (in_process, installed)
    except Exception:
        return None
    return None


def _install_signal_handlers() -> None:
    def _stop(signum, _frame):
        raise ListenerStopped(f"signal {signum}")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def _build_subscribe_url(
    ntfy_url: str, topic: str, tag_filter: str | None = None,
) -> str:
    """ntfy's JSON-stream endpoint — one message per stdout line.

    When `tag_filter` is set, append `?tags=<filter>` so ntfy only delivers
    messages tagged with that value. This relies on cortex publishing with
    `X-Tags: <source_claude>,<target_claudes...>` (proposed as cortex commit
    pending 2026-05-16 — until shipped, callers should leave this None or
    set EMPIRICA_NTFY_TAG_FILTER=false to avoid filtering out every event).

    Server-side filtering shrinks per-event wake traffic from "every
    listener wakes" to "only relevant listeners wake."
    """
    safe_topic = urllib.parse.quote(topic, safe="")
    base = f"{ntfy_url.rstrip('/')}/{safe_topic}/json"
    if tag_filter:
        return f"{base}?tags={urllib.parse.quote(tag_filter, safe=',')}"
    return base


def _ntfy_auth_header(
    user: str | None, password: str | None, token: str | None,
) -> dict[str, str]:
    """Resolve ntfy auth header by precedence: token (Bearer) > basic (user/pass).

    ntfy access tokens are prefixed `tk_` and use Bearer auth. The empirica
    extension obtains them when registering with the user's ntfy server +
    sets them on cortex. Basic auth (user + password) is the legacy path
    for username/password ntfy deployments. Either works; tokens are
    preferred because they're revocable + don't expose the account password.
    """
    if token:
        return {"Authorization": f"Bearer {token}"}
    if not user and not password:
        return {}
    encoded = base64.b64encode(
        f"{user or ''}:{password or ''}".encode()
    ).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


# Back-compat alias for the old name (callers / tests may still reference it).
_basic_auth_header = _ntfy_auth_header


def _open_stream(
    url: str, headers: dict[str, str],
) -> subprocess.Popen:
    """Spawn curl in held-connection mode. Returns the Popen so the caller
    can iterate stdout + terminate on shutdown.

    `-sS` keeps progress silent but surfaces errors on stderr (ntfy rate
    limits return HTTP 429 — without -S they were silently swallowed,
    making the broken listener look indistinguishable from a quiet
    healthy one). `-i` includes response headers on stdout for the FIRST
    response so the caller can read the HTTP status line; the listener
    body skips header lines until the empty-line separator.
    """
    args = ["curl", "-sSN", "-i", "--no-buffer", "--keepalive-time", "30"]
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    args.append(url)
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,  # line-buffered
    )


def _read_http_status(proc: subprocess.Popen) -> int | None:
    """Read curl's first response headers from stdout until the empty-line
    separator. Returns the HTTP status code (200, 401, 429, etc.) or None
    if the header block was malformed / stream ended before headers.

    With ``curl -i``, every response begins with ``HTTP/1.1 <code> <reason>``.
    Once we see the blank line, header parsing is done and the body
    (JSON stream) follows.
    """
    if proc.stdout is None:
        return None
    status: int | None = None
    while True:
        line = proc.stdout.readline()
        if not line:
            return status
        line = line.rstrip("\r\n")
        if not line:
            return status  # blank line = end of headers
        if status is None and line.startswith("HTTP/"):
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])


def _is_real_event(ntfy_message: dict[str, Any]) -> bool:
    """Filter out ntfy housekeeping (keepalive, open, poll_request).

    The 'message' event type is the only one that carries content. Cortex
    also occasionally sends 'open' on subscribe — informational only.
    """
    return ntfy_message.get("event") == "message"


def _listener_health_path(instance_id: str) -> Path:
    return Path.home() / ".empirica" / f"listener_health_{instance_id}.json"


def _emit_fail_heartbeat(instance_id: str, loop_name: str, *, reason: str) -> None:
    """Surface a listener-poll failure so it shows up in `empirica status`
    + cockpit instead of silently degrading (the 10-day-deaf failure mode).
    Writes a `degraded` health marker. Best-effort — never raises."""
    import datetime as _dt
    logger.error(
        "listener poll DEGRADED — instance=%s loop=%s reason=%s",
        instance_id, loop_name, reason,
    )
    try:
        _listener_health_path(instance_id).write_text(
            json.dumps({
                "instance_id": instance_id,
                "loop": loop_name,
                "status": "degraded",
                "reason": reason,
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug(f"listener health marker write failed (non-fatal): {e}")


def _clear_fail_heartbeat(instance_id: str, loop_name: str) -> None:
    """Mark the listener healthy after a successful poll. Best-effort."""
    import datetime as _dt
    try:
        _listener_health_path(instance_id).write_text(
            json.dumps({
                "instance_id": instance_id,
                "loop": loop_name,
                "status": "ok",
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug(f"listener health marker clear failed (non-fatal): {e}")


def _emit_catchup_events(
    instance_id: str, loop_name: str, output_stream=sys.stdout,
) -> int:
    """Run a content-poll catch-up. Each returned event becomes a stdout
    line — one Monitor event into the running Claude session.

    Reuses the T6/T7 poll_and_diff path: ECO-gated emission, dual-direction
    (inbox + outbox), bootstrap-aware on first run.

    Returns count of events emitted (useful for stderr-side telemetry —
    callers shouldn't depend on the value for control flow).
    """
    try:
        from empirica.config.credentials_loader import get_credentials_loader
        from empirica.core.loop_scheduler.content_poll import (
            ContentPollUnreachable,
            poll_and_diff,
        )
    except Exception as e:
        logger.warning(f"catch-up disabled — content_poll import failed: {e}")
        return 0

    try:
        cortex = get_credentials_loader().get_cortex_config()
    except Exception as e:
        logger.warning(f"catch-up disabled — cortex creds unreadable: {e}")
        return 0

    url, key = cortex.get("url"), cortex.get("api_key")
    if not url or not key:
        logger.debug("catch-up skipped — cortex creds missing")
        return 0

    # raise_on_unreachable=True: a total fetch failure must NOT be a silent
    # no-op (that's the 10-day-deaf bug). Surface it as a fail-heartbeat so
    # `empirica status` + cortex's listener aggregation see the degradation
    # immediately, and re-raise so run_listener logs it loudly to stderr/journal.
    try:
        events = poll_and_diff(
            instance_id, loop_name, url, key, raise_on_unreachable=True,
        )
    except ContentPollUnreachable:
        _emit_fail_heartbeat(instance_id, loop_name, reason="cortex_unreachable")
        raise
    # Tee target — cockpit TUI's `_read_recent_events_for_instance` tails
    # ~/.empirica/loop_fires.log to render the "N" events column + the
    # notifications detail pane. Post-T8 the listener streamed only to
    # stdout (for Monitor consumption), which left the cockpit reading a
    # stale log and showing "(no events yet — listener silent or not armed)"
    # even when listeners were actively firing (David, 2026-05-17).
    # Tee is best-effort: any write failure is logged + ignored so the
    # primary Monitor stream stays unaffected.
    log_path = Path.home() / ".empirica" / "loop_fires.log"
    # Rotate before append — cap unbounded growth at MAX_LINES with
    # hysteresis (keep last KEEP_LINES when over cap).
    _rotate_fires_log_if_oversized(log_path)
    for ev in events:
        line = ev.to_log_line()
        output_stream.write(line + "\n")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.debug(f"loop_fires.log tee failed (non-fatal): {e}")
    output_stream.flush()
    # Poll succeeded (fetch returned, state written) — mark healthy so a
    # prior `degraded` marker clears and recovery is visible.
    _clear_fail_heartbeat(instance_id, loop_name)
    return len(events)


# Size cap on the shared fires log. The file is append-only from many
# writers (one per ai_id's listener process) — left unbounded it causes
# disk bloat over weeks + slow `tail -F` start in fresh Monitor arms.
#
# Rotation is BY RENAME (current -> .1, then a fresh empty file at the
# watched path), NEVER an in-place rewrite. The wake-delivery Monitors
# `tail -F` this file; `tail -F` re-reads from offset 0 whenever the
# watched path's inode changes or the file shrinks. The previous
# rewrite-in-place (keep last N lines at the same path) therefore made
# every rotation re-emit the retained window as DUPLICATE wake events —
# a replay storm across every mesh listener's session (David, 2026-05-29).
# Renaming hands tail a fresh empty inode, so only new appends emit; the
# old lines live in `<log>.1` for cockpit/history readers. A fresh empty
# file is self-hysteretic — it won't rotate again until it regrows past
# the cap, so there's no thrash and no keep-N tuning to maintain.
_FIRES_LOG_MAX_LINES = 2000


def _rotate_fires_log_if_oversized(log_path: Path) -> None:
    """Rotate the fires log by RENAME when it exceeds _FIRES_LOG_MAX_LINES.

    Moves the current log to `<log>.1` (overwriting any previous `.1`) and
    leaves a fresh empty file at `log_path`. MUST NOT keep the retained
    tail at the watched path — `tail -F` Monitors would re-read it and
    re-fire every retained event as a duplicate wake (see module note).

    Best-effort — any failure is logged and ignored so the primary tee
    path stays unaffected. The line count is computed by streaming (not
    slurping the whole file) since rotation runs once per catch-up cycle.
    """
    try:
        if not log_path.exists():
            return
        with open(log_path, encoding="utf-8") as f:
            line_count = sum(1 for _ in f)
        if line_count <= _FIRES_LOG_MAX_LINES:
            return
        import os
        rotated = log_path.with_name(log_path.name + ".1")
        # Atomic rename: current -> .1 (replaces any prior .1). If this
        # raises, the live log is untouched (no partial state).
        os.replace(log_path, rotated)
        # Fresh empty file at the watched path so tail -F follows a clean
        # inode and subsequent appends start from zero.
        log_path.touch()
        logger.debug(
            f"loop_fires.log rotated by rename: moved {line_count} lines to "
            f"{rotated.name}, fresh empty log started"
        )
    except OSError as e:
        logger.debug(f"loop_fires.log rotation failed (non-fatal): {e}")


def run_listener(  # noqa: C901 — held-connection loop; clarity beats decomposition here
    instance_id: str,
    loop_name: str = "cortex-mailbox-poll",
    output_stream=sys.stdout,
    err_stream=sys.stderr,
    *,
    _stream_factory=_open_stream,
    _sleep=time.sleep,
    _initial_catchup: bool = True,
) -> int:
    """Held-ntfy-connection main loop. Runs forever (or until SIGTERM).

    Each iteration:
      1. (Re-)open the ntfy stream with basic auth from credentials.
      2. Run a catch-up content_poll — emits any events missed during
         the previous disconnect window.
      3. Read messages from the held stream. For each 'message' event,
         run a catch-up (the ntfy message is the wake-ping; the catch-up
         is the authoritative content fetch).
      4. On stream EOF/error → backoff + reconnect (back to step 1).

    Returns 0 on clean SIGTERM exit (so systemd doesn't treat it as a
    crash), nonzero on configuration errors (so systemd surfaces them).

    Test injection points:
      _stream_factory: replaces curl spawn — pass a fixture that yields
                       controlled stdout lines.
      _sleep: skip real sleeps in tests.
      _initial_catchup: set False in tests that want pure-stream behavior.
    """
    _install_signal_handlers()
    try:
        from empirica.config.credentials_loader import get_credentials_loader
    except Exception as e:
        err_stream.write(f"listener: credentials_loader import failed: {e}\n")
        return 2

    try:
        ntfy = get_credentials_loader().get_ntfy_config()
    except Exception as e:
        err_stream.write(f"listener: ntfy config load failed: {e}\n")
        return 2

    # Tag-filter subscription. Cortex publishes events with
    # `X-Tags: zap,orchestration_event,<source_claude>,<target_claudes...>`
    # (commit ae92166 shipped 2026-05-16). Default ON — listeners
    # subscribe with `?tags=<their_ai_id>` so they only receive events
    # touching their instance. Reduces per-event wake traffic from
    # O(N_instances) to O(involved_instances). Verified live against
    # cortex prod: positive case (source=empirica) delivered; negative
    # case (source=ecodex, target=cortex) silenced as expected.
    #
    # Override: set `EMPIRICA_NTFY_TAG_FILTER=false` to disable and
    # receive every event on the topic. Useful for debugging or for
    # listeners that need cross-instance visibility (e.g., audit
    # dashboards).
    import os as _os
    tag_filter = (
        None
        if _os.getenv("EMPIRICA_NTFY_TAG_FILTER", "true").lower() == "false"
        else instance_id
    )
    # Per-org wake topic. The legacy bare `orchestration-events` topic
    # (credentials_loader default) has no ntfy ACL grant for non-admin
    # users → every poll 403s. Resolve the org-prefixed topic
    # (`empirica-orchestration-events`) from cortex's notification-channels
    # registry instead. An explicit ORCHESTRATION_NTFY_TOPIC override still
    # wins (debug / custom deployments); resolution failure falls back to
    # the configured topic with a loud log rather than silently.
    base_topic = ntfy["topic"]
    if not _os.getenv("ORCHESTRATION_NTFY_TOPIC"):
        try:
            from empirica.core.cockpit.notification_channels import (
                _resolve_base_topic,
                fetch_notification_channels,
            )
            resolved = _resolve_base_topic(fetch_notification_channels())
            if resolved:
                base_topic = resolved
            else:
                err_stream.write(
                    "listener: per-org topic unresolved (cortex unreachable "
                    f"or no prefixed channels); using {base_topic!r}\n"
                )
        except Exception as e:
            err_stream.write(
                f"listener: per-org topic resolve failed, using {base_topic!r}: {e}\n"
            )
    url = _build_subscribe_url(ntfy["url"], base_topic, tag_filter=tag_filter)
    headers = _ntfy_auth_header(
        ntfy.get("user"), ntfy.get("password"), ntfy.get("token"),
    )
    if not headers:
        err_stream.write(
            "listener: no ntfy credentials configured. Add an `ntfy:` block to "
            "~/.empirica/credentials.yaml with one of:\n"
            "  token: tk_...    # ntfy access token (Bearer auth, preferred)\n"
            "  user: ...        # basic auth user + password (legacy)\n"
            "  password: ...\n"
            "Or set ORCHESTRATION_NTFY_USER / _PASS / _TOKEN env vars.\n"
        )
        return 2

    err_stream.write(f"listener: subscribing to {url} as {ntfy.get('user')}\n")
    err_stream.flush()

    backoff = _RECONNECT_BASE_SEC

    if _initial_catchup:
        # Catch-up on listener startup. Handles the gap between systemd
        # service start and the first ntfy message.
        try:
            n = _emit_catchup_events(instance_id, loop_name, output_stream)
            if n:
                err_stream.write(f"listener: initial catch-up emitted {n} event(s)\n")
        except Exception as e:
            err_stream.write(f"listener: initial catch-up failed: {e}\n")

    # Start the heartbeat thread (prop_5rlp6tk). Reports liveness to
    # Cortex's /v1/listeners/heartbeat so the extension's GET /v1/listeners
    # aggregation sees this ai_id as alive while the persistent service
    # is up. Failures are non-fatal — heartbeat never crashes the listener.
    heartbeat = None
    try:
        from empirica.core.loop_scheduler.heartbeat import HeartbeatEmitter
        heartbeat = HeartbeatEmitter(ai_id=instance_id)
        heartbeat.start()
        err_stream.write(f"listener: heartbeat emitter started for ai_id={instance_id}\n")
    except Exception as e:
        err_stream.write(f"listener: heartbeat start failed (non-fatal): {e}\n")

    try:
        while True:
            proc = _stream_factory(url, headers)
            if proc.stdout is None:
                err_stream.write("listener: stream factory returned no stdout — aborting\n")
                return 1

            # With `curl -i`, the first response carries HTTP headers on
            # stdout. Parse them so we can detect 429 (ntfy rate limit)
            # explicitly instead of treating it as a generic connect failure.
            http_status = _read_http_status(proc)
            if http_status is not None and http_status >= 400:
                err_stream.write(
                    f"listener: ntfy returned HTTP {http_status}; "
                    f"draining + applying error backoff\n"
                )
                err_stream.flush()
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, Exception):
                    try:
                        proc.kill()
                    except Exception as kill_err:
                        # Cleanup path — process is being torn down; logging
                        # the kill failure is informational, not actionable.
                        err_stream.write(f"listener: curl kill failed in 4xx/5xx cleanup: {kill_err}\n")

                if http_status == 429:
                    # Rate-limited. Long backoff with periodic catch-up so
                    # events keep flowing via the cortex inbox HTTP path
                    # (which is rate-limited separately, if at all).
                    err_stream.write(
                        f"listener: ntfy rate limit (429). Backing off "
                        f"{_RATE_LIMIT_BACKOFF_SEC:.0f}s with catch-up every "
                        f"{_RATE_LIMIT_CATCHUP_INTERVAL_SEC:.0f}s.\n"
                    )
                    err_stream.flush()
                    elapsed = 0.0
                    while elapsed < _RATE_LIMIT_BACKOFF_SEC:
                        try:
                            _emit_catchup_events(instance_id, loop_name, output_stream)
                        except Exception as e:
                            err_stream.write(
                                f"listener: rate-limit-window catch-up failed: {e}\n"
                            )
                        sleep_for = min(
                            _RATE_LIMIT_CATCHUP_INTERVAL_SEC,
                            _RATE_LIMIT_BACKOFF_SEC - elapsed,
                        )
                        _sleep(sleep_for)
                        elapsed += sleep_for
                    backoff = _RECONNECT_BASE_SEC
                    continue
                # Other 4xx/5xx — treat as auth-fail-style backoff.
                _sleep(_AUTH_FAIL_BACKOFF_SEC)
                backoff = _RECONNECT_BASE_SEC
                continue

            connected_ok = False
            # Watchdog state — guards against silent TCP-dead-curl-alive zombies.
            # Shared with the watchdog thread; protected by GIL (single-int
            # writes/reads are atomic in CPython).
            import threading as _threading
            last_activity_at: list[float] = [time.time()]
            watchdog_stop = _threading.Event()

            def _watchdog(
                # Bind loop variables at function-definition time so the
                # thread closure captures THIS iteration's values, not
                # whatever they become in the next outer-loop iteration.
                _stop_evt: _threading.Event = watchdog_stop,
                _activity_ref: list[float] = last_activity_at,
                _watched_proc: subprocess.Popen = proc,
            ) -> None:
                while not _stop_evt.wait(_WATCHDOG_CHECK_INTERVAL_SEC):
                    idle = time.time() - _activity_ref[0]
                    if idle > _STALE_THRESHOLD_SEC:
                        err_stream.write(
                            f"listener: stream stale for {idle:.0f}s "
                            f"(>{_STALE_THRESHOLD_SEC:.0f}s threshold). "
                            f"Terminating curl to force reconnect.\n"
                        )
                        err_stream.flush()
                        try:
                            _watched_proc.terminate()
                        except Exception as term_err:
                            # Watchdog terminate is best-effort; the outer
                            # loop will retry the reconnect path regardless.
                            err_stream.write(f"listener: watchdog terminate failed: {term_err}\n")
                        return  # one-shot — outer loop handles reconnect

            watchdog_thread = _threading.Thread(
                target=_watchdog, name=f"listener-watchdog-{instance_id}",
                daemon=True,
            )
            watchdog_thread.start()

            try:
                for line in proc.stdout:
                    last_activity_at[0] = time.time()  # ANY line resets staleness
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        err_stream.write(f"listener: skipping non-JSON line: {line[:80]}\n")
                        continue
                    if not _is_real_event(msg):
                        continue
                    connected_ok = True
                    backoff = _RECONNECT_BASE_SEC  # reset on successful message
                    err_stream.write(
                        f"listener: ntfy event arrived "
                        f"(id={msg.get('id','?')[:12]}) → running catch-up\n"
                    )
                    _emit_catchup_events(instance_id, loop_name, output_stream)
                    # Catch-up can take a few seconds; refresh activity stamp
                    # so the watchdog doesn't fire on a slow-but-healthy poll.
                    last_activity_at[0] = time.time()
            finally:
                watchdog_stop.set()
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

            # Stream ended. If we never got a message, this might be auth
            # failure — backoff harder. Otherwise it's a normal drop —
            # reconnect with exponential backoff. Always catch up first.
            err_stream.write(f"listener: stream ended (connected_ok={connected_ok}), reconnecting\n")
            try:
                _emit_catchup_events(instance_id, loop_name, output_stream)
            except Exception as e:
                err_stream.write(f"listener: post-drop catch-up failed: {e}\n")

            # Reconnect is the natural restart boundary — check if a pip
            # upgrade landed under us. Self-exit with code 0 lets
            # systemd Restart=always / launchd KeepAlive=true relaunch
            # the service against the new code on disk. Without this, the
            # listener pins to the pre-upgrade version until next reboot.
            drift = _check_version_drift()
            if drift is not None:
                in_proc, installed = drift
                err_stream.write(
                    f"listener: version drift detected — in-process v{in_proc}, "
                    f"installed v{installed}. Exiting for clean relaunch.\n"
                )
                raise ListenerUpgraded(f"{in_proc} != {installed}")

            if not connected_ok:
                _sleep(_AUTH_FAIL_BACKOFF_SEC)
                backoff = _RECONNECT_BASE_SEC
            else:
                _sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_SEC)
    except ListenerStopped as e:
        err_stream.write(f"listener: stopped by {e}\n")
        return 0
    except ListenerUpgraded as e:
        err_stream.write(f"listener: upgraded ({e}) — exiting for relaunch\n")
        return 0
    except Exception as e:
        err_stream.write(f"listener: unexpected exit: {type(e).__name__}: {e}\n")
        return 1
    finally:
        if heartbeat is not None:
            try:
                heartbeat.stop(timeout=2.0)
            except Exception as e:
                err_stream.write(f"listener: heartbeat stop failed (non-fatal): {e}\n")
