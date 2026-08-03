---
name: dispatch-agent
description: Dispatch subagents with inherited epistemic context from Cortex. Use when spawning Agent tool calls for tasks that would benefit from inherited findings, dead-ends, and anti-patterns. Triggers on 'dispatch agent', 'spawn agent with context', 'epistemic agent', or before any Agent tool call for non-trivial tasks.
---

# Epistemic Agent Dispatch

**Spawn subagents that inherit relevant knowledge from Cortex.**

Without this skill, subagents arrive blank — they repeat mistakes, miss known
dead-ends, and lack domain context. With it, they inherit findings, dead-ends,
anti-patterns, and governance rules from the parent's epistemic state.

---

## How to Use

Before spawning an Agent tool call, run this skill to enrich the prompt.

```
/dispatch-agent "Refactor handle_foo to reduce complexity"
```

Or invoke automatically when you're about to dispatch an agent for non-trivial work.

---

## Step 1: Query Cortex for Inherited Context

Use the task description to query Cortex for relevant epistemic artifacts:

```
mcp__cortex__investigate({
  "query": "<task description>",
  "limit": 10
})
```

If Cortex is unavailable, fall back to local Empirica CLI:

```bash
empirica project-search --task "<task description>" --global --output json
```

## Step 2: Categorize Results

From the Cortex/search results, extract and categorize:

| Category | What to Include | Why |
|----------|----------------|-----|
| **Dead-ends** | Failed approaches relevant to this task | Prevent repetition |
| **Findings** | Discoveries about the domain/files involved | Build on prior knowledge |
| **Decisions** | Architectural choices affecting this area | Maintain consistency |
| **Anti-patterns** | Mistakes made in similar work | Avoid known pitfalls |
| **Governance** | Standing rules for this type of work | Enforce standards |

### Filtering Rules

- **Dead-ends**: Include ALL that match (similarity > 0.5). These are the highest-value inheritance — preventing a subagent from wasting time on known failures.
- **Findings**: Include top 5 by relevance. Too many overwhelm the context.
- **Decisions**: Include only those affecting the specific files/domain.
- **Anti-patterns**: Extract from dead-ends and mistakes. Format as "DO NOT: ..."

## Step 3: Build the Dispatch Schema

Construct the enriched agent prompt with this structure:

```markdown
## Inherited Epistemic Context

Your parent agent has relevant knowledge for this task. Study this before starting.

### Dead-Ends (DO NOT repeat these)
{{for each dead-end}}
- **Approach:** {{approach}}
  **Why it failed:** {{why_failed}}
{{end}}

### Relevant Findings
{{for each finding}}
- {{finding}} (impact: {{impact}})
{{end}}

### Architectural Decisions in Effect
{{for each decision}}
- **Choice:** {{choice}}
  **Rationale:** {{rationale}}
{{end}}

### Anti-Patterns (AVOID these)
{{for each anti-pattern}}
- DO NOT: {{pattern}}
{{end}}

### Governance
- Run tests after EACH file modification (not after batching)
- Verify extracted helpers receive all needed variables as parameters
- Commit only after tests pass

---

## Your Task

{{original task description}}
```

## Step 4: Dispatch with the Agent Tool

Use the Agent tool with the enriched prompt:

```
Agent({
  "description": "{{short 3-5 word description}}",
  "prompt": "{{enriched prompt from Step 3}}",
  "subagent_type": "general-purpose",
  "run_in_background": true  // or false if you need results immediately
})
```

## Step 5: Review Before Launch

Before executing the Agent tool call, present the dispatch payload to the user:

> **Dispatching agent:** {{description}}
> **Inherited context:** {{N}} dead-ends, {{N}} findings, {{N}} decisions
> **Governance:** {{key rules}}
>
> Proceed?

On high-autonomy tasks, skip the review. On sensitive tasks, wait for confirmation.

---

## Example: Code Refactoring Dispatch

**Task:** "Refactor handle_session_commands to reduce C901 complexity"

**Cortex query returns:**
- Dead-end: "CLI handler Tier C agent created parameterless helpers — scope bugs in 20+ files"
- Finding: "Pattern: extract sequential stages into helpers, pass all variables as parameters"
- Decision: "Helpers in SAME file, defined BEFORE the function they serve"

**Enriched prompt:**

```markdown
## Inherited Epistemic Context

### Dead-Ends (DO NOT repeat these)
- **Approach:** Batch refactoring 35 functions across 12 files with automated extraction
  **Why it failed:** Created parameterless helpers referencing outer-scope variables. 20+ files broken.

### Relevant Findings
- Extract sequential stages into helper functions, main becomes orchestrator (impact: 0.8)
- Each helper must receive ALL referenced variables as parameters (impact: 0.7)

### Anti-Patterns (AVOID these)
- DO NOT extract helpers without passing variables they reference as parameters
- DO NOT batch more than 3-4 files per agent — verify each with tests
- DO NOT create recursive helper chains (_helper_helper_helper)

### Governance
- Run `python3 -m pytest tests/ -x -q --tb=short` after EACH file
- Target CC < 15 for main functions
- No behavior changes — pure structural refactoring

---

## Your Task

Refactor all C901 violations in empirica/cli/command_handlers/session_commands.py.
For each function over CC 15, extract the biggest conditional block into a helper.
Helpers go in the same file, defined before the function they serve.
```

---

## Cortex Unavailable Fallback

If Cortex MCP is not connected, use local Empirica search:

```bash
# Semantic search across dead-ends, findings and the rest of the graph.
# --global widens past this project into the shared learnings pool.
empirica project-search --task "<description>" --global --output json 2>/dev/null

# Narrow to one artifact kind when the query is broad
empirica project-search --task "<description>" --type assumptions --output json 2>/dev/null
```

**There is no `--list` on the `*-log` verbs.** They WRITE artifacts; retrieval
is semantic and goes through `project-search` (or `investigate`). This block
used to print `deadend-log --list` and `finding-log --list`, neither of which
exists — an AI following it got a usage error with no way to tell whether the
skill was wrong or its own invocation was.

Format the results the same way as the Cortex path.

---

## Why This Matters

Without inherited context, subagents:
- Repeat known dead-ends (wasting time and tokens)
- Violate established patterns (creating inconsistency)
- Miss anti-patterns (introducing bugs the parent already learned to avoid)
- Lack governance (no test discipline, no verification)

With inherited context, subagents:
- Skip known failures immediately
- Follow established patterns
- Avoid known pitfalls
- Verify their work before claiming completion

The quality difference is measurable — the same C901 refactoring task succeeded
cleanly with context-aware agents (core/data batch) and failed destructively
without it (CLI handler batch). Same task, same pattern, different outcome.
The variable was inherited knowledge.
