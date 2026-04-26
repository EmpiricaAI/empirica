---
name: epistemic-transaction
description: "Use when starting complex work, planning implementation, breaking down tasks, creating specs, or when the user says 'plan this as transactions', 'plan transactions', 'break this down', 'create a spec', 'how should I approach this', 'transaction plan', or mentions needing a structured approach to multi-step work. This skill guides the full epistemic workflow from task decomposition through measured execution. Prefer this over EnterPlanMode for non-trivial tasks."
version: 1.1.0
---

# Epistemic Transaction Planning

**Turn tasks into measured work.** This skill guides you through decomposing work into
epistemic transactions — measured chunks where investigation and implementation happen
together, artifacts are recorded, and learning compounds across boundaries.

---

## Plan Transactions Mode (Interactive)

When a user asks to plan work, or when you face a non-trivial task, use this
interactive mode **instead of EnterPlanMode**. It produces structured, measurable
plans with executable commands rather than generic step lists.

### How to Run

1. **Interview** — Clarify the task using AskUserQuestion
2. **Explore** — Read the codebase areas involved (Glob, Grep, Read)
3. **Decompose** — Break into goals with `empirica goals-create`
4. **Plan** — Generate transaction plan with estimated vectors
5. **Output** — Present as structured plan with executable commands

### Step P1: Interview the Task

Use AskUserQuestion to clarify before decomposing. Key questions:

| What to Ask | Why |
|-------------|-----|
| What is the end state? | Defines completion criteria |
| What constraints exist? | Bounds the solution space |
| Are there dependencies on other work? | Orders transactions |
| What areas of the codebase are involved? | Scopes investigation |
| What's the risk tolerance? | Determines noetic depth |

Don't over-interview. 2-3 focused questions max. If the task is clear, skip to P2.

### Step P2: Explore and Log

Use read-only tools to explore. **Log everything you find:**

```bash
# What you discover
empirica finding-log --finding "Auth module uses middleware pattern at routes/auth.py" --impact 0.5

# What you don't know
empirica unknown-log --unknown "How does the session store handle concurrent access?"

# What you're assuming
empirica assumption-log --assumption "Database migrations run automatically" --confidence 0.6 --domain infrastructure
```

### Step P3: Decompose into Goals

Create goals from your exploration. Each goal = one coherent deliverable.

```bash
empirica goals-create --objective "Implement X"
empirica goals-create --objective "Add tests for X"
empirica goals-create --objective "Update docs for X"
```

### Step P4: Generate Transaction Plan

For each goal, estimate the noetic-praxic loop:

```yaml
# Transaction Plan: [Task Name]
# Generated: [timestamp]
# Goals: [count]

transactions:
  - id: 1
    goal: "Goal A description"
    goal_id: "<from goals-create>"
    noetic:
      investigate:
        - "Read module X to understand pattern"
        - "Check if Y exists"
    check_gate: "Understand X pattern and know where to make changes"
    praxic:
      implement:
        - "Write implementation"
        - "Add unit tests"
        - "Commit"
    depends_on: []

  - id: 2
    goal: "Goal B description"
    goal_id: "<from goals-create>"
    noetic:
      investigate:
        - "Review output from T1"
    check_gate: "Know integration points from T1 findings"
    praxic:
      implement:
        - "Build on T1's work"
        - "Integration test"
        - "Commit"
    depends_on: [1]
```

### Step P5: Present and Execute

Present the plan to the user for approval. Once approved:
- Start Transaction 1 with PREFLIGHT using the estimated vectors
- Follow the noetic-praxic loop per transaction
- POSTFLIGHT at the end of each transaction
- Adjust subsequent transactions based on learnings

**Key principle:** The plan is a starting estimate, not a contract.
Vectors will shift as you learn. That's the point — measuring the delta
between estimated and actual is what builds calibration.

---

## Reference Guide

