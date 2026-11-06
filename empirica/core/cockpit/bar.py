"""The cockpit's bottom bar: practice tabs with health, command chips coloured by state, and a help popup.

Asked for by David through mesh-support (prop_uzewce5tbbew7dlvju2agoiz3i and its amendments, 2026-11-06; reference mock in
empirica-mesh-support docs/design/cockpit-bar). The aim: nobody leaves the cockpit for routine actions, and a new person can learn
Empirica from inside it.

How it is put together:

* **Layout.** Session options on the cockpit's own tmux session only: the status line at the bottom, two lines, or three when the
  practice tabs and the health readout do not fit on one. Health is whatever `status-right` already shows, so a machine's existing
  readout and its click targets carry over unchanged.
* **Colour.** Red is broken now, orange is waiting, dim is nothing to do. R and A are computed by tmux itself from `pane_dead`, so
  they are always current. M, I, U, C, S and L read per-window options that a background refresh sets.
* **No checks on the status tick.** The status line only looks at the age of a stamp file. When the state is older than
  STATE_MAX_AGE_MINUTES it starts the refresh in the background; the refresh also runs at launch and after every chip action.
* **Keys and clicks.** `prefix e` enters the `empirica` key table, then the chip's letter (the bare prefix letters are taken by tmux
  and by plugins). Each chip is also a click range. Key bindings are server-wide in tmux, so the click binding hands anything that is
  not one of our ranges to whatever was bound before.
* **State from elsewhere.** U, C, S and L read `~/.empirica/ecosystem-state.json` when a machine has one (mesh-support's
  ecosystem-update writes it). Without it those chips stay dim and say so when pressed.

Run as a module by the bindings: ``python -m empirica.core.cockpit.bar chip R --session S --pane %3 --client /dev/pts/1``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from empirica.core.cockpit.launcher import state as launcher_state
from empirica.core.cockpit.launcher.config import LauncherConfig, load_config
from empirica.core.cockpit.launcher.tmux import _sess_at, _tmux, cockpit_session_exists, exact_session, refresh_cockpit

#: Chip key and its label when nothing is waiting.
CHIPS: tuple[tuple[str, str], ...] = (
    ("R", "restart"),
    ("A", "refresh"),
    ("M", "listener"),
    ("I", "inbox"),
    ("U", "update"),
    ("C", "claude"),
    ("S", "sessions"),
    ("L", "login"),
)
#: Chips whose colour comes from options the background refresh sets. R and A are live tmux formats.
STATE_CHIPS = "MIUCSL"
#: Chips that change the machine or end sessions: they ask first.
CONFIRM: dict[str, str] = {
    "U": "Update Empirica and its plugins on this machine?",
    "C": "Update the AI client on this machine?",
    "S": "Restart the running sessions onto the new settings? This ends them.",
}
KEY_TABLE = "empirica"
RANGE_PREFIX = "emp_"
STATE_MAX_AGE_MINUTES = 5
POPUP_WIDTH = "100"
INBOX_UNKNOWN = "inbox ?"

_RED = "#[bg=colour160 fg=colour231 bold]"
_ORANGE = "#[bg=colour208 fg=colour16 bold]"
_DIM = "#[bg=colour237 fg=colour244]"
#: One x per dead pane in the session. Non-empty means some pane has stopped.
_DEAD = "#{W:#{P:#{?pane_dead,x,}}}"
_DEAD_IN_WINDOW = "#{P:#{?pane_dead,x,}}"
_DEFAULT_STATUS_CLICK = "switch-client -t ="
_PREV_CLICK_OPTION = "@empirica_prev_status_click"


# ── formats ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _style(state: str) -> str:
    """The chip style for a format that expands to `red`, `orange` or nothing."""
    return f"#{{?#{{==:{state},red}},{_RED},#{{?#{{==:{state},orange}},{_ORANGE},{_DIM}}}}}"


def chip_format(key: str, label: str) -> str:
    """One chip: a click range, a style from its state, the letter and a label."""
    if key == "R":
        state, text = "#{?pane_dead,red,}", label
    elif key == "A":
        state, text = f"#{{?{_DEAD},red,}}", f"#{{?{_DEAD},{label} #{{n:{_DEAD}}},{label}}}"
    else:
        state = f"#{{@empirica_chip_{key}}}"
        text = f"#{{?#{{@empirica_label_{key}}},#{{@empirica_label_{key}}},{label}}}"
    return f"#[range=user|{RANGE_PREFIX}{key}]{_style(state)} {key} {text} #[default]#[norange] "


def tabs_format() -> str:
    """The practice tabs. A red mark on a tab means a pane in that practice has stopped."""
    other = (
        "#[range=window|#{window_index} bg=colour238 fg=colour252] #I "
        f"#{{?{_DEAD_IN_WINDOW},#[fg=colour196]✗ #[fg=colour252],}}#W #[norange default] "
    )
    current = (
        "#[range=window|#{window_index} bg=colour34 fg=colour231 bold] #I > "
        f"#{{?{_DEAD_IN_WINDOW},✗ ,}}#W #[norange default] "
    )
    return f"#[align=left]#{{W:{other},{current}}}"


def health_format() -> str:
    """Whatever this machine's `status-right` already shows, so its readout and click targets carry over."""
    return "#[align=right]#{E:status-right}"


