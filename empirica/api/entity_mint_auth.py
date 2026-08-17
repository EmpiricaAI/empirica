"""Service-token guard for the hosted entity-mint endpoint.

Guards ``POST /api/v1/entities`` (the ``b068bedfd`` contact mint) when the
per-org ``empirica serve`` daemon binds beyond loopback — the hosted-daemon
deployment on Hetzner/EU. Self-hosted loopback daemons stay auth-free, so
same-box consumers (e.g. a CRM MCP server) are unaffected.

Co-spec: ``empirica-mesh-support/docs/entity-mint-service-token-spec.md (not in this repo)``.
Consumers: cortex OAuth P3 ``get_or_create_user``, crm-mcp (NLE CRM round-trip).

Model — shared-secret, deliberately NOT JWT:
  - Tokens are opaque ``emk_<urlsafe>`` bearers minted by cortex (the credential
    authority). Empirica validates by constant-time string-equality against a
    locally-configured *valid-token set* — no introspection round-trip back to
    cortex, so the mint path stays fast and uncoupled.
  - Empirica owns the SET (and the rotation overlap window); a consumer carries
    only its one *current* token. Rotation is zero-downtime: add ``emk_new`` to
    the set, consumers switch their single env, then drop ``emk_old``.
  - Forward hook (unified-auth migration): swap ``verify_mint_bearer`` for JWT
    signature verification against the unified-auth server's public key. One
    function to replace, no parallel shared-secret+JWT path to unwind.

Activation + fail-closed:
  - The guard enforces whenever a valid-token set is configured.
  - ``assert_bind_safe`` refuses startup when bound non-loopback with no token
    configured — the mint is never exposed unauthed.
"""

from __future__ import annotations

import hmac
import os
import secrets

from fastapi import Header, HTTPException

#: Prefix on every entity-mint key. For audit/triage legibility only — it is
#: NOT parsed for authorization (validation is constant-time string-equality).
TOKEN_PREFIX = "emk_"  # noqa: S105 — public key prefix, not a secret

#: Env carrying empirica's valid-token SET (comma-separated). Distinct from the
#: consumer-side singular ``EMPIRICA_ENTITY_MINT_TOKEN`` carry: empirica owns the
#: set + rotation overlap, each consumer presents one current token.
ENV_TOKENS = "EMPIRICA_ENTITY_MINT_TOKENS"

#: Hosts that bind loopback only (no network exposure → auth-free, back-compat).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})


