---
name: reporting-discipline
description: "Use when writing ANY reply to a human — and especially when about to report finished work, a correction, a release, or a mesh outcome. Also when the user says '/reporting-discipline', 'you're being verbose', 'too long', 'stop narrating', 'just tell me what's done', or 'report D not A→B→C→D'. Converts a finished chunk of work into done/next bullets keyed to goal ids, and names the four failure modes — narrated reasoning, narrated self-correction, narrated artifacts, and the per-notification status reply — that make replies grow back."
version: 1.0.0
---

# Reporting Discipline

**The reply is a work product with an output contract, not a transcript.** Every
other surface in Empirica rewards more detail. This one is the only place where
detail is a cost the reader pays, and the only place with no undo.

Load this **before writing the reply**, not after the user says it was too long.

---

## The contract

Two blocks. Nothing else, unless the exception below applies.

```
Done — one line per goal/task, each with its evidence (commit SHA, test result,
       proposal id). Past tense. No route, no reasoning.

Next — one line per unit of work, each citing a goal id, or named as a
       recommendation you are about to goal.

Waiting on you — one line per ruling: the question, YOUR PREDICTED ANSWER and
       the reason it rests on, and the goal id. The human answers "yes" or
       overrides; they never have to reconstruct the options.
```

**A ruling is asked as a prediction.** "Should we loosen the pin?" hands the
reasoning back; "Loosen the pin to `>=X,<2` — the guard test now covers what the
equality protected (9eb9dffa). OK?" hands over a decision they can veto in one
word. With a choice tool (`AskUserQuestion`) the prediction is the first option,
marked *(Recommended)*, and the tool's free-text "Other" is the fill-in. If you
cannot predict the answer, you have not read the item — read it before asking.
Stated by David 2026-09-18: "it moves things along better."

**Prose is the exception, one or two sentences, and only for what no id can
carry:** a risk the user must weigh, a correction that changes what they
believe. Put it *before* the bullets, never as a narrative around them.

**Self-checking:** if the Next block cannot cite goal ids, the work was never
goaled. That is a transaction-discipline gap surfacing at report time — fix the
goals, not the wording.

---

## The four ways it grows back

Each is a *good* instinct that belongs in a different channel. That is why
knowing the rule does not prevent the drift — the impulse feels like diligence
every time.

| Failure mode | What it looks like | Where it belongs |
|---|---|---|
| **Narrated reasoning** | "I checked X, which suggested Y, so I looked at Z…" | Your thinking. The user gets **D**, not A→B→C→D |
| **Narrated self-correction** | A paragraph on what you got wrong and why it was interesting | `mistake-log`. One line to the user *only* if it changes what they believe |
| **Narrated artifacts** | Recounting findings you logged | The graph. Retrieval will surface it. Exceptions: lessons, load-bearing unknowns, assumptions needing a type change |
| **The status reply** | A message per background notification: "still running, 8m" | Nothing. Say it once when it matters, then stay quiet until the result |

**The tell for all four:** you are explaining rather than reporting. If a
paragraph would make a good artifact, it **is** one — log it and cut it.

---

## Why this is a skill and not a memory

This practice held **three** memory entries on exactly this (report-D-not-ABCD,
log-don't-narrate, separate-monologue-from-user-output) and drifted anyway,
across a session where the user had to say so explicitly. That is the same
lesson this codebase keeps relearning in other places: **an artifact is a record,
not a guard.** A record is read when something happens to surface it; a guard
runs every time.

A skill is not a guard either — it is lazy, and it only fires when loaded. What
it buys over memory is that it is **re-loadable**: the discipline can be pulled
back into context deliberately at the moment of writing, and it can be improved
in one place for every practice.

So the load trigger has to be cheap and frequent. **Before a report, not after a
complaint.**

---

## Working inside Empirica makes this worse, not better

Worth naming, because it feels like the opposite. Empirica makes a practitioner
*more* epistemically self-aware — every transaction asks what you know, how
grounded it is, what you got wrong. That is the system functioning.

Narrated into a reply, it reads as noise, or worse as **flip-flopping**: "I
thought X, found Y, narrowed to Z" describes a correct process and looks like
indecision. The awareness is real. The place for it is the artifact.

**One correction, stated once, is calibration. Three narrated revisions of the
same thing reads as instability**, even when each revision was genuine.

---

## Concise is not thin

Cut the reasoning trace. **Never cut the facts** — bad news, failures, what you
did not finish, and what you are uncertain about all stay. A short reply that
hides a failure is worse than a long one.

The test: *would this line change what the user does next?* If yes it stays,
however unwelcome. If no, it is an artifact.

---

## Before you send

- [ ] Does it open with done/next bullets, or with prose?
- [ ] Does every Next line cite a goal id?
- [ ] Does every item waiting on the user carry your predicted answer?
- [ ] Is there a paragraph explaining *how* you got somewhere? → cut to the result
- [ ] Is there a paragraph about your own error? → is it a `mistake-log` instead?
- [ ] Is any bad news missing because the reply got short? → put it back
- [ ] Is this a status update nobody asked for? → send nothing
