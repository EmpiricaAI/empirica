---
name: epistemic-transaction
description: "Use when starting complex work, planning implementation, breaking down tasks, creating specs, or when the user says 'plan this as transactions', 'plan transactions', 'break this down', 'create a spec', 'how should I approach this', 'transaction plan', or mentions needing a structured approach to multi-step work. This skill guides the full epistemic workflow from task decomposition through measured execution. Prefer this over EnterPlanMode for non-trivial tasks."
version: 2.0.0
---

# Epistemic Transaction Planning

**Turn tasks into measured work.** Investigation and implementation happen inside ONE
measurement window, artifacts are recorded as you go, and learning compounds across
boundaries.

```
PREFLIGHT → [noetic: investigate] → CHECK → [praxic: implement] → POSTFLIGHT
```

Use this instead of EnterPlanMode for non-trivial work.

## Plan before you open

Decompose FIRST. A task added after the work is done is a self-graded checkbox, not a
tracked unit.

```bash
empirica goals-create --objective "<title, ≤256>" --description "<markdown body>"
empirica goals-add-task --goal-id <ID> --description "<one unit of work>"
empirica goals-complete-task --task-id <ID> --evidence "commit abc123, tests pass"
```

`--description` is the rich body (≤8000 chars, markdown — the extension renders it).
Use it for anything substantive: why this exists, success criteria, links. Title-only
goals are for genuinely trivial work.

`--evidence` is what makes a task **grounded** rather than self-reported. Tie it to a
commit SHA, a test result, a file path — something deterministic.

**Decompose into tasks when** the work spans several files, investigation precedes
implementation, or it will produce ≥2 commits. Anything you would otherwise track in
a TodoWrite belongs here instead, where calibration can see it.

Lifecycle verbs, all reversible — a mis-close is recoverable, so you need not be
perfect at the completion boundary:

```bash
empirica goals-create --objective "Future: X" --status planned   # queued, not started
empirica goals-reopen --goal-id <ID> --reason "scope wasn't actually done"
empirica goals-archive --older-than 30 --apply    # dry-run without --apply
empirica goals-list --status completed --include-archived
```

Add `--project-id <name-or-uuid>` to log against a different project.

**Sizing:** bug fix or single function → 1 transaction. Feature across 2–3 files →
1–2. Cross-cutting concern → 2–3. "Redesign the system" → split further.

## PREFLIGHT — open the window

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

- `work_type`: `code|infra|research|release|debug|config|docs|data|comms|design|audit|remote-ops`
  — scales evidence weights by source relevance. Use `remote-ops` for work the local
  Sentinel cannot observe (SSH, customer machines, remote config); POSTFLIGHT then
  returns `calibration_status=ungrounded_remote_ops` and self-assessment stands
  unchallenged.
- `work_context`: `greenfield|iteration|investigation|refactor` — adjusts
  normalization baselines for project maturity.

**PREFLIGHT declares scope.** If scope creeps, that is the signal to POSTFLIGHT and
open a new transaction — not to quietly widen this one.

**If you were already grounded before opening** — you read the files first, which is
the normal order — declare `claims` here with grounding `read` or `ran`. One such
claim certifies the transaction and praxic proceeds with **no CHECK at all**. That is
the correct path, not a shortcut.

## Noetic phase — investigate

Noetic work is ungated. Read, search and retrieve freely; individual Read / Grep /
Glob / investigate calls need no batching.

**Batch when you have ≥3 operations** — the value is one merged result and fewer
round-trips. It is NOT a Sentinel bypass, and calling it for a single read is misuse
(the executor returns a `warning` field).

```bash
empirica noetic-batch - << 'EOF'
{
  "intent": "understand auth middleware chain",
  "reads": [{"path": "src/auth.py"}, {"path": "src/middleware.py"}],
  "greps": [{"pattern": "decorator", "glob": "src/**/*.py", "context": 2}],
  "globs": ["src/**/*auth*", "tests/**/*auth*"],
  "investigate": [{"query": "auth middleware patterns", "scope": "project"}]
}
EOF
```

(MCP: `mcp__empirica__noetic_batch`, same payload.)

### Log as a graph, and type it by the question it answers

A bug you found in the code is a `finding`; *you* shipping it is a `mistake`.
Something you have not verified is an `assumption`; something you know you don't know
is an `unknown`. Defaulting everything to `finding` is the most common way this layer
degrades — `/empirica-constitution` §III-b has the full table.

