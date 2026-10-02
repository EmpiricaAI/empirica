"""setup-claude-code's JSON writer keeps the target file's mode.

empirica-nle (prop_tmljhqwuwremrpb7w4evberely): `_write_json_file` wrote a fresh temp file and
renamed it over the target, so the rename carried the temp's umask-default mode and discarded the
target's. A `chmod 600 ~/.claude.json` (the file holds static credentials) reverted to 0664 on the
next `empirica setup-claude-code`. David decided in that session: harden the file AND fix the writer.

Everything is built under tmp_path; nothing here touches the machine's ~/.claude.json.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from empirica.cli.command_handlers.setup_claude_code import ConcurrentlyModified, _write_json_file


@pytest.fixture(autouse=True)
def umask():
    old = os.umask(0o002)  # the permissive default that produced 0664
    yield
    os.umask(old)


def _mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_a_hardened_file_stays_hardened(tmp_path):
    f = tmp_path / "claude.json"
    f.write_text("{}")
    f.chmod(0o600)

    _write_json_file(f, {"a": 1})

    assert _mode(f) == 0o600 and json.loads(f.read_text()) == {"a": 1}


def test_a_group_readable_file_keeps_exactly_its_own_mode(tmp_path):
    """CONTROL: the fix preserves, it does not force 0600 on everything."""
    f = tmp_path / "settings.json"
    f.write_text("{}")
    f.chmod(0o664)

    _write_json_file(f, {"a": 1})

    assert _mode(f) == 0o664


def test_a_new_file_is_created_private(tmp_path):
    f = tmp_path / "fresh.json"

    _write_json_file(f, {"a": 1})

    assert _mode(f) == 0o600 and json.loads(f.read_text()) == {"a": 1}


def test_repeated_writes_do_not_drift_the_mode(tmp_path):
    f = tmp_path / "claude.json"
    f.write_text("{}")
    f.chmod(0o640)

    for i in range(3):
        _write_json_file(f, {"n": i})

    assert _mode(f) == 0o640


def test_no_temp_file_is_left_behind(tmp_path):
    f = tmp_path / "claude.json"
    f.write_text("{}")

    _write_json_file(f, {"a": 1})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["claude.json"]


def test_the_concurrent_modification_refusal_is_unchanged(tmp_path):
    f = tmp_path / "claude.json"
    f.write_text("{}")
    stale = (0, 0)
    before = _mode(f)

    with pytest.raises(ConcurrentlyModified):
        _write_json_file(f, {"a": 1}, expect_stamp=stale)

    assert json.loads(f.read_text()) == {}, "refused means nothing was written"
    assert _mode(f) == before, "and the mode is exactly what it was"


# ── hardening from the 1.14.5 broccoli sweep ────────────────────────────────


def test_a_symlinked_target_is_written_through_not_replaced(tmp_path):
    """A dotfile manager keeps ~/.claude.json as a link; renaming over it replaced the link with a regular
    file and left the real file stale."""
    real = tmp_path / "dotfiles" / "claude.json"
    real.parent.mkdir()
    real.write_text("{}")
    real.chmod(0o600)
    link = tmp_path / "claude.json"
    link.symlink_to(real)

    _write_json_file(link, {"a": 1})

    assert link.is_symlink(), "the link survives"
    assert json.loads(real.read_text()) == {"a": 1} and _mode(real) == 0o600


def test_a_stale_temp_file_from_a_dead_process_does_not_break_the_write(tmp_path):
    """The temp name was per-pid and opened O_EXCL; a leftover from a killed process with a reused pid raised."""
    f = tmp_path / "claude.json"
    f.write_text("{}")
    (tmp_path / f"claude.json.{os.getpid()}.tmp").write_text("junk")

    _write_json_file(f, {"a": 1})

    assert json.loads(f.read_text()) == {"a": 1}


def test_a_filesystem_that_rejects_chmod_still_gets_the_write(tmp_path, monkeypatch):
    """vfat and some FUSE mounts refuse chmod; the old writer succeeded there and the new one must too."""
    f = tmp_path / "claude.json"
    f.write_text("{}")
    real_chmod = os.chmod

    def picky(path, mode, *a, **k):
        if str(path).endswith(".tmp"):
            raise PermissionError("operation not permitted")
        return real_chmod(path, mode, *a, **k)

    monkeypatch.setattr(os, "chmod", picky)

    _write_json_file(f, {"a": 1})

    assert json.loads(f.read_text()) == {"a": 1}
