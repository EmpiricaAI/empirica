"""
Epistemic Schema

Database table schemas for epistemic-related tables.
Extracted from SessionDatabase._create_tables()
"""

SCHEMAS = [
    # Schema 1
    """
    CREATE TABLE IF NOT EXISTS bayesian_beliefs (
                    belief_id TEXT PRIMARY KEY,
                    cascade_id TEXT NOT NULL,
                    vector_name TEXT NOT NULL,

                    mean REAL NOT NULL,
                    variance REAL NOT NULL,
                    evidence_count INTEGER DEFAULT 0,

                    prior_mean REAL NOT NULL,
                    prior_variance REAL NOT NULL,

                    last_updated TIMESTAMP,

                    FOREIGN KEY (cascade_id) REFERENCES cascades(cascade_id)
                )
    """,

    # Schema 2
    """
    CREATE TABLE IF NOT EXISTS epistemic_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    ai_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,

                    cascade_phase TEXT,
                    cascade_id TEXT,

                    vectors TEXT NOT NULL,
                    delta TEXT,
                    previous_snapshot_id TEXT,

                    context_summary TEXT,
                    evidence_refs TEXT,
                    db_session_ref TEXT,

                    domain_vectors TEXT,

                    original_context_tokens INTEGER DEFAULT 0,
                    snapshot_tokens INTEGER DEFAULT 0,
                    compression_ratio REAL DEFAULT 0.0,

                    information_loss_estimate REAL DEFAULT 0.0,
                    fidelity_score REAL DEFAULT 1.0,

                    transfer_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
                    FOREIGN KEY (cascade_id) REFERENCES cascades(cascade_id),
                    FOREIGN KEY (previous_snapshot_id) REFERENCES epistemic_snapshots(snapshot_id)
                )
    """,

    # Schema 3
    """
    CREATE TABLE IF NOT EXISTS reflexes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    cascade_id TEXT,
                    phase TEXT NOT NULL,
                    round INTEGER DEFAULT 1,
                    timestamp REAL NOT NULL,

                    -- 13 epistemic vectors
                    engagement REAL,
                    know REAL,
                    do REAL,
                    context REAL,
                    clarity REAL,
                    coherence REAL,
                    signal REAL,
                    density REAL,
                    state REAL,
                    change REAL,
                    completion REAL,
                    impact REAL,
                    uncertainty REAL,

                    -- Metadata
                    reflex_data TEXT,
                    reasoning TEXT,
                    evidence TEXT,
                    project_id TEXT,
                    transaction_id TEXT,

                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
    """,

    # Schema 4 — scratchpad notes (note-to-self)
    #
    # Low-friction, transaction-scoped jottings captured mid-flow and reviewed
    # at the POSTFLIGHT retrospective. Pure metadata: NOT shared, NOT embedded
    # in Qdrant, NOT written to git notes. `triaged` flips to 1 once the note
    # has been promoted to an artifact/goal or consciously discarded.
    """
    CREATE TABLE IF NOT EXISTS notes (
                    note_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    transaction_id TEXT,
                    project_id TEXT,
                    ai_id TEXT,
                    text TEXT NOT NULL,
                    tag TEXT,
                    created_at REAL NOT NULL,
                    triaged INTEGER DEFAULT 0,

                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
    """,

]