```bash
empirica log-artifacts - << 'EOF'
{
  "nodes": [
    {"ref": "f1", "type": "finding",
     "data": {"finding": "Middleware chain uses app.use() with a path prefix", "impact": 0.5}},
    {"ref": "u1", "type": "unknown",
     "data": {"unknown": "Where are role definitions stored?"}},
    {"ref": "a1", "type": "assumption",
     "data": {"assumption": "All routes need auth except /health",
              "confidence": 0.8, "domain": "routing"}},
    {"ref": "d1", "type": "dead_end",
     "data": {"approach": "Tried passport.js", "why_failed": "Too heavy for JWT-only auth"}}
  ],
  "edges": [
    {"from": "d1", "to": "f1", "relation": "grounded_by"},
    {"from": "a1", "to": "f1", "relation": "evidence"},
    {"from": "u1", "to": "<id-of-a-PRIOR-finding>", "relation": "raised_by"}
  ]
}
EOF
```

Structural artifact→goal edges are written for you. Assert the SEMANTIC ones:
`evidence`, `grounded_by`, `caused_by`, `invalidates`, `resolves`, `sourced_from`.
Most edges should reach artifacts from EARLIER transactions — if every edge joins two
nodes you just created, you are building disconnected islands.

Single verbs remain right for one genuinely standalone artifact:

```bash
empirica finding-log --finding "..." --impact 0.5 --description "<markdown>"
empirica unknown-log --unknown "..."          # and RESOLVE it when answered
empirica assumption-log --assumption "..." --confidence 0.6 --domain infrastructure
empirica deadend-log --approach "..." --why-failed "..."
empirica decision-log --choice "..." --rationale "..." --reversibility committal
empirica mistake-log --mistake "..." --why-wrong "..." --prevention "..."
empirica note "..." --tag followup            # scratchpad; triaged at POSTFLIGHT
```

Every `*-log` takes `--description` (markdown body), `--epistemic-source
intuition|search|mixed`, and `--visibility local|shared|public`. Skip the body when
the title tells the whole story — over-describing trivia is its own anti-pattern.

`--reversibility` is `exploratory|committal|forced`. On `mistake-log`, `--prevention`
is the load-bearing field: what future-you needs in order not to repeat it.

### Sources

Register an external origin when the artifact came from something `git blame` cannot
reach — an RFC, a paper, a vendor advisory, a customer call. Essential in Claude
Desktop and other non-CLI surfaces, where most artifacts originate outside the repo.

```bash
empirica source-add --title "RFC 7519 — JSON Web Tokens" \
  --url "https://datatracker.ietf.org/doc/html/rfc7519" --noetic --confidence 0.95
# → source_id, then link it via "sourced_from" in log-artifacts
```

Skip it when the source is this repo at HEAD — git already holds that provenance.
Use `--visibility shared` for anything peers should reference rather than re-derive.

## CHECK — gate the transition

```bash
empirica check-submit - << 'EOF'
{
  "session_id": "<ID>",
  "vectors": {
    "know": 0.82, "uncertainty": 0.15,
    "context": 0.85, "clarity": 0.88
  },
  "reasoning": "Investigated middleware chain, understand JWT flow, know where roles live. Ready to implement.",
  "claims": [
    {"claim": "roles live in the JWT claims, not the session store",
     "grounding": "read", "ref": "src/auth/jwt.py:40-58"},
    {"claim": "the middleware chain runs auth before the route handler",
     "grounding": "ran", "ref": "curl -H 'Authorization: ...' /health → 401 before handler log"},
    {"claim": "all routes except /health need auth",
     "grounding": "assumed"}
  ]
}
EOF
```

`grounding`: **`read`** (opened the source) · **`ran`** (executed and observed — the
strongest) · **`retrieved`** (from our OWN prior artifact — testimony, not
observation) · **`assumed`** (acting without checking).

`retrieved` counts as weak deliberately. Our artifacts were true when written and age
like any other prior; the same artifact can be solid grounding for one claim and stale
for another, which is why the label belongs to the claim rather than the artifact.

Decision: `proceed` → write code (praxic, **same transaction**) · `investigate` →
keep exploring (noetic, **same transaction**). **CHECK does not end the transaction.**

**CHECK certifies; it does not unlock.** Name the 2–3 claims the praxic work actually
rests on. An empty CHECK is worse than none — it looks like diligence and carries
nothing. Measured on one practice: 47% of 728 CHECKs arrived within 30 seconds of
their PREFLIGHT.

## Praxic phase — implement

Write code. **Commit per completed task**, not batched at the end — uncommitted work
is invisible to grounded calibration. Keep logging: discoveries during implementation
are findings, choices are decisions, and an approach that failed is a dead end even
when the next one works.

## POSTFLIGHT — close the window

**Before closing**, in this order:

1. Log remaining artifacts — the window shuts here, and anything logged after is
   invisible to calibration.
2. Resolve unknowns the work answered.
3. **Retract what this transaction proved WRONG** — `finding-resolve <id> --kind
   retracted`. You hold the evidence *now*; a later gardening sweep will only have the
   age. `stale` means it merely aged; `superseded --superseded-by <id>` means
   something replaced it.
4. Complete finished goals and tasks.
5. Adjudicate the claims you declared.
6. Ask the user whether anything else should be logged.

