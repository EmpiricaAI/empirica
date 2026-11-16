# Memory & Context Architecture

> How Claude Code + Empirica loads, stores, and retrieves knowledge across sessions.

---

## Four Tiers

```
+================================================================+
|                    TIER 1: ALWAYS LOADED                        |
|                  (in context every turn)                        |
|                                                                |
|  System prompt and ~/.claude/CLAUDE.md, which @-includes the   |
|  Empirica core prompt (plus the org, Cortex and CRM layers      |
|  where those are installed)                                    |
|  {project}/CLAUDE.md, AGENTS.md (project-local)                |
|  MEMORY.md: Claude Code's auto-memory index. Claude Code owns   |
|  it; Empirica writes nothing to the memory folder              |
|  Hook output (SessionStart, UserPromptSubmit, PreToolUse, ...) |
+================================================================+
                             |
                             v
+================================================================+
|                   TIER 2: ON DEMAND                             |
|            (loaded by hooks or skills when relevant)            |
|                                                                |
|  memory/*.md: individual memories, loaded on relevance         |
|  .breadcrumbs.yaml: calibration block, injected at session     |
|    start                                                       |
|  project-bootstrap output: findings, goals, unknowns,          |
|    calibration; at session start and after compaction          |
|  Qdrant search: retrieval at PREFLIGHT / CHECK and on demand   |
|  Skills: loaded when their trigger fires                       |
+================================================================+
                             |
                             v
+================================================================+
|                TIER 3: PERSISTENT STORAGE                       |
|             (queried on demand, never bulk-loaded)              |
|                                                                |
|  {project}/.empirica/sessions/sessions.db: reflexes, goals,    |
|    artifacts, edges, calibration                               |
|  ~/.empirica/workspace/workspace.db: cross-project registry    |
|    (projects, sessions) and the CRM tables crm-mcp serves      |
|  Qdrant: ten collections per project, three global (below)     |
|  Git notes: refs/notes/empirica/* (one note per artifact,      |
|    goal, task, edge source, ...) and --ref=breadcrumbs; they    |
|    travel with the repo and rebuild the database               |
+================================================================+
                             |
                             v
+================================================================+
|                TIER 4: TRANSIENT STATE                          |
|                     (per session)                               |
|                                                                |
|  ~/.empirica/instance_projects/{id}.json  terminal -> project  |
|  ~/.empirica/active_work_{conversation}.json                   |
|                              conversation -> session, project  |
|  {project}/.empirica/active_transaction_{instance}.json        |
|                              the open transaction              |
|  {project}/.empirica/hook_counters_{instance}.json             |
|                              tool calls in the transaction     |
|  ~/.claude/projects/{path}/*.jsonl   conversation transcripts  |
+================================================================+
```

Qdrant collections, from `empirica/core/qdrant/collections.py`: per project `docs`, `memory`, `epistemics`, `eidetic`,
`episodic`, `goals`, `calibration`, `assumptions`, `decisions`, `intents` (named `project_{id}_{kind}`); global
`global_learnings`, `global_eidetic`, `workspace_index`.

---

## Data Flow

```
  Session start                    Each tool call                Session end
  =============                    ==============                ===========

  session-init.py                  sentinel-gate.py              session-end-postflight.py
       |                                |                              |
       +-- create or resume session     +-- read the transaction       +-- POSTFLIGHT if a PREFLIGHT
       +-- project-bootstrap            +-- classify the call          |   has none
       +-- write instance_projects      |   (noetic / praxic)          +-- embed the project
       |   and active_work              +-- gate or allow              +-- Cortex sync, where
       +-- Cortex sync, where           +-- count the call             |   configured
       |   configured                                                  +-- remove this conversation's
       |                                                               |   transient files
       v                                v                              v
  Context injected                 Allowed, or blocked           State persisted
                                   with a reason                 for the next session
```

Compaction runs `pre-compact.py` and `post-compact.py`: the open transaction carries across and the bootstrap is re-injected.
`session-monitor-arm.py` prints the mesh listener's arming instructions where a listener is installed.

---

## File Locations

| Scope | Path | Purpose |
|-------|------|---------|
| **Global** | `~/.claude/CLAUDE.md` | User instructions (includes the Empirica prompt) |
| **Global** | `~/.claude/settings.json` | Hooks, plugins, status line |
| **Global** | `~/.empirica/` | Global state: active_work, instance_projects, credentials, config |
| **Global** | `~/.empirica/workspace/workspace.db` | Cross-project registry |
| **Per project** | `{project}/.empirica/sessions/sessions.db` | All epistemic data |
| **Per project** | `{project}/.empirica/active_transaction_*.json` | Open transaction |
| **Per project** | `{project}/.empirica/hook_counters_*.json` | Tool call counts |
| **Per project** | `{project}/.breadcrumbs.yaml` | Calibration cache, rewritten at POSTFLIGHT (gitignored) |
| **Per conversation** | `~/.claude/projects/{path}/memory/` | Auto-memories (Claude Code's own) |
| **Per conversation** | `~/.claude/projects/{path}/*.jsonl` | Full transcripts |

---

## Design Principles

1. **Tier 1 is paid every turn.** The core prompt is lean by intent: rules and when-to-load triggers, with depth in skills.
   Claude Code truncates MEMORY.md, so it holds one line per memory.
2. **Tier 2 is injected when relevant**, by SessionStart, PREFLIGHT and post-compact output, or by loading a skill.
3. **Tier 3 is queried, never bulk-loaded.** CLI verbs and Qdrant search retrieve specific rows.
4. **Tier 4 is per session.** The session-end hook removes a conversation's files; a session that dies without ending
   leaves them, and `doctor --reconcile-sessions` closes its registry row.
5. **No CWD fallback mid-session.** Project resolution uses the instance files, not the working directory. Exception:
   `startup` prefers the working directory over a stale instance file.
6. **Git notes are portable.** The epistemic graph survives a clone and rebuilds the database from the notes.
7. **Qdrant is optional.** Core works without it; semantic retrieval needs it.
