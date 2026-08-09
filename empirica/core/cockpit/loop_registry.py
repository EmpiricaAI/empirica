"""Loop registry — per-instance JSON registry for `empirica loop` subcommand.

Replaces ad-hoc /tmp/{name}.disabled sentinel files with a uniform pattern:

  ~/.empirica/loops_{instance_id}.json   — declarative registry of loops
  ~/.empirica/loop_paused_{instance_id}_{name}  — empty file == paused

`register` is idempotent on (instance_id, name). `heartbeat` mutates the
last_run/last_status/last_message fields. Pause/resume operate on the
sidecar file so they're trivially atomic against the registry write.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / ".empirica"
VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
VALID_KIND = ("cron", "interval", "monitor")
VALID_STATUS = ("ok", "fail")
VALID_BACKOFF = ("none", "exponential")
# 'paused' added per PROPOSAL_LOOP_SELF_SCHEDULING — when the body
# short-circuits on the pause check it heartbeats with result=paused
# so the streak math doesn't treat the pause-skip as an empty fire.
VALID_RESULT = ("found", "empty", "fail", "paused")
VALID_SCHEDULER_KIND = (
    "cron-create",
    "systemd-user",
    # macOS. Its absence was not cosmetic: `update()` RAISES on any kind
    # outside this tuple, so `launchd` was unsettable and every macOS row was
    # wrong by construction — recorded as None or as a wrong-but-valid value.
    # Measured null on 17/17 loops on one box and 10/10 on another, which reads
    # as "nobody sets this field" and is actually "nobody CAN". An empty column
    # has two explanations and they look identical from the data.
    "launchd",
    "system-cron",
    "at-queue",
    "unknown",
)

# Kinds scheduled by the OS, where enable/disable routes through the scheduler
# backend (systemctl / launchctl) rather than the legacy CronCreate pending
# file. Named here, beside the vocabulary it derives from, because the callers
# were testing `scheduler_kind.startswith("systemd")` — a prefix match against
# one platform's spelling, which silently sent every launchd loop down the
# CronCreate path. Matching on another layer's vocabulary by prefix is the same
# defect class as the listener warning that keyed on "proposal_event" while the
# emitter wrote statuses.
OS_SCHEDULED_KINDS = frozenset({"systemd-user", "launchd"})


def is_os_scheduled(scheduler_kind: str | None) -> bool:
    """True when enable/disable must route through the scheduler backend.

    Tolerates the historical `systemd` / `systemd-user` spelling split, which is
    why the old prefix test existed — that part was reasonable, the platform
    assumption baked into it was not.
    """
    if not scheduler_kind:
        return False
    kind = scheduler_kind.strip().lower()
    return kind in OS_SCHEDULED_KINDS or kind.startswith("systemd")


# Default backoff envelope when caller passes --backoff exponential without
# explicit floor/ceiling. 15m base × 2^N capped at 4h matches the proposal.
DEFAULT_BASE_INTERVAL_S = 15 * 60
DEFAULT_MAX_INTERVAL_S = 4 * 60 * 60

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])$", re.IGNORECASE)


def parse_duration(text: str | None) -> int | None:
    """Parse '15m', '4h', '30s', '1d' to seconds. Bare integers → minutes."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).strip().lower()
    if not s:
        return None
    m = _DURATION_RE.match(s)
    if m:
        value = float(m.group(1))
        unit = m.group(2)
        return int(value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])
    try:
        return int(float(s) * 60)  # bare number → minutes
    except ValueError:
        return None


def format_duration(seconds: int | None) -> str:
    """Reverse of parse_duration — pick the largest unit that's whole-ish."""
    if seconds is None:
        return ""
    if seconds <= 0:
        return "0s"
    for unit_s, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds % unit_s == 0:
            return f"{seconds // unit_s}{unit}"
    return f"{seconds}s"


def _safe_suffix(text: str) -> str:
    return text.replace("/", "-").replace("%", "")


def registry_path(instance_id: str) -> Path:
    return EMPIRICA_DIR / f"loops_{_safe_suffix(instance_id)}.json"


def loop_pause_path(instance_id: str, name: str) -> Path:
    return EMPIRICA_DIR / f"loop_paused_{_safe_suffix(instance_id)}_{_safe_suffix(name)}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _validate_name(name: str) -> None:
    if not VALID_NAME.match(name):
        raise ValueError(f"Invalid loop name '{name}' — must match {VALID_NAME.pattern}")


