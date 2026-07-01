"""detect_environment() — Sentinel remote/trust classification.

Regression focus: a LOCAL machine must not read as an untrusted remote when the
SSH_* env vars are stale (inherited by a tmux/screen server whose SSH login has
since logged out). A local Mac was tripping `REMOTE:SSH:UNTRUSTED` this way
(autonomy bug report). The discriminator is SSH_TTY device-existence: a live
session's pty exists; a stale-inherited one is gone.
"""

from __future__ import annotations

import pytest

from empirica.utils.session_resolver import _ssh_env_is_stale, detect_environment

_SSH_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")


@pytest.fixture(autouse=True)
def _clear_ssh_env(monkeypatch):
    for v in _SSH_VARS:
        monkeypatch.delenv(v, raising=False)
    # Don't let a real ~/.empirica/trusted_hosts leak into trust assertions.
    monkeypatch.setenv("CI", "")  # ensure is_ci stays false unless a test sets it
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)


# ── _ssh_env_is_stale ─────────────────────────────────────────────────────────


def test_stale_false_when_no_ssh_tty(monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5555 6.7.8.9 22")
    # SSH_CONNECTION without SSH_TTY: non-interactive remote, not classified stale.
    assert _ssh_env_is_stale() is False


def test_stale_true_when_ssh_tty_device_missing(monkeypatch):
    monkeypatch.setenv("SSH_TTY", "/dev/pts/nonexistent-99999")
    assert _ssh_env_is_stale() is True


def test_stale_false_when_ssh_tty_device_exists(monkeypatch, tmp_path):
    live = tmp_path / "ptylike"
    live.write_text("")  # a path that exists stands in for a live pty device
    monkeypatch.setenv("SSH_TTY", str(live))
    assert _ssh_env_is_stale() is False


# ── detect_environment: is_remote ─────────────────────────────────────────────


def test_local_no_ssh_is_not_remote():
    assert detect_environment()["is_remote"] is False


def test_stale_ssh_tty_reads_local_not_remote(monkeypatch):
    # THE bug: tmux-inherited SSH_* on a now-local machine (pty gone).
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5555 6.7.8.9 22")
    monkeypatch.setenv("SSH_TTY", "/dev/pts/gone-12345")
    env = detect_environment()
    assert env["is_remote"] is False
    # …and therefore no untrusted-remote trust verdict is computed.
    assert env["is_trusted"] is None


def test_live_ssh_tty_is_remote(monkeypatch, tmp_path):
    live = tmp_path / "livepty"
    live.write_text("")
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5555 6.7.8.9 22")
    monkeypatch.setenv("SSH_TTY", str(live))
    assert detect_environment()["is_remote"] is True


def test_noninteractive_ssh_connection_still_remote(monkeypatch):
    # SSH_CONNECTION with no SSH_TTY (scp / forced command) is left as remote.
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5555 6.7.8.9 22")
    assert detect_environment()["is_remote"] is True


# ── trust verdict still works for a genuine remote ────────────────────────────


def test_live_remote_untrusted_without_trusted_hosts(monkeypatch, tmp_path):
    live = tmp_path / "pty"
    live.write_text("")
    monkeypatch.setenv("SSH_TTY", str(live))
    monkeypatch.setattr("empirica.utils.session_resolver.Path.home", staticmethod(lambda: tmp_path))
    env = detect_environment()
    assert env["is_remote"] is True
    assert env["is_trusted"] is False  # no trusted_hosts file → untrusted (unchanged)
