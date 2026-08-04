"""Core's leg of the api_key retirement: brokered-seat token storage + refresh.

Core owns ONE of the two auth paths — the daemon-brokered one. `credentials.yaml`
holds the refresh token and the daemon renews silently, which is what makes a 24h
access-token TTL invisible instead of a daily re-auth.

The other path (direct in-client OAuth) is NOT served from here. Cowork seats
authenticate natively through Claude's own client — confirmed independently from
the onboarding runbook §250 and from prod, where 239 of 241 refresh tokens were
issued to client "Claude" — so every seat reaching this store has a local daemon.
A single store serving both paths is how the bare-key risk returns under another
name.

Hard constraint from the SER: **api_keys stay valid throughout.** Nothing here may
make a seat conclude its key is dead because a token now exists.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
import yaml

from empirica.config.credentials_loader import CredentialsLoader

# Fake fixtures, not credentials — named so the noqa sits once rather than on
# every call site.
_AT = "AT1"
_AT2 = "AT2"
_RT = "RT1"
_RT2 = "RT2"
_OLD = "OLD"
_NEW = "NEW"
_LIVE = "LIVE"
_NEARLY = "NEARLY"
_ENDPOINT = "https://c.test/token"


@pytest.fixture
def creds(monkeypatch, tmp_path):
    # `tmp_path`, not `mkdtemp()` — pytest removes it. The repo guards this after
    # leaked fixture dirs filled a shared /tmp and surfaced as unrelated suite
    # failures somebody spent an hour debugging.
    p = tmp_path / "credentials.yaml"
    p.write_text(
        'version: "1.0"\n'
        "cortex:\n  url: https://c.test\n  api_key: SECRET-KEY\n"
        "providers:\n  anthropic:\n    api_key: OTHER\n"
    )
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(p))
    loader = CredentialsLoader()
    loader._credentials_cache = None
    return loader, p


def _read(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


# ─── storage must not disturb what is already there ───────────────────────


def test_saving_a_token_preserves_the_api_key(creds):
    """The SER's hard constraint. Keys stay valid for the whole migration —
    dual-accept by shape is the safety net, and revocation is a separate
    per-identity act gated on observed Bearer traffic."""
    loader, p = creds
    loader.save_cortex_oauth(access_token=_AT, refresh_token=_RT, expires_at=time.time() + 3600)

    assert _read(p)["cortex"]["api_key"] == "SECRET-KEY"


def test_saving_a_token_preserves_other_providers(creds):
    loader, p = creds
    loader.save_cortex_oauth(access_token=_AT)

    assert _read(p)["providers"]["anthropic"]["api_key"] == "OTHER"


def test_the_file_stays_0600(creds):
    """It holds a REFRESH token now, which mints access tokens indefinitely.

    The mode comes from `mkstemp` and survives because `os.replace` preserves the
    TEMP file's mode, not the destination's. A refactor away from mkstemp would
    widen it silently, so this is pinned rather than assumed.
    """
    loader, p = creds
    loader.save_cortex_oauth(access_token=_AT, refresh_token=_RT)

    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_an_unreadable_credentials_file_is_not_overwritten(creds):
    """Refusing beats clobbering: silently rewriting a file we could not parse
    discards working keys for every provider in order to save one token."""
    loader, p = creds
    p.write_text("{{{ not yaml")

    with pytest.raises(RuntimeError, match="unreadable"):
        loader.save_cortex_oauth(access_token=_AT)


def test_partial_updates_merge_rather_than_replace(creds):
    loader, p = creds
    loader.save_cortex_oauth(access_token=_AT, refresh_token=_RT, token_endpoint=_ENDPOINT)
    loader.save_cortex_oauth(access_token=_AT2)

    oauth = _read(p)["cortex"]["oauth"]
    assert oauth["access_token"] == _AT2
    assert oauth["refresh_token"] == _RT, "a partial update must not drop the refresh token"
    assert oauth["token_endpoint"] == _ENDPOINT


def test_saving_nothing_is_an_error_not_a_silent_noop(creds):
    loader, _ = creds
    with pytest.raises(ValueError):
        loader.save_cortex_oauth()


# ─── refresh ──────────────────────────────────────────────────────────────


def test_a_live_token_is_returned_without_refreshing(creds):
    loader, _ = creds
    loader.save_cortex_oauth(access_token=_LIVE, refresh_token=_RT, expires_at=time.time() + 3600)

    calls = []
    assert loader.cortex_access_token(refresh=lambda *a: calls.append(a)) == _LIVE
    assert not calls, "refreshed a token that was still valid"


def test_an_expired_token_is_refreshed_and_persisted(creds):
    loader, p = creds
    loader.save_cortex_oauth(access_token=_OLD, refresh_token=_RT, expires_at=time.time() - 10)

    def refresh(refresh_token, endpoint):
        assert refresh_token == _RT
        return {"access_token": _NEW, "expires_at": time.time() + 3600}

    assert loader.cortex_access_token(refresh=refresh) == _NEW
    assert _read(p)["cortex"]["oauth"]["access_token"] == _NEW, "the renewed token must be persisted"


def test_a_rotated_refresh_token_is_stored(creds):
    """Rotating auth servers issue a new refresh token on every use. Dropping it
    works exactly once, then locks the seat out at the next renewal — and the
    failure appears a full token-lifetime after the bug."""
    loader, p = creds
    loader.save_cortex_oauth(access_token=_OLD, refresh_token=_RT, expires_at=time.time() - 10)

    loader.cortex_access_token(
        refresh=lambda *_: {"access_token": _NEW, "refresh_token": "RT2", "expires_at": time.time() + 3600}
    )

    assert _read(p)["cortex"]["oauth"]["refresh_token"] == _RT2


def test_a_token_about_to_expire_is_refreshed_early(creds):
    """A token valid when checked and expired when it ARRIVES is the classic race.
    Zero leeway makes it a certainty under any latency."""
    loader, _ = creds
    loader.save_cortex_oauth(access_token=_NEARLY, refresh_token=_RT, expires_at=time.time() + 30)

    got = loader.cortex_access_token(
        refresh=lambda *_: {"access_token": _NEW, "expires_at": time.time() + 3600}, leeway_s=120
    )
    assert got == _NEW


def test_a_failed_refresh_returns_none_never_the_stale_token(creds):
    """A caller cannot distinguish a stale token from a live one, so it would send
    it. None is an error they can act on; a dead token is a confusing 401."""
    loader, _ = creds
    loader.save_cortex_oauth(access_token=_OLD, refresh_token=_RT, expires_at=time.time() - 10)

    def boom(*_):
        raise ConnectionError("auth server down")

    assert loader.cortex_access_token(refresh=boom) is None


def test_a_refresh_returning_no_token_is_not_treated_as_success(creds):
    loader, _ = creds
    loader.save_cortex_oauth(access_token=_OLD, refresh_token=_RT, expires_at=time.time() - 10)

    assert loader.cortex_access_token(refresh=lambda *_: {}) is None


def test_no_stored_token_yields_none(creds):
    loader, _ = creds
    assert loader.cortex_access_token(refresh=lambda *_: {"access_token": "X"}) is None


def test_the_oauth_block_takes_no_env_fallback(creds, monkeypatch):
    """The api_key path lets env fill gaps; this deliberately does not.

    A token set is four correlated fields that must move together. Letting a stray
    env var supply one produces a MISMATCHED set — a refresh token from one
    identity beside an access token from another — which fails far less legibly
    than "no token".
    """
    loader, _ = creds
    monkeypatch.setenv("CORTEX_ACCESS_TOKEN", "FROM-ENV")
    monkeypatch.setenv("CORTEX_OAUTH_ACCESS_TOKEN", "FROM-ENV")

    assert loader.get_cortex_oauth() == {}
