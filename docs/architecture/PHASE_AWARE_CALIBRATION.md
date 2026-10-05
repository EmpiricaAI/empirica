# Phase-Aware Evidence Collection for Calibration

**Status:** IMPLEMENTED (Phases 1-3 complete, domain-aware thresholds in v1.8.14)
**Author:** David + Claude Code
**Date:** 2026-02-10 (updated 2026-04-09)
**Depends on:** Grounded Calibration (v1.5.0), Sentinel Architecture, CHECK Gate, Domain Registry (v1.8.14)

---

## Problem

Grounded calibration conflates epistemic gain with artifact production. Evidence sources
(tests, git metrics, artifact counts, goal completions) are **praxic proxies** — they
measure what was done, not what was understood.

A verification session that confirms "63/63 functions preserved, no circular deps"
produces high knowledge (uncertainty 0.25 -> 0.10) but near-zero artifacts. The grounding
system scores `know` at 0.5 when self-assessed 0.9 because it can't distinguish
"searched thoroughly, found nothing wrong" from "didn't search."

This is not a bug — it's a category error. The system applies action-based calibration
to investigation work. Humans make the same mistake: systematically overvaluing action
tasks over the arguably harder noetic investigation that makes good action possible.

---

## Design: CHECK as Phase Boundary

The CHECK gate already separates noetic (investigation) from praxic (action) phases.
Calibration should respect this boundary.

```
PREFLIGHT ─────────── CHECK ─────────── POSTFLIGHT ──── POST-TEST
    │                   │                    │               │
    │   NOETIC PHASE    │   PRAXIC PHASE     │               │
    │   (investigation) │   (action)         │               │
    │                   │                    │               │
    ├─ Noetic vectors ──┤─ Praxic vectors ───┤               │
    │                   │                    │               │
    │  Noetic evidence  │  Praxic evidence   │  Grounding    │
    │  (sources, coverage│  (tests, git,     │  (post-test)  │
    │   unknowns, dead- │   artifacts,       │               │
    │   ends avoided)   │   completions)     │               │
```

### Track A: Noetic Calibration (PREFLIGHT -> CHECK)

**Delta:** CHECK vectors minus PREFLIGHT vectors = noetic gain claimed.

**Evidence sources:**

| Source | What it measures | Quality |
|--------|-----------------|---------|
| Qdrant queries issued | Coverage breadth | OBJECTIVE |
| Files/modules examined | Investigation depth | OBJECTIVE |
| Unknowns surfaced | Uncertainty honesty | SEMI-OBJECTIVE |
| Assumptions tested | Critical thinking | SEMI-OBJECTIVE |
| Dead-ends identified (before hitting them) | Pattern recognition | SEMI-OBJECTIVE |
| Sentinel decision quality | CHECK outcome vs subsequent reality | OBJECTIVE (retroactive) |
| Sources consulted (ref-docs, bootstrap) | Preparation quality | OBJECTIVE |

**Calibration question:** "Did investigation actually reduce uncertainty proportional to claim?"

**Key insight:** Absence of findings IS evidence. "Searched 14 modules, found 0 circular
deps" is a high-value epistemic outcome. The evidence is the coverage, not the bug count.

### Track B: Praxic Calibration (CHECK -> POSTFLIGHT)

**Delta:** POSTFLIGHT vectors minus CHECK vectors = praxic gain claimed.

**Evidence sources:**

| Source | What it measures | Quality |
|--------|-----------------|---------|
| pytest results | Implementation correctness | OBJECTIVE |
| Git metrics (commits, files changed) | Action volume | OBJECTIVE |
| Goal/task completions | Delivery | SEMI-OBJECTIVE |
| Artifact counts (findings from implementation) | Discovery during action | SEMI-OBJECTIVE |
| Issue resolution | Problem solving | SEMI-OBJECTIVE |

**Calibration question:** "Did actions produce the outcomes predicted?"

### Pure-Phase Transactions

Not all transactions have both phases:

| Transaction Type | Phases | Calibration |
|-----------------|--------|-------------|
| Investigation only | PREFLIGHT -> CHECK (investigate) -> POSTFLIGHT | Track A only |
| Implementation with prep | PREFLIGHT -> CHECK (proceed) -> POSTFLIGHT | Track A + Track B |
| Quick fix | PREFLIGHT -> CHECK (proceed) -> POSTFLIGHT | Track B dominates |
| Multiple CHECKs | PREFLIGHT -> CHECK (investigate) -> CHECK (proceed) -> POSTFLIGHT | Track A until final proceed, then Track B |

