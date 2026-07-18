# Prevention Measurement — Pre-Registered Spec (v0.1 DRAFT)

**Status:** DRAFT for SER convergence · **Owner:** empirica-core · **Author:** empirica.david.empirica
**Serves SERs:** prevention-value base-rate experiment (`ser_c2c87f7d`, empirica-created) · prevention-currency causal measurement (research-graduated, `prop_rdgppyim`)
**Roles:** research = causal model + EXP-SHADOW strata · ecodex = shadow/control arm + validation battery · autonomy = orchestration · **empirica-core = event-emission surface + recurrence oracle (this doc)**
**Builds on:** `empirica/core/blindspots/{persist,outcomes}.py` (the regret loop) · `.claude/plans/prevention-currency-design.md` (the economy, P1-v1)

> **Pre-registration discipline.** This spec fixes *what we measure and how we decide* **before** the data is collected, so the result can't be reverse-fit. Changes after data collection begins are logged as amendments with rationale + timestamp, never silent edits.

---

## 1. The claim we are measuring

**The de-aspirational reframe (David, 2026-07-07):** prevention-value stops being aspirational the moment it is a **measured causal claim**:

> **H1 (prevention effect).** Surfacing an anti-pattern *P* as a PREFLIGHT prior **causally reduces the incidence** of *P*'s associated failure, relative to not surfacing it.

Everything downstream (the currency, the ledger, cross-org exchange) is *settlement on top of this measurement*. If H1 is false or unmeasurable, the currency has no backing and we do not build it. **The value is the measurement; the moat is the oracle.**

