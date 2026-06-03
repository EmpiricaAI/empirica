# Logging and Finding — A Walkthrough

**Companion to [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md).** That doc covers the writing side — sessions, transactions, goals, the artifact types. This doc walks one complete scenario end-to-end and spends most of its real estate on the **finding-side** of Empirica: project-search, the entity graph, the commit-context walker, and how artifacts connect to one another.

If you've never logged an artifact, start with [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md). If you've been logging but feel like the data is going into a black hole, this is the doc.

---

## The Scenario

You're adding OAuth2 authentication to a web service. Real work — multi-file, requires investigation, takes more than one sitting. Three tasks: map the current auth surface, implement the JWT signing layer, write integration tests.

You'll see the full lifecycle: open a transaction, create the goal, log artifacts as you investigate, complete tasks with evidence, close the transaction. Then we'll spend most of the doc on **finding what you logged later** — the part most users underuse.

---

## Step 1 — Open the Work

```bash
# Open a measurement window with honest starting vectors
empirica preflight-submit - << 'EOF'
{
  "task_context": "Add OAuth2 to the web service. JWT signing + login + refresh.",
  "vectors": {
    "know": 0.4, "uncertainty": 0.5, "context": 0.6,
    "clarity": 0.5, "engagement": 0.85
  },
  "reasoning": "Familiar with OAuth2 in theory; haven't read this codebase's current auth flow"
}
EOF

# Create the goal — the tracked unit of work
empirica goals-create \
  --objective "Add OAuth2 authentication" \
  --description "## Scope
- JWT signing (RS256)
- /login endpoint accepting username + password
- /refresh endpoint accepting refresh token
- Integration tests covering both

## Success criteria
- All three endpoints return 200 on happy path + 401 on bad creds
- Refresh rotation closes the previous refresh token
- pytest passes locally"

# Returns: Goal ID: a1b2c3d4-...

# Decompose into tasks
empirica goals-add-task --goal-id a1b2c3d4-... \
  --description "Read current auth flow + log findings on what's there"
empirica goals-add-task --goal-id a1b2c3d4-... \
  --description "Implement JWT signing + /login + /refresh"
empirica goals-add-task --goal-id a1b2c3d4-... \
  --description "Write integration tests + verify"
```

---

## Step 2 — Investigate, Log as You Discover

```bash
# Read the existing auth code. Log what you find.
empirica finding-log \
  --finding "Current auth uses session cookies signed with HS256 in middleware/auth.py" \
  --impact 0.6

empirica finding-log \
  --finding "User table has password_hash but no refresh_token column — schema migration needed" \
  --impact 0.7

empirica unknown-log \
  --unknown "Should refresh tokens be in a separate sessions table or just on the users table?"

# Some path turns out to be a dead end:
empirica deadend-log \
  --approach "Tried passport.js for JWT — pulls 30+ dependencies for a 4-function need" \
  --why-failed "Way over-built for our scope; jose package + ~80 LOC of glue does it"

# Decision worth recording:
empirica decision-log \
  --choice "Separate refresh_tokens table over a column on users" \
  --rationale "Refresh rotation needs versioning; column would require complex update semantics; table = clean append + revoke flow" \
  --reversibility committal

# Complete task 1 with evidence
empirica goals-complete-task \
  --task-id <task-1-id> \
  --evidence "Auth surface mapped — findings 2 + decision logged. Schema migration: add refresh_tokens table."
```

---

## Step 3 — Implement, Complete

```bash
# Gate noetic → praxic
empirica check-submit - << 'EOF'
{
  "vectors": {
    "know": 0.8, "uncertainty": 0.15, "context": 0.85, "clarity": 0.85,
    "engagement": 0.9
  },
  "current_phase": "noetic",
  "reasoning": "Mapped the surface, picked the approach, ready to write"
}
EOF
# → decision: proceed

# Write code. Run tests. Commit.
git add -A && git commit -m "feat(auth): JWT signing + login + refresh endpoints"
# → commit SHA: 7c4f9a2

empirica goals-complete-task \
  --task-id <task-2-id> \
  --evidence "Shipped in 7c4f9a2 — JWT signing + /login + /refresh. Tests passing."

# Task 3 — integration tests
empirica goals-complete-task \
  --task-id <task-3-id> \
  --evidence "Integration tests in tests/test_auth.py: 7 cases, all passing. Coverage 91%."

# Close the goal
empirica goals-complete --goal-id a1b2c3d4-... \
  --reason "Shipped — all 3 tasks done, tests green, manual verification passed"
```

