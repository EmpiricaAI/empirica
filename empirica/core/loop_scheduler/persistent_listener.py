"""Persistent listener service — install `empirica loop listen` as an OS-level service.

The existing systemd.py / launchd.py modules install **periodic-timer** services
(oneshot ExecStart=`empirica loop tick`). This module installs a **persistent**
service (Restart=always / KeepAlive=true ExecStart=`empirica loop listen`) so
the ntfy SSE subscription stays alive even when no Claude session is open.

Without this, wake events accumulate in cortex inbox + ntfy 24h cache until a
Claude session opens — pull-when-session-starts, violating the push-primary
substrate claim.

Closes prop_flrtxxn32japbazq5awqydxbdy (cortex AI, 2026-05-18).

Cross-platform via the same OS-detection pattern as get_loop_scheduler():
  - Linux / WSL2 → systemd-user .service (Restart=always)
  - macOS        → launchd LaunchAgent .plist (KeepAlive=true, RunAtLoad=true)
  - Windows      → not supported v1; hint at WSL2

Public API:
  PersistentListenerService(empirica_bin="empirica")
    .install(ai_id)        → install + start the listener service
    .uninstall(ai_id)      → stop + remove
    .status(ai_id)         → ListenerStatus(installed, active, …)
    .is_running(ai_id)     → bool — used by session-monitor-arm to skip Monitor

  install_listener_for(ai_id, empirica_bin=None)   — convenience
  uninstall_listener_for(ai_id)                    — convenience
  listener_status_for(ai_id)                       — convenience
  is_listener_running(ai_id)                       — never raises (safe for hooks)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Helpers (mirror systemd.py conventions) ────────────────────────────


def _safe(s: str) -> str:
    """Sanitize ai_id for filesystem-safe service naming."""
    return "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in s) or "default"


def _unit_name(ai_id: str) -> str:
    """e.g. 'empirica-listener-cortex' for ai_id='cortex'."""
    return f"empirica-listener-{_safe(ai_id)}"


def _systemd_user_dir() -> Path:
    """User-scoped systemd unit directory; created if missing."""
    p = Path.home() / ".config" / "systemd" / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _launchd_agents_dir() -> Path:
    """User-scoped launchd agents directory (macOS); created if missing."""
    p = Path.home() / "Library" / "LaunchAgents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _logs_dir() -> Path:
    """~/.empirica/logs directory for listener service stdout/stderr."""
    p = Path.home() / ".empirica" / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run `systemctl --user <args>` with a 5s timeout. Defensive, never raises unless `check=True`."""
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=5, check=check,
    )


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run `launchctl <args>` with a 5s timeout. Defensive, never raises unless `check=True`."""
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True, text=True, timeout=5, check=check,
    )


# ─── Capability probes ──────────────────────────────────────────────────


def is_systemd_available() -> bool:
    """True if systemctl --user can connect to its session bus on this host."""
    if shutil.which("systemctl") is None:
        return False
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return "Failed to connect to bus" not in (r.stderr or "")
    except Exception:
        return False


def is_launchd_available() -> bool:
    """True on macOS when launchctl is on PATH."""
    return sys.platform == "darwin" and shutil.which("launchctl") is not None


# ─── Templates ──────────────────────────────────────────────────────────


_SYSTEMD_LISTENER_TEMPLATE = """\
[Unit]
Description=Empirica persistent listener — {ai_id}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={empirica_bin} loop listen --instance {ai_id}
Restart=always
RestartSec=5
StandardOutput=append:{log_path}
StandardError=append:{log_path}

[Install]
WantedBy=default.target
"""


_LAUNCHD_LISTENER_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.empirica.listener.{ai_id_safe}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{empirica_bin}</string>
    <string>loop</string>
    <string>listen</string>
    <string>--instance</string>
    <string>{ai_id}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log_path}</string>
  <key>StandardErrorPath</key>
  <string>{log_path}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
"""


# ─── Dataclasses ────────────────────────────────────────────────────────


@dataclass
class ListenerStatus:
    """Snapshot of a persistent listener service for a given ai_id."""

    ai_id: str
    backend: str  # 'systemd' | 'launchd' | 'unavailable'
    installed: bool
    active: bool
    unit_path: str | None = None
    log_path: str | None = None


class ListenerServiceUnavailable(RuntimeError):
    """No supported persistent-service backend on this host."""


# ─── Main class ─────────────────────────────────────────────────────────