def _validate_kind(kind: str) -> None:
    if kind not in VALID_KIND:
        raise ValueError(f"Invalid kind '{kind}' — must be one of {VALID_KIND}")


def is_loop_paused(instance_id: str, name: str) -> bool:
    return loop_pause_path(instance_id, name).exists()


def set_loop_paused(instance_id: str, name: str, paused: bool) -> bool:
    """Set or clear the pause sidecar file for a loop. Returns paused state."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = loop_pause_path(instance_id, name)
    if paused:
        path.write_text("", encoding="utf-8")
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return path.exists()


@dataclass
class BackoffState:
    """Per-loop exponential-backoff bookkeeping (PROPOSAL_LOOP_BACKOFF.md).

    `policy='none'` is the legacy / default behavior — should_fire() always
    returns True regardless of streak. `policy='exponential'` uses
    base_interval_seconds * 2^empty_streak, capped at max_interval_seconds.

    next_fire_threshold is the wall-clock ISO time after which the loop
    body is allowed to do work. Empty fires advance it; found/fail fires
    snap it back to base.
    """

    policy: str = "none"  # 'none' | 'exponential'
    base_interval_seconds: int | None = None
    max_interval_seconds: int | None = None
    empty_streak: int = 0
    next_fire_threshold: str | None = None  # ISO-8601 UTC, or None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "policy": self.policy,
            "base_interval_seconds": self.base_interval_seconds,
            "max_interval_seconds": self.max_interval_seconds,
            "empty_streak": self.empty_streak,
            "next_fire_threshold": self.next_fire_threshold,
        }
        # Surface human-readable duration strings for renderers/JSON consumers.
        if self.base_interval_seconds:
            d["base_interval"] = format_duration(self.base_interval_seconds)
        if self.max_interval_seconds:
            d["max_interval"] = format_duration(self.max_interval_seconds)
        d["current_interval"] = format_duration(self.current_interval_seconds())
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BackoffState:
        if not data:
            return cls()
        return cls(
            policy=data.get("policy", "none") or "none",
            base_interval_seconds=_safe_int(data.get("base_interval_seconds")),
            max_interval_seconds=_safe_int(data.get("max_interval_seconds")),
            empty_streak=int(data.get("empty_streak", 0) or 0),
            next_fire_threshold=data.get("next_fire_threshold"),
        )

    def current_interval_seconds(self) -> int | None:
        """Compute base × 2^streak, capped at max. None when policy=none."""
        if self.policy != "exponential" or not self.base_interval_seconds:
            return None
        # Avoid 2**100 — clamp the exponent at 16 (covers any sane envelope).
        exp = min(self.empty_streak, 16)
        candidate = self.base_interval_seconds * (2**exp)
        if self.max_interval_seconds:
            candidate = min(candidate, self.max_interval_seconds)
        return candidate

    def is_at_base(self) -> bool:
        if self.policy != "exponential":
            return True
        return self.empty_streak == 0


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _humanize_seconds(seconds: int) -> str:
    """Compact duration string for human-readable reasons."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        rem = seconds % 3600
        if rem == 0:
            return f"{seconds // 3600}h"
        return f"{seconds // 3600}h{rem // 60}m"
    return f"{seconds // 86400}d"


def cron_pin_one_shot(when: datetime) -> str:
    """5-field cron expression pinned to a single wall-clock minute (UTC).

    Format: 'M H D M *' (day-of-week wildcarded). For one-shot
    scheduling — the body re-installs each fire, so day-of-week
    matching is irrelevant. Caller is expected to pass `recurring=false`
    when handing this to a CronCreate-style scheduler.
    """
    return f"{when.minute} {when.hour} {when.day} {when.month} *"


