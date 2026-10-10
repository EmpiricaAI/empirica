"""The SessionStart hook asks for the arming payload without writing the state it later reads as intent.

`session-monitor-arm.py` called `empirica listener on`, which registers the listener and OVERWRITES
`listener_active_<instance>_<name>.json` (monitor_task_id back to null). The hook then reads that file
(`_has_active_listener_intent`) as proof a user wanted a listener, so its own query manufactured the evidence it
checks. `listener on --dry-run` returns the payload and writes nothing; `listener arm` creates the record when the
first real arm finds none.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.cockpit_commands import handle_listener_arm_command, handle_listener_on_command

HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "session-monitor-arm.py"
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("empirica.core.cockpit.listener_registry.EMPIRICA_DIR", tmp_path / ".empirica")
    monkeypatch.setattr(
        "empirica.core.cockpit.notification_channels.resolve_orchestration_events_topic",
        lambda ai_id: f"ntfy:empirica-orchestration-events?tags={ai_id}",
    )
    (tmp_path / ".empirica").mkdir()
    return tmp_path


def _args(**overrides):
    base = {
        "ai_id": "empirica",
        "name": None,
        "topic": None,
        "instance": "test_instance",
        "task_id": None,
        "output": "json",
        "dry_run": False,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _state_files(home):
    return sorted((home / ".empirica").glob("listener_active_*.json"))


def _registry_files(home):
    return sorted(p for p in (home / ".empirica").rglob("*") if p.is_file() and "listener_active_" not in p.name)


def test_a_dry_run_returns_the_payload_and_writes_nothing(home, capsys):
    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=True):
        rc = handle_listener_on_command(_args(dry_run=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["status"] == "persistent_service_tail_session"
    assert out["next_step"]["tool"] == "Monitor" and "tail -F" in out["next_step"]["args"]["command"]
    assert out["dry_run"] is True
    assert _state_files(home) == [] and _registry_files(home) == []


def test_a_dry_run_leaves_an_existing_record_untouched(home, capsys):
    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=False):
        handle_listener_on_command(_args())
        capsys.readouterr()
        (path,) = _state_files(home)
        record = json.loads(path.read_text())
        record["monitor_task_id"] = "task-live"
        path.write_text(json.dumps(record))
        handle_listener_on_command(_args(dry_run=True))
    assert json.loads(path.read_text())["monitor_task_id"] == "task-live"


def test_a_real_on_still_registers_and_writes(home, capsys):
    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=False):
        handle_listener_on_command(_args())
    out = json.loads(capsys.readouterr().out)
    assert out.get("dry_run") is not True
    assert len(_state_files(home)) == 1 and _registry_files(home) != []


def test_the_first_arm_creates_the_record_when_on_never_wrote_one(home, capsys):
    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=True):
        rc = handle_listener_arm_command(_args(task_id="task-1"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["monitor_task_id"] == "task-1"
    (path,) = _state_files(home)
    record = json.loads(path.read_text())
    assert record["monitor_task_id"] == "task-1" and record["ai_id"] == "empirica"
    assert _registry_files(home) != []  # `off` / `unregister` find the listener they are asked to remove


def test_arm_after_a_real_on_still_records_the_task_id(home, capsys):
    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=False):
        handle_listener_on_command(_args())
        capsys.readouterr()
        handle_listener_arm_command(_args(task_id="task-2"))
    (path,) = _state_files(home)
    assert json.loads(path.read_text())["monitor_task_id"] == "task-2"


def test_the_hook_asks_for_a_dry_run():
    spec = importlib.util.spec_from_file_location("monitor_arm_dry_run", HOOK)
    assert spec and spec.loader
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    seen = {}

    def fake_run(argv, **_kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="{}")

    with patch("subprocess.run", fake_run):
        hook._query_listener_on("empirica")
    assert seen["argv"][:3] == ["empirica", "listener", "on"] and "--dry-run" in seen["argv"]
