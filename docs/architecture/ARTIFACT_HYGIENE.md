# Artifact Hygiene — the cross-transaction, per-practice sweep

> **Status: design spec.** Companion to [GATED_ARTIFACT_GRAPH.md](GATED_ARTIFACT_GRAPH.md).
> That map governs *within-transaction* connectivity at POSTFLIGHT; this one
> governs the *whole-practice, cross-transaction* upkeep POSTFLIGHT structurally
> can't see. The primitives already exist — this is an **orchestration + policy**
> problem, not a build-from-scratch.

## 1. The problem

Epistemic artifacts decay across transactions in ways a single POSTFLIGHT can't
observe:

- **Unknowns** that a later finding/decision already answered, never resolved.
- **Goals** with completion evidence (a commit, a passing test) still sitting
  `in_progress`; or stale/abandoned; or duplicated.
- **Sources** whose URLs 404 or moved (link-rot), or whose *content* drifted.
- **Edges** — orphan artifacts (0 edges), or artifacts wired to a weaker edge
  than the graph now warrants.
- **Noise** — test-run artifacts, exact duplicates, superseded findings.

POSTFLIGHT already does the *within-transaction* half — weave-gate (orphans /
connectivity), breadth, edge-density, sources-discipline, deferred-proposals.
What's missing is the **whole-practice sweep**: link-check across every source,
cross-goal dedup, long-open-unknown triage, orphan re-wiring across transactions.

## 2. What already exists (build on these, don't reinvent)

| Primitive | Does |
|---|---|
| `empirica goals-get-stale` | detect stale goals (dry-run detection) |
| `empirica resolve-artifacts -` | batch-resolve unknowns / assumptions / goals |
| `empirica delete-artifacts -` | batch delete — **dry-run default + receipt logged as a decision for audit** |
| `empirica log-artifacts -` | node + edge writes (edge re-wiring) |
| `empirica sources-reconcile` | source-identity dedup vs the catalogue (`--apply`, dry-run default) |
| `empirica docs-link-check` | link-rot detection for docs (extend to sources — §7) |
| POSTFLIGHT hooks | within-tx: weave-gate, breadth, edge-density, sources-discipline, deferred-proposals |

The hygiene loop **wires these together** across the whole practice; it does not
add new destructive primitives.

## 3. The model — two tiers, split by reversibility

| Tier | Examples | Behavior |
|---|---|---|
| **Mechanical** (deterministic detection) | dead source links (404 probe), exact-duplicate artifacts, evidence-backed goals still open, source-identity drift | May **act** — but only through `delete-artifacts`' existing dry-run → receipt path |
| **Semantic** (needs judgment) | "unknown X is answered by finding Y", "artifact should move to edge Z", source *content* staleness (not just link) | **Surface a triage queue** — never auto-act |

**Decided default (surface + safe-mechanical auto):** auto-action is limited to
the provably-safe mechanical cases; everything semantic becomes a triage queue
the practitioner approves. This mirrors the artifact-graph gate's report-only
default — earned autonomy, dialed by policy (§4).

## 4. Per-practice — one loop body, per-practice via config

