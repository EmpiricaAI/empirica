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

### Step P3: Decompose into Goals (and tasks)

A **goal** is one coherent deliverable. **Tasks** are the AI-tracked
units of work *inside* a goal — distinct steps that each end in a
commit / test result / verifiable evidence.

The structural shape (Linear / GitHub / Jira convention):
- `objective` is title-shaped (≤256 chars). Short, actionable.
- `description` is the rich body (≤8000 chars, optional). Context,
  motivation, success criteria, links.

```bash
# Title-only goal — fine for small scope
empirica goals-create --objective "Implement auth middleware"

# Goal with rich description — when the why matters or success
# criteria need to be explicit
empirica goals-create \
  --objective "Implement auth middleware" \
  --description "Routes need JWT-based auth. Out-of-scope: session
storage (separate goal). Success: all routes except /health
require valid JWT, role-based guards work, unit tests pass.
References: RFC 7519, prior decision deead8f2 on bcrypt."

# Decompose into tasks — one per distinct unit of AI work
empirica goals-add-task --goal-id <ID> --description "Read existing middleware chain"
empirica goals-add-task --goal-id <ID> --description "Implement JWT validation middleware"
empirica goals-add-task --goal-id <ID> --description "Add role-based guards"
empirica goals-add-task --goal-id <ID> --description "Write unit tests + commit"
```

**When to decompose into tasks (vs single-shot goal):**
- Multi-file work → one task per file or logical unit
- Investigation followed by implementation → one task per phase
- Anything that will produce ≥2 commits → tasks make per-commit
  evidence linkage explicit
- Anything you'd otherwise track in a TodoWrite — log as tasks
  instead so the work is grounded against calibration

**As you complete each task, close it with evidence:**

```bash
empirica goals-complete-task \
  --task-id <ID> \
  --evidence "Commit abc1234: JWT validation middleware + unit tests passing"
```

The `--evidence` field is what makes tasks *grounded* AI work
rather than self-reported progress. Tie it to a commit SHA, test
result, or file path — something deterministic that grounded
calibration can verify.

**Planned vs in-progress goals:**

```bash
# Logged but not yet started (collaborative planning, queue work)
empirica goals-create --objective "Future: refactor X" --status planned

# Active immediately (default — start work now)
empirica goals-create --objective "Implement X"
```

**Cross-project goals:** add `--project-id <name-or-uuid>` to log against
a different project's epistemic state without switching session context.

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

**Use `noetic_batch` ONLY when batching ≥3 investigation operations.**
When a transaction's investigation needs reads + greps + globs + investigate
together, bundle them in one call — the value is one merged result for your
conversation and fewer round-trips, not a gating shortcut. Individual
Read/Grep/Glob/investigate calls are noetic in any phase and don't need
batching. **NOT a Sentinel bypass** — calling `noetic_batch` once for a
single read is misuse (the executor will surface a `warning` field in
the response).

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

#### Rich markdown bodies — `--description` for nuance

Every `*-log` command (finding, unknown, deadend, assumption, decision,
mistake) accepts an optional `--description` flag carrying a **markdown
body**. The extension and skill surfaces render this as prettified
markdown — use sections, lists, code blocks, tables, links for nuance
that doesn't fit the short title field.

Three shape examples (no rigid template — pick what the artifact
warrants):

**1. Prose body** — short context behind a finding:
```bash
empirica finding-log \
  --finding "Express 5 changed middleware signature to async" \
  --description "Caught during the auth middleware port: synchronous \`next()\` callbacks now resolve as awaited promises, so error handlers must \`return next(err)\` instead of fire-and-forget. The legacy middleware in routes/auth.js silently swallowed errors because the old signature didn't propagate them. Documented in [Express 5 migration guide](https://expressjs.com/en/guide/migrating-5.html)." \
  --impact 0.7
```

**2. Sectioned body** — decision with trade-offs:
```bash
empirica decision-log \
  --choice "Use Redis for session store" \
  --rationale "Available via docker-compose, supports TTL primitives, matches our existing infra" \
  --description "## Why Redis over alternatives

| Option | Verdict |
|---|---|
| In-memory (Map) | ❌ scales to 1 process only |
| Postgres | ❌ heavy for ephemeral key/value |
| Redis | ✅ matches existing infra |

## What would reverse this
- Single-region deployment requirement (Redis Sentinel adds ops)
- Sub-millisecond write needs (consider DragonflyDB)" \
  --reversibility committal
```

**3. Code-block body** — dead-end with reproducible signal:
```bash
empirica deadend-log \
  --approach "Tried passport.js for auth middleware" \
  --why-failed "Too heavy for JWT-only auth" \
  --description "## Signal: bundle bloat

\`\`\`
passport@0.7.0 + passport-jwt@4.0.1 + dependencies = +180KB
in-house JWT verifier = ~30 lines, +2KB
\`\`\`

Passport's value is its strategy ecosystem (OAuth, SAML, etc.) — we're
JWT-only so the abstraction was pure overhead. Reverted to a minimal
\`verify-jwt.js\` middleware."
```

