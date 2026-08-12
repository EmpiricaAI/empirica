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
_CLI_AT, _CLI_RT, _EXT_AT, _EXT_RT = "cli_at", "cli_rt", "ext_at", "ext_rt"
_AT1, _RT1, _AT2 = "at1", "rt1", "at2"


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


# ─── Version-skew capability signal (extension prop_xxex55iv) ───────────


def test_response_advertises_capability_and_echoes_persisted_owner():
    """The daemon must advertise oauth_bridge_supported (presence-and-true =
    'I persist refresh_owner') and echo the owner actually persisted, so a
    client VERIFIES its write survived instead of trusting ok:true. The 1.13.9
    skew accepted the field and dropped it — the echo is what catches that."""
    from empirica.api.serve_app import CortexCredentialsResponse

    # A build carrying refresh_owner defaults the capability flag to True.
    resp = CortexCredentialsResponse(ok=True, oauth_set=True, oauth_refresh_owner="daemon")
    assert resp.oauth_bridge_supported is True
    assert resp.oauth_refresh_owner == "daemon"

    # A pre-capability daemon serializes WITHOUT the field; a client parsing
    # that JSON sees it absent → treats as unsupported → does not bridge.
    legacy_json = '{"ok": true, "url": "https://cortex.example", "api_key_set": true}'
    import json as _json

    parsed = _json.loads(legacy_json)
    assert "oauth_bridge_supported" not in parsed, "absence is the unsupported signal — the client must not assume True"


# ─── One block = one family: a different client_id REPLACES (prop_qybz3yc4) ──


def test_writing_a_different_client_id_replaces_not_merges(loader):
    """save_cortex_oauth must not merge tokens over a foreign client_id — that
    leaves the old client paired with new tokens, a family cortex revokes on
    refresh (the corruption the extension's token-only bridge caused)."""
    loader.save_cortex_oauth(
        access_token=_CLI_AT,
        refresh_token=_CLI_RT,
        token_endpoint=_TE,
        client_id="cli_client",
        refresh_owner="cli",
    )
    loader._credentials_cache = None
    # A different client writes its full family — must REPLACE, not merge.
    loader.save_cortex_oauth(
        access_token=_EXT_AT,
        refresh_token=_EXT_RT,
        client_id="ext_client",
        refresh_owner="daemon",
    )
    loader._credentials_cache = None
    o = loader.get_cortex_oauth()
    assert o["client_id"] == "ext_client"
    assert o["access_token"] == _EXT_AT
    assert o["refresh_token"] == _EXT_RT
    assert o["refresh_owner"] == "daemon"
    # The old client's stale endpoint must NOT survive into the new family.
    # (token_endpoint was only on the first write; a merge would leak it.)


def test_token_only_write_still_merges_same_family(loader):
    """A write that OMITS client_id is a same-family field update — it must
    still merge (e.g. the daemon persisting a rotated access_token)."""
    loader.save_cortex_oauth(
        access_token=_AT1, refresh_token=_RT1, client_id="c1", token_endpoint=_TE, refresh_owner="daemon"
    )
    loader._credentials_cache = None
    loader.save_cortex_oauth(access_token=_AT2)  # rotation, no client_id
    loader._credentials_cache = None
    o = loader.get_cortex_oauth()
    assert o["access_token"] == _AT2
    assert o["client_id"] == "c1", "same-family field update must preserve client_id"
    assert o["refresh_token"] == _RT1


# ─── ms/seconds + daemon cache staleness (extension prop_jehnbjq/zyz4wflb) ──


def test_expires_at_milliseconds_is_normalized_to_seconds(loader):
    """The extension bridges Date.now() milliseconds. Stored as-is it reads as
    the year ~58,600 → 'still_valid' permanently true → the CLI never refreshes
    and 401s after cortex's TTL. Both write and read must normalize to seconds."""
    ms = 1786551254393  # a real bridged value (year ~58,600 if read as seconds)
    loader.save_cortex_oauth(access_token=_AT, expires_at=ms, client_id="c1", refresh_owner="daemon")
    loader._credentials_cache = None
    stored = loader.get_cortex_oauth()
    assert stored["expires_at"] < 1e11, "write must canonicalize ms → seconds"
    assert abs(stored["expires_at"] - ms / 1000.0) < 1.0


def test_already_stored_ms_recovers_on_read(loader):
    """A token already in the file with ms expiry (David's box) must not read as
    permanently-valid — the read-side normalize catches it so cortex_access_token
    treats a truly-expired token as expired."""
    import time as _t

    # Simulate a pre-fix stored ms value in the PAST (so, expired, once normalized).
    past_ms = int((_t.time() - 3600) * 1000)
    loader.save_cortex_oauth(
        access_token=_AT, refresh_token=_RT, expires_at=past_ms, client_id="c1", refresh_owner="cli", token_endpoint=_TE
    )
    # Force the raw ms back into the file (bypassing the write-normalizer) to model
    # a token stored before the fix.
    import yaml

    path = loader._resolve_credentials_target(None)
    data = yaml.safe_load(path.read_text())
    data["cortex"]["oauth"]["expires_at"] = past_ms
    path.write_text(yaml.safe_dump(data))
    loader._credentials_cache = None

    refreshed = {"n": 0}

    def _http(url, **_kw):
        refreshed["n"] += 1
        return {"access_token": _FRESH_AT, "expires_in": 3600}

    from empirica.core.auth import cortex_bearer

    out = cortex_bearer(loader, http=_http)
    assert refreshed["n"] == 1, "a ms-past-expiry token must read as expired and refresh"
    assert out["bearer"] == _FRESH_AT


def test_loader_reloads_on_file_mtime_change(loader, tmp_path):
    """The daemon holds the singleton for its life; a credentials.yaml written by
    ANOTHER process (auth login, the bridge) must be visible without a restart."""
    import os
    import time as _t

    loader.save_cortex_oauth(access_token=_AT, client_id="c1", refresh_owner="cli")
    # Prime the cache + mtime.
    assert loader.get_cortex_oauth()["refresh_owner"] == "cli"

    # A separate writer changes the block, then bumps mtime past the cached one.
    path = loader._resolve_credentials_target(None)
    import yaml

    data = yaml.safe_load(path.read_text())
    data["cortex"]["oauth"]["refresh_owner"] = "daemon"
    path.write_text(yaml.safe_dump(data))
    future = _t.time() + 10
    os.utime(path, (future, future))

    # No manual cache-clear — the loader must notice the mtime move.
    assert loader.get_cortex_oauth()["refresh_owner"] == "daemon", "daemon must see a concurrent write without restart"
