"""Tests for `empirica grounding-export --ai-id` — the per-practice grounding
snapshot primitive for mesh (L1/L2) tooling (autonomy's mesh-cohesion glance).

Emits {self_assessed_13, grounded_13, holistic_calibration_score, divergence,
evidence_count, last_updated, staleness_seconds, source_session_id} for a
practice present on THIS host; ok:false/not_local otherwise.
"""

from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import monitor_commands as mc

VECTORS = mc._GROUNDING_VECTORS


class _Belief:
    def __init__(self, mean, evidence_count, last_updated):
        self.mean = mean
        self.evidence_count = evidence_count
        self.last_updated = last_updated


class _FakeGroundedManager:
    """Returns grounded beliefs/divergence only for known ai_ids; zero-evidence
    defaults for unknown ones (mirrors the real manager's behaviour)."""

    def __init__(self, db, known):
        self._known = known

    def get_grounded_beliefs(self, ai_id):
        if ai_id in self._known:
            now = time.time()
            return {v: _Belief(0.75, 200, now) for v in VECTORS[:12]}
        return {v: _Belief(0.5, 0, None) for v in VECTORS}  # zero-evidence defaults

    def get_calibration_divergence(self, ai_id):
        if ai_id in self._known:
            return {v: {"gap": 0.1} for v in VECTORS[:12]}
        return {}


@pytest.fixture
def wired(monkeypatch):
    """In-memory sessions.db with one practice 'alpha' (session + POSTFLIGHT
    reflexes row), wired into the handler."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("CREATE TABLE sessions (session_id TEXT, ai_id TEXT)")
    cols = ", ".join(f"{v} REAL" for v in VECTORS)
    c.execute(f"CREATE TABLE reflexes (session_id TEXT, phase TEXT, timestamp REAL, {cols})")
    c.execute("INSERT INTO sessions VALUES ('s-alpha-1', 'alpha')")
    vals = ", ".join("0.8" for _ in VECTORS)
    c.execute(f"INSERT INTO reflexes VALUES ('s-alpha-1', 'POSTFLIGHT', {time.time()}, {vals})")
    conn.commit()

    fake_db = SimpleNamespace(conn=conn)
    monkeypatch.setattr(mc, "_GROUNDING_VECTORS", VECTORS)
    # SessionDatabase + GroundedCalibrationManager are imported INSIDE the handler.
    import empirica.core.post_test.grounded_calibration as gcal
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: fake_db)
    monkeypatch.setattr(gcal, "GroundedCalibrationManager", lambda db: _FakeGroundedManager(db, {"alpha"}))
    return conn


def _run(ai_id, output="json"):
    return mc.handle_grounding_export_command(SimpleNamespace(ai_id=ai_id, output=output))


def test_local_practice_full_shape(wired, capsys):
    rc = _run("alpha")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["ai_id"] == "alpha"
    assert len(out["self_assessed_13"]) == 13  # all 13 from the POSTFLIGHT row
    assert len(out["grounded_13"]) == 12  # the manager supplied 12
    assert set(out["self_assessed_13"]) <= set(VECTORS)
    assert 0.0 <= out["holistic_calibration_score"] <= 1.0
    assert out["divergence"] and out["evidence_count"] > 0
    assert out["source_session_id"] == "s-alpha-1"
    assert out["staleness_seconds"] is not None


def test_canonical_3form_resolves_to_basename(wired, capsys):
    _run("empirica.david.alpha")
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["ai_id"] == "alpha"


def test_unknown_ai_id_is_not_local(wired, capsys):
    rc = _run("ghost-practice")
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and out["reason"] == "not_local"


def test_missing_ai_id_errors(wired, capsys):
    rc = mc.handle_grounding_export_command(SimpleNamespace(output="json"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and "ai-id" in out["error"]


def test_human_output(wired, capsys):
    rc = _run("alpha", output="human")
    text = capsys.readouterr().out
    assert rc == 0 and "grounding-export alpha" in text and "holistic=" in text


def test_holistic_is_one_minus_mean_abs_gap(wired, capsys):
    # all gaps = 0.1 → holistic = 1 - 0.1 = 0.9
    _run("alpha")
    out = json.loads(capsys.readouterr().out)
    assert out["holistic_calibration_score"] == pytest.approx(0.9, abs=1e-6)
