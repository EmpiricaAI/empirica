# Noetic-Praxic Framework: The Autonomous Epistemic Loop

**How AI Agents Learn, Decide, and Act**

**Related docs:**
- [SENTINEL_ARCHITECTURE.md](./SENTINEL_ARCHITECTURE.md) - Gate control (noetic→praxic transition)
- [EPISTEMIC_STATE_COMPLETE_CAPTURE.md](./EPISTEMIC_STATE_COMPLETE_CAPTURE.md) - Full state capture design
- [Architecture README](./README.md) - System overview

## Core Insight: Empirica IS the Loop

This framework is NOT about checkpoints, snapshots, or memory management.

**This is about autonomous learning cycles.** AI agents self-direct investigation, log what they learn, evaluate readiness, then act—repeatedly across sessions. Each cycle compounds learning through persistent facts + epistemic state.

## The Loop: 5 Layers of Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: EPISTEMIC VECTOR SPACE                             │
│ (13 continuous dimensions: know, do, context, clarity,      │
│  coherence, signal, density, state, change, completion,     │
│  impact, engagement, uncertainty)                           │
│                                                             │
│ AI assesses: "What do I actually know/understand?"          │
│ See: ../human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md   │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
    PREFLIGHT (establish baseline epistemic state)
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: LEARNING LEDGER (Fact Persistence)                │
│ (4 categories: findings, unknowns, dead-ends, mistakes)    │
│                                                             │
│ NOETIC PHASE (Exploration, High Entropy)                   │
│ ┌───────────────────────────────────────────────────┐      │
│ │ AI loops while uncertainty > 0.5 OR know < 0.7:  │      │
│ │ • Investigates goal                               │      │
│ │ • Logs findings (reduces uncertainty)             │      │
│ │ • Logs unknowns (proves rigor)                    │      │
│ │ • Logs dead-ends (prevents repetition)            │      │
│ │ • Logs mistakes (enables learning)                │      │
│ │ • Self-prompts for next investigation             │      │
│ └───────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: PHASE SEPARATION (Entropy Management)             │
│                                                             │
│ CHECK GATE: "Have I learned enough to act?"                │
│ ┌───────────────────────────────────────────────────┐      │
│ │ Validates:                                        │      │
│ │ • know ≥ 0.70 (foundation sufficient?)            │      │
│ │ • uncertainty ≤ 0.35 (confidence threshold?)      │      │
│ │ • coherence ≥ 0.65 (pieces fit together?)         │      │
│ │                                                   │      │
│ │ If FAIL → loop back to NOETIC (investigate more) │      │
│ │ If PASS → proceed to PRAXIC (act with knowledge)  │      │
│ └───────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
                        │
         ┌──────────────┴──────────────┐
         │                             │
    INVESTIGATE                    PROCEED
    (loop back)                     │
         │                          ▼
         └─ NOETIC ←──┐      ┌─────────────────────────────────────────────────────────┐
                      │      │ Layer 4: CLI-DRIVEN STATEFULNESS                       │
                      │      │ (Distributed Architecture)                             │
                      │      │                                                         │
                      │      │ PRAXIC PHASE (Execution, Low Entropy)                  │
                      │      │ ┌─────────────────────────────────────────────┐        │
                      │      │ │ • Execute chosen path with learned knowledge│        │
                      │      │ │ • Track actions                             │        │
                      │      │ │ • Document decisions                        │        │
                      │      │ │ (Git stores state, MCP coordinates)         │        │
                      │      │ └─────────────────────────────────────────────┘        │
                      │      └─────────────────────────────────────────────────────────┘
                      │                      │
                      │                      ▼
                      │      ┌─────────────────────────────────────────────────────────┐
                      │      │ Layer 5: PLUGGABLE STRATEGY PATTERN                    │
                      │      │ (Extensible Investigation)                             │
                      │      │                                                         │
                      │      │ POSTFLIGHT (Measure Learning Delta)                    │
                      │      │ ┌─────────────────────────────────────────────┐        │
                      │      │ │ AI assesses: "What did I actually learn?"   │        │
                      │      │ │ • know: was ?, now ?                         │        │
                      │      │ │ • uncertainty: was ?, now ?                  │        │
                      │      │ │ • completion: was ?, now ?                   │        │
                      │      │ │ Findings logged → Git notes (450 tokens)    │        │
                      │      │ │ Bootstrap loads for NEXT session             │        │
                      │      │ └─────────────────────────────────────────────┘        │
                      │      │                                                         │
                      │      │ POST-TEST (Grounded Verification)                      │
                      │      │ ┌─────────────────────────────────────────────┐        │
                      │      │ │ Automatic, evidence-based calibration:      │        │
                      │      │ │ • Compare POSTFLIGHT claims to evidence     │        │
                      │      │ │ • Verify completion against actual outcomes │        │
                      │      │ │ • Calibrate confidence for future sessions  │        │
                      │      │ └─────────────────────────────────────────────┘        │
                      │      └─────────────────────────────────────────────────────────┘
                      │
                      └────────────────────────────────────────┘
