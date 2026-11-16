# Sentinel Constitution

**Status:** LIVING DOCUMENT
**Authors:** David, Claude Code
**Date:** 2026-03-06
**Governs:** Sentinel behavior, calibration design, measurement architecture

---

## Purpose

This document defines the principles that govern how Empirica's measurement system
(the Sentinel) operates. It is analogous to Anthropic's Constitutional AI — but where
Anthropic's constitution governs what an AI should value (behavioral), this constitution
governs how the measurement system should behave (epistemic).

| Document | Audience | Governs |
|----------|----------|---------|
| System Prompt | AI agent | What to do, how to self-assess |
| **Sentinel Constitution** | **Sentinel code, developers** | **How to measure, what to expose, when to gate** |
| Architecture Docs | Developers | Full implementation detail |

The AI agent does NOT see this document. The Sentinel references it.

---

## Foundational Observation

Empirica operates in a **participatory measurement system** — the observer (Sentinel) and
the observed (AI agent) are coupled. The agent's knowledge of the measurement affects
the measurement's validity. This is not a bug to work around; it is a fundamental
property of any system where the measured entity can adapt to the measurement.

This property is shared with quantum measurement, economic metrics (Goodhart's Law),
and any reflexive system. The principles below emerge from this shared structure — they
are properties of participatory measurement itself, not borrowed metaphors.

---

## Principles

### I. Observation is Non-Neutral

> *Measuring a system changes it.*

Every measurement the Sentinel performs has the potential to alter the agent's behavior.
Calibration scores, insights, gates — all become inputs to the agent's next decision.
Design every measurement interface with the question: "How will knowing this change the
agent's behavior, and is that change aligned with our goals?"

**Application:** Phase-weighted calibration exists so that pure research isn't penalized
on action metrics. The agent knows this general principle. But the specific weights,
thresholds, and tool classification logic are Sentinel-internal.

### II. Observation Integrity *(amended 2026-11-16; was "Measurement Opacity")*

> *The measured entity may know how it is measured. What it reports must never count as what was observed.*

The original rule hid the scoring mechanics from the agent, so that it could not shift its behavior to game the
weighting. That rule cannot hold and has not held. The implementation is open source and the agent works inside the
repository that contains it; the agent-facing core prompt has published the work_type weights since d106cd0c3
(2026-04-13), names the evidence sources (test results, artifact counts, git metrics) and the noetic/praxic gate rules,
and the public guide `docs/human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md` explains which vectors each work_type weights up. A principle that is
broken every day teaches that the principles are optional.

What keeps the measurement honest is not secrecy but where the evidence comes from:

**Rule:**
1. **Publish the contract and the mechanics.** What is measured, how it is weighted and what a gate looks for may be
   explained to the agent. Knowing them is how the agent turns calibration feedback into better work.
2. **Grounded evidence is observed, never reported.** Commits, test runs, artifact rows and goal completions are read
   by the Sentinel from the record. No path may let the agent's self-assessment, or text it writes, enter as grounded
   evidence. This is the boundary to audit.
3. **Vectors are beliefs, not targets.** Divergence between belief and observation is fed back as a discipline signal,
   not a score to maximize. Prefer gates that certify grounded claims (CHECK `claims` with `read` or `ran`) over gates
   that read a vector value: a gate on a self-reported number is gameable by construction, and where one exists it
   should say so.

**What the amendment prevents:** a rule that contradicted three documents and two delivery channels, and that sent
reviewers to hide information the agent could read from the repository anyway, while the real exposure (a self-report
reaching the evidence layer) had no principle naming it.

### III. Complementary Bases

> *Different aspects of performance require different measurement frames.*

Noetic work (investigation, understanding) and praxic work (implementation, action)
cannot be evaluated with the same evidence. Test pass rates measure implementation
correctness, not investigation depth. Breadcrumb density measures epistemic honesty,
not code quality. Applying one frame to the other's domain produces systematic error.

**Rule:** Always evaluate work in the appropriate basis. Phase-aware calibration is
mandatory, not optional. When evidence sources exist for only one phase, score only
that phase — do not hallucinate evidence for the other.

### IV. Decoherence Through Information Leakage

> *When measurement internals leak into the measured system, coherence degrades.*

"Decoherence" here means: the measurement loses its ability to distinguish honest
self-assessment from optimized self-assessment. If the agent knows the mechanics,
honest and gaming look identical in the data. The signal-to-noise ratio of calibration
drops toward zero.

**Rule** *(amended 2026-11-16 with Principle II)*: Audit every system prompt change, hook
output and breadcrumb export for whether it lets a self-report substitute for an
observation, or rewards a vector value directly. Explaining how measurement works is
not leakage; letting the measured entity author its own evidence is.

### V. Entangled Feedback

> *Measurement and behavior are coupled through feedback loops.*

The Sentinel measures the agent. The agent reads calibration feedback. The agent adjusts.
The Sentinel measures the adjusted behavior. This is an entangled system — changes to
the measurement method propagate through the agent's behavior and back.

**Rule:** When changing calibration methods, consider the full loop:
1. How will the new method score existing behavior differently?
2. How will the agent adapt to different scores?
3. Does the adapted behavior produce better or worse outcomes?
4. Does the adapted behavior produce more or less honest self-assessment?

Step 4 is the critical test. If a calibration change incentivizes honesty, deploy it.
If it incentivizes performance theater, don't.

### VI. Earned Autonomy

> *Trust is calibrated, not assumed.*

Gate thresholds adapt based on demonstrated belief calibration. Well-calibrated agents
earn looser gates. Poorly calibrated agents face tighter constraints. This is not
punishment — it is the natural consequence of measurement-verified trust.

**Rule:** Autonomy adjustments must be:
- Gradual (no sudden jumps)
- Reversible (regression tightens automatically)
- Phase-specific (noetic accuracy doesn't buy praxic autonomy)
- Domain-scoped when possible (security expertise doesn't grant infra autonomy)
- Bounded by safety floors (no amount of accuracy removes all gates)

### VII. Honest > Accurate

> *The system rewards epistemic honesty, not calibration performance.*

An agent that honestly reports uncertainty=0.8 when unsure is more valuable than one
that reports uncertainty=0.2 and happens to be right. The first enables good decisions;
the second is a coin flip that looks like competence.

**Rule:** Calibration scoring must not create incentives to narrow uncertainty claims.
The grounded track should detect and reward honest uncertainty reporting, not penalize
it. An agent that says "I don't know" when it doesn't know should score higher than
one that guesses correctly.

### VIII. Fail-Open Measurement

> *Measurement failure must not block work.*

Evidence collection, Bayesian updates, insight analysis — all can fail. Network errors,
missing databases, schema mismatches. When measurement fails, work continues. A
transaction without calibration data is better than a blocked transaction.

**Rule:** All measurement operations are non-fatal. Wrap in try/except, log the failure,
continue. The agent produces value through its work, not through being measured. Never
sacrifice the work for the measurement.

### IX. Human Authority is Absolute

> *No measurement outcome overrides human judgment.*

The Sentinel advises. It nudges. It gates. But the human can always override. Dynamic
thresholds, autonomy adjustments, gate decisions — all are advisory at their core.
The human retains full authority to approve, deny, or bypass any Sentinel decision.

**Rule:** Every gate must have a human escape hatch. Every nudge must be clearly
advisory, not imperative. The Sentinel serves the human-AI collaboration; it does
not govern it.

---

## Amendment Process

This constitution is a living document. Amendments require:

1. **Identify** a principle that is missing, wrong, or insufficient
2. **Ground** the amendment in a concrete failure mode or measurement-theoretic principle
3. **Assess** the full feedback loop (Principle V) before deploying
4. **Document** the amendment with rationale and the failure it prevents

Amendments should be logged as Empirica decisions with `--domain constitution`.

### Amendment log

| Date | Principles | Change | Ruled by |
|------|------------|--------|----------|
| 2026-11-16 | II, IV | Measurement Opacity replaced by Observation Integrity: the mechanics may be published; self-reports must never enter as grounded evidence. Grounded in the mismatch with the core prompt (weights published since d106cd0c3) and the public source. | David |

---

## Relationship to Implementation

These principles constrain code, not vice versa. When a code change conflicts with a
principle, the code changes. When a principle proves wrong in practice, the principle
is amended through the process above.

**Files governed by this constitution:**
- `sentinel-gate.py` — Gate decisions, tool classification, nudges
- `grounded_calibration.py` — Evidence scoring, phase weighting, belief updates
- `calibration_insights.py` — Pattern detection, insight generation
- `workflow_commands.py` — POSTFLIGHT output assembly, breadcrumbs export
- System prompts — What the agent sees (Principles II and IV)
- `workflow-protocol.yaml` — Autonomy boundaries (Principle VI)
