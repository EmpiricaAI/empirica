---
name: epistemic-gardening
description: "Use when the user says '/epistemic-gardening', 'garden the graph', 'de-weed', 'prune artifacts', 'epistemic hygiene', 'clean up findings/goals/sources', 'graph hygiene pass', or 'pre-release cleanup'. A PRAXIC pass that de-weeds a practice's epistemic graph — resolve stale/superseded findings, close answered unknowns, verify or drop assumptions, archive done goals and stale sources, prune dangling edges — so retrieval surfaces what's live, not what's rotted. Includes the mesh-wide propagation pattern for getting every practice to garden."
version: 2.0.0
---

# Epistemic Gardening 🌱

Resolve what's dead so retrieval surfaces what's live. Recency-decay knows *age*, not
*wrongness* — a superseded finding still scores high on impact and keeps resurfacing.

**PRAXIC.** It mutates the graph, so it runs inside a real transaction:
PREFLIGHT → CHECK → act → POSTFLIGHT.

## Three registers

| Register | When | How |
|---|---|---|
| **Surgical** | routine; one artifact or a small cluster, each one read | single verbs (`finding-resolve`, `unknown-resolve`) |
| **Batch-by-graph** | several artifacts connected through the graph — a finding and the unknowns it answered | one `resolve-artifacts -` call; this is the default for multi-artifact work |
| **Mass-policy** | clearing a backlog by rule rather than per-id | `resolve-artifacts` with a `filter` block — **requires explicit human sign-off on the policy**, dry-run first |

## When to run

| Trigger | Depth |
|---|---|
| Before a release | full pass |
| Periodically, or when a bootstrap feels noisy | standard pass on the loudest types |
| After a big investigation | scoped to that session's artifacts |
| PREFLIGHT surfaces something you know is stale | spot-resolve inline, no full pass |

Never garden mid-investigation — you will prune branches you are standing on.

## Lever choice: resolve ▸ archive ▸ delete

| Lever | Effect | Use when | Reverses? |
|---|---|---|---|
| **resolve** | kept for history, dropped from live retrieval | was true/open, now stale, answered, superseded or verified — **the common case** | yes |
| **archive** | hidden from default lists, kept whole | completed goal, or stale-but-citable source | yes |
| **delete** | gone, no history | test-noise, duplicates, mistaken logs — **no epistemic value** | no |

Bias to resolve. When unsure, resolve.

**Never resolve or delete dead-ends or mistakes.** They are the immune system — they are
*supposed* to resurface. Prune only literal duplicates and test noise.

## Weave as you log

Pruning removes what's dead; weaving connects what's live. Most connecting is automatic:

- **Goal attachment is automatic, both orders.** Log under an active goal and it attaches;
  create the goal after and `goals-create` back-wires the transaction's orphans. So the rule
  is just: every transaction has a goal.
- **Sources auto-connect.** `finding-log --source <id>` writes a real `sourced_from` edge.
  Cite as you log.
- **Semantic edges are the manual move worth making.** `evidence`, `grounded_by`,
  `caused_by`, `invalidates`, `resolves`, `sourced_from` each say something; `related` says
  almost nothing — reach for it last. Assert them via `log-artifacts` (nodes + edges in one
  call) or `--edge ID:RELATION` on any `*-log`.

## The pass

### 0 — PREFLIGHT + goal

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

empirica goals-create --objective "Epistemic gardening pass" \
  --description "Resolve stale/superseded findings, close answered unknowns, verify or drop
assumptions, archive done goals + stale sources, prune dangling edges. Success: EPISTEMIC
FOCUS surfaces only live artifacts."
```

### 1 — Survey (noetic)

**See the whole graph first — the list verbs scope to the active project's top-N, so
artifacts under divergent `project_id`s are invisible.** One real pass found artifacts across
12 ids while the default view showed a fraction.

```bash
empirica goals-list --all-projects
empirica unknown-list --all-projects
sqlite3 .empirica/sessions/sessions.db \
  "SELECT project_id, COUNT(*) FROM project_findings WHERE is_resolved IS NOT 1 GROUP BY project_id ORDER BY 2 DESC"
```

**If artifacts are scattered, consolidate identity BEFORE triaging** — reattach your own
divergent duplicates to the live `project_id`, resolve genuinely-other-practice orphans.
Fixing the scatter once is cheaper than gardening each stray id.

Then read current state:

```bash
empirica goals-list
empirica goals-get-stale
empirica project-search --task "<recent theme>"
empirica sources-map          # --global for shared
empirica sources-check        # unreviewed / stale-review

