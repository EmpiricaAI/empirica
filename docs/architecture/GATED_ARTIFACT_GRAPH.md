# Epistemic Map — Gated Artifact-Graph Discipline

**Status:** Design (spec). **Owner:** empirica core. **Date:** 2026-07-04.
**Register in:** `EPISTEMIC_MAP.md` territory inventory.

> Empirica gates the noetic→praxic transition (PREFLIGHT / CHECK / POSTFLIGHT)
> and AIs comply with it without fail. Artifact-graph construction is the **one
> step in the lifecycle that is *not* gated** — and it is exactly the step that
> rots. This map covers flipping it from *nudge* to *gate*. It is not a
> greenfield build: ~80% of the substrate already exists as reminders/reports;
> the work is promoting it to enforcement plus filling three gaps.

---

## 1. The territory

The knowledge graph is Empirica's differentiated asset (calibration-scored nodes
+ typed edges + provenance). But it is only as valuable as its **connectivity**.
In practice AIs log plenty of artifact *nodes* and almost **zero edges / zero
sources** — degrading the graph to a flat list and defeating the
`commit-context` walker, provenance, and cross-artifact retrieval.

The failure is not "no artifacts." It is **"no edges, no sources."** Any fix
must gate on the *graph property that is missing*, not on artifact count.

## 2. The empirical case (why now)

- **Falsified assumption** (logged, urgency 1.0): *"the provenance graph will be
  used by AIs naturally once the CLI flags exist — prompt guidance is sufficient
  without enforcement."* This session falsified it directly: **every POSTFLIGHT
  reported `0 declared edges` and `0 source_refs`** despite the flags
  (`--edge`, `--related-to`, `--source`) existing and the skill guidance saying
  to use them.
- **Decision reversal.** A prior decision chose *"behavioral rules in the skill
  over code enforcement"* (rationale: hard gates create friction). We are
  **reversing it** — the behavioral-rules approach demonstrably did not change
  behavior. The reversal rests on Empirica's own founding principle: discipline
  that isn't structurally enforced doesn't happen. We now have the evidence, in
  our own dogfooding, that the ungated step is the one that fails.

## 3. What already exists (do NOT rebuild)

