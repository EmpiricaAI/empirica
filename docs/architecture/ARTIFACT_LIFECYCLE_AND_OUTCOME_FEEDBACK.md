# Artifact Lifecycle and Outcome Feedback

Every artifact type that can steer later behaviour needs an event that says "this turned out to be wrong". This document describes how that is wired today: which transitions each type has, how outcomes flow back to sources, and what is still not connected. It supersedes the July 2026 draft spec of the same name.

Related: `ARTIFACT_HYGIENE.md`, the `/epistemic-gardening` skill, `empirica/core/falsifiers.py`, `empirica/core/sources/sanctify.py`.

## The problem it solves

Retrieval puts artifacts into future sessions as grounding. Types that assert positive knowledge (findings, unknowns, assumptions) were always revisable. Types that assert a permanent constraint (dead-ends, mistakes) had no lifecycle columns at all, and decisions had outcome columns that nothing wrote. A wrong dead-end silently removes a viable approach from the option space, and a wrong prevention advice keeps steering. Nothing ever retries a dead-end, so no event could contradict it. Stale or wrong artifacts are not neutral; they mis-steer, and the longer they sit the more sessions inherit them.

## Principles the code follows

1. Every type that can steer behaviour is falsifiable.
2. Scores are derived on read, never stored. Outcomes are recorded as events; relevance, accuracy and stability are computed from them (`derive_standing`), so the formula can change without a migration.
3. Attribution is declared, not inferred. An artifact can fail because its source was wrong or because the reasoning from it was wrong; blame is recorded only when the caller names the source.
4. Closing is cheap. Every transition is a field on a batch verb people already run, not a new verb.
5. "Never revisited" is a different state from "confirmed". New columns are nullable and existing rows are not back-filled into a verdict.

## Transitions per type

All transitions go through `empirica resolve-artifacts` (`empirica resolve-artifacts --schema` prints the payload). There are no `deadend-invalidate`, `mistake-assess` or `decision-assess` verbs.

| Type | Transition | Fields written | Meaning |
|---|---|---|---|
| finding | `resolve-artifacts` or `finding-resolve` | `is_resolved`, `resolution`, `resolved_timestamp`, `superseded_by`, `resolution_kind` | closed. `resolution_kind` is a closed vocabulary: `stale` (was true, aged), `superseded` (replaced by a named artifact), `retracted` (was false when written), `mistyped` (belongs to another type) |
| unknown | `resolve-artifacts` or `unknown-resolve` | `is_resolved`, resolver text, `resolved_timestamp` | answered |
| assumption | `resolve-artifacts` | status, optional `verified` | verified or falsified |
| goal | `resolve-artifacts` | completion with a reason | done |
| dead_end, mistake | `resolve-artifacts` (`invalidated_by` optional) | `is_invalidated`, `invalidated_at`, `invalidated_by`, `invalidation_reason`, `last_revisited_at` | no longer actionable: the approach works after all, or the prevention does not hold or no longer applies. One shape for both, on purpose: "was wrong" and "no longer applies" are the same state for a reader |
| decision | `resolve-artifacts` with `outcome` | `outcome` (`upheld`, `reversed`, `mixed`; required), `outcome_assessed_at`, `regret_score` | what the choice produced. `regret` is 0 to 1 and self-assessed, not derived from outcome and reversibility |
| lesson | `resolve-artifacts` with `superseded_by` | supersession edge | a lesson is retired only by a named successor |

Migration 060 (`migration_060_artifact_falsifiability`) added the invalidation columns to `project_dead_ends` and `mistakes_made`, a `domain` column on dead-ends (so staleness can be judged per domain, since a dead-end about a fast-moving dependency rots faster than one about arithmetic), and `derived_from` on `blindspot_events`. Retrieval over memory skips invalidated dead-ends and mistakes (`memory_manager.py`).

### Bulk mode for gardening

`resolve-artifacts` also takes a `filter` block (`type`, `project_id`, `older_than`, `matching` as a SQL LIKE pattern) plus `resolution`. It is a dry run unless `"apply": true`. For `finding` and `unknown` it resolves; for `dead_end` and `mistake` it invalidates. Filter mode mirrors finding and unknown resolutions into git notes; dead-end and mistake invalidations in filter mode are SQLite-only, so a from-notes rebuild would show them as valid again (`_persist_filter_resolution_to_notes` says so). I did not find a notes write for the per-id path either; treat invalidation of these two types as unreplicated.

