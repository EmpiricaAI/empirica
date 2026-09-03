"""sync-status was the only sync verb that ignored the configured remote.

It took its remote from args with a literal default and never read `sync_config`, so
on any seat whose remote was not named `origin` it reported on a remote nothing else
used. A practitioner set the remote correctly, ran `sync-status`, was told the remote
was unconfigured, and concluded the write had failed. It had not.

**The status verb is what someone consults to decide whether a thing is working.**
When it is the one that is wrong it does not merely fail to inform — it sends the
reader to fix something that was not broken, or to trust something that is.

The companion property (no verb may guess a remote at all) lives in
`test_sync_remotes_refuse_dont_guess.py`. This file is about AGREEMENT: status must
report the destination the other verbs would actually use.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import sync_commands
from empirica.core.sync_remotes import CODE, NOTES, resolve


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A repo whose remote is deliberately NOT named `origin` — the whole condition."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    for args in (
        ["config", "user.email", "t@e.com"],
        ["config", "user.name", "t"],
        ["remote", "add", "forgejo", str(tmp_path / "fake.git")],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "config.yaml").write_text('version: "2.0"\nsync:\n  notes_remote: forgejo\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sync_commands, "_get_workspace_root", lambda: str(tmp_path))
    return tmp_path


def _status(capsys, **kw) -> dict:
    args = SimpleNamespace(remote=None, output="json", verbose=False, **kw)
    assert sync_commands.handle_sync_status_command(args) == 0
    return json.loads(capsys.readouterr().out)


def test_status_reports_the_configured_remote(repo, capsys):
    """THE regression. Before the fix this reported a remote that does not exist in
    this repo, and therefore `remote_configured: false`."""
    result = _status(capsys)

    assert result["remote"] == "forgejo"
    assert result["remote_configured"] is True
    assert result["sync_available"] is True


def test_status_matches_what_push_would_use(repo):
    """The property that matters is AGREEMENT, not the literal string. A status verb
    reporting on a different remote than the one push uses is worse than one that
    reports nothing, because it answers the question it was asked and answers it
    wrongly."""
    cfg = sync_commands._load_sync_config()

    assert resolve(NOTES, cfg) == "forgejo"


def test_an_explicit_flag_still_wins(repo, capsys):
    """NEGATIVE CONTROL. Reading config must not swallow an explicit `--remote`."""
    result = _status(capsys, **{})  # baseline
    assert result["remote"] == "forgejo"

    args = SimpleNamespace(remote="upstream", output="json", verbose=False)
    assert sync_commands.handle_sync_status_command(args) == 0
    assert json.loads(capsys.readouterr().out)["remote"] == "upstream"


def test_unset_and_misconfigured_are_different_facts(tmp_path, monkeypatch, capsys):
    """These used to render identically, and their fixes are opposite.

    `remote: null` is a destination nobody chose — the fix is to choose one.
    `remote: <name>, remote_configured: false` is a destination that no longer matches
    the repo — the fix is to add the git remote or point at a different one. Collapsing
    both into "not configured" is what sent a practitioner to re-run a write that had
    already succeeded.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "config.yaml").write_text('version: "2.0"\nsync: {}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sync_commands, "_get_workspace_root", lambda: str(tmp_path))

    unset = _status(capsys)
    assert unset["remote"] is None
    assert unset["sync_available"] is False
    assert "sync-config" in unset["hint"], "an unset destination must carry the fix"

    (tmp_path / ".empirica" / "config.yaml").write_text('version: "2.0"\nsync:\n  notes_remote: nosuch\n')
    misconfigured = _status(capsys)
    assert misconfigured["remote"] == "nosuch"
    assert misconfigured["remote_configured"] is False


def test_code_is_reported_and_never_folded_into_sync_available(repo, capsys):
    """`sync-push` moves notes only, so a configured code remote says nothing about
    whether notes reach anywhere — and vice versa. They were one line once, which is
    how ~765 commits lived on a laptop while the config looked healthy."""
    result = _status(capsys)

    assert result["code_remote"] is None, "notes_remote must not leak into code"
    assert result["sync_available"] is True, "and an unset code remote must not mark notes unavailable"
    assert result["code_auto_push_on"] == []


def test_code_remote_is_reported_when_set(tmp_path, monkeypatch, capsys):
    """POSITIVE CONTROL on the code line — a status that reported `code_remote: null`
    unconditionally would satisfy the test above while telling a seat with auto-push
    ARMED nothing at all."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "config.yaml").write_text(
        'version: "2.0"\nsync:\n  code_remote: gh\n  auto_push_on: [postflight]\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sync_commands, "_get_workspace_root", lambda: str(tmp_path))

    result = _status(capsys)

    assert result["code_remote"] == "gh"
    assert result["code_auto_push_on"] == ["postflight"]
    assert resolve(CODE, sync_commands._load_sync_config()) == "gh"
