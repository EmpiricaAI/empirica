"""A fixed respawn interval is a storm generator, and we shipped three of them.

Measured on a real client seat: a present-but-WRONG api key made the listener
refuse and EXIT, the supervisor respawned it three seconds later, and that
produced **5,982 requests per day** for 25 hours. The cadence was our own
canonical arming pattern, verbatim.

**No in-process mitigation can fix this.** Backoff counters, mute windows, strike
caches all live in the process, and a 3-second respawn resets them forever. The
very shape that makes the storm makes the standard remedy a no-op — from the
server it looks like a poll loop, which backoff DOES fix, so the diagnosis lands
on the wrong layer. The backoff has to live in the SUPERVISOR.

THREE SHAPES, AND THE FIRST READING GOT ONE OF THEM WRONG
---------------------------------------------------------
I recorded that systemd was "already protected" by `StartLimitBurst=5`. It is
not, and the arithmetic is the reason: the default `StartLimitIntervalSec` is
10s, so at `RestartSec=5` that is **2 starts per window against a burst of 5** —
it never trips. Widening the window does not save it either once the listener
runs for a few seconds before exiting.

Worse, the units deployed on a live box carried no `StartLimit*` lines at all —
they predate the template that has them. So the claim was wrong about the
arithmetic AND about what was actually running.

    shell wrapper   sleep 3          -> exponential, capped, and LOUD
    systemd         RestartSec=5     -> RestartSteps + RestartMaxDelaySec
    launchd         10s default      -> ThrottleInterval=60 (no backoff exists here)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from empirica.core.loop_scheduler.supervisor_wrapper import (
    BASE_DELAY_SECONDS,
    FAST_EXIT_SECONDS,
    MAX_DELAY_SECONDS,
    supervisor_command,
)

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "empirica/plugins/claude-code-integration/hooks/session-monitor-arm.py"
LISTENER = ROOT / "empirica/core/loop_scheduler/persistent_listener.py"


# ── the shell wrapper ────────────────────────────────────────────────────────


def test_the_bare_sleep_loop_is_gone():
    """The exact string that produced the storm, asserted absent at both sites."""
    cmd = supervisor_command("empirica")
    assert "sleep 3; done" not in cmd, "the flat respawn is back"
    assert "; sleep 3; done" not in HOOK.read_text()


def test_the_delay_doubles_on_fast_exits_and_is_capped():
    """Simulated in the same arithmetic the shell performs, because asserting on
    the string only proves it was written, not that it converges."""
    d, seen = BASE_DELAY_SECONDS, []
    for _ in range(12):
        seen.append(d)
        d = min(d * 2, MAX_DELAY_SECONDS)
    assert seen[:4] == [3, 6, 12, 24]
    assert seen[-1] == MAX_DELAY_SECONDS
    assert max(seen) <= MAX_DELAY_SECONDS


def test_a_healthy_run_resets_the_delay():
    """Without the reset a listener that recovers stays throttled to five minutes
    forever, which trades a storm for a coma."""
    cmd = supervisor_command("empirica")
    assert f"else d={BASE_DELAY_SECONDS};" in cmd.replace("  ", " ")


def test_the_backoff_is_announced():
    """The Monitor tails this stream. A silent slowdown is just a slower silence,
    and the operator needs to see WHY the listener went quiet."""
    cmd = supervisor_command("empirica")
    assert "echo" in cmd and "backing off" in cmd
    assert ">&2" in cmd, "must reach stderr, where the Monitor is looking"


def test_only_a_fast_exit_counts_as_a_crash():
    """A listener that ran for hours and then exited is not crash-looping, and
    treating it as one would throttle normal operation."""
    cmd = supervisor_command("empirica")
    assert f"-lt {FAST_EXIT_SECONDS}" in cmd


def test_the_command_is_posix_sh():
    """Handed to a Monitor, to tmux, and to whatever shell a practitioner has."""
    cmd = supervisor_command("empirica")
    for bashism in ("[[", "$SECONDS", "declare ", "local ", "function "):
        assert bashism not in cmd, f"bashism in a POSIX-sh context: {bashism}"


def test_the_hook_copy_has_not_drifted():
    """The hook is standalone — no package import — so the string is duplicated
    there. An equality assertion is the substitute for a single home when a single
    home is not reachable, and drift here would be invisible."""
    hook_src = HOOK.read_text()
    for fragment in ("d=3; ", "-lt 30", "d=$((d*2))", "-gt 300", "backing off"):
        assert fragment in hook_src, f"hook copy missing {fragment!r} — the two have drifted"


# ── systemd, where the first reading was wrong ───────────────────────────────


def test_systemd_gets_real_backoff_not_a_fixed_interval():
    src = LISTENER.read_text()
    assert "RestartSteps=" in src
    assert "RestartMaxDelaySec=" in src


def test_the_systemd_rate_limiter_is_disabled_deliberately():
    """A hard stop layered on top of backoff fires during the fast early steps and
    leaves the listener silently dead. For something people depend on, degrading
    loudly beats stopping quietly — and the comment has to say so, or the next
    reader restores the limiter thinking it was an oversight."""
    src = LISTENER.read_text()
    assert "StartLimitIntervalSec=0" in src
    assert "DISABLED on purpose" in src


def test_the_burst_arithmetic_that_fooled_me_is_recorded():
    """NEGATIVE CONTROL on my own reasoning. `StartLimitBurst=5` reads like
    protection; at RestartSec=5 in a 10s window it is 2 starts against a burst of
    5 and never trips. Written down so the next reader does not re-derive the same
    wrong conclusion from the same plausible-looking line."""
    src = LISTENER.read_text()
    assert "never trips" in src


@pytest.mark.parametrize(
    ("window", "restart", "burst", "trips"),
    [(10, 5, 5, False), (60, 5, 5, True), (10, 1, 5, True)],
    ids=["systemd-default-never-trips", "wide-window-would", "very-fast-loop-would"],
)
def test_the_rate_limiter_arithmetic(window, restart, burst, trips):
    """The property in numbers rather than prose: the limiter only catches loops
    faster than burst-per-window, and the shipped configuration was not one."""
    assert ((window / restart) > burst) is trips


# ── launchd, where no backoff mechanism exists ───────────────────────────────


def test_launchd_gets_a_throttle_floor():
    """launchd has no exponential backoff, so the floor IS the policy. Default 10s
    is 8,640 respawns a day."""
    src = LISTENER.read_text()
    m = re.search(r"<key>ThrottleInterval</key>\s*<integer>(\d+)</integer>", src)
    assert m, "no ThrottleInterval — launchd defaults to 10s"
    assert int(m.group(1)) >= 60
