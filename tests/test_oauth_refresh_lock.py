"""The OAuth refresh lock serialises refreshers, and degrades, never raises, when it cannot lock.

Cortex rotates the refresh token on every use, so two processes refreshing at once revoke the family.
The lock's docstring promises that a lock that cannot be taken lets the refresh run unserialized with a
warning. It kept that promise for a lock file that would not open, and broke it for `flock` itself
failing (ENOLCK / ENOTSUP on NFS and FUSE homes), which propagated out of the token refresh.
"""

from __future__ import annotations

import errno
import fcntl
import logging

import pytest

from empirica.config.credentials_loader import _oauth_refresh_lock

LOCK_NAME = ".cortex_oauth_refresh.lock"


def test_the_lock_excludes_a_second_holder_while_the_body_runs(tmp_path):
    """POSITIVE CONTROL: the lock does something, so the degradation test below means something."""
    with _oauth_refresh_lock(tmp_path):
        other = open(tmp_path / LOCK_NAME, "a")  # noqa: SIM115
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            other.close()
    again = open(tmp_path / LOCK_NAME, "a")  # noqa: SIM115
    try:
        fcntl.flock(again.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # released on exit: does not raise
    finally:
        again.close()


def test_a_flock_that_fails_runs_the_body_unserialized_with_a_warning(tmp_path, monkeypatch, caplog):
    def deny(fd, op):
        if op & fcntl.LOCK_EX:
            raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", deny)
    ran = []

    with caplog.at_level(logging.WARNING), _oauth_refresh_lock(tmp_path):
        ran.append(True)

    assert ran == [True]
    assert any(
        "refreshing unserialized" in r.getMessage() and "No locks available" in r.getMessage() for r in caplog.records
    )


def test_an_unopenable_lock_directory_still_degrades(tmp_path, monkeypatch, caplog):
    """The case that already worked, kept beside the new one so the two stay the same shape."""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    ran = []

    with caplog.at_level(logging.WARNING), _oauth_refresh_lock(blocker / "sub"):
        ran.append(True)

    assert ran == [True] and any("refreshing unserialized" in r.getMessage() for r in caplog.records)
