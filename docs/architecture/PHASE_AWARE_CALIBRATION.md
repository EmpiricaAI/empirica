# Phase-Aware Evidence Collection for Calibration

**Status:** implemented. The phase split, work-type weighting, Brier-based dynamic thresholds and the calibration insights loop are all live.
**Depends on:** [Noetic-Praxic Framework](./NOETIC_PRAXIC_FRAMEWORK.md) (the transaction), [Sentinel Architecture](./SENTINEL_ARCHITECTURE.md) (the consumer of the thresholds)

---

## Problem

Grounded calibration compares a self-assessment with evidence from deterministic services: test results, git metrics, artifact counts, goal completions. Most of that evidence is a praxic proxy. It measures what was done, not what was understood.

A verification session that confirms "63 functions preserved, no circular dependencies" may raise real knowledge and leave almost no artifacts, commits or test changes. Graded against praxic evidence, a high `know` looks like a large overestimate, because the instrument cannot tell "searched thoroughly and found nothing wrong" from "did not search". That is a category error, not a bug: action-based calibration applied to investigation.

So the transaction is split at the point where investigation became action, and each half is graded against the evidence that fits it.

---

## How a transaction is split

CHECK already separates the phases, so calibration uses it as the boundary.

`detect_phase_boundary(session_id, db, transaction_id)` in `empirica/core/post_test/phase_boundary.py` reads the transaction's PREFLIGHT and CHECK rows. **The boundary belongs to the transaction, not the session:** rows are selected by `transaction_id` (commit `d077d5697`), and session scope remains only for a caller with no transaction id. Before that fix, a session's first CHECK made every later POSTFLIGHT in the session phase-split, and graded the new transaction's evidence against an unrelated earlier CHECK's vectors.

What the boundary decides:

| Transaction shape | What POSTFLIGHT verification writes |
|-------------------|--------------------------------------|
| PREFLIGHT, then a CHECK that returned `proceed`, then POSTFLIGHT | A noetic row and a praxic row |
| PREFLIGHT, CHECKs that all returned `investigate`, then POSTFLIGHT | A noetic row only (`noetic_only`); praxic evidence is absent by design and costs nothing |
| `investigate` CHECKs followed by a `proceed` CHECK | Noetic until the last `proceed`, praxic after it |
| No CHECK at all (certified by claims, or by a confident PREFLIGHT) | One combined row, not split. Earlier transactions' CHECKs do not apply. |

The two halves compare different self-assessments with different evidence:

- **Noetic:** the vectors reported at the final `proceed` CHECK (or the last CHECK, if `noetic_only`), against noetic evidence collected up to that CHECK.
- **Praxic:** the POSTFLIGHT vectors, against praxic evidence collected from the CHECK onward.

---

## Evidence by phase

`PostTestCollector(phase="noetic" | "praxic" | "combined")` runs only the collectors that belong to the phase. Each source is independent and failure-tolerant. Source IDs below are the ones in `collector.py`.

| Phase | Sources |
|-------|---------|
| Every phase | `artifacts` (findings, unknowns, dead-ends, mistakes and their ratios) |
| Noetic (and combined) | `noetic` (unknowns surfaced, dead-end avoidance, investigation findings and thoroughness), `sentinel` (CHECK `proceed`/`investigate` history) |
| Praxic (and combined) | `goals`, `issues`, `triage`, `codebase_model`, `non_git_files`, plus the profile collectors below |
| Profile collectors, praxic and combined only | Code: `pytest`, `git`, `code_quality` (ruff, radon, pyright). Prose and web profiles have their own collectors. |

Profile collectors never run in the noetic phase. Test results, code quality, git metrics and prose metrics measure output, and in a phase that has produced no output they would only add noise. Noetic grounding rests on process evidence.

Absence is evidence. "Searched 14 modules, found 0 circular dependencies" is a real outcome: what the evidence supports is coverage and unknowns surfaced, not bug count.

### Work type reweights the evidence

`work_type`, set at PREFLIGHT, multiplies each source's weight through `WORK_TYPE_RELEVANCE` in `mapper.py`. A weight of `0.0` excludes the source for that work type; the vectors that only that source could ground are marked insufficient evidence and the self-assessment stands, rather than a false grounded value being computed from absent signal.

| work_type | Effect |
|-----------|--------|
| `code` | All sources at default weight |
| `research`, `audit` | `git`, `code_quality`, `codebase_model` and `non_git_files` excluded (research also excludes `pytest`); `artifacts` and `noetic` weighted up |
| `docs`, `design`, `data` | `code_quality` and `codebase_model` excluded, `non_git_files` and `goals` weighted up |
| `debug` | `pytest`, `triage` and `artifacts` weighted up |
| `release` | Local sensors cannot see the pipeline: `git`, `code_quality`, `codebase_model`, `non_git_files` excluded |
| `remote-ops` | Every source excluded; the self-assessment stands unchallenged and the result is marked ungrounded |