Skip the body entirely when the title alone tells the full story.
Over-describing trivial artifacts is its own anti-pattern — let the
nuance threshold be "would someone reading this in 3 months understand
without the body?"

#### Sources — log when an artifact's origin matters

An **epistemic source** is the external thing your finding/decision came
from: a doc, a URL, a paper, a transcript, a customer call, a GitHub
issue. Sources are first-class artifacts (`source-add`) that other
artifacts link to via the `sourced_from` relation in batch operations.

**When to add a source:**

- A finding came from reading a non-code reference (RFC, paper, blog,
  spec, design doc) — log the source so future searches surface the
  origin, not just the conclusion
- A decision rests on an external authority (compliance doc, vendor
  contract, security advisory) — the audit trail needs the link
- A dead-end was learned the hard way from a community thread or
  postmortem — others can find the warning back to its origin
- You're working in **Claude Desktop or any non-CLI surface** where most
  artifacts originate from web pages, conversations, attachments, or
  manually-pasted text rather than code reads. In CLI mode, `git blame`
  + `finding_refs` auto-extraction often covers source provenance for
  free; in Desktop mode, explicit `source-add` is the only way to
  preserve where ideas came from.

**How:**

```bash
# Standalone: log a source first
empirica source-add --title "RFC 7519 — JSON Web Tokens" \
  --url "https://datatracker.ietf.org/doc/html/rfc7519" \
  --noetic --confidence 0.95
# Returns: source_id (UUID)

# Then link findings/decisions to it via batch graph:
empirica log-artifacts - << 'EOF'
{
  "nodes": [
    {"ref": "f1", "type": "finding",
     "data": {"finding": "JWTs are signed but not encrypted by default",
              "impact": 0.7}},
    {"ref": "d1", "type": "decision",
     "data": {"choice": "Use JWE for sensitive payloads",
              "rationale": "Default JWS leaks contents at rest"}}
  ],
  "edges": [
    {"from": "f1", "to": "<source_id_uuid>", "relation": "sourced_from"},
    {"from": "d1", "to": "f1", "relation": "evidence"}
  ]
}
EOF
```

**Skip when:** the source is the project's own code at the current HEAD —
that provenance is already in git. Sources earn their keep when the
origin is *outside* what `git blame` can reach.

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
steps, create tasks to track them:

```bash
# At PREFLIGHT, link to a goal
empirica goals-create --objective "Implement X"  # if not already created
empirica goals-add-task --goal-id <ID> --description "Read and understand module Y"
empirica goals-add-task --goal-id <ID> --description "Write implementation"
empirica goals-add-task --goal-id <ID> --description "Add tests"

# For goals you want to log but not start yet:
empirica goals-create --objective "Future: refactor Y" --status planned
```

**Why:** Goalless transactions produce ungrounded completion vectors. The
grounded calibration has nothing to measure your completion claims against.
`planned` goals are visible in `goals-list` but excluded from measurement
until moved to `in_progress`.

### Rule 2: Commit-per-Task

Commit after each completed task or coherent work unit. Don't batch commits
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

### Rule 5: Mesh Discipline (when peer practices are involved)

When the transaction crosses a practice boundary — you received a peer's
collab/proposal, or your work will land in someone else's domain — the
mesh-discipline rules apply in addition to the within-practice rules above:

| Trigger | Action | Why |
|---------|--------|-----|
| Mid-transaction peer collab arrives | Log `goals-create --objective "Process inbox/<status>: <proposal_id>"`, finish current chunk, reply substantively at next break | Silent accept-and-forget is the drop-thread anti-pattern. The goal stub is the cue you saw it. |
| You finished work a peer asked of you | `empirica mailbox reply --parent-id <proposal_id> --commit-sha <sha> ...` (atomic propose+complete) before POSTFLIGHT | Without the handshake, source AI's outbox stays visibly stalled even though the work landed. Ack IS part of the work. |
| You're uncertain and a peer practice's domain genuinely owns the answer | Send a collab brief (noetic — auto-accepted, ungated) instead of guessing | Asking is cheap; shipping on a bad assumption and being corrected at review is expensive. |
| You reached a grounded, actionable conclusion that crosses practice boundaries | Emit a typed propose (code_change_request / architecture_decision / etc. — ECO-gated) | Sitting on convergent insight because "they'll figure it out" is the inverse free-ride. |
| You registered a canonical reference (RFC, spec, design doc, customer call) | `source-add --visibility shared` (or `public`) — not the `local` default | `local` sources are invisible to `empirica sources-map --global`; peers can't reference what they can't see. |
| Your finding/decision rests on a peer's source or another practice's prior work | `--source <uuid>` or `sourced_from` edge in `log-artifacts` | The citation network is what makes the mesh self-correcting — useful peers earn weight; abandoned ideas fade. |

