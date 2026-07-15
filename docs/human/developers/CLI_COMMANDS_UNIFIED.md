# Empirica CLI Commands — Unified Reference

> **This document is reference-only.** It catalogs *what* commands and
> flags exist. For *why* — when to use a command, workflow patterns,
> decision trees — read the skills (`/empirica-constitution`,
> `/epistemic-transaction`, `/cortex-mailbox-send`, `/cortex-mailbox-poll`)
> and the `docs/architecture/` design docs. The split is intentional:
> mechanical reference rots fastest, so we auto-generate it; conceptual
> material is hand-curated where rot is slower and the cost of
> mis-explanation is highest.
>
> **Auto-generated** from the live argparse tree by
> `scripts/generate_cli_docs.py`. Do not edit by hand — your edits will
> be overwritten on the next regen. Add new commands by registering
> their parser via `add_*_parsers(subparsers)` in
> `empirica/cli/parsers/__init__.py`; the generator picks them up
> automatically. Per-command depth (the `help="..."` strings) is sourced
> from the parser definitions themselves — improving a description
> means editing the `add_argument` / `add_parser` call, not this file.
>
> Categories below follow `_HELP_CATEGORIES` in
> `empirica/cli/cli_core.py` — adding a new category means editing that
> dictionary, then running this script.

**Framework version:** 1.12.23
**Generated:** 2026-07-15 17:40:40 UTC
**Total commands:** 269 (across 26 categories)

For the most up-to-date detail on any single command, prefer
`empirica <command> --help` — the generator extracts the same `help`
strings argparse uses at runtime, but argparse can render dynamic context
(env-resolved defaults, conditional choices) that a static document
cannot.

