# Empirica Chat — Overnight Autonomous Build Plan

> **Status:** Draft for David's approval (T45, 2026-05-03).
> **Mode:** Autonomous execution after approval — no further check-ins
> mid-plan unless a phase fails twice.
> **Branch:** `develop` (empirica), `build/v1-plugin` (ecodex).
> **Substrate:** Sentinel discipline ENFORCED (PREFLIGHT/CHECK/POSTFLIGHT
> per phase, commit before POSTFLIGHT, log artifacts as discovered).

---

## What's currently shipped (baseline)

empirica chat v0 — all on `develop`, head commit `315a6f8f7`:

| Phase | Status | Commit | LOC |
|---|---|---|---|
| 0 | spec + skeleton | `7fb414b53` | ~50 |
| 1 | conversation render + jsonl persistence | `d50254bfb` | ~400 |
| 2a | direct translator dispatch | `77d7ef164` | ~250 |
| 4 | artifact cards (v0 demo with /finding /decision /unknown) | `0f6604456` | ~440 |
| 6 | basic statusline (4 modes) | `7e8920352` | ~180 |
| T40 | multi-provider selector (4 builtin empirica-server providers) | `1cae6324c` | ~520 |

ecodex translator — all on `build/v1-plugin`:
- 21/21 unit tests, mock smoke test, live-tested against DeepSeek (402)
  + against empirica-server Ollama (full streaming round-trip).

**Total shipped: ~1840 LOC across 6 chat phases + ~3000 LOC translator.**

---

## Plan structure

Three priority tiers, totaling ~14-18 hours of autonomous work. Each
phase = one transaction (PREFLIGHT → work → POSTFLIGHT). Skip + document
if any phase fails twice. Each commit goes to develop with the standard
co-author line.

**Per-phase acceptance criteria (every phase must meet all four):**
1. Code committed to `develop` branch
2. Imports verify (fast `python3 -c "import …"` test)
3. Programmatic smoke test of new behavior where possible
4. Spec entry in CHAT.md updated to "shipped" with commit hash + LOC

**Per-phase artifact discipline:**
- 1 finding logged (what shipped + verification result)
- 1 decision logged if any non-trivial design call made
- mistakes logged immediately with prevention rules

---

## TIER 1 — Foundational (do these first; everything else builds on them)

### Phase 8: System prompt + epistemic discipline integration
**Goal:** `b910b609` · **Est:** ~200 LOC · **Time:** 1.5h

Adapt `~/.claude/empirica-system-prompt.md` for chat mode. Inject as a
system message at session start. Survives compaction. Tunes AI behavior
to be epistemically aware WITHOUT forcing CC's transaction discipline
(chat is conversational, not praxic-gated).

**Deliverables:**
- `empirica/core/chat/system_prompt.py` — render_system_prompt(provider, model, autonomy_mode) → str
- Adapted prompt: empirica vocabulary, 13-vector awareness, when-to-log-artifacts heuristics, knowledge of slash commands AI can suggest, instruction to surface findings/decisions/unknowns as natural side effects (not forced)
- Wire into ChatApp.on_mount → inject as first turn of new session (system kind, persisted)
- Optional `--no-system-prompt` flag for testing

**Acceptance:** ChatSession with system prompt round-trips through jsonl;
prompt content visible in turn 0; doesn't break Phase 1 standalone mode.

**Why first:** Phases 13, 14, 15 all depend on the AI knowing the
empirica vocabulary and behaving consistently. Without Phase 8, Phase
15 narration would have to detect events the AI doesn't even know it's
emitting.

---

### Phase 6b: Full CC statusline extraction
**Goal:** `9c7e6abd` · **Est:** ~250 LOC · **Time:** 1.5h

