---
name: authoring-guidance
description: "Use when writing or editing a prompt, skill, hook or tool: which of them a piece of guidance should be (a tool acts, a skill guides, a prompt frames once), the 'anything re-typed twice' rule, never instructing what the platform cannot do, inventories living in live surfaces, and why a trigger the model must notice in itself fires rarely. Also the turtle principle: the same rules apply to meta-work. Triggers: 'write a skill', 'edit the prompt', 'where should this guidance live', 'add an instruction'."
version: 1.0.0
---

# Authoring guidance

Load it before adding to, moving, or removing guidance from a prompt, skill, hook or tool.

---

## Tools, skills, prompts: which one is this?

Three ways to make a capability available. Picking the wrong one is why guidance
gets re-typed, drifts, and stops being obeyed.

| | does what | governance |
|---|---|---|
| **tool** | **acts** — typed contract, executes now, refusals legible | the contract itself |
| **skill** | **guides** — packaged practice, trigger-loaded | org-governed via ECO |
| **prompt** | **frames once** — no persistence | none |

**The discriminator: anything re-typed twice belongs in a tool or a skill.** A
prompt frames one exchange. The moment its content has to survive one, it has
outgrown the layer, and the next copy will drift from the first.

Two corollaries you will need before you reach for any of the three:

- **Never instruct what the platform cannot do.** An impossible instruction is not
  a strict rule — it teaches the reader that the guidance is unreliable, and that
  lesson generalises to the rules that were correct.
- **Inventories live in live surfaces; teaching POINTS at them.** A hand-written
  list of verbs, practices or fields is a two-sources-of-truth defect scheduled for
  its first drift. Name the command that prints the current answer instead.

The full set, with reasoning, is **shared lesson `e3b487c997177444`**
(`cross_org`) — the single source this constitution and cortex's how-to-cortex
skill both cite. Pull it once with
`empirica lesson-create --from-global e3b487c997177444`, then
`empirica lesson-load --id <id>` locally. Do not copy it here: two hand-copies of
one policy is exactly the drift the second corollary names.

**A trigger the model must notice about itself fires rarely.** `/reporting-discipline` was loaded 3 times against about 4,970
POSTFLIGHTs because its trigger ("before you report") asks the model to catch a moment it cannot see from inside. Deliver such a
trigger from a tool at the moment it matters: the POSTFLIGHT response now carries the reply shape itself.

---

## The turtle principle

"Turtles all the way down" — the same epistemic rules at every meta-layer.

- The Sentinel monitors using the same 13 vectors it monitors you with.
- Goals about goal-management are themselves goals.
- This constitution governs itself: if a section is wrong, fix it through the same
  find–log–decide cycle as any other work.
- Auditing skills is itself skill usage, and gets the same
  PREFLIGHT/CHECK/POSTFLIGHT treatment.

Don't bypass measurement for meta-work. The loop closes by being load-bearing at
every level.