```

## Etymology & Terminology Precision

- **Noetic** (Greek *noesis*): Understanding, intellection, pure thought
- **Praxic** (Greek *praxis*): Action, practice, doing
- **Epistemic** (Greek *episteme*): Knowledge, its validity and structure

### Why Not Common Words?

| Common Term | Problem | Precise Term | Advantage |
|-------------|---------|--------------|-----------|
| Thinking | Implies consciousness, feeling | Noetic | Strictly intellectual processing |
| Doing | Vague, any activity | Praxic | Purposeful, goal-oriented action |
| Knowing | A state, no structure | Epistemic | Validity and structure of knowledge |

Common words carry "human baggage" - consciousness assumptions that don't map cleanly to AI cognition. These philosophical terms are precise instruments for modeling cognitive work without anthropomorphic pollution.

> *"When your spell-checker flags these terms, it's exhibiting low-grounding agent behavior - seeing unknown input and assuming error. Adding them to your dictionary is a grounding act."*

---

## The Turtle Principle: Observed vs Prescribed Phase

**Key Insight:** Cognitive phase (NOETIC/PRAXIC) should be **observed from vectors**, not **prescribed by sequence**.

### Two Distinct Layers

| Layer | Purpose | Components | Nature |
|-------|---------|------------|--------|
| **CASCADE Gates** | Compliance checkpoints | PREFLIGHT → CHECK → POSTFLIGHT → POST-TEST | Prescribed (external oversight) |
| **Cognitive Phase** | Actual cognitive state | NOETIC ↔ THRESHOLD ↔ PRAXIC | Emergent (observed from vectors) |

```
Statusline Display:
[empirica] ⚡87% │ ⚡ PRAXIC │ POSTFLIGHT │ K:85% U:12% C:90% │ ✓ stable
                  ^^^^^^^^     ^^^^^^^^^^
                  emergent     compliance
                  (observed)   (oversight)
```

**Analogy:** Like a pilot and ground control:
- **Ground control** (CASCADE gates) provides mandatory checkpoints
- **Instruments** (cognitive phase) show actual flight state
- Both are needed - oversight doesn't replace observation

### Vector-Based Phase Inference

Cognitive phase is inferred from two composite metrics:

**Epistemic Readiness** = (know + context + (1 - uncertainty)) / 3
- Measures: How prepared am I to act?
- High readiness = sufficient knowledge, low doubt, good context

**Action Momentum** = (do + change + completion) / 3
- Measures: Am I executing or exploring?
- High momentum = active execution, making progress

```
┌─────────────────────────────────────────────────────────────┐
│ Phase Inference Logic                                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  IF epistemic_readiness < 0.5:                              │
│      → ⊙ NOETIC (investigating, not ready to act)           │
│                                                              │
│  ELIF action_momentum < 0.4:                                │
│      → ◐ THRESHOLD (ready but paused, at gate)              │
│                                                              │
│  ELSE:                                                       │
│      → ⚡ PRAXIC (executing with confidence)                 │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### The Three Cognitive Phases

