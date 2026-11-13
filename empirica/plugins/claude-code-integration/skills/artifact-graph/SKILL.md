---
name: artifact-graph
description: "Use when logging two or more artifacts at once, when unsure which type an observation is (finding, unknown, assumption, decision, mistake, dead end), when connecting artifacts with edges, when closing one (retract, expire, invalidate, assess), when deciding whether to share one beyond the practice, or when a claim you logged turns out false. Covers type collapse, split-before-you-log, the gaps every practice shares, lifecycles and their honest endings, what federates and what does not, use by practice type, orphans and the cognitive immune system. Triggers: 'which artifact type', 'retract', 'expire', 'share this', 'visibility', 'orphan', 'log-artifacts', 'type collapse'."
version: 1.1.0
---

# Artifact graph

The depth behind the prompt's artifact-type table, for the moment you log, connect, close or share. The full reference, with
diagrams of every lifecycle and where each type is stored, is `docs/reference/ARTIFACT_GRAPH.md`. The vocabularies are the ones
`empirica log-artifacts --schema` and `empirica resolve-artifacts --schema` print; when this skill and a schema disagree, the
schema is right.

---

## The graph is the artifact

The epistemic layer is a **typed graph**, and both words carry weight. Two failure modes degrade it, and both are silent: nothing
errors, the graph simply stops being able to answer.

### Type collapse

Every type answers a different question; flatten them and retrieval returns a pile you cannot reason over. The prompt's table is
the reference for the six everyday types. The seventh, the **falsifier**, answers "what OBSERVATION would refute a belief I am
acting on?". It is registered before the evidence (the `falsifiers` array at PREFLIGHT or CHECK, naming its parent artifact) and
outlives the transaction. The two confusions worth repeating: a defect in the code is a **finding** and *shipping* it is a
**mistake**; something unverified is an **assumption** and something you know you don't know is an **unknown**.

**The symptom:** a session logs dozens of findings and no unknowns or assumptions while its vectors report uncertainty. The
uncertainty was real; the typing was not done. **Vector uncertainty with no `unknown` or `assumption` behind it is an unsupported
claim.**

### Split before you log

Scan your artifact for proper nouns, identities, versions, attributions: anything a reader could act on that you did not
directly observe. Of each, ask: did I *observe* this, or *supply* it? Supplied is an assumption however confident you are.

An artifact is read as a unit, so a tag on the whole cannot mark part of it: `epistemic_source`, a confidence and a caveat in the
prose are all honest, and none stops a reader lifting the name out of the sentence. Split, and the wrong half retracts without
losing the right one. **The types already carry this**: when it goes wrong the typing failed, not the vocabulary.

---

## The gaps every practice shares

Measured across 33 practices (2026-11-13), whatever their type:

| Gap | Measured | What to do at the moment |
|---|---|---|
| Assumptions are not typed | 1 to 4 per 100 findings everywhere | Before acting on something you did not check, log it as an assumption. If you register a falsifier, it needs a parent: this is it |
| Decisions are never graded | outcomes recorded for 0 to 4% of decisions | When a choice has played out, assess it: `outcome` upheld, reversed or mixed |
| Provenance is not linked | 70 `sourced_from` links across 15,462 findings (27 in one practice) | A claim from outside the repo gets a source and a `sourced_from` edge, at logging time, not later |

These matter more than any context-specific advice below.

---

## Connect it

**An artifact connected to nothing is barely worth logging.** It cannot be swept, re-evaluated, or invalidated alongside its
premises.

- **Most edges should point at PRIOR artifacts**, not only within the batch. All-internal edges build one island per
  transaction.
- **Pick the relation that carries meaning**: `evidence`, `grounded_by`, `raised_by`, `resolves`, `invalidates`,
  `sourced_from`, `caused_by`, `prevents`. `related` asserts almost nothing; reach for it last. `attached_to` (artifact to goal)
  is written for you.
- **`log-artifacts` / `resolve-artifacts` / `delete-artifacts` are the default path** because they operate relationally. A single
  `*-log` verb is right for one genuinely standalone artifact.

---

## Close it honestly

A graph that only grows is an archive, not a model of what you believe now, and stale artifacts mis-steer retrieval rather than
sitting inert. Every ending below goes through `resolve-artifacts`; none deletes anything.

| Type | Endings | The one to reach for when |
|---|---|---|
| finding | `stale`, `superseded` (name the successor), `retracted`, `mistyped` | **`retracted` when it was wrong.** `stale` only when it was true and aged |
| unknown | answered | cite what answered it |
| assumption | verified, falsified, **expired** | `expired` when nobody tested it before its subject went away: neither verified nor falsified |
| decision | outcome `upheld`, `reversed`, `mixed`, optional `regret` | when the choice has played out |
| dead end, mistake | invalidated | only when something showed the constraint false. Never for being old: they are meant to resurface |
| falsifier | `tripped`, `survived`, `expired` | `survived` needs evidence that you looked at the population, or it records as `expired` |
| lesson | superseded by a named successor | the only way a lesson retires |

**Retraction is the move practices skip.** Closing what is *done* and retracting what was *wrong* feel the same and are not. A
practice whose resolutions are almost all `stale` was not rarely wrong; it had no way to say so, and cannot calibrate on either.

Correct the CLAIM by retracting it; correct the METADATA (impact, visibility, epistemic_source) with `update-artifacts`. Claim
text is never edited. A tripped falsifier does not close its parent: invalidate or retract the parent in its own call.

---

## Share it, or don't

| You want | Do |
|---|---|
| Nothing to leave this practice | nothing: `local` is the default |
| The organisation's practices to find it | `--visibility shared` (`public` for anyone with a Cortex account) |
| A pattern another practice can act on | a **lesson** with `sharing_policy` `org` or `public` in the payload. Findings *describe*, lessons *transfer* |
| Another practice to own the follow-up | log it there with `--project-id <practice>`, or ask over the mesh, and cite the original |

What actually federates to `global_learnings`: unresolved findings with impact 0.7 or more that are shared or public; resolved
unknowns that are shared or public; shared or public dead ends; lessons at `org` or `public`. **Assumptions and decisions accept
`--visibility` and nothing federates them yet.** There is no verb that moves an existing artifact to another practice.

`--epistemic-source {intuition|search|mixed}` says how you arrived at the artifact. Be honest: high vectors with every artifact
tagged intuition is the rubber-stamp pattern.

---

## By practice type

The mix differs a little by context. These are recommendations drawn from one or two practices per type; the universal gaps
above come first.

| Practice | Lean on |
|---|---|
| software | dead ends and mistakes with a real prevention; findings tied to commits |
| content | a source and `sourced_from` for every claim from outside; decisions graded by how the piece landed |
| engagement | assumptions about the client, verified or falsified with them; decisions that name the client's reversal conditions |
| operations | mistakes whose prevention names the check; falsifiers on the state of running systems |
| research | unknowns as research questions; assumptions as hypotheses; falsifiers registered before the experiment; lessons for what transfers |

---

## The cognitive immune system

**Lessons are antibodies. Findings are antigens.** When `finding-log` fires, related lessons have their confidence reduced
(floor 0.3, so lessons never fully die). If a finding contradicts a lesson you would expect to apply here, that lesson's
confidence has already been adjusted. Trust the freshest evidence; reach for the lesson through `project-search` only when its
confidence still clears the threshold.

**The turtle check:** audit your own graph rather than assuming discipline held. Count the types, the orphan rate, the
retracted share. A practice that has never measured these knows nothing about them.