`WORK_TYPE_RELEVANCE` is the full table, including `infra`, `config` and `comms`.

### Effort counts no longer ground `change` or `uncertainty`

Several proxies mapped a count of bookkeeping onto a vector it does not measure: ten findings read as `change` 1.0 on a transaction that changed nothing; three logged assumptions read as maximal `uncertainty`, so honest logging could never shrink the gap. As of 1.14.7 those proxies are gone. `finding_production` supports `do` only, `assumption_logging` supports `know` only, both count the current transaction rather than the whole session, and the triage and goal-completion counts no longer support `change` (git sensors still observe it). Calibration rows written before that change carry no `scope` key on those observations, so history can be split at it.

---

## From gaps to a score

Each phase produces, per vector, a gap between self-assessed and grounded value.

**Category weights.** `_compute_weighted_calibration()` groups the gaps into categories (foundation, comprehension, execution, and so on) and weights each category by `work_type`, then `domain`, then a default, in that order of precedence. Optional per-vector weights can come from `project.yaml`.

**Uncertainty is not scored.** `uncertainty` is a meta-vector: its grounded value is derived from the coverage and the gap magnitudes of the other vectors (`_compute_meta_uncertainty()`: 0.4 times missing coverage plus 0.6 times scaled mean gap). Scoring it would grade a prediction against evidence derived from the same predictions. It stays in `calibration_gaps` for feedback, still feeds the CHECK threshold, and is excluded from the weighted score. `engagement` is ungroundable and skipped.

**Phase weights.** When both phases exist, the holistic score is the tool-call-weighted mean of the two:

```
holistic = noetic_weight * noetic_score + praxic_weight * praxic_score
```

Weights come from the Sentinel's `noetic_tool_calls` and `praxic_tool_calls` counters for the transaction. Any phase that has evidence gets at least 0.1. A `noetic_only` transaction is 100% noetic, and without tool data the weights default to 0.5 each. The weights are reported as `phase_weights` with a `source` of `tool_classification`, `noetic_only`, `no_tool_data` or `default`.

---

## Dynamic thresholds

The CHECK gate and the Sentinel hook compare `know` and `uncertainty` with thresholds that start at a **domain baseline** and can only move toward stricter. The code is `compute_dynamic_thresholds()` in `empirica/core/post_test/dynamic_thresholds.py`.

```python
inflation = min(reliability * (max_inflation / 0.15), max_inflation) * min(1.0, n / 20)
know_threshold        = min(know_ceiling, know_base + inflation)
uncertainty_threshold = max(unc_ceiling,  unc_base  - inflation)
```

- `know_base` and `unc_base` are the baseline: 0.70 and 0.35 by default. A cascade profile selected by `work_type` (`check-submit` only) or `calibration.yaml` (the uncertainty baseline; `check-submit` and the hook) replaces it.
- `reliability` is the Brier reliability term (calibration error, 0 is perfect) over the last `lookback` rows (default 20) of `calibration_trajectory` for that phase, where each row is one `(self_assessed, grounded)` pair. A row is one vector in one phase of one transaction, so the window is short in transactions.
- `max_inflation` caps the tightening (default 0.05, so know cannot exceed 0.75 from baseline 0.70).
- `n / 20` damps a cold start: with few points the estimate is noisy, and a noisy estimate that blocks CHECK would stop the data that would correct it from arriving.
- The ceilings are limits on how far calibration can push (default 0.90 for `know` and 0.15 for `uncertainty`), read from `calibration.safety_ceiling_*` with those fallbacks.
- With fewer than `min_transactions` points (default 5) in a phase, that phase returns the baseline unchanged.

Thresholds never go below the baseline. Good calibration is not rewarded with a lower bar; it keeps the baseline, which means the numbers are trusted as they stand. Only miscalibration moves the gate, and only toward stricter. Resolution and the Brier uncertainty term are reported as diagnostics and do not drive the thresholds.

### Whose history

Each phase reports a `basis`: `practitioner` when the model inhabiting the practice has at least `min_transactions` points of its own, otherwise `practice` (every point under that `ai_id`). Callers resolve the session's own `ai_id`, not a fixed `claude-code`, and the current practitioner model: `check-submit`, the Sentinel hook and the statusline all do.

### Which phase

Both phases are computed and reported (`noetic` and `praxic` blocks). Only the noetic figures are used by the gates, because the gates guard the move from investigation to action. The praxic block is computed and returned, and nothing gates on it.

### Where each consumer uses them

| Consumer | Uses |
|----------|------|
| `check-submit` | The uncertainty threshold, for its computed decision. Reports the base source, basis and inflation in `metacog`. |
| Sentinel hook, auto-proceed | Both thresholds against PREFLIGHT's vectors, with the uncertainty threshold additionally scaled by the `domain` and `criticality` declared at PREFLIGHT (`_get_domain_scaled_thresholds()`: higher criticality gives a stricter bar) |
| Sentinel hook, after a CHECK | Both thresholds against the CHECK's vectors; a miss is an advisory, not a denial |

