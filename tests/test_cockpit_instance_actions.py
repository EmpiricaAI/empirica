"""Tests for empirica.core.cockpit.instance_actions.

Covers label CRUD, forget cleanup, and kill resolution. Kill is tested
without actually signaling — we monkeypatch os.kill / subprocess.run so
the test can verify the chosen method without side effects.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from empirica.core.cockpit import instance_actions as ia


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake_dir = tmp_path / ".empirica"
    fake_dir.mkdir(parents=True)
    (fake_dir / "instance_projects").mkdir()
    (fake_dir / "tty_sessions").mkdir()
    monkeypatch.setattr(ia, "EMPIRICA_DIR", fake_dir)
    monkeypatch.setattr(ia, "TTY_SESSIONS_DIR", fake_dir / "tty_sessions")
    return fake_dir


# ─── label ─────────────────────────────────────────────────────────────────


def test_label_set_get_clear_round_trip(fake_home):
    assert ia.get_label("tmux_5") is None
    ia.set_label("tmux_5", "outreach")
    assert ia.get_label("tmux_5") == "outreach"
    ia.set_label("tmux_5", None)
    assert ia.get_label("tmux_5") is None


def test_label_strips_whitespace_and_extra_lines(fake_home):
    ia.set_label("tmux_5", "  multi\nline\n")
    assert ia.get_label("tmux_5") == "multi"


def test_label_filename_sanitizes_unsafe_chars(fake_home):
    ia.set_label("term/x86%pct", "foo")
    assert (fake_home / "instance_label_term-x86pct").exists()


# ─── forget ────────────────────────────────────────────────────────────────


def _seed_instance_files(home: Path, instance_id: str) -> list[str]:
    """Create the full state-file footprint for an instance."""
    files = [
        home / "instance_projects" / f"{instance_id}.json",
        home / f"sentinel_paused_{instance_id}",
        # loops_{id}.json intentionally NOT seeded — loops are practice-keyed now
        # and are NOT forgotten with a seat (see _FORGET_PATTERNS).
        home / f"active_session_{instance_id}",
        home / f"hook_counters_{instance_id}.json",
        home / f"context_usage_{instance_id}.json",
        home / f"cortex_remote_cache_{instance_id}.json",
        home / f"pre_tx_calls_{instance_id}.json",
        home / f"instance_label_{instance_id}",
        home / f"loop_paused_{instance_id}_loop-a",
        home / f"loop_paused_{instance_id}_loop-b",
    ]
    for path in files:
        path.write_text("{}" if path.suffix == ".json" else "")
    return [str(f.relative_to(home)) for f in files]


def test_forget_removes_all_state_files(fake_home):
    expected = _seed_instance_files(fake_home, "tmux_42")
    result = ia.forget_instance("tmux_42")
    assert sorted(result.removed) == sorted(expected)
    assert result.skipped == []
    # Verify no leftover files
    leftovers = sorted(p.name for p in fake_home.iterdir() if "tmux_42" in p.name)
    assert leftovers == []


def test_forget_idempotent_on_dead_instance(fake_home):
    result = ia.forget_instance("never-existed")
    assert result.removed == []
    assert result.skipped == []


def test_forget_does_not_touch_other_instances(fake_home):
    _seed_instance_files(fake_home, "tmux_42")
    _seed_instance_files(fake_home, "tmux_99")
    ia.forget_instance("tmux_42")
    survivors = sorted(p.name for p in fake_home.iterdir() if "tmux_99" in p.name)
    assert len(survivors) >= 9, f"expected tmux_99 files to survive, got {survivors}"


def test_forget_requires_instance_id(fake_home):
    with pytest.raises(ValueError):
        ia.forget_instance("")


# ─── kill ──────────────────────────────────────────────────────────────────


def test_kill_tmux_uses_kill_pane(fake_home, monkeypatch):
    """tmux_N instance should invoke `tmux kill-pane -t %N`."""
    captured = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(ia.subprocess, "run", fake_run)
    monkeypatch.setattr(ia.shutil, "which", lambda _: "/usr/bin/tmux")

    result = ia.kill_instance("tmux_42")
    assert result.success is True
    assert result.method == "tmux"
    assert captured["cmd"] == ["tmux", "kill-pane", "-t", "%42"]


def test_kill_tmux_when_tmux_not_installed_fails_clean(fake_home, monkeypatch):
    monkeypatch.setattr(ia.shutil, "which", lambda _: None)
    result = ia.kill_instance("tmux_42")
    assert result.success is False
    assert "not found in PATH" in result.detail


def test_kill_tmux_kill_pane_failure_surfaces_stderr(fake_home, monkeypatch):
    class FakeCompleted:
        returncode = 1
        stderr = "can't find pane: %42"

    monkeypatch.setattr(ia.subprocess, "run", lambda *a, **k: FakeCompleted())
    monkeypatch.setattr(ia.shutil, "which", lambda _: "/usr/bin/tmux")

    result = ia.kill_instance("tmux_42")
    assert result.success is False
    assert "can't find pane" in result.detail


def test_kill_non_tmux_with_no_pid_returns_unreachable(fake_home):
    # No instance_projects entry, no tty_sessions entry → no PID at all
    result = ia.kill_instance("term-pts-7")
    assert result.success is False
    assert result.method == "unreachable"
    assert "no tracked PID" in result.detail


def test_kill_non_tmux_signals_ppid(fake_home, monkeypatch):
    """Should signal the captured ppid (long-lived parent), not the dead pid."""
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(
        json.dumps({"pid": 12345, "ppid": 67890, "tty_key": "pts-7"})
    )
    sent = {}
    monkeypatch.setattr(ia.os, "kill", lambda pid, sig: sent.setdefault("args", (pid, sig)))
    monkeypatch.setattr(ia, "_process_alive", lambda _pid: True)

    result = ia.kill_instance("term-pts-7", force=True)
    assert result.success is True
    assert result.method == "sigkill"
    assert sent["args"] == (67890, signal.SIGKILL)


def test_kill_already_dead_pid_reports_success(fake_home, monkeypatch):
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    monkeypatch.setattr(ia, "_process_alive", lambda _pid: False)
    result = ia.kill_instance("term-pts-7")
    assert result.success is True
    assert "already dead" in result.detail


def test_kill_falls_back_to_tty_sessions_when_instance_projects_missing(fake_home, monkeypatch):
    (fake_home / "tty_sessions" / "pts-7.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    # Stub instance_projects with only the tty_key linkage, no pids
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"tty_key": "pts-7"}))
    sent = {}
    monkeypatch.setattr(ia.os, "kill", lambda pid, sig: sent.setdefault("args", (pid, sig)))
    monkeypatch.setattr(ia, "_process_alive", lambda _pid: True)

    result = ia.kill_instance("term-pts-7", force=True)
    assert sent["args"] == (67890, signal.SIGKILL)
    assert result.success is True


def test_a_live_pid_in_the_tty_record_beats_a_dead_ppid_in_the_instance_record(fake_home, monkeypatch):
    """The instance record answered first with its dead ppid 'as a hint' and the tty record naming the live process was never read."""
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(
        json.dumps({"pid": 111, "ppid": 222, "tty_key": "pts-7"})
    )
    (fake_home / "tty_sessions" / "pts-7.json").write_text(json.dumps({"pid": 333, "ppid": 444}))
    sent = {}
    monkeypatch.setattr(ia.os, "kill", lambda pid, sig: sent.setdefault("args", (pid, sig)))
    monkeypatch.setattr(ia, "_process_alive", lambda pid: pid == 444)
    result = ia.kill_instance("term-pts-7", force=True)
    assert sent["args"] == (444, signal.SIGKILL) and result.success is True


def test_when_every_pid_is_dead_the_instance_records_ppid_is_reported_dead(fake_home, monkeypatch):
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(
        json.dumps({"pid": 111, "ppid": 222, "tty_key": "pts-7"})
    )
    (fake_home / "tty_sessions" / "pts-7.json").write_text(json.dumps({"pid": 333, "ppid": 444}))
    monkeypatch.setattr(ia, "_process_alive", lambda _pid: False)
    result = ia.kill_instance("term-pts-7")
    assert result.success is True and result.pid == 222 and "already dead" in result.detail


def test_an_unreadable_or_non_object_record_is_skipped_not_fatal(fake_home, monkeypatch):
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"tty_key": "pts-7"}))
    (fake_home / "tty_sessions" / "pts-7.json").write_text("[1, 2]")  # valid JSON, not an object
    assert ia.kill_instance("term-pts-7").method == "unreachable"


# ─── kill: a pid is only a number ─────────────────────────────────────────
# David's ruling 2026-10-06: when the pid's start time cannot be verified, refuse unless --force. A pid KNOWN to belong to another
# process is never signalled.


def _seed_ppid(home, **extra):
    record = {"pid": 12345, "ppid": 67890, "tty_key": "pts-7", **extra}
    (home / "instance_projects" / "term-pts-7.json").write_text(json.dumps(record))


def _spy_kill(monkeypatch):
    sent = {}
    monkeypatch.setattr(ia.os, "kill", lambda pid, sig: sent.setdefault("args", (pid, sig)))
    monkeypatch.setattr(ia, "_process_alive", lambda _pid: True)
    return sent


def test_kill_signals_a_pid_whose_start_time_matches_the_record(fake_home, monkeypatch):
    _seed_ppid(fake_home, ppid_create_time=1000.0)
    sent = _spy_kill(monkeypatch)
    monkeypatch.setattr(ia, "_psutil_create_time", lambda _pid: 1000.4)
    result = ia.kill_instance("term-pts-7")
    assert result.success is True and sent["args"][0] == 67890


@pytest.mark.parametrize("force", [False, True])
def test_kill_never_signals_a_recycled_pid_even_with_force(fake_home, monkeypatch, force):
    _seed_ppid(fake_home, ppid_create_time=1000.0)
    sent = _spy_kill(monkeypatch)
    monkeypatch.setattr(ia, "_psutil_create_time", lambda _pid: 2000.0)
    result = ia.kill_instance("term-pts-7", force=force)
    assert result.success is False and result.method == "unreachable"
    assert "no longer this instance's process" in result.detail
    assert "args" not in sent


def test_kill_refuses_when_no_start_time_was_recorded_unless_forced(fake_home, monkeypatch):
    _seed_ppid(fake_home)  # a record from before ppid_create_time existed
    sent = _spy_kill(monkeypatch)
    refused = ia.kill_instance("term-pts-7")
    assert refused.success is False and "--force" in refused.detail and "no start time was recorded" in refused.detail
    assert "args" not in sent
    forced = ia.kill_instance("term-pts-7", force=True)
    assert forced.success is True and sent["args"] == (67890, signal.SIGKILL)


def test_kill_refuses_when_the_live_start_time_cannot_be_read_unless_forced(fake_home, monkeypatch):
    _seed_ppid(fake_home, ppid_create_time=1000.0)
    sent = _spy_kill(monkeypatch)
    monkeypatch.setattr(ia, "_psutil_create_time", lambda _pid: None)  # psutil missing or unreadable
    refused = ia.kill_instance("term-pts-7")
    assert refused.success is False and "psutil" in refused.detail and "args" not in sent
    assert ia.kill_instance("term-pts-7", force=True).success is True


def test_kill_does_not_trust_a_recorded_start_time_for_the_hook_pid(fake_home, monkeypatch):
    """ppid_create_time describes the ppid only: when the chosen pid is the hook's pid it is unverifiable, not 'matching'."""
    _seed_ppid(fake_home, ppid_create_time=1000.0)
    sent = {}
    monkeypatch.setattr(ia.os, "kill", lambda pid, sig: sent.setdefault("args", (pid, sig)))
    monkeypatch.setattr(ia, "_process_alive", lambda pid: pid == 12345)  # only the hook pid is alive
    monkeypatch.setattr(ia, "_psutil_create_time", lambda _pid: 1000.0)
    result = ia.kill_instance("term-pts-7")
    assert result.success is False and "no start time was recorded" in result.detail and "args" not in sent


