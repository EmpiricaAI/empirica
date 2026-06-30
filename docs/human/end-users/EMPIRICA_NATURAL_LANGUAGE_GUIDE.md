# Empirica Natural Language Guide

**How to collaborate with AI using Empirica — the user's role in the epistemic workflow**

> **The key insight:** You don't learn CLI commands. You learn to *think in measured
> transactions* — and guide the AI through natural language. The AI handles the CLI,
> the measurement, and the artifact logging. You direct the epistemic flow.

---

## Part 1: Getting Started

### What Happens When You Install

```bash
pip install empirica
empirica setup-claude-code
```

This single command installs the complete framework into Claude Code:
- **Hooks** that automate session management, Sentinel gating, and context recovery
- **Skills** (`/empirica`, `/epistemic-transaction`, `/empirica-framework`) for workflow guidance
- **System prompt** injected as `@include` in your CLAUDE.md
- **Statusline** showing real-time epistemic state in your terminal
- **MCP server** configuration for IDE environments

After setup, measurement starts automatically. You write code and collaborate normally —
Empirica tracks what the AI knows and learns in the background.

### Your First Session

When you start Claude Code in a project with Empirica installed, several things happen automatically:

1. **Session created** — A unique session ID is generated, linked to this project
2. **Context loaded** — Previous findings, goals, and calibration are retrieved
3. **Statusline active** — Your terminal shows the AI's current epistemic state
4. **Sentinel watching** — The noetic firewall prevents premature action

You don't need to do anything special. Just describe what you want to work on:

> "I need to fix the authentication bug in the login flow."

The AI will automatically:
- Create a goal for this work
- Run PREFLIGHT to establish a baseline assessment
- Begin investigating (noetic phase) before making any changes
- Log findings and unknowns as it reads your code
- Ask CHECK when it believes it understands enough to act
- Implement the fix (praxic phase) after passing the Sentinel gate
- Run POSTFLIGHT to measure what was learned
- Commit when complete

### What the Statusline Shows

The statusline in your terminal displays the AI's current state.
A typical line looks like:

```
🧠 know:0.72 unc:0.18 │ ⏱ noetic │ 🎯 2 goals │ 📊 tx:active
```

It tells you the AI's knowledge level, uncertainty, current phase
(noetic/praxic), active goals, and transaction state. You can use it
to guide the conversation:

- Low know + high uncertainty → "Take more time investigating"
- High know + low uncertainty → "I think we're ready to implement"
- Many transactions → "Let's wrap up and commit what we have"

### The `/empirica` Command

Type `/empirica status` at any time to see the full epistemic state, or:
- `/empirica on` — Resume tracking (this instance)
- `/empirica off` — Pause tracking for exploratory chat (this instance)

These default to the current instance, so pausing one terminal/pane doesn't
silence the others. Add `--global` (`/empirica off --global`) to pause every
instance at once. The same toggle is available straight from the shell as
`empirica off` / `empirica on`.

---

## Part 2: The CASCADE Workflow

### Transactions: The Core Unit of Work

Every piece of measured work is a **transaction** — a cycle of investigation followed
by action, bookended by measurement:

```
PREFLIGHT → Investigate → CHECK → Implement → POSTFLIGHT
   │           │            │         │            │
Baseline    Noetic       Gate     Praxic       Close window
Assessment  phase       decision  phase        + grounded verification
```

**Both investigation and implementation happen within the same transaction.**
CHECK is a gate *inside* the transaction, not a boundary between transactions.
POSTFLIGHT closes the measurement window and triggers deterministic
services (tests, git, code-quality, codebase model) which surface
divergence between the AI's beliefs and observable outcomes.

### Your Role: Guide with Natural Language

The CLI is for the AI, not for you. Your job is to **direct the epistemic flow**
through conversation. Here are the phrases that drive each phase:

#### Starting Work
| You say | What happens |
|---------|-------------|
| "I need to add rate limiting to our API." | AI creates goals, runs PREFLIGHT, starts investigating |
| "Can you investigate the codebase first?" | AI stays in noetic phase — reads, searches, logs findings |
| "Break this into goals and plan the transactions." | AI decomposes the task, sequences transactions with dependencies |

#### During Investigation (Noetic Phase)
| You say | What happens |
|---------|-------------|
| "What do we still not know?" | AI checks open unknowns, searches project memory, surfaces gaps |
| "That approach won't work. Log it as a dead-end." | AI logs dead-end with reason — this approach is never retried |
| "I think the database schema changed last week." | AI logs this as an assumption with a confidence level |
| "Check if there's prior work on this." | AI searches Qdrant memory for related findings from past sessions |

