"""Tests for the system-level canonical loop catalog.

The catalog backs the TUI cockpit's auto-install fallback: when an
instance has no loops registered AND no project.yaml cockpit.loops
block, the cockpit falls back to CANONICAL_LOOPS to install a sane
default set.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit.canonical_loops import (
    CANONICAL_LOOPS,
    canonical_loop_by_name,
    canonical_loop_names,
)


def test_catalog_is_non_empty():
    """The catalog must ship at least one entry — empty defeats its
    purpose (the TUI would just fall through to the CLI hint)."""
    assert len(CANONICAL_LOOPS) > 0


def test_every_entry_has_required_fields():
    """Each entry must satisfy the loop-install-request schema:
    name + kind + (cron OR interval) + description."""
    for entry in CANONICAL_LOOPS:
        assert isinstance(entry, dict)
        assert entry.get("name"), f"missing name in {entry}"
        assert entry.get("kind") in {"interval", "cron", "monitor"}, f"invalid kind in {entry}"
        # Must have either cron or interval (depending on kind)
        has_schedule = bool(entry.get("cron") or entry.get("interval"))
        assert has_schedule, f"no schedule (cron or interval) in {entry}"
        assert isinstance(entry.get("description"), str), f"missing/non-string description in {entry}"


def test_adaptive_entries_have_backoff_floor_and_ceiling():
    """Interval entries that ship with backoff must declare both
    base_interval and max_interval so the decay is bounded both ways."""
    for entry in CANONICAL_LOOPS:
        has_base = bool(entry.get("base_interval"))
        has_max = bool(entry.get("max_interval"))
        # Either neither (no backoff) or both (bounded backoff) — not one
        # without the other.
        assert has_base == has_max, f"{entry.get('name')} has half-specified backoff (base={has_base}, max={has_max})"


def test_names_are_unique():
    """Two entries with the same name would collide on install — the
    second would either overwrite the first silently or fail."""
    names = [e.get("name") for e in CANONICAL_LOOPS]
    assert len(names) == len(set(names)), f"duplicate names in catalog: {names}"


def test_canonical_loop_names_returns_all_names():
    assert canonical_loop_names() == [e["name"] for e in CANONICAL_LOOPS]


def test_canonical_loop_by_name_hit():
    """Roundtrip: pull a known entry by its name."""
    name = CANONICAL_LOOPS[0]["name"]
    entry = canonical_loop_by_name(name)
    assert entry is not None
    assert entry["name"] == name


def test_canonical_loop_by_name_miss():
    """Unknown name returns None — never raises."""
    assert canonical_loop_by_name("not-a-real-loop-zzz9") is None


def test_cortex_mailbox_poll_preset_present():
    """The orchestration spine ships in the catalog. If we remove it,
    that's a deliberate decision — failing this test forces the
    decision to be conscious."""
    entry = canonical_loop_by_name("cortex-mailbox-poll")
    assert entry is not None, (
        "cortex-mailbox-poll is the orchestration spine — if you removed it, log a decision-log explaining why"
    )
    # Adaptive cadence is the whole point — fast base, idle ceiling
    assert entry["base_interval"]
    assert entry["max_interval"]
    # 30s base is intentional — David's requirement: must be faster
    # than 15m for true interactive work
    assert entry["base_interval"] == "30s"


@pytest.fixture
def fake_project_no_yaml(tmp_path):
    """A project path with no .empirica/project.yaml — triggers the
    canonical catalog fallback when used with project_loops."""
    return tmp_path / "fake_project"


def test_fallback_path_returns_canonical_when_project_has_none(fake_project_no_yaml):
    """The integration-shaped check: project_loops() returns [] for an
    empty project, which is the trigger condition for the canonical
    fallback in _install_loops_from_project."""
    from empirica.core.cockpit.project_cockpit_config import project_loops

    assert project_loops(fake_project_no_yaml) == []
    # CANONICAL_LOOPS is the fallback the TUI uses when this returns []
    assert len(CANONICAL_LOOPS) > 0


def test_cortex_mailbox_poll_uses_systemd_scheduler():
    """Phase 1c (goal f718156c): cortex-mailbox-poll is the pilot loop for
    the systemd scheduler path. The TUI's _install_loops_from_project +
    action_toggle_events dispatch on this field. Must match
    VALID_SCHEDULER_KIND in loop_registry.py — using the wrong value here
    causes the loop_registry.heartbeat() validation to silently reject the
    scheduler_kind stamp (caught during real-host smoke-test 2026-05-15).
    Removing the field reverts to the legacy CronCreate path — log a
    decision if you do so."""
    entry = canonical_loop_by_name("cortex-mailbox-poll")
    assert entry is not None
    assert entry.get("scheduler_kind") == "systemd-user", (
        "cortex-mailbox-poll must carry scheduler_kind='systemd-user' (the "
        "canonical value in VALID_SCHEDULER_KIND) so the TUI routes through "
        "systemctl rather than CronCreate and the registry stamp persists"
    )


def test_body_kind_discriminator_gates_autonomous_execution():
    """The body_kind discriminator is what grants (or withholds) autonomous
    timer execution — the ecodex loop-enable fix (prop_s7ac5).

    - message-cleanup is a pure-CLI body: body_kind='cli' + a body_command that
      the scheduler runs DIRECTLY, so daily housekeeping happens even with no
      session alive.
    - cortex-mailbox-poll is claude-react (needs the AI to poll + react): it MUST
      NOT carry a body_command, so the scheduler emits the tick-only ExecStart
      and the timer can never run it autonomously. Wake-on-event stays its sole
      trigger (David 2026-07-22 — the fix must not re-introduce auto-looping for
      monitors/listeners).
    """
    cleanup = canonical_loop_by_name("message-cleanup")
    assert cleanup is not None
    assert cleanup.get("body_kind") == "cli"
    assert cleanup.get("body_command"), "a cli body must declare its ExecStart body_command"

    poll = canonical_loop_by_name("cortex-mailbox-poll")
    assert poll is not None
    assert poll.get("body_kind") == "claude-react"
    assert poll.get("body_command") is None, (
        "cortex-mailbox-poll must NOT carry a body_command — a direct ExecStart "
        "would re-introduce autonomous polling on wake-on-event seats"
    )


def test_no_claude_react_loop_carries_a_body_command():
    """Fleet-wide invariant: only cli bodies get a body_command. Any monitor /
    listener / poll loop that gained one would start auto-running on its timer."""
    for entry in CANONICAL_LOOPS:
        if entry.get("body_kind") != "cli":
            assert entry.get("body_command") is None, (
                f"{entry.get('name')!r} is not body_kind='cli' but carries a "
                f"body_command — that would grant it autonomous timer execution"
            )