def _self_command() -> str:
    """How the bindings call this module: the interpreter that has Empirica, quoted for a shell."""
    return f"{shlex.quote(sys.executable)} -m empirica.core.cockpit.bar"


def stamp_path(session_name: str, state_dir: Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_name).strip(".") or "cockpit"
    return (state_dir or launcher_state.COCKPIT_DIR) / f"bar_state_{safe}.stamp"


def tick_format(session_name: str) -> str:
    """Prints nothing. Starts the background refresh when the state is older than STATE_MAX_AGE_MINUTES.

    This is the only thing the status line runs, and it is a file-age test: the checks themselves (an inbox poll per practice, the
    listener services) run detached. No parentheses or braces, which would end the `#(...)` early.
    """
    stamp = shlex.quote(str(stamp_path(session_name)))
    return (
        f"#(find {stamp} -mmin -{STATE_MAX_AGE_MINUTES} 2>/dev/null | grep -q . || "
        f"{_self_command()} tick {shlex.quote(session_name)} --state-dir {shlex.quote(str(launcher_state.COCKPIT_DIR))} "
        ">/dev/null 2>&1 &)"
    )


def commands_format(session_name: str) -> str:
    chips = "".join(chip_format(key, label) for key, label in CHIPS)
    help_chip = f"#[range=user|{RANGE_PREFIX}help bg=colour25 fg=colour231 bold] ? help #[norange default]"
    return f"#[align=left]{chips}{help_chip}{tick_format(session_name)}"


# ── layout ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _show(target: str, fmt: str) -> str:
    return _tmux("display-message", "-p", "-t", target, fmt).stdout.rstrip("\n")


def _option(scope: list[str], name: str) -> str:
    return _tmux("show-option", *scope, "-qv", name).stdout.strip()


def _fits_on_one_line(session_name: str) -> bool:
    """Whether the practice tabs and the health readout fit beside each other at the current width."""
    target = _sess_at(session_name)
    try:
        width = int(_show(target, "#{?client_width,#{client_width},#{window_width}}") or 0)
    except ValueError:
        width = 0
    listing = _tmux("list-windows", "-t", target, "-F", "#{window_index} #{window_name}").stdout.splitlines()
    tabs = sum(len(f" {line} ") + 5 for line in listing if line)  # room for the active mark and a stopped mark
    health = re.sub(r"#\[[^\]]*\]", "", _show(target, "#{E:status-right}"))
    if not health.strip():
        # A `#(command)` has not answered yet on its first evaluation: use the room the option reserves.
        try:
            health_width = int(_option(["-t", target], "status-right-length") or 40)
        except ValueError:
            health_width = 40
    else:
        health_width = len(health)
    return width > 0 and tabs + health_width + 2 <= width


