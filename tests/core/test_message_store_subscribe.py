"""
Tests for GitMessageStore get_inbox_since() and subscribe() — the delta polling
and pull-based subscription additions for T2 of the dispatch bus work.
"""

import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from empirica.core.canonical.empirica_git.message_store import GitMessageStore


@pytest.fixture
def git_repo():
    """Create a temporary git repo and initialize it with a commit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, check=True)
        # Create initial commit
        (Path(tmpdir) / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmpdir, check=True)
        yield tmpdir


@pytest.fixture
def store(git_repo):
    """GitMessageStore bound to a clean git repo."""
    return GitMessageStore(workspace_root=git_repo)


# ---------------------------------------------------------------------------
# get_inbox_since
# ---------------------------------------------------------------------------


class TestGetInboxSince:
    def test_empty_inbox_returns_empty(self, store):
        msgs = store.get_inbox_since(ai_id="alice", since_timestamp=0.0)
        assert msgs == []

    def test_returns_messages_after_timestamp(self, store):
        before = time.time()
        # Brief sleep to ensure the message timestamp is strictly greater
        time.sleep(0.01)

        store.send_message(
            from_ai_id="bob",
            to_ai_id="alice",
            channel="test",
            subject="hello",
            body="world",
        )
        msgs = store.get_inbox_since(ai_id="alice", since_timestamp=before)
        assert len(msgs) == 1
        assert msgs[0]["subject"] == "hello"

    def test_excludes_messages_before_timestamp(self, store):
        store.send_message(
            from_ai_id="bob",
            to_ai_id="alice",
            channel="test",
            subject="old",
            body="old",
        )
        time.sleep(0.1)
        after_old = time.time()

        store.send_message(
            from_ai_id="bob",
            to_ai_id="alice",
            channel="test",
            subject="new",
            body="new",
        )

        msgs = store.get_inbox_since(ai_id="alice", since_timestamp=after_old)
        assert len(msgs) == 1
        assert msgs[0]["subject"] == "new"

    def test_channel_filter(self, store):
        before = time.time()
        time.sleep(0.01)

        store.send_message(
            from_ai_id="bob",
            to_ai_id="alice",
            channel="a",
            subject="msg-a",
            body="x",
        )
        store.send_message(
            from_ai_id="bob",
            to_ai_id="alice",
            channel="b",
            subject="msg-b",
            body="x",
        )

        msgs_a = store.get_inbox_since(ai_id="alice", since_timestamp=before, channel="a")
        assert len(msgs_a) == 1
        assert msgs_a[0]["subject"] == "msg-a"

    def test_limit(self, store):
        before = time.time()
        time.sleep(0.01)

        for i in range(5):
            store.send_message(
                from_ai_id="bob",
                to_ai_id="alice",
                channel="test",
                subject=f"msg-{i}",
                body="x",
            )

        msgs = store.get_inbox_since(ai_id="alice", since_timestamp=before, limit=3)
        assert len(msgs) == 3

    def test_not_addressed_to_us(self, store):
        before = time.time()
        time.sleep(0.01)

        store.send_message(
            from_ai_id="bob",
            to_ai_id="charlie",
            channel="test",
            subject="for-charlie",
            body="x",
        )

        msgs = store.get_inbox_since(ai_id="alice", since_timestamp=before)
        assert len(msgs) == 0

    def test_broadcast_to_us(self, store):
        before = time.time()
        time.sleep(0.01)

        store.send_message(
            from_ai_id="bob",
            to_ai_id="*",
            channel="test",
            subject="broadcast",
            body="x",
        )

        msgs = store.get_inbox_since(ai_id="alice", since_timestamp=before)
        assert len(msgs) == 1
        assert msgs[0]["subject"] == "broadcast"


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_no_callback_returns_early(self, store):
        # Should not block or error
        store.subscribe(ai_id="alice", callback=None, poll_interval=0.1)

    def test_receives_new_messages(self, store):
        received = []
        stop = threading.Event()

        def callback(msg):
            received.append(msg)
            if len(received) >= 2:
                stop.set()

        subscriber = threading.Thread(
            target=store.subscribe,
            kwargs={
                "ai_id": "alice",
                "channel": "test",
                "callback": callback,
                "poll_interval": 0.2,
                "stop_event": stop,
                "mark_read": False,
            },
            daemon=True,
        )
        subscriber.start()
        try:
            # Give subscriber time to start
            time.sleep(0.3)

            store.send_message(
                from_ai_id="bob",
                to_ai_id="alice",
                channel="test",
                subject="first",
                body="x",
            )
            time.sleep(0.3)
            store.send_message(
                from_ai_id="bob",
                to_ai_id="alice",
                channel="test",
                subject="second",
                body="x",
            )

            # Wait for callbacks
            subscriber.join(timeout=3.0)

            assert len(received) >= 1
            subjects = [m.get("subject") for m in received]
            assert "first" in subjects or "second" in subjects
        finally:
            # ALWAYS tear the daemon poller down. `stop` is only set inside the
            # callback once ≥2 messages arrive; if timing delivers fewer, it
            # would never be set and this daemon thread would leak — polling
            # `git for-each-ref refs/notes/empirica/messages/test/` forever and
            # landing a stray subprocess.run call inside a *later* test's mock
            # (the order-dependent CI flake this guards against).
            stop.set()
            subscriber.join(timeout=2.0)

    def test_stop_event_halts_subscription(self, store):
        received = []
        stop = threading.Event()

        def callback(msg):
            received.append(msg)

        subscriber = threading.Thread(
            target=store.subscribe,
            kwargs={
                "ai_id": "alice",
                "channel": "test",
                "callback": callback,
                "poll_interval": 0.1,
                "stop_event": stop,
            },
            daemon=True,
        )
        subscriber.start()
        time.sleep(0.2)
        stop.set()
        subscriber.join(timeout=2.0)

        assert not subscriber.is_alive()

    def test_mark_read_after_callback(self, store):
        received = []
        stop = threading.Event()

        def callback(msg):
            received.append(msg)
            stop.set()

        subscriber = threading.Thread(
            target=store.subscribe,
            kwargs={
                "ai_id": "alice",
                "channel": "test",
                "callback": callback,
                "poll_interval": 0.2,
                "stop_event": stop,
                "mark_read": True,
            },
            daemon=True,
        )
        subscriber.start()
        try:
            # Give subscriber ample time to complete its first poll cycle
            time.sleep(0.5)

            # Now send the message so it arrives after the subscriber's first poll
            store.send_message(
                from_ai_id="bob",
                to_ai_id="alice",
                channel="test",
                subject="first",
                body="x",
            )

            subscriber.join(timeout=5.0)

            # Callback should have fired
            assert len(received) == 1

            # After mark_read, inbox with status='unread' should be empty
            unread = store.get_inbox(ai_id="alice", channel="test", status="unread")
            assert len(unread) == 0
        finally:
            # Always stop the daemon poller (see test_receives_new_messages) —
            # if the callback never fires, `stop` stays unset and the thread
            # leaks a background `for-each-ref …/messages/test/` poll.
            stop.set()
            subscriber.join(timeout=2.0)
