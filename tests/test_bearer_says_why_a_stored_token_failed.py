"""cortex_bearer names why a stored OAuth set gave no credential (mesh-support prop_ha5bpa5w4be7nj44arykd3djwa).

For about fifteen minutes on one seat `mailbox poll` said "no cortex credential configured (no OAuth token, no api_key)" while
`auth status` reported a valid token set. An expired token whose refresh failed, or was another owner's to do, fell through
to the message for a seat with nothing stored, and the refresh error went only to a warning log the CLI does not show.
"""

from __future__ import annotations

import time

from empirica.core.auth import cortex_oauth as co

# A fake fixture, not a credential (named so S105/S106 stays quiet, as in test_cortex_auth_login).
_RT = "RT"


class Loader:
    def __init__(self, oauth=None, api_key=None, refresh_raises=None):
        self.oauth, self.api_key, self.refresh_raises = oauth or {}, api_key, refresh_raises

    def get_cortex_config(self):
        return {"url": "https://cortex.example.test", **({"api_key": self.api_key} if self.api_key else {})}

    def get_cortex_oauth(self):
        return dict(self.oauth)

    def cortex_access_token(self, *, refresh=None, leeway_s=120.0):
        if self.oauth.get("expires_at", 0) > time.time() + leeway_s:
            return self.oauth["access_token"]
        if refresh and self.oauth.get("refresh_token"):
            if self.refresh_raises:
                raise self.refresh_raises
            return None
        return None


def _expired(**extra):
    return {"access_token": "AT", "expires_at": time.time() - 60, "token_endpoint": "https://x/token", **extra}


def _reason(loader, monkeypatch):
    monkeypatch.setattr(co, "default_refresh", lambda loader, http=None: lambda *a: {})
    out = co.cortex_bearer(loader)
    assert out["bearer"] is None and out["source"] == "none"
    return out["reason"]


def test_a_refresh_that_raised_is_named_with_its_error(monkeypatch):
    loader = Loader(_expired(refresh_token=_RT, refresh_owner="cli"), refresh_raises=TimeoutError("timed out"))
    reason = _reason(loader, monkeypatch)
    assert "no cortex credential configured" not in reason
    assert "refresh failed" in reason and "timed out" in reason


def test_a_token_another_owner_refreshes_says_so(monkeypatch):
    reason = _reason(Loader(_expired(refresh_token=_RT, refresh_owner="daemon")), monkeypatch)
    assert "no cortex credential configured" not in reason
    assert "daemon" in reason


def test_an_expired_token_with_no_refresh_token_says_so(monkeypatch):
    reason = _reason(Loader(_expired(refresh_owner="cli")), monkeypatch)
    assert "no refresh token" in reason and "auth login" in reason


def test_nothing_stored_still_reads_as_nothing_configured(monkeypatch):  # control
    assert _reason(Loader(), monkeypatch) == "no cortex credential configured (no OAuth token, no api_key)"


def test_an_api_key_still_covers_an_expired_token(monkeypatch):  # control: the fallback is unchanged
    monkeypatch.setattr(co, "default_refresh", lambda loader, http=None: lambda *a: {})
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    out = co.cortex_bearer(Loader(_expired(refresh_owner="daemon"), api_key="ctx_k"))
    assert out["source"] == "api_key"
