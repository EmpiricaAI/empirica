"""Systemd-user backend for canonical loops.

Replaces Claude Code's in-session CronCreate as the firing mechanism for
canonical loops. The actual wake-from-idle bridge into a running Claude
session is handled by the Monitor tool (armed at SessionStart), tailing the
fires log this scheduler appends to.

Design (decision logged 2026-05-15, goal f718156c):

  systemd-user timer (cadence)
       ↓ fires per schedule
  service ExecStart=empirica loop tick <inst> <name>
       ↓ appends one JSON line to ~/.empirica/loop_fires.log
       ↓
  [Monitor armed in running Claude session]
       ↓ streams each new line as an event
  Claude reacts in-context (full epistemic state intact)

Public API:

  SystemdLoopScheduler(empirica_bin="empirica")
    .enable(instance_id, name, interval)   → installs timer+service, starts
    .disable(instance_id, name)             → stops + removes from systemctl
    .status(instance_id, name)              → {active, enabled, last_trigger}
    .list_enabled()                          → list of unit names
    .unit_paths(instance_id, name)           → (timer_path, service_path)

  is_systemd_available()                     → bool capability probe

Cross-platform: this module is Linux/WSL2 only. macOS (launchd) ships in
Phase 2 with the same scheduler protocol. is_systemd_available() returns
False on macOS/Windows-native so callers can graceful-degrade.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Shared placeholder-instance + cron-detection helpers live in launchd (the
# other backend); import them so both schedulers reject the same ghost inputs
# from a single source of truth. launchd does not import systemd → no cycle.
from empirica.core.loop_scheduler.launchd import is_placeholder_instance, looks_like_cron

logger = logging.getLogger(__name__)


def cron_to_systemd_oncalendar(cron: str) -> str:
    """Map a simple 5-field cron (``M H D Mo W``) to a systemd OnCalendar string.

    Supports a fixed minute + hour with wildcard day/month/weekday — the
    canonical daily-at-a-time loops (e.g. ``17 3 * * *`` → ``*-*-* 03:17:00``).
    Ranges/steps/lists, non-integer minute/hour, and non-wildcard
    day/month/weekday raise ValueError rather than mis-schedule — a daily cron
    must never silently become a 30-second timer.
    """
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"expected a 5-field cron expression, got {cron!r}")
    minute, hour, dom, month, dow = fields
    for label, value in (("minute", minute), ("hour", hour)):
        if not value.isdigit():
            raise ValueError(f"cron {label}={value!r} must be a fixed integer for a calendar schedule (got {cron!r})")
    if not (dom == "*" and month == "*" and dow == "*"):
        raise ValueError(
            f"cron {cron!r}: only fixed minute+hour with wildcard day/month/weekday is supported "
            "for the systemd OnCalendar mapping"
        )
    return f"*-*-* {int(hour):02d}:{int(minute):02d}:00"


# Sanitize instance_id / loop name for unit-file naming. systemd accepts
# [A-Za-z0-9:-_.\\] in unit names but we want our paths URL-safe + readable.
def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in s) or "default"


def _unit_name(instance_id: str, name: str) -> str:
    return f"empirica-loop-{_safe(instance_id)}-{_safe(name)}"


def _systemd_user_dir() -> Path:
    """`~/.config/systemd/user/` — created idempotently."""
    p = Path.home() / ".config" / "systemd" / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _fires_log_path() -> Path:
    """`~/.empirica/loop_fires.log` — single tail target for the Monitor bridge."""
    p = Path.home() / ".empirica"
    p.mkdir(parents=True, exist_ok=True)
    return p / "loop_fires.log"


class SystemdUnavailable(RuntimeError):
    """Raised when systemd-user operations are attempted on a host without it."""


def is_systemd_available() -> bool:
    """Probe `systemctl --user is-system-running` returns ANY exit code → OK.

    We only need systemctl to exist and respond — even a "degraded" reply is
    fine; the timers/services we own still work. Hosts without systemd (macOS,
    Windows-native, minimal Alpine) lack systemctl entirely.
    """
    if shutil.which("systemctl") is None:
        return False
    try:
        # --user with no DBus user-bus → exits non-zero with stderr "Failed to
        # connect to bus". We don't care about exit code; the binary must just
        # respond. Timeout 2s — anything hung means broken environment.
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        # If the binary ran (even with non-zero exit), systemd-user is callable.
        # The real availability test happens when we actually enable a timer.
        return "Failed to connect to bus" not in (r.stderr or "")
    except Exception:
        return False


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run `systemctl --user <args>` with a short timeout."""
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=check,
    )


