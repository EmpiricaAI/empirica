"""Two reporter-found defects, both of the session's dominant class.

**#390 (FrancisFerrero)** — `unknown-resolve` printed "Unknown resolved successfully"
for UUIDs that do not exist. The repository ran its UPDATE, ignored the rowcount and
returned None, so a typo'd id was indistinguishable from a real close. Reproduced
verbatim before fixing.

**#389 (FrancisFerrero)** — `message-send`/`message-reply` defaulted the sender to the
literal `"claude-code"`, and no `EMPIRICA_MESH_*` variable was consulted anywhere in
the package. Every practice's messages were attributed to the same name and the
documented override did nothing.

Both are the same shape as everything else found this session: an operation whose
output cannot distinguish success from non-event.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid

import pytest

from empirica.cli.command_handlers.message_commands import _default_sender
from empirica.data.repositories.breadcrumbs import BreadcrumbRepository

SESSION = str(uuid.uuid4())


# ── #390: resolving nothing is not resolving ──────────────────────────


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY, session_id TEXT, unknown TEXT,
            is_resolved BOOLEAN DEFAULT 0, resolved_by TEXT,
            resolved_timestamp REAL, resolution_finding_id TEXT,
            created_timestamp REAL
        )
        """
    )
    conn.commit()
    r = BreadcrumbRepository(conn)
    r._persist_resolution_to_git_notes = lambda *a, **k: None  # no git in a unit test
    return r


def _insert(repo) -> str:
    uid = str(uuid.uuid4())
    repo.conn.execute(
        "INSERT INTO project_unknowns (id, session_id, unknown, is_resolved, created_timestamp) "
        "VALUES (?, ?, 'q', 0, ?)",
        (uid, SESSION, time.time()),
    )
    repo.conn.commit()
    return uid


def test_a_nonexistent_uuid_does_not_report_a_resolution(repo):
    """POSITIVE CONTROL — the exact reproduction from #390."""
    assert repo.resolve_unknown(str(uuid.uuid4()), "probe") is False


def test_a_nonexistent_partial_id_does_not_report_a_resolution(repo):
    """The partial-id branch takes a different SQL path and needs its own control."""
    assert repo.resolve_unknown("deadbeef", "probe") is False


def test_a_real_unknown_still_resolves(repo):
    """NEGATIVE CONTROL. Without this, returning False unconditionally would pass
    both tests above while breaking the verb entirely."""
    uid = _insert(repo)

    assert repo.resolve_unknown(uid, "answered") is True
    row = repo.conn.execute("SELECT is_resolved, resolved_by FROM project_unknowns WHERE id = ?", (uid,)).fetchone()
    assert row["is_resolved"] == 1
    assert row["resolved_by"] == "answered"


def test_a_real_partial_id_still_resolves(repo):
    uid = _insert(repo)

    assert repo.resolve_unknown(uid[:8], "answered by prefix") is True


def test_a_failed_resolve_leaves_every_row_untouched(repo):
    """The bug's real cost was not the message — it was that a caller could not tell.
    Confirm nothing moved, so a wrong id cannot silently alter state."""
    uid = _insert(repo)

    repo.resolve_unknown(str(uuid.uuid4()), "probe")

    row = repo.conn.execute("SELECT is_resolved FROM project_unknowns WHERE id = ?", (uid,)).fetchone()
    assert row["is_resolved"] == 0


# ── #389: the sender is resolved, not hardcoded ───────────────────────


def test_the_env_override_is_honoured(monkeypatch):
    """POSITIVE CONTROL — the documented variable that did nothing."""
    monkeypatch.setenv("EMPIRICA_MESH_AI_ID", "philipp-code")

    assert _default_sender() == "philipp-code"


def test_a_blank_env_override_falls_through(monkeypatch, tmp_path):
    """An empty variable is not an identity. Without this, `export EMPIRICA_MESH_AI_ID=`
    would attribute messages to the empty string."""
    monkeypatch.setenv("EMPIRICA_MESH_AI_ID", "   ")
    monkeypatch.chdir(tmp_path)

    assert _default_sender() == "claude-code"


