"""A TTL that expires nothing is a setting, not a policy (#394, "Adjacent").

The default ttl is 86400, but nothing enforced it unless someone scheduled
`message-cleanup`. FrancisFerrero's repo had 306 refs of which 150 were already
expired. A fix that depends on a scheduled loop does not run for anyone who
didn't read the docs — and cron is opt-in-only in this project — so enforcement
hangs off the send instead, interval-gated so a burst costs one prune.

Also covers the sibling defect the original #394 fix missed: `cleanup_expired`
still read one message per ref with two subprocess spawns each, and deleted with
one spawn per expired message — on the exact path where the expired count is
largest.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from empirica.core.canonical.empirica_git.message_store import GitMessageStore


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=15
    ).stdout.strip()


@pytest.fixture
def store(tmp_path: Path) -> GitMessageStore:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return GitMessageStore(workspace_root=str(repo))


def _ref_count(store: GitMessageStore) -> int:
    out = _git(Path(store.workspace_root), "for-each-ref", "refs/notes/empirica/messages/", "--format=%(refname)")
    return len([line for line in out.splitlines() if line.strip()])


def _send(store: GitMessageStore, subject: str, ttl: int) -> str | None:
    return store.send_message(from_ai_id="a", to_ai_id="b", channel="direct", subject=subject, body="body", ttl=ttl)


def test_expired_messages_are_pruned_without_anyone_scheduling_cleanup(store):
    """The whole point: no cron, no scheduled loop, no operator memory."""
    assert _send(store, "expired", ttl=1)
    time.sleep(1.1)

    # Backdate the marker so the interval gate opens (a fresh repo has none, but
    # the first send may have created one).
    marker = Path(store.workspace_root) / ".git" / "empirica-last-message-prune"
    if marker.exists():
        os.utime(marker, (0, 0))

    assert _ref_count(store) == 1
    assert _send(store, "the trigger", ttl=86400)

    assert _ref_count(store) == 1, "the expired one should be gone, the fresh one kept"
    inbox = store.get_inbox(ai_id="b", status="all")
    assert [m["subject"] for m in inbox] == ["the trigger"]


def test_a_live_message_is_never_pruned(store):
    assert _send(store, "alive", ttl=86400)
    marker = Path(store.workspace_root) / ".git" / "empirica-last-message-prune"
    if marker.exists():
        os.utime(marker, (0, 0))
    assert _send(store, "trigger", ttl=86400)

    assert _ref_count(store) == 2


def test_the_interval_gate_stops_a_send_burst_pruning_every_time(store, monkeypatch):
    """NEGATIVE CONTROL for putting an O(total) scan on the write path.

    Without the gate, sending N messages runs N full ref scans — reintroducing
    the cost #394 was filed about, on the other side of the store.
    """
    calls = []
    monkeypatch.setattr(GitMessageStore, "cleanup_expired", lambda self, **kw: calls.append(1) or [])

    marker = Path(store.workspace_root) / ".git" / "empirica-last-message-prune"
    if marker.exists():
        marker.unlink()

    for i in range(8):
        _send(store, f"m{i}", ttl=86400)

    assert len(calls) == 1, f"one prune per interval, not per send (got {len(calls)})"


def test_the_marker_is_stamped_even_when_the_prune_raises(store, monkeypatch):
    """A prune that dies must not become a permanent tax on every later send.

    Stamping only on success would make a broken prune retry on every write —
    turning one failure into a sustained cost on the hot path.
    """

    def _boom(self, **kwargs):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(GitMessageStore, "cleanup_expired", _boom)
    marker = Path(store.workspace_root) / ".git" / "empirica-last-message-prune"
    if marker.exists():
        marker.unlink()

    assert _send(store, "still sends", ttl=86400), "a prune failure must not fail the send"
    assert marker.exists(), "marker stamped before the prune, so the failure is not retried forever"


def test_cleanup_batches_reads_and_deletes(store, monkeypatch):
    """NEGATIVE CONTROL for the sibling of #394 problem 1.

    cleanup_expired used load_message per ref (2 spawns each) and one
    `update-ref -d` per expired message. Counts subprocess invocations rather
    than asserting a duration, so it cannot pass by running on a fast machine.
    """
    for i in range(6):
        _send(store, f"old{i}", ttl=1)
    time.sleep(1.1)

    real_run = subprocess.run
    seen: list[list[str]] = []

    def _counting_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            seen.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)
    removed = store.cleanup_expired()

    assert len(removed) == 6
    assert _ref_count(store) == 0

    assert not any("notes" in c and "show" in c for c in seen), "must not read notes one at a time"
    deletes = [c for c in seen if "update-ref" in c]
    assert len(deletes) == 1, f"one batched delete, not one per message (got {len(deletes)})"
    assert "--stdin" in deletes[0]


def test_dry_run_removes_nothing(store):
    for i in range(3):
        _send(store, f"old{i}", ttl=1)
    time.sleep(1.1)

    removed = store.cleanup_expired(dry_run=True)

    assert len(removed) == 3
    assert _ref_count(store) == 3, "dry-run must report without deleting"
