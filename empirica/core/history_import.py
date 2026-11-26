"""Copy one practitioner's local history from an old practice's store into another (goal f357ecf4, David 2026-11-26).

A practitioner who moves into a shared practice (Francisco: empirica-ai-consulting into empirica-consult) leaves artifacts and
calibration behind, because ``project-register --reconcile`` re-keys ONE store's ``project_id`` in place and never touches
``ai_id``, and calibration keys on ``ai_id``. This is the missing path, and it COPIES, never moves:

- rows of the old project (every table with ``project_id``), of the old practitioner (tables with ``ai_id``), of their sessions
  (tables keyed by ``session_id``), and of what hangs off those rows (tasks, success criteria, dependencies, edges, lesson
  parts, cascades' beliefs);
- ``project_id`` and ``ai_id`` are rewritten only where they equal the old values, so a subagent's or a peer's id stays;
- ids are kept, so an edge still joins its two ends and Cortex's own re-key names the same rows;
- ``INSERT OR IGNORE ... EXCEPT``: a row already in the target is never overwritten, and a re-run adds nothing;
- preview by default; ``apply`` backs up the target file first and writes in one transaction; the old store is opened
  read-only and never written. Git notes are not touched: pushing them into a shared repo needs its own ruling.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

#: Tables never copied: the project's own row and catalogue tables belong to the target; learned patterns are not one
#: practitioner's history; and the code model and concept graph are DERIVED from the code and artifacts (rebuilt by the
#: target), so copying them would graft the old repo's model onto the new one (418k concept edges in a real preview).
SKIP_TABLES = frozenset(
    {
        "projects",
        "clients",
        "schema_migrations",
        "trajectory_patterns",
        "project_relationships",
        "codebase_entities",
        "codebase_relationships",
        "concept_nodes",
        "concept_edges",
        "concept_clusters",
    }
)

#: Keyless tables, copied when the row they hang off is copied: (column, source table, source column).
DEPENDENTS: dict[str, list[tuple[str, str, str]]] = {
    "subtasks": [("goal_id", "goals", "id")],
    "success_criteria": [("goal_id", "goals", "id")],
    "goal_dependencies": [("goal_id", "goals", "id")],
    "task_decompositions": [("goal_id", "goals", "id")],
    "subtask_dependencies": [("subtask_id", "subtasks", "id")],
    "lesson_steps": [("lesson_id", "lessons", "id")],
    "lesson_corrections": [("lesson_id", "lessons", "id")],
    "lesson_prerequisites": [("lesson_id", "lessons", "id")],
    "lesson_epistemic_deltas": [("lesson_id", "lessons", "id")],
    "bayesian_beliefs": [("cascade_id", "cascades", "cascade_id")],
    "cross_project_finding_links": [("finding_id", "project_findings", "id")],
}

#: Keyless tables that join two artifacts by id: copied when either end is a copied row.
EDGE_TABLES: dict[str, tuple[str, str]] = {
    "artifact_edges": ("from_id", "to_id"),
    "knowledge_graph": ("source_id", "target_id"),
}


def _tables(conn: sqlite3.Connection, schema: str) -> dict[str, list[str]]:
    names = [
        r[0]
        for r in conn.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {n: [r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{n}")')] for n in names}


def _quote(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def _session_set() -> str:
    return "(SELECT session_id FROM src.sessions WHERE ai_id = :old_ai OR project_id = :old_pid)"


def _condition(name: str, cols: list[str]) -> str:
    """Which rows of a keyed table belong to the old practice and practitioner ('' when the table carries no key)."""
    if name == "sessions":
        return "ai_id = :old_ai OR project_id = :old_pid"
    if "project_id" in cols:
        return "project_id = :old_pid"
    if "ai_id" in cols:
        return f"ai_id = :old_ai OR session_id IN {_session_set()}" if "session_id" in cols else "ai_id = :old_ai"
    if "session_id" in cols:
        return f"session_id IN {_session_set()}"
    return ""


def _rows_of(name: str, src_tables: dict[str, list[str]], keyed: list[str], depth: int = 0) -> str:
    """The SQL condition for the rows of ``name`` that are copied, or '' when none are: keyed tables by their key, a
    dependent by its parent's copied rows (recursively: subtask_dependencies -> subtasks -> goals), an edge table by
    either end being a copied row."""
    if name not in src_tables or depth > 4:
        return ""
    cols = src_tables[name]
    if name in DEPENDENTS:
        parts = []
        for col, parent, pcol in DEPENDENTS[name]:
            parent_cond = _rows_of(parent, src_tables, keyed, depth + 1)
            if col in cols and parent_cond:
                parts.append(f'{_quote(col)} IN (SELECT {_quote(pcol)} FROM src."{parent}" WHERE {parent_cond})')
        return " OR ".join(parts)
    if name in EDGE_TABLES:
        a, b = EDGE_TABLES[name]
        ids = " UNION ".join(
            f'SELECT CAST(id AS TEXT) FROM src."{t}" WHERE {_condition(t, src_tables[t])}'
            for t in keyed
            if "id" in src_tables[t]
        )
        if not ids or a not in cols or b not in cols:
            return ""
        return f"CAST({_quote(a)} AS TEXT) IN ({ids}) OR CAST({_quote(b)} AS TEXT) IN ({ids})"
    return _condition(name, cols)


def _keyed(cols: list[str]) -> bool:
    return any(c in cols for c in ("project_id", "ai_id", "session_id"))


def _select_exprs(cols: list[str]) -> str:
    """The copied columns, with the old project and practitioner rewritten only where they hold the old values."""
    rewrite = {
        "project_id": ":old_pid",
        "source_project_id": ":old_pid",
        "ai_id": ":old_ai",
    }
    new = {":old_pid": ":new_pid", ":old_ai": ":new_ai"}
    out = []
    for c in cols:
        if c in rewrite:
            old = rewrite[c]
            out.append(f"CASE WHEN {_quote(c)} = {old} THEN {new[old]} ELSE {_quote(c)} END")
        else:
            out.append(_quote(c))
    return ", ".join(out)


def _plan(conn: sqlite3.Connection, params: dict[str, str]) -> dict[str, Any]:
    src_tables, dst_tables = _tables(conn, "src"), _tables(conn, "main")
    keyed = [n for n, cols in src_tables.items() if n not in SKIP_TABLES and _keyed(cols)]
    statements: dict[str, tuple[str, str]] = {}  # table -> (insert sql, count sql)
    skipped: list[str] = []
    missing: dict[str, int] = {}
    for name, cols in src_tables.items():
        cond = "" if name in SKIP_TABLES else _rows_of(name, src_tables, keyed)
        if not cond:
            skipped.append(name)
            continue
        if name not in dst_tables:  # reported, never dropped silently
            missing[name] = conn.execute(f'SELECT COUNT(*) FROM src."{name}" WHERE {cond}', params).fetchone()[0]
            continue
        common = [c for c in cols if c in dst_tables[name]]
        col_list = ", ".join(_quote(c) for c in common)
        new_rows = (
            f'SELECT {_select_exprs(common)} FROM src."{name}" WHERE {cond} EXCEPT SELECT {col_list} FROM main."{name}"'
        )
        statements[name] = (
            f'INSERT OR IGNORE INTO main."{name}" ({col_list}) {new_rows}',
            f"SELECT COUNT(*) FROM ({new_rows})",
        )
    return {"statements": statements, "skipped": sorted(skipped), "missing_in_target": missing}


def copy_history(
    source_db: str | Path,
    target_db: str | Path,
    *,
    old_project_id: str,
    new_project_id: str,
    old_ai_id: str,
    new_ai_id: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview (default) or apply the copy. Returns per-table counts of rows that are (or were) new to the target."""
    source_db, target_db = Path(source_db).resolve(), Path(target_db).resolve()
    if source_db == target_db:
        raise ValueError("source and target are the same store; nothing to copy")
    if not source_db.exists():
        raise FileNotFoundError(f"no store at {source_db}")
    if not target_db.exists():
        raise FileNotFoundError(
            f"no store at {target_db}; open the target practice once (empirica session-create) first"
        )
    params = {"old_pid": old_project_id, "new_pid": new_project_id, "old_ai": old_ai_id, "new_ai": new_ai_id}
    backup = None
    if apply:
        backup = target_db.with_name(f"{target_db.name}.bak-history-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(target_db, backup)
    conn = sqlite3.connect(str(target_db), isolation_level=None)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (source_db.as_uri() + "?mode=ro",))
        plan = _plan(conn, params)
        counts: dict[str, int] = {}
        kept: dict[str, int] = {}
        # The preview runs the same inserts and rolls them back, so its counts are exact: a row whose id is already in the
        # target with different content is kept there (INSERT OR IGNORE) and reported under kept_in_target, never counted
        # as copied. busy_timeout lets a live hook's write wait instead of failing.
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for name, (insert, count) in plan["statements"].items():
                candidates = conn.execute(count, params).fetchone()[0]
                counts[name] = conn.execute(insert, params).rowcount
                if candidates > counts[name]:
                    kept[name] = candidates - counts[name]
            conn.execute("COMMIT" if apply else "ROLLBACK")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return {
        "applied": apply,
        "source": str(source_db),
        "target": str(target_db),
        "tables": counts,
        "skipped": plan["skipped"],
        "missing_in_target": plan["missing_in_target"],
        "kept_in_target": kept,
        "backup": str(backup) if backup else None,
    }
