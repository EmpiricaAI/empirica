---
name: epistemic-gardening
description: "Use when the user says '/epistemic-gardening', 'garden the graph', 'de-weed', 'prune artifacts', 'epistemic hygiene', 'clean up findings/goals/sources', 'graph hygiene pass', or 'pre-release cleanup'. A PRAXIC pass that de-weeds a practice's epistemic graph — resolve stale/superseded findings, close answered unknowns, verify or drop assumptions, archive done goals and stale sources, prune dangling edges — so retrieval surfaces what's live, not what's rotted. Includes the mesh-wide propagation pattern for getting every practice to garden."
version: 1.1.0
---

# Epistemic Gardening 🌱

**De-weed the epistemic graph so retrieval surfaces what's live, not what's rotted.**

The knowledge layer accretes. Findings that were true get superseded. Unknowns get
answered. Goals finish. Sources go stale. Assumptions get verified — or falsified.
None of that decay is self-cleaning: a two-month-old finding that's been *superseded*
still scores high on impact, so it keeps resurfacing in PREFLIGHT/CHECK and crowds out
what's current. Recency-decay knows *age*, not *wrongness*. Gardening is the deliberate
pass that tells the graph what's dead.

> **This skill is PRAXIC, not noetic.** Unlike `/code-audit` (which only investigates),
> gardening *mutates* the graph — it resolves, archives, and deletes. So it runs inside a
> real epistemic transaction: PREFLIGHT → CHECK → act → POSTFLIGHT. Open the window before
> you prune.

