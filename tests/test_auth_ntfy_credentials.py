"""auth login fetches the seat's ntfy credentials, so the listeners can subscribe (mesh-support prop_ajiz272z, goal c22d6a10).

Cortex: GET /v1/users/me/ntfy-credentials with the seat's own OAuth bearer returns {ntfy_url, bearer, created_at}; 404
bearer_not_provisioned when the server has no notification account for the person; limited to 5 calls an hour.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.parse

import pytest
import yaml

_DISCO = {
    "authorization_endpoint": "https://cortex.example/v1/oauth/authorize",
    "token_endpoint": "https://cortex.example/v1/oauth/token",
    "registration_endpoint": "https://cortex.example/v1/oauth/register",
}


@pytest.fixture
def creds(tmp_path, monkeypatch):
    from empirica.config.credentials_loader import CredentialsLoader

    path = tmp_path / "credentials.yaml"
    path.write_text("version: '1.0'\ncortex:\n  url: https://cortex.example\n")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(path))
    for v in (
        "CORTEX_API_KEY",
        "CORTEX_URL",
        "CORTEX_REMOTE_URL",
        "ORCHESTRATION_NTFY_TOKEN",
        "ORCHESTRATION_NTFY_URL",
    ):
        monkeypatch.delenv(v, raising=False)
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    yield path
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


def _login(ntfy_response, calls):
    from empirica.config.credentials_loader import CredentialsLoader
    from empirica.core.auth import cortex_oauth

    def http(url, *, data=None, headers=None, timeout=15.0, form=False):
        calls.append(url)
        if "/.well-known" in url:
            return _DISCO
        if "/register" in url:
            return {"client_id": "cli"}
        if "/token" in url:
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
        if url.endswith("/v1/users/me"):
            return {"org_id": "org-a"}
        if "/ntfy-credentials" in url:
            assert headers == {"Authorization": "Bearer AT"}  # the seat's own new bearer
            if isinstance(ntfy_response, Exception):
                raise ntfy_response
            return ntfy_response
        raise AssertionError(url)

    def read_code(authorize_url):
        return "code#" + dict(urllib.parse.parse_qsl(urllib.parse.urlparse(authorize_url).query))["state"]

    return cortex_oauth.login(loader=CredentialsLoader(), http=http, remote=True, read_code=read_code, timeout_s=5)


def _http_error(code, body):
    return urllib.error.HTTPError(
        "https://cortex.example/v1/users/me/ntfy-credentials", code, "x", {}, io.BytesIO(body)
    )


def test_a_sign_in_writes_the_seats_ntfy_url_and_token(creds):
    calls = []
    result = _login({"ntfy_url": "https://ntfy.example/", "bearer": "tk_abc", "created_at": "2026-11-19"}, calls)
    ntfy = yaml.safe_load(creds.read_text())["ntfy"]
    assert ntfy["url"] == "https://ntfy.example" and ntfy["token"] == "tk_abc"  # noqa: S105 - a test token
    assert result["ntfy"]["written"] is True and "tk_abc" not in str(result["ntfy"])


def test_a_seat_without_a_notification_account_is_told_so_and_nothing_is_written(creds):
    result = _login(_http_error(404, b'{"error": "bearer_not_provisioned"}'), [])
    assert result["ok"] and result["ntfy"]["written"] is False
    assert "no notification account" in result["ntfy"]["reason"]
    assert "ntfy" not in (yaml.safe_load(creds.read_text()) or {})


def test_a_token_already_stored_is_kept_and_the_limited_endpoint_is_not_called(creds):
    creds.write_text(creds.read_text() + "ntfy:\n  url: https://ntfy.example\n  token: tk_mine\n")
    calls = []
    result = _login({"ntfy_url": "https://ntfy.example", "bearer": "tk_new"}, calls)
    assert not any("/ntfy-credentials" in c for c in calls)
    assert yaml.safe_load(creds.read_text())["ntfy"]["token"] == "tk_mine" and result["ntfy"]["written"] is False  # noqa: S105


def test_a_failed_fetch_never_fails_the_sign_in(creds):
    result = _login(_http_error(429, b'{"error": "rate_limited"}'), [])
    assert result["ok"] and result["ntfy"]["written"] is False and "429" in result["ntfy"]["reason"]
