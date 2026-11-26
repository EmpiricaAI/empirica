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

# The reason for a seat with nothing stored. It read "no cortex credential configured (no OAuth token, no api_key)" until
# core retired the api_key (goal 739e47b2); the tests below assert a stored-but-failed set never falls through to it.
NOTHING_STORED = "no Cortex sign-in on this machine — run `empirica auth login`"


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
            if self.refresh_raises:  # as the real loader does: caught, recorded, None
                self.last_refresh_error = f"{type(self.refresh_raises).__name__}: {self.refresh_raises}"
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
    assert reason != NOTHING_STORED
    assert "refresh failed" in reason and "timed out" in reason


def test_a_token_another_owner_refreshes_says_so(monkeypatch):
    reason = _reason(Loader(_expired(refresh_token=_RT, refresh_owner="daemon")), monkeypatch)
    assert reason != NOTHING_STORED
    assert "daemon" in reason


def test_an_expired_token_with_no_refresh_token_says_so(monkeypatch):
    reason = _reason(Loader(_expired(refresh_owner="cli")), monkeypatch)
    assert "no refresh token" in reason and "auth login" in reason


def test_nothing_stored_still_reads_as_nothing_configured(monkeypatch):  # control
    assert _reason(Loader(), monkeypatch) == NOTHING_STORED


def test_an_api_key_no_longer_covers_an_expired_token(monkeypatch):
    """The api_key fallback is gone (goal 739e47b2): a leftover key does not stand in for an expired token, and the
    reason still names why the token gave nothing rather than reading as nothing stored."""
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    reason = _reason(Loader(_expired(refresh_owner="daemon"), api_key="ctx_k"), monkeypatch)
    assert reason != NOTHING_STORED
    assert "no refresh token" in reason


def test_a_refresh_that_fails_inside_the_real_loader_is_named(tmp_path, monkeypatch):
    """1.17.1 sweep: the real loader catches a failed refresh and returns None, so the fake above never matched production;
    a timeout read as 'the refresh returned no token — re-authenticate'."""
    from empirica.config.credentials_loader import CredentialsLoader

    creds = tmp_path / "credentials.yaml"
    creds.write_text(
        "version: '1.0'\ncortex:\n  url: https://cortex.example\n  oauth:\n    access_token: AT\n"
        f"    refresh_token: {_RT}\n    expires_at: {time.time() - 60}\n    token_endpoint: https://cortex.example/t\n"
        "    client_id: c\n    refresh_owner: cli\n"
    )
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(creds))
    for var in ("CORTEX_API_KEY", "CORTEX_URL", "CORTEX_REMOTE_URL"):
        monkeypatch.delenv(var, raising=False)
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    try:

        def refresh(*_a):
            raise TimeoutError("timed out")

        monkeypatch.setattr(co, "default_refresh", lambda loader, http=None: refresh)
        out = co.cortex_bearer(CredentialsLoader())
    finally:
        CredentialsLoader._instance = None
        CredentialsLoader._credentials_cache = None
    assert out["bearer"] is None
    assert "refresh failed" in out["reason"] and "TimeoutError" in out["reason"], out["reason"]


def test_an_unreadable_credentials_file_is_not_reported_as_nothing_configured(monkeypatch):
    class Broken(Loader):
        def get_cortex_oauth(self):
            raise OSError("permission denied")

    monkeypatch.setattr(co, "default_refresh", lambda loader, http=None: lambda *a: {})
    out = co.cortex_bearer(Broken())
    assert out["reason"] != NOTHING_STORED
    assert "permission denied" in out["reason"]
