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

**Shrinkage is mandatory.** A practitioner with 3 cycles showing a great Brier is
not more reliable than the practice with 300 — it's under-sampled. The fold-weight
must pull a thin practitioner's reliability toward the practice prior until it earns
divergence, or a lucky short conversation hijacks the practice direction.

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

## 6. Open questions for ratification

- **Summary/tl;dr as a first-class practitioner attribute** — wiring the CC
  conversation summary into the presence/entity record. Worth it?
- **Shrinkage model** — what prior + sample-floor before a practitioner's divergence
  counts (autonomy's lane).
- **Arbitration trigger** — when does a deliberation get arbitrated (on convergence?
  on an ECO gate? on a SER transition)?
- **Fold mechanism** — does the practice profile literally update, or does the
  practice just *weight retrieval* by practitioner reliability at query time?
