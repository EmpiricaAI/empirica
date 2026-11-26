"""Copy a practice's local history from an old store into another practice (goal f357ecf4, David 2026-11-26).

A practitioner who moves into a shared practice (Francisco: empirica-ai-consulting into empirica-consult) leaves artifacts and
calibration behind, because ``project-register --reconcile`` re-keys ONE store's ``project_id`` in place and never touches
``ai_id``, and calibration keys on ``ai_id``. This is the missing path. It COPIES, never moves, and it selects by the rule
the practice model already states: **artifacts accrue to the practice, calibration accrues to the practitioner**.

- **Artifacts** (findings, unknowns, dead ends, mistakes, assumptions, decisions, sources, goals, and the parts that hang off
  them) are copied when they belong to the old project, whoever wrote them. ``--types`` narrows them to some types.
- **Practitioner rows** (sessions, PREFLIGHT/CHECK/POSTFLIGHT, calibration, events, claims, snapshots) are copied only for the
  old practitioner's own sessions in that project (or with no project). A store other practitioners also wrote into (one real
  store held six ai_ids) keeps their rows behind, so their calibration is never attributed to the new practice.
  ``include_calibration=False`` copies artifacts only.
- Rows of other projects in the same store are left behind, and the preview counts them per table (``left_behind``).
- ``project_id`` and ``ai_id`` are rewritten only where they hold the old values. Ids are kept.
- An edge is copied only when both of its ends are copied or already in the target, so a type filter leaves no dangling edge.
- ``INSERT OR IGNORE ... EXCEPT``: a row already in the target is kept, never overwritten, and a re-run adds nothing. The
  preview runs the same inserts and rolls them back, so its counts are exact.
- ``apply`` backs up the target first and writes in one transaction. The old store is attached read-only. Derived models
  (code, concept graph) and git notes are not copied.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

#: The artifact types and their tables. ``--types`` takes these names.
ARTIFACT_TABLES: dict[str, str] = {
    "finding": "project_findings",
    "unknown": "project_unknowns",
    "dead_end": "project_dead_ends",
    "mistake": "mistakes_made",
    "assumption": "assumptions",
    "decision": "decisions",
    "source": "epistemic_sources",
    "goal": "goals",
}

#: Other tables of the practice (not of one practitioner), copied by project with the artifacts when no type filter is given.
PRACTICE_TABLES = frozenset({"falsifiers", "lessons", "beads", "project_handoffs", "engagements", "client_projects"})

#: Rows that hang off a copied row: (column, parent table, parent column).
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

#: Tables joining two artifacts by id: copied when BOTH ends are copied or already in the target.
EDGE_TABLES: dict[str, tuple[str, str]] = {
    "artifact_edges": ("from_id", "to_id"),
    "knowledge_graph": ("source_id", "target_id"),
}

#: Never copied: the target's own project and catalogue rows, learned patterns, and models DERIVED from the code and
#: artifacts, which the target rebuilds (copying them would graft the old repo's model onto the new one: 418k concept edges
#: in a real preview).
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

#: The old practitioner's own sessions in the old project (or with none): the only sessions whose rows are copied.
_SESSIONS = (
    "(SELECT session_id FROM src.sessions WHERE ai_id = :old_ai AND (project_id = :old_pid OR project_id IS NULL))"
)


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


class _Selection:
    """Which rows of each source table are copied, as SQL conditions over ``src``."""

    def __init__(self, src: dict[str, list[str]], dst: dict[str, list[str]], types: set[str] | None, calibration: bool):
        self.src, self.dst, self.calibration = src, dst, calibration
        self.artifacts = {ARTIFACT_TABLES[t] for t in (types if types is not None else ARTIFACT_TABLES)}
        self.practice = PRACTICE_TABLES if types is None else frozenset()

    def condition(self, name: str, depth: int = 0) -> str:
        """The rows of ``name`` that are copied, or '' when none are."""
        cols = self.src.get(name)
        if cols is None or name in SKIP_TABLES or depth > 4:
            return ""
        if name in self.artifacts or name in self.practice:
            return "project_id = :old_pid" if "project_id" in cols else ""
        if name in ARTIFACT_TABLES.values() or name in PRACTICE_TABLES:
            return ""  # an artifact table outside the type filter
        if name in DEPENDENTS:
            parts = []
            for col, parent, pcol in DEPENDENTS[name]:
                parent_cond = self.condition(parent, depth + 1)
                if col in cols and parent_cond:
                    parts.append(f'{_quote(col)} IN (SELECT {_quote(pcol)} FROM src."{parent}" WHERE {parent_cond})')
            return " OR ".join(parts)
        if name in EDGE_TABLES:
            a, b = EDGE_TABLES[name]
            if a not in cols or b not in cols:
                return ""
            ends = self._artifact_ids()
            return f"CAST({_quote(a)} AS TEXT) IN ({ends}) AND CAST({_quote(b)} AS TEXT) IN ({ends})" if ends else ""
        if "project_id" in cols and "session_id" not in cols and "ai_id" not in cols:
            return "project_id = :old_pid"  # another table of the practice, not of one practitioner
        if not self.calibration:
            return ""
        if name == "sessions":
            return "ai_id = :old_ai AND (project_id = :old_pid OR project_id IS NULL)"
        if "session_id" in cols:
            return f"session_id IN {_SESSIONS}"
        if "ai_id" in cols:
            return "ai_id = :old_ai"
        return ""

    def _artifact_ids(self) -> str:
        """Every id an edge may point at: copied artifacts and artifacts already in the target."""
        parts = []
        for table in sorted(ARTIFACT_TABLES.values()):  # copied now (the selected types) or already there (any type)
            if table in self.artifacts and "id" in self.src.get(table, []):
                parts.append(f'SELECT CAST(id AS TEXT) FROM src."{table}" WHERE {self.condition(table)}')
            if "id" in self.dst.get(table, []):
                parts.append(f'SELECT CAST(id AS TEXT) FROM main."{table}"')
        return " UNION ".join(parts)


def _select_exprs(cols: list[str]) -> str:
    """The copied columns, with the old project and practitioner rewritten only where they hold the old values."""
    rewrite = {
        "project_id": ("old_pid", "new_pid"),
        "source_project_id": ("old_pid", "new_pid"),
        "ai_id": ("old_ai", "new_ai"),
    }
    out = []
    for c in cols:
        if c in rewrite:
            old, new = rewrite[c]
            out.append(f"CASE WHEN {_quote(c)} = :{old} THEN :{new} ELSE {_quote(c)} END")
        else:
            out.append(_quote(c))
    return ", ".join(out)


def _plan(
    conn: sqlite3.Connection, params: dict[str, str], types: set[str] | None, calibration: bool
) -> dict[str, Any]:
    src, dst = _tables(conn, "src"), _tables(conn, "main")
    selection = _Selection(src, dst, types, calibration)
    statements: dict[str, tuple[str, str]] = {}
    skipped: list[str] = []
    missing: dict[str, int] = {}
    left: dict[str, int] = {}
    for name, cols in src.items():
        total = conn.execute(f'SELECT COUNT(*) FROM src."{name}"').fetchone()[0]
        cond = selection.condition(name)
        if not cond:
            skipped.append(name)
            if total:
                left[name] = total
            continue
        chosen = conn.execute(f'SELECT COUNT(*) FROM src."{name}" WHERE {cond}', params).fetchone()[0]
        if total - chosen:
            left[name] = total - chosen
        if name not in dst:  # reported, never dropped silently
            if chosen:
                missing[name] = chosen
            continue
        common = [c for c in cols if c in dst[name]]
        col_list = ", ".join(_quote(c) for c in common)
        new_rows = (
            f'SELECT {_select_exprs(common)} FROM src."{name}" WHERE {cond} EXCEPT SELECT {col_list} FROM main."{name}"'
        )
        statements[name] = (
            f'INSERT OR IGNORE INTO main."{name}" ({col_list}) {new_rows}',
            f"SELECT COUNT(*) FROM ({new_rows})",
        )
    return {"statements": statements, "skipped": sorted(skipped), "missing_in_target": missing, "left_behind": left}


def copy_history(
    source_db: str | Path,
    target_db: str | Path,
    *,
    old_project_id: str,
    new_project_id: str,
    old_ai_id: str,
    new_ai_id: str,
    apply: bool = False,
    types: set[str] | None = None,
    include_calibration: bool = True,
) -> dict[str, Any]:
    """Preview (default) or apply the copy. Returns per-table counts of rows that are (or were) new to the target."""
    source_db, target_db = Path(source_db).resolve(), Path(target_db).resolve()
    if source_db == target_db:
        raise ValueError("source and target are the same store; nothing to copy")
    if types is not None:
        unknown = sorted(set(types) - set(ARTIFACT_TABLES))
        if unknown:
            raise ValueError(f"unknown artifact type(s) {unknown}; choose from {sorted(ARTIFACT_TABLES)}")
    for path in (source_db, target_db):
        if not path.exists():
            raise FileNotFoundError(f"no store at {path}")
    params = {"old_pid": old_project_id, "new_pid": new_project_id, "old_ai": old_ai_id, "new_ai": new_ai_id}
    backup = None
    if apply:
        backup = target_db.with_name(f"{target_db.name}.bak-history-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(target_db, backup)
    conn = sqlite3.connect(str(target_db), isolation_level=None)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (source_db.as_uri() + "?mode=ro",))
        plan = _plan(conn, params, types, include_calibration)
        counts: dict[str, int] = {}
        kept: dict[str, int] = {}
        # busy_timeout lets a live hook's write wait instead of failing; the preview rolls back the same inserts.
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
        "types": sorted(types) if types is not None else "all",
        "calibration": include_calibration,
        "tables": counts,
        "kept_in_target": kept,
        "left_behind": plan["left_behind"],
        "skipped": plan["skipped"],
        "missing_in_target": plan["missing_in_target"],
        "backup": str(backup) if backup else None,
    }