For investigate-only sessions, praxic evidence is absent by design — no penalty.

**The boundary belongs to the transaction, not the session.** `detect_phase_boundary()`
selects the PREFLIGHT and CHECK rows by `transaction_id` (commit `d077d5697`); session scope
remains only for a caller that has no transaction id. A transaction that skipped CHECK
therefore writes one combined (non-phase-split) verification row, whatever CHECKs earlier
transactions in the same session had. Before that fix, every POSTFLIGHT after the session's
first CHECK was split against an unrelated earlier CHECK's vectors.

### Multiple CHECKs

Transactions can have multiple CHECK gates (investigate loops). Each `investigate`
decision stays in noetic calibration. Only the final `proceed` CHECK starts the
praxic clock.

```
PREFLIGHT -> CHECK(investigate) -> CHECK(investigate) -> CHECK(proceed) -> POSTFLIGHT
             |--- noetic --------------------------------||- praxic ----|
```

---

## Dynamic Thresholds from Calibration History

The Sentinel uses Brier-based dynamic thresholds that adapt based on
demonstrated belief calibration. In v1.8.14, domain criticality further scales
the uncertainty threshold via the Domain Registry (`DomainRegistry.resolve()`).

**Key principle (v1.8.14):** Deterministic services produce **observed vectors** —
information. The AI synthesizes the **grounded state** from that information with
explicit rationale. The services inform; they do not score. The AI gives the score.

### Uncertainty Exclusion from Calibration Score (v1.8.14)

Uncertainty is a **meta-vector** — its grounded value is derived from the coverage
and gap magnitudes of the other 12 vectors. Including it in the calibration score
creates a circular dependency: the score would grade a meta-prediction using
evidence derived from the same predictions it's meta about.

**What changed:** Uncertainty is excluded from `_compute_weighted_calibration()`.
The `scoring_gaps` dict filters it out before computing the weighted Brier number.

**What didn't change:** Uncertainty still:
- Appears in `calibration_gaps` (visible in POSTFLIGHT response for feedback)
- Gates CHECK (via `uncertainty_threshold` in sentinel-gate.py)
- Feeds PREFLIGHT trajectory feedback (`underestimate_tendency: [uncertainty]`)
- Gets a grounded observation via `_compute_meta_uncertainty()`

The behavioral fix (AI reporting higher uncertainty) comes from trajectory pattern
feedback, not per-transaction grounding. The measurement doesn't need to be in
the score to drive improvement.

### Mechanism

```python
inflation = min(reliability * (max_inflation / 0.15), max_inflation) * min(1.0, n / 20)
know_threshold        = min(know_ceiling, know_base + inflation)
uncertainty_threshold = max(unc_ceiling,  unc_base  - inflation)
```

Where:
- `know_base` / `unc_base` = the domain baseline (0.70 / 0.35 by default, from `cascade_styles.yaml`, a cascade profile, or `calibration.yaml`)
- `reliability` = the Brier reliability component (calibration error, 0 = perfect) over the last `lookback` points (default 20) of that phase's `calibration_trajectory`
- `max_inflation` = the most the gate may tighten (default 0.05)
- `n / 20` = cold-start damper: with few points the estimate is noisy, so inflation ramps to full at 20
- ceilings = safety limits (default 0.90 for know, 0.15 for uncertainty)
- Fewer than `min_transactions` (default 5) points: the baseline is used unchanged

Thresholds never go **below** the baseline. Good calibration is not rewarded with a
lower bar; it keeps the baseline, which means the system trusts the numbers as-is.
Only miscalibration moves the gate, and only toward stricter. See the module docstring
of `empirica/core/post_test/dynamic_thresholds.py`.

### Per-Phase Calibration History

| Phase | Calibration Track | Effect |
|-------|------------------|--------|
| Noetic | Track A history | Drives the know/uncertainty thresholds the CHECK gate and the Sentinel hook actually use |
| Praxic | Track B history | Computed and reported alongside (`praxic` block of `compute_dynamic_thresholds()`); the gates read the noetic figures |

