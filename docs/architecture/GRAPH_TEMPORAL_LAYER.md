# Graph + Temporal Layer

**Status:** shipped in 1.9.0

The artifact store gains a *primary-key-by-commit* dimension. Findings,
decisions, dead-ends, mistakes, unknowns, assumptions, goals, and cascades
have all been written to `refs/notes/empirica/<type>/<uuid>` for many
releases. Each git note is anchored to the commit that existed when the
artifact was logged. The new layer makes that anchoring **queryable in one
fetch** instead of requiring git-notes archaeology, and adds **edge
declaration** so the artifact graph is navigable rather than a flat
collection of isolated nodes.

This doc covers:

1. The three composable pieces (`commit-context`, `--depth N` walker,
   inline edge flags).
2. Edge persistence model — where edges live, how the walker traverses them.
3. Behavioural nudges — how unsourced/unedged artifacts surface as POSTFLIGHT
   warnings.
4. Post-compact temporal trail — how AIs discover the layer after compaction.
5. What's deferred (full git-notes-to-SQLite reconstruction; routing rules
   on `epistemic_source`).

---

## 1. Three composable pieces

### `empirica commit-context <sha|range|--since|--session>`

Aggregates artifact notes anchored to one or more commits. Read-only.
Tier 1 in the Sentinel allowlist.

```
empirica commit-context <sha>                # single commit
empirica commit-context HEAD~5..HEAD         # rev range
empirica commit-context --since 2026-04-01   # date window
empirica commit-context --session <id>       # all commits in an Empirica session
empirica commit-context --output json        # machine-readable
empirica commit-context --only-with-artifacts # skip empty commits in human output
empirica commit-context --rebuild-index      # force reindex
```

**Index:** `.empirica/cache/commit_artifact_index.json` is a flat
`commit_sha → [{type, ref, artifact_id}]` map. Built once via one
subprocess per artifact ref (one `git notes list` to find the annotated
commit per ref). Invalidated by mtime comparison against
`.git/refs/notes/empirica/`.

**Output (human):**
```
● fb73c9d9  2026-05-04 16:35:40 +0200  test(cockpit): delete test_detach_handler
  findings (2):
    [55edcd68] 2026-05-04T14:35:41  Sweep complete on tmux-touching tests …
    [eb07bd69] 2026-05-04T16:39:39  Git notes are the authoritative temporal …
  decisions (1):
    [367ca9ea] 2026-05-04T14:35:44  Delete tests that have any path to a live tmux …
```

### `--depth N` recursive walker

Walks edges from each artifact's note JSON to depth N, rendering the
**epistemic neighbourhood** at the commit. Edge sources, in order:

1. `<type>_data.edges[]` — graph-format outgoing edges from
   `log-artifacts` (entries are `{to, relation}`).
2. `goal_id` — implicit pointer to the parent goal.
3. `subtask_id` — implicit pointer to the parent subtask.
4. `<type>_data.parent_id` (+ `parent_type`) — sub-artifact hierarchy.

Cycle detection via a `visited` set. Unresolved targets render as stubs
(`[unresolved · <relation>]`) so the edge isn't silent.

**Output (human, depth 1):**
```
● 92ea988a  feat(cli): add commit-context CLI
  decisions (1):
    [1b6d037d] v1 commit-context surfaces artifact NODES per commit …
    └─ [goals/4bb4b8cd] ←in_goal  Follow-on to commit-context: add --depth N flag …
```

`--output json` returns a nested `tree` field with the same structure
machine-readable.

### Inline edge declaration on `*-log` commands

All six artifact log commands gain repeatable edge flags:

| Flag | Repeatable | Default relation | Available on |
|---|---|---|---|
| `--edge ID:RELATION` | yes | (parsed from `:`) | all six |
| `--related-to ID` | yes | `related` | all six |
| `--evidence-from ID` | yes | `evidence` | `decision-log` only |

Edges persist to **both** SQLite (via `_store_edge` from
`graph_commands.py`) **and** the artifact's git note (read-modify-write
via `git notes` plumbing — uniform across types, no per-type
`Git*Store` class changes needed). The walker traverses them
automatically.

```
empirica finding-log --finding "X" \
  --edge a1008873:supports \
  --related-to b361b2b6 \
  --impact 0.6
```

---

## 2. Edge persistence model

`log-artifacts` (the batch graph CLI) writes edges to the artifact's
`<type>_data` JSON column as `edges: [{to, relation}]`. The new inline
flags reuse the same shape:

| Storage layer | What it sees | Updated by |
|---|---|---|
| SQLite `<type>_data` JSON column | `edges: [{to, relation}]` | `graph_commands._store_edge` |
| Git note (canonical) | Same `edges` array nested under `<type>_data` | `_patch_git_note_with_edges` (read-modify-write) |
| `knowledge_graph` table | (Separate, supplementary — 16 entries today) | Not touched by inline flags |

