# Empirica CLI Commands — Unified Reference

> **Auto-generated.** This document is regenerated from the live argparse
> tree by `scripts/generate_cli_docs.py`. Do not edit by hand — your edits
> will be overwritten on the next regen. Add new commands by registering
> their parser via `add_*_parsers(subparsers)` in
> `empirica/cli/parsers/__init__.py`; the generator picks them up
> automatically.
>
> Categories below follow `_HELP_CATEGORIES` in
> `empirica/cli/cli_core.py` — adding a new category means editing that
> dictionary, then running this script.

**Framework version:** 1.9.10
**Generated:** 2026-05-22 12:56:08 UTC
**Total commands:** 231 (across 26 categories)

For the most up-to-date detail on any single command, prefer
`empirica <command> --help` — the generator extracts the same `help`
strings argparse uses at runtime, but argparse can render dynamic context
(env-resolved defaults, conditional choices) that a static document
cannot.

---

## Transaction-First Pattern

Most commands auto-derive `--session-id` from the active transaction.
When you're inside an epistemic transaction workflow (after PREFLIGHT),
you don't need to specify `--session-id` explicitly.

The CLI uses `get_active_empirica_session_id()` with this priority chain:

1. **Active transaction** (`active_transaction_*.json`) — highest priority
2. **Active work context** (`active_work_*.json`) — from project-switch
3. **Instance projects** (`instance_projects/*.json`) — tmux/terminal aware

Commands that auto-derive `session_id` include all `*-log` artifacts,
`goals-*`, `epistemics-*`, and most read paths. The few that still
require `--session-id` (`project-bootstrap`, `sessions-show`,
`sessions-export`) document it explicitly.

---

## Category Index


