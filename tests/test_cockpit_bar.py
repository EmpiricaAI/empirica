"""The cockpit's bottom bar: practice tabs with health, chips coloured by state, keys, clicks and a scrolling help.

Asked for by David through mesh-support on 2026-11-06 (prop_uzewce5tbbew7dlvju2agoiz3i and three amendments): nobody should have to
leave the cockpit for routine actions, and a new person should be able to learn Empirica from inside it.

What these tests hold the bar to:

- red means broken now, orange means waiting, dim means nothing to do, and a state nobody could read never looks clean;
- R and A are computed by tmux from dead panes, so they are current without any check;
- the status line runs no check: it tests the age of a stamp and starts the refresh in the background;
- the chips live in an `empirica` key table behind `prefix e` (the bare prefix letters are taken), and are click ranges;
- key bindings are server-wide, so a click that is not one of ours goes to whatever was bound before, and a key someone else uses
  is not taken;
- `refresh --pane` restarts one dead pane and leaves the rest alone.

Real tmux on a private server with a short socket path (a unix socket path longer than about 100 characters is refused).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from empirica.core.cockpit import bar, bar_help
from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec, load_config
from empirica.core.cockpit.launcher.tmux import _tmux, launch_groups, refresh_cockpit

# ── pure: formats, state file, help ─────────────────────────────────────────────────────────────────────────────────────────


def test_every_chip_is_a_click_range_with_its_letter_and_the_help_chip_is_there():
    line = bar.commands_format("s")
    for key, label in bar.CHIPS:
        assert f"#[range=user|emp_{key}]" in line and f" {key} " in line and label in line
    assert "#[range=user|emp_help" in line and "? help" in line


def test_the_status_line_runs_only_a_file_age_test_that_starts_the_refresh_in_the_background():
    tick = bar.tick_format("my session")
    assert tick.startswith("#(find ") and tick.endswith("&)")
    inner = tick[2:-1]
    assert "(" not in inner and ")" not in inner and "{" not in inner, "a bracket would end the #(...) early"
    assert f"-mmin -{bar.STATE_MAX_AGE_MINUTES}" in inner and " tick " in inner and "--state-dir" in inner
    assert "mailbox" not in inner and "systemctl" not in inner, "no check runs on the status interval"


def test_r_and_a_are_computed_by_tmux_and_the_others_read_options():
    assert "pane_dead" in bar.chip_format("R", "restart")
    assert "#{W:#{P:#{?pane_dead,x,}}}" in bar.chip_format("A", "refresh")
    for key in bar.STATE_CHIPS:
        chip = bar.chip_format(key, "x")
        assert f"@empirica_chip_{key}" in chip and f"@empirica_label_{key}" in chip and "pane_dead" not in chip


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
            "login": {"verdict": "ok", "label": ""},
            "something-else": {"verdict": "needed", "label": "x", "chip": "S", "fix": "restart-sessions"},
            "unknown-component": {"verdict": "needed", "label": "ignored"},
        },
    )
    state = bar.read_ecosystem_state(path)
    assert state["U"] == {"state": "red", "label": "core below floor", "fix": "update-core"}
    assert state["C"] == {"state": "orange", "label": "claude 2.1.320", "fix": "claude update"}
    assert state["L"]["state"] == ""
    assert state["S"]["fix"] == "restart-sessions", "an explicit chip field places a component"
    assert set(state) == {"U", "C", "S", "L"}


@pytest.mark.parametrize("content", ["{not json", '{"components": []}', "[]"])
def test_a_state_file_that_cannot_be_read_does_not_look_clean(tmp_path, content):
    path = tmp_path / "ecosystem-state.json"
    path.write_text(content)
    state = bar.read_ecosystem_state(path)
    assert {chip: entry["label"] for chip, entry in state.items()} == dict.fromkeys("UCSL", "state ?")


def test_the_help_names_every_chip_and_has_a_section_for_every_guide_topic():
    text = bar_help.HELP_TEXT
    for key, _label in bar.CHIPS:
        assert re.search(rf"^\s+{key}  \S", text, flags=re.M), key
    index = re.findall(r"^\s+(\d+)  \S", text, flags=re.M)
    assert index == [str(n) for n in range(1, 12)], "the guide index lists topics 1 to 11"
    for n in range(1, 12):
        assert re.search(rf"^  §{n}  \S", text, flags=re.M), f"topic {n} has no section"
    assert max(len(line) for line in text.splitlines()) <= int(bar.POPUP_WIDTH) - 2, "a line would wrap in the popup"
    assert "Ctrl+b, then e, then the letter" in text


def _less(version_line):
    def run(_argv, **_kw):
        return subprocess.CompletedProcess([], 0, stdout=version_line + "\n", stderr="")

    return bar.less_command(Path("/x/help.txt"), run=run)


def test_help_scrolls_with_the_wheel_when_less_supports_it_and_with_keys_when_it_does_not():
    new, old = _less("less 668 (GNU regular expressions)"), _less("less 487 (POSIX regular expressions)")
    assert "--mouse" in new and "--wheel-lines=3" in new
    assert "--mouse" not in old
    for args, wheel in ((new, True), (old, False)):
        prompt = args[args.index("-P") + 1]
        assert ("wheel" in prompt) is wheel and "PgUp PgDn" in prompt and "q closes" in prompt
        assert args[-1] == "/x/help.txt"


def test_a_less_whose_version_cannot_be_read_gets_keys_only():
    def boom(_argv, **_kw):
        raise OSError("no less")

    assert "--mouse" not in bar.less_command(Path("/x"), run=boom)


# ── real tmux, private server ───────────────────────────────────────────────────────────────────────────────────────────────

FAKE_CLAUDE = "#!/bin/sh\nexec sleep 600\n"


@pytest.fixture
def cockpit_dir(tmp_path, monkeypatch):
    """Keep every cockpit state file off the real ~/.empirica/cockpit, and start no background process."""
    from empirica.core.cockpit.launcher import state

    d = tmp_path / "cockpit-state"
    d.mkdir()
    monkeypatch.setattr(state, "COCKPIT_DIR", d)
    monkeypatch.setattr(state, "LAST_SESSION_START_PATH", d / "last_session_start")
    monkeypatch.setattr(state, "LAST_CLEAN_SHUTDOWN_PATH", d / "last_clean_shutdown")
    monkeypatch.setattr(state, "LOCK_PATH", d / "active.lock")
    return d


@pytest.fixture
def tmux_env(tmp_path, monkeypatch, cockpit_dir):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    sock_dir = Path(tempfile.mkdtemp(prefix="eb.", dir="/tmp"))
    monkeypatch.setenv("TMUX_TMPDIR", str(sock_dir))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("EMPIRICA_INSTANCE_ID", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no ~/.tmux.conf: the server starts with tmux's own bindings
    (tmp_path / "home").mkdir()
    yield tmp_path
    private = sock_dir / f"tmux-{os.getuid()}" / "default"
    subprocess.run(["tmux", "-S", str(private), "kill-server"], capture_output=True)
    shutil.rmtree(sock_dir, ignore_errors=True)


def _practice(root: Path, name: str) -> Path:
    path = root / name
    (path / ".empirica").mkdir(parents=True)
    (path / ".empirica" / "project.yaml").write_text(f"project_id: {name}\nname: {name}\nai_id: {name}\n")
    return path


def _cockpit(root: Path, session: str, names=("alpha", "beta")) -> tuple[LauncherConfig, Path]:
    projects = [ProjectSpec(name=n, path=str(_practice(root, n)), launch="claude --continue") for n in names]
    groups = [GroupSpec(name=n, panes=[PaneSpec(project_ref=n)]) for n in names]
    cfg = LauncherConfig(session_name=session, projects=projects, groups=groups)
    config_path = root / f"config-{session}.yaml"
    config_path.write_text(
        json.dumps(
            {
                "session_name": session,
                "surface": "tmux",
                "projects": [{"name": p.name, "path": p.path, "launch": p.launch} for p in projects],
                "groups": [{"name": n, "panes": [{"project": n}]} for n in names],
            }
        )
    )  # JSON is YAML
    assert launch_groups(cfg, spawn_terminal=False).error is None
    return cfg, config_path


def _opt(session: str, name: str) -> str:
    return _tmux("show-option", "-t", f"={session}:", "-qv", name).stdout.strip()


def _window_opt(target: str, name: str) -> str:
    return _tmux("show-option", "-w", "-t", target, "-qv", name).stdout.strip()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _kill_program(session: str, window: str) -> str:
    pane = _tmux("list-panes", "-t", f"={session}:{window}", "-F", "#{pane_id}").stdout.split()[0]
    pid = int(_tmux("display-message", "-p", "-t", pane, "#{pane_pid}").stdout.strip())
    os.kill(pid, signal.SIGTERM)
    assert _wait(lambda: _tmux("display-message", "-p", "-t", pane, "#{pane_dead}").stdout.strip() == "1")
    return pane


def _screen(session: str, width=170) -> list[str]:
    """What an attached client sees, with colours as <codes>: attach inside a second session and capture it."""
    _tmux(
        "new-session",
        "-d",
        "-s",
        f"view-{session}",
        "-x",
        str(width),
        "-y",
        "30",
        f"env -u TMUX tmux attach -t ={session}",
    )
    assert _wait(lambda: bool(_tmux("list-clients", "-t", f"={session}", "-F", "#{client_name}").stdout.strip()))
    time.sleep(1.0)
    raw = _tmux("capture-pane", "-e", "-p", "-t", f"view-{session}").stdout
    return [re.sub(r"\x1b\[([0-9;]*)m", r"<\1>", line) for line in raw.splitlines()]


def test_install_sets_the_bar_on_the_session_and_records_the_profile(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b1")

    out = bar.install(cfg, "b1", config_path=config_path, profile="work")

    assert out["installed"] is True and out["keys"] is True and out["clicks"] is True
    assert _opt("b1", "status-position") == "bottom"
    assert _opt("b1", "status") == str(out["lines"])
    assert _opt("b1", "@empirica_profile") == "work"
    assert _opt("b1", "@empirica_config") == str(config_path)
    assert "emp_R" in _opt("b1", f"status-format[{out['lines'] - 1}]")
    assert "E:status-right" in _opt("b1", "status-format[0]") + _opt("b1", "status-format[1]")


def test_the_chips_sit_in_their_own_key_table_behind_prefix_e(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b2")
    bar.install(cfg, "b2", config_path=config_path, profile=None)

    assert bar._binding("prefix", "e") == f"switch-client -T {bar.KEY_TABLE}"
    table = _tmux("list-keys", "-T", bar.KEY_TABLE).stdout
    for key in [k for k, _ in bar.CHIPS] + ["?"]:
        assert re.search(rf"-T {bar.KEY_TABLE}\s+\\?{re.escape(key)}\s+run-shell -b", table), key
    for bare in ("C", "L", "I", "U", "M"):
        assert "empirica.core.cockpit.bar" not in (bar._binding("prefix", bare) or ""), f"prefix {bare} was taken"


def test_a_prefix_key_someone_else_uses_is_not_taken_and_the_install_says_so(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b3")
    _tmux("bind-key", "-T", "prefix", "e", "display-message", "mine")

    out = bar.install(cfg, "b3", config_path=config_path, profile=None)

    assert out["keys"] is False and "prefix e is already bound" in out["detail"]
    assert bar._binding("prefix", "e").startswith("display-message")


def test_a_click_that_is_not_ours_goes_to_what_was_bound_before_and_a_second_install_does_not_nest(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b4")
    _tmux("bind-key", "-T", "root", "MouseDown1Status", "display-message", "theirs")

    bar.install(cfg, "b4", config_path=config_path, profile=None)
    first = bar._binding("root", "MouseDown1Status")
    bar.install(cfg, "b4", config_path=config_path, profile=None)
    second = bar._binding("root", "MouseDown1Status")

    assert first == second
    assert first.startswith('if-shell -F "#{m:emp_*,#{mouse_status_range}}"')
    assert first.rstrip().endswith('"display-message theirs"')
    assert first.count("empirica.core.cockpit.bar") == 1


def test_without_an_earlier_binding_the_fallback_is_tmux_s_own(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b5")
    default = bar._binding("root", "MouseDown1Status")
    bar.install(cfg, "b5", config_path=config_path, profile=None)
    assert bar._binding("root", "MouseDown1Status").rstrip().endswith(f'"{default}"')


def test_bar_false_in_the_config_installs_nothing(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b6")
    cfg.bar = False
    out = bar.install(cfg, "b6", config_path=config_path, profile=None)
    assert out["installed"] is False and _opt("b6", "@empirica_bar") == ""
    assert bar._binding("prefix", "e") is None


def test_install_on_a_session_that_is_not_running_says_so(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b7")
    out = bar.install(cfg, "no-such-session", config_path=config_path, profile=None)
    assert out["installed"] is False and "not running" in out["detail"]


def test_the_config_key_is_read_and_defaults_to_on(tmp_path):
    on, off = tmp_path / "on.yaml", tmp_path / "off.yaml"
    on.write_text("session_name: x\n")
    off.write_text("session_name: x\nbar: false\n")
    assert load_config(path=on).bar is True and load_config(path=off).bar is False


def test_a_dead_pane_turns_r_and_a_red_and_marks_its_tab_with_no_refresh_at_all(tmux_env):
    cfg, config_path = _cockpit(tmux_env, "b8")
    bar.install(cfg, "b8", config_path=config_path, profile=None)
    _kill_program("b8", "beta")
    _tmux("select-window", "-t", "=b8:beta")

    chips = _screen("b8")[-1]

    assert "<1><38;5;231><48;5;160> R restart " in chips, "R is red on the pane that stopped"
    assert "<48;5;160> A refresh 1 " in chips, "A is red and counts the stopped panes"
    assert "<38;5;244><48;5;237> M listener " in chips, "nothing else lit up"
    assert any("✗" in line and "beta" in line for line in _screen_lines("b8"))


def _screen_lines(session: str) -> list[str]:
    return _tmux("capture-pane", "-p", "-t", f"view-{session}").stdout.splitlines()[-3:]


def test_waiting_mail_turns_that_practice_s_inbox_chip_orange_and_only_that_one(tmux_env, cockpit_dir, monkeypatch):
    cfg, config_path = _cockpit(tmux_env, "b9")
    bar.install(cfg, "b9", config_path=config_path, profile=None)
    monkeypatch.setattr(bar, "read_ecosystem_state", dict)

    out = bar.refresh_state(
        "b9",
        inbox=lambda ai_id, _path: ("orange", "inbox 2") if ai_id == "beta" else ("", ""),
        listener=lambda ai_id: ("red", "listener down", True) if ai_id == "alpha" else ("", "", True),
    )

    assert out["ok"] is True and out["practices"] == 2
    assert (_window_opt("=b9:beta", "@empirica_chip_I"), _window_opt("=b9:beta", "@empirica_label_I")) == (
        "orange",
        "inbox 2",
    )
    assert _window_opt("=b9:alpha", "@empirica_chip_I") == ""
    assert (_window_opt("=b9:alpha", "@empirica_chip_M"), _window_opt("=b9:alpha", "@empirica_label_M")) == (
        "red",
        "listener down",
    )
    assert (cockpit_dir / "bar_state_b9.stamp").exists(), "the stamp is what stops the status line starting it again"

    _tmux("select-window", "-t", "=b9:beta")
    assert "<1><38;5;16><48;5;208> I inbox 2 " in _screen("b9")[-1]


def test_a_poll_that_fails_shows_unknown_on_a_mesh_practice_and_nothing_on_one_that_is_not(tmux_env, monkeypatch):
    cfg, config_path = _cockpit(tmux_env, "b10")
    bar.install(cfg, "b10", config_path=config_path, profile=None)
    monkeypatch.setattr(bar, "read_ecosystem_state", dict)

    bar.refresh_state(
        "b10",
        inbox=lambda _ai, _path: ("", bar.INBOX_UNKNOWN),
        listener=lambda ai_id: ("", "", ai_id == "alpha"),
    )

    assert _window_opt("=b10:alpha", "@empirica_label_I") == "inbox ?"
    assert _window_opt("=b10:beta", "@empirica_label_I") == ""


def test_a_state_that_clears_is_removed_not_left_behind(tmux_env, monkeypatch):
    cfg, config_path = _cockpit(tmux_env, "b11")
    bar.install(cfg, "b11", config_path=config_path, profile=None)
    monkeypatch.setattr(bar, "read_ecosystem_state", lambda: {"U": {"state": "orange", "label": "drift", "fix": "x"}})
    quiet = {"inbox": lambda _a, _p: ("", ""), "listener": lambda _a: ("", "", True)}
    bar.refresh_state("b11", **quiet)
    assert _window_opt("=b11:alpha", "@empirica_chip_U") == "orange"

    monkeypatch.setattr(bar, "read_ecosystem_state", dict)
    bar.refresh_state("b11", **quiet)
    assert _window_opt("=b11:alpha", "@empirica_chip_U") == "" and _window_opt("=b11:alpha", "@empirica_label_U") == ""


def test_health_moves_to_its_own_line_when_the_practices_do_not_fit(tmux_env):
    many = tuple(f"practice-number-{i}" for i in range(12))
    cfg, config_path = _cockpit(tmux_env, "b12", names=many)
    _tmux("set-option", "-g", "status-right", "serve ok  cortex ok  load 2.1  disk 61%  12:00")

    out = bar.install(cfg, "b12", config_path=config_path, profile=None)

    assert out["lines"] == 3
    assert "E:status-right" in _opt("b12", "status-format[1]") and "emp_R" in _opt("b12", "status-format[2]")

    few_cfg, few_path = _cockpit(tmux_env, "b13")
    _tmux("resize-window", "-t", "=b13:", "-x", "200", "-y", "40")
    assert bar.install(few_cfg, "b13", config_path=few_path, profile=None)["lines"] == 2


# ── refresh --pane ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_refresh_with_a_pane_restarts_that_one_and_leaves_the_other_dead_pane_alone(tmux_env):
    cfg, _ = _cockpit(tmux_env, "r1")
    alpha, beta = _kill_program("r1", "alpha"), _kill_program("r1", "beta")

    result = refresh_cockpit(cfg, only_pane=beta)

    assert result.error is None
    assert [r["key"] for r in result.respawned] == ["beta/0"] and result.left_dead == ["alpha/0"]
    assert _tmux("display-message", "-p", "-t", beta, "#{pane_dead}").stdout.strip() == "0"
    assert _tmux("display-message", "-p", "-t", alpha, "#{pane_dead}").stdout.strip() == "1"


def test_refresh_with_a_running_pane_restarts_nothing(tmux_env):
    cfg, _ = _cockpit(tmux_env, "r2")
    pane = _tmux("list-panes", "-t", "=r2:alpha", "-F", "#{pane_id}").stdout.split()[0]
    result = refresh_cockpit(cfg, only_pane=pane.lstrip("%"))  # the id is accepted with or without its %
    assert result.error is None and result.respawned == [] and "alpha/0" in result.alive


def test_refresh_with_a_pane_that_is_not_a_practice_pane_is_an_error(tmux_env):
    cfg, _ = _cockpit(tmux_env, "r3")
    spare = _tmux("split-window", "-t", "=r3:alpha", "-P", "-F", "#{pane_id}", "sleep 600").stdout.strip()
    assert "not a practice pane" in refresh_cockpit(cfg, only_pane=spare).error
    assert "is not in cockpit session" in refresh_cockpit(cfg, only_pane="%99999").error


# ── chip actions ────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def said(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(bar, "_say", lambda _client, text, ms=0: messages.append(text))
    return messages


def test_r_restarts_the_dead_pane_it_is_pressed_in(tmux_env, said, monkeypatch):
    import empirica.core.cockpit.wake_after_resume as war

    cfg, config_path = _cockpit(tmux_env, "c1")
    bar.install(cfg, "c1", config_path=config_path, profile=None)
    beta = _kill_program("c1", "beta")
    monkeypatch.setattr(
        war, "wake_resumed", lambda done, **_k: [{"key": d["key"], "woken": True, "detail": "woken"} for d in done]
    )

    bar.run_chip("R", "c1", beta, None)

    assert _tmux("display-message", "-p", "-t", beta, "#{pane_dead}").stdout.strip() == "0"
    assert any("Restarted and woken: beta/0" in m for m in said)


def test_r_in_a_running_pane_says_there_is_nothing_to_restart(tmux_env, said):
    cfg, config_path = _cockpit(tmux_env, "c2")
    bar.install(cfg, "c2", config_path=config_path, profile=None)
    pane = _tmux("list-panes", "-t", "=c2:alpha", "-F", "#{pane_id}").stdout.split()[0]
    bar.run_chip("emp_R", "c2", pane, None)  # a click passes the range name
    assert said == ["Nothing to restart: this pane is running."]


def test_a_chip_pressed_in_a_session_that_is_not_a_cockpit_says_so(tmux_env, said):
    _tmux("new-session", "-d", "-s", "plain", "sleep 600")
    bar.run_chip("A", "plain", None, None)
    assert said and "not an Empirica cockpit" in said[0]


def test_u_without_a_command_on_this_machine_says_so_and_runs_nothing(tmux_env, said, monkeypatch):
    cfg, config_path = _cockpit(tmux_env, "c3")
    bar.install(cfg, "c3", config_path=config_path, profile=None)
    monkeypatch.setattr(bar, "read_ecosystem_state", dict)
    before = _tmux("list-windows", "-t", "=c3:", "-F", "#{window_name}").stdout

    bar.run_chip("U", "c3", None, None)

    assert said and "nothing is set up for this on this machine" in said[0]
    assert _tmux("list-windows", "-t", "=c3:", "-F", "#{window_name}").stdout == before


def test_u_asks_first_and_runs_the_machine_s_command_only_once_confirmed(tmux_env, monkeypatch):
    cfg, config_path = _cockpit(tmux_env, "c4")
    bar.install(cfg, "c4", config_path=config_path, profile=None)
    monkeypatch.setattr(
        bar, "read_ecosystem_state", lambda: {"U": {"state": "orange", "label": "drift", "fix": "sleep 600"}}
    )
    calls: list[tuple] = []
    real = bar._tmux

    def recording(*args, **kw):
        calls.append(args)
        return real(*args, **kw) if args[0] != "confirm-before" else subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(bar, "_tmux", recording)

    bar.run_chip("U", "c4", None, None)
    asked = [c for c in calls if c[0] == "confirm-before"]
    assert len(asked) == 1 and "sleep 600" in " ".join(asked[0]) and "--yes" in asked[0][-1]
    assert "update" not in real("list-windows", "-t", "=c4:", "-F", "#{window_name}").stdout.split()

    bar.run_chip("U", "c4", None, None, confirmed=True)
    assert "update" in real("list-windows", "-t", "=c4:", "-F", "#{window_name}").stdout.split()


def test_the_bindings_run_this_module_and_an_error_in_a_chip_is_said_not_raised(said, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("tmux went away")

    monkeypatch.setattr(bar, "run_chip", boom)
    assert bar.main(["chip", "R", "--session", "s", "--pane", "%1"]) == 1
    assert said and "RuntimeError: tmux went away" in said[0]


def test_the_background_refresh_is_told_where_this_process_keeps_its_state(cockpit_dir):
    started: list[list[str]] = []
    bar.start_refresh("s", spawn=started.append)
    (argv,) = started
    assert argv[-2:] == ["--state-dir", str(cockpit_dir)] and argv[-3] == "s"


def test_a_refresh_that_cannot_be_started_does_not_break_the_launch(cockpit_dir):
    def boom(_argv):
        raise OSError("no fork")

    bar.start_refresh("s", spawn=boom)  # the status line starts it again later


# ── launch installs it ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_launch_puts_the_bar_on_and_reports_it(tmux_env, capsys):
    import empirica.cli.command_handlers.cockpit_launcher_commands as cmd

    root = tmux_env
    cfg = LauncherConfig(
        session_name="l1",
        surface="tmux",
        projects=[ProjectSpec(name="alpha", path=str(_practice(root, "alpha")), launch="claude --continue")],
        groups=[GroupSpec(name="alpha", panes=[PaneSpec(project_ref="alpha")])],
    )

    rc = cmd._handle_groups_in_terminal(cfg, "json", no_attach=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bar"]["installed"] is True and payload["bar"]["lines"] in (2, 3)
    assert _opt("l1", "@empirica_bar") == "1"
