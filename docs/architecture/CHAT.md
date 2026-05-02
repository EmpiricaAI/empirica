# Empirica Chat

> Single-instance collaborative epistemic workspace.
>
> **Status:** Spec (T33). Implementation pending.
> **Sister:** [`COCKPIT.md`](COCKPIT.md) (multi-instance orchestration — complementary, not replaced)
> **Related:** [`EWM.md`](EWM.md), [`EPISTEMIC_PROMPT_ENGINE.md`](EPISTEMIC_PROMPT_ENGINE.md),
>   ecodex translator + event tap (`ecodex/codex-rs/codex-empirica-translator/`)

---

## What it is

A focused single-instance Python Textual TUI (`empirica chat`) that hosts the
AI ↔ human collaborative loop. Both parties contribute to the epistemic
state — the chat renders the conversation **and** the epistemic
artifacts as first-class elements, not as buried tool-call output.

| Surface | Purpose |
|---|---|
| `empirica chat` (this spec) | Single-instance conversation + epistemic workspace |
| `empirica tui` ([COCKPIT.md](COCKPIT.md)) | Multi-instance orchestration view — sister tool |
| `ecodex` (codex's TUI) | Standard codex experience — full mode for power users |

These are complementary. Common pattern is to run `empirica chat` and
`empirica tui` in tmux panes side-by-side: chat for the active
conversation, cockpit for "what are my other agents doing." On a phone:
pick whichever fits the moment.

---

## Principle

**The epistemic state is the work.** A conversation that renders only
prose is incomplete — findings, decisions, unknowns, dead-ends, goals,
and transactions are not byproducts of the conversation, they *are* the
conversation. Render them as first-class turn elements with the same
weight as text.

Corollary: **the human is a peer in the epistemic loop, not an
audience.** The chat affords human participation in artifact creation
(👍 a finding to confirm it, 👎 to challenge, "tell me more" to deepen,
"resolve" to close an unknown). Both parties shape the epistemic map.

---

## Three autonomy modes

The chat renders differently based on the AI's current operating mode.
Mode is set per-session and surfaced as a header badge.

| Mode | Badge | Rendering |
|---|---|---|
| **Conversational** | `🤖 conversational` | Every AI turn is interactive. User sees each step. Default for exploration, design, learning. |
| **Multi-agentic loop** | `🌀 multi-agentic` | AI is in a cooperative loop with other agents. Chat shows loop progress against an epistemic map. User can intervene but doesn't drive each turn. |
| **Full earned autonomy** | `🦅 autonomous` | AI works against plan/transactions independently. Chat becomes ambient — surfaces only escalations, milestone artifacts, and human-required decisions. Foreground noise drops; epistemic delta foregrounds. |

Mode is a property of the empirica-autonomy integration layer (separate
spec), not of chat itself. Chat consumes the current mode via the same
`~/.empirica/` state files cockpit reads.

---

## Layout

Vertical Textual layout. Targets ~80×24 minimum, scales up. Phone
landscape works; phone portrait acceptable but optimized for tmux pane
or laptop terminal first (chat is a focused-attention surface, not a
glance-and-go like cockpit).

```
┌─ Header           (mode badge · model · instance label · clock)
│  Statusline       (k:.. c:.. conf:.. phase:noetic|praxic — ctx:N%)
│
│  Conversation     (scroll, takes most of the screen)
│    ├─ Turn (user) — plain
│    ├─ Turn (agent) — text + inline artifact cards
│    │   ┌─ Finding card    (impact, subject, confirm/reject)
│    │   ├─ Decision card   (rationale, reversibility, evidence links)
│    │   ├─ Unknown card    (resolve / convert-to-finding)
│    │   ├─ Goal card       (subtasks, completion %)
│    │   └─ Transaction card (PREFLIGHT/CHECK/POSTFLIGHT vectors)
│    └─ Tool call (collapsed by default — Ctrl-O to expand all)
│
│  Input            (multi-line, paste-friendly, autocomplete commands)
└─ Footer           (key bindings + active autonomy notice)
```

Tool calls and code diffs are **collapsed by default** in conversational
mode and hidden in autonomous mode. `Ctrl-O` reveals everything
(matches codex's existing convention).

---

## Conversation rendering — turn types

Every turn is one of:

| Type | Rendered as | Source |
|---|---|---|
| `user` | Plain text bubble (right-aligned or distinct background) | User input |
| `agent_text` | Plain text (left-aligned, agent style) | Translator event tap `text_delta` events accumulated → agent turn |
| `agent_reasoning` | Collapsed thinking-block (chevron to expand) | Translator `reasoning_delta` (Anthropic + future o-series providers) |
| `tool_call` | Collapsed strip (`▶ Read foo.py`) — expand to see full | App-server agent-loop notification |
| `tool_result` | Collapsed by default unless error — show inline if surfaces something user-relevant | App-server notification |
| `epistemic_action` | **First-class card** — see "Artifact cards" below | Empirica MCP / CLI traffic intercepted via translator event tap or app-server hook events |
| `system` | Italic muted text (compaction markers, mode transitions, sentinel pauses) | Various |

Each turn carries a stable `turn_id` so artifact cards can resolve into
the user's interactions without losing identity across re-renders.

---

## Artifact cards (the differentiator)

When the AI logs a finding, decision, unknown, mistake, dead-end,
assumption, goal, or transaction, the chat renders a **structured card
inline in the conversation flow**, not a wall of JSON in a tool-call
expand.

Pattern lifted directly from `empirica_workspace`'s
`CandenceChatScreen` + `IntelligencePanel` (see
`empirica_workspace/dashboard/CANDENCE_API.md`):

```
┌─ 🔎 Finding · impact 0.85 ──────────────────────────────────┐
│ codex-app-server WebSocket transport is multi-client +      │
│ JSON-RPC. Means Cockpit Option C is fully viable.           │
│                                                              │
│ subject: codex-app-server  · session 26942c05               │
│ [👍 confirm]  [👎 challenge]  [💬 discuss]  [📌 pin]          │
└──────────────────────────────────────────────────────────────┘
```

Per-artifact-type quick actions:

| Artifact | Actions |
|---|---|
| `finding` | confirm · challenge · discuss · pin |
| `decision` | acknowledge · reverse · discuss · cite-as-precedent |
| `unknown` | resolve (with finding) · escalate · discuss · de-prioritize |
| `mistake` | acknowledge · add-prevention · discuss |
| `dead_end` | acknowledge · discuss |
| `assumption` | confirm (→ finding) · falsify (→ decision) · discuss |
| `goal` | view-subtasks · complete · split · re-scope |
| `transaction` | view-vectors · view-deltas · view-grounded-calibration |
| `source` | open-link · view-citations · discuss |

Action invocations either:
1. **Direct CLI write** — clear cases like `confirm finding` →
   `empirica finding-log` with same content + link as confirmed
2. **Send to AI via chat** — like `discuss` → injects a system message
   into the next turn ("user discusses finding X: ...")
3. **Open modal** — like `view-vectors` for a transaction

The card is the **action surface**, not just a render. This is what
makes the chat a *workspace* and not a transcript.

---

## Knowledge graph linkage

Every artifact card carries its UUID. Clicking a card opens a side
panel (or modal in narrow layouts) showing:

- The artifact's full content (untruncated)
- Edges into/out of the artifact (evidence, caused-by, prevents,
  resolves, sourced-from, attached-to)
- Related artifacts via Qdrant semantic search
- Related goals (where applicable)
- Related sources

This is the **knowledge graph spelunking** view — the chat is the
read/write surface; the side panel is the navigate surface. Same data
as `empirica project-search` + `empirica project-bootstrap`, rendered
for direct interaction.

Pattern parallels Candence's "click card → discuss with EPE" flow but
generalized to all artifact types.

---

## Statusline integration

The header includes a 1-line condensed statusline. Pressing `s` (or
clicking it) expands the full statusline modes from
`statusline_empirica.py` (basic | default | learning | full):

```
┌─ Statusline ─────────────────────────────────────────────────────┐
│ Mode: [basic] [default*] [learning] [full]                        │
│                                                                   │
│ Phase: 🟡 praxic     · transaction 944a20f2 · age 4m              │
│ Vectors:                                                          │
│   know: ████████░░ 0.85    uncertainty: ██░░░░░░░░ 0.18           │
│   context: █████████░ 0.92   completion: ███░░░░░░░ 0.30          │
│   ...                                                             │
│ Open: 3 goals (1 in-scope) · 2 unknowns (1 blocking)              │
│ Calibration trajectory: ↗ Brier improving (0.142 → 0.115)         │
└───────────────────────────────────────────────────────────────────┘
```

Renderer is the same `format_vectors_compact` from
`empirica.core.signaling` plus the count queries from
`statusline_empirica.py:get_open_counts`. Cockpit shows a 1-line
version; chat shows full when expanded. **Same renderer, different
fidelity** — no duplication.

---

## Data flow / subscriptions

Chat subscribes to **two streams** (per the architecture decided in T29 + T30):

```
                 ┌───────────────────────────────┐
                 │  empirica chat (Python+Textual)│
                 │  ┌────────────────────────────┐│
                 │  │ Turn aggregator            ││
                 │  │ (merges both streams into  ││
                 │  │  ordered turn timeline)    ││
                 │  └─────▲──────────▲───────────┘│
                 └────────┼──────────┼────────────┘
                          │          │
       ┌──────────────────┘          └──────────────────────┐
       │                                                    │
┌──────▼─────────────────────┐         ┌────────────────────▼──────────┐
│ codex-app-server           │         │ ecodex translator event tap   │
│ ws://localhost:NNNN        │         │ ~/.empirica/translator-events │
│ JSON-RPC (ClientRequest /  │         │   .jsonl  (tail -F)           │
│ ServerNotification)        │         │                               │
│ → agent-loop semantics:    │         │ → raw model events:           │
│   user prompts in,         │         │   text_delta, tool_call_delta,│
│   tool_call notifications, │         │   reasoning_delta, completed, │
│   thread state changes     │         │   request_started/completed   │
└──────┬─────────────────────┘         └────────────────────┬──────────┘
       │                                                    │
       └────────► codex agent loop ◄────► provider (DS/Q/G/K/Anth)
                  ▲
                  │ (translator interposes for chat-completions providers)
```

**App-server stream** = "what the agent loop is doing." Authoritative
for: turn boundaries, tool calls, thread state, mode transitions,
epistemic actions emitted by the empirica plugin's hook handlers.

**Translator stream** = "what the model is producing." Authoritative
for: token-level deltas, latency, finish reasons, raw error classes.

Chat aggregates both into the turn timeline. The two streams arrive
out of order; chat reconciles by matching `request_id` (translator) ↔
`turn_id` (app-server) — the empirica plugin's hook captures both at
turn boundary and writes the mapping to a session file.

---

## State files

All under `~/.empirica/`. Chat reads (mostly) and writes only its own
session-scoped files.

| File | Owner | Purpose |
|---|---|---|
| `chat_sessions/{session_id}.jsonl` | chat | Full turn history (append-only). One JSON line per turn (or sub-turn). Replayable. |
| `chat_layout_{session_id}.json` | chat | Layout state — collapsed/expanded artifact cards, side panel state |
| `chat_pinned_{session_id}.json` | chat | User-pinned artifacts for quick access |
| `instance_projects/{instance_id}.json` | session-init hook | (read) project lookup |
| `active_transaction_{instance_id}.json` | workflow_commands | (read) current transaction for statusline |
| `hook_counters_{instance_id}.json` | hooks | (read) phase derivation |
| `translator_request_to_turn_{session_id}.json` | empirica plugin | (read) request_id ↔ turn_id mapping for stream reconciliation |

`chat_sessions/*.jsonl` is the source of truth for replay + analysis.
Chat is reconstructable purely from this file. Layout/pinned files are
ephemeral UX state and can be deleted without losing the conversation.

---

## Subscriptions to the existing primitives

Don't reinvent — **subscribe**. Each primitive already exists in
empirica core; chat is a renderer + interaction surface for them.

| Primitive | Source | Used for |
|---|---|---|
| `aggregate_all()` | `empirica.core.cockpit.instance_state` | Header instance label, project context |
| `statusline_summary()` + `format_vectors_compact()` | `empirica.core.cockpit` + `empirica.core.signaling` | Statusline header + expanded view |
| `get_open_counts()` | `~/.claude/plugins/local/empirica/scripts/statusline_empirica.py` (extract to `empirica.core.statusline`) | Goals/unknowns counts in statusline |
| `EpistemicPromptEngine` | `empirica_workspace.engine` | (Optional) for "discuss finding X" flow if available |
| `SessionDatabase` | `empirica.data.session_database` | Artifact lookup for card content + edge resolution |
| Sentinel pause/loops/listeners | `empirica.core.cockpit.*` | (Optional) chat may surface that the instance is paused |

The CLI subprocess pattern is ALSO available: chat can shell out to
`empirica finding-log`, `empirica unknown-resolve`, etc. for action
invocations. Subprocess is cheaper to maintain than direct module
imports — no internal API churn risk.

---

## Wiring summary (planned files)

```
empirica/cli/parsers/chat_parsers.py           # `empirica chat` argparse
empirica/cli/command_handlers/chat_commands.py # handler dispatching to TUI
empirica/cli/tui/chat_app.py                   # Textual app entry
empirica/cli/tui/chat/                         # widget package
    __init__.py
    conversation.py     # ConversationScroll widget — turn list
    turn.py             # Turn widgets (UserTurn, AgentTurn, SystemTurn)
    artifact_card.py    # ArtifactCard with type-specific actions
    statusline.py       # Header statusline (delegates to core renderer)
    side_panel.py       # Knowledge-graph side panel
    input.py            # Multi-line input with command autocomplete
    aggregator.py       # Two-stream merger (app-server + translator)
empirica/core/chat/                            # business logic
    __init__.py
    session.py          # ChatSession state + jsonl persistence
    appserver_client.py # Python websockets + JSON-RPC client
    tap_subscriber.py   # tail -F on translator-events.jsonl
    actions.py          # CLI invocation for artifact actions
empirica/core/statusline.py                    # extracted from CC plugin
                                                # script for shared use
```

Estimated: ~1200 LOC chat package + ~200 LOC statusline extraction +
~150 LOC tests. Total fresh code ~1500 LOC. Reuses existing cockpit
core (~100KB) + statusline renderer + signaling module + session DB.

---

## Bindings

| Key | Action |
|---|---|
| `Enter` | Send input (Shift+Enter for newline) |
| `Ctrl-O` | Toggle full-stream view (reveal all tool calls + diffs) |
| `Ctrl-K` | Quick artifact search modal |
| `Ctrl-G` | Knowledge-graph side panel toggle |
| `s` | Expand/collapse statusline |
| `m` | Cycle autonomy mode (only available if user has authority) |
| `r` | Refresh statusline + counts |
| `?` | Help overlay |
| `q` | Quit (confirm if mid-stream) |

Card-specific actions are buttons inside the card body — keyboard
focus walks card → button → next card.

---

## What's deliberately out of scope

Same discipline as cockpit — write a separate proposal first.

- **Multi-instance chat** — chat is single-instance by design; multi-
  instance orchestration is cockpit's job. If you want to chat with
  agent A while observing agents B/C, run cockpit alongside.
- **Voice / audio I/O** — text-first. Voice integration belongs in
  `empirica voice` skill (separate).
- **Embedded code editing** — chat is a workspace surface, not an
  editor. Edits go through the AI; for direct edits use `$EDITOR` or
  the codex TUI.
- **Knowledge graph EDITING via chat UI** — read/navigate yes, edit
  no. Artifact creation happens through AI tool calls or CLI.
  Editing-via-chat-UI invites entropy.
- **Ambassador / external agent dispatch** — that's the
  `AMBASSADOR_ARCHITECTURE.md` territory; chat may surface
  ambassadorial events but doesn't drive them.
- **Web UI / Chrome panel** — terminal first. JSON schemas in
  `chat_sessions/*.jsonl` make a future web renderer trivial; build
  that proposal separately when the watch-recipe analog ("read the
  jsonl file in your browser") surfaces ≥3 documented gaps from real
  use.

---

## Build phases

Sized for incremental shipping. Each phase ends with a working binary
+ tests + commit.

### Done — v0 (substantively complete)

| Phase | Status | Deliverable | LOC | Commit (empirica/develop) | Goal |
|---|---|---|---|---|---|
| **0** | ✅ | Spec (this doc) + `empirica chat --help` skeleton subcommand | ~50 | `7fb414b53` | — |
| **1** | ✅ | Conversation rendering: ChatSession state + jsonl persistence + UserTurn/AgentTurn widgets + Input widget + `--feed sample.jsonl` | ~400 | `d50254bfb` | `436e6244` |
| **2a** | ✅ | Direct translator dispatch (HTTP+SSE — Responses-format → translator → upstream provider) | ~250 | `77d7ef164` | `436e6244` |
| **4** | ✅ | Artifact cards (Finding/Decision/Unknown) + per-type action buttons + slash commands | ~440 | `0f6604456` | `436e6244` |
| **6** | ✅ | Statusline integration (live vectors + open counts + 4 render modes via /statusline) | ~180 | `7e8920352` | `436e6244` |
| **T40** | ✅ | Multi-provider selector (`/providers /provider /models /model`) + direct chat-completions client (no-translator path) | ~520 | `1cae6324c` | `436e6244` |
| **8** | ✅ | System prompt + epistemic discipline integration. `render_system_prompt(provider, model, autonomy_mode)` adapts CC's empirica-system-prompt.md pattern for chat (conversational, NOT praxic-gated). Three autonomy modes (assistant/copilot/autonomous) with distinct behavior blocks. Wired as turn 0 in `ChatApp.on_mount`; `--autonomy` + `--no-system-prompt` CLI flags; `--system` text preserved as user appendix. SYSTEM turns excluded from LLM history (line 205-209 filter) so turn 0 shows visually without polluting context. 19/19 pytest pass. | ~340 | `639f22934` | `b910b609` |
| **6b** | ✅ | Shared statusline renderer module (`empirica.core.statusline`) with Backend abstraction (AnsiBackend for CC plugin, RichBackend for Textual chat). Lifts CC's renderer core (1455-LOC `statusline_empirica.py`) into a reusable package — chat now gets vector emojis (⚡💡💫🌑), color-tiered values, open-counts with goal/blocker breakdown (🎯3 ❓6/4), phase indicator (PRE 🔍/CHK ⚙→/POST), delta summary (✓⚠△). Two-letter labels (Cx/Cl/Cm) avoid collision. context-window field NOT extracted (Phase 9 owns it); CC session-resolution stays in plugin. 74 tests parametrized over both backends. | ~770 | `d840e85ad` | `9c7e6abd` |
| **16** | ✅ | Slash command surface refinement. `_handle_slash` refactored from 19-complexity if-chain into SLASH_HANDLERS dispatch table (drops C901). New commands: `/plan` (queries `empirica goals-list`, formats open goals with status+progress), `/autonomy MODE` (runtime switch + re-renders system prompt). User-facing surface (in `/help`): `/help /model /plan /autonomy`. Dev-internal (in `/help debug`): `/providers /provider /models /statusline /finding /decision /unknown`. Surface table at `empirica/core/chat/slash.py` is the single source of truth. 23 dispatch tests pass. | ~180 | `ea6aad5a1` | `0c36aef5` |
| **13** | ✅ | Phase indicator badge in statusline. Surfaces work phase under David's conversational-layer surface principle: 🔍 INVESTIGATE (noetic, cyan) / ▶ ACT (praxic, bright_green). NOT the full PRE/CHK/POST transaction lifecycle (that's substrate). Badge omits cleanly when no active transaction. Reads phase from `.empirica/active_transaction*.json` via existing `_read_transaction_state` helper. New `format_work_phase_badge` renderer in shared statusline module — both backends. Wired into default/learning/full modes (basic stays confidence-only). 5 new tests over both backends. | ~90 | `3b9d72608` | `3d82a10a` |
| **14** | ✅ | Intuition vs search transparency badge per agent turn. 💡 intuition (yellow) = model training data (default); 🔎 search (cyan) = external retrieval (tool calls, file reads, web fetch, KG lookups). New `format_source_badge` renderer following Phase 13 pattern. `Turn.metadata['source']` carries the value; `AgentTurn._format_body` prepends the badge; `_stream_agent_response` defaults new turns to 'intuition'; `_update_agent_turn` re-renders via AgentTurn so badge persists across streaming deltas. Phase 2b will flip to 'search' on tool-call observation. 5 new tests. | ~85 | `6fdf4c8b6` | `9c11964c` |
| **15** | ✅ | Natural-language workflow narration v0 (pure translation layer). `empirica/core/chat/narration.py` translates raw empirica + translator events into terse one-liners suitable for muted SystemTurns. 14 empirica event kinds (preflight/check/postflight + 8 artifact_log + skill/agent/plan) and 4 translator kinds (started/completed/errored/stream). `_EMPIRICA_NARRATORS` dispatch table + per-kind narrator functions; `_n_artifact` factory dedupes the 8 artifact body-extraction shapes. `narrate()` dispatcher routes by event source/shape. 36 golden-snapshot tests pin verbiage. Phase 15b follow-up wires the live tail/dedup/threading into ChatApp. | ~300 | `b9ea80c35` | `3d7303af` |
| **12** | ✅ | Arrow-key model selector modal (Ctrl+M). Textual ModalScreen with OptionList (up/down/Enter built-in). Loads instantly, fetches /v1/models in worker, populates list. Currently-active model marked ▶. Dismiss callback applies registry.set_active_model + refreshes subtitle. Reuses list_models + set_active_model — no duplication. 9 unit tests for construction + render paths + dismiss handling. | ~270 | `09e909471` | `30fb4a25` |
| **11** | ✅ | Batch artifact operations slash commands. `/batch PATH` (log-artifacts from JSON file), `/resolve-batch ID1 ID2…`, `/delete-batch ID1 ID2…` — wrappers around existing empirica CLI batch endpoints. All dev-internal (in `/help debug`). Subprocess pipe-to-CLI pattern, tolerant to non-JSON responses (raw_output fallback). 11 mocked-subprocess tests. Useful for bulk cleanup + AI-driven artifact graphs (agentic mode) + reviewer-batch-resolves. | ~320 | `0fbe991a9` | `fa433410` |
| **4b** | ✅ | Artifact-card action buttons wired to real CLI/local-state actions. (unknown, resolve) → empirica unknown-resolve. (*, pin) → ~/.empirica/chat_pinned_{session_id}.json (append-only, corruption-tolerant). (*, discuss) → SystemTurn context injection. (finding, confirm) / (decision, ack) / (decision, reverse) / (unknown, escalate) / (*, challenge) → log_finding chained with subject for searchability. New actions.resolve_unknown wrapper. 7 tests on wrapper + pin file persistence. | ~230 | `a7e73103d` | (no goal — pending v1) |
| **7** | ✅ | Replay mode (`--replay SESSION_ID`). Read-only playback of past chat sessions. Loads jsonl from ~/.empirica/chat_sessions/, renders all turns, disables LLM dispatch on non-slash input. Mutually exclusive with --session-id and --feed (validated in command handler with helpful error messages). Slash commands still work in replay (review surface without dispatch). Banner SystemTurn at mount documents the mode. 9 tests (construction, input dispatch, parser flag, 3 validation paths). | ~170 | `0b7e3db17` | (no goal — pending v1) |

### Pending — v1 backlog

| Phase | Deliverable | LOC est. | Goal |
|---|---|---|---|
| **2b** | App-server WebSocket client (full agent loop via codex-app-server JSON-RPC) | ~250 | `436e6244` |
| **3** | Translator event tap subscriber + stream reconciliation (request_id ↔ turn_id) | ~200 | `436e6244` |
| **5** | Knowledge graph side panel + Qdrant lookups | ~150 | `436e6244` |

### Forward scope (T42 + T43 capture)

These goals were opened during T42 + T43 (2026-05-02) when David flagged
forward scope while we were mid-build. Each is its own goal so the
work doesn't lose specificity when picked up later.

| Phase | Theme | LOC est. | Goal |
|---|---|---|---|
| **9** | Token tracking + per-model context window awareness — token bar UI (`\|\|\|\|\|\|\| 47%`), per-provider tokenizer, auto-compact suggest at 80/90% | ~300 | `544a6000` |
| **10** | Pre/post compact lifecycle hooks — chat session state save/recover via `~/.empirica/chat_breadcrumbs/{session_id}.yaml`, mirrors CC's plugin compact hooks | ~200 | `ed7bdef6` |

Total v0 shipped: ~4595 LOC across 16 phases. Pending v1 backlog:
~600 LOC across 3 phases. Forward scope: ~500 LOC across 2 phases.
Phase numbers are not strictly ordered — pick by leverage.

### Conversational-layer surface principle (T43 + T44 framing)

David's pattern for what surfaces vs what stays under the hood:

> Epistemic awareness should happen under the hood, but the statusline
> should switch when needed.
>
> Workflow discipline is worth surfacing — but as natural conversation,
> not as JSON or tool-call output.

Concretely:

- **Substrate (always-on, never user-visible)** — Sentinel firewall,
  PREFLIGHT/CHECK/POSTFLIGHT lifecycle, plan decomposition into
  transactions, transaction sequencing, vector reasoning, artifact-
  graph edge wiring, calibration-loop math, hook event payloads. The
  ecodex empirica plugin's hooks ENFORCE this — without it there is no
  empirica discipline. But raw machinery never hits the chat surface.
- **Surfaced via statusline** — phase transitions (CHECK decisions:
  investigate vs act), intuition vs search distinction, current
  vectors+counts, autonomy mode badge, current provider:model.
- **Surfaced inline as natural-language one-liners** (Phase 15) —
  "thinking through the auth design", "ready to act on the migration
  plan", "logged: middleware uses next() pattern", "unknown: where
  are roles defined?", "plan transitioned: investigation → implementation",
  "invoking the code-audit skill", "launching the explore subagent",
  "transaction closed: 3 findings, 1 decision".
- **Surfaced inline as cards** (Phase 4) — artifact creations get a
  rendered card with quick actions; the natural-language line above
  flags WHAT just happened, the card shows the substance.
- **NEVER surfaced** — raw JSON, tool-call payload printouts, vector
  arrays, hook protocol details. Always translatable to natural
  language; if it can't be translated, the user doesn't need to see it.

### Slash command surface (T44 framing)

David: "no one will really use /commands. They will use natural language
+ a handful of empirica-like terms the model understands."

The minimal user-facing slash surface:

| Command | Purpose | Phase |
|---|---|---|
| `/model NAME` | Switch active model on current provider | T40 (shipped) |
| `/help` | Brief help overlay | T36 (shipped) |
| `/plan` | Show current plan + transaction list + status | 16 (planned) |
| `/autonomy MODE` | Switch conversational / multi-agentic / autonomous | 16 (planned) |

Dev-internal (still functional, but hidden from `/help` by default —
exposed via `/help debug`):

| Command | Purpose |
|---|---|
| `/providers` `/provider NAME` `/models` `/statusline [MODE]` | Provider + statusline config |
| `/finding TEXT` `/decision TEXT` `/unknown TEXT` | Direct artifact creation (Phase 4 v0 demo path) |

For users who want the full power-user experience with raw tool calls
visible: run `ecodex` (the codex TUI directly), not `ecodex --minimal`
or `empirica chat`. Chat is intentionally the curated surface.

The chat is the most permissive empirica surface — designed for AI ↔
human collaboration without forcing structure into every turn. Cockpit
is for orchestration; CC plugin is for praxic discipline; chat is for
conversation that happens to be epistemically aware.

Phase 1 is buildable standalone (no app-server dependency) — useful
for reviewing the conversation render UX before wiring the rest.

---

## Prior art credits

Lifting design patterns from:

- **`empirica_workspace.dashboard.CandenceChatScreen`** — card-as-chat-
  element with Authorize/Queue/Deny actions, lifecycle states, worker-
  thread async generation, message bubbling. Direct inspiration for
  artifact-card pattern.
- **`empirica_workspace.dashboard.IntelligencePanel`** — sidebar
  showing predictions/gaps/drafts/activity/media. Direct inspiration
  for knowledge-graph side panel.
- **`empirica.cli.tui.cockpit_app`** — Textual app structure, bindings
  pattern, refresh loop. Sister tool by design.
- **Toad (Will McGugan, Textual creator, private dev)** —
  architectural validation: Python+Textual front-end + JSON-protocol
  back-end subprocess. Confirmed our codex-app-server design is sound.
- **Elia (`darrenburns/elia`, Apache-2.0)** — keyboard-centric UX
  idioms, scroll patterns. Design reference, not code base.
- **GPTUI (`happyapplehorse/gptui`)** — multi-AI conversation
  rendering for the multi-agentic-loop autonomy mode.

No code lifted from any of the above. Patterns + UX decisions only.
The artifact-card flow is the genuinely new piece — Candence does it
for predictions, we generalize to all eight Empirica artifact types
plus goals + transactions.
