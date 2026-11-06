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
    refresh_cockpit,
    write_clean_shutdown,
    write_default_config,
)
from empirica.core.cockpit.launcher.config import config_file_state, validate_file
from empirica.core.cockpit.launcher.detection import SessionAlreadyRunning
from empirica.core.cockpit.launcher.tmux import exact_session


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


def _config_path_arg(args) -> tuple[str | None, str | None]:
    """(config path, error) from --config or --profile.

    --profile NAME means ~/.empirica/cockpit/config-NAME.yaml, so a second
    cockpit is `--profile ecodex` rather than a full path. An unknown profile is
    an error naming the ones that exist, never a silently written default: a
    typo must not bring up the wrong cockpit.
    """
    config_path = getattr(args, "config", None)
    profile = getattr(args, "profile", None)
    if not profile:
        return config_path, None
    if config_path:
        return None, "pass --config or --profile, not both"
    base = Path.home() / ".empirica" / "cockpit"
    path = base / f"config-{profile}.yaml"
    if not path.is_file():
        found = sorted(p.stem.removeprefix("config-") for p in base.glob("config-*.yaml"))
        return None, f"no cockpit profile {profile!r} ({path}); available: {', '.join(found) or 'none'}"
    return str(path), None


def _profile_error(args, error: str) -> int:
    if getattr(args, "output", "human") == "json":
        print(json.dumps({"ok": False, "error": error}))
    else:
        print(f"❌ {error}")
    return 1


def _install_bar(config, session_name: str, args, *, human: bool) -> dict:
    """Put the bottom bar on the session and start its first state refresh. Never raises: the layout is up either way."""
    try:
        from empirica.core.cockpit import bar
        from empirica.core.cockpit.launcher.config import DEFAULT_CONFIG_PATH

        config_path, _error = _config_path_arg(args)
        result = bar.install(
            config,
            session_name,
            config_path=Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH,
            profile=getattr(args, "profile", None),
        )
        if result.get("installed"):
            bar.start_refresh(session_name)
    except Exception as e:
        result = {"installed": False, "detail": f"the cockpit bar could not be installed ({type(e).__name__}: {e})"}
    if human and result.get("installed"):
        keys = "Ctrl+b e then R A M I U C S L, ? for help" if result.get("keys") else "click a chip"
        print(f"  ▁ cockpit bar on ({result.get('lines')} lines): {keys}")
        if result.get("detail"):
            print(f"    {result['detail']}")
    elif human and result.get("detail") and "bar: false" not in result["detail"]:
        print(f"  ⚠ {result['detail']}")
    return result


def _start_launch_wake(opened: list[dict], session_name: str, *, human: bool) -> dict:
    """Give the claude panes this launch opened the wake `refresh` gives a respawned pane, from a detached process.

    Launch hands the terminal to tmux, so the wake cannot run here (core/cockpit/launch_wake.py). Never raises: a launch that
    built its layout has succeeded whatever the wake does, and what happened is in `cockpit status`.
    """
    try:
        from empirica.core.cockpit import launch_wake

        wake = launch_wake.start(opened, session_name)
    except Exception as e:
        wake = {"started": False, "panes": 0, "detail": f"the wake could not be started ({type(e).__name__}: {e})"}
    if human and wake.get("started"):
        print(f"  ⏰ {wake['detail']}; `empirica cockpit status` shows the result")
    elif human and wake.get("panes"):
        print(f"  ⚠ {wake['detail']}")
    return wake


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
    config_path, profile_error = _config_path_arg(args)
    if profile_error:
        return _profile_error(args, profile_error)
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
        return _handle_groups_launch(config, output, quiet, args)
    if config.is_groups_mode():
        # surface: tmux with groups — same layout, in the terminal launch ran from.
        # This used to fall through to the one-window-per-project path and
        # silently ignore the groups, so panes never appeared side by side
        # unless alacritty or ghostty was installed.
        return _handle_groups_in_terminal(config, output, no_attach, args)

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
        "bar": _install_bar(config, result.session_name, args, human=output == "human"),
        "wake": _start_launch_wake(result.opened, result.session_name, human=output == "human"),
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
    os.execvp("tmux", ["tmux", "attach-session", "-t", exact_session(result.session_name)])  # noqa: S606 — tmux is the OS executable, args are sanitized config values
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