def layout(session_name: str) -> int:
    """Set the status lines for the current width: 2 when tabs and health share a line, else 3. Returns the count."""
    target = _sess_at(session_name)
    one_line = _fits_on_one_line(session_name)
    if one_line:
        lines = [tabs_format() + health_format(), commands_format(session_name)]
    else:
        lines = [tabs_format(), health_format(), commands_format(session_name)]
    _tmux("set-option", "-t", target, "status", str(len(lines)))
    for i, text in enumerate(lines):
        _tmux("set-option", "-t", target, f"status-format[{i}]", text)
    if one_line:
        _tmux("set-option", "-u", "-t", target, "status-format[2]")
    return len(lines)


# ── bindings ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _binding(table: str, key: str) -> str | None:
    """The command currently bound to ``key`` in ``table``, as tmux prints it, or None."""
    result = _tmux("list-keys", "-T", table, key)
    line = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    match = re.match(r"bind-key\s+(?:-r\s+)?-T\s+\S+\s+\S+\s+(.*)$", line)
    return match.group(1).strip() if match else None


def _dq(text: str) -> str:
    """``text`` inside tmux double quotes."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _action_command(verb: str, what: str) -> str:
    """The tmux command a key or a click runs. tmux fills in the session, pane and client before the shell sees it."""
    shell = f"{_self_command()} {verb} {what} --session '#{{session_name}}' --pane '#{{pane_id}}' --client '#{{client_name}}'"
    return f"run-shell -b {_dq(shell)}"


def install_bindings(mouse: bool = True) -> dict[str, Any]:
    """Bind the key table and the chip clicks. Returns ``{keys, clicks, detail}``; never replaces a key someone else uses.

    Key bindings are server-wide: pressed in a session that is not a cockpit, a chip says so and does nothing.
    """
    out: dict[str, Any] = {"keys": False, "clicks": False, "detail": ""}
    enter = f"switch-client -T {KEY_TABLE}"
    bound = _binding("prefix", "e")
    if bound is None or bound == enter:
        _tmux("bind-key", "-T", "prefix", "e", "switch-client", "-T", KEY_TABLE)
        out["keys"] = True
    else:
        out["detail"] = (
            f"prefix e is already bound ({bound}), so the chips have no keys here; they still answer a click"
        )
    for key, _label in CHIPS:
        _tmux("bind-key", "-T", KEY_TABLE, key, *shlex.split(_action_command("chip", key)))
    _tmux("bind-key", "-T", KEY_TABLE, "?", *shlex.split(_action_command("chip", "help")))

    if mouse:
        previous = _binding("root", "MouseDown1Status")
        if previous and RANGE_PREFIX in previous:
            previous = (
                _option(["-s"], _PREV_CLICK_OPTION) or _DEFAULT_STATUS_CLICK
            )  # ours already: keep what was there before us
        else:
            previous = previous or _DEFAULT_STATUS_CLICK
            _tmux("set-option", "-s", _PREV_CLICK_OPTION, previous)
        _tmux(
            "bind-key",
            "-T",
            "root",
            "MouseDown1Status",
            "if-shell",
            "-F",
            f"#{{m:{RANGE_PREFIX}*,#{{mouse_status_range}}}}",
            _action_command("click", "'#{mouse_status_range}'"),
            previous,
        )
        out["clicks"] = True
    return out


# ── install ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def install(
    config: LauncherConfig, session_name: str, *, config_path: Path | None, profile: str | None
) -> dict[str, Any]:
    """Put the bar on a cockpit session. Safe to repeat. Returns ``{installed, lines, keys, clicks, detail}``."""
    if not getattr(config, "bar", True):
        return {"installed": False, "detail": "bar: false in the cockpit config"}
    if not cockpit_session_exists(session_name):
        return {
            "installed": False,
            "detail": f"session {session_name!r} is not running, so there is nothing to put the bar on",
        }
    target = _sess_at(session_name)
    for name, value in (
        ("status-position", "bottom"),
        ("status-interval", "15"),
        ("status-style", "bg=colour235,fg=colour250"),
        ("@empirica_bar", "1"),
        ("@empirica_profile", profile or ""),
        ("@empirica_config", str(config_path) if config_path else ""),
    ):
        _tmux("set-option", "-t", target, name, value)
    lines = layout(session_name)
    bindings = install_bindings(mouse=config.mouse)
    return {"installed": True, "lines": lines, **bindings}


def _spawn_detached(argv: list[str]) -> None:
    """Start a process that outlives this one and holds no terminal. The one place the bar starts anything in the background."""
    subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


def start_refresh(session_name: str, spawn=None) -> None:
    """Start the state refresh detached. The caller does not wait: it polls an inbox per practice."""
    argv = [
        sys.executable,
        "-m",
        "empirica.core.cockpit.bar",
        "tick",
        session_name,
        # The child must write where this process would: a state directory redirected here is not redirected there.
        "--state-dir",
        str(launcher_state.COCKPIT_DIR),
    ]
    try:
        (spawn or _spawn_detached)(argv)
    except Exception:
        return  # the status line starts it again within STATE_MAX_AGE_MINUTES


# ── state ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

_VERDICT_STATE = {"needed": "red", "recommended": "orange"}
_COMPONENT_CHIP = {
    "empirica": "U",
    "core": "U",
    "ecosystem": "U",
    "plugins": "U",
    "prompts": "U",
    "mcp": "U",
    "claude": "C",
    "claude-code": "C",
    "ecodex": "C",
    "codex": "C",
    "sessions": "S",
    "login": "L",
    "oauth": "L",
    "auth": "L",
}


def ecosystem_state_path() -> Path:
    return Path.home() / ".empirica" / "ecosystem-state.json"


def read_ecosystem_state(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Per chip: ``{state, label, fix}`` from the machine's ecosystem state file. Empty when there is none.

    The file is written by whatever keeps the machine up to date (mesh-support's ecosystem-update). The shape read here:
    ``{"components": {NAME: {"verdict": "needed|recommended|ok", "label": "...", "fix": "command", "chip": "U|C|S|L"}}}``.
    ``chip`` is optional; without it the component's name decides. The worst verdict among a chip's components wins.
    A file that cannot be read gives every chip a dim "state ?" label instead of looking clean.
    """
    path = path or ecosystem_state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        components = raw.get("components") if isinstance(raw, dict) else None
        if not isinstance(components, dict):
            raise ValueError("no components mapping")
    except Exception:
        return {chip: {"state": "", "label": "state ?", "fix": ""} for chip in "UCSL"}
    out: dict[str, dict[str, str]] = {}
    for name, entry in components.items():
        if not isinstance(entry, dict):
            continue
        chip = str(entry.get("chip") or _COMPONENT_CHIP.get(str(name).lower(), "")).upper()
        if chip not in "UCSL" or not chip:
            continue
        state = _VERDICT_STATE.get(str(entry.get("verdict") or "").lower(), "")
        current = out.get(chip)
        rank = {"red": 2, "orange": 1, "": 0}
        if current is None or rank[state] > rank[current["state"]]:
            out[chip] = {"state": state, "label": str(entry.get("label") or ""), "fix": str(entry.get("fix") or "")}
    return out


