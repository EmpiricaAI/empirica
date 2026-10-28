---
name: forecast
description: "Use when deciding what to build next, asking where a practice stands, or checking how good its predictions are — 'what's next', 'what's coming', 'roadmap', 'plan the next release', 'turn this into goals', 'are we on track', 'where are we', 'status of the practice', 'what have we done this session', 'is the forecast still true', 'how good are our predictions' — and when grading earlier predictions. Drafts predicted directions with an evidence kind and a capped confidence, turns thin evidence into at most three questions, routes human attention by impact and confidence, creates planned goals only after confirmation, and gardens the forecast so it stays honest. Modes: status, verify, grade, record, away."
version: 1.1.0
---

# Forecast

Load this when the question is "what comes next", "where are we", or "were we right", or when a release is about to grade the last forecast.

A direction is a **prediction**: what will happen, by which release, how sure we are, what that rests on, and what would prove it wrong.
In empirica core, README's What's Coming renders `docs/ROADMAP.yaml`: the schema, the evidence kinds and their confidence caps are in that
file's header and `scripts/release.py` enforces them. Read it, do not restate it here. A project without that file keeps its forecast in
its goals; every mode below applies unchanged, and creating a public roadmap is a question for the human, not a default.

## Modes

The skill takes arguments (`/forecast status`) or reads the user's words. One word picks the mode; none is the **full pass**. A modifier
`since <tag, commit or date>` moves the window every mode reads from (default: this session's first PREFLIGHT for `status`, the last tag
for the rest).

| Mode | Question it answers | Writes? |
|---|---|---|
| *(none)* | What should we build next? | goals and roadmap, after the human confirms |
| `status` | Where is this practice, and what did this session do? | nothing |
| `verify` | Is each open prediction still true at HEAD? | nothing |
| `grade` | Which predictions are past their horizon, and what became of them? | roadmap revisions and resolutions |
| `record` | How good has the forecasting been? | nothing |
| `away` | The human is not here: do what the routing table allows | planned goals and parked unknowns only |

Every figure in every mode comes from a command run in that pass. A number typed from memory is not evidence, and a mode that cannot
run its command says so instead of estimating.

## The pass (no mode)

1. **Gather (noetic).** Open goals (`goals-list`), unknowns, open assumptions, recent mistakes, accepted mailbox items
   (`mailbox poll --status accepted`), calibration biases, the reproduced-defect backlog, and the last forecast in `docs/ROADMAP.yaml`.
   Count with a command.
2. **Draft candidates.** Per candidate: one-sentence prediction with a `horizon` release, `evidence` kind, `confidence` (the kind caps it),
   a `falsifier` a stranger can observe, `impact` (low / medium / high), and `steered` (is a goal already open for it?).
3. **Sufficiency.** A candidate with no measured or reviewed basis is not a direction yet: it becomes a question. So does one whose
   falsifier you cannot write as the prediction's own sentence (if you cannot, there are two claims). Check each basis against HEAD:
   a report older than the code is not evidence (`git log -S<symbol> --since=<report date> -- <path>`).
