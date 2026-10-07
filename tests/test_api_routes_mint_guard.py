"""The artifacts and practice routers sit behind verify_mint_bearer, like entities and calibration.

Found by the 2026-10-06 pipeline sweep (unit U4): the artifact routes had no bearer guard, so on a hosted daemon a caller with network
reach could read artifacts and any project's source text through `?path=`, and PATCH or DELETE them. The guard is a no-op when no token
set is configured (loopback), so the default install is unchanged: the last test pins that.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from empirica.api import entity_mint_auth as ema
from empirica.api.routes import artifacts, practice
from empirica.api.serve_app import create_serve_app

TOKEN = "emk_test_token_for_the_guard"  # noqa: S105 - a fixture, not a credential


def _calls(dependant):
    out = []
    for dep in dependant.dependencies:
        out.append(dep.call)
        out.extend(_calls(dep))
    return out


@pytest.mark.parametrize("router", [artifacts.router, practice.router], ids=["artifacts", "practice"])
def test_every_route_of_the_router_carries_the_bearer_dependency(router):
    unguarded = [
        (sorted(r.methods), r.path)
        for r in router.routes
        if isinstance(r, APIRoute) and ema.verify_mint_bearer not in _calls(r.dependant)
    ]
    assert unguarded == []


def _guarded_routes(app):
    modules = {artifacts.__name__, practice.__name__}
    return [r for r in app.routes if isinstance(r, APIRoute) and r.endpoint.__module__ in modules]


def _request(client, route, headers=None):
    method = sorted(route.methods - {"HEAD", "OPTIONS"})[0]
    path = re.sub(r"\{[^}]+\}", "x", route.path)
    return client.request(method, path, headers=headers or {}, json={} if method in {"POST", "PATCH", "PUT"} else None)


def test_a_configured_token_set_rejects_a_missing_or_wrong_bearer_on_every_route(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, TOKEN)
    app = create_serve_app()
    client = TestClient(app, raise_server_exceptions=False)
    routes = _guarded_routes(app)
    assert len(routes) >= 10  # the enumerator walked the real routers, not an empty list
    for route in routes:
        assert _request(client, route).status_code == 401, route.path
        assert _request(client, route, {"Authorization": "Bearer emk_wrong"}).status_code == 401, route.path


def test_the_dependency_lets_a_valid_bearer_through_and_is_inactive_without_a_token_set(monkeypatch):
    """Handlers are not run here (they would resolve a real project): the dependency itself carries the allow path."""
    import asyncio

    monkeypatch.setenv(ema.ENV_TOKENS, TOKEN)
    assert asyncio.run(ema.verify_mint_bearer(f"Bearer {TOKEN}")) is None
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    assert asyncio.run(ema.verify_mint_bearer(None)) is None  # loopback: auth-free, as before
