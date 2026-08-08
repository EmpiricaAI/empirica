"""A paused loop must not emit a wake event.

`empirica loop pause` had never been enforced by code. All six `is_loop_paused`
call sites read the flag into a display or status payload — one of them a bare
`noqa: B018` expression kept alive only to warm an import. Not one gated
behaviour.

The only enforcement was a `PAUSED=$(empirica loop status …); exit 0` snippet
carried under `prompt_template` in a loop's install request. That reaches ONLY
`cron-create` loops, and it runs in an already-woken session — downstream of the
entire cost the pause exists to avoid. A `systemd-user` / `launchd` / kindless
loop has its unit run `handle_loop_tick_command` directly, so there was no body
for a pause check to live in.

Measured across two machines by empirica.philipp.empirica-autonomy and
empirica.david.empirica-mesh-support: 28 loops, 19 registries, **zero** prompts,
`scheduler_kind` null on 17/17 here. Three armed launchd loops paused since
2026-08-05 were still waking sessions roughly 24×/day.

The guard belongs beside the not-registered check because that one is proven:
~80,438 ghost fires over 14 days, zero wake events. The comparison that names
the defect — ghost timers were *cheaper* than paused loops, because ghosts fail
that guard while paused loops passed it and reached the emit.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from empirica.cli.command_handlers import cockpit_commands as cc


@pytest.fixture
def tick(monkeypatch):
    """Drive handle_loop_tick_command with storage and scheduler stubbed out."""
    emitted: list[dict] = []

    monkeypatch.setattr(cc, "_emit", lambda _args, payload, _summary="": emitted.append(payload) or payload)
    monkeypatch.setattr("empirica.core.loop_scheduler.launchd.is_placeholder_instance", lambda _i: False, raising=False)

    class _Reg:
        def __init__(self, _instance):
            pass

        def get(self, _name):
            return {"name": _name}  # registered

    monkeypatch.setattr(cc, "LoopRegistry", _Reg)

    ticked: list[str] = []

    class _Sched:
        @staticmethod
        def tick(_instance, name):
            ticked.append(name)
            return "/tmp/fires.log"

    monkeypatch.setattr("empirica.core.loop_scheduler.SystemdLoopScheduler", _Sched, raising=False)

    def _run(paused: bool | Exception):
        def _is_paused(_i, _n):
            if isinstance(paused, Exception):
                raise paused
            return paused

        monkeypatch.setattr(cc, "is_loop_paused", _is_paused)
        emitted.clear()
        ticked.clear()
        cc.handle_loop_tick_command(Namespace(instance_id="empirica", name="cortex-mailbox-poll", output="json"))
        return emitted[-1] if emitted else {}, ticked

    return _run


def test_a_paused_loop_skips_without_ticking(tick):
    """THE REGRESSION. The whole defect is that this reached the emit."""
    payload, ticked = tick(paused=True)

    assert payload.get("skipped") == "paused"
    assert ticked == [], "a paused loop still ran the scheduler tick"


def test_an_unpaused_loop_still_ticks(tick):
    """The guard must not turn every loop off — the failure in the other
    direction is worse, because it is silent in exactly the same way."""
    _payload, ticked = tick(paused=False)

    assert ticked == ["cortex-mailbox-poll"]


def test_the_skip_is_distinguishable_from_the_not_registered_skip(tick):
    """Two reasons to skip, reported distinctly. A shared label would make a
    paused loop and a ghost unit indistinguishable in the fires log — and the
    difference between them is the whole point: a ghost should be removed, a
    paused loop should stay registered."""
    payload, _ = tick(paused=True)

    assert payload["skipped"] == "paused"
    assert payload["skipped"] != "not_registered"


def test_a_pause_read_error_fails_OPEN(tick):
    """Deliberate: a transient read error ticks rather than skips.

    Failing closed would silently stop healthy loops on a read hiccup, which is
    a worse outcome than one extra fire — the pause flag is a preference, not a
    safety interlock. Pinned so a later 'tighten this' change has to argue with
    the reason.
    """
    _payload, ticked = tick(paused=OSError("registry unreadable"))

    assert ticked == ["cortex-mailbox-poll"], "a read error suppressed a healthy loop"
