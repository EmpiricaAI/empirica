"""empirica-local's skeleton (spec phase 1, goal 92fe2c88): mounted into `empirica serve` only on a loopback bind,
answering the capability document and 501 {capability} for every contract route it does not implement yet."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO = Path(__file__).resolve().parents[1]


def test_the_package_copy_of_the_contract_is_the_vendored_one():
    assert (REPO / "empirica/local_server/routes.yaml").read_bytes() == (REPO / "contract/routes.yaml").read_bytes()


@pytest.fixture
def client_for(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HOME", str(tmp_path))

    def make(host: str):
        monkeypatch.setenv("EMPIRICA_SERVE_HOST", host)
        from empirica.api.serve_app import create_serve_app

        return TestClient(create_serve_app())

    return make


def test_on_loopback_the_capability_document_is_served(client_for):
    client = client_for("127.0.0.1")
    for path in ("/v1/health", "/health"):
        doc = client.get(path).json()
        assert (
            doc["status"] == "healthy"
            and doc["edition"] == "local"
            and doc["contract"] == "1.0"
            and doc["capabilities"] == []
        )


@pytest.mark.parametrize(
    "method, path, capability",
    [
        ("GET", "/v1/users/me", "identity.session"),
        ("POST", "/v1/orchestration/propose", "orchestration.mailbox"),
        ("GET", "/v1/projects/by-slug/demo", "projects.registry"),
        ("POST", "/v1/admin/projects", "admin.projects"),
    ],
)
def test_an_unimplemented_contract_route_answers_501_with_its_capability(client_for, method, path, capability):
    resp = client_for("127.0.0.1").request(method, path, json={})
    assert resp.status_code == 501 and resp.json() == {"capability": capability}


def test_a_path_outside_the_contract_is_404_not_501(client_for):
    assert client_for("127.0.0.1").get("/v1/not-a-route").status_code == 404


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])  # noqa: S104 - the exposed binds under test
def test_on_any_other_bind_the_contract_routes_do_not_exist(client_for, host):
    client = client_for(host)
    assert client.get("/v1/health").status_code == 404
    assert client.get("/v1/users/me").status_code == 404
