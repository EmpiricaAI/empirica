"""Tests for the practitioner-presence substrate (B2a).

File-per-practitioner presence keyed on the durable claude_session_id.
"""

from __future__ import annotations

import json
import time

import pytest

from empirica.core import practitioner_presence as pp


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    d = tmp_path / ".empirica"
    d.mkdir()
    monkeypatch.setattr(pp, "EMPIRICA_DIR", d)
    return d


def test_write_read_roundtrip(fake_home):
    rec = pp.write_presence(
        "cc-abc",
        practice_ai_id="empirica",
        location="tmux_8",
        active_transaction_id="tx1",
        empirica_session_id="es1",
    )
    assert rec["claude_session_id"] == "cc-abc"
    assert rec["practice_ai_id"] == "empirica"
    assert rec["status"] == "active"
    assert rec["last_heartbeat"] > 0
    got = pp.read_presence("cc-abc")
    assert got["location"] == "tmux_8"
    assert got["active_transaction_id"] == "tx1"
    assert got["empirica_session_id"] == "es1"
    assert got["practitioner_id"] is None  # nullable seam


def test_keyed_on_claude_session_id_not_empirica(fake_home):
    # The durable key is claude_session_id; the empirica session id is a churning
    # attribute. A compaction rotates the empirica id but it's the SAME practitioner.
    pp.write_presence("cc-1", practice_ai_id="empirica", empirica_session_id="es-A")
    pp.write_presence("cc-1", practice_ai_id="empirica", empirica_session_id="es-B")
    assert len(pp.list_presence(include_stale=True)) == 1  # one practitioner, not two
    assert pp.read_presence("cc-1")["empirica_session_id"] == "es-B"


def test_invalid_status_rejected(fake_home):
    with pytest.raises(ValueError, match="invalid status"):
        pp.write_presence("cc-x", practice_ai_id="empirica", status="zombie")


def test_clear_is_idempotent(fake_home):
    pp.write_presence("cc-c", practice_ai_id="empirica")
    assert pp.clear_presence("cc-c") is True
    assert pp.read_presence("cc-c") is None
    assert pp.clear_presence("cc-c") is False


def test_read_missing_is_none(fake_home):
    assert pp.read_presence("nope") is None


def test_list_scoped_by_practice(fake_home):
    pp.write_presence("cc-e1", practice_ai_id="empirica")
    pp.write_presence("cc-e2", practice_ai_id="empirica")
    pp.write_presence("cc-c1", practice_ai_id="cortex")
    assert {r["claude_session_id"] for r in pp.list_presence("empirica")} == {"cc-e1", "cc-e2"}
    assert {r["claude_session_id"] for r in pp.list_presence("cortex")} == {"cc-c1"}


def test_stale_excluded_by_default(fake_home):
    pp.write_presence("cc-live", practice_ai_id="empirica")
    pp.write_presence("cc-old", practice_ai_id="empirica")
    # back-date one record past the stale threshold
    p = pp.presence_path("cc-old")
    data = json.loads(p.read_text())
    data["last_heartbeat"] = time.time() - (pp.DEFAULT_STALE_AFTER_S + 60)
    p.write_text(json.dumps(data))

    assert {r["claude_session_id"] for r in pp.list_presence("empirica")} == {"cc-live"}
    everyone = pp.list_presence("empirica", include_stale=True)
    assert {r["claude_session_id"] for r in everyone} == {"cc-live", "cc-old"}
    old = next(r for r in everyone if r["claude_session_id"] == "cc-old")
    assert old["stale"] is True


def test_resolve_practitioners_carries_gate_state(fake_home):
    pp.write_presence("cc-1", practice_ai_id="empirica", location="tmux_8", status="active")
    pp.write_presence(
        "cc-2", practice_ai_id="empirica", location="tmux_9", status="blocked", pending_question="which db?"
    )
    by_id = {r["claude_session_id"]: r for r in pp.resolve_practitioners("empirica")}
    assert by_id["cc-2"]["status"] == "blocked"
    assert by_id["cc-2"]["pending_question"] == "which db?"
    assert by_id["cc-1"]["location"] == "tmux_8"


def test_safe_filename(fake_home):
    pp.write_presence("a/b%c", practice_ai_id="empirica")
    assert pp.presence_path("a/b%c").name == "practitioner_presence_a-bc.json"
    assert pp.read_presence("a/b%c") is not None
