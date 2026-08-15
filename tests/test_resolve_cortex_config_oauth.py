"""The three hand-rolled `_resolve_cortex_config` copies must route through
`cortex_bearer` — OAuth-first with api_key fallback — so an OAuth-only seat
(api_key retired) still authenticates. They used to read `cortex.api_key`
straight from credentials.yaml and returned a None token on such a seat.
"""

import types

import pytest


def _fake_bearer():
    return {"url": "https://cortex.example", "bearer": "oauth-token-xyz"}


@pytest.fixture
def patched_bearer(monkeypatch):
    import empirica.core.auth as auth

    monkeypatch.setattr(auth, "cortex_bearer", _fake_bearer)


def test_forgejo_resolver_returns_oauth_token(patched_bearer):
    from empirica.cli.command_handlers.forgejo_commands import _resolve_cortex_config

    url, key = _resolve_cortex_config()
    assert url == "https://cortex.example"
    assert key == "oauth-token-xyz"  # the OAuth token, not None


def test_projects_resolver_returns_oauth_token(patched_bearer):
    from empirica.cli.command_handlers.projects_commands import _resolve_cortex_config

    args = types.SimpleNamespace(cortex_url=None, api_key=None)
    _url, key = _resolve_cortex_config(args)
    assert key == "oauth-token-xyz"


def test_practice_context_resolver_returns_oauth_token(patched_bearer):
    from empirica.cli.command_handlers.practice_context_commands import _resolve_cortex_config

    args = types.SimpleNamespace(cortex_url=None, api_key=None)
    _url, key = _resolve_cortex_config(args)
    assert key == "oauth-token-xyz"


def test_explicit_cli_flags_still_short_circuit_before_bearer():
    """The per-invocation override must win before the credential fall-through,
    so cortex_bearer is not even consulted when both flags are supplied."""
    from empirica.cli.command_handlers.projects_commands import _resolve_cortex_config

    args = types.SimpleNamespace(cortex_url="https://override/", api_key="explicit-key")
    url, key = _resolve_cortex_config(args)
    assert (url, key) == ("https://override", "explicit-key")
