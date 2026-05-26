# Session and Goal Workflow

This is the day-to-day rhythm of working inside Empirica: sessions hold
context, transactions hold measurement, goals hold structure.

---

## The Three Layers

| Layer | Purpose | Lifetime |
|---|---|---|
| **Session** | Continuous AI working window | Compaction / explicit close |
| **Transaction** | Epistemic measurement cycle | PREFLIGHT → POSTFLIGHT |
| **Goal** | Tracked unit of work | Until `goals-complete` |

A session contains many transactions. A goal can span many transactions
(and many sessions). Every artifact you log carries a `transaction_id`
linking it back to the measurement window it belongs to.

---

## Minimal Workflow

```bash
# 1. Create a session (once per AI working window)
empirica session-create --ai-id $(basename $PWD) --output json
# → returns session_id

# 2. Open a measurement window
empirica preflight-submit - << 'EOF'
{
  "task_context": "What you're about to do",
  "vectors": {"know": 0.5, "uncertainty": 0.5, "context": 0.6},
  "reasoning": "Honest baseline"
}
EOF

# 3. Create a goal that ties this work to a tracked unit
empirica goals-create --objective "Implement JWT authentication"

# 4. (Optional) decompose into tasks
empirica goals-add-task --goal-id <GOAL_ID> --description "Map current auth surface"
empirica goals-add-task --goal-id <GOAL_ID> --description "Implement RS256 signing"
empirica goals-add-task --goal-id <GOAL_ID> --description "Write integration tests"

# 5. Investigate — log as you discover
empirica finding-log --finding "Current Auth0 setup uses HS256" --impact 0.7
empirica unknown-log --unknown "Are refresh tokens in scope?"

# 6. Gate noetic → praxic
empirica check-submit - << 'EOF'
{
  "vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.85},
  "reasoning": "Understand the auth surface, ready to implement"
}
EOF

# 7. Do the work + complete tasks as you go
# ... write code, run tests ...
empirica goals-complete-task --task-id <ID> --evidence "commit abc123 — auth.py + tests pass"

# 8. Close the transaction
empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.92, "uncertainty": 0.1, "context": 0.9, "completion": 1.0},
  "reasoning": "JWT auth shipped, tests green. Compare to PREFLIGHT for the learning delta."
}
EOF

# 9. Close the goal
empirica goals-complete --goal-id <GOAL_ID> --reason "Shipped + tested"
```

When you're inside an open transaction, `session_id` is auto-derived
from the active transaction file. Pass `--session-id` only when you
need to target a different session (handoff / discovery flows).

---

## Goal Lifecycle

Goals move through three states:

| State | Meaning | When |
|---|---|---|
| `planned` | Logged, not started | Use for backlog / collaborative planning |
| `in_progress` | Active | Default on `goals-create` |
| `completed` | Done | After `goals-complete` |

### Planned goals — collaborative planning

```bash
# Catalog goals first, decide priorities together, then activate
empirica goals-create --objective "Implement auth middleware" --status planned
empirica goals-create --objective "Add session management" --status planned
empirica goals-create --objective "Write integration tests" --status planned

# See what's queued
empirica goals-list --status planned

# Activate when ready
empirica goals-activate --goal-id <ID>
```

Planned goals are excluded from active metrics — they don't pollute
in-progress counts. Use this when decomposing a large piece of work
before starting any transaction.

---

## Goal Decomposition (Tasks)

A goal with tasks is the natural unit for grounded calibration:
each task is one tracked chunk of AI work that gets evidence on
completion.

```bash
# Add a task
empirica goals-add-task --goal-id <GOAL_ID> --description "Map current auth surface"
# → returns task_id

# List tasks
empirica goals-get-tasks --goal-id <GOAL_ID>

# Complete with evidence (commit SHA, test result, file path)
empirica goals-complete-task --task-id <ID> --evidence "commit abc123"

# Check goal progress
empirica goals-progress --goal-id <GOAL_ID>
```

**Decompose at PREFLIGHT, not retroactively.** A task added after
the work is done is a self-graded checkbox, not a tracked unit.

---

## Discovering and Resuming Work

```bash
# See active goals in this project
empirica goals-list

# See goals across all projects (workspace registry)
empirica goals-discover

# Resume a goal another AI worked on
empirica goals-resume --goal-id <ID> --ai-id $(basename $PWD)

# Claim a goal (creates branch if BEADS enabled)
empirica goals-claim --goal-id <ID>
```

`goals-ready` shows goals you're epistemically ready to work on
(combining BEADS dependency unblock state + your current vector state):

```bash
empirica goals-ready
```

---

## Handoffs

When you're handing off to another AI or another session:

```bash
empirica handoff-create \
  --task-summary "Auth middleware shipped; refresh tokens still TODO" \
  --key-findings "JWT RS256 chosen" "Auth0 already provides PKCE" \
  --next-session-context "Wire refresh-token rotation; spec is at docs/specs/AUTH.md"

# Receiving end:
empirica handoff-query --ai-id <THEIR_AI_ID> --limit 5
```

A handoff is roughly 90% smaller than carrying full context into the
next session.

---

## Common Pitfalls

**"No active transaction"** — call `preflight-submit` first. Most goal
commands work without one but log/CHECK requires it.

**"No CHECK passed"** — submit `check-submit` with `proceed: true` before
running praxic tools (Edit, Write, Bash). The Sentinel gates the
noetic→praxic transition.

**Goal not showing in list** — `goals-list` defaults to in-progress.
Use `--status planned` or `--status completed` or `--status all`.

**Task added but no progress shown** — check the goal-id matches:
`empirica goals-get-tasks --goal-id <ID>`.

---

## BEADS Integration (Optional)

If the `bd` CLI is installed and `.empirica/project.yaml` opts in
(`beads: { default_enabled: true }` or `--use-beads` flag), goals
get a paired BEADS issue:

```bash
empirica goals-create --objective "Implement auth" --use-beads
# → goal_id + beads_issue_id (e.g. bd-a1b2)
```

BEADS adds dependency tracking + ready-work detection on top of
Empirica's epistemic layer. See [BEADS_QUICKSTART.md](BEADS_QUICKSTART.md).

---

## Visual Flow

```
  ┌────────────────────────────────────────────────────────────┐
  │  Session (working window)                                  │
  │                                                            │
  │  ┌──────────────────────────────────────────────────────┐  │
  │  │  Transaction 1                                       │  │
  │  │  PREFLIGHT → noetic → CHECK → praxic → POSTFLIGHT    │  │
  │  │                                                      │  │
  │  │  goals-create — opens Goal A                         │  │
  │  │  goals-complete-task × N                          │  │
  │  └──────────────────────────────────────────────────────┘  │
  │                                                            │
  │  ┌──────────────────────────────────────────────────────┐  │
  │  │  Transaction 2                                       │  │
  │  │  PREFLIGHT → noetic → CHECK → praxic → POSTFLIGHT    │  │
  │  │                                                      │  │
  │  │  goals-complete-task × N                          │  │
  │  │  goals-complete — closes Goal A                      │  │
  │  └──────────────────────────────────────────────────────┘  │
  │                                                            │
  └────────────────────────────────────────────────────────────┘
```

---

## See Also

- **First time:** [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)
- **CLI basics:** [04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)
- **Vectors:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
- **Project model:** [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
- **BEADS:** [BEADS_QUICKSTART.md](BEADS_QUICKSTART.md)