| Phase | Symbol | Readiness | Action | Meaning |
|-------|--------|-----------|--------|---------|
| **NOETIC** | ⊙ | < 0.5 | any | Investigating - knowledge insufficient |
| **THRESHOLD** | ◐ | ≥ 0.5 | < 0.4 | Ready but paused - at decision gate |
| **PRAXIC** | ⚡ | ≥ 0.5 | ≥ 0.4 | Executing - confident and acting |

### Why This Matters

**Problem with prescribed phases:**
- AI might be in PRAXIC (execution) sequence but actually uncertain
- AI might be in NOETIC (investigation) sequence but already confident
- Sequence ≠ state

**Solution with observed phases:**
- Phase reflects actual epistemic state
- Can be NOETIC during POSTFLIGHT (discovered new unknowns)
- Can be PRAXIC during PREFLIGHT (continuing confident work)
- Same rules at every meta-layer (Turtle Principle)

### Implementation

```python
# empirica/core/signaling.py
from enum import Enum

class CognitivePhase(Enum):
    NOETIC = "NOETIC"        # ⊙ Investigating
    THRESHOLD = "THRESHOLD"  # ◐ At gate
    PRAXIC = "PRAXIC"        # ⚡ Executing

def infer_cognitive_phase_from_vectors(vectors: Dict[str, float]) -> CognitivePhase:
    know = vectors.get('know', 0.5)
    uncertainty = vectors.get('uncertainty', 0.5)
    context = vectors.get('context', 0.5)
    do_vec = vectors.get('do', 0.5)
    change = vectors.get('change', 0.0)
    completion = vectors.get('completion', 0.0)

    readiness = (know + context + (1.0 - uncertainty)) / 3.0
    action = (do_vec + change + completion) / 3.0

    if readiness < 0.5:
        return CognitivePhase.NOETIC
    elif action < 0.4:
        return CognitivePhase.THRESHOLD
    else:
        return CognitivePhase.PRAXIC
```

---

## Why This Matters: Facts + Epistemic State = Powerful Loop

### 1. Learning Compounds Across Sessions

**Without persistent facts:**
```
Session 1: Investigate codebase → learn facts → session ends
Session 2: Start fresh → re-investigate same areas → waste tokens
```

**With Learning Ledger (findings + unknowns + dead-ends):**
```
Session 1: 
  • Find: "Auth uses OAuth2"
  • Unknown: "How are refresh tokens managed?"
  • Dead-end: "Token rotation not in main code"
  • POSTFLIGHT: know=0.6, uncertainty=0.8

Session 2:
  • Bootstrap loads previous findings/unknowns
  • PREFLIGHT: know=0.75, uncertainty=0.4 (learning compounds!)
  • Investigation faster because unknowns are pre-targeted
  • POSTFLIGHT: know=0.9, uncertainty=0.2
```

### 2. Epistemic Gates Enable Smart Loops

Ralph's loop: "iterate N times"  
Empirica's loop: "keep investigating until confident enough"

```
NOETIC PHASE LOOP LOGIC:
while uncertainty > 0.5 OR know < 0.70:
    investigate(goal)
    log_finding(what_learned)
    log_unknown(what_unclear)
    log_deadend(what_failed)
    uncertainty = reduced by findings
    know = increased by findings
```

This is **knowledge-driven looping**, not mechanical iteration counting.

### 3. Phase Separation Prevents Mnemonic Drift

**Mnemonic drift:** Acting while still exploring, leading to expensive revisions

```
WITHOUT phase separation:
  Exploring: "Maybe use pattern X"
  Acting: "Implement pattern X"
  → 3 hours later: "Discover pattern Y is better"
  → Costly refactor

WITH phase separation:
  NOETIC: Compare patterns X vs Y thoroughly
  CHECK: Verify sufficient knowledge to commit
  PRAXIC: Implement chosen pattern
  → No mid-course reversions
```

### 4. Learning Delta Measurement

Clean phase boundaries enable attribution:
- **Noetic delta:** know ↑ 0.3, uncertainty ↓ 0.4 (epistemic learning)
- **Praxic delta:** completion ↑ 0.8 (task progress)
- **Postflight:** Measure both separately