_AI_ID_LINE = re.compile(r"^ai_id:[ \t]*[\"']?([A-Za-z0-9_.-]+)", re.MULTILINE)


def _practice_of(path: str) -> str | None:
    """The ai_id of the practice a pane's directory belongs to, or None."""
    try:
        match = _AI_ID_LINE.search((Path(path) / ".empirica" / "project.yaml").read_text(errors="replace"))
    except OSError:
        return None
    return match.group(1) if match else None


def _empirica_argv() -> list[str]:
    exe = shutil.which("empirica")
    return [exe] if exe else [sys.executable, "-m", "empirica.cli"]


def _listener_chip(ai_id: str) -> tuple[str, str, bool]:
    """(state, label, on the mesh). Red when this practice has a listener service installed and it is not running.

    No service installed is not a fault: a practice that was never put on the mesh has no listener to be down. The third value says
    whether one is installed, which is also what decides if a failed inbox poll is worth showing.
    """
    try:
        from empirica.core.loop_scheduler.persistent_listener import PersistentListenerService

        status = PersistentListenerService().status(ai_id)
    except Exception:
        return "", "", False
    if status.installed and not status.active:
        return "red", "listener down", True
    return "", "", bool(status.installed)


def _inbox_chip(ai_id: str, path: str, run=subprocess.run) -> tuple[str, str]:
    """Orange with a count when accepted mail is waiting. A poll that fails shows `inbox ?`, never a clean chip."""
    try:
        result = run(
            [*_empirica_argv(), "mailbox", "poll", "--ai-id", ai_id, "--status", "accepted", "--output", "json"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return "", INBOX_UNKNOWN
    proposals = payload.get("proposals") or []
    matched = payload.get("matched")
    count = matched if isinstance(matched, int) and matched >= len(proposals) else len(proposals)
    return ("orange", f"inbox {count}") if count else ("", "")


def _set_chip(window_id: str, key: str, state: str, label: str) -> None:
    for name, value in ((f"@empirica_chip_{key}", state), (f"@empirica_label_{key}", label)):
        if value:
            _tmux("set-option", "-w", "-t", window_id, name, value)
        else:
            _tmux("set-option", "-w", "-u", "-t", window_id, name)


def refresh_state(
    session_name: str, *, state_dir: Path | None = None, inbox=_inbox_chip, listener=_listener_chip
) -> dict[str, Any]:
    """Set every practice window's chip state, then re-fit the layout. Run detached; touches the stamp first."""
    stamp = stamp_path(session_name, state_dir)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError:
        pass  # the status line will start another refresh; nothing else depends on the stamp
    target = _sess_at(session_name)
    if _option(["-t", target], "@empirica_bar") != "1":
        return {"ok": False, "detail": "no bar on this session"}
    rows = _tmux("list-panes", "-s", "-t", exact_session(session_name), "-F", "#{window_id}\t#{pane_current_path}")
    practices: dict[str, tuple[str, str]] = {}  # window id -> (ai_id, directory)
    windows: list[str] = []
    for line in rows.stdout.splitlines():
        window_id, _, path = line.partition("\t")
        if window_id and window_id not in windows:
            windows.append(window_id)
        if window_id and window_id not in practices:
            ai_id = _practice_of(path)
            if ai_id:
                practices[window_id] = (ai_id, path)

    def one(item: tuple[str, tuple[str, str]]) -> tuple[str, tuple[str, str], tuple[str, str]]:
        window_id, (ai_id, path) = item
        m_state, m_label, on_mesh = listener(ai_id)
        i_state, i_label = inbox(ai_id, path)
        if i_label == INBOX_UNKNOWN and not on_mesh:
            i_label = ""  # a practice that is not on the mesh has no inbox to fail to read
        return window_id, (m_state, m_label), (i_state, i_label)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, practices.items()))
    machine = read_ecosystem_state()
    for window_id in windows:
        for chip in "UCSL":
            entry = machine.get(chip, {})
            _set_chip(window_id, chip, entry.get("state", ""), entry.get("label", ""))
        if window_id not in practices:
            _set_chip(window_id, "M", "", "")
            _set_chip(window_id, "I", "", "")
    for window_id, (m_state, m_label), (i_state, i_label) in results:
        _set_chip(window_id, "M", m_state, m_label)
        _set_chip(window_id, "I", i_state, i_label)
    return {"ok": True, "windows": len(windows), "practices": len(practices), "lines": layout(session_name)}


