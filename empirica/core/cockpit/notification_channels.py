"""Notification-channels discovery — cockpit's wire to cortex's per-tenant topic registry.

Per cortex's ECO_COLLAB_RESCOPE Phase 4 (prop_oe7jz5...) the bare
`orchestration-events` ntfy topic was deprecated in favour of
org-prefixed topics (e.g. `empirica-orchestration-events`). T16/T17
tightened isolation further so the channel registry is now per-tenant
(each tenant's `/v1/users/me/notification-channels` returns the topic
keyed for THAT tenant's wake stream). Cortex runs dual-emit during
the transition; once Phase 5 lands, only the
prefixed topic will be live and listeners hardcoded to bare break.

This module queries cortex for the canonical topic names so the
listener defaults to the per-tenant shape automatically — no
env-var override needed, no version-pinned hardcoded topic.

## Endpoint (cortex side)

  GET /v1/users/me/notification-channels
      → {channels: [{topic: str, kind: str, ...}], system_topic: str}

Each `channels[i].topic` is the fully-resolved topic name cortex
wants THIS caller (tenant-scoped post-T16/T17) to subscribe to — e.g.
`empirica-orchestration-events` for the org-shared topic, or a
tenant-keyed variant where cortex has narrowed isolation further.
Filtering by AI is still done with the `?tags=<ai_id>` suffix at
subscription time.

## No silent bare fallback (legacy topic killed — 2026-06)

The bare `orchestration-events` topic has NO ntfy ACL grant for
non-admin users, so subscribing to it 403s on every poll. Worse, the
old resolver fell back to it *silently* whenever it couldn't find an
explicit `orchestration-events` channel in the endpoint payload — and
cortex's payload never contained one (it lists eco/collab/system/
publish/roster, keyed by `category` not `kind`). Net effect: the whole
fleet silently pinned to the dead bare topic, generating a 403 storm,
while the per-org migration looked "complete".

So the resolver now DERIVES the per-org prefix from the sibling
channels cortex DOES return (all fully `<org>-` prefixed, e.g.
`empirica-system`) and constructs `<org>-orchestration-events`. If the
endpoint is genuinely unreachable / returns nothing prefixable, it
RAISES rather than silently subscribing to the dead bare topic — the
caller surfaces a clean error instead of registering a 403-ing
listener.

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
        url,
        method="GET",
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
      2. Match an explicit orchestration-events channel — by cortex's
         `category` field, the legacy `kind` field, OR a name-hint substring
      3. Else DERIVE the per-org prefix from the sibling channels cortex
         returns (all `<org>-` prefixed) and build `<org>-orchestration-events`
      4. Else RAISE — never silently subscribe to the deprecated bare topic
         (no ACL grant → 403 storm)

    Always appends `?tags=<canonical-3-form>` for per-AI filtering and prepends
    the `ntfy:` scheme (matches the listener's existing topic shape). The tag is
    canonicalized to the full `<org>.<tenant>.<project>` form — the SAME tag
    cortex publishes and `loop listen` subscribes with — so an in-session
    listener armed via `listener on` matches live ntfy pushes instead of relying
    on catch-up polling. (Subscribing with the bare slug matches nothing.)
    """
    body = fetch_notification_channels(force=force)
    base_topic = _resolve_base_topic(body)
    if base_topic is None:
        raise RuntimeError(
            "Cannot resolve the per-org orchestration-events topic: cortex's "
            f"{_PATH} endpoint was unreachable or returned no org-prefixed "
            "channels. Refusing to fall back to the deprecated bare "
            "'orchestration-events' topic (no ACL grant — it 403s). Check "
            "cortex reachability / credentials, then retry."
        )
    return f"ntfy:{base_topic}?tags={_canonical_tag(ai_id)}"


