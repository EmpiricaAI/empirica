"""
Codebase Model Schema

Database tables for temporal entity tracking and relationships.

The fact and constraint tables were removed (migration 077, David's dead-code ruling 2026-10-26): nothing ever wrote them.
All tables live in sessions.db alongside other Empirica data for transactional consistency.
"""

SCHEMAS = [
    # Codebase entities: functions, classes, APIs, files
    """
    CREATE TABLE IF NOT EXISTS codebase_entities (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        name TEXT NOT NULL,
        file_path TEXT,
        signature TEXT,
        first_seen REAL NOT NULL,
        last_seen REAL,
        project_id TEXT,
        session_id TEXT,
        metadata TEXT,

        FOREIGN KEY (project_id) REFERENCES projects(id),
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_codebase_entities_type ON codebase_entities(entity_type)",
    "CREATE INDEX IF NOT EXISTS idx_codebase_entities_file ON codebase_entities(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_codebase_entities_name ON codebase_entities(name)",
    "CREATE INDEX IF NOT EXISTS idx_codebase_entities_project ON codebase_entities(project_id)",
    # Entity relationships: directional links (calls, imports, depends_on)
    """
    CREATE TABLE IF NOT EXISTS codebase_relationships (
        id TEXT PRIMARY KEY,
        source_entity_id TEXT NOT NULL,
        target_entity_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        weight REAL DEFAULT 1.0,
        first_seen REAL NOT NULL,
        last_seen REAL NOT NULL,
        evidence_count INTEGER DEFAULT 1,
        project_id TEXT,

        FOREIGN KEY (source_entity_id) REFERENCES codebase_entities(id),
        FOREIGN KEY (target_entity_id) REFERENCES codebase_entities(id),
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_codebase_rel_source ON codebase_relationships(source_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_codebase_rel_target ON codebase_relationships(target_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_codebase_rel_type ON codebase_relationships(relationship_type)",
    "CREATE INDEX IF NOT EXISTS idx_codebase_rel_project ON codebase_relationships(project_id)",
]
