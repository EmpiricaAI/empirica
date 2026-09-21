"""Every table the code queries is one the schema creates and does not later drop.

Found 2026-09-21 by subtracting the live store's tables from every name the code
reads or writes. Five dropped or never-created tables were still being queried:

- session_dead_ends (dropped 2026-02-03): prevention detection, blindspot regret,
  the prevention oracle. In detection the raise followed an UPDATE with no
  rollback and no close, which held sessions.db's write lock for the rest of
  POSTFLIGHT and dropped ~90% of grounded verifications as "database is locked".
- project_goals, project_mistakes (never existed): memory_manager, so MEMORY.md's
  EPISTEMIC FOCUS block only ever listed findings.
- session_findings, session_mistakes (dropped 2026-02-03): session-rollup and
  `query mistakes --scope session`.

Every one was silent: a missing table is caught and logged at debug, or treated
as quiet by design, and hand-built test fixtures created the dropped tables, so
the suite was green exactly where it was blind.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "empirica"

_SQL_TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z][a-z0-9_]*)\b")
_SQL_LINE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b")
_CTE = re.compile(r"\b(?:WITH|,)\s*([a-z][a-z0-9_]*)\s+AS\s*\(", re.I)
#: SQLite built-ins and table-valued functions that are not schema tables.
_BUILTIN = {"sqlite_master", "sqlite_schema", "pragma_table_info", "json_each", "json_tree", "sqlite_sequence"}


def _inventory() -> tuple[set[str], set[str]]:
    created: set[str] = set()
    dropped: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(errors="replace")
        created |= set(re.findall(r"CREATE (?:VIRTUAL )?TABLE(?: IF NOT EXISTS)? [\"`]?(\w+)", text))
        created |= set(re.findall(r"CREATE (?:TEMP(?:ORARY)? )?VIEW(?: IF NOT EXISTS)? [\"`]?(\w+)", text))
        dropped |= set(re.findall(r"DROP TABLE(?: IF EXISTS)? [\"`]?(\w+)", text))
    migrations = (SRC / "data" / "migrations" / "migrations.py").read_text()
    for block in re.findall(r"(?:tables_to_drop|LEGACY_TABLES_\d+)\s*=\s*[\[(](.*?)[\])]", migrations, re.S):
        dropped |= set(re.findall(r'"(\w+)"', block))
    # A table dropped by one migration and re-created by a later one is live.
    return created, dropped - _recreated_after_drop(migrations, dropped)


def _recreated_after_drop(migrations: str, dropped: set[str]) -> set[str]:
    alive = set()
    for name in dropped:
        last_drop = max(
            (m.start() for m in re.finditer(rf"DROP TABLE(?: IF EXISTS)? {name}\b|\"{name}\"", migrations)), default=-1
        )
        last_create = max(
            (m.start() for m in re.finditer(rf"CREATE TABLE(?: IF NOT EXISTS)? {name}\b", migrations)), default=-1
        )
        if last_create > last_drop:
            alive.add(name)
    return alive


def _queried() -> dict[str, list[str]]:
    sites: dict[str, list[str]] = {}
    for path in SRC.rglob("*.py"):
        rel = str(path.relative_to(REPO))
        if "/migrations/" in rel or "/tests/" in rel:
            continue
        text = path.read_text(errors="replace")
        ctes = {m.lower() for m in _CTE.findall(text)}
        for i, line in enumerate(text.splitlines(), 1):
            if not _SQL_LINE.search(line) and "FROM " not in line:
                continue
            for name in _SQL_TABLE.findall(line):
                if name in _BUILTIN or name in ctes:
                    continue
                sites.setdefault(name, []).append(f"{rel}:{i}")
    return sites


def test_positive_control_the_inventory_sees_live_and_dropped_tables():
    created, dropped = _inventory()
    assert {"project_dead_ends", "goals", "mistakes_made", "project_findings"} <= created - dropped
    assert {"session_dead_ends", "session_findings", "session_mistakes"} <= dropped


def test_no_code_queries_a_dropped_table():
    _, dropped = _inventory()
    offenders = {name: sites for name, sites in _queried().items() if name in dropped}
    assert offenders == {}, "\n".join(f"{n}: {s[:4]}" for n, s in sorted(offenders.items()))


def test_no_code_queries_a_table_that_was_never_created():
    """Restricted to names that LOOK like schema tables, so prose after FROM in a
    comment or an f-string fragment is not a finding."""
    created, _ = _inventory()
    shaped = re.compile(
        r"^(project|session|global|entity|epistemic|transaction|prevention|blindspot|grounded)_\w+$|_(events|logs|edges|claims|beliefs)$"
    )
    offenders = {n: s for n, s in _queried().items() if shaped.search(n) and n not in created}
    assert offenders == {}, "\n".join(f"{n}: {s[:4]}" for n, s in sorted(offenders.items()))
