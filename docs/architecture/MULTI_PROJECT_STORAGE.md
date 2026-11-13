# Multi-Project Storage Architecture

**Version:** 1.9.6 | **Status:** Production

---

## Overview

Empirica uses a **project-local primary** architecture:
- **Primary:** Each git repo has its own `.empirica/sessions/sessions.db` for all project data
- **Global:** `~/.empirica/` stores credentials, config, cross-project Qdrant vectors, 
- **CRM:** not stored by core. Organizations, contacts and engagements live in the shared `crm-mcp` store (owned by `empirica-workspace`); the old `~/.empirica/crm/crm.db` holds retired CRM tables and nothing reads it (commit `fcf442110`)
- **Fallback:** If no local `.empirica/` exists, falls back to `~/.empirica/`

**Key principle:** Data follows the project. When you `cd` into a repo, you get that project's sessions, goals, and findings. CRM identity (organizations, contacts, engagements) is not project data and is not in core's stores; it lives in `crm-mcp`.

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
                              │  │   • goals, tasks             │   │
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
                              │  │   crm/crm.db  (RETIRED)         │   │
                              │  │   ═══════════                   │   │
                              │  │   • legacy CRM tables, unused   │   │
                              │  │   • live CRM = crm-mcp          │   │
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
                              │                   │        tasks     │
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

### Layer 5: BRIDGE (session-start context)
- Built from the project's SQLite at session start and after compaction, injected as hook context; nothing on disk
- Goals, findings, unknowns, decisions, assumptions, mistakes, dead ends and links, each with an id, within a token budget
- Project-scoped (queries filter by `project_id`)
- Claude Code's own `~/.claude/projects/{key}/memory/MEMORY.md` is left to Claude Code
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

Engagements are canonical in `crm-mcp`, not in a local SQL table. Use
`empirica-workspace engagement list|show` (or the CRM `crm_list_engagements`
tool) to find the engagements for an organization or contact, and
`empirica-workspace entity knowledge --entity <id>` for the artifacts bound to one.
The legacy local `crm.db` query that used to stand here is retired.

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
# In the first repo
cd ~/code/frontend
empirica project-init --project-name "My App"
# Returns: project_id = abc-123

# In each further repo, link to the same project instead of creating another
cd ~/code/backend
empirica project-init --project-id abc-123
```

### 2. Link Sessions to Project
```bash
# In each repo, sessions auto-link via project detection
cd ~/code/frontend
empirica session-create --ai-id empirica
# Detects project from git remote / config

# Or explicitly link
empirica session-create --ai-id empirica --project-id abc-123
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
| CRM | - | `crm-mcp` (shared store; the local `~/.empirica/crm/crm.db` is retired) |
| Qdrant vectors | - | `~/.empirica/qdrant_storage/` (always global) |
| Global lessons | - | `~/.empirica/lessons/*.yaml` |
| Client lessons | - | `~/.empirica/lessons/clients/{client_id}/*.yaml` |
| Project lessons | `<repo>/.empirica/lessons/*.yaml` | - |
| Git checkpoints | `<repo>/.git/refs/notes/empirica/` | - |
| Config | `<repo>/.empirica/config.yaml` | `~/.empirica/config.yaml` |
| Credentials | - | `~/.empirica/credentials.yaml` (always global) |

**Resolution order:** Project-local `.empirica/` is checked first. Falls back to `~/.empirica/` only if local dir doesn't exist.

**Always Global:** Qdrant vectors and credentials are always stored globally because they span multiple projects. CRM identity is not in core's stores (see `crm-mcp`).

**Per-Project (Claude Code):** the session-start picture is built from the project's own database, so instances on one project see the same artifacts and other projects' stay out.

---

---

## Swarm Learning via the project database

Multiple Claude Code instances working on the same project read one project database at session start. This creates
emergent swarm learning:

```
Agent A: discovers dead-end → logs it → it is in the project database at once
Agent B: starts → its session-start picture includes A's dead-end → avoids it → finds solution
Agent C: starts → gets combined epistemic state of A + B
```

**Properties:**
- **No explicit coordination** — agents share via the project's artifacts
- **Ranked** — by impact, type and recency, within a token budget
- **Project-isolated** — scoped by project_id, no cross-project bleeding
- **Correctable** — a retracted or resolved artifact leaves every agent's picture
- **Cumulative** — knowledge accumulates across agents and sessions

The Qdrant layer adds depth: the session-start picture is what fits the budget,
`project-search` gives semantic access to the full history.

**See also:** [claude-code-symbiosis.md](./claude-code-symbiosis.md) for the full integration architecture.

---

**Website:** [getempirica.com](https://getempirica.com)