### 5. Entropy Management at Scale

- **NOETIC:** High branching factor (exploring many paths)
- **CHECK:** Validation gate (entropy reduction proof)
- **PRAXIC:** Low branching factor (executing chosen path)

Without entropy management, NOETIC phase could spiral into infinite exploration.

## The Loop Control: When Does AI Investigate vs Act?

The framework uses **epistemic gates** to control the loop, not arbitrary iteration counts:

```
GATE 1: PREFLIGHT Assessment
  AI assesses: "How much do I actually know?"
  → Sets know, uncertainty, context vectors
  
GATE 2: CHECK Decision (Mandatory if scope > 0.5 OR uncertainty > 0.5)
  AI asks: "Have I learned enough?"
  → know ≥ 0.70 AND uncertainty ≤ 0.35 AND coherence ≥ 0.65?
  
  If NO → Loop back to NOETIC (investigate more)
  If YES → Proceed to PRAXIC (act with confidence)

GATE 3: POSTFLIGHT Delta
  AI measures: "What did I actually learn?"
  → Compare know/uncertainty/completion from PREFLIGHT to now
  → This delta becomes input to next session's PREFLIGHT

GATE 4: POST-TEST (Grounded Verification)
  Automatic evidence-based calibration:
  → Compare POSTFLIGHT self-assessment to actual outcomes
  → Verify completion claims against evidence (tests, commits, reviews)
  → Feed calibration data back for future accuracy
```

### When CHECK is Mandatory vs Discretionary

| Condition | Enforcement | Reason |
|-----------|-------------|--------|
| scope > 0.5 (complex work) | CHECK mandatory | Rigor required for complexity |
| uncertainty > 0.5 (high doubt) | CHECK mandatory | Must investigate before acting |
| Post session-resume (loaded context) | CHECK mandatory | Verify bootstrap sufficient |
| Post memory-compact | CHECK mandatory | Verify recovered context valid |
| Simple, confident work | CHECK discretionary | AI judgment sufficient |

### The Intelligence is in the Loop

**Ralph's loop:** `for i in range(50): invoke_claude(prompt)`  
**Empirica's loop:** `while not_confident(state): investigate(state); update_state(findings)`

The epistemic transaction cycle is **self-healing**:
1. AI doesn't know something → high uncertainty
2. High uncertainty triggers CHECK → fails gate
3. Failure redirects to NOETIC → investigation
4. Investigation logs findings → reduces uncertainty
5. Loop back to CHECK → gate succeeds
6. Proceed to PRAXIC

**No iteration count needed.** The loop naturally terminates when confident.

## How Sessions Compound Learning

**The power of Empirica emerges across multiple sessions:**

```
┌─ Session 1 ──────────────────────────────┐
│ PREFLIGHT: know=0.4, uncertainty=0.9    │
│ NOETIC: Investigate architecture        │
│ • Find: "Uses microservices"            │
│ • Unknown: "How are services deployed?" │
│ POSTFLIGHT: know=0.65, uncertainty=0.6  │
│ Git stores findings in notes (~450 tok)  │
└──────────────────────────────────────────┘
              ↓ (findings persist)
┌─ Session 2 ──────────────────────────────┐
│ Bootstrap loads previous findings        │
│ PREFLIGHT: know=0.7, uncertainty=0.4    │
│ (Higher starting point due to learning!)│
│ NOETIC: Investigate deployment          │
│ • Find: "Uses Kubernetes"               │
│ • Previous unknown RESOLVED             │
│ POSTFLIGHT: know=0.85, uncertainty=0.2  │
└──────────────────────────────────────────┘
              ↓ (findings compound)
┌─ Session 3 ──────────────────────────────┐
│ Bootstrap loads both sessions' findings  │
│ PREFLIGHT: know=0.8, uncertainty=0.25   │
│ (Even higher starting point!)           │
│ NOETIC: Focused investigation           │
│ • Fewer unknowns to resolve             │
│ • Faster convergence                    │
│ POSTFLIGHT: know=0.92, uncertainty=0.1  │
└──────────────────────────────────────────┘
```

