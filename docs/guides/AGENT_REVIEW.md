# Reviewing agent threads after the fact

When an agent finishes, what it learned is in its transcript and nowhere else. The `SubagentStop`
hook rolls findings up by matching sentence prefixes (`Found:`, `Unknown:`) in the assistant text,
capped at five per type, and never fills dead ends. An agent that does not write those prefixes
contributes nothing to the graph; on five finished threads from the 2026-10-05 sweep it extracted
nothing. That describes the hook. It is not evidence for the method: keyword matching cannot assess
epistemic state, and the claim below is about AI reading against AI reading.

The `agent-review` skill replaces that guess with a second reading. The worker works. Then a
fresh reviewer reads the **whole finished thread** and stamps it with typed artifacts, graded
claims, tasks and proposals, each anchored to a turn and a verbatim quote, and a script checks
the anchors. This guide is the method, a worked example, and what has and has not been shown.

Skill: [`agent-review`](../../empirica/plugins/claude-code-integration/skills/agent-review/SKILL.md).
Opt-in per dispatch.

## Why two parts

Tagging while working splits the worker's attention between the task and the bookkeeping. Tagging
a finished thread puts all of one reader's attention on reading and typing, and it can use what
only hindsight gives:

- a **dead end** is an approach abandoned for another that worked, which is only visible once the
  next approach works;
- a **retraction** is a belief a later turn contradicts;
- an **edge** (`caused_by`, `invalidates`, `resolves`) points at events that had not happened yet
  when the first artifact would have been written;
- the reviewer is **not the worker**, so it grades the worker's claims instead of repeating them.

## The method

**Part 1, the work.** Dispatch the agent normally. Ask it to quote or run, never summarise, so
that its results carry commands and file lines a reviewer can anchor to.

**Part 2, the review.**

1. Build a numbered trace from the transcript:
   `python3 scripts/build_trace.py <transcript.jsonl> --out-dir <dir> --name <name>`.
   Each turn is `[T<n>] <role>: <text>` with role `assistant`, `tool_call` or `tool_result`.
2. A fresh reviewer reads the trace with Read, rg, fd and sed -n only and returns a stamp that
   validates against `stamp.schema.json`.
3. `python3 scripts/check_stamp.py stamp.json turns_<name>.json` checks that every anchor names a
   real turn and every quote is a verbatim substring of it. A forged or misplaced quote fails, and
   the exit code is 1.
4. What survives is logged with `log-artifacts` (so edges travel with it), and the proposals are put
   to the human.

### The stamp

| Field | What it holds |
|---|---|
| `artifacts` (max 25) | `type` finding, unknown, assumption, decision, dead_end or mistake; `text`; `anchor` (turn); `quote` (verbatim, under 200 characters); `grounding` ran, read, retrieved or assumed (how the **agent** knew); `needs_hindsight` |
| `edges` | `from`/`to` artifact indexes and a relation that says something: caused_by, evidence, invalidates, resolves, grounded_by, raised_by |
| `tasks` | what the agent was asked to do, `done`, `open` or `abandoned`, with the turn that shows it |
| `proposals` (max 3) | an objective, a question, a **predicted answer** with the one reason that grounds it, and 2 to 4 options with consequences |

Typing follows the question each answers. A bug the agent found in the code is a finding; the agent
shipping that bug would be a mistake. Something it acted on without checking is an assumption.

### Putting proposals to the human

A proposal is a decision already made that the human can veto, not an open question. Re-check each
against current state first: it was written when the thread ended. Ask with `AskUserQuestion`, the
prediction first and marked Recommended, and record the pick next to the prediction. The agreement
rate over time is the reviewer's calibration.

## Worked example: the sweep threads

Five finished threads: three agents that rewrote architecture and reference docs against the code,
one blind code reviewer reading half of a 5,000-line hook file, one reading a sync module. Traces of
48 to 251 turns.

```
python3 scripts/build_trace.py <transcript> --out-dir review/ --name docs-cluster-D
# five reviewers in parallel, each given reviewer-prompt.md and the schema
python3 scripts/check_stamp.py stamp_D.json review/turns_docs-cluster-D.json
```

On the cluster D thread the stamp had 24 artifacts and all 24 anchors checked. They included the
defects that agent had found (the shipped Sentinel profile YAMLs nest keys the loader reads at the
top level; the sync status counted refs no push carries), two dead ends visible only in hindsight
(a grep that returned nothing, an 824 KB command it had to abandon), and a mistake (a doc written
before checking where the insights surface, then patched).

### Using it in a Workflow

A Workflow script can run Part 2 over many threads: build the traces first (a Bash step, not an
agent), then one agent per trace with the schema, then the checker over each result. The agents
need only Read, rg, fd and sed -n. In a sweep run, a prompt that asked the agents to run python
raised eleven permission prompts for the human, who declined many; a workflow's rules should be
written from the tools its agents actually have.

## What was measured, and what it does not show

Five threads, one reviewer each, 589k tokens in total. This is not a comparison against the hook's keyword extractor: matching sentence prefixes is not an epistemic assessment and is not a baseline anyone should beat. The comparison the method needs is AI against AI (tagging while working against stamping afterwards, and one reviewer against a second), and it has not been run. Every figure below is recomputed by one script from the archived inputs (`reproduce_numbers.py` in the experiment archive, which also runs the baseline's positive control).

| Measure | Result | Read it as |
|---|---|---|
| Artifacts the reviewer stamped | 101 | |
| Anchors valid (turn exists, quote verbatim) | 101 of 101 | the evidence exists where the reviewer says |
| Edges well-formed (both ends name a real artifact) | 35 of 36 | one edge pointed at an artifact index that does not exist, so the stamp as a whole did not pass |
| Needed hindsight | 34% (34 of 101) | what tagging during the work cannot produce |
| Known defects present in the stamps | 12 of 13 | the miss was in a trace that truncates tool calls at 500 characters |
| Predicted answers the human picked | 4 of 4 | small sample, and the prediction was shown first as Recommended, which itself nudges a pick |

What it does **not** show:

- **Typing correctness.** Anchors prove a quote exists; nothing yet grades whether each artifact is
  the right type. Cross-grading below is the planned estimate.
- **Independent grounding.** About a third of the artifacts anchor to the agent's own final message.
  Their `ran` or `read` is the agent's testimony. `check_stamp.py` reports `self_report_share` so
  this stays visible.
- **That proposals stay valid.** 11 of the 15 proposals the reviewers attached had already been
  answered by the time they were read.

### Cross-grading

The owning practice runs Part 2 on its own threads, because the reviewer needs that practice's repo
and graph to type artifacts and to check proposals against current state. A second practice
re-stamps a **sample** of the same threads blind. The share of artifacts the two stamps disagree on
(different type, or one has an artifact the other lacks) is the typing-correctness signal, and the
only one that does not reuse the first reviewer's own judgement. Cortex is the first to run it, on
four of its own subagent threads, with core re-stamping a sample.

## Where the artifacts go

Findings with `ran` or `read` grounding are logged as findings; `retrieved` and `assumed` as
assumptions; the other types keep theirs. Put the thread name and the anchor turn in each
description. The reviewer's stamp is testimony about someone else's work: the quote proves the words
exist, and for anything you will act on you still run the check the quote points at.

## Related

- [`dispatch-agent`](../../empirica/plugins/claude-code-integration/skills/dispatch-agent/SKILL.md): puts what the practice learned into an agent's prompt before the work; this guide reads the work afterwards.
- [Epistemic transactions](../../empirica/plugins/claude-code-integration/skills/epistemic-transaction/SKILL.md): the artifact types and the `log-artifacts` payload.