Each phase reports `basis`: `practitioner` when the model inhabiting the practice has at
least `min_transactions` points of its own, otherwise `practice` (the whole `ai_id` store).
CHECK reads the **session's own `ai_id`** (not a fixed `claude-code`) and the current
practitioner model, with the fallback to the practice (commits `ac78d1170`, `8974c6a0c`).

### Domain Scoping

Domain criticality scales the uncertainty threshold at the CHECK gate
(`_get_domain_scaled_thresholds()`): higher criticality means a stricter uncertainty bar.
Calibration accuracy is domain-scoped (via `subject` on findings/calibration records): an AI
can be well calibrated on security investigation and poorly calibrated on performance
implementation.

### Progression

```
New AI on project:     Baseline gates (0.70 / 0.35), no inflation (< 5 points)
        |               calibration data accumulates
        v
~10-20 transactions:   Reliability estimate stabilises; inflation ramps to full weight
        |
        v
Well calibrated:       reliability near 0 -> gates stay at baseline
        |
        v (overconfidence detected)
Auto-tighten:          reliability rises -> know raised / uncertainty tolerance
                       lowered by up to max_inflation -> forced investigation
```

### Self-Correcting Properties

1. **Overconfidence** -> high reliability error -> tighter gates -> forced investigation -> better calibration
2. **Good calibration** -> reliability near 0 -> gates stay at baseline -> numbers trusted as-is
3. **Domain criticality** -> stricter uncertainty bar for critical domains -> other domains unaffected
4. **Phase-specific** -> noetic and praxic history are separate trajectories

---

## Implementation Status

### Phase 1: Split Evidence Collection -- COMPLETE (v1.5.1, updated v1.6.6)

- `detect_phase_boundary()` finds CHECK proceed timestamp
- `PostTestCollector(phase="noetic"|"praxic")` filters evidence by `check_timestamp`
- `EvidenceMapper.map_evidence(phase=...)` returns phase-tagged `GroundedAssessment`
- `grounded_beliefs` and `grounded_verifications` tables have `phase` column
- `run_grounded_verification()` runs separate noetic + praxic passes
- **Profile-specific collectors excluded from noetic phase** (v1.6.6): Code quality (ruff,
  radon, pyright), test results (pytest), git metrics, prose metrics, and web metrics only
  run during praxic or combined phases. Noetic grounding uses only epistemic process evidence
  (artifact counts, investigation thoroughness, sentinel decisions). This prevents
  deterministic output-quality metrics from conflating noetic calibration — a principle that
  applies across all domains, not just software engineering.

### Phase 2: Noetic Evidence Sources -- COMPLETE (v1.5.1)

Noetic-specific evidence collected pre-CHECK:
- Unknowns surfaced (epistemic honesty)
- Dead-ends identified (pattern recognition)
- Investigation findings (knowledge depth)
- CHECK iterations (investigate decisions counted)
- Source consultation quality

### Phase 2.5: Phase-Weighted Holistic Score -- COMPLETE (v1.5.9+)

The Sentinel now splits tool counts into `noetic_tool_calls` and `praxic_tool_calls`
based on existing tool classification (NOETIC_TOOLS set + safe bash detection).

At POSTFLIGHT, the holistic calibration score is computed as a weighted average:

```
holistic_score = noetic_weight * noetic_calibration + praxic_weight * praxic_calibration
```

Where weights are derived from tool call distribution:
- 95% noetic tools -> noetic_weight=0.9, praxic_weight=0.1 (floor applied)
- 50/50 split -> equal weights
- noetic_only transaction -> 100% noetic weight

Floor: any phase with evidence gets minimum 0.1 weight to prevent complete zeroing.

**POSTFLIGHT output now includes:**
```json
{
  "phase_weights": {"noetic": 0.92, "praxic": 0.08, "source": "tool_classification"},
  "holistic_calibration_score": 0.15,
  "holistic_gaps": {"know": 0.12, "signal": 0.08}
}
```

### Phase 2.6: Calibration Insights Loop -- COMPLETE (v1.5.9+)

New `CalibrationInsightsAnalyzer` detects systemic patterns across verification history:

