"""Handlers for ``empirica cockpit <action>`` (launcher v1).

Per ``docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md``. Subgroup actions:
``launch / status / detach / kill``. ``save / restore`` deferred to v1.1.

Each handler returns an exit code and prints either human-readable
output (default) or JSON (with ``--output json``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from empirica.core.cockpit.launcher import (
    AbnormalExit,
    alacritty_available,
    cockpit_kill,
    cockpit_session_exists,
    cockpit_status,
    detect_abnormal_exit,
    ghostty_available,
    launch_cockpit,
    launch_groups,
    load_config,
    write_clean_shutdown,
    write_default_config,
)
from empirica.core.cockpit.launcher.detection import SessionAlreadyRunning


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _format_iso(epoch: float | None) -> str:
    if epoch is None:
        return "never"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def handle_cockpit_launch_command(args) -> int:
    """``empirica cockpit launch [--config PATH] [--no-attach]``.

    Idempotent — attaches to an existing session if one is already
    running with the configured ``session_name``. Otherwise creates the
    canonical layout per config.

    Auto-generates ``~/.empirica/cockpit/config.yaml`` on first run from
    detected projects under ``~/empirical-ai/`` (defer interactive
    confirmation to v1.1 — for now we just write defaults).

    Detects abnormal exit on launch and surfaces a warning unless
    ``--quiet-warnings`` is set.
    """
    config_path = getattr(args, "config", None)
    no_attach = bool(getattr(args, "no_attach", False))
    quiet = bool(getattr(args, "quiet_warnings", False))
    output = getattr(args, "output", "human")

    config_p = Path(config_path).expanduser() if config_path else None

    # 1. Load config — write defaults on first run
    from empirica.core.cockpit.launcher.config import DEFAULT_CONFIG_PATH

    target = config_p or DEFAULT_CONFIG_PATH
    if not target.exists():
        try:
            written = write_default_config(path=target)
            if output == "human":
                print(f"📝 Wrote default cockpit config: {written}")
                print("   Edit it to customise session_name, projects, status_windows.")
        except Exception as exc:
            if output == "json":
                print(json.dumps({"ok": False, "error": f"config write failed: {exc}"}))
            else:
                print(f"❌ config write failed: {exc}")
            return 1

    config = load_config(path=config_p)

    # CLI override for surface (--surface alacritty / --surface tmux)
    surface_override = getattr(args, "surface", None)
    if surface_override:
        config.surface = surface_override

    # Groups mode short-circuits the legacy single-session path: spawn one
    # terminal window (alacritty or ghostty) per group, each with its own
    # tmux session and panes.
    if config.is_groups_mode() and config.surface in ("alacritty", "ghostty"):
        return _handle_groups_launch(config, output, quiet)

    # 2. Abnormal-exit detection
    abnormal_payload = _check_and_print_abnormal(config, quiet, output)

    # 3. Bring up the layout
    result = launch_cockpit(config)
    if result.error:
        if output == "json":
            print(json.dumps({"ok": False, "error": result.error}))
        else:
            print(f"❌ {result.error}")
        return 1

    # 4. Build response payload
    payload = {
        "ok": True,
        "session_name": result.session_name,
        "created": result.created,
        "windows_created": result.windows_created,
        "status_windows_created": result.status_windows_created,
        "abnormal_exit": abnormal_payload,
    }

    if output == "json":
        print(json.dumps(payload, indent=2, default=str))

    # 5. Attach (unless --no-attach)
    will_attach = config.attach_on_launch and not no_attach
    if not will_attach:
        if output == "human":
            verb = "created" if result.created else "attached to existing"
            print(f"✅ Cockpit {verb}: {result.session_name}")
            print(f"   Attach manually: tmux attach -t {result.session_name}")
        return 0

    # Hand control to tmux. From here, the cockpit owns the terminal.
    if output == "human":
        verb = "created" if result.created else "attaching to existing"
        print(f"✅ Cockpit {verb}: {result.session_name} — handing off to tmux...")
    os.execvp("tmux", ["tmux", "attach-session", "-t", result.session_name])  # noqa: S606 — tmux is the OS executable, args are sanitized config values
    # execvp doesn't return on success; if we get here, something failed.
    return 1


def _check_and_print_abnormal(config, quiet: bool, output: str) -> dict | None:
    """Detect abnormal-exit / already-running, print human warnings,
    return the JSON payload field for the caller. Extracted to keep
    handle_cockpit_launch_command under the C901 complexity bar."""
    abnormal = detect_abnormal_exit()
    if isinstance(abnormal, AbnormalExit):
        if config.warn_on_abnormal_exit and not quiet and output == "human":
            print("⚠️  Previous cockpit session ended without clean shutdown")
            print(f"   started: {_format_iso(abnormal.started_at)}")
            print(f"   duration: {_format_age(abnormal.duration_lost_seconds)} ago")
            print(f"   likely cause: {abnormal.likely_cause}")
            print("   Suggested: empirica instance prune --dry-run")
        return {
            "kind": "abnormal_exit",
            "started_at": abnormal.started_at,
            "duration_lost_seconds": abnormal.duration_lost_seconds,
            "likely_cause": abnormal.likely_cause,
        }
    if isinstance(abnormal, SessionAlreadyRunning) and output == "human":
        print(f'ℹ️  Cockpit session "{config.session_name}" already running (pid {abnormal.pid}). Attaching.')
    return None


def _wm_class_for(surface: str, session_name: str) -> str:
    """Per-surface window-class/app-id value for the ONE terminal
    spawned per config — must match what the corresponding
    ``_spawn_*`` function in launcher/tmux.py actually passes, since
    this is display-only (status/JSON output), not the source of
    truth. Ghostty requires reverse-domain-name (GTK app-id rules);
    alacritty accepts a bare string."""
    if surface == "ghostty":
        return f"com.empirica.cockpit.{session_name}"
    return f"empirica-{session_name}"


def _handle_groups_launch(config, output: str, quiet: bool) -> int:
    """Groups mode: ONE terminal window (alacritty or ghostty) per
    config/monitor, hosting every group as a tmux window in a single
    shared session — native status-line click-to-switch between them.

    The terminal gets a WM_CLASS/app-id so KDE/wmctrl can target it
    (Meta+1..N once pinned to taskbar). Idempotent — re-running after a
    hibernate-detach re-wraps the surviving tmux session in a fresh
    terminal window without spawning a duplicate.
    """
    surface = config.surface
    available = ghostty_available() if surface == "ghostty" else alacritty_available()
    if not available:
        msg = (
            f"{surface} not found on PATH. Install it, "
            "or set surface: tmux in cockpit/config.yaml for the legacy single-attach mode."
        )
        if output == "json":
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"❌ {msg}")
        return 1

    result = launch_groups(config)
    if result.error:
        if output == "json":
            print(json.dumps({"ok": False, "error": result.error}))
        else:
            print(f"❌ {result.error}")
        return 1

    payload = {
        "ok": result.all_ok(),
        "surface": surface,
        "session_name": result.session_name,
        "terminal_pid": result.terminal_pid,
        "terminal_skipped": result.terminal_skipped,
        "wm_class": _wm_class_for(surface, result.session_name),
        "windows": [
            {
                "name": g.group_name,
                "tmux_target": g.tmux_session,
                "created": g.created,
                "panes": g.panes_created,
                "error": g.error,
            }
            for g in result.groups
        ],
    }

    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.all_ok() else 1

    # Human-readable summary doubles as a keybinding cheatsheet.
    if result.terminal_skipped:
        window_state = "window already attached, skipped spawn"
    elif result.terminal_pid:
        window_state = f"{surface} pid {result.terminal_pid}"
    else:
        window_state = f"{surface} pid n/a"
    print(f"✅ cockpit ({surface} surface) — session {result.session_name} ({window_state})")
    for i, g in enumerate(result.groups, 1):
        verb = "created" if g.created else "adopted existing"
        marker = "✗" if g.error else "·"
        line = f"  {marker} {i}. {g.group_name:12s} ({verb}, {g.panes_created} panes)"
        if g.error:
            line += f"  ⚠ {g.error}"
        print(line)
    if not quiet:
        print()
        print("  Click a window name in the top status-line to switch between groups.")
        print()
        print("  Detach all: empirica cockpit detach   (writes clean-shutdown marker)")
        print("  Refresh:    empirica cockpit launch   (re-wraps the surviving session)")
    return 0 if result.all_ok() else 1


def handle_cockpit_status_command(args) -> int:
    """``empirica cockpit status``. Read-only state snapshot — does NOT
    attach. Reports session liveness, last clean shutdown, abnormal-exit
    state, and configured project list."""
    config_path = getattr(args, "config", None)
    output = getattr(args, "output", "human")

    config_p = Path(config_path).expanduser() if config_path else None
    config = load_config(path=config_p)
    snap = cockpit_status()
    abnormal = detect_abnormal_exit()
    session_live = cockpit_session_exists(config.session_name)

    payload = {
        "ok": True,
        "session_name": config.session_name,
        "session_live": session_live,
        "last_session_start": _format_iso(snap.last_session_start),
        "last_clean_shutdown": _format_iso(snap.last_clean_shutdown),
        "lock_pid": snap.lock_pid,
        "lock_alive": snap.lock_alive,
        "abnormal_exit": None,
        "configured_projects": [{"name": p.name, "path": p.path, "launch": p.launch} for p in config.projects],
        "status_windows": [{"name": w.name, "command": w.command} for w in config.status_windows],
    }
    if isinstance(abnormal, AbnormalExit):
        payload["abnormal_exit"] = {
            "started_at": _format_iso(abnormal.started_at),
            "duration_lost": _format_age(abnormal.duration_lost_seconds),
            "likely_cause": abnormal.likely_cause,
        }
    elif isinstance(abnormal, SessionAlreadyRunning):
        payload["session_already_running"] = {
            "pid": abnormal.pid,
            "started_at": _format_iso(abnormal.started_at),
        }

    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
        return 0

    # Human-readable
    print(f"🛫 cockpit · session: {config.session_name}")
    print(f"   live: {'yes' if session_live else 'no'}")
    print(
        f"   last start:  {_format_iso(snap.last_session_start)} ({_format_age(_age_seconds(snap.last_session_start))})"
    )
    print(
        f"   last clean:  {_format_iso(snap.last_clean_shutdown)} "
        f"({_format_age(_age_seconds(snap.last_clean_shutdown))})"
    )
    if snap.lock_pid is not None:
        print(f"   lock pid:    {snap.lock_pid} ({'alive' if snap.lock_alive else 'dead'})")
    if isinstance(abnormal, AbnormalExit):
        print(f"   ⚠ abnormal exit: started {_format_iso(abnormal.started_at)}, cause={abnormal.likely_cause}")
    elif isinstance(abnormal, SessionAlreadyRunning):
        print(f"   running: pid {abnormal.pid} since {_format_iso(abnormal.started_at)}")
    if config.projects:
        print("   projects:")
        for p in config.projects:
            print(f"     · {p.name:14s} {p.launch:8s} {p.path}")
    if config.status_windows:
        print("   status windows:")
        for w in config.status_windows:
            print(f"     · {w.name:14s} {w.command}")
    return 0


def _age_seconds(epoch: float | None) -> float | None:
    if epoch is None:
        return None
    import time

    return max(0.0, time.time() - epoch)


def handle_cockpit_detach_command(args) -> int:
    """``empirica cockpit detach``. Wrapper for ``tmux detach-client`` +
    write the clean-shutdown marker. Useful as a hotkey."""
    output = getattr(args, "output", "human")

    write_clean_shutdown()
    # Best-effort tmux detach — caller may not be inside the cockpit pane,
    # in which case tmux returns an error we ignore. The marker is what
    # matters for abnormal-exit detection.
    try:
        subprocess.run(
            ["tmux", "detach-client"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if output == "json":
        print(json.dumps({"ok": True, "detached": True}))
    else:
        print("✅ Clean shutdown marker written. Detached.")
    return 0


def handle_cockpit_kill_command(args) -> int:
    """``empirica cockpit kill [--prune]``. Destroy the tmux session
    and optionally prune dead per-instance state files."""
    config_path = getattr(args, "config", None)
    prune = bool(getattr(args, "prune", False))
    output = getattr(args, "output", "human")

    config_p = Path(config_path).expanduser() if config_path else None
    config = load_config(path=config_p)

    success, error = cockpit_kill(session_name=config.session_name)
    if not success:
        if output == "json":
            print(json.dumps({"ok": False, "error": error}))
        else:
            print(f"❌ {error}")
        return 1

    pruned_count = 0
    if prune:
        try:
            from empirica.core.cockpit.instance_actions import forget_instance
            from empirica.core.cockpit.instance_state import discover_dead_instances

            dead = discover_dead_instances()
            for iid in dead:
                try:
                    forget_instance(iid)
                    pruned_count += 1
                except Exception:
                    pass
        except Exception as exc:
            if output == "human":
                print(f"⚠ Prune failed: {exc}")

    payload = {
        "ok": True,
        "session_name": config.session_name,
        "killed": True,
        "pruned_count": pruned_count if prune else None,
    }
    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"✅ Killed cockpit session: {config.session_name}")
        if prune:
            print(f"   Pruned {pruned_count} dead per-instance state files.")
    return 0


def handle_cockpit_group_command(args) -> int:
    """Dispatcher for ``empirica cockpit <action>``."""
    action = getattr(args, "cockpit_action", None)
    if not action:
        sys.stderr.write("usage: empirica cockpit <launch|status|detach|kill> [args...]\n")
        return 2
    handler = _COCKPIT_DISPATCH.get(action)
    if handler is None:
        sys.stderr.write(f"error: unknown cockpit action: {action}\n")
        return 2
    return handler(args) or 0


_COCKPIT_DISPATCH = {
    "launch": handle_cockpit_launch_command,
    "status": handle_cockpit_status_command,
    "detach": handle_cockpit_detach_command,
    "kill": handle_cockpit_kill_command,
}


__all__ = [
    "handle_cockpit_detach_command",
    "handle_cockpit_group_command",
    "handle_cockpit_kill_command",
    "handle_cockpit_launch_command",
    "handle_cockpit_status_command",
]
