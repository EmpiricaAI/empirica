# Practitioner Deliberation Model

**Status:** partly built. The identity, presence and calibration-keying slices exist; the arbitration and credibility-weighting slices are design only. Section 5 lists each slice against the code.

**Lanes:** empirica core (entity and Brier surfacing), autonomy (arbitration, shrinkage, gating semantics), cortex (mesh addressing). Anything in the "not built" column needs ratification by the owning lane before it is built.

The model: a practice is durable and shared, practitioners are individually calibrated participants in it, and a practice's decisions can come from several practitioners' attributed reads, weighted by how reliable each has been, rather than from a flat merge.

## 1. The ontology

The axis is shared (practice) versus individual (practitioner).

| Concept | Identity key | Durability | Individual to it | Shared or inherited |
|---|---|---|---|---|
| **Practice** | `ai_id` (canonical `org.tenant.project` on the mesh) | durable; outlives any practitioner | the aggregate calibration profile | the knowledge graph: artifacts, goals, sources, lessons, skills, spawnable agents |
| **Practitioner** | see the two keys below | ephemeral identity, durable state | presence, conversation summary, its own trajectory points | artifacts it logs merge up; retrieval is shared at practice level |
| **Agent / subagent** | transient per spawn | ephemeral | nothing persistent; work rolls up to the spawner | inherits the practice context for the task |
| **Skill** | name | durable, stateless | none | a loadable capability, not an epistemic actor |
| **Epistemic profile** | layered | layered | practitioner layer: trajectory points | practice layer: artifacts and aggregate calibration |

Containment: agent within the practitioner that spawned it, practitioner within the practice it occupies. A skill is orthogonal.

### Two keys for "practitioner" in today's code

The word currently names two different things, and the code keys them differently:

- **The conversation.** `claude_session_id` is the durable key for presence and for the ERM practitioner entity (`empirica/core/practitioner_presence.py`, `WorkspaceDBRepository.upsert_practitioner_entity`). It survives compaction. The empirica `session_id` does not: it rotates per compact window because it contains measurement cycles.
- **The model.** `practitioner_model` is the model id read from the last assistant line of the Claude Code transcript when a transaction closes (`empirica/utils/practitioner_model.py`). It is stored on `calibration_trajectory` and `grounded_verifications` (migration 074), nullable and not back-filled, and is what the CHECK gate keys on.

Calibration therefore accrues to the model within the practice. A human seat is not an axis anywhere yet.

## 2. Mesh addressing: address the practice, attribute the practitioner

The mesh addresses practices (`source_claude` and `target_claudes` carry canonical ai_ids). That stays the default, since the practice is the accountable unit and holds the shared knowledge. Three layers:

- **Default, practice-addressed.** A proposal goes to the practice; whichever practitioner is live picks it up.
- **Optional, practitioner-addressed.** To continue a thread with the practitioner holding its context. Presence resolves a practice to its live practitioners (`empirica practitioner list`). It should degrade to practice addressing when that practitioner is gone. No practitioner addressing is implemented on the mesh path I read.
- **Always, practitioner-attributed.** Within a practice's handling, each practitioner's read is tagged with who and how reliable.

## 3. Reliability: a vector, with shrinkage

A practitioner's reliability relative to the practice is meant to act as a weight: better calibrated than the practice, fold the contribution up; worse, discount it. The practice profile becomes a reliability-weighted ensemble.

Brier alone is too thin. The signals the arbiter would weigh, and where each comes from today:

| Signal | Source today |
|---|---|
| Brier and calibration | `calibration_trajectory` points; `get_brier_profile` per practice, `get_practitioner_brier_profile` per session |
| Coverage | artifact and goal footprint per session |
| Age and maturity | session lifetime, cycle count |
| Artifact attribution | `finding_refs`, artifact authorship |
| Lineage and track record | gap history, drift, phase boundaries |

Only the first is exposed as a function. The rest are data that exists, not signals anything combines.