# ── actions ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _say(client: str | None, text: str, ms: int = 6000) -> None:
    args = ["display-message", "-d", str(ms)]
    if client:
        args += ["-c", client]
    _tmux(*args, text.replace("#", "##"))


def _config_of(session_name: str) -> LauncherConfig | None:
    path = _option(["-t", _sess_at(session_name)], "@empirica_config")
    if _option(["-t", _sess_at(session_name)], "@empirica_bar") != "1":
        return None
    return load_config(path=Path(path) if path else None)


def _restart(session: str, pane: str | None, client: str | None) -> None:
    """R (one pane) and A (every dead pane): restart, then give each restarted claude its wake turn."""
    from empirica.core.cockpit.wake_after_resume import wake_resumed

    config = _config_of(session)
    if config is None:
        _say(client, "This tmux session is not an Empirica cockpit, so there is nothing to restart.")
        return
    result = refresh_cockpit(config, only_pane=pane)
    if result.error:
        _say(client, f"restart: {result.error}")
        return
    failed = [r for r in result.respawned if r["error"]]
    done = [r for r in result.respawned if not r["error"]]
    if failed:
        _say(client, f"restart failed for {failed[0]['key']}: {failed[0]['error']}")
    if not result.respawned:
        which = "this pane is" if pane else f"all {len(result.alive)} practice pane(s) are"
        _say(client, f"Nothing to restart: {which} running.")
        return
    if done:
        _say(client, f"Restarted {', '.join(r['key'] for r in done)}. Waking once idle (up to 90 s)...")
        woken = wake_resumed(done)
        missed = [w for w in woken if not w["woken"]]
        if missed:
            _say(client, f"Restarted, but not woken: {missed[0]['key']} ({missed[0]['detail']})", ms=9000)
        else:
            _say(client, f"Restarted and woken: {', '.join(w['key'] for w in woken)}")