@dataclass
class SchedulePlan:
    """Output of `schedule_next`: when to fire next + why.

    `cron_one_shot` is convenience for callers that hand cron strings
    directly to CronCreate / `at`. `interval_seconds` is canonical for
    callers that have their own scheduler API.
    """

    fire_at: datetime
    interval_seconds: int
    current_streak: int
    reason: str
    # Set only for kind='cron' loops, where the expression IS the schedule and
    # the interval fields are not a computed next fire. Absent (None) means the
    # interval fields are authoritative, which is the interval-loop case.
    cron: str | None = None

    @property
    def cron_one_shot(self) -> str:
        return cron_pin_one_shot(self.fire_at)

    @property
    def is_cron(self) -> bool:
        """True when this loop's schedule is a cron expression.

        Callers must not read ``interval_seconds`` as a next-fire delay when this
        is set — it is 0, and ``fire_at`` is 'now', because neither is known
        without cron arithmetic.
        """
        return self.cron is not None

    def to_dict(self) -> dict[str, Any]:
        if self.cron is not None:
            # A cron loop has no computed next fire, so every field that would
            # imply one is omitted rather than filled with a placeholder.
            # `cron_one_shot` is the trap in particular: it pins a one-shot to
            # `fire_at`, which here is *now*, so a caller passing it to
            # CronCreate would fire immediately and believe it had scheduled
            # tomorrow's 09:00 run.
            return {
                "is_cron": True,
                "cron": self.cron,
                "current_streak": self.current_streak,
                "reason": self.reason,
            }
        return {
            "next_fire_at": self.fire_at.isoformat(),
            "interval_seconds": self.interval_seconds,
            "current_streak": self.current_streak,
            "reason": self.reason,
            "cron_one_shot": self.cron_one_shot,
        }


@dataclass
class SchedulingState:
    """Self-scheduling bookkeeping (PROPOSAL_LOOP_SELF_SCHEDULING.md).

    Self-scheduling is the only mode — there's no recurring fallback.
    The body owns the schedule: each fire computes the next fire's
    timestamp from backoff state and installs a one-shot scheduler job
    pinned to that wall-clock time. `next_scheduled_job_id` is the
    opaque identifier the scheduler returned; pause uses it to cancel
    the future fire.

    Fields are nullable because:
      - scheduler_kind: known after registration / first fire
      - next_scheduled_job_id: known after the body installs the next fire
      - next_fire_at: known after schedule-next computes it
    """

    scheduler_kind: str | None = None
    next_scheduled_job_id: str | None = None
    next_fire_at: str | None = None  # ISO-8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduler_kind": self.scheduler_kind,
            "next_scheduled_job_id": self.next_scheduled_job_id,
            "next_fire_at": self.next_fire_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SchedulingState:
        if not data:
            return cls()
        return cls(
            scheduler_kind=data.get("scheduler_kind"),
            next_scheduled_job_id=data.get("next_scheduled_job_id"),
            next_fire_at=data.get("next_fire_at"),
        )