**Why this is structural, not moral:** Same logic as the artifact-breadth
rule. Gaming the mesh (silent free-ride, no acks, hoarded sources) doesn't
hurt anyone other than your own practice's discoverability + trust
trajectory. Your peers route attention based on which practices return
calls. There is no opponent to deceive.

Full framing + examples: `/empirica-constitution` §V. Send-side mechanism:
`/cortex-mailbox-send` (collab vs ECO-gated flavors, completion handshake,
recovery on mis-routing).

### Rule 5b: SER-Aware Transactions (sustained multi-practice work)

When a transaction is part of work that spans ≥2 practices and outlives
this session, the coordination lives in a **Shared Epistemic Record
(SER)** — the cortex-resident shared-state object — not in any one
practice's goals. Your local transaction discipline (PREFLIGHT → CHECK →
POSTFLIGHT) is unchanged; what's added is keeping the SER in sync.

| Trigger | Action | Why |
|---------|--------|-----|
| A collab thread you're in has run ≥3 rounds across the same practices, or work gains named participants that outlive the session | Graduate it: `cortex_propose` with `payload.action='create_ser'` + `ser_spec` | A standing coordination surface needs a home; N collab replies leave the shared state homeless |
| You opened a transaction to do *your leg* of SER-coordinated work | Link it: reference the `ser_id` in your goal description; optionally carry `goal_refs:[{practice_id, goal_id}]` in the `ser_spec` | Makes your per-practice goal walkable from the shared record |
| You start the work the SER tracks | Propose `transition_ser` → `in_progress` **before** the praxic phase | The SER state should lead reality, so peers see "in progress" not "still open" |
| You're a `required` participant and a transition landed | Propose `ser_ack` (stamps `last_ack_at`) | Silences the escalation re-ping; it's the SER analog of the completion handshake — skipping it leaves you looking idle |
| Your leg is done and the shared outcome is reached | Propose `transition_ser` → `closed` **before POSTFLIGHT**, then close your local goal | Close the shared record in the same window you close your goal — a closed goal under an `open` SER reads as abandoned coordination |

**The gating caveat that changes your timing:** every SER mutation
(`create_ser`, `transition_ser`, `ser_ack`) is **ECO-gated** — it lands
`eco_review` and waits for a human Accept, *even at
`action_category=REFLEX`*. So an SER transition is **not** a synchronous
step you can assume completes inside your transaction. Propose it, then
either (a) continue your local praxic work in parallel (the transition
is bookkeeping, not a blocker), or (b) if a peer is genuinely blocked on
the state change, POSTFLIGHT and pick up when the Accept lands via your
listener. Do **not** busy-wait on a gated SER mutation mid-transaction.

**Why gated:** creating or moving an SER commits *other practices'*
workloads — the human authorizes that cross-practice mapping. You can
propose coordination freely; binding peers to it is gated. Full rationale
+ validated call shapes (`ser_spec`, `transition_spec.new_state`,
`ack_spec`): `/empirica-constitution` §VI. Send-side mechanics
(graduation discipline, cross-org scope, AFK-ambassador):
`/cortex-mailbox-send` Flavor 3.

### Rule 6: Mirror Empirica Tasks → Claude Code Tasks (Visibility)

Empirica tasks and Claude Code Tasks share the same name now (this is
deliberate — they're the same shape of work, tracked in two surfaces).
For larger transactions, mirror empirica tasks to Claude Code Tasks so
the user sees progress. Create at PREFLIGHT, update as you complete:

```
empirica goals-add-task → Claude Code TaskCreate (mirror)
empirica goals-complete-task → Claude Code TaskUpdate (mirror)
```

This is advisory — use your judgment on when the user benefits from
visible task tracking vs when it's overhead.

---

## Quick Reference: Commands by Phase

| Phase | Commands |
|-------|----------|
| **Planning** | `goals-create`, `goals-add-task`, `unknown-log`, `assumption-log` |
| **PREFLIGHT** | `preflight-submit` (opens transaction) |
| **Noetic** | `noetic-batch` (3+ ops in one call — preferred), `source-add`, `finding-log`, `unknown-log`, `deadend-log`, `assumption-log` |
| **CHECK** | `check-submit` (gates noetic → praxic) |
| **Praxic** | `finding-log`, `decision-log`, `goals-complete-task` |
| **Before POSTFLIGHT** | `goals-complete`, `unknown-resolve`, or batch: `resolve-artifacts` |
| **POSTFLIGHT** | `postflight-submit` (closes transaction + triggers grounded verification) |
| **Between** | `goals-list`, `resolve-artifacts` (batch), `delete-artifacts` (cleanup) |
| **Batch** | `log-artifacts` (connected graph), `resolve-artifacts`, `delete-artifacts` |

---

## Spec-to-Transactions Cheatsheet

Given a spec or feature description:

1. **Read it fully** — don't start decomposing mid-read
2. **Identify nouns** — these are your domains/modules (potential goal boundaries)
3. **Identify verbs** — these are your actions (potential tasks)
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
