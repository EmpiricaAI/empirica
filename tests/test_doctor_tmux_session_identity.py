"""doctor names a tmux session whose environment hands out a practitioner identity.

cockpit-a carried EMPIRICA_INSTANCE_ID=empirica in its session environment (new-session -e is
session-scoped), and a claude started by hand there ran as `empirica` inside empirica-nle.
Real tmux on a private socket: the check shells out to `tmux`, and $TMUX outranks TMUX_TMPDIR, so
teardown uses an explicit -S path and can never reach a live server.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from empirica.cli.command_handlers.doctor import PASS, SKIP, WARN, check_tmux_session_identity


@pytest.fixture
def private_tmux(tmp_path, monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    sock_dir = tmp_path / "s"
    sock_dir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(sock_dir))
    monkeypatch.delenv("TMUX", raising=False)
    private = sock_dir / f"tmux-{os.getuid()}" / "default"
    yield private
    subprocess.run(["tmux", "-S", str(private), "kill-server"], capture_output=True)


def _session(name: str, *env: str) -> None:
    args = ["tmux", "new-session", "-d", "-s", name]
    for e in env:
        args += ["-e", e]
    subprocess.run([*args, "sleep 60"], check=True)


def test_a_session_carrying_an_id_is_named(private_tmux):
    """POSITIVE CONTROL for the whole file: the leaking shape is reported."""
    _session("cockpit-x", "EMPIRICA_INSTANCE_ID=empirica")
    _session("clean")

    c = check_tmux_session_identity()

    assert c.status == WARN
    assert c.data["sessions"] == {"cockpit-x": "empirica"}
    assert "2 tmux session(s) read" in c.detail and "cockpit-x=empirica" in c.detail
    assert "set-environment -t <session> -r EMPIRICA_INSTANCE_ID" in c.hint


def test_clean_sessions_pass_and_say_how_many_were_read(private_tmux):
    _session("a")
    _session("b", "SOMETHING_ELSE=1")

    c = check_tmux_session_identity()

    assert c.status == PASS and "2 tmux session(s) read" in c.detail


def test_a_removed_variable_is_not_reported(private_tmux):
    """After `set-environment -r` tmux lists the variable as removed; that is the healed state."""
    _session("healed", "EMPIRICA_INSTANCE_ID=old")
    subprocess.run(["tmux", "set-environment", "-t", "healed", "-r", "EMPIRICA_INSTANCE_ID"], check=True)

    assert check_tmux_session_identity().status == PASS


def test_no_server_is_a_skip_not_a_pass(private_tmux):
    assert check_tmux_session_identity().status == SKIP