**Shrinkage design (autonomy's position, a position to calibrate against):**

- Prior is the practice aggregate profile.
- Credibility weight is Bühlmann `w = n / (n + k)` with `k` the ratio of within-practitioner to between-practitioner variance, so the half-credibility point is earned from data. One boolean floor: `n < n_min` gives `w = 0`.
- Asymmetric: shrink a thin practitioner claiming better-than-practice harder than one claiming worse, driven by the standard error of the practitioner's Brier. Uncertainty defaults toward the practice prior.

None of this exists in code. `compute_practitioner_divergence` returns the raw per-phase deltas and per-side Brier variances so a consumer can form `SE = sqrt(variance / n)`, and its docstring names the weighting as autonomy's lane.

## 4. Deliberation

A deliberation is the set of attributed practitioner reads on one engagement. Sentinel, or an arbiter acting for it, would pick a direction on reliability and on the engagement's feasibility, and the winning direction would fold back into the practice weighted, not flat.

Positions autonomy anchored (design, not built):

- **Trigger:** arbitration is CHECK at the deliberation layer, at the praxic boundary: an ECO-gated proposal graduates, a SER reaches a decision state, or a fold-back commits. Never on read convergence, because agreement is not authority.
- **Fold:** weight at query time by default, so the raw trajectory points stay the source of truth and a corrected shrinkage model can recompute the fold. Mutating the profile is a gated promotion of a repeatedly confirmed direction, never a side effect.
- **The arbiter obeys the floor it enforces:** fail closed to the flat practice prior and escalate on no arbiter, a tie, or a sub-floor sample; a practitioner cannot arbitrate in favor of its own read; the feasibility vector (`do`) can veto, not only down-weight.
- **Parity:** arbitration attaches its basis as a recorded field (`arbitration_basis`, parallel to `autonomy_verdict_basis`) and must not change the underlying gate's outcome semantics.

CRM note: engagements are canonical in crm-mcp. A deliberation read stores only the engagement id on an edge, which is a join id and stays valid; it never reads engagement state from `entity_registry`.

## 5. Build state per slice

| Slice | What it is | State |
|---|---|---|
| B2 presence | `empirica practitioner write\|clear\|list\|heartbeat`; presence files keyed on `claude_session_id`; heartbeat to cortex | built |
| B4 practitioner entity | `upsert_practitioner_entity` writes `entity_registry` (`entity_type='practitioner'`) and an `occupies` edge to the practice; `list_practitioner_entities`. `practitioner write` calls it best-effort on every presence write | built. `summary` and `trajectory_pointer` are supported but the caller never passes them, so they stay empty |
| Calibration keyed on practitioner model | `compute_dynamic_thresholds(..., practitioner_model=...)` uses that model's points when it has at least `min_transactions`, else the practice's; each phase reports `basis`, `practitioner_points`, `lookback`. CHECK passes the current model | built |
| B5 reliability view | `get_practitioner_brier_profile` and `compute_practitioner_divergence` | functions only: no CLI, hook or statusline calls them. They filter by empirica `session_id`, which rotates per compact window, so "per practitioner" currently means per session window, not per conversation |
| B6 deliberation record | `record_deliberation_read` writes a `contributes_to` edge (practitioner to engagement) with the read summary as the edge note; `get_deliberation` returns reads oldest first, LEFT JOINed to the practitioner entity | repository methods and unit tests only; nothing in the CLI or hooks records a read |
| B7 arbitration and fold | multi-signal weighting, Bühlmann shrinkage, `arbitration_basis`, feasibility veto, reliability-weighted fold | not built |

## 6. Open

- Whether the conversation summary should be a first-class practitioner attribute, wired from Claude Code into the presence and entity record. Predicted answer: yes, small, since the entity already has the field.
- Reconciling the two keys in section 1. B5 and B6 are keyed on the conversation or session while calibration is keyed on the model; the arbiter needs one answer to "who is this practitioner".
- A human practitioner axis. `practitioner_model` names the model, not the seat.
