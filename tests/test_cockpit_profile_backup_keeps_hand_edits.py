"""A profile edited by hand is backed up before provision-practice rewrites it, however many times it was rewritten before.

The rule was "back up once: the first backup is the user's own version". When provision-practice created the file, the first
backup was its own output, and a hand edit made after that was lost on the next rewrite (pre-publish sweep, 1.14.22). The
writer now remembers a digest of what it wrote; a file that differs from it was edited by someone else and is backed up.
"""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from empirica.core.cockpit.launcher.config import add_practice_to_profile, profile_path


def _flow(text: str) -> str:
    """The same profile in flow style, which text insertion cannot extend: these tests are about the rewrite's backups."""
    import yaml

    return yaml.safe_dump(yaml.safe_load(text), default_flow_style=True)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()


def test_a_hand_edit_made_after_our_writes_is_backed_up(tmp_path):
    add_practice_to_profile("dev", "a", str(tmp_path / "a"))  # we create it
    add_practice_to_profile("dev", "b", str(tmp_path / "b"))  # we rewrite it
    target = profile_path("dev")
    edited = _flow(target.read_text()) + "# my note: keep the monitor small\n"  # flow style: only a rewrite can add
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
    hand = _flow(target.read_text()) + "# hand\n"
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


# ── a hand-edited profile is appended to, not rewritten (goal 39280461) ────────────────────────────────────────────────

_HAND = """# My cockpit: thirty lines of notes about why the windows are arranged like this.
session_name: monitor-a   # the tmux session
attach_on_launch: true
surface: tmux
alacritty_args: ["--option", "font.size=11"]   # not modelled by the launcher, must survive
projects:
  # core first
  - name: a
    path: /tmp/a
    launch: claude
groups:
  - name: monitor
    panes:
      - command: empirica tui
  - name: a
    panes:
      - project: a
# trailing note
"""


def test_adding_to_a_hand_edited_profile_keeps_its_comments_and_unknown_keys(tmp_path):
    from empirica.core.cockpit.launcher.config import last_profile_write, load_config

    target = profile_path("dev")
    target.parent.mkdir(parents=True)
    target.write_text(_HAND)

    _, changed = add_practice_to_profile("dev", "b", str(tmp_path / "b"))

    text = target.read_text()
    assert changed and text.startswith(_HAND.split("projects:")[0])  # header, comments and alacritty_args untouched
    assert "# core first" in text and "# trailing note" in text and "alacritty_args" in text
    config = load_config(target)
    assert [p.name for p in config.projects] == ["a", "b"] and [g.name for g in config.groups] == ["monitor", "a", "b"]
    assert last_profile_write() == "appended" and not target.with_suffix(".yaml.bak").exists()


def test_a_profile_text_cannot_be_appended_to_is_rewritten_and_says_so(tmp_path):
    from empirica.core.cockpit.launcher.config import last_profile_write

    target = profile_path("dev")
    target.parent.mkdir(parents=True)
    target.write_text("{session_name: x, projects: [{name: a, path: /tmp/a}]}\n")  # flow style: no line to insert after

    add_practice_to_profile("dev", "b", str(tmp_path / "b"))

    assert last_profile_write() == "rewritten" and target.with_suffix(".yaml.bak").exists()
