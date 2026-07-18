"""Migration runner with tracking for database schema changes"""

import sqlite3
from collections.abc import Callable


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


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check if a column exists in a table"""
    VALID_TABLES = {
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
    }

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
    VALID_TABLES = {
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
    }
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
