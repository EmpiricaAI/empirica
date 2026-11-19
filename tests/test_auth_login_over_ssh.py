"""auth login picks the remote flow by itself over SSH (mesh-support prop_wrg7as6d, goal 2c4f4f97).

Run by hand over ssh, `auth login` offered a localhost callback the person's browser, on another machine, cannot reach.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import auth_commands as ac


@pytest.fixture
def clean_env(monkeypatch):
    for v in ("SSH_CONNECTION", "SSH_TTY", "TMUX", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(ac.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    return monkeypatch


def test_a_desktop_session_stays_local(clean_env):
    assert ac._remote_reason() is None


@pytest.mark.parametrize("var", ["SSH_CONNECTION", "SSH_TTY"])
def test_an_ssh_session_is_remote(clean_env, var):
    clean_env.setenv(var, "10.0.0.2 51000 10.0.0.1 22")
    assert ac._remote_reason() is not None


def test_linux_without_a_display_is_remote(clean_env):
    clean_env.delenv("DISPLAY")
    assert ac._remote_reason() is not None


def test_macos_without_display_variables_stays_local(clean_env):
    clean_env.delenv("DISPLAY")
    clean_env.setattr(ac.sys, "platform", "darwin")
    assert ac._remote_reason() is None


def test_inside_tmux_the_session_environment_is_read(clean_env):
    """A tmux server started locally and attached over ssh: the pane has no SSH_*, tmux's session environment does."""
    clean_env.setenv("TMUX", "/tmp/tmux-1/default,1,0")
    clean_env.setattr(
        ac.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="SSH_CONNECTION=1 2 3 22\n")
    )
    assert ac._remote_reason() is not None
    clean_env.setattr(
        ac.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="-SSH_CONNECTION\n")
    )
    assert ac._remote_reason() is None


def test_local_overrides_and_the_choice_is_said(clean_env, capsys):
    clean_env.setenv("SSH_TTY", "/dev/pts/3")
    seen = {}
    clean_env.setattr(
        "empirica.core.auth.login", lambda **k: seen.update(k) or (_ for _ in ()).throw(RuntimeError("stop"))
    )
    ac.handle_auth_login_command(SimpleNamespace(output="human", remote=False, local=False))
    assert seen["remote"] is True and "SSH" in capsys.readouterr().err
    ac.handle_auth_login_command(SimpleNamespace(output="human", remote=False, local=True))
    assert seen["remote"] is False


def test_the_line_names_the_real_reason(clean_env, monkeypatch, capsys):
    """Linux with no display is not SSH, and nothing has signed in yet when the line prints (1.17.11 sweep)."""
    clean_env.delenv("DISPLAY")
    assert ac._choose_remote(type("A", (), {"remote": False, "local": False})()) is True
    err = capsys.readouterr().err
    assert "no display" in err and "SSH" not in err and "signed in" not in err


def test_a_closed_stdin_points_at_local(monkeypatch):
    from empirica.core.auth import cortex_oauth

    def eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(RuntimeError, match="--local"):
        cortex_oauth._read_pasted_code("https://cortex.example/authorize")
