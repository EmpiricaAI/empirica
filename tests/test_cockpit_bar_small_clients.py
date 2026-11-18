"""The cockpit bar on small clients and failed commands (goal 8da5f220 gap check): a menu or popup tmux cannot draw used to
show nothing, and an update window closed after 10 s whatever its exit status."""

from __future__ import annotations

from types import SimpleNamespace

from empirica.core.cockpit import bar


def test_the_menu_is_guarded_by_the_clients_height():
    args = bar.guarded_menu("P")
    assert args[:2] == ["if-shell", "-F"] and "client_height" in args[2]
    assert "display-menu" in args[3] and "too small for the command menu" in args[4]
    assert int(args[2].rsplit(",", 1)[1].rstrip("}")) >= bar.menu_rows(bar.menu_args("P"))


def test_the_help_popup_fits_a_narrow_client_and_says_when_it_cannot(monkeypatch):
    calls, said = [], []

    def fake_tmux(*args):
        calls.append(args)
        if args[0] == "display-message" and "-p" in args:
            return SimpleNamespace(stdout="80\n", returncode=0, stderr="")
        refused = args[0] == "display-popup"  # tmux's own refusal, which comes at once
        return SimpleNamespace(stdout="", returncode=1 if refused else 0, stderr="width too large" if refused else "")

    monkeypatch.setattr(bar, "_tmux", fake_tmux)
    monkeypatch.setattr(bar, "_say", lambda client, text, ms=6000: said.append(text))
    bar._popup("c1", "help")
    popup = next(c for c in calls if c[0] == "display-popup")
    assert popup[popup.index("-w") + 1] == "78"
    assert said and "too small for the help" in said[0]


def test_a_failed_window_command_stays_on_screen(monkeypatch):
    calls = []
    monkeypatch.setattr(bar, "_tmux", lambda *a: calls.append(a) or SimpleNamespace(stdout="", returncode=0))
    bar._window("s", "update", "false")
    shown = calls[-1][-1]
    assert "FAILED" in shown and "read -r" in shown and "rc -eq 0" in shown


def test_core_computes_a_health_readout(monkeypatch, tmp_path):
    """David 2026-11-17: a client's fresh machine showed only its name and the time; core ships the readout."""
    from empirica.config import credentials_loader as cl

    monkeypatch.setattr(
        cl, "get_credentials_loader", lambda: SimpleNamespace(get_cortex_config=lambda: {"url": "https://c.test"})
    )
    asked = []
    text = bar.health_readout(answers=lambda url: asked.append(url) or "c.test" in url)
    assert asked[0] == "https://c.test/health" and "/health" in asked[1]
    assert "#[fg=colour2]cortex●" in text and "serve●" in text and "disk " in text


def test_the_bar_shows_core_health_unless_the_machine_runs_its_own(monkeypatch):
    assert "@empirica_health" in bar.health_format() and "status-right" in bar.health_format()
    monkeypatch.setattr(bar, "_tmux", lambda *a: SimpleNamespace(stdout="#(~/status-line.sh) %H:%M\n", returncode=0))
    assert bar._machine_has_own_readout() is True
    monkeypatch.setattr(
        bar, "_tmux", lambda *a: SimpleNamespace(stdout='"#{=21:pane_title}" %H:%M %d-%b-%y\n', returncode=0)
    )
    assert bar._machine_has_own_readout() is False


def test_layout_is_redone_on_resize_and_new_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(bar, "_tmux", lambda *a: calls.append(a) or SimpleNamespace(stdout="", returncode=0))
    monkeypatch.setattr(bar, "cockpit_session_exists", lambda _s: True)
    monkeypatch.setattr(bar, "layout", lambda _s: 2)
    monkeypatch.setattr(bar, "install_bindings", lambda mouse=True: {})
    bar.install(SimpleNamespace(bar=True, mouse=True), "cock", config_path=None, profile=None)
    hooks = {c[3]: c[4] for c in calls if c[0] == "set-hook"}
    assert set(hooks) == {"client-resized", "after-new-window", "client-attached"}
    assert all(" layout " in h and h.startswith("run-shell -b") for h in hooks.values())


