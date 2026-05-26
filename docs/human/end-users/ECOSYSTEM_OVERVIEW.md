# Empirica Ecosystem Overview

**Where your data lives, and why.**

---

## The Mental Model

Working with Empirica through an AI agent, two kinds of things are stored:

| What | Where | Example |
|---|---|---|
| **Your actual content** | Wherever it naturally belongs | Code in GitHub, docs in Drive, videos on YouTube |
| **What you've learned about it** | Empirica databases (per-project + user-tenant) | "I learned X about the auth flow" |

**Empirica doesn't store your files.** It stores what you've learned
about them, what's still unclear, what's been tried and failed, and
how your understanding has changed over time.

---

## Three Storage Tiers

### 1. Per-Project — Your work

**Where:** `<repo>/.empirica/sessions/sessions.db` (SQLite, gitignored)
**Plus:** `<repo>/.git/refs/notes/empirica_*` (per-artifact-type git notes)

Every project gets its own database. It tracks:
- **Sessions** + **transactions** — your work history
- **Goals** + **tasks** — structural progress
- **Findings, unknowns, dead-ends, decisions, assumptions, mistakes** — epistemic artifacts (all carry `transaction_id`)
- **Calibration breadcrumbs** — per-AI bias patterns

The artifact graph is also mirrored into `refs/notes/empirica_*` so it
travels with the code: `git push origin 'refs/notes/empirica_*'` shares
the epistemic trail with teammates.

### 2. User-Tenant — Your config (shared across projects)

**Where:** `~/.empirica/`

Contains things that span every project under your user identity:
- `credentials.yaml` — cortex + ntfy creds (or use env vars)
- `workspace/workspace.db` — registry of every project Empirica has seen
- `registry.yaml` — the daemon's served project set
- `tty_sessions/` — TTY → claude_session_id mapping (transient)

### 3. Workspace — Cross-project view

**Where:** `~/.empirica/workspace/workspace.db`

When you work on multiple projects, the workspace layer gives you:
- **Project registry** — all projects with trajectory pointers
- **Aggregate stats** — transaction counts, findings counts, dormancy
- **Cross-project search** — `empirica project-search --task "..." --global`
  queries the `global_learnings` Qdrant collection
- **Pattern recognition** — "I underestimate caching complexity in 3/5 projects"

---

## Optional Layers (Extensions)

These aren't in base empirica — they're separate packages or services
that extend it:

| Layer | What it adds | Repo |
|---|---|---|
| **empirica-workspace** | TUI analytics, CRM (clients + engagements + memories), portfolio dashboards | `Nubaeon/empirica-workspace` |
| **empirica-cortex** | Cross-AI orchestration (cortex_propose, listener mesh, ECO trust gating) | `Nubaeon/empirica-cortex` |
| **empirica-extension** | Browser extension surfacing artifacts in Chrome | `Nubaeon/empirica-extension` |
| **empirica-mcp** | MCP server bridge for Claude Desktop / Cursor / etc. | `Nubaeon/empirica-mcp` |

Base empirica (this package) is the measurement + storage core. The
extensions layer different surfaces on top.

---

## How the Layers Connect

```
┌────────────────────────────────────────────────────────────────┐
│  YOUR ACTUAL CONTENT                                           │
│  (code, docs, media — lives wherever it lives)                 │
└────────────────────────────────────────────────────────────────┘
                              │
              Empirica stores metadata about it
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  USER-TENANT  ( ~/.empirica/ )                                 │
├────────────────────────────────────────────────────────────────┤
│  credentials.yaml  │  workspace.db   │  registry.yaml          │
│  cortex + ntfy     │  cross-project  │  daemon-served projects │
│                    │  registry +     │                         │
│                    │  rollups        │                         │
└────────────────────┴─────────────────┴─────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  PER-PROJECT  ( <repo>/.empirica/ )                            │
├────────────────────────────────────────────────────────────────┤
│  sessions.db  ────  Goals, tasks                            │
│                     Findings, unknowns, dead-ends              │
│                     Decisions, assumptions, mistakes           │
│                     Sessions, transactions, breadcrumbs        │
│                                                                │
│  refs/notes/empirica_*  ────  Same artifacts, git-shared       │
│                               (push/fetch for team sharing)    │
└────────────────────────────────────────────────────────────────┘
```

---

## Sessions vs Transactions

A key distinction:

| Concept | What It Is | Bounded By |
|---|---|---|
| **Session** | A continuous AI working window | Compaction / explicit close |
| **Transaction** | An epistemic measurement cycle | PREFLIGHT → POSTFLIGHT |
| **Goal** | A tracked unit of work | `goals-complete` |

Sessions can contain many transactions. Transactions can outlive a
session (POSTFLIGHT after a compaction). Goals can span many
transactions and many sessions. Every artifact carries a
`transaction_id` linking it back to the measurement window.

---

## What Gets Committed vs Gitignored

| Path | Status |
|---|---|
| `.empirica/project.yaml` | ✅ Committed — project identity |
| `.empirica/sessions/sessions.db` | ❌ Gitignored |
| `.empirica/breadcrumbs.yaml` | ❌ Gitignored |
| `.empirica/credentials.yaml` | ❌ Gitignored (if it exists per-project) |
| `.git/refs/notes/empirica_*` | ✅ In git, not pushed by default |
| `~/.empirica/*` | n/a (user-tenant, never in git) |

**To share the epistemic trail with teammates:**
```bash
git push origin 'refs/notes/empirica_*:refs/notes/empirica_*'
git fetch origin 'refs/notes/empirica_*:refs/notes/empirica_*'
```

---

## Cross-Project Search

The Qdrant layer makes "what have I learned about auth across all 27
projects?" answerable:

```bash
# Within current project
empirica project-search --task "auth flow"

# Across all projects (the global_learnings collection)
empirica project-search --task "auth flow" --global
```

Artifacts opt into cross-project visibility via the
`--visibility {local,shared,public}` flag on `*-log` commands.
Default is `local` — explicit opt-in for `shared` (within-org) or
`public` (anyone).

---

## Key Insight

**Empirica separates WHAT you're working on from WHAT you've learned
about it.**

Your files, code, documents, and media stay where they are. Empirica
tracks the *knowledge* — what you've discovered, what's still unknown,
how your beliefs have changed, and whether they matched observable
outcomes. This means:

- Your actual work is never locked into Empirica
- Your knowledge persists even if the original content changes
- You can connect insights across different projects and codebases
- The AI can build on what it learned in past sessions

---

## See Also

- **First time:** [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)
- **CLI basics:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
- **Project model:** [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
- **Cross-project search:** [../../reference/api/CROSS_PROJECT.md](../../reference/api/CROSS_PROJECT.md)
- **Vocabulary:** [../../reference/TAXONOMY.md](../../reference/TAXONOMY.md)
