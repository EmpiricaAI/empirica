"""`empirica auth login` works on a machine that has never been configured (Philipp's Air, 2026-11-14).

With no ~/.empirica/credentials.yaml it stopped with "no cortex url configured — set cortex.url ... first", so a new seat could
not sign in and its listener could not be armed. It now takes --url, else the configured URL, else the hosted Cortex (the
default `doctor` and `setup-claude-code` already use), says which, and saves the URL after a successful sign-in so everything
after it finds it.
"""

from __future__ import annotations

import types

import pytest

from empirica.core.auth import cortex_oauth as co


@pytest.fixture(autouse=True)
def _no_listener_restart(monkeypatch):
    """Login and connect restart this machine's listener services; a test must not touch the real ones."""
    import empirica.core.loop_scheduler.persistent_listener as _pl

    monkeypatch.setattr(_pl, "restart_after_signin", lambda *a, **k: {"restarted": [], "note": None})


class Loader:
    def __init__(self, url=None):
        self.url, self.saved = url, []

    def get_cortex_config(self):
        return {"url": self.url} if self.url else {}

    def get_cortex_oauth(self):
        return {}

    def save_cortex_config(self, *, url=None, api_key=None, config_path=None):
        self.saved.append(url)


class Stop(Exception):
    pass


def _discovery_seen(monkeypatch):
    seen = []

    def disco(base_url, http=None):
        seen.append(base_url)
        raise Stop  # the URL is all this test is about; stop before any network or browser

    monkeypatch.setattr(co, "fetch_discovery", disco)
    return seen


@pytest.mark.parametrize(
    ("configured", "flag", "used", "source"),
    [
        (None, None, co.DEFAULT_CORTEX_URL, "default"),
        ("https://cortex.example.test", None, "https://cortex.example.test", "config"),
        ("https://cortex.example.test", "https://other.test/", "https://other.test", "flag"),
    ],
)
def test_the_url_comes_from_the_flag_then_the_config_then_the_hosted_default(
    monkeypatch, configured, flag, used, source
):
    seen = _discovery_seen(monkeypatch)
    with pytest.raises(Stop):
        co.login(loader=Loader(configured), url=flag)
    assert seen == [used]
    assert co.resolve_cortex_url(Loader(configured), flag) == (used, source)


def _run_command(monkeypatch, result, loader, url=None):
    from empirica.cli.command_handlers import auth_commands
    from empirica.core import auth

    monkeypatch.setattr(auth, "login", lambda **_kw: dict(result))
    monkeypatch.setattr(auth_commands, "_verify_token", lambda identity=None, org_id=None: True)
    monkeypatch.setattr("empirica.config.credentials_loader.get_credentials_loader", lambda: loader)
    args = types.SimpleNamespace(output="json", timeout=5, url=url)
    return auth_commands.handle_auth_login_command(args)


def test_a_url_that_was_not_configured_is_saved_after_a_successful_sign_in(monkeypatch):
    loader = Loader()
    ok = {"ok": True, "client_id": "c" * 20, "expires_at": None, "has_refresh_token": True,
          "cortex_url": co.DEFAULT_CORTEX_URL, "url_source": "default"}  # fmt: skip
    assert _run_command(monkeypatch, ok, loader) == 0
    assert loader.saved == [co.DEFAULT_CORTEX_URL]


def test_a_configured_url_is_not_rewritten(monkeypatch):
    loader = Loader("https://cortex.example.test")
    ok = {"ok": True, "client_id": "c" * 20, "expires_at": None, "has_refresh_token": True,
          "cortex_url": "https://cortex.example.test", "url_source": "config"}  # fmt: skip
    assert _run_command(monkeypatch, ok, loader) == 0
    assert loader.saved == []


def test_the_command_takes_url():
    import argparse

    from empirica.cli.parsers.auth_parsers import add_auth_parsers

    parser = argparse.ArgumentParser()
    add_auth_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["auth", "login", "--url", "https://cortex.example.test"])
    assert args.url == "https://cortex.example.test"


def test_a_client_registered_on_another_cortex_is_not_reused(monkeypatch):
    """Reusing it left the browser on an error and the login waiting out its timeout (1.17.0 sweep)."""
    registered = []

    class Stored(Loader):
        def get_cortex_oauth(self):
            return {"client_id": "old-client", "refresh_owner": "cli", "token_endpoint": "https://old.test/oauth/token"}

    monkeypatch.setattr(co, "fetch_discovery", lambda base_url, http=None: {
        "authorization_endpoint": f"{base_url}/authorize", "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/register"})  # fmt: skip

    def register(endpoint, http=None):
        registered.append(endpoint)
        raise Stop

    monkeypatch.setattr(co, "register_client", register)
    with pytest.raises(Stop):
        co.login(loader=Stored(), url="https://new.test")
    assert registered == ["https://new.test/register"], "a new client for the new Cortex"


def test_the_org_signed_in_to_is_reported(monkeypatch, capsys):
    """An invite's whole point is joining an org: the sign-in says which one, from the identity call it already makes."""
    from empirica.cli.command_handlers import auth_commands
    from empirica.core import auth

    seen = {}

    def login(**kw):
        seen.update(kw)
        return {"ok": True, "client_id": "c" * 20, "expires_at": None, "has_refresh_token": True,
                "cortex_url": "https://cortex.example.test", "url_source": "config", "invite": True}  # fmt: skip

    def verify(identity=None, org_id=None):
        identity.update({"org_id": "org-acme", "org_name": "Acme", "email": "x@acme.test"})
        return True

    monkeypatch.setattr(auth, "login", login)
    monkeypatch.setattr(auth_commands, "_verify_token", verify)
    monkeypatch.setattr(
        "empirica.config.credentials_loader.get_credentials_loader", lambda: Loader("https://cortex.example.test")
    )
    args = types.SimpleNamespace(output="human", timeout=5, url=None, invite="tok", org="acme", remote=True)
    assert auth_commands.handle_auth_login_command(args) == 0
    assert seen["invite"] == "tok" and seen["org"] == "acme" and seen["remote"] is True
    out = capsys.readouterr().out
    assert "Acme" in out and "org-acme" in out and "invite" in out