The walker reads from **git notes** (canonical). SQLite stays in sync
because both writes happen in the same handler. If the SQLite update
succeeded but git-notes patch failed, the next run of `commit-context
--rebuild-index` would still see the artifact (just without the edge
on the walker's first pass).

**Why `knowledge_graph` is supplementary:** that table predates the
graph-format `<type>_data.edges` design. Today it has ~16 entries
project-wide. The walker doesn't query it — it reads what's in the
artifact's note JSON, which is where new edge writes land. If
`knowledge_graph` becomes load-bearing later, the walker can be
extended to merge edge sources.

---

## 3. Behavioural nudges

POSTFLIGHT retrospective and CHECK-proceed reminders gain two new
dimensions, mirroring the existing artifact-breadth nudge:

| Nudge | Fires when | Surfaces as |
|---|---|---|
| `edge_density_nudge` | ≥2 artifacts in transaction declare 0 edges | `edge_density_note` (retro) + `edge_density_warning` (POSTFLIGHT feedback) |
| `sources_discipline_nudge` | ≥2 artifacts in transaction have empty `source_refs` | `sources_discipline_note` + `sources_discipline_warning` |

`_retro_count_edges` parses `<type>_data.edges` per artifact via
`json_array_length(json_extract(...))`. `_retro_count_sources` checks
`source_refs IS NOT NULL AND NOT IN ('', '[]', 'null')`.

**Why these specifically:** the walker reach scales with edge declaration.
Sources discipline is the lever for compliance/provenance. Both have
existing helpers (`--related-to`, `--source`) — the nudge surfaces when
the AI used the simpler form without leveraging the linkage.

---

## 4. Post-compact temporal trail

`post-compact.py` injects a one-line trail into all three prompt
generators (new-session, transaction-continue, check-gate). After
compaction, an AI reading the bootstrap context sees:

```
**Temporal trail:** 4,343 artifact git notes anchored to commits.
Query: `empirica commit-context <sha>` or
`empirica commit-context --range HEAD~10..HEAD` or
`empirica commit-context --since <date>`.
```

Without this, the only path to per-commit artifact retrieval was raw
`git notes` archaeology — the gap that motivated the layer's design.

The constitution skill (`empirica-constitution`) has a parallel entry
in the "I don't know something" decision tree:

```
About a past commit → empirica commit-context <sha> [--depth N]
```

---

## 5. What's deferred

- **Full git-notes-to-SQLite reconstruction.** Profile-Manager goal
  exists. The walker reads from git notes already, so the cache index
  is sufficient for queries. Reconstruction matters for SQLite
  bootstrap from git-only profile shares.
- **`epistemic_source` routing rules** (v1 source-aware Sentinel).
  Migration 040 adds the column; POSTFLIGHT surfaces the
  intuition/search/mixed ratio. v0 is visibility-only.
  `docs/architecture/PROPOSAL_SOURCE_AWARE_SENTINEL_v1.md` is the
  design doc; routing deferred until calibration history accumulates.
- **`commit-context` recursive walker on `knowledge_graph` edges.** The
  walker stops at `<type>_data.edges` + implicit pointers. Adding
  `knowledge_graph` as a fifth edge source is a few lines if/when the
  table gets adoption beyond its current ~16 entries.
- **Workflow-commands split.** `_build_retrospective` +
  `_retro_count_*` helpers all live in
  `empirica/cli/command_handlers/workflow_commands.py` (3,874 LOC).
  Splitting into focused modules (PREFLIGHT/CHECK/POSTFLIGHT
  handlers, retrospective+nudges helpers, output formatters) is the
  highest-leverage refactor in the codebase but multi-transaction
  work, deferred from 1.9.0.

---

## See also

- `docs/architecture/CHAT.md` — `log-artifacts` graph-format spec
  (nodes + edges, 9 relation types).
- `docs/architecture/PROPOSAL_SOURCE_AWARE_SENTINEL_v1.md` — v1
  routing-rule design for `epistemic_source`.
- `~/.claude/plugins/local/empirica/skills/empirica-constitution/SKILL.md`
  — search routing tree pointing at `commit-context`.
- Source: `empirica/cli/command_handlers/commit_context_commands.py`,
  `empirica/cli/command_handlers/artifact_log_commands.py`
  (`_collect_edges_from_args`, `_persist_edges`,
  `_patch_git_note_with_edges`),
  `empirica/cli/command_handlers/workflow_commands.py`
  (`_retro_count_edges`, `_retro_count_sources`,
  edge_density / sources_discipline nudges).
