"""Auto-accept mode toggle — cockpit's wire to cortex's per-user auto-accept.

Per cortex's design (Option 1, 2026-05-15): users.auto_accept_mode lives
server-side on cortex. When ON, every cortex_propose with this user's
api_key skips ECO review and goes straight to status='accepted' with
eco_decision.actor='auto-mode:<user>'. The target AI's listener wakes as
if ECO had pressed Accept on the phone.

## Endpoints (cortex side, shipping in parallel)

  GET /v1/users/me/auto-accept   → {enabled: bool}
  POST /v1/users/me/auto-accept  body {enabled: bool} → {enabled: bool}

## Why this lives in core/cockpit/ not core/loop_scheduler/

Auto-accept is an orchestration policy (does ECO need to ack each proposal?),
not a scheduler concern. The TUI cockpit is the canonical control surface
for it on the desktop — the extension has its own toggle that hits the
same endpoint. Both are clients of the same per-user column.

## Cache + degradation

Aggregator calls fetch_auto_accept_mode() on every refresh (~5s by default).
Module caches with a configurable TTL (default 30s) to avoid hammering
cortex on TUI refresh ticks. On 404 / connection-refused / auth-fail, the
function returns None — caller treats None as "unavailable, hide the chip"
(graceful when cortex hasn't shipped the endpoint yet).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_PATH = "/v1/users/me/auto-accept"
_CACHE_TTL_SEC = 30.0
_REQUEST_TIMEOUT_SEC = 3.0

# Module-level cache so the TUI's 5s refresh doesn't hammer cortex.
# Value is None when unknown (initial state or stale > TTL).
_cache_value: bool | None = None
_cache_at: float = 0.0


def _cortex_creds() -> tuple[str, str] | None:
    """Resolve (url, bearer) through the SHARED cortex credential contract.

    Was a private `(url, api_key)` read off the loader — the same shape, with the
    same docstring, as the one that made `empirica listener on` fail on an
    OAuth-only seat. `cortex_bearer` is OAuth-first with an api_key fallback and
    skips a key recorded as terminally dead; a direct `cfg["api_key"]` read can
    see neither, so every private resolver is its own 401 contract.
    """
    try:
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
    except Exception as e:
        logger.debug(f"auto-accept: cortex creds load failed: {e}")
        return None
    url, bearer = creds.get("url"), creds.get("bearer")
    if not url or not bearer:
        logger.debug("auto-accept: no cortex credential (%s)", creds.get("reason") or creds.get("source"))
        return None
    return url, bearer


def _request(method: str, url: str, key: str, body: dict | None = None) -> dict | None:
    """Bearer-authenticated HTTP. Returns parsed body or None on any error.
    Distinguishes connection-refused (cortex down) from 404 (endpoint not
    shipped yet) only via the debug log — both surface as None to callers."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug(f"auto-accept: endpoint {url} not shipped yet (404)")
        else:
            logger.debug(f"auto-accept: HTTPError {e.code}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug(f"auto-accept: request failed ({type(e).__name__}: {e})")
        return None


def fetch_auto_accept_mode(*, force: bool = False) -> bool | None:
    """Return current auto-accept state. Cached for _CACHE_TTL_SEC.

    Returns:
      True  → auto-accept ON (proposals from this user auto-accept on cortex)
      False → auto-accept OFF (normal ECO review)
      None  → state unknown (cortex unreachable, endpoint not shipped, etc.)
              caller should treat as "hide UI chip" / "don't trust either way"

    Pass force=True to bypass the cache (e.g. immediately after toggling).
    """
    global _cache_value, _cache_at
    if not force and _cache_value is not None and (time.time() - _cache_at) < _CACHE_TTL_SEC:
        return _cache_value
    creds = _cortex_creds()
    if creds is None:
        return None
    url, key = creds
    body = _request("GET", f"{url.rstrip('/')}{_PATH}", key)
    if body is None:
        return None
    enabled = bool(body.get("enabled", False))
    _cache_value = enabled
    _cache_at = time.time()
    return enabled


def set_auto_accept_mode(enabled: bool) -> bool | None:
    """Flip the per-user toggle on cortex. Returns the new state on success,
    None on failure (caller surfaces error in the UI). Bypasses + invalidates
    the cache so the next read reflects the new value."""
    global _cache_value, _cache_at
    creds = _cortex_creds()
    if creds is None:
        return None
    url, key = creds
    body = _request("POST", f"{url.rstrip('/')}{_PATH}", key, body={"enabled": bool(enabled)})
    if body is None:
        return None
    new_state = bool(body.get("enabled", enabled))
    _cache_value = new_state
    _cache_at = time.time()
    return new_state


def reset_cache() -> None:
    """Test-only: clear the module-level cache between assertions."""
    global _cache_value, _cache_at
    _cache_value = None
    _cache_at = 0.0
