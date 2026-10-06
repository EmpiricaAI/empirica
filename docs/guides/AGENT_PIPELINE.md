# From a review to fixed code with tiered agents

A review that nobody acts on is a report. A fix nobody checked is a risk. The `agent-pipeline`
skill ties the two together with agents of different cost: a cheap agent maps, a smart agent
tags findings and writes the work and a test for it, a skeptic verifies, a cheap agent does the
work in a sandbox, and a final reader checks the whole chain. This guide is the method, what
four fresh units showed on 2026-10-06, and what that does not show.

Skill: [`agent-pipeline`](../../empirica/plugins/claude-code-integration/skills/agent-pipeline/SKILL.md).
Companion: [`agent-review`](AGENT_REVIEW.md), which reads the finished threads. Opt-in, and
costly: the run below came to about $90 at list price.

## The idea

Each stage runs on the cheapest tier whose output can be **checked mechanically** at that stage,
and an unchecked output is never handed to the next stage.

| stage | tier used | what is checked, and by what |
|---|---|---|
| map | sonnet | pointers: file in the unit, range valid, at most 150 lines (a script) |
| tag | opus | quotes verbatim (a script); a pytest written before any action |
| verify | sonnet skeptic | the tagger's test on the UNMODIFIED base (a script); a planted false control per unit |
| act | haiku, sandboxed | the hidden test passes and no existing test regresses (a script) |
| review | sonnet, opus | anchors of every stamped artifact (`agent-review`'s `check_stamp.py`) |

The tagger also asks a human what to decide, as proposals with a **predicted answer**: the
decision the human is likely to make, so agreeing costs one word.

## What ran

Four units frozen at one commit (11,053 lines: two CLI handlers, two Qdrant modules, ten cockpit
modules, five API route modules). Two pipelines over the same units: **P-map** (sonnet maps, opus
tags the pointed regions) and **P-full** (opus maps and tags itself). 4 mappers, 8 taggers, 4 skeptics, 4 clusterers, 4 forgers,
45 haiku action runs, 2 sonnet escalations, 9 diff reviewers, 12 thread reviewers. Costs are list-price
estimates from token counts, not billing.

| stage | est cost |
|---|---|
| map (4 sonnet) | $5.30 |
| tag (8 opus) | $18.68 (P-map) / $18.10 (P-full) |
| verify (skeptics, clusterers, forgers) | $10.08 |
| act (45 haiku + 2 sonnet) | $14.10 |
| diff review | $15.49 |
| thread review (agent-review) | $8.71 |
| **total** | **$90.46** |

## Results

- **The mapper did not save money.** Opus tagging only the sonnet-pointed regions (31-50% of the
  lines) cost 0.94-1.12 times opus reading the whole unit. Per confirmed finding: $0.60 against
  $0.53; per accepted fix across the whole chain: $2.46 against $2.20. Opus cost does not scale
  with lines read. **The hypothesis that the mapped pipeline costs no more was falsified.**
- **The mapper did add coverage.** P-map found 40 of 51 distinct verified clusters, P-full 34; 17 were
  unique to P-map and 11 to P-full. The use of a cheap mapper on this evidence is a second,
  differently-aimed pass, not a cheaper one. Adding the other pipeline cost about $1.41 per extra
  cluster (P-map on top of P-full) to $1.65 (P-full on top of P-map).
- **Haiku acted well on well-specified tasks.** 43 of 45 passed the tagger's test with no
  regression; 35 of 45 after a sonnet reviewer read each diff against its report; both
  failures passed on one sonnet retry (escalation rate 4%). The committed predictions (55-75% pass,
  25-45% escalation) were too pessimistic.
- **Tests and the skeptic are both soft.** 34 of 45 tagger tests reproduced the defect on the base by
  assertion; the other 11 were broken or exceptions, so a pass there is weak. The skeptic confirmed 74
  of 77 real findings (96%) and refuted all four planted controls, but the controls shared one
  template, so that is a weak test of skepticism.
- **The final review caught what the mechanical stages passed.** 10 of 45 diffs were flagged although
  the tests passed (a parser swap that regresses the local dead-end text; a return type changed from
  int to dict with the only caller untouched). `agent-review` stamped 15 mistakes in eight haiku
  threads: reports of "all tests pass" that the thread contradicts, an overstated count of existing
  tests, and new tests never run against the unfixed code. 193 of 193 stamp anchors checked out.
- **Predicted answers matched, with a bias.** The human's answers matched the tagger's prediction on
  8 of 8 items, but the prediction was shown first and marked Recommended, which can anchor the
  pick. An unanchored arm was not run.

## Mistakes made on the way, now rules

- **A wrong interpreter** made 38 of 45 tests "broken" on the first pass; it looked like a result
  about the tests. `run_base_tests.py` now runs pass, fail and broken controls first and aborts
  if they misclassify.
- **A control that said CTRL.** The diff reviewers saw the label, so 8 of 8 "no" says nothing.
  Controls and real cases come from one template with neutral ids.
- **Freshness read from file-name hits.** Nine of the 17 files were cited as context in earlier
  results, 38 of 77 findings sit in them, and one replicates an earlier finding. Count the overlap per
  file and record it when freezing the units.

## What this does not show

- Four units, one run, one repository, one model family.
- Both taggers hit the 14-artifact cap, so recall is relative to the pool they produced.
- The skeptic and the clusterer were both sonnet; the skeptic's softness is untested beyond one
  control class, and the diff-review controls were not blind.
- Skeptic-confirmed findings on this code were mostly low impact (3 high of 74).
- Local models (the strix, via the ecodex harness) were not run; haiku is the only action tier
  measured. There is no sonnet or opus action arm beyond the two escalations.
- Not tested: in-work tagging against retroactive tagging, a cheap mapper on fresh code (haiku was
  only tested against findings a full read had already made), thinking-effort, multi-turn sessions.

## Running it

The skill carries the prompts, the schemas and four scripts. The orchestration (fan-out, the
pipeline between stages) is a short Workflow script per sweep; the stage prompts are templates
with `{UPPER_CASE}` placeholders, filled by plain string replacement.

```bash
S=empirica/plugins/claude-code-integration/skills/agent-pipeline/scripts

python3 $S/validate_pointers.py pointers.json --root REPO --files a.py b.py
python3 $S/check_tag_quotes.py tag.json REPO
python3 $S/run_base_tests.py tag.json REPO --python /path/to/venv/bin/python3 --out base_tests.json
python3 $S/run_action.py CLONE prompt.md --model claude-haiku-4-5-20251001 \
    --test-path tests/test_pipe_x.py --test-file hidden_test.py --scope pkg/mod.py --out out/
```

Keep these separate, because the permissions differ: Workflow agents (map, tag, verify, review) get
only Read, rg, fd and `sed -n`; the action agent runs headless through `run_action.py`, where an
allow-list and `--permission-mode dontAsk` mean nothing can prompt a human.
