"""Retrieval must not present resolved work as open, and projections must not drop
populated columns.

Two defects reported by peer practices, both the same shape as the project-search
P0: state is written correctly to SQLite and then does not reach what the reader
actually sees.

**A1 — resolved unknowns were embedded as if open.** `rebuild` called
`get_project_unknowns(project_id)` with no `resolved` argument. That parameter
defaults to `None`, meaning *no filter*, so answered questions were re-embedded
and returned at rank 1 from `project-search` with no resolution marker. Measured
on this practice: **396 of 410** embedded unknowns were already resolved.

The asymmetry is what made it hard to see: `get_project_findings` filters
resolved/deprecated rows INTERNALLY, so the finding path behaved correctly after
the same rebuild. The difference lives in the getters, not in the embed or the
payload.

**A2 — `linkedin_url` was absent from the contact projection.** Not empty —
absent. The column existed with 8 populated rows and the extension had rendered a
LinkedIn chip since v0.9.x, so it read as a data-population failure rather than a
projection gap.
"""

from __future__ import annotations

import inspect

import pytest

from empirica.data.session_database import SessionDatabase

# ── A1: resolved unknowns must not be embedded ────────────────────────


def test_rebuild_requests_only_unresolved_unknowns():
    """THE regression. `rebuild` must pass `resolved=False`.

    Asserted against the source of the call rather than by running a full rebuild:
    the bug was a missing argument on one line, and an omitted default is exactly
    what a behavioural test over a live Qdrant would be slowest to catch.
    """
    from empirica.core.qdrant import rebuild

    src = inspect.getsource(rebuild)
    assert "get_project_unknowns(project_id, resolved=False)" in src, (
        "rebuild must request only UNRESOLVED unknowns; omitting `resolved` "
        "defaults to None (no filter) and embeds answered questions as open"
    )


def test_get_project_unknowns_defaults_to_no_filter():
    """Pins WHY the call site must be explicit.

    If this default ever changes to False, the call-site argument becomes
    belt-and-braces rather than load-bearing — but until then, omitting it is a
    silent bug, and this test documents that.
    """
    sig = inspect.signature(SessionDatabase.get_project_unknowns)
    assert sig.parameters["resolved"].default is None


def test_resolved_filter_actually_excludes_resolved(tmp_path):
    """Behavioural proof that `resolved=False` does what the fix relies on."""
    db = SessionDatabase(db_path=str(tmp_path / "t.db"))
    try:
        conn = db.conn
        conn.execute(
            "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, is_resolved, unknown_data) "
            "VALUES ('u-open','p','s','still open',0.0,0,'{}')"
        )
        conn.execute(
            "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, is_resolved, unknown_data) "
            "VALUES ('u-done','p','s','already answered',0.0,1,'{}')"
        )
        conn.commit()

        unfiltered = {str(r.get("id")) for r in db.get_project_unknowns("p")}
        filtered = {str(r.get("id")) for r in db.get_project_unknowns("p", resolved=False)}

        assert "u-open" in unfiltered and "u-done" in unfiltered, "unfiltered must return both"
        assert "u-open" in filtered, "an open unknown must survive the filter"
        assert "u-done" not in filtered, "a RESOLVED unknown must not reach the embed"
    finally:
        db.close()


# ── A2: the contact projection must carry populated columns ───────────


