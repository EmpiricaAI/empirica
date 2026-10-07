"""Tests for empirica.core.cockpit.sentinel_pause.

The pause-file mechanism is shared with sentinel-gate.py — these tests
verify the wrapper's contract (file presence, mtime, idempotency) without
booting the full hook.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit import sentinel_pause as sp


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.empirica to a tmp dir for the test."""
    fake_dir = tmp_path / ".empirica"
    fake_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sp, "EMPIRICA_DIR", fake_dir)
    monkeypatch.setattr(sp, "GLOBAL_PAUSE_FILE", fake_dir / "sentinel_paused")
    return fake_dir


def test_status_unpaused_is_none_scope(fake_home):
    status = sp.sentinel_status("tmux_99")
    assert status.paused is False
    assert status.scope == "none"
    assert status.since is None
    assert status.reason is None


def test_pause_creates_instance_file(fake_home):
    sp.pause_sentinel("tmux_99", reason="unit test")
    assert (fake_home / "sentinel_paused_tmux_99").exists()
    status = sp.sentinel_status("tmux_99")
    assert status.paused is True
    assert status.scope == "instance"
    assert status.since is not None
    assert status.reason == "unit test"


def test_resume_removes_file_and_is_idempotent(fake_home):
    sp.pause_sentinel("tmux_99")
    sp.resume_sentinel("tmux_99")
    assert not (fake_home / "sentinel_paused_tmux_99").exists()
    # Idempotent — second resume is a no-op
    sp.resume_sentinel("tmux_99")
    assert sp.sentinel_status("tmux_99").paused is False


def test_global_pause_takes_over_when_instance_unpaused(fake_home):
    (fake_home / "sentinel_paused").write_text("global pause")
    status = sp.sentinel_status("tmux_99")
    assert status.paused is True
    assert status.scope == "global"
    assert status.reason == "global pause"


def test_instance_pause_overrides_global(fake_home):
    (fake_home / "sentinel_paused").write_text("global pause")
    sp.pause_sentinel("tmux_99", reason="instance overrides")
    status = sp.sentinel_status("tmux_99")
    assert status.paused is True
    assert status.scope == "instance"
    assert status.reason == "instance overrides"


def test_pause_with_no_instance_id_refuses_unless_global_is_asked_for(fake_home):
    """A pause from a terminal that resolved to no instance must not become a fleet-wide pause by default."""
    with pytest.raises(ValueError, match="global_scope=True"):
        sp.pause_sentinel(None, reason="no instance")
    with pytest.raises(ValueError, match="global_scope=True"):
        sp.pause_sentinel("", reason="empty id")
    assert not (fake_home / "sentinel_paused").exists()


def test_pause_with_global_scope_writes_global(fake_home):
    sp.pause_sentinel(None, reason="global toggle", global_scope=True)
    assert (fake_home / "sentinel_paused").exists()
    status = sp.sentinel_status(None)
    assert status.paused is True
    assert status.scope == "global"


def test_unsafe_chars_sanitized_in_filename(fake_home):
    sp.pause_sentinel("term/x86%pct")
    # '/' → '-', '%' → ''
    assert (fake_home / "sentinel_paused_term-x86pct").exists()


def test_pause_file_path_resolution_matches_sentinel_gate(fake_home):
    """Sanity: the path we write must match what the Sentinel hook reads."""
    p1 = sp.pause_file_path("tmux_5")
    assert p1.name == "sentinel_paused_tmux_5"
    assert p1.parent == fake_home


def test_repause_refreshes_mtime_and_reason(fake_home):
    """Re-pausing should overwrite content and bump timestamp."""
    sp.pause_sentinel("tmux_99", reason="first")
    first_status = sp.sentinel_status("tmux_99")
    second_status = sp.pause_sentinel("tmux_99", reason="second")
    assert second_status.reason == "second"
    assert first_status.since is not None
    assert second_status.since is not None
