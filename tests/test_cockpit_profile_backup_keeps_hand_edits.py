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
