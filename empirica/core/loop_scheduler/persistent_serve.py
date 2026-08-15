"""Persistent serve daemon service — install `empirica serve` as an OS-level service.

Sibling of `persistent_listener.py`. Where that module supervises the wake-event
listener, this one supervises the `empirica serve` FastAPI daemon (the local
:8000 endpoint the Chrome extension talks to). Same persistent shape
(Restart=always / KeepAlive=true), different ExecStart.

Why this exists
---------------
`empirica serve` runs a blocking uvicorn loop. After a code update it keeps
serving the code it loaded at start until something restarts it — a daemon
observed running for weeks on a pre-update commit while the extension talked to
stale code. Two failures compound without a supervisor:

  1. **No reboot-restart** — a reboot kills the daemon and nothing brings it back.
  2. **No drift pickup** — serve's own drift watcher (serve_app._drift_watch_loop,
     v1.12.22+) self-exits on version drift ONLY when supervised
     (`_serve_drift_exit_enabled`: INVOCATION_ID set, or EMPIRICA_SERVE_DRIFT_EXIT
     truthy). Unsupervised, the self-exit is inert — drift is surfaced on /health
     and the restart is manual.

This service closes both: systemd/launchd restart it on reboot, and — because the
unit sets the supervised signal — serve self-exits on drift and the supervisor
relaunches it against the new code.

Safety
------
The 2026-07-27 flap (serve restarted 1849× in ~4 days on a false-positive drift
check under an editable install) is why this is not trivially "Restart=always".
Two guards:

  - The false positive itself is fixed upstream: `core.version_drift.version_drift`
    now skips the compare entirely under editable installs (PEP 610
    `dir_info.editable`), so an editable seat no longer self-exits on every bump.
  - The **StartLimit circuit breaker** (5 starts / 60s, same as the listener)
    bounds any residual pathology: at the limit systemd leaves the unit `failed`
    and stops relaunching, so a tight loop surfaces loudly in minutes instead of
    degrading silently for days.

Opt-in only
-----------
NEVER auto-installed by setup — a persistent OS service is opt-in per the
cron/service-opt-in rule. The install verb is the only path.

Cross-platform (same OS-detection as get_loop_scheduler / persistent_listener):
  - Linux / WSL2 → systemd-user .service (Restart=always; INVOCATION_ID supervises)
  - macOS        → launchd LaunchAgent .plist (KeepAlive=true, RunAtLoad=true;
                   EMPIRICA_SERVE_DRIFT_EXIT=1 supplies the supervised signal
                   launchd does not set INVOCATION_ID)
  - Windows      → not supported v1; hint at WSL2

Public API:
  PersistentServeService(empirica_bin="empirica")
    .install(project_root, port=8000, host="127.0.0.1")  → install + start
    .uninstall()                                          → stop + remove
    .status()                                             → ServeStatus
    .is_running()                                         → bool

  install_serve_service(project_root, port, host, empirica_bin=None)  — convenience
  uninstall_serve_service()                                           — convenience
  serve_service_status()                                              — convenience
  is_serve_service_running()                                          — never raises
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Reuse the generic OS-service helpers from the sibling listener module rather
# than duplicate them (read-only imports — the listener module is unchanged).
from empirica.core.loop_scheduler.persistent_listener import (
    _launchctl,
    _launchd_agents_dir,
    _logs_dir,
    _systemctl,
    _systemd_user_dir,
    is_launchd_available,
    is_systemd_available,
)

logger = logging.getLogger(__name__)

# Singleton daemon — one serve per box on the extension's :8000. Not keyed by
# ai_id (serve binds to a project by WorkingDirectory, not by identity).
_SYSTEMD_UNIT_NAME = "empirica-serve"
_LAUNCHD_LABEL = "com.empirica.serve"


# ─── Templates ──────────────────────────────────────────────────────────

# ExecStartPre reaper — verbatim from the hand-built unit (prop_hhxoop3s), with
# the port parameterized. Kills any process holding the serve port that is NOT in
# this service's cgroup, so a stray manual `empirica serve` can't win the :{port}
# bind and leave systemd's copy dead. `$$` is systemd's escape for a literal `$`
# (systemd expands `$` itself); `\\K` reaches grep as `\K`. systemd-only — launchd
# has no ExecStartPre equivalent.
_REAPER_EXECSTARTPRE = (
    r"""ExecStartPre=/bin/bash -c 'pid=$$(ss -tlnpH "sport = :{port}" 2>/dev/null | """
    r"""grep -oP "pid=\\K[0-9]+" | head -1); """
    r"""if [ -n "$$pid" ] && ! grep -q "empirica-serve.service" /proc/$$pid/cgroup 2>/dev/null; """
    r"""then echo "[reaper] killing orphan serve pid=$$pid holding :{port}"; """
    r"""kill "$$pid" 2>/dev/null || true; sleep 2; fi; exit 0'"""
)


