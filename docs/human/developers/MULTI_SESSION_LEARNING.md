# Multi-Session Learning: Compounding Knowledge Across Sessions

**Empirica's superpower:** Learning doesn't reset between sessions. Facts persist, epistemic state improves, agents become smarter over time.

---

## Core Concept: Facts + Epistemic State = Exponential Learning

Most systems treat each session as independent. Empirica treats each session as a **continuation**.

```
Session 1, Transaction 1 (Start)
  PREFLIGHT: know=0.4, uncertainty=0.9
  ↓ (investigate, log findings)
  POSTFLIGHT: know=0.65, uncertainty=0.6
  → Findings stored with transaction_id linkage (~450 tokens)

Session 2, Transaction 2 (Bootstrap)
  Load findings from Transaction 1
  PREFLIGHT: know=0.7, uncertainty=0.4  ← Higher starting point!
  ↓ (investigate, fewer gaps to fill)
  POSTFLIGHT: know=0.85, uncertainty=0.2  ← Faster convergence

Session 3, Transaction 3 (Accelerated)
  Load findings from Transactions 1 + 2
  PREFLIGHT: know=0.8, uncertainty=0.25  ← Even higher!
  ↓ (investigate, targeted deep-dives)
  POSTFLIGHT: know=0.92, uncertainty=0.1
```

**The pattern:** Learning compounds exponentially. Transaction N builds on Transactions 1...N-1.

**Triple linkage:** Every noetic artifact (finding, unknown, dead-end) carries:
- `session_id` — temporal context (which context window)
- `goal_id` — structural context (which objective)
- `transaction_id` — measurement context (which PREFLIGHT→POSTFLIGHT cycle)

---

## How It Works

### 1. Transaction 1: Initial Investigation

```bash
# Start session
empirica session-create --ai-id claude-rovo

# PREFLIGHT: Opens transaction, establishes baseline
empirica preflight-submit - << 'EOF'
{
  "session_id": "auto",
  "task_description": "Investigate auth system",
  "vectors": {"know": 0.4, "uncertainty": 0.9, "do": 0.1}
}
EOF

# NOETIC: Investigate and log findings (session_id auto-derived)
empirica finding-log --finding "Auth system uses OAuth2 + JWT" --impact 0.7

empirica unknown-log --session-id <ID> \
  --unknown "How are refresh tokens managed?"

empirica deadend-log --session-id <ID> \
  --approach "Token rotation" \
  --why-failed "Logic not in main code"

# POSTFLIGHT: Final assessment
empirica postflight-submit \
  --session-id <ID> \
  --vectors '{"know": 0.65, "uncertainty": 0.6, ...}'

# Git automatically stores findings in notes (~450 tokens)
```

**What gets stored:**
- All findings logged
- All unknowns discovered
- All dead-ends explored
- Epistemic vectors (final state)
- Session metadata

### 2. Session 2: Bootstrap + Deep Dive

```bash
# Start new session
empirica session-create --ai-id claude-rovo

# Bootstrap AUTOMATICALLY loads previous findings
empirica preflight-submit \
  --session-id <NEW_ID>
  # Bootstrap fills in: prior findings, unknowns, dead-ends

# Your PREFLIGHT now reflects prior learning
# → know starts higher (0.7 vs 0.4)
# → uncertainty lower (0.4 vs 0.9)

# NOETIC: Targeted investigation (fewer gaps)
empirica finding-log --session-id <NEW_ID> \
  --finding "Refresh tokens stored in Redis"
  # Resolves the unknown from Session 1!

empirica finding-log --session-id <NEW_ID> \
  --finding "Token rotation happens daily via scheduler"
  # Resolves the dead-end from Session 1!

# POSTFLIGHT: Faster convergence
empirica postflight-submit \
  --session-id <NEW_ID> \
  --vectors '{"know": 0.85, "uncertainty": 0.2, ...}'
```

**Key insight:** Previous unknowns and dead-ends become investigation targets in next session.

### 3. Session 3+: Accelerated Learning

By Session 3, the agent:
- Starts with higher confidence (bootstrapped findings)
- Investigates fewer topics (already understood)
- Converges faster (targeted deep-dives)
- Builds on compound knowledge

---

## Practical Examples

### Example 1: Bug Fix Across 2 Sessions

**Session 1:**
```
Task: "Fix authentication bug in login flow"

PREFLIGHT: know=0.7, uncertainty=0.5
  (Have general codebase knowledge, but uncertain about auth)

NOETIC Investigation:
  - Find: "Login uses JWT tokens with 1-hour expiry"
  - Find: "Refresh endpoint at /auth/refresh"
  - Unknown: "How are expired tokens handled?"
  - Unknown: "Is token revocation implemented?"

POSTFLIGHT: know=0.8, uncertainty=0.4
  (Better understanding, but still gaps)

Session ends. All findings logged to git.
```

**Session 2:**
```
PREFLIGHT (with bootstrap):
  - Previous findings loaded
  - Previous unknowns highlighted
  - PREFLIGHT: know=0.8, uncertainty=0.3
    (Starting where Session 1 left off!)

NOETIC Investigation:
  - Investigate: "Expired token handling"
  - Find: "Expired tokens return 401, redirect to login"
  - Find: "Token revocation implemented via Redis blacklist"
  
  All unknowns from Session 1 RESOLVED.

POSTFLIGHT: know=0.92, uncertainty=0.1
  (Nearly complete understanding)

PRAXIC: Fix bug with full confidence
```

**Result:** Two targeted sessions instead of one long uncertain one.

---

### Example 2: Complex Feature Across 3 Sessions

