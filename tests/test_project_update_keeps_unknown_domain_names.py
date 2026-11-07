"""A domain name this machine does not know is kept, not moved to the tags; only old free text is moved.

User domain files live in ~/.empirica/domains, one machine's, while project.yaml is shared. project-update moved any unknown
value to the tags, so running it on a second machine erased a real domain defined on the first (pre-publish sweep, 1.14.22).
Free text from before the domain was a name looks like `ai/measurement`: that, and only that, is moved.
"""

from __future__ import annotations

import types

import pytest

from empirica.cli.command_handlers import project_update as pu
from empirica.core import project_types as pt


@pytest.mark.parametrize(
    "value, moved",
    [
        ("ai/measurement", True),
        ("ai/coding-agent", True),
        ("client-delivery", False),
        ("legal.eu", False),
        ("research", False),
    ],
)
def test_only_old_free_text_is_taken(value, moved):
    config = types.SimpleNamespace(domain=value, tags=[])

    taken = pu._take_free_text_domain(config, ["research", "default"])

    assert (taken == value) is moved
    assert config.domain == ("" if moved else value)


def test_the_session_note_says_an_unknown_name_may_be_another_machine_s(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    proj = tmp_path / "p"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("type: research\ndomain: client-delivery\n")

    note = pt.classification_note(proj) or ""

    assert "moves it to the tags" not in note
    assert "client-delivery" in note and "machine" in note


def test_the_session_note_still_says_old_free_text_will_move(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    proj = tmp_path / "p"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("type: research\ndomain: ai/measurement\n")

    assert "moves it to the tags" in (pt.classification_note(proj) or "")
