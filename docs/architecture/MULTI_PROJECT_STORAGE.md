# Multi-Project Storage Architecture

**Version:** 1.8.19 | **Status:** Production

---

## Overview

Empirica uses a **project-local primary** architecture:
- **Primary:** Each git repo has its own `.empirica/sessions/sessions.db` for all project data
- **Global:** `~/.empirica/` stores credentials, config, cross-project Qdrant vectors, and **CRM data**
- **CRM:** `~/.empirica/crm/crm.db` stores clients and engagements (inherently cross-project)
- **Fallback:** If no local `.empirica/` exists, falls back to `~/.empirica/`

**Key principle:** Data follows the project. When you `cd` into a repo, you get that project's sessions, goals, and findings. CRM data (clients, engagements) is always global since relationships span multiple projects.

```
                              ┌─────────────────────────────────────────┐
                              │           GLOBAL HUB                    │
                              │         ~/.empirica/                    │
                              │                                         │
                              │  ┌─────────────────────────────────┐   │
                              │  │   sessions/sessions.db          │   │
                              │  │   ════════════════════          │   │
                              │  │   • projects table              │   │
                              │  │   • sessions table              │   │
                              │  │   • reflexes (vectors)          │   │
                              │  │   • goals, subtasks             │   │
                              │  │   • project_findings            │   │
                              │  │   • project_unknowns            │   │
                              │  │   • project_dead_ends           │   │
                              │  │   • lessons, mistakes           │   │
                              │  │   • handoff_reports             │   │
                              │  │   • bayesian_beliefs            │   │
                              │  └─────────────────────────────────┘   │
                              │                                         │
                              │  ┌─────────────────────────────────┐   │
                              │  │   qdrant_storage/               │   │
                              │  │   ═══════════════               │   │
                              │  │   • Semantic vectors            │   │
                              │  │   • Cross-project search        │   │
                              │  │   • Eidetic memory (facts)      │   │
                              │  │   • Episodic memory (sessions)  │   │
                              │  └─────────────────────────────────┘   │
                              │                                         │
                              │  ┌─────────────────────────────────┐   │
                              │  │   lessons/                      │   │
                              │  │   ════════                      │   │
                              │  │   • *.yaml (cold storage)       │   │
                              │  │   • Cross-project learnings     │   │
                              │  │   • clients/ (client lessons)   │   │
                              │  └─────────────────────────────────┘   │
                              │                                         │
                              │  ┌─────────────────────────────────┐   │
                              │  │   crm/crm.db                    │   │
                              │  │   ═══════════                   │   │
                              │  │   • clients (relationships)     │   │
                              │  │   • engagements (client↔project)│   │
                              │  │   • client_interactions         │   │
                              │  │   • client_memory (semantic)    │   │
                              │  └─────────────────────────────────┘   │
                              │                                         │
                              └──────────────────┬──────────────────────┘
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    │                            │                            │
                    ▼                            ▼                            ▼
    ┌───────────────────────────┐  ┌───────────────────────────┐  ┌───────────────────────────┐
    │   PROJECT A               │  │   PROJECT B               │  │   PROJECT C               │
    │   ~/code/empirica/        │  │   ~/code/webapp/          │  │   ~/code/api-service/     │
    │                           │  │                           │  │                           │
    │   .git/                   │  │   .git/                   │  │   .git/                   │
    │   └─ refs/notes/empirica/ │  │   └─ refs/notes/empirica/ │  │   └─ refs/notes/empirica/ │
    │      └─ session/{id}/     │  │      └─ session/{id}/     │  │      └─ session/{id}/     │
    │         ├─ PREFLIGHT      │  │         ├─ PREFLIGHT      │  │         ├─ PREFLIGHT      │
    │         ├─ CHECK          │  │         ├─ CHECK          │  │         ├─ CHECK          │
    │         └─ POSTFLIGHT     │  │         └─ POSTFLIGHT     │  │         └─ POSTFLIGHT     │
    │                           │  │                           │  │                           │
    │   .empirica/              │  │   .empirica/              │  │   .empirica/              │
    │   ├─ config.yaml          │  │   ├─ config.yaml          │  │   ├─ config.yaml          │
    │   ├─ lessons/             │  │   ├─ lessons/             │  │   ├─ lessons/             │
    │   │  └─ *.yaml            │  │   │  └─ *.yaml            │  │   │  └─ *.yaml            │
    │   ├─ personas/            │  │   ├─ personas/            │  │   ├─ personas/            │
    │   └─ hooks/               │  │   └─ hooks/               │  │   └─ hooks/               │
    │                           │  │                           │  │                           │
    └───────────────────────────┘  └───────────────────────────┘  └───────────────────────────┘
              │                              │                              │
              │ project_id                   │ project_id                   │ project_id
              │ = "abc-123"                  │ = "def-456"                  │ = "ghi-789"
              │                              │                              │
              └──────────────────────────────┴──────────────────────────────┘
                                             │
                                             ▼
                              ┌─────────────────────────────────────────┐
                              │        DATABASE RELATIONSHIPS          │
                              │                                         │
                              │   projects ◄──────┬──────► sessions    │
                              │      │            │            │        │
                              │      │            │            │        │
                              │      ▼            │            ▼        │
                              │  project_*        │        reflexes     │
                              │  • findings       │        (vectors)    │
                              │  • unknowns       │            │        │
                              │  • dead_ends      │            ▼        │
                              │  • handoffs       │         goals       │
                              │  • sources        │            │        │
                              │                   │            ▼        │
                              │                   │        subtasks     │
                              │                   │                     │
                              └─────────────────────────────────────────┘
```

