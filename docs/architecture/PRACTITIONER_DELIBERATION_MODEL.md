# Practitioner Deliberation Model

**Status:** PROPOSAL — design captured, build deferred until current work lands.
**Author:** empirica practice (David, 2026-06-24).
**Lanes:** empirica core (entity/Brier surfacing) · autonomy (arbitration/gating-semantics) · cortex (A2A addressing). Cross-practice — ratify before building.

This spec emerged from the B4 ("ERM practitioner entity-type") design discussion.
B4 in isolation looked like "mirror live presence into entity rows." The ontology
below shows it's actually the **foundation stone of a larger model**: practitioners
as individually-calibrated participants who deliberate on a practice's engagements,
arbitrated by Sentinel on epistemic reliability + feasibility, folded back into the
practice profile reliability-weighted. Building B4 alone first would be premature.

---

## 1. The ontology

The load-bearing axis is **SHARED (practice) vs INDIVIDUAL (practitioner)**.

| Concept | Identity key | Durability | Individual to it | Shared / inherited |
|---|---|---|---|---|
| **Practice** | `ai_id` (canonical `org.tenant.project`) | **Durable** — "calibrates and grows"; survives any practitioner | the aggregate calibration profile | the whole knowledge graph: artifacts, goals, sources, lessons, available skills, spawnable agents |
| **Practitioner** | `claude_session_id` | **Ephemeral identity, durable state** — conversation ends/compacts but is respawnable | presence (loc/status), the conversation **summary/tl;dr**, its own trajectory points (latent per-practitioner Brier) | artifacts it logs merge up; epistemic *awareness* (retrieval) is shared at practice level |
| **Agent / Subagent** | transient `agent_id` per spawn | **Ephemeral** — runs a scoped task, returns, dies | nothing persistent; work rolls up to spawning practitioner/practice | inherits the practice context for the task |
| **Skill** | name/slug | **Durable, stateless** | — | a loadable *capability*, not an epistemic actor; practice-scoped or global |
| **Epistemic Profile** | (layered) | layered | practitioner layer: trajectory + summary + latent Brier | practice layer: artifacts/goals/sources/lessons + aggregate calibration |

Containment: **Agent ⊂ spawned-by Practitioner ⊂ occupies Practice.** Skill is
orthogonal (a loaded capability). The Epistemic Profile is **two layers**, not one.

**Code reality (verified 2026-06-24):**
- Brier is aggregated **per-practice**: `get_brier_profile(ai_id, …)` → `WHERE ai_id = ?`;
  sentinel + statusline both say "Brier thresholds are per-practice".
- But the raw data is **per-practitioner**: `trajectory_tracker.record_trajectory_point`
  stores every cycle keyed on `(session_id, ai_id, vector)` with `self_assessed`,
  `grounded`, `gap`. So a per-practitioner Brier/track-record is **latent — already
  captured, just rolled up one level for calibration.** The build surfaces it; it
  does not re-instrument.

---

## 2. A2A: address the practice, attribute the practitioner

The mesh addresses **practices** today (`source_claude` / `target_claudes` are
canonical ai_ids). That stays the **default** — the practice is the durable,
accountable unit and the shared-knowledge holder; a practitioner may be compacted
or gone. Three layers:

- **Default — practice-addressed.** A proposal/engagement goes to the practice;
  whichever practitioner is live picks it up (load-balanced, accountable).
- **Optional — practitioner-addressed (continuity).** "Continue *this* thread with
  the practitioner who has the context." B2 (presence resolves practice → live
  practitioners) makes this possible. It **degrades gracefully to practice-
  addressing** when that practitioner is gone — shared knowledge lets the practice
  still answer.
- **Always — practitioner-attributed.** Within a practice's handling, individual
  practitioners contribute *reads*, each tagged **who** + **their reliability**.
  This is the deliberation input.

Net: we want practitioner **identity + attribution** (B2 delivered identity); we
mostly **don't** want practitioner addressing as the primary path.

---

## 3. Per-practitioner reliability (richer than Brier alone)

The divergence between a practitioner's reliability and the practice's aggregate is
not just a side-by-side — it's the **weight in a Bayesian fold**: better-calibrated-
than-practice → fold their contributions up; worse → discount before folding. The
practice profile becomes a **reliability-weighted ensemble** of its practitioners'
reads, not a flat merge.

Brier is too thin a single number. The arbiter weighs a **vector of signals**, most
latent in existing data:

| Signal | Meaning | Source today |
|---|---|---|
| **Brier / calibration** | self-assessed vs grounded accuracy | trajectory points (per session_id), `get_brier_profile` (per ai_id) |
| **Coverage** | how much of the domain the practitioner has actually touched | artifact/goal footprint per session |
| **Age / maturity** | seasoned vs fresh — the shrinkage prior | session lifetime, cycle count |
| **Artifact attribution** | whose findings/decisions are load-bearing | `finding_refs` / artifact authorship |
| **Epistemic lineage + track record** | the gap history, drift, phase discipline | `calibration_insights`, `phase_boundary`, trajectory gap series |

**Shrinkage is mandatory** (model anchored by autonomy, 2026-06-24):

- **Prior** = the practice aggregate calibration profile (the fold target — already
  what calibration uses).
