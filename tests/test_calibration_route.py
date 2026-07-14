"""Route tests for GET/PATCH /api/v1/calibration/config.

Mounts just the calibration FastAPI router on a bare app (bypassing the daemon's
other routes + DB), and monkeypatches the scope dirs to tmp so no real
~/.empirica or project files are touched. The route moved from a Flask blueprint
(api/app.py, which the daemon doesn't run → 404) to a FastAPI router in
serve_app.py; these tests track that.
"""

from __future__ import annotations

import pytest

from empirica.api.routes import calibration


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

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

    app = FastAPI()
    app.include_router(calibration.router)
    return TestClient(app)


def test_get_returns_schema_presets_and_defaults(client):
    resp = client.get("/api/v1/calibration/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["weights"]["foundation"] == 0.35
    assert body["thresholds"]["engagement_gate"] == 0.60
    assert len(body["schema"]) == 9
    # Typed presets: two orthogonal axes (stance = gates, persona = weights).
    assert "security" in body["presets"]["persona"]
    assert "rigorous" in body["presets"]["stance"]
    assert body["overridden"] == []
    assert body["stance"] is None  # no stance override by default
    assert body["active_transaction"] is False  # global scope → no open tx


def test_patch_global_persists_and_reflects(client):
    resp = client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"engagement_gate": 0.7}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["thresholds"]["engagement_gate"] == 0.7
    # a fresh GET sees the persisted global override
    body2 = client.get("/api/v1/calibration/config").json()
    assert body2["thresholds"]["engagement_gate"] == 0.7
    assert "thresholds.engagement_gate" in body2["overridden"]


def test_patch_practice_layers_over_global(client):
    client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"engagement_gate": 0.7}})
    client.patch(
        "/api/v1/calibration/config?scope=practice&practice_id=practice-A",
        json={"thresholds": {"engagement_gate": 0.9}},
    )
    body = client.get("/api/v1/calibration/config?practice_id=practice-A").json()
    assert body["thresholds"]["engagement_gate"] == 0.9  # practice wins
    assert body["sources"]["thresholds.engagement_gate"] == "practice"


def test_patch_invalid_key_is_422(client):
    resp = client.patch("/api/v1/calibration/config?scope=global", json={"thresholds": {"bogus": 0.5}})
    assert resp.status_code == 422
    # FastAPI nests the HTTPException detail; the validation errors ride under it.
    assert "details" in resp.json()["detail"]


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
    body = client.get("/api/v1/calibration/config").json()
    assert body["thresholds"]["engagement_gate"] == 0.60  # back to default
    assert body["overridden"] == []


# ── stance presets (orthogonal calibration-stance axis) ──────────────────────


def test_stance_preset_moves_the_gate_meters(client):
    """A stance PATCH moves the two gate thresholds (and only those)."""
    resp = client.patch("/api/v1/calibration/config?scope=global", json={"stance": "rigorous"})
    assert resp.status_code == 200
    body = client.get("/api/v1/calibration/config").json()
    assert body["stance"] == "rigorous"
    assert body["thresholds"]["ready_uncertainty"] == 0.25  # stricter gate
    assert body["thresholds"]["engagement_gate"] == 0.80
    assert body["sources"]["thresholds.ready_uncertainty"] == "stance:rigorous"


def test_stance_and_persona_compose_orthogonally(client):
    """Persona owns weights; stance owns gates — they don't clobber each other."""
    client.patch("/api/v1/calibration/config?scope=global", json={"preset": "security", "stance": "exploratory"})
    body = client.get("/api/v1/calibration/config").json()
    assert body["preset"] == "security"
    assert body["stance"] == "exploratory"
    # weights from persona
    assert body["sources"]["weights.foundation"] == "preset:security"
    # gate from stance
    assert body["thresholds"]["ready_uncertainty"] == 0.45
    assert body["sources"]["thresholds.ready_uncertainty"] == "stance:exploratory"


