"""A cron loop's schedule is its expression, not an interval.

`schedule_next` derived a fire time from interval + backoff for every loop,
including `kind='cron'` ones, and returned it in the same shape as a real answer.
"Every day at 09:00" came back as "in 15 minutes" with no way for the caller to
tell the number was invented (#396, graemester — the second defect, found while
verifying the first).

Computing a true next fire needs cron arithmetic and therefore a dependency this
package does not have. Adding one immediately before a release, or hand-rolling
date arithmetic that would be wrong in its own quieter way, both trade a loud
wrong answer for a subtle one. So the plan now carries the expression and states
that the interval fields are not a schedule — the caller is told, rather than
guessed at.

The trap worth naming: `cron_one_shot` pins a one-shot cron to `fire_at`. For a
cron loop `fire_at` is *now*, so a caller handing that to CronCreate would fire
immediately while believing it had scheduled tomorrow's 09:00 run. Every field
that would imply a computed next fire is omitted for cron loops rather than
filled with a placeholder.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit.loop_registry import LoopRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_HOME", str(tmp_path / ".empirica"))
    return LoopRegistry("test-instance")


def _register(registry, name, **kw):
    registry.register(name=name, description="test loop", **kw)
    return name


def test_a_cron_loop_does_not_get_an_invented_interval(registry):
    """POSITIVE CONTROL — the reproduction. This used to return a 15m plan."""
    _register(registry, "daily-report", kind="cron", cron="0 9 * * *")

    plan = registry.schedule_next("daily-report")

    assert plan is not None
    assert plan.is_cron is True
    assert plan.cron == "0 9 * * *"


def test_the_cron_expression_survives_into_the_payload(registry):
    _register(registry, "daily-report", kind="cron", cron="0 9 * * *")

    payload = registry.schedule_next("daily-report").to_dict()

    assert payload["is_cron"] is True
    assert payload["cron"] == "0 9 * * *"


def test_a_cron_plan_omits_every_fabricated_next_fire_field(registry):
    """The heart of it. `cron_one_shot` would pin a one-shot to 'now', so a
    caller passing it to CronCreate fires immediately and believes it scheduled
    the real cadence. Absent beats plausible-but-wrong."""
    _register(registry, "daily-report", kind="cron", cron="0 9 * * *")

    payload = registry.schedule_next("daily-report").to_dict()

    assert "cron_one_shot" not in payload
    assert "next_fire_at" not in payload
    assert "interval_seconds" not in payload


def test_the_reason_says_the_interval_is_not_the_schedule(registry):
    """A caller reading only `reason` must still be warned."""
    _register(registry, "daily-report", kind="cron", cron="0 9 * * *")

    reason = registry.schedule_next("daily-report").reason

    assert "0 9 * * *" in reason
    assert "not" in reason.lower()


def test_an_interval_loop_is_completely_unaffected(registry):
    """NEGATIVE CONTROL: interval loops are the common case and their payload
    must be byte-identical to before — is_cron false, all fields present."""
    _register(registry, "poll-inbox", kind="interval", interval="30s")

    plan = registry.schedule_next("poll-inbox")
    payload = plan.to_dict()

    assert plan.is_cron is False
    assert plan.cron is None
    assert payload["interval_seconds"] > 0
    assert "next_fire_at" in payload
    assert "cron_one_shot" in payload
    assert "is_cron" not in payload, "interval payloads must not grow a new key"


def test_a_cron_kind_loop_with_no_expression_falls_back_to_interval(registry):
    """Defensive: kind='cron' with no expression has nothing to schedule from,
    so the interval path is the only answer available and must still work."""
    _register(registry, "half-configured", kind="cron", interval="5m")

    plan = registry.schedule_next("half-configured")

    assert plan is not None
    assert plan.is_cron is False


def test_an_unregistered_loop_still_returns_none(registry):
    """NEGATIVE CONTROL: the not-registered contract is unchanged."""
    assert registry.schedule_next("never-registered") is None
