# Empirica Chat

> `empirica chat`: a single-instance Textual TUI for talking to an LLM while
> logging epistemic artifacts from the conversation.
>
> **Sister surface:** [`COCKPIT.md`](COCKPIT.md) (`empirica tui`, multi-instance view).
> **Install:** Textual is an optional extra, `pip install "empirica[tui]"`.
> Without it the command prints that hint and exits with status 2.

---

## What it is today

Chat is a conversation window with three Empirica pieces attached:

- a **statusline strip** showing the live vectors and open counts of the
  project's active transaction,
- **slash commands** that create artifacts (finding, decision, unknown) and
  render them as inline cards with action buttons,
- a **plain streaming LLM conversation** against any OpenAI-compatible
  endpoint, with the history persisted to disk and replayable.

The model has no tools in chat. It cannot call `finding-log` or open a
transaction itself, so artifact cards appear only when the user runs a slash
command, presses a card button, or when a `--feed` file contains
`epistemic_action` turns. The system prompt says "or do it yourself if you have
tool access"; in chat you do not.

An earlier version of this document specified a larger design: a codex
app-server WebSocket stream, a translator event tap with request-to-turn
reconciliation, a knowledge-graph side panel, token tracking, and live
narration. None of those are wired. They are listed under
[Not built](#not-built) so a reader who met the old spec knows where it went.

| Surface | Purpose |
|---|---|
| `empirica chat` | One conversation, artifacts as cards |
| `empirica tui` ([COCKPIT.md](COCKPIT.md)) | Every running instance, glanceable |

---

## Running it

```bash
empirica chat                                   # builtin providers, assistant mode
empirica chat --provider NAME=URL[,model=M][,wire=W][,key_env=ENV]
empirica chat --autonomy copilot
empirica chat --session-id <UUID>               # resume
empirica chat --replay <UUID>                   # read-only playback
empirica chat --feed sample.jsonl --feed-delay 0.5
```

`empirica chat --help` is the flag reference. Rules worth knowing:

- `--provider` is repeatable. With none given, chat loads four builtin
  endpoints on the empirica-server LAN host (`ollama`, `qwopus`, `llcpp`,
  `llcpp-alt`), so on any other network you must pass your own.
- `--translator-url URL` is the older spelling for a provider named
  `translator` with `wire=responses`.
- `--replay` conflicts with `--session-id` and `--feed`; the handler refuses the
  combination and checks the session file exists before the TUI starts.
- `--system TEXT` is appended to the chat system prompt as user instructions;
  with `--no-system-prompt` it is sent on its own.

---

## Layout

Textual `Header` (clock; subtitle shows autonomy badge and `provider · model`),
the statusline strip, the conversation scroll, a multi-line input, and the
`Footer`.

| Key | Action |
|---|---|
| `Enter` | send (Shift+Enter inserts a newline) |
| `Ctrl+M` | model selector modal for the active provider |
| `Ctrl+L` | clear the input box |
| `Ctrl+Q` | quit |

There are no other bindings. Earlier drafts listed `Ctrl-O`, `Ctrl-K`,
`Ctrl-G`, `s`, `m` and `?`; none exist.

### Turn types

`TurnKind` in `empirica/core/chat/session.py` has seven values. Four render
properly:

| Kind | Rendered as |
|---|---|
| `user` | `you:` bubble |
| `agent_text` | `agent:` bubble with a source badge |
| `system` | muted italic line (mode changes, errors, command output) |
| `epistemic_action` | `ArtifactCard` |

`agent_reasoning`, `tool_call` and `tool_result` are defined and fall through
to a dimmed `UnknownTurn`, because nothing produces them.

The agent badge defaults to `intuition` for every turn. Nothing flips it to
`search`, since there are no tool calls to observe, so the badge carries no
information today.

---

## Slash commands

The table is `SLASH_TABLE` in `empirica/core/chat/slash.py`; `/help` renders it.

| Command | Visible in `/help` | What it does |
|---|---|---|
| `/help [debug]` | yes | list commands; `debug` adds the rest |
| `/model NAME` | yes | set the model on the active provider |
| `/plan` | yes | `empirica goals-list`, prints open goals with status and progress |
| `/autonomy MODE` | yes | `assistant`, `copilot` or `autonomous`; re-renders the system prompt |
| `/compact` | yes | save a breadcrumb (below) |
| `/providers`, `/provider NAME`, `/models` | debug | list or switch providers, list models |
| `/statusline [MODE]` | debug | cycle or set `basic`, `default`, `learning`, `full` |
| `/finding TEXT`, `/decision TEXT`, `/unknown TEXT` | debug | create the artifact, render a card |
| `/batch PATH`, `/resolve-batch IDS`, `/delete-batch IDS` | debug | wrap the batch verbs |

Anything not starting with `/` goes to the model. The chat system prompt lists
only the artifact, provider, model and statusline commands, so the model does
not know about `/plan`, `/autonomy` or `/compact`.

**`/resolve-batch` and `/delete-batch` are stale.** They send
`{"unknown_ids": [...]}` and `{"ids": [...]}`. `empirica resolve-artifacts`
now expects `resolutions` (or `filter`) and `delete-artifacts` expects
`deletions`; both reject the old shapes with an error, which chat prints.
`/batch` pipes the file to `log-artifacts -` unchanged and is unaffected. See
`empirica resolve-artifacts --schema` and `empirica delete-artifacts --schema`.

### Autonomy modes

A mode is a block of text in the system prompt, nothing more. Chat does not
enforce anything per mode; there is no gate, no PREFLIGHT, no Sentinel.

| Mode | Prompt behaviour |
|---|---|
| `assistant` (default) | describe steps, let the user decide |
| `copilot` | take the obvious next step unless the user objects in the same turn |
| `autonomous` | pursue the objective, report at checkpoints |

---

## Artifact cards

`empirica/cli/tui/chat/artifact_card.py`. A card shows a type badge, the text,
a meta line (subject, rationale, short id) and buttons. Badges exist for
finding, decision, unknown, mistake, dead_end, assumption, goal, transaction
and source. Specialised buttons exist for four of them; every other type gets
only `discuss`.

| Card | Buttons | What the button does |
|---|---|---|
| finding | confirm, challenge, discuss, pin | confirm and challenge log a chained finding (`finding-log`, subject `chat-action-<type>-<action>`, impact 0.4) |
| decision | acknowledge, reverse, discuss | both log a chained finding; no decision is reversed or resolved |
| unknown | resolve, escalate, discuss | resolve runs `unknown-resolve`; escalate logs a chained finding |
| mistake | acknowledge, discuss | acknowledge logs a chained finding |

- **pin** appends to `~/.empirica/chat_pinned_<session_id>.json`.
- **discuss** prints "the next agent turn will see this as system context". It
  does not: history sent to the model is built from `user` and `agent_text`
  turns only, and the note is a `system` turn.

Actions shell out to the `empirica` CLI with `--output json` appended
(`empirica/core/chat/actions.py`), so chat depends on the CLI surface, not on
internal imports. A card's artifact id comes from the CLI's JSON response.

---

## Statusline

`StatuslinePanel` refreshes every two seconds. It resolves the instance id,
calls `empirica.core.cockpit.enrichment.statusline_summary`, and renders through
the shared `empirica.core.statusline` package (a `RichBackend`; the Claude Code
plugin statusline uses the same renderers with an ANSI backend). Modes:

- `basic`: confidence only
- `default`: phase badge (investigate or act) plus the default line
- `learning`: open counts and K, U, Cx, Cl
- `full`: confidence, counts, K, U, Cx, Cl, Cm, artifact count

With no active transaction it shows a placeholder. That placeholder text says to
"use /preflight"; there is no such slash command. Open a transaction from the
CLI.

---

## Persistence

| File | Written by | Purpose |
|---|---|---|
| `~/.empirica/chat_sessions/<session_id>.jsonl` | chat | append-only, one turn per line; the source of truth for resume and replay |
| `~/.empirica/chat_pinned_<session_id>.json` | pin button | pinned artifacts |
| `~/.empirica/chat_breadcrumbs/<session_id>.yaml` | `/compact` | provider, model, modes, last 12 user and agent turns |

- **Resume** (`--session-id`) reloads the turns. If a breadcrumb exists it is
  appended as a `system` recovery turn. Resume skips installing a new system
  prompt turn, and the model-side `instructions` are not rebuilt in that
  branch, so a resumed session talks to the provider without the chat system
  prompt unless `--system` was passed.
- **Replay** renders the file read-only. Non-slash input is refused with a
  system note; slash commands still run.
- `/compact` only writes the breadcrumb. It does not shrink anything, and the
  automatic trigger the old spec described was never built.

---

## Providers

`empirica/core/chat/providers.py`. A provider is `name`, `base_url`, optional
`default_model`, a `wire`, and an optional `key_env` naming an environment
variable that holds the API key.

- `wire=chat_completions` (default): direct OpenAI-compatible
  `/chat/completions` streaming (Ollama, llama.cpp, vLLM, hosted APIs).
- `wire=responses`: Responses-format request to an ecodex translator, SSE
  events normalised to text deltas.

Both paths run in a worker thread and update one `agent_text` turn as deltas
arrive; the turn is written to the jsonl on the `completed` event only.

---

## Not built

Present in `empirica/core/chat/` or the code comments but not wired into
`ChatApp`:

- `narration.py` translates Empirica and translator events into one-line system
  notes. It has tests and no caller.
- The app-server WebSocket client, translator event tap subscriber, and the
  request-id to turn-id reconciliation.
- The knowledge-graph side panel and any artifact search modal.
- Token counting, a context bar, and auto-compact.
- Source-aware calibration (feeding the intuition/search badge into PREFLIGHT).

Out of scope by design: multi-instance chat (use the cockpit), voice, an
embedded editor, editing the knowledge graph from the UI, and a web renderer
(the jsonl is the contract if one is ever wanted).

---

## Files

```
empirica/cli/parsers/chat_parsers.py            argparse for `empirica chat`
empirica/cli/command_handlers/chat_commands.py  validation, then run_chat()
empirica/cli/tui/chat_app.py                    ChatApp: slash dispatch, streaming, card actions
empirica/cli/tui/chat/                          artifact_card, conversation, input, model_selector, statusline, turn
empirica/core/chat/                             session, slash, providers, system_prompt, actions, compact,
                                                openai_compat_client, translator_client, narration (unwired)
empirica/core/statusline/                       shared renderers (also used by the Claude Code plugin)
```

Tests are `tests/test_chat_*.py` (slash table, system prompt, compact,
replay, card actions, batch actions, model selector, narration).
