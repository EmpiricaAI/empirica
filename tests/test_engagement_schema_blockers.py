"""Legacy-shaped `engagements` tables must be detected by SHAPE, per box.

One fleet box cannot create engagements at all: its `workspace.db` was seeded by
carrying the retired CRM's tables over instead of being created fresh, so
`engagements` still declares `client_id NOT NULL` — a column no current code path
supplies. Every insert this codebase can construct is rejected, permanently, on
that box only.

Two things make this hard to find, and the test pins both.

**The self-heal does not cover it.** `open()` PRAGMAs the table and ALTER-ADDs any
MISSING sidecar column. The legacy case is the opposite — an EXTRA column that is
NOT NULL with no default — and sqlite cannot ALTER that away. So a box can pass
schema initialisation cleanly and still be unwritable.

**Provenance cannot answer it.** autonomy established (2026-08-18) that
`~/.empirica/crm/crm.db` is present on boxes whose `workspace.db` is perfectly
clean — David's box has the retired db and 49 healthy rows. The discriminator is
whether the workspace db was *seeded* or *created fresh*, and nothing records
that. "Which boxes ran the old CRM" is therefore the wrong sweep question, and the
table's own shape is the only honest source.

Detection only. Repairing such a table means rebuilding it over real engagement
history, which is an explicit, backed-up, opt-in act — not something a checker
does.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository

CURRENT_DDL = """
CREATE TABLE engagements (
    engagement_id TEXT PRIMARY KEY,
    contact_id TEXT,
    project_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    engagement_type TEXT DEFAULT 'outreach',
    started_at REAL,
    ended_at REAL,
    status TEXT DEFAULT 'active',
    outcome TEXT,
    lifecycle_state TEXT DEFAULT 'open',
    stage TEXT,
    domain TEXT,
    created_at REAL,
    created_by_ai_id TEXT,
    updated_at REAL
)
"""

LEGACY_DDL = """
CREATE TABLE engagements (
    engagement_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'active'
)
"""


@pytest.fixture
def repo(tmp_path):
    """A repository over a throwaway db — never the box's real workspace.db."""
    conn = sqlite3.connect(tmp_path / "probe.db")
    conn.row_factory = sqlite3.Row
    try:
        yield WorkspaceDBRepository(conn)
    finally:
        conn.close()


def test_the_current_shape_reports_no_blockers(repo):
    """Positive control: the detector must be silent on a healthy table.

    Without this, a detector that returns [] unconditionally would look identical
    to a clean box.
    """
    repo.conn.execute(CURRENT_DDL)
    assert repo.engagement_schema_blockers() == []


def test_the_legacy_shape_is_detected_by_its_unsatisfiable_column(repo):
    repo.conn.execute(LEGACY_DDL)
    blockers = repo.engagement_schema_blockers()
    assert [b["column"] for b in blockers] == ["client_id"]


def test_the_detector_is_a_shape_check_not_a_client_id_check(repo):
    """`client_id` is this incident's column, not the rule.

    Any NOT NULL, no-default column outside the canonical set is unsatisfiable —
    naming the one we happened to hit would leave the next one undetected.
    """
    repo.conn.execute(
        "CREATE TABLE engagements ("
        "engagement_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "legacy_account_ref TEXT NOT NULL)"
    )
    assert [b["column"] for b in repo.engagement_schema_blockers()] == ["legacy_account_ref"]


def test_an_extra_column_that_is_writable_is_not_a_blocker(repo):
    """Nullable or defaulted extras are harmless — flagging them would be noise.

    A checker that fires on every box teaches practitioners to ignore it, which
    costs more than the check earns.
    """
    repo.conn.execute(
        "CREATE TABLE engagements ("
        "engagement_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "legacy_note TEXT, legacy_flag TEXT NOT NULL DEFAULT 'x')"
    )
    assert repo.engagement_schema_blockers() == []


def test_a_missing_table_is_reported_as_no_blockers_not_a_crash(repo):
    """Absent is a different condition from mis-shaped, and louder elsewhere."""
    assert repo.engagement_schema_blockers() == []


def test_detection_writes_nothing(repo, tmp_path):
    """The repair half needs a human's signoff; the detector must not drift into it."""
    repo.conn.execute(LEGACY_DDL)
    repo.conn.commit()
    before = (tmp_path / "probe.db").read_bytes()
    repo.engagement_schema_blockers()
    repo.conn.commit()
    assert (tmp_path / "probe.db").read_bytes() == before