def _handle_groups_in_terminal(config, output: str, no_attach: bool, args=None) -> int:
    """Groups layout with ``surface: tmux``: build it, then attach this terminal."""
    result = launch_groups(config, spawn_terminal=False)
    if result.error:
        if output == "json":
            print(json.dumps({"ok": False, "error": result.error}))
        else:
            print(f"❌ {result.error}")
        return 1
    windows = [
        {"name": g.group_name, "created": g.created, "panes": g.panes_created, "error": g.error} for g in result.groups
    ]
    if output != "json":
        _print_stripped(result.session_identity_stripped)
        for g in result.groups:
            verb = "created" if g.created else "adopted existing"
            print(f"  · {g.group_name:12s} ({verb}, {g.panes_created} panes){'  ⚠ ' + g.error if g.error else ''}")
    # Before the attach below: that replaces this process, so this is the last point the bar and the wake can be started from.
    bar = _install_bar(config, result.session_name, args, human=output != "json")
    wake = _start_launch_wake(result.opened, result.session_name, human=output != "json")
    if output == "json":
        print(
            json.dumps(
                {
                    "ok": result.all_ok(),
                    "surface": "tmux",
                    "session_name": result.session_name,
                    "windows": windows,
                    "bar": bar,
                    "wake": wake,
                }
            )
        )
    if not result.all_ok():
        # Attaching would hand the terminal to tmux and the warnings above would be gone with the
        # scrollback. Stop, say what is missing, and leave the choice to attach with the user.
        if output == "human":
            bad = [g for g in result.groups if g.error]
            print(f"❌ Cockpit incomplete: {len(bad)} window(s) with problems, not attaching.")
            print(f"   Attach anyway: tmux attach -t ={result.session_name}")
        return 1
    if no_attach or not config.attach_on_launch:
        if output == "human":
            print(f"✅ Cockpit ready: {result.session_name} — attach with: tmux attach -t {result.session_name}")
        return 0
    if os.environ.get("TMUX"):
        # Already inside tmux: attaching would nest. Move this client instead.
        os.execvp("tmux", ["tmux", "switch-client", "-t", exact_session(result.session_name)])  # noqa: S606
    os.execvp("tmux", ["tmux", "attach-session", "-t", exact_session(result.session_name)])  # noqa: S606
    return 1


def _handle_groups_launch(config, output: str, quiet: bool, args=None) -> int:
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
        "session_identity_stripped": result.session_identity_stripped,
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
        "bar": None,
        "wake": None,
    }

    if output == "json":
        payload["bar"] = _install_bar(config, result.session_name, args, human=False)
        payload["wake"] = _start_launch_wake(result.opened, result.session_name, human=False)
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.all_ok() else 1

    # Human-readable summary doubles as a keybinding cheatsheet.
    _print_stripped(result.session_identity_stripped)
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
    _install_bar(config, result.session_name, args, human=True)
    _start_launch_wake(result.opened, result.session_name, human=True)
    if not quiet:
        print()
        print("  Click a window name in the top status-line to switch between groups.")
        print()
        print("  Detach all: empirica cockpit detach   (writes clean-shutdown marker)")
        print("  Re-wrap:    empirica cockpit launch   (re-attaches the surviving session, adds missing panes)")
        print("  Refresh:    empirica cockpit refresh  (restarts dead claude panes in place, resumed)")
    return 0 if result.all_ok() else 1


def _print_stripped(stripped: str | None) -> None:
    if stripped:
        print(
            f"  ⊖ removed EMPIRICA_INSTANCE_ID={stripped} from the session environment: it made every pane "
            "started here without its own id run as that practitioner. Running claudes keep the id they "
            "started with; restart any that were started by hand."
        )


def _wake_resumed_panes(respawned: list[dict], *, quiet: bool) -> list[dict]:
    """Wake each respawned claude pane once it is idle. Never raises: a refresh that brought panes back has succeeded whatever the wake does."""
    try:
        from empirica.core.cockpit.wake_after_resume import wake_resumed

        if not quiet and any(not r["error"] for r in respawned):
            print("  waiting for resumed claude panes to be idle, then waking them (up to 90 s)...")
        return wake_resumed(respawned)
    except Exception as e:
        return [
            {"key": r["key"], "pane_id": r.get("pane_id"), "woken": False, "detail": f"not woken: {e}"}
            for r in respawned
        ]


def handle_cockpit_refresh_command(args) -> int:
    """``empirica cockpit refresh``. Respawn dead claude panes in place,
    with their identity and conversation. Live panes are never touched."""
    config_path, profile_error = _config_path_arg(args)
    if profile_error:
        return _profile_error(args, profile_error)
    output = getattr(args, "output", "human")
    config = load_config(path=Path(config_path).expanduser() if config_path else None)

    only_pane = getattr(args, "pane", None)
    result = refresh_cockpit(config, only_pane=only_pane) if only_pane else refresh_cockpit(config)
    failed = [r for r in result.respawned if r["error"]]
    ok = result.error is None and not failed
    # A resumed claude has had its SessionStart and then sits idle; its Monitor is armed by a turn, so give it one (David's ruling (a)).
    woken = _wake_resumed_panes(result.respawned, quiet=output == "json") if result.respawned else []
    if output == "json":
        print(
            json.dumps(
                {
                    "ok": ok,
                    "session_name": result.session_name,
                    "respawned": result.respawned,
                    "alive": result.alive,
                    "missing": result.missing,
                    "session_identity_stripped": result.session_identity_stripped,
                    "adopted": result.adopted,
                    "unkeyed_panes": result.unkeyed,
                    "left_dead": result.left_dead,
                    "woken": woken,
                    "error": result.error,
                },
                indent=2,
            )
        )
        return 0 if ok else 1

    if result.error:
        print(f"❌ {result.error}")
        return 1
    print(f"🔄 cockpit refresh · session {result.session_name}")
    _print_stripped(result.session_identity_stripped)
    if result.adopted:
        print(f"  ⊕ now tracking {len(result.adopted)} pane(s) from an earlier launch: {', '.join(result.adopted)}")
    for r in result.respawned:
        who = r["instance_id"] or "(no identity)"
        if r["error"]:
            print(f"  ✗ {r['key']:24s} {who} — {r['error']}")
        else:
            print(f"  ↻ {r['key']:24s} {who} — {r['command']}")
    for w in woken:
        print(f"  {'⏰' if w['woken'] else '⚠'} {w['key']:24s} {w['detail']}")
    if not result.respawned:
        print(f"  nothing to do: {len(result.alive)} claude pane(s) running")
    if result.left_dead:
        print(f"  · left as they are (not the pane named): {', '.join(result.left_dead)}")
    if result.missing:
        print(f"  ⚠ not in the session: {', '.join(result.missing)} — `empirica cockpit launch` adds them back")
    if result.unkeyed:
        print(
            f"  ⚠ {result.unkeyed} pane(s) could not be matched to a configured pane (window or directory "
            "differs), so they are not tracked and missing panes cannot be listed"
        )
    return 0 if ok else 1


