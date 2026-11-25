---
name: epistemic-gardening
description: "Use when the user says '/epistemic-gardening', 'garden the graph', 'de-weed', 'prune artifacts', 'epistemic hygiene', 'clean up findings/goals/sources', 'graph hygiene pass', or 'pre-release cleanup'. A PRAXIC pass that de-weeds a practice's epistemic graph — resolve stale/superseded findings, close answered unknowns, verify or drop assumptions, archive done goals and stale sources, prune dangling edges — so retrieval surfaces what's live, not what's rotted. Includes the mesh-wide propagation pattern for getting every practice to garden."
version: 2.1.0
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
| **archive** | hidden from default lists, kept whole | completed or abandoned goal, or stale-but-citable source | yes |
| **delete** | gone, no history | test-noise, duplicates, mistaken logs — **no epistemic value** | no |

Bias to resolve. When unsure, resolve.

**Never resolve or delete dead-ends or mistakes.** They are the immune system — they are
*supposed* to resurface. Prune only literal duplicates and test noise.

## Weave as you log

Pruning removes what's dead; weaving connects what's live. Most connecting is automatic:

- **Goal attachment is automatic, both orders.** Log under an active goal and it attaches;
  create the goal after and `goals-create` back-wires the transaction's orphans. So the rule
  is just: every transaction has a goal. Forward-attach resolves the goal in tiers — the one
  bound to the current transaction, then the session's latest open goal, then the project's
  latest open `in_progress` goal. That last tier is what makes the promise hold across
  sessions; before 1.13.45 resolution was session-only, and 53% of artifacts logged inside a
  transaction carried no goal because their goal had been created in an earlier session.
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
 "vectors": {"...": "YOUR assessment across the 13 vectors"},
 "current_phase": "noetic"}
EOF
```

**Assess your own vectors — do not paste these.** A canned vector set is not a
low reading, it is a *fabricated* one, and it corrupts the calibration record more
than skipping the transaction would. The shape is the contract; the numbers are
yours.

```bash
empirica goals-create --objective "Epistemic gardening pass" \
  --description "Resolve stale/superseded findings, close answered unknowns, verify or drop
assumptions, archive done goals + stale sources, prune dangling edges. Success: EPISTEMIC
FOCUS surfaces only live artifacts."
```

### 1 — Survey (noetic): every type, by subject

**Holistic, never by keyword or impact.** A pass that reads the top findings by impact never
sees old, low-impact leftovers, and one that greps for a word resolves artifacts that only
mention it: of 39 open findings matching removed names (2026-11-24), 8 were about them. Survey
every artifact type, group by subject, read the group, and judge each artifact on its own
reading. A keyword, an impact score or a registry hit only says what to read next.

**First, the whole graph.** List verbs scope to the active project, so artifacts under
divergent `project_id`s are invisible; one pass found artifacts across 12 ids. Consolidate
identity before triaging: reattach your own divergent duplicates to the live `project_id`,
leave other practices' orphans to them.

```bash
empirica goals-list --all-projects
empirica goals-list --all-projects --status abandoned   # dead goals: archive, do not accumulate
empirica unknown-list --all-projects
empirica falsifier-list
empirica lesson-list
empirica note --list
empirica sources-check        # unreviewed / stale-review
# Open artifacts per type (assumptions, decisions, dead ends and mistakes have no list verb):
sqlite3 .empirica/sessions/sessions.db "
  SELECT 'finding',    COUNT(*) FROM project_findings  WHERE is_resolved IS NOT 1 UNION ALL
  SELECT 'unknown',    COUNT(*) FROM project_unknowns  WHERE is_resolved IS NOT 1 UNION ALL
  SELECT 'assumption', COUNT(*) FROM assumptions       WHERE status NOT IN ('verified','falsified','expired') UNION ALL
  SELECT 'decision',   COUNT(*) FROM decisions         WHERE outcome IS NULL UNION ALL
  SELECT 'dead_end',   COUNT(*) FROM project_dead_ends WHERE is_invalidated IS NOT 1 UNION ALL
  SELECT 'mistake',    COUNT(*) FROM mistakes_made WHERE is_invalidated IS NOT 1"
```

**Then pick the subjects to read, not the rows.** A subject is something the graph talks
about: an open goal, a component or verb changed since the last pass (`git log --since=<last
pass>`), something removed (the CHANGELOG's Removed entries), a theme the bootstrap keeps
surfacing, a release. For each subject:

```bash
empirica project-search --task "<subject>"     # semantic, across every type
sqlite3 .empirica/sessions/sessions.db \
  "SELECT relation, from_id, to_id FROM artifact_edges WHERE from_id = '<id>' OR to_id = '<id>'"
