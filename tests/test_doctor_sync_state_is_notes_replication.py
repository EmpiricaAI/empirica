"""doctor's 'sync state' says what is true about the notes remote, not about the git working tree.

check_sync_state counted `git status --porcelain` lines and hinted "Call empirica sync-push to propagate to Cortex".
Notes are replicated to a git NOTES remote (sync-config notes_remote), not to Cortex, and "N uncommitted changes" reads
as the working tree: an NLE reader took it that way and decided on it. sync-status already computes the replication
verdict; the doctor reports that, with the remote-must-be-configured hint first.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import doctor, sync_commands

PASS, WARN, SKIP = doctor.PASS, doctor.WARN, doctor.SKIP


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


def _sync(monkeypatch, *, remote, configured=True, replication=None):
    monkeypatch.setattr(sync_commands, "_load_sync_config", lambda: {})
    monkeypatch.setattr(sync_commands, "_resolve_remote", lambda kind, config, explicit=None: remote)
    monkeypatch.setattr(sync_commands, "_check_remote", lambda name: configured)
    monkeypatch.setattr(
        sync_commands,
        "_replication_block",
        lambda r, c, local_only: {
            "replication": replication or {"state": "unknown", "reason": "not computed", "behind": None}
        },
    )


def test_a_dirty_working_tree_is_no_longer_reported_as_sync_state(repo, monkeypatch):
    _sync(
        monkeypatch,
        remote="forge",
        replication={"state": "replicated", "reason": "all 40 local note refs are on the remote", "behind": 0},
    )
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **kw: (0, " M a.py\n?? b.py\n", "")
    )  # would have been '2 uncommitted'
    check = doctor.check_sync_state(repo)
    assert check.status == PASS
    assert "uncommitted" not in check.detail and "all 40 local note refs" in check.detail


def test_no_notes_remote_says_so_and_names_the_command_that_configures_it(repo, monkeypatch):
    _sync(monkeypatch, remote=None)
    check = doctor.check_sync_state(repo)
    assert check.status == WARN
    assert "no notes remote" in check.detail.lower() and "Cortex" not in (check.hint or "")
    assert "sync-config notes_remote" in (check.hint or "")


def test_a_configured_name_that_is_not_a_git_remote_is_named(repo, monkeypatch):
    _sync(monkeypatch, remote="forge", configured=False)
    check = doctor.check_sync_state(repo)
    assert check.status == WARN and "'forge'" in check.detail and "not a git remote" in check.detail


def test_notes_behind_the_remote_warn_with_the_count_and_the_push_hint(repo, monkeypatch):
    _sync(
        monkeypatch,
        remote="forge",
        replication={"state": "behind", "reason": "12 of 40 local note refs (30%) are NOT on the remote", "behind": 12},
    )
    check = doctor.check_sync_state(repo)
    assert check.status == WARN and "12 of 40" in check.detail
    assert "sync-push" in (check.hint or "") and "Cortex" not in (check.hint or "")


def test_an_unreachable_remote_is_a_warning_not_a_pass(repo, monkeypatch):
    _sync(
        monkeypatch,
        remote="forge",
        replication={"state": "unknown", "reason": "remote unreachable: timeout", "behind": None},
    )
    assert doctor.check_sync_state(repo).status == WARN


def test_not_a_git_repo_still_skips(tmp_path):
    assert doctor.check_sync_state(tmp_path).status == SKIP