The sections below are the full reference for epistemic transactions.
Use them during execution, not just planning.

---

## When to Use This Skill

- Starting a complex task (3+ files, multiple concerns)
- User provides a spec, ticket, or feature description
- You need to plan before acting
- Work will span multiple transactions or sessions
- You want to ensure nothing falls through the cracks

---

## Step 1: Understand the Task

Before creating any goals or transactions, assess what you're working with.

**Read the spec/task/request.** Then ask yourself:

| Question | If Yes | If No |
|----------|--------|-------|
| Do I understand what's being asked? | Move to Step 2 | Log unknowns, investigate |
| Do I know the codebase areas involved? | Move to Step 2 | Read code, log findings |
| Are there architectural decisions needed? | Log assumptions, investigate options | Move to Step 2 |
| Is this a single coherent change? | Single transaction, skip to Step 3 | Decompose into goals |

```bash
# Log what you don't know yet
empirica unknown-log --unknown "How does the auth middleware chain work?"
empirica unknown-log --unknown "What's the expected behavior when X?"

# Log assumptions you're making
empirica assumption-log --assumption "The API is RESTful" --confidence 0.7 --domain architecture
```

---

## Step 2: Decompose into Goals

Each goal = one coherent piece of work. Goals are structural (what needs doing),
transactions are measurement windows (how you track doing it).

**Decomposition heuristics:**

| Signal | Goal Boundary |
|--------|---------------|
| Different files/modules | Separate goals |
| Different concerns (UI vs API vs DB) | Separate goals |
| Dependency chain (B needs A) | Separate goals, ordered |
| Single atomic change | One goal |
| Tests for implementation | Same goal as implementation |

```bash
# Create goals from decomposition
empirica goals-create --objective "Implement authentication middleware"
empirica goals-create --objective "Add user session management"
empirica goals-create --objective "Write integration tests for auth flow"
```

**Goal sizing guidance:**

| Size | Description | Transactions |
|------|-------------|--------------|
| Small | Bug fix, config change, single function | 1 |
| Medium | Feature with 2-3 files, schema + UI | 1-2 |
| Large | Cross-cutting concern, multiple modules | 2-3 |
| Too large | "Redesign the whole system" | Split further |

---

## Step 3: Plan Transaction Sequence

Each transaction picks up one goal (or a coherent subset) and runs the full
noetic-praxic loop. Plan the sequence based on dependencies and information flow.

### Transaction Template

```
Transaction N: [Goal Name]
  PREFLIGHT: Declare scope, assess baseline
    Noetic: [what to investigate]
    - Read relevant code
    - Check for existing patterns
    - Log findings, unknowns, dead-ends
  CHECK: Gate readiness
    - know >= threshold (holistic)
    - Key unknowns resolved
  Praxic: [what to implement]
    - Write code
    - Run tests
    - Commit
  POSTFLIGHT: Measure learning
    Artifacts to resolve:
    - Close goal if complete
    - Resolve unknowns answered during work
    - Convert verified assumptions to decisions/findings
```

### Example: 3-Transaction Plan

