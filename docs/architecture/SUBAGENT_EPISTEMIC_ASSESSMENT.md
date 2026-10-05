# Subagent Epistemic Assessment

How Empirica governs subagents spawned through Claude Code's Task/Agent tool:
lineage, delegated work counting, a findings budget, and a quality gate on what
flows back to the parent. It runs entirely from two hooks and a few core
modules. It does **not** measure a subagent's own calibration; see
[Not implemented](#not-implemented).

---

## What a subagent is, to Empirica

A subagent is not a practitioner. Its epistemic state does not persist into the
practice, it runs no PREFLIGHT of its own that anyone reads, and its tool calls
bypass the parent's Sentinel gates. The parent's CHECK authorized the spawn, and
that is the only gate. What Empirica adds around it:

| Concern | Mechanism | Where |
|---|---|---|
| Lineage | child row in `subagent_sessions` linked by `parent_session_id` | `subagent-start.py`, `SessionDatabase` |
| Identity of the subagent's CLI calls | per-subagent `active_work_<claude_session_id>.json` with `is_subagent: true` | `subagent-start.py` |
| Gate exemption | `_detect_subagent` lets subagents through the praxic gate | `sentinel-gate.py` |
| Delegated work counting | subagent tool calls added to the parent's transaction counters | `subagent-stop.py` |
| Findings budget and spawn regulation | `attention_budgets`, `should_spawn_more` | `subagent-start.py`, `subagent-stop.py`, `core/attention_budget.py`, `core/information_gain.py` |
| Quality gate on rollup | `EpistemicRollupGate`, decisions logged to `rollup_logs` | `core/epistemic_rollup.py` |

Both hooks are registered in `templates/settings-hooks.json` under
`SubagentStart` (timeout 10) and `SubagentStop` (timeout 15), each with
`allowFailure: true`. They fail open: a hook error means the agent proceeds
untracked.

---

## Lifecycle

```
Parent spawns a subagent
        |
        v
SubagentStart  (subagent-start.py)
  1. find the parent Empirica session (latest active session for this practice)
  2. create_subagent_session(agent_name, parent_session_id)  -> child_session_id
  3. write .empirica/subagent_sessions/<agent>_<timestamp>.json (status active,
     child id, parent id, budget block)
  4. get the attention budget allocation for this agent, or create a default one
  5. write ~/.empirica/active_work_<subagent claude_session_id>.json
  6. warn if the budget is already exhausted
        |
        v   agent runs; its empirica CLI calls resolve to child_session_id
        |
        v
SubagentStop  (subagent-stop.py)
  1. find the most recent ACTIVE session file for this agent_name
  2. count tool_use blocks in the agent transcript
  3. add that count to the parent's open transaction counters
  4. extract findings/unknowns from the transcript text
  5. roll up through the gate; log accepted ones to the parent session
  6. end_subagent_session(child_session_id); delete the active_work file
  7. mark the session file completed; report budget and regulation
```

### Storage

Subagent rows live in `subagent_sessions` (migration 034), separate from
`sessions`. Before 1.8.14 every spawn wrote to `sessions`, and because children
are always newer than their parent, "recent sessions" lookups returned only
subagents and hid the real parent. Columns (`empirica/data/schema/sessions_schema.py`):
`session_id`, `agent_name`, `parent_session_id`, `project_id`, `instance_id`,
`start_time`, `end_time`, `status` (default `active`), `rollup_summary`,
`created_at`. `SessionDatabase` methods: `create_subagent_session`,
`end_subagent_session`, `get_subagent_session`, `list_subagents_for_parent`
(optional `status`). Signatures:
[core_session_management.md](../reference/api/core_session_management.md#subagent-sessions-v1814).

The child session is lineage, not artifact storage: accepted findings are logged
to the **parent** session.

### Identity of the subagent's CLI calls

Each Task spawn gets its own `claude_session_id` from Claude Code, and the hooks
receive it. Without a per-subagent `active_work` file, a subagent's `empirica`
commands fell through to TTY-based resolution, which is shared with the parent,
and tagged the parent's session (the contamination described in the comment on
`_write_subagent_active_work`). SubagentStart now writes the file with
`is_subagent: true` and `empirica_session_id` set to the child session;
SubagentStop deletes it.

`_detect_subagent(claude_session_id)` in `sentinel-gate.py` reads that flag (file
present with `is_subagent: true` means subagent; present without it means
parent). If the file is absent it falls back to absence detection, with an extra
signal for subagents running in a linked git worktree. A detected subagent is
allowed through the praxic gate (`_check_exemptions`, rule 3a), and the tool-call
counter increments only for sessions that have an `active_work` file, so subagent
calls are never double counted.

### Delegated work counting

`count_transcript_tool_calls` counts assistant `tool_use` blocks in the subagent
transcript. `add_delegated_work_to_parent` adds that to both `tool_call_count` and
`delegated_tool_calls` in the parent transaction's hook-counters file, and only if
the transaction is open (it writes the counters file, not the transaction file, to
avoid racing POSTFLIGHT). The reason is the autonomy nudges in the Sentinel: with
no counting, delegating would make a transaction look shorter than the work done.
The hook message says whether the count was added.

---

## The findings budget

### Allocation

`empirica/core/attention_budget.py` allocates a findings budget across domains
using information gain (higher uncertainty and lower knowledge give more;
prior findings give diminishing returns). Two ways a budget gets created:

- Planned: `empirica memory-prime --session-id S --domains '["a","b"]'
  [--budget N] [--know K] [--uncertainty U] [--prior-findings JSON]
  [--dead-ends JSON] [--persist]`. Without `--persist` it only prints.
- Spontaneous: if no budget exists for the parent session, SubagentStart creates
  one: total 20, strategy `spontaneous`, a single `general` domain.

A subagent's domain is the part of its agent name after the colon
(`empirica:security` gives `security`), `general` otherwise. If the planned budget
has no allocation for that domain, the defaults are budget 5, priority 0.5,
expected gain 0.5.

### Rollup gate

SubagentStop only extracts what the agent wrote in a recognisable form. Per
assistant message it takes the first sentence containing one of:

- findings: `Found:`, `Discovered:`, `Key insight:`, `Result:`
- unknowns: `Unknown:`, `Unclear:`, `Need to investigate:`, `TODO:`

Caps are 5 findings, 5 unknowns and 3 dead ends. The dead-end list is never
filled; no extraction pattern exists for it. A subagent that does not write in
those forms rolls up nothing.

Findings go through `EpistemicRollupGate` (`min_score` 0.3, Jaccard dedup at 0.7):

```
score = confidence * novelty * domain_relevance
```

`novelty` is measured against the parent session's last 50 findings and earlier
findings in the batch. In the hook path `domain_relevance` is 1.0 and
`confidence` is the budget allocation's `priority` (0.7 when the stored budget
block has none), not anything the agent reported. Below `min_score` is rejected;
accepted findings consume budget, highest score first, until it is exhausted.
Accepted findings are logged to the parent as `[<agent_name>] <text>` with
`impact = min(1.0, score)`; every decision, accepted or not, is written to
`rollup_logs`. Unknowns pass through ungated. If the gate cannot be imported the
hook falls back to logging findings at impact 0.5.