# See module docstring "Safety": StartLimit is the circuit breaker that bounds a
# drift-triggered relaunch loop. Kept identical to the listener's (5 / 60s).
_SYSTEMD_SERVE_TEMPLATE = """\
[Unit]
Description=Empirica serve daemon (Chrome extension) — {project_root}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={project_root}
{reaper}
ExecStart={empirica_bin} serve --port {port} --host {host}
Restart=always
RestartSec=5
StandardOutput=append:{log_path}
StandardError=append:{log_path}

[Install]
WantedBy=default.target
"""


# launchd does NOT set INVOCATION_ID, so the supervised signal that lets serve
# self-exit on drift is supplied explicitly via EMPIRICA_SERVE_DRIFT_EXIT=1.
_LAUNCHD_SERVE_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{empirica_bin}</string>
    <string>serve</string>
    <string>--port</string>
    <string>{port}</string>
    <string>--host</string>
    <string>{host}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{project_root}</string>
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
    <key>EMPIRICA_SERVE_DRIFT_EXIT</key>
    <string>1</string>
  </dict>
</dict>
</plist>
"""


# ─── Helpers ────────────────────────────────────────────────────────────


def _backup_existing(unit_file: Path) -> Path | None:
    """Copy an existing unit/plist to `<name>.bak` before overwrite. None if absent.

    Adopt-not-clobber: makes a hand-built unit recoverable after a reinstall.
    Best-effort — a copy failure logs and returns None rather than blocking the
    install (the install is the point; the backup is insurance).
    """
    if not unit_file.exists():
        return None
    backup = unit_file.with_name(unit_file.name + ".bak")
    try:
        import shutil as _sh

        _sh.copy2(unit_file, backup)
        logger.info("Backed up existing unit %s → %s before overwrite", unit_file, backup)
        return backup
    except OSError as e:
        logger.warning("Could not back up existing unit %s: %s", unit_file, e)
        return None


# ─── Dataclasses ────────────────────────────────────────────────────────


@dataclass
class ServeStatus:
    """Snapshot of the persistent serve daemon service."""

    backend: str  # 'systemd' | 'launchd' | 'unavailable'
    installed: bool
    active: bool
    unit_path: str | None = None
    log_path: str | None = None


class ServeServiceUnavailable(RuntimeError):
    """No supported persistent-service backend on this host."""


# ─── Main class ─────────────────────────────────────────────────────────


class PersistentServeService:
    """Install / uninstall / inspect the persistent serve daemon service.

    Singleton — one serve daemon per box (the extension's :8000), so unlike the
    listener there is no ai_id in the unit name. The daemon binds to a project by
    its WorkingDirectory, set at install time from ``project_root``.

    Args:
        empirica_bin: Absolute path to the `empirica` CLI. Defaults to
            ``shutil.which('empirica')`` — systemd-user / launchd run with a
            minimal PATH and a bare command often fails to resolve.
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
        """'launchd' on macOS, 'systemd' on Linux/WSL2, else 'unavailable'."""
        if sys.platform == "darwin" and is_launchd_available():
            return "launchd"
        if is_systemd_available():
            return "systemd"
        return "unavailable"

    # ── Path resolution ─────────────────────────────────────────────────

    def unit_path(self) -> Path | None:
        """Resolve the unit/plist path under the active backend; None on unsupported."""
        if self.backend == "systemd":
            return _systemd_user_dir() / f"{_SYSTEMD_UNIT_NAME}.service"
        if self.backend == "launchd":
            return _launchd_agents_dir() / f"{_LAUNCHD_LABEL}.plist"
        return None

    def log_path(self) -> Path:
        """Path to the serve daemon's stdout/stderr append log."""
        return _logs_dir() / "serve.log"

    # ── Install ─────────────────────────────────────────────────────────

    def install(
        self,
        project_root: str | Path,
        port: int = 8000,
        host: str = "127.0.0.1",
    ) -> Path:
        """Install + start the persistent serve service.

        Args:
            project_root: Directory the daemon runs in (WorkingDirectory) — serve
                resolves its bound project from cwd, so this pins the binding.
            port: Port to serve on (default 8000, matching the extension).
            host: Bind host (default 127.0.0.1).

        Returns the installed unit file path. Idempotent — re-running overwrites
        the unit and restarts the service.
        """
        if self.backend == "unavailable":
            raise ServeServiceUnavailable(
                "No supported persistent-service backend on this host. "
                "Linux/WSL2 needs systemd-user (systemctl --user is-system-running). "
                "macOS needs launchctl. Windows-native is not supported in v1 — use WSL2."
            )
        root = Path(project_root).resolve()
        log_path = self.log_path()
        if self.backend == "systemd":
            return self._install_systemd(root, port, host, log_path)
        return self._install_launchd(root, port, host, log_path)

    def _install_systemd(self, root: Path, port: int, host: str, log_path: Path) -> Path:
        """Write the systemd-user unit + daemon-reload + enable --now.

        Adopt-not-clobber: an existing unit (e.g. a hand-built one) is backed up
        to `<unit>.bak` before overwriting, so a customization is recoverable. A
        drop-in `<unit>.d/override.conf` is untouched — this only writes the base
        unit — so a WorkingDirectory override survives the reinstall.
        """
        unit_file = _systemd_user_dir() / f"{_SYSTEMD_UNIT_NAME}.service"
        _backup_existing(unit_file)
        unit_file.write_text(
            _SYSTEMD_SERVE_TEMPLATE.format(
                project_root=str(root),
                empirica_bin=self.empirica_bin,
                reaper=_REAPER_EXECSTARTPRE.format(port=port),
                port=port,
                host=host,
                log_path=log_path,
            ),
            encoding="utf-8",
        )
        _systemctl("daemon-reload", check=True)
        _systemctl("enable", "--now", f"{_SYSTEMD_UNIT_NAME}.service", check=True)
        logger.info("Installed systemd serve service: %s", unit_file)
        return unit_file

    def _install_launchd(self, root: Path, port: int, host: str, log_path: Path) -> Path:
        """Write the launchd plist + bootout-then-load via launchctl."""
        plist_file = _launchd_agents_dir() / f"{_LAUNCHD_LABEL}.plist"
        _backup_existing(plist_file)
        if plist_file.exists():
            _launchctl("unload", str(plist_file), check=False)
        plist_file.write_text(
            _LAUNCHD_SERVE_TEMPLATE.format(
                label=_LAUNCHD_LABEL,
                project_root=str(root),
                empirica_bin=self.empirica_bin,
                port=port,
                host=host,
                log_path=log_path,
            ),
            encoding="utf-8",
        )
        _launchctl("load", "-w", str(plist_file), check=True)
        logger.info("Installed launchd serve service: %s", plist_file)
        return plist_file

    # ── Uninstall ───────────────────────────────────────────────────────

    def uninstall(self) -> bool:
        """Stop + remove the serve service. True if removed, False if absent. Never raises on missing."""
        path = self.unit_path()
        if not path or not path.exists():
            return False
        if self.backend == "systemd":
            _systemctl("disable", "--now", f"{_SYSTEMD_UNIT_NAME}.service", check=False)
            path.unlink()
            _systemctl("daemon-reload", check=False)
        elif self.backend == "launchd":
            _launchctl("unload", str(path), check=False)
            path.unlink()
        else:
            return False
        logger.info("Uninstalled serve service: %s", path)
        return True

    # ── Status ──────────────────────────────────────────────────────────

    def status(self) -> ServeStatus:
        """Current service status. Always returns a ServeStatus — never raises."""
        path = self.unit_path()
        log_path = self.log_path()
        if self.backend == "unavailable" or path is None:
            return ServeStatus(
                backend="unavailable",
                installed=False,
                active=False,
                log_path=str(log_path),
            )
        installed = path.exists()
        active = False
        if self.backend == "systemd":
            if installed:
                r = _systemctl("is-active", f"{_SYSTEMD_UNIT_NAME}.service")
                active = (r.stdout or "").strip() == "active"
        else:  # launchd — the loaded label is authoritative regardless of plist location
            try:
                r = _launchctl("list", _LAUNCHD_LABEL)
                active = r.returncode == 0
            except Exception:
                active = False
            installed = installed or active
        return ServeStatus(
            backend=self.backend,
            installed=installed,
            active=active,
            unit_path=str(path),
            log_path=str(log_path),
        )

    def is_running(self) -> bool:
        """Quick boolean — is the serve service active?"""
        return self.status().active


# ─── Module-level convenience ───────────────────────────────────────────


def install_serve_service(
    project_root: str | Path,
    port: int = 8000,
    host: str = "127.0.0.1",
    empirica_bin: str | None = None,
) -> Path:
    """Convenience: instantiate + install in one call. Returns the unit file path."""
    return PersistentServeService(empirica_bin).install(project_root, port=port, host=host)


def uninstall_serve_service() -> bool:
    """Convenience: stop + remove the serve service. Returns True if anything was removed."""
    return PersistentServeService().uninstall()


def serve_service_status() -> ServeStatus:
    """Convenience: snapshot the serve service status."""
    return PersistentServeService().status()


def is_serve_service_running() -> bool:
    """Cheap availability check. Returns False on any error (missing binary, unsupported platform)."""
    try:
        return PersistentServeService().is_running()
    except Exception:
        return False


def restart_serve_service_if_running() -> bool:
    """Restart the serve daemon IFF it is installed + active — else no-op.

    The update-flow hook: a long-running editable daemon holds its start-time
    code (editable installs skip drift-self-exit by design, to avoid the flap),
    so after a code update it must be restarted to load the new code. Calling
    this from the post-update path makes that automatic where the service exists,
    and a safe no-op everywhere else (unsupervised daemon, no service, other OS).

    Never raises — returns True if a restart was issued, False otherwise.
    """
    try:
        svc = PersistentServeService()
        if not svc.status().active:
            return False
        if svc.backend == "systemd":
            r = _systemctl("restart", f"{_SYSTEMD_UNIT_NAME}.service")
            ok = r.returncode == 0
        elif svc.backend == "launchd":
            plist = svc.unit_path()
            if not plist or not plist.exists():
                return False
            _launchctl("unload", str(plist), check=False)
            r = _launchctl("load", "-w", str(plist), check=False)
            ok = r.returncode == 0
        else:
            return False
        if ok:
            logger.info("Restarted serve service to pick up updated code")
        return ok
    except Exception as e:
        logger.warning("serve service restart-on-update skipped (non-fatal): %s", e)
        return False
