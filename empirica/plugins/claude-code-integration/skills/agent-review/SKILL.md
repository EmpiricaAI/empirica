---
name: agent-review
description: Review a finished subagent or workflow thread after the fact. A fresh reviewer reads the whole numbered thread and stamps typed artifacts, graded claims, tasks and predicted-answer proposals, each anchored to a turn with a verbatim quote, and a script checks the anchors. Use after dispatching agents whose work you will act on, not while they run.
---

# Agent review: work first, then stamp

**The worker does the work and returns raw evidence. A separate reviewer reads the finished
thread and tags it.** Tagging while working splits the worker's attention; tagging a finished
thread puts all of the reviewer's attention on reading and typing. It also sees what only
hindsight shows: which approach was a dead end, which belief was later retracted, what a
result turned out to cause.

Opt-in. Use it when you will act on what agents found (a sweep, a multi-agent audit, a long
research thread). A one-line lookup does not need it.

Measured on five finished threads (2026-10-05): a reviewer stamped 101 artifacts, every anchor and
quote checked out mechanically, 34% needed hindsight, and the known defects were present in 12 of
13 cases. (The `SubagentStop` hook's keyword extractor found nothing on the same threads; that
describes the hook, it is not a baseline for the method.) Caveats are at the
end. Method and numbers: `docs/guides/AGENT_REVIEW.md`.

## Part 1: the work (nothing to add to the worker)

Dispatch the agent as usual. The one request worth making: *quote or run, never summarise*.
Every result it reports should carry the command it ran or the file lines it read, so a
reviewer has evidence to anchor to. An agent that only reports its conclusion gives the
reviewer nothing but testimony.

If it is a Workflow, give its agents only prompt-free tools (Read, rg, fd, sed -n). A prompt that
asks them to run python raises a permission prompt per call for the human.

## Part 2: the review

Files beside this skill: `scripts/build_trace.py`, `scripts/check_stamp.py`, `stamp.schema.json`,
`reviewer-prompt.md`.

1. **Find the transcript.** Subagents: `~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl`.
   Workflow agents: `.../subagents/workflows/wf_*/agent-*.jsonl`, labelled in the adjacent
   `.meta.json`.
2. **Build the numbered trace.**
   `python3 scripts/build_trace.py <transcript.jsonl> --out-dir <dir> --name <name>` writes
   `trace_<name>.txt` and `turns_<name>.json`. Tool calls and results are truncated (500 and 700
   characters); raise `--call-limit` / `--result-limit` if the evidence you need is cut off, because
   a reviewer can only quote what the trace shows.
3. **Dispatch a fresh reviewer** with `reviewer-prompt.md` (fill `{ABOUT}` and `{TRACE_PATH}`) and
   `stamp.schema.json` as the structured-output schema. Tools: **Read, rg, fd, sed -n only**. One
   reviewer per thread; run them in parallel for several threads.
4. **Check the stamp.** `python3 scripts/check_stamp.py stamp.json turns_<name>.json`. It verifies
   that every anchor names a real turn and every quote is a verbatim substring of that turn, and
   exits 1 if any fail. Reject a failing stamp: re-run the reviewer, do not repair quotes by hand.
5. **Log what survives.** Findings with grounding `ran` or `read` go in as findings; `retrieved` and
   `assumed` go in as assumptions; `unknown`, `decision`, `dead_end`, `mistake` keep their type.
   Use `log-artifacts` so the edges come with them, and put the thread name and anchor turn in
   each description so the provenance survives. Tasks become `goals-add-task` entries only if the
   work is still yours to do.
6. **Put the proposals to the human, after re-checking them.** A proposal is a question with a
   predicted answer and options. It was written when the thread ended; before asking, check it
   against current state (11 of 15 were already answered by the time they were read). Ask with
   `AskUserQuestion`, the prediction first and marked Recommended. Record the pick next to the
   prediction (`finding-log`): agreement rate over time is the reviewer's calibration.

## Choosing the reviewer

Tier matters for the reviewer, and not evenly. Measured on 8 finished threads, four tiers, one pass each, scored by the mechanical
quote check and by pooled adjudication (`scripts/score_tiers.py`):

| Reviewer | Quotes verbatim | Precision (supported and correctly typed) | Pooled recall | Est. cost per good artifact |
|---|---|---|---|---|
| haiku | 62% | 78% | 47% | 2 cents |
| sonnet | 100% | 96% | 63% | 3 cents |
| opus | 100% | 98% | 74% | 6 cents |
| fable | 100% | 98% | 83% | 14 cents |

- **Do not use the cheapest tier as the reviewer.** 38% of its quotes fail the verbatim check, so `check_stamp.py` rejects the stamp even
  though most of those claims were true; it is also weakest on mistakes and decisions.
- **Sonnet is the default first pass:** every anchor valid and near-top precision. It misses more of what the pool contains.
- **Spend on a smarter tier where the hard types are.** Mistakes, assumptions and dead ends are where opus and fable were clearly better. A
  two-pass shape (sonnet over everything, then a smarter pass restricted to those types) is the obvious test and is **not yet measured**:
  do not present it as a result.
- **To decide for a new kind of thread, measure it:** run two or more tiers over the same threads, pool their artifacts anonymised, have a
  strong adjudicator judge each (supported, type, cluster), and score with `score_tiers.py TIER_RESULT.json TRACES_DIR`. The verbatim check is the
  only judge-independent column; the adjudicator may favour its own family; pooled recall cannot see what no tier found.
- Thinking effort was not varied. Costs are list-price estimates from token counts, not billed amounts. n is 8 threads.

## What this does not prove

- **Anchor validity is not typing correctness.** The checker proves the quote exists where the
  reviewer says. It does not prove the artifact is typed right. Until a grader exists, estimate
  typing quality by cross-grading: a second reviewer re-stamps a sample blind and you read the
  disagreement rate (see the guide).
- **Self-report anchors are testimony.** `check_stamp.py` reports `self_report_share`: the share of
  artifacts anchored to the agent's own final message. On the measured threads that was about a
  third. Such an artifact says what the agent claims, not what it showed. Prefer evidence anchors;
  ask the reviewer to anchor to the tool result.
- **Proposals go stale, and the prediction nudges.** Shown first and marked Recommended, a
  prediction raises the chance it is picked. Four for four is a small sample.
- **A reviewer can still invent a claim that is true to the quote.** The quote proves the words
  exist. For anything you will act on, run the check the quote points at.

## Related

- `/dispatch-agent` puts what the practice already learned into a subagent's prompt (before the
  work). This skill reads the work afterwards.
- `/epistemic-transaction` for the artifact types and `log-artifacts` payload.
- `/reporting-discipline` for how to bring the proposals to the human.
