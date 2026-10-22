"""No cron loop is ever installed by default.

Wake-on-event is the preferred trigger wherever it is possible; a cron job is a
standing scheduled process on someone's machine and must be asked for (David,
2026-08-01: "cronjobs should be opt in only, never installed by default — we
always want wake on event if possible").

`cortex-mailbox-poll` already carried `opt_in_only` from the earlier version of
this rule. `message-cleanup` did not — it is `kind="cron"` and auto-installed,
with a comment explicitly exempting "genuine housekeeping crons" from the
carve-out. So the entry that broke the rule was the one that never opted into the
flag guarding it.

The auto-install cascade that used to gate on `kind` is retired (goal d02fcc9c;
test_canonical_install_cascade_retired.py): nothing installs from the catalogue on
its own. These two checks keep the catalogue honest about it: a cron entry that
forgot the flag would read as installable to anyone who reads the catalogue.
"""

from __future__ import annotations

from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS


def test_every_cron_loop_in_the_catalogue_is_opt_in():
    """POSITIVE CONTROL — message-cleanup failed this."""
    offenders = [e["name"] for e in CANONICAL_LOOPS if e.get("kind") == "cron" and not e.get("opt_in_only")]

    assert offenders == [], f"cron loops missing opt_in_only: {offenders}"


def test_the_catalogue_still_contains_a_cron_loop():
    """NEGATIVE CONTROL: the test above passes trivially if cron loops are all
    deleted. Something must still be under test."""
    assert any(e.get("kind") == "cron" for e in CANONICAL_LOOPS)
