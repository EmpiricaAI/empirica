# Project Context for AI Agents — Authoritative Guide

**Audience:** AI agents working with Empirica across multiple projects.
**Status:** Reflects current behaviour as of 1.8.x. Replaces the earlier
proposal-shaped document.

---

## TL;DR

- Empirica state is **directory-scoped**. Each git repo with a `.empirica/`
  directory is its own isolated project.
- Each tmux pane / terminal is its own **instance**. State doesn't leak between
  panes even when they share a working directory.
- The session row in SQLite is the **canonical project_id**. `project.yaml`
  is the human-readable inventory; the DB is the source of truth.
- When you change directories, your project context changes. Use
  `empirica project-switch <name>` to make it explicit and verifiable.
- The **practice anchor** is `ai_id` (set in `.empirica/project.yaml`), not
  cwd or folder basename. cwd is just working-context. See
  [AI_ID_AS_ANCHOR.md](../architecture/AI_ID_AS_ANCHOR.md) for the full
  resolution chain — the canonical mechanism that makes practices portable
  across filesystems and provisioning models.

---

## How Project Context Resolves

### Three coordinates

Every Empirica command resolves three things to know where to read/write:

| Coordinate | Source | Why |
|---|---|---|
| `instance_id` | `$TMUX_PANE` (or `EMPIRICA_INSTANCE_ID` override) | Pane isolation — separate transactions per terminal |
| `claude_session_id` | Claude Code hook env | Multi-instance correlation across compaction |
| `project_id` | `.empirica/sessions/sessions.db` (sessions row) | Where artifacts get written |

The `project_id` is set when `session-create` runs. It does NOT change just
because you `cd` to another directory — the session is anchored where it was
created. This is intentional: it prevents accidental cross-project writes.

### Resolution priority for project_id

`session-create` and friends resolve `project_id` in this order:

1. **Explicit `--project-id`** flag (highest priority)
2. **Active session row** in the local `sessions.db` if one is open
3. **Match by `git remote get-url origin`** against the `projects` table
4. **`.empirica/project.yaml`'s `project_id`** field (fresh-project fallback)

If all four fail, `session-create` registers a new project from the current
directory's git remote.

### Database scoping

Each project has its own SQLite database. Layout:

```
~/.empirica/                          # Global config
    config.yaml                       # User-level settings
    instance_projects/                # Per-pane active project
        tmux_0.json
        tmux_1.json

/path/to/project-a/.empirica/         # Project A
    project.yaml                      # Inventory (project_id, name, repos)
    sessions/sessions.db              # All Project A artifacts
    active_transaction_tmux_0.json    # Per-pane transaction state

/path/to/project-b/.empirica/         # Project B (fully separate)
    project.yaml
    sessions/sessions.db
```

When you run `empirica finding-log`, the CLI walks up from `cwd` to find the
nearest `.empirica/sessions/sessions.db` and writes there. Different working
directories = different databases = different projects.

---

## Switching Projects

### The explicit way

```bash
cd /path/to/other-project
empirica project-switch <name-or-id>
```

`project-switch` does three things:

1. Validates that the named project exists (in this repo's DB)
2. Updates the per-pane `instance_projects/<instance>.json` so subsequent
   commands resolve to this project
3. Prints a confirmation banner with the project's recent activity:
   `[XX sessions, YY findings, last activity ZZ ago]`

This is the **canonical workflow** when a user says "work on project X" or
when you change directories mid-conversation.

### The implicit way

Since 1.14 one case no longer "mostly works": a PREFLIGHT whose session belongs
to **another registered practice** is refused before any write, naming the owning
practice and its store. A session that exists nowhere still only warns — that is
a legitimate first transaction. The rest of the caveats stand:

- `session-create` will auto-link to whatever project the current `cwd`
  resolves to (via git remote, then `project.yaml`)
- `finding-log` and friends will also resolve via `cwd` walk-up
- BUT: if a transaction was opened against project A and you `cd` to project
  B's directory mid-transaction, the transaction state file is still scoped
  to project A. You'll get drift.

**Rule:** `project-switch` before opening a transaction in a different project.

### Cross-project writes (no switch needed)

To log to another project without switching:

```bash
empirica finding-log --project-id <project-name-or-id> --finding "..."
```

Available on all `*-log` commands and `goals-create`. Useful for
note-while-you-work patterns: see something relevant to project B while
working in project A, log it without leaving the transaction.

---

## Verifying You're in the Right Place

### Quick check

```bash
empirica project-status
```

Shows the resolved project, recent activity, and active transaction (if any).

### Suspicion-prompted check

If you're unsure whether your context matches what the user just asked for:

1. **Run `empirica project-status`** before logging anything substantive
2. **Compare project name to the user's stated intent** — match? Continue.
   Mismatch? `project-switch` or ask for clarification.

### Don't trust conversation memory alone

If the conversation has spanned compaction or multiple `cd`s, your beliefs
about which project is active can drift. The session row in SQLite is the
authoritative answer; verify before writing.

---

## Multi-Pane Workflow

Two panes can work on different projects simultaneously without interference:

```
┌──────────────────────┬──────────────────────┐
│  Pane 0 (tmux_0)     │  Pane 1 (tmux_1)     │
│  cd ~/project-a      │  cd ~/project-b      │
│  project-switch a    │  project-switch b    │
│                      │                      │
│  Independent:        │  Independent:        │
│  • transactions      │  • transactions      │
│  • goals             │  • goals             │
│  • PREFLIGHT/CHECK   │  • PREFLIGHT/CHECK   │
└──────────────────────┴──────────────────────┘
```

See [TMUX_MULTI_PANE_GUIDE.md](./TMUX_MULTI_PANE_GUIDE.md) for the full
isolation architecture (pane detection, namespaced state files,
cockpit overview).

---

## Common Failure Modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `finding-log` writes to "wrong" project | `cd`'d without `project-switch` | `project-switch` then re-log |
| `project-bootstrap` shows zero history | Wrong project resolved | Check `project-status`, switch explicitly |
| Two panes interfering | `EMPIRICA_INSTANCE_ID` set globally | Unset (only set per-terminal in non-tmux) |
| Transaction won't close | Transaction file under different instance | Use the same pane that opened it, or `postflight-submit -` |
| Session links to wrong project | `git remote origin` matched a stale project row | `project-switch <correct>` then re-create session |

---

## For System Prompts

Minimum guidance for AI agents:

```
PROJECT CONTEXT VERIFICATION

When the user says "work on project X" or you change directories:
  1. Run `empirica project-status` to verify resolved context
  2. If mismatch, run `empirica project-switch <name>` explicitly
  3. All subsequent *-log commands write to that project's DB

The session row in SQLite is canonical. project.yaml is informational.
Never assume project context from conversation history alone.
```

---

## Cross-References

- [TMUX_MULTI_PANE_GUIDE.md](./TMUX_MULTI_PANE_GUIDE.md) — Pane-level isolation
- [SESSION_GOAL_WORKFLOW.md](../human/end-users/SESSION_GOAL_WORKFLOW.md) — Session and goal lifecycle
- [WORKSPACE_DATABASE_SCHEMA.md](../reference/WORKSPACE_DATABASE_SCHEMA.md) — DB layout
- [workspace_management.md](../reference/api/workspace_management.md) — CLI surface
