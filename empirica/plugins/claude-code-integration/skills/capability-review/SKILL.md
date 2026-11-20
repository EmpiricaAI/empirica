---
name: capability-review
description: "Use when a person or the AI has lost track of what Empirica provides, or when deciding what to make more prominent, teach in the instructions, or retire — 'what can Empirica do', 'remind me what's here', 'what am I not using', 'what's worth keeping', 'which commands are dead', 'what should be in the prompt', 'capability review', and before a minor release or once a month. Gives a tour of what is available grouped by the moment it is used, filtered to this practice; measures what is actually used with a census; and proposes keep, make more prominent, demote or retire, each with evidence, for the human to decide. Modes: refresh, usage, assess."
version: 1.0.0
---

# Capability review

Load this when the question is "what does Empirica do for me", "what are we not using", or "what should go". Even the people who
build Empirica forget what it provides (David, 2026-11-20), and a feature nobody remembers is as good as absent; one nobody
uses is cost. This skill keeps both visible.

**Never answer from memory.** What exists and what is used change every release. Every list below comes from a command run in this
pass, and a mode that cannot run its command says so instead of recalling.

## Modes

| Mode | Question | Writes? |
|---|---|---|
| `refresh` | What is here, and what of it fits my work? | nothing |
| `usage` | What has actually been used, and what never? | nothing |
| `assess` | What should be more prominent, taught in the instructions, demoted or retired? | after the human decides: goals, a deprecation entry, a proposed prompt or skill edit |

No mode word: run `refresh`, then offer `assess`.

## Where the truth lives

| What | Live source |
|---|---|
| Commands | `empirica --help` (the parser's own list); `empirica <command> --help` for one |
| Skills | the plugin's `skills/` folder, one directory per skill with a `SKILL.md` whose description says when it loads (`docs/reference/SKILLS.md` in the repository indexes them) |
| What the always-loaded prompt teaches | the lean prompt template (`templates/empirica-system-prompt-lean.md`), its WHEN TO LOAD SKILLS table above all |
| What is used | `empirica efficiency-report [--days N] --output json`: the census |
| What is going away | `config/deprecated_commands.yaml` |

## `refresh`: what is here

One screen, for the person and the AI alike. Group what exists by the moment someone needs it, not by subsystem: starting a session,
planning, investigating, logging what was learned, closing a transaction, releasing, working with peers (mesh), the cockpit,
gardening the graph, forecasting. For each moment: the two or three commands or skills that matter, one line each on what it gives.

Filter to this practice. Read its type from `.empirica/project.yaml`, whether Cortex is configured (`empirica auth status`), and
whether the mesh is on. Leave out what cannot apply here (mesh skills without Cortex, release gates in a practice that ships
nothing). Then run the census and end with **"fits your work, not used here"**: at most five capabilities that match this practice
and show no use in the window, each with the one situation it is for.

## `usage`: what is used

Run `empirica efficiency-report --days 30 --output json` (another window if asked) and report: the most used commands, commands and
aliases never used, those used one to three times, skills never loaded, artifacts logged by type. Copy the census's `limits` line
into the report every time: it counts what the AI ran through Claude Code on this machine, and does not see hooks, scripts, other
harnesses, other machines or people at a terminal.

## `assess`: what to change

Start from `usage`. Classify each candidate:

| Verdict | When | What follows, after the human says yes |
|---|---|---|
| **retire** | unused in the window, and nothing that is a caller names it | a deprecation entry: `since` the next release, `remove_in` the one after, a replacement or null, the reason |
| **demote** | taught in the always-loaded prompt but rarely used | move the teaching into the skill or doc where it is used (`/authoring-guidance`) |
| **more prominent** | used often or clearly useful, but nothing teaches when to reach for it | a trigger in the prompt's load table, a line in the skill that owns that moment, or a bootstrap hint |
| **keep** | used, and taught where it is needed | nothing |

Before calling anything dead, **read its `references`**. A hit in the command's own handler (its usage docstring) is not a caller; a
hook, a skill, a prompt, an API route or another command's output telling someone to run it is. Aliases go first: they cost surface
and teaching for the least use. A skill never loaded may still be right if its moment is rare (a release-only skill, an onboarding
skill): say how often its moment comes up, and do not retire a skill for a window shorter than that.

Ask the human as predicted answers (`AskUserQuestion`, your recommendation first and marked Recommended, the evidence in the option):
one question per batch, never one per command. **Nothing is retired, demoted or promoted without that yes.** Approved retirements go
into `config/deprecated_commands.yaml`; using one then prints a notice for a release, and a test fails if it outlives `remove_in`. A
skill to retire gets a `planned` goal to remove it in the same release a command would go.

## Cadence

Once a month and before a minor release. At the end of each pass, propose a `planned` goal with `--due +30d` for the next one, so the
review comes back up rather than depending on someone remembering it.

## Report

Done / Next / Waiting. `refresh`: the grouped tour and the "fits your work, not used here" list. `usage`: the counts and the limits
line. `assess`: the verdicts with their evidence, what the human approved, and the goals and deprecation entries created.