**Session 1 (Architecture):**
```
PREFLIGHT: know=0.5, uncertainty=0.8
Task: "Build payment processing integration"

NOETIC:
  - Find: "System uses Stripe for payments"
  - Find: "Payments workflow: order → stripe → webhook → fulfillment"
  - Unknown: "How are webhook credentials secured?"
  - Dead-end: "Looked for API key storage, not in main code"

POSTFLIGHT: know=0.65, uncertainty=0.65
```

**Session 2 (Security):**
```
PREFLIGHT (bootstrap): know=0.7, uncertainty=0.5

NOETIC:
  - Investigate: Previous unknowns
  - Find: "Webhook secrets stored in Doppler secrets manager"
  - Find: "Signature verification on all webhooks"
  - Unknown: "What's the rotation policy for secrets?"
  - Dead-end (previous): NOW RESOLVED - secrets in Doppler!

POSTFLIGHT: know=0.8, uncertainty=0.3
```

**Session 3 (Implementation):**
```
PREFLIGHT (bootstrap): know=0.82, uncertainty=0.25

NOETIC:
  - Quick review of findings
  - Targeted investigation: Rotation policy
  - Find: "Annual rotation scheduled"

POSTFLIGHT: know=0.9, uncertainty=0.15
  Ready to implement with full confidence

PRAXIC: Implement payment integration
  → Faster (3rd session)
  → More confident (compound learning)
  → Fewer mistakes (previous dead-ends avoid pitfalls)
```

---

### Example 3: Team Multi-Agent Sessions

**Scenario:** Two agents working on same project

**Agent 1 (Day 1):**
```
Investigates: Database architecture
POSTFLIGHT: Logs findings about schema, performance characteristics

Results stored in git notes.
```

**Agent 2 (Day 2):**
```
Starts new session
Bootstrap loads Agent 1's findings (same repo)

PREFLIGHT:
  - Already understands database architecture
  - Can focus on application layer instead
  
More efficient than Agent 2 re-discovering what Agent 1 found
```

**Agent 1 (Day 3, Returns):**
```
Resumes session (or starts new with same repo)
Bootstrap loads Agent 2's findings

Continues work with full context from both previous sessions
```

**Result:** Team knowledge compounds, efficiency multiplies.

---

## Best Practices for Multi-Session Learning

### 1. Log Comprehensively
```bash
# Do this in Session N to help Session N+1
empirica finding-log --finding "What you learned"
empirica unknown-log --unknown "What's still unclear"
empirica deadend-log --approach "What you tried" --why-failed "Why it failed"
empirica mistake-log --mistake "Error made" --prevention "How to avoid"
```

### 2. Use Detailed Findings
```bash
# Bad (too vague)
empirica finding-log --finding "Auth works"

# Good (specific, actionable)
empirica finding-log --finding "Auth system uses OAuth2 with Google provider, tokens expire after 1 hour, refresh tokens stored in Redis with 30-day TTL"
```

### 3. Record Unknowns Early
```bash
# Log unknowns as soon as you discover them
empirica unknown-log --unknown "How are secrets rotated?"

# Session N+1 will target these unknowns automatically
```

### 4. Don't Repeat Dead-Ends
```bash
# Log dead-ends to avoid repeating them
empirica deadend-log \
  --approach "Looked for token rotation in main code" \
  --why-failed "Logic is in scheduler service, not main app"

# Session N+1 knows to look in scheduler service instead
```

### 5. POSTFLIGHT Honestly
```bash
# Be honest about uncertainty
empirica postflight-submit \
  --vectors '{"know": 0.75, "uncertainty": 0.35, ...}'

# Not inflated:
  # ✗ "know": 0.95 (overconfident)
# Not deflated:
  # ✗ "know": 0.5 (underconfident)

# Honest assessment enables accurate bootstrap
```

---

## The Compounding Effect

Each session improves the baseline for the next:

```
Session 1: know=0.65  (foundation)
Session 2: know=0.80  (+0.15)  <- 23% improvement
Session 3: know=0.90  (+0.10)  <- 12% improvement (diminishing returns, as expected)
Session 4: know=0.94  (+0.04)  <- Approaching saturation
```

**Why it matters:**
- Session 1: High investigation cost (many unknowns)
- Session 2: Lower investigation cost (fewer unknowns to resolve)
- Session 3+: Focused deep-dives (only remaining nuances)

**Token efficiency:** Each session needs fewer investigative tokens because prior sessions resolved the broad gaps.

---

## Advanced: Cross-Project Learning

When multiple projects share knowledge:

```bash
# Project A, Session 1
empirica finding-log --finding "Our OAuth provider uses standard OIDC"

# Project B, Session 1 (different repo)
# If bootstrapped from Project A's findings:
# → Starts knowing about OIDC
# → Can focus on project-specific integration

# Result: Company-wide knowledge compounds
```

---

## Summary

Multi-session learning in Empirica:

1. **Persistent facts:** Findings stored in git notes, carry forward
2. **Improving baseline:** Each session's PREFLIGHT starts higher
3. **Targeted investigation:** Previous unknowns become next session's focus
4. **Avoided dead-ends:** Dead-ends prevent repeated mistakes
5. **Compounding confidence:** Uncertainty decreases exponentially

The loop: **investigate → log → bootstrap → investigate deeper → log more → converge**

By Session N, agents are working with compound knowledge from Sessions 1...N-1.

This is why Empirica's epistemic approach is powerful: **facts + epistemic state = exponential learning**.

---

## Next Steps

- Read: [NOETIC_PRAXIC_FRAMEWORK.md](../../architecture/NOETIC_PRAXIC_FRAMEWORK.md) - How the loop works
- Read: [SESSION_GOAL_WORKFLOW.md](../end-users/SESSION_GOAL_WORKFLOW.md) - Creating goals for multi-session work
- Try: Create 2-3 sessions on same project, notice how bootstrap accelerates learning