def test_kill_requires_instance_id(fake_home):
    with pytest.raises(ValueError):
        ia.kill_instance("")


# ── wake_instance ─────────────────────────────────────────────────────────


def test_wake_tmux_sends_space_enter(fake_home, monkeypatch):
    """tmux instance → wake sends Space + Enter via tmux send-keys."""
    sent = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(args, **kw):
        sent["args"] = args
        return _Result()

    monkeypatch.setattr(ia.shutil, "which", lambda b: "/usr/bin/tmux" if b == "tmux" else None)
    monkeypatch.setattr(ia.subprocess, "run", fake_run)

    result = ia.wake_instance("tmux_5")
    assert result.success is True
    assert result.method == "tmux-send-keys"
    # Verify Space + Enter were sent
    assert sent["args"][:3] == ["tmux", "send-keys", "-t"]
    assert sent["args"][3] == "%5"
    assert " " in sent["args"] and "Enter" in sent["args"]


def test_wake_non_tmux_is_unreachable(fake_home):
    """Non-tmux instances can't be wake'd — graceful degrade, pending file
    still fires on next manual user prompt."""
    result = ia.wake_instance("x11:104427765653088")
    assert result.success is False
    assert result.method == "unreachable"
    assert "next user prompt" in result.detail


def test_wake_when_tmux_not_installed_fails_clean(fake_home, monkeypatch):
    monkeypatch.setattr(ia.shutil, "which", lambda _: None)
    result = ia.wake_instance("tmux_5")
    assert result.success is False
    assert result.method == "unreachable"
    assert "tmux binary not found" in result.detail


