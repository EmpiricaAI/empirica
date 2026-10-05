"""`delete-artifacts` archives the artifact's git note, for every type.

It passed the verb's SINGULAR item type (`dead_end`) to archive_note, which builds `refs/notes/empirica/<type>/<id>`; notes are
written under the PLURAL namespace (`dead_ends`). The lookup read a ref that never exists, answered `not_present`, and every
delete left its note behind, silently. Found when nine deleted dead-end rows still had notes holding a credential. The
lifecycle tests all called archive_note with the plural, so none of them could see it.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from empirica.cli.command_handlers.graph_commands import _delete_artifact_git_notes
from empirica.core.canonical.empirica_git.note_lifecycle import ACTIVE_PREFIX, ARCHIVE_PREFIX

PAIRS = [
    ("finding", "findings"),
    ("unknown", "unknowns"),
    ("dead_end", "dead_ends"),
    ("mistake", "mistakes"),
    ("assumption", "assumptions"),
    ("decision", "decisions"),
    ("goal", "goals"),
    ("source", "sources"),
]


def _git(repo, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "c")
    return tmp_path


def _ref(repo, ref):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref], capture_output=True
        ).returncode
        == 0
    )


@pytest.mark.parametrize(("item_type", "namespace"), PAIRS)
def test_deleting_an_artifact_moves_its_note_out_of_the_active_namespace(repo, item_type, namespace):
    _git(repo, "notes", f"--ref=empirica/{namespace}/a1", "add", "-f", "-m", "{}", "HEAD")
    assert _ref(repo, f"{ACTIVE_PREFIX}/{namespace}/a1"), "positive control: the note is where the writer puts it"

    assert _delete_artifact_git_notes(item_type, "a1", str(repo)) is True

    assert not _ref(repo, f"{ACTIVE_PREFIX}/{namespace}/a1"), "the active note must be gone"
    assert _ref(repo, f"{ARCHIVE_PREFIX}/{namespace}/a1"), "and the journey must survive in the archive"


def test_an_artifact_with_no_note_is_reported_not_present_not_as_a_success(repo):
    assert _delete_artifact_git_notes("dead_end", "never-written", str(repo)) is False


def test_an_unknown_type_falls_back_to_the_name_it_was_given(repo):
    """Control: the map is a translation, not a filter; a type already in plural form keeps working."""
    _git(repo, "notes", "--ref=empirica/dead_ends/a2", "add", "-f", "-m", "{}", "HEAD")

    assert _delete_artifact_git_notes("dead_ends", "a2", str(repo)) is True
