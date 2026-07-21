"""SessionStart hook skips re-arming when a live tail-Monitor already exists.

Regression: SessionStart re-fires on compaction / new-session-init, and the AI
can't dedup across a compaction (the prior Monitor's task id is gone), so the
hook re-emitted arm instructions and each arm stacked another loop_fires tail
that delivered every wake event an extra time. `_has_live_log_tail` detects a
live (non-orphan) tail for the ai_id so the hook can skip re-arming.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

_HOOK = (
    Path(__file__).parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "session-monitor-arm.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("session_monitor_arm", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tail(ai_id: str, ppid: int = 12345) -> dict:
    return {
        "pid": 999,
        "ppid": ppid,
        "kind": "log_tail",
        "cmdline": (
            f'tail -F -n 0 /home/x/.empirica/loop_fires.log | grep -E --line-buffered \'"instance_id": "{ai_id}"\''
        ),
    }


_WALK = "empirica.core.cockpit.listener_processes.walk_listener_processes"


def test_live_tail_for_ai_id_is_detected():
    mod = _load()
    with patch(_WALK, return_value=[_tail("empirica")]):
        assert mod._has_live_log_tail("empirica") is True


def test_orphan_tail_does_not_count():
    # ppid == 1 → dead parent, delivers nowhere; handled by listener gc, not a
    # reason to skip arming an active session.
    mod = _load()
    with patch(_WALK, return_value=[_tail("empirica", ppid=1)]):
        assert mod._has_live_log_tail("empirica") is False


def test_tail_for_a_different_ai_id_is_ignored():
    mod = _load()
    with patch(_WALK, return_value=[_tail("empirica-workspace")]):
        assert mod._has_live_log_tail("empirica") is False


def test_no_tails_returns_false():
    mod = _load()
    with patch(_WALK, return_value=[]):
        assert mod._has_live_log_tail("empirica") is False


def test_loop_listen_kind_is_not_a_tail():
    # A loop_listen process is OS-supervised, not a session tail-Monitor.
    mod = _load()
    proc = {"pid": 5, "ppid": 5, "kind": "loop_listen", "cmdline": "empirica loop listen --instance empirica"}
    with patch(_WALK, return_value=[proc]):
        assert mod._has_live_log_tail("empirica") is False