sqlite3 .empirica/sessions/sessions.db \
  "SELECT id, substr(finding,1,60), impact FROM project_findings \
   WHERE is_resolved IS NULL OR is_resolved=0 ORDER BY impact DESC LIMIT 40"
```

### 2 — CHECK

```bash
empirica check-submit - << 'EOF'
{"vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.8, "clarity": 0.8},
 "current_phase": "noetic",
 "reasoning": "Surveyed — N stale findings, M answered unknowns, K done goals, J stale sources."}
EOF
```

### 3 — Triage and act

```bash
# Batch, mixed types, one call:
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

Use `resolution_kind` to preserve *why*: `stale | superseded | retracted | mistyped`.
`mistyped` is not `stale` — a mis-typed artifact was never wrong, it was never that type.

**Mass-policy by filter** — dry-run, read it, then apply. **Never hand-write SQL to bulk
resolve.**

```bash
echo '{"filter":{"type":"finding","matching":"test %"},"resolution":"test-noise","apply":false}' \
  | empirica resolve-artifacts -
```

⚠️ A leading token selects for artifacts *about* a topic at least as often as artifacts *of*
it. A real `matching: 'test %'` dry-run returned 3 rows, all genuine knowledge. Read every
dry-run row before applying. Findings are retrieval substrate: prune noise, keep high-impact
durables. `null` impact is not a noise signal.

**Goals / sources:**

```bash
empirica goals-complete --goal-id <id> --reason "<evidence>"
empirica goals-archive  --goal-id <id>
empirica goals-mark-stale --goal-id <id>
empirica source-archive <id>
empirica source-update <id> ...
```

**Delete — true noise only** (dry-run default; review the receipt, then `--apply`):

```bash
empirica delete-artifacts - << 'EOF'
{"deletions": [{"type": "finding", "id": "<test-noise-id>"}],
 "prune_dangling": true,
 "reason": "test artifacts + edges left dangling by resolved nodes"}
EOF
```

### 4 — Verify

```bash
empirica project-search --task "<theme you just pruned>"
empirica goals-list
```

To refresh embedded payloads: **`rebuild --qdrant-only`**.
**Never `rebuild --qdrant`** — it force-imports git notes into SQLite first and reverts any
direct or bulk change not yet in notes, then embeds the reverted state.

### 5 — POSTFLIGHT

Complete the goal *before* POSTFLIGHT. Log one finding recording the pass's counts so the
next gardener has a baseline.

## Cross-practice 🌐

Shared retrieval is only as clean as the messiest contributor.

**Propagation unit is a lesson.** A finding *describes* local state; a **lesson transfers a
pattern across the practice boundary**. If a peer can pick it up and act on it, it is a
lesson — `lesson-create --visibility shared/public`.

1. Register the discipline: `empirica source-add --title "Epistemic gardening pass" --visibility shared --noetic`
2. FYI the mesh when you finish a pass — `/cortex-mailbox-send`, Flavor 1, canonical 3-form target.
3. For a coordinated fleet sweep with named owners, graduate to an SER via
   `cortex_propose(payload.action='create_ser')`.
4. **Never garden a peer's graph.** Only the practitioner inhabiting a practice knows whether
   a finding is truly superseded. Propose the pass; never reach into their DB with
   `--project-id` to prune.

## Anti-patterns

| Smell | Why |
|---|---|
| Deleting what should be resolved | throws away the calibration trail |
| Resolving dead-ends or mistakes | they are *meant* to resurface |
| Gardening mid-investigation | prunes branches you are standing on |
| A pass with no POSTFLIGHT | counts invisible to calibration |
| N single verbs on related artifacts | use `resolve-artifacts -` |
| `--apply` without reading the dry-run | the receipt exists to catch a mis-scoped prune |
| Reaching into a peer's DB | practice-owned judgment |
| Gardening only the active-project view | list verbs undercount; run `--all-projects` |
| Hand-written SQL to bulk-resolve | not durable, not the mechanism to teach |
| Bulk-age-resolving findings | prune noise, keep durables |
| Acting on a stale "our state is bad" self-assessment | git-date it first |

## Output contract

Resolved findings/unknowns/assumptions, archived goals + sources, pruned dangling edges,
deleted noise with a receipt, and **one summary finding** recording the pass. Re-running on a
clean graph resolves nothing and says so.

## See also

`docs/architecture/ARTIFACT_HYGIENE.md` — the design spec this operationalizes.