def test_contact_detail_map_carries_linkedin_url(tmp_path, monkeypatch):
    """A populated column must reach the projection, or it reads as missing data."""
    import sqlite3

    from empirica.data.repositories import workspace_db as wdb

    db_file = tmp_path / "workspace.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, email_primary TEXT, phone_primary TEXT, "
        "organization_title TEXT, tags TEXT, notes TEXT, contact_type TEXT, lifecycle_stage TEXT, linkedin_url TEXT)"
    )
    conn.execute(
        "INSERT INTO contacts VALUES ('c1','a@b.c',NULL,'CEO','[]',NULL,'person','active','https://linkedin.com/in/x')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: db_file)

    with wdb.WorkspaceDBRepository.open() as repo:
        detail = repo.get_contact_detail_map()

    assert "c1" in detail
    assert detail["c1"]["linkedin_url"] == "https://linkedin.com/in/x"


def test_contact_detail_map_survives_a_schema_without_linkedin_url(tmp_path, monkeypatch):
    """Older workspace DBs predate the column. Selecting it unconditionally would
    break the WHOLE projection instead of omitting one field — the key is simply
    absent, matching the honest-empty shape used elsewhere in this map."""
    import sqlite3

    from empirica.data.repositories import workspace_db as wdb

    db_file = tmp_path / "workspace.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, email_primary TEXT, phone_primary TEXT, "
        "organization_title TEXT, tags TEXT, notes TEXT, contact_type TEXT, lifecycle_stage TEXT)"
    )
    conn.execute("INSERT INTO contacts VALUES ('c1','a@b.c',NULL,'CEO','[]',NULL,'person','active')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: db_file)

    with wdb.WorkspaceDBRepository.open() as repo:
        detail = repo.get_contact_detail_map()

    assert detail["c1"]["email"] == "a@b.c", "the rest of the projection must still work"
    assert "linkedin_url" not in detail["c1"]


@pytest.mark.parametrize("field", ["email", "phone", "title", "contact_type", "lifecycle_stage"])
def test_existing_projection_fields_are_unchanged(tmp_path, monkeypatch, field):
    """Guard against the fix quietly dropping a sibling field."""
    import sqlite3

    from empirica.data.repositories import workspace_db as wdb

    db_file = tmp_path / "workspace.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, email_primary TEXT, phone_primary TEXT, "
        "organization_title TEXT, tags TEXT, notes TEXT, contact_type TEXT, lifecycle_stage TEXT, linkedin_url TEXT)"
    )
    conn.execute("INSERT INTO contacts VALUES ('c1','a@b.c','+1','CEO','[]','n','person','active','u')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: db_file)

    with wdb.WorkspaceDBRepository.open() as repo:
        detail = repo.get_contact_detail_map()

    assert field in detail["c1"]


# ── prune_dangling must not treat sources as missing endpoints ─────────


def test_a_source_counts_as_an_existing_edge_endpoint(tmp_path):
    """`prune_dangling` judged EVERY `sourced_from` edge dangling and deleted it.

    `_artifact_exists` walked `_ARTIFACT_TABLES`, which deliberately excludes
    `epistemic_sources` (that map also drives what `delete-artifacts` may DELETE, and
    sources are archived rather than deleted). But omitting it from the EXISTENCE
    check meant every source id read as missing.

    Not hypothetical: a routine prune during a gardening pass destroyed this
    practice's only two citation edges while both endpoints sat present on disk, and
    would have wiped every citation the artifact verbs now write.

    An ARCHIVED source still exists — `source-archive` preserves the audit chain by
    design — so it must not read as a missing endpoint either.
    """
    from empirica.cli.command_handlers.graph_commands import _artifact_exists
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "t.db"))
    try:
        live = db.breadcrumbs.create_source(
            project_id="p", session_id="s", title="live source", url="http://x", source_type="reference"
        )
        gone = db.breadcrumbs.create_source(
            project_id="p", session_id="s", title="archived source", url="http://y", source_type="reference"
        )
        db.conn.execute("UPDATE epistemic_sources SET archived=1 WHERE id=?", (gone,))
        db.conn.commit()

        assert _artifact_exists(db, live) is True, "a live source is a real edge endpoint"
        assert _artifact_exists(db, gone) is True, "an ARCHIVED source still exists — archiving is not deletion"
        assert _artifact_exists(db, "not-a-real-id") is False, "a genuine unknown id must still read missing"
    finally:
        db.close()
