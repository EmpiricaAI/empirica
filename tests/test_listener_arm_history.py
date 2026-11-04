"""`listener arm` keeps a history, not only the latest arm (goal b1c28f60; mesh-support's resumed-pane measurement).

`armed_at` was overwritten on every arm, so the registry held only the latest one per practice. "How long after a pane resumed did it first arm?" could
not be answered from it: mesh-support had to say its table was read with that limit. The state file now also carries `first_armed_at`, `arm_count`
and the last twenty arm times, so a first-arm-after-resume gap is a query over the file instead of a guess. `armed_at` keeps meaning the latest arm.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.cockpit_commands import handle_listener_arm_command, handle_listener_on_command


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("empirica.core.cockpit.listener_registry.EMPIRICA_DIR", tmp_path / ".empirica")
    monkeypatch.setattr(
        "empirica.core.cockpit.notification_channels.resolve_orchestration_events_topic",
        lambda ai_id: f"ntfy:empirica-orchestration-events?tags={ai_id}",
    )
    (tmp_path / ".empirica").mkdir()

    def args(**kw):
        base = {
            "ai_id": "myai",
            "name": None,
            "topic": None,
            "instance": "test_instance",
            "task_id": None,
            "output": "json",
        }
        base.update(kw)
        return types.SimpleNamespace(**base)

    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=False):
        handle_listener_on_command(args())
    path = tmp_path / ".empirica" / "listener_active_test_instance_myai-inbox.json"
    ticks = iter(float(t) for t in range(1_000, 2_000, 10))
    monkeypatch.setattr("time.time", lambda: next(ticks))

    def arm(task_id):
        assert handle_listener_arm_command(args(task_id=task_id)) == 0
        return json.loads(path.read_text())

    return types.SimpleNamespace(arm=arm, path=path)


def test_the_first_arm_sets_first_armed_at_and_a_count_of_one(state):
    data = state.arm("tk1")
    assert data["first_armed_at"] == data["armed_at"] and data["arm_count"] == 1
    assert data["arm_history"] == [data["armed_at"]]


def test_a_rearm_moves_armed_at_but_keeps_the_first(state):
    first = state.arm("tk1")
    second = state.arm("tk2")
    assert second["first_armed_at"] == first["armed_at"]
    assert second["armed_at"] > first["armed_at"]
    assert second["arm_count"] == 2 and second["arm_history"] == [first["armed_at"], second["armed_at"]]
    assert second["monitor_task_id"] == "tk2"


def test_the_history_keeps_only_the_last_twenty_arms_but_the_count_and_the_first_survive(state):
    first = state.arm("tk0")
    for i in range(1, 25):
        data = state.arm(f"tk{i}")
    assert len(data["arm_history"]) == 20 and data["arm_count"] == 25
    assert data["first_armed_at"] == first["armed_at"]
    assert data["arm_history"][-1] == data["armed_at"]


def test_a_state_file_from_before_this_change_gets_a_history_from_its_next_arm(state):
    data = json.loads(state.path.read_text())
    for key in ("first_armed_at", "arm_count", "arm_history"):
        data.pop(key, None)
    state.path.write_text(json.dumps(data))
    after = state.arm("tk1")
    assert after["arm_count"] == 1 and after["first_armed_at"] == after["armed_at"]
