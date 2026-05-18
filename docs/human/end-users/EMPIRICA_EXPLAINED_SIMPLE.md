# Empirica, Explained Simply

**What it is:** A measurement layer for AI work. It tracks what your
AI knows, what it's learning, what it's failed at, and how its beliefs
match (or don't match) observable outcomes — so you can trust it more.

---

## The Problem

AI agents are often **confidently wrong**:

```
You: "Can you implement OAuth2 authentication?"
AI:  "Sure! I know OAuth2 well." [Actually doesn't]
AI:  [Implements something that compiles but is wrong]
You: [Wastes hours debugging]
```

**Root cause:** AI can't reliably distinguish "I know this" from
"I think I can figure this out."

---

## The Solution

Empirica makes AI agents **epistemically honest** — they declare what
they actually know vs what they're guessing about, then act on it.

```
You: "Can you implement OAuth2 authentication?"
AI:  "know=0.45, uncertainty=0.70 — let me investigate the spec first."
AI:  [Reads docs, searches the codebase, logs findings]
AI:  "know=0.85, uncertainty=0.20 — ready to proceed."
AI:  [Implements correctly]
```

The system then **grounds** those self-assessments against deterministic
observations (tests passing, commits landed, code complexity changed)
and surfaces the divergence as a discipline signal. Good calibration →
the AI earns autonomy.

---

## Three Systems in One

### 1. Epistemic ledger (self-assessment)

13 vectors on `0.0–1.0`, reported at PREFLIGHT (start), CHECK (gate),
and POSTFLIGHT (end) of every measured chunk of work. See
[05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
for the full set; the foundation five are:

- **know** — domain understanding
- **do** — execution capability
- **context** — situational awareness
- **engagement** — focused vs distracted
- **uncertainty** — explicit unknowns (higher = more uncertain)

### 2. Project memory (compressed context loading)

Every project has its own `.empirica/sessions/sessions.db`. The AI
logs findings, unknowns, dead-ends, assumptions, decisions, and
mistakes as it works — all anchored to the transaction they were
made in. Future sessions load this context in ~800 tokens via
`empirica project-bootstrap`, instead of starting from a blank slate.

```bash
empirica finding-log --finding "Auth uses Auth0 SSO with PKCE" --impact 0.7
empirica unknown-log --unknown "How are refresh tokens stored?"
empirica deadend-log --approach "Tried passport.js" --why-failed "Too heavy"
```

### 3. Goal tracking (structural progress)

Goals are tracked units of AI work, optionally decomposed into
subtasks. Each subtask gets evidence (commit SHA, test result, file
path) when complete. Goal-per-transaction discipline is what makes
grounded calibration possible.

```bash
empirica goals-create --objective "Implement OAuth2 client with PKCE"
empirica goals-add-subtask --goal-id <ID> --description "Map current auth"
empirica goals-complete-subtask --subtask-id <ID> --evidence "commit abc123"
```

---

## The CASCADE Workflow

Every measured chunk of work follows the same shape:

```
PREFLIGHT → (noetic: investigate) → CHECK → (praxic: implement) → POSTFLIGHT
```

### 1. PREFLIGHT — "What do I know going in?"

Opens the measurement window. Honest baseline.

```bash
empirica preflight-submit - << 'EOF'
{
  "task_context": "Implement OAuth2 authentication",
  "vectors": {"know": 0.45, "uncertainty": 0.7, "context": 0.5},
  "reasoning": "Familiar with OAuth2 generally, haven't read this codebase's auth surface yet."
}
EOF
```

### 2. Noetic phase — "Reduce my uncertainty"

Read, search, log. Praxic tools (Edit/Write/Bash) are blocked by the
Sentinel until CHECK passes — this is the noetic firewall.

```bash
empirica finding-log --finding "Auth0 PKCE is used" --impact 0.7
empirica unknown-log --unknown "Refresh token rotation TBD"
```

### 3. CHECK — "Am I ready to act?"

Gate between investigation and action.

```bash
empirica check-submit - << 'EOF'
{
  "vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.85},
  "reasoning": "Understand the auth surface, ready to implement."
}
EOF
```

Returns `proceed` or `investigate`. (Or auto-proceeds if your vectors
are high enough that no CHECK ceremony is needed.)

### 4. Praxic phase — "Do the work"

Write code, run tests, commit. Keep logging as new things come up.

### 5. POSTFLIGHT — "What did I learn?"

Closes the window. The system computes:

- **Learning delta:** PREFLIGHT → POSTFLIGHT vector change
- **Grounded observations:** tests, git, code-quality, codebase model
- **Divergence:** where your beliefs vs observations don't match

That divergence is your calibration signal — not a verdict on truth,
but pointers at where work discipline needs attention next time.

```bash
empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.92, "uncertainty": 0.1, "context": 0.9, "completion": 1.0},
  "reasoning": "OAuth2 + PKCE shipped, tests pass."
}
EOF
```

---

## A Real Example

**Without Empirica:**
```
1. AI starts implementing immediately
2. Makes architectural assumptions
3. Implements OAuth2 with a security hole
4. Hours wasted debugging
```

**With Empirica:**

```
PREFLIGHT: know=0.40, context=0.30, uncertainty=0.75 → investigate first

NOETIC:    log findings: "Auth0 SSO used", "PKCE required for public clients"
           log unknown:  "How are refresh tokens stored?"

CHECK:     know=0.80, context=0.85, uncertainty=0.20 → proceed

PRAXIC:    goals-create "Integrate Auth0 OAuth2 with PKCE"
           implement, test, commit

POSTFLIGHT: know=0.92, uncertainty=0.10
           Δuncertainty = -0.65  (investigation effective!)
           grounded: tests passed, 3 files changed, ruff clean
```

---

## Where Things Live (4-Layer Storage)

| Layer | What | Where |
|---|---|---|
| **HOT** | Active transaction state | Process memory |
| **WARM** | Sessions, transactions, artifacts | `.empirica/sessions/sessions.db` (SQLite, per-project) |
| **SEARCH** | Semantic retrieval | Qdrant collections (per-project + `global_learnings`) |
| **COLD** | Versioned + sharable | Git notes (`refs/notes/empirica_*`) |

What this means for you:
- Cloning a repo with empirica history = empty `.empirica/` (gitignored)
- `git fetch refs/notes/empirica_*` pulls in the team's epistemic trail
- `empirica project-search --task "..."` queries WARM + SEARCH for relevant artifacts

---

## Hooks: Why Everything Is Automatic (Claude Code)

When you run `empirica setup-claude-code`, Empirica installs hooks
that drive measurement without you doing anything:

| Hook | Fires | What it does |
|---|---|---|
| `SessionStart` | Conversation begins | Creates session, loads project context, posts breadcrumbs |
| `PreToolUse` | Before any tool call | Sentinel gate — blocks praxic tools until CHECK passes |
| `PreCompact` | Before context compression | Saves state to breadcrumbs so nothing is lost |
| `PostCompact` | After compression | Recovers state |
| `SessionEnd` | Conversation ends | Cleanup + persist final state |
| `UserPromptSubmit` | Each user message | Context injection (active goals, artifact reminders) |

This is why measurement "just works" once installed — the AI doesn't
have to remember to call PREFLIGHT or CHECK; the hooks make it
natural.

---

## Dual-Track Calibration

Calibration measures how well the AI's beliefs match observable outcomes.

**Track 1 — self-referential (learning trajectory):** PREFLIGHT →
POSTFLIGHT delta. Catches bias patterns like "always underestimates
completion by +0.5."

**Track 2 — grounded (service observations):** After POSTFLIGHT, services
collect deterministic measurements (tests, git, complexity) and surface
divergence from your beliefs. Doesn't claim to know what the AI "really"
knew — it tells you where to look for discipline gaps next time.

Better calibration → Sentinel loosens gates → AI gets more autonomy.
Worse calibration → tighter gates → more investigation required.
**Autonomy is earned, not asserted.**

---

## The Cognitive Immune System

The system learns from mistakes:

- **Findings** are antigens — new facts that challenge beliefs
- **Lessons** are antibodies — procedural knowledge with confidence
  that decays (min floor 0.3) when contradicted by new findings
- **Dead-ends** prevent re-exploration — once an approach fails, the
  reason is logged and surfaced if you try the same thing again

Mistakes have prevention strategies. Failed approaches are remembered.
Patterns are recognized across sessions and (with `--visibility shared`)
across projects.

---

## What Makes It Different

| Traditional AI workflows | Empirica |
|---|---|
| AI claims confidence without evidence | 13-vector self-assessment with grounded verification |
| No systematic learning tracking | PREFLIGHT → POSTFLIGHT delta + artifact graph |
| Context lost between sessions | `project-bootstrap` compressed context |
| No collaboration framework | Goals, handoffs, BEADS, cortex-mediated mesh |
| Token-heavy handoffs | ~90% reduction via handoff reports |

---

## Get Started

```bash
pip install empirica
cd your-project              # any git repo
empirica project-init        # mints .empirica/ + project.yaml
empirica setup-claude-code   # Claude Code users only
empirica diagnose            # sanity check
```

See [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md) for the full walkthrough,
[04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md) for daily CLI usage, and
[05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
for the 13 vectors.

---

**Key insight:** Empirica isn't task tracking. It's a measurement layer
that makes AI work checkable — and the divergence between belief and
observation is where collaboration actually improves over time.
