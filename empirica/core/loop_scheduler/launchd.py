"""macOS launchd backend for canonical loops — Phase 2 cross-platform parity.

Mirrors the SystemdLoopScheduler interface but writes plist files at
`~/Library/LaunchAgents/com.empirica.loop.<inst>.<name>.plist` and manages
them via `launchctl load -w` / `unload -w`. The fire mechanism (tick →
content_poll → fires.log → Monitor) is unchanged: the OS scheduler is
just timekeeper.

## Platform behavior

  - macOS desktop (David's case): agents run while the user is logged in.
    On logout they stop (same as systemd-user without linger). Fine for
    workstation use.
  - macOS headless / server: LaunchDaemons (root-level, /Library/LaunchDaemons)
    would survive logout but require sudo. Out of scope for this backend;
    if needed, ship a separate `LaunchdSystemScheduler` later.
  - Linux: `is_launchd_available()` returns False — caller falls through
    to SystemdLoopScheduler.

## Naming convention

  Label: `com.empirica.loop.<safe_instance>.<safe_name>`
  Plist: `~/Library/LaunchAgents/com.empirica.loop.<safe_instance>.<safe_name>.plist`

  Apple's reverse-DNS pattern. The dot-separator means instance_id and
  loop name get sanitized (dots removed) the same way as systemd's
  hyphen scheme.

## Interval mapping

  Systemd accepts time strings ("30s", "5min", "1h"); launchd takes
  StartInterval as seconds (integer). The constructor accepts both
  shapes — interval strings are parsed to seconds via a tiny duration
  helper.
"""

from __future__ import annotations

import logging
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe(s: str) -> str:
    """Sanitize for reverse-DNS labels — alphanumeric + hyphen only.
    Dots are reserved as label separators."""
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s) or "default"


def _label(instance_id: str, name: str) -> str:
    return f"com.empirica.loop.{_safe(instance_id)}.{_safe(name)}"


def _launch_agents_dir() -> Path:
    """`~/Library/LaunchAgents/` — created idempotently."""
    p = Path.home() / "Library" / "LaunchAgents"
    p.mkdir(parents=True, exist_ok=True)
    return p


class LaunchdUnavailable(RuntimeError):
    """Raised when launchd operations are attempted on a host without it."""


def is_launchd_available() -> bool:
    """True only on macOS with launchctl available."""
    if sys.platform != "darwin":
        return False
    return shutil.which("launchctl") is not None


def parse_interval_seconds(interval: str | int) -> int:
    """Convert systemd-style time string to seconds.

    Accepts: "30s", "5min", "5m", "2h", "1d", "90" (bare seconds), or int.
    Returns: positive integer seconds.

    The systemd `OnUnitActiveSec` syntax is richer; we cover the canonical
    loop interval formats. Anything weirder raises ValueError so callers
    don't silently install a broken 0-second timer.
    """
    if isinstance(interval, int):
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        return interval
    s = str(interval).strip().lower()
    if not s:
        raise ValueError("empty interval")

    m = re.fullmatch(r"(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|day|days)?", s)
    if not m:
        raise ValueError(f"cannot parse interval: {interval!r}")
    n, unit = int(m.group(1)), (m.group(2) or "s").lower()
    if unit in ("s", "sec", "secs", "second", "seconds"):
        secs = n
    elif unit in ("m", "min", "mins", "minute", "minutes"):
        secs = n * 60
    elif unit in ("h", "hr", "hrs", "hour", "hours"):
        secs = n * 3600
    elif unit in ("d", "day", "days"):
        secs = n * 86400
    else:
        raise ValueError(f"unknown interval unit: {unit!r}")
    if secs <= 0:
        raise ValueError(f"interval must be positive, got {secs}s")
    return secs


# Placeholder / unresolved instance ids — a loop unit must NEVER be installed
# under one; it produces ghost com.empirica.loop.<placeholder>.* units that map
# to no real practitioner and can never be reconciled. Callers must resolve the
# real ai_id before enable(). Shared by both schedulers (systemd imports these).
PLACEHOLDER_INSTANCE_IDS = frozenset({"project", "unknown", "none", ""})


