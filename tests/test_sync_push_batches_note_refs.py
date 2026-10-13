"""sync-push replicates a large notes store: wildcard note refspecs go in batches, each with its own timeout.

`_push_note_namespaces` pushed `refs/notes/empirica/*` in ONE `git push` with a 60 s limit. On core's store (23k note refs,
12k already remote) that timed out, the remote did not move, and the verification layer correctly said not_replicating with
11164 missing: the push layer was the defect. forgejo-publish already batches note wildcards at 250 refs per RPC because a
single RPC of thousands of refs 504s at the gateway; sync-push now does the same. Real git repos under tmp_path: the
operation under test IS git.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers import sync_commands


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repos(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    remote.mkdir()
    work.mkdir()
    _git(remote, "init", "--bare", "-q")
    _git(work, "init", "-q")
    _git(work, "commit", "-q", "--allow-empty", "-m", "base")
    head = _git(work, "rev-parse", "HEAD").stdout.strip()
    _git(work, "remote", "add", "forge", str(remote))
    for i in range(23):
        _git(work, "update-ref", f"refs/notes/empirica/session/n{i:03d}", head)
    _git(work, "update-ref", "refs/notes/breadcrumbs", head)
    _git(work, "update-ref", "refs/notes/empirica-precompact", head)
    monkeypatch.chdir(work)
    return work, remote


def _remote_refs(remote):
    out = _git(remote, "for-each-ref", "--format=%(refname)", "refs/notes/").stdout.split()
    return set(out)


def test_a_large_wildcard_is_pushed_in_batches_and_all_of_it_lands(repos, monkeypatch):
    _work, remote = repos
    monkeypatch.setattr(sync_commands, "_NOTES_PUSH_BATCH", 5)
    pushes = []
    real_run = subprocess.run

    def counting(cmd, *a, **k):
        if cmd[:2] == ["git", "push"]:
            pushes.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(sync_commands.subprocess, "run", counting)
    results, errors = sync_commands._push_note_namespaces("forge")
    assert results["empirica/*"] is True and errors == []
    assert len([r for r in _remote_refs(remote) if r.startswith("refs/notes/empirica/")]) == 23
    wildcard_pushes = [p for p in pushes if any("refs/notes/empirica/session" in arg for arg in p)]
    assert len(wildcard_pushes) == 5  # ceil(23 / 5)
    assert all(len(p) - 3 <= 5 for p in wildcard_pushes)


def test_one_batch_timing_out_does_not_stop_the_others_and_is_named(repos, monkeypatch):
    _work, remote = repos
    monkeypatch.setattr(sync_commands, "_NOTES_PUSH_BATCH", 5)
    real_run = subprocess.run
    calls = {"n": 0}

    def flaky(cmd, *a, **k):
        if cmd[:2] == ["git", "push"] and any("refs/notes/empirica/session" in arg for arg in cmd):
            calls["n"] += 1
            if calls["n"] == 2:
                raise subprocess.TimeoutExpired(cmd, k.get("timeout", 0))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(sync_commands.subprocess, "run", flaky)
    results, errors = sync_commands._push_note_namespaces("forge")
    assert results["empirica/*"] is False
    assert any("batch 2 of 5" in e and "timed out" in e for e in errors)
    landed = [r for r in _remote_refs(remote) if r.startswith("refs/notes/empirica/")]
    assert len(landed) == 23 - 5  # the batch that timed out is the only one missing


def test_the_small_namespaces_still_go_in_one_push(repos):
    _work, remote = repos
    results, _errors = sync_commands._push_note_namespaces("forge")
    assert results["breadcrumbs"] is True and "refs/notes/breadcrumbs" in _remote_refs(remote)


def test_a_wildcard_with_no_local_refs_is_not_an_error(tmp_path, monkeypatch):
    remote, work = tmp_path / "r.git", tmp_path / "w"
    remote.mkdir()
    work.mkdir()
    _git(remote, "init", "--bare", "-q")
    _git(work, "init", "-q")
    _git(work, "remote", "add", "forge", str(remote))
    monkeypatch.chdir(work)
    results, _errors = sync_commands._push_note_namespaces("forge")
    assert results["empirica/*"] is True


def test_a_failed_listing_of_local_note_refs_is_an_error_not_an_empty_push(repos, monkeypatch):
    """git for-each-ref failing used to read as 'nothing to push' and the namespace was reported pushed."""
    real_run = subprocess.run

    def broken_listing(cmd, *a, **k):
        if cmd[:2] == ["git", "for-each-ref"]:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: not a git repository")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(sync_commands.subprocess, "run", broken_listing)
    results, errors = sync_commands._push_note_namespaces("forge")
    assert results["empirica/*"] is False
    assert any("could not list local note refs" in e for e in errors)
