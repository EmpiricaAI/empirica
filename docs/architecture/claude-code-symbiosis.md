# Empirica + Claude Code: Epistemic Infrastructure, Not a Wrapper

## The Misconception

"Just another wrapper for agents."

This document explains why that framing is wrong and what Empirica actually does
when integrated with Claude Code.

## What Claude Code Does Well

Claude Code is an excellent action layer:

- **Tasks**: Create, track, and complete work items with dependencies
- **Plans**: Read-only permission gating for investigation before action
- **Memory**: Per-project MEMORY.md (200 lines auto-loaded) + topic files
- **Sessions**: Conversation persistence, compaction, checkpointing
- **Subagents**: Parallel execution with model selection and turn limits
- **Worktrees**: Isolated git branches for concurrent work

These are capabilities Empirica does not replicate.

## What Claude Code Cannot Do

Claude Code has no self-awareness infrastructure:

| Gap | Impact |
|-----|--------|
| No epistemic vectors | Cannot measure what it knows vs. doesn't know |
| No calibration | Cannot detect when it's overconfident or underconfident |
| No uncertainty quantification | Cannot distinguish high-confidence facts from guesses |
| No grounded verification | Cannot compare beliefs against deterministic service observations |
| No dead-end memory | Cannot prevent re-exploring failed approaches |
| No way to retract a belief | Cannot mark what it once wrote as wrong, or keep a refutation test open |
| No transaction measurement | Cannot capture learning deltas across work sessions |

Claude Code's memory is flat text. Every fact has equal weight. There is no
confidence scoring, no recency decay, no impact ranking, no truth-tracking.

## What Empirica Adds

Empirica is the **measurement layer** that makes Claude Code epistemically aware.

### 1. Epistemic Vectors (13 dimensions)

Every work unit is measured across 13 vectors:

```
Foundation:     know, do, context
Comprehension:  clarity, coherence, signal, density
Execution:      state, change, completion, impact
Meta:           engagement, uncertainty
```

These enable the PREFLIGHT -> CHECK -> POSTFLIGHT cycle that captures learning
deltas. Without measurement, compaction destroys context permanently.

### 2. Dual-Track Calibration

- **Track 1 (self-referential)**: How vectors change from PREFLIGHT to POSTFLIGHT
- **Track 2 (grounded)**: POSTFLIGHT belief vectors compared against service observations
  (test results, git metrics, goal completion, artifact counts)

When Track 1 and Track 2 disagree, Track 2 is more trustworthy. This prevents
the AI from gaming its own assessment.

### 3. Noetic RAG (Epistemically-Weighted Retrieval)

Not just "search memory" but retrieval weighted by epistemic quality:

```
weight = impact x type_confidence x (0.65 x relevance + 0.35 x recency)

where:
  type_confidence = finding(0.9) > dead_end(0.85) = mistake(0.85) > subtask(0.80) > goal(0.75) > unknown(0.6)
  relevance       = semantic match to the current task (Qdrant), 0 to 1
  recency         = exp(-0.029 x age_hours)  # 24-hour half-life
```

Relevance is added, not multiplied: a dead-end from months ago about exactly this task still surfaces, while old
artifacts about something else stop crowding out today's. With no task to match, or no Qdrant, the weight falls back to
`impact x type_confidence x recency`, and the output says so. Dead-ends are surfaced to prevent re-exploration.

### 4. The Sentinel (Epistemic Gating)

Claude Code's plan mode is a binary permission gate: read-only or read-write.

Empirica's Sentinel gates on *epistemic readiness*: have you said what the next change rests on? It is a discipline
layer, not a security boundary. Reading is always free; a change waits until the transaction is certified, by the
grounded claims declared at PREFLIGHT or by a CHECK, which returns `proceed` or `investigate` from the vectors and the
calibration history. Outside a transaction, a small budget of project-local edits runs and is recorded.

### 5. Project Awareness

Claude Code is single-project, cwd-bound. Empirica provides:

- `project-bootstrap`: Load full project context (breadcrumbs, goals, vectors)
- `project-switch`: Change project without restarting the session
- `project-search`: Semantic search across project history via Qdrant
- Multi-instance isolation: Multiple Claude Code instances on different projects
  don't interfere with each other

## The Memory Bridge

This is where the symbiosis becomes concrete.

### How It Works