def _status_findings(config_p: Path | None) -> tuple[str, list[str], list[str]]:
    """``(file state, errors, warnings)`` for `cockpit status`.

    An unreadable file is a problem on the DEFAULT path too, which is the common one (only "missing"
    is normal there: first launch writes it), and a malformed section is a finding, not a traceback.
    """
    file_state = config_file_state(config_p)
    try:
        errors, warnings = validate_file(config_p)
    except Exception as exc:
        errors, warnings = [f"the config could not be checked: {type(exc).__name__}: {exc}"], []
    if file_state in ("unreadable", "not-a-mapping") or (config_p is not None and file_state != "ok"):
        shown = config_p or "the default cockpit config"
        errors.insert(0, f"{shown} is {file_state.replace('-', ' ')}; showing the built-in defaults instead")
    return file_state, errors, warnings


def _last_launch_wake(session_name: str) -> dict | None:
    """What the last `launch` of this session did about waking its panes, or None. A reader that breaks says so."""
    try:
        from empirica.core.cockpit import launch_wake

        return launch_wake.read(session_name)
    except Exception as e:
        return {"status": "unreadable", "error": f"{type(e).__name__}: {e}", "path": ""}


def _launch_wake_lines(record: dict | None) -> list[str]:
    from empirica.core.cockpit import launch_wake

    return launch_wake.summary_lines(record)


def handle_cockpit_status_command(args) -> int:
    """``empirica cockpit status``. Read-only state snapshot — does NOT
    attach. Reports session liveness, last clean shutdown, abnormal-exit
    state, and configured project list."""
    config_path, profile_error = _config_path_arg(args)
    if profile_error:
        return _profile_error(args, profile_error)
    output = getattr(args, "output", "human")

    config_p = Path(config_path).expanduser() if config_path else None
    config = load_config(path=config_p)
    # An explicitly named config that is missing or unreadable is a problem, not "defaults": load_config
    # answers both with the built-in layout, and reporting that layout as the profile hides the typo.
    file_state, errors, warnings = _status_findings(config_p)
    snap = cockpit_status()
    abnormal = detect_abnormal_exit()
    session_live = cockpit_session_exists(config.session_name)

    payload = {
        "ok": not errors,
        "problems": errors,
        "warnings": warnings,
        "config_file": file_state,
        "session_name": config.session_name,
        "session_live": session_live,
        "last_session_start": _format_iso(snap.last_session_start),
        "last_clean_shutdown": _format_iso(snap.last_clean_shutdown),
        "lock_pid": snap.lock_pid,
        "lock_alive": snap.lock_alive,
        "abnormal_exit": None,
        "configured_projects": [{"name": p.name, "path": p.path, "launch": p.launch} for p in config.projects],
        "status_windows": [{"name": w.name, "command": w.command} for w in config.status_windows],
        "launch_wake": _last_launch_wake(config.session_name),
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
        return 1 if errors else 0

    # Human-readable
    print(f"🛫 cockpit · session: {config.session_name}")
    for msg in errors:
        print(f"   ✗ {msg}")
    for msg in warnings:
        print(f"   ⚠ {msg}")
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
    for line in _launch_wake_lines(payload["launch_wake"]):
        print(f"   {line}")
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
    return 1 if errors else 0


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
    config_path, profile_error = _config_path_arg(args)
    if profile_error:
        return _profile_error(args, profile_error)
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
        sys.stderr.write("usage: empirica cockpit <launch|refresh|status|detach|kill> [args...]\n")
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
    "refresh": handle_cockpit_refresh_command,
}


__all__ = [
    "handle_cockpit_detach_command",
    "handle_cockpit_group_command",
    "handle_cockpit_kill_command",
    "handle_cockpit_launch_command",
    "handle_cockpit_refresh_command",
    "handle_cockpit_status_command",
]