---

## Storage Layers

### Layer 1: HOT (In-Memory)
- Active session state
- Current epistemic vectors
- Goal/task tracking

### Layer 2: WARM (SQLite)
- `<project>/.empirica/sessions/sessions.db` (PRIMARY - project-local)
- Falls back to `~/.empirica/sessions/sessions.db` if no local dir
- All structured data: sessions, projects, reflexes, goals
- Fast queries for recent context

### Layer 3: SEARCH (Qdrant)
- `~/.empirica/qdrant_storage/`
- Semantic embeddings for findings, unknowns, dead_ends
- Cross-project similarity search
- Eidetic (fact) and episodic (narrative) memory

### Layer 4: COLD (YAML/Git)
- `~/.empirica/lessons/*.yaml` - Global procedural knowledge
- `<project>/.empirica/lessons/*.yaml` - Project-specific lessons
- `<project>/.git/refs/notes/empirica/` - Git-attached checkpoints

### Layer 5: BRIDGE (Claude Code MEMORY.md)
- `~/.claude/projects/{key}/memory/MEMORY.md` - Epistemically-curated hot cache
- Auto-curated at session end from SQLite + Qdrant
- Top 12 artifacts ranked by `impact × type_confidence × recency_decay`
- Project-scoped (queries filter by `project_id`)
- Claude Code auto-loads first 200 lines at session start
- **Key derivation:** `{key}` = absolute project path with `/` → `-` (e.g., `/home/user/myapp` → `-home-user-myapp`)

---

## Key Relationships

### Project → Sessions
```sql
-- A project spans multiple sessions across repos
SELECT s.session_id, s.ai_id, s.start_time
FROM sessions s
WHERE s.project_id = 'abc-123'
ORDER BY s.start_time;
```

### Project → Epistemic Artifacts
```sql
-- All findings for a project (cross-session)
SELECT f.finding, f.impact, s.ai_id
FROM project_findings f
JOIN sessions s ON f.session_id = s.session_id
WHERE f.project_id = 'abc-123';
```

### Cross-Project Search (Qdrant)
```python
# Search across all projects for similar patterns
from empirica.core.qdrant.vector_store import search

results = search(
    project_id="abc-123",
    query="authentication flow",
    kind="findings",
    include_global=True  # Search other projects too
)
```

### Client → Project (via Engagements)
```sql
-- Find all projects linked to a client via engagements
-- (Queries ~/.empirica/crm/crm.db)
SELECT e.project_id, e.title, e.status, e.engagement_type
FROM engagements e
WHERE e.client_id = 'client-uuid'
  AND e.status = 'active';
```

Engagements serve as the many-to-many connection layer between clients and projects.
A client can have multiple engagements with multiple projects over time.

---

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `EMPIRICA_HOME` | Override global hub location | `/data/.empirica` |
| `EMPIRICA_WORKSPACE_ROOT` | Multi-AI workspace root | `/workspace` |
| `EMPIRICA_QDRANT_URL` | External Qdrant server | `http://qdrant:6333` |
| `EMPIRICA_DATA_DIR` | Explicit data directory | `/opt/empirica/data` |

---

## Multi-Repo Project Setup

### 1. Create Project
```bash
empirica project-create --name "My App" --repos "frontend,backend,shared"
# Returns: project_id = abc-123
```

### 2. Link Sessions to Project
```bash
# In each repo, sessions auto-link via project detection
cd ~/code/frontend
empirica session-create --ai-id claude-code
# Detects project from git remote / config

# Or explicitly link
empirica session-create --ai-id claude-code --project-id abc-123
```

### 3. Bootstrap Context
```bash
# Load all project learnings (findings, unknowns, dead_ends)
empirica project-bootstrap --project-id abc-123 --output json

# Includes cross-repo context:
# - All sessions from all linked repos
# - Aggregated epistemic deltas
# - Semantic search results from Qdrant
```