def test_u_defaults_to_the_upgrade_that_matches_the_install(tmp_path):
    """David 2026-11-17: with nothing configured, U told a browser user to edit a config file."""

    def installed(*parts):
        f = tmp_path.joinpath(*parts, "empirica", "core", "cockpit", "bar.py")
        f.parent.mkdir(parents=True)
        f.write_text("")
        return str(f)

    pipx = bar.default_update_command(package_file=installed("pipx", "venvs", "empirica", "lib", "site-packages"))
    assert pipx.startswith("pipx upgrade empirica && ") and pipx.endswith("setup-claude-code --force")
    pip = bar.default_update_command(package_file=installed("venv", "lib", "site-packages"), executable="/v/bin/python")
    assert pip.startswith("/v/bin/python -m pip install --upgrade empirica && ")
    checkout = tmp_path / "src"
    (checkout / ".git").mkdir(parents=True)
    f = checkout / "empirica" / "core" / "cockpit" / "bar.py"
    f.parent.mkdir(parents=True)
    f.write_text("")
    assert bar.default_update_command(package_file=str(f)) == ""  # a source checkout updates by git


def test_sweep_fixes_for_1_17_7(monkeypatch, tmp_path):
    """Fish users' windows, a long-open help popup, the menu guard's height, and a near-miss flag (1.17.7 sweep)."""
    calls = []
    monkeypatch.setattr(bar, "_tmux", lambda *a: calls.append(a) or SimpleNamespace(stdout="", returncode=0, stderr=""))
    bar._window("s", "u", "true")
    assert calls[-1][-1].startswith("sh -c ")  # never the user's default-shell
    said = []
    monkeypatch.setattr(bar, "_say", lambda c, t, ms=6000: said.append(t))
    monkeypatch.setattr(bar, "_tmux", lambda *a: SimpleNamespace(stdout="120", returncode=124, stderr=""))
    bar._popup("c", "help")
    assert not said  # a popup open past the timeout is not "too small"
    assert bar.menu_rows(bar.menu_args("P")) <= 16
    from empirica.core.cockpit.launcher.config import _with_remote_control_name

    assert _with_remote_control_name("claude --remote-control-session-name-prefix box", "p").endswith(
        "--remote-control p"
    )


def test_a_resize_storm_runs_one_relayout_at_a_time(monkeypatch, tmp_path):
    """Each relayout is a fresh Python process (median 0.23 s, ~30 MB, measured 2026-11-18). A drag fires client-resized
    many times, so the hook holds a lock: a second relayout while one runs exits at once, and the bar's 15 s refresh
    catches the last size. Where flock is missing (macOS), the hook runs the relayout as before."""
    import subprocess

    calls = []
    monkeypatch.setattr(bar, "_tmux", lambda *a: calls.append(a) or SimpleNamespace(stdout="", returncode=0))
    monkeypatch.setattr(bar, "cockpit_session_exists", lambda _s: True)
    monkeypatch.setattr(bar, "layout", lambda _s: 2)
    monkeypatch.setattr(bar, "install_bindings", lambda mouse=True: {})
    monkeypatch.setattr(bar.launcher_state, "COCKPIT_DIR", tmp_path / "not-yet")
    bar.install(SimpleNamespace(bar=True, mouse=True), "cock", config_path=None, profile=None)
    hook = next(c[4] for c in calls if c[0] == "set-hook" and c[3] == "client-resized")
    assert "flock -n" in hook and "command -v flock" in hook
    # the shell run-shell hands it to must parse it: run it with the relayout replaced by a marker
    shell = hook.removeprefix("run-shell -b ").strip('"').replace(bar._self_command() + " layout", "echo relayout")
    out = subprocess.run(["/bin/sh", "-c", shell.replace('\\"', '"')], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0 and "relayout" in out.stdout, out.stderr
    assert (tmp_path / "not-yet").is_dir()  # the lock's folder exists before the first resize