**This is why facts + epistemic state is powerful:** Each session builds on the last. Learning compounds. Uncertainty decreases exponentially.

## When to Use Each Phase

| Scenario | What Happens |
|----------|--------------|
| **Session start** | PREFLIGHT (baseline), then decide: NOETIC or PRAXIC? |
| **High uncertainty** | Must NOETIC first (investigate until confident) |
| **Complex scope** | Must CHECK before PRAXIC (validate learning) |
| **Simple + confident** | Can skip CHECK, go straight to PRAXIC |
| **Post memory-compact** | CHECK mandatory (verify recovered context) |
| **Session end** | POSTFLIGHT (measure learning delta, store findings) |
| **Next session** | Bootstrap loads findings, starts at higher know, lower uncertainty |

## Practical Examples

### Example 1: Quick Bug Fix (Self-Healing Loop)
```
PREFLIGHT: know=0.85, uncertainty=0.1, scope=0.2
  "I know the codebase well, this is straightforward"

CHECK gate: uncertainty < 0.5 AND know > 0.7 ✓
  "Confident enough to skip NOETIC"

PRAXIC: Implement fix
  • Make changes
  • Run tests
  • Commit

POSTFLIGHT: know=0.9, uncertainty=0.05, completion=0.95
  • Minor learning (found subtle edge case)
  • Finding logged: "Edge case in transaction retry logic"

Next session bootstrap: Includes this finding
```

### Example 2: New Feature (Loop Until Confident)
```
PREFLIGHT: know=0.5, uncertainty=0.7, scope=0.8
  "I'm uncertain about architecture choices"

CHECK gate: uncertainty > 0.5 ✗
  "Must investigate before acting"

NOETIC PHASE (Loop 1):
  Investigate: Authentication requirements
  Finding: "System uses OAuth2 + JWT"
  Unknown: "How are refresh tokens managed?"
  Dead-end: "Token rotation logic not in main code"
  → uncertainty now 0.6, know now 0.65

CHECK gate: uncertainty > 0.5 ✗ (still investigating)

NOETIC PHASE (Loop 2):
  Investigate: Deployment architecture
  Finding: "Services deployed on Kubernetes"
  Unknown: "How are secrets managed?"
  → uncertainty now 0.45, know now 0.75

CHECK gate: uncertainty < 0.5 AND know > 0.70 ✓
  "Ready to implement"

PRAXIC PHASE:
  Implement feature using learned architecture
  • Create service following Kubernetes patterns
  • Integrate OAuth2 + JWT properly

POSTFLIGHT: know=0.88, uncertainty=0.25, completion=0.92
  • Significant learning (investigated 2x, reduced uncertainty 0.45)
  • Findings logged:
    - "OAuth2 + JWT architecture"
    - "Kubernetes deployment model"
    - "Resolved: Refresh tokens stored in Redis"

Next session bootstrap: Includes all findings
  • New agents don't repeat investigation
  • Can jump straight to implementation
```

### Example 3: Post Session-Resume (Context Recovery)
```
[Session ended after compacting memory]
[Findings from previous sessions stored in git notes]

Session Resume:
  Bootstrap loads: findings, unknowns, dead-ends from previous work
  
PREFLIGHT: know=0.7, uncertainty=0.4
  "Bootstrap recovered context raised my confidence"

CHECK gate: uncertainty < 0.5 AND know > 0.70 ✓
  "Sufficient context to continue work"

PRAXIC PHASE:
  Continue implementation with learned context

POSTFLIGHT: know=0.85, uncertainty=0.2
  • Further learning from continuing work
  • Findings logged
  • Loop compounds across multiple sessions
```