```
Session Start
  Create goals: A (auth middleware), B (session mgmt), C (integration tests)

Transaction 1: Goal A — Auth Middleware
  PREFLIGHT: scope = auth middleware, know ~0.5, uncertainty ~0.4
  Noetic:
    - Read existing middleware chain
    - Check how routes are protected
    - Log finding: "Express middleware uses next() pattern"
    - Log unknown: "How are roles differentiated?"
    - Resolve unknown → finding: "Roles in JWT claims"
  CHECK: know ~0.8, uncertainty ~0.15 → proceed
  Praxic:
    - Implement auth middleware
    - Add role-based guards
    - Write unit tests
    - Commit: "feat(auth): add JWT middleware with role guards"
  POSTFLIGHT: know 0.9, completion 1.0
    Close Goal A, resolve unknowns

Transaction 2: Goal B — Session Management (informed by T1's findings)
  PREFLIGHT: know ~0.7 (JWT patterns from T1), uncertainty ~0.25
  Noetic:
    - Read session store options
    - Check token refresh patterns
    - Log assumption: "Redis available for session store" --confidence 0.6
  CHECK: → proceed
  Praxic:
    - Implement session creation/refresh/revoke
    - Decision: "Use httpOnly cookies for refresh tokens"
    - Commit: "feat(auth): add session management with token refresh"
  POSTFLIGHT: Close Goal B

Transaction 3: Goal C — Integration Tests
  PREFLIGHT: know ~0.85 (deep understanding from T1+T2)
  Noetic: Quick review of test patterns
  CHECK: → proceed
  Praxic:
    - Write integration tests covering auth + sessions
    - Commit: "test(auth): add integration tests for full auth flow"
  POSTFLIGHT: Close Goal C, session complete
```

---

## Step 4: Execute Each Transaction

Within each transaction, follow the noetic-praxic loop:

### 4a. PREFLIGHT — Open the Measurement Window

```bash
empirica preflight-submit - << 'EOF'
{
  "session_id": "<ID>",
  "task_context": "Transaction 1: Implement auth middleware. Scope: middleware chain, role guards, unit tests.",
  "work_type": "code",
  "work_context": "iteration",
  "domain": "default",
  "criticality": "medium",
  "vectors": {
    "know": 0.5, "uncertainty": 0.4,
    "context": 0.6, "clarity": 0.7,
    "coherence": 0.6, "signal": 0.5,
    "density": 0.4, "state": 0.5,
    "change": 0.1, "completion": 0.0,
    "impact": 0.7, "do": 0.7,
    "engagement": 0.9
  },
  "reasoning": "Starting auth middleware. Read the route definitions but haven't explored the middleware chain yet. High engagement, moderate knowledge."
}
EOF
```

**Context fields (optional, improve grounded calibration):**
- `work_type`: `code|infra|research|release|debug|config|docs|data|comms|design|audit|remote-ops` — scales evidence weights by source relevance. Use `remote-ops` for work the local Sentinel doesn't observe (SSH, customer machines, remote config); the POSTFLIGHT will return `calibration_status=ungrounded_remote_ops` and self-assessment will stand unchallenged.
- `work_context`: `greenfield|iteration|investigation|refactor` — adjusts normalization baselines for project maturity

**PREFLIGHT declares scope.** If scope creeps during work, that's a signal to
POSTFLIGHT and start a new transaction.

### 4b. Noetic Phase — Investigate

**Prefer `noetic_batch` for multi-step investigation.** When a transaction's
investigation involves 3+ reads/greps/globs/searches, batch them in one call
instead of round-tripping per-step. The Sentinel sees one noetic intent
(zero per-call gating overhead), the TUI stays clean, and you get a merged
structured response.

```bash
empirica noetic-batch - << 'EOF'
{
  "intent": "understand auth middleware chain",
  "reads": [{"path": "src/auth.py"}, {"path": "src/middleware.py"}],
  "greps": [
    {"pattern": "decorator", "glob": "src/**/*.py", "context": 2},
    {"pattern": "Bearer", "glob": "src/**/*.py"}
  ],
  "globs": ["src/**/*auth*", "tests/**/*auth*"],
  "investigate": [{"query": "auth middleware patterns", "scope": "project"}]
}
EOF
```

(Or via MCP: `mcp__empirica__noetic_batch` with the same JSON payload.)

Fall back to individual Read/Grep/Glob for one-shot lookups after a batch
surfaces something you need to drill into.

Read code. Search patterns. Build understanding. **Log as you go:**