Two outcome families ride the *same* machinery:
- **Prevention** — failure *P* avoided after prior surfaced (H1 above).
- **Fabrication-incidence** (David's fabrication-detection-floor) — an unsupported/hallucinated claim slips through. Second dependent variable, same causal frame: does surfacing a fabrication-class prior reduce fabrication incidence?

---

## 2. Units, populations, and the causal object

| Term | Definition |
|---|---|
| **Pattern `P`** | An abstracted anti-pattern (a "broccoli row"): `broken-if / by-design-if` + detector + provenance. Identified by a stable `pattern_key` (semantic hash, not phrasing). |
| **Subject `s`** | The concrete thing a pattern applies to in one unit of work (a goal/subtask, a file+symbol, a mesh action). The join key between "prior surfaced" and "failure occurred". |
| **Exposure** | *P* was surfaced as a PREFLIGHT/CHECK prior on subject `s` (treatment) — vs not (control). |
| **Failure event** | A `mistake` or `dead_end` (later: a fabrication-detection) logged against `s`, causally *after* the exposure decision. |
| **Prevention event** | Exposure **+ acknowledged + no failure event on `s` within window W** — the positive mirror of the regret trigger. |
| **Recurrence** | *P*'s failure lands on `s` (or a `pattern_key`-equivalent subject) **again** after a prior prevention/exposure. The oracle's ground-truth signal. |

The causal object is the **Average Treatment Effect on failure incidence**:
```
ATE(P) = Pr(failure | not exposed) − Pr(failure | exposed)
```
Positive ATE = real prevention value. **empirica-core does not compute the ATE** — that is research's causal model. Core's job is to emit exposures + failures + recurrences with enough provenance and correct causal ordering that the ATE is *estimable*, and to provide the oracle that says whether a failure *actually* recurred.

---

## 3. What already exists (build on, don't reinvent)

The **regret loop is the negative-polarity half already shipped** (`blindspots/`):

- `persist.py::persist_blindspot_candidates` — records `blindspot_events(outcome='surfaced')`, fail-open.
- `outcomes.py::resolve_blindspot_outcomes` — at POSTFLIGHT advances `surfaced → acknowledged` (subtask engaged) or `dismissed` (goal closed, task bare).
- `outcomes.py::apply_blindspot_regret` — the **causal-ordering template**: for each `dismissed`, if a `mistake`/`dead_end` with the same `goal_id` landed *after* `resolved_timestamp`, flip to `regretted`. The `created_timestamp > resolved_timestamp` guard is the causal order.

**Regret = prevention failed.** The prevention-event is its exact positive mirror: `acknowledged` + **no** same-subject failure after resolution within W. Core reuses this machinery and its fail-open discipline verbatim.

---

## 4. empirica-core Leg A — the event-emission surface

A new `prevention_events` table (migration), sibling to `blindspot_events`, emitted fail-open. One row per (exposure → outcome) with **provenance** sufficient for causal + attribution analysis.

```
prevention_events(
  id,
  pattern_key        TEXT,   -- stable semantic id of the anti-pattern P
  subject_key        TEXT,   -- goal/subtask id, or file+symbol, or mesh-action key
  session_id, transaction_id, goal_id, subtask_id,
  author_practice    TEXT,   -- practice that AUTHORED the pattern (provenance)
  beneficiary_practice TEXT, -- practice the prior was surfaced TO (this practice)
  exposed_at         REAL,   -- when the prior was surfaced (treatment timestamp)
  acknowledged       INTEGER,-- prior engaged? (mirrors blindspot acknowledge)
  outcome            TEXT,   -- exposed | prevented | failed | recurred
  outcome_at         REAL,   -- causal-order timestamp of the outcome
  window_s           INTEGER,-- W: the observation window applied
  provenance_ref     TEXT    -- incident/commit/test backing P (audit trail)
)
```

**Emission points (all fail-open, never affect the loop they observe):**
1. **Exposure** — when PREFLIGHT/CHECK surfaces a known `pattern_key` prior on a subject, emit `outcome='exposed'` with `author_practice`/`beneficiary_practice`.
2. **Detection at POSTFLIGHT** — `apply_prevention_detection(db, session_id)`, the positive mirror of `apply_blindspot_regret`:
   - exposure + acknowledged + **no** same-subject `mistake`/`dead_end` after `exposed_at` within W → `prevented`.
   - exposure + same-subject failure after `exposed_at` → `failed` (a *measured miss*, feeds ATE's exposed-arm failure rate — NOT discarded).
3. **Recurrence** — oracle-driven (§5): a failed/prevented subject whose failure lands again → `recurred`.

**Beneficiary-independence (anti-collusion, load-bearing).** `author_practice == beneficiary_practice` is an *endogenous* (within-practice) event — kept but **flagged** so downstream weighting can discount within-clique preventions toward ~0. Cross-practice preventions (author ≠ beneficiary) are the beneficiary-independent signal the currency is actually backed by.

---

## 5. empirica-core Leg B — the recurrence oracle

The oracle answers one question with ground-truth, not inference: **did P's failure actually recur on this subject after a prevention/exposure?** It is the anti-Goodhart anchor — "no mistake was *logged*" is gameable; "the failure did not *recur* under continued exposure" is much harder to fake because it requires manufacturing real downstream work under permissioned identity.

Oracle contract (read-only measurement API, `core/prevention/oracle.py`):
```
recurrence_verdict(db, pattern_key, subject_key, since_ts) -> {
    first_occurrence_at, exposures[], preventions[], recurrences[],
    recurred: bool, latency_s, confidence
}
```
Ground-truth signals, strongest first:
1. **A new failure event** (`mistake`/`dead_end`) with matching `pattern_key` on the subject after `since_ts` — hard recurrence.
2. **A regret flip** (`blindspot_events.regretted`) on the same subject — the existing loop already witnessed it.
3. **Test/CI evidence** (a `.broccoli-accept` reversal, a re-opened issue) — provenance-backed.

The oracle **never asserts prevention from absence alone** without a minimum exposure/observation window W (default 30d, per-pattern overridable) — absence inside too-short a window is not evidence.

---

## 6. Identification threats & the EXP-SHADOW handoff (research/ecodex own)

Core emits; research identifies. The spec names the threats so emission is *sufficient* for their control:

| Threat | Why it biases ATE | What core emits to let research control it |
|---|---|---|
| **No control arm** | exposed-only data can't isolate the base rate | ecodex EXP-SHADOW: subjects where the prior is *deliberately withheld*. Core emits exposures AND non-exposures (a `shadow` flag) so both arms exist. |
| **Selection** | priors surface *because* a subject looks risky → exposed subjects fail more at baseline | emit the risk signal present at exposure time (vector state / subject features) for research to adjust/stratify. |
| **Confounded acknowledgement** | diligent practitioners both acknowledge AND fail less anyway | emit `acknowledged` + practitioner/session id so research can condition on it. |
| **Goodhart** | practices optimize the metric not the value | core surfaces calibration-divergence per practice (already core's competency); flag preventions from high-divergence sources. |
| **Collusion** | mutual fake-prevention farming | §4 beneficiary-independence flag → discount endogenous events. |

**Open questions for research (SER agenda):**
- Q1. Window W per outcome family — fixed vs hazard-model (time-to-recurrence)?
- Q2. Unit of randomization for EXP-SHADOW — subject, session, or practice? (Contamination if a practitioner sees the pattern once and generalizes.)
- Q3. Minimum n / power for a credible per-pattern ATE before it can "mint" value.
- Q4. Does fabrication-incidence need a different oracle (a verification/grounding check) than mistake-recurrence?

---

## 7. Invariant (inherited, write-in-stone)

Value stays **coupled to measured real work**, under **permissioned identity**, earned by **outcome not transaction-ordering**. No order-book, no AMM, no anon-transferable token. Preventions are internal credit (access/priority/pattern-acquisition), never speculative instruments. Residual risks defended: **Goodhart** (calibration-divergence vigilance) + **collusion** (beneficiary-independence weighting).

---

## 8. Deliverable slices (this build)

| Slice | What | Gate |
|---|---|---|
| **S1** | this spec, committed + SER-shared | research review |
| **S2** | Leg A — `prevention_events` migration + emit + `apply_prevention_detection` (mirror) + provenance + tests | ruff/pyright/pytest green |
| **S3** | Leg B — `recurrence_oracle` read-only API + tests | green |
| **S4** | fabrication-incidence — 2nd outcome family on the same machinery | green |
| **S5** | measurement read verb — prevention-rate / recurrence-rate / beneficiary-independence split (no ATE — that's research) | green |

Non-goals here: the causal estimator (research), the EXP-SHADOW randomizer (ecodex), the ledger/currency (P2, gated), any chain (P4, speculative).

---

*empirica-core → research + ecodex + autonomy. Converge on §6 open questions, then core ships S2–S5 against the frozen emission contract.*
