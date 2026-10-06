---
name: agent-pipeline
description: Turn a review of code or docs into verified, actioned work with tiered agents. A cheap agent maps, a smart agent tags findings with predicted steps and a test written before any action, a skeptic verifies, a cheap agent acts in a sandbox, and the whole chain is reviewed afterwards with agent-review. Use for a sweep whose findings you intend to fix, not for a single bug.
---

# Agent pipeline: map, tag, act, review

Reviewing code and fixing it are different jobs with different costs. This pipeline puts each
stage on the cheapest tier that its output can be **mechanically checked** at, and refuses to
let an unchecked output become work for the next stage.

```
map (sonnet)  ->  tag (opus)  ->  verify (skeptic + the tagger's own test)  ->  act (haiku, sandboxed)  ->  review (agent-review)
pointers          findings,       default-refute, planted controls,            edits a clone, hidden test,    stamps threads and diffs;
checked           predicted       test run on the UNMODIFIED base              related tests, one escalation  catches what the tests passed
mechanically      steps, a test
```

Opt-in, and costly: a sweep of four units (11,053 lines) ran about **$90** at list price. Use it
when you will act on what the agents find. A single bug, a lookup, or a docs edit does not need it.
Method, numbers and caveats in full: `docs/guides/AGENT_PIPELINE.md`.

## What was measured (four units, one run, 2026-10-06; list-price estimates)

| | result |
|---|---|
| Mapper as a saving | **No.** Opus tagging only the sonnet-pointed regions (31-50% of lines) cost 0.94-1.12x opus reading the whole unit: $0.60 vs $0.53 per confirmed finding. |
| Mapper as diversity | **Yes.** The mapped pipeline found 40 distinct verified clusters of 51, the smart-only one 34; 17 and 11 were unique to each. Union the two if recall matters. |
| Haiku as the action tier | 43 of 45 tasks passed the tagger's test with no regression; 35 of 45 after a sonnet read of the diff; 2 escalations to sonnet, both passed. |
| Tagger predictions | 8 of 8 predicted answers matched the human's, **but** the prediction was shown first and labelled Recommended, which can anchor the pick. |
| What the mechanical stages missed | 10 of 45 diffs flagged by a reviewer although the tests passed; 15 thread-level mistakes, including "all tests pass" reports the thread contradicts and new tests never run against the unfixed code. |

## Stages and their gates

1. **Map** (optional). A sonnet agent reads the unit and returns pointers (file, lines, reason).
   Run `scripts/validate_pointers.py`: drop any pointer whose file is not in the unit, whose range
   is inverted, runs past the file or exceeds 150 lines. A cheap mapper's line numbers are the first
   thing to distrust. Prompt: `prompts/mapper.md`. Use sonnet to map: haiku as a mapper
   was only tested against findings a full read had already produced (a circular baseline), so it is unmeasured on fresh code.
2. **Tag.** An opus agent reads the pointed regions (or the whole unit, the smart-only arm) and
   returns `schemas/tag.schema.json`: typed artifacts, tasks with predicted steps, **a complete pytest
   file per task written before any action**, and proposals with predicted answers. Prompt:
   `prompts/tagger.md`. Run `scripts/check_tag_quotes.py`: every quote must be verbatim; it is the
   one check on a tagger that depends on no model.
3. **Verify.** Three instruments, because each catches something the others pass:
   - the tagger's test, run on the **unmodified base** by `scripts/run_base_tests.py` (an
     AssertionError means the finding reproduces; an exception is "broken", a lower bound);
   - a default-refute sonnet skeptic over every finding (`prompts/skeptic.md`, verdict schema) with
     **one planted false control per unit** (`prompts/forger.md`): a skeptic that confirms the
     control is not skeptical. Cluster the union of both pipelines with `prompts/cluster.md`;
   - unverified findings do not become tasks.
4. **Act.** One haiku run per task through `scripts/run_action.py`: a headless `claude -p` in a
   throwaway clone, an allow-list, `--permission-mode dontAsk`, the objective and predicted steps
   only (never the hidden test). Success is mechanical: the hidden test passes and no existing
   test that passed before now fails (a pristine re-run separates a regression from a failure
   that already existed). Escalate once to sonnet on failure; record the rate.
5. **Review.** Read each diff against its report (`prompts/diff-review.md`, sonnet, skeptical: a
   comment-only change is not a fix), then run **`agent-review`** over the action and tagger threads
   (sonnet by default, opus where mistakes and tacit assumptions matter). This is the stage that
   found what the tests passed.

## Rules that cost an experiment to learn

- **Run the controls before the grid.** Pass, fail and deliberately broken controls go through any
  new runner first (`run_base_tests.py` does this and aborts); name the interpreter that has the
  project's dependencies. A wrong interpreter made 38 of 45 tests "broken" and read as a result.
- **A planted control must be blind.** Render control and real cases from one template with a
  neutral id; the marker lives only in your key file. A diff titled "CTRL" proved nothing.
- **Check freshness by reading the hits.** A file named in an earlier result is not the same as
  reviewed, nor the same as fresh: count the overlap per file and put it in the freeze record.
- **Workflow agents get prompt-free tools only** (Read, rg, fd, sed -n): anything else raises a
  permission prompt for a human per call. **Editing agents do not run as Workflow agents:** run them
  with `run_action.py` (headless, sandboxed), where nothing can prompt.
- **A tagger's test is its own claim.** 11 of 45 did not reproduce on the base by assertion, so a
  pass there is weak. Read a "passed" next to its base class.
- **Unanchored predictions.** If you want to know whether the tagger's predicted answers are right,
  ask the human without showing them first.

## Choosing tiers

Reading dominates cost, and a smarter tier is not cheaper per line: size the work by what must be
read, not by how many lines are pointed at. Reviewing threads: sonnet had 96% precision and 63% pooled
recall for an estimated $4 per eight threads, opus 98% and 74% for $10; haiku is never worth it (62% of
its quotes were verbatim). Haiku
acts well on well-specified tasks with a test; the two cases where it failed were fixed on one
sonnet retry. These are four units on one repository: re-measure on yours.

## Caveats

n is four units, one run, one model family, one repository. Both taggers hit the 14-artifact cap, so
recall is relative to the pool they produced. The skeptic and the clusterer were both sonnet and
the skeptic confirmed 96% of real findings: its softness is untested beyond one control template.
Local models (the strix) were not run. Skeptic-confirmed findings on fresh code were mostly low
impact (3 high of 74).

Files beside this skill: `scripts/` (validate_pointers, check_tag_quotes, run_base_tests,
run_action), `schemas/` (tag, verdict), `prompts/` (placeholders are `{UPPER_CASE}`; substitute
with a plain string replace, not `str.format`, because the prompts contain braces). Companion:
`agent-review`.