```
Session start, and again after every compaction
  |
  v
The hook builds the project's bootstrap picture from SQLite
  (goals, findings, unknowns, decisions, assumptions, mistakes, dead ends, links)
  |
  v
Each item with an 8-character id, taking turns until a token budget is spent,
with a coverage line per category: shown out of how many, or why none
  |
  v
Injected into the conversation as hook context
  |
  v
Agent starts with the project's current epistemic state, not raw history
```

The picture is rebuilt from the database each time, so it is never staler than the last artifact logged, and a
retracted or resolved artifact leaves it at once. `project-search` reaches the rest of the history on demand.

Claude Code's own `MEMORY.md` is left to Claude Code. The user and feedback memories it keeps there are guidance about
how to work, which the epistemic graph does not hold. Until 1.16.3 Empirica also wrote a ranked block into that file at
POSTFLIGHT; it duplicated the session-start picture with no ids and no way to retract an entry, and `setup-claude-code`
now removes it.

### What This Means

- High-impact findings, open unknowns and the latest decisions are in front of the agent from its first turn
- Dead-ends and mistakes, with their prevention, are surfaced to prevent re-exploration
- What was retracted or resolved does not come back as if it were current
- Claude Code's own memory stays the agent's and the user's

Empirica doesn't replace MEMORY.md. It works beside it.

## Swarm Learning (Emergent Property)

Multiple Claude Code instances on the same project share one project database, and each one's session start reads
it. This creates emergent swarm learning:

```
Agent A: discovers dead-end -> logs it -> it is in the project database at once
Agent B: starts -> its session-start picture includes A's dead-end -> avoids it -> finds solution
Agent B: logs the finding
Agent C: starts -> gets the combined epistemic state of A + B
```

Properties:
- **No explicit coordination required** - agents share via the project's artifacts
- **Ranked** - by impact, type and recency, within a token budget
- **Project-isolated** - scoped by project_id, no cross-project bleeding
- **Correctable** - a retracted or resolved artifact leaves every agent's picture
- **Cumulative** - knowledge accumulates across agents and sessions

The Qdrant layer adds depth: the session-start picture is what fits the budget,
`project-search` gives semantic access to the full history.

## Correcting Shared Memory

Shared memory corrects itself through explicit acts, not keyword matching:

1. **Retraction.** A finding shown false is resolved with `finding-resolve --kind retracted`. It keeps its wording and
   leaves live retrieval, and the graph records that it was wrong, not merely old (`stale`) or replaced (`superseded`).
2. **Falsifiers.** A belief an agent acts on can carry the observation that would refute it. The falsifier outlives the
   transaction and is raised at every later PREFLIGHT, in any agent's session, until someone adjudicates it.
3. **Expiry.** An assumption nobody checked before its subject went away closes as `expired`, counted as neither
   confirmation nor error.

An earlier design lowered a lesson's confidence whenever a new finding shared keywords with it. It is retired: keyword
overlap could not tell agreement from contradiction, so findings that confirmed a lesson eroded it.

## Integration Architecture

```
Claude Code (Action Layer)          Empirica (Measurement Layer)
========================          ============================
Tasks / TaskCreate          <-->   Goals / goals-create
  |                                  |
  | TaskCompleted hook               | Auto-complete matching goal
  v                                  v
Plan Mode                   <-->   Noetic Phase
  |                                  |
  | Permission gate                  | Epistemic gate (Sentinel)
  v                                  v
Session-start context       <--   SQLite (+ Qdrant search)
  |                                  |
  | Injected per session             | Rebuilt at session start and compaction
  v                                  v
Compaction (silent)         -->    POSTFLIGHT (measured)
  |                                  |
  | Context lost                     | Learning delta captured
  v                                  v
Subagents                   <-->   Subagent governance
  |                                  |
  | maxTurns, model selection        | Budget checks, delegated counting
```

## What Empirica Is

Empirica is cognitive infrastructure for AI agents. It does not wrap Claude Code.
It does not replace Claude Code. It instruments Claude Code with:

- **Self-awareness**: Epistemic vectors that measure what the agent knows
- **Calibration**: Dual-track system that catches overconfidence
- **Memory intelligence**: Confidence-weighted retrieval, not flat text
- **Swarm learning**: Shared epistemic state across agent instances
- **Measurement**: Transaction boundaries that capture learning deltas

The relationship is symbiotic. Claude Code is better with Empirica because it
knows what it knows. Empirica needs Claude Code because it provides the action
layer. Neither is a wrapper for the other.

All roads lead back to Empirica. Plugins in, knowledge out.
