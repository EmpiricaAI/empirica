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


# ── session_pid: the daemon's liveness anchor (da902b30) ────────────────────


def test_write_stores_session_pid(fake_home):
    rec = pp.write_presence("cc-pid", practice_ai_id="empirica", session_pid=4242)
    assert rec["session_pid"] == 4242
    assert pp.read_presence("cc-pid")["session_pid"] == 4242


def test_session_pid_preserved_on_none_rewrite(fake_home):
    # The per-turn refresh / a daemon touch rewrites presence WITHOUT re-supplying
    # the pid. The anchor must survive — else the daemon loses its liveness probe.
    pp.write_presence("cc-keep", practice_ai_id="empirica", session_pid=777)
    pp.write_presence("cc-keep", practice_ai_id="empirica", status="idle")  # no session_pid
    rec = pp.read_presence("cc-keep")
    assert rec["session_pid"] == 777  # anchor survived the churn
    assert rec["status"] == "idle"


def test_explicit_session_pid_overrides_preserved(fake_home):
    pp.write_presence("cc-ov", practice_ai_id="empirica", session_pid=1)
    pp.write_presence("cc-ov", practice_ai_id="empirica", session_pid=2)
    assert pp.read_presence("cc-ov")["session_pid"] == 2


def test_legacy_record_has_null_session_pid(fake_home):
    rec = pp.write_presence("cc-legacy", practice_ai_id="empirica")
    assert rec["session_pid"] is None


# ── status-aware staleness: blocked/paused get the longer grace window ───────


def test_status_aware_staleness_blocked_grace(fake_home):
    # Both back-dated to the same age: just past the active window, but within the
    # blocked grace window. Active goes stale; blocked survives.
    age = pp.DEFAULT_STALE_AFTER_S + 60
    assert age < pp._blocked_stale_after()  # precondition for the test to be meaningful
    for sid, status in (("cc-act", "active"), ("cc-blk", "blocked")):
        pp.write_presence(sid, practice_ai_id="empirica", status=status)
        p = pp.presence_path(sid)
        d = json.loads(p.read_text())
        d["last_heartbeat"] = time.time() - age
        p.write_text(json.dumps(d))
    live = {r["claude_session_id"] for r in pp.list_presence("empirica")}
    assert live == {"cc-blk"}  # blocked survives the active-window cutoff, active doesn't


def test_blocked_grace_env_tunable(fake_home, monkeypatch):
    monkeypatch.setenv("EMPIRICA_PRESENCE_BLOCKED_STALE_S", "10")
    pp.write_presence("cc-blk", practice_ai_id="empirica", status="blocked")
    p = pp.presence_path("cc-blk")
    d = json.loads(p.read_text())
    d["last_heartbeat"] = time.time() - 30  # past the now-tightened 10s grace
    p.write_text(json.dumps(d))
    assert pp.list_presence("empirica") == []  # blocked but past the tuned window → stale


def test_explicit_stale_after_forces_flat_threshold(fake_home):
    # Passing stale_after overrides status-awareness with a single flat threshold.
    pp.write_presence("cc-b", practice_ai_id="empirica", status="blocked")
    p = pp.presence_path("cc-b")
    d = json.loads(p.read_text())
    d["last_heartbeat"] = time.time() - 10
    p.write_text(json.dumps(d))
    assert pp.list_presence("empirica", stale_after=5) == []  # flat 5s threshold → stale
    assert len(pp.list_presence("empirica")) == 1  # status-aware default → still fresh


# ── refresh_live_presence: the daemon liveness re-stamp (the core fix) ───────


def test_refresh_live_presence_alive_dead_nopid(fake_home):
    import os
    import subprocess

    pp.write_presence("cc-alive", practice_ai_id="empirica", session_pid=os.getpid())
    proc = subprocess.Popen(["true"])  # a process that exits immediately
    proc.wait()  # reaped → its PID is now dead (not reused within the test window)
    pp.write_presence("cc-dead", practice_ai_id="empirica", session_pid=proc.pid)
    pp.write_presence("cc-nopid", practice_ai_id="empirica")  # legacy: no anchor

    old = time.time() - 1000
    for sid in ("cc-alive", "cc-dead", "cc-nopid"):
        p = pp.presence_path(sid)
        d = json.loads(p.read_text())
        d["last_heartbeat"] = old
        p.write_text(json.dumps(d))

    counts = pp.refresh_live_presence()
    assert counts == {"refreshed": 1, "alive": 1, "dead": 1, "no_pid": 1}
    assert pp.read_presence("cc-alive")["last_heartbeat"] > old  # alive → re-stamped
    assert pp.read_presence("cc-dead")["last_heartbeat"] == old  # dead → untouched
    assert pp.read_presence("cc-nopid")["last_heartbeat"] == old  # no anchor → untouched


def test_refresh_keeps_blocked_alive_session_nonstale(fake_home):
    import os

    # The whole point: a blocked, ALIVE session back-dated past the active window
    # would be dropped — refresh re-stamps it so it stays on the mesh.
    pp.write_presence("cc-blk", practice_ai_id="empirica", status="blocked", session_pid=os.getpid())
    p = pp.presence_path("cc-blk")
    d = json.loads(p.read_text())
    d["last_heartbeat"] = time.time() - (pp.DEFAULT_STALE_AFTER_S + 30)
    p.write_text(json.dumps(d))

    pp.refresh_live_presence()
    # fresh again — present even under a strict flat active-window check
    assert len(pp.list_presence("empirica", stale_after=pp.DEFAULT_STALE_AFTER_S)) == 1


def test_pid_alive_probe(fake_home):
    import os
    import subprocess

    assert pp._pid_alive(os.getpid()) is True
    assert pp._pid_alive(0) is False
    assert pp._pid_alive(-1) is False
    proc = subprocess.Popen(["true"])
    proc.wait()
    assert pp._pid_alive(proc.pid) is False