Artifact hygiene is **per-practice** — a research practice's open unknowns *are*
the work (don't reap them); a code practice should close evidence-backed goals
fast; outreach lives and dies by source freshness (links rot weekly). The
resolution is **not per-practice loop code** — it's **one canonical loop body
that reads a per-practice `hygiene_policy`**, the exact pattern the artifact-graph
gate uses for its `strictness / connectivity_floor / patience` scalars (#253).

`.empirica/project.yaml`:

```yaml
hygiene_policy:
  source_staleness_days: 30      # outreach: 7 · code: 90
  unknown_triage_days: 14        # research: 60 (unknowns ARE the work) · code: 7
  goal_auto_close: evidence_only # evidence_only | surface_only
  auto_delete: test_noise_only   # off | test_noise_only  — NEVER semantic-on-age
  dedup: exact_only              # exact_only | fuzzy
```

Per-`work_type` defaults seed it (research vs code vs outreach differ exactly as
you'd expect); the practice overrides. A `_resolve_hygiene_policy()` reader
mirrors `_resolve_gate_scalars()` — single source of truth for defaults + config,
clamped, fallback-safe.

## 5. The load-bearing safety rule

`message-cleanup` (the sibling loop) is safe because messages carry an explicit
`expiry_at` — deletion is contractual, not a judgment. **Artifacts have no
expiry.** "Stale" is a judgment, and deleting a finding/unknown *loses
knowledge*. Therefore:

- **Default surface-not-act.** Auto-action only on provably-safe mechanical cases.
- **Never auto-delete a finding/unknown on age alone.** Age flags for *triage*, it
  does not authorize deletion.
- **Keep the `delete-artifacts` dry-run → receipt discipline.** Every auto-delete
  is dry-run-previewed and logs a decision artifact — the audit trail is the
  guardrail, and the loop respects it.
- **Reversibility gradient.** Flag (free) < re-wire edge (reversible) < resolve
  unknown (reversible) < delete (destructive, receipted). Auto-action climbs the
  gradient only as far as `hygiene_policy` authorizes.

## 6. Delivery — loop **and** skill

Decided: **both**, each for what it's good at.

- **Canonical loop** (`artifact-hygiene`, low-frequency ~daily) — the cheap
  mechanical sweep + triage-queue emission. Self-throttles when a transaction is
  open (like `cortex-mailbox-poll` / `message-cleanup`). Emits a **hygiene
  receipt** (N dead sources, M closable goals, K unknowns to triage, L orphans)
  — surfaced, not silently actioned.
- **On-demand skill** (`/artifact-hygiene`) — the deep semantic pass, run when
  wanted (like `/code-audit`, `/eat-the-broccoli`). Does the judgment work the
  loop deliberately defers: which unknowns look answered, which edges to re-wire,
  which sources drifted in content.

Loop for continuous mechanical upkeep; skill for the deep pass. They share the
`hygiene_policy` and the receipt format.

## 7. Net-new work (the gaps — each a work-stream)

1. **Source link-rot check.** Extend `docs-link-check`'s URL probe to the
   `sources` table → flag 404 / moved (dead-ref), gated by
   `source_staleness_days`. Smallest, safest slice — mechanical, surface-only.
2. **`hygiene_policy` schema + resolver.** `_resolve_hygiene_policy()` mirroring
   `_resolve_gate_scalars` (#253): defaults + env/config, per-`work_type` seeds,
   clamped, fallback-safe.
3. **The canonical loop body.** Orchestrates the existing verbs + emits the
   hygiene receipt / triage queue. Registered via the canonical-loop catalog
   (`canonical_loops.py`), installed like `message-cleanup`.
4. **Semantic-candidate surfacing.** "Which unknowns look answered / which edges
   to re-wire" via the artifact graph + semantic search — the judgment layer the
   `/artifact-hygiene` skill drives.

## 8. Relationship to the artifact-graph map

This is the **cross-transaction complement** to GATED_ARTIFACT_GRAPH.md:

| | Artifact-graph gate (that map) | Artifact hygiene (this spec) |
|---|---|---|
| **Scope** | one transaction, at POSTFLIGHT | whole practice, across transactions |
| **When** | at the gate (write time) | on a loop / on demand |
| **Concern** | *is this transaction's work woven?* | *has the whole graph decayed?* |
| **Shared** | the per-practice scalar/policy config pattern; connectivity/orphan vocabulary |

The gate keeps *new* work connected; hygiene keeps the *accumulated* graph clean.
Orphan re-wiring is where they meet — the gate flags an orphan at write time; the
hygiene sweep re-homes orphans the gate flagged in past transactions.

## 9. Decisions (settled) + open questions

**Settled:**
- Autonomy default = **surface + safe-mechanical auto** (semantic → triage queue).
- Delivery = **both** canonical loop + `/artifact-hygiene` skill.

**Open:**
- `hygiene_policy` home — `.empirica/project.yaml` (per-practice, filesystem) vs
  the `entity_registry` practice row (DB, travels with the practice id)? Lean
  project.yaml for parity with the gate scalars, but the practice-model row is
  the more canonical home long-term.
- Loop cadence — fixed daily vs adaptive (backoff when the receipt is empty, like
  `cortex-mailbox-poll`)? Link-check is network-slow; daily is likely enough.
- Semantic-match confidence bar for auto-surfacing "unknown answered by finding"
  — reuse the artifact-graph edge-inference, or a dedicated semantic threshold?
