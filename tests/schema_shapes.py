"""Build a test database at either schema GENERATION — from production code only.

Three defects in one session were invisible to their own tests, all for one
reason: **a test written from the same world-model as the code cannot disagree
with an assumption it shares with the code.** Fixtures are where that
world-model gets baked in, and the suite had two fixture populations with
complementary blind spots:

* **109 files hand-roll ``CREATE TABLE``.** A hand-rolled schema drifts from
  production silently, and then fixture and code agree with each other while
  both disagree with reality — the suite is green precisely where it is blind.
* **28 files run migrations in their fixtures.** For those tests the
  pre-migration world does not exist, so ``goals-list`` failed outright
  (``no such column: g.completion_reason``) on every pre-068 database while all
  18 of the feature's own tests stayed green. It was caught by unrelated tests
  that happened to build the other world.

Both directions are the same mistake — picking ONE world and hand-describing
it. This module removes the choice: both generations are built from the exact
code a real install executes, so there is nothing to drift and nothing to
forget.

    base     = ALL_SCHEMAS                       (a fresh install, pre-migration)
    current  = base + MigrationRunner.run_all()  (what every live db converges to)

Why both generations are PERMANENT test targets, not a transition state:
``--all-projects`` opens OTHER practices' ``sessions.db`` files, and this
process cannot migrate them — each practice migrates its own db when its own
practitioner next runs a command. Readers therefore meet both shapes
indefinitely, and a reader that assumes the migrated shape is a reader that
breaks on someone else's database.

The builder **asserts the generation it produced** (marker column present or
absent). That is not decoration: the unmigrated-compat regression test proves
nothing the moment its fixture quietly becomes migrated — e.g. someone "fixes"
the helper to run migrations — and nothing else would notice. A fixture that
cannot state which world it built is a fixture one refactor away from testing
the wrong one.

Usage::

    from tests.schema_shapes import build_db, GENERATIONS

    def test_x(tmp_path):
        db = build_db(tmp_path / "s.db", "current")

    @pytest.mark.parametrize("generation", GENERATIONS)
    def test_works_on_both_worlds(tmp_path, generation):
        db = build_db(tmp_path / "s.db", generation)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Every generation a reader can meet in the wild. Parametrize over this when a
#: test's subject reads schema it did not create.
GENERATIONS: tuple[str, ...] = ("base", "current")

#: Columns that exist ONLY in the migrated world — used to assert which world
#: the builder actually produced. One entry per table is enough: the point is a
#: canary that fires when the generations collapse, not an inventory.
_CURRENT_ONLY_MARKERS: tuple[tuple[str, str], ...] = (
    ("goals", "completion_reason"),  # migration 068
    ("project_findings", "retrieval_count_at_resolution"),  # migration 067
)


def build_db(path: Path | str, generation: str) -> Path:
    """Create a sessions-shaped SQLite db at ``path`` at the given generation.

    Returns the path. Raises if the produced schema does not match the requested
    generation — loudly, at fixture time, where the mismatch is diagnosable.
    """
    if generation not in GENERATIONS:
        raise ValueError(f"unknown schema generation {generation!r} — one of {GENERATIONS}")

    from empirica.data.schema import ALL_SCHEMAS

    path = Path(path)
    conn = sqlite3.connect(path)
    try:
        for sql in ALL_SCHEMAS:
            conn.execute(sql)
        conn.commit()

        if generation == "current":
            from empirica.data.migrations.migration_runner import MigrationRunner
            from empirica.data.migrations.migrations import ALL_MIGRATIONS

            MigrationRunner(conn).run_all(ALL_MIGRATIONS)
            conn.commit()

        _assert_generation(conn, generation)
    finally:
        conn.close()
    return path


def _assert_generation(conn: sqlite3.Connection, generation: str) -> None:
    """The self-check: did we build the world we were asked for?

    Verified 2026-09-09 before this module existed: the full registry (67
    migrations) runs clean against an empty ALL_SCHEMAS database, 54 tables,
    and the marker columns split the generations exactly.
    """
    for table, column in _CURRENT_ONLY_MARKERS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            raise AssertionError(f"schema_shapes built a db with no {table!r} table — ALL_SCHEMAS changed shape?")
        present = column in cols
        if generation == "current" and not present:
            raise AssertionError(
                f"asked for 'current' but {table}.{column} is missing — migrations did not run or were pruned"
            )
        if generation == "base" and present:
            raise AssertionError(
                f"asked for 'base' but {table}.{column} exists — the base fixture has silently become "
                f"migrated, and every unmigrated-compat test using it now proves nothing"
            )


def db_fingerprint(conn: sqlite3.Connection) -> dict[str, tuple[tuple[str, ...], int]]:
    """Every table's (columns, row_count) — the cheap idempotency oracle.

    "Ran twice without raising" is the weakest true statement about an
    idempotent operation, and it misses the failure mode that matters: a
    migration whose backfill INSERTs again on the second run does not raise.
    Comparing fingerprints before and after the second run catches double
    writes, dropped rows, and schema mutation in one assert, for the cost of a
    dict compare. (`schema_migrations` is excluded — the ledger legitimately
    grows when a re-run records itself.)
    """
    fp: dict[str, tuple[tuple[str, ...], int]] = {}
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'")
    ]
    for t in tables:
        cols = tuple(r[1] for r in conn.execute(f"PRAGMA table_info({t})"))
        count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        fp[t] = (cols, count)
    return fp