#### Transitioning to Action
| You say | What happens |
|---------|-------------|
| "I think we know enough. Let's implement." | AI submits CHECK, Sentinel validates readiness, transitions to praxic |
| "How confident are you in this approach?" | AI reports current vectors, calibration biases, uncertainty levels |
| "We need to investigate more before acting." | AI stays noetic, continues exploring |

#### During Implementation (Praxic Phase)
| You say | What happens |
|---------|-------------|
| "Good, implement the middleware." | AI writes code, runs tests, logs decisions |
| "Let's use Redis for this — log that decision." | AI records the choice point with rationale |
| "That's a good discovery, log it." | AI logs finding with impact score (discoveries happen during coding too) |

#### Closing a Transaction
| You say | What happens |
|---------|-------------|
| "Good work. Commit and close this transaction." | AI commits, runs POSTFLIGHT, measures the learning delta |
| "Let's move to the next transaction." | AI reviews open artifacts, resolves unknowns, starts fresh PREFLIGHT |
| "We're done with this goal." | AI marks goal complete with evidence, closes transaction |

### The Sentinel: Why Investigation Comes First

The **Sentinel** is a noetic firewall — it blocks destructive actions (editing files,
running commands) until the AI has demonstrated sufficient understanding through CHECK.

This isn't bureaucracy. It prevents the most common AI failure mode: **acting before
understanding**. The AI can read, search, and explore freely (noetic tools). But it
can't edit, write, or execute (praxic tools) until CHECK passes.

You don't need to manage the Sentinel directly. It works automatically through hooks.
If the AI says "CHECK returned investigate — I need to learn more before proceeding,"
that's the Sentinel doing its job.

### Noetic Artifacts: What Gets Logged

As the AI investigates, it logs structured artifacts automatically:

| Artifact | What it captures | Why it matters |
|----------|-----------------|---------------|
| **Finding** | A discovered fact, with measured impact (0.0–1.0) | Feeds memory, searchable in future sessions |
| **Unknown** | A question that needs answering | Guides investigation, urgency increases with age |
| **Dead-end** | A failed approach, with reason | Prevents re-exploration of paths that don't work |
| **Assumption** | An unverified belief, with confidence level | Tracked until verified or falsified |
| **Decision** | A choice point, with rationale and alternatives | Permanent audit trail of why choices were made |
| **Mistake** | An error, with root cause and prevention strategy | Feeds the cognitive immune system |

**You can trigger these naturally:**
- "I discovered that..." → finding-log
- "I'm not sure about..." → unknown-log
- "That didn't work because..." → deadend-log
- "I'm assuming that..." → assumption-log
- "Let's go with X because..." → decision-log

### A Real Example: Two-Transaction Feature

**You:** "I need to add rate limiting to our API. Can you investigate the current setup and propose an approach?"

**Transaction 1: Research & Design**

The AI runs PREFLIGHT (know: 0.3, uncertainty: 0.6). Reads route handlers, middleware
chain, existing auth setup. Logs findings: "Express middleware, no existing rate limiter,
Redis already in stack." Logs unknown: "Which endpoints need rate limiting?"

**You:** "Just the write endpoints and auth endpoints. Read endpoints are cached anyway."

The AI logs finding, resolves unknown. Proposes plan: 3 goals across 2 transactions.
Logs assumption: "Redis sliding window counter is sufficient" (confidence: 0.7).

**You:** "Good plan. Let's implement the middleware in this transaction."

The AI submits CHECK (know: 0.8, uncertainty: 0.15) → proceeds. Writes rate limiter
middleware, wires it to endpoints, runs tests. Commits. POSTFLIGHT: know +0.5, uncertainty -0.45.

**Transaction 2: Config & Tests** (informed by T1)

The AI starts fresh PREFLIGHT — T1's findings are automatically loaded. Reviews open
work: "Middleware implemented, write endpoints wired. Remaining: integration tests, edge cases."

**You:** "Make sure we test the burst scenario — 100 requests in 1 second."

The AI checks → proceeds. Adds per-endpoint config, writes burst test, discovers edge
case (Redis connection failure leaves endpoints unprotected). Logs finding, adds fallback.
Commits. POSTFLIGHT — closes all goals.

**Behind the scenes:** 5 findings, 2 unknowns (resolved), 1 assumption (verified),
1 decision (Redis sliding window), 3 goals (completed). All searchable in future sessions.

### Between Transactions: Artifact Hygiene

At the start of each new transaction, the AI should clean up:

1. **Complete goals** — Mark finished work with evidence
2. **Resolve unknowns** — Answered questions become findings
3. **Verify assumptions** — Confirmed beliefs become findings; falsified become decisions