| Capability | Where | Wired as |
|---|---|---|
| Normalized edge storage `artifact_edges` (from_id, to_id, relation; PK + inverse index) | `data/migrations/migrations.py` (migration 041) | **built** |
| Edge vocabulary + validation (`evidence`, `sourced_from`, `resolves`, `invalidates`, `caused_by`, `attached_to`, `grounded_by`, `prevents`, `raised_by`, …) | `cli/command_handlers/graph_commands.py` | **built** |
| Edge/source flags on the `*-log` commands (`--edge ID:rel`, `--related-to ID`, `--source`) | `cli/command_handlers/artifact_log_commands.py` | **built, unused** |
| Auto goal-edges (every POSTFLIGHT artifact → its goal) | `cli/command_handlers/_workflow_postflight.py` | **built (goal only)** |
| Note-aware soft-gate ("softgate smarter": notes = partial credit; 0 artifacts + 0 notes = strongest signal) | `cli/command_handlers/_workflow_preflight.py` | **built as nudge** |
| Transaction enforcer + configurable reminder cadence | `hooks/transaction-enforcer.py`, `hooks/context-shift-tracker.py` | **built as reminder** |
| Connectivity compliance checks (edge density / orphan / dangling) | `config/service_registry.py` | **built as report** |
| Graph walker (`commit-context --depth N`) | shipped | **built** |
| `ungrounded_remote_ops` calibration status (work the Sentinel can't observe → self-assessment stands) | `hooks/sentinel-gate.py` | **built (pattern to mirror)** |
| In-flight goal: *"Strengthen artifact breadth enforcement — breadth_note fires every tx but gets ignored"* | goals graph | **open — this map subsumes it** |

## 4. The model (what changes)

```
PRE ─▶ [ capture: note ] ─▶ ⟦weave-gate⟧ ─▶ CHECK ─▶ [ capture: note ] ─▶ ⟦weave-gate⟧ ─▶ POST ─▶ resolve
```

- **Capture stays cheap and in-flow** via `note` (already exists) — the AI is
  *not* gated mid-work. Notes are transaction-scoped scratchpad.
- **The weave-gate** at each phase boundary promotes notes → a *connected*
  sub-graph (`log-artifacts`). This is the natural moment to weave: all of the
  phase's artifacts are in hand, so edges can be declared in one pass.
- **Gate on connectivity, not count.** Pass-condition: every artifact carries
  ≥1 edge (or is explicitly flagged standalone) and sources are linked when
  external material was cited. Gating count alone yields edgeless nodes — today's
  behavior.
- **log-or-waive, never log-N.** Empty phases are legitimate (a trivial change
  has no findings). The gate accepts "declare artifacts+edges **or** assert
  nothing-to-log, with a reason." Forcing a count produces token artifacts +
  junk edges — worse than none.

## 5. Net-new work (the gaps — each a work-stream)

1. **Gate promotion — scalar control surface (built: report-only).** The gate is
   driven by **three orthogonal 0.0–1.0 scalar dimensions**, not a discrete
   `off/nudge/soft/hard` mode. The extension's Sentinel config owns them as
   **sliders**; the human sets them, the AI inherits them (see §5a). This build
   ships the scalars + verdict computation **report-only** (`enforced: False` at
   every setting); the actual soft/hard *blocking* keyed on `strictness` is the
   remaining half of this work-stream. Per-practice configurable via env
   transport (`EMPIRICA_ARTIFACT_GRAPH_STRICTNESS` / `_FLOOR` / `_PATIENCE`).
2. **Schema-injection at the gate — the make-or-break.** CHECK and POSTFLIGHT
   responses inject the `log-artifacts` schema (node-type enum + relation
   vocabulary from `graph_commands.py`) **plus the transaction's linkable ids**
   (open goals + this transaction's prior artifacts). The AI builds edges by
   *referencing given ids*, not guessing. Without this, a hard gate just raises
   the AI's JSON error rate (observed: repeated quoting/schema errors this
   session). PREFLIGHT already injects `noetic_guidance`; extend that pattern.
3. **Auto-edges beyond goal.** The POSTFLIGHT builder already links artifacts →
   goal. Add **sibling edges** (artifacts from the same transaction). Structural
   edges become free; the AI is asked only for **semantic** edges
   (`evidence` / `contradicts` / `refines`) — the judgment part.
4. **Adaptive weave-enforcement (promotion-trigger).** Refines the existing
   note-aware soft-gate. Trigger enforcement on **skipped *weaving*** — real
   change (git delta) + notes that were **never promoted** to connected
   artifacts — **not** on absent notes (a single throwaway note must not satisfy
   it). Applied to the *next* transaction, and it **decays on compliance**
   (weave once → back to freedom). Earned-autonomy in both directions, matching
   the Sentinel's adaptive thresholds. The **`patience` scalar** (§5a) sets the
   consecutive-miss tolerance before escalation — high patience forgives more
   before it bites.
5. **`ungrounded_bypass` (the `--yolo` bypass).** See §6.

### 5a. The gate's control surface — three scalar dimensions (built)

A discrete `off/nudge/soft/hard` mode is the wrong shape: it forces the human to
pick a bucket when the real settings are continuous and *orthogonal*. The gate is
instead three independent 0.0–1.0 sliders — the AI shaping how its own discipline
gets enforced, via the extension's Sentinel config, which inherits how the human
sets it:

| Dimension | Slider question | Drives | Default |
|---|---|---|---|
| **strictness** | *How loud when connectivity is low?* | Response band: `silent` (<0.05) → `report` (<0.40) → `warn` (<0.70) → `enforce` (≥0.70) | `0.25` (report) |
| **connectivity_floor** | *What fraction of artifacts must be woven?* | `satisfied = connected_ratio ≥ floor` | `0.50` |
| **patience** | *How forgiving of consecutive misses?* | Adaptive-escalation window (work-stream 4) | `0.80` |

Defaults keep a fresh install **report-only + forgiving** — the gate never blocks
until a human dials `strictness` up. `strictness` *is* the axis the old ramp
expressed, but as a continuum: old `off` = 0.0 (silent), `nudge` ≈ 0.25 (report),
`soft` ≈ 0.5 (warn), `hard` ≈ 0.9 (enforce). What a single mode couldn't express
is the split between **floor** (how much to weave) and **strictness** (how hard to
push): a practice can now demand high connectivity but only warn, or tolerate low
connectivity yet hard-block on it.

Single source of truth: `_GATE_SCALARS` in `_workflow_shared.py` (default + env
name per dimension, deliberately not duplicated across call sites — the
engagement_gate's 12-site duplicated-default drift is the anti-pattern this
avoids). Bad slider values clamp to `[0,1]` / fall back to default, so a bad
config never breaks the retrospective. `_resolve_gate_scalars()` reads them;
`_gate_response_for()` owns the strictness→band ladder (the *only* place that
mapping lives); `_weave_gate_block()` returns `{scalars, response, verdict,
connected_ratio, satisfied, enforced, note}`.

`ungrounded_bypass` (§6) stays a **separate boolean**, not a fourth scalar — it's
a categorical human capability-toggle, not a continuous dimension.

## 6. The bypass — `--yolo` (human-only)

A **human** capability toggle to run Empirica with the Sentinel off. Rationale:
some operators believe the discipline is a burden on speed; they learn otherwise
by feeling the difference. Constraints that keep it honest, not theatrical:

- **Human-only.** The AI **cannot self-select** it — otherwise it becomes the
  escape hatch one level up (the AI reaches for the frictionless mode for the
  same reason it reaches for the cheap command).
- **Capability-fallback framing, not "discipline off."** The legitimate case is
  *"the Sentinel can't run here"* (CI/headless/embedded; vendoring only the
  retrieval lib), not *"I don't want to be measured."*
- **Don't forbid — measure.** Mirror `ungrounded_remote_ops`: every bypassed
  transaction is stamped `calibration_status: ungrounded_bypass`. The practice's
  trajectory then *honestly shows* it ran ungrounded. The divergence is the
  disincentive; nobody can run yolo and then claim the calibration number means
  something. Under `--yolo` measurement is largely *not possible* anyway (the AI
  won't voluntarily PRE/CHECK/POST without the gate) — which is exactly why the
  label matters.

## 7. Scope split

- **This map** owns the *discipline model* (§4–6): the gate, the connectivity
  pass-condition, schema-injection, adaptive enforcement, the bypass.
- **CLI collapse/deprecation is a SEPARATE, linked spec.** Folding the six
  `*-log` commands into `log-artifacts` as the canonical writer (they are
  already just `type` values in the node schema), the compat-shim strategy, the
  deprecation timeline, and the skill/doc/hook rewrites are *migration
  mechanics* with their own blast radius. Keeping them out of this map keeps the
  map a discipline spec, not a migration ticket.

## 8. Risks

- **Token-edge gaming.** A hard "every artifact needs an edge" invites junk
  edges. Mitigate: gate on connectivity with light plausibility (a goal-edge is
  always valid; semantic edges are AI-asserted), and let calibration later flag
  transactions where declared edges don't correlate with a useful walker result.
- **Migration blast radius.** Every skill, doc, hook, and prompt references the
  current commands. The ramp (setting default = nudge) de-risks the cutover.
- **Tooling caveat.** `grep`/Bash output mangles multi-char identifiers
  intermittently in this environment — implementation must `Read` the files for
  exact symbols, not rely on greps.

## 9. Open questions

- Default scalar values per `work_type`? Now that strictness/floor/patience are
  continuous (§5a), the question is which *triple* each work_type seeds (e.g.
  `release` = low strictness + low floor; `research` = high floor, moderate
  strictness) — and whether work_type sets defaults the human slider then
  overrides, or the slider is absolute.
- Reconcile the promotion-trigger with the existing "0 artifacts + 0 notes =
  strongest" soft-gate logic in `_workflow_preflight.py` — is the trigger a
  refinement of that function or a sibling signal?
- Does `--yolo` disable the *retrieval* layer too, or only the *gating*? (Recommend:
  gating only — retrieval/logging still works, just ungrounded.)
