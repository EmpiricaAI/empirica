"""The cockpit's bars: practices and health on top, command chips under each pane, a menu and a help with a Close button.

Asked for by David through mesh-support (prop_uzewce5tbbew7dlvju2agoiz3i and its amendments, 2026-11-06; reference mock in
empirica-mesh-support docs/design/cockpit-bar), then revised by David the same day after the first real launch: practices and
health belong on top, the cockpit commands and help at the bottom, and every popup needs a way to close it with a mouse.

How it is put together:

* **Top.** tmux's status line, on the cockpit's own session: practice tabs, and whatever `status-right` already shows (a machine's
  health readout and its click targets carry over unchanged). Two lines when they do not fit beside each other.
* **Bottom.** tmux has one status line with one position, so the commands cannot be a second one. They are the line tmux draws under
  each pane (`pane-border-status bottom`): chips coloured by state, red for broken now, orange for waiting, dim for nothing to do.
  R and A are tmux formats over `pane_dead`, evaluated per pane, so they are always current and R is about the pane it sits under.
* **Clicks.** tmux reports which pane's line was clicked, not where on it, so the whole line is one button: it opens a menu of the
  commands. A menu is clickable and closes on its Close item, on Escape or on a click outside. U, C and S ask first with a menu too.
* **Keys.** `prefix e` enters the `empirica` key table: the chip's letter, `?` for help, Space for the menu.
* **No checks on the status tick.** The status line only tests the age of a stamp file. When the state is older than
  STATE_MAX_AGE_MINUTES it starts the refresh in the background; the refresh also runs at launch and after every command.
* **Popups.** Help and inbox open in a viewer with Close, up and down buttons (bar_pager.py). Login and the machine commands run in
  a window of their own, where their output can be read and their tab is the way out.
* **Machine commands.** U, C and S run what `~/.empirica/ecosystem-state.json` names, else the cockpit config's `bar_commands`,
  else a built-in: `claude update` for C, and for S a restart of every practice pane with its conversation resumed. U has no
  built-in, because the right update command depends on how Empirica was installed.

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
from functools import partial
from pathlib import Path
from typing import Any

from empirica.core.cockpit.launcher import state as launcher_state
from empirica.core.cockpit.launcher.config import LauncherConfig, load_config
from empirica.core.cockpit.launcher.tmux import _sess_at, _tmux, cockpit_session_exists, exact_session, refresh_cockpit

#: Chip key and its label when nothing is waiting: what the line under each pane shows, and what has a key.
CHIPS: tuple[tuple[str, str], ...] = (
    ("R", "restart"),
    ("A", "refresh"),
    ("M", "mail"),
    ("C", "claude"),
    ("L", "login"),
)
#: Commands reached only through the menu: no chip and no key, so nobody starts them by accident.
#: U is here because updating the whole ecosystem from a click is too easy to start and too hard to follow (David, 2026-11-06);
#: S, restarting every running session, joined it on 2026-11-14 (David's rulings, prop_vc67kjtw4vfj3b5uavfqyjsycu).
MENU_ONLY = "US"
#: Keys an earlier version bound that no longer exist: I (inbox) is now part of M, which wakes the practice to read its mail.
RETIRED_KEYS = "I"
#: Chips whose colour comes from options the background refresh sets. R and A are live tmux formats.
STATE_CHIPS = "MUCSL"
#: Chips whose state is per pane (its practice's), set as pane options; the others are this machine's, set per window.
PANE_CHIPS = "M"
#: Chips that change the machine or end sessions: they ask first.
CONFIRM: dict[str, str] = {
    "U": "Update Empirica and its plugins on this machine?",
    "C": "Update the AI client on this machine?",
    "S": "Restart the running sessions onto the new settings? This ends them.",
}
KEY_TABLE = "empirica"
STATE_MAX_AGE_MINUTES = 5
POPUP_WIDTH = "100"
MAIL_UNKNOWN = "mail ?"

_RED = "#[bg=colour160 fg=colour231 bold]"
_ORANGE = "#[bg=colour208 fg=colour16 bold]"
_DIM = "#[bg=colour237 fg=colour244]"
#: One x per dead pane in the session. Non-empty means some pane has stopped.
_DEAD = "#{W:#{P:#{?pane_dead,x,}}}"
_DEAD_IN_WINDOW = "#{P:#{?pane_dead,x,}}"
#: Non-empty when a pane in the window waits on its person (a permission dialog or a question); hooks set and clear the pane option.
_WAITING_IN_WINDOW = "#{P:#{?@empirica_waiting,w,}}"
_DEFAULT_STATUS_CLICK = "switch-client -t ="
_PREV_BORDER_OPTION = "@empirica_prev_border_click"
BORDER_CLICK = "MouseUp1Border"
_PREV_CLICK_OPTION = "@empirica_prev_status_click"


# ── formats ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _style(state: str) -> str:
    """The chip style for a format that expands to `red`, `orange` or nothing."""
    return f"#{{?#{{==:{state},red}},{_RED},#{{?#{{==:{state},orange}},{_ORANGE},{_DIM}}}}}"


def _chip_state(key: str) -> str:
    """The format that expands to a chip's state: `red`, `orange` or nothing."""
    if key == "R":
        return "#{?pane_dead,red,}"
    if key == "A":
        return f"#{{?{_DEAD},red,}}"
    return f"#{{@empirica_chip_{key}}}"