def _wake(pane: str | None, client: str | None) -> None:
    """M: give this pane a wake turn, which reads its mail and re-arms its listener."""
    from empirica.core.cockpit.instance_actions import wake_instance

    if not pane:
        _say(client, "listener: no pane to wake")
        return
    outcome = wake_instance(f"tmux_{pane.lstrip('%')}")
    _say(client, "Woken: it will read its mail and re-arm its listener." if outcome.success else outcome.detail)


def _popup(
    client: str | None, command: str, *, directory: str | None = None, width: str = "80%", height: str = "80%"
) -> None:
    args = ["display-popup", "-w", width, "-h", height, "-E"]
    if client:
        args += ["-c", client]
    if directory:
        args += ["-d", directory]
    _tmux(*args, command)


def _inbox(session: str, pane: str | None, client: str | None) -> None:
    """I: this practice's unanswered mail in a scrollable popup."""
    directory = _show(pane, "#{pane_current_path}") if pane else ""
    ai_id = _practice_of(directory) if directory else None
    if not ai_id:
        _say(client, "inbox: this pane is not in a practice directory")
        return
    poll = " ".join(
        shlex.quote(a) for a in [*_empirica_argv(), "mailbox", "poll", "--ai-id", ai_id, "--status", "accepted"]
    )
    prompt = shlex.quote(f" Inbox of {ai_id}  ·  arrows / PgUp PgDn scroll  ·  q closes ")
    _popup(client, f"{poll} 2>&1 | less -R -P {prompt}", directory=directory)


def _login(client: str | None) -> None:
    """L: renew the Cortex login in a popup, where its prompts can be answered."""
    login = " ".join(shlex.quote(a) for a in [*_empirica_argv(), "auth", "login"])
    _popup(client, f"{login}; printf '\\npress Enter to close '; read _", width="90", height="24")


