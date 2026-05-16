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
import json
import logging
import signal
import subprocess
import sys
import time
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)


# Backoff caps — bounded reconnect storm protection.
_RECONNECT_BASE_SEC = 1.0
_RECONNECT_MAX_SEC = 60.0
_AUTH_FAIL_BACKOFF_SEC = 300.0  # 5 min — auth issues rarely self-fix in seconds


class ListenerStopped(Exception):
    """Raised on SIGTERM/SIGINT so the main loop can exit cleanly."""


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
    can iterate stdout + terminate on shutdown."""
    args = ["curl", "-sN", "--no-buffer", "--keepalive-time", "30"]
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    args.append(url)
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,  # line-buffered
    )


def _is_real_event(ntfy_message: dict[str, Any]) -> bool:
    """Filter out ntfy housekeeping (keepalive, open, poll_request).

    The 'message' event type is the only one that carries content. Cortex
    also occasionally sends 'open' on subscribe — informational only.
    """
    return ntfy_message.get("event") == "message"


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
        from empirica.core.loop_scheduler.content_poll import poll_and_diff
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

    events = poll_and_diff(instance_id, loop_name, url, key)
    for ev in events:
        output_stream.write(ev.to_log_line() + "\n")
    output_stream.flush()
    return len(events)


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

    # Tag-filter subscription (opt-in via EMPIRICA_NTFY_TAG_FILTER=true).
    # Requires cortex to publish with `X-Tags: <source>,<targets...>` —
    # pending cortex proposal 2026-05-16. Until shipped, leave the env
    # var off (default) so listener receives all messages on the topic.
    # Filter value is the instance's ai_id so we only get events tagged
    # for this instance.
    import os as _os
    tag_filter = (
        instance_id
        if _os.getenv("EMPIRICA_NTFY_TAG_FILTER", "").lower() == "true"
        else None
    )
    url = _build_subscribe_url(ntfy["url"], ntfy["topic"], tag_filter=tag_filter)
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

    try:
        while True:
            proc = _stream_factory(url, headers)
            if proc.stdout is None:
                err_stream.write("listener: stream factory returned no stdout — aborting\n")
                return 1
            connected_ok = False
            try:
                for line in proc.stdout:
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
            finally:
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
            if not connected_ok:
                _sleep(_AUTH_FAIL_BACKOFF_SEC)
                backoff = _RECONNECT_BASE_SEC
            else:
                _sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_SEC)
    except ListenerStopped as e:
        err_stream.write(f"listener: stopped by {e}\n")
        return 0
    except Exception as e:
        err_stream.write(f"listener: unexpected exit: {type(e).__name__}: {e}\n")
        return 1
