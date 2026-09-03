---
name: pre-action-grounding
description: "Use when the user says '/pre-action-grounding', 'ground this first', 'plan this properly', or hands you a task whose WHY or done-condition they did not volunteer — especially requests from non-devs that arrive unstructured. Runs the pre-action grounding protocol: derive what must be true for 'done' to be checkable, investigate before asking, grade every load-bearing item (read/ran/retrieved/assumed), ask at most 1-2 questions, bank the ungrounded residue as assumptions, and emit a goal whose success criteria are typed where an evaluator can reach and honestly prose where none can."
version: 1.0.0
---

# Pre-Action Grounding

Increase prediction confidence by **investigating first and asking last** — then
fill the knowledge graph and go. The questioning part is minimal: enough to close
what investigation cannot reach, never a substitute for it.

**Why this exists, measured:** across 59 practice databases, 3,011 of 5,484
success criteria were the auto-filled `Goal completion achieved` and 5,462
(99.6%) carried the same `validation_method`. Four goals in five said "done when
done." The asking was missing, and a default filled the slot so the absence read
as an answer.

## The protocol

```
decompose → investigate → grade → ask (residue only) → bank → emit → go
```

### 1. Decompose

Break the ask into **what must be true for "done" to be checkable**. Not task
steps — knowledge preconditions. For each: could I check this today, and with
what?

### 2. Investigate (before any question)

One `noetic-batch` bundling the lot: `project-search --task` (prior goals,
findings, dead-ends on this topic), `investigate`, targeted reads, and — mesh
present — cortex `search_knowledge`. Most done-conditions are knowable; usually
only the requester's *intent* is not.

### 3. Grade

Reuse the CHECK claims vocabulary — do not invent a parallel one:

| Grade | Meaning |
|---|---|
| `ran` | executed and observed this session |
| `read` | opened the source this session |
| `retrieved` | from a prior artifact — testimony, not observation |
| `assumed` | acting without checking |

### 4. Ask — only the residue

A question is earned only by an item that is still `assumed` **and**
load-bearing **and** knowable only from the requester's intent. Typically 0–2.
The discriminator is the Sentinel's own: *is this knowable from data I can pull,
or only from the requester's head?*

### 5. Bank the rest

Ungrounded residue you did NOT ask about → `assumption-log --confidence <0-1>`,
edged to the goal. Never a silent guess. External material cited → `source-add`,
linked via `sourced_from`.

### 6. Emit

`goals-create` with:
- `--objective` — title-shaped
- `--description` — the WHY as markdown: motivation, constraints, what finished
  looks like, links
- `--success-criteria-file` — **typed where an evaluator can reach, prose where
  none can**:

| Method | Checks | Contract |
|---|---|---|
| `completion` | subtask ratio | auto-filled sentinel, metric names, or explicit threshold only |
| `quality_gate` | named evidence metric vs threshold | description IS the metric name |
| `tests_pass` | `test_pass_rate` >= threshold (default 1.0) | skips if pytest evidence absent |
| `committed` | `commit_count` >= threshold (default 1) | threshold is a COUNT |
| `artifact_exists` | path exists | description IS the path |
| `prose` | nothing — a human reads it | deliberate, declared |
| `undetermined` | nothing yet — checkability undecided | carries a reason; revisit |

Then `goals-add-task` per unit of work, and go.

## Constraints (non-negotiable)

- **Never re-emit the default.** If investigation and one question both come up
  short, write `undetermined` with a reason. Unasked and unanswerable must not
  look the same.
- **Skipping must be structurally visible.** A skill can be skipped and nothing
  notices — so the tell is in the graph: a goal with no criteria and no logged
  assumptions means nobody ran the grading.
- **Say which mode you are in.** On a Sentinel-present seat (CLI, hooks live)
  the gate argues back at POSTFLIGHT; on a Sentinel-absent seat (Cowork, chat)
  the success criterion is the only falsifiable completion claim that survives —
  grade accordingly, and say so in the goal body.

## Worked shape

User: *"clean up the export flow, it's confusing."*

1. Decompose: which export flow; confusing to whom; what does fixed look like.
2. Investigate: `project-search --task "export flow"`, read the module, check
   prior findings — resolves *which* (`ran`/`read`).
3. Grade: "confusing to whom" → `assumed`, load-bearing, intent-only → ask ONE
   question. "What fixed looks like" → partially derivable (error rate, steps
   count), partially intent.
4. Bank: `assumption-log "users mean the CSV path, not the PDF path" --confidence 0.6`.
5. Emit: goal, why in the body, criteria:
   `tests_pass` (typed) · `artifact_exists: docs/export-flow.md` (typed) ·
   prose: "a first-time user completes an export without asking for help".
6. Go.

## Do not rebuild

Decomposition depth lives in `/epistemic-transaction` Plan Mode. Guardrails are
the Sentinel's. Retrieval is `project-search` / `investigate` / `noetic-batch` /
cortex `search_knowledge`. Storage is `goals-create` + criteria. This skill only
adds the eliciting of the *why* and the *done-condition* from a requester who
did not volunteer them — and writes both where they already belong.
