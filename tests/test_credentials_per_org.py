"""Several teams' sign-ins on one machine (goal da0396f8).

A token is bound to one user in one org, so several teams means several token sets. `cortex.oauth` stays where it is, the
default team's set, because other tools read it directly (cortex-cli reads cortex.oauth.access_token); another team's set
lives at `cortex.orgs.<org_id>` with the same fields, and each block records its own org_id.
"""

from __future__ import annotations

import time

import pytest
import yaml

_AT_A, _AT_B, _RT, _NEW_B = "AT_A", "AT_B", "RT", "NEW_B"  # fake fixtures, not credentials


@pytest.fixture
def creds(tmp_path, monkeypatch):
    from empirica.config.credentials_loader import CredentialsLoader

    path = tmp_path / "credentials.yaml"
    for var in ("CORTEX_API_KEY", "CORTEX_URL", "CORTEX_REMOTE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(path))

    def write(cortex: dict) -> CredentialsLoader:
        path.write_text(yaml.safe_dump({"version": "1.0", "cortex": cortex}))
        CredentialsLoader._instance = None
        CredentialsLoader._credentials_cache = None
        return CredentialsLoader()

    yield write, path
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


def _set(token, org=None, **extra):
    return {"access_token": token, "refresh_token": _RT, "expires_at": time.time() + 3600,
            "token_endpoint": "https://c/t", "client_id": "cli", "refresh_owner": "cli",
            **({"org_id": org} if org else {}), **extra}  # fmt: skip


def test_no_org_asked_reads_the_default_block_as_before(creds):
    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert loader.get_cortex_oauth()["access_token"] == _AT_A
    assert loader.cortex_access_token() == _AT_A


def test_an_org_asked_reads_its_own_block(creds):
    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert loader.get_cortex_oauth(org_id="org-b")["access_token"] == _AT_B
    assert loader.get_cortex_oauth(org_id="org-a")["access_token"] == _AT_A  # the default is that org's block
    assert loader.cortex_access_token(org_id="org-b") == _AT_B


def test_an_org_with_no_sign_in_reads_as_none(creds):
    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a")})
    assert loader.get_cortex_oauth(org_id="org-c") == {}
    assert loader.cortex_access_token(org_id="org-c") is None


def test_a_default_signed_in_before_orgs_were_recorded_serves_any_org(creds):
    """Its org is unknown (pre-1.17.2 sign-in): it is offered, as before, rather than refusing every practice."""
    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A)})
    assert loader.get_cortex_oauth(org_id="org-a")["access_token"] == _AT_A


def test_every_sign_in_is_listed(creds):
    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert [(slot, block["access_token"]) for slot, block in loader.cortex_oauth_sets()] == [
        (None, _AT_A),
        ("org-b", _AT_B),
    ]


def test_a_refresh_of_one_org_writes_that_org_only(creds):
    write, path = creds
    expired = _set(_AT_B, "org-b", expires_at=time.time() - 60)
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": expired}})
    token = loader.cortex_access_token(
        org_id="org-b", refresh=lambda rt, ep: {"access_token": _NEW_B, "expires_at": time.time() + 3600}
    )
    assert token == _NEW_B
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["orgs"]["org-b"]["access_token"] == _NEW_B
    assert stored["oauth"]["access_token"] == _AT_A


def test_the_bearer_for_an_org_is_that_orgs_token(creds, monkeypatch):
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    assert co.cortex_bearer(loader)["bearer"] == _AT_A
    assert co.cortex_bearer(loader, org_id="org-b")["bearer"] == _AT_B


def test_an_org_with_no_sign_in_says_which_and_how(creds, monkeypatch):
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a")})
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    out = co.cortex_bearer(loader, org_id="org-c")
    assert out["bearer"] is None
    assert "org-c" in out["reason"] and "auth login --org org-c" in out["reason"]


def test_refresh_custody_is_read_from_the_orgs_own_block(creds, monkeypatch):
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": "https://c", "oauth": _set(_AT_A, "org-a", refresh_owner="daemon"),
                    "orgs": {"org-b": _set(_AT_B, "org-b", refresh_owner="cli")}})  # fmt: skip
    assert co.custody_refresh(loader, org_id="org-a")[1] == "daemon"
    assert co.custody_refresh(loader, org_id="org-b")[1] == "cli"