Extract `~/.claude/plugins/local/empirica/scripts/statusline_empirica.py`
(225 LOC) into shared `empirica.core.statusline` module. Swap into chat,
upgrading Phase 6 v0's basic 4-mode renderer to full CC fidelity (vector
emojis, calibration trajectory ↗/↘, brier-score awareness). Remove
context-window field — Phase 9 handles that per-model.

**Deliverables:**
- `empirica/core/statusline/__init__.py` — extracted renderer
- `empirica/core/statusline/renderer.py` — full mode logic
- `empirica/cli/tui/chat/statusline.py` — swap to use shared renderer
- (Optional) cockpit_app.py adopts the same module for its 1-line panel

**Acceptance:** all 4 statusline modes render with full CC fidelity in
chat. Direct invocation of the extracted module produces identical
output to running statusline_empirica.py directly (minus context window).

**Why second:** Quick, contained, completes a phase. Sets up Phase 13
which adds the phase-indicator badge to the same panel.

---

### Phase 16 (partial): Slash command refinement
**Goal:** `0c36aef5` · **Est:** ~150 LOC · **Time:** 1h

Implement the user-facing minimal slash surface per T44:
- KEEP user-facing: `/model` `/help` `/plan` (NEW) `/autonomy` (NEW)
- HIDE behind `/help debug`: `/providers` `/provider` `/models` `/statusline`
- DEMOTE: `/finding` `/decision` `/unknown` (Phase 4 v0 demos — keep enabled, hide help)

**Deliverables:**
- `/plan` — show current plan: open goals, transaction list, status (queries empirica via subprocess)
- `/autonomy MODE` — switch to conversational/multi-agentic/autonomous (writes to chat session metadata; Phase 13 reflects in statusline)
- `/help` rewritten to surface only user-facing commands by default; `/help debug` shows the rest
- All existing slash commands continue to work (no removal, just demotion in help)

**Acceptance:** `/help` shows minimal user surface; `/help debug` shows
all; `/plan` displays current plan + transactions; `/autonomy` accepts
the 3 modes and updates state.

**Why third:** Cleans up the slash surface BEFORE Phase 15 starts adding
new event narration (which might tempt me to add more slash commands).

---

## TIER 2 — Visible signals (the conversational-layer differentiators)

### Phase 13: Phase indicator badge (🔍 INVESTIGATE / ▶ ACT)
**Goal:** `3d82a10a` · **Est:** ~150 LOC · **Time:** 1h

Add a phase-indicator badge to the StatuslinePanel showing the AI's
current epistemic phase. Triggered by:
- Heuristic from Phase 8 system prompt behavior (default — no app-server)
- Real CHECK decisions when chat is wired to agent loop (Phase 2b — later)

For autonomy mode = conversational, badge defaults to ▶ (act) since
casual chat doesn't have a noetic gate. For agentic loops (Phase 8+),
the badge flips based on AI's stated mode.

**Deliverables:**
- StatuslinePanel adds a small badge slot
- ChatSession tracks `current_phase: "investigate" | "act"` field
- AI can update via system message detection (Phase 15 hook)

**Acceptance:** Badge visible in statusline; cycles correctly when
phase changes via test injection.

---

### Phase 14: Intuition vs search transparency badge
**Goal:** `9c11964c` · **Est:** ~100 LOC · **Time:** 45m

Per-turn badge: 💡 intuition (training data) vs 🔎 search (external
retrieval). Detection signals:
- Tool-call notifications via app-server (Phase 2b dependency — fall back
  to heuristic for now)
- Translator event tap when streaming includes tool-call-delta events
- Heuristic: agent text mentions URL fetch / file read / explicit lookup

**Deliverables:**
- AgentTurn widget has an optional badge slot
- Detection runs in `_dispatch` after each turn — if any
  tool_call_delta seen during stream, badge = 🔎 else 💡