def is_placeholder_instance(instance_id: str | None) -> bool:
    """True if `instance_id` is unset or a known unresolved placeholder."""
    return instance_id is None or str(instance_id).strip().lower() in PLACEHOLDER_INSTANCE_IDS


def looks_like_cron(spec: str | int) -> bool:
    """True if `spec` is a 5-field cron expression (vs an interval like '30s')."""
    return isinstance(spec, str) and len(spec.split()) == 5


_CRON_FIELD_KEYS = ("Minute", "Hour", "Day", "Month", "Weekday")


def cron_to_launchd_calendar(cron: str) -> dict[str, int]:
    """Parse a 5-field cron (``M H D Mo W``) into a launchd StartCalendarInterval
    dict. Only fixed integers + ``*`` (wildcard → omitted) are supported; ranges,
    steps and lists raise ValueError — a daily cron must never silently degrade
    to a 30-second timer, so we refuse what we can't map exactly. launchd's
    ``Weekday`` is 0-7 (0/7 = Sunday), matching cron, so it passes through."""
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"expected a 5-field cron expression, got {cron!r}")
    out: dict[str, int] = {}
    for key, field in zip(_CRON_FIELD_KEYS, fields):
        if field == "*":
            continue
        if not field.isdigit():
            raise ValueError(
                f"cron field {key}={field!r} unsupported (only fixed integers or '*' — "
                f"ranges/steps/lists can't map to a launchd calendar entry)"
            )
        out[key] = int(field)
    if not out:
        raise ValueError(f"cron {cron!r} is all-wildcard; use an interval loop, not a calendar schedule")
    return out


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=check,
    )


@dataclass
class LoopUnitFiles:
    """On-disk paths for one loop's launchd plist (single file, not pair)."""

    plist: Path

    @property
    def timer(self) -> Path:
        """systemd compat alias — handlers reference .timer; launchd has
        just one file, so we expose it under both names."""
        return self.plist

    @property
    def service(self) -> Path:
        return self.plist


@dataclass
class LoopStatus:
    """Parallels the systemd backend's LoopStatus."""

    name: str
    active: bool
    enabled: bool
    last_trigger: str | None = None
    next_trigger: str | None = None


