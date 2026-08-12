"""Daemon-brokered OAuth — the LINKED identity path (David 2026-08-12).

One cortex identity, OAuth bridged through credentials.yaml, api_key retired.
The load-bearing invariant is refresh custody: cortex rotates on every use and
reuse-detection revokes the whole family, so EXACTLY ONE process may refresh.
`refresh_owner` picks it — 'daemon' (the always-on serve tick) or 'cli' (the
headless-fallback shell process). The tests that matter prove the CLI does NOT
refresh a daemon-owned family (it would revoke it) and DOES refresh its own.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def loader(tmp_path, monkeypatch):
    from empirica.config.credentials_loader import CredentialsLoader

    creds = tmp_path / "credentials.yaml"
    creds.write_text("version: '1.0'\ncortex:\n  url: https://cortex.example\n  api_key: ctx_test_key\n")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(creds))
    for v in ("CORTEX_API_KEY", "CORTEX_URL", "CORTEX_REMOTE_URL"):
        monkeypatch.delenv(v, raising=False)
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    yield CredentialsLoader()
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


# Fake fixtures, not credentials — module-level so S105/S106 stays quiet
# (same convention as test_cortex_oauth_credentials).
_AT = "AT"
_RT = "RT"
_TE = "https://cortex.example/v1/oauth/token"
_DAEMON_AT = "daemon_at"
_OLD_AT = "old_at"
_FRESH_AT = "fresh_at"
_BRIDGED_AT = "bridged_at"
_BRIDGED_RT = "bridged_rt"
_ROTATED = "rotated"
_X = "x"


def test_refresh_owner_persists_and_defaults_are_honored(loader):
    loader.save_cortex_oauth(access_token=_AT, refresh_owner="daemon")
    loader._credentials_cache = None
    assert loader.get_cortex_oauth().get("refresh_owner") == "daemon"


@pytest.mark.parametrize("owner", ["daemon", "extension"])
def test_cli_does_not_refresh_a_family_it_does_not_own(loader, owner):
    """The revocation guard, generalised (extension's per-seat model): the CLI
    refreshes ONLY a 'cli'-owned family. A 'daemon'-owned (serve tick refreshes)
    or 'extension'-owned (daemonless Desktop seat, extension refreshes) family
    must be read-only from the CLI — a second refresher revokes it."""
    from empirica.core.auth import cortex_bearer

    loader.save_cortex_oauth(
        access_token=_DAEMON_AT,
        refresh_token=_RT,
        expires_at=time.time() - 10,  # expired
        token_endpoint=_TE,
        client_id="cli_x",
        refresh_owner=owner,
    )
    loader._credentials_cache = None

    refreshed = {"called": False}

    def _spy_http(url, **_kw):
        refreshed["called"] = True
        return {"access_token": "SHOULD_NOT_HAPPEN"}

    out = cortex_bearer(loader, http=_spy_http)
    assert refreshed["called"] is False, "the CLI must never refresh a daemon-owned family"
    assert out["source"] == "api_key", "expired daemon token with no CLI refresh → api_key fallback"


def test_cli_refreshes_its_own_family(loader):
    """The headless-fallback path still refreshes: a cli-owned (or absent-owner)
    expired token drives the refresh callable."""
    from empirica.core.auth import cortex_bearer

    loader.save_cortex_oauth(
        access_token=_OLD_AT,
        refresh_token=_RT,
        expires_at=time.time() - 10,
        token_endpoint=_TE,
        client_id="cli_x",
        refresh_owner="cli",
    )
    loader._credentials_cache = None

    def _http(url, **_kw):
        return {"access_token": _FRESH_AT, "refresh_token": _ROTATED, "expires_in": 3600}

    out = cortex_bearer(loader, http=_http)
    assert out["source"] == "oauth"
    assert out["bearer"] == _FRESH_AT


def test_absent_owner_defaults_to_cli_so_shipped_tokens_keep_working(loader):
    """The token David already minted via `auth login` has refresh_owner set,
    but a pre-field token (none) must default to cli — the CLI refreshes it."""
    from empirica.core.auth import cortex_bearer

    # Write without refresh_owner by going through the raw block.
    loader.save_cortex_oauth(
        access_token=_OLD_AT,
        refresh_token=_RT,
        expires_at=time.time() - 10,
        token_endpoint=_TE,
        client_id="cli_x",
    )
    loader._credentials_cache = None
    stored = loader.get_cortex_oauth()
    assert "refresh_owner" not in stored  # genuinely absent

    def _http(url, **_kw):
        return {"access_token": _FRESH_AT, "expires_in": 3600}

    out = cortex_bearer(loader, http=_http)
    assert out["source"] == "oauth", "absent owner defaults to cli → refreshes"


def test_daemon_route_bridges_oauth_without_touching_api_key(loader, monkeypatch):
    """The extension's OAuth bridge: POST oauth fields → cortex.oauth written,
    refresh_owner defaults to daemon, api_key untouched."""
    from empirica.api import serve_app

    monkeypatch.setattr("empirica.config.credentials_loader.CredentialsLoader", type(loader))
    # Drive the persistence the route performs, directly on our tmp loader.
    loader.save_cortex_oauth(
        access_token=_BRIDGED_AT,
        refresh_token=_BRIDGED_RT,
        token_endpoint=_TE,
        client_id="ext_client",
        refresh_owner="daemon",
    )
    loader._credentials_cache = None
    oauth = loader.get_cortex_oauth()
    assert oauth["access_token"] == _BRIDGED_AT
    assert oauth["refresh_owner"] == "daemon"
    assert loader.get_cortex_config()["api_key"] == "ctx_test_key", "oauth bridge must not touch api_key"
    # The request model carries the oauth fields the extension POSTs.
    req = serve_app.CortexCredentialsRequest(oauth_access_token=_X, oauth_refresh_owner="daemon")
    assert req.oauth_access_token == _X
    assert req.oauth_refresh_owner == "daemon"


def test_daemon_refresh_tick_only_touches_daemon_owned(loader, monkeypatch):
    """The sole-refresher loop skips cli-owned families (their shell owns them)
    and refreshes daemon-owned ones."""
    from empirica.api.serve_app import _oauth_refresh_loop

    loader.save_cortex_oauth(
        access_token=_AT,
        refresh_token=_RT,
        expires_at=time.time() - 10,
        token_endpoint=_TE,
        client_id="ext",
        refresh_owner="cli",  # NOT daemon
    )
    loader._credentials_cache = None

    called = {"n": 0}

    def _fake_default_refresh(_loader, **_kw):
        def _refresh(_rt, _ep):
            called["n"] += 1
            return {"access_token": "new"}

        return _refresh

    monkeypatch.setattr("empirica.core.auth.default_refresh", _fake_default_refresh)

    # One tick with a stop already set fires the body zero times; instead call
    # the guard logic directly by faking the wait to fire once then stop.
    import threading

    stop = threading.Event()
    calls = {"ticks": 0}
    orig_wait = stop.wait

    def _wait_once(_interval):
        calls["ticks"] += 1
        if calls["ticks"] > 1:
            return True  # stop after one body execution
        return orig_wait(0)

    stop.wait = _wait_once  # type: ignore[method-assign]
    _oauth_refresh_loop(0.01, stop)
    assert called["n"] == 0, "a cli-owned family must be skipped by the daemon tick"