Domain scaling is applied only at the auto-proceed step in the hook. `check-submit` and the post-CHECK comparison use the unscaled dynamic threshold.

### Self-correcting behaviour

```
New practice:        baseline gates, no inflation (fewer than 5 points)
Well calibrated:     reliability near 0, gates stay at baseline
Overconfident:       reliability rises, know raised and uncertainty tolerance lowered by up to max_inflation
                     (a stricter gate forces more investigation before action)
```

---

## Feedback to the practitioner

All of these inform work discipline. None adjusts a vector for the practitioner.

- **POSTFLIGHT** returns an `evidence_summary`, a `calibration_reflection` (a narrative of what the evidence showed, carrying the calibration insights below and per-transaction `epistemic_provenance` counts of intuition- versus search-sourced artifacts), `phase_aware` and `phase_weights`. The scores and gaps are stored under underscore-prefixed keys, marked not for optimization.
- **Calibration insights.** `CalibrationInsightsAnalyzer` looks at the last 10 grounded verifications and needs at least 5 before reporting anything. It reports patterns at severity 0.3 or higher:

  | Pattern | Detected when |
  |---------|---------------|
  | `chronic_overestimate` / `chronic_underestimate` | The same vector has a gap beyond 0.05 in the same direction in more than 70% of records |
  | `evidence_gap` | A groundable vector has evidence in fewer than 30% of records |
  | `phase_mismatch` | A vector's mean gap is more than 2x larger in one phase than the other (at least 3 records per phase) |
  | `volatile` | The sign of the gap flips in more than half of consecutive pairs |

  Insights are stored in `calibration_insights` with an `acted_on` flag, exported to `.breadcrumbs.yaml`, and folded into the POSTFLIGHT reflection. They are prompts to investigate, not corrections: a chronic overestimate may reflect a proxy that cannot see deep understanding rather than a miscalibrated practitioner.
- **PREFLIGHT** returns `previous_transaction_feedback` for the last POSTFLIGHT: artifact gaps, suggestions, a calibration trend, and a retrospective gate when a transaction made many tool calls and logged no artifacts. `EMPIRICA_CALIBRATION_FEEDBACK=false` suppresses this feedback; it never changes gating.
- **Session start.** The calibration export in `.breadcrumbs.yaml` (a generated file; this repo gitignores it) is injected as the bias block at session start and after compaction.

### Check outcomes

When a transaction predicted the outcomes of its compliance checks, POSTFLIGHT's `compliance` block carries a `check_brier`: the Brier score of the predicted pass probability against whether each check passed (`compute_check_brier()`). That one is a falsifiable prediction with a ground-truth outcome. It is reported; it does not feed the thresholds.

---

## Reading the calibration

```bash
empirica calibration-report                     # grounded divergence over all history
empirica calibration-report --windowed --weeks 4  # adds the gap over the last N weeks, under its own key
empirica calibration-report --brier             # Brier decomposition per phase
empirica calibration-report --trajectory        # closing / widening / stable
empirica calibration-report --learning-trajectory  # PREFLIGHT to POSTFLIGHT deltas (learning, not calibration)
```

`--windowed` recomputes the self-versus-grounded gap from `grounded_verifications` inside the window. It is a different quantity from the all-time divergence (per-verification means rather than aggregated belief means) and is reported separately, never merged. `--weeks` applies to `--windowed`, `--trajectory` and `--learning-trajectory`; the default report covers all history, and its JSON says so (`window.applied: false`).

**`calibration_exclusions`.** A practice can declare known-bad measurement windows in `.empirica/project.yaml`:

```yaml
calibration_exclusions:
  - vectors: [change]        # required: known vector names
    source: some_source      # optional grounding source; at least one of source/from/until is required
    from: 2026-09-01         # optional, inclusive
    until: 2026-09-20        # optional, exclusive
    reason: why this window is unreliable
```

An entry that cannot be read is dropped with a warning rather than read as "exclude everything". The grounded belief for an affected vector is replayed without the matching observations (stored rows are untouched), and the report and the injected bias block both say what was left out. `--windowed` does not apply exclusions.

`empirica domain-validate` checks the domain registry (`(work_type, domain, criticality)` mapped to compliance checklists) that supplies the criticality scaling and the POSTFLIGHT compliance loop.

---

## Design principles

1. **CHECK is the boundary.** The split uses a gate that already exists.
2. **Absence is evidence.** "Searched and found nothing" is noetic signal.
3. **Services inform, the practitioner calibrates.** Deterministic services produce observed vectors; the practitioner's beliefs are never overwritten, and the divergence between them is the signal.
4. **Gates only tighten.** Miscalibration raises the bar; good calibration keeps the baseline.
5. **Phase-specific.** Noetic and praxic competence are separate trajectories.
6. **Honest absence over a false number.** A source that cannot see the work is excluded rather than defaulted.
7. **Human override.** Dynamic thresholds adjust the practitioner's autonomy, not human authority.
