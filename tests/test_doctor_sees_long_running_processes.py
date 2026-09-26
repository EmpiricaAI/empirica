"""doctor flags long-running empirica processes older than the code on disk.

Every other deploy-gap check compares artifacts on disk. Measured 2026-09-21:
`--deploy-gaps` passed every check while `empirica tui` had been running for 47
days on August code (cortex's handover).
"""

from __future__ import annotations

import os
import time

from empirica.cli.command_handlers import doctor


def _pkg(tmp_path, mtime):
    pkg = tmp_path / "empirica"
    pkg.mkdir()
    f = pkg / "mod.py"
    f.write_text("x = 1\n")
    os.utime(f, (mtime, mtime))
    return pkg


def test_a_process_started_before_the_code_changed_is_flagged(tmp_path):
    now = time.time()
    pkg = _pkg(tmp_path, now - 3600)
    procs = [
        {"pid": 11, "create_time": now - 47 * 86400, "cmd": "empirica tui"},
        {"pid": 12, "create_time": now - 60, "cmd": "empirica loop listen --instance x"},
    ]
    check = doctor.check_long_running_processes(pkg, procs)
    assert check.status == doctor.WARN
    assert [p["pid"] for p in check.data["stale"]] == [11]
    assert "empirica tui" in check.detail and "47.0d" in check.detail


def test_oldest_is_listed_first(tmp_path):
    now = time.time()
    pkg = _pkg(tmp_path, now - 60)
    procs = [
        {"pid": 21, "create_time": now - 2 * 86400, "cmd": "empirica serve"},
        {"pid": 22, "create_time": now - 40 * 86400, "cmd": "empirica tui"},
    ]
    check = doctor.check_long_running_processes(pkg, procs)
    assert [p["pid"] for p in check.data["stale"]] == [22, 21]


def test_processes_newer_than_the_code_pass(tmp_path):
    """Positive control: the check is not simply always warning."""
    now = time.time()
    pkg = _pkg(tmp_path, now - 86400)
    procs = [{"pid": 31, "create_time": now - 60, "cmd": "empirica tui"}]
    assert doctor.check_long_running_processes(pkg, procs).status == doctor.PASS


def test_this_process_is_never_flagged(tmp_path):
    now = time.time()
    pkg = _pkg(tmp_path, now)
    procs = [{"pid": os.getpid(), "create_time": now - 3600, "cmd": "empirica doctor"}]
    assert doctor.check_long_running_processes(pkg, procs).status == doctor.PASS