For workflow guidance — "I want to do X, which command(s)?" — load
the relevant skill instead of grepping this reference. The skills know
the *why*; this doc only knows the *what*.

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
| [logging](#logging) | 32 | `finding-log`, `finding-resolve`, `unknown-log`, … |
| [project](#project) | 18 | `project-init`, `project-update`, `project-create`, … |
| [workspace](#workspace) | 22 | `workspace-init`, `workspace-map`, `workspace-list`, … |
| [checkpoint](#checkpoint) | 7 | `checkpoint-create`, `checkpoint-load`, `checkpoint-list`, … |
| [sync](#sync) | 6 | `sync-config`, `sync-push`, `sync-pull`, … |
| [profile](#profile) | 4 | `profile-sync`, `profile-prune`, `profile-status`, … |
| [identity](#identity) | 4 | `identity-create`, `identity-export`, `identity-list`, … |
| [handoff](#handoff) | 2 | `handoff-create`, `handoff-query` |
| [issue](#issue) | 6 | `issue-list`, `issue-show`, `issue-handoff`, … |
| [investigation](#investigation) | 5 | `investigate`, `investigate-create-branch`, `investigate-checkpoint-branch`, … |
| [monitoring](#monitoring) | 10 | `monitor`, `assess-state`, `trajectory-project`, … |
| [cockpit](#cockpit) | 16 | `status`, `tui`, `off`, … |
| [skills](#skills) | 3 | `skill-suggest`, `skill-fetch`, `skill-extract` |
| [architecture](#architecture) | 3 | `assess-component`, `assess-compare`, `assess-directory` |
| [agents](#agents) | 7 | `agent-spawn`, `agent-report`, `agent-aggregate`, … |
| [sentinel](#sentinel) | 4 | `sentinel-orchestrate`, `sentinel-load-profile`, `sentinel-status`, … |
| [personas](#personas) | 4 | `persona-list`, `persona-show`, `persona-promote`, … |
| [lessons](#lessons) | 9 | `lesson-create`, `lesson-load`, `lesson-list`, … |
| [mcp](#mcp) | 1 | `mcp-list-tools` |
| [memory](#memory) | 6 | `memory-prime`, `memory-scope`, `memory-value`, … |
| [vision](#vision) | 1 | `vision` |
| [domains](#domains) | 4 | `domain-list`, `domain-show`, `domain-resolve`, … |
| [setup](#setup) | 8 | `onboard`, `setup-claude-code`, `plugin-sync`, … |

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

Open an epistemic transaction. Records baseline vectors + task context as the starting measurement point. Must be called before any praxic tool (Edit/Write/Bash); the Sentinel firewall enforces this. Pairs with check-submit (mid-cycle gate) and postflight-submit (close). AI-first: pass JSON via stdin or a config file path.

**Arguments:**

- `config` — **required**
  JSON config file path, or "-" to read JSON from stdin (AI-first mode). Required unless using --vectors / --reasoning flags. The JSON object holds the full assessment payload — `vectors`, `reasoning`, optional `session_id`, plus PREFLIGHT-only `task_context` / `work_type` / `domain` / `criticality`. Example: `empirica preflight-submit - <<EOF\n{"vectors":{...}, "reasoning":"..."}\nEOF`
- `--session-id` — optional
  Session UUID (legacy flag-based mode). Normally auto-derived from the active transaction file; only needed when running outside a transaction or against a specific session_id.
- `--vectors` — optional
  Epistemic vectors as a JSON dict (legacy mode). Prefer passing them inside the config-file payload instead. Example: '{"know":0.7, "uncertainty":0.2, "context":0.6, "engagement":0.9}'
- `--reasoning` — optional
  Free-text narrative explaining the baseline assessment (legacy mode). What you know, what you don't, why these vector values reflect your actual epistemic state right now. Prefer setting in the config-file payload as `"reasoning": "..."`.
- `--voice` — optional
  Voice profile name to load for outreach drafting work (e.g. `--voice david`). Resolved via the empirica voice loader. Only relevant for outreach / publishing transactions; ignored for code / docs / research work.
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json — AI-friendly, machine-parsable). Use `human` when reading by eye at the terminal.
- `--verbose` — optional · flag
  Echo extra operation info to stderr (DB paths, timing, debug detail). Doesn't affect the structured output on stdout.

#### `empirica check`

Run an epistemic check WITHOUT submitting it as the gate decision. Use this to probe whether your current state would pass the noetic→praxic gate before committing to the transition. For actually gating, use `check-submit`.

**Arguments:**

- `config` — **required**
  JSON config file path, or "-" to read JSON from stdin (AI-first mode). Required unless using --vectors / --reasoning flags. The JSON object holds the full assessment payload — `vectors`, `reasoning`, optional `session_id`, plus PREFLIGHT-only `task_context` / `work_type` / `domain` / `criticality`. Example: `empirica preflight-submit - <<EOF\n{"vectors":{...}, "reasoning":"..."}\nEOF`
- `--session-id` — optional
  Session UUID (legacy flag-based mode). Normally auto-derived from the active transaction file; only needed when running outside a transaction or against a specific session_id.
- `--findings` — optional
  Investigation findings logged this transaction, as a JSON array (legacy mode). Usually unnecessary — the gate reads logged findings from the active transaction directly.
- `--unknowns` — optional
  Open unknowns at the gate, as a JSON array (legacy mode). Usually unnecessary — the gate reads logged unknowns from the active transaction directly. See also --remaining-unknowns.
- `--remaining-unknowns` — optional
  Alias for --unknowns (legacy compatibility shim).
- `--confidence` — optional · type=`float`
  Overall confidence score 0.0–1.0 (legacy mode). The gate prefers the per-vector breakdown in the config payload; --confidence is a flat-scalar fallback for old callers.
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json — AI-friendly, machine-parsable). Use `human` when reading by eye at the terminal.
- `--verbose` — optional · flag
  Show detailed gate-decision analysis (which vectors blocked, what threshold inflation was applied, Brier scoring detail).

#### `empirica check-submit`

Submit a check assessment AND apply the gate decision. Pass `decision`=`proceed` to move to the praxic phase, `investigate` to stay noetic, `proceed_with_caution` for a soft gate. The Sentinel firewall reads the result to allow/deny subsequent praxic tools. Required after PREFLIGHT before any Edit/Write/Bash.

**Arguments:**

- `config` — **required**
  JSON config file path, or "-" to read JSON from stdin (AI-first mode). Required unless using --vectors / --reasoning flags. The JSON object holds the full assessment payload — `vectors`, `reasoning`, optional `session_id`, plus PREFLIGHT-only `task_context` / `work_type` / `domain` / `criticality`. Example: `empirica preflight-submit - <<EOF\n{"vectors":{...}, "reasoning":"..."}\nEOF`
- `--session-id` — optional
  Session UUID (legacy flag-based mode). Normally auto-derived from the active transaction file; only needed when running outside a transaction or against a specific session_id.
- `--vectors` — optional
  Epistemic vectors as a JSON dict (legacy mode). Prefer passing them inside the config-file payload instead. Example: '{"know":0.7, "uncertainty":0.2, "context":0.6, "engagement":0.9}'
- `--decision` — optional · type=`choice` · choices={proceed, investigate, proceed_with_caution}
  Gate decision (legacy mode). `proceed` → praxic phase unlocks. `investigate` → stay noetic, more reads/searches needed. `proceed_with_caution` → soft gate (tools unlock but Sentinel logs a warning). Usually carried inside the config payload rather than this flag.
- `--reasoning` — optional
  Free-text explaining the gate decision (legacy mode). What investigation answered the original unknowns, what residual uncertainty remains, why proceeding now is the right call.
- `--cycle` — optional · type=`int`
  Investigation cycle number (legacy mode). 1 on first CHECK, increments if you re-investigate then re-CHECK before proceeding.
- `--round` — optional · type=`int`
  Round number used for checkpoint tracking across multi-stage investigations (legacy mode).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human — readable at the terminal). Use `json` when scripting or feeding into another tool.
- `--verbose` — optional · flag
  Echo extra operation info to stderr (DB paths, timing, debug detail). Doesn't affect the structured output on stdout.

#### `empirica postflight-submit`  _(aliases: `post`, `postflight`)_

Close the epistemic transaction. Records final vectors + a reasoning narrative describing what changed since PREFLIGHT. Triggers the grounded-calibration pipeline (compares your beliefs to deterministic evidence: git, lint, tests, artifact logs). Run after committing the work — uncommitted edits are invisible to the change/state/do evidence sensors.

**Arguments:**

- `config` — **required**
  JSON config file path, or "-" to read JSON from stdin (AI-first mode). Required unless using --vectors / --reasoning flags. The JSON object holds the full assessment payload — `vectors`, `reasoning`, optional `session_id`, plus PREFLIGHT-only `task_context` / `work_type` / `domain` / `criticality`. Example: `empirica preflight-submit - <<EOF\n{"vectors":{...}, "reasoning":"..."}\nEOF`
- `--session-id` — optional
  Session UUID (legacy flag-based mode). Normally auto-derived from the active transaction file; only needed when running outside a transaction or against a specific session_id.
- `--vectors` — optional
  Epistemic vectors as a JSON dict (legacy mode). Prefer passing them inside the config-file payload instead. Example: '{"know":0.7, "uncertainty":0.2, "context":0.6, "engagement":0.9}'
- `--reasoning` — optional
  Free-text describing what changed from PREFLIGHT to POSTFLIGHT (legacy mode). Surface what you learned, what surprised you, what you shipped, what residual unknowns carry into the next transaction.
- `--changes` — optional
  Deprecated alias for --reasoning. Use --reasoning instead.
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json — AI-friendly, machine-parsable). Use `human` when reading by eye at the terminal.
- `--verbose` — optional · flag
  Echo extra operation info to stderr (DB paths, timing, debug detail). Doesn't affect the structured output on stdout.

---

## goals

#### `empirica goals-create`  _(aliases: `goal-create`, `gc`)_

Create a new goal — the unit of tracked work. One per coherent deliverable: a feature, a fix, a doc sweep. Set --status planned when scoped-but-not-started (collaborative planning); in_progress when actively working. For multi-step work, follow with goals-add-task per distinct unit. AI-first: pass JSON via stdin/file; legacy: --objective + flags.

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
- `--status` — optional · type=`choice` · choices={planned, in_progress, blocked} · default=`in_progress`
  Initial status: 'planned' (logged, not started), 'in_progress' (active, default), or 'blocked' (waiting on external dependency)
- `--force` — optional · flag
  Create goal even if similar goal exists
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-list`  _(aliases: `goal-list`, `gl`)_

List goals in the current project. Default: active (in_progress). Use --status {planned,in_progress,completed,all,drift} for finer filtering; "drift" surfaces goals where the status text disagrees with is_completed (data-integrity check). Scope-* flags filter on goal-shape vectors (breadth, duration, coordination). For semantic queries, use goals-search.

**Arguments:**

- `--ai-id` — optional
  Filter by AI identifier
- `--session-id` — optional
  Derive project_id from session (convenience)
- `--transaction-id` — optional
  Filter by transaction ID (measurement scope)
- `--project-id` — optional
  Filter by project ID (structural scope)
- `--all-projects` — optional · flag
  Cross-project view (gardening): list goals across ALL project_ids, not just the active project. Adds a project column and raises the default limit. Surfaces goals stranded under other/divergent project_ids that the normal active-project scope hides.
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
- `--status` — optional · type=`choice` · choices={planned, in_progress, blocked, completed, all, drift}
  Filter by lifecycle status. Takes precedence over --completed. "drift" surfaces rows where status text disagrees with is_completed (canonical).
- `--limit` — optional · type=`int` · default=`20`
  Max results (default: 20)
- `--include-archived` — optional · flag
  Include archived completed goals (hidden by default; archive via goals-archive)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-search`

Semantic search across goals + tasks (Qdrant embeddings). Finds matches by meaning, not just keyword — "authentication system" surfaces "user login flow", "JWT validation". Pass a positional query string. Use to find prior work on a topic before duplicating effort, or to resurface relevant goals across sessions. For status-only listing, use goals-list.

**Arguments:**

- `query` — **required**
  Search query (e.g., "authentication system")
- `--project-id` — optional
  Project ID (auto-detects if not provided)
- `--type` — optional · type=`choice` · choices={goal, task}
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

Close a goal as done. Pass --reason explaining what shipped (commit SHAs, what got verified). Optional: --merge-branch + --delete-branch to wrap the git side, --run-postflight to auto-close the active transaction. Run BEFORE postflight-submit so the closure shows up in the transaction's grounded evidence.

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

Start working on a goal: create a git branch named after it, link to the BEADS issue, optionally run PREFLIGHT. Differs from goals-resume (takeover of a peer's goal) — claim is for goals already yours that you're committing to start. Skip branch creation with --no-branch for non-code goals.

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

#### `empirica goals-add-task`  _(aliases: `goal-add-task`)_

Decompose a goal into trackable units. One task per distinct piece of work you'll execute (read this, edit that, write these tests). Decompose at PREFLIGHT, not retroactively — tasks added after the work is done are self-graded checkboxes, not tracked units. Close each with goals-complete-task + --evidence as you finish.

**Arguments:**

- `--goal-id` — **required**
  Goal UUID
- `--description` — **required**
  Task description
- `--importance` — optional · type=`choice` · choices={critical, high, medium, low} · default=`medium`
  Epistemic importance
- `--dependencies` — optional
  Dependencies as JSON array
- `--estimated-tokens` — optional · type=`int`
  Estimated token usage
- `--use-beads` — optional · flag
  Create BEADS task and link to goal
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

#### `empirica goals-complete-task`  _(aliases: `goal-complete-task`)_

Close a task with evidence of completion. Always pass --evidence: commit SHA, test result, file path, link — whatever proves the work landed. Empty completions inflate the goal-completion vector without grounding it. Close as-you-go, not batched at the end.

**Arguments:**

- `--task-id` — **required**
  Task UUID (full or unambiguous prefix)
- `--evidence` — optional
  Completion evidence (commit hash, file path, etc.)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-get-tasks`

Dump the full task list for a goal (id, description, status, evidence, importance). Useful for picking the next task to work on, or for grepping task ids when completing several at once.

**Arguments:**

- `--goal-id` — **required**
  Goal UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-progress`  _(aliases: `goal-progress`)_

Show task-level progress for a single goal: how many tasks total, how many completed, with their evidence. Useful before deciding whether to close the goal (goals-complete) or whether more tasks are needed.

**Arguments:**

- `--goal-id` — **required**
  Goal UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica goals-discover`

Surface goals created by OTHER AIs in this project (via git notes sync). Use for cross-AI coordination — "what is the cortex AI working on right now?" — before duplicating or colliding. Filter by --from-ai-id or --session-id. Pair with goals-resume to pick one up.

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

Find work that's ready to start — open goals/tasks with their dependencies satisfied AND your current epistemic state meets the confidence/uncertainty thresholds. Wraps BEADS priority filtering with empirica's vector gates. Use when asking "what can I tackle next?" rather than scrolling goals-list manually.

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

Take over a goal another AI started. Reassigns the goal's ai_id to you, imports its tasks + history into your session's context. Use after goals-discover surfaces work a peer left mid-flight, or during planned handoff.

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

Flag in_progress goals as stale (typically called by the pre-compact hook before context loss). Marks them for re-evaluation on the other side. Not for manual cleanup — use goals-prune for that. Pair: goals-get-stale to retrieve.

**Arguments:**

- `--session-id` — **required**
  Session UUID
- `--reason` — optional · default=`memory_compact`
  Reason for marking stale (default: memory_compact)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-get-stale`

List goals marked stale by goals-mark-stale (typically set by the pre-compact hook). Used after compaction to decide which goals to refresh (still relevant) vs prune (superseded by what happened). Pair: goals-refresh / goals-prune.

**Arguments:**

- `--session-id` — optional
  Filter by session ID
- `--project-id` — optional
  Filter by project ID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica goals-refresh`

Move a stale goal back to in_progress after you've regained context (typically post-compact). Use after goals-get-stale surfaces the goal and you've confirmed it's still relevant. For irrelevant stale goals, prefer goals-complete (with reason) or goals-prune.

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to refresh
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

---

## logging

#### `empirica finding-log`  _(aliases: `fl`)_

Log a discovery — something concrete you NOW know that wasn't obvious before. Use for: facts surfaced from a read/grep, patterns observed in the codebase, verified assumptions, resolved unknowns, behaviors confirmed by experiment. The core building block of the project knowledge graph. --impact 0.0-1.0 weights how much it matters. Pair with --source <id> when the finding came from external material.

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
- `--task-id` — optional
  Optional task UUID
- `--subject` — optional
  Subject/workstream identifier (auto-detected from directory if omitted)
- `--impact` — optional · type=`float`
  Impact score 0.0-1.0 (importance of this finding, auto-derived from CASCADE if omitted)
- `--scope` — optional · type=`choice` · choices={session, project, both}
  Scope: session (ephemeral), project (persistent), or both (dual-log). Auto-inferred if omitted.
- `--source` — optional
  Source ID (from source-add). Repeatable for multiple sources.
- `--cite` — optional
  Inline source: create + link a source in one call (kills the source-add→--source two-step). Value is the source title; pair with --cite-url / --cite-type.
- `--cite-url` — optional
  URL/path for the --cite inline source.
- `--cite-type` — optional · default=`reference`
  Source type for --cite (reference, doc, paper, url, design, …). Default: reference.
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

#### `empirica finding-resolve`

Resolve/supersede a finding — kept for history, dropped from live retrieval (PREFLIGHT/CHECK relevant_findings). Findings are the fruit that must be pluckable: recency-decay only knows 'old', never 'superseded'. Run to stop a stale/superseded finding resurfacing.

**Arguments:**

- `finding_id` — **required**
  Finding UUID (full or 8+ char prefix)
- `--resolution` — **required**
  Why resolved (e.g. stale, superseded, invalidated)
- `--superseded-by` — optional
  Finding ID that replaced it (superseded finding → its replacement)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica unknown-log`  _(aliases: `ul`)_

Log an open question — something you'd need to know before acting confidently, but don't yet. Use when investigation surfaces a gap (file not read yet, behavior unclear, decision pending input). The Sentinel CHECK gate reads open unknowns as a signal you may still be noetic. Close with `unknown-resolve` once answered (ideally with a finding link).

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
- `--task-id` — optional
  Optional task UUID
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

List open project unknowns (default) or resolved ones with --resolved. Useful at PREFLIGHT to surface stale questions that should be cleaned up, or between transactions to triage what still needs investigation. For cross-project unknowns, use `project-search --task "..." --global`.

**Arguments:**

- `--project-id` — optional
  Project UUID
- `--session-id` — optional
  Session UUID (to derive project)
- `--resolved` — optional · flag
  Show resolved unknowns instead of open
- `--all` — optional · flag
  Show both open and resolved
- `--all-projects` — optional · flag
  Cross-project view (gardening): list unknowns across ALL project_ids, not just the active project. Adds a project column. Surfaces unknowns stranded under divergent project_ids.
- `--subject` — optional
  Filter by subject/workstream
- `--limit` — optional · type=`int` · default=`30`
  Max unknowns to show (default: 30)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica unknown-resolve`

Close an open unknown — pass the answer as --resolved-by and optionally link the finding that answered it via --finding. Run before POSTFLIGHT to drop the CHECK-gate weight of stale questions and surface the resolution as evidence for the next transaction's grounded calibration.

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

Log an approach that didn't work. Use when you tried something and the result rules out a path (lib X doesn't support Y, refactor strategy hit a wall, fix attempt made things worse). Differs from mistake-log (an error you made) — dead-ends are about the approach. CHECK reads dead-ends as evidence of search effort. --why-failed is load-bearing.

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
- `--task-id` — optional
  Optional task UUID
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

Log a belief you're acting on without verification. Use when proceeding requires taking something for granted (e.g. "Redis is available", "the spec is current"). Differs from finding-log (verified fact) — assumptions are explicitly unverified, with a --confidence 0.0-1.0 stating how much you trust them. Convert to finding-log once verified, or decision-log if you decide to act despite the uncertainty.

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

Log a deliberate choice between alternatives. Use at every fork: which library, which approach, which trade-off, even "keep the current behavior" when it was reconsidered. --rationale explains the WHY, --alternatives lists what was rejected, --reversibility flags how easily it can be undone (exploratory / committal / forced). Link supporting findings via --evidence <id>. The audit trail for "why is the code this way?" questions.

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

Log an error YOU made + how to prevent it. Use when you introduced a bug, mis-applied a pattern, broke an assumption, or otherwise produced output that needed correction. Differs from deadend-log (an approach that didn't work) — mistakes are about your decision-making, dead-ends about the approach. The --prevention flag is the load-bearing field for future-you.

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

Look up logged mistakes — useful before tackling work that echoes a pattern you've gotten wrong before. Filter by --session-id (this session's only) or --goal-id (mistakes against a specific goal). For semantic search across mistake narratives, use `project-search --task "..."` instead.

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

#### `empirica note`

Jot a quick note-to-self while in flow — a scratchpad for things to check on after the current work. Faster + lower-friction than a full finding/decision: pure metadata, NOT shared, NOT embedded. Notes are transaction-scoped and surface at POSTFLIGHT for triage (promote to an artifact/goal, or discard). They survive context compaction. Use --list to review, --clear to mark triaged.

**Arguments:**

- `text` — **required**
  The note text (positional, the common case)
- `--text` — optional
  The note text (flag form, for MCP/scripts)
- `--tag` — optional
  Optional free-form tag (suggested: followup | doubt | idea)
- `--list` — optional · flag
  List untriaged notes for the current transaction/session
- `--clear` — optional · flag
  Mark the current transaction/session notes as triaged
- `--session-id` — optional
  Session UUID
- `--project-id` — optional
  Project UUID
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica source-add`

Register external material as a citable source. Use for any evidence outside the current code (RFC, paper, blog, customer call, design doc, screenshot, vendor contract). Pass --noetic when it informed your knowledge, --praxic when you produced it as output. Returns a source UUID — link it from findings / decisions / dead-ends via `--source <uuid>` on those *-log commands so the audit trail traces back to origin.

**Arguments:**

- `--title` — **required**
  Source title
- `--description` — optional
  Source description
- `--source-type` — optional · default=`document`
  Source type (document, meeting, email, calendar, code, web, design, api)
- `--path` — optional
  File path (for local documents)
- `--media` — optional
  Local media/binary file (image, etc.) to register AND upload to cortex as a blob, so peers can fetch it cross-tenant via `source-get`. Implies --path. Pair with --visibility shared for cross-tenant reads (producer half of media-bearing sources).
- `--url` — optional
  URL (for web sources)
- `--cortex-url` — optional
  Cortex URL override (default: credentials.yaml)
- `--api-key` — optional
  Cortex API key override (default: credentials.yaml)
- `--noetic` — optional · flag
  Source used — evidence that informed knowledge (source IN)
- `--praxic` — optional · flag
  Source created — output produced by action (source OUT)
- `--confidence` — optional · type=`float` · default=`0.7`
  Confidence in source quality (0.0-1.0, default: 0.7)
- `--visibility` — optional · type=`choice` · choices={public, shared, local}
  Visibility tier (default: shared). public=world-shareable, shared=team-private, local=machine-only. Required for cross-mesh source-map participation.
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

#### `empirica source-get`

Download a media-bearing source's retained bytes from cortex to a local file. Fetches via the source uuid, verifies the SHA-256 content hash, and writes to --out. Cross-tenant fetch is permitted when the source's visibility (shared/public) allows the caller; cortex writes a per-fetch access-log entry. The consumer half of media-bearing sources — pairs with `source-add --media`.

**Arguments:**

- `--id` — **required**
  Source UUID to fetch
- `--out` — **required**
  Local path to write the fetched bytes to
- `--cortex-url` — optional
  Cortex URL override (default: credentials.yaml)
- `--api-key` — optional
  Cortex API key override (default: credentials.yaml)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica source-list`

List registered sources for a project. Filter by --type (document/code/web/api/…) or --direction (noetic/praxic/all). Useful for finding the source UUID to cite in a new artifact, or for auditing what external material has informed the project. Archived sources are hidden by default — pass --include-archived for forensics.

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

#### `empirica sources-map`

Show the cross-mesh source map for the current project. Locally owned sources (from epistemic_sources) plus, with --global, sources discoverable across other practices via project-scoped Qdrant collections. The Maven-POM-for-knowledge view: who owns what canonical reference material across the mesh.

**Arguments:**

- `--project-id` — optional
  Project UUID or name (auto-derived from context)
- `--global` — optional · flag
  Include sources discoverable in other projects' Qdrant collections (cross-mesh)
- `--query` — optional
  Optional semantic search query for cross-mesh discovery (default: empty → recent sources by upload order)
- `--type` — optional
  Filter by source type (document, code, web, api, etc.)
- `--limit` — optional · type=`int` · default=`20`
  Max cross-mesh results to surface (default: 20)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed info

#### `empirica sources-reconcile`

Match local sources against the central catalogue by content identity and adopt the catalogue uuid. Default adopt is NON-DESTRUCTIVE: the local row keeps its PK and stores the catalogue uuid as an alias (the daemon resolves id OR cortex_uuid). Pass --converge for the destructive one-uuid PK-swap + cascade (edges, supersession pointers, finding source_refs), then run `empirica rebuild` to re-point Qdrant. Also lazy-backfills content_hash/size/canonical_path on file-backed rows predating migration 050. Dry-run by default; pass --apply to mutate.

**Arguments:**

- `--apply` — optional · flag
  Perform the confirmed adopts (default: dry-run report)
- `--converge` — optional · flag
  With --apply: PK-swap local ids to the catalogue uuid (destructive one-uuid convergence + edge cascade). Default is a non-destructive alias adopt. Run `empirica rebuild` after --converge to re-point Qdrant.
- `--push-bodies` — optional · flag
  With --apply: also upload the BODY of each small adopted source (<= EMPIRICA_SMALL_BODY_THRESHOLD, default 1MB) to cortex via POST /v1/sources/{id}/body, so a remote peer can fetch it — sync-when-small (P2). Best-effort + idempotent (cortex dedupes on body_hash).
- `--project-id` — optional
  Project UUID (auto-derived from active session when omitted)
- `--cortex-url` — optional
  Cortex base URL (default: credentials.yaml / CORTEX_URL env)
- `--api-key` — optional
  Cortex API key (default: credentials.yaml / CORTEX_API_KEY env)
- `--register-shared` — optional · flag
  One-time convergence: push existing local-only shared/public sources up to cortex's catalogue (POST /v1/sources/register) so they materialize on the shared surface. Skips already-registered rows. Runs standalone (ignores the reconcile flow).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica sources-sanctify`

Classify the active source corpus and recommend hygiene actions: dead (canonical_path missing), duplicate (shared content_hash), zombie (no sourced_from reference), valid. Report-only (deletions go through review); retire flagged sources via source-archive.

**Arguments:**

- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica sources-check`

Probe the http(s) URLs in this project's epistemic sources and surface link-rot (dead / auth-walled / errored). SURFACE-ONLY — reports rot, never deletes (retire a dead source via delete-artifacts or source-archive). Exit 1 if any URL is dead. The smallest mechanical slice of artifact-hygiene (docs/architecture/ARTIFACT_HYGIENE.md).

**Arguments:**

- `--project-id` — optional
  Project UUID (auto-derived from active session when omitted)
- `--timeout` — optional · type=`float` · default=`6.0`
  Per-URL probe timeout in seconds (default: 6.0)
- `--staleness-days` — optional · type=`int`
  Only re-probe sources older than N days (fresh ones presumed live); 0 probes everything. Default: the practice's hygiene_policy source_staleness_days (30).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica source-archive`

Soft-delete a source. Use when the source is no longer valid (file deleted, URL dead, superseded by newer material). Edges from citing artifacts are preserved so the audit trail stays intact — the source just disappears from default listings. Pass --reason superseded + --target-id <newer-uuid> to chain forward to the replacement.

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

#### `empirica source-update`

Re-fetch a source and recompute its content identity (content_hash / size / mime). The ACT half of the source lifecycle: run it after sources-check flags a source stale or broken. Prefers a local canonical_path, else the http(s) source_url. A failed re-fetch updates nothing — an existing content_hash is never wiped by an unreachable source.

**Arguments:**

- `--source-id` — **required**
  Source UUID (or unique prefix) to re-fetch
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica source-review`

Record a human/AI review verdict on a source — the REVIEW half of the source lifecycle (CHECK detects, UPDATE re-fetches, REVIEW judges). Stamps last_reviewed_at + review_verdict and appends a 'reviewed' event to the lifecycle audit log. The verdict routes to the next action: stale→source-update, superseded/irrelevant→source-archive.

**Arguments:**

- `--source-id` — **required**
  Source UUID (or unique prefix) to review
- `--verdict` — **required** · type=`choice` · choices={valid, stale, superseded, irrelevant}
  valid (keep) | stale (→source-update) | superseded/irrelevant (→source-archive)
- `--note` — optional
  Optional free-text review note
- `--reviewer` — optional
  Who reviewed (ai_id or human name); recorded in the audit event
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica enforcement-report`

Artifact-graph enforce telemetry: block-rate and self-resolve-rate from weave_enforce_events. self-resolve-rate is the health metric for enforce-by-default — high means the gate nudges and the system recovers on its own; low means it may be over-blocking.

**Arguments:**

- `--session-id` — optional
  Scope to one session (default: all recorded verdicts)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica blindspot-scan`

Dry-run blindspot detection: predicted unknown-unknowns for a session — stated goals/tasks with no covering artifact and no acknowledging unknown (the intent-gap signal). Reports only; wired to nobody yet.

**Arguments:**

- `--session-id` — optional
  Session to scan (default: current session)
- `--include-planned` — optional · flag
  Include dormant 'planned' goals (backlog view); default scans active goals only
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica blindspot-report`

Blindspot telemetry: surfaced / acknowledged / dismissed / regretted rates from blindspot_events. acknowledge-rate = the nudge is useful; regret-rate = dismissed ones that later became mistakes/dead-ends.

**Arguments:**

- `--session-id` — optional
  Scope to one session (default: all events)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica act-log`

Log a batch of praxic actions (file edits, commands run, commits made) with their artifacts. Use to record a coherent unit of execution work in one call rather than several. For tracking individual artifact creations, prefer per-type *-log commands; for tracking task completion, prefer goals-complete-task with --evidence.

**Arguments:**

- `--session-id` — optional
  Session UUID. Auto-derived from active transaction if omitted.
- `--actions` — **required**
  JSON array describing actions taken. Example: '["Edited src/x.py", "Added test_y", "Ran ruff check"]'.
- `--artifacts` — optional
  JSON array of files modified/created/deleted. Example: '["src/x.py", "tests/test_y.py"]'. Augments git for actions that don't produce a commit yet.
- `--goal-id` — optional
  Goal UUID this action sequence advanced. Ties act-log to a tracked work unit.
- `--output` — optional · type=`choice` · choices={json, text} · default=`text`
  Output format. Use `json` when scripting; `text` for terminal.
- `--verbose` — optional · flag
  Echo extra diagnostic info to stderr.

#### `empirica investigate-log`

Log a batch of findings produced by an investigation phase. Use when you have multiple related discoveries to record at once (e.g. after reading several files, running a series of greps). For single discoveries, prefer finding-log directly.

**Arguments:**

- `--session-id` — optional
  Session UUID. Auto-derived from active transaction if omitted.
- `--findings` — **required**
  JSON array of finding strings or {finding, impact} objects. Example: '["X uses Y", "Z deprecated since v3"]' or '[{"finding":"X uses Y","impact":0.7}]'.
- `--evidence` — optional
  JSON object linking findings to supporting evidence — file paths, line numbers, commit SHAs, URLs. Example: '{"files":["src/x.py:42"], "commits":["abc123"]}'.
- `--output` — optional · type=`choice` · choices={json, text} · default=`text`
  Output format. Use `json` when scripting; `text` for terminal.
- `--verbose` — optional · flag
  Echo extra diagnostic info to stderr.

#### `empirica log-artifacts`

Log ≥3 connected artifacts in one call instead of N individual *-log invocations. Accepts a JSON graph (nodes = typed artifacts, edges = relationships). Use when artifacts have declared edges between them (sourced_from, evidence_for, supersedes, etc.) — the batch keeps the graph atomic. For a single artifact, prefer the per-type *-log command.

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

Close multiple open artifacts (unknowns, assumptions, goals) in one call. Typically used pre-POSTFLIGHT to clean up the ledger when investigation answered several questions at once. For a single artifact, prefer the per-type resolve verb (unknown-resolve, goals-complete).

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

Remove stale, duplicate, or test-noise artifacts from the ledger. Unlike resolve-artifacts (closes WITH a resolution reason), this hard-deletes from SQLite + Qdrant. The deletion itself is logged as a decision for audit. Use --dry-run first to preview. For "still valid but answered", use resolve. For "never should have been logged", use this.

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
  AI identifier to load epistemic handoff for (e.g., empirica, cortex; derives from project basename if omitted)
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
  Also search the global-learnings pool + other LOCAL projects (semantic, this machine). Cross-practice/mesh search is `cortex investigate`.

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

#### `empirica practice-context`

Ambassador addressbook — project roster as per-practitioner rows with substrate

**Arguments:**

- `--cortex-url` — optional
  Cortex base URL override (else env CORTEX_URL or ~/.empirica/credentials.yaml).
- `--api-key` — optional
  Cortex API key override (else env CORTEX_API_KEY or credentials.yaml).
- `--ai-id` — optional
  Filter to a single ai_id (default: all).
- `--timeout` — optional · type=`float` · default=`10.0`
  HTTP timeout in seconds (default: 10).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human table; json for autonomy / scripting).

#### `empirica projects-sync`  _(aliases: `project-sync`)_

One-shot: walk filesystem → upsert ~/.empirica/registry.yaml → register on Cortex. Idempotent. Use --no-cortex for offline, --no-write for pure preview, --dry-run for full preview. (Alias: project-sync.)

**Arguments:**

- `--root` — optional
  Root directory to walk (default: $HOME). Repeatable.
- `--max-depth` — optional · type=`int` · default=`5`
  Maximum walk depth from each root (default: 5).
- `--include-hidden` — optional · flag
  Walk hidden directories during discovery (default: skip).
- `--include` — optional
  Regex matched against project name OR path during Cortex POST. Repeatable — multi --include is OR. Doesn't affect discovery or registry.yaml — only filters what gets registered on Cortex.
- `--exclude` — optional
  Regex matched against project name OR path during Cortex POST. Repeatable — multi --exclude is OR (project dropped if ANY pattern matches). Applied after --include.
- `--no-cortex` — optional · flag
  Stop after registry.yaml write. Use when Cortex is down, offline-first setup, or when you only need the daemon's served set populated.
- `--no-write` — optional · flag
  Pure discover-only preview. Don't write the manifest cache, don't upsert registry.yaml, don't POST to Cortex. Equivalent to `--dry-run` for the discover phase only.
- `--prune` — optional · flag
  Remove stale entries from registry.yaml (projects no longer present on disk). Off by default — keeps the registry additive-only unless explicitly asked.
- `--dry-run` — optional · flag
  Full pipeline preview: walk, show what would be written/registered, but make no changes (no manifest write, no registry upsert, no Cortex POST). Strongest no-op flag.
- `--cortex-url` — optional
  Override Cortex base URL (default: $CORTEX_REMOTE_URL).
- `--api-key` — optional
  Override Cortex API key (default: $CORTEX_API_KEY).
- `--timeout` — optional · type=`float` · default=`10.0`
  Per-request timeout for Cortex POSTs in seconds (default: 10).
- `--force-metadata-update` — optional · flag
  Set `force_metadata_update: true` in each Cortex request body, asking Cortex to backfill UUID-shaped placeholder names + empty repo_urls on already-existing rows. Useful when Cortex has stale metadata that should be refreshed from local.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format for the summary (default: human).

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
- `--register` — optional
  After scanning, upsert each discovered project into ~/.empirica/registry.yaml (the daemon's served set). Idempotent — matches on project_id. Pass NAME to register a single project by directory basename or project.yaml name (e.g. `--register empirica-mesh-support`); pass no value to register all discovered projects. (v1.9.6+)
- `--prune` — optional · flag
  Only with --register (no NAME): also remove registry entries whose path no longer exists or no longer contains .empirica/.

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

#### `empirica projects-unregister`

Unregister a project from Cortex (soft archive by default; --purge to hard-delete).

**Arguments:**

- `--project-id` — optional
  Cortex project UUID. Mutually exclusive with --slug; one of them or .empirica/project.yaml required.
- `--slug` — optional
  Project slug (resolves on the cortex side against caller's projects).
- `--purge` — optional · flag
  Hard-delete instead of soft-archive. Cascade-deletes proposals + SERs + artifacts. Requires --confirm.
- `--confirm` — optional · flag
  Required with --purge — acknowledge the destructive operation.
- `--cortex-url` — optional
  Override Cortex base URL.
- `--api-key` — optional
  Override Cortex API key.
- `--timeout` — optional · type=`float` · default=`10.0`
  HTTP timeout in seconds (default: 10).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human).

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

#### `empirica entity-create`

Idempotent mint of a contact, engagement, or organization into the workspace entity registry. Contacts dedupe by email first (strongest key) then deterministic slug ('c-<name>[-<company>]'); engagements/organizations dedupe by slug id ('e-'/'o-' prefix, or pass --id explicitly). Re-minting the same identity returns the existing entity_id with created=false — a verified no-op. Other entity types (project, user) are written by their owning pipelines.

**Arguments:**

- `--type` — optional · type=`choice` · choices={contact, engagement, organization} · default=`contact`
  Entity type to mint (default: contact)
- `--name` — **required**
  Entity display name
- `--id` — optional
  Explicit entity_id (engagement/organization only; defaults to a '<prefix>-<name>' slug)
- `--email` — optional
  Email (contact primary identity key for dedupe)
- `--phone` — optional
  Phone number (contact)
- `--role` — optional
  Role/title at their organization (contact)
- `--company` — optional
  Company/organization name (contact — folded into the slug)
- `--description` — optional
  Free-text context for the entity
- `--metadata` — optional
  Extra metadata as a JSON object string
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica entity-list`

List entities from the workspace registry. Currently populated types: project, contact, organization, engagement, user. Default scope is active entities; use --status all to include inactive/archived.

**Arguments:**

- `--type` — optional
  Filter by entity_type (project|contact|organization|engagement|user)
- `--status` — optional · type=`choice` · choices={active, inactive, archived, all} · default=`active`
  Filter by status (default: active)
- `--limit` — optional · type=`int` · default=`100`
  Max rows (default: 100)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica entity-show`

Show one entity's full record plus membership edges (incoming and outgoing). Pass entity as 'type:id' or split via --type + --id. The id can be a full value or unambiguous prefix (≥4 chars).

**Arguments:**

- `entity` — **required**
  Entity reference as "type:id" (or use --type + --id)
- `--type` — optional
  Entity type (alternative to positional)
- `--id` — optional
  Entity id (alternative to positional)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica entity-walk`

BFS the membership graph from a starting entity, following edges in both directions (member_of + members). Pass the start node as 'type:id'. Default depth is 2; increase with --depth. Cycles are detected and skipped.

**Arguments:**

- `entity` — **required**
  Start entity as "type:id" (or use --type + --id)
- `--type` — optional
  Entity type (alternative to positional)
- `--id` — optional
  Entity id (alternative to positional)
- `--depth` — optional · type=`int` · default=`2`
  Max traversal depth (default: 2)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica entity-search`

Text-search entities by display_name + description (case-insensitive LIKE). For semantic search across artifacts, use project-search or workspace-search instead.

**Arguments:**

- `query` — **required**
  Search query (e.g. "MastersOfDirt")
- `--type` — optional
  Optional entity_type filter
- `--semantic` — optional · flag
  Semantic vector search over entity-row points (§6.2) instead of SQL LIKE
- `--status` — optional · type=`choice` · choices={active, inactive, archived, all} · default=`active`
  Filter by status (default: active)
- `--limit` — optional · type=`int` · default=`50`
  Max results (default: 50)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica entity-link`

Write (or soft-close) a typed membership edge between two entities: '<member> is <role> of <group>'. The write peer to entity-show/-walk's read path. Both refs are 'type:id'. Idempotent on the edge — re-linking updates role/notes and re-activates a soft-closed edge. Edges are never deleted; --close soft-closes (stamps left_at) so history stays auditable. Example: entity-link engagement:e-cowork-recovery organization:o-nle --role ticket_of

**Arguments:**

- `member` — **required**
  Member entity as 'type:id' (e.g. engagement:e-x)
- `group` — **required**
  Group entity as 'type:id' (e.g. organization:o-y)
- `--role` — optional
  Relation verb for the edge (e.g. ticket_of, member, serves)
- `--notes` — optional
  Optional free-text note on the edge
- `--close` — optional · flag
  Soft-close the edge (stamp left_at) instead of writing it
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica entity-delete`

Delete an entity. Default is a reversible soft-archive (status='archived' + close memberships); --hard does an irreversible dependent-order cascade and requires --confirm. Pass as 'type:id'.

**Arguments:**

- `entity` — **required**
  Entity as "type:id" (or use --type + --id)
- `--type` — optional
  Entity type (alternative to positional)
- `--id` — optional
  Entity id (alternative to positional)
- `--hard` — optional · flag
  Irreversible dependent-order cascade delete (requires --confirm)
- `--confirm` — optional · flag
  Confirm an irreversible --hard delete
- `--dry-run` — optional · flag
  Preview the effect without mutating
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica entity-reindex`

Backfill: embed every entity_registry row as a searchable workspace_index point (ERM §6.2 point_kind='entity'). Idempotent (stable ids → upsert). Run once after upgrading, or to reindex.

**Arguments:**

- `--type` — optional
  Optional entity_type filter (contact, organization, engagement)
- `--dry-run` — optional · flag
  Count rows without embedding
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

#### `empirica engagement-create`

Create an engagement: mints the engagement entity (the entities-mint path) then writes the operational sidecar row. Idempotent by slug. Optionally link to an organization with --org (role='ticket_of').

**Arguments:**

- `--title` — **required**
  Engagement title
- `--id` — optional
  Explicit engagement_id (defaults to an 'e-<title>' slug)
- `--domain` — optional
  Engagement domain (outreach|sales|support|security|infra|onboarding|...)
- `--stage` — optional
  Initial stage_id (must belong to --domain)
- `--engagement-type` — optional · default=`outreach`
  Engagement type (default: outreach)
- `--org` — optional
  Organization entity_id to link as role='ticket_of'
- `--description` — optional
  Free-text context
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica engagement-list`

List engagements (active-by-default) filtered by --domain / --lifecycle / --org; --include-closed for terminal ones.

**Arguments:**

- `--domain` — optional
  Filter by domain
- `--lifecycle` — optional
  Filter by lifecycle_state (planned|open|in_progress|blocked|closed), or 'all' for the full set
- `--org` — optional
  Scope to an organization's tickets (role='ticket_of')
- `--include-closed` — optional · flag
  Legacy sugar — add terminal (closed) engagements back. Default: active-only (open|in_progress|blocked); pre-active 'planned' stays out unless requested or --lifecycle all. Ignored when --lifecycle is given.
- `--limit` — optional · type=`int` · default=`100`
  Max rows (default: 100)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica engagement-show`

Show one engagement's record + its membership edges.

**Arguments:**

- `engagement_id` — **required**
  Engagement id (full value or unambiguous prefix)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica engagement-walk`

BFS the membership graph from an engagement (default depth 2).

**Arguments:**

- `engagement_id` — **required**
  Engagement id (full value or unambiguous prefix)
- `--depth` — optional · type=`int` · default=`2`
  Max traversal depth (default: 2)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

#### `empirica engagement-update`

Update mutable fields (title/description/stage/domain/lifecycle-state/outcome) on an existing engagement. No field flags = no-op read.

**Arguments:**

- `engagement_id` — **required**
  Engagement id (full value or unambiguous prefix)
- `--title` — optional
  New title
- `--description` — optional
  New free-text description
- `--domain` — optional
  New domain (must have a definition row)
- `--stage` — optional
  New stage_id (must belong to --domain, or the engagement's current domain)
- `--lifecycle-state` — optional · type=`choice` · choices={planned, open, in_progress, blocked, closed}
  New lifecycle_state
- `--outcome` — optional · type=`choice` · choices={won, lost, resolved, wont_fix, defer, superseded}
  Terminal outcome (typically set alongside --lifecycle-state closed)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output

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
  Rebuild SQLite from notes FIRST, then also rebuild Qdrant
- `--qdrant-only` — optional · flag
  Re-embed Qdrant from CURRENT SQLite only, SKIPPING the notes->SQLite rebuild (safe resync after direct-SQL/bulk changes not yet persisted to git notes)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

#### `empirica artifacts-generate`

Generate browsable .empirica/ markdown files from git notes

**Arguments:**

- `--output-dir` — optional
  Output directory (default: .empirica/, or .empirica/okf/ for OKF)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--format` — optional · type=`choice` · choices={audit, okf} · default=`audit`
  Bundle format: 'audit' = browsable per-type markdown (default); 'okf' = Open Knowledge Format bundle (one concept per artifact, portable/AI-friendly)
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
  Filter by AI identifier (default: all; canonical ai_ids derived from project basename)
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

#### `empirica grounding-export`

Export one practice's current grounding state (self-assessed + grounded 13-vectors + divergence) as JSON

**Arguments:**

- `--ai-id` — **required**
  Practice to export (canonical 3-form or bare basename)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)

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
- `--emit` — optional · flag
  Emit the result to cortex System│Diagnostics (needs a cortex api_key)
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

#### `empirica off`

Pause the Sentinel for this instance (off-the-record). Add --global to pause all instances.

**Arguments:**

- `--reason` — optional
  Optional human-readable reason for the pause
- `--instance` — optional
  Target instance_id OR a practice ai_id (resolved to its live runtime instance; no-match or ambiguous resolution fails loud)
- `--session` — optional
  Target the live instance running this claude_session_id
- `--all` — optional · flag
  Fan out across ALL live instances of the resolved practice (required when an ai_id maps to >1 live instance)
- `--global` — optional · flag
  Target the single global pause file (pauses the Sentinel for ALL instances, present and future). Broadest scope — overrides --instance/--all.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica on`

Resume the Sentinel for this instance (back on-the-record). Add --global for the global pause file.

**Arguments:**

- `--instance` — optional
  Target instance_id OR a practice ai_id (resolved to its live runtime instance; no-match or ambiguous resolution fails loud)
- `--session` — optional
  Target the live instance running this claude_session_id
- `--all` — optional · flag
  Fan out across ALL live instances of the resolved practice (required when an ai_id maps to >1 live instance)
- `--global` — optional · flag
  Target the single global pause file (pauses the Sentinel for ALL instances, present and future). Broadest scope — overrides --instance/--all.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)

#### `empirica sentinel`

Sentinel pause/resume/status (per-instance noetic firewall control)

**Subcommands:**

##### `empirica sentinel pause`

Pause Sentinel for an instance

**Arguments:**

- `--reason` — optional
  Optional human-readable reason for the pause
- `--instance` — optional
  Target instance_id OR a practice ai_id (resolved to its live runtime instance; no-match or ambiguous resolution fails loud)
- `--session` — optional
  Target the live instance running this claude_session_id
- `--all` — optional · flag
  Fan out across ALL live instances of the resolved practice (required when an ai_id maps to >1 live instance)
- `--global` — optional · flag
  Target the single global pause file (pauses the Sentinel for ALL instances, present and future). Broadest scope — overrides --instance/--all.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica sentinel resume`

Resume Sentinel for an instance

**Arguments:**

- `--instance` — optional
  Target instance_id OR a practice ai_id (resolved to its live runtime instance; no-match or ambiguous resolution fails loud)
- `--session` — optional
  Target the live instance running this claude_session_id
- `--all` — optional · flag
  Fan out across ALL live instances of the resolved practice (required when an ai_id maps to >1 live instance)
- `--global` — optional · flag
  Target the single global pause file (pauses the Sentinel for ALL instances, present and future). Broadest scope — overrides --instance/--all.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica sentinel status`

Show Sentinel pause state

**Arguments:**

- `--instance` — optional
  Target instance_id OR a practice ai_id (resolved to its live runtime instance; no-match or ambiguous resolution fails loud)
- `--session` — optional
  Target the live instance running this claude_session_id
- `--all` — optional · flag
  Fan out across ALL live instances of the resolved practice (required when an ai_id maps to >1 live instance)
- `--global` — optional · flag
  Target the single global pause file (pauses the Sentinel for ALL instances, present and future). Broadest scope — overrides --instance/--all.
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

Tear down the canonical mesh listener — reaps orphan listener processes for the ai_id, deletes the state file, and emits TaskStop + `unregister` next_step JSON

**Arguments:**

- `--name` — optional
  Listener name (default: <ai_id>-inbox)
- `--ai-id` — optional
  AI identifier (default: project basename via .empirica/project.yaml)
- `--instance` — optional
  Target instance_id (default: auto-detect from current process)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


##### `empirica listener gc`

Garbage-collect stale ~/.empirica/listener_active_*.json files AND orphaned listener processes (parent session dead). Dry-run by default; pass --apply to actually remove.

**Arguments:**

- `--apply` — optional · flag
  Actually remove the stale files + reap orphan processes (default: dry-run shows what would be removed)
- `--age-days` — optional · type=`int` · default=`7`
  Age threshold in days for the stale criterion (default: 7). Files older than this with no recent wake activity are pruned.
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


##### `empirica instance rebind`

Re-stamp an instance's captured pid from its live process (fixes stale pid after `claude --resume`)

**Arguments:**

- `instance_id` — **required**
  Target instance_id
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)


#### `empirica practitioner`

Practitioner presence: write/clear/list (keyed on claude_session_id)

**Subcommands:**

##### `empirica practitioner write`

Register/heartbeat this practitioner's presence

**Arguments:**

- `--session` — **required**
  claude_session_id (the durable practitioner key)
- `--status` — optional · default=`active`
  active | idle | paused | blocked (default: active)
- `--pending-question` — optional
  Blocked-reason (emit-and-park signal)
- `--session-pid` — optional · type=`int`
  Claude Code parent PID (os.getppid() at session-init) — the daemon's liveness anchor
- `--ai-id` — optional
  Practice ai_id (default: resolve from project context)
- `--location` — optional
  Location/instance_id (default: resolve from current process)
- `--empirica-session` — optional
  Empirica session id (default: resolve)
- `--active-transaction` — optional
  Active transaction id (default: resolve)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output


##### `empirica practitioner clear`

Clear this practitioner's presence (session-end)

**Arguments:**

- `--session` — **required**
  claude_session_id
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output


##### `empirica practitioner list`

List live practitioners (optionally scoped to a practice)

**Arguments:**

- `--practice` — optional
  Scope to a practice ai_id
- `--include-stale` — optional · flag
  Include stale (no recent heartbeat)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output


##### `empirica practitioner heartbeat`

Push local presence to cortex's /v1/practitioners/heartbeat

**Arguments:**

- `--session` — optional
  claude_session_id to emit (default: all local non-stale practitioners)
- `--include-stale` — optional · flag
  Include stale records when emitting all
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Verbose output


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
- `--no-archive` — optional · flag
  Close the parent but do NOT archive it. Default behaviour archives the parent after close to keep your inbox view focused on un-actioned work. Use --no-archive when you want the parent to stay visible in audit / status=accepted polls.
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)


##### `empirica mailbox poll`

Poll your cortex mesh inbox (or --outbox) — a CLI receive path so tool-aggregating harnesses skip the MCP namespace call

**Arguments:**

- `--ai-id` — optional
  Your ai_id (canonical 3-form or basename; default: from .empirica/project.yaml)
- `--outbox` — optional · flag
  Poll your OUTBOX (status changes on proposals YOU sent) instead of the inbox
- `--status` — optional
  Comma-separated status filter (default: 'accepted,changed' for inbox, 'completed,changed,declined' for outbox). Choices: eco_review, accepted, changed, declined, completed, expired.
- `--since` — optional
  ISO-8601 timestamp — only proposals created_at >= since (incremental polling)
- `--limit` — optional · type=`int` · default=`20`
  Max proposals (default: 20, cortex caps at 200)
- `--related` — optional · flag
  Include per-proposal related_goals[] semantic hints (default off — faster polls)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)


##### `empirica mailbox show`

Show one proposal's full body — GET /v1/orchestration/<id>

**Arguments:**

- `proposal_id` — **required**
  Proposal id (prop_…) to fetch
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format (default: json)


##### `empirica mailbox archive`

Archive a proposal (soft-delete from inbox view) — POST /v1/orchestration/<id>/archive

**Arguments:**

- `proposal_id` — **required**
  Proposal id (prop_…) to archive
- `--reason` — optional
  Optional archive reason (audit trail)
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

#### `empirica daemon-grant`

Approve a pending credential grant requested by the extension.

**Arguments:**

- `user_code` — **required**
  The short code printed by `empirica serve` (e.g. AB23-CDEF).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human).

#### `empirica daemon-deny`

Deny a pending credential grant requested by the extension.

**Arguments:**

- `user_code` — **required**
  The short code printed by `empirica serve`.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human).

#### `empirica daemon-grants-list`

List current daemon credential grant records on disk.

**Arguments:**

- `--output` — optional · type=`choice` · choices={table, json} · default=`table`
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

#### `empirica mcp-list-tools`

List MCP tools registered in the installed empirica-mcp package

**Arguments:**

- `--verbose` / `-v` — optional · flag
  Show tip for inspecting per-tool param schemas

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

- `--ai-id` — optional
  AI identifier (optional, derives from project basename or .empirica/project.yaml)

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
  Override tenant org_id (skip cortex tenant-metadata fetch for this field)
- `--tenant-slug` — optional
  Override tenant_slug (skip cortex tenant-metadata fetch for this field)
- `--mesh-id-prefix` — optional
  Override mesh_id_prefix (skip cortex tenant-metadata fetch for this field)
- `--skip-claude-md` — optional · flag
  Skip CLAUDE.md installation (keep existing system prompt)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format (default: human)
- `--verbose` — optional · flag
  Show detailed output

#### `empirica plugin-sync`

Re-sync the installed Claude Code plugin if it has drifted behind the running empirica version

**Arguments:**

- `--force` — optional · flag
  Sync even if the version stamp matches
- `--quiet` — optional · flag
  Suppress the human status line (still exits non-zero on error)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

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
  Port to listen on (default: 8000, or EMPIRICA_SERVE_PORT env; the explicit flag wins over the env var)
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

#### `empirica forgejo-publish`

Provision a managed Forgejo remote for a project (operator / self-hosting power-user tool, not an end-user default): POST cortex's forgejo-publish endpoint, write the access token 0600, add the 'forgejo' git remote, and push the cortex-supplied refspecs. This is the PUSH mode for projects with no existing remote — distinct from the managed pull-mirror path. Leaves 'origin' (repo_url) untouched.

**Arguments:**

- `path` — **required** · default=`.`
  Project root path (default: current directory)
- `--rotate` — optional · flag
  Mint a fresh access token (revokes the prior) — also the way to re-push an already-published project.
- `--description` — optional
  Optional Forgejo repo description.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

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

Flip a planned goal to in_progress and link it to the active transaction. Use when you're ready to start work on a goal created earlier as planned (collaborative pre-scoping). Differs from goals-claim — activate is the same-AI status transition; claim is the lifecycle hook (branch, BEADS).

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to activate (prefix match)
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

#### `empirica goals-archive`  _(aliases: `goal-archive`)_

Archive completed goals older than N days so the completed list doesn't grow unbounded (mirrors source-archive). Archived goals drop out of goals-list unless --include-archived; goals-reopen un-archives. Dry-run by default; pass --apply to archive.

**Arguments:**

- `--older-than` — optional · type=`int` · default=`30`
  Age threshold in days on completion time (default: 30)
- `--goal-id` — optional
  Archive one completed goal by id/prefix (ignores --older-than)
- `--apply` — optional · flag
  Actually archive (default: dry-run report)
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

#### `empirica goals-reopen`  _(aliases: `goal-reopen`)_

Reopen a COMPLETED goal — flip it back to in_progress and re-link it to the active transaction. The inverse of goals-complete: undo an accidental or premature completion so it re-enters the active list.

**Arguments:**

- `--goal-id` — **required**
  Goal UUID to reopen (prefix match)
- `--reason` — optional
  Optional note recorded in the goal's reopen history
- `--output` — optional · type=`choice` · choices={human, json} · default=`json`
  Output format

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

#### `empirica mesh`

Unified mesh diagnostic + control surface across listener instances and (optional) cortex bridge

**Subcommands:**

##### `empirica mesh status`

Show health table across mesh instances (green/yellow/red + reason)

**Arguments:**

- `instance` — **required**
  ai_id (default: enumerate all installed listener services)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`


##### `empirica mesh diagnose`

Deep per-instance diagnostic + suggest exact fix command

**Arguments:**

- `instance` — **required**
  ai_id to diagnose
- `--cortex` — optional · flag
  Also run cortex-side participation checks (identity + channels endpoint + listener subscription URL + ntfy ACL probe + mesh agreements). Cross-correlates the local view with cortex's view of this practitioner so silent-failure classes (label mismatch, topic drift, ACL 403, silent strand) surface at one verb.
- `--peer` — optional
  With --cortex, also probe mesh_sharing_agreement with this peer (canonical 3-form like 'empirica.philipp.empirica-autonomy'). Fails if the agreement row is missing.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`


##### `empirica mesh restart`

Restart the listener service for an instance (clears curl zombies)

**Arguments:**

- `instance` — **required**
  ai_id to restart


##### `empirica mesh on`

Install + start + enable the listener service for an instance

**Arguments:**

- `instance` — **required**
  ai_id to bring online


##### `empirica mesh off`

Stop the listener service for an instance

**Arguments:**

- `instance` — **required**
  ai_id to bring offline
- `--uninstall` — optional · flag
  Also remove the systemd/launchd unit (default: stop only)


##### `empirica mesh tail`

Live tail loop_fires.log filtered by instance(s)

**Arguments:**

- `instance` — **required**
  ai_id (default: tail all installed instances)


##### `empirica mesh migrate-topics`

Migrate legacy per-practice + retired bare ntfy topics to the per-tenant canonical (closes SER canonical-channel model)

**Arguments:**

- `--apply` — optional · flag
  Actually rewrite credentials.yaml + listener_active markers (default: dry-run reports what would change)
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`


#### `empirica mesh-agreements`

Mesh sharing agreement mirror — sync / list cortex agreements locally

**Subcommands:**

##### `empirica mesh-agreements sync`

Pull the org mesh-sharing agreements from cortex; upsert into entity_registry

**Arguments:**

- `--cortex-url` — optional
  Cortex base URL override.
- `--api-key` — optional
  Cortex API key override.
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`


##### `empirica mesh-agreements list`

List mirrored mesh sharing agreements

**Arguments:**

- `--status` — optional · type=`choice` · choices={active, proposed, suspended, revoked, all} · default=`active`
- `--limit` — optional · type=`int` · default=`100`
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`


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

#### `empirica module`

Practice-module manifest tooling (validate; fetch/provision land in later legs)

**Subcommands:**

##### `empirica module validate`

Validate a module.yaml manifest (structural; fail-fast before install)

**Arguments:**

- `path` — **required**
  Path to the module.yaml to validate
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)


##### `empirica module fetch`

Stage a module's distribution artifacts (auth-gated pre-step before seat + provision)

**Arguments:**

- `path` — **required**
  Path to the module.yaml
- `--dry-run` — optional · flag
  Compute the fetch plan; write nothing
- `--registry` — optional
  Plugin-archive registry base URL (default: $EMPIRICA_MODULE_REGISTRY)
- `--index-url` — optional
  pip index URL for python_packages (default: $EMPIRICA_MODULE_INDEX_URL)
- `--staging-root` — optional
  Override the staging root (default: ~/.empirica/module_staging)
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)


##### `empirica module provision`

Plugin layer: place files, register automations, grant ntfy topics, check env

**Arguments:**

- `path` — **required**
  Path to the module.yaml
- `--dry-run` — optional · flag
  Compute the provision plan; perform nothing
- `--plugin-root` — optional
  Override plugin root (default: ~/.claude/plugins/local)
- `--staging-root` — optional
  Staging root holding fetched artifacts
- `--cortex-url` — optional
  Cortex base URL for ntfy ACL grants (default: credentials.yaml)
- `--org` — optional
  Org slug for ntfy grant users (e.g. empirica); topics skip without it
- `--tenant` — optional
  Tenant slug for the subscriber grant user
- `--output` — optional · type=`choice` · choices={json, text} · default=`json`
  Output format (default: json)


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

#### `empirica project-register`

Atomic single-project register: read .empirica/project.yaml at PATH, dual-write workspace.db (global_projects + entity_registry), upsert ~/.empirica/registry.yaml, POST to cortex with the local project_id. Replaces the chained 'projects-discover --register NAME && projects-bulk-register --include NAME' with one verb for the AI-as-CLI-user / copy-prompt UX (extension's Discover/Register surface).

**Arguments:**

- `path` — **required** · default=`.`
  Project root path (default: current directory)
- `--no-cortex` — optional · flag
  Stop after local writes (workspace.db + registry.yaml). Use offline-first or when cortex is down.
- `--skip-user-link` — optional · flag
  Skip the defensive user-project link after register.
- `--force-metadata-update` — optional · flag
  Carry force_metadata_update:true so cortex refreshes name/repo_url on an existing row.
- `--cortex-url` — optional
  Override cortex URL (default: ~/.empirica/credentials.yaml)
- `--api-key` — optional
  Override cortex API key (default: ~/.empirica/credentials.yaml)
- `--timeout` — optional · type=`float` · default=`10.0`
  Cortex POST timeout in seconds (default: 10)
- `--reconcile` — optional · flag
  If the local project_id has diverged from cortex's canonical id, rekey EVERY local store (sessions/artifacts DBs, workspace.db, registry.yaml, project.yaml) to the cortex id. Without this, a divergence is only warned about. Run 'empirica rebuild --qdrant' afterwards to re-point Qdrant collections.
- `--force` — optional · flag
  Bypass the open-transaction guard on --reconcile (only if you know no live session depends on the old id).
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format

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
  Filter by AI ID (e.g., empirica, cortex, autonomy)
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


#### `empirica workspace-backfill-entities`

Backfill workspace.db.entity_registry with entity_type=project rows for every existing global_projects row. Closes the gap where projects registered before the dual-write path don't appear in the Practice Model surface (extension dashboard, entity-list/-show/-walk). Idempotent.

**Arguments:**

- `--dry-run` — optional · flag
  Preview what would change without writing
- `--output` — optional · type=`choice` · choices={human, json} · default=`human`
  Output format
- `--verbose` — optional · flag
  Show detailed operation info

---
