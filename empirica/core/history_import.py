"""Copy a practice's local history from an old store into another practice (goal f357ecf4, David 2026-11-26).

A practitioner who moves into a shared practice (Francisco: empirica-ai-consulting into empirica-consult) leaves artifacts and
calibration behind, because ``project-register --reconcile`` re-keys ONE store's ``project_id`` in place and never touches
``ai_id``, and calibration keys on ``ai_id``. This is the missing path. It COPIES, never moves, and it selects by the rule
the practice model already states: **artifacts accrue to the practice, calibration accrues to the practitioner**.

- **Artifacts** (findings, unknowns, dead ends, mistakes, assumptions, decisions, sources, goals, and the parts that hang off
  them) are copied when they belong to the old project, whoever wrote them. ``--types`` narrows them to some types.
- **Practitioner rows** (PREFLIGHT/CHECK/POSTFLIGHT, calibration, events, claims, snapshots) are copied only for the old
  practitioner's own sessions in that project (or with no project), and a session-keyed row tagged with another project
  stays. A store other practitioners also wrote into (one real store held six ai_ids) keeps their rows, so their calibration
  is never attributed to the new practice. ``include_calibration=False`` copies artifacts only.
- **Session rows** go for those sessions AND for every session a copied artifact was written in, keeping that session's own
  ai_id: readers join artifacts to ``sessions`` (``project-embed`` reads mistakes that way), so an artifact without its
  session would be copied and never found.
- Rows of other projects stay, and the preview counts them per table (``left_behind``).
- ``project_id`` and ``ai_id`` are rewritten only where they hold the old values. Ids are kept.
- An edge is copied only when both ends are copied or already in the target, so a type filter leaves none dangling.
- ``INSERT OR IGNORE ... EXCEPT``: a row already in the target is kept, never overwritten (``kept_in_target``), and a re-run
  adds nothing. A row the target refuses for another reason (a NOT NULL column the old store lacks, a unique clash) is
  counted under ``not_copied``, never as kept, and source columns the target lacks are named (``columns_not_in_target``).
- The preview runs the same inserts in a transaction and rolls back, so its counts are exact; it holds the target's write
  lock while it runs (seconds on a large store), and another writer waits up to its own busy timeout.
- ``apply`` backs up the target first with SQLite's online backup (which includes writes still in the WAL) and writes in one
  transaction. The old store is attached read-only. Derived models (code, concept graph) and git notes are not copied.
"""

from __future__ import annotations

import os
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

#: Tables joining two rows by id: copied when BOTH ends are copied or already in the target.
EDGE_TABLES: dict[str, tuple[str, str]] = {
    "artifact_edges": ("from_id", "to_id"),
    "knowledge_graph": ("source_id", "target_id"),
}

#: Tables whose ids an edge may point at: artifacts, the practice's other tables, and tasks.
_EDGE_ENDS = (*ARTIFACT_TABLES.values(), *sorted(PRACTICE_TABLES), "subtasks")

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

#: The old practitioner's own sessions in the old project (or with none): the sessions whose practitioner rows are copied.
_OWN_SESSIONS = (
    "(SELECT session_id FROM src.sessions WHERE ai_id = :old_ai AND (project_id = :old_pid OR project_id IS NULL))"
)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn: sqlite3.Connection, schema: str) -> dict[str, list[tuple[str, int, str]]]:
    """Each table's columns as (name, primary-key position (0 when not part of the key), declared type)."""
    names = [
        r[0]
        for r in conn.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        n: [(r[1], r[5], str(r[2] or "").upper()) for r in conn.execute(f"PRAGMA {schema}.table_info({_quote(n)})")]
        for n in names
    }


