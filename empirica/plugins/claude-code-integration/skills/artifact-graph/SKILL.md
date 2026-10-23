---
name: artifact-graph
description: "Use when logging two or more artifacts at once, when unsure which type an observation is (finding, unknown, assumption, decision, mistake, dead end), when connecting artifacts with edges, or when a claim you logged turns out false and needs retracting rather than closing. Covers type collapse, split-before-you-log, provenance and visibility, orphan accumulation, the retraction table and the cognitive immune system. Triggers: 'which artifact type', 'retract', 'orphan', 'log-artifacts', 'type collapse'."
version: 1.0.0
---

# Artifact graph

The depth behind the prompt's artifact-type table and signal-to-action table: how to TYPE what you log, how to CONNECT it, and how to
retire it honestly. Load it at the moment you are about to log a batch, hesitate over a type, or find that something you logged was
false.

---

## The graph is the artifact

The epistemic layer is a **typed graph**, and both words carry weight. Two failure
modes degrade it, and both are silent — nothing errors, the graph simply stops
being able to answer.

### Type collapse

Every type answers a different question; flatten them and retrieval returns a pile you cannot reason over. The prompt's
artifact-type table is the reference for the six everyday types. The seventh it does not carry: a **falsifier** answers "what
OBSERVATION would refute a belief I am acting on?". It is registered before the evidence (the `falsifiers` array at
PREFLIGHT/CHECK, naming its parent artifact) and, unlike a claim, outlives the transaction. The two confusions worth repeating: a
defect in the code is a **finding** and *shipping* it is a **mistake**; something unverified is an **assumption** and something you
know you don't know is an **unknown**.

**The symptom:** a session logs dozens of findings and no unknowns or assumptions while its vectors report uncertainty. The
uncertainty was real; the typing was not done.
**Vector uncertainty with no `unknown`/`assumption` behind it is an unsupported
claim** — exactly the divergence calibration exists to surface.

### Split before you log

Scan your own artifact for proper nouns,
identities, versions, attributions — anything a reader could act on that you did
not directly observe — and ask of each: did I *observe* this, or *supply* it?
Supplied is an assumption however confident you are. Proper nouns bind hardest,
because a name is what a reader can least check and is most likely to act on.

An artifact is read as a unit, so a tag on the WHOLE of it cannot mark part of it:
`epistemic_source`, a confidence, a caveat in the prose are all honest and none
stops a reader lifting the name out of the sentence. Split, and the wrong half
retracts without losing the right one. **The types already carry this** — when it
goes wrong the typing failed, not the vocabulary, so the answer is gardening and
never a new field.

### Provenance and visibility

`--epistemic-source {intuition|search|mixed}` on every `*-log` (and `data.epistemic_source` in `log-artifacts` payloads) tags how
you arrived at the artifact; POSTFLIGHT's calibration reflection counts intuition against search per transaction. It is
visibility only (no routing rule yet). Be honest: vectors asserted high while every artifact is intuition-tagged is the
rubber-stamp CHECK pattern the substrate exists to expose.

`--visibility {public,shared,local}` is the opt-in for being discoverable from other projects: `local` (default) stays in this
project, `shared` is visible across the org's projects (Cortex tenancy), `public` to anyone with a Cortex account. The pull side
is `empirica project-search --task "..." --global`, which searches the `global_learnings` collection (promoted shared/public
artifacts) and this machine's other projects' collections. It stops at this machine: mesh-wide retrieval is `cortex investigate`
when Cortex is connected. Share liberally what
has ecosystem-wide value (security patterns, cross-repo bugs, reusable lessons); keep tactical work `local`.

### Orphan accumulation

**An artifact connected to nothing is barely worth logging.** It cannot be swept,
re-evaluated, or invalidated alongside its premises — which is what gardening and
blindspot propagation operate on.

A graph where most artifacts have no edge is a list wearing a graph's clothes.

- **Most edges should point at PRIOR artifacts**, not only within the batch you are
  writing. All-internal edges build one disconnected island per transaction.
- **Pick the relation that carries meaning** — `evidence`, `grounded_by`,
  `caused_by`, `invalidates`, `resolves`, `sourced_from`. `related` asserts almost
  nothing; reach for it last.
- **`log-artifacts` / `resolve-artifacts` / `delete-artifacts` are the default path**
  because they operate relationally. Single `*-log` verbs stay correct for one
  genuinely standalone artifact — the exception, not the habit.

### Closing the loop, and the retraction gap

Resolve unknowns when answered. Invalidate what new evidence kills. Supersede what
you replaced. **A graph that only grows is an archive, not a model of what you
believe now** — and stale artifacts actively mis-steer retrieval rather than sitting
inert, because what survives compaction is the graph, and the graph is what comes
back to you.

**Retraction is the move practices reliably skip.** Closing what is *done* and
retracting what was *wrong* feel similar and are not: the first records progress, the
second records error.

| Kind | Means |
|---|---|
| `stale` | it was true when written and has aged |
| `superseded --superseded-by <id>` | a newer artifact replaced it |
| `retracted` | **it was wrong** |
| `mistyped` | it was another type wearing a finding's clothes |

Before `resolution_kind` existed, a practice's resolutions were free text with no way to record *wrong*, and nearly all read as
staleness. An error rate near zero across thousands of claims is not plausible: errors were simply not being expressed. **A practice that cannot
distinguish its ageing from its errors cannot calibrate on either.**

Correct the CLAIM with `finding-resolve --kind retracted`; correct the METADATA
(impact, visibility, epistemic_source) with `update-artifacts`. Claim text is
immutable by design, so retraction preserves the original wording and records that
it failed.

**The turtle check:** audit your own graph rather than assuming discipline
held — count the types, count the orphan rate. A practice that has never measured
these does not know they are healthy; it knows nothing about them.

---

## The cognitive immune system

**Lessons are antibodies. Findings are antigens.**

When `finding-log` fires, related lessons have their confidence mechanically
reduced — floor 0.3, so lessons never fully die. Fresh evidence outranks stored
knowledge without the history being lost.

The discipline implication: if a finding contradicts a lesson you would expect to
apply here, that lesson's confidence has *already* been adjusted. Trust the freshest
evidence; reach for the lesson through `project-search` only when its
decay-adjusted confidence still clears the threshold.