The same gate is available after the fact:
`empirica session-rollup --parent-session-id P [--budget N] [--min-score F]
[--jaccard-threshold F] [--semantic-dedup] [--log-decisions]`, which aggregates
findings of child sessions with `impact` standing in for confidence.

### Regulation

After each rollup `_check_regulation` calls
`should_spawn_more(budget_remaining, gain_estimate=0.5, rounds_without_novel)`
(`core/information_gain.py`), which stops on: no budget, `rounds_without_novel`
at or above 2, or gain below 0.1. As called from the hook, `gain_estimate` is the
constant 0.5 and `rounds_without_novel` is 0 or 1, so in practice **only an
exhausted budget stops it**. When it does, the hook message ends with
`REGULATION: DO NOT spawn more agents`. This is advisory text to the parent;
nothing blocks the next spawn.

---

## Gaps and caveats

- `find_subagent_session` picks the newest active session file for an agent *name*.
  Two concurrent subagents with the same name can be matched to each other's file.
- The session files live under `Path.cwd()/.empirica/subagent_sessions/`, so the
  hooks depend on the working directory being the project.
- The rollup `confidence` is a budget-derived constant, so the score ranks by
  novelty, and a verbose subagent's findings are treated like a careful one's.
- Subagent output is an uncalibrated self-report. Trust its artifacts (diffs,
  test output you can re-run), not its verdict, and re-run the gates yourself.
  Enrich what it knows at spawn time with `/dispatch-agent`; the practice's
  prior findings and dead ends do not reach a fresh subagent otherwise.

---

## Not implemented

An earlier revision of this document specified a design that was never built.
Searching `empirica/` finds no code for any of it:

- decomposing the subagent prompt into estimated vectors and an archetype
  (researcher, explorer, implementer, auditor, analyst);
- an imprint (breadth, depth, confidence floor, scope lists) injected as a prompt
  preamble;
- calibration points pairing estimated against parent-assessed vectors, with a
  per-archetype Brier score and bias discount;
- trust tiers by Brier score ("earned autonomy for subagents").

Per-practitioner calibration (`empirica calibration-report`) is described in
[SELF_MONITORING.md](SELF_MONITORING.md); nothing applies it to subagents.