---

## Data Flow Example

```
Session in Repo A                    Global Hub                    Qdrant
─────────────────                    ──────────                    ──────

1. PREFLIGHT ──────────────────────► INSERT reflexes
   (vectors)                         (session_id, phase, vectors)

2. finding-log ────────────────────► INSERT project_findings ────► Embed vector
   "Auth uses JWT"                   (project_id, finding)         (semantic)

3. CHECK ──────────────────────────► INSERT reflexes
   (gate decision)                   (session_id, CHECK, vectors)

4. POSTFLIGHT ─────────────────────► INSERT reflexes
   (learning delta)                  + Auto-embed to Qdrant ──────► Embed snapshot

5. Git commit ─────► .git/refs/notes/empirica/session/{id}/POSTFLIGHT
   (checkpoint)      (git-attached, survives branch operations)
```

---

## Cognitive Immune System (Cross-Project)

When `finding-log` is called:

```
New Finding                          Lessons (YAML)                 Qdrant
───────────                          ──────────────                 ──────

1. "JWT better than sessions" ──────► Decay matching lessons ─────► Re-embed
                                      in ~/.empirica/lessons/       with lower
                                      AND <project>/.empirica/      confidence
                                      lessons/
```

Lessons with overlapping keywords have `source_confidence` reduced.
Min floor: 0.3 (lessons never fully die, just become less trusted).

---

## Docker/Multi-AI Setup

```yaml
# docker-compose.yml
services:
  claude-agent:
    environment:
      - EMPIRICA_WORKSPACE_ROOT=/workspace
      - EMPIRICA_QDRANT_URL=http://qdrant:6333
    volumes:
      - ./:/workspace
      - empirica_data:/workspace/.empirica

  qdrant:
    image: qdrant/qdrant
    volumes:
      - qdrant_storage:/qdrant/storage

volumes:
  empirica_data:
  qdrant_storage:
```

All containers share the same `.empirica/` via volume mount.
Qdrant runs as separate service for semantic memory.

---

## File Locations Summary

| Artifact | Primary (Project-Local) | Fallback (Global) |
|----------|------------------------|-------------------|
| Sessions DB | `<repo>/.empirica/sessions/sessions.db` | `~/.empirica/sessions/sessions.db` |
| CRM DB | - | `~/.empirica/crm/crm.db` (always global) |
| Qdrant vectors | - | `~/.empirica/qdrant_storage/` (always global) |
| Global lessons | - | `~/.empirica/lessons/*.yaml` |
| Client lessons | - | `~/.empirica/lessons/clients/{client_id}/*.yaml` |
| Project lessons | `<repo>/.empirica/lessons/*.yaml` | - |
| Git checkpoints | `<repo>/.git/refs/notes/empirica/` | - |
| Config | `<repo>/.empirica/config.yaml` | `~/.empirica/config.yaml` |
| Personas | `<repo>/.empirica/personas/` | `~/.empirica/personas/` |
| Credentials | - | `~/.empirica/credentials.yaml` (always global) |
| MEMORY.md hot cache | - | `~/.claude/projects/{key}/memory/MEMORY.md` (per-project) |

**Resolution order:** Project-local `.empirica/` is checked first. Falls back to `~/.empirica/` only if local dir doesn't exist.

**Always Global:** CRM data (clients, engagements), Qdrant vectors, and credentials are always stored globally because they span multiple projects.

**Per-Project (Claude Code):** MEMORY.md is keyed by project path, ensuring project isolation. Multiple Claude instances on the same project share one MEMORY.md file.

---

---

## Swarm Learning via MEMORY.md

Multiple Claude Code instances working on the same project share one MEMORY.md file
(keyed by git repo path). This creates emergent swarm learning:

```
Agent A: discovers dead-end → logs it → session ends → MEMORY.md updated
Agent B: starts → loads MEMORY.md → sees dead-end → avoids it → finds solution
Agent B: session ends → MEMORY.md updated with A's dead-end + B's finding
Agent C: starts → gets combined epistemic state of A + B
```

**Properties:**
- **No explicit coordination** — agents share via the memory file
- **Confidence-ranked** — not all-or-nothing, weighted by epistemic quality
- **Project-isolated** — scoped by project path, no cross-project bleeding
- **Recency-aware** — old noise decays, recent insights surface
- **Cumulative** — knowledge accumulates across agents and sessions
- **Self-correcting** — cognitive immune system reduces confidence of contradicted findings

The Qdrant layer adds depth: MEMORY.md is the hot cache (12 items),
`project-search` gives semantic access to the full history.

**See also:** [claude-code-symbiosis.md](./claude-code-symbiosis.md) for the full integration architecture.

---

**Website:** [getempirica.com](https://getempirica.com)
