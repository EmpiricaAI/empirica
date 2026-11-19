"""Cortex's signed seat assertion, verified by core (goal 3796fe5f; David's ruling on 82da0826, 2026-11-16).

At the authorization_code exchange, when the request carries a ``nonce`` form field, Cortex returns ``seat_assertion``: a
compact JWS (``{alg: EdDSA, kid, typ: JWT}``) whose claims are ``iss``, ``aud: empirica-core``, ``sub`` (the user id),
``org_id`` (the org the access token acts in), the ``nonce`` echoed, ``iat`` and ``exp`` (iat + 600 s). Core checks it with
a compiled-in Ed25519 public key, so a server that copies Empirica's public OAuth routes cannot claim a seat: several teams
on one machine need a proof only Empirica's key can sign.

The private half never leaves Cortex's box; rotation adds a new ``seat-YYYY-MM`` kid beside the old one (contract from
cortex, prop_mpd4ovuqd5gjlmwydq5cyc6mia). Not a lock, since core is open source: a bar a configuration change cannot pass.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from typing import Any

#: kid -> Ed25519 public key, raw 32 bytes, base64url without padding. Add a kid here when Cortex rotates.
PUBLIC_KEYS: dict[str, str] = {
    "seat-2026-11": "fehFT_YQCCqMDIkaWZZgt0CPn1inY5kyLZkoHh5WfGE",
}

AUDIENCE = "empirica-core"
_SKEW_S = 60


class SeatAssertionError(ValueError):
    """The assertion does not prove a seat; the message says which check failed."""


def new_nonce() -> str:
    """A login nonce Cortex accepts: 43 characters of [A-Za-z0-9_-] (it takes 16 to 128)."""
    return secrets.token_urlsafe(32)


def _unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify(token: str, *, nonce: str, keys: dict[str, str] | None = None, now: float | None = None) -> dict[str, Any]:
    """The assertion's claims if it proves a seat for this login, else SeatAssertionError naming the failed check."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = PUBLIC_KEYS if keys is None else keys
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise SeatAssertionError("malformed: not a compact JWS")
    try:
        header = json.loads(_unb64(parts[0]))
        claims = json.loads(_unb64(parts[1]))
        signature = _unb64(parts[2])
    except (binascii.Error, ValueError) as e:
        raise SeatAssertionError(f"malformed: {e}") from e
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise SeatAssertionError("malformed: header and claims must be objects")
    if header.get("alg") != "EdDSA":
        raise SeatAssertionError(f"alg {header.get('alg')!r} is not EdDSA")  # a symmetric alg must never verify
    kid = header.get("kid")
    if kid not in keys:
        raise SeatAssertionError(f"unknown kid {kid!r}")
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(keys[kid])).verify(signature, f"{parts[0]}.{parts[1]}".encode())
    except InvalidSignature as e:
        raise SeatAssertionError("signature does not verify") from e
    if claims.get("aud") != AUDIENCE:
        raise SeatAssertionError(f"aud {claims.get('aud')!r} is not {AUDIENCE!r}")
    if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise SeatAssertionError("nonce does not match this login")
    t = time.time() if now is None else now
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp + _SKEW_S < t:
        raise SeatAssertionError("expired")
    for field in ("sub", "org_id"):
        if not claims.get(field):
            raise SeatAssertionError(f"no {field}")
    return {**claims, "_kid": kid}  # the key that verified it, for the caller to report
