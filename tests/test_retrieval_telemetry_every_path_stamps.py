"""Every surfacing path stamps — and each one is asserted SEPARATELY.

The defect this file exists to prevent is not "stamping is broken". It is
"stamping works on one path and nobody noticed the other three were dark".

Migration 063 gave `project_findings` a retrieval counter. It then acquired a
single writer — bootstrap's 7-day active-goal query — while PREFLIGHT/CHECK
context injection, `project-search` and the noetic-batch investigate leg wrote
nothing at all. The column therefore meant "surfaced by bootstrap" while reading
as "surfaced", and an artifact injected into context every session still showed
`retrieval_count = 0`, which is exactly what "never surfaced" shows.

**An aggregate assertion would have passed that whole time**, because one path
did work. So there is one test per path, each able to fail alone.

The other half is that a stamp can match NOTHING and raise nothing: the mistake
id is `mistake_<uuid>` in the Qdrant payload and bare in `mistakes_made.id`, and
the table is `mistakes_made` rather than the plausible `mistakes`. Both produce
a clean zero-row UPDATE. Hence every assertion here is on the RETURNED COUNT or
on the row itself — never on "did not raise".
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.retrieval_telemetry import (
    collect_ids,
    normalize_artifact_id,
    stamp_items,
    stamp_retrieval,
)
from empirica.data.migrations.migrations import (
    migration_063_finding_retrieval_signal,
    migration_067_retrieval_telemetry_all_types,
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply the retrieval-telemetry migrations IN ORDER, as a real DB does.

    063 gives `project_findings` its columns; 067 extends the rest and creates
    the snapshot triggers. Running 067 alone leaves findings without a counter —
    which the first draft of this fixture did, and the tests caught.
    """
    migration_063_finding_retrieval_signal(conn.cursor())
    migration_067_retrieval_telemetry_all_types(conn.cursor())
    conn.commit()


# Minimal shapes — only the columns the telemetry and its triggers touch. Built
# here rather than by copying the live schema, so the test does not silently
# depend on the developer's own database (see: "tests must not measure the box").
_SCHEMA = """
CREATE TABLE project_findings (
    id TEXT PRIMARY KEY, finding TEXT, is_resolved INTEGER DEFAULT 0,
    resolved_timestamp REAL, project_id TEXT
);
CREATE TABLE project_unknowns (
    id TEXT PRIMARY KEY, unknown TEXT, is_resolved INTEGER DEFAULT 0,
    resolved_timestamp REAL, project_id TEXT
);
CREATE TABLE project_dead_ends (
    id TEXT PRIMARY KEY, approach TEXT, is_invalidated INTEGER DEFAULT 0,
    invalidated_at REAL, project_id TEXT
);
CREATE TABLE mistakes_made (
    id TEXT PRIMARY KEY, mistake TEXT, is_invalidated INTEGER DEFAULT 0,
    invalidated_at REAL, project_id TEXT
);
CREATE TABLE assumptions (
    id TEXT PRIMARY KEY, assumption TEXT, status TEXT,
    resolved_timestamp REAL, project_id TEXT
);
CREATE TABLE decisions (id TEXT PRIMARY KEY, choice TEXT, project_id TEXT);
"""


@pytest.fixture
def db(tmp_path):
    """A migrated database with one row of every artifact type."""
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO project_findings (id, finding) VALUES ('f1', 'a finding')")
    conn.execute("INSERT INTO project_unknowns (id, unknown) VALUES ('u1', 'an unknown')")
    conn.execute("INSERT INTO project_dead_ends (id, approach) VALUES ('d1', 'a dead end')")
    conn.execute("INSERT INTO mistakes_made (id, mistake) VALUES ('m1', 'a mistake')")
    conn.execute("INSERT INTO assumptions (id, assumption) VALUES ('a1', 'an assumption')")
    conn.commit()
    _migrate(conn)
    conn.close()
    return path


def _count(db, table, rid):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT retrieval_count FROM {table} WHERE id = ?", (rid,)).fetchone()[0]
    finally:
        conn.close()