@dataclass
class LoopUnitFiles:
    """Resolved on-disk paths for one loop's systemd units."""

    timer: Path
    service: Path


@dataclass
class LoopStatus:
    """systemctl is-active / is-enabled result + last trigger timestamp."""

    name: str
    active: bool
    enabled: bool
    last_trigger: str | None = None
    next_trigger: str | None = None


# ── Unit-file templates ──────────────────────────────────────────────────


_SERVICE_TEMPLATE = """\
[Unit]
Description=Empirica canonical loop tick — {name} (instance: {instance_id})
After=default.target

[Service]
Type=oneshot
ExecStart={empirica_bin} loop tick {instance_id} {name}
# Append-only — failures here should not propagate to the timer. The body
# skill reacts to log lines via the Monitor bridge; even a missed tick is
# recoverable on the next interval.
SuccessExitStatus=0
"""

# Pure-CLI body (canonical_loops.py body_kind="cli"): run the verb DIRECTLY on
# the timer instead of the tick-append. Used for deterministic housekeeping
# (e.g. message-cleanup) that must run even when no session is alive to react
# to a heartbeat. NEVER used for claude-react / monitor / listener loops — those
# stay on the tick template so the timer can't auto-run them.
_CLI_SERVICE_TEMPLATE = """\
[Unit]
Description=Empirica canonical loop — {name} (instance: {instance_id})
After=default.target

[Service]
Type=oneshot
ExecStart={empirica_bin} {command}
SuccessExitStatus=0
"""

_TIMER_TEMPLATE = """\
[Unit]
Description=Empirica canonical loop timer — {name} (instance: {instance_id})

[Timer]
# Cadence — every {interval}. AccuracySec keeps systemd from coalescing
# multiple loop timers into one fire if they happen to align.
OnUnitActiveSec={interval}
OnBootSec={interval}
AccuracySec=1s
Unit={unit_name}.service

[Install]
WantedBy=timers.target
"""

_CRON_TIMER_TEMPLATE = """\
[Unit]
Description=Empirica canonical loop timer — {name} (instance: {instance_id})

[Timer]
# Calendar schedule (cron {cron}). Persistent=true so a fire missed while the
# machine was asleep runs once on wake, rather than being silently skipped.
OnCalendar={oncalendar}
Persistent=true
AccuracySec=1s
Unit={unit_name}.service

[Install]
WantedBy=timers.target
"""


# ── Scheduler ────────────────────────────────────────────────────────────