| Category | Count | Commands |
|---|---|---|
| [session](#session) | 8 | `session-create`, `sessions-list`, `sessions-show`, … |
| [workflow](#workflow) | 4 | `preflight-submit`, `check`, `check-submit`, … |
| [goals](#goals) | 16 | `goals-create`, `goals-list`, `goals-search`, … |
| [logging](#logging) | 19 | `finding-log`, `unknown-log`, `unknown-list`, … |
| [project](#project) | 15 | `project-init`, `project-update`, `project-create`, … |
| [workspace](#workspace) | 9 | `workspace-init`, `workspace-map`, `workspace-list`, … |
| [checkpoint](#checkpoint) | 7 | `checkpoint-create`, `checkpoint-load`, `checkpoint-list`, … |
| [sync](#sync) | 6 | `sync-config`, `sync-push`, `sync-pull`, … |
| [profile](#profile) | 4 | `profile-sync`, `profile-prune`, `profile-status`, … |
| [identity](#identity) | 4 | `identity-create`, `identity-export`, `identity-list`, … |
| [handoff](#handoff) | 2 | `handoff-create`, `handoff-query` |
| [issue](#issue) | 6 | `issue-list`, `issue-show`, `issue-handoff`, … |
| [investigation](#investigation) | 5 | `investigate`, `investigate-create-branch`, `investigate-checkpoint-branch`, … |
| [monitoring](#monitoring) | 9 | `monitor`, `assess-state`, `trajectory-project`, … |
| [cockpit](#cockpit) | 10 | `status`, `tui`, `sentinel`, … |
| [skills](#skills) | 3 | `skill-suggest`, `skill-fetch`, `skill-extract` |
| [architecture](#architecture) | 3 | `assess-component`, `assess-compare`, `assess-directory` |
| [agents](#agents) | 7 | `agent-spawn`, `agent-report`, `agent-aggregate`, … |
| [sentinel](#sentinel) | 4 | `sentinel-orchestrate`, `sentinel-load-profile`, `sentinel-status`, … |
| [personas](#personas) | 4 | `persona-list`, `persona-show`, `persona-promote`, … |
| [lessons](#lessons) | 9 | `lesson-create`, `lesson-load`, `lesson-list`, … |
| [mcp](#mcp) | 6 | `mcp-start`, `mcp-stop`, `mcp-status`, … |
| [memory](#memory) | 6 | `memory-prime`, `memory-scope`, `memory-value`, … |
| [vision](#vision) | 1 | `vision` |
| [domains](#domains) | 4 | `domain-list`, `domain-show`, `domain-resolve`, … |
| [setup](#setup) | 7 | `onboard`, `setup-claude-code`, `enp-setup`, … |

---

## session

#### `empirica session-create`  _(aliases: `sc`)_

Create new session (AI-first: use config file, Legacy: use flags)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--ai-id` — optional
  AI agent identifier (legacy)
- `--user-id` — optional
  User identifier (legacy)
- `--project-id` — optional
  Project UUID to link session to (optional, auto-detected from git remote if omitted)
- `--subject` — optional
  Subject/workstream identifier (auto-detected from directory if omitted)
- `--parent-session-id` — optional
  Parent session UUID for sub-agent lineage tracking
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json for AI)
- `--verbose` — optional · flag
  Show detailed operation info
- `--auto-init` — optional · flag
  Auto-initialize .empirica/ if not present in git repo (prevents orphaned sessions)

#### `empirica sessions-list`  _(aliases: `session-list`, `sl`)_

List all sessions

**Arguments:**

- `--ai-id` — optional
  Filter by AI identifier
- `--limit` — optional · type=`int` · default=`50`
  Maximum sessions to show
- `--verbose` — optional · flag
  Show detailed info
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica sessions-show`  _(aliases: `session-show`)_

Show detailed session info

**Arguments:**

- `session_id` — **required**
  Session ID or alias (latest, latest:active, latest:<ai_id>, latest:active:<ai_id>)
- `--session-id` — optional
  Session ID (alternative to positional argument)
- `--verbose` — optional · flag
  Show all vectors and cascades
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica sessions-export`  _(aliases: `session-export`)_

Export session to JSON

**Arguments:**

- `session_id` — **required**
  Session ID or alias (latest, latest:active, latest:<ai_id>)
- `--session-id` — optional
  Session ID (alternative to positional argument)
- `--output` / `-o` — optional
  Output file path (default: session_<id>.json)

#### `empirica sessions-resume`  _(aliases: `session-resume`, `sr`)_

Resume previous sessions

**Arguments:**

- `--ai-id` — optional
  Filter by AI ID
- `--count` — optional · type=`int` · default=`1`
  Number of sessions to retrieve
- `--detail-level` — optional · type=`choice` · choices={summary, detailed, full} · default=`summary`
  Detail level
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica session-snapshot`

Show session snapshot (where you left off)

**Arguments:**

- `session_id` — **required**
  Session ID or alias
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica memory-compact`

Create epistemic continuity across memory compaction boundaries

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode, default: stdin)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica transaction-adopt`  _(aliases: `tx-adopt`)_

Adopt an orphaned transaction from a different instance (e.g., after tmux restart)

**Arguments:**

- `--from` — **required**
  Source instance ID (e.g., tmux_4) - the orphaned transaction's instance
- `--to` — optional
  Target instance ID (e.g., tmux_7) - your current instance (auto-detected if not specified)
- `--project` — optional
  Project path containing the transaction (auto-detected if not specified)
- `--dry-run` — optional · flag
  Show what would be done without making changes
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

---

## workflow

#### `empirica preflight-submit`  _(aliases: `pre`, `preflight`)_

Submit preflight assessment (AI-first: use config file, Legacy: use flags)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--session-id` — optional
  Session ID (legacy)
- `--vectors` — optional
  Epistemic vectors as JSON string or dict (legacy)
- `--reasoning` — optional
  Reasoning for assessment scores (legacy)
- `--voice` — optional
  Voice profile name to load for outreach drafting (e.g. --voice david). Profile is resolved via the empirica voice loader.
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json for AI)
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica check`

Execute epistemic check (AI-first: use config file, Legacy: use flags)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--session-id` — optional
  Session ID (legacy)
- `--findings` — optional
  Investigation findings as JSON array (legacy)
- `--unknowns` — optional
  Remaining unknowns as JSON array (legacy)
- `--remaining-unknowns` — optional
  Alias for --unknowns (legacy)
- `--confidence` — optional · type=`float`
  Confidence score (0.0-1.0) (legacy)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json for AI)
- `--verbose` — optional · flag
  Show detailed analysis

#### `empirica check-submit`

Submit check assessment (AI-first: use config file, Legacy: use flags)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--session-id` — optional
  Session ID (legacy)
- `--vectors` — optional
  Epistemic vectors as JSON string or dict (legacy)
- `--decision` — optional · type=`choice` · choices={proceed, investigate, proceed_with_caution}
  Decision made (legacy)
- `--reasoning` — optional
  Reasoning for decision (legacy)
- `--cycle` — optional · type=`int`
  Investigation cycle number (legacy)
- `--round` — optional · type=`int`
  Round number (for checkpoint tracking) (legacy)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica postflight-submit`  _(aliases: `post`, `postflight`)_

Submit postflight assessment (AI-first: use config file, Legacy: use flags)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--session-id` — optional
  Session ID (legacy)
- `--vectors` — optional
  Epistemic vectors as JSON string or dict (legacy)
- `--reasoning` — optional
  Description of what changed from preflight (legacy)
- `--changes` — optional
  Alias for --reasoning (deprecated, use --reasoning)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json for AI)
- `--verbose` — optional · flag
  Show detailed operation info

---

## goals

#### `empirica goals-create`  _(aliases: `goal-create`, `gc`)_

Create new goal (AI-first: use config file, Legacy: use flags)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--session-id` — optional
  Session ID (auto-derived from active transaction)
- `--project-id` — optional
  Target project UUID or name (for cross-project goal creation)
- `--ai-id` — optional · default=`empirica_cli`
  AI identifier (legacy)
- `--objective` — optional
  Goal title — short, actionable (~256 char cap)
- `--description` — optional
  Optional rich body — context, motivation, success-criteria detail (8000 char cap)
- `--scope-breadth` — optional · type=`float` · default=`0.3`
  Goal breadth (0.0-1.0, how wide the goal spans)
- `--scope-duration` — optional · type=`float` · default=`0.2`
  Goal duration (0.0-1.0, expected lifetime)
- `--scope-coordination` — optional · type=`float` · default=`0.1`
  Goal coordination (0.0-1.0, multi-agent coordination needed)
- `--success-criteria` — optional
  Success criteria as JSON array (or "-" to read from stdin)
- `--success-criteria-file` — optional
  Read success criteria from file (avoids shell quoting issues)
- `--estimated-complexity` — optional · type=`float`
  Complexity estimate (0.0-1.0)
- `--constraints` — optional
  Constraints as JSON object
- `--metadata` — optional
  Metadata as JSON object
- `--use-beads` — optional · flag
  Create BEADS issue and link to goal
- `--status` — optional · type=`choice` · choices={planned, in_progress} · default=`in_progress`
  Initial status: 'planned' (logged, not started) or 'in_progress' (active, default)
- `--force` — optional · flag
  Create goal even if similar goal exists
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-list`  _(aliases: `goal-list`, `gl`)_

List goals

**Arguments:**

- `--ai-id` — optional
  Filter by AI identifier
- `--session-id` — optional
  Derive project_id from session (convenience)
- `--transaction-id` — optional
  Filter by transaction ID (measurement scope)
- `--project-id` — optional
  Filter by project ID (structural scope)
- `--scope-breadth-min` — optional · type=`float`
  Filter by minimum breadth (0.0-1.0)
- `--scope-breadth-max` — optional · type=`float`
  Filter by maximum breadth (0.0-1.0)
- `--scope-duration-min` — optional · type=`float`
  Filter by minimum duration (0.0-1.0)
- `--scope-duration-max` — optional · type=`float`
  Filter by maximum duration (0.0-1.0)
- `--scope-coordination-min` — optional · type=`float`
  Filter by minimum coordination (0.0-1.0)
- `--scope-coordination-max` — optional · type=`float`
  Filter by maximum coordination (0.0-1.0)
- `--completed` — optional · flag
  Show completed goals (default: active). Use --status for finer filtering.
- `--status` — optional · type=`choice` · choices={planned, in_progress, completed, all, drift}
  Filter by lifecycle status. Takes precedence over --completed. "drift" surfaces rows where status text disagrees with is_completed (canonical).
- `--limit` — optional · type=`int` · default=`20`
  Max results (default: 20)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-search`

Semantic search for goals across sessions (Qdrant)

**Arguments:**

- `query` — **required**
  Search query (e.g., "authentication system")
- `--project-id` — optional
  Project ID (auto-detects if not provided)
- `--type` — optional · type=`choice` · choices={goal, subtask}
  Filter by type (default: both)
- `--status` — optional · type=`choice` · choices={in_progress, complete, pending, completed}
  Filter by status
- `--ai-id` — optional
  Filter by AI identifier
- `--limit` — optional · type=`int` · default=`10`
  Maximum results (default: 10)
- `--sync` — optional · flag
  Sync SQLite goals to Qdrant before searching
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-complete`  _(aliases: `goal-complete`)_

Complete goal, merge branch, close BEADS issue

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to complete
- `--run-postflight` — optional · flag
  Run POSTFLIGHT before completing
- `--merge-branch` — optional · flag
  Merge git branch to main
- `--delete-branch` — optional · flag
  Delete branch after merge
- `--create-handoff` — optional · flag
  Create handoff report
- `--reason` — optional · default=`completed`
  Completion reason (for BEADS)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-claim`

Claim goal, create git branch, link to BEADS

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to claim
- `--create-branch` — optional · flag · default=`True`
  Create git branch (default: True)
- `--no-branch` — optional · flag · default=`True`
  Skip branch creation
- `--run-preflight` — optional · flag
  Run PREFLIGHT after claiming
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-add-subtask`  _(aliases: `goal-add-subtask`)_

Add subtask to existing goal

**Arguments:**

- `--goal-id` — **required**
  Goal UUID
- `--description` — **required**
  Subtask description
- `--importance` — optional · type=`choice` · choices={critical, high, medium, low} · default=`medium`
  Epistemic importance
- `--dependencies` — optional
  Dependencies as JSON array
- `--estimated-tokens` — optional · type=`int`
  Estimated token usage
- `--use-beads` — optional · flag
  Create BEADS subtask and link to goal
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-add-dependency`

Add dependency between goals (Goal A depends on Goal B)

**Arguments:**

- `--goal-id` — **required**
  Goal that has the dependency
- `--depends-on` — **required**
  Goal that must complete first
- `--type` — optional · type=`choice` · choices={blocks, informs, extends} · default=`blocks`
  Dependency type: blocks (must complete first), informs (provides context), extends (builds upon)
- `--description` — optional
  Description of dependency relationship
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-complete-subtask`  _(aliases: `goal-complete-subtask`)_

Mark subtask as complete

**Arguments:**

- `--subtask-id` — optional
  Subtask UUID (preferred)
- `--task-id` — optional
  Subtask UUID (deprecated, use --subtask-id)
- `--evidence` — optional
  Completion evidence (commit hash, file path, etc.)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-get-subtasks`

Get detailed subtask information

**Arguments:**

- `--goal-id` — **required**
  Goal UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-progress`  _(aliases: `goal-progress`)_

Get goal completion progress

**Arguments:**

- `--goal-id` — **required**
  Goal UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-discover`

Discover goals from other AIs via git

**Arguments:**

- `--from-ai-id` — optional
  Filter by AI creator
- `--session-id` — optional
  Filter by session
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-ready`

Query ready work (BEADS + epistemic filtering)

**Arguments:**

- `--session-id` — optional
  Session UUID (auto-detects active session if not provided)
- `--min-confidence` — optional · type=`float` · default=`0.7`
  Minimum confidence threshold (0.0-1.0)
- `--max-uncertainty` — optional · type=`float` · default=`0.3`
  Maximum uncertainty threshold (0.0-1.0)
- `--min-priority` — optional · type=`int`
  Minimum BEADS priority (1, 2, or 3)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-resume`

Resume another AI's goal

**Arguments:**

- `goal_id` — **required**
  Goal ID to resume
- `--ai-id` — optional · default=`empirica_cli`
  Your AI identifier
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-mark-stale`

Mark in_progress goals as stale during memory compaction

**Arguments:**

- `--session-id` — **required**
  Session UUID
- `--reason` — optional · default=`memory_compact`
  Reason for marking stale (default: memory_compact)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-get-stale`

Get stale goals that need re-evaluation after compaction

**Arguments:**

- `--session-id` — optional
  Filter by session ID
- `--project-id` — optional
  Filter by project ID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-refresh`

Refresh a stale goal back to in_progress (AI has regained context)

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to refresh
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

---

## logging

#### `empirica finding-log`  _(aliases: `fl`)_

Log a project finding (what was learned/discovered)

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin (AI-first mode)
- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID
- `--finding` — optional
  Short title — what was learned/discovered. Rendered as markdown; use --description for rich body if the title alone is too dense.
- `--description` — optional
  Optional rich markdown body — rendered in the extension and skill surfaces. Use sections, lists, code blocks, tables, links for nuance that doesn't fit the short --finding title.
- `--goal-id` — optional
  Optional goal UUID
- `--subtask-id` — optional
  Optional subtask UUID
- `--subject` — optional
  Subject/workstream identifier (auto-detected from directory if omitted)
- `--impact` — optional · type=`float`
  Impact score 0.0-1.0 (importance of this finding, auto-derived from CASCADE if omitted)
- `--scope` — optional · type=`choice` · choices={session, project, both}
  Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.
- `--source` — optional
  Source ID (from source-add). Repeatable for multiple sources.
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--edge` — optional · default=`[]`
  Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.
- `--related-to` — optional · default=`[]`
  Anchor this artifact to another (relation=related). Repeatable.
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.
- `--epistemic-source` — optional · type=`choice` · choices={intuition, search, mixed}
  How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica unknown-log`  _(aliases: `ul`)_

Log a project unknown (what's still unclear)

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin (AI-first mode)
- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID
- `--unknown` — optional
  Short title — what is unclear/unknown. Rendered as markdown; use --description for rich body when the question has context.
- `--description` — optional
  Optional rich markdown body — context behind the question, what you tried, what would resolve it. Rendered in extension and skill surfaces.
- `--goal-id` — optional
  Optional goal UUID
- `--subtask-id` — optional
  Optional subtask UUID
- `--subject` — optional
  Subject/workstream identifier (auto-detected from directory if omitted)
- `--impact` — optional · type=`float`
  Impact score 0.0-1.0 (importance of this unknown, auto-derived from CASCADE if omitted)
- `--scope` — optional · type=`choice` · choices={session, project, both}
  Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.
- `--source` — optional
  Source ID (from source-add). Repeatable for multiple sources.
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--edge` — optional · default=`[]`
  Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.
- `--related-to` — optional · default=`[]`
  Anchor this artifact to another (relation=related). Repeatable.
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.
- `--epistemic-source` — optional · type=`choice` · choices={intuition, search, mixed}
  How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica unknown-list`

List project unknowns (open or resolved)

**Arguments:**

- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID (to derive project)
- `--resolved` — optional · flag
  Show resolved unknowns instead of open
- `--all` — optional · flag
  Show both open and resolved
- `--subject` — optional
  Filter by subject/workstream
- `--limit` — optional · type=`int` · default=`30`
  Max unknowns to show (default: 30)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica unknown-resolve`

Mark unknown as resolved

**Arguments:**

- `--unknown-id` — **required**
  Unknown UUID
- `--resolved-by` — **required**
  How was this unknown resolved?
- `--finding` — optional
  Finding ID that answered this unknown (provenance link)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica deadend-log`  _(aliases: `de`)_

Log a project dead end (what didn't work)

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin (AI-first mode)
- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID
- `--approach` — optional
  Short title — what approach was tried. Rendered as markdown; use --description for the full story.
- `--why-failed` — optional
  Short title — why it failed. Rendered as markdown.
- `--description` — optional
  Optional rich markdown body — full account: what you expected, what happened, signals you noticed, what alternative might work. Rendered in extension and skill surfaces.
- `--goal-id` — optional
  Optional goal UUID
- `--subtask-id` — optional
  Optional subtask UUID
- `--subject` — optional
  Subject/workstream identifier (auto-detected from directory if omitted)
- `--impact` — optional · type=`float`
  Impact score 0.0-1.0 (importance of this dead end, auto-derived from CASCADE if omitted)
- `--scope` — optional · type=`choice` · choices={session, project, both}
  Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.
- `--source` — optional
  Source ID (from source-add). Repeatable for multiple sources.
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--edge` — optional · default=`[]`
  Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.
- `--related-to` — optional · default=`[]`
  Anchor this artifact to another (relation=related). Repeatable.
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.
- `--epistemic-source` — optional · type=`choice` · choices={intuition, search, mixed}
  How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica assumption-log`

Log an unverified assumption (what you're taking for granted)

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin (AI-first mode)
- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID
- `--assumption` — optional
  Short title — the assumption being made. Rendered as markdown; use --description to record the basis for the confidence.
- `--description` — optional
  Optional rich markdown body — what would verify or falsify the assumption, why you're leaning toward the stated confidence, how brittle it is. Rendered in extension and skill surfaces.
- `--confidence` — optional · type=`float` · default=`0.5`
  Confidence in this assumption (0.0-1.0)
- `--domain` — optional
  Domain scope (e.g., security, architecture)
- `--goal-id` — optional
  Optional goal UUID
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--edge` — optional · default=`[]`
  Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.
- `--related-to` — optional · default=`[]`
  Anchor this artifact to another (relation=related). Repeatable.
- `--source` — optional
  Source ID (from source-add). Repeatable for multiple sources.
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.
- `--epistemic-source` — optional · type=`choice` · choices={intuition, search, mixed}
  How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica decision-log`

Log a decision with alternatives and rationale

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin (AI-first mode)
- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID
- `--choice` — optional
  Short title — the choice made. Rendered as markdown; use --description for the full deliberation.
- `--alternatives` — optional
  Alternatives considered (comma-separated or JSON array)
- `--rationale` — optional
  Short rationale — why this choice was made. Rendered as markdown; use --description for extended reasoning.
- `--description` — optional
  Optional rich markdown body — extended reasoning, trade-offs table, what would change this decision, related findings. Rendered in extension and skill surfaces.
- `--confidence` — optional · type=`float` · default=`0.7`
  Confidence in this decision (0.0-1.0)
- `--reversibility` — optional · type=`choice` · choices={exploratory, committal, forced} · default=`exploratory`
  How reversible is this decision?
- `--domain` — optional
  Domain scope (e.g., security, architecture)
- `--goal-id` — optional
  Optional goal UUID
- `--evidence` — optional
  Finding ID as evidence for this decision. Repeatable for multiple findings.
- `--source` — optional
  Source ID (from source-add) for external citations. Repeatable.
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--edge` — optional · default=`[]`
  Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.
- `--related-to` — optional · default=`[]`
  Anchor this artifact to another (relation=related). Repeatable.
- `--evidence-from` — optional · default=`[]`
  Finding/source IDs that ground this decision (relation=evidence). Repeatable.
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.
- `--epistemic-source` — optional · type=`choice` · choices={intuition, search, mixed}
  How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica mistake-log`

Log a mistake for learning and future prevention

**Arguments:**

- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID (auto-derived from active transaction)
- `--mistake` — **required**
  Short title — what was done wrong. Rendered as markdown.
- `--why-wrong` — **required**
  Short explanation of why it was wrong. Rendered as markdown.
- `--cost-estimate` — optional
  Estimated time/effort wasted (e.g., "2 hours")
- `--root-cause-vector` — optional
  Epistemic vector that caused the mistake (e.g., "KNOW", "CONTEXT")
- `--prevention` — optional
  Short — how to prevent this mistake in the future. Rendered as markdown.
- `--description` — optional
  Optional rich markdown body — full account: trigger, signals you missed, recovery path, related findings/dead-ends. Rendered in extension and skill surfaces.
- `--goal-id` — optional
  Optional goal identifier this mistake relates to
- `--scope` — optional · type=`choice` · choices={session, project, both}
  Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--edge` — optional · default=`[]`
  Declare a graph edge as ID:RELATION (e.g. abc12345:supports). Repeatable.
- `--related-to` — optional · default=`[]`
  Anchor this artifact to another (relation=related). Repeatable.
- `--source` — optional
  Source ID (from source-add). Repeatable for multiple sources.
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only.
- `--epistemic-source` — optional · type=`choice` · choices={intuition, search, mixed}
  How this artifact was arrived at: intuition (training data + loaded context, no external retrieval since goal opened), search (external retrieval this session), or mixed.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica mistake-query`

Query logged mistakes

**Arguments:**

- `--session-id` — optional
  Filter by session UUID
- `--goal-id` — optional
  Filter by goal UUID
- `--limit` — optional · type=`int` · default=`10`
  Number of results (default: 10)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica refdoc-add`

Add a reference document to project (legacy — use source-add instead)

**Arguments:**

- `--project-id` — **required**
  Project UUID
- `--doc-path` — **required**
  Document path
- `--doc-type` — optional
  Document type (architecture, guide, api, design)
- `--description` — optional
  Document description
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica source-add`

Add an epistemic source (noetic: evidence IN, praxic: output OUT)

**Arguments:**

- `--title` — **required**
  Source title
- `--description` — optional
  Source description
- `--source-type` — optional · default=`document`
  Source type (document, meeting, email, calendar, code, web, design, api)
- `--path` — optional
  File path (for local documents)
- `--url` — optional
  URL (for web sources)
- `--noetic` — optional · flag
  Source used — evidence that informed knowledge (source IN)
- `--praxic` — optional · flag
  Source created — output produced by action (source OUT)
- `--confidence` — optional · type=`float` · default=`0.7`
  Confidence in source quality (0.0-1.0, default: 0.7)
- `--session-id` — optional
  Session ID (auto-derived from transaction)
- `--project-id` — optional
  Project ID (auto-derived from context)
- `--entity-type` — optional · type=`choice` · choices={project, organization, contact, engagement}
  Entity type this artifact relates to (default: project)
- `--entity-id` — optional
  Entity UUID (organization, contact, or engagement ID)
- `--via` — optional
  Discovery channel (cli, email, linkedin, calendar, agent, web)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica act-log`

Log actions taken during ACT phase

**Arguments:**

- `--session-id` — optional
  Session ID (auto-derived from active transaction)
- `--actions` — **required**
  JSON array of actions taken
- `--artifacts` — optional
  JSON array of files modified/created
- `--goal-id` — optional
  Goal UUID being worked on
- `--output` — optional · type=`choice` · choices={json, text} · default=`text`
  Output format (json or text)
- `--verbose` — optional · flag
  Verbose output

#### `empirica investigate-log`

Log investigation findings during INVESTIGATE phase

**Arguments:**

- `--session-id` — optional
  Session ID (auto-derived from active transaction)
- `--findings` — **required**
  JSON array of findings discovered
- `--evidence` — optional
  JSON object with evidence (file paths, line numbers, etc.)
- `--output` — optional · type=`choice` · choices={json, text} · default=`text`
  Output format (json or text)
- `--verbose` — optional · flag
  Verbose output

#### `empirica log-artifacts`

Batch log connected artifacts with graph format (nodes + edges)

**Arguments:**

- `config` — **required** · default=`-`
  JSON file or - for stdin (default: stdin)
- `--schema` — optional · flag
  Print the input JSON schema and exit (use this to learn the shape)
- `--session-id` — optional
  Session UUID (auto-derived)
- `--project-id` — optional
  Project UUID (auto-derived)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed output

#### `empirica resolve-artifacts`

Batch resolve open artifacts (unknowns, assumptions, goals)

**Arguments:**

- `config` — **required** · default=`-`
  JSON file or - for stdin (default: stdin)
- `--schema` — optional · flag
  Print the input JSON schema and exit
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed output

#### `empirica delete-artifacts`

Batch delete non-pertinent artifacts (anti-pollution cleanup)

**Arguments:**

- `config` — **required** · default=`-`
  JSON file or - for stdin (default: stdin)
- `--schema` — optional · flag
  Print the input JSON schema and exit
- `--dry-run` — optional · flag
  Preview deletions without executing
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed output

#### `empirica epistemics-list`

List epistemic trajectory

**Arguments:**

- `--session-id` — optional
  Session ID (auto-derived from active transaction)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica epistemics-show`

Show epistemic trajectory details

**Arguments:**

- `--session-id` — optional
  Session ID (auto-derived from active transaction)
- `--phase` — optional
  Filter by phase (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica noetic-batch`

Batched investigation: reads + greps + globs + investigate in one call

**Arguments:**

- `config` — **required**
  JSON config file path, or "-" for stdin (AI-first mode)
- `--intent` — optional
  One-line investigation goal (alternative to JSON config)
- `--read` — optional
  File path to read (repeatable)
- `--grep` — optional
  Grep spec: "pattern" or "pattern:glob" or "pattern:glob:context=N" (repeatable)
- `--glob` — optional
  Glob pattern (repeatable)
- `--investigate` — optional
  project-search query (repeatable)
- `--project-root` — optional
  Project root for relative paths. Default: InstanceResolver.project_path() (the active Empirica project), falling back to cwd if unresolvable. Pass explicitly to override.
- `--schema` — optional · flag
  Print the input JSON schema and exit
- `--dry-run` — optional · flag
  Validate input without executing operations
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)

---

## project

#### `empirica project-init`

Initialize Empirica in a new git repository (creates config files)

**Arguments:**

- `--project-name` — optional
  Project name (defaults to repo name)
- `--project-description` — optional
  Project description
- `--project-id` — optional
  Link to existing workspace project ID (skip DB creation, reuse existing)
- `--enable-beads` — optional · flag
  Enable BEADS by default
- `--create-semantic-index` — optional · flag
  Create SEMANTIC_INDEX.yaml template
- `--type` — optional · type=`choice` · choices={software, content, research, data, design, operations, strategic, engagement, legal}
  Project type (default: software)
- `--domain` — optional
  Domain taxonomy (e.g., ai/measurement)
- `--classification` — optional · type=`choice` · choices={open, internal, restricted} · default=`internal`
  Access classification
- `--evidence-profile` — optional · type=`choice` · choices={code, prose, web, hybrid, auto} · default=`auto`
  Evidence profile for grounded calibration
- `--languages` — optional · type=`list`
  Programming languages
- `--tags` — optional · type=`list`
  Project tags
- `--non-interactive` — optional · flag
  Skip interactive prompts
- `--force` — optional · flag
  Reinitialize if already initialized
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica project-update`

Update project.yaml fields (type, domain, contacts, edges, etc.)

**Arguments:**

- `--type` — optional · type=`choice` · choices={software, content, research, data, design, operations, strategic, engagement, legal}
  Project type
- `--domain` — optional
  Domain taxonomy (e.g., ai/measurement)
- `--classification` — optional · type=`choice` · choices={open, internal, restricted}
  Access classification
- `--status` — optional · type=`choice` · choices={active, dormant, archived}
  Project status
- `--evidence-profile` — optional · type=`choice` · choices={code, prose, web, hybrid, auto}
  Evidence profile for grounded calibration
- `--languages` — optional · type=`list`
  Set programming languages
- `--tags` — optional · type=`list`
  Set project tags (replaces all)
- `--add-tag` — optional
  Add a single tag
- `--remove-tag` — optional
  Remove a single tag
- `--add-contact` — optional
  Add contact by ID
- `--roles` — optional · type=`list`
  Roles for --add-contact (e.g., owner architect)
- `--remove-contact` — optional
  Remove contact by ID
- `--add-edge` — optional
  Add edge to entity (e.g., project/empirica-iris)
- `--relation` — optional
  Relation type for --add-edge (default: related)
- `--remove-edge` — optional
  Remove edge to entity
- `--migrate` — optional · flag
  Upgrade v1.0 to v2.0 with auto-detected values
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed info

#### `empirica project-create`

Create a new project for multi-repo tracking

**Arguments:**

- `--name` — **required**
  Project name
- `--description` — optional
  Project description
- `--path` — optional
  Path to git repo — also initializes .empirica/ filesystem config (bridges project-create + project-init)
- `--repos` — optional
  JSON array of repository names (e.g., '["empirica", "empirica-dev"]')
- `--type` — optional · type=`choice` · choices={product, application, feature, research, documentation, infrastructure, operations} · default=`product`
  Project type for workspace categorization
- `--tags` — optional
  Tags for categorization (comma-separated or JSON array)
- `--parent` — optional
  Parent project ID for hierarchical organization
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica project-list`

List all projects

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica project-switch`

Switch to a different project with clear context banner

**Arguments:**

- `project_identifier` — **required**
  Project name or UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--claude-session-id` — optional
  Claude Code conversation UUID (for instance isolation)

#### `empirica project-bootstrap`  _(aliases: `pb`, `bootstrap`)_

Show epistemic breadcrumbs for project

**Arguments:**

- `--project-id` — optional
  Project UUID or name (auto-detected from git remote if omitted)
- `--session-id` — optional
  Session UUID (auto-resolved from project if omitted)
- `--ai-id` — optional
  AI identifier to load epistemic handoff for (e.g., claude-code)
- `--subject` — optional
  Subject/workstream to filter by (auto-detected from directory if omitted)
- `--check-integrity` — optional · flag
  Analyze doc-code integrity (adds ~2s)
- `--context-to-inject` — optional · flag
  Generate markdown context for AI prompt injection
- `--task-description` — optional
  Task description for context load balancing
- `--epistemic-state` — optional
  Epistemic vectors from PREFLIGHT as JSON string (e.g., '{"uncertainty":0.8,"know":0.3}')
- `--include-live-state` — optional · flag
  Include current epistemic vectors + git state
- `--trigger` — optional · type=`choice` · choices={pre_compact, post_compact, manual}
  Compact boundary trigger for session auto-resolution
- `--depth` — optional · type=`choice` · choices={minimal, moderate, full, auto} · default=`auto`
  Context depth: minimal (~500 tokens), moderate (~1500), full (~3000-5000), auto (drift-based)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info
- `--global` — optional · flag
  Include global cross-project learnings (requires --task-description)

#### `empirica project-handoff`

Create project-level handoff report

**Arguments:**

- `--project-id` — **required**
  Project UUID
- `--summary` — **required**
  Project summary
- `--key-decisions` — optional
  JSON array of key decisions
- `--patterns` — optional
  JSON array of patterns discovered
- `--remaining-work` — optional
  JSON array of remaining work
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica project-search`

Semantic search for relevant docs/memory by task description

**Arguments:**

- `--project-id` — **required**
  Project UUID
- `--task` — **required**
  Task description to search for
- `--type` — optional · type=`choice` · choices={focused, all, intelligence, docs, memory, eidetic, episodic, assumptions, decisions, goals} · default=`focused`
  Result type: focused (docs+eidetic+episodic), all, intelligence (goals+decisions+assumptions), or single collection
- `--limit` — optional · type=`int` · default=`5`
  Number of results to return (default: 5)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info
- `--global` — optional · flag
  Include global cross-project learnings in search

#### `empirica project-embed`

Embed project docs & memory into Qdrant for semantic search

**Arguments:**

- `--project-id` — **required**
  Project UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info
- `--global` — optional · flag
  Sync high-impact items to global learnings collection
- `--min-impact` — optional · type=`float` · default=`0.7`
  Minimum impact for global sync (default: 0.7)

#### `empirica code-embed`

Extract and embed Python API surfaces into Qdrant for semantic search

**Arguments:**

- `--project-id` — **required**
  Project UUID
- `--path` — optional
  Root directory to scan (default: project root from DB, or cwd)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica doc-check`

Compute documentation completeness and suggest updates

**Arguments:**

- `--project-id` — **required**
  Project UUID
- `--session-id` — optional
  Optional session UUID for context
- `--goal-id` — optional
  Optional goal UUID for context
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica bootstrap-context`

Emit the bootstrap context payload (schema v2) — three-circle artifact graph

**Arguments:**

- `--project-path` — optional
  Project root (default: resolve via InstanceResolver canonical chain).
- `--session-id` — optional
  Active session UUID (informational; queries scope by project_id).
- `--similarity-threshold` — optional · type=`float` · default=`0.65`
  Cosine threshold for circle 3 topic-relevance pull (default: 0.65).
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json — what hooks/MCP consume).

#### `empirica projects-discover`

Walk filesystem for .empirica/ directories and emit a manifest.

**Arguments:**

- `--root` — optional
  Root directory to walk (default: $HOME). Repeatable.
- `--max-depth` — optional · type=`int` · default=`5`
  Maximum walk depth from each root (default: 5).
- `--include-hidden` — optional · flag
  Walk hidden directories (default: skip).
- `--output` — optional · type=`choice` · choices={yaml, json} · default=`yaml`
  Output format (default: yaml).
- `--manifest` — optional
  Write manifest to this path (default: ~/.empirica/discovered_projects.yaml). Use '-' to write to stdout only.
- `--register` — optional · flag
  After scanning, upsert each discovered project into ~/.empirica/registry.yaml (the daemon's served set). Idempotent — matches on project_id. (v1.9.6+)
- `--prune` — optional · flag
  Only with --register: also remove registry entries whose path no longer exists or no longer contains .empirica/.

#### `empirica projects-list`

List discovered local Empirica projects.

**Arguments:**

- `--output` — optional · type=`choice` · choices={yaml, json, table} · default=`table`
  Output format (default: table).
- `--manifest` — optional
  Read manifest from this path (default: ~/.empirica/discovered_projects.yaml).
- `--refresh` — optional · flag
  Force a fresh discover scan even if cache exists.

#### `empirica projects-bulk-register`

[CORTEX] Register all discovered projects on the Cortex backend.

**Arguments:**

- `--from` — optional
  Manifest YAML to read (default: ~/.empirica/discovered_projects.yaml). Falls back to running projects-discover live if absent.
- `--include` — optional
  Regex matched against project name OR path. Repeatable — multi --include is OR (project kept if ANY pattern matches). If no --include is given, all projects pass the include stage.
- `--exclude` — optional
  Regex matched against project name OR path. Repeatable — multi --exclude is OR (project dropped if ANY pattern matches). Applied after --include.
- `--dry-run` — optional · flag
  Show what would be registered without making HTTP calls.
- `--force-metadata-update` — optional · flag
  Set `force_metadata_update: true` in each request body. Cortex's safe-update logic then backfills UUID-shaped placeholder names + empty repo_urls on already-existing rows. Useful when Cortex has stale metadata that should be refreshed from the local registry. (v1.9.6+)
- `--from-discovered` — optional · flag
  Source projects from the raw scanner output (~/.empirica/discovered_projects.yaml) instead of the curated daemon registry (~/.empirica/registry.yaml, the default). Use when you want to register EVERY project you have on disk, not just the curated set the daemon serves. (v1.9.6+)
- `--cortex-url` — optional
  Override Cortex base URL (default: $CORTEX_REMOTE_URL).
- `--api-key` — optional
  Override Cortex API key (default: $CORTEX_API_KEY).
- `--timeout` — optional · type=`float` · default=`10.0`
  Per-request timeout in seconds (default: 10).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format for the summary (default: human).

---

## workspace

#### `empirica workspace-init`

Initialize workspace with epistemic self-awareness (uses CASCADE workflow)

**Arguments:**

- `--path` — optional · type=`str`
  Workspace path (defaults to current directory)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--non-interactive` — optional · flag
  Skip user questions, use defaults
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica workspace-map`

Discover git repositories in parent directory and show epistemic health

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica workspace-list`

List projects with types, tags, and hierarchical relationships

**Arguments:**

- `--type` — optional · type=`choice` · choices={product, application, feature, research, documentation, infrastructure, operations}
  Filter by project type
- `--tags` — optional
  Filter by tags (comma-separated, matches any)
- `--parent` — optional
  Show only children of this project ID
- `--tree` — optional · flag
  Show hierarchical tree view
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica workspace-overview`

Show epistemic health overview of all projects in workspace

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--sort-by` — optional · type=`choice` · choices={activity, knowledge, uncertainty, name} · default=`activity`
  Sort projects by
- `--filter` — optional · type=`choice` · choices={active, inactive, complete}
  Filter projects by status
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica workspace-search`

Search across all projects by entity or semantic query

**Arguments:**

- `--entity` — optional
  Entity filter: TYPE/ID (e.g., contact/david, org/acme)
- `--task` — optional
  Semantic search query
- `--project-id` — optional
  Restrict to specific project
- `--limit` — optional · type=`int` · default=`20`
  Maximum results
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format

#### `empirica engagement-focus`

Set active engagement for current transaction (auto-links all artifacts)

**Arguments:**

- `engagement_id` — **required**
  Engagement UUID or name
- `--clear` — optional · flag
  Clear active engagement
- `--output` — optional · type=`choice` · choices={json, default} · default=`json`
  Output format

#### `empirica ecosystem-check`

Analyze ecosystem dependencies, impact, and health from ecosystem.yaml

**Arguments:**

- `--file` — optional
  File or module path to check impact for
- `--project` — optional
  Project name to check downstream/upstream
- `--role` — optional
  Filter projects by role (core, extension, ecosystem-tool, etc.)
- `--tag` — optional
  Filter projects by tag
- `--validate` — optional · flag
  Validate manifest integrity
- `--manifest` — optional
  Path to ecosystem.yaml (auto-detected if not specified)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica save`

Save current work (git add + commit with auto-generated message)

**Arguments:**

- `--message` / `-m` — optional
  Custom commit message
- `--output` — optional · type=`choice` · choices={json, default} · default=`json`
  Output format

#### `empirica history`

Show epistemic timeline from git log + notes

**Arguments:**

- `--entity` — optional
  Filter by entity: TYPE/ID
- `--limit` — optional · type=`int` · default=`20`
  Maximum entries
- `--output` — optional · type=`choice` · choices={json, human} · default=`human`
  Output format

---

## checkpoint

#### `empirica checkpoint-create`

Create git checkpoint for session (Phase 1.5/2.0)

**Arguments:**

- `--session-id` — **required**
  Session ID (required)
- `--phase` — **required** · type=`choice` · choices={PREFLIGHT, CHECK, ACT, POSTFLIGHT}
  Workflow phase (required)
- `--round` — optional · type=`int` · default=`1`
  Round number (optional, default: 1)
- `--metadata` — optional
  JSON metadata (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica checkpoint-load`

Load latest checkpoint for session

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--max-age` — optional · type=`int` · default=`24`
  Max age in hours (default: 24)
- `--phase` — optional
  Filter by specific phase (optional)
- `--output` — optional · type=`choice` · choices={table, json} · default=`table`
  Output format (also accepts --output json)
- `--format` — optional · type=`choice` · choices={json, table}
  Output format (deprecated, use --output)
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica checkpoint-list`

List checkpoints for session

**Arguments:**

- `--session-id` — optional
  Session ID (optional, lists all if omitted)
- `--limit` — optional · type=`int` · default=`10`
  Maximum checkpoints to show
- `--phase` — optional
  Filter by phase (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica checkpoint-diff`

Show vector differences from last checkpoint

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--threshold` — optional · type=`float` · default=`0.15`
  Significance threshold
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica checkpoint-sign`

Sign checkpoint with AI identity (Phase 2 - Crypto)

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--phase` — **required** · type=`choice` · choices={PREFLIGHT, CHECK, ACT, POSTFLIGHT}
  Workflow phase
- `--round` — **required** · type=`int`
  Round number
- `--ai-id` — **required**
  AI identity to sign with
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica checkpoint-verify`

Verify signed checkpoint (Phase 2 - Crypto)

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--phase` — **required** · type=`choice` · choices={PREFLIGHT, CHECK, ACT, POSTFLIGHT}
  Workflow phase
- `--round` — **required** · type=`int`
  Round number
- `--ai-id` — optional
  AI identity (uses embedded public key if omitted)
- `--public-key` — optional
  Public key hex (overrides AI ID)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica checkpoint-signatures`

List all signed checkpoints (Phase 2 - Crypto)

**Arguments:**

- `--session-id` — optional
  Filter by session ID (optional)
- `--ai-id` — optional
  AI identity (only needed if no local identities exist)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

---

## sync

#### `empirica sync-config`

Configure sync settings (remote, visibility, provider)

**Arguments:**

- `key` — **required**
  Config key to get/set (enabled, remote, visibility, provider)
- `value` — **required**
  Value to set
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica sync-push`

Push all epistemic notes to remote

**Arguments:**

- `--remote` — optional
  Git remote name (uses config default if not specified)
- `--dry-run` — optional · flag
  Show what would be pushed without pushing
- `--force` — optional · flag
  Push even if sync is disabled in config
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica sync-pull`

Pull all epistemic notes from remote

**Arguments:**

- `--remote` — optional
  Git remote name (uses config default if not specified)
- `--rebuild` — optional · flag
  Also rebuild SQLite from notes after pull
- `--force` — optional · flag
  Pull even if sync is disabled in config
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica sync-status`

Show sync status (local note counts, remote availability)

**Arguments:**

- `--remote` — optional
  Git remote name (uses config default if not specified)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica rebuild`

Reconstruct SQLite from git notes

**Arguments:**

- `--from-notes` — optional · flag · default=`True`
  Rebuild from git notes (default)
- `--qdrant` — optional · flag
  Also rebuild Qdrant embeddings
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica artifacts-generate`

Generate browsable .empirica/ markdown files from git notes

**Arguments:**

- `--output-dir` — optional
  Output directory (default: .empirica/)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

---

## profile

#### `empirica profile-sync`

Sync epistemic profile: fetch notes → import to SQLite → rebuild Qdrant

**Arguments:**

- `--remote` — optional
  Git remote to sync with (default: from sync config, typically "forgejo")
- `--push` — optional · flag
  Push local notes to remote after import (bidirectional sync)
- `--qdrant` — optional · flag
  Rebuild Qdrant semantic index after import
- `--import-only` — optional · flag
  Skip fetch, only import existing local git notes into SQLite
- `--force` — optional · flag
  Force sync even if disabled in config
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)

#### `empirica profile-prune`

Prune low-value artifacts with transparent audit receipts in git notes

**Arguments:**

- `--rule` — optional · type=`choice` · choices={stale-resolved-unknowns, test-transactions, low-impact-findings, falsified-assumptions, old-dead-ends, low-confidence-imports}
  Apply a specific mechanical pruning rule
- `--artifact-id` — optional
  Prune a specific artifact by UUID
- `--artifact-type` — optional · type=`choice` · choices={finding, unknown, dead_end, mistake, goal}
  Type of artifact to prune (required with --artifact-id)
- `--reason` — optional
  Reason for pruning (recorded in prune receipt)
- `--older-than` — optional · type=`int`
  Only prune artifacts older than N days
- `--scope` — optional · type=`choice` · choices={memory}
  Prune scope: "memory" archives stale CC memory files (promoted_*.md)
- `--dry-run` — optional · flag
  Show what would be pruned without actually removing anything
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)

#### `empirica profile-status`

Show epistemic profile status: artifact counts, sync state, calibration

**Arguments:**

- `--remote` — optional
  Git remote to check sync state against (default: from sync config)
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)

#### `empirica profile-import`

Import epistemic artifacts from AI conversation transcripts

**Arguments:**

- `--source` — **required** · type=`choice` · choices={claude-code, claude-ai}
  Source platform to import from
- `--project` — optional
  Claude Code project directory name to import from (default: auto-discover from .claude/projects/)
- `--file` — optional
  Path to Claude.ai export JSON file (required for --source claude-ai)
- `--session` — optional
  Import a specific session by ID (Claude Code only)
- `--min-confidence` — optional · type=`float` · default=`0.5`
  Minimum extraction confidence to include (0.0-1.0, default: 0.5)
- `--dry-run` — optional · flag
  Show what would be imported without storing anything
- `--include-sidechains` — optional · flag
  Include subagent/sidechain conversations (Claude Code only)
- `--output` — optional · type=`choice` · choices={json, text} · default=`text`
  Output format (default: text)

---

## identity

#### `empirica identity-create`

Create new AI identity with Ed25519 keypair

**Arguments:**

- `--ai-id` — **required**
  AI identifier
- `--overwrite` — optional · flag
  Overwrite existing identity
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica identity-export`

Export public key for sharing

**Arguments:**

- `--ai-id` — **required**
  AI identifier
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica identity-list`

List all AI identities

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica identity-verify`

Verify signed session

**Arguments:**

- `session_id` — **required**
  Session ID to verify
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

---

## handoff

#### `empirica handoff-create`

Create handoff report: epistemic (with CASCADE deltas) or planning (documentation-only)

**Arguments:**

- `config` — **required**
  JSON config file path or "-" for stdin (AI-first mode)
- `--session-id` — optional
  Session UUID (auto-derived from active transaction)
- `--task-summary` — optional
  What was accomplished (2-3 sentences) (required)
- `--summary` — optional
  Alias for --task-summary
- `--key-findings` — optional
  JSON array of findings (required)
- `--findings` — optional
  Alias for --key-findings
- `--remaining-unknowns` — optional
  JSON array of unknowns (optional)
- `--unknowns` — optional
  Alias for --remaining-unknowns
- `--next-session-context` — optional
  Critical context for next session (required)
- `--artifacts` — optional
  JSON array of files created (optional)
- `--planning-only` — optional · flag
  Create planning handoff (no CASCADE workflow required) instead of epistemic handoff
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica handoff-query`

Query handoff reports

**Arguments:**

- `--session-id` — optional
  Specific session UUID
- `--ai-id` — optional
  Filter by AI ID
- `--limit` — optional · type=`int` · default=`5`
  Number of results (default: 5)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

---

## issue

#### `empirica issue-list`

List captured issues

**Arguments:**

- `--session-id` — optional
  Session ID to list issues for (session-scoped)
- `--project-id` — optional
  Project ID to list issues for (project-scoped, shows all sessions)
- `--status` — optional · type=`choice` · choices={new, investigating, handoff, resolved, wontfix}
  Filter by issue status
- `--category` — optional · type=`choice` · choices={bug, error, warning, deprecation, todo, performance, compatibility, design, other}
  Filter by issue category
- `--severity` — optional · type=`choice` · choices={blocker, high, medium, low}
  Filter by severity level
- `--limit` — optional · type=`int` · default=`100`
  Maximum number of issues to return (default: 100)
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)

#### `empirica issue-show`

Show detailed issue information

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--issue-id` — **required**
  Issue ID to show
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)

#### `empirica issue-handoff`

Mark issue for handoff to another AI

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--issue-id` — **required**
  Issue ID to hand off
- `--assigned-to` — **required**
  AI ID or name to assign this issue to
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)

#### `empirica issue-resolve`

Mark issue as resolved

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--issue-id` — **required**
  Issue ID that was resolved
- `--resolution` — **required**
  How was this issue resolved?
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)

#### `empirica issue-export`

Export issues for handoff

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--assigned-to` — **required**
  AI ID to export issues for
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)

#### `empirica issue-stats`

Show issue capture statistics

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)

---

## investigation

#### `empirica investigate`

Investigate file/directory/concept

**Arguments:**

- `target` — **required**
  Target to investigate
- `--session-id` — optional
  Session ID (for noetic recalibration - loads context anchor via project-bootstrap)
- `--type` — optional · type=`choice` · choices={auto, file, directory, concept, comprehensive} · default=`auto`
  Investigation type. Use "comprehensive" for deep analysis (replaces analyze command)
- `--context` — optional
  JSON context data
- `--detailed` — optional · flag
  Show detailed investigation
- `--verbose` — optional · flag
  Show detailed investigation
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format. empirica-mcp always passes --output json; bare CLI users get human by default.

#### `empirica investigate-create-branch`

Create parallel investigation branch (epistemic auto-merge)

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--investigation-path` — **required**
  What is being investigated (e.g., oauth2)
- `--description` — optional
  Description of investigation
- `--preflight-vectors` — optional
  Epistemic vectors at branch start (JSON)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica investigate-checkpoint-branch`

Checkpoint branch after investigation

**Arguments:**

- `--branch-id` — **required**
  Branch ID
- `--postflight-vectors` — **required**
  Epistemic vectors after investigation (JSON)
- `--tokens-spent` — optional
  Tokens spent in investigation
- `--time-spent` — optional
  Time spent in investigation (minutes)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica investigate-merge-branches`

Auto-merge best branch based on epistemic scores

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--round` — optional
  Investigation round number
- `--tag-losers` — optional · flag
  Auto-tag losing branches as dead ends with divergence reason
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica investigate-multi`

Multi-persona parallel investigation with epistemic auto-merge

**Arguments:**

- `--task` — **required**
  Task for all personas to investigate
- `--personas` — **required**
  Comma-separated persona IDs (e.g., security,ux,performance)
- `--session-id` — **required**
  Session ID
- `--context` — optional
  Additional context from parent investigation
- `--aggregate-strategy` — optional · type=`choice` · choices={epistemic-score, consensus, all} · default=`epistemic-score`
  How to merge results (default: epistemic-score)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

---

## monitoring

#### `empirica monitor`

Monitoring dashboard and statistics

**Arguments:**

- `--export` — optional
  Export data to file (replaces monitor-export)
- `--reset` — optional · flag
  Reset statistics (replaces monitor-reset)
- `--cost` — optional · flag
  Show cost analysis (replaces monitor-cost)
- `--history` — optional · flag
  Show recent request history
- `--health` — optional · flag
  Include adapter health checks
- `--turtle` — optional · flag
  Show epistemic health: flow state, transaction completeness, unknowns/findings
- `--project` — optional · flag
  Show cost projections (with --cost)
- `--output` — optional · type=`choice` · choices={json, csv} · default=`json`
  Export format (with --export)
- `--yes` / `-y` — optional · flag
  Skip confirmation (with --reset)
- `--verbose` — optional · flag
  Show detailed stats

#### `empirica assess-state`

Capture sessionless epistemic state (for statusline, monitoring, compact boundaries)

**Arguments:**

- `--session-id` — optional
  Session UUID (optional, for context)
- `--prompt` — optional
  Self-assessment context/evidence (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed output
- `--turtle` — optional · flag
  Recursive grounding check: verify observer stability before observing (Noetic Handshake)

#### `empirica trajectory-project`

Project viable epistemic paths forward based on current grounding (Turtle Telescope)

**Arguments:**

- `--session-id` — optional
  Session UUID for context
- `--turtle` — optional · flag
  Include full turtle stack in projection
- `--depth` — optional · type=`choice` · choices={1, 2, 3} · default=`3`
  Projection depth: 1=immediate, 2=short-term, 3=strategic (default: 3)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed reasoning for each path

#### `empirica efficiency-report`

Show token efficiency report

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica workflow-patterns`

Detect repeated workflow patterns across transactions (tool sequence mining)

**Arguments:**

- `--limit` — optional · type=`int` · default=`50`
  Number of recent transactions to analyze (default: 50)
- `--min-frequency` — optional · type=`int` · default=`2`
  Minimum transaction count for a pattern (default: 2)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica calibration-report`

Generate calibration report from grounded evidence

**Arguments:**

- `--ai-id` — optional
  Filter by AI identifier (default: claude-code)
- `--weeks` — optional · type=`int` · default=`8`
  Number of weeks to analyze (default: 8)
- `--include-tests` — optional · flag
  Include test sessions in analysis (normally filtered)
- `--min-samples` — optional · type=`int` · default=`10`
  Minimum samples per vector for confident analysis (default: 10)
- `--output` — optional · type=`choice` · choices={human, json, markdown} · default=`human`
  Output format (default: human)
- `--update-prompt` — optional · flag
  Generate copy-paste ready calibration table for system prompts
- `--verbose` — optional · flag
  Show detailed per-vector analysis
- `--learning-trajectory` — optional · flag
  Show learning trajectory (PREFLIGHT→POSTFLIGHT deltas) - NOT calibration
- `--trajectory` — optional · flag
  Show calibration trend over time (closing/widening/stable)
- `--list-disputes` — optional · flag
  Show all calibration disputes (open and resolved)
- `--brier` — optional · flag
  Show Brier score decomposition per phase (reliability, resolution, uncertainty)

#### `empirica commit-context`

Show artifacts (git notes under refs/notes/empirica/*) anchored to commits

**Arguments:**

- `commit` — **required**
  Commit SHA or ref (default mode)
- `--range` — optional
  Git rev range, e.g. HEAD~10..HEAD
- `--since` — optional
  Date string (e.g. 2026-04-01) — uses git log --since
- `--until` — optional
  Date string — uses git log --until
- `--session` — optional
  Empirica session_id prefix — all commits in session window
- `--depth` — optional · type=`int`
  Walk artifact graph edges to depth N (default: 0, no walk)
- `--full` — optional · flag
  Include full artifact JSON payloads in output
- `--only-with-artifacts` — optional · flag
  Skip commits that have no notes (human output only)
- `--rebuild-index` — optional · flag
  Force rebuild of the commit→artifact index cache
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
- `--verbose` — optional · flag
  Show indexing progress

#### `empirica compact-analysis`

Analyze epistemic loss during memory compaction

**Arguments:**

- `--include-tests` — optional · flag
  Include test sessions in analysis (normally filtered)
- `--min-findings` — optional · type=`int`
  Minimum findings count to include session (default: 0)
- `--limit` — optional · type=`int` · default=`20`
  Maximum compact events to analyze (default: 20)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica compliance-report`

Generate compliance report mapped to regulatory frameworks

**Arguments:**

- `--tests` — optional · flag
  Include test suite execution (slow)
- `--dep-audit` — optional · flag
  Include dependency CVE audit
- `--security` — optional · flag
  Include semgrep OWASP security scan
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`
  Output format (default: text)

---

## cockpit

#### `empirica status`

Cockpit overview — per-instance phase, Sentinel, loops, transactions

**Arguments:**

- `--all` — optional · flag
  Show every discoverable instance
- `--instance` — optional
  Limit to a single instance
- `--include-dead` — optional · flag
  Show instances whose Claude process is dead (diagnostic — by default only live instances are listed)
- `--pretty` — optional · flag
  ANSI colored layout (default for TTY)
- `--json` — optional · flag
  Machine-readable JSON output (default for pipes)
- `--output` — optional · type=`choice` · choices={human, json}
  Explicit output format (overrides --pretty/--json)

#### `empirica tui`

Launch the interactive cockpit (Textual app — clickable controls)

**Arguments:**

- `--include-dead` — optional · flag
  Show instances whose Claude process is dead (diagnostic — toggle in-app with D)

#### `empirica sentinel`

Sentinel pause/resume/status (per-instance noetic firewall control)

**Subcommands:**

##### `empirica sentinel pause`

Pause Sentinel for an instance

**Arguments:**

- `--reason` — optional
  Optional human-readable reason for the pause
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica sentinel resume`

Resume Sentinel for an instance

**Arguments:**

- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica sentinel status`

Show Sentinel pause state

**Arguments:**

- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


#### `empirica loop`

Loop registry: register, pause, heartbeat per-instance scheduled work

**Subcommands:**

##### `empirica loop register`

Register a loop (idempotent)

**Arguments:**

- `--name` — **required**
  Loop name (alphanumeric, dot, dash, underscore)
- `--kind` — **required** · type=`choice` · choices={cron, interval, monitor}
  Loop kind: cron | interval | monitor
- `--cron` — optional
  Cron expression (for kind=cron)
- `--interval` — optional
  Interval like "5m", "30s", "2h" (for kind=interval)
- `--description` — optional
  Optional human-readable description
- `--backoff` — optional · type=`choice` · choices={none, exponential}
  Backoff policy when empty fires accumulate (default: none)
- `--base-interval` — optional
  Backoff floor — used after a found/fail fire (default: 15m)
- `--max-interval` — optional
  Backoff ceiling — cap on stretched interval (default: 4h)
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop unregister`

Remove a loop from the registry

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop pause`

Pause a loop (writes pause sidecar)

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop resume`

Resume a loop (removes pause sidecar)

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop set-interval`

Update a registered loop interval

**Arguments:**

- `name` — **required**
  Loop name
- `interval` — **required**
  New interval (e.g. "5m")
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop heartbeat`

Record a loop fire (call after each run)

**Arguments:**

- `name` — **required**
  Loop name
- `--status` — optional · type=`choice` · choices={ok, fail} · default=`ok`
  Run status (default: ok)
- `--result` — optional · type=`choice` · choices={found, empty, fail, paused}
  Signal: found (new work), empty (no work), fail (errored), paused (body short-circuited). Defaults from --status if omitted.
- `--message` — optional
  Optional summary message for this fire
- `--next-scheduled-job-id` — optional
  Opaque scheduler job id for the next fire — pause uses it to cancel future fires (PROPOSAL_LOOP_SELF_SCHEDULING)
- `--scheduler-kind` — optional · type=`choice` · choices={cron-create, systemd-user, system-cron, at-queue, unknown}
  Which scheduler installed the next fire
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop should-fire`

Exit 0 if loop body should run this fire, exit 1 if backoff says skip

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop poke`

Manual escape hatch — zero the streak, clear next_fire_threshold

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop schedule-next`

Compute the next-fire timestamp + cron expression. Body uses this to install the next one-shot fire.

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop fire`

Manually trigger one fire of the loop body. Bootstraps after resume on Claude Code (CronCreate-mode only emits a hint).

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop install-request`

Cockpit→Claude install: register loop + queue a pending install request the target Claude picks up via UserPromptSubmit and installs via /loop / CronCreate.

**Arguments:**

- `--name` — **required**
  Loop name
- `--interval` — **required**
  Base interval (e.g. "15m"). Acts as the cadence after a found fire and the floor for backoff.
- `--description` — optional
  One-line description
- `--base-interval` — optional
  Backoff floor (default: same as --interval)
- `--max-interval` — optional · default=`4h`
  Backoff ceiling (default: 4h)
- `--body-skill` — optional
  Optional: paired skill name whose `## Cron Prompt Template` section becomes the install request prompt_template. Auto-resolved from canonical_loops.CANONICAL_LOOPS by loop name when not given.
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop list`

List all loops registered for an instance

**Arguments:**

- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop status`

Show status for a single loop

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop enable`

Install + start a systemd-user timer for this loop (Phase 1a — wake-from-idle bridge via Monitor armed at SessionStart).

**Arguments:**

- `name` — **required**
  Loop name
- `--interval` — **required**
  systemd time spec: 30s | 5min | 1h
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop disable`

Stop + remove the systemd-user timer for this loop. Idempotent — no error if the loop was never enabled.

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop systemd-status`

Query systemctl for the timer state (is-active, is-enabled, last/next trigger). Separate from `status` which inspects the in-DB registry + pause sidecar.

**Arguments:**

- `name` — **required**
  Loop name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop tick`

ExecStart target for systemd-user .service units. Appends one JSON event to ~/.empirica/loop_fires.log (Monitor bridge input). Internal — but callable manually for testing or manual fire.

**Arguments:**

- `instance_id` — **required**
  Instance identifier
- `name` — **required**
  Loop name


##### `empirica loop listen`

Long-running ntfy listener — push-primary wake mechanism. Holds an HTTP stream to cortex ntfy topic, prints one JSON event line to stdout per ECO-decided proposal change. Runs forever; SessionStart hook arms a Monitor on its stdout. On disconnect: runs one catch-up content_poll, reconnects.

**Arguments:**

- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--loop-name` — optional · default=`cortex-mailbox-poll`
  Canonical loop name to attribute events to (default: cortex-mailbox-poll)


##### `empirica loop listen-install`

Install the persistent listener service for an ai_id. Auto-detects OS (systemd-user / launchd). The service runs `empirica loop listen --instance <ai_id>` with auto-restart, so wake events arrive even when no Claude session is open.

**Arguments:**

- `--ai-id` — optional
  AI identifier (default: project basename via project.yaml)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop listen-uninstall`

Stop + remove the persistent listener service. Idempotent.

**Arguments:**

- `--ai-id` — optional
  AI identifier (default: project basename)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica loop listen-status`

Inspect the persistent listener service state (installed, active, unit path, log path).

**Arguments:**

- `--ai-id` — optional
  AI identifier (default: project basename)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


#### `empirica listener`

Event listener registry: register, pause, resume per-instance event-driven work

**Subcommands:**

##### `empirica listener register`

Register a listener (idempotent)

**Arguments:**

- `--name` — **required**
  Listener name (alphanumeric, dot, dash, underscore)
- `--topic` — **required**
  Topic URL: <scheme>:<rest>. V1: ntfy:<channel>. Future: sse:<url>, websocket:<url>, gmail:<query>, whatsapp:<num>
- `--description` — optional
  Optional human-readable description
- `--on-wake` — optional
  Prompt template the listener body replays on each wake. Empty = use the default from the inbox-listener skill.
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener unregister`

Remove a listener from the registry (also clears pause/active state)

**Arguments:**

- `name` — **required**
  Listener name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener pause`

Pause a listener — sets pause flag (mechanical kill of Monitor + curl requires the install-request analog, item 4 of PROPOSAL_EVENT_LISTENER)

**Arguments:**

- `name` — **required**
  Listener name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener resume`

Resume a listener (clears pause flag; bootstrap arming via the wake template)

**Arguments:**

- `name` — **required**
  Listener name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener record-wake`

Record a wake fire (call after the listener body processes a message)

**Arguments:**

- `name` — **required**
  Listener name
- `--message` — optional
  Optional summary message for this wake
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener fire`

Manually trigger one wake of the listener body (testing).

**Arguments:**

- `name` — **required**
  Listener name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener install-request`

Cockpit→Claude install: register listener + queue a pending install request the target Claude picks up via UserPromptSubmit and arms via /inbox-listener (curl + Monitor).

**Arguments:**

- `--name` — **required**
  Listener name
- `--topic` — **required**
  Topic URL: <scheme>:<rest>. V1: ntfy:<channel>.
- `--description` — optional
  One-line description
- `--on-wake` — optional
  Prompt template the listener body replays on each wake (empty = inbox-listener default).
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener list`

List all listeners registered for an instance

**Arguments:**

- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener status`

Show status for a single listener

**Arguments:**

- `name` — **required**
  Listener name
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener on`

Arm the canonical mesh listener for ai_id (short-circuits when persistent OS service is running)

**Arguments:**

- `--ai-id` — optional
  AI identifier (default: project basename via .empirica/project.yaml)
- `--name` — optional
  Listener name (default: <ai_id>-inbox)
- `--topic` — optional
  ntfy topic (default: ntfy:orchestration-events?tags=<ai_id>)
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener arm`

Record the Monitor task_id post-arm (chained after `on` + Monitor)

**Arguments:**

- `task_id` — **required**
  Monitor task id (from the Monitor tool response)
- `--name` — optional
  Listener name (default: <ai_id>-inbox)
- `--ai-id` — optional
  AI identifier (default: project basename via .empirica/project.yaml)
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener off`

Tear down the canonical mesh listener — emits TaskStop + `unregister` next_step JSON

**Arguments:**

- `--name` — optional
  Listener name (default: <ai_id>-inbox)
- `--ai-id` — optional
  AI identifier (default: project basename via .empirica/project.yaml)
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


#### `empirica instance`

Instance lifecycle: kill, forget, label (the destructive control plane)

**Subcommands:**

##### `empirica instance kill`

Terminate an instance (tmux kill-pane for tmux_*, SIGTERM for others)

**Arguments:**

- `instance_id` — **required**
  Target instance_id
- `--force` — optional · flag
  Use SIGKILL instead of SIGTERM (non-tmux only)
- `--yes` / `-y` — optional · flag
  Bypass safety check when targeting current instance
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica instance forget`

Remove all per-instance state files from ~/.empirica/ (cleanup for dead instances)

**Arguments:**

- `instance_id` — **required**
  Target instance_id
- `--yes` / `-y` — optional · flag
  Bypass safety check when targeting current instance
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica instance label`

Set/show/clear the human-readable label for an instance

**Arguments:**

- `instance_id` — **required**
  Target instance_id
- `label` — **required**
  New label (omit to show current value)
- `--clear` — optional · flag
  Clear the manual label (revert to project basename)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica instance prune`

Bulk forget every instance that fails the liveness check

**Arguments:**

- `--dry-run` — optional · flag
  Show which instances would be removed without removing them
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


#### `empirica mailbox`

Cortex AI mesh interaction — atomic reply with auto-close (distinct from message-* git-notes local messaging)

**Subcommands:**

##### `empirica mailbox reply`

Atomic propose + complete in one call — fixes the AI ack-discipline gap (skip the second cortex_complete_proposal step)

**Arguments:**

- `--parent-id` — **required**
  Parent proposal id being replied to (the inbox row)
- `--summary` — **required**
  Reply body (the actual message)
- `--title` — optional
  Reply title (default: "Re: <parent.title>", truncated to 200)
- `--type` — optional · type=`choice` · choices={architecture_decision, collab_brief, code_change_request, investigation_request, spec_updated, publish, trust_escalation_request} · default=`collab_brief`
  Reply proposal type (default: collab_brief)
- `--target-claudes` — optional
  Comma-separated target ai_ids (default: auto-derive from parent.source_claude)
- `--source-claude` — optional
  Your ai_id (default: from .empirica/project.yaml)
- `--payload` — optional
  Optional type-specific payload as JSON string (default: {})
- `--result` — optional · type=`choice` · choices={shipped, failed, wont_fix} · default=`shipped`
  Completion result applied to parent (default: shipped)
- `--commit-sha` — optional
  Optional commit_sha attached to parent completion
- `--no-close` — optional · flag
  Send reply WITHOUT closing parent (follow-up question case)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)


#### `empirica cockpit`

Multi-instance cockpit launcher — bring up the canonical tmux layout in one command, with abnormal-exit detection

**Subcommands:**

##### `empirica cockpit launch`

Bring up the cockpit (idempotent — attaches if already running)

**Arguments:**

- `--config` — optional
  Override the default config path (~/.empirica/cockpit/config.yaml)
- `--no-attach` — optional · flag
  Don't attach after creating the layout — useful for headless / scripted bring-up
- `--quiet-warnings` — optional · flag
  Suppress the abnormal-exit warning even when the previous session ended uncleanly
- `--surface` — optional · type=`choice` · choices={tmux, alacritty}
  Override the surface from config. tmux = legacy single-attach. alacritty = one alacritty window per group with WM_CLASS for KDE Meta+1..N switching (requires "groups:" in config).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format


##### `empirica cockpit status`

Show current cockpit state without attaching (read-only)

**Arguments:**

- `--config` — optional
  Override the default config path
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format


##### `empirica cockpit detach`

Clean detach: write the clean-shutdown marker + tmux detach-client

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format


##### `empirica cockpit kill`

Destroy the cockpit session and write clean-shutdown marker

**Arguments:**

- `--config` — optional
  Override the default config path
- `--prune` — optional · flag
  Also prune dead per-instance state files (equivalent to `empirica instance prune`)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format


#### `empirica daemon-list`

List projects registered with the local daemon (~/.empirica/registry.yaml).

**Arguments:**

- `--output` — optional · type=`choice` · choices={yaml, json, table} · default=`table`
  Output format (default: table).

#### `empirica notify`

Notification dispatcher — emit events through configured backends

**Subcommands:**

##### `empirica notify emit`

Emit a notification event

**Arguments:**

- `--severity` — **required** · type=`choice` · choices={info, warning, critical}
  info | warning | critical (drives default routing)
- `--title` — **required**
  One-line title
- `--message` — **required**
  Body text
- `--rationale` — optional
  Why this event is being raised (surfaces in detail-capable backends)
- `--tags` — optional
  Comma-separated tag list, e.g. "clipboard,empirica"
- `--click-url` — optional
  Primary tap-through URL
- `--actions` — optional
  Action buttons in ntfy format: "Label1|URL1,Label2|URL2,..."
- `--source` — optional
  Opaque emitter identifier — convention: loop:<name>, hook:<event>, manual, script:<n>
- `--topic-override` — optional
  Explicit topic for backends that have topics (bypasses routing)
- `--backend-override` — optional
  Explicit backend (e.g. stdout, log, ntfy) — bypasses routing
- `--dry-run` — optional · flag
  Print resolved event + backend choice; do not emit
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)


##### `empirica notify config`

Print effective notify config (secrets redacted)

**Arguments:**

- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)


##### `empirica notify backends`

List registered backends and configured-status

**Arguments:**

- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)


##### `empirica notify test`

Send a test event end-to-end

**Arguments:**

- `--backend` — optional
  Force a specific backend for the test (default: routing rules)
- `--output` — optional · type=`choice` · choices={json, human} · default=`json`
  Output format (default: json)


---

## skills

#### `empirica skill-suggest`

Suggest skills for a task

**Arguments:**

- `--task` — optional
  Task description to suggest skills for
- `--project-id` — optional
  Project ID for context-aware suggestions
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed suggestions

#### `empirica skill-fetch`

Fetch and normalize a skill

**Arguments:**

- `--name` — **required**
  Skill name
- `--url` — optional
  URL to fetch skill from (markdown)
- `--file` — optional
  Local .skill archive file to load
- `--tags` — optional
  Comma-separated tags for the skill
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed output

#### `empirica skill-extract`

Extract decision frameworks from skill to meta-agent-config.yaml

**Arguments:**

- `--skill-dir` — optional
  Path to skill directory (with SKILL.md and/or references/)
- `--skills-dir` — optional
  Path to directory containing multiple skills (extracts all)
- `--output-file` — optional · default=`meta-agent-config.yaml`
  Output YAML file path (default: meta-agent-config.yaml)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed extraction progress

---

## architecture

#### `empirica assess-component`

Assess epistemic health of a code component

**Arguments:**

- `path` — **required**
  Path to file or package to assess (relative or absolute)
- `--project-root` — optional · default=`.`
  Root directory of the project (default: current directory)
- `--output` — optional · type=`choice` · choices={text, json, summary} · default=`text`
  Output format (default: text)

#### `empirica assess-compare`

Compare epistemic health of two components

**Arguments:**

- `path_a` — **required**
  First component path
- `path_b` — **required**
  Second component path
- `--project-root` — optional · default=`.`
  Root directory of the project (default: current directory)
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`
  Output format (default: text)

#### `empirica assess-directory`

Assess all Python modules in a directory

**Arguments:**

- `path` — **required**
  Directory to assess
- `--project-root` — optional · default=`.`
  Root directory of the project (default: current directory)
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`
  Output format (default: text)
- `--top` — optional · type=`int` · default=`10`
  Show top N worst components (default: 10)
- `--include-init` — optional · flag
  Include __init__.py files (excluded by default as they are thin wrappers)

---

## agents

#### `empirica agent-spawn`

Spawn epistemic agent (returns prompt with branch tracking)

**Arguments:**

- `--session-id` — **required**
  Parent session ID
- `--task` — **required**
  Task for the agent
- `--persona` — optional · default=`general`
  Persona ID to use
- `--turtle` — optional · flag
  Auto-select best emerged persona for task (overrides --persona)
- `--context` — optional
  Additional context from parent
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica agent-report`

Report agent postflight results

**Arguments:**

- `--branch-id` — **required**
  Branch ID from agent-spawn
- `--postflight` — optional
  Postflight JSON or "-" for stdin
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica agent-aggregate`

Aggregate results from multiple agents

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--round` — optional · type=`int` · default=`1`
  Investigation round
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica agent-parallel`

Plan and orchestrate parallel epistemic agents with attention budget

**Arguments:**

- `--session-id` — **required**
  Parent session ID
- `--task` — **required**
  Investigation task
- `--budget` — optional · type=`int` · default=`20`
  Total findings budget (default: 20)
- `--max-agents` — optional · type=`int` · default=`5`
  Maximum parallel agents (default: 5)
- `--strategy` — optional · type=`choice` · choices={information_gain, uniform, priority} · default=`information_gain`
  Budget allocation strategy
- `--domains` — optional · type=`list`
  Override investigation domains (auto-detected if not specified)
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica agent-export`

Export epistemic agent as shareable JSON package

**Arguments:**

- `--branch-id` — **required**
  Branch ID to export
- `--output-file` — optional
  Output file path (prints to stdout if not specified)
- `--register` — optional · flag
  Register to sharing network (Qdrant)
- `--output` — optional · type=`choice` · choices={text, json} · default=`json`

#### `empirica agent-import`

Import epistemic agent from JSON package

**Arguments:**

- `--session-id` — **required**
  Session to import into
- `--input-file` — **required**
  Agent JSON file to import
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica agent-discover`

Discover epistemic agents in sharing network

**Arguments:**

- `--domain` — optional
  Search by domain expertise (e.g., security, multi-persona)
- `--min-reputation` — optional · type=`float`
  Minimum reputation score (0.0-1.0)
- `--limit` — optional · type=`int` · default=`10`
  Maximum results
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

---

## sentinel

#### `empirica sentinel-orchestrate`

Run autonomous multi-agent orchestration with persona selection

**Arguments:**

- `--session-id` — **required**
  Session ID for orchestration context (required)
- `--task` — **required**
  Task description for persona selection and orchestration (required)
- `--max-agents` — optional · type=`int` · default=`3`
  Maximum parallel agents to spawn (optional, default: 3)
- `--profile` — optional
  Domain profile name: general, healthcare, finance, or custom (optional)
- `--scope-breadth` — optional · type=`float` · default=`0.5`
  Scope breadth 0.0-1.0, affects max loops (optional, default: 0.5)
- `--scope-duration` — optional · type=`float` · default=`0.5`
  Scope duration 0.0-1.0, affects max loops (optional, default: 0.5)
- `--merge` — optional · type=`choice` · choices={union, consensus, best_score, weighted} · default=`union`
  Merge strategy for aggregating findings (optional, default: union)
- `--dry-run` — optional · flag
  Select personas without spawning agents (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (optional, default: human)

#### `empirica sentinel-load-profile`

Load domain compliance profile for gate enforcement

**Arguments:**

- `--session-id` — **required**
  Session ID (required)
- `--profile` — **required**
  Profile name: general, healthcare, finance (required)
- `--file` — optional
  Custom profile YAML file path (optional, overrides built-in)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (optional, default: human)

#### `empirica sentinel-status`

Show Sentinel status, loop tracking, and available profiles

**Arguments:**

- `--session-id` — **required**
  Session ID (required)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (optional, default: human)

#### `empirica sentinel-check`

Run compliance check against domain gates

**Arguments:**

- `--session-id` — **required**
  Session ID (required)
- `--profile` — optional
  Domain profile to use for compliance (optional)
- `--vectors` — optional
  Epistemic vectors as JSON string or "-" for stdin (optional)
- `--know` — optional · type=`float` · default=`0.5`
  Knowledge level 0.0-1.0 (optional, default: 0.5)
- `--uncertainty` — optional · type=`float` · default=`0.5`
  Uncertainty level 0.0-1.0 (optional, default: 0.5)
- `--findings` — optional · type=`list`
  List of findings for compliance check (optional)
- `--unknowns` — optional · type=`list`
  List of unknowns for compliance check (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (optional, default: human)

---

## personas

#### `empirica persona-list`

List all emerged personas

**Arguments:**

- `--domain` — optional
  Filter by domain (e.g., security, performance)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica persona-show`

Show details of a specific emerged persona

**Arguments:**

- `--persona-id` — **required**
  Persona ID to show
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica persona-promote`

Promote emerged persona to MCO personas.yaml for global reuse

**Arguments:**

- `--persona-id` — **required**
  Persona ID to promote
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica persona-find`

Find emerged personas similar to a task description

**Arguments:**

- `--task` — **required**
  Task description to match against
- `--limit` — optional · type=`int` · default=`5`
  Maximum results (default: 5)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

---

## lessons

#### `empirica lesson-create`

Create a new lesson from JSON input

**Arguments:**

- `--name` — optional
  Lesson name
- `--input` / `-i` — optional
  Input JSON file (use "-" for stdin)
- `--json` — optional
  Inline JSON data
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-load`

Load and display a lesson

**Arguments:**

- `--id` / `--lesson-id` — **required**
  Lesson ID (required)
- `--steps-only` — optional · flag
  Only show steps
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-list`

List all lessons

**Arguments:**

- `--domain` — optional
  Filter by domain
- `--limit` — optional · type=`int` · default=`20`
  Maximum results (default: 20)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-search`

Search for lessons by query, vector, or domain

**Arguments:**

- `--query` / `-q` — optional
  Semantic search query
- `--improves` — optional
  Find lessons that improve this vector (know, do, context, etc.)
- `--domain` — optional
  Filter by domain
- `--limit` — optional · type=`int` · default=`10`
  Maximum results (default: 10)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-recommend`

Get lesson recommendations based on epistemic state

**Arguments:**

- `--session-id` — optional
  Session ID to load epistemic state from
- `--know` — optional · type=`float`
  Current know vector (0-1)
- `--do` — optional · type=`float`
  Current do vector (0-1)
- `--context` — optional · type=`float`
  Current context vector (0-1)
- `--uncertainty` — optional · type=`float`
  Current uncertainty vector (0-1)
- `--threshold` — optional · type=`float` · default=`0.6`
  Threshold for "acceptable" (default: 0.6)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-path`

Get learning path to reach a target lesson

**Arguments:**

- `--target` — **required**
  Target lesson ID (required)
- `--completed` — optional
  Comma-separated list of already completed lesson IDs
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-replay-start`

Start tracking a lesson replay

**Arguments:**

- `--lesson-id` — **required**
  Lesson ID (required)
- `--session-id` — **required**
  Session ID (required)
- `--ai-id` — optional
  AI agent ID
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-replay-end`

End a lesson replay and record results

**Arguments:**

- `--replay-id` — **required**
  Replay ID (required)
- `--success` — optional · flag
  Mark replay as successful
- `--failed` — optional · flag
  Mark replay as failed
- `--steps-completed` — optional · type=`int`
  Number of steps completed
- `--error` — optional
  Error message if failed
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica lesson-stats`

Show lesson storage statistics

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

---

## mcp

#### `empirica mcp-start`

Start Empirica MCP server in background

**Arguments:**

- `--verbose` / `-v` — optional · flag
  Show detailed output

#### `empirica mcp-stop`

Stop Empirica MCP server

**Arguments:**

- `--verbose` / `-v` — optional · flag
  Show detailed output

#### `empirica mcp-status`

Check Empirica MCP server status

**Arguments:**

- `--verbose` / `-v` — optional · flag
  Show detailed process info

#### `empirica mcp-test`

Test Empirica MCP server connection

**Arguments:**

- `--verbose` / `-v` — optional · flag
  Show detailed output

#### `empirica mcp-list-tools`

List available MCP tools

**Arguments:**

- `--verbose` / `-v` — optional · flag
  Show usage examples

#### `empirica mcp-call`

Call MCP tool directly (experimental)

**Arguments:**

- `tool_name` — **required**
  Name of the MCP tool to call
- `arguments` — **required** · default=`{}`
  JSON arguments for the tool
- `--verbose` / `-v` — optional · flag
  Show detailed output

---

## memory

#### `empirica memory-prime`

Allocate attention budget across investigation domains using Shannon info-gain

**Arguments:**

- `--session-id` — **required**
  Session ID for budget tracking
- `--domains` — **required**
  JSON array of domain names, e.g. '["security", "architecture"]'
- `--budget` — optional · type=`int` · default=`20`
  Total findings budget to allocate (default: 20)
- `--know` — optional · type=`float` · default=`0.5`
  Current know vector (0.0-1.0, default: 0.5)
- `--uncertainty` — optional · type=`float` · default=`0.5`
  Current uncertainty vector (0.0-1.0, default: 0.5)
- `--prior-findings` — optional · default=`{}`
  JSON object of prior findings per domain, e.g. '{"security": 3}'
- `--dead-ends` — optional · default=`{}`
  JSON object of dead ends per domain, e.g. '{"architecture": 1}'
- `--persist` — optional · flag
  Persist budget to database for later retrieval
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica memory-scope`

Retrieve memories by scope vectors using zone-tiered access

**Arguments:**

- `--session-id` — **required**
  Session ID for context management
- `--scope-breadth` — optional · type=`float` · default=`0.5`
  Scope breadth (0.0=narrow, 1.0=wide). Affects zone selection.
- `--scope-duration` — optional · type=`float` · default=`0.5`
  Scope duration (0.0=ephemeral, 1.0=long-term). Affects priority.
- `--zone` — optional · type=`choice` · choices={anchor, working, cache, all} · default=`all`
  Specific zone to query (default: all)
- `--content-type` — optional
  Filter by content type (finding, unknown, goal, etc.)
- `--min-priority` — optional · type=`float`
  Minimum priority score to include
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica memory-value`

Retrieve memories ranked by information gain / token cost

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--query` — **required**
  Query text to match against memories
- `--budget` — optional · type=`int` · default=`5000`
  Token budget for retrieval (default: 5000)
- `--project-id` — optional
  Project ID (auto-detected if not provided)
- `--min-gain` — optional · type=`float` · default=`0.1`
  Minimum information gain to include (default: 0.1)
- `--include-eidetic` — optional · flag
  Include eidetic (fact) memory
- `--include-episodic` — optional · flag
  Include episodic (narrative) memory
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica pattern-check`

Check current approach against dead-ends and mistake patterns (real-time sentinel)

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--approach` — **required**
  Description of current approach to validate
- `--project-id` — optional
  Project ID (auto-detected if not provided)
- `--know` — optional · type=`float` · default=`0.5`
  Current know vector (for mistake risk calculation)
- `--uncertainty` — optional · type=`float` · default=`0.5`
  Current uncertainty vector (for mistake risk calculation)
- `--threshold` — optional · type=`float` · default=`0.7`
  Similarity threshold for pattern matching (default: 0.7)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica session-rollup`

Aggregate findings and epistemic state from parallel sub-agents

**Arguments:**

- `--parent-session-id` — **required**
  Parent session ID to aggregate children for
- `--budget` — optional · type=`int` · default=`20`
  Max findings to accept (default: 20)
- `--min-score` — optional · type=`float` · default=`0.3`
  Minimum quality score to accept finding (default: 0.3)
- `--jaccard-threshold` — optional · type=`float` · default=`0.7`
  Jaccard similarity for dedup (default: 0.7)
- `--semantic-dedup` — optional · flag
  Use Qdrant semantic dedup in addition to Jaccard
- `--project-id` — optional
  Project ID for semantic dedup (auto-detected if not provided)
- `--log-decisions` — optional · flag
  Log accept/reject decisions to database
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica memory-report`

Get context budget report (token usage by zone)

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

---

## vision

#### `empirica vision`

Process visual information

**Arguments:**

- `image_path` — **required**
  Path to image file
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

---

## domains

#### `empirica domain-list`

List all loaded domains

**Arguments:**

- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica domain-show`

Show a domain's checklist details

**Arguments:**

- `domain` — **required**
  Domain name (e.g., cybersec, default, remote-ops)
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica domain-resolve`

Resolve a (work_type, domain, criticality) tuple

**Arguments:**

- `work_type` — **required**
  Work type (code, infra, docs, remote-ops, ...)
- `--domain` — optional · default=`default`
  Domain name (default: default)
- `--criticality` — optional · default=`medium`
  Criticality level (low|medium|high|critical)
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

#### `empirica domain-validate`

Validate all YAML domain files

**Arguments:**

- `--output` — optional · type=`choice` · choices={text, json} · default=`text`

---

## setup

#### `empirica onboard`

Interactive introduction to Empirica (recommended for first-time users)

**Arguments:**

- `--ai-id` — optional · default=`claude-code`
  AI identifier (optional, default: claude-code)

#### `empirica setup-claude-code`

Configure Claude Code integration (hooks, CLAUDE.md, MCP server)

**Arguments:**

- `--force` — optional · flag
  Reinstall plugin even if it already exists
- `--skip-mcp` — optional · flag
  Skip MCP server installation and configuration
- `--skip-credentials` — optional · flag
  Skip the credentials validation + wizard (use env vars or pre-populated credentials.yaml)
- `--skip-listener-service` — optional · flag
  Skip installing the persistent listener service (systemd-user / launchd). Use when you want session-only Monitor.
- `--org-id` — optional
  Override tenant org_id (skip cortex /v1/tenant/me fetch for this field)
- `--tenant-slug` — optional
  Override tenant_slug (skip cortex /v1/tenant/me fetch for this field)
- `--mesh-id-prefix` — optional
  Override mesh_id_prefix (skip cortex /v1/tenant/me fetch for this field)
- `--skip-claude-md` — optional · flag
  Skip CLAUDE.md installation (keep existing system prompt)
- `--full-prompt` — optional · flag
  Use full system prompt instead of lean core default (lean loads skills on demand)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)
- `--verbose` — optional · flag
  Show detailed output

#### `empirica enp-setup`

Initialize the Epistemic Network Protocol (ENP) watcher

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica diagnose`

Check Empirica + Claude Code integration health (run this when statusline isn't showing)

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)
- `--frontend` — optional · type=`choice` · choices={claude-code, ecodex} · default=`claude-code`
  Which frontend to diagnose (default: claude-code). 'ecodex' runs the ecodex-specific check set: codex-empirica-plugin install, statusline runtime stdin wiring, codex-empirica-translator on 127.0.0.1:18080, curated provider env_keys, Rust cargo fmt+check.
- `--fast` — optional · flag
  Skip slow checks (cargo check). Useful for the /diagnose skill's interactive walk-through; CI can leave this off.

#### `empirica doctor`

Check Empirica install health (Desktop + general — empirica-mcp, .empirica/, git remote, Cortex reachability)

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json — Desktop calls expect machine-readable)
- `--strict-warn` — optional · flag
  Exit code 2 when any WARN check fires (default: only FAIL fires non-zero exit)

#### `empirica release`

Run the release pipeline (wraps scripts/release.py)

**Arguments:**

- `--dry-run` — optional · flag
  Preview changes without executing
- `--prepare` — optional · flag
  Merge to main, build, and test — but do NOT publish
- `--publish` — optional · flag
  Publish a prepared release (requires --prepare to have been run first)
- `--version-only` — optional · flag
  Update version strings only (no build/publish). Requires --old-version.
- `--old-version` — optional
  Previous version for broad sweep replacement (e.g. 1.5.6)

#### `empirica serve`

Start local daemon for Chrome extension integration

**Arguments:**

- `--port` — optional · type=`int` · default=`8000`
  Port to listen on (default: 8000)
- `--host` — optional · default=`127.0.0.1`
  Host to bind to (default: 127.0.0.1, use 0.0.0.0 for network access)
- `--reload` — optional · flag
  Enable auto-reload on code changes (development only)

---

## uncategorized

_These commands are registered in the parser but not yet listed in_ `_HELP_CATEGORIES` _in `empirica/cli/cli_core.py`. Add them to a_ _category to make them discoverable via_ `empirica help`.

#### `empirica bus-dispatch`

Send a typed dispatch action to another instance

**Arguments:**

- `--from` — optional
  Sender instance ID (default: claude-code)
- `--to` — **required**
  Target instance ID, or "*" for capability-routed
- `--action` — **required**
  Action name (e.g., schedule_cron, send_email)
- `--payload` — optional
  JSON payload string for the action
- `--priority` — optional · type=`choice` · choices={low, normal, high, urgent} · default=`normal`
- `--deadline` — optional · type=`int`
  Dispatch deadline in seconds from now
- `--required-capabilities` — optional
  Comma-separated capabilities (for --to "*" routing)
- `--callback-channel` — optional
  Channel for the response (default: dispatch)
- `--ttl` — optional · type=`int` · default=`86400`
  Git message TTL seconds (default: 24h)
- `--wait` — optional · flag
  Block until the dispatch completes or times out
- `--wait-timeout` — optional · type=`int` · default=`60`
  Max seconds to wait if --wait (default: 60)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica bus-instances`

List all registered bus instances

**Arguments:**

- `--capability` — optional
  Filter instances that have this capability
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica bus-register`

Register this Claude instance in the shared dispatch bus registry

**Arguments:**

- `--instance-id` — **required**
  Unique instance ID (e.g., terminal-claude-1)
- `--type` — **required**
  Instance type (claude-code-cli, cowork-web, desktop-app, cortex-server)
- `--capabilities` — optional
  Comma-separated capabilities (e.g., codebase,git,shell)
- `--subscribes` — optional
  Comma-separated channels to subscribe to
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica bus-status`

Show an instance's registry state and inbox summary

**Arguments:**

- `--instance-id` — **required**
  Instance ID to query
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica bus-subscribe`

Subscribe to a dispatch channel (blocking)

**Arguments:**

- `--instance-id` — **required**
  This instance ID
- `--channel` — optional · default=`dispatch`
  Channel to subscribe to (default: dispatch)
- `--poll-interval` — optional · type=`float` · default=`2.0`
  Seconds between polls (default: 2.0)
- `--limit` — optional · type=`int` · default=`50`
  Max dispatches per poll (default: 50)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica calibration-dispute`

Dispute a grounded calibration measurement as a measurement artifact

**Arguments:**

- `--vector` — **required**
  Vector name to dispute (e.g., change, impact, do)
- `--reported` — **required** · type=`float`
  The grounded value reported by post-test (e.g., 0.2)
- `--expected` — **required** · type=`float`
  The value you believe is correct (e.g., 0.85)
- `--reason` — **required**
  Why this measurement is wrong (e.g., "Greenfield repo, normalization inappropriate")
- `--evidence` — optional · default=``
  Supporting evidence (e.g., "git log --stat shows 8 files created")
- `--session-id` — optional
  Session to dispute (default: active session)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)

#### `empirica chat`

Launch the empirica chat TUI (single-instance collaborative workspace)

**Arguments:**

- `--feed` — optional
  Replay a pre-baked jsonl conversation (Phase 1 demo mode — no app-server needed)
- `--feed-delay` — optional · type=`float`
  Delay between feed turns when replaying (default: 0, instant)
- `--session-id` — optional
  Resume an existing chat session from ~/.empirica/chat_sessions/
- `--translator-url` — optional
  ecodex translator base URL (e.g. http://127.0.0.1:18080/v1). When set, user messages are dispatched to the translator and agent responses stream back as AgentTurns. When unset, chat runs in render-only mode (Phase 1 fallback).
- `--model` — optional · default=`deepseek-chat`
  Model id passed to the translator (default: deepseek-chat)
- `--system` — optional
  System instructions injected as the leading message
- `--provider` — optional
  Register a provider. Repeatable. Examples: ollama=http://192.168.1.68:11434/v1,model=qwen3.5:latest  · deepseek=https://api.deepseek.com/v1,model=deepseek-chat,key_env=DEEPSEEK_API_KEY  · translator=http://127.0.0.1:18080/v1,wire=responses. When omitted: builtin empirica-server defaults are loaded (ollama, qwopus, llcpp, llcpp-alt). Switch at runtime with /provider NAME and /model NAME.
- `--autonomy` — optional · type=`choice` · choices={assistant, copilot, autonomous} · default=`assistant`
  Autonomy mode for the AI in this session (default: assistant). assistant = waits for confirmation; copilot = takes obvious next steps; autonomous = pursues stated objective with checkpoints at coherent boundaries.
- `--no-system-prompt` — optional · flag · default=`True`
  Disable the empirica chat system prompt. The model will not be told it's in empirica chat or made aware of slash commands. Use --system to supply your own prompt instead.
- `--replay` — optional
  Open a past chat session in read-only replay mode. Loads all turns from ~/.empirica/chat_sessions/{SESSION_ID}.jsonl, renders them, and disables LLM dispatch. Use --feed-delay to pace the playback if reviewing visually. Cannot be combined with --session-id (resume) or --feed.

#### `empirica concept-build`

Build concept graph from findings/unknowns (experimental)

**Arguments:**

- `--project-id` — optional
  Project ID (auto-detects if not provided)
- `--overwrite` — optional · flag
  Overwrite existing concept data
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica concept-related`

Find concepts related to a search term (experimental)

**Arguments:**

- `search_term` — **required**
  Term to search for related concepts
- `--project-id` — optional
  Project ID (auto-detects if not provided)
- `--limit` — optional · type=`int` · default=`10`
  Maximum related concepts to show (default: 10)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica concept-stats`

Show concept graph statistics (experimental)

**Arguments:**

- `--project-id` — optional
  Project ID (auto-detects if not provided)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica concept-top`

Show top concepts by frequency (experimental)

**Arguments:**

- `--project-id` — optional
  Project ID (auto-detects if not provided)
- `--limit` — optional · type=`int` · default=`20`
  Maximum concepts to show (default: 20)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica config`

Configuration management

**Arguments:**

- `key` — **required**
  Configuration key (dot notation, e.g., routing.default_strategy)
- `value` — **required**
  Value to set (if key provided)
- `--init` — optional · flag
  Initialize configuration (replaces config-init)
- `--validate` — optional · flag
  Validate configuration (replaces config-validate)
- `--section` — optional
  Show specific section (e.g., routing, adapters)
- `--output` — optional · type=`choice` · choices={yaml, json} · default=`yaml`
  Output format
- `--force` — optional · flag
  Overwrite existing config (with --init)
- `--verbose` — optional · flag
  Show detailed output

#### `empirica docs-assess`

Epistemic documentation assessment - measures docs coverage against actual features

**Arguments:**

- `--project-root` — optional
  Root directory of the project (default: current directory)
- `--verbose` — optional · flag
  Show detailed undocumented items
- `--summary-only` — optional · flag
  Lightweight summary (~50 tokens) for bootstrap context
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--check-docstrings` — optional · flag
  Check Python code for missing docstrings (functions, classes, modules)
- `--turtle` — optional · flag
  Epistemic recursive mode: iterate between code and docs to surface gaps
- `--check-staleness` — optional · flag
  Detect stale docs by cross-referencing with recent findings, dead-ends, and mistakes
- `--staleness-threshold` — optional · type=`float` · default=`0.7`
  Minimum similarity threshold for staleness detection (default: 0.7)
- `--staleness-days` — optional · type=`int` · default=`30`
  Look back N days for memory items (default: 30)

#### `empirica docs-explain`

Get focused explanation of Empirica topics - inverts docs-assess

**Arguments:**

- `--topic` — optional
  Topic to explain (e.g., "vectors", "sessions", "goals")
- `--question` — optional
  Question to answer (e.g., "How do I start a session?")
- `--audience` — optional · type=`choice` · choices={user, developer, ai, all} · default=`all`
  Target audience for explanation
- `--project-root` — optional
  Root directory of the project (default: current directory)
- `--project-id` — optional
  Project ID for Qdrant semantic search (auto-detected if not specified)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica docs-link-check`

Verify markdown internal links — finds broken relative paths in tech docs

**Arguments:**

- `--root` — optional
  Project root to scan (default: current directory).
- `--exclude` — optional
  Additional directory names to skip (repeatable). On top of the default skip set.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format. JSON shape: {scanned_files, broken_total, passed, tiers}.

#### `empirica edit-with-confidence`

Edit file with metacognitive confidence assessment (prevents 80%% of edit failures)

**Arguments:**

- `--file-path` — **required**
  Path to file to edit (required)
- `--old-str` — **required**
  String to replace (exact match) (required)
- `--new-str` — **required**
  Replacement string (required)
- `--context-source` — optional · type=`choice` · choices={view_output, fresh_read, memory} · default=`memory`
  Source of context (affects confidence assessment) (optional, default: memory)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (optional, default: json)
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica epp-activate`

Log EPP (Epistemic Persistence Protocol) activation — self-reported telemetry

**Arguments:**

- `--category` — **required** · type=`choice` · choices={emotional, rhetorical, evidential, logical, contextual}
  Pushback category classified
- `--action` — **required** · type=`choice` · choices={hold, soften, update, reframe}
  Action decided: HOLD / SOFTEN / UPDATE / REFRAME
- `--session-id` — optional
  Session ID (auto-derived if omitted)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica goal-analysis`

Analyze goal feasibility

**Arguments:**

- `goal` — **required**
  Goal to analyze
- `--context` — optional
  JSON context data
- `--verbose` — optional · flag
  Show detailed analysis

#### `empirica goals-activate`  _(aliases: `goal-activate`)_

Activate a planned goal — set to in_progress and link to current transaction

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to activate (prefix match)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica goals-prune`

Bulk close stale, duplicate, or planned-never-activated goals (dry-run by default)

**Arguments:**

- `--test-pollution` — optional · flag
  Close goals matching test-runner patterns (objective starts with 'Test '/'E2E test', ai_id starts with 'test-')
- `--by-status-planned` — optional · flag
  Close all goals with status=planned
- `--auto-stale` — optional · type=`int`
  Close in_progress goals older than N days with no activity (default: 30)
- `--duplicates` — optional · type=`float`
  Close goals whose objective text is ≥ thresh similar to another (default: 0.7)
- `--apply` — optional · flag
  Actually mutate (omit for dry-run)
- `--project-id` — optional
  Override project_id (auto-resolved if omitted)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed output

#### `empirica lesson-embed`

Embed all lessons into Qdrant for semantic search

**Arguments:**

- `--force` — optional · flag
  Force re-embed all
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica log-token-saving`

Log a token saving event

**Arguments:**

- `--session-id` — **required**
  Session ID
- `--type` — **required** · type=`choice` · choices={doc_awareness, finding_reuse, mistake_prevention, handoff_efficiency}
  Type of token saving
- `--tokens` — **required** · type=`int`
  Tokens saved
- `--evidence` — **required**
  What was avoided/reused
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica mco-load`

Load MCO (Meta-Agent Configuration Object) configuration

**Arguments:**

- `--session-id` — optional
  Session UUID (optional, for inference)
- `--ai-id` — optional
  AI identifier (optional, for model/persona inference)
- `--snapshot` — optional
  Path to pre_summary snapshot (for post-compact reload)
- `--model` — optional
  Explicit model override (claude_haiku, claude_sonnet, gpt4, etc.)
- `--persona` — optional
  Explicit persona override (researcher, implementer, reviewer, etc.)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed output

#### `empirica message-channels`

List channels with message counts

**Arguments:**

- `--ai-id` — optional
  Count unread for this AI ID (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica message-cleanup`

Remove expired messages

**Arguments:**

- `--dry-run` — optional · flag
  Show what would be removed without removing
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica message-inbox`  _(aliases: `msg-inbox`, `mi`)_

Check inbox for messages addressed to this agent

**Arguments:**

- `--ai-id` — **required**
  Your AI ID (required)
- `--machine` — optional
  Your machine hostname (optional, auto-detected)
- `--channel` — optional
  Filter by channel (optional)
- `--status` — optional · type=`choice` · choices={unread, read, all} · default=`unread`
  Filter by status (optional, default: unread)
- `--limit` — optional · type=`int` · default=`50`
  Max messages to return (optional, default: 50)
- `--include-expired` — optional · flag
  Include expired messages (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
- `--verbose` — optional · flag

#### `empirica message-read`  _(aliases: `msg-read`, `mr`)_

Mark a message as read

**Arguments:**

- `--message-id` — **required**
  Message UUID (required)
- `--channel` — **required**
  Channel name (required)
- `--ai-id` — **required**
  Your AI ID (required)
- `--machine` — optional
  Your machine hostname (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica message-reply`  _(aliases: `msg-reply`)_

Reply to a message

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin
- `--message-id` — optional
  Original message ID (required)
- `--channel` — optional
  Channel of original message (required)
- `--from-ai-id` — optional
  Your AI ID (optional, default: claude-code)
- `--body` — optional
  Reply body (required)
- `--type` — optional · type=`choice` · choices={response, ack} · default=`response`
  Reply type (optional, default: response)
- `--session-id` — optional
  Your session ID (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica message-send`  _(aliases: `msg-send`, `ms`)_

Send message to another agent via git notes

**Arguments:**

- `config` — **required**
  JSON config file or - for stdin (AI-first mode)
- `--from-ai-id` — optional
  Sender AI ID (optional, default: claude-code)
- `--to-ai-id` — optional
  Recipient AI ID or * for broadcast (required)
- `--to-machine` — optional
  Recipient machine hostname (optional)
- `--channel` — optional · default=`direct`
  Channel: crosscheck, direct, broadcast, or custom (optional, default: direct)
- `--subject` — optional
  Message subject (required)
- `--body` — optional
  Message body (required)
- `--type` — optional · type=`choice` · choices={request, response, notification, ack} · default=`request`
  Message type (optional, default: request)
- `--reply-to` — optional
  Message ID this replies to (optional)
- `--thread-id` — optional
  Thread ID to join (optional)
- `--ttl` — optional · type=`int` · default=`86400`
  Time-to-live in seconds (optional, default: 86400 = 24h, 0 = never)
- `--priority` — optional · type=`choice` · choices={low, normal, high} · default=`normal`
  Message priority (optional, default: normal)
- `--session-id` — optional
  Sender session ID (optional)
- `--goal-id` — optional
  Related goal ID (optional)
- `--project-id` — optional
  Related project ID (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
- `--verbose` — optional · flag

#### `empirica message-thread`

View conversation thread

**Arguments:**

- `--thread-id` — **required**
  Thread ID (required)
- `--channel` — optional
  Filter by channel (optional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`

#### `empirica performance`

Analyze performance or run benchmarks

**Arguments:**

- `--benchmark` — optional · flag
  Run performance benchmarks (replaces benchmark command)
- `--target` — optional · default=`system`
  Performance analysis target
- `--type` — optional · default=`comprehensive`
  Benchmark/analysis type
- `--iterations` — optional · type=`int` · default=`10`
  Number of iterations (for benchmarks)
- `--memory` — optional · flag · default=`True`
  Include memory analysis
- `--context` — optional
  JSON context data
- `--detailed` — optional · flag
  Show detailed metrics
- `--verbose` — optional · flag
  Show detailed results

#### `empirica qdrant-cleanup`

Remove empty Qdrant collections to reduce resource usage

**Arguments:**

- `--execute` — optional · flag
  Actually delete empty collections (default: dry-run)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica qdrant-status`

Show Qdrant collection inventory and stats

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica query`

Query epistemic breadcrumbs (findings, unknowns, deadends, mistakes, issues, handoffs, blockers)

**Arguments:**

- `type` — **required** · type=`choice` · choices={findings, unknowns, deadends, mistakes, issues, handoffs, goals, blockers}
  Type of breadcrumb to query (blockers = goal-linked unknowns)
- `--scope` — optional · type=`choice` · choices={session, project, global} · default=`global`
  Query scope: session (one session), project (all sessions in project), global (all)
- `--session-id` — optional
  Session ID (required for session scope)
- `--project-id` — optional
  Project ID (required for project scope)
- `--limit` — optional · type=`int` · default=`20`
  Maximum results to return (default: 20)
- `--status` — optional
  Filter by status (type-specific: new/resolved for unknowns, active/completed for goals, etc.)
- `--ai-id` — optional
  Filter by AI ID
- `--since` — optional
  Filter by date (ISO format: 2025-01-01)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica release-ready`

Epistemic release assessment - verifies version sync, architecture health, security, and documentation

**Arguments:**

- `--project-root` — optional
  Root directory of the project (default: current directory)
- `--quick` — optional · flag
  Quick check (skip architecture assessment)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica resolve`

Resolve any artifact by ID (auto-detects type)

**Arguments:**

- `artifact_id` — **required**
  Artifact ID or prefix (e.g., first 8 chars)
- `--resolved-by` — optional
  Resolution reason
- `--output` — optional · type=`choice` · choices={text, json} · default=`json`

#### `empirica rust-docs-assess`

Rust-aware documentation coverage — pub items + /// docs in workspace crates

**Arguments:**

- `--project-root` — optional
  Root directory of the project (default: current directory)
- `--include` — optional · default=`[]`
  Path prefix to include (relative to project_root). Can repeat. When set, only matching crates are walked. Combines with .empirica/rust_docs.toml [rust_docs] include list.
- `--exclude` — optional · default=`[]`
  Path prefix to skip. Can repeat. Combines with config exclude list. Excludes win over includes — safety bias is to skip.
- `--strict` — optional · flag
  Only /// outer doc comments count; reject #[doc=...] attribute form. More conservative, more honest.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format. JSON shape compatible with docpistemic for compliance-report.

#### `empirica scan`

One-shot inventory of running AI-touching services (read-only)

**Arguments:**

- `--output` — optional · type=`choice` · choices={markdown, json} · default=`markdown`
  Output format (default: markdown)
- `--save` — optional · flag
  Persist the JSON snapshot to ~/.empirica/scans/<scan_id>.json and update last_scan_<project_id>.json for cockpit consumption
- `--explain` — optional · flag
  Hand the snapshot to the services-auditor skill for AI judgment (Phase 2). Auto-saves the snapshot and prints a system-reminder pointing the AI at /services-auditor with the snapshot path.
- `--project-id` — optional
  Project UUID (overrides automatic resolution)

#### `empirica scan-diff`

Diff two saved scan snapshots — added/removed processes + ports

**Arguments:**

- `scan_id_a` — **required**
  Older snapshot UUID or prefix
- `scan_id_b` — **required**
  Newer snapshot UUID or prefix
- `--project-id` — optional
  Project UUID (overrides auto-resolution)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica scan-history`

List past scan snapshots for the project (audit trail)

**Arguments:**

- `--limit` — optional · type=`int` · default=`20`
  Max rows to show (default: 20, 0 = all)
- `--project-id` — optional
  Project UUID (overrides auto-resolution)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica scan-show`

Show a saved scan snapshot by scan_id (UUID prefix accepted)

**Arguments:**

- `scan_id` — **required**
  Scan UUID or ≥8-char prefix
- `--project-id` — optional
  Project UUID (overrides auto-resolution)
- `--output` — optional · type=`choice` · choices={markdown, json} · default=`markdown`
  Output format (default: markdown)

#### `empirica security-audit`

Supply-chain security audit (pip-audit + CISA KEV)

**Arguments:**

- `--project-root` — optional · default=`.`
  Project root to audit (default: current directory)
- `--refresh-feeds` — optional · flag
  Force re-download of CISA KEV feed (otherwise cached for 24h)
- `--output` — optional · type=`choice` · choices={text, json} · default=`text`
  Output format (default: text)

#### `empirica services-audit`

One fire of the services-audit loop: scan + diff vs prior + notify on novel services

**Arguments:**

- `--no-notify` — optional · flag
  Skip notification dispatch even when novelty detected (testing / dry-run mode)
- `--project-id` — optional
  Project UUID (overrides auto-resolution)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json — loop bodies consume this)

#### `empirica source-archive`

Soft-delete an epistemic source (preserves edges + citing artifacts)

**Arguments:**

- `--source-id` — **required**
  Source UUID (or unique prefix) to archive
- `--reason` — **required** · type=`choice` · choices={user_deleted, file_missing, url_unreachable, superseded}
  Why this source is being archived
- `--target-id` — optional
  Replacement source UUID (REQUIRED when --reason superseded — the chain forward)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica source-list`

List epistemic sources for a project

**Arguments:**

- `--project-id` — optional
  Project UUID or name (auto-derived from context)
- `--type` — optional
  Filter by source type (document, code, web, api, etc.)
- `--direction` — optional · type=`choice` · choices={noetic, praxic, all} · default=`all`
  Filter by direction (noetic=evidence IN, praxic=output OUT)
- `--include-archived` — optional · flag
  Include soft-deleted/archived sources (forensics view; archived rows hidden by default)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed info

#### `empirica system-status`

Unified Noetic OS system status (config, memory, bus, gate, integrity)

**Arguments:**

- `--session-id` — optional
  Session UUID (auto-detects if omitted)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)
- `--summary` — optional · flag
  One-line summary (for statusline)

#### `empirica training-export`

Export epistemic transaction data as JSONL for model fine-tuning

**Arguments:**

- `--output-path` — optional
  Output JSONL file path (default: stdout)
- `--workspace` — optional · flag
  Export from ALL project databases in workspace (not just current)
- `--project-id` — optional
  Filter by project (prefix match)
- `--ai-id` — optional
  Filter by AI ID (e.g., claude-code)
- `--min-vectors` — optional · type=`int` · default=`3`
  Minimum vector count to include a transaction (default: 3)
- `--no-artifacts` — optional · flag
  Exclude noetic artifacts (findings, unknowns, dead-ends)
- `--no-grounded` — optional · flag
  Exclude grounded calibration data
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed info

#### `empirica trajectory-backfill`

Backfill trajectories from historical git notes (experimental)

**Arguments:**

- `--min-phases` — optional · type=`int` · default=`2`
  Minimum phases required (default: 2)
- `--analyze` — optional · flag
  Run pattern analysis after backfill
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica trajectory-show`

Show vector trajectory for a session (experimental)

**Arguments:**

- `--session-id` — optional
  Session ID to show trajectory for
- `--pattern` — optional · type=`choice` · choices={breakthrough, dead_end, stable, oscillating, unknown}
  Filter by pattern type
- `--limit` — optional · type=`int` · default=`10`
  Maximum trajectories to show (default: 10)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica trajectory-stats`

Show trajectory pattern statistics (experimental)

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica visibility`

Visibility tiers (public/shared/local) — list and inspect artifact classification

**Subcommands:**

##### `empirica visibility list`

Show artifact counts by visibility tier

**Arguments:**

- `--project-id` — optional
  Project UUID (default: active project)
- `--tier` — optional · type=`choice` · choices={public, shared, local}
  Filter to a single tier
- `--type` — optional · type=`choice` · choices={finding, unknown, dead_end, mistake, assumption, decision, goal}
  Filter to a single artifact type
- `--limit` — optional · type=`int` · default=`10`
  Recent items to show per tier (default: 10)
- `--output` — optional · type=`choice` · choices={json, human} · default=`human`
  Output format (default: human)


##### `empirica visibility show`

Show visibility tier for one artifact (by UUID prefix)

**Arguments:**

- `artifact_id` — **required**
  Artifact UUID or prefix (≥8 chars)
- `--output` — optional · type=`choice` · choices={json, human} · default=`human`
  Output format (default: human)


#### `empirica voice`

Prosodic voice profiles — load tendencies for outreach drafting

**Subcommands:**

##### `empirica voice list`

List available voice profiles (project-local + global)

**Arguments:**

- `--output` — optional · type=`choice` · choices={json, human} · default=`human`
  Output format (default: human)


##### `empirica voice show`

Print full profile yaml + computed summary

**Arguments:**

- `name` — **required**
  Profile name (filename without .yaml)
- `--output` — optional · type=`choice` · choices={json, human} · default=`human`
  Output format (default: human)


##### `empirica voice apply`

Print structured AI guidance for adopting a voice in a register

**Arguments:**

- `name` — **required**
  Profile name (filename without .yaml)
- `--register` — optional
  Platform register: email | reddit | devto | linkedin | medium | book. Falls back to natural_register if unset.
- `--output` — optional · type=`choice` · choices={json, human} · default=`human`
  Output format (default: human)


---
