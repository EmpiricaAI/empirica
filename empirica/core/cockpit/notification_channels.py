"""Notification-channels discovery — cockpit's wire to cortex's per-org topic registry.

Per cortex's ECO_COLLAB_RESCOPE Phase 4 (prop_oe7jz5...) the bare
`orchestration-events` ntfy topic is being deprecated in favour of
per-org-prefixed topics (e.g. `empirica-orchestration-events`). Cortex
runs dual-emit during the transition; once Phase 5 lands, only the
prefixed topic will be live and listeners hardcoded to bare break.

This module queries cortex for the canonical topic names so the
listener defaults to the per-org-prefixed shape automatically — no
env-var override needed, no version-pinned hardcoded topic.

## Endpoint (cortex side)

  GET /v1/users/me/notification-channels
      → {channels: [{topic: str, kind: str, ...}], system_topic: str}

Each `channels[i].topic` is a fully-resolved per-org topic name like
`empirica-orchestration-events`. Filtering by AI is still done with
the `?tags=<ai_id>` suffix at subscription time.

## Fallback

When cortex is unreachable, the endpoint returns 404 (older deploys),
or auth fails, the resolver returns the legacy bare-name topic
(`orchestration-events?tags=<ai_id>`) so the listener stays
functional. Dual-emit still covers it during the transition window.

## Cache

5-minute TTL — topic names rarely change, and the resolver is called
on every `empirica listener on` invocation. Cache is module-level
state, reset via `reset_cache()` in tests.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_PATH = "/v1/users/me/notification-channels"
_CACHE_TTL_SEC = 300.0
_REQUEST_TIMEOUT_SEC = 3.0

# Map of topic-kind hints we know how to consume. Cortex may return more
# channel kinds over time; we look up by kind and fall back to a
# substring scan on topic name when kind isn't set.
_ORCH_EVENTS_KIND = "orchestration_events"
_ORCH_EVENTS_NAME_HINT = "orchestration-events"

_cache_value: dict | None = None
_cache_at: float = 0.0


def _cortex_creds() -> tuple[str, str] | None:
    """Resolve (url, api_key) via the standard CLI loader. None when missing."""
    try:
        from empirica.config.credentials_loader import get_credentials_loader
        cfg = get_credentials_loader().get_cortex_config()
    except Exception as e:
        logger.debug(f"notification-channels: cortex creds load failed: {e}")
        return None
    url, key = cfg.get("url"), cfg.get("api_key")
    if not url or not key:
        return None
    return url, key


def _request(url: str, key: str) -> dict | None:
    """Bearer-authenticated GET. Returns parsed body or None on any error."""
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug(f"notification-channels: endpoint {url} not shipped yet (404)")
        else:
            logger.debug(f"notification-channels: HTTPError {e.code}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug(f"notification-channels: request failed ({type(e).__name__}: {e})")
        return None


def fetch_notification_channels(*, force: bool = False) -> dict | None:
    """Fetch cortex's notification-channels registry. Cached for _CACHE_TTL_SEC.

    Returns the parsed JSON body — typically:
        {"channels": [{"topic": "...", "kind": "..."}, ...],
         "system_topic": "..."}
    or None on any failure (cortex down, endpoint absent, auth fail).
    Callers MUST handle None by falling back to the legacy bare topic.

    Pass force=True to bypass the cache (e.g. immediately after a config
    change).
    """
    global _cache_value, _cache_at
    if not force and _cache_value is not None and (time.time() - _cache_at) < _CACHE_TTL_SEC:
        return _cache_value
    creds = _cortex_creds()
    if creds is None:
        return None
    url, key = creds
    body = _request(f"{url.rstrip('/')}{_PATH}", key)
    if body is None:
        return None
    _cache_value = body
    _cache_at = time.time()
    return body


def resolve_orchestration_events_topic(ai_id: str, *, force: bool = False) -> str:
    """Return the ntfy topic the listener should subscribe to for ai_id.

    Resolution order:
      1. Query cortex /v1/users/me/notification-channels
      2. Find a channel with kind='orchestration_events' OR topic containing
         'orchestration-events' — use its `topic` field as the per-org name
      3. Fall back to bare 'orchestration-events' on any failure

    Always appends `?tags=<ai_id>` for per-AI filtering and prepends the
    `ntfy:` scheme (matches the listener's existing topic shape).
    """
    body = fetch_notification_channels(force=force)
    base_topic = "orchestration-events"  # legacy fallback
    if body is not None:
        channels = body.get("channels") or []
        for ch in channels:
            kind = ch.get("kind")
            topic = ch.get("topic")
            if not topic:
                continue
            if kind == _ORCH_EVENTS_KIND or _ORCH_EVENTS_NAME_HINT in topic:
                base_topic = topic
                break
    return f"ntfy:{base_topic}?tags={ai_id}"


def reset_cache() -> None:
    """Test-only: clear the module-level cache between assertions."""
    global _cache_value, _cache_at
    _cache_value = None
    _cache_at = 0.0