4. **Ask at most three questions.** Rank by impact × (1 − confidence), the answer that would reorder the list most. Each is an
   `AskUserQuestion` (or your harness's ask-the-user tool) with your predicted answer first, marked Recommended, and the one reason that grounds it. If you cannot predict the
   answer you have not read enough. Attention is the scarce resource: one question that changes the ranking beats five that do not.
5. **Route attention.**

   | impact | confidence | what happens |
   |---|---|---|
   | high | low | the human decides; spend a question here |
   | high | high | one-tap confirm of the ranked list |
   | low | high | create a `planned` goal and move on |
   | low | low | park as an `unknown`; do not forecast it |

6. **Emit, after the human confirms the ranked list.** `goals-create --status planned` with a real description and tasks per direction,
   a falsifier registered for each (`falsifiers` at PREFLIGHT or CHECK, with a `query`), and, where the project has a
   `docs/ROADMAP.yaml`, the public subset added to it. A roadmap is public text: a human reads every line before it ships, and it carries
   no ids.

**Steered.** A goal that exists, or that you create in this pass because of the prediction, makes the item `steered: true`. Only an item
predicted *before* any goal and shipped anyway is unplanned foresight. Do not create the goal first and mark the item unsteered.

## `status`: where are we

Read-only, one screen. No questions, no goals, no files. Three blocks, each from commands:

- **This session.** Commits since the window start (`git log --oneline --since=<start>`), what the artifacts logged in that window say
  (`empirica commit-context --since <start>`), goals created or tasks closed (`goals-list`, then `goals-progress` on the ones that moved),
  open falsifiers (`falsifier-list`), notes not yet triaged (`note --list`).
- **Related work.** Open goals ranked by how many tasks are left, mail waiting on this practice (`mailbox poll --status accepted`), the
  open unknowns and assumptions that touch the same goals, and anything parked as load-bearing.
- **Directions.** The open predictions already in `docs/ROADMAP.yaml` and the `planned` goals, ranked by impact × confidence, each with its
  evidence kind and whether it is steered. Do not draft new ones here: a new direction is the full pass.

Report as Done (this session) / Next (ranked, each citing a goal id; a Next with no goal id is unsteered work: say so) / Waiting.

## `verify`: is the forecast still true

For every open prediction: run its falsifier `query` if it has one and say what it returned; re-check its basis against HEAD
(`git log -S<symbol> --since=<basis date>`); state `tripped`, `basis moved` or `held`. **A `held` needs the count of what the query or
search covered**, because a falsifier that looked at nothing also reports clean. A prediction with no runnable falsifier is itself a
finding. Read-only: list the gardening moves that follow (below) and leave the human to take them, except a tripped falsifier, which
is resolved that day.

## `grade`: what became of the predictions

The gardening section below as its own entry, for the day a release is cut or a horizon passes. Start from what the mechanical step reports
(`release.py --docs` in empirica core) and adjudicate each past-horizon item with evidence. `retracted` and `dropped` are a human's
call to confirm; `shipped` with a commit or a changelog line can be applied.

## `record`: how good has the forecasting been

Read-only. Two ledgers, kept apart:

- **Predictions.** Counts of roadmap items by status and by `steered`
  (`yq '.items[] | .status + " steered=" + (.steered | tostring)' docs/ROADMAP.yaml | sort | uniq -c`). Shipping planned work is
  delivery, not foresight. Report counts, not a percentage, while fewer than ten *unplanned* items are resolved.
- **Beliefs.** Whether stated confidence tracks outcome, for the artifacts that carry a confidence and later resolve. For assumptions:
  `sqlite3 .empirica/sessions/sessions.db "select case when confidence>=0.8 then '0.8+' when confidence>=0.6 then '0.6-0.8' else '<0.6' end, status, count(*) from assumptions where status in ('verified','falsified') group by 1,2"`.
  Only resolved ones are in it, and someone chose to check them: say so beside the numbers. Mistake recurrence has no query yet (an
  open goal); do not invent one.

Name the surprise, if there is one: a bucket whose outcome rate does not follow its stated confidence is the finding.

## `away`: the human is not here

Run the full pass with the routing table as the only authority. Low impact and high confidence becomes a `planned` goal; low and low becomes
an `unknown`. **Every high-impact item stays undecided**: write it under Waiting as a question with your predicted answer and its one reason,
and go on to the work the table allows. Emit nothing public, so `docs/ROADMAP.yaml` is not touched. Say at the top that no human saw this
ranking.

## Gardening (every release, and the day something trips)

The principle that keeps epistemic tagging honest, applied to predictions. In empirica core `release.py --docs` does the mechanical part;
elsewhere make the same moves by hand at each release.

- **Past its horizon.** Every predicted item whose release has been cut gets adjudicated: `shipped` (set `resolved_in`), `dropped` (a fair
  bet that missed), **`retracted`** (wrong when written because its basis was false; say what), `superseded` (name the replacement), or
  move the horizon with a `revisions` entry that says why. Leaving it open is allowed once, with a reason, not twice.
- **When a falsifier trips or a basis turns out false**, resolve it that day. Do not wait for the release.
- **Numbers move only through `revisions`** (date, from, to, why). After a release the docs step refuses a silent edit and a deleted item.
- **Read the record honestly.** A record with no retractions has not been checked hard enough.

## Rules earned

- Confidence is capped by the weakest load-bearing part of the basis. A peer's report is `reported` (≤ 0.70), however confident it sounded.
- A claim about current code cites HEAD, not a report. One 1.14.14 item described a gap fixed three weeks earlier.
- Two items that count the same backlog are one item.
- A falsifier that tests a neighbouring quantity (absolute size for a claim about growth) passes while the prediction fails.
- Never write ids into public text; the release script refuses them.

## Report

Done / Next / Waiting. In the full pass, list the open predictions with confidence and evidence kind, the questions you asked with the
answers, the goals created (`planned`, with ids), and what the gardening pass resolved; say plainly which items are steered. In `status`,
`verify` and `record`, say what each command covered, so a clean result can be told from an empty one.
