"""A profile edited by hand is backed up before provision-practice rewrites it, however many times it was rewritten before.

The rule was "back up once: the first backup is the user's own version". When provision-practice created the file, the first
backup was its own output, and a hand edit made after that was lost on the next rewrite (pre-publish sweep, 1.14.22). The
writer now remembers a digest of what it wrote; a file that differs from it was edited by someone else and is backed up.
"""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from empirica.core.cockpit.launcher.config import add_practice_to_profile, profile_path


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()


def test_a_hand_edit_made_after_our_writes_is_backed_up(tmp_path):
    add_practice_to_profile("dev", "a", str(tmp_path / "a"))  # we create it
    add_practice_to_profile("dev", "b", str(tmp_path / "b"))  # we rewrite it
    target = profile_path("dev")
    edited = target.read_text() + "# my note: keep the monitor small\nmy_key: 1\n"
    target.write_text(edited)

    add_practice_to_profile("dev", "c", str(tmp_path / "c"))

    assert target.with_suffix(".yaml.bak").read_text() == edited


def test_a_file_only_we_wrote_is_not_backed_up_again(tmp_path):
    add_practice_to_profile("dev", "a", str(tmp_path / "a"))
    add_practice_to_profile("dev", "b", str(tmp_path / "b"))

    assert not profile_path("dev").with_suffix(".yaml.bak").exists()


def test_a_file_someone_else_created_is_backed_up_before_the_first_rewrite(tmp_path):
    target = profile_path("dev")
    target.parent.mkdir(parents=True)
    mine = "session_name: mine\nprojects: []\n# hand-written\n"
    target.write_text(mine)

    add_practice_to_profile("dev", "a", str(tmp_path / "a"))

    assert target.with_suffix(".yaml.bak").read_text() == mine


def test_upgrading_from_the_backup_once_writer_keeps_the_user_s_backup(tmp_path):
    """The earlier writer kept the user's first version in .bak and recorded no digest; its own output carries its header."""
    target = profile_path("dev")
    target.parent.mkdir(parents=True)
    users_original = "session_name: mine\n# the user's own\n"
    target.with_suffix(".yaml.bak").write_text(users_original)
    target.write_text(
        "# Cockpit profile 'dev': written by `empirica provision-practice --cockpit-profile dev`.\nsession_name: dev\n"
    )

    add_practice_to_profile("dev", "a", str(tmp_path / "a"))

    assert target.with_suffix(".yaml.bak").read_text() == users_original


def test_a_failed_digest_write_does_not_leave_a_stale_digest(tmp_path):
    """A stale digest would make the next rewrite back up the writer's own output over the user's version."""
    from pathlib import Path as _Path

    add_practice_to_profile("dev", "a", str(tmp_path / "a"))
    target = profile_path("dev")
    hand = target.read_text() + "# hand\n"
    target.write_text(hand)
    add_practice_to_profile("dev", "b", str(tmp_path / "b"))  # hand edit backed up, digest D1 written
    assert target.with_suffix(".yaml.bak").read_text() == hand

    real_write_text = _Path.write_text

    def failing(self, *a, **k):
        if self.name.endswith(".yaml.written"):
            raise OSError("disk full")
        return real_write_text(self, *a, **k)

    with pytest.MonkeyPatch.context() as mp:  # its own patcher: undoing the shared one would also undo the HOME pin
        mp.setattr(_Path, "write_text", failing)
        add_practice_to_profile("dev", "c", str(tmp_path / "c"))  # profile replaced, digest write fails
    add_practice_to_profile("dev", "d", str(tmp_path / "d"))

    assert target.with_suffix(".yaml.bak").read_text() == hand