def _canonical_tag(ai_id: str) -> str:
    """Resolve `ai_id` to its canonical 3-form (`<org>.<tenant>.<project>`) for
    the subscribe tag — the single source of truth shared with `loop listen`
    (both call `_resolve_canonical_ai_id`). Cortex publishes events tagged with
    the 3-form; subscribing with the bare slug matches nothing (live pushes
    silently dropped, only catch-up poll catches up).

    Falls back to the bare `ai_id` when cortex creds are absent or the roster
    lookup fails — `_resolve_canonical_ai_id` already returns the basename
    unchanged on failure, so the failure is loud (0-result warnings) not silent.
    """
    creds = _cortex_creds()
    if creds is None:
        return ai_id
    try:
        from empirica.core.loop_scheduler.content_poll import (
            _resolve_canonical_ai_id,
        )

        return _resolve_canonical_ai_id(creds[0], creds[1], ai_id)
    except Exception:
        return ai_id


def _resolve_base_topic(body: dict | None) -> str | None:
    """Per-org orchestration-events topic name from cortex's payload, or None.

    None means: nothing to resolve from (caller should fail loud, NOT fall
    back to the dead bare topic).
    """
    if not body:
        return None
    channels = body.get("channels") or []
    # 1. Explicit orchestration-events channel. Cortex keys channels by
    #    `category`; older shapes used `kind`. Accept either, plus a
    #    name-hint substring so a future `empirica-orchestration-events`
    #    channel is picked up directly.
    for ch in channels:
        topic = ch.get("topic")
        if not topic:
            continue
        kind = ch.get("kind") or ch.get("category")
        if kind == _ORCH_EVENTS_KIND or _ORCH_EVENTS_NAME_HINT in topic:
            return topic
    # 2. Derive the org prefix from sibling channels. Cortex returns every
    #    channel fully org-prefixed (`empirica-system`, `empirica-eco-david`,
    #    `mod-collab`, ...); the orchestration-events topic shares that
    #    prefix even though the endpoint doesn't list it yet.
    topics = [c.get("topic") for c in channels if c.get("topic")]
    for key in ("system_topic", "eco_topic", "collab_topic", "roster_changed_topic"):
        t = body.get(key)
        if isinstance(t, dict) and t.get("topic"):
            topics.append(t["topic"])
    prefix = _derive_org_prefix(topics)
    if prefix:
        return f"{prefix}{_ORCH_EVENTS_NAME_HINT}"
    return None


def retired_topic_names(body: dict | None) -> set[str]:
    """Names of topics cortex has retired (the `retired_channels` field on
    /v1/users/me/notification-channels). A listener must never subscribe to any
    of these — they've lost their ntfy ACL and 403 on every poll. Cortex is the
    authority, so this supersedes per-client hardcoded retired-topic lists.

    Returns an empty set when the field is absent (older cortex) or `body` is
    None (cortex unreachable); callers keep a hardcoded fallback for that
    offline window.
    """
    if not body:
        return set()
    return {c["name"] for c in body.get("retired_channels") or [] if isinstance(c, dict) and c.get("name")}


def _derive_org_prefix(topics: list[str]) -> str | None:
    """Org prefix (incl. trailing '-') from a set of per-org topic names.

    Longest common prefix trimmed to the last '-' boundary. Needs >=2
    distinct topics so the boundary is unambiguous (cortex always returns
    several: eco/collab/system/publish/roster):

        ['empirica-system', 'empirica-eco-david', 'empirica-publish'] -> 'empirica-'
        ['mod-collab', 'mod-system']                                  -> 'mod-'

    Returns None when it can't derive one safely.
    """
    import os

    distinct = sorted({t for t in topics if t})
    if len(distinct) < 2:
        return None
    lcp = os.path.commonprefix(distinct)
    if "-" in lcp:
        return lcp[: lcp.rindex("-") + 1]
    return None


def reset_cache() -> None:
    """Test-only: clear the module-level cache between assertions."""
    global _cache_value, _cache_at
    _cache_value = None
    _cache_at = 0.0