The gardening skill still tells practitioners not to resolve or delete dead-ends and mistakes outside literal duplicates and test noise. Invalidation is for the case where something showed the constraint false.

### Falsifiers

A falsifier names the observation that would refute a belief, registered at PREFLIGHT or CHECK against a finding, assumption, decision, dead-end, mistake or lesson (`PARENT_TABLES` in `falsifiers.py`; unknowns are excluded because they assert nothing). It stays `registered` and is re-surfaced at later PREFLIGHTs until a POSTFLIGHT adjudicates it as `tripped`, `survived` or `expired`. `survived` without evidence is recorded as `expired`. `empirica falsifier-list [--state registered|tripped|survived|expired|all]` lists them.

One gap against the older description: adjudicating a falsifier `tripped` updates the falsifier row and nothing else. It does not set `is_invalidated` on the parent. Invalidating the parent is a separate `resolve-artifacts` call by a person or the practice.

## Blindspots

A blindspot is inferred, so it inherits the fate of its premises. `assess_blindspot_inputs` in `sanctify.py` returns `stands`, `stale_inputs` or `unknown_provenance` from a `derived_from` list and the set of invalidated ids (stale at a ratio of 0.5 by default). It deliberately recommends re-derivation and never auto-invalidates, because deleting an unknown-unknown is the worst failure direction.

Not connected: the column and the function exist, and a test covers the function, but nothing in the CLI calls it and `blindspot-scan` does not write `derived_from`. Every blindspot is therefore `unknown_provenance` in practice.

## Source outcome feedback

When `resolve-artifacts` closes a finding, invalidates a dead-end or mistake, or assesses a decision, `_record_source_outcomes` appends a `source_outcome` event to the `lifecycle_audit_log` of every source the artifact cites through a `sourced_from` edge:

```json
{"event": "source_outcome", "at": 0, "artifact_id": "...", "artifact_type": "finding",
 "outcome": "confirmed|invalidated|superseded|retracted", "implicated": false}
```

`implicated` is true only when the item carries `source_implicated` (a list of source ids, or `true` for every cited source). The write is fail-open: a bookkeeping failure never blocks the resolution.

Outcome per transition: finding with `superseded_by` is `superseded`; finding with `resolution_kind: retracted` is `retracted`; any other finding resolution is `confirmed`; dead-end or mistake invalidation is `invalidated`; decision `upheld` is `confirmed`, other decision outcomes are `invalidated`.

### Derived standing

`derive_standing(outcome_events, citation_count, last_reviewed_at, now)` computes, without storing anything:

| Metric | Derived from |
|---|---|
| relevance | citation count and number of observed outcomes; `uncited` is reported as a state |
| accuracy | `confirmed` events against `invalidated` events with `implicated: true`; `None` when nothing is judged |
| stability | share of events that are `superseded` or `invalidated` |
| review age | `last_reviewed_at`, which `sources-check` stamps per source (default re-probe window from the practice's `hygiene_policy.source_staleness_days`) |

`sources-check` prints a corpus rollup (scored, uncited, never reviewed, review overdue, implicated failures) using these.

Two consequences of the event vocabulary, from reading the code rather than from a run:

- `retracted` is in neither the negative nor the moved set in `sanctify.py`, so a retracted finding does not lower a source's accuracy even when the source is declared implicated, and does not count against its stability.
- A finding closed as `stale` without `superseded_by` is recorded as `confirmed` for its sources.

## What is still open

- Per-domain staleness windows for dead-ends: the column exists, no window logic reads it.
- Gardening prompts for "dead-ends never revisited", "decisions never assessed" and "mistakes whose prevention was never validated": the columns make them queryable, but I found no surface that asks them.
- Blindspot propagation (above).
- Feeding outcome events into calibration: not built; the events carry a timestamp but no actor field.
- Whether a re-derived mistake should link back to the one it replaced.