def test_unknown_stance_is_422(client):
    resp = client.patch("/api/v1/calibration/config?scope=global", json={"stance": "bogus"})
    assert resp.status_code == 422


def test_stance_reset_restores_default_gate(client):
    client.patch("/api/v1/calibration/config?scope=global", json={"stance": "rigorous"})
    client.patch("/api/v1/calibration/config?scope=global", json={"stance": None})
    body = client.get("/api/v1/calibration/config").json()
    assert body["stance"] is None
    assert body["thresholds"]["ready_uncertainty"] == 0.35  # base default


# ── active_transaction flag (defer-to-boundary surfacing) ────────────────────


def test_active_transaction_true_when_open_tx(client, tmp_path):
    """The flag reflects an OPEN active_transaction*.json in the practice dir."""
    import json

    emp = tmp_path / "practice-A" / ".empirica"
    emp.mkdir(parents=True)
    (emp / "active_transaction_tmux0.json").write_text(json.dumps({"status": "open"}))
    body = client.get("/api/v1/calibration/config?practice_id=practice-A").json()
    assert body["active_transaction"] is True


def test_active_transaction_false_when_closed(client, tmp_path):
    import json

    emp = tmp_path / "practice-A" / ".empirica"
    emp.mkdir(parents=True)
    (emp / "active_transaction_tmux0.json").write_text(json.dumps({"status": "closed"}))
    body = client.get("/api/v1/calibration/config?practice_id=practice-A").json()
    assert body["active_transaction"] is False


def test_active_transaction_false_for_global_scope(client):
    body = client.get("/api/v1/calibration/config").json()
    assert body["active_transaction"] is False


# ── defer-to-boundary (queue during open transaction, promote at PREFLIGHT) ───


def test_patch_defers_during_open_transaction(client, tmp_path):
    """A practice PATCH with an open transaction is queued, not applied live."""
    import json

    from empirica.core import calibration_config as cc

    emp = tmp_path / "practice-A" / ".empirica"
    emp.mkdir(parents=True)
    (emp / "active_transaction_tmux0.json").write_text(json.dumps({"status": "open"}))

    resp = client.patch(
        "/api/v1/calibration/config?scope=practice&practice_id=practice-A",
        json={"stance": "rigorous"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deferred"] is True
    assert body["active_transaction"] is True
    # NOT applied live yet — effective stance still None, queued in pending.
    assert body["stance"] is None
    assert body["pending"].get("stance") == "rigorous"
    assert cc.read_override(tmp_path / "practice-A") == {}  # live untouched

    # Promote at the boundary → live now carries it, pending cleared.
    promoted = cc.promote_pending(tmp_path / "practice-A")
    assert promoted.get("stance") == "rigorous"
    assert cc.read_override(tmp_path / "practice-A").get("stance") == "rigorous"
    assert cc.read_pending(tmp_path / "practice-A") == {}


def test_patch_applies_live_when_no_open_transaction(client, tmp_path):
    """Without an open transaction, a practice PATCH applies immediately."""
    from empirica.core import calibration_config as cc

    resp = client.patch(
        "/api/v1/calibration/config?scope=practice&practice_id=practice-A",
        json={"stance": "exploratory"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deferred"] is False
    assert body["stance"] == "exploratory"  # live
    assert cc.read_override(tmp_path / "practice-A").get("stance") == "exploratory"


def test_queued_patches_accumulate_then_promote_together(tmp_path):
    """Multiple PATCHes before the boundary accumulate; one promote applies all."""
    from empirica.core import calibration_config as cc

    d = tmp_path / "prac"
    (d / ".empirica").mkdir(parents=True)
    cc.queue_pending(d, {"stance": "rigorous"})
    cc.queue_pending(d, {"preset": "security"})
    pending = cc.read_pending(d)
    assert pending.get("stance") == "rigorous" and pending.get("preset") == "security"
    cc.promote_pending(d)
    live = cc.read_override(d)
    assert live.get("stance") == "rigorous" and live.get("preset") == "security"
    assert cc.read_pending(d) == {}
