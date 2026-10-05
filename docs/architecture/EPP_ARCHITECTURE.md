# Epistemic Persistence Protocol (EPP) Architecture

**Purpose:** keep a well-grounded position from being dropped under pushback that
carries no new evidence, while still updating when the pushback does.

The protocol itself (ANCHOR, CLASSIFY, DECIDE, RESPOND) is the
`/epistemic-persistence-protocol` skill. This document covers how that protocol
is triggered and measured, and why the trigger is shaped the way it is.

---

## The problem

Under pushback, a model's default pattern is to acknowledge displeasure,
apologise, drop the prior position, and offer to do whatever the user wants. The
user loses a collaborator who holds ground under non-evidential pressure. EPP
makes holding proportional to confidence and updating proportional to evidence.
Both failure directions count: resisting every challenge is the same defect with
the sign flipped.

---

## Two parts

```
UserPromptSubmit hook (tool-router.py)
   |  prompt >= 20 chars, not a slash command
   |  appends <epp-check> pointer to the injected context (last part)
   v
Claude's generation step
   |  reads the pointer, decides itself whether the message is pushback
   |  if yes: runs the skill (anchor -> classify -> decide -> respond)
   v
empirica epp-activate --category C --action A      (optional self-report)
   -> ~/.empirica/hook_counters{suffix}.json
```

### 1. The hook pointer

`build_semantic_pushback_check()` in
`empirica/plugins/claude-code-integration/hooks/tool-router.py` returns the
constant `SEMANTIC_PUSHBACK_POINTER`, a single `<epp-check>` line. It is returned
when `len(prompt) >= SEMANTIC_CHECK_MIN_LENGTH` (20) and the prompt does not start
with `/`; otherwise nothing is injected.

The pointer names the trigger (contradiction, doubt, reframe, scope shift, or a
request to justify), the four steps, and the skill to load. It does not carry the
protocol. An earlier version injected a roughly 21-line block on every
substantive prompt; it was cut to the one-liner on 2026-07-05 because of the
per-prompt token cost on every surface (the change is recorded in the comment
above `SEMANTIC_PUSHBACK_POINTER`).

The hook builds its context parts in a fixed order: epistemic routing, the
`<aap-hedge-detected>` block (only when AAP is enabled and hedges are found),
the investigation-proportionality block or pointer, the prompt-relevance block,
then the EPP pointer last. It is last on purpose, for attention recency on
pushback handling.

### 2. Self-reported telemetry

`empirica epp-activate` (handler `empirica/cli/command_handlers/epp_commands.py`)
increments `epp_activations` and appends to `epp_activations_log` in
`~/.empirica/hook_counters{suffix}.json`, where the suffix comes from the
instance resolver. Entries are `{timestamp, category, action, session_id}`; the log
keeps the most recent 50 (`MAX_LOG_ENTRIES`). Flags, both closed enums, checked
against `empirica epp-activate --help`:

- `--category`: `emotional`, `rhetorical`, `evidential`, `logical`, `contextual`
- `--action`: `hold`, `soften`, `update`, `reframe`
- `--session-id` optional, auto-derived; `--output {human,json}`

This is weak signal, because the model reports on itself. It shows whether the
pointer is leading to protocol runs, nothing more. Nothing measures sycophancy
across real conversations.

---

## Why a semantic check and not a regex

Pushback is a speech act defined by intent, not wording. A pattern list misses
"that doesn't quite track with what I saw yesterday" and "help me understand why
you think X", and fires falsely on "no problem, let me check" or "actually, I have
a different question". An LLM is already in the loop and reads these natively, so
the hook only reminds; the detection stays in generation. The cost is that the hook
cannot log "pushback detected": only the model's own `epp-activate` call
records an activation.

The same reasoning rules out a keyword gate on the pointer. The pointer fires on
every substantive prompt, and the model decides whether it applies.

## Why no stored position anchors

EPP does not write the prior claim to a file. The claim is in the conversation
history the model already holds; extracting "claims worth holding" into a file
would need heuristics or a second model call, and either adds failure modes. The
fallback considered was a Stop hook writing a position-anchors file. It is not
implemented.

---

## Phase 0 experiment (2026-04-07)

The original always-on block was gated on an experiment: 6 scenarios (5 pushback
categories plus one edge clarification case), control against injection, three
generation models (Opus 4.6, Sonnet 4.6, Haiku 4.5), a fixed scorer (Opus 4.6),
through the `claude -p` CLI. The decision gate was at least 20% relative
improvement on at least 2 of 6 metrics (classified, anchored, basis cited, audit
trail, no sycophancy, correct action), averaged over the five pushback
scenarios.

Recorded in `scripts/phase0_epp_results.json`: all three models passed the gate,
with 4 of 6 metrics passing for Opus and 2 of 6 for each of Sonnet and Haiku.

Limits that matter when reading this:

- The experiment tested the full block, not the current one-line pointer. The
  pointer has not been measured against it.
- `scripts/phase0_epp_calibration.py` still hardcodes the old
  `<semantic-pushback-check>` tag. It is a record of that experiment, not a live
  check; re-running it would test a block the hook no longer emits.
- Sample size was small (n=5 per condition), so treat the per-metric
  percentages as direction, not effect size.

The design spec the hook comments cite
(`docs/superpowers/specs/2026-04-07-epp-strengthening-design.md`) is not in this
repository.

---

## Not implemented

1. Persistent position anchors (above).
2. Per-model forcing strength: one pointer for all models.
3. Automated measurement of sycophancy rate across real conversations.
4. LLM classification inside the hook: a synchronous model call per prompt is too
   slow and costly.
5. A statusline marker for "EPP activated".

---

## Files

| File | Role |
|---|---|
| `empirica/plugins/claude-code-integration/hooks/tool-router.py` | Pointer constant, trigger conditions, context ordering |
| `empirica/plugins/claude-code-integration/skills/epistemic-persistence-protocol/SKILL.md` | The protocol |
| `empirica/cli/command_handlers/epp_commands.py` | `epp-activate` handler |
| `empirica/cli/parsers/checkpoint_parsers.py` | `epp-activate` parser |
| `scripts/phase0_epp_calibration.py`, `phase0_epp_scenarios.yaml`, `phase0_epp_results.json` | Phase 0 harness, scenarios, results |
