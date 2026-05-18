# CLI Quick Start

**Time:** 10 minutes. **Prerequisites:** `pip install empirica` and `empirica project-init` in your repo (see [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)).

This guide walks the most common CLI commands. For the full reference,
see [CLI_COMMANDS_UNIFIED.md](../developers/CLI_COMMANDS_UNIFIED.md).

---

## The Core Loop

Every measured chunk of work follows the same shape:

```
PREFLIGHT → (noetic: investigate) → CHECK → (praxic: implement) → POSTFLIGHT
```

```bash
# 1. Create a session (once per AI working window)
SESSION_ID=$(empirica session-create --ai-id $(basename $PWD) --output json | jq -r .session_id)

# 2. Open a transaction
empirica preflight-submit - << 'EOF'
{
  "task_context": "Fix auth bug in token validation",
  "vectors": {"know": 0.45, "uncertainty": 0.6, "context": 0.5, "clarity": 0.7},
  "reasoning": "Seen the area, haven't read the actual code yet."
}
EOF

# 3. Investigate (noetic phase — log as you discover)
empirica finding-log --finding "Token audience check is skipped on refresh" --impact 0.8
empirica unknown-log --unknown "Does the same gap exist on access-token validation?"
empirica deadend-log --approach "Tried patching the JWT lib" --why-failed "We don't own that code path"

# 4. Gate the noetic → praxic transition
empirica check-submit - << 'EOF'
{
  "vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.85, "clarity": 0.9},
  "reasoning": "Read the validator, understand the gap, ready to fix."
}
EOF

# 5. Do the work (write code, run tests, commit)
empirica goals-create --objective "Fix audience check on refresh path"
# ... write code ...
empirica goals-complete --goal-id <ID> --reason "Fix shipped, tests cover the regression"

# 6. Close the transaction
empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.92, "uncertainty": 0.1, "context": 0.9, "completion": 1.0},
  "reasoning": "Audience check now enforced on both paths. POSTFLIGHT delta = learning."
}
EOF
```

POSTFLIGHT also triggers grounded verification: deterministic services
(ruff, pyright, tests, git, codebase model) collect observations and
surface divergence from your beliefs. That divergence is your
calibration signal.

---

## Sessions & Transactions

```bash
# Create
empirica session-create --ai-id $(basename $PWD)
empirica session-create --ai-id myai --output json    # machine-readable

# List
empirica sessions-list
empirica sessions-list --limit 5 --output json

# Show
empirica sessions-show --session-id <ID>

# Resume a prior session
empirica sessions-resume --ai-id $(basename $PWD) --count 1
```

When you're inside an open transaction, `session_id` is auto-derived —
you don't need to pass it to log/CHECK/POSTFLIGHT.

---

## Projects

```bash
# Initialize this repo as an Empirica project
empirica project-init

# Load current project context (recent findings, open goals, etc.)
empirica project-bootstrap

# List all locally-known projects
empirica projects-list

# Switch by directory — auto-detected
cd ../other-project && empirica project-bootstrap
```

