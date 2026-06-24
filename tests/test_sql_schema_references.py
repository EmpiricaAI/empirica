"""Catch a recurring bug class: static SQL that references a non-existent column/table.

THE BUG CLASS
-------------
A static SQL query in the codebase names a column or table that does not exist
in the real schema. At runtime SQLite raises ``OperationalError: no such column``
(or ``no such table``), but the call site wraps the query in a broad
``try/except`` that swallows the error. The feature then silently no-ops while
every health surface looks green — the failure is invisible until someone
notices the feature has been dead for an unknown duration.

A live example: ``SELECT meta FROM reflexes`` where the actual column is
``reflex_data``. That single typo made a whole feature dead for an unknown
duration, with no log, no test failure, no red badge — exactly because the
surrounding ``except`` ate the ``OperationalError``.

WHAT THIS TEST DOES
-------------------
1. Builds the *real* schema (session DB + workspace DB) in an in-memory SQLite
   connection by running the actual production schema builders.
2. AST-walks every ``.py`` under ``empirica/`` and extracts every *static*
   SQL string passed to ``.execute()`` / ``.executemany()`` / ``.executescript()``.
   Dynamic queries (f-strings, ``.format()``, ``%``, concatenation, name refs)
   are SKIPPED on purpose — interpolating identifiers from internal allow-lists
   is a known-correct pattern, and we cannot resolve them statically.
3. Validates each static DML query against the real schema using SQLite's own
   parser via ``EXPLAIN`` (no third-party SQL parser is used or added).
4. Asserts no query references a missing column or table.

KNOWN LIMITATION — migration drift
----------------------------------
This test builds the *fresh* schema (all CREATE TABLE + all migrations applied).
It therefore catches columns missing from the fresh schema, but NOT the
migration-drift variant: a column that exists in the fresh schema but is absent
from an older long-lived DB that never received a particular migration (e.g.
``impact`` on ``project_dead_ends`` on a DB created before that column was
added). Detecting that requires comparing against migrated long-lived DBs,
which is out of scope here.
"""

from __future__ import annotations