def _machine_action(key: str, session: str, client: str | None, confirmed: bool) -> None:
    """U, C and S: run the fix the machine's state file names for this chip, after asking."""
    entry = read_ecosystem_state().get(key, {})
    fix = entry.get("fix") or ("claude update" if key == "C" else "")
    if not fix:
        _say(
            client,
            f"{key}: nothing is set up for this on this machine ({ecosystem_state_path()} names no command for it).",
            ms=8000,
        )
        return
    if not confirmed:
        again = f"{_self_command()} chip {key} --yes --session {shlex.quote(session)}"
        if client:
            again += f" --client {shlex.quote(client)}"
        args = ["confirm-before", "-p", f"{CONFIRM[key]} Runs: {fix}  (y/n)"]
        if client:
            args += ["-c", client]
        _tmux(*args, f"run-shell -b {_dq(again)}")
        return
    shown = f"{fix}; echo; printf 'done - press Enter to close '; read _"
    _tmux("new-window", "-t", _sess_at(session), "-n", {"U": "update", "C": "client", "S": "sessions"}[key], shown)


def less_command(path: Path, run=subprocess.run) -> list[str]:
    """`less` on the help text: mouse wheel when the installed less has it (551 or newer), keys otherwise."""
    prompt = " Help  %lt-%lb of %L  ·  wheel / arrows / PgUp PgDn scroll  ·  /§N jumps to topic N  ·  q closes "
    args = ["less", "-R"]
    try:
        first = run(["less", "--version"], capture_output=True, text=True, timeout=5, check=False).stdout.splitlines()[
            0
        ]
        version = int(re.search(r"(\d+)", first).group(1))  # type: ignore[union-attr]
    except Exception:
        version = 0
    if version >= 551:
        args += ["--mouse", "--wheel-lines=3"]
    else:
        prompt = prompt.replace("wheel / ", "")
    return [*args, "-P", prompt, str(path)]


def help_view() -> int:
    """What the help popup runs: write the text where less can read it, then become less."""
    from empirica.core.cockpit.bar_help import HELP_TEXT

    path = launcher_state.COCKPIT_DIR / "bar_help.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HELP_TEXT)
    if shutil.which("less") is None:
        print(HELP_TEXT)
        input("press Enter to close ")
        return 0
    os.execvp("less", less_command(path))  # noqa: S606 — less is the OS pager; its arguments are built here
    return 1


def run_chip(key: str, session: str, pane: str | None, client: str | None, confirmed: bool = False) -> None:
    """One chip's action. Says what happened in the client's message line; never prints."""
    key = key.removeprefix(RANGE_PREFIX)
    if key in ("help", "?"):
        _popup(client, f"{_self_command()} help-view", width=POPUP_WIDTH, height="24")
        return
    key = key.upper()
    if key == "R":
        _restart(session, pane, client)
    elif key == "A":
        _restart(session, None, client)
    elif key == "M":
        _wake(pane, client)
    elif key == "I":
        _inbox(session, pane, client)
    elif key == "L":
        _login(client)
    elif key in CONFIRM:
        _machine_action(key, session, client, confirmed)
    else:
        _say(client, f"unknown cockpit command {key!r}")
        return
    if key != "L" and not (key in CONFIRM and not confirmed):
        start_refresh(session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m empirica.core.cockpit.bar", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("chip", "click"):
        p = sub.add_parser(verb)
        p.add_argument("key")
        p.add_argument("--session", default="")
        p.add_argument("--pane")
        p.add_argument("--client")
        p.add_argument("--yes", action="store_true")
    tick = sub.add_parser("tick")
    tick.add_argument("session")
    tick.add_argument("--state-dir")
    sub.add_parser("help-view")
    args = parser.parse_args(argv)
    if args.verb == "tick":
        refresh_state(args.session, state_dir=Path(args.state_dir) if args.state_dir else None)
        return 0
    if args.verb == "help-view":
        return help_view()
    try:
        run_chip(args.key, args.session, args.pane or None, args.client or None, confirmed=args.yes)
    except Exception as e:  # a chip must answer in the message line, not with a traceback nobody sees
        _say(args.client or None, f"cockpit {args.key}: {type(e).__name__}: {e}", ms=9000)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
