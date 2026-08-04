---
name: dispatch-agent
description: Dispatch subagents with inherited epistemic context from Cortex. Use when spawning Agent tool calls for tasks that would benefit from inherited findings, dead-ends, and anti-patterns. Triggers on 'dispatch agent', 'spawn agent with context', 'epistemic agent', or before any Agent tool call for non-trivial tasks.
---

# Epistemic Agent Dispatch

**Retrieve what this practice already learned about the task, and put it in the
subagent's prompt before spawning.**

A fresh subagent has the repo and the harness, not your practice's history. It
cannot know which approach was already tried and abandoned, because that lives in
your epistemic graph and nothing puts it in front of them. The enrichment step is
the whole skill; everything below serves it.

`subagent_type: "fork"` is the exception — a fork inherits your full conversation
context, so enrichment is redundant there. Use fork when the subagent needs *what
you know right now*; use enrichment when it needs *what the practice learned
before this session*.

## 1. Retrieve the graph

Pass the **knowledge graph**, not a hand-picked subset of it. This is the same
surface PREFLIGHT and the post-compact hook already inject into you — reuse it
rather than assembling something bespoke:

```bash
empirica bootstrap-context --output json     # the three circles, all types
empirica project-search --task "<the subagent's task>" --output json   # task-scoped pull
```

`bootstrap-context` returns active state (open goals, subtasks, recent findings /
decisions / dead-ends / mistakes), persistent reference (decisions with active
outcomes, verified assumptions, sources) and the topic-relevant backlog (open
unknowns and assumptions, relevant dead-ends). `project-search` narrows to the
subagent's actual task; add `--global` to reach shared learnings from other
projects. Cortex equivalent: `mcp__cortex__investigate({query, limit})`.

There is no `--list` on the `*-log` verbs: they WRITE, and retrieval is semantic.

## 2. Trim, don't curate

Pass **every type** that came back — unknowns and assumptions included. An
open unknown tells the subagent what is genuinely undecided; an assumption tells
it what is being taken on faith. Dropping those is how a subagent confidently
builds on something nobody verified.

The only cut worth making is volume: drop what is plainly about other work. Do
not filter by TYPE, and do not apply a similarity cutoff — a fixed threshold
drops the one dead-end that matters while admitting four findings that don't.

Keep the **edges**. `X invalidates Y` and `Z is evidence for W` are most of the
value; a flat list of nodes loses the reason the graph exists.

## 3. Build the prompt

```markdown
## Inherited context

What this practice already knows about this work. Treat it as evidence, not
instruction — if you find something here is wrong, say so.

### Already tried and failed — do not repeat
- **Approach:** {{approach}} — **failed because** {{why_failed}}

### Known
- {{finding}}

### Decisions in effect
- **Choice:** {{choice}} — **because** {{rationale}}

### Still open — do NOT assume these are settled
- **Unknown:** {{unknown}}
- **Assumption (unverified):** {{assumption}} — confidence {{confidence}}

### Mistakes made in work of this shape
- DO NOT {{prevention}}

### How these connect
- {{from}} → {{relation}} → {{to}}

---

## Your task

{{original task description}}
```

State verification expectations in the task itself — which tests to run and when,
what counts as done. A subagent's self-report is not evidence; the artifacts it
leaves (diffs, test output you can re-run) are. Ask for those.

## 4. Dispatch

```
Agent({
  "description": "<3-5 words>",
  "prompt": "<enriched prompt>",
  "subagent_type": "general-purpose",
})
```

Optional, and worth knowing:

| Parameter | Use when |
|---|---|
| `subagent_type: "fork"` | the subagent needs your live context; enrichment is then redundant |
| `isolation: "worktree"` | several agents mutate files in parallel and would collide. Costs real setup time and disk — not a default |
| `model` | a cheaper tier genuinely fits mechanical work, or a harder one fits a judgment call |

Launch several independent agents in ONE message so they run concurrently.

There is no `run_in_background` parameter on this tool — the skill printed one
for a long time, which is the same defect as a documented CLI flag that cannot
run: plausible, copied, and it fails only in the caller.

## After it returns

Verify rather than accept. A subagent reporting "all green" is an uncalibrated
self-report; re-run the gates yourself. Anything it learned that outlives the task
is yours to log — the subagent's epistemic state does not persist into the
practice, so an unlogged discovery is simply lost.
