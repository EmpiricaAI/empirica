"""Route tests for GET/PATCH /api/v1/calibration/config.

Registers just the calibration blueprint on a bare Flask app (bypassing the
daemon's auth middleware + DB), and monkeypatches the scope dirs to tmp so no
real ~/.empirica or project files are touched.
"""

from __future__ import annotations

import pytest
from flask import Flask

from empirica.api.routes import calibration


@pytest.fixture()
def client(tmp_path, monkeypatch):
    global_dir = tmp_path / "global"
    practice_dir = tmp_path / "practice-A"
    global_dir.mkdir()
    practice_dir.mkdir()

    monkeypatch.setattr(calibration, "_global_dir", lambda: global_dir)
    monkeypatch.setattr(
        calibration,
        "_resolve_practice_dir",
        lambda pid: practice_dir if pid == "practice-A" else None,
    )

    app = Flask(__name__)
    app.register_blueprint(calibration.bp, url_prefix="/api/v1")
    return app.test_client()


def test_get_returns_schema_presets_and_defaults(client):
    resp = client.get("/api/v1/calibration/config")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["weights"]["foundation"] == 0.35
    assert body["thresholds"]["engagement_gate"] == 0.60
    assert len(body["schema"]) == 8
    assert "security" in body["presets"]
    assert body["overridden"] == []


def test_patch_global_persists_and_reflects(client):
    resp = client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"engagement_gate": 0.7}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["thresholds"]["engagement_gate"] == 0.7
    # a fresh GET sees the persisted global override
    body2 = client.get("/api/v1/calibration/config").get_json()
    assert body2["thresholds"]["engagement_gate"] == 0.7
    assert "thresholds.engagement_gate" in body2["overridden"]


def test_patch_practice_layers_over_global(client):
    client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"engagement_gate": 0.7}})
    client.patch(
        "/api/v1/calibration/config?scope=practice&practice_id=practice-A",
        json={"thresholds": {"engagement_gate": 0.9}},
    )
    body = client.get("/api/v1/calibration/config?practice_id=practice-A").get_json()
    assert body["thresholds"]["engagement_gate"] == 0.9  # practice wins
    assert body["sources"]["thresholds.engagement_gate"] == "practice"


def test_patch_invalid_key_is_422(client):
    resp = client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"bogus": 0.5}})
    assert resp.status_code == 422
    assert "details" in resp.get_json()


def test_patch_practice_without_id_is_400(client):
    resp = client.patch("/api/v1/calibration/config?scope=practice", json={"thresholds": {"engagement_gate": 0.7}})
    assert resp.status_code == 400


def test_patch_unknown_practice_is_404(client):
    resp = client.patch(
        "/api/v1/calibration/config?scope=practice&practice_id=nope",
        json={"thresholds": {"engagement_gate": 0.7}},
    )
    assert resp.status_code == 404


def test_patch_reset_key_restores_default(client):
    client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"engagement_gate": 0.7}})
    client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"engagement_gate": None}})
    body = client.get("/api/v1/calibration/config").get_json()
    assert body["thresholds"]["engagement_gate"] == 0.60  # back to default
    assert body["overridden"] == []