@dataclass
class LoopEntry:
    name: str
    kind: str  # 'cron' | 'interval' | 'monitor'
    cron: str | None = None
    interval: str | None = None  # e.g. "5m"
    description: str = ""
    registered_at: str = field(default_factory=_now_iso)
    last_run: str | None = None
    last_status: str | None = None  # 'ok' | 'fail'
    last_message: str | None = None
    last_result: str | None = None  # 'found' | 'empty' | 'fail' | 'paused'
    backoff: BackoffState = field(default_factory=BackoffState)
    scheduling: SchedulingState = field(default_factory=SchedulingState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "cron": self.cron,
            "interval": self.interval,
            "description": self.description,
            "registered_at": self.registered_at,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "last_result": self.last_result,
            "backoff": self.backoff.to_dict(),
            "scheduling": self.scheduling.to_dict(),
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> LoopEntry:
        return cls(
            name=name,
            kind=data.get("kind", "monitor"),
            cron=data.get("cron"),
            interval=data.get("interval"),
            description=data.get("description", ""),
            registered_at=data.get("registered_at", _now_iso()),
            last_run=data.get("last_run"),
            last_status=data.get("last_status"),
            last_message=data.get("last_message"),
            last_result=data.get("last_result"),
            backoff=BackoffState.from_dict(data.get("backoff")),
            scheduling=SchedulingState.from_dict(data.get("scheduling")),
        )


class LoopRegistry:
    """Per-instance loop registry stored at ~/.empirica/loops_{instance_id}.json.

    All mutating methods read-modify-write the JSON file atomically (tempfile
    + rename). Pause state is a sidecar file, not a registry field — that
    keeps pause toggles independent of registry rewrites and lets the loop
    runner check pause without parsing the registry.
    """

    def __init__(self, instance_id: str, label: str | None = None):
        if not instance_id:
            raise ValueError("instance_id required")
        self.instance_id = instance_id
        self.path = registry_path(instance_id)
        self._label = label

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "instance_id": self.instance_id,
                "instance_label": self._label,
                "loops": {},
            }
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("instance_id", self.instance_id)
            data.setdefault("instance_label", self._label)
            data.setdefault("loops", {})
            return data
        except (OSError, json.JSONDecodeError):
            return {
                "instance_id": self.instance_id,
                "instance_label": self._label,
                "loops": {},
            }

    def _write(self, data: dict[str, Any]) -> None:
        EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
        # Atomic write: tempfile in same dir, fsync, rename
        fd, tmp_path = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(EMPIRICA_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_loops(self) -> list[LoopEntry]:
        data = self._read()
        return [LoopEntry.from_dict(name, entry) for name, entry in data["loops"].items()]

    def get(self, name: str) -> LoopEntry | None:
        data = self._read()
        entry = data["loops"].get(name)
        if entry is None:
            return None
        return LoopEntry.from_dict(name, entry)

    def register(
        self,
        name: str,
        kind: str,
        cron: str | None = None,
        interval: str | None = None,
        description: str = "",
        backoff_policy: str | None = None,
        base_interval: str | None = None,
        max_interval: str | None = None,
    ) -> LoopEntry:
        """Register a loop. Idempotent on (instance_id, name).

        Re-registering an existing loop preserves runtime state (last_run,
        last_status, last_message, last_result, empty_streak,
        next_fire_threshold) but updates declarative fields including the
        backoff envelope. Lets callers safely re-issue `register` at
        startup without losing history.

        Backoff:
          backoff_policy='none' (default) → no backoff
          backoff_policy='exponential'    → empty fires advance threshold
            base_interval defaults to 15m, max_interval to 4h.
        """
        _validate_name(name)
        _validate_kind(kind)
        if backoff_policy is not None and backoff_policy not in VALID_BACKOFF:
            raise ValueError(f"Invalid backoff '{backoff_policy}' — must be one of {VALID_BACKOFF}")

        data = self._read()
        existing = data["loops"].get(name)

        if existing:
            entry = LoopEntry.from_dict(name, existing)
            entry.kind = kind
            entry.cron = cron
            entry.interval = interval
            entry.description = description
        else:
            entry = LoopEntry(
                name=name,
                kind=kind,
                cron=cron,
                interval=interval,
                description=description,
            )

        # Backoff envelope: only mutate if caller specified a policy.
        if backoff_policy is not None:
            entry.backoff.policy = backoff_policy
            if backoff_policy == "exponential":
                base_s = parse_duration(base_interval) or DEFAULT_BASE_INTERVAL_S
                max_s = parse_duration(max_interval) or DEFAULT_MAX_INTERVAL_S
                if max_s < base_s:
                    raise ValueError(f"max_interval ({max_s}s) must be >= base_interval ({base_s}s)")
                entry.backoff.base_interval_seconds = base_s
                entry.backoff.max_interval_seconds = max_s
            else:
                # Switching to 'none' clears the envelope and any pending threshold.
                entry.backoff.base_interval_seconds = None
                entry.backoff.max_interval_seconds = None
                entry.backoff.empty_streak = 0
                entry.backoff.next_fire_threshold = None

        data["loops"][name] = entry.to_dict()
        if self._label:
            data["instance_label"] = self._label
        self._write(data)
        return entry

    def unregister(self, name: str) -> bool:
        """Remove a loop from the registry. Returns True if removed, False if absent.

        Also clears the pause sidecar and any pending install/uninstall
        request files so resurrection of the same name starts clean —
        closes the orphan-install gap where unregister could leave a
        pending file that re-arms the loop on the next prompt.
        """
        _validate_name(name)
        data = self._read()
        if name not in data["loops"]:
            return False
        del data["loops"][name]
        self._write(data)
        set_loop_paused(self.instance_id, name, False)
        # Lazy import — install/uninstall request modules import from us.
        from empirica.core.cockpit import (
            loop_install_request,
            loop_uninstall_request,
        )

        for path in (
            loop_install_request.pending_path(self.instance_id, name),
            loop_uninstall_request.pending_path(self.instance_id, name),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return True

    def set_interval(self, name: str, interval: str) -> LoopEntry:
        _validate_name(name)
        data = self._read()
        if name not in data["loops"]:
            raise KeyError(f"Loop '{name}' not registered")
        data["loops"][name]["interval"] = interval
        self._write(data)
        return LoopEntry.from_dict(name, data["loops"][name])

    def heartbeat(
        self,
        name: str,
        status: str = "ok",
        message: str | None = None,
        result: str | None = None,
        next_scheduled_job_id: str | None = None,
        scheduler_kind: str | None = None,
    ) -> LoopEntry:
        """Record a fire — updates last_run/last_status/last_message and,
        when backoff policy is exponential, the streak + next_fire_threshold.

        `result` (PROPOSAL_LOOP_SELF_SCHEDULING):
          'found'  → new work happened — reset streak, threshold = now + base
          'empty'  → fire ran cleanly, nothing to do — advance streak
          'fail'   → errored — reset streak, threshold = now + base
                     (failures retry at base, don't compound delay)
          'paused' → body short-circuited on pause check — no streak math
                     (the loop is silent; backoff state freezes until resume)

        If `result` is None, it's inferred from `status`:
          status='ok'   → result='empty' (conservative)
          status='fail' → result='fail'

        `next_scheduled_job_id` records the opaque scheduler job id for
        the next fire (so pause can cancel it). `scheduler_kind` records
        which scheduler installed it ('cron-create', 'systemd-user', ...).

        If the loop isn't registered yet, auto-registers as a monitor loop.
        """
        _validate_name(name)
        if status not in VALID_STATUS:
            raise ValueError(f"Invalid status '{status}' — must be one of {VALID_STATUS}")
        if result is not None and result not in VALID_RESULT:
            raise ValueError(f"Invalid result '{result}' — must be one of {VALID_RESULT}")
        if scheduler_kind is not None and scheduler_kind not in VALID_SCHEDULER_KIND:
            raise ValueError(f"Invalid scheduler_kind '{scheduler_kind}' — must be one of {VALID_SCHEDULER_KIND}")

        if result is None:
            result = "fail" if status == "fail" else "empty"

        data = self._read()
        if name not in data["loops"]:
            self.register(name=name, kind="monitor", description="auto-registered via heartbeat")
            data = self._read()

        entry = LoopEntry.from_dict(name, data["loops"][name])
        now = datetime.now(tz=timezone.utc)
        entry.last_run = now.isoformat()
        entry.last_status = status
        entry.last_message = message
        entry.last_result = result

        # Backoff math (no-op when policy=none, and when result=='paused':
        # pause is a no-state transition — backoff freezes mid-stretch and
        # resumes at the same streak when the body fires again).
        if entry.backoff.policy == "exponential" and entry.backoff.base_interval_seconds and result != "paused":
            if result == "empty":
                entry.backoff.empty_streak += 1
            else:  # 'found' or 'fail' → reset
                entry.backoff.empty_streak = 0
            interval_s = entry.backoff.current_interval_seconds() or entry.backoff.base_interval_seconds
            threshold = now.timestamp() + interval_s
            entry.backoff.next_fire_threshold = datetime.fromtimestamp(threshold, tz=timezone.utc).isoformat()

        # Scheduling bookkeeping — only mutate when the caller provided
        # values; otherwise preserve whatever was already there.
        if scheduler_kind is not None:
            entry.scheduling.scheduler_kind = scheduler_kind
        if next_scheduled_job_id is not None:
            # Empty string means "clear" (e.g. when pause cancels the next fire).
            entry.scheduling.next_scheduled_job_id = next_scheduled_job_id or None

        data["loops"][name] = entry.to_dict()
        self._write(data)
        return entry

    def should_fire(self, name: str) -> tuple[bool, str]:
        """Check whether the loop body should do work this fire.

        Returns (should_fire, reason). Used by `empirica loop should-fire <NAME>`
        which the loop body calls right after the pause check.

        Reasons:
          'no policy'       → backoff disabled, always fire
          'past threshold'  → wall clock has passed next_fire_threshold
          'no threshold'    → policy enabled but threshold not yet set
          'before threshold' → still in backoff window, skip this fire
          'no loop'         → not registered (treat as fire — caller decides)
        """
        entry = self.get(name)
        if entry is None:
            return True, "no loop"
        if entry.backoff.policy != "exponential":
            return True, "no policy"
        threshold_iso = entry.backoff.next_fire_threshold
        if not threshold_iso:
            return True, "no threshold"
        try:
            threshold = datetime.fromisoformat(threshold_iso.replace("Z", "+00:00"))
            if threshold.tzinfo is None:
                threshold = threshold.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return True, "invalid threshold"
        if datetime.now(tz=timezone.utc) >= threshold:
            return True, "past threshold"
        return False, "before threshold"

    def poke(self, name: str) -> LoopEntry | None:
        """Manual escape hatch — zero the streak and clear the threshold.

        For when the user knows new work just arrived and doesn't want to
        wait through backoff. Returns the updated entry, or None if the
        loop isn't registered.
        """
        _validate_name(name)
        data = self._read()
        if name not in data["loops"]:
            return None
        entry = LoopEntry.from_dict(name, data["loops"][name])
        entry.backoff.empty_streak = 0
        entry.backoff.next_fire_threshold = None
        data["loops"][name] = entry.to_dict()
        self._write(data)
        return entry

    def schedule_next(self, name: str) -> SchedulePlan | None:
        """Compute the next-fire timestamp for a self-scheduling loop.

        Uses the entry's current backoff state — `empty_streak` from the
        most recent heartbeat, `policy='exponential'` to stretch the
        interval, base+max as floor/ceiling. When backoff is `none`, the
        interval is the base (or 15m default).

        Returns None when the loop isn't registered. Otherwise returns a
        SchedulePlan with fire_at + interval_seconds + cron_one_shot
        (5-field UTC pinned to that exact timestamp).

        The plan is also stamped onto entry.scheduling.next_fire_at so
        the registry reflects the body's installed schedule.
        """
        _validate_name(name)
        data = self._read()
        if name not in data["loops"]:
            return None

        entry = LoopEntry.from_dict(name, data["loops"][name])
        now = datetime.now(tz=timezone.utc)

        # A cron loop's schedule is its cron expression. Everything below derives
        # a fire time from interval + backoff, which for kind='cron' is not a
        # worse answer — it is an unrelated one, presented in the same shape as a
        # real one. "Every day at 09:00" came back as "in 15 minutes", and the
        # caller had no way to tell that the number was invented (#396,
        # graemester, second defect).
        #
        # Computing a true next-fire needs cron arithmetic, which needs a
        # dependency this package does not have. Rather than add one immediately
        # before a release, or hand-roll date arithmetic that would be wrong in
        # its own quieter way, the plan now carries the cron expression and says
        # plainly that the interval fields are not the schedule. Callers that own
        # a cron scheduler (CronCreate, `at`) have what they need; callers that
        # would have silently trusted interval_seconds now get told not to.
        if entry.kind == "cron" and entry.cron:
            return SchedulePlan(
                fire_at=now,
                interval_seconds=0,
                current_streak=entry.backoff.empty_streak,
                reason=(
                    f"cron loop — schedule is the expression {entry.cron!r}, not an interval. "
                    "interval_seconds/fire_at are NOT a computed next fire; hand the cron "
                    "expression to your scheduler."
                ),
                cron=entry.cron,
            )

        base_s = entry.backoff.base_interval_seconds or parse_duration(entry.interval) or DEFAULT_BASE_INTERVAL_S
        if entry.backoff.policy == "exponential":
            interval_s = entry.backoff.current_interval_seconds() or base_s
            streak = entry.backoff.empty_streak
            if streak == 0:
                reason = f"streak 0 (snap-back), interval {_humanize_seconds(interval_s)}"
            else:
                reason = (
                    f"empty-streak-{streak}, "
                    f"base {_humanize_seconds(base_s)} * 2^{streak} = "
                    f"{_humanize_seconds(interval_s)}"
                )
        else:
            interval_s = base_s
            reason = f"no backoff, interval {_humanize_seconds(interval_s)}"

        fire_at = now + timedelta(seconds=interval_s)
        plan = SchedulePlan(
            fire_at=fire_at,
            interval_seconds=interval_s,
            current_streak=entry.backoff.empty_streak,
            reason=reason,
        )

        # Stamp on registry — body still owns the install, but the
        # registry's view of "what we plan to fire next" needs to match.
        entry.scheduling.next_fire_at = fire_at.isoformat()
        data["loops"][name] = entry.to_dict()
        self._write(data)
        return plan

    def to_dict(self) -> dict[str, Any]:
        return self._read()
