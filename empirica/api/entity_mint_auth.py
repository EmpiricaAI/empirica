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
