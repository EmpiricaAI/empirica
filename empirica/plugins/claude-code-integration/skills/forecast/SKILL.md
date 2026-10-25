---
name: forecast
description: "Use when deciding what to build next or preparing a release or quarter — 'what's next', 'what's coming', 'roadmap', 'plan the next release', 'turn this into goals', 'are we on track' — and when grading earlier predictions. Drafts predicted directions with an evidence kind and a capped confidence, turns thin evidence into at most three questions, routes human attention by impact and confidence, creates planned goals only after confirmation, and gardens the forecast so it stays honest."
version: 1.0.0
---

# Forecast

Load this when the question is "what comes next", or when a release is about to grade the last forecast.

A direction is a **prediction**: what will happen, by which release, how sure we are, what that rests on, and what would prove it wrong.
README's What's Coming renders `docs/ROADMAP.yaml`. The schema, the evidence kinds and their confidence caps are in that file's header and
are enforced by `scripts/release.py`; read it, do not restate it here.

## The pass

1. **Gather (noetic).** Open goals (`goals-list`), unknowns, open assumptions, recent mistakes, accepted mailbox items
   (`mailbox poll --status accepted`), calibration biases, the reproduced-defect backlog, and the last forecast in `docs/ROADMAP.yaml`.
   Count with a command. A figure you typed from memory is not evidence.
2. **Draft candidates.** Per candidate: one-sentence prediction with a `horizon` release, `evidence` kind, `confidence` (the kind caps it),
   a `falsifier` a stranger can observe, `impact` (low / medium / high), and `steered` (is a goal already open for it?).
3. **Sufficiency.** A candidate with no measured or reviewed basis is not a direction yet: it becomes a question. So does one whose
   falsifier you cannot write as the prediction's own sentence (if you cannot, there are two claims). Check each basis against HEAD:
   a report older than the code is not evidence (`git log -S<symbol> --since=<report date> -- <path>`).
4. **Ask at most three questions.** Rank by impact × (1 − confidence), the answer that would reorder the list most. Each is an
   `AskUserQuestion` with your predicted answer first, marked Recommended, and the one reason that grounds it. If you cannot predict the
   answer you have not read enough. Attention is the scarce resource: one question that changes the ranking beats five that do not.
5. **Route attention.**

   | impact | confidence | what happens |
   |---|---|---|
   | high | low | the human decides; spend a question here |
   | high | high | one-tap confirm of the ranked list |
   | low | high | create a `planned` goal and move on |
   | low | low | park as an `unknown`; do not forecast it |

6. **Emit, after the human confirms the ranked list.** `goals-create --status planned` with a real description and tasks per direction,
   a falsifier registered for each (`falsifiers` at PREFLIGHT or CHECK, with a `query`), and the public subset added to
   `docs/ROADMAP.yaml`. The roadmap is public text: a human reads every line before it ships, and it carries no ids.

**Steered.** A goal that exists, or that you create in this pass because of the prediction, makes the item `steered: true`. Only an item
predicted *before* any goal and shipped anyway is unplanned foresight. Do not create the goal first and mark the item unsteered.

## Gardening (every release, and the day something trips)

The principle that keeps epistemic tagging honest, applied to predictions. `release.py --docs` does the mechanical part.

- **Past its horizon.** Every predicted item whose release has been cut gets adjudicated: `shipped` (set `resolved_in`), `dropped` (a fair
  bet that missed), **`retracted`** (wrong when written because its basis was false; say what), `superseded` (name the replacement), or
  move the horizon with a `revisions` entry that says why. Leaving it open is allowed once, with a reason, not twice.
- **When a falsifier trips or a basis turns out false**, resolve it that day. Do not wait for the release.
- **Numbers move only through `revisions`** (date, from, to, why). After a release the docs step refuses a silent edit and a deleted item.
- **Read the record honestly.** Shipping planned work is delivery, not foresight. A record with no retractions has not been checked hard
  enough. Under ten resolved *unplanned* items the hit rate is noise: report the counts, not a percentage.

## Rules earned

- Confidence is capped by the weakest load-bearing part of the basis. A peer's report is `reported` (≤ 0.70), however confident it sounded.
- A claim about current code cites HEAD, not a report. One 1.14.14 item described a gap fixed three weeks earlier.
- Two items that count the same backlog are one item.
- A falsifier that tests a neighbouring quantity (absolute size for a claim about growth) passes while the prediction fails.
- Never write ids into public text; the release script refuses them.

## Report

Done / Next / Waiting. List the open predictions with confidence and evidence kind, the questions you asked with the answers, the goals
created (`planned`, with ids), and what the gardening pass resolved. Say plainly which items are steered.
