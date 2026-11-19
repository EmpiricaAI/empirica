"""Core verifies Cortex's signed seat assertion at sign-in (goal 3796fe5f, the 'signed seat proof' step of 82da0826).

Cortex signs an Ed25519 JWS at the authorization_code exchange when the request carried a nonce; core checks it with a
compiled-in public key, so a server copying the public OAuth routes cannot claim a seat. These tests sign with their own
key and hand it in as `keys`; the compiled-in key is pinned separately.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from empirica.core.auth import seat_assertion as sa


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def key():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, {"test-kid": _b64(public)}


def _sign(private, claims: dict, header: dict | None = None) -> str:
    h = _b64(json.dumps(header or {"alg": "EdDSA", "kid": "test-kid", "typ": "JWT"}).encode())
    p = _b64(json.dumps(claims).encode())
    return f"{h}.{p}.{_b64(private.sign(f'{h}.{p}'.encode()))}"


def _claims(**over):
    now = int(time.time())
    base = {"iss": "https://cortex.getempirica.com", "aud": "empirica-core", "sub": "u1", "org_id": "org-a",
            "nonce": "n" * 32, "iat": now, "exp": now + 600}  # fmt: skip
    return {**base, **over}


def test_a_valid_assertion_verifies_and_returns_its_claims(key):
    private, keys = key
    claims = sa.verify(_sign(private, _claims()), nonce="n" * 32, keys=keys)
    assert claims["org_id"] == "org-a" and claims["sub"] == "u1"


@pytest.mark.parametrize(
    ("claims", "header", "why"),
    [
        (_claims(nonce="other" * 8), None, "nonce"),
        (_claims(aud="someone-else"), None, "aud"),
        (_claims(exp=int(time.time()) - 3600), None, "expired"),
        (_claims(org_id=None), None, "org_id"),
        (_claims(), {"alg": "HS256", "kid": "test-kid"}, "alg"),
        (_claims(), {"alg": "EdDSA", "kid": "unknown-kid"}, "kid"),
    ],
)
def test_an_assertion_that_fails_any_check_is_refused_with_the_reason(key, claims, header, why):
    private, keys = key
    with pytest.raises(sa.SeatAssertionError, match=why):
        sa.verify(_sign(private, claims, header), nonce="n" * 32, keys=keys)


def test_a_tampered_payload_fails_the_signature(key):
    private, keys = key
    h, _, s = _sign(private, _claims()).split(".")
    forged = _b64(json.dumps(_claims(org_id="org-evil")).encode())
    with pytest.raises(sa.SeatAssertionError, match="signature"):
        sa.verify(f"{h}.{forged}.{s}", nonce="n" * 32, keys=keys)


def test_the_compiled_in_key_is_cortex_production_key():
    """Cortex 2026-11-18 (prop_mpd4ovuq): kid seat-2026-11, raw 32 bytes base64url."""
    assert sa.PUBLIC_KEYS["seat-2026-11"] == "fehFT_YQCCqMDIkaWZZgt0CPn1inY5kyLZkoHh5WfGE"
    assert len(base64.urlsafe_b64decode(sa.PUBLIC_KEYS["seat-2026-11"] + "=")) == 32


def test_a_login_nonce_is_valid_for_cortex():
    n = sa.new_nonce()
    assert 16 <= len(n) <= 128 and all(c.isalnum() or c in "-_" for c in n)


# ─── login wiring ───────────────────────────────────────────────────────────

_DISCO = {
    "authorization_endpoint": "https://cortex.example/v1/oauth/authorize",
    "token_endpoint": "https://cortex.example/v1/oauth/token",
    "registration_endpoint": "https://cortex.example/v1/oauth/register",
}


@pytest.fixture
def loader(tmp_path, monkeypatch):
    from empirica.config.credentials_loader import CredentialsLoader

    creds = tmp_path / "credentials.yaml"
    creds.write_text("version: '1.0'\ncortex:\n  url: https://cortex.example\n")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(creds))
    for v in ("CORTEX_API_KEY", "CORTEX_URL", "CORTEX_REMOTE_URL"):
        monkeypatch.delenv(v, raising=False)
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    yield CredentialsLoader()
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


def _login(loader, monkeypatch, keys, token_response, me_org="org-a"):
    import urllib.parse

    from empirica.core.auth import cortex_oauth

    monkeypatch.setattr(sa, "PUBLIC_KEYS", keys)
    sent: dict = {}

    def http(url, *, data=None, headers=None, timeout=15.0, form=False):
        if "/.well-known" in url:
            return _DISCO
        if "/register" in url:
            return {"client_id": "cli"}
        if "/token" in url:
            sent.update(data or {})
            body: dict = token_response(sent) if callable(token_response) else dict(token_response)
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, **body}
        if "/users/me" in url:
            return {"org_id": me_org}
        raise AssertionError(url)

    def read_code(authorize_url):
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(authorize_url).query))["state"]
        return f"code123#{state}"

    result = cortex_oauth.login(loader=loader, http=http, remote=True, read_code=read_code, timeout_s=5)
    return result, sent


def test_login_sends_a_nonce_and_records_a_verified_seat(loader, monkeypatch, key):
    private, keys = key
    result, sent = _login(
        loader, monkeypatch, keys, lambda form: {"seat_assertion": _sign(private, _claims(nonce=form["nonce"]))}
    )
    assert len(sent["nonce"]) >= 16
    assert result["seat"] == {"present": True, "verified": True, "kid": "test-kid", "reason": None}


def test_a_seat_assertion_that_fails_refuses_the_login_and_stores_nothing(loader, monkeypatch, key):
    private, keys = key
    with pytest.raises(RuntimeError, match="seat assertion"):
        _login(loader, monkeypatch, keys, {"seat_assertion": _sign(private, _claims(nonce="not-this-login" * 2))})
    loader._credentials_cache = None
    assert not loader.get_cortex_oauth()


def test_a_seat_for_another_org_than_the_account_refuses_the_login(loader, monkeypatch, key):
    private, keys = key
    with pytest.raises(RuntimeError, match="org"):
        _login(
            loader,
            monkeypatch,
            keys,
            lambda f: {"seat_assertion": _sign(private, _claims(nonce=f["nonce"], org_id="org-b"))},
        )


def test_no_assertion_keeps_todays_behaviour_and_says_unverified(loader, monkeypatch, key):
    _, keys = key
    result, _ = _login(loader, monkeypatch, keys, {})
    assert result["ok"] and result["seat"]["present"] is False and result["seat"]["verified"] is False
