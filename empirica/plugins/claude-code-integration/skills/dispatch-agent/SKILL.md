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

## 1. Retrieve

```
mcp__cortex__investigate({ "query": "<task description>", "limit": 10 })
```

Cortex unavailable — local, both runnable as written:

```bash
empirica project-search --task "<description>" --global --output json
empirica project-search --task "<description>" --type assumptions --output json
```

`--global` widens past this project into shared learnings. There is no `--list`
on the `*-log` verbs: they WRITE, retrieval is semantic through `project-search`
or `investigate`.

## 2. Select

Include what would change the subagent's *behaviour*, and leave out what would
merely inform it — a prompt that recites everything known about a domain buries
the two lines that matter.

| Kind | Include when |
|---|---|
| **dead_end** | it names an approach for THIS task. Highest-value inheritance — the subagent cannot rediscover a dead end cheaply, it can only re-walk it. Prefer over-including here. |
| **finding** | the subagent would otherwise have to derive it, or would derive it wrong |
| **decision** | it constrains how this work must be done, not merely how something came to be |
| **mistake** | it happened in work of this shape. Reframe as the prohibition, not the story. |

Judge relevance by reading the artifact against the task. Similarity scores rank
candidates; they do not decide inclusion, and a fixed cutoff will drop the one
dead-end that matters while admitting four findings that don't.

## 3. Build the prompt

```markdown
## Inherited context

Your parent practice has already learned the following about this work.

### Do not repeat these — they were tried and failed
- **Approach:** {{approach}}
  **Why it failed:** {{why_failed}}

### Findings
- {{finding}}

### Decisions in effect
- **Choice:** {{choice}} — **because** {{rationale}}

### Prohibitions
- DO NOT {{pattern}}

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
