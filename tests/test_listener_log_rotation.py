"""Tests for loop_fires.log size cap + rotation (housekeeping fix #2).

The shared fires log is appended to by every per-ai_id listener. Without
rotation it grows unboundedly: disk bloat over weeks + slow `tail -F`
start in fresh Monitor arms. _rotate_fires_log_if_oversized caps growth.
"""

from __future__ import annotations

from empirica.core.loop_scheduler.listener import (
    _FIRES_LOG_MAX_LINES,
    _rotate_fires_log_if_oversized,
)


def test_no_rotation_when_under_cap(tmp_path):
    log = tmp_path / "loop_fires.log"
    log.write_text("\n".join(f"line-{i}" for i in range(100)) + "\n")
    before = log.read_text()
    _rotate_fires_log_if_oversized(log)
    assert log.read_text() == before, "should not touch a small log"


def test_no_rotation_at_exact_cap(tmp_path):
    log = tmp_path / "loop_fires.log"
    lines = [f"line-{i}" for i in range(_FIRES_LOG_MAX_LINES)]
    log.write_text("\n".join(lines) + "\n")
    before_lines = log.read_text().splitlines()
    _rotate_fires_log_if_oversized(log)
    after_lines = log.read_text().splitlines()
    # At the cap (not over), rotation should NOT trigger
    assert after_lines == before_lines


def test_rotates_by_rename_leaves_empty_live_log(tmp_path):
    """Over cap → rotate by RENAME: live log becomes empty, old lines move to .1.

    The live log MUST end empty (not a retained tail) — that's what stops
    tail -F Monitors from re-reading + re-firing the retained window as
    duplicate wake events (the replay-storm bug, David 2026-05-29).
    """
    log = tmp_path / "loop_fires.log"
    total = _FIRES_LOG_MAX_LINES + 500
    lines = [f"line-{i}" for i in range(total)]
    log.write_text("\n".join(lines) + "\n")

    _rotate_fires_log_if_oversized(log)

    # Live log exists but is empty — fresh inode for tail -F.
    assert log.exists()
    assert log.read_text() == "", "live log must be empty after rotate-by-rename"

    # All old lines preserved in the .1 sidecar for cockpit/history readers.
    rotated = tmp_path / "loop_fires.log.1"
    assert rotated.exists()
    rotated_lines = rotated.read_text().splitlines()
    assert len(rotated_lines) == total
    assert rotated_lines[0] == "line-0"
    assert rotated_lines[-1] == f"line-{total - 1}"


def test_rotation_overwrites_previous_sidecar(tmp_path):
    """A second rotation replaces .1 (bounded disk: only one sidecar kept)."""
    log = tmp_path / "loop_fires.log"
    rotated = tmp_path / "loop_fires.log.1"
    rotated.write_text("stale-old-rotation\n")

    total = _FIRES_LOG_MAX_LINES + 10
    log.write_text("\n".join(f"new-{i}" for i in range(total)) + "\n")
    _rotate_fires_log_if_oversized(log)

    rotated_lines = rotated.read_text().splitlines()
    assert "stale-old-rotation" not in rotated_lines
    assert rotated_lines[0] == "new-0"


def test_rotation_is_atomic_no_partial_file(tmp_path, monkeypatch):
    """If rotation crashes mid-write, log shouldn't be left half-written."""
    log = tmp_path / "loop_fires.log"
    total = _FIRES_LOG_MAX_LINES + 100
    log.write_text("\n".join(f"line-{i}" for i in range(total)) + "\n")
    original = log.read_text()

    # Sabotage os.replace to raise mid-rotation
    import os
    real_replace = os.replace

    def boom(*a, **kw):
        # Tmp file exists at this point but we fail before swap → log stays original
        raise OSError("simulated mid-rotation crash")

    monkeypatch.setattr(os, "replace", boom)

    # Should not raise — best-effort path swallows errors
    _rotate_fires_log_if_oversized(log)

    # Log unchanged (atomic-rename failure ≠ partial file)
    assert log.read_text() == original

    # Restore for cleanup
    monkeypatch.setattr(os, "replace", real_replace)


def test_missing_log_is_noop(tmp_path):
    log = tmp_path / "does-not-exist.log"
    _rotate_fires_log_if_oversized(log)  # should not raise
    assert not log.exists()