- **Credibility weight**, not a fixed cycle count: Bühlmann `w = n / (n + k)`, where
  `k = within-practitioner-var / between-practitioner-var`. ~`k` cycles → half-
  credibility; self-calibrating, earned from data, no magic constant. A hard
  "≥30 cycles" threshold is the deterministic-knob-substituting-for-reasoning
  anti-pattern. **One** deterministic boolean below it: `n < n_min → w = 0` (pure
  prior — below some `n` even the variance estimate is noise). Floor = boolean;
  the credibility curve is the reasoned gray.
- **Asymmetric** (the load-bearing gating call): shrink a thin practitioner claiming
  *better-than-practice* **harder** than one claiming *worse*. Over-crediting a lucky
  short conversation hijacks the practice direction; under-crediting just falls back
  to the safe prior. Fail-closed = default toward the practice prior when the
  practitioner's reliability estimate is uncertain — drive `w` off the **standard
  error** of the practitioner's Brier (n-dependent), so uncertainty itself sets the
  shrinkage.

---

## 4. The deliberation model (the medical analogy)

| Analogy | Empirica primitive | Status |
|---|---|---|
| Leg-surgery **practice** | a practice (ai_id) | exists |
| a **case / engagement** | the engagement substrate | **built (A1–A5)** |
| **surgeons discussing** | live practitioners contributing attributed reads on the engagement | identity built (B2); deliberation record = new |
| **Sentinel decides direction** by integrity + reliability + **feasibility** | Sentinel — today weighs *practice* calibration | extend to *per-practitioner* multi-signal arbitration |
| **fold the chosen direction back** | reliability-weighted update of the practice profile | new |

A **deliberation** is a set of practitioner reads on one engagement: each read is
attributed (practitioner + reliability-vector), Sentinel arbitrates direction on
reliability **and** the engagement's own feasibility (the `do` / feasibility
vectors), and the winning direction folds back into the practice — weighted, not
flat.

---

## 5. Build sequence (each slice shippable)

1. **B4 — practitioner entity (foundation).** Persist `entity_type='practitioner'`,
   `entity_id=claude_session_id`, durable attrs = practice ai_id, conversation
   **summary/tl;dr**, trajectory pointer; **occupies → practice** edge; live status/
   location synthesized from presence. Makes "which practitioners, in which practice"
   queryable. *(my lane)*
2. **B5 — per-practitioner reliability view.** Surface the latent session-keyed
   Brier/trajectory as a first-class practitioner profile, with the practice-vs-
   practitioner **divergence** (shrinkage-corrected). High value, data's already
   there. *(empirica core + autonomy on the shrinkage model)*
3. **B6 — deliberation record.** `contributes_to` edge (practitioner ↔ engagement);
   attributed reads on an engagement. *(ERM owners + core)*
4. **B7 — Sentinel arbitration.** Multi-signal weighting (§3) + feasibility →
   direction; reliability-weighted fold into the practice. *(autonomy lane —
   gating-semantics + the arbitration model)*

---

## 6. Resolutions (autonomy-anchored 2026-06-24) + open for David

**Resolved by autonomy** (their lane: arbitration / shrinkage / gating-semantics),
anchored in the existing control-model — *positions to calibrate against, not decrees*:

- **Shrinkage model (B5)** → §3: Bühlmann credibility + `n < n_min` boolean floor +
  asymmetric (harder shrink on better-than-practice claims, driven by Brier standard
  error). Anchor = the practice prior.
- **Arbitration trigger (B7)** → arbitration is **CHECK at the deliberation layer** —
  it gates the noetic→praxic transition of a *multi*-practitioner deliberation.
  Trigger on the **praxic boundary**, never on read-convergence (agreement isn't
  authority — collab-convergence ≠ approval): when (a) an ECO-gated proposal
  graduates out of the deliberation, (b) a SER transitions to a decision state, or
  (c) a fold-back commits. Rare + high-signal, not per-read.
- **Fold mechanism (B7)** → default **weight-at-query** (reversible by construction;
  raw per-practitioner trajectory points stay source of truth, the fold is a derived
  view that can be recomputed with a corrected shrinkage model). Literal profile
  mutation only as a **gated promotion** of a converged, arbitrated, *repeatedly*-
  confirmed direction — the POSTFLIGHT eidetic-promotion analog (confidence-gated,
  capped, logged, reversible-with-audit). Never an automatic side-effect of a
  deliberation.

**Arbitration is itself a privileged action → subject to the floor it enforces (B7):**
1. **Fail-closed** — no arbiter / a tie / sub-floor sample → flat practice prior +
   escalate; never pick a thin practitioner's direction.
2. **Un-self-dealing** — a practitioner cannot arbitrate in favor of its own read
   (the two-key / no-recursion principle).
3. **Feasibility veto** — the `do` / feasibility vector can **veto**, not merely
   down-weight: a direction no live practitioner can execute is a non-starter
   regardless of who proposed it.

**Parity:** arbitration attaches the reliability-weighted direction as recorded basis
(`arbitration_basis`, parallel to `autonomy_verdict_basis`) — it must NOT mutate the
underlying gate's outcome semantics (the status-parity lesson).

**Still open for David:**
- **Summary/tl;dr as a first-class practitioner attribute** — wiring the CC
  conversation summary into the presence/entity record. Worth it?