class SystemdLoopScheduler:
    """Manages loop timers via systemd-user.

    Stateless — the source of truth for "is this loop running?" lives in
    systemd itself (`systemctl --user is-active`). This class only knows
    where to write unit files + how to invoke systemctl.

    Args:
        empirica_bin: Absolute path to the `empirica` CLI binary used in
            the service's ExecStart. Defaults to the bare command — relies
            on the user's PATH at timer-fire time. For installer use,
            resolve this to `shutil.which('empirica')` so the unit file
            captures the canonical path (resilient to PATH drift).
    """

    def __init__(self, empirica_bin: str = "empirica"):
        self.empirica_bin = empirica_bin
        if not is_systemd_available():
            raise SystemdUnavailable(
                "systemd-user not available on this host — "
                "macOS uses launchd (Phase 2), Windows-native uses Task Scheduler (Phase 3); "
                "Linux+WSL2 should have it."
            )

    # ── Path resolution ──────────────────────────────────────────────────

    def unit_paths(self, instance_id: str, name: str) -> LoopUnitFiles:
        base = _systemd_user_dir()
        unit = _unit_name(instance_id, name)
        return LoopUnitFiles(
            timer=base / f"{unit}.timer",
            service=base / f"{unit}.service",
        )

    # ── Write + register ─────────────────────────────────────────────────

    def enable(self, instance_id: str, name: str, interval: str, body_command: str | None = None) -> LoopUnitFiles:
        """Write the .timer + .service unit files, daemon-reload, enable+start.

        Args:
            instance_id: cockpit slot name (e.g. 'cortex', 'empirica').
            name: canonical loop name (e.g. 'cortex-mailbox-poll').
            interval: systemd time spec (e.g. '30s', '5min', '1h').
            body_command: ExecStart target for a pure-CLI body (canonical_loops
                body_kind="cli", e.g. 'message-cleanup --output json') — the
                verb runs DIRECTLY on the timer. ``None`` (the default) emits the
                tick-only ExecStart (a heartbeat for a live session's Monitor),
                which is what claude-react / monitor / listener loops MUST get so
                the timer never runs them autonomously.

        Returns:
            LoopUnitFiles with on-disk paths (timer + service).

        Raises:
            subprocess.CalledProcessError on systemctl failures.
        """
        if is_placeholder_instance(instance_id):
            raise ValueError(
                f"refusing to install a loop under placeholder instance id {instance_id!r} — "
                "resolve the real ai_id first (guards ghost empirica-loop-<placeholder>-* units)"
            )
        paths = self.unit_paths(instance_id, name)
        unit_name = _unit_name(instance_id, name)

        if body_command:
            service_unit = _CLI_SERVICE_TEMPLATE.format(
                name=name,
                instance_id=instance_id,
                empirica_bin=self.empirica_bin,
                command=body_command,
            )
        else:
            service_unit = _SERVICE_TEMPLATE.format(
                name=name,
                instance_id=instance_id,
                empirica_bin=self.empirica_bin,
            )
        paths.service.write_text(service_unit, encoding="utf-8")
        # cron-shaped interval → OnCalendar timer (a daily cron must never become
        # a repeating OnUnitActiveSec interval); otherwise the interval timer.
        if looks_like_cron(interval):
            oncalendar = cron_to_systemd_oncalendar(str(interval))
            timer_unit = _CRON_TIMER_TEMPLATE.format(
                name=name,
                instance_id=instance_id,
                cron=interval,
                oncalendar=oncalendar,
                unit_name=unit_name,
            )
            sched_desc = f"cron {interval!r} → OnCalendar {oncalendar}"
        else:
            timer_unit = _TIMER_TEMPLATE.format(
                name=name,
                instance_id=instance_id,
                interval=interval,
                unit_name=unit_name,
            )
            sched_desc = f"every {interval}"
        paths.timer.write_text(timer_unit, encoding="utf-8")

        _systemctl("daemon-reload", check=True)
        _systemctl("enable", "--now", f"{unit_name}.timer", check=True)
        logger.info(f"systemd loop enabled: {unit_name}.timer ({sched_desc})")
        return paths

    def disable(self, instance_id: str, name: str) -> bool:
        """Stop + disable the timer, remove the unit files.

        Returns True if anything was removed, False if the loop wasn't installed.
        Never raises on a non-existent loop — disable is idempotent by design.
        """
        paths = self.unit_paths(instance_id, name)
        unit_name = _unit_name(instance_id, name)
        removed = False

        if paths.timer.exists() or paths.service.exists():
            _systemctl("disable", "--now", f"{unit_name}.timer", check=False)
            if paths.timer.exists():
                paths.timer.unlink()
                removed = True
            if paths.service.exists():
                paths.service.unlink()
                removed = True
            _systemctl("daemon-reload", check=False)
            logger.info(f"systemd loop disabled: {unit_name}.timer")
        return removed

    # ── Inspect ──────────────────────────────────────────────────────────

    def status(self, instance_id: str, name: str) -> LoopStatus:
        """Query systemd for is-active + is-enabled + (optionally) trigger times."""
        unit_name = _unit_name(instance_id, name)
        timer_unit = f"{unit_name}.timer"

        active = _systemctl("is-active", timer_unit).stdout.strip() == "active"
        enabled = _systemctl("is-enabled", timer_unit).stdout.strip() == "enabled"

        last_trigger: str | None = None
        next_trigger: str | None = None
        if active:
            r = _systemctl(
                "show",
                timer_unit,
                "--property=LastTriggerUSec,NextElapseUSecRealtime",
            )
            for ln in r.stdout.splitlines():
                if "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                if k == "LastTriggerUSec" and v and v != "n/a":
                    last_trigger = v
                elif k == "NextElapseUSecRealtime" and v and v != "n/a":
                    next_trigger = v

        return LoopStatus(
            name=name,
            active=active,
            enabled=enabled,
            last_trigger=last_trigger,
            next_trigger=next_trigger,
        )

    def list_enabled(self) -> list[str]:
        """Return unit names (without .timer suffix) for all empirica-loop-* timers
        that systemd currently knows about. Includes both active and inactive."""
        # `list-timers --all` shows all timers including inactive
        r = _systemctl("list-unit-files", "empirica-loop-*.timer", "--no-legend")
        out: list[str] = []
        for ln in r.stdout.splitlines():
            parts = ln.split()
            if parts and parts[0].endswith(".timer"):
                out.append(parts[0].removesuffix(".timer"))
        return out

    # ── Tick (service ExecStart target) ──────────────────────────────────

    @staticmethod
    def _instance_has_open_transaction(instance_id: str) -> bool:
        """Probe ~/.empirica/active_transaction_<inst>.json for status='open'.

        Used by tick() to skip log writes when the target AI is busy — every
        30s heartbeat in the chat while the AI is mid-transaction is just
        noise. False on any read error (conservative: when in doubt, fire).
        """
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in instance_id)
        path = Path.home() / ".empirica" / f"active_transaction_{safe}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("status") == "open"
        except Exception:
            return False

    @staticmethod
    def tick(instance_id: str, name: str, *, force: bool = False) -> Path | None:
        """Append zero-or-more JSON lines to the fires log — Monitor bridge target.

        Called by the systemd service's ExecStart. Idempotent + cheap so a
        failing tick doesn't escalate (the next interval will fire again).

        **Dispatch (Phase 2 / T6, goal f718156c):**
          - `cortex-mailbox-poll`: content-aware via `content_poll.poll_and_diff`.
            Emits one line per new-or-status-changed ECO-decided proposal.
            Empty inbox = silent (zero token cost). The AI's wake signal
            traces back to an ECO decision (David's ECO-gated autonomy property).
          - Any other loop name: legacy heartbeat — appends one timestamp
            per fire. Body skills decide what to do.

        **Self-throttling:** when the target instance has an open empirica
        transaction, skip emission entirely. AI is already working — adding
        events to the chat is noise. `force=True` bypasses (manual fires).

        Returns the fires log path when at least one line was written, None
        when throttled OR when content-poll produced zero events.
        """
        import datetime as _dt

        if not force and SystemdLoopScheduler._instance_has_open_transaction(instance_id):
            logger.debug(f"tick suppressed for {instance_id}/{name}: open transaction")
            return None

        # Content-aware path for cortex-mailbox-poll.
        if name == "cortex-mailbox-poll":
            return SystemdLoopScheduler._tick_content_aware(instance_id, name)

        # Legacy heartbeat path (other canonical loops, custom user loops).
        path = _fires_log_path()
        event = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "instance_id": instance_id,
            "loop": name,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        return path

    @staticmethod
    def _tick_content_aware(instance_id: str, name: str) -> Path | None:
        """Cortex poll-and-diff body. Emits one fires-log line per new or
        status-changed ECO-decided proposal. Silent when nothing new.

        Returns the fires-log path if any events were emitted, None if
        silent (no new content) or on credential/Cortex failure (degrade
        gracefully — next tick retries)."""
        try:
            from empirica.config.credentials_loader import get_credentials_loader
            from empirica.core.loop_scheduler.content_poll import poll_and_diff
        except Exception as e:
            logger.debug(f"content_poll import failed: {e}")
            return None

        try:
            cfg = get_credentials_loader().get_cortex_config()
            cortex_url = cfg.get("url")
            api_key = cfg.get("api_key")
        except Exception as e:
            logger.debug(f"cortex credentials load failed: {e}")
            return None

        if not cortex_url or not api_key:
            logger.debug(
                f"cortex credentials missing in ~/.empirica/credentials.yaml — "
                f"skipping content poll for {instance_id}/{name}"
            )
            return None

        events = poll_and_diff(instance_id, name, cortex_url, api_key)
        if not events:
            return None

        path = _fires_log_path()
        with open(path, "a", encoding="utf-8") as f:
            for ev in events:
                f.write(ev.to_log_line() + "\n")
        logger.info(f"content_poll emitted {len(events)} event(s) for {instance_id}/{name}")
        return path


# ── Convenience: instance-scoped discovery (used by SessionStart hook) ───


def list_active_loops_for_instance(instance_id: str) -> list[str]:
    """Names of canonical loops with an active systemd timer for this instance.

    Returns loop names (not unit names) — e.g. ['cortex-mailbox-poll'].
    Empty list when systemd isn't available or no loops are enabled, never
    raises. Caller uses this to decide whether to arm a Monitor at SessionStart.
    """
    if not is_systemd_available():
        return []
    try:
        r = _systemctl(
            "list-unit-files",
            "empirica-loop-*.timer",
            "--no-legend",
        )
    except Exception:
        return []

    safe_inst = _safe(instance_id)
    prefix = f"empirica-loop-{safe_inst}-"
    loops: list[str] = []
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if not parts or not parts[0].endswith(".timer"):
            continue
        unit = parts[0].removesuffix(".timer")
        if not unit.startswith(prefix):
            continue
        loop_name = unit.removeprefix(prefix)
        # Only count timers that are actually active (running), not just
        # installed-but-disabled.
        try:
            chk = _systemctl("is-active", f"{unit}.timer")
            if chk.stdout.strip() == "active":
                loops.append(loop_name)
        except Exception:
            continue
    return loops