class LaunchdLoopScheduler:
    """LaunchAgent-backed scheduler for canonical loops.

    Same API surface as SystemdLoopScheduler so handlers can dispatch by
    factory without conditional branches.
    """

    def __init__(self, empirica_bin: str = "empirica"):
        self.empirica_bin = empirica_bin
        if not is_launchd_available():
            raise LaunchdUnavailable(
                "launchd not available on this host — Linux/WSL2 uses "
                "systemd-user; Windows-native uses Task Scheduler (Phase 3)."
            )

    # ── Path resolution ──────────────────────────────────────────────────

    def unit_paths(self, instance_id: str, name: str) -> LoopUnitFiles:
        return LoopUnitFiles(plist=_launch_agents_dir() / f"{_label(instance_id, name)}.plist")

    # ── Write + register ─────────────────────────────────────────────────

    def enable(self, instance_id: str, name: str, interval: str | int) -> LoopUnitFiles:
        """Write the agent plist + launchctl load -w it (persistent until
        explicit unload). Returns the on-disk path.

        Refuses a placeholder/unresolved ``instance_id`` (no ghost
        ``com.empirica.loop.<placeholder>.*`` units). A cron-shaped ``interval``
        (5 fields) installs a ``StartCalendarInterval`` — a daily cron must never
        become a 30-second ``StartInterval`` timer; anything else is an interval
        timer in seconds.
        """
        if is_placeholder_instance(instance_id):
            raise ValueError(
                f"refusing to install a loop under placeholder instance id {instance_id!r} — "
                "resolve the real ai_id first (guards ghost com.empirica.loop.<placeholder>.* units)"
            )
        paths = self.unit_paths(instance_id, name)
        label = _label(instance_id, name)

        plist_dict = {
            "Label": label,
            "ProgramArguments": [
                self.empirica_bin,
                "loop",
                "tick",
                instance_id,
                name,
            ],
            "RunAtLoad": False,
            # Agent stdout/stderr → /tmp for debugging (macOS launchd
            # convention). S108 noqa: launchd agents expect /tmp paths
            # since the agent runs as the user with their tmpdir umask.
            "StandardOutPath": f"/tmp/empirica-loop-{_safe(instance_id)}-{_safe(name)}.log",  # noqa: S108
            "StandardErrorPath": f"/tmp/empirica-loop-{_safe(instance_id)}-{_safe(name)}.err",  # noqa: S108
            # Don't restart on exit — `tick` is a one-shot, completion is success.
            "KeepAlive": False,
        }
        if looks_like_cron(interval):
            plist_dict["StartCalendarInterval"] = cron_to_launchd_calendar(str(interval))
            sched_desc = f"cron {interval!r}"
        else:
            seconds = parse_interval_seconds(interval)
            plist_dict["StartInterval"] = seconds
            sched_desc = f"every {seconds}s"
        with open(paths.plist, "wb") as f:
            plistlib.dump(plist_dict, f)

        # `launchctl load -w <path>` loads the agent and marks it enabled
        # in the user database so it persists across logout/login.
        _launchctl("load", "-w", str(paths.plist), check=True)
        logger.info(f"launchd loop enabled: {label} ({sched_desc})")
        return paths

    def disable(self, instance_id: str, name: str) -> bool:
        """Stop the agent and remove the plist. Idempotent — returns False
        if the loop was never installed."""
        paths = self.unit_paths(instance_id, name)
        if not paths.plist.exists():
            return False
        _launchctl("unload", "-w", str(paths.plist), check=False)
        paths.plist.unlink()
        logger.info(f"launchd loop disabled: {_label(instance_id, name)}")
        return True

    # ── Inspect ──────────────────────────────────────────────────────────

    def status(self, instance_id: str, name: str) -> LoopStatus:
        """`launchctl list <label>` returns a plist dict with PID +
        LastExitStatus. Active = PID present OR last exit status was 0.
        Enabled = plist file exists."""
        label = _label(instance_id, name)
        paths = self.unit_paths(instance_id, name)
        enabled = paths.plist.exists()

        r = _launchctl("list", label)
        active = False
        last_trigger = None
        if r.returncode == 0 and r.stdout:
            # `launchctl list` output for a known label looks like:
            #   { "Label" = "..."; "LastExitStatus" = 0; "PID" = 12345; ... };
            # PID present → still running this fire (rare for one-shot)
            # LastExitStatus = 0 → last fire succeeded → consider active
            if '"PID"' in r.stdout or '"LastExitStatus" = 0' in r.stdout:
                active = True
            # Last-trigger timestamp: launchd doesn't expose this directly
            # in `list`; we'd need `log show --predicate 'process == ...'`
            # for real timestamps. Skip for now — last_trigger stays None.

        return LoopStatus(
            name=name,
            active=active,
            enabled=enabled,
            last_trigger=last_trigger,
            next_trigger=None,
        )

    def list_enabled(self) -> list[str]:
        """Return labels (com.empirica.loop.*) for all empirica loop plists
        currently in ~/Library/LaunchAgents/."""
        out: list[str] = []
        try:
            for p in _launch_agents_dir().glob("com.empirica.loop.*.plist"):
                out.append(p.stem)  # filename without .plist
        except OSError:
            pass
        return out

    # ── Tick (ExecStart equivalent — reuses systemd's tick logic) ───────

    @staticmethod
    def tick(instance_id: str, name: str, *, force: bool = False) -> Path | None:
        """Same tick semantics as SystemdLoopScheduler.tick — content-aware
        for cortex-mailbox-poll, heartbeat for others, throttled on open
        transaction. Single implementation in systemd module; we delegate
        here so plists and timers share one code path."""
        from empirica.core.loop_scheduler.systemd import SystemdLoopScheduler

        return SystemdLoopScheduler.tick(instance_id, name, force=force)
