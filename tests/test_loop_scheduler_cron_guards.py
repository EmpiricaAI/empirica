"""Canonical-loop ghost-unit guards: placeholder-instance refusal + cron→calendar
mapping (a daily cron must never silently degrade to a 30-second timer).

Fleet-wide fix (mesh-support-routed): the auto-register→enable path could install
ghost `com.empirica.loop.project.*` units and map a `kind=cron` loop to
`StartInterval=30`. These test the caller-independent chokepoint guards.
"""

from __future__ import annotations

import pytest

from empirica.core.loop_scheduler.launchd import (
    cron_to_launchd_calendar,
    is_placeholder_instance,
    looks_like_cron,
)
from empirica.core.loop_scheduler.systemd import cron_to_systemd_oncalendar


@pytest.mark.parametrize("iid", ["project", "unknown", "none", "None", "", "  ", None])
def test_placeholder_instances_detected(iid):
    assert is_placeholder_instance(iid) is True


@pytest.mark.parametrize("iid", ["empirica", "cortex", "empirica-autonomy", "outreach"])
def test_real_instances_not_placeholder(iid):
    assert is_placeholder_instance(iid) is False


def test_looks_like_cron_distinguishes_from_interval():
    assert looks_like_cron("17 3 * * *") is True
    assert looks_like_cron("* * * * *") is True  # 5 fields
    assert looks_like_cron("30s") is False
    assert looks_like_cron("5m") is False
    assert looks_like_cron(30) is False  # int interval


# ---- launchd StartCalendarInterval mapping -----------------------------------


def test_cron_to_launchd_calendar_daily():
    # message-cleanup: 03:17 daily → Minute/Hour only, wildcards omitted.
    assert cron_to_launchd_calendar("17 3 * * *") == {"Minute": 17, "Hour": 3}


def test_cron_to_launchd_calendar_weekday_passes_through():
    # cron Weekday 0-7 (0/7=Sun) matches launchd — Sundays 04:00.
    assert cron_to_launchd_calendar("0 4 * * 0") == {"Minute": 0, "Hour": 4, "Weekday": 0}


def test_cron_to_launchd_calendar_rejects_unmappable():
    # ranges / steps / lists must fail loud, never silently mis-fire.
    for bad in ("*/5 * * * *", "0-30 3 * * *", "0 3 1,15 * *"):
        with pytest.raises(ValueError):
            cron_to_launchd_calendar(bad)


def test_cron_to_launchd_calendar_rejects_all_wildcard():
    with pytest.raises(ValueError):
        cron_to_launchd_calendar("* * * * *")  # every-minute → use an interval loop


# ---- systemd OnCalendar mapping ----------------------------------------------


def test_cron_to_systemd_oncalendar_daily():
    assert cron_to_systemd_oncalendar("17 3 * * *") == "*-*-* 03:17:00"
    assert cron_to_systemd_oncalendar("0 0 * * *") == "*-*-* 00:00:00"


def test_cron_to_systemd_oncalendar_rejects_wildcard_minute_hour():
    for bad in ("* 3 * * *", "17 * * * *", "*/5 * * * *"):
        with pytest.raises(ValueError):
            cron_to_systemd_oncalendar(bad)


def test_cron_to_systemd_oncalendar_rejects_dom_month_weekday():
    for bad in ("0 3 1 * *", "0 3 * 6 *", "0 4 * * 0"):
        with pytest.raises(ValueError):
            cron_to_systemd_oncalendar(bad)


# ---- loop tick ghost-fire guard ----------------------------------------------


def test_loop_tick_skips_placeholder_instance(monkeypatch):
    """A ghost tick under a placeholder instance must NOT append a fire event."""
    from types import SimpleNamespace

    from empirica.cli.command_handlers import cockpit_commands as cc
    from empirica.core.loop_scheduler import SystemdLoopScheduler

    def _must_not_run(*a, **kw):
        raise AssertionError("SystemdLoopScheduler.tick must not run for a placeholder instance")

    monkeypatch.setattr(SystemdLoopScheduler, "tick", staticmethod(_must_not_run))
    args = SimpleNamespace(instance_id="project", name="message-cleanup", output="json")
    rc = cc.handle_loop_tick_command(args)
    assert rc == 0  # clean skip (ok:True), not the error path — and tick never ran
