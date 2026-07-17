# Idempotent-by-Design — W1a Spec

**Status:** DRAFT for mesh convergence · **Owner:** empirica-core · **Co-dev:** cortex (executor-side)
**Roadmap:** Mesh coordination-consistency, workstream **W1a** (emitter-side half)
**Composes with:** W1b (is-anyone-acting pre-check, cortex) · W1c (optimistic leases, cortex)

---

## 1. Problem

Practices act in parallel faster than any one narrates. Two incidents motivated the roadmap:

1. A manual `reconcile` executed while a *drafted reply about reconciling it* was still in flight — the reply narrated work already done.
2. A 9-project batch executed while a reply said "hold off."

The shared shape: **an action fires, and something else re-issues or narrates the same action** because it couldn't see the first had landed. The mesh already routes, calibrates, and gates (ECO); what it lacks is a guarantee that a re-issued action is *harmless*.

W1a is the **emitter-side** half of the fix: make every actionable proposal **safe to run twice**. W1b/W1c are the executor-side half (detect + prevent the parallel double-claim). W1a is the invariant that holds even when W1b/W1c race — belt to their suspenders.

---

## 2. The convention

> **Every actionable `cortex_propose` SHOULD be safe to apply more than once.**
> Bake the question *"what happens if this runs twice?"* into the propose discipline itself, the way `--epistemic-source` bakes provenance in.

An actionable proposal (`code_change_request`, `architecture_decision`, `investigation_request`, `publish`, …) carries an **`idempotency_key`**: a stable, semantic identifier of the **action**, not the emission. Two proposals requesting the *same underlying action* produce the *same key*.

The executor keeps an **applied-keys ledger**. On accept→execute:
- **key already applied** → **no-op**; return the prior receipt (a `completed` ack that points at the *original* proposal_id). No re-dispatch, no double side effect.
- **key unseen** → execute; record `{key → receipt}`.

The re-issue becomes a cheap idempotent replay that resolves to the first result, and the narrating reply resolves against a real, stable receipt.

---

## 3. Two flavors of idempotency

Emitters reach idempotency two ways; prefer the first:

| Flavor | Mechanism | Examples | Key role |
|---|---|---|---|
| **(a) Naturally idempotent** | The action is safe by construction | upsert-by-key, set-membership, `check-then-act`, PUT-semantics, "ensure X exists" | Key is a courtesy (for the receipt), not load-bearing |
| **(b) Dedup-guarded** | Non-idempotent action made safe by key + ledger | append, increment, send-once, one-shot side effects | Key is **load-bearing** — the ledger is what makes replay safe |

**Discipline:** design for (a). When (a) is impossible, use (b). If an action is *genuinely* un-dedupable one-shot (rare), it is **NOT W1a-covered** and MUST declare `payload.idempotent: false` so the executor treats it as lease-only (W1b/W1c) and never auto-replays it. Honesty flag, same spirit as `--epistemic-source`: don't paper a non-idempotent action as safe.

---

## 4. Key derivation (emitter's responsibility)

```
idempotency_key = stable_hash(action_type, target_practice, canonical(semantic_params))
```

- **`canonical(...)`** normalizes the params: sort keys, drop volatile fields (timestamps, nonces, free-text prose, the proposal_id itself).
- The load-bearing judgment is **which params define action identity** vs which are incidental. Two proposals differing only in incidental fields (phrasing, emission time) MUST collide; two proposals for genuinely different actions MUST NOT.
- **NOT** the `proposal_id` (per-emission — defeats dedup). **NOT** a timestamp/nonce (never collides). **NOT** the free-text summary.

Worked example — a code change to a file/function:
```
key = hash("code_change_request", "empirica.david.empirica-cortex",
           {"path": "src/cortex/router.py", "symbol": "resolve_topic", "intent": "reject-retired"})
```
Re-issued with different phrasing → same key → dedup. A change to a *different* symbol → different key → executes.

empirica-core ships a helper (`idempotency_key(action_type, target, params)`) so emitters don't hand-roll the hash + canonicalization.

---

## 5. Applied-keys ledger (cortex's responsibility)

W1a defines the **contract**; cortex implements the **ledger** (executor-side, where the SER + proposal state already live):

```
ledger[idempotency_key] = { receipt, first_applied_at, proposal_id }
```

- **Read-before-execute:** on ECO-accept of an actionable proposal, look up `payload.idempotency_key`. Hit → return `{status: completed, receipt, original_proposal_id}` WITHOUT re-dispatch. Miss → dispatch, then record.
- **The `completed` ack points at the original** so the source AI's outbox handshake closes against the real work, not a phantom.

**Open design decisions for cortex (flagged, not prescribed):**
- **Scope / TTL of a key.** A legitimately-*intended* repeat of the same action after a long gap should not be silently swallowed. Options: (i) thread-scoped (key unique within `thread_root_id`), (ii) TTL'd (e.g. 24h), (iii) explicit `payload.idempotency_window`. Recommend **thread-scoped by default** (most races are within a coordination thread) with an opt-in TTL.
- **Where the ledger lives** relative to the SER store (same store, keyed table).

---

## 6. Composition with W1b / W1c

The three interlock; none alone suffices:

| Workstream | Side | Guarantee |
|---|---|---|
| **W1a** (this) | emitter | A re-issued/replayed action is *harmless* (idempotent or dedup-guarded) |
| **W1b** (cortex) | executor | "Is anyone acting on this right now?" — cheap pre-check before dispatch |
| **W1c** (cortex) | executor | Optimistic lease — first to claim a multi-target proposal wins; peers stand down |

W1b/W1c *prevent* the parallel double-claim; they are best-effort (they can still race under concurrency). **W1a is the invariant that holds when they race** — if two executors both slip past the pre-check, the idempotency key still collapses the second apply to a no-op. Defence in depth: prevent first, but be safe if prevention fails.

---

## 7. What empirica-core ships

Core owns the *convention* + the emitter ergonomics; cortex owns the ledger:

1. **This spec** — the convention + the `payload.idempotency_key` / `payload.idempotent` contract.
2. **A key helper** — `idempotency_key(action_type, target, params)` (stable hash + canonicalization), so emitters get it right by default.
3. **Propose-discipline guidance** — a line in the `/cortex-mailbox-send` skill + the propose surface: *"is this safe to run twice? attach an idempotency_key or declare idempotent:false."*
4. **The honesty flag** `payload.idempotent: true|false` — surfaced the same way `--epistemic-source` is.

The **crm-mcp dedup** is the existence proof — the one place this pattern was applied ad hoc. W1a generalizes it into a mesh-wide convention.

---

## 8. Rollout (non-breaking)

- **Additive.** Proposals without an `idempotency_key` keep working exactly as today — they are simply not dedup-protected. No forced migration.
- **Opt-in → default.** Start with the high-frequency actionable types (`code_change_request`, `architecture_decision`). As the helper + guidance land, keys become the default for those types.
- **Measured.** The ledger's hit-rate *is* the metric: every ledger hit is a double-apply that W1a prevented — a direct read on how often the race actually fires.

---

## 9. Open questions for convergence

1. Key scope/TTL (§5) — thread-scoped vs TTL vs explicit window. **cortex's call as ledger owner.**
2. Do we want a `payload.supersedes: <prior_proposal_id>` companion for the *intentional* replace-the-earlier-action case (distinct from accidental replay)?
3. Should the ECO surface show "this action was already applied (idempotent replay)" to the human, or silently return the receipt? (Leaning: show it — the human should see the race was caught.)

---

*empirica-core → cortex + mesh-support. Converge, then core ships the helper + guidance and cortex ships the ledger.*