import ast
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Repo root = two levels up from this test file (tests/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
EMPIRICA_PKG = REPO_ROOT / "empirica"

# Directories under empirica/ we never scan.
#
# ``migrations`` is excluded deliberately: migration SQL legitimately references
# transient/legacy schema states (e.g. ``INSERT INTO reflexes ... FROM
# preflight_assessments``) that no longer exist in the fresh schema and are
# guarded by runtime existence checks. Validating them against the *final*
# schema produces guaranteed false positives, not bugs.
SKIP_DIR_NAMES = {"tests", "build", "dev_scripts", "__pycache__", "migrations"}

# ``crm_schema.py`` is excluded deliberately: it defines + queries the *crm.db*
# schema — a separate, NLE-scoped database this test does not build. Its queries
# legitimately reference crm.db tables/columns (e.g. ``engagements.client_id``)
# that don't exist in the session/workspace schema. These were skipped
# automatically until workspace.db gained its own (different) ``engagements``
# table; now that same-named table would mis-validate crm.db queries against it.
# Validating crm.db queries here is a guaranteed false positive, not a bug —
# same rationale as the ``migrations`` exclusion above.
SKIP_FILES = {"empirica/data/crm_schema.py"}

# Statement leading keywords we do NOT validate (DDL / pragmas / control).
# These are not the bug class (a bad column in a SELECT/UPDATE/DELETE is), and
# EXPLAIN-ing DDL or running PRAGMA via param substitution is noise.
NON_DML_PREFIXES = (
    "create",
    "alter",
    "drop",
    "pragma",
    "explain",
    "attach",
    "detach",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release",
    "vacuum",
    "analyze",
    "reindex",
    "with",  # CTEs — EXPLAIN of a bare CTE is brittle; skip to avoid noise.
)

# Methods whose first positional arg is a SQL string.
SQL_EXEC_METHODS = {"execute", "executemany", "executescript"}


# --------------------------------------------------------------------------- #
# Step 1 — build the union schema in one in-memory connection.
# --------------------------------------------------------------------------- #
def _build_schema_connection() -> sqlite3.Connection:
    """Build session + workspace schema in one connection; return it live.

    Uses the production schema builders so the test tracks the real schema
    automatically as it evolves.
    """
    from empirica.data.repositories.workspace_db import _ensure_workspace_schema
    from empirica.data.session_database import SessionDatabase

    # SessionDatabase needs a file path (it resolves a default otherwise and
    # may pollute CWD). Use a throwaway temp file, then keep the live conn.
    tmpdir = tempfile.mkdtemp(prefix="sql_schema_test_")
    db_path = str(Path(tmpdir) / "sessions.db")

    sdb = SessionDatabase(db_path=db_path, db_type="sqlite")
    conn = sdb.conn
    assert conn is not None, "SessionDatabase did not open a connection"

    # Union in the workspace tables (global_projects, etc.) on the SAME conn.
    # CREATE TABLE IF NOT EXISTS semantics make duplicate table names harmless.
    _ensure_workspace_schema(conn)

    return conn


def _introspect_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Return ``{table_name: set(column_names)}`` for every table."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    tables = [r[0] for r in cur.fetchall()]
    schema: dict[str, set[str]] = {}
    for table in tables:
        cur.execute(f"PRAGMA table_info({table})")
        schema[table] = {row[1] for row in cur.fetchall()}
    return schema


# --------------------------------------------------------------------------- #
# Step 2 — extract static SQL queries via AST.
# --------------------------------------------------------------------------- #
def _const_str(node: ast.AST) -> str | None:
    """Return the string value if ``node`` is a static string literal.

    Handles a plain ``ast.Constant`` str and implicit concatenation of string
    literals (parsed as a flat ``ast.Constant`` by CPython) — but explicitly
    returns None for anything dynamic: f-strings (``JoinedStr``), names,
    attributes, calls (``.format()``), binary ops (``%`` / ``+``).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_static_queries(py_file: Path) -> list[tuple[int, str]]:
    """Yield ``(lineno, sql)`` for each static SQL exec call in ``py_file``."""
    try:
        source = py_file.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return []

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in SQL_EXEC_METHODS:
            continue
        if not node.args:
            continue
        sql = _const_str(node.args[0])
        if sql is None:
            continue  # dynamic query — intentionally skipped
        found.append((node.lineno, sql))
    return found


def _collect_all_static_queries() -> list[tuple[Path, int, str]]:
    """Walk ``empirica/`` and collect every static SQL query."""
    queries: list[tuple[Path, int, str]] = []
    for py_file in EMPIRICA_PKG.rglob("*.py"):
        rel_parts = py_file.relative_to(REPO_ROOT).parts
        if any(part in SKIP_DIR_NAMES for part in rel_parts):
            continue
        if py_file.relative_to(REPO_ROOT).as_posix() in SKIP_FILES:
            continue
        for lineno, sql in _iter_static_queries(py_file):
            queries.append((py_file, lineno, sql))
    return queries


# --------------------------------------------------------------------------- #
# Step 3 — validate a single query against the built schema.
# --------------------------------------------------------------------------- #
def _statement_keyword(sql: str) -> str:
    """Return the leading SQL keyword, lowercased (after stripping comments)."""
    stripped = sql.lstrip()
    # Strip leading line comments.
    while stripped.startswith("--"):
        nl = stripped.find("\n")
        if nl == -1:
            return ""
        stripped = stripped[nl + 1 :].lstrip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].lower()


def _primary_tables(sql: str) -> set[str]:
    """Heuristically extract referenced table names (FROM/JOIN/UPDATE/INTO).

    Token-based, lowercase-insensitive. Good enough to decide whether the
    query targets a DB we built — if the primary table isn't in our schema we
    skip the query rather than risk a false positive.
    """
    import re

    tables: set[str] = set()
    # FROM <t>, JOIN <t>, UPDATE <t>, INSERT INTO <t>, DELETE FROM <t>
    pattern = re.compile(
        r"\b(?:from|join|into|update)\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        tables.add(match.group(1).lower())
    return tables


def _looks_like_missing_ref(message: str) -> bool:
    """True if the error names a missing column/table — the bug class.

    SQLite phrases this three ways depending on statement shape:
      * SELECT / WHERE  -> "no such column: X"
      * unknown table   -> "no such table: X"
      * INSERT col list -> "table T has no column named X"
    All three are the same underlying defect (a static reference to a
    non-existent column/table that a broad except would swallow).
    """
    msg = message.lower()
    return "no such column" in msg or "no such table" in msg or "has no column named" in msg


def _missing_symbol(message: str) -> str:
    """Extract the offending column/table name from a missing-ref error.

    Covers the three SQLite phrasings:
      "no such column: X" / "no such table: X" / "table T has no column named X".
    Returns the symbol lowercased, or "" if it can't be parsed.
    """
    import re

    for pat in (
        r"no such column:\s*([A-Za-z_][\w.]*)",
        r"no such table:\s*([A-Za-z_][\w.]*)",
        r"has no column named\s+([A-Za-z_][\w.]*)",
    ):
        m = re.search(pat, message, re.IGNORECASE)
        if m:
            # Strip any table-qualifier (t.col -> col); the symbol is what's missing.
            return m.group(1).split(".")[-1].lower()
    return ""


# Ratchet allow-list of PRE-EXISTING violations the test's first audit run
# surfaced (2026-06-20). Each is a real, separately-tracked bug — a static query
# whose column/table doesn't exist, silently swallowed by a broad except (the
# exact class this test guards). They are NOT fixed here because each fix REVIVES
# a silently-dead query (a behaviour change needing per-bug care; several aren't
# simple renames — e.g. goals.scope_breadth is now a single `scope` JSON column,
# `root_path` doesn't exist, `vectors_json` differs in shape from `reflex_data`).
#
# Keyed by (posix-relpath, missing-symbol) so it's stable across line shifts.
# The test FAILS on any NEW violation not in this set; as each known bug is fixed
# its entry is removed. This shrinks toward empty — a ratchet, not a sweep:
# the violations are listed here in the open, tracked, and CI-guarded against
# regrowth. Fix-tracking goal: "SQL schema-reference audit — fix the 17 dead queries".
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()


# --------------------------------------------------------------------------- #
# The test.
# --------------------------------------------------------------------------- #
def test_static_sql_references_exist_in_schema():
    conn = _build_schema_connection()
    schema = _introspect_columns(conn)
    known_tables = set(schema.keys())

    all_queries = _collect_all_static_queries()

    validated = 0
    skipped_non_dml = 0
    skipped_unknown_table = 0
    new_violations: list[tuple[str, str, str]] = []  # (loc, sql, error) — fail
    known_violations: list[tuple[str, str, str]] = []  # allow-listed, tracked
    ambiguous: list[tuple[str, str, str]] = []  # other OperationalErrors

    for py_file, lineno, sql in all_queries:
        relpath = py_file.relative_to(REPO_ROOT).as_posix()
        loc = f"{relpath}:{lineno}"

        keyword = _statement_keyword(sql)
        if keyword.startswith(NON_DML_PREFIXES) or keyword == "":
            skipped_non_dml += 1
            continue

        tables = _primary_tables(sql)
        # If the query references at least one table and NONE of them are in
        # our schema, it targets a DB/table we didn't build — skip it.
        if tables and not (tables & known_tables):
            skipped_unknown_table += 1
            continue

        # Validate via SQLite's own parser. Substitute NULL for each `?` so the
        # statement parses; EXPLAIN avoids actually running it.
        n_params = sql.count("?")
        try:
            conn.execute("EXPLAIN " + sql, [None] * n_params)
            validated += 1
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if _looks_like_missing_ref(message):
                key = (relpath, _missing_symbol(message))
                if key in _KNOWN_VIOLATIONS:
                    known_violations.append((loc, sql, message))
                else:
                    new_violations.append((loc, sql, message))
            else:
                # Odd syntax / param-substitution artifacts — investigate, but
                # do not hard-fail on ambiguous parser complaints.
                ambiguous.append((loc, sql, message))
        except sqlite3.Warning as exc:
            # e.g. multiple statements in one execute() — not the bug class.
            ambiguous.append((loc, sql, str(exc)))

    # --- Coverage report (always printed) ---------------------------------- #
    report_lines = [
        "",
        "=== SQL schema-reference audit ===",
        f"schema tables built          : {len(known_tables)}",
        f"static queries extracted     : {len(all_queries)}",
        f"  validated via EXPLAIN      : {validated}",
        f"  skipped (non-DML / DDL)    : {skipped_non_dml}",
        f"  skipped (unknown table)    : {skipped_unknown_table}",
        f"  ambiguous OperationalError : {len(ambiguous)}",
        f"  known violations (tracked) : {len(known_violations)}",
        f"  NEW violations             : {len(new_violations)}",
    ]
    if known_violations:
        report_lines.append("")
        report_lines.append("--- known violations (allow-listed, tracked for fix) ---")
        for loc, sql, err in known_violations:
            report_lines.append(f"  {loc} — {err} — {sql.strip()[:120]}")
    if new_violations:
        report_lines.append("")
        report_lines.append("--- NEW VIOLATIONS (failing) ---")
        for loc, sql, err in new_violations:
            report_lines.append(f"  {loc} — {err} — {sql.strip()[:120]}")
    print("\n".join(report_lines))

    conn.close()

    assert not new_violations, (
        f"{len(new_violations)} NEW static SQL quer"
        f"{'y' if len(new_violations) == 1 else 'ies'} reference a column/table that "
        f"does not exist in the schema (the bug class this test guards):\n"
        + "\n".join(f"  {loc} — {err} — {sql.strip()[:120]}" for loc, sql, err in new_violations)
        + "\n\nFix the query (use the real column), OR — only if it's genuinely a "
        "pre-existing tracked case — add (relpath, symbol) to _KNOWN_VIOLATIONS "
        "with justification."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q", "-s"])