```bash
# Every discovery → finding
empirica finding-log --finding "Middleware chain uses app.use() with path prefix" --impact 0.5

# Every question → unknown
empirica unknown-log --unknown "Where are role definitions stored?"

# Every failed approach → dead-end
empirica deadend-log --approach "Tried passport.js" --why-failed "Too heavy for JWT-only auth"

# Every unverified belief → assumption
empirica assumption-log --assumption "All routes need auth except /health" --confidence 0.8 --domain routing
```

### 4c. CHECK — Gate the Transition

```bash
empirica check-submit - << 'EOF'
{
  "session_id": "<ID>",
  "vectors": {
    "know": 0.82, "uncertainty": 0.15,
    "context": 0.85, "clarity": 0.88
  },
  "reasoning": "Investigated middleware chain, understand JWT flow, know where roles live. Ready to implement."
}
EOF
```

- `proceed` → Start writing code (praxic phase, **same transaction**)
- `investigate` → Keep exploring (noetic phase, **same transaction**)

**CHECK does NOT end the transaction.** It gates the transition.

### 4d. Praxic Phase — Implement

Write code. Run tests. Commit. **Still log artifacts:**

```bash
# Discoveries during implementation
empirica finding-log --finding "Express 5 changed middleware signature to async" --impact 0.6

# Decisions made while coding
empirica decision-log --choice "Use middleware factory pattern" \
  --rationale "Enables per-route config without duplication" \
  --reversibility exploratory
```

### 4e. POSTFLIGHT — Close the Measurement Window

**BEFORE running POSTFLIGHT, always:**
1. Log all remaining epistemic artifacts (findings, unknowns, decisions, dead-ends, mistakes)
2. Resolve any unknowns that were answered during the transaction
3. Complete any goals that were finished
4. Ask the user: "Any artifacts to log before I close the transaction?"

POSTFLIGHT without artifact sweep = lost data. The measurement window closes
and unlogged work becomes invisible to calibration. Always log first, then close.

```bash
empirica postflight-submit - << 'EOF'
{
  "session_id": "<ID>",
  "vectors": {
    "know": 0.92, "uncertainty": 0.08,
    "context": 0.90, "clarity": 0.95,
    "completion": 1.0, "do": 0.90
  },
  "reasoning": "Auth middleware implemented with role guards. Unit tests passing."
}
EOF
```

### 4f. Compliance Loop — Domain Checklist (automatic)

After POSTFLIGHT, the compliance loop runs automatically when `domain` and
`criticality` were set in PREFLIGHT. It checks the domain's required services:

```
POSTFLIGHT response includes:
  "compliance": {
    "status": "complete" | "iteration_needed" | "max_iterations_exceeded",
    "checks_run": 3,
    "checks_passed": 2,
    "checks_failed": 1,
    "check_results": [
      {"check_id": "lint", "passed": true, "summary": "lint clean (scoped to 4 files)"},
      {"check_id": "complexity", "passed": true, "summary": "complexity A (avg 2.1)"},
      {"check_id": "tests", "deferred": true, "tier": "goal_completion"}
    ],
    "next_transaction": {  // only if iteration_needed
      "intent": "address failures: tests",
      "inherited_domain": "default",
      "inherited_criticality": "medium"
    }
  }
```

**Tiered execution:** Checks run at different points to manage resource cost:
- **always** (every POSTFLIGHT): lint, complexity, git_metrics — ~5s, ~80MB
- **goal_completion** (at goal close): tests — runs full pytest
- **release** (pre-release only): dep_audit — pip-audit for CVEs

**Cached results:** Same changed files = same content hash = cached result.
The AI sees `"cached": true` and knows it wasn't a fresh run.

**Brier scoring:** If you stated check outcome beliefs in PREFLIGHT
(`predicted_check_outcomes`), the compliance response includes a `check_brier`
block measuring belief calibration. Only freshly-run checks count —
deferred and cached are excluded.

**Three-vector model:** After seeing compliance results, you can submit
`grounded_vectors` + `grounded_rationale` in POSTFLIGHT to record your
reasoned synthesis. Services inform; you synthesize.

---