def _rowid_key(info: list[tuple[str, int, str]]) -> str | None:
    """The column of an auto-numbered INTEGER PRIMARY KEY (both stores count their own rows 1..N, so the copied rows take new
    numbers instead of clashing with unrelated rows: 1.17.20 second pass, reflexes 1-5 copied as 2 and reported 'kept')."""
    keys = [(c, t) for c, pos, t in info if pos]
    return keys[0][0] if len(keys) == 1 and keys[0][1] == "INTEGER" else None


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
                    parts.append(
                        f"{_quote(col)} IN (SELECT {_quote(pcol)} FROM src.{_quote(parent)} WHERE {parent_cond})"
                    )
            return " OR ".join(parts)
        if name in EDGE_TABLES:
            a, b = EDGE_TABLES[name]
            if a not in cols or b not in cols:
                return ""
            ends = self._edge_end_ids()
            return f"CAST({_quote(a)} AS TEXT) IN ({ends}) AND CAST({_quote(b)} AS TEXT) IN ({ends})" if ends else ""
        if name == "sessions":
            parts = [f"session_id IN ({anchors})" for anchors in [self._artifact_sessions()] if anchors]
            if self.calibration:
                parts.append("ai_id = :old_ai AND (project_id = :old_pid OR project_id IS NULL)")
            return " OR ".join(parts)
        if "project_id" in cols and "session_id" not in cols and "ai_id" not in cols:
            return "project_id = :old_pid"  # another table of the practice, not of one practitioner
        if not self.calibration:
            return ""
        if "session_id" in cols:
            own = f"session_id IN {_OWN_SESSIONS}"
            # A row of his own session that names another project belongs to that project (1.17.20 sweep).
            return f"{own} AND (project_id = :old_pid OR project_id IS NULL)" if "project_id" in cols else own
        if "ai_id" in cols:
            return "ai_id = :old_ai"
        return ""

    def _artifact_sessions(self) -> str:
        """The sessions copied artifacts were written in, so readers that join on sessions find them."""
        parts = [
            f"SELECT session_id FROM src.{_quote(t)} WHERE {self.condition(t)}"
            for t in sorted(self.artifacts | set(self.practice))
            if "session_id" in self.src.get(t, []) and self.condition(t)
        ]
        return " UNION ".join(parts)

    def _edge_end_ids(self) -> str:
        """Every id an edge may point at: rows copied now, and rows already in the target."""
        parts = []
        for table in _EDGE_ENDS:
            cond = self.condition(table)
            if cond and "id" in self.src.get(table, []):
                parts.append(f"SELECT CAST(id AS TEXT) FROM src.{_quote(table)} WHERE {cond}")
            if "id" in self.dst.get(table, []):
                parts.append(f"SELECT CAST(id AS TEXT) FROM main.{_quote(table)}")
        return " UNION ".join(parts)


_REWRITE = {
    "project_id": ("old_pid", "new_pid"),
    "source_project_id": ("old_pid", "new_pid"),
    "ai_id": ("old_ai", "new_ai"),
}


def _expr(c: str, prefix: str = "") -> str:
    """One copied column's value, with the old project and practitioner rewritten only where they hold the old values."""
    col = f"{prefix}{_quote(c)}"
    if c in _REWRITE:
        old, new = _REWRITE[c]
        return f"CASE WHEN {col} = :{old} THEN :{new} ELSE {col} END"
    return col


def _select_exprs(cols: list[str], prefix: str = "") -> str:
    return ", ".join(f"{_expr(c, prefix)} AS {_quote(c)}" for c in cols)