def test_wake_send_keys_failure_surfaces_stderr(fake_home, monkeypatch):
    class _Result:
        returncode = 1
        stderr = "can't find pane: %99"

    monkeypatch.setattr(ia.shutil, "which", lambda b: "/usr/bin/tmux" if b == "tmux" else None)
    monkeypatch.setattr(ia.subprocess, "run", lambda *a, **k: _Result())

    result = ia.wake_instance("tmux_99")
    assert result.success is False
    assert result.method == "tmux-send-keys"
    assert "can't find pane" in result.detail


def test_wake_requires_instance_id(fake_home):
    with pytest.raises(ValueError):
        ia.wake_instance("")


# ─── rebind ──────────────────────────────────────────────────────────────


def _write_inst(fake_home, iid, data):
    (fake_home / "instance_projects" / f"{iid}.json").write_text(json.dumps(data))


def test_rebind_no_record_fails(fake_home):
    r = ia.rebind_instance("tmux_9")
    assert r.success is False
    assert "no instance record" in r.detail


def test_rebind_no_live_proc_fails(fake_home, monkeypatch):
    _write_inst(fake_home, "tmux_5", {"project_path": "/p", "pid": 1, "ppid": 2})
    monkeypatch.setattr("empirica.core.cockpit.liveness.live_claude_pids_by_instance", lambda: {})
    r = ia.rebind_instance("tmux_5")
    assert r.success is False
    assert "no live claude" in r.detail