## Step 5: Between Transactions — Artifact Review

At the start of each new transaction, review open artifacts. Resolve those
that are completed or no longer pertinent. Where uncertainty is high about
whether an artifact is still relevant, surface it collaboratively:

```bash
# 1. Review what's open
empirica goals-list
empirica unknown-list

# 2. Goals no longer needed → close with reason
empirica goals-complete --goal-id <ID> --reason "Superseded by new approach"

# 3. Verify/falsify assumptions
# Confirmed assumption → finding
empirica finding-log --finding "Confirmed: all routes except /health need auth" --impact 0.3
# Falsified assumption → decision about what to do instead
empirica decision-log --choice "Use Redis for sessions" --rationale "Confirmed Redis available via docker-compose"
```

**Why this matters:** Unresolved artifacts accumulate as noise. Each transaction's
PREFLIGHT retrieves your prior artifacts via pattern matching — clean signal means
better context for the next transaction.

---

## Anti-Patterns

### The Split-Brain (most common mistake)

```
WRONG:
  PREFLIGHT → [noetic: investigate] → POSTFLIGHT    ← closes before acting!
  PREFLIGHT → [praxic: implement] → POSTFLIGHT      ← acts without baseline!
```

Investigation and implementation belong in the **same transaction**. The
PREFLIGHT-to-POSTFLIGHT delta should capture the full journey from "I don't
know" to "I investigated, understood, and implemented."

### The Mega-Transaction

```
WRONG:
  PREFLIGHT → [5 goals, 15 files, 3 domains] → POSTFLIGHT
```

Too much in one measurement window. The delta becomes meaningless noise.
Scope to what you can hold coherently — 1-2 goals per transaction.

### The Rush-Through

```
WRONG:
  PREFLIGHT → CHECK → POSTFLIGHT (no actual work between them)
```

Transactions need real noetic/praxic work. The system detects rushed
transactions via minimum duration checks (30s noetic with evidence).

### The Artifact Hoarder

```
WRONG:
  Transaction 1: Log 5 unknowns
  Transaction 2: Log 5 more unknowns (never resolve the first 5)
  Transaction 3: Log 5 more unknowns (pile grows...)
```

Resolve artifacts between transactions. Unknowns become findings. Assumptions
become decisions. Unresolved artifacts accumulate as noise — resolve what's
answered, close what's no longer pertinent.

---

## Transaction Discipline Rules

These rules encode the working discipline that makes transactions meaningful.
They are behavioral commitments, not code enforcement — internalize them.

### Rule 1: Goal-per-Transaction

Every transaction should reference an empirica goal. If the goal has distinct
steps, create subtasks to track them:

```bash
# At PREFLIGHT, link to a goal
empirica goals-create --objective "Implement X"  # if not already created
empirica goals-add-subtask --goal-id <ID> --description "Read and understand module Y"
empirica goals-add-subtask --goal-id <ID> --description "Write implementation"
empirica goals-add-subtask --goal-id <ID> --description "Add tests"

# For goals you want to log but not start yet:
empirica goals-create --objective "Future: refactor Y" --status planned
```

**Why:** Goalless transactions produce ungrounded completion vectors. The
grounded calibration has nothing to measure your completion claims against.
`planned` goals are visible in `goals-list` but excluded from measurement
until moved to `in_progress`.

### Rule 2: Commit-per-Subtask

Commit after each completed subtask or coherent work unit. Don't batch commits
to the end of the transaction. Each commit should be meaningful and atomic.

```
WRONG: noetic → praxic → [edit 5 files] → one big commit → POSTFLIGHT
RIGHT: noetic → praxic → [edit files A,B] → commit → [edit C] → commit → POSTFLIGHT
```

**Why:** Uncommitted work is invisible to grounded calibration. The `change`,
`state`, and `do` vectors ground against git evidence. Late commits mean
the POSTFLIGHT snapshot misses the learning trajectory.

