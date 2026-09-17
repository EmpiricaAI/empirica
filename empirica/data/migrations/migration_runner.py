"""Migration runner with tracking for database schema changes"""

import sqlite3
from collections.abc import Callable

#: Set once a skew warning has been printed, so a process that opens the DB many
#: times says it once rather than on every connection.
_SKEW_WARNED = False


class MigrationRunner:
    """Manages database migrations with execution tracking"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize migration runner with database connection."""
        self.conn = conn
        self._ensure_migrations_table()

    def _ensure_migrations_table(self):
        """Create migrations tracking table if it doesn't exist"""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
        """)
        self.conn.commit()

    def has_run(self, migration_id: str) -> bool:
        """Check if a migration has already been executed"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?", (migration_id,))
        return cursor.fetchone()[0] > 0

    def mark_as_run(self, migration_id: str, description: str = ""):
        """Mark a migration as executed"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO schema_migrations (migration_id, description) VALUES (?, ?)", (migration_id, description)
        )
        self.conn.commit()

    def run_migration(self, migration_id: str, description: str, migration_func: Callable):
        """
        Run a migration if it hasn't been executed yet

        Args:
            migration_id: Unique identifier (e.g., "20240101_add_status_column")
            description: Human-readable description
            migration_func: Function that executes the migration (takes cursor as arg)
        """
        if self.has_run(migration_id):
            return  # Already applied

        cursor = self.conn.cursor()
        try:
            migration_func(cursor)
            self.mark_as_run(migration_id, description)
        except Exception as e:
            # Re-raise with context
            raise RuntimeError(f"Migration {migration_id} failed: {e}") from e

    def run_all(self, migrations: list[tuple[str, str, Callable]]):
        """
        Run all pending migrations

        Args:
            migrations: List of (migration_id, description, migration_func) tuples
        """
        for migration_id, description, migration_func in migrations:
            self.run_migration(migration_id, description, migration_func)
        self._warn_if_code_is_older_than_schema(migrations)

    def schema_ahead_of_code(self, migrations: list[tuple[str, str, Callable]]) -> list[str]:
        """Migration ids numbered BEYOND anything the running code ships.

        Non-empty means this process is OLDER than the schema it is about to write
        to: some newer install of empirica migrated this database, and this one is
        still writing the previous row shape.

        That is not hypothetical. On 2026-09-17 migration 071 added `scope` and
        `measured_count` to `transaction_claims`. The columns were applied by an
        editable install running from the working tree; the `empirica` binary on
        PATH was a frozen 1.13.46 copy sharing the same database. Pydantic accepted
        the new keys (`claims: list[dict]` takes anything), the old writer ignored
        them, and every CLI call reported ok while storing NULL. Several practices
        measured it independently — "columns exist, populated on 0 of 1,111" — and it
        read as a defect in the new code, which was correct the whole time.

        Accepted-and-discarded is normally a bug in a handler. Here it was produced
        by VERSION SKEW with no bug anywhere: a newer schema, an older writer, and
        an input model permissive enough to let them disagree in silence.
        """
        return self._unknown_ids(migrations)["newer"]

    @staticmethod
    def _seq(migration_id: str) -> int | None:
        """Leading sequence number of an id like `071_claim_scope`, or None."""
        head = migration_id.split("_", 1)[0]
        return int(head) if head.isdigit() else None

    def _unknown_ids(self, migrations: list[tuple[str, str, Callable]]) -> dict[str, list[str]]:
        """Split ids the code does not know into NEWER and RESIDUE.

        The first version of this guard reported every unknown id as "a newer
        install migrated this DB", and on its first real run it was wrong: the
        database held `068_goal_completion_reason_` — a trailing underscore, applied
        from an uncommitted tree and renamed before commit. Harmless residue, and
        the message asserted a cause the code had never observed. That is the defect
        this whole area exists to remove, reproduced inside its own guard.

        The discriminator is the sequence number. A genuinely newer install's
        migration is numbered BEYOND anything this code ships; an id at or below the
        code's highest number that the code does not recognise was renamed or
        removed, and says nothing about another install.
        """
        known = {m[0] for m in migrations}
        top = max((n for n in (self._seq(m[0]) for m in migrations) if n is not None), default=None)
        try:
            rows = self.conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        except Exception:
            return {"newer": [], "residue": []}
        newer: list[str] = []
        residue: list[str] = []
        for (mid,) in rows:
            if mid in known:
                continue
            n = self._seq(mid)
            (newer if (top is not None and n is not None and n > top) else residue).append(mid)
        return {"newer": sorted(newer), "residue": sorted(residue)}

    def _warn_if_code_is_older_than_schema(self, migrations: list[tuple[str, str, Callable]]) -> None:
        """Say it once per process, loudly, on stderr.

        A warning and not an error: an older reader is usually harmless, and refusing
        to open the database would turn a recoverable skew into an outage. But it
        must not be SILENT, because the failure it predicts is one that reports ok.
        """
        global _SKEW_WARNED
        if _SKEW_WARNED:
            return
        ahead = self.schema_ahead_of_code(migrations)
        if not ahead:
            return
        _SKEW_WARNED = True
        import sys

        try:
            import empirica

            version, where = getattr(empirica, "__version__", "?"), getattr(empirica, "__file__", "?")
        except Exception:
            version, where = "?", "?"
        sys.stderr.write(
            f"empirica: WARNING — this database is NEWER than the code running against it.\n"
            f"  The schema has {len(ahead)} migration(s) numbered beyond anything this install "
            f"ships: {', '.join(ahead[:3])}{' …' if len(ahead) > 3 else ''}\n"
            f"  Running: empirica {version} from {where}\n"
            "  A newer install migrated this DB. Fields it added will be ACCEPTED and "
            "silently NOT STORED by this one — commands will still report ok.\n"
            "  Upgrade this install, or check for a second copy: a frozen binary and an "
            "editable tree sharing one database is how this happens.\n"
        )


#: Tables a migration may ALTER. One definition, read by both `column_exists` and
#: `add_column_if_missing` — it used to be two hand-maintained copies of the same
#: set, so adding a table to one and not the other half-worked: the existence
#: check passed and the ALTER raised `Invalid table name`, which reads as a bad
#: migration rather than an incomplete allowlist. Migration 071 hit exactly that.
MIGRATABLE_TABLES: frozenset[str] = frozenset(
    {
        "sessions",
        "reflexes",
        "cascades",
        "findings",
        "unknowns",
        "dead_ends",
        "reference_docs",
        "mistakes",
        "goals",
        "subtasks",
        "checkpoints",
        "handoffs",
        "schema_migrations",
        "epistemic_snapshots",
        "bayesian_beliefs",
        "projects",
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "clients",
        "engagements",
        "client_interactions",
        "client_projects",
        "investigation_branches",
        "epistemic_sources",
        "assumptions",
        "decisions",
        # Post-test grounded calibration tables
        "grounded_beliefs",
        "verification_evidence",
        "grounded_verifications",
        "calibration_trajectory",
        # Subagent isolation (migration 034)
        "subagent_sessions",
        # Composable epistemic patterns (migration 037)
        "lessons",
        "lesson_steps",
        # Prevention-currency measurement (migration 058/059)
        "prevention_events",
        "blindspot_events",
        # Per-claim grounding (062) + scope and measured_count (071)
        "transaction_claims",
    }
)


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check if a column exists in a table"""
    VALID_TABLES = MIGRATABLE_TABLES

    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")

    cursor.execute("SELECT COUNT(*) FROM pragma_table_info(?) WHERE name=?", (table, column))
    return cursor.fetchone()[0] > 0


def table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    """Check if a table exists in the database."""
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone()[0] > 0


def add_column_if_missing(cursor: sqlite3.Cursor, table: str, column: str, column_type: str, default: str = ""):
    """Add a column to a table if it doesn't already exist.

    Silently skips if the table doesn't exist (schema will create it later).
    """
    VALID_TABLES = MIGRATABLE_TABLES
    VALID_COLUMN_TYPES = {"TEXT", "INTEGER", "REAL", "BLOB", "NULL", "TIMESTAMP", "BOOLEAN", "JSON"}

    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")

    column_type_upper = column_type.upper().split("(")[0]
    if column_type_upper not in VALID_COLUMN_TYPES:
        raise ValueError(f"Invalid column type: {column_type}")

    # Skip if table doesn't exist yet (schema will create it with the column)
    if not table_exists(cursor, table):
        return

    if not column_exists(cursor, table, column):
        default_clause = f" DEFAULT {default}" if default else ""
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}{default_clause}")
