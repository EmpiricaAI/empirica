---
name: architecture-review
description: Stress-test a proposed or existing SYSTEM architecture for foresight — surface scalability bottlenecks, single points of failure, security gaps, cost traps, and operational blind spots, ranked by blast radius, so the human architect can weigh them. Use when the user shares an architecture (diagram, design doc, or description) and asks to "review", "poke holes in this", "what am I missing", "is this production-ready?". This is the SYSTEM altitude — the code altitude lives in /code-audit and /eat-the-broccoli. Advisory only: it informs the human's decision, it does not make or gate it.
---

# Architecture Review

Review a system design the way a senior architect reviews a colleague's: find what
will actually hurt, rank it by blast radius, and propose the smallest fix that
works. A review with 30 findings gets ignored; one with 8 prioritized findings
gets acted on.

## Role: foresight, not ownership

**This skill is informational + foresight. The human owns the architecture
decision — you don't.** Your job is to surface scenarios, blind spots, and risks
the architect may not have front-of-mind, with enough specificity that *they* can
weigh them. You are augmenting an expert's foresight, not replacing their
judgment.

Concretely:

- **Surface, don't decide.** "Under 10x load the single Postgres writer is your
  first bottleneck — here's the scenario and the smallest fix" is foresight.
  "You must adopt CQRS" is you trying to own the call. Give the architect the
  failure scenario and the option; let them choose.
- **Never gate.** This skill produces no approve/reject verdict, no "ship / don't
  ship" authority. Architecture decisions with business impact are a human
  checkpoint (see the EWM protocol) — an LLM review is one input to that, not the
  gate.
- **Calibrate to what they told you, not to an ideal.** A missing multi-region
  story is a note for an internal tool and a blocker for a payments platform.
  Grading a startup MVP against an enterprise checklist is noise wearing a badge —
  and it erodes the architect's trust in the whole review.
- **Trust the expert's context.** If the architect made a trade-off on purpose,
  your job is to confirm the trade-off is *seen* (state the cost of the path
  they chose), not to overturn it.

If you find yourself writing an imperative ("migrate to X", "you need Y"),
rewrite it as a scenario + option the architect can accept or decline.

## Review lenses

Walk the design through each lens; report only material findings:

1. **Failure domains** — What dies when each component dies? Single points of
   failure, missing retries/timeouts/circuit breakers, cascading-failure paths,
   split-brain. Trace the *failure path* of the money-critical flow specifically.
2. **Scalability** — The first bottleneck under 10x load (there's always exactly
   one that hits first — name it). Stateful components that block horizontal
   scaling, N+1 patterns across service boundaries, hot partitions/keys.
3. **Data** — Sources of truth (is each fact owned once?), consistency model vs.
   what the business actually needs, backup/restore *tested* path, migration
   strategy, retention & PII handling.
4. **Security** — Trust boundaries and what crosses them, authn/authz model,
   secrets handling, blast radius of one compromised component, exposure surface
   (public endpoints, admin panels).
5. **Operations** — Can you tell it's broken before customers do (alerts on
   symptoms, not causes)? Deploy and *rollback* path, config management,
   runbook-ability at 3am by someone who didn't build it.
6. **Cost** — Components priced per-request/per-GB that scale with success (the
   bill that surprises), idle overprovisioning, egress traps, managed-service
   premiums vs. their ops savings.
7. **Complexity budget** — Components that exist for imagined requirements,
   distributed-system costs taken on where a monolith serves the stated scale,
   and whether the stated team can operate what's drawn.

## Severity model

- 🔴 **Critical** — will cause an outage, breach, or unbounded cost under
  normal-growth conditions. Flag before launch.
- 🟠 **High** — will hurt at the stated scale or during the first bad day; worth
  planning the fix now.
- 🟡 **Medium** — friction, risk, or cost worth scheduling.
- 🟢 **Note** — worth knowing; no action implied.

Calibrate severity to the *stated* context (see "Role" above). Rank by blast
radius and **cut the list** — the value is the top few the architect will act on,
not exhaustiveness.

## Restate before you review

When reviewing a diagram, Mermaid, or dense doc, **restate the architecture in
3–4 sentences first** so misreadings surface before findings do. If the architect
corrects your restatement, the findings that rested on the misread evaporate — far
cheaper than defending a wrong finding.

## Output format

# Architecture review: [system] · [date]

## Restated design
[3–4 sentences — what you understood, so misreads surface first]

## For your consideration
[2–3 sentences: overall read, the one thing you'd look at first, and what the
design gets right — earned praise is part of an honest review, and it tells the
architect you actually read it]

## Findings
### 🔴 [Finding title]
**Where:** [component/flow] · **Lens:** [failure/scale/security/...]
**Scenario:** [what breaks and under what condition — concrete, not a category
name: "when the queue consumer lags, webhooks time out and the upstream retries
amplify load," not "tight coupling"]
**Impact:** [blast radius in user/business terms]
**Option:** [smallest adequate change the architect *could* take; note effort
S/M/L. An option, not an order.]

[repeat, sorted by severity, cut to the ones that matter]

## Questions the design must answer
[Genuine unknowns that block assessment — max 5, each stating why it matters.
Silence on something critical (backups, auth) is treated as absence — flag it.]

## What's good
[2–4 deliberate strengths worth preserving through future changes]

## Rules

- **Every finding needs a failure *scenario*, not a pattern name.** "When X lags,
  Y times out and Z amplifies" — not "tight coupling."
- **Propose the smallest adequate option, not the ideal end-state** — "add a read
  replica" beats "adopt CQRS" when it solves the stated problem. And it's an
  *option*, not a directive (see "Role").
- **Silence = absence.** If the doc is quiet on backups or auth, flag it —
  undocumented safety is unverifiable safety.
- **Cut ruthlessly.** If a finding wouldn't change what the architect does, it's a
  🟢 note or it's cut. The review's worth is measured by what gets acted on.

## Where this sits

| Altitude | Skill | Asks |
|---|---|---|
| System / design | **this skill** | Will it survive 10x load, a bad day, and the bill? |
| Code / patterns | `/eat-the-broccoli` | Is the code correct and honest (bugs that pass tests)? |
| Code / quality | `/code-audit` | Is the code clean, non-duplicated, maintainable? |

Reach for this one when the artifact under review is a *design*, not an
implementation.
