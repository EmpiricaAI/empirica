# Missing database migration for auto_captured_issues table causes project-bootstrap crash

## Bug Description

When upgrading from an older version of Empirica to 1.3.3, `project-bootstrap` crashes with:

```
❌ Project bootstrap error: no such table: auto_captured_issues
```

## Root Cause

The `auto_captured_issues` table is only created in `empirica/core/issue_capture.py` when the `IssueCapture` class is instantiated - not during normal database initialization. Users who upgrade from older versions have databases without this table.

## Steps to Reproduce

1. Have an existing Empirica database from version < 1.3.x
2. Upgrade to 1.3.3 via `pipx install empirica`
3. Run `empirica project-bootstrap`
4. Crash occurs

## Proposed Solution

Implement a proper migration system:

1. **Add `empirica db-migrate` command** - Run pending migrations manually
2. **Auto-migrate on CLI startup** - Check schema version, apply missing migrations
3. **Migration scripts** - Version-tracked SQL migrations in `empirica/migrations/`

Example migration structure:
```
empirica/migrations/
├── 001_initial_schema.sql
├── 002_add_auto_captured_issues.sql
├── 003_add_lessons_tables.sql
└── ...
```

## Workaround

Manually create the table:
```python
import sqlite3
conn = sqlite3.connect('.empirica/sessions/sessions.db')
conn.execute("""
CREATE TABLE IF NOT EXISTS auto_captured_issues (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    code_location TEXT,
    message TEXT NOT NULL,
    stack_trace TEXT,
    context TEXT,
    status TEXT DEFAULT 'new',
    assigned_to_ai TEXT,
    resolution TEXT,
    created_at REAL NOT NULL,
    resolved_at REAL
)
""")
conn.commit()
```

## Additional Context

- Affects: Any user upgrading from pre-1.3.x versions
- Severity: High (blocks core functionality)
- Related: All new tables added in 1.3.x need migration paths
