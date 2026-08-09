"""macOS launchd loops must be representable and must route to launchctl.

**What these tests can and cannot prove.** They run on Linux. They verify that
`launchd` is a storable `scheduler_kind`, that each backend declares its own
kind, and that dispatch selects the OS-scheduled path for it. They do NOT prove
`launchctl` accepts the generated plists, that enable/disable load and unload
agents, or that a paused launchd loop stops firing. That needs a mac, and the
confirmation to ask for is **absence from `loop_fires.log`** — not `loop list`
reporting paused, which is the distinction the whole pause thread turned on.

The defect, verified by reading rather than inferred from the empty column:
`LoopRegistry.update()` raises `ValueError` on any `scheduler_kind` outside
`VALID_SCHEDULER_KIND`, and `launchd` was not in it. So the value was
*unsettable* — every macOS row was wrong by construction, recorded as `None` or
as a wrong-but-valid `systemd-user`. Measured null on 17/17 loops on one box and
10/10 on another, which reads as "nobody sets this field" and is actually
"nobody can". An empty column has two explanations that look identical from the
data.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit.loop_registry import (
    OS_SCHEDULED_KINDS,
    VALID_SCHEDULER_KIND,
    is_os_scheduled,
)

# ─── Representability ──────────────────────────────────────────────────


def test_launchd_is_a_valid_scheduler_kind():
    """THE DEFECT. Absent from the tuple meant unstorable, not merely unused."""
    assert "launchd" in VALID_SCHEDULER_KIND


def test_the_registry_accepts_a_launchd_heartbeat(tmp_path, monkeypatch):
    """End-to-end on the storage path: the value must round-trip, not raise."""
    from empirica.core.cockpit import loop_registry as lr

    # EMPIRICA_DIR too: the atomic write puts its temp file there and
    # os.replace()s onto the target, which is a cross-device link if only the
    # target moves.
    monkeypatch.setattr(lr, "EMPIRICA_DIR", tmp_path)
    monkeypatch.setattr(lr, "registry_path", lambda _i: tmp_path / "loops_test.json")
    reg = lr.LoopRegistry("test-instance")
    reg.register(name="probe", kind="interval", interval="30s", description="")
    reg.heartbeat(name="probe", status="ok", scheduler_kind="launchd")

    # It lives on the nested SchedulingState, not on LoopEntry directly.
    assert reg.get("probe").scheduling.scheduler_kind == "launchd"


def test_an_unknown_scheduler_kind_still_raises(tmp_path, monkeypatch):
    """Widening the vocabulary must not disable the validator."""
    from empirica.core.cockpit import loop_registry as lr

    monkeypatch.setattr(lr, "EMPIRICA_DIR", tmp_path)
    monkeypatch.setattr(lr, "registry_path", lambda _i: tmp_path / "loops_test.json")
    reg = lr.LoopRegistry("test-instance")
    reg.register(name="probe", kind="interval", interval="30s", description="")

    with pytest.raises(ValueError, match="scheduler_kind"):
        reg.heartbeat(name="probe", status="ok", scheduler_kind="upstart")


# ─── Each backend owns its kind ────────────────────────────────────────


def test_each_backend_declares_its_own_kind():
    """The enable handler hard-coded 'systemd-user' while the scheduler above it
    was already platform-selected — so macOS stamped launchd loops as systemd.
    Wrong AND valid, therefore invisible to the validator."""
    from empirica.core.loop_scheduler.launchd import LaunchdLoopScheduler
    from empirica.core.loop_scheduler.systemd import SystemdLoopScheduler

    assert SystemdLoopScheduler.SCHEDULER_KIND == "systemd-user"
    assert LaunchdLoopScheduler.SCHEDULER_KIND == "launchd"


@pytest.mark.parametrize("backend", ["systemd", "launchd"])
def test_every_backend_kind_is_storable(backend: str):
    """A backend that declares a kind the registry rejects would raise on its
    own first heartbeat — on the platform where it is the only option."""
    mod = __import__(f"empirica.core.loop_scheduler.{backend}", fromlist=["x"])
    cls = getattr(mod, "LaunchdLoopScheduler" if backend == "launchd" else "SystemdLoopScheduler")

    assert cls.SCHEDULER_KIND in VALID_SCHEDULER_KIND


# ─── Dispatch ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["launchd", "systemd-user", "systemd", "SYSTEMD-USER", " launchd "])
def test_os_scheduled_kinds_route_to_the_backend(kind: str):
    """`launchd` is the regression; the systemd spellings are why the original
    prefix test existed and must keep working."""
    assert is_os_scheduled(kind)


@pytest.mark.parametrize("kind", ["cron-create", "system-cron", "at-queue", "unknown", None, ""])
def test_other_kinds_keep_the_legacy_path(kind):
    """Over-routing would send CronCreate loops to a scheduler that never
    installed them — the failure in the other direction."""
    assert not is_os_scheduled(kind)


def test_every_os_scheduled_kind_is_a_valid_kind():
    """The predicate and the vocabulary live in one file so they cannot drift;
    this pins that they actually agree."""
    assert set(VALID_SCHEDULER_KIND) >= OS_SCHEDULED_KINDS


def test_the_tui_dispatch_sites_use_the_predicate():
    """Both sites tested `scheduler_kind.startswith("systemd")`, which silently
    sent every launchd loop down the CronCreate path. Structural, because the
    TUI is not exercised by unit tests."""
    from pathlib import Path

    src = Path(__import__("empirica.cli.tui.cockpit_app", fromlist=["x"]).__file__).read_text()
    dispatch = src.count("if is_os_scheduled(scheduler_kind):")

    assert dispatch == 2, f"expected both dispatch sites to use the predicate, found {dispatch}"