def chip_format(key: str, label: str) -> str:
    """One chip: a style from its state, the letter and a label. Evaluated per pane, so R is about the pane it sits under."""
    if key == "A":
        text = f"#{{?{_DEAD},{label} #{{n:{_DEAD}}},{label}}}"
    elif key == "R":
        text = label
    else:
        text = f"#{{?#{{@empirica_label_{key}}},#{{@empirica_label_{key}}},{label}}}"
    return f"{_style(_chip_state(key))} {key} {text} #[default] "


def shown_chip_format(key: str, label: str) -> str:
    """The chip only while it is orange or red: the line under a pane shows what needs attention, nothing dim."""
    state, chip = _chip_state(key), chip_format(key, label)
    return f"#{{?#{{==:{state},red}},{chip},#{{?#{{==:{state},orange}},{chip},}}}}"


#: The practice's name, first on the line, as a coloured block: side-by-side panes are told apart at a glance. The refresh sets
#: the pane's practice; until it has, the directory's name stands in.
PRACTICE_BLOCK = (
    "#[bg=colour31 fg=colour231 bold] #{?@empirica_practice,#{@empirica_practice},#{b:pane_current_path}} #[default] "
)


def tabs_format() -> str:
    """The practice tabs. A red mark on a tab means a pane in that practice has stopped; an orange ! that one waits on its person."""
    other = (
        "#[range=window|#{window_index} bg=colour238 fg=colour252] #I "
        f"#{{?{_DEAD_IN_WINDOW},#[fg=colour196]✗ #[fg=colour252],}}"
        f"#{{?{_WAITING_IN_WINDOW},#[fg=colour208 bold]! #[fg=colour252 nobold],}}#W #[norange default] "
    )
    current = (
        "#[range=window|#{window_index} bg=colour34 fg=colour231 bold] #I > "
        f"#{{?{_DEAD_IN_WINDOW},✗ ,}}#{{?{_WAITING_IN_WINDOW},! ,}}#W #[norange default] "
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


def top_lines(session_name: str, one_line: bool) -> list[str]:
    """The status lines at the top: practices and health, on one line when they fit beside each other."""
    tabs = tabs_format() + tick_format(session_name)
    return [tabs + health_format()] if one_line else [tabs, health_format()]


def commands_format() -> str:
    """The line under each pane: the practice's name, then only the chips that are orange or red, then the menu and help.

    The whole line is one button that opens the menu: tmux reports which pane's line was clicked but not where on it, so the chips
    here show state and the menu takes the click (David's layout, 2026-11-14).
    """
    button = "#[bg=colour25 fg=colour231 bold] ☰ commands #[default] "
    chips = "".join(shown_chip_format(key, label) for key, label in CHIPS)
    waiting = f"#{{?@empirica_waiting,{_ORANGE} waiting: #{{@empirica_waiting}} #[default] ,}}"
    return f"{PRACTICE_BLOCK}{waiting}{chips}{button}#[bg=colour25 fg=colour231 bold] ? help #[default] "


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
    """Set the top status lines for the current width and the command line under every pane. Returns the top line count."""
    target = _sess_at(session_name)
    lines = top_lines(session_name, _fits_on_one_line(session_name))
    _tmux("set-option", "-t", target, "status", "on" if len(lines) == 1 else str(len(lines)))
    for i, text in enumerate(lines):
        _tmux("set-option", "-t", target, f"status-format[{i}]", text)
    for i in range(len(lines), 3):
        _tmux("set-option", "-u", "-t", target, f"status-format[{i}]")
    commands = commands_format()
    for window_id in _tmux("list-windows", "-t", target, "-F", "#{window_id}").stdout.split():
        _tmux("set-option", "-w", "-t", window_id, "pane-border-status", "bottom")
        _tmux("set-option", "-w", "-t", window_id, "pane-border-format", commands)
    return len(lines)


# ── bindings ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _binding(table: str, key: str) -> str | None:
    """The command currently bound to ``key`` in ``table``, as tmux prints it, or None."""
    result = _tmux("list-keys", "-T", table, key)
    line = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    # Flags may stand before -T: -r, and -N with a note that may be quoted (a binding with a note was not read at all).
    match = re.match(r"""bind-key(?:\s+-r|\s+-N\s+(?:"(?:[^"\\]|\\.)*"|\S+))*\s+-T\s+\S+\s+\S+\s+(.*)$""", line)
    return match.group(1).strip() if match else None


def _as_command_string(printed: str) -> str:
    """A binding as ``list-keys`` prints it, made into text tmux parses as the same commands.

    list-keys prints the separator between chained commands as ``\\;``; inside a command string that is a literal ``;``
    handed to the first command as an argument, so replaying the printed form ran nothing.
    """
    return re.sub(r"(?<=\s)\\;(?=\s|$)", ";", printed)


def _dq(text: str) -> str:
    """``text`` inside tmux double quotes."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _action_command(verb: str, what: str) -> str:
    """The tmux command a key or a menu item runs. tmux fills in the session, pane and client before the shell sees it."""
    shell = f"{_self_command()} {verb} {what} --session '#{{session_name}}' --pane '#{{pane_id}}' --client '#{{client_name}}'"
    return f"run-shell -b {_dq(shell)}"


def menu_mouse_flag() -> list[str]:
    """``["-M"]`` when this tmux's display-menu takes it (3.5 and later), else nothing.

    Asked of tmux itself, not inferred from a version string. Without the flag a menu still answers its keys, and the menu the
    line under a pane opens still takes clicks, because a mouse binding opens it; only menus opened by a key or a process
    (prefix e Space, and the Yes/Cancel confirmation) are keyboard-only on an older tmux. Passing a flag tmux does not know makes
    it refuse the whole command, which is what CI's tmux 3.4 did.
    """
    usage = _tmux("list-commands", "display-menu").stdout
    match = re.search(r"\[-([A-Za-z]+)\]", usage)
    return ["-M"] if match and "M" in match.group(1) else []


def menu_args(position: str) -> list[str]:
    """`display-menu` arguments for the command menu. ``position`` is ``M`` at the mouse or ``P`` at the pane.

    A tmux menu is clickable and closes when its last item, Escape or anything outside it is clicked, so every command here works
    with a mouse alone. The item names are formats: they show what the chips show (a stopped pane, waiting mail).
    """
    wait = "#{?#{@empirica_label_%s}, · #{@empirica_label_%s},}"
    items = [
        ("R  restart this pane#{?pane_dead, · it has stopped,}", "r", "R"),
        (f"A  restart every stopped pane#{{?{_DEAD}, · #{{n:{_DEAD}}} stopped,}}", "a", "A"),
        ("M  mail: wake it to read its mail and re-arm its listener" + wait % ("M", "M"), "m", "M"),
        None,
        ("C  update the AI client" + wait % ("C", "C"), "c", "C"),
        # Menu only, no chip and no key: it ends every running session (David, 2026-11-14).
        ("S  restart the running sessions" + wait % ("S", "S"), "s", "S"),
        ("L  log in to Cortex" + wait % ("L", "L"), "l", "L"),
        None,
        ("?  help and guide", "?", "help"),
        None,
        # Menu only, with no shortcut key: it updates the whole ecosystem and asks first.
        ("   update Empirica on this machine…" + wait % ("U", "U"), "", "U"),
    ]
    # -M (tmux 3.5+): handle the mouse even when a key opened the menu; a menu not opened by a mouse binding ignores it otherwise.
    # Not -O: with it a click on an item did not choose it (observed on tmux 3.6).
    args = [
        "display-menu",
        *menu_mouse_flag(),
        "-T",
        "#[align=centre] cockpit · #{window_name} ",
        "-x",
        position,
        "-y",
        position,
    ]
    for item in items:
        if item is None:
            args.append("")
        else:
            name, key, chip = item
            args += [name, key, _action_command("chip", chip)]
    return [*args, "", "✕  Close", "q", ""]


def _command_string(args: list[str]) -> str:
    """A tmux argument list as one command string, for a binding that takes its command as text."""
    return " ".join(_dq(a) for a in args)


def install_bindings(mouse: bool = True) -> dict[str, Any]:
    """Bind the key table and the click on the command line. Returns ``{keys, clicks, detail}``; never takes a key in use.

    Key bindings are server-wide: pressed in a session that is not a cockpit, a command says so and does nothing, and a click on a
    pane border there goes to whatever was bound before.
    """
    out: dict[str, Any] = {"keys": False, "clicks": False, "detail": ""}
    enter = f"switch-client -T {KEY_TABLE}"
    bound = _binding("prefix", "e")
    if bound is None or bound == enter:
        _tmux("bind-key", "-T", "prefix", "e", "switch-client", "-T", KEY_TABLE)
        out["keys"] = True
    else:
        out["detail"] = (
            f"prefix e is already bound ({bound}), so the commands have no keys here; the menu still opens on a click"
        )
    for key, _label in CHIPS:
        _tmux("bind-key", "-T", KEY_TABLE, key, *shlex.split(_action_command("chip", key)))
    for key in MENU_ONLY + RETIRED_KEYS:
        _tmux("unbind-key", "-q", "-T", KEY_TABLE, key)  # an earlier version bound it
    _tmux("bind-key", "-T", KEY_TABLE, "?", *shlex.split(_action_command("chip", "help")))
    _tmux("bind-key", "-T", KEY_TABLE, "Space", *menu_args("P"))

    # An earlier version put the chips in the status line and took the status click. Give that back.
    status_click = _binding("root", "MouseDown1Status")
    if status_click and "empirica.core.cockpit.bar" in status_click:
        previous = _option(["-s"], _PREV_CLICK_OPTION) or _DEFAULT_STATUS_CLICK
        _tmux("bind-key", "-T", "root", "MouseDown1Status", *shlex.split(previous))

    if mouse:
        # MouseUp, not MouseDown: a drag on a border resizes panes and must not open the menu.
        previous = _binding("root", BORDER_CLICK)
        if previous and "@empirica_bar" in previous:
            stored = _option(["-s"], _PREV_BORDER_OPTION)
            previous = _as_command_string(stored) if stored else stored  # stored before the fix with the printed \\;
        else:
            previous = _as_command_string(previous) if previous else previous
            _tmux("set-option", "-s", _PREV_BORDER_OPTION, previous or "")
        args = ["bind-key", "-T", "root", BORDER_CLICK, "if-shell", "-F", "#{@empirica_bar}"]
        args.append(_command_string([*menu_args("M")[:1], "-t", "=", *menu_args("M")[1:]]))
        if previous:
            args.append(previous)
        _tmux(*args)
        out["clicks"] = True
    return out


# ── install ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def install(
    config: LauncherConfig, session_name: str, *, config_path: Path | None, profile: str | None
) -> dict[str, Any]:
    """Put the bars on a cockpit session. Safe to repeat. Returns ``{installed, lines, keys, clicks, detail}``."""
    if not getattr(config, "bar", True):
        return {"installed": False, "detail": "bar: false in the cockpit config"}
    if not cockpit_session_exists(session_name):
        return {
            "installed": False,
            "detail": f"session {session_name!r} is not running, so there is nothing to put the bar on",
        }
    target = _sess_at(session_name)
    for name, value in (
        ("status-position", "top"),
        ("status-interval", "15"),
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


_SHELLS_THAT_WRAP = ("sh", "bash", "zsh", "dash", "fish")


def listener_worker_counts(procs: list[dict] | None = None) -> dict[str, int]:
    """How many listener workers each practice runs: `empirica loop listen` processes, not the shells that start them.

    A listener a session starts runs as a shell (`bash -c ... empirica loop listen --instance X`) and the worker under it, and
    both carry the command line, so counting every match reads one listener as two (seen on this box for empirica-nle,
    2026-11-06). The shell is left out by its program name. ``procs`` is ``walk_listener_processes()``'s output, for tests.
    """
    from empirica.core.cockpit.listener_processes import _ai_id_from_listener_cmdline, walk_listener_processes

    counts: dict[str, int] = {}
    for proc in walk_listener_processes() if procs is None else procs:
        if proc.get("kind") != "loop_listen":
            continue
        program = os.path.basename((proc.get("cmdline") or "").split(" ", 1)[0])
        if program in _SHELLS_THAT_WRAP:
            continue
        ai_id = _ai_id_from_listener_cmdline(proc.get("cmdline") or "")
        if ai_id:
            counts[ai_id] = counts.get(ai_id, 0) + 1
    return counts


def _listener_chip(ai_id: str, workers: dict[str, int] | None = None) -> tuple[str, str, bool]:
    """(state, label, on the mesh). Red when this practice has a listener service installed and it is not running; orange when
    more than one listener worker runs for it (a duplicate, which delivers every wake twice).

    No service installed is not a fault: a practice that was never put on the mesh has no listener to be down. The third value says
    whether one is installed, which is also what decides if a failed inbox poll is worth showing. ``workers`` is
    ``listener_worker_counts()``, taken once per refresh; without it no duplicate is reported.
    """
    try:
        from empirica.core.loop_scheduler.persistent_listener import PersistentListenerService

        status = PersistentListenerService().status(ai_id)
    except Exception:
        return "", "", False
    if status.installed and not status.active:
        return "red", "listener down", True
    running = (workers or {}).get(ai_id, 0)
    if running > 1:
        return "orange", f"listener ×{running}", True
    return "", "", bool(status.installed) or running > 0


def _poll(ai_id: str, path: str, run, *extra: str) -> list[dict] | None:
    """One mailbox poll's proposals, or None when it could not be read."""
    try:
        result = run(
            [*_empirica_argv(), "mailbox", "poll", "--ai-id", ai_id, *extra, "--limit", "200", "--output", "json"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
    except Exception:
        return None
    proposals = payload.get("proposals") if isinstance(payload, dict) else None
    return [p for p in proposals if isinstance(p, dict)] if isinstance(proposals, list) else None


def _names(ai_id: str, wire: object) -> bool:
    """Whether a wire address (``org.tenant.project``) or a bare name is this practice."""
    return isinstance(wire, str) and (wire == ai_id or wire.endswith("." + ai_id))


def mail_count(ai_id: str, path: str, run=subprocess.run) -> int | None:
    """Mail addressed to this practice that still needs its reply; None when the mailbox could not be read.

    Accepted and not archived, still pending for this practice when it was sent to several (its ack not given), and not
    answered by a message from this practice in reply to it (parent_id). Everything accepted and never archived only ever grew
    (outreach read 108, most of it answered or for other targets: prop_xlduifbf5vdenieiq2hrbk4jk4).
    """
    inbox = _poll(ai_id, path, run, "--status", "accepted")
    if inbox is None:
        return None
    outbox = _poll(ai_id, path, run, "--outbox") or []
    answered = {p.get("parent_id") for p in outbox if p.get("parent_id")}
    count = 0
    for p in inbox:
        if p.get("is_archived") or p.get("id") in answered:
            continue
        pending = p.get("pending_targets") or []
        if pending and not any(_names(ai_id, w) for w in pending):
            continue  # sent to several, and this practice has acked its part
        count += 1
    return count


def mail_chip(listener_state: tuple[str, str, bool], count: int | None) -> tuple[str, str]:
    """M's (state, label): red when the listener is down, orange for a duplicate listener or mail waiting, else nothing.

    A count that could not be read shows `mail ?` on a practice on the mesh (it is dim, so only the menu shows it).
    """
    m_state, m_label, on_mesh = listener_state
    mail = f"mail {count}" if count else (MAIL_UNKNOWN if count is None and on_mesh else "")
    label = " · ".join(x for x in (m_label, mail) if x)
    if m_state:
        return m_state, label
    return ("orange", label) if count else ("", label)


def _set_option(scope: str, target: str, name: str, value: str) -> None:
    if value:
        _tmux("set-option", scope, "-t", target, name, value)
    else:
        _tmux("set-option", scope, "-u", "-t", target, name)


def _set_chip(target: str, key: str, state: str, label: str, scope: str = "-w") -> None:
    """A chip's state and label: per window (``-w``) for this machine's chips, per pane (``-p``) for the practice's."""
    for name, value in ((f"@empirica_chip_{key}", state), (f"@empirica_label_{key}", label)):
        _set_option(scope, target, name, value)


def refresh_state(
    session_name: str, *, state_dir: Path | None = None, mail=mail_count, listener=_listener_chip
) -> dict[str, Any]:
    """Set every pane's practice and M chip, and every window's machine chips, then re-fit the layout. Run detached.

    Per pane, not per window: a grid window holds several practices, and taking the first pane's state showed one practice's
    mail under all of them (prop_vc67kjtw4vfj3b5uavfqyjsycu). Each practice is checked once however many panes it has.
    """
    stamp = stamp_path(session_name, state_dir)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError:
        pass  # the status line will start another refresh; nothing else depends on the stamp
    target = _sess_at(session_name)
    if _option(["-t", target], "@empirica_bar") != "1":
        return {"ok": False, "detail": "no bar on this session"}
    rows = _tmux(
        "list-panes", "-s", "-t", exact_session(session_name), "-F", "#{pane_id}\t#{window_id}\t#{pane_current_path}"
    )
    panes: list[tuple[str, str, str | None]] = []  # (pane id, window id, practice)
    windows: list[str] = []
    paths: dict[str, str] = {}  # practice -> a directory to poll from
    for line in rows.stdout.splitlines():
        pane_id, window_id, path = (line.split("\t") + ["", ""])[:3]
        if not pane_id:
            continue
        if window_id not in windows:
            windows.append(window_id)
        ai_id = _practice_of(path)
        panes.append((pane_id, window_id, ai_id))
        if ai_id:
            paths.setdefault(ai_id, path)

    if listener is _listener_chip:
        # One process walk for the whole refresh, not one per practice.
        listener = partial(_listener_chip, workers=listener_worker_counts())

    def one(ai_id: str) -> tuple[str, tuple[str, str]]:
        return ai_id, mail_chip(listener(ai_id), mail(ai_id, paths[ai_id]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        chips = dict(pool.map(one, paths))
    machine = read_ecosystem_state()
    for window_id in windows:
        for chip in "UCSL":
            entry = machine.get(chip, {})
            _set_chip(window_id, chip, entry.get("state", ""), entry.get("label", ""))
        for chip in ("M", "I"):  # earlier versions kept these per window, where every pane in it inherited them
            _set_chip(window_id, chip, "", "")
    for pane_id, _window_id, ai_id in panes:
        _set_option("-p", pane_id, "@empirica_practice", ai_id or "")
        state, label = chips.get(ai_id, ("", "")) if ai_id else ("", "")
        _set_chip(pane_id, "M", state, label, scope="-p")
    return {"ok": True, "windows": len(windows), "practices": len(paths), "lines": layout(session_name)}


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


def _restart(session: str, pane: str | None, client: str | None, running_too: bool = False) -> None:
    """R (one pane), A (every stopped pane) and S (every practice pane, running ones too): restart, then wake each claude."""
    from empirica.core.cockpit.wake_after_resume import wake_resumed

    config = _config_of(session)
    if config is None:
        _say(client, "This tmux session is not an Empirica cockpit, so there is nothing to restart.")
        return
    result = refresh_cockpit(config, only_pane=pane, restart_running=running_too)
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
    """M (mail): give this pane a wake turn, in which the practice reads its own mail and re-arms its listener."""
    from empirica.core.cockpit.instance_actions import wake_instance

    if not pane:
        _say(client, "listener: no pane to wake")
        return
    outcome = wake_instance(f"tmux_{pane.lstrip('%')}")
    _say(client, "Woken: it will read its mail and re-arm its listener." if outcome.success else outcome.detail)


def _popup(
    client: str | None, command: str, *, directory: str | None = None, width: str = POPUP_WIDTH, height: str = "80%"
) -> None:
    """A popup running the viewer, which draws its own Close button. Nothing else is shown in a popup."""
    args = ["display-popup", "-w", width, "-h", height, "-E"]
    if client:
        args += ["-c", client]
    if directory:
        args += ["-d", directory]
    _tmux(*args, command)


def _window(session: str, name: str, command: str) -> None:
    """Run something that prints or asks questions in a window of its own: its tab is the way out, and it closes when done."""
    shown = f"{command}; echo; printf 'finished - this window closes in 10 seconds '; sleep 10"
    _tmux("new-window", "-t", _sess_at(session), "-n", name, shown)


def _login(session: str) -> None:
    """L: renew the Cortex login in a window, where its questions can be answered and its tab is the way out."""
    _window(session, "login", " ".join(shlex.quote(a) for a in [*_empirica_argv(), "auth", "login"]))


#: What a chip runs when the machine's state file and the cockpit config name nothing. S has a built-in action instead.
_BUILT_IN_COMMANDS = {"C": "claude update"}


def machine_command(key: str, config: LauncherConfig | None) -> str:
    """The command U, C or S runs here: the state file's fix, then the cockpit config's `bar_commands`, then the built-in."""
    fix = read_ecosystem_state().get(key, {}).get("fix", "")
    if not fix and config is not None:
        fix = getattr(config, "bar_commands", {}).get(key, "")
    return fix or _BUILT_IN_COMMANDS.get(key, "")


def _command_digest(command: str) -> str:
    """A short digest of the command a confirm menu showed, so Yes runs that command and no other."""
    import hashlib

    return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]


def _confirm(
    client: str | None, key: str, session: str, pane: str | None, what: str, expect: str | None = None
) -> None:
    """Ask before U, C or S acts, with a menu: Yes and Cancel are both clickable, and Escape or a click outside cancels.

    Yes carries ``expect``, the digest of the command shown, so a command that changed before Yes is asked about again.
    """
    again = f"{_self_command()} chip {key} --yes --session {shlex.quote(session)}"
    if expect:
        again += f" --expect {expect}"
    if pane:
        again += f" --pane {shlex.quote(pane)}"
    if client:
        again += f" --client {shlex.quote(client)}"
    # -M matters here: this menu is opened by a process, not by a mouse binding, and would otherwise ignore clicks. Not -O.
    args = ["display-menu", *menu_mouse_flag(), "-T", f"#[align=centre] {CONFIRM[key]} ", "-x", "C", "-y", "C"]
    if client:
        args += ["-c", client]
    _tmux(*args, f"Yes: {what}".replace("#", "##"), "y", f"run-shell -b {_dq(again)}", "", "✕  Cancel", "n", "")


def _machine_action(
    key: str, session: str, pane: str | None, client: str | None, confirmed: bool, expect: str | None = None
) -> None:
    """U, C and S: ask first, then run this machine's command in a window of its own. S restarts the panes itself by default."""
    config = _config_of(session)
    command = machine_command(key, config)
    if not command and key != "S":
        _say(
            client,
            f"{key}: no command is set for this on this machine. Add it to the cockpit config: bar_commands: {{{key}: '...'}}",
            ms=9000,
        )
        return
    shown = command or "restart every practice pane with its conversation resumed"
    if not confirmed or expect != _command_digest(shown):
        # Not confirmed yet, or confirmed for a different command (the update state changed since the menu opened).
        _confirm(client, key, session, pane, shown, expect=_command_digest(shown))
        return
    if command:
        _window(session, {"U": "update", "C": "client", "S": "sessions"}[key], command)
    else:
        _restart(session, None, client, running_too=True)


def help_view() -> int:
    """What the help popup runs: the help text in the viewer, with its Close button."""
    from empirica.core.cockpit import bar_pager
    from empirica.core.cockpit.bar_help import HELP_TEXT

    path = launcher_state.COCKPIT_DIR / "bar_help.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HELP_TEXT)
    return bar_pager.run(path, "Empirica cockpit help")


def run_chip(
    key: str, session: str, pane: str | None, client: str | None, confirmed: bool = False, expect: str | None = None
) -> None:
    """One command's action. Says what happened in the client's message line; never prints."""
    if key in ("help", "?"):
        _popup(client, f"{_self_command()} help-view")
        return
    key = key.upper()
    if key == "R":
        _restart(session, pane, client)
    elif key == "A":
        _restart(session, None, client)
    elif key in ("M", "I"):  # I was the inbox; M's wake makes the practice read its own mail (David, 2026-11-14)
        _wake(pane, client)
    elif key == "L":
        _login(session)
    elif key in CONFIRM:
        _machine_action(key, session, pane, client, confirmed, expect)
    else:
        _say(client, f"unknown cockpit command {key!r}")
        return
    if key != "L" and not (key in CONFIRM and not confirmed):
        start_refresh(session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m empirica.core.cockpit.bar", description=(__doc__ or "").splitlines()[0]
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    chip = sub.add_parser("chip")
    chip.add_argument("key")
    chip.add_argument("--session", default="")
    chip.add_argument("--pane")
    chip.add_argument("--client")
    chip.add_argument("--yes", action="store_true")
    chip.add_argument("--expect", help="digest of the command the confirm menu showed")
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
        run_chip(args.key, args.session, args.pane or None, args.client or None, confirmed=args.yes, expect=args.expect)
    except Exception as e:  # a command must answer in the message line, not with a traceback nobody sees
        _say(args.client or None, f"cockpit {args.key}: {type(e).__name__}: {e}", ms=9000)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
