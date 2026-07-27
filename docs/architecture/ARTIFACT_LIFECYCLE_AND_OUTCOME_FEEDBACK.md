# Artifact Lifecycle & Outcome Feedback — Spec

**Status:** DRAFT for review · **Owner:** empirica-core · **Requested by:** David (2026-07-27)
**Related:** decision `f5c59ec8` (sources as ongoing ground truth) · `ARTIFACT_HYGIENE.md` · `/epistemic-gardening`

---

## 1. The problem, measured

The epistemic graph has **asymmetric falsifiability**. Artifact types that assert
*positive* knowledge can be revised. Types that assert a *permanent constraint* — the
ones that steer future behaviour hardest — cannot be revised at all.

Measured on empirica, 2026-07-27:

| type | rows | lifecycle columns | ever closed |
|---|---:|---|---:|
| finding | 4163 | `is_resolved`, `resolution`, `resolved_timestamp`, `superseded_by` | 1267 |
| unknown | 473 | `is_resolved`, `resolved_timestamp` | 460 |
| blindspot | 33 | `outcome`, `resolved_timestamp` | 33 |
| assumption | 56 | `status`, `resolved_timestamp` | — |
| **decision** | **486** | `outcome`, `outcome_assessed_at`, `regret_score` | **0** |
| **dead_end** | **750** | **none** | — |
| **mistake** | **133** | **none** | — |

Three distinct failures:

1. **`dead_end` (750) and `mistake` (133) have no lifecycle whatsoever.** They are
   permanent *negative* guidance — "approach X failed", "I did Y wrong, prevention: Z"
   — retrieved into future sessions to steer practitioners away. **Nothing ever
   retries a dead-end**, so a mistaken one is invisible *by construction*: there is no
   event that could ever contradict it. A wrong dead-end silently removes a viable
   approach from the practice's option space, forever.

2. **`decision` (486) has the columns and no surface.** `outcome`,
   `outcome_assessed_at` and `regret_score` exist — someone designed this loop — but
   nothing writes them. **Zero of 486 decisions have ever been assessed against what
   actually happened.** Reversibility is recorded at decision time; consequence never
   is.

3. **Nothing flows back to sources.** A source's relevance/accuracy/stability should
   be evidenced by what happened to the artifacts citing it. Today that channel does
   not exist, so source quality is unmeasurable (see §5).

> **Why this matters more than it looks.** Retrieval surfaces these artifacts into
> future sessions as *grounding*. An unfalsifiable wrong artifact is not neutral — it
> is actively mis-steering, and it compounds: the longer it sits, the more sessions
> inherit it. This is the mechanism behind "stale and dead artifacts poison the well".

---

## 2. Design principles

1. **Every artifact type is falsifiable.** If an artifact can steer future behaviour,
   there must exist an event that says "this turned out to be wrong."
2. **Derived, never stored, scores.** Outcomes are recorded as events; relevance /
   accuracy / stability are computed on read. A stored score is an asserted number
   that drifts from its evidence — the exact failure empirica exists to prevent.
   Corollary: the formula can change without migrating data.
3. **Attribution is declared, not inferred.** An artifact can fail because its
   *source* was wrong or because the *reasoning* was wrong. Inferring blame from
   invalidation would systematically slander good sources. Blame is only recorded when
   someone asserts it.
4. **Closing is cheap, or it will not happen.** 0/486 decisions assessed is what an
   expensive path produces. Every transition must be one flag on a command someone
   already runs.
5. **Absence of evidence is a first-class state.** "Never revisited" is different from
   "confirmed good", and gardening must be able to tell them apart.

---

## 3. Per-type lifecycle

Terminal states per type. All transitions record `{actor, at, rationale}`.

| type | states to add | transition | meaning |
|---|---|---|---|
| **dead_end** | `is_invalidated`, `invalidated_by`, `invalidated_at`, `invalidation_reason` | `deadend-invalidate` | we retried the approach and it WORKS — the constraint was false |
| **mistake** | `is_superseded`, `superseded_by`, `prevention_verdict` | `mistake-assess` | the prevention advice held / did not hold / no longer applies |
| **decision** | *(columns exist)* | `decision-assess --outcome upheld\|reversed\|mixed [--regret 0-1]` | what the choice actually produced |
| **assumption** | *(has `status`)* | existing `resolve-artifacts` | verified / falsified |
| **finding** | *(complete)* | existing `finding-resolve` | resolved / superseded |
| **unknown** | *(complete)* | existing `unknown-resolve` | answered |
| **blindspot** | `invalidated_by_inputs` (derived, §4) | propagation | its inputs no longer hold |

**Naming discipline:** these are *flags on existing verbs* wherever possible, not new
verbs (`resolve-artifacts` gains `dead_end`, `mistake`, `decision` types). New verbs
only where no batch path fits.

---

## 4. Blindspot propagation — the derived case

