"""A pane `cockpit refresh` resumes is woken once it is idle, so it takes its first turn (goal 87772ac4, David's ruling (a)).

After a restart a resumed claude sits idle at its prompt and its SessionStart hook has already run; the Monitor is armed by a TURN, and no turn comes
until someone types (mesh-support measured one of about twelve resumed panes armed; a wake turn polls the inbox and re-arms, finding 117de356).
`wake_resumed` polls `wake_instance` per respawned claude pane until the pane is idle enough to be woken, and gives up after a deadline: a pane that
never settles is reported, not typed into.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit.instance_actions import WakeResult
from empirica.core.cockpit.wake_after_resume import wake_resumed


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _entry(pane_id="%12", key="g/0", command="claude --continue", error=None):
    return {"key": key, "pane_id": pane_id, "instance_id": "x", "command": command, "error": error}


def _refused(pane):
    return WakeResult(pane, False, "not woken: the pane is busy", "refused")


def _woke(pane):
    return WakeResult(pane, True, "woken", "tmux-send-keys")


def _run(entries, wake, **kw):
    clock = Clock()
    out = wake_resumed(entries, wake=wake, sleep=clock.sleep, now=clock.now, **kw)
    return out, clock


def test_a_pane_that_is_idle_straight_away_is_woken_once_by_its_tmux_id():
    calls = []

    def wake(pane):
        calls.append(pane)
        return _woke(pane)

    (got,), _ = _run([_entry("%12")], wake)
    assert calls == ["tmux_12"] and got["woken"] is True and got["key"] == "g/0"


def test_a_booting_pane_is_retried_until_it_is_idle():
    seen = []

    def wake(pane):
        seen.append(pane)
        return _woke(pane) if len(seen) >= 4 else _refused(pane)

    (got,), clock = _run([_entry()], wake, interval=3.0)
    assert got["woken"] is True and len(seen) == 4 and clock.t == pytest.approx(9.0)


def test_a_pane_that_never_settles_is_reported_not_woken_after_the_deadline():
    (got,), clock = _run([_entry()], _refused, timeout=30.0, interval=3.0)
    assert got["woken"] is False and "busy" in got["detail"] and clock.t >= 30.0


def test_an_unreachable_pane_is_not_retried():
    seen = []

    def wake(pane):
        seen.append(pane)
        return WakeResult(pane, False, "tmux binary not found in PATH", "unreachable")

    (got,), _ = _run([_entry()], wake)
    assert got["woken"] is False and len(seen) == 1


def test_a_pane_whose_respawn_failed_is_not_woken():
    called = []
    (got,), _ = _run([_entry(error="respawn-pane failed")], lambda p: called.append(p) or _woke(p))
    assert called == [] and got["woken"] is False and "respawn" in got["detail"]


@pytest.mark.parametrize("command", ["ecodex resume --last", "codex resume --last", "bash", None])
def test_only_claude_panes_are_woken(command):
    """Control: the idle check and the wake marker are claude's; a codex pane's prompt looks different."""
    called = []
    (got,), _ = _run([_entry(command=command)], lambda p: called.append(p) or _woke(p))
    assert called == [] and got["woken"] is False


def test_each_pane_is_woken_on_its_own_and_one_failure_does_not_stop_the_rest():
    def wake(pane):
        return _woke(pane) if pane == "tmux_2" else _refused(pane)

    got, _ = _run([_entry("%1", "g/0"), _entry("%2", "g/1")], wake, timeout=6.0, interval=3.0)
    assert {g["key"]: g["woken"] for g in got} == {"g/0": False, "g/1": True}


def test_an_exception_from_the_wake_is_a_failed_pane_not_a_failed_refresh():
    def wake(_pane):
        raise OSError("tmux gone")

    (got,), _ = _run([_entry()], wake)
    assert got["woken"] is False and "tmux gone" in got["detail"]


def test_nothing_respawned_means_nothing_to_do():
    assert _run([], _woke)[0] == []


# ── wired into `cockpit refresh` ─────────────────────────────────────────────────────────────────────────────────


def _refresh_with(monkeypatch, capsys, respawned, wake_results=None, output="json"):
    import types

    from empirica.cli.command_handlers import cockpit_launcher_commands as h
    from empirica.core.cockpit.launcher.tmux import RefreshResult

    monkeypatch.setattr(h, "_config_path_arg", lambda _a: (None, None))
    monkeypatch.setattr(h, "load_config", lambda path=None: object())
    monkeypatch.setattr(h, "refresh_cockpit", lambda _c: RefreshResult(session_name="t", respawned=respawned))
    import empirica.core.cockpit.wake_after_resume as w

    seen: list = []
    monkeypatch.setattr(w, "wake_resumed", lambda entries: seen.append(entries) or (wake_results or []))
    code = h.handle_cockpit_refresh_command(types.SimpleNamespace(output=output))
    return code, capsys.readouterr().out, seen


def test_refresh_wakes_what_it_respawned_and_says_so_in_its_json(monkeypatch, capsys):
    import json

    entries = [_entry()]
    results = [{"key": "g/0", "pane_id": "%12", "woken": True, "detail": "woken"}]
    code, out, seen = _refresh_with(monkeypatch, capsys, entries, results)
    assert code == 0 and seen == [entries]
    assert json.loads(out)["woken"] == results


def test_refresh_with_nothing_respawned_never_calls_the_wake(monkeypatch, capsys):
    import json

    code, out, seen = _refresh_with(monkeypatch, capsys, [])
    assert code == 0 and seen == [] and json.loads(out)["woken"] == []


def test_a_wake_that_fails_does_not_fail_the_refresh(monkeypatch, capsys):
    import json

    from empirica.cli.command_handlers import cockpit_launcher_commands as h

    monkeypatch.setattr(
        "empirica.core.cockpit.wake_after_resume.wake_resumed", lambda _e: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = h._wake_resumed_panes([_entry()], quiet=True)
    assert out[0]["woken"] is False and "boom" in out[0]["detail"]
    json.dumps(out)
