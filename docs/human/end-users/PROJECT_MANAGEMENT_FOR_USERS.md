# Project Management for Empirica Users

Empirica is project-centric. Each git repo gets its own project identity,
its own SQLite database, its own epistemic history. This guide covers the
day-to-day commands for living with that model.

---

## Project Identity

Every Empirica project owns:

| Asset | Path | In git? |
|---|---|---|
| Project identity (`project_id`, `ai_id`, `name`, repo URL) | `.empirica/project.yaml` | ✅ committed |
| Per-project SQLite (sessions, transactions, artifacts) | `.empirica/sessions/sessions.db` | ❌ gitignored |
| Per-AI calibration breadcrumbs | `.empirica/breadcrumbs.yaml` | ❌ gitignored |
| Path/runtime config | `.empirica/config.yaml` | ❌ gitignored |
| Git-notes-anchored artifacts | `.git/refs/notes/empirica_*` | ✅ in `.git/`, not pushed by default |

The repo-level `~/.empirica/` directory holds user-tenant config that's
shared across all projects: cortex creds (optional, for mesh layer),
ntfy creds (optional, for push wake), the local-daemon registry, and
the workspace database.

---

## Basic Operations

### Initialize a project

```bash
cd your-project
empirica project-init
```

Writes `.empirica/project.yaml` with:
- `project_id` — UUID
- `name`, `description`, `repository`
- `ai_id` — derived from the project basename (strips `empirica-` prefix
  so `empirica-cortex` → `cortex`). Override with `--ai-id custom-name`.

You only run this once per repo. Re-run with `--force` if you want to
overwrite an existing `project.yaml`.

### Switch projects

```bash
cd ../other-project
empirica project-bootstrap
```

That's it. Project context is keyed on the CWD's git root — no explicit
switch is needed. `project-bootstrap` prints the current project's
recent findings, open unknowns, top-confidence artifacts, and active
goals.

### Inspect what's registered

```bash
# Locally-known projects in the daemon registry
empirica projects-list

# What the daemon is currently serving
empirica daemon-list

# Walk the filesystem for projects with .empirica/project.yaml
empirica projects-discover

# Bulk-register discovered projects on Cortex (optional — only if you
# want cross-project search via the cortex serving layer; requires
# CORTEX_API_KEY). Empirica stays fully usable per-project without this.
empirica projects-bulk-register

# OR the single-verb pipeline (discover + register in one) — also optional:
empirica projects-sync
```

### Update project metadata

```bash
empirica project-update --type software --domain "AI infrastructure"
empirica project-update --description "New description"
```

`project-update` merges into `.empirica/project.yaml` atomically.

---

## Auto-Detection vs Explicit `--project-id`

By default, every command resolves the project from the current working
directory's git root. Override with `--project-id <UUID>` when:

- Writing an artifact into a different project's DB from inside this one:
  ```bash
  empirica finding-log --project-id <OTHER_UUID> --finding "..."
  ```
- Running maintenance against a non-CWD project.

Most CLI commands accept `--project-id`. Where it's not yet wired,
`cd` into the target project first.

---

## Database & Migrations

Schema migrations run automatically. When any command opens the SQLite
DB, the migration runner compares the recorded schema version against
the migrations listed in `empirica/data/migrations/migrations.py`,
applies any missing migrations in order, and updates the version. You
don't run migrations by hand.

Latest migration as of 1.9.8: `045_assumption_decision_description`.

To force-replay migrations after a manual schema fix:
```bash
empirica rebuild --migrations-only
```

---

## Cross-Project View (Workspace)

The workspace database at `~/.empirica/workspace/workspace.db` keeps
a registry of every project Empirica has seen plus rollup analytics.

```bash
# Snapshot view
empirica workspace-overview

# Walk a directory tree and register every project found
empirica workspace-init --path ~/projects

# Pull stats from a project's SQLite into workspace.db
empirica workspace-map

# Semantic search across all projects' Qdrant collections
empirica project-search --task "auth flow" --global
```

| Per-project SQLite | Workspace DB |
|---|---|
| Sessions, transactions | Project registry + paths |
| Findings, unknowns, dead-ends | Aggregated stats |
| Goals, tasks | Cross-project patterns |
| Per-AI calibration | Project trajectories |

The workspace layer is what makes "what have I learned about auth
across all 27 projects?" answerable.

---

## Sharing Per-Project Epistemic Data

Artifacts are written to `refs/notes/empirica_*` in your local git, one
ref per artifact type. They're **not pushed by default**.

```bash
# Share your team's epistemic trail
git push origin 'refs/notes/empirica_*:refs/notes/empirica_*'

# Pull a teammate's
git fetch origin 'refs/notes/empirica_*:refs/notes/empirica_*'
```

For cross-AI orchestration (proposals, completion handshakes) — an
**optional** layer that empirica core doesn't require — see
`docs/architecture/EVENT_LISTENER.md`. Cortex + ntfy mediate that layer,
not git; both are opt-in when you want peer-AI mesh coordination.

---

## Sessions vs Transactions

| Concept | What it is | Bounded by |
|---|---|---|
| **Session** | A continuous AI working window | Compaction / explicit close |
| **Transaction** | A measurement cycle (PREFLIGHT → POSTFLIGHT) | Your choice — typically one coherent chunk of work |

A session can contain many transactions. A transaction can outlive a
session (POSTFLIGHT after a compaction). Every artifact is linked to
the transaction it was logged within — that's how the calibration
pipeline grounds your beliefs against observable outcomes.

---

## Troubleshooting

**"No project found at $PWD"** — run `empirica project-init`.

**`project-bootstrap` shows empty context** — no artifacts logged yet for
this project. Use `finding-log` / `unknown-log` / `decision-log` and
re-bootstrap.

**Project on disk but not in registry** — `empirica projects-discover --register`.

**Database corruption** — see `03_TROUBLESHOOTING.md`.

---

## See Also

- **First time:** [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)
- **Multi-project lifecycle (discover / register / sync / prune / unregister):** [PROJECT_LIFECYCLE.md](PROJECT_LIFECYCLE.md)
- **CLI basics:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
- **Workflow:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Cross-project queries:** `docs/reference/api/CROSS_PROJECT.md`
