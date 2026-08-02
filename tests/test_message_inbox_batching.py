"""get_inbox reads every note in a constant number of git calls.

#394 problem 1 (FrancisFerrero): get_inbox called load_message per ref, and
load_message spawns TWO subprocesses — `git notes list` to find the annotated
commit, then `git notes show` for content. So answering "what is in my inbox"
cost 2N+1 process spawns, and a 500-message mailbox spent 1001 spawns to return
5 messages.

The fix reads every note in three calls regardless of N: one `for-each-ref` for
the refs and their notes-commit objects, then two `cat-file --batch` passes —
one resolving each ref's tree to its single note blob, one reading those blobs.

**This test asserts the SCALING, not a magic number.** A count taken at one N
proves nothing: the old path and the new one both cost 4 calls at N=1. What
distinguishes them is that the old count grows with N and the new one does not.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.core.canonical.empirica_git.message_store import GitMessageStore


def _git(root, *args, **kw):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False, **kw)


@pytest.fixture
def repo_with_messages(tmp_path):
    """A real git repo carrying N messages, written by the REAL send_message.

    An earlier version of this fixture hand-rolled the envelope JSON and drifted
    from the real shape three times — `from`/`to` are nested objects not strings,
    and timestamps are tz-aware ISO not date strings. Each divergence surfaced as
    an exception inside get_inbox that looked like a batching bug and was not.
    Using the writer that production uses makes the shape correct by construction.
    """

    def _make(n: int):
        root = tmp_path / f"repo{n}"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "t")
        (root / "f").write_text("x")
        _git(root, "add", "f")
        _git(root, "commit", "-q", "-m", "base")

        store = GitMessageStore(str(root))
        for i in range(n):
            store.send_message(
                from_ai_id="peer",
                to_ai_id="empirica",
                channel="direct",
                subject=f"s{i}",
                # A newline inside the payload: cat-file --batch frames by BYTE
                # COUNT, so a body containing newlines must not desynchronise the
                # parser and eat the messages after it.
                body=f"line one\nline two of {i}\n",
            )
        return root

    return _make


def _count_git_calls(store, **kw) -> tuple[int, int]:
    """Return (messages_returned, subprocess_calls)."""
    import empirica.core.canonical.empirica_git.message_store as ms

    calls = []
    real = ms.subprocess.run

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    ms.subprocess.run = counting
    try:
        msgs = store.get_inbox(**kw)
    finally:
        ms.subprocess.run = real
    return len(msgs), len(calls)


def test_call_count_does_not_grow_with_mailbox_size(repo_with_messages):
    """POSITIVE CONTROL — the reproduction, expressed as scaling.

    Old path: 2N+1 → 4 calls at N=1, 21 at N=10, 41 at N=20.
    New path: constant.
    """
    small = GitMessageStore(str(repo_with_messages(2)))
    large = GitMessageStore(str(repo_with_messages(20)))

    n_small, calls_small = _count_git_calls(small, ai_id="empirica", status="all", limit=50)
    n_large, calls_large = _count_git_calls(large, ai_id="empirica", status="all", limit=50)

    assert n_small == 2 and n_large == 20, "both mailboxes must actually be read"
    assert calls_large == calls_small, (
        f"call count grew with mailbox size ({calls_small} -> {calls_large}); the per-message subprocess pair is back"
    )


def test_the_calls_are_a_small_constant(repo_with_messages):
    """Guards against a 'constant' that is constantly huge."""
    store = GitMessageStore(str(repo_with_messages(15)))

    _, calls = _count_git_calls(store, ai_id="empirica", status="all", limit=50)

    assert calls <= 6, f"expected ~4 calls (rev-parse, for-each-ref, 2x cat-file), got {calls}"


def test_message_bodies_containing_newlines_survive(repo_with_messages):
    """`cat-file --batch` frames payloads by byte count, so a body with newlines
    must not desynchronise the parser and corrupt every message after it."""
    store = GitMessageStore(str(repo_with_messages(5)))

    got = store.get_inbox(ai_id="empirica", status="all", limit=50)

    assert len(got) == 5, "a newline in one body must not eat the messages after it"
    for m in got:
        assert m["body"].count("\n") == 2
        assert m["subject"].startswith("s")


def test_newest_first_ordering_survives_batching(repo_with_messages):
    """Regression guard on d05ffc0b7: sorting must still happen AFTER collection.
    Batching changes how messages are gathered, which is exactly where a
    limit-before-sort bug would creep back in."""
    store = GitMessageStore(str(repo_with_messages(20)))

    got = store.get_inbox(ai_id="empirica", status="all", limit=3)

    assert len(got) == 3
    stamps = [m["timestamp"] for m in got]
    assert stamps == sorted(stamps, reverse=True), "limited fetch must return the NEWEST n"


def test_an_empty_mailbox_returns_empty(repo_with_messages):
    """NEGATIVE CONTROL: the batch path must not invent messages or raise."""
    store = GitMessageStore(str(repo_with_messages(0)))

    assert store.get_inbox(ai_id="empirica", status="all", limit=50) == []