# ─── the helper itself ─────────────────────────────────────────────────


def test_stamp_returns_rows_written_not_none(db):
    """The count IS the contract.

    A stamping function returning None makes "wrote 12 rows" and "matched
    nothing" identical to every caller and every test. Both of this module's
    real silent-failure modes (namespaced ids, wrong table name) produce a
    clean, exception-free, zero-row UPDATE.
    """
    assert stamp_retrieval([("f1", "finding")], db_path=db) == 1
    assert _count(db, "project_findings", "f1") == 1


def test_unmatched_ids_report_zero_rather_than_pretending(db):
    assert stamp_retrieval([("no-such-finding", "finding")], db_path=db) == 0


def test_mistake_ids_are_namespaced_in_qdrant_and_bare_in_sqlite(db):
    """`mistake_<uuid>` vs `<uuid>` — unstripped, the UPDATE matches nothing.

    This is not defensive trivia: `_build_mistake_items` really does prefix the
    point id, and `mistakes_made.id` really is bare, so the naive version of
    this code silently stamps zero mistakes forever.
    """
    assert normalize_artifact_id("mistake_m1", "mistake") == "m1"
    assert stamp_retrieval([("mistake_m1", "mistake")], db_path=db) == 1
    assert _count(db, "mistakes_made", "m1") == 1


def test_every_artifact_type_resolves_to_a_real_table(db):
    """One assertion per type, because a wrong table name fails silently.

    `mistakes` and `project_decisions` do not exist; a query against either
    returns zero rows forever and reports success.
    """
    for rid, atype, table in [
        ("f1", "finding", "project_findings"),
        ("u1", "unknown", "project_unknowns"),
        ("d1", "dead_end", "project_dead_ends"),
        ("m1", "mistake", "mistakes_made"),
        ("a1", "assumption", "assumptions"),
    ]:
        stamped = stamp_retrieval([(rid, atype)], db_path=db)
        assert stamped == 1, f"{atype} did not stamp — check the table mapping for {table}"


def test_types_without_retrieval_columns_are_skipped_not_counted(db):
    """Lessons and episodic narratives live elsewhere and have no counters."""
    assert stamp_retrieval([("x", "lesson"), ("y", "episodic")], db_path=db) == 0


def test_collect_ids_skips_items_with_no_id_rather_than_guessing():
    items = [{"artifact_id": "f1", "type": "finding"}, {"text": "no id here", "type": "finding"}]
    assert collect_ids(items) == [("f1", "finding")]


# ─── the resolution snapshot ───────────────────────────────────────────


def test_snapshot_at_resolution_makes_before_and_after_separable(db):
    """The column the paper's question actually needs.

    A cumulative counter says how often an artifact was ever surfaced. It cannot
    say how much of that happened while the claim was still believed — and
    "how often was a stale claim served as current" is the whole question.
    """
    stamp_retrieval([("f1", "finding")] * 1, db_path=db)
    stamp_retrieval([("f1", "finding")] * 1, db_path=db)
    stamp_retrieval([("f1", "finding")] * 1, db_path=db)

    conn = sqlite3.connect(db)
    conn.execute("UPDATE project_findings SET is_resolved = 1, resolved_timestamp = 1.0 WHERE id = 'f1'")
    conn.commit()
    before, snap = conn.execute(
        "SELECT retrieval_count, retrieval_count_at_resolution FROM project_findings WHERE id='f1'"
    ).fetchone()
    conn.close()
    assert before == 3
    assert snap == 3, "counter was not snapshotted when the finding closed"

    # A retrieval AFTER resolution must move the total and NOT the snapshot —
    # that delta is how a leaking retrieval filter becomes visible.
    stamp_retrieval([("f1", "finding")], db_path=db)
    conn = sqlite3.connect(db)
    after, snap2 = conn.execute(
        "SELECT retrieval_count, retrieval_count_at_resolution FROM project_findings WHERE id='f1'"
    ).fetchone()
    conn.close()
    assert (after, snap2) == (4, 3)
    assert after - snap2 == 1, "post-resolution retrievals are not recoverable"