| Pattern | Detection | Suggestion |
|---------|-----------|------------|
| **chronic_overestimate** | Same vector overestimated in >70% of records | Reduce self-assessment |
| **chronic_underestimate** | Same vector underestimated in >70% of records | Increase self-assessment |
| **evidence_gap** | Vector has evidence in <30% of verifications | Add evidence source |
| **phase_mismatch** | Gap >2x larger in one phase than the other | Phase evidence imbalance |
| **volatile** | Gap direction flips in >50% of consecutive pairs | Stabilize evidence |

Insights are:
- Stored in `calibration_insights` table (with `acted_on` flag for closing the loop)
- Exported to `.breadcrumbs.yaml` for session-start injection
- Included in POSTFLIGHT output as `insights[]`

This creates a feedback loop: each calibration cycle identifies where evidence
collection is weak, which informs improvements to the collection methods themselves.

### Phase 2.8: Actionable PREFLIGHT Feedback -- COMPLETE (v1.6.6)

PREFLIGHT now includes `suggested_ranges` in `previous_transaction_feedback`. For each
vector with a significant gap (|gap| > 0.1), the system computes a suggested range
from the grounded posterior mean ± 1 standard deviation:

```json
{
  "previous_transaction_feedback": {
    "significant_gaps": {"know": 0.348, "signal": -0.129},
    "suggested_ranges": {
      "know": {"grounded_mean": 0.77, "suggest_low": 0.75, "suggest_high": 0.78},
      "signal": {"grounded_mean": 0.85, "suggest_low": 0.83, "suggest_high": 0.87}
    },
    "note": "Use suggested_ranges to calibrate your next self-assessment."
  }
}
```

Requires >= 3 grounded observations per vector before suggesting ranges (avoids
premature suggestions from sparse data). Range narrows as evidence accumulates —
this is correct Bayesian behavior, not a bug.

### Phase 3: Dynamic Thresholds -- COMPLETE (v1.8.14)

- `calibration_trajectory` per-phase tracking with `state_type` column
- Brier-based dynamic thresholds (`compute_dynamic_thresholds()` in `dynamic_thresholds.py`)
- Domain-aware threshold scaling via `DomainRegistry` at CHECK gate
- Higher criticality = stricter uncertainty threshold
- Safety floors hardcoded, not adjustable by calibration
- **Check-outcome Brier (B4):** AI predicts P(check passes), Brier measures
  prediction vs actual. Falsifiable, ground-truth calibration alongside vector Brier.

### Phase 4: Domain-Scoped Autonomy -- PARTIALLY COMPLETE (v1.8.14)

- Domain registry with `(work_type, domain, criticality)` tuples → checklists
- Service registry with self-declaring deterministic checks
- Compliance loop runs domain checklist at POSTFLIGHT
- Per-domain threshold computation via `_get_domain_scaled_thresholds()`
- Dashboard: `empirica calibration-report` (`--windowed`, `--brier`, `--trajectory` views)
- CLI: `empirica domain-validate`

---

## Evidence That This Matters

From the session that motivated this spec:

| Metric | Self-assessed | Grounded | Reality |
|--------|--------------|----------|---------|
| know | 0.90 | 0.50 | Investigation confirmed 63/63 functions, clean DAG, no circular deps |
| signal | 0.80 | 1.00 | Findings about dead references were high-value |
| uncertainty | 0.10 | 0.18 | Uncertainty WAS genuinely low after thorough verification |

The grounded system undervalued `know` by 0.4 because no tests changed and no code
was committed. But the epistemic state genuinely improved — the uncertainty about
modularization quality was fully resolved.

With phase-aware calibration, this session would be evaluated as pure noetic work
against noetic evidence (coverage, queries, uncertainty reduction). The 0.4 gap
would not exist.

---

## Design Principles

1. **CHECK is the boundary** — not an arbitrary split, it's the gate that already exists
2. **Absence is evidence** — "searched and found nothing" is noetic signal, not silence
3. **Earned not given** — autonomy increases only with demonstrated belief calibration
4. **Self-correcting** — regression automatically tightens gates, no manual intervention
5. **Domain-scoped** — expertise in one area doesn't grant autonomy in another
6. **Phase-specific** — noetic and praxic competence are independent axes
7. **Safety floors** — no amount of belief calibration removes all gates
8. **Human retains override** — dynamic thresholds adjust AI autonomy, not human authority