**Acceptance:** Heuristic correctly tags a known-search response (e.g.,
agent asked "what time is it" → search) vs known-intuition (e.g., "what
is 2+2" → intuition).

---

### Phase 15: Natural-language workflow narration
**Goal:** `3d7303af` · **Est:** ~250 LOC · **Time:** 2h

Translate hook events / lifecycle events into terse natural-language
one-liners surfaced as system turns. Per the verbiage table in the goal:
- `PREFLIGHT` → "thinking through <task_context>"
- `POSTFLIGHT` → "transaction closed: <retrospective summary>"
- `finding-log` → "logged: <text>"
- `decision-log` → "decided: <choice>"
- `unknown-log` → "open question: <text>"
- `unknown-resolve` → "resolved: <unknown> → <finding>"
- `goal-create` → "new plan: <objective>"
- `goal-complete` → "plan complete: <objective>"
- `skill invocation` → "invoking the <skill> skill"
- `subagent spawn` → "launching the <agent_type> subagent"

**Deliverables:**
- `empirica/core/chat/narration.py` — event_to_narration(event_dict) → str
- Listens on translator event tap JSONL (when present) + can subscribe
  to local empirica session DB for our own session events
- Renders as SystemTurn with muted style

**Acceptance:** programmatic test: feed 5 sample event dicts, get back
correctly-phrased one-liners; edge cases (unknown event type → silent
drop) handled.

**Why this tier ordering:** Phase 13 + 14 are small visual additions to
existing statusline + AgentTurn. Phase 15 is the big translation layer.
Phase 13/14 first means Phase 15 just adds another rendering path.

---

## TIER 3 — Convenience features

### Phase 12: Arrow-key model selector
**Goal:** `30fb4a25` · **Est:** ~80 LOC · **Time:** 30m

Modal-list overlay: up/down arrows cycle available models on active
provider, Enter selects + switches, Esc cancels. Auto-fetches /v1/models
on open.

**Deliverables:**
- New widget `ModelSelectorModal` in chat/
- Bound to keyboard shortcut (probably Ctrl+M to avoid conflict)
- `/model` without arg also opens this

**Acceptance:** modal opens; arrow keys cycle; Enter switches; Esc closes.

---

### Phase 4b: Wire artifact-card buttons → real CLI invocations
**Goal:** part of `436e6244` · **Est:** ~100 LOC · **Time:** 1h

Phase 4 v0 emits ActionInvoked but the App handler just renders a
"Phase 5+ wiring" system note. Phase 4b wires the buttons:
- `finding.confirm` → log_finding(text + " — confirmed by user", ...)
- `finding.challenge` → log_decision("challenge finding X", ...)
- `unknown.resolve` → unknown-resolve --unknown-id ID --resolved-by FINDING_ID
- `unknown.escalate` → set unknown impact higher (CLI may not support; alternative: log a finding + mark unknown impact-bumped)
- `*.discuss` → inject system message "user discusses artifact X"
  into next agent turn
- `*.pin` → write to `~/.empirica/chat_pinned_{session_id}.json`

**Deliverables:** `empirica/core/chat/actions.py` extended with the new
action functions; on_artifact_card_action_invoked routes correctly.

**Acceptance:** clicking confirm on a finding card creates a real
follow-up artifact in the empirica DB (verified via `empirica project-search`).

---

### Phase 9: Token tracking + per-model context window
**Goal:** `544a6000` · **Est:** ~300 LOC · **Time:** 2h

Add token bar UI strip. Per-provider tokenizer. Auto-warn at 80%,
auto-suggest /compact at 90%.

**Deliverables:**
- `empirica/core/chat/tokens.py` — count_tokens(provider, model, text)
  with tiktoken (OpenAI family) + transformers AutoTokenizer (HF
  family) fallback chain
- `empirica/core/chat/context_windows.py` — per-model max tokens
  registry (hardcoded defaults + provider /v1/models lookup if available)
- New TokenBar widget below statusline: `||||||| 47% (2300/4096)`
- Updated on every turn append

**Acceptance:** count_tokens returns correct count for known string
against known tokenizer; bar visualizes correctly at edge cases (0%, 50%,
90%, 100%); warns at 80%.

**Risk:** transformers AutoTokenizer is a heavy import (~200MB). Lazy-
import only when needed; fall back to ~chars/4 estimate if not available.

---

### Phase 10: Pre/post compact lifecycle hooks
**Goal:** `ed7bdef6` · **Est:** ~200 LOC · **Time:** 1.5h

Pre-compact saves chat session state to
`~/.empirica/chat_breadcrumbs/{session_id}.yaml`; post-compact restores.
Mirrors CC plugin's pattern.

**Deliverables:**
- `empirica/core/chat/breadcrumbs.py` — save_state / load_state
- `/compact` slash command (manual trigger)
- Auto-trigger when token bar passes 90% (Phase 9 dependency)
- Auto-trigger when provider returns context-overflow error
- Post-compact restores: active provider+model, autonomy mode, recent
  turns (last N), open artifacts, statusline mode

**Acceptance:** /compact saves YAML; restart-with---session-id loads
restored state correctly.

---

### Phase 11: Batch artifact operations
**Goal:** `fa433410` · **Est:** ~150 LOC · **Time:** 1h

Wrap empirica's existing batch CLI endpoints as slash commands:
- `/batch` — opens multiline modal for JSON graph paste (nodes + edges)
- `/resolve-batch ID1 ID2 …` — batch resolve unknowns/assumptions/goals
- `/delete-batch ID1 ID2 …` — batch delete stale artifacts

Each created artifact renders as its own ArtifactCard (Phase 4 reuse).

**Acceptance:** /batch with sample JSON creates the expected artifacts;
each renders inline.

---

## TIER 4 — Architecture / integration (defer if time runs short)

### Phase 7: Replay mode
**Goal:** part of `436e6244` · **Est:** ~150 LOC · **Time:** 1h

Open old session jsonl with `--replay <session-id>` flag. Renders all
turns as historical (read-only mode). Useful for review + sharing.

**Deliverables:** `--replay` flag; ChatApp constructor handles replay
mode (no input dispatch); UI clearly marks read-only.

---

### T53: ecodex wrapper auto-spawn translator
**Goal:** subtask · **Est:** ~150 LOC · **Time:** 1h

`ecodex` wrapper script auto-spawns codex-empirica-translator on
startup when chat-completions providers configured; rewrites those
providers' base_urls to `http://localhost:18080/v1`. install.sh drops
the translator binary alongside the main ecodex binary.

**Deliverables:**
- Update `ecodex/scripts/ecodex-wrapper.sh` to spawn translator if
  needed (detect via config.toml.default scan or env var)
- Update `ecodex/scripts/install.sh` to install translator binary
- Document in `docs/ecodex/integrations/discipline-strengthening.md`

**Acceptance:** running `ecodex` (cold) starts translator + connects
without user intervention.

---

### T55: Live smoke test against real chat-completions provider
**Est:** ~50 LOC · **Time:** 30m

Convert the existing mock-based smoke_test.sh into a live variant
`live_test.sh` that takes `--provider NAME --base-url URL --api-key-env ENV`
and runs the same contract checks against a live endpoint.

**Acceptance:** runs against empirica-server with qwen3.5:latest, gets
real response; runs against (mocked-balance) DeepSeek, surfaces the 402
through the same assertion pipeline.

---

## TIER 5 — Big lifts (only if Tiers 1-4 finish AND there's time)

### Phase 5: Knowledge graph side panel
**Goal:** part of `436e6244` · **Est:** ~250 LOC · **Time:** 2.5h

Click an artifact card → side panel opens showing artifact full content
+ edges + Qdrant semantic neighbors + related goals.

### Phase 2b: codex-app-server WebSocket dispatch
**Est:** ~250 LOC + integration testing · **Time:** 3-4h

Replace direct translator dispatch with JSON-RPC over WebSocket to
codex-app-server. Exposes full agent loop (tool calls, plan/act/observe,
codex hooks). Significant integration effort — defer if any earlier tier
ate budget.

---

## Execution order summary

```
T1 → Phase 8  (system prompt)            ~1.5h   ★ unblocks 13/14/15
T1 → Phase 6b (full statusline)          ~1.5h
T1 → Phase 16 (slash refinement)         ~1.0h
─── 4.0h budget for foundations ──────────────────
T2 → Phase 13 (phase indicator)          ~1.0h
T2 → Phase 14 (intuition vs search)      ~0.75h
T2 → Phase 15 (natural-language narration) ~2.0h
─── 3.75h budget for visible signals ─────────────
T3 → Phase 12 (arrow selector)           ~0.5h
T3 → Phase 4b (real button actions)      ~1.0h
T3 → Phase 9  (token bar)                ~2.0h
T3 → Phase 10 (compact hooks)            ~1.5h
T3 → Phase 11 (batch artifacts)          ~1.0h
─── 6.0h budget for convenience ──────────────────
T4 → Phase 7  (replay mode)              ~1.0h
T4 → T53      (ecodex wrapper)           ~1.0h
T4 → T55      (live test)                ~0.5h
─── 2.5h budget for polish ───────────────────────

Total tiers 1-4: ~16.25h. Tier 5 only if time permits.
```

**If everything goes well:** chat ships Phases 6b/8/9/10/11/12/13/14/15/16
+ Phase 4b + ecodex T53 + T55 — most of the forward backlog.

**If only Tier 1 + Tier 2 finish:** chat is dramatically more useful
(epistemic awareness, full statusline, narration, slash refinement).

**If only Tier 1 finishes:** still meaningful — system prompt is
foundational.

---

## Risk management

- **Per-phase 2-fail rule:** if a phase's PREFLIGHT-to-commit cycle
  fails twice (e.g., import errors, test failures, persistent design
  ambiguity), I skip and document. Don't burn 2h fighting one phase.
- **Commit discipline:** every phase commits before POSTFLIGHT.
  Uncommitted work is lost.
- **No interactive prompts:** if a CLI subprocess needs interactive
  input, document and skip rather than hang.
- **Spec discipline:** every shipped phase updates CHAT.md with status,
  commit hash, LOC delta. So when you wake up, CHAT.md alone tells you
  what shipped.
- **Honest reporting:** if I produce slop or skip steps, the POSTFLIGHT
  artifacts will show it (calibration_status, evidence_count, mistakes
  logged). I will not embellish.
- **Sentinel discipline:** Sentinel hooks remain enforced; no shortcuts
  via `--no-verify` or sentinel pause. Every transaction is a clean
  PREFLIGHT → CHECK → POSTFLIGHT cycle.

---

## What WON'T be done overnight (out of scope)

- Phase 5 (KG side panel) and Phase 2b (app-server WS) — too big for
  overnight, defer to a fresh session
- Cockpit conversation widget — superseded by the chat-as-separate-tool
  decision (T31)
- New features David hasn't requested — no scope creep
- Tests beyond programmatic smoke — full pytest suite is for next session
- Live tests requiring keys we don't have (DeepSeek balance, etc.)
- Anything requiring David's input — if the build hits a real
  architectural fork, I document and stop on that phase only

---

## When you wake up

1. **Read** `empirica/docs/architecture/CHAT.md` — every shipped phase
   has its commit hash + LOC delta marked
2. **Run** `git log --oneline develop -30` — chronological commits
3. **Check** `empirica goals-list` — completed goals + remaining
4. **Test** `empirica chat` — try the new commands, see the new badges,
   the new narration
5. **Issues?** Each transaction has a POSTFLIGHT with confidence + any
   mistakes logged

---

**Awaiting your approval to proceed.** Suggested response if approving:
"go" or "approved, run T1 only" or any specific tier you want to cap at.