---

## Step 4 — Close the Transaction

```bash
empirica postflight-submit - << 'EOF'
{
  "vectors": {
    "know": 0.9, "uncertainty": 0.1, "context": 0.9,
    "completion": 1.0, "do": 0.92, "engagement": 0.9
  },
  "reasoning": "Shipped end-to-end with tests."
}
EOF
```

That's the writing side. Now the part this doc is about.

---

# The Finding Side

You've logged ~6 artifacts (2 findings, 1 unknown, 1 dead-end, 1 decision, plus the goal + tasks). The week passes. You come back. Or a teammate inherits the work. Or a peer AI is asked "what's the auth state?" Here's how to find it all.

## Finding 1 — Semantic Search by Content

The most-used finder:

```bash
# Search artifacts in THIS project semantically
empirica project-search --task "JWT refresh token storage"

# Returns ranked matches:
#  → DECISION  Separate refresh_tokens table over a column on users  (impact 0.7)
#  → FINDING   User table has password_hash but no refresh_token column...
#  → DEAD-END  Tried passport.js for JWT — pulls 30+ dependencies...
```

`project-search` queries the Qdrant collections that get populated automatically when you log artifacts. The match is semantic (not regex) — "JWT refresh token storage" matches "refresh_tokens table" even though the words don't overlap.

```bash
# Cross-project: find similar work I've done anywhere
empirica project-search --task "JWT refresh rotation" --global
```

`--global` queries the `global_learnings` Qdrant collection — your accumulated lessons across every project you've worked in. Useful for "have I solved this before in a different repo?"

---

## Finding 2 — The Artifact Graph

Artifacts aren't islands — they're nodes in a graph. The graph is:

```
goal ──addresses_goal── finding
  ╲                    ╱
   tracks       evidence
        ╲     ╱
       decision ──invalidates──► finding (older)
         │
        sourced_from ──► source(doc)
```

Edges are typed: `evidence`, `raised_by`, `grounded_by`, `resolves`, `invalidates`, `sourced_from`, `caused_by`, `prevents`, `attached_to`. (The v0 bead-specific edges `tracks` / `owned_by` / `about` / `worked_by` shipped in 1.10.5 + were retired in 1.11.2. Cross-practitioner coordination state has moved to Shared Epistemic Records in cortex — see [`MESH_CONCEPTS.md`](MESH_CONCEPTS.md).)

You can declare edges when you log, or use `log-artifacts` to log a batch with edges in one call:

```bash
empirica log-artifacts - << 'EOF'
{
  "nodes": [
    {"ref": "f1", "type": "finding",
     "data": {"finding": "JWT refresh rotation needs a revocation list", "impact": 0.6}},
    {"ref": "d1", "type": "decision",
     "data": {"choice": "Use a separate refresh_tokens table",
              "rationale": "Append + revoke is cleaner than column-update"}}
  ],
  "edges": [
    {"from": "d1", "to": "f1", "relation": "grounded_by"}
  ]
}
EOF
```

Why edges matter: they make your reasoning **walkable**. Once you can walk from a decision back to its supporting findings, audits work, future-you reconstructs intent, peer AIs inherit context.

---

## Finding 3 — Entity Discovery

Projects, contacts, organisations, engagements, users — Empirica tracks them as **entities** in a workspace-wide registry, with typed memberships connecting them.

```bash
# What entities exist in my workspace?
empirica entity-list

# Filter by type
empirica entity-list --type project
empirica entity-list --type contact

# Find by name (substring match on display + description)
empirica entity-search "OAuth"
```

The interesting one is walking the graph:

