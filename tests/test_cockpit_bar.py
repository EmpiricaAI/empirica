"""The cockpit's bars: practices and health on top, commands under each pane, a clickable menu, and popups that can be closed.

Asked for by David through mesh-support on 2026-11-06 (prop_uzewce5tbbew7dlvju2agoiz3i and its amendments), then revised by David
the same day after the first real launch: practices and health on top, cockpit commands and help at the bottom, update, claude and
sessions must work, and everything that opens a popup needs a close button.

What these tests hold the bars to:

- red means broken now, orange means waiting, dim means nothing to do, and a state nobody could read never looks clean;
- R and A are computed by tmux from stopped panes, per pane, so they are current without any check;
- the status line runs no check: it tests the age of a stamp and starts the refresh in the background;
- every command works with a mouse alone: the line under a pane opens a menu, the menu's items act, confirmations are menus, and
  the help and inbox viewer has a Close button. The clicks here are real mouse sequences sent to an attached client;
- key bindings are server-wide, so a key someone else uses is not taken and a border click outside a cockpit goes to what was
  bound before;
- `refresh --pane` restarts one stopped pane and leaves the rest alone.

Real tmux on a private server with a short socket path (a unix socket path longer than about 100 characters is refused).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from empirica.core.cockpit import bar, bar_help
from empirica.core.cockpit.bar_pager import CLOSE_LABEL, Pager
from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec, load_config
from empirica.core.cockpit.launcher.tmux import _tmux, launch_groups, refresh_cockpit

# ── pure: formats, menu, state file, help, viewer ───────────────────────────────────────────────────────────────────────────


def test_the_line_under_a_pane_shows_every_chip_and_is_not_made_of_click_ranges():
    line = bar.commands_format()
    for key, label in bar.CHIPS:
        assert f" {key} " in line and label in line
    assert "☰ commands" in line and "? help" in line
    assert "range=" not in line, "tmux reports no position for a border click, so ranges there would do nothing"


def test_the_top_is_practices_and_health_on_one_line_or_two():
    one, two = bar.top_lines("s", one_line=True), bar.top_lines("s", one_line=False)
    assert len(one) == 1 and "range=window" in one[0] and "E:status-right" in one[0]
    assert len(two) == 2 and "range=window" in two[0] and "E:status-right" in two[1]
    assert all("@empirica_chip" not in line for line in one + two), "the commands are not in the status line"


def test_the_status_line_runs_only_a_file_age_test_that_starts_the_refresh_in_the_background():
    tick = bar.tick_format("my session")
    assert tick in bar.top_lines("my session", True)[0]
    inner = tick[2:-1]
    assert tick.startswith("#(find ") and tick.endswith("&)")
    assert "(" not in inner and ")" not in inner and "{" not in inner, "a bracket would end the #(...) early"
    assert f"-mmin -{bar.STATE_MAX_AGE_MINUTES}" in inner and " tick " in inner and "--state-dir" in inner
    assert "mailbox" not in inner and "systemctl" not in inner, "no check runs on the status interval"


def test_r_and_a_are_computed_by_tmux_and_the_others_read_options():
    assert "pane_dead" in bar.chip_format("R", "restart")
    assert "#{W:#{P:#{?pane_dead,x,}}}" in bar.chip_format("A", "refresh")
    for key in bar.STATE_CHIPS:
        chip = bar.chip_format(key, "x")
        assert f"@empirica_chip_{key}" in chip and f"@empirica_label_{key}" in chip and "pane_dead" not in chip


def test_the_menu_has_every_command_handles_the_mouse_and_ends_with_close():
    args = bar.menu_args("P")
    assert args[0] == "display-menu" and "-O" not in args
    assert ("-M" in args) == bool(bar.menu_mouse_flag()), "the mouse flag only where this tmux takes it"
    assert args[args.index("-x") + 1] == "P" and args[args.index("-y") + 1] == "P"
    for key in [k for k, _ in bar.CHIPS] + ["help"]:
        assert any(f" chip {key} " in a for a in args), key
    assert args[-3:] == ["✕  Close", "q", ""]


@pytest.mark.parametrize(
    ("usage", "flag"),
    [
        ("display-menu (menu) [-MO] [-b border-lines] [-c target-client] ...", ["-M"]),  # tmux 3.6
        ("display-menu (menu) [-O] [-b border-lines] [-c target-client] ...", []),  # tmux 3.4
        ("", []),  # tmux missing or refusing
    ],
)
def test_the_mouse_flag_is_passed_only_to_a_tmux_that_takes_it(monkeypatch, usage, flag):
    monkeypatch.setattr(bar, "_tmux", lambda *_a, **_k: subprocess.CompletedProcess([], 0, usage, ""))
    assert bar.menu_mouse_flag() == flag
    assert ("-M" in bar.menu_args("P")) == bool(flag)


def test_update_is_in_the_menu_only_with_no_chip_and_no_shortcut():
    """David, 2026-11-06: updating the whole machine is too easy to start by accident, so it is reached through the menu only."""
    assert "U" not in dict(bar.CHIPS) and "U" in bar.MENU_ONLY
    assert " U " not in bar.commands_format() and "update" not in bar.commands_format()
    args = bar.menu_args("P")
    i = next(i for i, a in enumerate(args) if a.startswith("   update Empirica on this machine"))
    assert args[i + 1] == "", "no shortcut key in the menu either"
    assert " chip U " in args[i + 2]
    assert i > args.index("?  help and guide"), "it sits after help, away from the everyday commands"
    assert (
        not re.search(r"^\s+U  ", bar_help.HELP_TEXT, flags=re.M)
        and "update Empirica on this machine" in bar_help.HELP_TEXT
    )


def _state_file(tmp_path, components) -> Path:
    path = tmp_path / "ecosystem-state.json"
    path.write_text(json.dumps({"components": components}))
    return path


def test_no_state_file_means_no_machine_state():
    assert bar.read_ecosystem_state(Path("/nonexistent/ecosystem-state.json")) == {}


def test_a_verdict_becomes_a_colour_and_the_worst_one_wins(tmp_path):
    path = _state_file(
        tmp_path,
        {
            "plugins": {"verdict": "recommended", "label": "plugins 2 behind", "fix": "update-plugins"},
            "core": {"verdict": "needed", "label": "core below floor", "fix": "update-core"},
            "claude": {"verdict": "recommended", "label": "claude 2.1.320", "fix": "claude update"},
            "something-else": {"verdict": "needed", "label": "x", "chip": "S", "fix": "restart-sessions"},
            "unknown-component": {"verdict": "needed", "label": "ignored"},
        },
    )
    state = bar.read_ecosystem_state(path)
    assert state["U"] == {"state": "red", "label": "core below floor", "fix": "update-core"}
    assert state["C"]["state"] == "orange" and state["S"]["fix"] == "restart-sessions"
    assert set(state) == {"U", "C", "S"}


@pytest.mark.parametrize("content", ["{not json", '{"components": []}', "[]"])
def test_a_state_file_that_cannot_be_read_does_not_look_clean(tmp_path, content):
    path = tmp_path / "ecosystem-state.json"
    path.write_text(content)
    assert {c: e["label"] for c, e in bar.read_ecosystem_state(path).items()} == dict.fromkeys("UCSL", "state ?")


def test_a_machine_command_comes_from_the_state_file_then_the_config_then_the_built_in(monkeypatch):
    cfg = LauncherConfig(session_name="x", bar_commands={"U": "from-config", "C": "config-client"})
    monkeypatch.setattr(bar, "read_ecosystem_state", lambda: {"U": {"state": "", "label": "", "fix": "from-state"}})
    assert bar.machine_command("U", cfg) == "from-state"
    monkeypatch.setattr(bar, "read_ecosystem_state", dict)
    assert bar.machine_command("U", cfg) == "from-config" and bar.machine_command("C", cfg) == "config-client"
    bare = LauncherConfig(session_name="x")
    assert bar.machine_command("C", bare) == "claude update"
    assert bar.machine_command("U", bare) == "" and bar.machine_command("S", bare) == "", (
        "U needs a command; S restarts itself"
    )


def test_the_help_names_every_chip_and_has_a_section_and_an_index_link_for_every_guide_page():
    text = bar_help.HELP_TEXT
    for key, _label in bar.CHIPS:
        assert re.search(rf"^\s+{key}  \S", text, flags=re.M), key
    pages = sorted(bar_help.GUIDE_DIR.glob("[0-9][0-9]-*.md"))
    numbers = [str(int(p.name[:2])) for p in pages]
    assert len(pages) >= 12, "the guide pages ship with the package"
    assert re.findall(r"^\s+(\d+)  \S", text, flags=re.M) == numbers, "one index line per page, in order"
    for n in numbers:
        assert re.search(rf"^  §{n}  \S", text, flags=re.M), f"page {n} has no section"
        pager = Pager(text.splitlines(), "t", 30, 100)
        row = next(i for i, line in enumerate(pager.lines) if re.match(rf"^\s+{n}  \S", line))
        pager.top = 0
        pager.top = max(0, min(pager.max_top, row))
        pager.click(10, row - pager.top + 1)
        assert pager.visible()[0].startswith(f"  §{n}  "), f"the index line for page {n} does not jump to it"
    assert max(len(line) for line in text.splitlines()) <= int(bar.POPUP_WIDTH) - 2, "a line would wrap in the popup"


def test_the_rendered_guide_keeps_no_markdown_marks():
    text = bar_help.HELP_TEXT
    assert "**" not in text and "](" not in text and "|---" not in text and "`" not in text


def test_the_guide_pages_are_packaged():
    import tomllib

    root = Path(__file__).parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["empirica"]
    assert "core/cockpit/guide/*.md" in data
    assert "recursive-include empirica/core/cockpit/guide *.md" in (root / "MANIFEST.in").read_text()


def _pager(n=60, height=12, width=80) -> Pager:
    lines = ["  INDEX", "     1  First topic", "     2  Second topic"] + [f"line {i}" for i in range(n)]
    lines[20], lines[40] = "  §1  FIRST TOPIC", "  §2  SECOND TOPIC"
    return Pager(lines, "Title", height, width)


def test_the_viewer_has_a_close_button_and_clicking_it_closes():
    pager = _pager()
    start, end = pager.buttons()["close"]
    assert pager.header()[start : end + 1] == CLOSE_LABEL and pager.header().lstrip().startswith("Title")
    assert pager.click(start, 0) == "close" and pager.click(end, 0) == "close"
    assert pager.click(start - 1, 0) is None, "next to the button is not the button"


def test_the_viewer_scrolls_by_its_buttons_the_wheel_and_the_keys_and_stays_inside_the_text():
    pager = _pager()
    down, up = pager.buttons()["down"][0], pager.buttons()["up"][0]
    pager.click(down, 0)
    assert pager.top == pager.body_rows
    pager.click(up, 0)
    pager.key("up")
    assert pager.top == 0, "it does not scroll above the first line"
    pager.key("wheeldown")
    assert pager.top == 3
    pager.key("end")
    assert pager.top == pager.max_top and len(pager.visible()) == pager.body_rows
    pager.key("pgdn")
    assert pager.top == pager.max_top, "nor below the last"
    assert f"of {len(pager.lines)}" in pager.footer() and "Close" in pager.footer()


def test_a_line_in_the_index_is_a_link_to_its_section_and_q_or_escape_closes():
    pager = _pager()
    assert pager.click(10, 3) is None  # row 3 shows line 2: "     2  Second topic"
    assert pager.visible()[0] == "  §2  SECOND TOPIC"
    pager.key("home")
    pager.click(10, 1)  # the INDEX heading is not a link
    assert pager.top == 0
    assert pager.key("q") == "close" and pager.key("esc") == "close" and pager.key("down") is None


# ── real tmux, private server ───────────────────────────────────────────────────────────────────────────────────────────────

FAKE_CLAUDE = "#!/bin/sh\nexec sleep 600\n"


@pytest.fixture
def tmux_env(tmp_path, monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    # A unix socket path must be short, so the socket directory cannot live under tmp_path. The context manager removes it.
    with tempfile.TemporaryDirectory(prefix="eb.", dir="/tmp") as sock:
        monkeypatch.setenv("TMUX_TMPDIR", sock)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TMUX_PANE", raising=False)
        monkeypatch.delenv("EMPIRICA_INSTANCE_ID", raising=False)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv(
            "HOME", str(tmp_path / "home")
        )  # no ~/.tmux.conf: the server starts with tmux's own bindings
        (tmp_path / "home").mkdir()
        yield tmp_path
        subprocess.run(["tmux", "-S", f"{sock}/tmux-{os.getuid()}/default", "kill-server"], capture_output=True)


def _practice(root: Path, name: str) -> Path:
    path = root / name
    (path / ".empirica").mkdir(parents=True)
    (path / ".empirica" / "project.yaml").write_text(f"project_id: {name}\nname: {name}\nai_id: {name}\n")
    return path


def _cockpit(root: Path, session: str, names=("alpha", "beta"), **extra) -> tuple[LauncherConfig, Path]:
    raw = {
        "session_name": session,
        "surface": "tmux",
        "projects": [{"name": n, "path": str(_practice(root, n)), "launch": "claude --continue"} for n in names],
        "groups": [{"name": n, "panes": [{"project": n}]} for n in names],
        **extra,
    }
    config_path = root / f"config-{session}.yaml"
    config_path.write_text(json.dumps(raw))  # JSON is YAML
    cfg = load_config(path=config_path)
    assert launch_groups(cfg, spawn_terminal=False).error is None
    return cfg, config_path


def _opt(session: str, name: str) -> str:
    return _tmux("show-option", "-t", f"={session}:", "-qv", name).stdout.strip()


def _window_opt(target: str, name: str) -> str:
    return _tmux("show-option", "-w", "-t", target, "-qv", name).stdout.strip()


def _wait(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _pane(session: str, window: str) -> str:
    return _tmux("list-panes", "-t", f"={session}:{window}", "-F", "#{pane_id}").stdout.split()[0]


def _pid(pane: str) -> str:
    return _tmux("display-message", "-p", "-t", pane, "#{pane_pid}").stdout.strip()


def _dead(pane: str) -> bool:
    return _tmux("display-message", "-p", "-t", pane, "#{pane_dead}").stdout.strip() == "1"


def _kill_program(session: str, window: str) -> str:
    pane = _pane(session, window)
    os.kill(int(_pid(pane)), signal.SIGTERM)
    assert _wait(lambda: _dead(pane))
    return pane


class Client:
    """An attached client: a second session whose pane runs `tmux attach`. Reads the screen and clicks with real mouse sequences."""

    def __init__(self, session: str, width=170, height=34):
        self.view = f"view-{session}"
        _tmux(
            "new-session",
            "-d",
            "-s",
            self.view,
            "-x",
            str(width),
            "-y",
            str(height),
            f"env -u TMUX tmux attach -t ={session}",
        )
        assert _wait(lambda: bool(_tmux("list-clients", "-t", f"={session}", "-F", "#{client_name}").stdout.strip()))
        time.sleep(1.0)

    def lines(self, colours=False) -> list[str]:
        raw = _tmux("capture-pane", *(["-e"] if colours else []), "-p", "-t", self.view).stdout
        return [re.sub(r"\x1b\[([0-9;]*)m", r"<\1>", line) for line in raw.splitlines()]

    def row_of(self, text: str) -> int:
        return next(i + 1 for i, line in enumerate(self.lines()) if text in line)

    def click(self, col: int, row: int) -> None:
        for final in ("M", "m"):  # press, then release
            _tmux("send-keys", "-t", self.view, "-l", f"\x1b[<0;{col};{row}{final}")
        time.sleep(0.3)

    def shows(self, text: str) -> bool:
        return any(text in line for line in self.lines())


def test_install_puts_practices_on_top_and_the_commands_under_every_pane(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b1")

    out = bar.install(cfg, "b1", config_path=config_path, profile="work")

    assert out["installed"] is True and out["keys"] is True and out["clicks"] is True
    assert _opt("b1", "status-position") == "top"
    assert _opt("b1", "@empirica_profile") == "work" and _opt("b1", "@empirica_config") == str(config_path)
    assert "range=window" in _opt("b1", "status-format[0]")
    for window in ("alpha", "beta"):
        assert _window_opt(f"=b1:{window}", "pane-border-status") == "bottom"
        assert "☰ commands" in _window_opt(f"=b1:{window}", "pane-border-format")

    screen = Client("b1").lines()
    assert "alpha" in screen[0] and "beta" in screen[0], "practices are on the first row"
    assert "R restart" in screen[-1] and "? help" in screen[-1], "commands are on the last row"


def test_the_commands_sit_in_their_own_key_table_behind_prefix_e(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b2")
    bar.install(cfg, "b2", config_path=config_path, profile=None)

    assert bar._binding("prefix", "e") == f"switch-client -T {bar.KEY_TABLE}"
    table = _tmux("list-keys", "-T", bar.KEY_TABLE).stdout
    for key in [k for k, _ in bar.CHIPS] + ["?"]:
        assert re.search(rf"-T {bar.KEY_TABLE}\s+\\?{re.escape(key)}\s+run-shell -b", table), key
    assert re.search(rf"-T {bar.KEY_TABLE}\s+Space\s+display-menu", table), "Space opens the menu"
    for bare in ("C", "L", "I", "U", "M"):
        assert "empirica.core.cockpit.bar" not in (bar._binding("prefix", bare) or ""), f"prefix {bare} was taken"
    assert bar._binding(bar.KEY_TABLE, "U") is None, "update has no key"


def test_a_u_key_bound_by_an_earlier_version_is_removed(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b2u")
    _tmux("bind-key", "-T", bar.KEY_TABLE, "U", "display-message", "old")
    bar.install(cfg, "b2u", config_path=config_path, profile=None)
    assert bar._binding(bar.KEY_TABLE, "U") is None


def test_a_prefix_key_someone_else_uses_is_not_taken_and_the_install_says_so(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b3")
    _tmux("bind-key", "-T", "prefix", "e", "display-message", "mine")
    out = bar.install(cfg, "b3", config_path=config_path, profile=None)
    assert out["keys"] is False and "prefix e is already bound" in out["detail"]
    assert bar._binding("prefix", "e").startswith("display-message")


def test_a_border_click_outside_a_cockpit_goes_to_what_was_bound_before_and_a_second_install_does_not_nest(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b4")
    _tmux("bind-key", "-T", "root", bar.BORDER_CLICK, "display-message", "theirs")

    bar.install(cfg, "b4", config_path=config_path, profile=None)
    first = bar._binding("root", bar.BORDER_CLICK)
    bar.install(cfg, "b4", config_path=config_path, profile=None)

    assert bar._binding("root", bar.BORDER_CLICK) == first
    assert first.startswith('if-shell -F "#{@empirica_bar}"') and first.rstrip().endswith('"display-message theirs"')
    assert first.count("help and guide") == 1


def test_a_chained_border_click_still_runs_every_command_outside_a_cockpit(tmux_env):
    """tmux prints a chained binding with `\\;`; replayed as text that is one literal argument, so the click did nothing."""
    cfg, config_path = _cockpit(tmux_env, "b4c")
    # "\\;" reaches tmux as \; : one binding of two commands (a bare ";" would end bind-key's own command line)
    _tmux(
        "bind-key",
        "-T",
        "root",
        bar.BORDER_CLICK,
        "set-option",
        "-s",
        "@x1",
        "1",
        "\\;",
        "set-option",
        "-s",
        "@x2",
        "1",
    )

    bar.install(cfg, "b4c", config_path=config_path, profile=None)
    else_branch = shlex.split(bar._binding("root", bar.BORDER_CLICK))[-1]
    ran = _tmux("if-shell", "-F", "0", "", else_branch)

    assert ran.returncode == 0, ran.stderr
    assert _tmux("show-options", "-sv", "@x1").stdout.strip() == "1"
    assert _tmux("show-options", "-sv", "@x2").stdout.strip() == "1"


def test_the_status_click_taken_by_the_first_version_is_given_back(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b5")
    _tmux("set-option", "-s", "@empirica_prev_status_click", "display-message theirs")
    _tmux(
        "bind-key", "-T", "root", "MouseDown1Status", "run-shell", "-b", "python -m empirica.core.cockpit.bar click x"
    )

    bar.install(cfg, "b5", config_path=config_path, profile=None)

    assert bar._binding("root", "MouseDown1Status") == "display-message theirs"


def test_bar_false_installs_nothing_and_a_missing_session_says_so(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b6", bar=False)
    assert cfg.bar is False
    assert bar.install(cfg, "b6", config_path=config_path, profile=None)["installed"] is False
    assert _opt("b6", "@empirica_bar") == "" and bar._binding("prefix", "e") is None
    cfg.bar = True
    out = bar.install(cfg, "no-such-session", config_path=config_path, profile=None)
    assert out["installed"] is False and "not running" in out["detail"]


def test_the_config_keys_are_read(tmp_path):
    plain, set_ = tmp_path / "a.yaml", tmp_path / "b.yaml"
    plain.write_text("session_name: x\n")
    set_.write_text("session_name: x\nbar: false\nbar_commands:\n  u: do-update\n  S: ''\n")
    assert load_config(path=plain).bar is True and load_config(path=plain).bar_commands == {}
    assert load_config(path=set_).bar is False and load_config(path=set_).bar_commands == {"U": "do-update"}


def test_a_stopped_pane_turns_r_and_a_red_under_it_and_marks_its_tab_with_no_refresh_at_all(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b8")
    bar.install(cfg, "b8", config_path=config_path, profile=None)
    _kill_program("b8", "beta")
    _tmux("select-window", "-t", "=b8:beta")

    screen = Client("b8").lines(colours=True)

    assert "<48;5;160> R restart " in screen[-1].replace("<1><38;5;231>", ""), "R is red under the pane that stopped"
    assert "A refresh 1 " in screen[-1] and "<48;5;237> M listener " in screen[-1].replace("<38;5;244>", "")
    assert "✗" in screen[0] and "beta" in screen[0], "the stopped practice is marked on the top row"


def test_waiting_mail_turns_that_practice_s_inbox_chip_orange_and_only_that_one(tmux_env, monkeypatch):
    from empirica.core.cockpit.launcher import state

    cfg, config_path = _cockpit(tmux_env, "b9")
    bar.install(cfg, "b9", config_path=config_path, profile=None)
    monkeypatch.setattr(bar, "read_ecosystem_state", dict)

    out = bar.refresh_state(
        "b9",
        inbox=lambda ai_id, _path: ("orange", "inbox 2") if ai_id == "beta" else ("", bar.INBOX_UNKNOWN),
        listener=lambda ai_id: ("red", "listener down", True) if ai_id == "alpha" else ("", "", False),
    )

    assert out["ok"] is True and out["practices"] == 2
    assert (_window_opt("=b9:beta", "@empirica_chip_I"), _window_opt("=b9:beta", "@empirica_label_I")) == (
        "orange",
        "inbox 2",
    )
    assert _window_opt("=b9:alpha", "@empirica_label_I") == "inbox ?", (
        "a poll that failed on a mesh practice shows as unknown"
    )
    assert _window_opt("=b9:alpha", "@empirica_chip_M") == "red"
    assert (state.COCKPIT_DIR / "bar_state_b9.stamp").exists(), "the stamp stops the status line starting it again"
    _tmux("select-window", "-t", "=b9:beta")
    assert "<48;5;208> I inbox 2 " in Client("b9").lines(colours=True)[-1].replace("<1><38;5;16>", "")

    bar.refresh_state("b9", inbox=lambda _a, _p: ("", bar.INBOX_UNKNOWN), listener=lambda _a: ("", "", False))
    assert _window_opt("=b9:beta", "@empirica_chip_I") == "" and _window_opt("=b9:beta", "@empirica_label_I") == ""


def test_health_moves_to_its_own_line_when_the_practices_do_not_fit(tmux_env):
    many = tuple(f"practice-number-{i}" for i in range(12))
    cfg, config_path = _cockpit(tmux_env, "b12", names=many)
    _tmux("set-option", "-g", "status-right", "serve ok  cortex ok  load 2.1  disk 61%  12:00")
    assert bar.install(cfg, "b12", config_path=config_path, profile=None)["lines"] == 2
    assert _opt("b12", "status") == "2" and "E:status-right" in _opt("b12", "status-format[1]")

    few_cfg, few_path = _cockpit(tmux_env, "b13")
    _tmux("resize-window", "-t", "=b13:", "-x", "200", "-y", "40")
    assert bar.install(few_cfg, "b13", config_path=few_path, profile=None)["lines"] == 1


# ── refresh: one pane, and running ones too ─────────────────────────────────────────────────────────────────────────────────


def test_refresh_with_a_pane_restarts_that_one_and_leaves_the_other_dead_pane_alone(tmux_env):
    cfg, _ = _cockpit(tmux_env, "r1")
    alpha, beta = _kill_program("r1", "alpha"), _kill_program("r1", "beta")
    result = refresh_cockpit(cfg, only_pane=beta)
    assert result.error is None
    assert [r["key"] for r in result.respawned] == ["beta/0"] and result.left_dead == ["alpha/0"]
    assert not _dead(beta) and _dead(alpha)


def test_refresh_with_a_running_pane_restarts_nothing_and_a_pane_that_is_not_a_practice_is_an_error(tmux_env):
    cfg, _ = _cockpit(tmux_env, "r2")
    pane = _pane("r2", "alpha")
    result = refresh_cockpit(cfg, only_pane=pane.lstrip("%"))  # the id is accepted with or without its %
    assert result.error is None and result.respawned == [] and "alpha/0" in result.alive
    spare = _tmux("split-window", "-t", "=r2:alpha", "-P", "-F", "#{pane_id}", "sleep 600").stdout.strip()
    assert "not a practice pane" in refresh_cockpit(cfg, only_pane=spare).error
    assert "is not in cockpit session" in refresh_cockpit(cfg, only_pane="%99999").error


def test_restart_running_restarts_every_practice_pane_and_no_other(tmux_env):
    cfg, _ = _cockpit(tmux_env, "r3")
    spare = _tmux("split-window", "-t", "=r3:alpha", "-P", "-F", "#{pane_id}", "sleep 600").stdout.strip()
    before = {p: _pid(p) for p in (_pane("r3", "alpha"), _pane("r3", "beta"), spare)}
    result = refresh_cockpit(cfg, restart_running=True)
    assert sorted(r["key"] for r in result.respawned) == ["alpha/0", "beta/0"]
    assert _pid(spare) == before[spare]
    assert all(_pid(p) != pid for p, pid in before.items() if p != spare)


# ── with a mouse alone ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_click_on_the_line_under_a_pane_opens_the_menu_and_help_opens_and_closes_by_its_button(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "m1", names=("alpha",))
    bar.install(cfg, "m1", config_path=config_path, profile=None)
    client = Client("m1")

    client.click(20, len(client.lines()))
    assert _wait(lambda: client.shows("?  help and guide")), "the menu did not open"
    assert client.shows("✕  Close"), "the menu has a close item"

    client.click(12, client.row_of("?  help and guide"))
    assert _wait(lambda: client.shows(CLOSE_LABEL), timeout=10), "the help did not open"
    assert client.shows("EMPIRICA COCKPIT")

    header = next(line for line in client.lines() if CLOSE_LABEL in line)
    client.click(header.index(CLOSE_LABEL) + 4, client.row_of(CLOSE_LABEL))
    assert _wait(lambda: not client.shows(CLOSE_LABEL)), "the Close button did not close the help"


def test_restarting_the_sessions_asks_with_a_menu_and_cancel_and_yes_both_answer_a_click(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "m2", names=("alpha",))
    bar.install(cfg, "m2", config_path=config_path, profile=None)
    client = Client("m2")
    pane = _pane("m2", "alpha")
    who = _tmux("list-clients", "-t", "=m2", "-F", "#{client_name}").stdout.split()[0]
    before = _pid(pane)

    # A menu a process opens takes clicks only where tmux has display-menu -M (3.5+); elsewhere it answers its keys.
    mouse = bool(bar.menu_mouse_flag())

    def choose(label: str, key: str) -> None:
        if mouse:
            client.click(60, client.row_of(label))
        else:
            _tmux("send-keys", "-t", client.view, key)

    bar.run_chip("S", "m2", pane, who)
    assert _wait(lambda: client.shows("✕  Cancel")) and client.shows("Yes: restart every practice pane")
    choose("✕  Cancel", "n")
    assert _wait(lambda: not client.shows("✕  Cancel")) and _pid(pane) == before, "Cancel must change nothing"

    bar.run_chip("S", "m2", pane, who)
    assert _wait(lambda: client.shows("Yes: restart"))
    choose("Yes: restart", "y")
    assert _wait(lambda: _pid(pane) != before, timeout=10), "Yes did not restart the session"


# ── command actions ─────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def said(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(bar, "_say", lambda _client, text, ms=0: messages.append(text))
    return messages


def test_r_restarts_the_stopped_pane_it_is_pressed_in_and_says_so_when_there_is_nothing_to_do(
    tmux_env, said, monkeypatch
):
    import empirica.core.cockpit.wake_after_resume as war

    cfg, config_path = _cockpit(tmux_env, "c1")
    bar.install(cfg, "c1", config_path=config_path, profile=None)
    beta = _kill_program("c1", "beta")
    monkeypatch.setattr(
        war, "wake_resumed", lambda done, **_k: [{"key": d["key"], "woken": True, "detail": "woken"} for d in done]
    )

    bar.run_chip("R", "c1", beta, None)
    assert not _dead(beta) and any("Restarted and woken: beta/0" in m for m in said)

    said.clear()
    bar.run_chip("r", "c1", _pane("c1", "alpha"), None)
    assert said == ["Nothing to restart: this pane is running."]


def test_a_command_pressed_in_a_session_that_is_not_a_cockpit_says_so(tmux_env, said):
    _tmux("new-session", "-d", "-s", "plain", "sleep 600")
    bar.run_chip("A", "plain", None, None)
    assert said and "not an Empirica cockpit" in said[0]


def test_update_without_a_command_names_the_config_key_and_with_one_runs_it_in_its_own_window_once_confirmed(
    tmux_env, said, monkeypatch
):
    monkeypatch.setattr(bar, "read_ecosystem_state", dict)
    cfg, config_path = _cockpit(tmux_env, "c3")
    bar.install(cfg, "c3", config_path=config_path, profile=None)
    bar.run_chip("U", "c3", None, None)
    assert said and "bar_commands" in said[0]

    cfg2, path2 = _cockpit(tmux_env, "c4", names=("gamma", "delta"), bar_commands={"U": "sleep 600"})
    bar.install(cfg2, "c4", config_path=path2, profile=None)
    calls: list[tuple] = []
    real = bar._tmux

    def recording(*args, **kw):
        calls.append(args)
        return real(*args, **kw) if args[0] != "display-menu" else subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(bar, "_tmux", recording)
    bar.run_chip("U", "c4", None, None)
    asked = [c for c in calls if c[0] == "display-menu"]
    assert len(asked) == 1 and "Yes: sleep 600" in asked[0] and "✕  Cancel" in asked[0]
    assert ("-M" in asked[0]) == bool(bar.menu_mouse_flag()), "the mouse flag only where this tmux takes it"
    assert "update" not in real("list-windows", "-t", "=c4:", "-F", "#{window_name}").stdout.split()

    yes = next(arg for arg in asked[0] if "--yes" in arg)
    assert "--expect " in yes, "Yes carries the digest of the command it showed"
    expect = re.search(r"--expect ([0-9a-f]{16})", yes).group(1)
    bar.run_chip("U", "c4", None, None, confirmed=True, expect=expect)
    assert "update" in real("list-windows", "-t", "=c4:", "-F", "#{window_name}").stdout.split()


def test_login_runs_in_a_window_of_its_own_not_a_popup(tmux_env, monkeypatch):
    cfg, config_path = _cockpit(tmux_env, "c5")
    bar.install(cfg, "c5", config_path=config_path, profile=None)
    monkeypatch.setattr(bar, "_empirica_argv", lambda: ["sleep", "600", "#"])
    bar.run_chip("L", "c5", None, None)
    assert "login" in _tmux("list-windows", "-t", "=c5:", "-F", "#{window_name}").stdout.split()


def test_an_error_in_a_command_is_said_not_raised(said, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("tmux went away")

    monkeypatch.setattr(bar, "run_chip", boom)
    assert bar.main(["chip", "R", "--session", "s", "--pane", "%1"]) == 1
    assert said and "RuntimeError: tmux went away" in said[0]


def test_the_background_refresh_is_told_where_this_process_keeps_its_state_and_a_failed_start_is_survived():
    from empirica.core.cockpit.launcher import state

    started: list[list[str]] = []
    bar.start_refresh("s", spawn=started.append)
    assert started[0][-2:] == ["--state-dir", str(state.COCKPIT_DIR)] and started[0][-3] == "s"

    def boom(_argv):
        raise OSError("no fork")

    bar.start_refresh("s", spawn=boom)  # the status line starts it again later


def test_launch_puts_the_bars_on_and_reports_it(tmux_env, capsys):
    import empirica.cli.command_handlers.cockpit_launcher_commands as cmd

    cfg = LauncherConfig(
        session_name="l1",
        surface="tmux",
        projects=[ProjectSpec(name="alpha", path=str(_practice(tmux_env, "alpha")), launch="claude --continue")],
        groups=[GroupSpec(name="alpha", panes=[PaneSpec(project_ref="alpha")])],
    )
    assert cmd._handle_groups_in_terminal(cfg, "json", no_attach=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bar"]["installed"] is True and _opt("l1", "status-position") == "top"


# ── M: a duplicate listener ─────────────────────────────────────────────────────────────────────────────────────────────────


def _proc(cmdline, kind="loop_listen", pid=1, ppid=2):
    return {"pid": pid, "ppid": ppid, "kind": kind, "cmdline": cmdline}


def test_a_listener_s_wrapper_shell_is_not_counted_as_a_second_listener():
    """Seen on this machine 2026-11-06: one session-run listener is a bash wrapper and a python worker, both matching."""
    procs = [
        _proc("/bin/bash -c source snap.sh && empirica loop listen --instance empirica-nle; echo done"),
        _proc("/home/u/.venv/bin/python3.14 /home/u/.venv/bin/empirica loop listen --instance empirica-nle"),
        _proc("/usr/bin/python3 /home/u/.local/bin/empirica loop listen --instance empirica-outreach"),
        _proc('tail -F /home/u/.empirica/loop_fires.log | grep "instance_id": "empirica-outreach"', kind="log_tail"),
    ]
    assert bar.listener_worker_counts(procs) == {"empirica-nle": 1, "empirica-outreach": 1}


def test_two_workers_for_one_practice_are_a_duplicate_and_its_neighbour_is_not():
    procs = [
        _proc("python /x/empirica loop listen --instance empirica"),
        _proc("python /y/empirica loop listen --instance empirica", pid=3),
        _proc("python /x/empirica loop listen --instance empirica-outreach", pid=4),
    ]
    assert bar.listener_worker_counts(procs) == {"empirica": 2, "empirica-outreach": 1}


@pytest.fixture
def service(monkeypatch):
    from empirica.core.loop_scheduler import persistent_listener as pl

    state = {"installed": True, "active": True}

    class Fake:
        def status(self, ai_id):
            return pl.ListenerStatus(
                ai_id=ai_id, backend="systemd", installed=state["installed"], active=state["active"]
            )

    monkeypatch.setattr(pl, "PersistentListenerService", Fake)
    return state


def test_m_is_orange_with_the_count_for_a_duplicate_and_red_wins_when_the_service_is_down(service):
    assert bar._listener_chip("empirica", {"empirica": 2}) == ("orange", "listener ×2", True)
    assert bar._listener_chip("empirica", {"empirica": 1}) == ("", "", True)
    service["active"] = False
    assert bar._listener_chip("empirica", {"empirica": 2})[:2] == ("red", "listener down")


def test_a_practice_with_only_a_session_listener_counts_as_on_the_mesh(service):
    service["installed"] = False
    assert bar._listener_chip("empirica-nle", {"empirica-nle": 1}) == ("", "", True)
    assert bar._listener_chip("empirica-nle", {}) == ("", "", False)
