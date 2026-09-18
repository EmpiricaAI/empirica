"""Tables that repositories used to create lazily, now in the registry.

Seven tables were created by `CREATE TABLE IF NOT EXISTS` inside the module
that first needed them — goals/tasks repositories, the epistemic bus, the
context budget, calibration insights — and by nothing else. A fresh install
got them only once that code path ran; a long-lived database had them from
the start. Measured 2026-09-18 with `scripts/gen_schema_doc.py --diff-db`: 82
tables live against 64 in the registry, and these seven (holding 8,017
calibration insights, 5,171 events, 2,888 success criteria on one practice)
were the live-only tables current code actually writes.

The DDL lives HERE, once. The lazy creators execute these same strings, so
the registry and the runtime cannot disagree about a shape; a fresh install
materialises them with everything else, and the schema doc generator sees
them.

`CREATE TABLE IF NOT EXISTS` on a database that already has the table is a
no-op, as before — this changes nothing for an existing store.
"""

from __future__ import annotations

#: name -> DDL. Order matters only for foreign keys, which reference tables
#: created earlier in ALL_SCHEMAS (goals, subtasks).
LAZY_TABLE_DDL: dict[str, str] = {
    "success_criteria": """
        CREATE TABLE IF NOT EXISTS success_criteria (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            description TEXT NOT NULL,
            validation_method TEXT NOT NULL,
            threshold REAL,
            is_required BOOLEAN DEFAULT 1,
            is_met BOOLEAN DEFAULT 0,
            FOREIGN KEY (goal_id) REFERENCES goals(id)
        )
    """,
    "goal_dependencies": """
        CREATE TABLE IF NOT EXISTS goal_dependencies (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            depends_on_goal_id TEXT NOT NULL,
            dependency_type TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (goal_id) REFERENCES goals(id),
            FOREIGN KEY (depends_on_goal_id) REFERENCES goals(id)
        )
    """,
    "subtask_dependencies": """
        CREATE TABLE IF NOT EXISTS subtask_dependencies (
            subtask_id TEXT NOT NULL,
            depends_on_subtask_id TEXT NOT NULL,
            PRIMARY KEY (subtask_id, depends_on_subtask_id),
            FOREIGN KEY (subtask_id) REFERENCES subtasks(id),
            FOREIGN KEY (depends_on_subtask_id) REFERENCES subtasks(id)
        )
    """,
    "task_decompositions": """
        CREATE TABLE IF NOT EXISTS task_decompositions (
            goal_id TEXT PRIMARY KEY,
            total_estimated_tokens INTEGER,
            created_timestamp REAL NOT NULL,
            decomposition_data TEXT NOT NULL,
            FOREIGN KEY (goal_id) REFERENCES goals(id)
        )
    """,
    "epistemic_events": """
        CREATE TABLE IF NOT EXISTS epistemic_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            agent_id TEXT,
            data_json TEXT,
            timestamp REAL NOT NULL,
            node_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """,
    "context_budget_state": """
        CREATE TABLE IF NOT EXISTS context_budget_state (
            session_id TEXT PRIMARY KEY,
            node_id TEXT,
            inventory_json TEXT,
            thresholds_json TEXT,
            page_faults INTEGER,
            evictions INTEGER,
            created_at REAL,
            updated_at REAL
        )
    """,
    "calibration_insights": """
        CREATE TABLE IF NOT EXISTS calibration_insights (
            insight_id TEXT PRIMARY KEY,
            session_id TEXT,
            transaction_id TEXT,
            vector TEXT,
            phase TEXT,
            pattern TEXT,
            severity REAL,
            description TEXT,
            suggestion TEXT,
            evidence_sources TEXT,
            observation_count INTEGER,
            acted_on BOOLEAN DEFAULT FALSE,
            created_at REAL
        )
    """,
}

SCHEMAS: list[str] = list(LAZY_TABLE_DDL.values())

__all__ = ["LAZY_TABLE_DDL", "SCHEMAS"]
