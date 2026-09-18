"""`empirica listener pause` is enforced by the listener body, not only displayed.

The pause verb's docstring promised a "body pause check at next wake" as the
backstop for when the Monitor is not torn down in time. No such check existed:
all four `is_listener_paused` call sites rendered the flag, and a paused
listener kept waking its session — the shape `loop pause` had before 1761911a7.

The guard sits BEFORE the content poll so the poll cursor does not advance:
events that arrive while paused are delivered on unpause, not dropped. It fails
open on an unreadable registry and says so at WARNING, because a persistent
read failure must not look, from outside, like "not paused".
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from empirica.core.cockpit import listener_registry as reg_mod
from empirica.core.loop_scheduler import listener as listener_mod


@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A registry under tmp_path with one listener; the poll is a recording fake."""
    home = tmp_path / ".empirica"
    home.mkdir()
    monkeypatch.setattr(reg_mod, "EMPIRICA_DIR", home)
    monkeypatch.setattr(reg_mod, "registry_path", lambda iid: home / f"listeners_{iid}.json")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    reg = reg_mod.ListenerRegistry("seat-1")
    reg.register(name="seat-1-inbox", topic="ntfy:seat-1")

    polls: list[tuple] = []

    class _Event:
        def to_log_line(self):
            return '{"event_type": "proposal_event", "proposal_id": "prop_x"}'

    def fake_poll_and_diff(instance_id, loop_name, url, key, raise_on_unreachable=False):
        polls.append((instance_id, loop_name))
        return [_Event()]

    import types

    fake_cp = types.SimpleNamespace(poll_and_diff=fake_poll_and_diff, ContentPollUnreachable=RuntimeError)
    monkeypatch.setitem(__import__("sys").modules, "empirica.core.loop_scheduler.content_poll", fake_cp)
    fake_auth = types.SimpleNamespace(cortex_bearer=lambda: {"url": "https://cortex.test", "bearer": "tok"})
    monkeypatch.setitem(__import__("sys").modules, "empirica.core.auth", fake_auth)
    return {"reg": reg, "polls": polls}


def test_unpaused_listener_polls_and_emits(instance):
    out = io.StringIO()
    n = listener_mod._emit_catchup_events("seat-1", "cortex-mailbox-poll", output_stream=out)
    assert n == 1
    assert instance["polls"] == [("seat-1", "cortex-mailbox-poll")]
    assert "prop_x" in out.getvalue()


def test_paused_listener_neither_polls_nor_emits(instance, caplog):
    """The cursor must not advance: no poll at all while paused."""
    reg_mod.set_listener_paused("seat-1", "seat-1-inbox", paused=True)
    out = io.StringIO()
    with caplog.at_level(logging.INFO, logger=listener_mod.logger.name):
        n = listener_mod._emit_catchup_events("seat-1", "cortex-mailbox-poll", output_stream=out)
    assert n == 0
    assert instance["polls"] == []
    assert out.getvalue() == ""
    assert "listener paused (seat-1-inbox)" in caplog.text


def test_unpause_resumes_delivery(instance):
    reg_mod.set_listener_paused("seat-1", "seat-1-inbox", paused=True)
    assert listener_mod._emit_catchup_events("seat-1", "cortex-mailbox-poll", output_stream=io.StringIO()) == 0
    reg_mod.set_listener_paused("seat-1", "seat-1-inbox", paused=False)
    assert listener_mod._emit_catchup_events("seat-1", "cortex-mailbox-poll", output_stream=io.StringIO()) == 1
    assert len(instance["polls"]) == 1


def test_another_instances_pause_does_not_pause_this_one(instance):
    reg_mod.ListenerRegistry("seat-2").register(name="seat-2-inbox", topic="ntfy:seat-2")
    reg_mod.set_listener_paused("seat-2", "seat-2-inbox", paused=True)
    assert listener_mod._emit_catchup_events("seat-1", "cortex-mailbox-poll", output_stream=io.StringIO()) == 1


def test_unreadable_registry_fails_open_and_warns(instance, monkeypatch, caplog):
    """Not paused — a transient read error must not silence a healthy listener —
    but WARNING, so a persistent one is visible from outside."""

    def boom(self):
        raise OSError("registry unreadable")

    monkeypatch.setattr(reg_mod.ListenerRegistry, "list_listeners", boom)
    with caplog.at_level(logging.WARNING, logger=listener_mod.logger.name):
        n = listener_mod._emit_catchup_events("seat-1", "cortex-mailbox-poll", output_stream=io.StringIO())
    assert n == 1
    assert "pause check failed" in caplog.text
    assert "registry unreadable" in caplog.text
