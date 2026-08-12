"""`empirica auth login` — the CLI-owned OAuth path (goal 24c55db7).

Everything here runs against a tmp_path credentials file and a mocked HTTP
layer — no network, no browser, no ambient ~/.empirica (tests must not
measure the box). The rotation-persistence tests are the load-bearing ones:
cortex rotates refresh tokens on every use and reuse detection revokes the
family, so an unpersisted rotation is not a bug, it is a lockout.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
import urllib.request

import pytest

from empirica.core.auth import cortex_oauth
from empirica.core.auth.cortex_oauth import (
    LOOPBACK_PORTS,
    _bind_first_free_port,
    _prepare_callback_server,
    _wait_for_callback,
    build_pkce,
    cortex_bearer,
    default_refresh,
    login,
)


@pytest.fixture
def loader(tmp_path, monkeypatch):
    """A real CredentialsLoader pinned to a tmp file carrying an api_key.

    CredentialsLoader is a SINGLETON with a class-level cache — without the
    resets below, one test's oauth block leaks into the next test's loader
    (found by pytest-randomly, an order-dependent failure)."""
    from empirica.config.credentials_loader import CredentialsLoader

    creds = tmp_path / "credentials.yaml"
    creds.write_text("version: '1.0'\ncortex:\n  url: https://cortex.example\n  api_key: ctx_test_key\n")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(creds))
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    yield CredentialsLoader()
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


# Fake fixtures, not credentials — named so S105/S106 stays quiet without
# scattering noqa (same convention as test_cortex_oauth_credentials).
_AUTH_NONE = "none"
_OLD_AT, _OLD_RT = "OLD_AT", "OLD_RT"
_NEW_AT, _NEW_RT = "NEW_AT", "NEW_RT"
_LIVE_AT, _STALE_AT, _RT = "LIVE_AT", "STALE_AT", "RT"
_AT1, _RT1, _AT2 = "AT1", "RT1", "AT2"

DISCO = {
    "authorization_endpoint": "https://cortex.example/v1/oauth/authorize",
    "token_endpoint": "https://cortex.example/v1/oauth/token",
    "registration_endpoint": "https://cortex.example/v1/oauth/register",
}


def _mock_http(responses: dict, calls: list):
    """http(url, data=..., form=...) double keyed by url substring."""

    def _http(url, *, data=None, headers=None, timeout=15.0, form=False):
        calls.append({"url": url, "data": data, "form": form})
        for key, resp in responses.items():
            if key in url:
                return resp(data) if callable(resp) else resp
        raise AssertionError(f"unexpected url {url}")

    return _http


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = build_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert len(verifier) >= 43  # RFC 7636 minimum


def test_registration_is_public_client_with_full_port_block(loader):
    calls = []
    http = _mock_http({"/register": {"client_id": "cli_abc"}}, calls)
    out = cortex_oauth.register_client("https://cortex.example/v1/oauth/register", http=http)
    assert out["client_id"] == "cli_abc"
    payload = calls[0]["data"]
    assert payload["token_endpoint_auth_method"] == _AUTH_NONE, "a CLI cannot keep a secret — PKCE is the binding"
    # Cortex matches redirect_uri exact-string (no RFC 8252 §7.3): the whole
    # block must be registered or login fails on any non-first port.
    assert payload["redirect_uris"] == [f"http://127.0.0.1:{p}/callback" for p in LOOPBACK_PORTS]
    assert "client_credentials" not in payload["grant_types"]


def test_refresh_rotation_is_persisted(loader):
    """Cortex rotates per use; the NEW refresh token must hit the file before
    the callable returns, or the next refresh kills the family."""
    loader.save_cortex_oauth(
        access_token=_OLD_AT,
        refresh_token=_OLD_RT,
        expires_at=time.time() - 10,
        token_endpoint=DISCO["token_endpoint"],
        client_id="cli_abc",
    )
    calls = []
    http = _mock_http({"/token": {"access_token": _NEW_AT, "refresh_token": _NEW_RT, "expires_in": 3600}}, calls)
    out = default_refresh(loader, http=http)(_OLD_RT, None)
    assert out["access_token"] == _NEW_AT
    body = calls[0]["data"]
    assert body["grant_type"] == "refresh_token"
    assert body["client_id"] == "cli_abc", "public-client refresh must carry the CLI's own client_id"
    assert "client_secret" not in body
    # The rotated set is on disk — re-read through a fresh cache
    loader._credentials_cache = None
    stored = loader.get_cortex_oauth()
    assert stored["refresh_token"] == _NEW_RT
    assert stored["access_token"] == _NEW_AT


def test_bearer_prefers_valid_token(loader):
    loader.save_cortex_oauth(access_token=_LIVE_AT, expires_at=time.time() + 3600)
    loader._credentials_cache = None
    out = cortex_bearer(loader)
    assert out == {"url": "https://cortex.example", "bearer": _LIVE_AT, "source": "oauth"}


def test_bearer_falls_back_to_api_key_when_no_token(loader):
    out = cortex_bearer(loader)
    assert out["source"] == "api_key"
    assert out["bearer"] == "ctx_test_key"


def test_bearer_falls_back_when_refresh_fails_and_never_sends_stale(loader):
    loader.save_cortex_oauth(
        access_token=_STALE_AT,
        refresh_token=_RT,
        expires_at=time.time() - 10,
        token_endpoint=DISCO["token_endpoint"],
        client_id="cli_abc",
    )
    loader._credentials_cache = None

    def _dead_http(url, **_kw):
        raise OSError("token endpoint down")

    out = cortex_bearer(loader, http=_dead_http)
    assert out["source"] == "api_key"
    assert out["bearer"] == "ctx_test_key"
    assert out["bearer"] != _STALE_AT


def test_loopback_rejects_state_mismatch():
    sock, port = _bind_first_free_port()
    server = _prepare_callback_server(sock, port)

    def _hit():
        time.sleep(0.05)
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=x&state=WRONG", timeout=5)

    t = threading.Thread(target=_hit, daemon=True)
    t.start()
    with pytest.raises(RuntimeError, match="state mismatch"):
        _wait_for_callback(server, "RIGHT", timeout_s=5)


def test_login_end_to_end_persists_the_full_token_set(loader):
    calls = []
    http = _mock_http(
        {
            "/.well-known": DISCO,
            "/register": {"client_id": "cli_new"},
            "/token": {"access_token": _AT1, "refresh_token": _RT1, "expires_in": 3600},
        },
        calls,
    )

    def _fake_browser(url):
        # Simulate the user completing authorize: cortex redirects to the
        # loopback with code + the state the CLI put in the URL.
        import urllib.parse

        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        urllib.request.urlopen(f"{q['redirect_uri']}?code=authcode&state={q['state']}", timeout=5)

    result = login(loader=loader, open_browser=_fake_browser, timeout_s=10, http=http)
    assert result["ok"] and result["has_refresh_token"]

    exchange = next(c for c in calls if c["url"].endswith("/token"))
    assert exchange["data"]["grant_type"] == "authorization_code"
    assert exchange["data"]["code"] == "authcode"
    assert "code_verifier" in exchange["data"]

    loader._credentials_cache = None
    stored = loader.get_cortex_oauth()
    assert stored["access_token"] == _AT1
    assert stored["refresh_token"] == _RT1
    assert stored["client_id"] == "cli_new"
    assert stored["token_endpoint"] == DISCO["token_endpoint"]
    # And the api_key was NOT touched — keys stay valid through migration.
    assert loader.get_cortex_config()["api_key"] == "ctx_test_key"


def test_login_reuses_stored_client(loader):
    """Re-login must not re-register — one DCR row per seat, not per login."""
    loader.save_cortex_oauth(client_id="cli_existing", token_endpoint=DISCO["token_endpoint"])
    loader._credentials_cache = None
    calls = []
    http = _mock_http(
        {
            "/.well-known": DISCO,
            "/token": {"access_token": _AT2, "expires_in": 3600},
        },
        calls,
    )

    def _fake_browser(url):
        import urllib.parse

        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        urllib.request.urlopen(f"{q['redirect_uri']}?code=c2&state={q['state']}", timeout=5)

    result = login(loader=loader, open_browser=_fake_browser, timeout_s=10, http=http)
    assert result["client_id"] == "cli_existing"
    assert not any("/register" in c["url"] for c in calls)


def test_mesh_transports_resolve_through_the_bearer(monkeypatch):
    """The wired sites (mailbox, heartbeats, listener catch-up) must reach
    cortex_bearer — an oauth seat's token, not the raw api_key. Asserted at
    the helper seam each site funnels through. The patch targets the PACKAGE
    re-export because the sites import `from empirica.core.auth import
    cortex_bearer` at call time."""
    from empirica.cli.command_handlers import mailbox_commands

    monkeypatch.setattr(
        "empirica.core.auth.cortex_bearer",
        lambda *a, **k: {"url": "https://cortex.example", "bearer": "oauth_tok", "source": "oauth"},
    )
    url, key = mailbox_commands._default_resolve_cortex_creds()
    assert (url, key) == ("https://cortex.example", "oauth_tok")
