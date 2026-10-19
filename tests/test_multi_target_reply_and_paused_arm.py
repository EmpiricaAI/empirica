"""Two small defects from Philipp's mesh-support seat (prop_bxjntibm, defects 7 and 6b).

7. ``mailbox reply`` completed a proposal addressed to several seats as soon as ONE of them replied. Completion is per proposal,
   not per recipient (archive is per recipient), so the other two seats' open ask vanished from the source's outbox as done.
   A reply to a multi-target parent now posts the reply and leaves the parent open.

6b. ``listener arm`` on a paused registry entry reported success. The Monitor was armed and nothing would ever wake the
   session, which reads as a quiet inbox. Arming still works, but it now says the entry is paused and how to resume it.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from empirica.cli.command_handlers.cockpit_commands import handle_listener_arm_command, handle_listener_on_command
from empirica.cli.command_handlers.mailbox_commands import handle_mailbox_reply_command
from empirica.core.cockpit.listener_registry import set_listener_paused

# ─── 7. multi-target reply ────────────────────────────────────────────────────────────────────────────────────────────────

THREE = [
    "empirica.david.empirica",
    "empirica.david.empirica-cortex",
    "empirica.david.empirica-extension",
]


def _reply_args(**overrides):
    base = {
        "parent_id": "prop_parent",
        "summary": "done",
        "title": None,
        "type": "collab_brief",
        "target_claudes": None,
        "source_claude": None,
        "payload": None,
        "result": "shipped",
        "commit_sha": None,
        "no_close": False,
        "no_archive": False,
        "output": "json",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _post():
    calls = []

    def fn(url, body, api_key, timeout):
        calls.append(url)
        if "/propose" in url:
            return 200, {"ok": True, "proposal_id": "prop_new", "status": "accepted"}
        return 200, {"ok": True}

    return calls, fn


def _parent(targets):
    row = {"source_claude": "empirica.philipp.empirica-mesh-support", "title": "Seven defects"}
    if targets is not None:
        row["target_claudes"] = targets
    return lambda url, key, pid, timeout=5.0: {"id": pid, **row}


def _reply(parent_targets, capsys, **overrides):
    calls, post = _post()
    rc = handle_mailbox_reply_command(
        _reply_args(**overrides),
        _resolve_cortex_creds=lambda: ("https://cortex.example", "ctx_test"),
        _resolve_ai_id=lambda: "empirica",
        _http_post=post,
        _fetch_parent=_parent(parent_targets),
    )
    return rc, calls, json.loads(capsys.readouterr().out)


def test_a_reply_to_a_multi_target_parent_does_not_complete_it_for_everyone(capsys):
    rc, calls, out = _reply(THREE, capsys)
    assert rc == 0
    assert any("/propose" in u for u in calls), "the reply itself is still sent"
    assert not any("/complete" in u for u in calls), (
        "completion is per proposal: one target must not close it for three"
    )
    assert out["parent_closed"] is False
    assert "3" in out["parent_close_skipped"] and "target" in out["parent_close_skipped"]


def test_it_does_not_archive_what_it_did_not_close(capsys):
    _, calls, out = _reply(THREE, capsys)
    assert not any("/archive" in u for u in calls)
    assert out["parent_archived"] is False


def test_a_single_target_parent_is_closed_as_before(capsys):
    _, calls, out = _reply(["empirica.david.empirica"], capsys)
    assert any("/complete" in u for u in calls)
    assert out["parent_closed"] is True and "parent_close_skipped" not in out


def test_an_older_parent_without_a_target_list_is_closed_as_before(capsys):
    _, calls, out = _reply(None, capsys)
    assert any("/complete" in u for u in calls) and out["parent_closed"] is True


def test_the_same_seat_listed_twice_is_one_target(capsys):
    _, calls, out = _reply(["empirica.david.empirica", "empirica.david.empirica"], capsys)
    assert any("/complete" in u for u in calls) and out["parent_closed"] is True


def test_no_close_still_means_no_close_and_says_nothing_about_targets(capsys):
    _, calls, out = _reply(["empirica.david.empirica"], capsys, no_close=True)
    assert not any("/complete" in u for u in calls)
    assert "parent_close_skipped" not in out, "the operator asked for it; do not blame the target count"


# ─── 6b. arming a paused listener ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def listener_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("empirica.core.cockpit.listener_registry.EMPIRICA_DIR", tmp_path / ".empirica")
    (tmp_path / ".empirica").mkdir()
    monkeypatch.setattr(
        "empirica.core.cockpit.notification_channels.resolve_orchestration_events_topic",
        lambda ai_id: f"ntfy:empirica-orchestration-events?tags={ai_id}",
    )
    return tmp_path


def _listener_args(**overrides):
    base = {
        "ai_id": "myai",
        "name": None,
        "topic": None,
        "instance": "test_instance",
        "task_id": None,
        "output": "json",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _registered(listener_home, capsys):
    from unittest.mock import patch

    with patch("empirica.core.loop_scheduler.persistent_listener.is_listener_running", return_value=False):
        handle_listener_on_command(_listener_args())
    capsys.readouterr()


def test_arming_a_paused_listener_still_arms_but_warns_and_names_the_resume_command(listener_home, capsys):
    _registered(listener_home, capsys)
    set_listener_paused("test_instance", "myai-inbox", True)

    rc = handle_listener_arm_command(_listener_args(task_id="tk_paused"))
    out = json.loads(capsys.readouterr().out)

    assert rc == 0 and out["ok"] is True and out["monitor_task_id"] == "tk_paused"
    assert "paused" in out["warning"] and "listener resume" in out["warning"]


def test_arming_a_listener_that_is_not_paused_has_no_warning(listener_home, capsys):
    _registered(listener_home, capsys)
    rc = handle_listener_arm_command(_listener_args(task_id="tk_live"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and "warning" not in out