class PersistentListenerService:
    """Install / uninstall / inspect the persistent listener service for an ai_id.

    Reuses the OS-detection pattern from get_loop_scheduler() but renders
    persistent (Restart=always) service units instead of periodic timers.

    Args:
        empirica_bin: Absolute path to the `empirica` CLI. Defaults to the
            result of `shutil.which('empirica')` — systemd-user / launchd
            run with minimal PATH and a bare command often fails to resolve.
    """

    def __init__(self, empirica_bin: str | None = None):
        if empirica_bin:
            self.empirica_bin = empirica_bin
        else:
            resolved = shutil.which("empirica")
            self.empirica_bin = resolved or "empirica"
        self.backend = self._detect_backend()

    @staticmethod
    def _detect_backend() -> str:
        """Pick the persistent-service backend for this host: 'launchd' on macOS, 'systemd' on Linux/WSL2, 'unavailable' otherwise."""
        if sys.platform == "darwin" and is_launchd_available():
            return "launchd"
        if is_systemd_available():
            return "systemd"
        return "unavailable"

    # ── Path resolution ─────────────────────────────────────────────────

    def unit_path(self, ai_id: str) -> Path | None:
        """Resolve the unit/plist file path for `ai_id` under the active backend; None on unsupported."""
        if self.backend == "systemd":
            return _systemd_user_dir() / f"{_unit_name(ai_id)}.service"
        if self.backend == "launchd":
            return _launchd_agents_dir() / f"com.empirica.listener.{_safe(ai_id)}.plist"
        return None

    def log_path(self, ai_id: str) -> Path:
        """Path to the listener's stdout/stderr append log for `ai_id`."""
        return _logs_dir() / f"listener-{_safe(ai_id)}.log"

    # ── Install ─────────────────────────────────────────────────────────

    def install(self, ai_id: str) -> Path:
        """Install + start the persistent listener service for `ai_id`.

        Returns the path of the installed unit file. Idempotent — running
        again overwrites the unit and restarts the service.
        """
        if self.backend == "unavailable":
            raise ListenerServiceUnavailable(
                "No supported persistent-service backend on this host. "
                "Linux/WSL2 needs systemd-user (systemctl --user is-system-running). "
                "macOS needs launchctl. Windows-native is not supported in v1 — use WSL2."
            )
        log_path = self.log_path(ai_id)

        if self.backend == "systemd":
            return self._install_systemd(ai_id, log_path)
        return self._install_launchd(ai_id, log_path)

    def _install_systemd(self, ai_id: str, log_path: Path) -> Path:
        """Write the systemd-user unit file for `ai_id` + daemon-reload + enable --now.

        Self-heals from the pre-strict-canonical naming convention: if a
        legacy stripped-form unit exists (e.g. `empirica-listener-autonomy.service`
        when installing `empirica-listener-empirica-autonomy.service`), uninstall
        it first to prevent duplicate-listener double-delivery.
        """
        self._purge_legacy_systemd_unit(ai_id)
        unit_name = _unit_name(ai_id)
        unit_file = _systemd_user_dir() / f"{unit_name}.service"
        unit_file.write_text(
            _SYSTEMD_LISTENER_TEMPLATE.format(
                ai_id=ai_id,
                empirica_bin=self.empirica_bin,
                log_path=log_path,
            ),
            encoding="utf-8",
        )
        _systemctl("daemon-reload", check=True)
        _systemctl("enable", "--now", f"{unit_name}.service", check=True)
        logger.info("Installed systemd listener service: %s", unit_file)
        return unit_file

    def _install_launchd(self, ai_id: str, log_path: Path) -> Path:
        """Write the launchd plist for `ai_id` + bootout-then-load via launchctl.

        Self-heals from the pre-strict-canonical naming convention: if a
        legacy stripped-form plist exists, unload + remove it first to
        prevent duplicate-listener double-delivery.
        """
        self._purge_legacy_launchd_plist(ai_id)
        plist_file = _launchd_agents_dir() / f"com.empirica.listener.{_safe(ai_id)}.plist"
        # If a previous version is loaded, unload first so launchctl picks up the new file
        if plist_file.exists():
            _launchctl("unload", str(plist_file), check=False)
        plist_file.write_text(
            _LAUNCHD_LISTENER_TEMPLATE.format(
                ai_id=ai_id,
                ai_id_safe=_safe(ai_id),
                empirica_bin=self.empirica_bin,
                log_path=log_path,
            ),
            encoding="utf-8",
        )
        _launchctl("load", "-w", str(plist_file), check=True)
        logger.info("Installed launchd listener service: %s", plist_file)
        return plist_file

    # ── Legacy self-heal ────────────────────────────────────────────────

    @staticmethod
    def _legacy_short_form(ai_id: str) -> str | None:
        """If `ai_id` is the strict-canonical `empirica-<rest>` form, return
        the pre-canonical stripped form (`<rest>`). Otherwise None.

        Pre-canonical convention (deprecated): `InstanceResolver.ai_id()`
        stripped the `empirica-` prefix, so `empirica-autonomy` ai_id
        resolved to `autonomy` and the systemd unit was
        `empirica-listener-autonomy.service`. After f7cd0433a the
        prefix is always kept, and new installs create
        `empirica-listener-empirica-autonomy.service`. Existing units on
        disk from the legacy run need cleanup so the host doesn't carry
        two listeners after the convention swap.
        """
        if ai_id.startswith("empirica-") and ai_id != "empirica-":
            stripped = ai_id.removeprefix("empirica-")
            if stripped and stripped != ai_id:
                return stripped
        return None

    def _purge_legacy_systemd_unit(self, ai_id: str) -> None:
        """Best-effort remove the pre-canonical stripped-form unit.

        Never raises — if the legacy unit doesn't exist or systemctl is
        unavailable, this is a no-op. Logs the action so post-install
        audits can see what was healed.
        """
        legacy = self._legacy_short_form(ai_id)
        if not legacy:
            return
        legacy_unit_name = _unit_name(legacy)
        legacy_unit_file = _systemd_user_dir() / f"{legacy_unit_name}.service"
        if not legacy_unit_file.exists():
            return
        logger.info(
            "Purging legacy stripped-form systemd unit %s before installing canonical %s",
            legacy_unit_file, _unit_name(ai_id),
        )
        _systemctl("disable", "--now", f"{legacy_unit_name}.service", check=False)
        try:
            legacy_unit_file.unlink()
        except OSError as e:
            logger.warning("Failed to remove legacy unit file %s: %s", legacy_unit_file, e)
        _systemctl("daemon-reload", check=False)

    def _purge_legacy_launchd_plist(self, ai_id: str) -> None:
        """Best-effort remove the pre-canonical stripped-form launchd plist."""
        legacy = self._legacy_short_form(ai_id)
        if not legacy:
            return
        legacy_plist = _launchd_agents_dir() / f"com.empirica.listener.{_safe(legacy)}.plist"
        if not legacy_plist.exists():
            return
        logger.info(
            "Purging legacy stripped-form launchd plist %s before installing canonical",
            legacy_plist,
        )
        _launchctl("unload", str(legacy_plist), check=False)
        try:
            legacy_plist.unlink()
        except OSError as e:
            logger.warning("Failed to remove legacy plist %s: %s", legacy_plist, e)

    # ── Uninstall ───────────────────────────────────────────────────────

    def uninstall(self, ai_id: str) -> bool:
        """Stop + remove the persistent listener service.

        Returns True if anything was removed, False if not installed.
        Idempotent — never raises on missing service.
        """
        path = self.unit_path(ai_id)
        if not path or not path.exists():
            return False

        if self.backend == "systemd":
            _systemctl("disable", "--now", f"{_unit_name(ai_id)}.service", check=False)
            path.unlink()
            _systemctl("daemon-reload", check=False)
        elif self.backend == "launchd":
            _launchctl("unload", str(path), check=False)
            path.unlink()
        else:
            return False
        logger.info("Uninstalled listener service: %s", path)
        return True

    # ── Status ──────────────────────────────────────────────────────────

    def status(self, ai_id: str) -> ListenerStatus:
        """Return current service status for `ai_id`.

        Always returns a ListenerStatus — never raises. On platforms without
        a supported backend, returns backend='unavailable', installed=False.
        """
        path = self.unit_path(ai_id)
        log_path = self.log_path(ai_id)
        if self.backend == "unavailable" or path is None:
            return ListenerStatus(
                ai_id=ai_id, backend="unavailable", installed=False,
                active=False, log_path=str(log_path),
            )
        installed = path.exists()
        active = False
        if self.backend == "systemd":
            if installed:
                r = _systemctl("is-active", f"{_unit_name(ai_id)}.service")
                active = (r.stdout or "").strip() == "active"
        else:  # launchd
            # The RUNNING label is authoritative independent of where the plist
            # file lives. launchd can load a service from a domain/dir other than
            # ~/Library/LaunchAgents (e.g. bootstrapped into gui/<uid>, showing
            # PPID 1). Gating the liveness check on path.exists() then
            # false-negatives, and the caller (session-monitor-arm) arms a
            # DUPLICATE listener. Check the label regardless of plist location;
            # stay defensive against a launchctl timeout so status() never raises.
            try:
                r = _launchctl("list", f"com.empirica.listener.{_safe(ai_id)}")
                active = r.returncode == 0
            except Exception:
                active = False
            # A loaded-but-plist-elsewhere service still counts as installed.
            installed = installed or active
        return ListenerStatus(
            ai_id=ai_id, backend=self.backend, installed=installed,
            active=active, unit_path=str(path), log_path=str(log_path),
        )

    def is_running(self, ai_id: str) -> bool:
        """Quick boolean — is the listener service active for this ai_id?

        Used by session-monitor-arm to decide whether to emit a Monitor
        command (would duplicate the persistent listener) or skip.
        """
        return self.status(ai_id).active


# ─── Module-level convenience ───────────────────────────────────────────


def install_listener_for(ai_id: str, empirica_bin: str | None = None) -> Path:
    """Convenience: instantiate + install in one call. Returns the unit file path."""
    return PersistentListenerService(empirica_bin).install(ai_id)


def uninstall_listener_for(ai_id: str) -> bool:
    """Convenience: stop + remove the persistent listener for `ai_id`. Returns True if anything was removed."""
    return PersistentListenerService().uninstall(ai_id)


def listener_status_for(ai_id: str) -> ListenerStatus:
    """Convenience: snapshot the persistent listener status for `ai_id`."""
    return PersistentListenerService().status(ai_id)


def is_listener_running(ai_id: str) -> bool:
    """Cheap availability check — used by session-monitor-arm + doctor.

    Returns False on any error (missing binary, unsupported platform,
    permission issue) — caller treats False as "no persistent listener,
    fall back to Monitor".
    """
    try:
        return PersistentListenerService().is_running(ai_id)
    except Exception:
        return False