**You can prompt this:** "Before we start the next transaction, let's review what's still open."

### Scoping Transactions

| Task size | Example | Transactions |
|-----------|---------|--------------|
| Small | Bug fix, config change | 1 |
| Medium | Feature with 2-3 files | 1-2 |
| Large | Cross-cutting architecture | 2-3 |
| Too large | "Redesign everything" | Split further |

**Signs you need a new transaction:**
- Scope grew beyond what PREFLIGHT declared
- Confidence inflected (know jumped or uncertainty spiked)
- Switching domains or approaches
- Completed a coherent chunk (tests pass, code committed)

---

## Part 3: How It All Fits Together

### The Architecture in 60 Seconds

```
You (natural language) ──► AI (Claude Code) ──► Empirica CLI ──► Storage
       │                        │                     │              │
   Direction              Investigation          Measurement    Persistence
   Context                Implementation         Artifacts      Memory
   Feedback               Logging                Calibration    Git notes
```

**You** bring direction, domain knowledge, and feedback.
**The AI** investigates, implements, and handles all CLI operations.
**Empirica** measures, logs, and persists everything.
**Storage** keeps it all across sessions, compactions, and projects.

### Hooks: Why Everything Is Automatic

Empirica uses Claude Code's hook system to automate the workflow:

| Hook | When it fires | What it does |
|------|--------------|-------------|
| **SessionStart** | Conversation begins | Creates session, loads context, posts breadcrumbs |
| **PreToolUse** | Before any tool call | Sentinel gate — blocks praxic tools until CHECK passes |
| **PreCompact** | Before context compression | Saves state to breadcrumbs so nothing is lost |
| **PostCompact** | After compression | Recovers state + posts orientation context |
| **UserPromptSubmit** | Each user message | Context injection (active goals, artifact reminders) |
| **SessionEnd** | Conversation ends | Cleanup, persist final state |

This means measurement happens without you doing anything. The AI's epistemic state
is tracked from the moment the conversation starts to the moment it ends.

### 4-Layer Storage: Where Everything Lives

| Layer | What | Where | Purpose |
|-------|------|-------|---------|
| **HOT** | Active session state | Memory | Current transaction, live vectors |
| **WARM** | Persistent structured data | SQLite (`.empirica/sessions/sessions.db`) | Sessions, goals, artifacts, calibration |
| **SEARCH** | Semantic retrieval | Qdrant | Cross-session search by meaning |
| **COLD** | Archival + versioned | Git notes | Travels with the code, shared across machines |

**What this means for you:** When you start a new session, the AI can search for
relevant findings from any previous session. When you push code, the git notes travel
with it — another AI working on the same repo can see what was learned.

### Dual-Track Calibration: How Trust Is Earned

Calibration measures whether the AI's beliefs match observable outcomes:

