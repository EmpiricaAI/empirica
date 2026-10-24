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


SEAT = "empirica.david.empirica"


def _post(complete_resp=None, complete_status=200):
    """A fake cortex that records every POST (url, body) and answers /complete with the given reply."""
    calls: list[tuple[str, dict]] = []

    def fn(url, body, api_key, timeout):
        calls.append((url, body))
        if "/propose" in url:
            return 200, {"ok": True, "proposal_id": "prop_new", "status": "accepted"}
        if "/complete" in url:
            return complete_status, (complete_resp if complete_resp is not None else {"ok": True})
        return 200, {"ok": True}

    return calls, fn


def _parent(targets):
    row = {"source_claude": "empirica.philipp.empirica-mesh-support", "title": "Seven defects"}
    if targets is not None:
        row["target_claudes"] = targets
    return lambda url, key, pid, timeout=5.0: {"id": pid, **row}


def _reply(parent_targets, capsys, complete_resp=None, complete_status=200, **overrides):
    calls, post = _post(complete_resp, complete_status)
    overrides.setdefault("source_claude", SEAT)
    rc = handle_mailbox_reply_command(
        _reply_args(**overrides),
        _resolve_cortex_creds=lambda: ("https://cortex.example", "ctx_test"),
        _resolve_ai_id=lambda: "empirica",
        _http_post=post,
        _fetch_parent=_parent(parent_targets),
    )
    out = capsys.readouterr()
    return rc, calls, json.loads(out.out), out.err


def _urls(calls):
    return [u for u, _ in calls]


def _complete_body(calls):
    return next(b for u, b in calls if "/complete" in u)


# Cortex 97820077 (2026-10-24): /complete on a multi-target proposal acknowledges ONE target and the row stays accepted until all
# have acked. The client no longer skips the call; it names the acking seat, and an ack that leaves others pending is reported as
# an ack, not as a close.

WAITING = {
    "ok": True,
    "note": "acked_waiting_for_other_targets",
    "target_acks": {SEAT: {"result": "shipped"}},
    "pending_targets": ["empirica.david.empirica-cortex", "empirica.david.empirica-extension"],
}


def test_a_reply_to_a_multi_target_parent_acks_its_own_target(capsys):
    rc, calls, _out, _ = _reply(THREE, capsys, complete_resp=WAITING)
    assert rc == 0
    assert any("/propose" in u for u in _urls(calls)), "the reply itself is still sent"
    body = _complete_body(calls)
    assert body["ai_id"] == SEAT, "cortex needs to know WHICH target this seat is acking"
    assert body["result"] == "shipped"


def test_an_ack_that_leaves_other_targets_pending_is_reported_as_an_ack_not_a_close(capsys):
    _, _, out, _ = _reply(THREE, capsys, complete_resp=WAITING)
    assert out["parent_acked"] is True and out["parent_closed"] is False
    assert out["pending_targets"] == ["empirica.david.empirica-cortex", "empirica.david.empirica-extension"]
    assert SEAT in out["target_acks"]


def test_our_own_copy_is_archived_once_our_ack_is_recorded(capsys):
    """Archive is per recipient (archived_by[]), so clearing our copy does not hide the open ask from the other targets."""
    _, calls, out, _ = _reply(THREE, capsys, complete_resp=WAITING)
    assert any("/archive" in u for u in _urls(calls)) and out["parent_archived"] is True


def test_the_last_target_to_ack_closes_it(capsys):
    _, _, out, _ = _reply(THREE, capsys, complete_resp={"ok": True})
    assert out["parent_closed"] is True and out["parent_acked"] is True and out["pending_targets"] is None


def test_a_repeat_ack_is_an_idempotent_no_op_not_a_close(capsys):
    _, _, out, _ = _reply(THREE, capsys, complete_resp={"ok": True, "note": "already_acked", "pending_targets": ["x"]})
    assert out["parent_acked"] is True and out["parent_closed"] is False


@pytest.mark.parametrize("status", [400, 403])
def test_a_refused_ack_is_surfaced_and_nothing_is_archived(capsys, status):
    _, calls, out, err = _reply(THREE, capsys, complete_resp={"error": "not a target"}, complete_status=status)
    assert out["parent_acked"] is False and out["parent_closed"] is False
    assert not any("/archive" in u for u in _urls(calls)), "an ask we did not ack must stay in our inbox"
    assert str(status) in err


def test_a_single_target_parent_needs_no_ai_id(capsys):
    _, calls, out, _ = _reply(["empirica.david.empirica"], capsys)
    assert "ai_id" not in _complete_body(calls), "cortex infers it when exactly one target is ours"
    assert out["parent_closed"] is True


def test_the_acking_seat_is_the_canonical_form_not_the_bare_slug(capsys):
    calls, post = _post(WAITING)
    handle_mailbox_reply_command(
        _reply_args(source_claude="empirica"),
        _resolve_cortex_creds=lambda: ("https://cortex.example", "ctx_test"),
        _resolve_ai_id=lambda: "empirica",
        _http_post=post,
        _fetch_parent=_parent(THREE),
        _http_get=lambda url, key, timeout=5.0: (200, {"ai_id_mesh": SEAT, "canonical": SEAT}),
    )
    capsys.readouterr()
    ai_id = _complete_body(calls).get("ai_id")
    assert ai_id in (None, SEAT), "a bare slug sent as ai_id is a guaranteed participant mismatch"


def test_a_single_target_parent_is_closed_as_before(capsys):
    _, calls, out, _ = _reply(["empirica.david.empirica"], capsys)
    assert any("/complete" in u for u in _urls(calls))
    assert out["parent_closed"] is True and "parent_close_skipped" not in out


def test_an_older_parent_without_a_target_list_is_closed_as_before(capsys):
    _, calls, out, _ = _reply(None, capsys)
    assert any("/complete" in u for u in _urls(calls)) and out["parent_closed"] is True


def test_the_same_seat_listed_twice_is_one_target(capsys):
    _, calls, out, _ = _reply(["empirica.david.empirica", "empirica.david.empirica"], capsys)
    assert any("/complete" in u for u in _urls(calls)) and out["parent_closed"] is True
    assert "ai_id" not in _complete_body(calls), "one distinct target: nothing to disambiguate"


def test_no_close_still_means_no_close_and_says_nothing_about_targets(capsys):
    _, calls, out, _ = _reply(THREE, capsys, no_close=True)
    assert not any("/complete" in u for u in _urls(calls))
    assert out["parent_acked"] is False and out["parent_closed"] is False


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