A blindspot is **not observed, it is inferred** — `blindspot-scan` derives it from the
pattern across other artifacts. So its validity is *downstream* of its inputs:

> If the artifacts a blindspot was derived from are invalidated, the blindspot is
> suspect. It is a conclusion, and conclusions inherit the fate of their premises.

**Rule:** a blindspot records the artifact ids it was derived from
(`derived_from[]`). When ≥ *N* of those inputs are invalidated/superseded, the
blindspot is flagged `stale_inputs` — **not** auto-invalidated. It is re-scanned, and
a human or the practice decides.

Auto-invalidation is deliberately rejected: a blindspot can remain true even when a
supporting finding was wrong, and silently deleting an unknown-unknown is the worst
possible failure direction. **Flag, re-derive, decide.**

This also means `blindspot-scan` must persist its inputs, which it does not do today.

---

## 5. Source outcome feedback

Once artifacts have outcomes, they flow to the sources they cite via `sourced_from`.

**Recording** — append to the source's `lifecycle_audit_log` (already exists, already
holds `repointed` and archive events):

```json
{"event": "source_outcome", "at": ..., "artifact_id": "...", "artifact_type": "finding",
 "outcome": "confirmed|invalidated|superseded", "implicated": true}
```

`implicated` is only ever `true` when declared via `--source-implicated <id>` at
resolution time (principle 3).

**Derived metrics** (computed on read, never stored):

| metric | derived from |
|---|---|
| **relevance** | citation count + recency of citing artifacts |
| **accuracy** | confirmed vs **implicated**-invalidated outcomes |
| **stability** | rate of citing artifacts going stale + `content_hash` changes across reviews |
| **standing** | review age (`last_reviewed_at`) — unreviewed is not "good", it is unknown |

**Sparsity note:** fleet-wide there are 446 sources and 38 citations. Any *statistical*
score is noise at this volume; an *event trail* is useful from the first event. This is
a second, independent reason for principle 2.

---

## 6. Gardening integration

Gardening becomes the consumer, and gains the questions it currently cannot ask:

- dead-ends never revisited, older than N — *candidates for retry*, not deletion
- decisions never assessed (today: **all 486**)
- mistakes whose prevention was never validated
- blindspots with `stale_inputs`
- sources: uncited, unreviewed, or implicated-inaccurate

Each is a **prompt**, not an automatic action. Prune *and replant*: the point is to
retry a suspect dead-end, not to delete the record of it.

---

## 7. Rollout

Non-breaking and incremental; each phase is independently useful.

| phase | content | unblocks |
|---|---|---|
| **1** | migration: lifecycle columns for `dead_end` + `mistake`; `blindspot.derived_from` | everything |
| **2** | transitions: `deadend-invalidate`, `mistake-assess`, `decision-assess`; extend `resolve-artifacts` to the new types | closing the 1369 unfalsifiable artifacts |
| **3** | source outcome recording (§5) + `--source-implicated` | source accuracy |
| **4** | derived metrics + gardening surfaces (§6) | acting on it |
| **5** | blindspot propagation (§4) | derived-artifact integrity |

Existing artifacts keep working throughout — every new column is nullable, and
"never assessed" is a legitimate, queryable state (principle 5).

---

## 8. Resolved decisions

All four settled by David, 2026-07-27.

1. **Retry cadence for dead-ends → DOMAIN-SCOPED.** Age alone is weak evidence, so
   staleness is evaluated per domain: a dead-end about a fast-moving dependency rots
   far faster than one about arithmetic. Implemented as `project_dead_ends.domain`
   (migration 060); the per-domain windows themselves are a Phase 4 tuning question,
   deliberately not hard-coded now.

2. **`regret_score` → SELF-ASSESSED 0–1.** Trust the practitioner's own assessment
   rather than deriving it from outcome × reversibility. This is consistent with how
   the rest of empirica works — vectors are self-reported beliefs, and evidence
   *informs* them rather than overriding them. A derived regret would be an asserted
   number wearing the costume of a measurement.

3. **Mistake supersession vs invalidation → ONE STATE.** "No longer applies" and "was
   wrong" both mean *not actionable*, so both invalidate; re-derive the mistake
   afterwards if it is still pertinent. Two states nobody could reliably tell apart
   would be worse than one that is always clear. This is why `dead_end` and `mistake`
   share an identical invalidation shape in migration 060.

4. **Calibration feed → EVENTUALLY, NOT V1.** Out of scope here, but the event shape
   must not preclude it: outcome events carry actor + timestamp so a later calibration
   consumer can read them without a migration.

### Still open (deferred, not blocking)

- Per-domain staleness windows for dead-ends (Phase 4, needs data on which domains
  actually rot).
- Whether a re-derived mistake should link back to the invalidated one
  (`superseded_by`-style provenance) or stand alone.

---

*Written against measured state, not assumption — every count in §1 is from a live
read of the practice DB on 2026-07-27.*
