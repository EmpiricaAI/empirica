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
