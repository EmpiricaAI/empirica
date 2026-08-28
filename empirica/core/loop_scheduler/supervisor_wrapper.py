"""`sleep 3` in a supervisor loop is a storm generator, and it was our canonical advice.

The shell wrapper we prescribe to every practitioner was:

    while true; do empirica loop listen --instance <id>; sleep 3; done

Measured on a real client seat: a present-but-WRONG api key made the listener
refuse and EXIT, the wrapper respawned it three seconds later, and that produced
**5,982 requests per day** against cortex for 25 hours. The cadence was not
improvisation — it was the arming pattern, verbatim, from our own SessionStart
hook.

**Why every in-process mitigation is inert against this.** Backoff counters, mute
windows, strike caches — all of it lives in the process, and a three-second
respawn resets all of it, forever. The very shape that makes the storm makes the
standard remedy a no-op. From the server it looks like a poll loop, which backoff
DOES fix, so the diagnosis lands on the wrong layer.

**All three surfaces had the defect, and the first reading of one of them was
wrong.** The systemd unit carried `StartLimitBurst=5` / `StartLimitIntervalSec=60`,
which reads like protection and is not: at `RestartSec=5` in a 10s window that is 2
starts against a burst of 5, so it never trips — and the units deployed on a live
box carried no `StartLimit` lines at all, predating the template that has them. The
launchd plist had no `ThrottleInterval` (10s default, no give-up). The shell wrapper
had neither. Reading a directive and inferring protection from its NAME, without
computing what it bounds or checking what is deployed, is how a storm ships under a
line that looks like a rate limiter.

This module is the shell wrapper's version of that protection. It cannot use
systemd's give-up semantics — a Monitor-armed session with a dead listener and no
message is worse than a slow one — so it degrades instead: exponential on fast
exits, capped, reset by a healthy run, and **loud on every backoff**, because the
Monitor tails this stream and a silent slowdown is just a slower silence.

DUPLICATION, DELIBERATE AND GUARDED
-----------------------------------
The Claude Code hook that also emits this command is standalone by design — it
cannot import from the package. So the string is duplicated there rather than
imported, and `tests/test_supervisor_backoff.py` asserts the two are identical.
An assertion of equality is the substitute for a single home when a single home
is not reachable.
"""

from __future__ import annotations

#: Exit faster than this and the run counts as a crash rather than a lifetime.
FAST_EXIT_SECONDS = 30
#: First backoff, and the value a healthy run resets to.
BASE_DELAY_SECONDS = 3
#: Ceiling. 5 minutes turns 28,800 requests/day into ~288 at the worst case,
#: while still recovering within minutes once the cause is fixed.
MAX_DELAY_SECONDS = 300


def supervisor_command(instance_id: str) -> str:
    """The canonical supervised-listener command, with respawn backoff.

    POSIX sh only — this string is handed to a Monitor, to tmux, and to whatever
    shell a practitioner has. No bashisms, no arrays, no `$SECONDS`.

    The shape:
      - time each run; an exit under FAST_EXIT_SECONDS is a crash, not a lifetime
      - crash → sleep, then double the delay, capped
      - a run that lasts → reset to base, because the cause was transient
      - every backoff prints to stderr, so the Monitor tail shows WHY it slowed
    """
    return (
        f"d={BASE_DELAY_SECONDS}; "
        "while true; do "
        "s=$(date +%s); "
        f"empirica loop listen --instance {instance_id}; "
        "e=$(date +%s); "
        f"if [ $((e-s)) -lt {FAST_EXIT_SECONDS} ]; then "
        'echo "[empirica] listener exited after $((e-s))s — backing off ${d}s '
        'before respawn (repeat crash; see ~/.empirica/loop_fires.log)" >&2; '
        "sleep $d; "
        f"d=$((d*2)); [ $d -gt {MAX_DELAY_SECONDS} ] && d={MAX_DELAY_SECONDS}; "
        "else "
        f"d={BASE_DELAY_SECONDS}; "
        "fi; "
        "done"
    )
