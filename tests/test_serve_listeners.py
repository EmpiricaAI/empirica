"""Tests for the serve daemon's GET /api/v1/listeners bridge.

Merges the on-disk listener registry (`listeners_<inst>.json`) + heartbeat
health (`listener_health_<inst>.json`) into rows for the extension's
receive-path health indicator. Fixtures skipped; missing health → null fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from empirica.api import serve_app as sa


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    emp = tmp_path / ".empirica"
    # canonical seat WITH health
    _write(emp / "listeners_empirica.json", {
        "instance_id": "empirica", "instance_label": None,
        "listeners": {"empirica-inbox": {
            "topic": "ntfy:empirica-orchestration-events-david?tags=empirica",
            "description": "Canonical mesh listener for ai_id=empirica",
            "registered_at": "2026-06-04T10:00:00Z",
            "last_wake_at": "2026-06-17T20:00:00Z", "last_message": "woke",
            "wake_count": 5,
        }},
    })
    _write(emp / "listener_health_empirica.json", {
        "instance_id": "empirica", "loop": "cortex-mailbox-poll",
        "status": "ok", "ts": "2026-06-17T21:00:00Z", "source": "liveness_probe",
    })
    # canonical seat WITHOUT health (health_* must be null)
    _write(emp / "listeners_ecodex.json", {
        "instance_id": "ecodex",
        "listeners": {"ecodex-inbox": {"topic": "ntfy:x?tags=ecodex", "wake_count": 0}},
    })
    # fixtures — must be skipped
    _write(emp / "listeners_smoke_test.json", {"instance_id": "smoke_test", "listeners": {"a": {"topic": "t"}}})
    _write(emp / "listeners_tmux_0.json", {"instance_id": "tmux_0", "listeners": {"b": {"topic": "t"}}})
    # malformed — must be skipped, not fatal
    (emp / "listeners_broken.json").write_text("{not json", encoding="utf-8")
    return tmp_path


def test_gather_merges_registry_and_health(fake_home):
    rows = {r["instance_id"]: r for r in sa._gather_listeners()}
    assert set(rows) == {"empirica", "ecodex"}  # fixtures + broken skipped
    emp = rows["empirica"]
    assert emp["name"] == "empirica-inbox"
    assert emp["topic"] == "ntfy:empirica-orchestration-events-david?tags=empirica"
    assert emp["wake_count"] == 5
    assert emp["last_wake_at"] == "2026-06-17T20:00:00Z"
    assert emp["health_status"] == "ok"
    assert emp["health_loop"] == "cortex-mailbox-poll"
    assert emp["health_ts"] == "2026-06-17T21:00:00Z"


def test_gather_null_health_when_no_marker(fake_home):
    rows = {r["instance_id"]: r for r in sa._gather_listeners()}
    eco = rows["ecodex"]
    assert eco["health_status"] is None
    assert eco["health_loop"] is None
    assert eco["health_ts"] is None
    assert eco["wake_count"] == 0


@pytest.mark.parametrize("inst", ["test_instance", "smoke_test", "custom", "tmux_0", "tmux_42"])
def test_is_fixture_instance(inst):
    assert sa._is_fixture_instance(inst) is True


@pytest.mark.parametrize("inst", ["empirica", "empirica-autonomy", "cortex", "ecodex"])
def test_canonical_not_fixture(inst):
    assert sa._is_fixture_instance(inst) is False


def test_gather_empty_when_no_empirica_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # no .empirica
    assert sa._gather_listeners() == []


def test_endpoint_returns_rows(fake_home):
    client = TestClient(sa.create_serve_app())
    r = client.get("/api/v1/listeners")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    insts = {row["instance_id"] for row in body["listeners"]}
    assert insts == {"empirica", "ecodex"}


def test_endpoint_open_when_guard_inactive(fake_home, monkeypatch):
    """No token set configured (loopback case) → no auth required.

    This is the extension's local read; it must stay unauthenticated.
    """
    monkeypatch.delenv("EMPIRICA_ENTITY_MINT_TOKENS", raising=False)
    client = TestClient(sa.create_serve_app())
    assert client.get("/api/v1/listeners").status_code == 200


def test_endpoint_401_when_guard_active_and_no_bearer(fake_home, monkeypatch):
    """Token set configured (non-loopback deployment) → bearer required.

    The rows carry ntfy topic names + last-message bodies, so a
    network-exposed daemon must not serve them unauthenticated.
    """
    monkeypatch.setenv("EMPIRICA_ENTITY_MINT_TOKENS", "emk_secret_one")
    client = TestClient(sa.create_serve_app())
    assert client.get("/api/v1/listeners").status_code == 401


def test_endpoint_200_when_guard_active_with_valid_bearer(fake_home, monkeypatch):
    monkeypatch.setenv("EMPIRICA_ENTITY_MINT_TOKENS", "emk_secret_one,emk_secret_two")
    client = TestClient(sa.create_serve_app())
    r = client.get(
        "/api/v1/listeners",
        headers={"Authorization": "Bearer emk_secret_two"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
