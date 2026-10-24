"""The listener's wake service: opt-in, one wake per message, and fed by the listener's own event path (goal 87772ac4).

This is the piece that types into terminals with nobody watching, so the properties that bound it are tested before it is wired:

* it is OFF unless `EMPIRICA_LISTENER_WAKE` is set, because scheduled behaviour is never installed by default;
* each message causes at most one successful wake: a pane woken for mail that nobody archived must not be nudged again every two
  hours, while a NEW message still wakes it;
* the listener tells it about events through `_emit_catchup_events`, the single function every event path returns through, and a
  failure inside the service never reaches the listener.

Clock, inbox poll, pane list and wake are injected. No test touches tmux, cortex or the wall clock.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from empirica.core.cockpit.instance_actions import WakeResult
from empirica.core.cockpit.wake_service import InboxWatch, WakeService, build_wake_service
from empirica.core.loop_scheduler import listener as listener_mod

HOUR = 3600.0


def _poll(*ids, rc=0):
    body = json.dumps({"ok": True, "matched": len(ids), "proposals": [{"id": i} for i in ids]})
    return lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=rc, stdout=body, stderr="")


# ── the inbox watch ──────────────────────────────────────────────────────────


def test_unseen_mail_is_pending_until_it_has_been_surfaced(tmp_path):
    watch = InboxWatch("proj", tmp_path / "s.json", run=_poll("prop_1", "prop_2"))
    assert watch.has_pending() is True
    watch.mark_surfaced()
    assert watch.has_pending() is False


def test_a_new_message_after_a_wake_is_pending_again(tmp_path):
    state = tmp_path / "s.json"
    InboxWatch("proj", state, run=_poll("prop_1")).mark_surfaced()
    assert InboxWatch("proj", state, run=_poll("prop_1")).has_pending() is False, (
        "surfaced ids survive a listener restart"
    )
    assert InboxWatch("proj", state, run=_poll("prop_1", "prop_2")).has_pending() is True


def test_an_empty_inbox_is_not_pending(tmp_path):
    assert InboxWatch("proj", tmp_path / "s.json", run=_poll()).has_pending() is False


@pytest.mark.parametrize(
    "run",
    [
        _poll("prop_1", rc=1),
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="not json", stderr=""),
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("empirica", 20)),
    ],
    ids=["nonzero exit", "garbage output", "timeout"],
)
def test_a_poll_that_fails_raises_so_the_scheduler_can_treat_it_as_mail(tmp_path, run):
    with pytest.raises((RuntimeError, ValueError, subprocess.TimeoutExpired)):
        InboxWatch("proj", tmp_path / "s.json", run=run).has_pending()


def test_a_damaged_state_file_means_nothing_is_surfaced_yet(tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{{{ not json")
    assert InboxWatch("proj", state, run=_poll("prop_1")).has_pending() is True


def test_the_poll_names_the_practice_and_asks_for_accepted_mail(tmp_path):
    seen = {}

    def run(args, **kw):
        seen["args"] = args
        return _poll()(args)

    InboxWatch("myproj", tmp_path / "s.json", run=run).has_pending()
    assert "mailbox" in seen["args"] and "poll" in seen["args"]
    assert seen["args"][seen["args"].index("--ai-id") + 1] == "myproj"
    assert seen["args"][seen["args"].index("--status") + 1] == "accepted"


# ── the service ──────────────────────────────────────────────────────────────


class Rig:
    def __init__(self, tmp_path, ids=("prop_1",)):
        self.now = 5_000_000.0
        self.ids = list(ids)
        self.wakes: list[str] = []
        self.watch = InboxWatch("proj", tmp_path / "s.json", run=lambda *a, **k: _poll(*self.ids)())
        self.service = WakeService(
            "proj",
            watch=self.watch,
            panes=lambda: ["tmux_3"],
            wake=self._wake,
            clock=lambda: self.now,
        )

    def _wake(self, pane):
        self.wakes.append(pane)
        return WakeResult(pane, True, "sent", "tmux-send-keys")

    def advance(self, seconds):
        self.now += seconds
        return self.service.tick_once()


def test_an_event_wakes_the_pane_and_marks_the_mail_surfaced(tmp_path):
    rig = Rig(tmp_path)
    rig.service.note_event()
    rig.service.tick_once()
    assert rig.wakes == ["tmux_3"]
    assert rig.watch.has_pending() is False


def test_the_heartbeat_does_not_nag_about_mail_it_already_surfaced(tmp_path):
    rig = Rig(tmp_path)
    rig.service.note_event()
    rig.service.tick_once()
    for _ in range(3):
        rig.advance(2 * HOUR + 1)
    assert rig.wakes == ["tmux_3"], "one wake for the one message, however many heartbeats pass"


def test_unsurfaced_backlog_is_woken_once_at_the_first_heartbeat(tmp_path):
    rig = Rig(tmp_path, ids=("prop_old1", "prop_old2"))
    rig.advance(2 * HOUR + 1)
    assert rig.wakes == ["tmux_3"]
    rig.advance(2 * HOUR + 1)
    assert rig.wakes == ["tmux_3"]


def test_a_new_message_wakes_again_at_the_next_heartbeat(tmp_path):
    rig = Rig(tmp_path)
    rig.service.note_event()
    rig.service.tick_once()
    rig.ids.append("prop_2")
    rig.advance(2 * HOUR + 1)
    assert rig.wakes == ["tmux_3", "tmux_3"]


def test_every_wake_log_line_carries_a_utc_timestamp(tmp_path):
    """Listener stderr goes to a file, not journald, and a line without a time cannot be compared with armed_at or with a message's
    arrival. Live trial 2026-10-24: mesh-support could not find the wake time. The lines now start with an ISO UTC stamp."""
    import re

    rig = Rig(tmp_path)
    out = io.StringIO()
    rig.service._err = out
    rig.service.note_event()
    rig.service.tick_once()
    line = out.getvalue().splitlines()[0]
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z listener: wake event tmux_3: sent$", line), line


def test_a_service_failure_never_escapes_tick_once(tmp_path):
    rig = Rig(tmp_path)
    rig.watch.has_pending = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
    rig.service.note_event()
    rig.advance(2 * HOUR + 1)  # reaches the failing check without raising


# ── opt-in ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "env", [{}, {"EMPIRICA_LISTENER_WAKE": ""}, {"EMPIRICA_LISTENER_WAKE": "0"}, {"EMPIRICA_LISTENER_WAKE": "no"}]
)
def test_the_service_is_off_unless_asked_for(env):
    assert build_wake_service("proj", env=env) is None


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_the_service_is_built_when_asked_for(value):
    assert build_wake_service("proj", env={"EMPIRICA_LISTENER_WAKE": value}) is not None


def test_waking_over_a_suggestion_is_a_separate_opt_in(monkeypatch):
    from empirica.core.cockpit import wake_service as ws

    calls = []
    monkeypatch.setattr(
        ws,
        "wake_instance",
        lambda pane, **kw: calls.append((pane, kw)) or WakeResult(pane, True, "sent", "tmux-send-keys"),
    )
    off = build_wake_service("proj", env={"EMPIRICA_LISTENER_WAKE": "1"})
    on = build_wake_service("proj", env={"EMPIRICA_LISTENER_WAKE": "1", "EMPIRICA_WAKE_OVER_SUGGESTION": "1"})
    assert off is not None and on is not None
    off._raw_wake("tmux_1")
    on._raw_wake("tmux_2")
    assert calls == [("tmux_1", {"allow_suggestion": False}), ("tmux_2", {"allow_suggestion": True})]


# ── the listener feeds it ────────────────────────────────────────────────────


class _Events:
    def __init__(self, n):
        self.n = n

    def __iter__(self):
        return iter([SimpleNamespace(to_log_line=lambda i=i: json.dumps({"i": i})) for i in range(self.n)])

    def __len__(self):
        return self.n


@pytest.fixture
def catchup(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(listener_mod, "_paused_listener_names", lambda _i: [])
    monkeypatch.setattr(listener_mod, "_clear_fail_heartbeat", lambda *a, **k: None)
    import empirica.core.auth as auth
    import empirica.core.loop_scheduler.content_poll as cp

    monkeypatch.setattr(auth, "cortex_bearer", lambda: {"url": "https://x", "bearer": "k"})
    state = {"events": _Events(0)}
    monkeypatch.setattr(cp, "poll_and_diff", lambda *a, **k: list(state["events"]))
    listener_mod._WAKE_SERVICES.clear()
    yield state
    listener_mod._WAKE_SERVICES.clear()


class _Spy:
    def __init__(self):
        self.noted = 0

    def note_event(self):
        self.noted += 1


def test_emitted_events_are_reported_to_the_practices_wake_service(catchup, tmp_path):
    spy = _Spy()
    listener_mod._WAKE_SERVICES["proj"] = spy  # type: ignore[assignment]
    catchup["events"] = _Events(2)
    assert listener_mod._emit_catchup_events("proj", "cortex-mailbox-poll", output_stream=io.StringIO()) == 2
    assert spy.noted == 1


def test_a_catch_up_that_found_nothing_does_not_wake_anyone(catchup, tmp_path):
    spy = _Spy()
    listener_mod._WAKE_SERVICES["proj"] = spy  # type: ignore[assignment]
    listener_mod._emit_catchup_events("proj", "cortex-mailbox-poll", output_stream=io.StringIO())
    assert spy.noted == 0


def test_another_practices_events_do_not_wake_this_one(catchup, tmp_path):
    spy = _Spy()
    listener_mod._WAKE_SERVICES["other"] = spy  # type: ignore[assignment]
    catchup["events"] = _Events(1)
    listener_mod._emit_catchup_events("proj", "cortex-mailbox-poll", output_stream=io.StringIO())
    assert spy.noted == 0


def test_a_wake_service_that_raises_cannot_break_the_listeners_event_path(catchup, tmp_path):
    class Boom:
        def note_event(self):
            raise RuntimeError("wake service exploded")

    listener_mod._WAKE_SERVICES["proj"] = Boom()  # type: ignore[assignment]
    catchup["events"] = _Events(1)
    assert listener_mod._emit_catchup_events("proj", "cortex-mailbox-poll", output_stream=io.StringIO()) == 1
