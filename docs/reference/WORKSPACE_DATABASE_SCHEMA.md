# Workspace Database Schema Reference

**Location:** `~/.empirica/workspace/workspace.db`
**Version:** 1.6.6
**Purpose:** Cross-project portfolio management and trajectory tracking

---

## Overview

The workspace database is a **global registry** that tracks all Empirica projects. It enables:
- Portfolio-level views across projects
- Cross-project pattern discovery
- Project switching and instance binding
- Trajectory health monitoring

---

## Tables

### `global_projects`

Primary table tracking all registered projects.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `id` | TEXT | (required) | Project UUID (primary key) |
| `name` | TEXT | (required) | Human-readable project name |
| `description` | TEXT | NULL | Project description |
| `trajectory_path` | TEXT | (required) | Path to project's `.empirica/` directory |
| `git_remote_url` | TEXT | NULL | Git remote URL for sync/discovery |
| `git_branch` | TEXT | `'main'` | Current git branch |
| `total_transactions` | INTEGER | `0` | Cached transaction count |
| `total_findings` | INTEGER | `0` | Cached findings count |
| `total_unknowns` | INTEGER | `0` | Cached unknowns count |
| `total_dead_ends` | INTEGER | `0` | Cached dead-ends count |
| `total_goals` | INTEGER | `0` | Cached active goals count |
| `last_transaction_id` | TEXT | NULL | Most recent transaction UUID |
| `last_transaction_timestamp` | REAL | NULL | Unix timestamp of last transaction |
| `last_sync_timestamp` | REAL | NULL | When stats were last refreshed |
| `status` | TEXT | `'active'` | `'active'`, `'dormant'`, `'archived'` |
| `project_type` | TEXT | `'product'` | `'software'`, `'content'`, `'research'`, `'data'`, `'design'`, `'operations'`, `'strategic'`, `'engagement'`, `'legal'` |
| `project_tags` | TEXT | NULL | JSON array of tags |
| `created_timestamp` | REAL | (required) | Unix timestamp of creation |
| `updated_timestamp` | REAL | (required) | Unix timestamp of last update |
| `metadata` | TEXT | NULL | JSON — v2.0 enrichment fields (see below) |

**Metadata Column (v2.0):**

The `metadata` column stores v2.0 project.yaml enrichment fields as JSON, synced by `project-init` and `project-update`:

```json
{
  "domain": "ai/measurement",
  "classification": "open",
  "evidence_profile": "code",
  "languages": ["python"],
  "contacts": [{"id": "alice", "roles": ["reviewer"]}],
  "engagements": [{"id": "internal", "type": "internal", "status": "ongoing"}],
  "edges": [{"entity": "project/other", "relation": "related"}]
}
```

**Indexes:**
- `idx_global_projects_status` — Fast filtering by status
- `idx_global_projects_type` — Fast filtering by project type
- `idx_global_projects_last_tx` — Sort by recent activity

**Status Values:**
| Value | Description |
|-------|-------------|
| `active` | Actively worked on, shown by default |
| `dormant` | Not recently active, still tracked |
| `archived` | Hidden from default views |

---

### `instance_bindings`

Which project each instance is bound to.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `instance_id` | TEXT | (required) | Instance identifier (primary key) |
| `project_id` | TEXT | (required) | Bound project UUID (foreign key to `global_projects(id)`) |
| `project_path` | TEXT | NULL | Project path at bind time |
| `bound_timestamp` | REAL | (required) | Unix timestamp of the binding |

---

### `global_sessions`

Sessions across projects, with the project each started in and the one it is in now.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `session_id` | TEXT | (required) | Session UUID (primary key) |
| `ai_id` | TEXT | NULL | AI identifier |
| `origin_project_id` | TEXT | NULL | Project the session started in |
| `current_project_id` | TEXT | NULL | Project the session is in now |
| `instance_id` | TEXT | NULL | Owning instance |
| `status` | TEXT | `'active'` | Session status |
| `parent_session_id` | TEXT | NULL | Parent session (subagents) |
| `created_at` | REAL | NULL | Unix timestamp |
| `last_activity` | REAL | NULL | Unix timestamp |

**Indexes:** `idx_global_sessions_instance` on `(instance_id, status)`, `idx_global_sessions_project` on `current_project_id`.

---