def test_snapshot_fires_for_invalidation_not_only_resolution(db):
    """Dead-ends and mistakes are INVALIDATED, not resolved — different column.

    Three vocabularies across five tables is exactly where a single hard-coded
    predicate quietly covers three of them.
    """
    stamp_retrieval([("d1", "dead_end"), ("mistake_m1", "mistake")], db_path=db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE project_dead_ends SET is_invalidated = 1 WHERE id = 'd1'")
    conn.execute("UPDATE mistakes_made SET is_invalidated = 1 WHERE id = 'm1'")
    conn.commit()
    d = conn.execute("SELECT retrieval_count_at_resolution FROM project_dead_ends WHERE id='d1'").fetchone()[0]
    m = conn.execute("SELECT retrieval_count_at_resolution FROM mistakes_made WHERE id='m1'").fetchone()[0]
    conn.close()
    assert (d, m) == (1, 1)


def test_reopen_and_reresolve_keeps_the_first_boundary(db):
    """Idempotent by guard, not by luck — and the FIRST close is the boundary."""
    stamp_retrieval([("u1", "unknown")], db_path=db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE project_unknowns SET is_resolved = 1 WHERE id = 'u1'")
    conn.commit()
    conn.execute("UPDATE project_unknowns SET is_resolved = 0 WHERE id = 'u1'")
    conn.commit()
    conn.close()
    stamp_retrieval([("u1", "unknown")], db_path=db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE project_unknowns SET is_resolved = 1 WHERE id = 'u1'")
    conn.commit()
    snap = conn.execute("SELECT retrieval_count_at_resolution FROM project_unknowns WHERE id='u1'").fetchone()[0]
    conn.close()
    assert snap == 1, "the snapshot moved on re-resolution; the first close is the boundary"


# ─── one test per surfacing path ───────────────────────────────────────
#
# Each of these must be able to fail ALONE. That is the entire point: for a
# month, one path worked and three did not, and any aggregate check was green.


def test_path_bootstrap_circles_delegates_to_the_shared_helper():
    """bootstrap was the only path that ever stamped — it must keep doing so."""
    import inspect

    from empirica.core.bootstrap import circles

    src = inspect.getsource(circles._stamp_retrieval)
    assert "retrieval_telemetry" in src, "bootstrap no longer routes through the shared helper"
    assert "commit=True" in src, (
        "bootstrap borrows a cursor from a READ path — without an explicit commit the "
        "UPDATE is discarded on close and stamps nothing, silently. That is the "
        "original bug, verified at 0 stamped rows before and after a real bootstrap."
    )


def test_path_preflight_injection_stamps_from_the_raw_lists():
    """The highest-volume path, and the one that recorded nothing for a month.

    Asserted against the ranked/raw lists specifically: the projections that
    build the injected block drop `artifact_id`, so a stamp placed after them
    has nothing to key on and would silently write zero rows.
    """
    import inspect

    from empirica.core.qdrant import pattern_retrieval

    src = inspect.getsource(pattern_retrieval.retrieve_task_patterns)
    assert "_stamp_surfaced(" in src, "PREFLIGHT/CHECK context injection stamps nothing"
    assert "findings_ranked" in src.split("_stamp_surfaced(")[1].split(")")[0], (
        "stamping must use findings_RANKED, not findings_raw — raw is an over-fetch that "
        "is then cut to the limit, so counting it inflates the signal with artifacts the "
        "practitioner never saw"
    )


def test_path_project_search_stamps_local_memory_only():
    """`--global` results belong to OTHER projects and must not be stamped here."""
    import inspect

    from empirica.cli.command_handlers import project_search

    handler = inspect.getsource(project_search.handle_project_search_command)
    assert "_stamp_surfaced(results)" in handler, "project-search stamps nothing"

    stamper = inspect.getsource(project_search._stamp_surfaced)
    assert '"memory"' in stamper or "'memory'" in stamper
    assert "global" not in stamper.split('"""')[2], (
        "cross-project results must not be stamped against this project's rows"
    )


def test_path_noetic_batch_investigate_reaches_search():
    """noetic-batch's investigate leg shells out to project-search.

    Recorded as a test because it is the reason that path needs no separate
    wiring — if the executor ever stops delegating, this fails and the coverage
    claim stops being true silently.
    """
    import inspect

    from empirica.core.noetic_batch import executor

    src = inspect.getsource(executor._execute_investigate)
    assert "project-search" in src, (
        "noetic-batch no longer delegates to project-search — its investigate leg now needs its own retrieval stamp"
    )


# ─── the doctor control ────────────────────────────────────────────────


def test_doctor_notices_when_nothing_is_ever_stamped(tmp_path):
    """The control that was missing while the bug ran.

    A persistent stamping failure looks exactly like a young practice: the
    counter reads 0. The check has to separate them, and it does it on corpus
    size, so it must WARN on a large unstamped corpus and stay quiet on a
    small one.
    """
    from empirica.cli.command_handlers.doctor import PASS, WARN, check_retrieval_telemetry

    proj = tmp_path / "proj"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    dbp = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(dbp)
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.executemany(
        "INSERT INTO project_findings (id, finding, retrieval_count) VALUES (?, ?, 0)",
        [(f"f{i}", "x") for i in range(200)],
    )
    conn.commit()
    conn.close()

    warned = check_retrieval_telemetry(proj)
    assert warned.status == WARN, "200 findings and zero ever stamped should not read as healthy"

    conn = sqlite3.connect(dbp)
    conn.execute("UPDATE project_findings SET retrieval_count = 2 WHERE id = 'f1'")
    conn.commit()
    conn.close()
    assert check_retrieval_telemetry(proj).status == PASS


def test_doctor_notices_a_half_applied_migration(tmp_path):
    """Columns present, triggers absent — the state I actually produced.

    A migration is applied ONCE. Editing its definition afterwards leaves every
    database that already ran it permanently holding the old half, with the
    ledger reporting success. Here that means resolution writes no snapshot, so
    every artifact closes looking as though it was never retrieved before it
    closed — wrong data that reads exactly like correct data.

    Nothing else in the system looks at this, which is why the check does.
    """
    from empirica.cli.command_handlers.doctor import WARN, check_retrieval_telemetry

    proj = tmp_path / "proj"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    dbp = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(dbp)
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.executemany(
        "INSERT INTO project_findings (id, finding, retrieval_count) VALUES (?, ?, 1)",
        [(f"f{i}", "x") for i in range(100)],
    )
    conn.commit()
    # Simulate the half-applied state: columns stay, triggers go.
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%_retrieval_snapshot'"
    ).fetchall():
        conn.execute(f"DROP TRIGGER {row[0]}")
    conn.commit()
    conn.close()

    result = check_retrieval_telemetry(proj)
    assert result.status == WARN, "columns without triggers reported as healthy"
    assert "trigger" in result.detail.lower()


def test_doctor_does_not_warn_on_a_young_practice(tmp_path):
    """A check that fires on every fresh install trains people to ignore it."""
    from empirica.cli.command_handlers.doctor import PASS, check_retrieval_telemetry

    proj = tmp_path / "proj"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    conn = sqlite3.connect(proj / ".empirica" / "sessions" / "sessions.db")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.execute("INSERT INTO project_findings (id, finding) VALUES ('only', 'x')")
    conn.commit()
    conn.close()
    assert check_retrieval_telemetry(proj).status == PASS


def test_stamp_items_handles_the_shape_search_actually_returns(db):
    """End-to-end on the real payload shape, mixed types in one call."""
    items = [
        {"artifact_id": "f1", "type": "finding", "score": 0.8},
        {"artifact_id": "u1", "type": "unknown", "score": 0.7},
        {"artifact_id": "mistake_m1", "type": "mistake", "score": 0.6},
        {"artifact_id": "bogus", "type": "finding"},
    ]
    assert stamp_items(items, db_path=db) == 3