def test_rebind_psutil_unavailable_fails(fake_home, monkeypatch):
    _write_inst(fake_home, "tmux_5", {"pid": 1})
    monkeypatch.setattr("empirica.core.cockpit.liveness.live_claude_pids_by_instance", lambda: None)
    r = ia.rebind_instance("tmux_5")
    assert r.success is False
    assert "psutil" in r.detail


def test_rebind_restamps_pid_and_preserves_fields(fake_home, monkeypatch):
    _write_inst(
        fake_home,
        "tmux_5",
        {"project_path": "/proj", "instance_id": "tmux_5", "pid": 1, "ppid": 2, "ppid_create_time": 111.0},
    )
    monkeypatch.setattr(
        "empirica.core.cockpit.liveness.live_claude_pids_by_instance",
        lambda: {"tmux_5": (9999, 222.0)},
    )
    r = ia.rebind_instance("tmux_5")
    assert r.success is True
    assert r.pid == 9999
    data = json.loads((fake_home / "instance_projects" / "tmux_5.json").read_text())
    assert data["pid"] == 9999
    assert data["ppid"] == 9999  # live claude proc IS the durable parent
    assert data["ppid_create_time"] == 222.0
    assert data["project_path"] == "/proj"  # other fields preserved
    assert "rebound_at" in data