### `entity_artifacts`

Pointers from a practice's artifacts to entities (project, contact, organization, engagement).

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `id` | TEXT | (required) | Link UUID (primary key) |
| `artifact_type` | TEXT | (required) | Artifact kind (`finding`, `unknown`, ...) |
| `artifact_id` | TEXT | (required) | Artifact UUID |
| `artifact_source` | TEXT | NULL | Where the artifact lives |
| `entity_type` | TEXT | (required) | Entity kind |
| `entity_id` | TEXT | (required) | Entity id |
| `relationship` | TEXT | `'about'` | Relationship of the artifact to the entity |
| `relevance` | REAL | `1.0` | Relevance score (0-1) |
| `discovered_via` | TEXT | NULL | How the link was found |
| `engagement_id` | TEXT | NULL | Engagement the link belongs to |
| `transaction_id` | TEXT | NULL | Transaction that created it |
| `created_at` | REAL | NULL | Unix timestamp |
| `created_by_ai` | TEXT | NULL | AI that created the link |

**Constraints:** unique on `(artifact_type, artifact_id, entity_type, entity_id)`.

---

### `entity_registry`

The global directory of first-class entities (project, contact, organization, engagement, user). Backs `entity-list`, `entity-show`, `entity-walk` and `entity-search`.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `entity_type` | TEXT | (required) | Entity kind (primary key, with `entity_id`) |
| `entity_id` | TEXT | (required) | Entity id |
| `display_name` | TEXT | (required) | Human-readable name |
| `description` | TEXT | NULL | Description |
| `source_db` | TEXT | (required) | Database the entity comes from |
| `source_table` | TEXT | (required) | Table the entity comes from |
| `emoji_state` | TEXT | NULL | Display state |
| `status` | TEXT | `'active'` | Status |
| `created_at` | REAL | (required) | Unix timestamp |
| `updated_at` | REAL | NULL | Unix timestamp |
| `metadata` | TEXT | NULL | JSON |

Organization, contact and engagement rows here are no longer authoritative: the CRM store (`crm-mcp`) is the source of truth for them. Projects, practitioners and sharing agreements still live here.

---

### `entity_memberships`

Many-to-many typed relationships between entities.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `entity_type` | TEXT | (required) | Member kind (primary key with the next three) |
| `entity_id` | TEXT | (required) | Member id |
| `group_type` | TEXT | (required) | Group kind |
| `group_id` | TEXT | (required) | Group id |
| `role` | TEXT | NULL | Role within the group |
| `joined_at` | REAL | (required) | Unix timestamp |
| `left_at` | REAL | NULL | Unix timestamp when the membership ended |
| `created_at` | REAL | (required) | Unix timestamp |
| `notes` | TEXT | NULL | Notes |
| `is_primary` | INTEGER | NULL | Marks the canonical membership when several are active for one group type |

The database also holds the engagement substrate tables (`engagements`, `domain_definitions`, `stage_definitions`, `practice_domains`). The `trajectory_patterns` table is part of the per-project schema, not this database, and there is no `trajectory_links` table.

---

## CLI Commands

```bash
# List all projects in workspace
empirica workspace-list

# Overview with stats
empirica workspace-overview

# Project dependency map
empirica workspace-map

# Initialize workspace (creates database)
empirica workspace-init
```

---

## Query Examples

```sql
-- Get all active projects sorted by recent activity
SELECT name, trajectory_path, last_transaction_timestamp
FROM global_projects
WHERE status = 'active'
ORDER BY last_transaction_timestamp DESC;

-- Find projects with many dead-ends (potential learning opportunities)
SELECT name, total_dead_ends, total_findings
FROM global_projects
WHERE total_dead_ends > 5
ORDER BY total_dead_ends DESC;

-- Which project is each instance bound to
SELECT ib.instance_id, gp.name, ib.project_path
FROM instance_bindings ib
JOIN global_projects gp ON ib.project_id = gp.id;
```

---

## Related Documentation

- [Instance Isolation](../architecture/instance_isolation/ARCHITECTURE.md) — How instances bind to projects
- [Database Schema (Project-Level)](./DATABASE_SCHEMA_UNIFIED.md) — Per-project sessions.db
- [Project Switching](../guides/PROJECT_SWITCHING_FOR_AIS.md) — How projects are selected