def _plan(
    conn: sqlite3.Connection, params: dict[str, str], types: set[str] | None, calibration: bool
) -> dict[str, Any]:
    src_info, dst_info = _tables(conn, "src"), _tables(conn, "main")
    src = {n: [c for c, _, _ in cols] for n, cols in src_info.items()}
    dst = {n: [c for c, _, _ in cols] for n, cols in dst_info.items()}
    selection = _Selection(src, dst, types, calibration)
    statements: dict[str, dict[str, str]] = {}
    skipped: list[str] = []
    missing: dict[str, int] = {}
    left: dict[str, int] = {}
    dropped_columns: dict[str, list[str]] = {}
    for name, cols in src.items():
        table = _quote(name)
        total = conn.execute(f"SELECT COUNT(*) FROM src.{table}").fetchone()[0]
        cond = selection.condition(name)
        if not cond:
            skipped.append(name)
            if total:
                left[name] = total
            continue
        chosen = conn.execute(f"SELECT COUNT(*) FROM src.{table} WHERE {cond}", params).fetchone()[0]
        if total - chosen:
            left[name] = total - chosen
        if name not in dst:  # reported, never dropped silently
            if chosen:
                missing[name] = chosen
            continue
        renumbered = _rowid_key(dst_info[name])
        common = [c for c in cols if c in dst[name] and c != renumbered]
        if chosen and any(c not in dst[name] for c in cols):
            dropped_columns[name] = [c for c in cols if c not in dst[name]]
        col_list = ", ".join(_quote(c) for c in common)
        prepare: list[str] = []
        if renumbered:
            # Row by row, not EXCEPT: without the id, two genuinely repeated records would collapse into one. A re-run
            # still adds nothing, because each row it would add is already there. Compared against an indexed TEMP copy
            # of the target's rows (a TEMP table: its name resolves before main's, and its index lands in temp too):
            # unindexed, it was quadratic (20 s at 10k x 10k, 1.17.20 third pass).
            snap = _quote(f"_hi_snapshot_{len(statements)}")
            prepare = [  # the connection is fresh per call, so no earlier snapshot exists and none is dropped
                f"CREATE TEMP TABLE {snap} AS SELECT {col_list} FROM main.{table}",
                f"CREATE INDEX {_quote(f'_hi_ix_{len(statements)}')} ON {snap} ({col_list})",
            ]
            same = " AND ".join(f"m.{_quote(c)} IS {_expr(c, 's.')}" for c in common) or "1"
            new_rows = (
                f"SELECT {_select_exprs(common, 's.')} FROM src.{table} AS s WHERE ({cond}) "
                f"AND NOT EXISTS (SELECT 1 FROM {snap} AS m WHERE {same})"
            )
        else:
            new_rows = f"SELECT {_select_exprs(common)} FROM src.{table} WHERE {cond} EXCEPT SELECT {col_list} FROM main.{table}"
        pk = [c for c, pos, _ in sorted(dst_info[name], key=lambda cp: cp[1]) if pos and c in common]
        present = (  # `=`, not IS: a NULL key matches nothing, as the insert treats it
            f"SELECT COUNT(*) FROM ({new_rows}) AS n WHERE EXISTS (SELECT 1 FROM main.{table} AS m WHERE "
            + " AND ".join(f"m.{_quote(c)} = n.{_quote(c)}" for c in pk)
            + ")"
            if pk
            else ""
        )
        statements[name] = {
            "prepare": prepare,
            "insert": f"INSERT OR IGNORE INTO main.{table} ({col_list}) {new_rows}",
            "count": f"SELECT COUNT(*) FROM ({new_rows})",
            "present": present,
        }
    return {
        "statements": statements,
        "skipped": sorted(skipped),
        "missing_in_target": missing,
        "left_behind": left,
        "columns_not_in_target": dropped_columns,
    }


def _backup(target_db: Path) -> Path:
    """A consistent copy of the target, WAL included (a file copy of sessions.db alone misses recent commits). Called while
    the copy holds the target's write lock, so nothing can change between the backup and the inserts, and no other writer
    can hold the lock the backup would wait on."""
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.time_ns() % 1_000_000:06d}"  # two runs never collide
    backup = target_db.with_name(f"{target_db.name}.bak-history-{stamp}")
    live = sqlite3.connect(str(target_db), timeout=10)
    try:
        copy = sqlite3.connect(str(backup))
        try:
            live.backup(copy)
        finally:
            copy.close()
    finally:
        live.close()
    return backup


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
    conn = sqlite3.connect(str(target_db), isolation_level=None)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (source_db.as_uri() + "?mode=ro",))
        plan = _plan(conn, params, types, include_calibration)
        counts: dict[str, int] = {}
        kept: dict[str, int] = {}
        not_copied: dict[str, int] = {}
        conn.execute("PRAGMA busy_timeout = 10000")  # wait up to 10 s for a live writer, then fail loudly
        conn.execute("BEGIN IMMEDIATE")
        try:
            if apply:  # under the write lock: current, and only made when the copy can actually run
                backup = _backup(target_db)
            for name, sql in plan["statements"].items():
                for step in sql["prepare"]:
                    conn.execute(step)
                candidates = conn.execute(sql["count"], params).fetchone()[0]
                already = conn.execute(sql["present"], params).fetchone()[0] if sql["present"] and candidates else 0
                counts[name] = conn.execute(sql["insert"], params).rowcount
                if already:
                    kept[name] = already
                refused = candidates - counts[name] - already
                if refused > 0:
                    not_copied[name] = refused
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
        "not_copied": not_copied,
        "columns_not_in_target": plan["columns_not_in_target"],
        "left_behind": plan["left_behind"],
        "skipped": plan["skipped"],
        "missing_in_target": plan["missing_in_target"],
        "backup": str(backup) if backup else None,
    }