**Track 1 — Learning trajectory (self-referential):** PREFLIGHT →
POSTFLIGHT vector delta. Did the AI's self-assessment change
consistently across transactions? This catches bias patterns ("always
underestimates completion by +0.5").

**Track 2 — Grounded observations:** After POSTFLIGHT, deterministic
services collect measurements — did tests pass? How many files changed?
Was ruff clean? Did goals close with evidence? These observations are
compared to the AI's belief vectors. Divergence is a **discipline
signal**, not a verdict on truth — it points at where work discipline
needs attention (more grounding before CHECK, more commits before
POSTFLIGHT, broader artifact logging).

**Why this matters:** Good calibration → Sentinel loosens gates → AI
gets more autonomy. Worse calibration → tighter gates → more
investigation required before acting.

This is **earned autonomy** — trust through demonstrated accuracy, not
through assertion. Gaming vectors (inflating self-assessment) is
caught by grounded observations and degrades autonomy over time.

**You can check calibration:** "How's your calibration looking?" or "Show me the
calibration report." The AI will run `empirica calibration-report`.

### The Cognitive Immune System

The system learns from mistakes:

- **Findings** act as antigens — new facts that challenge existing beliefs
- **Lessons** act as antibodies — procedural knowledge with confidence that decays
  when contradicted by new findings
- **Dead-ends** prevent re-exploration — once an approach fails, it's never retried

This means the AI gets better over time within a project. Mistakes have prevention
strategies. Failed approaches are remembered. Patterns are recognized across sessions.

### Multi-Agent Coordination

For complex problems, the AI can spawn specialist subagents:

**You:** "This bug could be in the auth layer, the database, or the API. Can you investigate all three?"

The AI spawns parallel investigation agents — each explores one angle. Results are
consolidated, findings logged, and the noetic phase completes faster. Subagent work
counts toward the parent transaction's tool budget.

**Key patterns:**
- **Parallel investigation** — multiple agents explore different angles simultaneously
- **Sequential handoff** — one agent's findings feed into the next agent's starting context
- **Specialist delegation** — domain-specific agents with focused expertise

### Context Recovery Across Compactions

When Claude Code compresses context (compaction), Empirica recovers automatically:

1. **PreCompact hook** fires — saves current state to git notes
2. Context is compressed — most conversation history is lost
3. **SessionStart hook** fires on resume — loads project context, findings, goals, calibration
4. The AI picks up where it left off, informed by everything it learned before compaction

**You don't manage this.** It just works. The AI will say something like "Resuming from
previous context — I see 3 open goals and 5 findings from before compaction."

### The EWM Protocol: Personalizing the Workflow

The Epistemic Workflow Management (EWM) protocol personalizes how Empirica works for you:

**You:** `/ewm-interview`

The AI interviews you about your goals, domains, tools, work preferences, and trust
boundaries. It generates a `workflow-protocol.yaml` that configures:
- Your autonomy level (how much the AI can do without checking in)
- Your pushback style (direct, diplomatic, etc.)
- What the AI can do autonomously vs. what needs a checkpoint
- Your domain expertise (so the AI calibrates accordingly)
- Non-negotiable rules

---

## Quick Reference

### Natural Language → What Happens

| You say | The AI does |
|---------|------------|
| "Fix the auth bug" | Creates goal, PREFLIGHT, investigates, CHECK, implements, POSTFLIGHT |
| "Let's investigate first" | Stays noetic — reads, searches, logs findings |
| "I think we're ready" | Submits CHECK to Sentinel |
| "Good, implement it" | Transitions to praxic phase |
| "Commit and close" | Commits code, runs POSTFLIGHT |
| "What don't we know?" | Surfaces open unknowns from project memory |
| "That won't work" | Logs dead-end with reason |
| "How confident are you?" | Reports vectors and calibration |
| "Break this into goals" | Decomposes task, creates goal hierarchy |
| "Next transaction" | Cleans up artifacts, opens new PREFLIGHT |

### Knowledge Assessment (What the Vectors Mean)

| You observe | What it indicates |
|------------|------------------|
| "I understand this well" | know: 0.8+ |
| "I'm somewhat familiar" | know: 0.5–0.7 |
| "I'm new to this" | know: 0.3–0.5 |
| "I'm very confident" | uncertainty: <0.2 |
| "I'm somewhat unsure" | uncertainty: 0.3–0.5 |
| "I'm very uncertain" | uncertainty: 0.6+ |

### The 13 Epistemic Vectors

| Category | Vectors | What they measure |
|----------|---------|------------------|
| **Foundation** | know, do, context | Understanding, capability, situational awareness |
| **Comprehension** | clarity, coherence, signal, density | How well the AI understands what it's looking at |
| **Execution** | state, change, completion, impact | Progress and effect of work |
| **Meta** | engagement, uncertainty | Motivation and self-assessed confidence |

### Common Workflow Patterns

**Quick fix (1 transaction):**
> "Fix the typo in the README" → investigate → CHECK → fix → POSTFLIGHT

**Feature (2-3 transactions):**
> "Add OAuth2 authentication" → T1: research + design → T2: implement + test → T3: edge cases

**Investigation (1 noetic transaction):**
> "Research how the payment system works" → deep read → findings logged → POSTFLIGHT (no praxic phase needed)

**Spec-driven (planned transactions):**
> "Here's the spec. Break it into goals and work through them." → AI decomposes → sequential transactions

---

## Tips for Effective Collaboration

1. **Describe outcomes, not commands.** Say "fix the auth bug" not "run empirica preflight-submit."

2. **Let the AI investigate first.** The best results come from investigation before action.
   If you're impatient, say "I think we know enough" — the AI will CHECK and proceed if ready.

3. **Guide with questions.** "What do we still not know?" and "How confident are you?" drive
   the workflow more effectively than giving step-by-step instructions.

4. **Review transaction boundaries.** When the AI suggests closing a transaction, check: is this
   a coherent chunk? Did we learn what we set out to learn? If yes, close it.

5. **Trust the calibration.** If the AI says "I need to investigate more," it's usually right.
   The Sentinel is calibrated from hundreds of past transactions.

6. **Use `/epistemic-transaction` for complex work.** This skill guides the AI through the
   full P1-P5 planning procedure for multi-transaction tasks.

7. **Be honest about what you know.** The AI calibrates to your domain expertise. If you're
   an expert in auth but new to databases, say so — the investigation will focus accordingly.