def test_the_practice_ai_id_is_used_when_no_env_is_set(monkeypatch, tmp_path):
    monkeypatch.delenv("EMPIRICA_MESH_AI_ID", raising=False)
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("ai_id: empirica-outreach\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _default_sender() == "empirica-outreach"


def test_the_historical_default_survives_outside_a_project(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: a project-less invocation must still send rather than fail.
    Dropping the fallback would turn a cosmetic attribution bug into a hard error."""
    monkeypatch.delenv("EMPIRICA_MESH_AI_ID", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _default_sender() == "claude-code"


def test_no_caller_hardcodes_the_sender_any_more():
    """Source guard: the literal must not reappear at the call sites."""
    from pathlib import Path

    import empirica.cli.command_handlers.message_commands as mc

    src = Path(mc.__file__).read_text(encoding="utf-8")

    assert 'or "claude-code"' not in src, "a call site is hardcoding the sender again"
    assert os.path.basename(mc.__file__) == "message_commands.py"


# ── #391: message-read never read ─────────────────────────────────────
#
# Reported as "returns empty from/subject/body". The fields were never in the
# projection at all — the verb called mark_read and returned the receipt, doing the
# write half of "read" and none of the read half, while the content sat intact in the
# git note. Hence the `git notes show` workaround: the data was never missing, only
# unreachable through the verb named after fetching it.


class _FakeStore:
    """Stands in for GitMessageStore. Key names mirror what send_message WRITES —
    `from`, `to`, `timestamp` — which is the detail that matters here."""

    def __init__(self, message=None, mark_ok=True):
        self._message = message
        self._mark_ok = mark_ok
        self.marked = False

    def load_message(self, channel, message_id):
        return self._message

    def mark_read(self, channel, message_id, ai_id, machine=None):
        self.marked = True
        return self._mark_ok


_STORED = {
    "from": "empirica-cortex",
    "to": "empirica",
    "subject": "Boundary cleanup",
    "body": "The full body text.",
    "timestamp": 1785500000.0,
    "thread_id": "t-1",
    "priority": "normal",
}


def _read(monkeypatch, store, capsys):
    import types

    from empirica.cli.command_handlers import message_commands as mc

    monkeypatch.setattr(mc, "_get_store", lambda: store)
    args = types.SimpleNamespace(channel="direct", message_id="m-1", ai_id="empirica", output="json")
    mc.handle_message_read_command(args)
    import json as _json

    return _json.loads(capsys.readouterr().out)


def test_message_read_returns_the_content(monkeypatch, capsys):
    """POSITIVE CONTROL — the fields the reporter could not get."""
    body = _read(monkeypatch, _FakeStore(_STORED), capsys)

    assert body["from"] == "empirica-cortex"
    assert body["subject"] == "Boundary cleanup"
    assert body["body"] == "The full body text."


def test_it_still_marks_the_message_read(monkeypatch, capsys):
    """NEGATIVE CONTROL: the write half worked and must keep working. Adding the read
    half must not cost the behaviour the verb already had."""
    store = _FakeStore(_STORED)

    body = _read(monkeypatch, store, capsys)

    assert store.marked is True
    assert body["marked_read"] is True


def test_a_missing_message_is_an_error_not_an_empty_envelope(monkeypatch, capsys):
    """Returning ok:true with null fields would be the original defect wearing the
    fix's clothes — the caller still could not tell absent from unreadable."""
    store = _FakeStore(None)

    body = _read(monkeypatch, store, capsys)

    assert body["ok"] is False
    assert "No message" in body["error"]
    assert store.marked is False, "an unloadable message must not have its unread flag consumed"


# ── #394 problem 2: limit applied before the sort ─────────────────────
#
# The break ran inside the iteration, before the sort. Ref order is message UUID, so a
# limited fetch returned an arbitrary subset which was then sorted — not the newest N.
# Symptom the reporter chased first: with a backlog above the limit, marking messages
# read let OLDER messages enter the window and look new.


def _store_with(messages):
    """A GitMessageStore whose ref walk and note loads are stubbed, so the test
    exercises the ordering logic rather than git."""
    from empirica.core.canonical.empirica_git.message_store import GitMessageStore

    store = GitMessageStore.__new__(GitMessageStore)
    store._git_available = True
    store.workspace_root = "."
    by_id = {m["message_id"]: m for m in messages}

    class _R:
        returncode = 0
        # Deliberately NOT in timestamp order — ref order is UUID order.
        stdout = "\n".join(f"refs/notes/empirica/messages/direct/{m}" for m in sorted(by_id))

    store.load_message = lambda channel, message_id: by_id.get(message_id)
    store._matches_inbox_filters = lambda *a, **k: True
    return store, _R


def test_a_limited_inbox_returns_the_NEWEST_messages(monkeypatch):
    """POSITIVE CONTROL — the arbitrary-subset bug."""
    import subprocess

    from empirica.core.canonical.empirica_git.message_store import GitMessageStore

    # The alphabetically-FIRST ref must be the OLDEST message, otherwise ref order
    # coincides with newest-first and the old code passes. My first fixture made
    # exactly that mistake and the failing-first control caught it.
    msgs = [
        {"message_id": "aaaa", "timestamp": "2026-01-01", "subject": "oldest"},
        {"message_id": "bbbb", "timestamp": "2026-07-01", "subject": "middle"},
        {"message_id": "ffff", "timestamp": "2026-08-01", "subject": "newest"},
    ]
    store, fake = _store_with(msgs)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake())

    got = GitMessageStore.get_inbox(store, ai_id="me", limit=1)

    assert [m["subject"] for m in got] == ["newest"], "limit returned an arbitrary ref-order subset"


def test_the_limit_is_still_respected(monkeypatch):
    """NEGATIVE CONTROL: sorting before slicing must not stop bounding the result."""
    import subprocess

    from empirica.core.canonical.empirica_git.message_store import GitMessageStore

    msgs = [{"message_id": f"{i:04x}", "timestamp": f"2026-01-{i + 1:02d}", "subject": str(i)} for i in range(10)]
    store, fake = _store_with(msgs)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake())

    assert len(GitMessageStore.get_inbox(store, ai_id="me", limit=3)) == 3
