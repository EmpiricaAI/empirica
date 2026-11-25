"""profile-sync --push refuses a public host like sync-push does (found verifying public sharing copy, 2026-11-25).

sync-push refuses to publish epistemic notes (findings, mistakes, mesh messages) to GitHub, GitLab, Bitbucket, Codeberg or
a remote declared public unless --force; profile-sync --push pushed the same notes with no check.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import profile_commands as pc
from empirica.cli.command_handlers import sync_commands as sc


@pytest.fixture
def pipeline(monkeypatch):
    pushed = []
    monkeypatch.setattr(
        pc, "_push_notes", lambda remote: pushed.append(remote) or {"ok": True, "pushed": {}, "errors": []}
    )
    monkeypatch.setattr(pc, "_import_notes_to_sqlite", lambda: {"ok": True})
    monkeypatch.setattr(pc, "_fetch_notes", lambda remote: {"ok": True})
    return pushed


def test_the_detection_names_public_hosts_and_passes_private_ones(monkeypatch):
    monkeypatch.setattr(sc, "_get_remote_url", lambda r: "git@github.com:me/x.git")
    monkeypatch.setattr(sc, "_load_sync_config", lambda: {})
    assert sc.public_notes_remote("origin")
    monkeypatch.setattr(sc, "_get_remote_url", lambda r: "https://git.example.org/me/x.git")
    assert sc.public_notes_remote("origin") is None


@pytest.mark.parametrize("force, pushes", [(False, False), (True, True)])
def test_profile_sync_push_is_refused_on_a_public_host_unless_forced(pipeline, monkeypatch, capsys, force, pushes):
    monkeypatch.setattr(sc, "public_notes_remote", lambda remote: "github.com")
    args = SimpleNamespace(push=True, qdrant=False, import_only=False, force=force, remote="origin", output="json")
    pc.handle_profile_sync_command(args)
    assert bool(pipeline) is pushes
    if not pushes:
        assert "public" in capsys.readouterr().out
