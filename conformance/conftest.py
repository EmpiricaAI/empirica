"""Options, the contract, and one HTTP client for the whole run."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import yaml


def pytest_addoption(parser):
    g = parser.getgroup("conformance")
    g.addoption("--url", default=os.environ.get("CONFORMANCE_URL"), help="base URL of the server under test")
    g.addoption("--token", default=os.environ.get("CONFORMANCE_TOKEN"), help="bearer credential for the caller")
    g.addoption("--contract", default=os.environ.get("CONFORMANCE_CONTRACT"), help="path to routes.yaml")
    g.addoption("--allow-writes", action="store_true", default=bool(os.environ.get("CONFORMANCE_ALLOW_WRITES")),
                help="run the write tier. ONLY against a disposable server: it creates and archives proposals, "
                     "registers and unregisters a project, and toggles auto-accept")


def pytest_collection_modifyitems(config, items):
    """Without a URL there is nothing to test: skip, so a repo that vendors this directory stays green."""
    if config.getoption("--url"):
        return
    skip = pytest.mark.skip(reason="no --url / CONFORMANCE_URL: conformance suite not pointed at a server")
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)


def _find_contract(option):
    here = Path(__file__).parent
    for cand in (option, here / "routes.yaml", here.parent / "contract" / "routes.yaml"):
        if cand and Path(cand).is_file():
            return Path(cand)
    raise pytest.UsageError("routes.yaml not found: pass --contract or place it next to this package")


@pytest.fixture(scope="session")
def contract(request):
    return yaml.safe_load(_find_contract(request.config.getoption("--contract")).read_text())


@pytest.fixture(scope="session")
def http(request):
    url = request.config.getoption("--url")
    if not url:
        pytest.skip("no --url")
    with httpx.Client(base_url=url.rstrip("/"), timeout=20.0, follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="session")
def token(request):
    return request.config.getoption("--token")


@pytest.fixture(scope="session")
def auth(token):
    """Headers for an authenticated call; the test is skipped when no credential was given."""
    if not token:
        pytest.skip("no --token / CONFORMANCE_TOKEN")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def server(http, contract):
    """The server's own capability document, fetched once."""
    resp = http.get("/v1/health")
    assert resp.status_code == 200, f"GET /v1/health answered {resp.status_code}: the contract's capability document is missing"
    return resp.json()


@pytest.fixture(scope="session")
def writes(request):
    """Gate for the write tier: nothing that changes the server runs without --allow-writes."""
    if not request.config.getoption("--allow-writes"):
        pytest.skip("write tier is opt-in (--allow-writes) and for disposable servers only")


@pytest.fixture(scope="session")
def me(http, auth, server):
    """The caller's identity and one address it may send from and to (its own)."""
    who = http.get("/v1/users/me", headers=auth)
    assert who.status_code == 200, who.text[:200]
    roster = http.get("/v1/users/me/roster", headers=auth).json()
    mine = [t for t in roster["org"]["tenants"] if t["tenant_slug"] == roster["self"]["tenant_slug"]]
    projects = [p for t in mine for p in t.get("projects", [])]
    if not projects:
        pytest.skip("the caller has no practice to send from")
    return {"tenant_slug": roster["self"]["tenant_slug"], "address": projects[0]["ai_id_mesh"]}