```bash
empirica postflight-submit - << 'EOF'
{
  "session_id": "<ID>",
  "vectors": {
    "know": 0.92, "uncertainty": 0.08,
    "context": 0.90, "clarity": 0.95,
    "completion": 1.0, "do": 0.90
  },
  "reasoning": "Auth middleware implemented with role guards. Unit tests passing.",
  "claims": [
    {"index": 1, "verdict": "held",    "evidence": "middleware tests pass against real JWTs"},
    {"index": 2, "verdict": "refuted", "evidence": "auth runs AFTER the logger, not before"}
  ]
}
EOF
```

Verdicts: `held` · `refuted` · `untested`. Address a claim by `index` (1-based,
declaration order) or `id`. The payload keys are `verdict` and `evidence` — `note` is
accepted as an alias.

**Anything you don't adjudicate is recorded as `untested` and reported as a gap.**
That is the feature, not a penalty. `refuted` is rare, `held` is cheap, and *"I acted
on this and never checked it"* is the one state a single `know` score cannot express.
An untested claim is not a failure of the transaction; **hiding it would be.**

A `refuted` claim usually means a prior artifact is now false too — that is the moment
to retract it, while you still hold the evidence.

**POSTFLIGHT when:** a coherent chunk is complete, confidence shifts, context changes,
scope creeps, or 10+ turns have passed without measurement.

### Compliance loop

Runs automatically when `domain` and `criticality` were set at PREFLIGHT:

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

Tiered by cost: lint, complexity and git_metrics run at every POSTFLIGHT (~5s, ~80MB);
tests run at goal completion.

## The rules that bind

**Goal-per-transaction.** Every transaction links to a goal; multi-step requests
decompose at PREFLIGHT.

**Commit-per-task.** Not one batched commit at the end.

**Artifact breadth.** A session logging 25 findings and zero unknowns or assumptions
did not have zero uncertainty — it failed to type it. Reported uncertainty with no
`unknown` or `assumption` behind it is an unsupported claim.

**Close before POSTFLIGHT.** The window shuts; late artifacts are invisible.

**Never split noetic and praxic across transactions.** Investigating in one and
implementing in another destroys the delta that IS the measurement — a PREFLIGHT that
closes before acting has no outcome, and one that acts without a baseline has no
start. It is the most common mistake and it looks tidy, which is why it persists.

**Keep the window holdable.** Five goals and fifteen files in one transaction makes
the delta meaningless noise. One or two goals.

**Mirror tasks to Claude Code Tasks** on larger transactions so the user sees progress
(`goals-add-task` → `TaskCreate`, `goals-complete-task` → `TaskUpdate`). Advisory —
judge when visibility beats overhead.

**Mesh work:** ask when uncertain (`cortex_collab`, ungated), propose when convergent
(ECO-gated), ack what you complete. Depth in `/cortex-mailbox-send` where your install has Cortex.

## Between transactions

```bash
empirica goals-list                  # complete what's done
empirica resolve-artifacts -         # batch: unknowns, assumptions, goals, findings
empirica delete-artifacts -          # batch cleanup; preview by default, --apply to act
empirica update-artifacts -          # METADATA only — impact, visibility, epistemic_source
```

Unresolved artifacts accumulate as noise, and PREFLIGHT retrieves your prior artifacts
to build context — so a clean graph is directly better context for the next
transaction.

**A wrong CLAIM takes `finding-resolve --kind retracted`; wrong METADATA takes
`update-artifacts`.** Do not resolve a true finding to fix a number. Claim text is
immutable by design — retraction preserves the original wording and records that it
failed.

Measured on one practice: 1268 findings resolved, of which **1267 meant *stale* and 1
meant *wrong***. A true error rate near zero across thousands of claims is not
plausible; errors were simply not being expressed. A practice that cannot distinguish
its ageing from its errors cannot calibrate on either.

## Commands by phase

| Phase | Commands |
|-------|----------|
| **Planning** | `goals-create`, `goals-add-task`, `unknown-log`, `assumption-log` |
| **PREFLIGHT** | `preflight-submit` (opens transaction) |
| **Noetic** | `noetic-batch` (3+ ops), `source-add`, `finding-log`, `unknown-log`, `deadend-log`, `assumption-log`, `note` |
| **CHECK** | `check-submit` (gates noetic → praxic) |
| **Praxic** | `finding-log`, `decision-log`, `goals-complete-task` |
| **Before POSTFLIGHT** | `goals-complete`, `unknown-resolve`, or batch `resolve-artifacts` |
| **POSTFLIGHT** | `postflight-submit` (closes + triggers grounded verification) |
| **Between** | `goals-list`, `resolve-artifacts`, `delete-artifacts`, `update-artifacts` |

## Earned autonomy

The plan is a starting estimate, not a contract. Vectors shift as you learn, and
measuring the delta between estimated and actual is what builds calibration — a
PREFLIGHT that predicted badly is data, not a failure.