def load_valid_tokens() -> set[str]:
    """Parse the configured valid-token set from ``EMPIRICA_ENTITY_MINT_TOKENS``.

    Comma-separated, whitespace-trimmed, empties dropped. Read fresh on every
    call so a daemon reload (token rotation / revocation) takes effect without
    restarting this module's import-time state.
    """
    raw = os.environ.get(ENV_TOKENS, "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def is_guard_active() -> bool:
    """The guard enforces iff a valid-token set is configured."""
    return bool(load_valid_tokens())


def is_loopback_host(host: str | None) -> bool:
    """True if ``host`` binds loopback only (no network exposure)."""
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def assert_bind_safe(host: str | None) -> None:
    """Fail-closed startup check: refuse a non-loopback bind with no token.

    Loopback binds are always allowed (auth-free, same-box). A non-loopback
    bind REQUIRES a configured token set, or the mint would be exposed unauthed.
    Raises ``RuntimeError`` in the unsafe case; the serve command turns that
    into a clean refusal-to-start.
    """
    if is_loopback_host(host):
        return
    if not is_guard_active():
        raise RuntimeError(
            f"Refusing to start: daemon bound to non-loopback host {host!r} with "
            f"no entity-mint token configured. Set {ENV_TOKENS} (comma-separated "
            "emk_… tokens) before exposing the mint, or bind to 127.0.0.1. The "
            "entity-mint endpoint must never be exposed unauthenticated."
        )


def _extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def _token_in_set(token: str, valid: set[str]) -> bool:
    """Constant-time membership test — compares against every candidate so the
    work doesn't short-circuit on the first mismatch (timing-attack resistant).
    The valid set is intentionally small (≤2 during a rotation overlap).
    """
    matched = False
    for candidate in valid:
        if hmac.compare_digest(token, candidate):
            matched = True
    return matched


async def verify_mint_bearer(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency guarding the entity-mint route.

    THE seam for the unified-auth migration — replace the string-equal body
    with JWT signature verification and nothing else changes.

    Behaviour (per co-spec):
      - guard inactive (no token set configured) → allow (loopback back-compat)
      - active + valid bearer in the set         → allow
      - active + missing/invalid bearer          → 401

    ``403`` is reserved for the future multi-scope case; today a token is either
    in the single ``entity:mint`` set (allow) or not (401).
    """
    valid = load_valid_tokens()
    if not valid:
        return  # guard inactive — same-box loopback, auth-free
    token = _extract_bearer(authorization)
    if not token or not _token_in_set(token, valid):
        raise HTTPException(
            status_code=401,
            detail="entity-mint requires a valid service token (Authorization: Bearer emk_…)",
        )


def generate_mint_token() -> str:
    """Mint a fresh ``emk_<32-byte urlsafe>`` token.

    Cortex is the credential authority in the hosted deployment; this helper
    backs tests and self-hosted operators who stand up their own hosted daemon
    without cortex in the loop.
    """
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


# ─── Cortex-checked write auth (engagement writes) ──────────────────────
#
# David's ruling 2026-08-17 (option b): engagement WRITES over HTTP are gated
# by a live cortex check — engagements are CRM-layer records ("not
# fleet-writable over HTTP" in the ratified model), so authoring them via the
# daemon requires a cortex-issued credential, verified live. Reads stay on
# verify_mint_bearer. FAIL-CLOSED: no bearer → 401; cortex unreachable or not
# configured → 503 (the enforcement IS that engagement HTTP writes need the
# proprietary layer reachable). The emk_ service-token lane stays honored —
# those tokens are cortex-minted, and the hosted deployment depends on them.

#: Seconds a successful cortex validation is cached per token (hash-keyed).
#: Writes are rare; 60s keeps bursts (extension creating a ticket + attaching
#: sources) to one round-trip without meaningfully extending revocation lag.
CORTEX_CHECK_TTL_S = 60.0

#: token-sha256 → monotonic expiry. Process-local; the daemon is a singleton.
_cortex_ok_cache: dict[str, float] = {}


def _cortex_url_from_credentials() -> str | None:
    """The DAEMON's configured cortex URL (never the caller's). None if absent."""
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        url = (get_credentials_loader().get_cortex_config().get("url") or "").strip()
        return url.rstrip("/") or None
    except Exception:
        return None


def _validate_bearer_with_cortex(token: str, cortex_url: str, timeout_s: float = 5.0) -> bool | None:
    """One GET /v1/users/me with the CALLER's bearer.

    True → cortex says the credential is a live seat. False → cortex rejected
    it (401/403). None → the check itself failed (network, 5xx) — the caller
    maps that to 503, never to allow. /v1/users/me is the verified-in-prod
    identity call (auth_commands._verify_token uses the same one); endpoint
    existence is load-bearing here — /v1/tenant/me 404s on prod, so do not
    "lighten" this to an unverified path.
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(f"{cortex_url}/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False
        return None
    except Exception:
        return None


async def verify_engagement_write_auth(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency for engagement WRITE endpoints — cortex-checked.

    Order:
      1. missing bearer                      → 401
      2. bearer in the emk_ service set      → allow (hosted lane, unchanged)
      3. cached cortex-valid                 → allow
      4. live cortex check: valid            → allow (+cache)
                            rejected         → 401
                            check failed / no cortex configured → 503 fail-closed
    """
    import hashlib
    import time as _time

    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="engagement writes require a bearer (cortex OAuth token or emk_ service token)",
        )
    if _token_in_set(token, load_valid_tokens()):
        return
    key = hashlib.sha256(token.encode()).hexdigest()
    if _cortex_ok_cache.get(key, 0.0) > _time.monotonic():
        return
    cortex_url = _cortex_url_from_credentials()
    if not cortex_url:
        raise HTTPException(
            status_code=503,
            detail="engagement writes are cortex-gated and this daemon has no cortex configured "
            "(credentials.yaml cortex.url) — authoring belongs to the workspace/CRM layer",
        )
    verdict = _validate_bearer_with_cortex(token, cortex_url)
    if verdict is True:
        _cortex_ok_cache[key] = _time.monotonic() + CORTEX_CHECK_TTL_S
        return
    if verdict is False:
        raise HTTPException(status_code=401, detail="cortex rejected the bearer for engagement write")
    raise HTTPException(
        status_code=503,
        detail="cortex unreachable — engagement writes fail closed until the check can run",
    )