> **Why it matters now.** Before finding-resolve + read-time reconciliation (#307),
> resolving a finding was nearly cosmetic — the Qdrant payload stayed stale and the
> finding kept surfacing. Now a resolved finding is genuinely dropped from live retrieval
> (Qdrant reconcile + the breadcrumbs `EPISTEMIC FOCUS` filter). **Resolution finally
> lands.** That's what makes a hygiene pass worth running.

## Surgical by default; batch by graph; mass-policy only with sign-off

Three registers — don't confuse them:

- **Surgical (the default for human-facing gardening).** Resolution is per-artifact
  *judgment* — "is THIS finding stale / superseded / still load-bearing?". A human (or an
  AI acting on a human's behalf) gardens one artifact, or one small cluster, at a time,
  reading each. This is the routine, careful register. Reach for the single verbs
  (`finding-resolve`, `unknown-resolve`) here when it's genuinely one artifact.
- **Batch-by-graph (how an AI handles a connected cluster in regular work).** When several
  artifacts are related through the knowledge graph — a finding and the two unknowns it
  answered, a dead-end and the decision that replaced it — resolve them together in one
  `resolve-artifacts -` call rather than N single verbs. The batch verbs
  (`log-artifacts` / `resolve-artifacts` / `delete-artifacts`) are the *default* for
  multi-artifact work; singles are the exception. This is efficiency, still grounded in
  per-cluster judgment.
- **Mass-policy (a deliberate backlog tool, NOT routine).** A filter-and-bulk-resolve
  (e.g. "resolve all >4mo low-impact findings as stale, protect the keepers") clears an
  accumulated backlog fast, but it trades per-artifact judgment for a *policy*. It is
  irreducibly probabilistic — you accept a small, reversible error rate. **Use it only
  deliberately, with explicit human sign-off on the policy** (which age gate, which
  keepers protected), not as the everyday hygiene move. The everyday move is surgical +
  batch-by-graph.

---

## When to run

| Trigger | Depth |
|---|---|
| **Before a release** | Full pass — a clean graph is part of the release artifact |
| **Periodically** (e.g. every N sessions, or when a bootstrap feels noisy) | Standard pass on the loudest artifact types |
| **After a big investigation** that spawned many exploratory findings/unknowns | Scoped pass on that session's artifacts |
| **When PREFLIGHT/EPISTEMIC FOCUS surfaces something you *know* is stale** | Spot-resolve inline (one verb, no full pass) |

Don't garden *mid-investigation* — you'll prune branches you're still standing on. Garden
at a coherent break, not while the question is open.

---

## Weave as you log — the other half of a healthy graph

Pruning removes what's dead; **weaving connects what's live**. A graph's value is the
connections — a finding linked to its source and the decision it grounds is knowledge; the
same finding as an orphan row is just a log line. The default failure mode is a flat log:
logging is one command, connecting felt like several, so the connections never got made
(empirica's own graph ran ~95% orphaned, 0 `sourced_from` edges, before this was fixed).

Most of the connecting is now **automatic** — the friction is gone, so there's no excuse to
log flat:

- **Goal attachment is automatic (both orders).** Log an artifact under an active goal and
  it auto-attaches; create the goal *after* logging and `goals-create` backward-wires the
  transaction's orphans. So the rule is simply: **every transaction has a goal** (big goals
  get `goals-add-task` per unit of work) — and your artifacts weave into it for free. The
  weave-gate is satisfied by working disciplined, not by hand-wiring edges.
- **Sources auto-connect.** `finding-log --source <id>` now writes a real `sourced_from`
  edge, not just a column. So *cite as you log* — the friction that kept sources at
  60-for-9000 is the two-step `source-add` → `--source`; do it anyway when an artifact came
  from an external origin (doc, URL, paper, transcript), and the graph link is written for you.
- **Semantic edges are the one manual move worth making.** When artifacts relate by meaning
  — a finding is `evidence` for a decision, a mistake was `caused_by` an assumption — assert
  it with `log-artifacts` (nodes + edges in one call, the batch-first default) or
  `--edge ID:RELATION` / `--related-to ID` on any `*-log`. This is where the graph earns its
  keep; it's cheap once the artifacts exist, and `empirica note` is the place to park a
  "should connect X to Y" thought until you do.

Weaving and pruning are the two hands of tending: connect live knowledge in, resolve dead
knowledge out. A practice that does both surfaces a dense, current graph; one that does
neither drowns in a flat, stale log.

---

## The core discipline: resolve ▸ archive ▸ delete (in that preference order)

The single most important call in gardening is **which lever** an artifact gets. Default
toward the *least* destructive one that removes it from live retrieval:

| Lever | What it does | Use when | Reverses? |
|---|---|---|---|
| **resolve** | keeps the artifact for history, drops it from live retrieval | the artifact *was* true/open and is now stale, answered, superseded, or verified — **the common case** | yes (`goals-reopen`; re-log) |
| **archive** | hides from default lists, kept fully | a *completed* goal or a *stale-but-real* source you may cite later | yes (`goals-reopen`) |
| **delete** | removes it entirely, no history | test-noise, duplicates, mistaken logs — artifacts with **no epistemic value** | no |

**The bias is resolve-over-delete.** Epistemic history is an asset: a superseded finding
plus its `superseded_by` link is a *record of how understanding changed* — that's the
practice's calibration trajectory. Delete only what was never knowledge: a `TEST` finding,
an accidental double-log, a goal you created then immediately abandoned. When unsure,
resolve — it's reversible and keeps the trail.

**Never resolve or delete dead-ends and mistakes.** They are the cognitive immune system —
"we tried X, it failed" is *supposed* to resurface so nobody re-walks it. Prune those only
if they're literal duplicates or test noise.

---

## The pass — six phases

### Phase 0 — PREFLIGHT (open the window)

```bash
empirica preflight-submit - << 'EOF'
{"work_type": "audit", "criticality": "medium",
 "task_context": "Epistemic gardening pass on <practice>",
 "vectors": {"know": 0.7, "do": 0.9, "context": 0.75, "clarity": 0.7,
   "coherence": 0.7, "signal": 0.6, "density": 0.5, "state": 0.7,
   "change": 0.1, "completion": 0.0, "impact": 0.5, "engagement": 0.9,
   "uncertainty": 0.3},
 "current_phase": "noetic"}
EOF
```

Create a goal so the pass is a tracked unit:

```bash
empirica goals-create --objective "Epistemic gardening pass" \
  --description "De-weed the graph: resolve stale/superseded findings, close answered
unknowns, verify/drop assumptions, archive done goals + stale sources, prune dangling
edges. Success: bootstrap/EPISTEMIC FOCUS surfaces only live artifacts."
```

### Phase 1 — Survey (noetic: what's in the graph)

**First, see the WHOLE graph — the list verbs lie by omission.** `goals-list` /
`unknown-list` scope to the *active* project's top-N, so artifacts stranded under other or
**divergent `project_id`s are invisible**. A practice's graph scatters across many
`project_id`s over time (wrong-project logging, identity divergence) — one real pass found
artifacts spread across **12 ids** while the default view showed a fraction. You cannot
garden what you cannot see, so start with the full view and diagnose the scatter:

```bash
empirica goals-list --all-projects       # every project_id, not the active top-N
empirica unknown-list --all-projects
# Diagnose the scatter (noetic — plain SELECT):
sqlite3 .empirica/sessions/sessions.db \
  "SELECT project_id, COUNT(*) FROM project_findings WHERE is_resolved IS NOT 1 GROUP BY project_id ORDER BY 2 DESC"
```

**Structural-first beats N triage passes.** If artifacts are scattered, *consolidate
identity first* — reattach your-own divergent-dups to the live `project_id`; resolve
genuinely-other-practice orphans (their home practice holds the canonical copy) — THEN
triage the now-single-project graph. Fixing the scatter once is cheaper than gardening
each stray id separately.

Then read the current state before touching anything. `log-artifacts -` with an empty
payload is not how you read — use these:

```bash
empirica goals-list                              # open/planned/in_progress + stale candidates
empirica goals-get-stale                         # goals past their freshness window
empirica project-search --task "<recent theme>"  # what retrieval actually surfaces
empirica sources-map                             # source inventory (add --global for shared)
empirica sources-check                           # unreviewed / stale-review sources
```

For findings/unknowns/assumptions, inspect the practice DB read-only (this is noetic —
a plain SELECT):

```bash
sqlite3 .empirica/sessions/sessions.db \
  "SELECT id, substr(finding,1,60), impact FROM project_findings \
   WHERE is_resolved IS NULL OR is_resolved=0 ORDER BY impact DESC LIMIT 40" | column -t -s '|'
sqlite3 .empirica/sessions/sessions.db \
  "SELECT id, substr(unknown,1,60) FROM project_unknowns WHERE is_resolved=0"
```

Note the counts and the loudest items. You're building a triage list, not acting yet.

### Phase 2 — CHECK (gate the transition)

You've surveyed; now you know what to prune. CHECK with honest vectors, then act.

```bash
empirica check-submit - << 'EOF'
{"vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.8, "clarity": 0.8},
 "current_phase": "noetic",
 "reasoning": "Surveyed the graph — N stale findings, M answered unknowns, K done goals, J stale sources identified for the pass."}
EOF
```

### Phase 3 — Triage + act (per artifact type)

Prefer the **batch verbs** — one call, connected, auditable — over N single verbs.

**Findings** — resolve stale/superseded; link the replacement:
```bash
# Single, with supersession link:
empirica finding-resolve <old-id> --resolution "superseded" --superseded-by <new-id>
# Batch (mixed types in one call):
empirica resolve-artifacts - << 'EOF'
{"resolutions": [
  {"type": "finding",   "id": "<id>", "resolution": "stale — subsystem removed"},
  {"type": "finding",   "id": "<id>", "resolution": "superseded", "superseded_by": "<new-id>"},
  {"type": "unknown",   "id": "<id>", "resolution": "answered: see finding <id>"},
  {"type": "assumption","id": "<id>", "resolution": "verified", "verified": true},
  {"type": "goal",      "id": "<id>", "resolution": "done"}
]}
EOF
```

**Bulk-by-filter (the *mass-policy* mechanism — dry-run by default).** When clearing a
backlog by policy rather than per-id, `resolve-artifacts` takes a `filter` block:
enumerate OPEN findings/unknowns by `older_than` / `matching` / `project_id` and resolve
them in one call. **Dry-run first** (`apply:false` — reports matched count + a sample),
read it, THEN `apply:true`. This is the safe mechanism — **never hand-write SQL** (not
durable, not the pattern to teach). Findings are retrieval *substrate*: filter to clear
*noise* (test-noise, cross-project orphans), and preserve high-impact durable keepers —
`null` impact is **not** a noise signal.
```bash
# DRY-RUN — what would resolve?
echo '{"filter":{"type":"finding","matching":"test %"},"resolution":"test-noise","apply":false}' \
  | empirica resolve-artifacts -
# then re-run with "apply": true to commit
```
Per the register split above, filter-mode is *mass-policy* — deliberate, with sign-off on
the policy (which gate, which keepers) — not the everyday move.

**Goals** — close, archive, or mark stale:
```bash
empirica goals-complete --goal-id <id> --reason "<evidence>"
empirica goals-archive  --goal-id <id>          # completed + old → out of the default list
empirica goals-mark-stale --goal-id <id>        # abandoned but worth recording
```

**Sources** — archive stale, or refresh:
```bash
empirica source-archive <id>                    # stale but may cite later
empirica source-update <id> ...                 # content moved / refreshed
```

**Delete — only true noise** (dry-run is the default; review the receipt, then `--apply`):
```bash
empirica delete-artifacts - << 'EOF'
{"deletions": [
  {"type": "finding", "id": "<test-noise-id>"},
  {"type": "unknown", "id": "<accidental-dup-id>"}
],
 "prune_dangling": true,
 "reason": "test artifacts + edges left dangling by resolved nodes"}
EOF
```
`prune_dangling` sweeps edges whose endpoints no longer exist (with `repair` rewiring
recoverable prefixes by default). Deletions log a decision receipt for audit.

### Phase 4 — Verify (did the pruning land?)

Resolution is only real if retrieval reflects it. Confirm:

```bash
empirica project-search --task "<theme you just pruned>"   # resolved items gone?
empirica goals-list                                        # closed/archived gone from active?
```

If a resolved finding still surfaces, its Qdrant payload predates #307 — the read-time
reconcile drops it by `artifact_id` or text-prefix, so it should vanish from
PREFLIGHT/CHECK regardless. To refresh the embedded payload itself, run **`rebuild
--qdrant-only`** — it re-embeds Qdrant from the *current* SQLite. **Do NOT run `rebuild
--qdrant`**: it force-imports git notes into SQLite *first* and reverts any direct/bulk
change not yet persisted to notes (e.g. a filter-mode resolve) before embedding the
reverted state — the footgun. `--qdrant-only` never touches SQLite.

### Phase 5 — POSTFLIGHT (close the window)

Complete the goal *before* POSTFLIGHT (the window closes there). Log a finding recording
the pass's scope (what was resolved/archived/deleted, counts) so the *next* gardener sees
the last pass.

```bash
empirica goals-complete --goal-id <pass-goal> --reason "Resolved N findings, closed M unknowns, archived K goals + J sources, pruned E edges."
empirica postflight-submit - << 'EOF'
{"work_type": "audit", "vectors": {"...": "..."}, "current_phase": "praxic",
 "reasoning": "Gardening pass complete: <counts>."}
EOF
```

---

## Cross-practice: garden the whole mesh 🌐

A single clean practice is local hygiene. The value compounds when **every** practice
gardens — the shared/global retrieval surfaces (`project-search --global`, `sources-map
--global`, the `global_learnings` collection) are only as clean as the messiest
contributor. Propagating the discipline is part of the pass.

> **A lesson is the propagation unit.** When a pass (or any work) surfaces a reusable
> pattern or anti-pattern — something a *peer* could apply, not just a fact about this
> practice — author it with `lesson-create` and propagate at `--visibility shared/public`
> + collab. That's the load-bearing line between artifact types: a finding *describes*
> local state; a **lesson transfers a pattern across the practice boundary**. It isn't a
> lesson until a peer (local or remote) can pick it up and act on it.

**1. Register this skill's discipline as a shared reference** so peers pull it rather than
re-derive it:
```bash
empirica source-add --title "Epistemic gardening pass — hygiene discipline" \
  --visibility shared --noetic
```

**2. Collab the mesh when you finish a pass** (noetic — auto-accepted, no ECO gate). FYI
peers that you gardened, and nudge them to run their own:
> Use `/cortex-mailbox-send` (Flavor 1, `cortex_collab`). Lead with substance: *"Ran an
> epistemic-gardening pass on `<practice>` — resolved N stale/superseded findings + closed
> M unknowns; shared-visibility retrieval should be cleaner. Recommend each practice run
> `/epistemic-gardening` before the next release — resolution now lands in retrieval
> (#307)."* Target the canonical 3-form (`empirica.<tenant>.<practice>`).

**3. For a coordinated fleet-wide sweep** — when it's not one FYI but sustained
multi-practice work with named owners — graduate to an **SER** (Shared Epistemic Record)
via `cortex_propose(payload.action='create_ser')`:
> Participants = the practices that must garden (role `required`), coordination state
> tracks the sweep (`open → in_progress → closed`). This is the right primitive when
> "get every practice clean before 1.30" needs shared, persistent, cross-session state
> rather than a thread. See `/cortex-mailbox-send` Flavor 3.

**4. Don't garden a peer's graph for them.** Resolution/deletion is a *practice-owned*
judgment — only the practitioner inhabiting a practice knows whether a finding is truly
superseded. Propose (ECO-gated) that they run the pass; never reach into their DB with
`--project-id` to prune. Sharing the *discipline* is collab; pruning *their* artifacts is
overreach.

---

## Anti-patterns

| Smell | Why it's wrong |
|---|---|
| Deleting a finding that should be resolved | Throws away the calibration trail. Resolve keeps history + drops from retrieval — that's the point. |
| Resolving/deleting dead-ends or mistakes | They're the immune system — they're *meant* to resurface. Prune only literal dupes/noise. |
| Gardening mid-investigation | You'll prune branches you're still on. Garden at a coherent break. |
| A pass with no POSTFLIGHT | The window never closes; the counts and the summary finding are invisible to calibration. |
| N single `*-resolve` calls when they're related | Use `resolve-artifacts -` — one batch, connected, auditable. |
| Deleting straight to `--apply` without reading the dry-run | The receipt is there to catch a mis-scoped prune before it's irreversible. |
| Reaching into a peer practice's DB to prune | Practice-owned judgment. Propose the pass; don't execute it on their graph. |
| Gardening only the active-project view | The list verbs undercount (active top-N). Run `--all-projects` first — you'll miss cross-project scatter otherwise. |
| Hand-writing SQL to bulk-resolve | Not durable, not the mechanism to teach. Use `resolve-artifacts` with a `filter` block (dry-run default). |
| Bulk-age-resolving findings | Findings are retrieval substrate. Prune *noise*, preserve high-impact durable keepers; `null` impact is not a noise signal. |
| Acting on a stale "our state is bad" self-assessment | Git-date it first. An old "1/62 linked" finding was actually 45/63 after intervening work — check ground truth before a big prune. |

---

## Output contract

After a pass, the graph has: resolved findings/unknowns/assumptions (kept, out of
retrieval), archived goals + sources, pruned dangling edges, deleted noise (with an audit
receipt), and **one summary finding** recording the pass so the next gardener has a
baseline. Re-running is idempotent — the second pass on an already-clean graph resolves
nothing and says so.

---

## See also

- **`docs/architecture/ARTIFACT_HYGIENE.md`** — the design spec this skill
  operationalizes (the cross-transaction, whole-practice sweep). That doc governs
  *policy* (what decays, which primitive addresses it); this skill is the *procedure*.
- **`docs/architecture/GATED_ARTIFACT_GRAPH.md`** — the *within-transaction* half
  (weave-gate + connectivity at POSTFLIGHT). Gardening handles what a single POSTFLIGHT
  structurally can't see.
- **`/epistemic-transaction`** — the transaction discipline the pass runs inside.
- **`/cortex-mailbox-send`** — the collab / propose / SER mechanics for the mesh-wide
  propagation in the cross-practice section.

🌱 *A practice that gardens surfaces its best current knowledge. A practice that doesn't
drowns its present in its past.*