```bash
# Walk outward from a specific entity, 2 levels deep
empirica entity-walk project:a1b2c3d4-... --depth 2

# Or: "show me everything connected to a contact"
empirica entity-walk contact:c-bob-jones-acme
```

`entity-walk` does a BFS with cycle protection — returns the entity + its incoming + outgoing edges out to `--depth N` hops. Useful when you want "what's connected to this client" or "what projects has this engagement covered."

For per-project artifact discoverability, the more common verb is the per-project artifact search (`project-search --task`) — entity-walk is for the meta layer where projects intersect with contacts / orgs / engagements.

---

## Finding 4 — Commit-Context Walker

Every artifact's `transaction_id` ties it to a commit (or commits). The commit-context walker inverts that — given a commit, find all the artifacts logged in its transaction.

```bash
# What artifacts were logged around this commit?
empirica commit-context 7c4f9a2

# Or a date range
empirica commit-context --since 2026-05-01 --until 2026-05-31

# Or a rev range
empirica commit-context --range HEAD~10..HEAD

# Or a session
empirica commit-context --session b9cf3ad9
```

Output groups artifacts by type — findings, unknowns, decisions, dead-ends, mistakes, sources — and walks edges out to depth N (default 2). Useful for `git blame`-shaped questions ("what did I learn while writing this commit?") and for audit trails ("why was this decision made?").

```bash
# Just artifact-linked commits in a range
empirica commit-context --range HEAD~20..HEAD --only-with-artifacts

# Full traversal (deeper edge-walk than default)
empirica commit-context 7c4f9a2 --full
```

---

## Finding 5 — Sessions and Trajectories

Each session has its own state and trajectory. Inspect via:

```bash
# Recent sessions
empirica sessions-list --limit 10

# A specific session's full state
empirica session-show <session-id>

# Snapshot to file
empirica session-snapshot <session-id>
```

For multi-session inheritance — "what did the previous AI working on this project know?" — `project-bootstrap` rolls up the most relevant recent artifacts at session start. It's automatic on session-create, but you can run it manually too.

---

## A Day-Later Worked Example

You come back to the project a week later. "What was that OAuth2 work?"

```bash
# 1. Where did I leave it?
empirica project-bootstrap   # rolls up recent findings + open unknowns + active goals
# → "Active goal: Add OAuth2 authentication (3/3 tasks done, status=completed)"

# 2. What did I decide and why?
empirica project-search --task "OAuth2 refresh tokens decision"
# → finds the decision artifact + its grounding findings

# 3. What was the implementation commit?
empirica commit-context --range HEAD~20..HEAD --only-with-artifacts
# → 7c4f9a2: feat(auth) JWT signing + login + refresh — has 4 linked findings + 1 decision

# 4. Drill in
empirica commit-context 7c4f9a2 --full
# → full artifact set + edge walk

# 5. Anything similar I've done elsewhere?
empirica project-search --task "OAuth2 JWT" --global
# → maybe a finding from a previous project: "RS256 vs HS256 — RS256 worth it for shared validation"
```

The point: every step you've taken left a trace. Searching is the muscle Empirica gives you to use those traces.

---

## What Makes This Sticky

Two patterns that decide whether the data stays useful:

1. **Log breadth.** Findings alone aren't enough. Decisions explain *why*. Unknowns let future-you see what you didn't know. Dead-ends prevent re-walking failed paths. Mistakes carry prevention. Assumptions surface beliefs that turned out wrong.

2. **Link with edges.** A finding without `grounded_by` / `evidence` / `invalidates` / `sourced_from` edges is a free-floating sticky note. The graph is what makes it walkable.

The CLI nudges you on both — POSTFLIGHT retrospectives surface "narrow breadth" and "low edge density" notes. Heed them.

---

## See Also

- **Writing-side workflow:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Per-project basics:** [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
- **Multi-project lifecycle:** [PROJECT_LIFECYCLE.md](PROJECT_LIFECYCLE.md)
- **CLI reference:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
- **Architecture (artifact graph + commit-context):** `docs/architecture/GRAPH_TEMPORAL_LAYER.md`
