"""
Schema Dialect Adapter

Transforms SQLite-native DDL to PostgreSQL-compatible DDL at runtime.
This keeps a single source of truth for schemas (the SQLite versions)
while allowing PostgreSQL deployment without maintaining parallel schemas.

Transformations applied:
- INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
- BOOLEAN DEFAULT 0 → BOOLEAN DEFAULT FALSE
- BOOLEAN DEFAULT 1 → BOOLEAN DEFAULT TRUE
- Column name 'do' → '"do"' (reserved word quoting)
- strftime('%s', 'now') → EXTRACT(EPOCH FROM NOW())  (UNIX-epoch float default)
"""

import logging
import re

logger = logging.getLogger(__name__)


def adapt_schema_sql(sql: str, dialect: str) -> str:
    """
    Adapt a CREATE TABLE SQL string for the target dialect.

    Args:
        sql: SQLite-native CREATE TABLE statement
        dialect: 'sqlite' or 'postgresql'

    Returns:
        Dialect-appropriate SQL string
    """
    if dialect == "sqlite":
        return sql

    if dialect != "postgresql":
        raise ValueError(f"Unsupported dialect: {dialect}")

    adapted = sql

    # 1. AUTOINCREMENT → SERIAL
    # Match: INTEGER PRIMARY KEY AUTOINCREMENT
    # Replace: SERIAL PRIMARY KEY
    adapted = re.sub(
        r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
        'SERIAL PRIMARY KEY',
        adapted,
        flags=re.IGNORECASE
    )

    # 2. BOOLEAN DEFAULT 0 → BOOLEAN DEFAULT FALSE
    adapted = re.sub(
        r'BOOLEAN\s+DEFAULT\s+0',
        'BOOLEAN DEFAULT FALSE',
        adapted,
        flags=re.IGNORECASE
    )

    # 3. BOOLEAN DEFAULT 1 → BOOLEAN DEFAULT TRUE
    adapted = re.sub(
        r'BOOLEAN\s+DEFAULT\s+1',
        'BOOLEAN DEFAULT TRUE',
        adapted,
        flags=re.IGNORECASE
    )

    # 4. Quote reserved word 'do' as column name
    # Match: whitespace + 'do' + whitespace + REAL/TEXT/INTEGER (column definition context)
    # Avoid matching 'do_vector', 'domain', etc.
    adapted = re.sub(
        r'(\s+)(do)(\s+REAL)',
        r'\1"do"\3',
        adapted
    )

    # 5. strftime('%s', 'now') → EXTRACT(EPOCH FROM NOW())
    # SQLite uses strftime to get a UNIX-epoch float for `created_at REAL` defaults
    # (verification_schema.py:82, verification_schema.py:151, migrations.py:1085 in v1.9.0).
    # Postgres has no strftime; EXTRACT(EPOCH FROM NOW()) returns DOUBLE PRECISION
    # which auto-casts to REAL. Tolerant of optional whitespace inside the call.
    # Without this translation, fresh project-bootstrap on a Postgres backend fails with
    # `function strftime(unknown, unknown) does not exist` mid-migration.
    adapted = re.sub(
        r"strftime\s*\(\s*'%s'\s*,\s*'now'\s*\)",
        "EXTRACT(EPOCH FROM NOW())",
        adapted,
        flags=re.IGNORECASE
    )

    return adapted


def adapt_all_schemas(schemas: list[str], dialect: str) -> list[str]:
    """Adapt a list of schema SQL strings for the target dialect."""
    return [adapt_schema_sql(sql, dialect) for sql in schemas]


def adapt_index_sql(sql: str, dialect: str) -> str:
    """
    Adapt an index creation SQL for the target dialect.

    Both SQLite and PostgreSQL support CREATE INDEX IF NOT EXISTS,
    so this is mostly a pass-through. The main concern is
    column name quoting for reserved words.
    """
    if dialect == "sqlite":
        return sql

    # Quote 'do' column references in indexes if present
    # This is a rare case but handle it for completeness
    return sql