### Rule 3: Artifact Breadth

Log the full breadth of epistemic artifacts — not just findings. Every
transaction should capture what was relevant:

| Happened | Log It |
|----------|--------|
| Made a choice between options | `decision-log` |
| Assumed something unverified | `assumption-log` |
| Tried something that didn't work | `deadend-log` |
| Made an error | `mistake-log` |
| Discovered something | `finding-log` |
| Hit an open question | `unknown-log` |

**Why:** Single-type artifact logging (only findings) leaves calibration
gaps ungrounded. The retrospective breadth_note will flag this, but by then
the measurement window is closing.

### Rule 4: Close Artifacts Before POSTFLIGHT

Complete goals and resolve unknowns BEFORE submitting POSTFLIGHT:

```bash
# Close what's done
empirica goals-complete --goal-id <ID> --reason "Implemented and tested"
empirica unknown-resolve --unknown-id <ID> --resolved-by "Found in codebase"

# THEN close the measurement window
empirica postflight-submit -
```

**Why:** The measurement window closes at POSTFLIGHT. Goal completion and
unknown resolution feed grounded calibration's completion and know vectors.
If you POSTFLIGHT first, the evidence is invisible to calibration.

### Rule 5: Subtask-Task Visibility (When Using Claude Code Tasks)

For larger transactions, map empirica subtasks to Claude Code tasks so the
user sees progress. Create tasks at PREFLIGHT, update as you complete:

```
empirica goals-add-subtask → Claude Code TaskCreate (mirror)
empirica goals-complete-subtask → Claude Code TaskUpdate (mirror)
```

This is advisory — use your judgment on when the user benefits from
visible task tracking vs when it's overhead.

---

## Quick Reference: Commands by Phase

| Phase | Commands |
|-------|----------|
| **Planning** | `goals-create`, `goals-add-subtask`, `unknown-log`, `assumption-log` |
| **PREFLIGHT** | `preflight-submit` (opens transaction) |
| **Noetic** | `noetic-batch` (3+ ops in one call — preferred), `source-add`, `finding-log`, `unknown-log`, `deadend-log`, `assumption-log` |
| **CHECK** | `check-submit` (gates noetic → praxic) |
| **Praxic** | `finding-log`, `decision-log`, `goals-complete-subtask` |
| **Before POSTFLIGHT** | `goals-complete`, `unknown-resolve`, or batch: `resolve-artifacts` |
| **POSTFLIGHT** | `postflight-submit` (closes transaction + triggers grounded verification) |
| **Between** | `goals-list`, `resolve-artifacts` (batch), `delete-artifacts` (cleanup) |
| **Batch** | `log-artifacts` (connected graph), `resolve-artifacts`, `delete-artifacts` |

---

## Spec-to-Transactions Cheatsheet

Given a spec or feature description:

1. **Read it fully** — don't start decomposing mid-read
2. **Identify nouns** — these are your domains/modules (potential goal boundaries)
3. **Identify verbs** — these are your actions (potential subtasks)
4. **Identify dependencies** — A before B? Separate transactions, ordered
5. **Identify unknowns** — what the spec doesn't say (log immediately)
6. **Identify assumptions** — what you're inferring (log with confidence)
7. **Group into goals** — by domain coherence
8. **Order into transactions** — by dependency chain + information flow
9. **Execute** — one transaction at a time, full noetic-praxic loop each

---

## Earned Autonomy

Vectors are beliefs about your epistemic state. Deterministic services provide
observations that inform those beliefs. The divergence between your beliefs and
observations tells you where work discipline needs attention — not where to
adjust numbers.

Each transaction with good discipline (artifact breadth, commit cadence, goal
closure before POSTFLIGHT) builds a behavioral track record that the Sentinel
uses to adapt thresholds → better discipline earns more autonomy.

**Believe what you observe. Log what you learn. Let discipline drive improvement.**