```

Read the cluster whole (findings, unknowns, assumptions, decisions, dead ends, mistakes, lessons,
falsifiers, goals, sources), then decide each artifact by what it says: still true, answered,
superseded, wrong when written, or about something that no longer exists. Mentioning a subject
is not being about it. Dead ends and mistakes are read like the rest and resolved only by
evidence that the constraint is false (see Lever choice); a decision closes with an honest
outcome (upheld, reversed, mixed) or stays open; an assumption whose subject went away is
`expired`, not verified.

### 2 — CHECK

```bash
empirica check-submit - << 'EOF'
{"vectors": {"...": "YOUR assessment — what the survey actually taught you"},
 "current_phase": "noetic",
 "reasoning": "Surveyed K subjects across every type: N findings stale or superseded, M unknowns answered, A assumptions expired, D decisions graded, G goals done."}
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
empirica goals-archive  --goal-id <id>            # one completed or abandoned goal
empirica goals-archive  --older-than 30 --apply   # the sweep: every completed or abandoned goal that ended 30+ days ago (dry run without --apply)
empirica goals-mark-stale --goal-id <id>         # ABANDONS the goal: dead, not delivered (name kept; no 'stale' status since migration 038)
empirica source-archive --source-id <id> --reason {user_deleted,file_missing,url_unreachable,superseded}
empirica source-update  --source-id <id> --url <new-path-or-url>   # re-point a source whose file moved
```

**Delete — true noise only** (dry-run default; review the receipt, then `--apply`):

```bash
empirica delete-artifacts - << 'EOF'
{"deletions": [{"type": "finding", "id": "<test-noise-id>"}],
 "prune_dangling": true,
 "reason": "test artifacts + edges left dangling by resolved nodes"}
EOF
```

Phantom CHECK rows (a second, all-0.5 CHECK the auto-checkpoint wrote before 1.14.6; no reasoning, `auto_checkpoint`
true) are removed per practice with the same verb, previewing first:
`echo '{"reflexes": {"phantom_checks": true}}' | empirica delete-artifacts -`, then the same with `--apply`.

### 4 — Verify

```bash
empirica project-search --task "<theme you just pruned>"
empirica goals-list
```

To refresh embedded payloads: **`project-embed`** — scoped to one project, no drop, and it
re-upserts every point through the same embed path (deterministic ids, so payloads are
rewritten rather than duplicated).

**Not `rebuild`, either flavour.** Before v1.13.39 `--qdrant-only` dropped ten collections
while the refill covered three, permanently emptying calibration / episodic / goals /
decisions / assumptions and reporting success — 4,781 points across 24 practices on one
box. Fixed by deriving the drop set from the refill set, but two reasons remain to prefer
`project-embed` for a gardening refresh: `rebuild` walks **every project in workspace.db**,
including your peers' (*never garden a peer's graph*), and `--qdrant` additionally
force-imports git notes into SQLite first, reverting any direct or bulk change not yet in
notes and then embedding the reverted state.

**Decisions and assumptions are re-embedded from SQLite** (`rebuild` and `project-embed` read the
`decisions` and `assumptions` tables since 2026-09-06, `core/qdrant/rebuild.py`
`_read_decisions_and_assumptions`), so a lost Qdrant point there is recoverable. The wiring is pinned
structurally (`tests/test_rebuild_drops_only_what_it_refills.py`); no test refills a live Qdrant end to
end, so compare the point counts before and after a refresh rather than trusting the success line.

### 5 — POSTFLIGHT

Complete the goal *before* POSTFLIGHT. Log one finding recording the pass's counts so the
next gardener has a baseline.

## Cross-practice 🌐

Shared retrieval is only as clean as the messiest contributor.

**Propagation unit is a lesson.** A finding *describes* local state; a **lesson transfers a
pattern across the practice boundary**. If a peer can pick it up and act on it, it is a
lesson — `lesson-create` with `sharing_policy` `org` or `public` in the payload (`visibility` shared/public is accepted as an alias).

1. Register the discipline: `empirica source-add --title "Epistemic gardening pass" --visibility shared --noetic`
2. FYI the mesh when you finish a pass — `/cortex-mailbox-send`, Flavor 1, canonical 3-form target.
3. For a coordinated fleet sweep with named owners, send ONE multi-target `cortex_propose`; each owner acks its own
   target with `empirica mailbox reply`, and the row closes when all have (`pending_targets` shows who still owes).
4. **Never garden a peer's graph.** Only the practitioner inhabiting a practice knows whether
   a finding is truly superseded. Propose the pass; never reach into their DB with
   `--project-id` to prune.

## Output contract

Resolved findings/unknowns/assumptions, archived goals + sources, pruned dangling edges,
deleted noise with a receipt, and **one summary finding** recording the pass. Re-running on a
clean graph resolves nothing and says so.

## See also

`docs/architecture/ARTIFACT_HYGIENE.md` — the design spec this operationalizes.