See [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
for the full project model.

---

## Goals & Subtasks

```bash
# Create a goal (defaults to in_progress)
empirica goals-create --objective "Implement JWT auth" \
  --description "RS256 signing, audience check on both validate + refresh paths"

# Plan a goal without starting work
empirica goals-create --objective "Migrate to Redis sessions" --status planned

# Decompose into subtasks
empirica goals-add-subtask --goal-id <GOAL_ID> --description "Map current auth surface"
empirica goals-add-subtask --goal-id <GOAL_ID> --description "Implement RS256"
empirica goals-add-subtask --goal-id <GOAL_ID> --description "Write integration tests"

# List + progress
empirica goals-list
empirica goals-list --status planned
empirica goals-progress --goal-id <GOAL_ID>

# Complete with evidence
empirica goals-complete-subtask --subtask-id <ID> --evidence "commit abc123"
empirica goals-complete --goal-id <GOAL_ID> --reason "Shipped + tested"

# Find ready work (BEADS unblocked + epistemically fit)
empirica goals-ready
```

`--description` accepts up to 8000 chars of markdown — use it for
substantive context, success criteria, and links. The TUI and extension
render it as prettified markdown.

---

## Artifacts (Log as You Work)

```bash
# Discoveries
empirica finding-log --finding "Routes use Bearer tokens" --impact 0.6
empirica finding-log --finding "Lib X has a bug at v2.3" --impact 0.7 --epistemic-source search

# Open questions
empirica unknown-log --unknown "How are refresh tokens stored?"

# Failed approaches (so you and future-you don't repeat them)
empirica deadend-log --approach "Tried passport.js" --why-failed "Too heavy for JWT-only"

# Errors made
empirica mistake-log --mistake "Bumped to v2.3 without checking changelog" \
  --why-wrong "v2.3 introduced the audience-check regression" \
  --prevention "Read upstream changelog on every bump"

# Beliefs you're working from
empirica assumption-log --assumption "Redis is available in prod" --confidence 0.7

# Decisions you make
empirica decision-log --choice "Use RS256 over HS256" --rationale "Public verifier separation"

# External sources cited
empirica source-add --title "RFC 7519 — JWT" --source-url "https://datatracker.ietf.org/doc/html/rfc7519"
```

Provenance: `--epistemic-source intuition|search|mixed` records whether
the artifact came from session reads vs priors. Honest tagging produces
honest calibration.

Visibility: `--visibility local|shared|public` opts an artifact into
cross-project surfacing via Qdrant. Default is `local`.

---

## Batch Operations

When logging ≥3 connected artifacts in one transaction, use the graph
form instead of N individual `*-log` calls:

```bash
empirica log-artifacts - << 'EOF'
{
  "nodes": [
    {"ref": "f1", "type": "finding",
     "data": {"finding": "JWT validator skips audience on refresh", "impact": 0.8}},
    {"ref": "d1", "type": "decision",
     "data": {"choice": "Enforce audience on both paths",
              "rationale": "Closes the symmetric gap",
              "reversibility": "exploratory"}}
  ],
  "edges": [
    {"from": "d1", "to": "f1", "relation": "evidence"}
  ]
}
EOF
```

Edges anchor artifacts in the commit-context graph — `empirica
commit-context <sha>` walks them.

---

## Inspecting the Project

```bash
# Recent findings, open unknowns, active goals
empirica project-bootstrap

# Search across this project's Qdrant
empirica project-search --task "auth bug"

# Cross-project search (global_learnings collection)
empirica project-search --task "JWT pattern" --global

# Walk commit-anchored artifacts
empirica commit-context HEAD
empirica commit-context --range HEAD~10..HEAD

# Calibration report
empirica calibration-report
empirica calibration-report --learning-trajectory    # PREFLIGHT→POSTFLIGHT deltas
```

---

## Output Formats

```bash
empirica sessions-list                    # Human-friendly (colored)
empirica sessions-list --output json      # JSON for scripting
echo '{...}' | empirica session-create -  # AI-first JSON stdin
```

---

## Common Workflows

### Solo work, single transaction

```bash
empirica preflight-submit -    # JSON via stdin
# ... investigate + log ...
empirica check-submit -        # gate
# ... implement + commit ...
empirica goals-complete --goal-id <ID> --reason "..."
empirica postflight-submit -   # close
```

### Long-running goal across sessions

```bash
# Session 1
empirica goals-create --objective "Refactor auth" --status planned
# ...activate when ready in next session...

# Session N
empirica goals-activate --goal-id <ID>
empirica preflight-submit -
# ... work on subtasks ...
empirica goals-complete-subtask --subtask-id <ID> --evidence "commit ..."
empirica postflight-submit -
```

### Handoff to another AI / session

```bash
empirica handoff-create \
  --task-summary "Auth middleware shipped; refresh-token rotation TODO" \
  --key-findings "RS256 chosen" "Auth0 already provides PKCE" \
  --next-session-context "Wire rotation; spec at docs/specs/AUTH.md"

# Receiving end:
empirica handoff-query --ai-id <THEIR_AI_ID> --limit 5
```

---

## AI-Mesh Orchestration (Optional)

If you've set up cortex creds in `~/.empirica/credentials.yaml`, you can
send work to other AIs:

```bash
# Start the listener subprocess (subscribes to ntfy wake bridge)
empirica loop listen --instance $(basename $PWD)

# In another AI session: see what was queued for you
# (via Cortex MCP — see docs/architecture/EVENT_LISTENER.md)
```

The full send/receive surface lives in the `cortex-mailbox-send` /
`cortex-mailbox-poll` skills (loaded by AIs when a Monitor is armed).

---

## Quick Reference

```bash
# Essentials
empirica session-create --ai-id $(basename $PWD)
empirica project-bootstrap
empirica preflight-submit -
empirica check-submit -
empirica postflight-submit -

# Artifacts
empirica finding-log --finding "..." --impact 0.7
empirica unknown-log --unknown "..."
empirica decision-log --choice "..." --rationale "..."
empirica goals-create --objective "..." --description "..."
empirica goals-complete --goal-id <ID> --reason "..."

# Inspection
empirica goals-list
empirica project-search --task "..."
empirica calibration-report
empirica commit-context HEAD

# Diagnostics
empirica diagnose       # integration health
empirica doctor         # install health
empirica system-status  # runtime status
```

---

## Next Steps

- **The 13 vectors:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
- **Full CLI reference:** [CLI_COMMANDS_UNIFIED.md](../developers/CLI_COMMANDS_UNIFIED.md)
- **Project model:** [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
- **Workflow patterns:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Troubleshooting:** [03_TROUBLESHOOTING.md](03_TROUBLESHOOTING.md)