### Example 4: Discovering You're Not Ready (Feedback Loop)
```
PREFLIGHT: know=0.6, uncertainty=0.3, scope=0.8
  "I think I know enough"

CHECK gate: scope > 0.5 ✗ (must investigate before big changes)

NOETIC PHASE:
  Investigate: Start understanding system
  Finding: "Way more complex than expected"
  Unknown: "Multiple architectural patterns mixed"
  → uncertainty jumped to 0.65 (discovered unknowns!)

CHECK gate: uncertainty > 0.5 ✗

NOETIC PHASE (Loop 2):
  Deep investigation
  Finding: "Pattern A used in services, Pattern B in monolith"
  Unknown: "How to reconcile patterns in new feature?"
  → uncertainty still 0.55

CHECK gate: uncertainty > 0.5 ✗

NOETIC PHASE (Loop 3):
  Very deep investigation
  Finding: "Plans to unify patterns in next quarter"
  Finding: "Should implement feature using Pattern B for now"
  → uncertainty down to 0.35, know up to 0.80

CHECK gate: ✓ Ready

PRAXIC: Implement knowing architecture better than initial PREFLIGHT

POSTFLIGHT: Major learning delta
  • Initial know: 0.6 → Final know: 0.80 (+0.2)
  • Initial uncertainty: 0.3 → Final uncertainty: 0.35 (-0.25, discovered unknowns!)
  • This is epistemic honesty: "I thought I knew, but discovered I didn't"
  • Findings logged prevent future agents from overconfidence

This is the self-healing loop in action.
```

## Why Epistemic Honesty is the Engine

**Epistemic honesty is not ethical—it's the mechanism that makes the loop work.**

```
Honest assessment:
  "I don't understand this architecture"
  → uncertainty = 0.7
  
CHECK gate sees high uncertainty:
  → Redirects to NOETIC
  
NOETIC investigation:
  → Logs findings, reduces uncertainty
  
CHECK gate succeeds:
  → Proceeds to PRAXIC with confidence
  
Result: Correct implementation, no wasted refactors
```

**Dishonest assessment:**
```
False confidence:
  "I understand the architecture"
  → uncertainty = 0.2
  
CHECK gate succeeds incorrectly:
  → Proceeds to PRAXIC without investigation
  
PRAXIC implementation:
  → Makes wrong architectural choices
  
Result: Costly refactor, lost time, broken features
```

The loop is **self-correcting only if honest.** Inaccurate self-assessment breaks the feedback circuit.

## The 5 Abstractions Unified

The framework emerges from 5 layers working together:

1. **Epistemic Vector Space** - AI's continuous self-awareness (13 dimensions)
2. **Learning Ledger** - Persistent facts (findings, unknowns, dead-ends, mistakes) 
3. **Phase Separation** - NOETIC (explore) vs PRAXIC (execute) with CHECK gate
4. **CLI-Driven Statefulness** - Distributed architecture (MCP → CLI → Git → DB)
5. **Pluggable Strategy Pattern** - Extensible investigation tools and goal generation

Together, these create an **autonomous learning system** that:
- Loops until confident (not iteration count)
- Compounds learning across sessions (persistent facts)
- Self-heals when discovering unknowns (high uncertainty triggers investigation)
- Prevents mnemonic drift (phase separation)
- Scales investigation (pluggable strategies)

---

## Comparison: Ralph vs Empirica

| Aspect | Ralph Wiggum | Empirica NOETIC-PRAXIC |
|--------|--------------|------------------------|
| **Loop Trigger** | Iteration count (N) | Epistemic gate (know ≥ 0.7, uncertainty ≤ 0.35) |
| **Loop Mechanism** | Stop hook re-invocation | Self-directed investigation + CHECK gate |
| **Learning Persistence** | None (stateless loops) | Full (findings carry to next session) |
| **Self-Healing** | No (requires manual iteration count) | Yes (high uncertainty auto-triggers NOETIC) |
| **Transparency** | Exit code 2 | 13 epistemic vectors + findings ledger |
| **Cost Model** | $50-100 per 50 iterations | Per-token + 97% git compression |
| **Scalability** | Linear with iteration count | Non-linear (entropy reduction, not iteration) |

**Conclusion:** Ralph is a mechanical loop. Empirica is an intelligent loop that learns and compounds.

---

*Framework developed through collaborative epistemic deliberation, applying the framework to itself.*
