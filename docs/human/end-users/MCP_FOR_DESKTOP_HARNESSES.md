# Empirica via MCP — for Desktop Harnesses & Non-Claude-Code CLIs

If you use **Claude Desktop**, **Cursor**, **Gemini CLI**, **Codex**,
or any other AI harness that doesn't have Claude Code's plugin/hook
system, this is the doc for you. Empirica reaches you through the
**Model Context Protocol (MCP)** instead.

## TL;DR (3 minutes)

```bash
# 1. Install — global, so $HOME/.local/bin/empirica-mcp exists for any harness
pipx install empirica-mcp        # OR: pip install empirica[mcp]

# 2. Verify
empirica-mcp --help

# 3. Configure your harness (see "Per-harness configs" below)
#    All harnesses use the same shape — an mcp.json (or equivalent)
#    pointing at $HOME/.local/bin/empirica-mcp.

# 4. Start the harness. Empirica's 70 tools are now available with the
#    mcp__empirica__ prefix (Claude Code style) or the harness's
#    equivalent.
```

## Why this matters

Claude Code has the **Sentinel** — hook-based runtime gating that
enforces the noetic firewall, autonomy nudges, transaction discipline.
Other harnesses don't have hooks (or have weaker hook surfaces) so
Empirica's runtime gating is **self-enforced** there. The MCP server
exposes the same tool surface; you supply the discipline.

```
Claude Code:                       Desktop / Cursor / Gemini CLI / Codex:
┌─────────────────────────┐       ┌─────────────────────────┐
│  Claude Code            │       │  Your Harness            │
│  ┌───────────────────┐  │       │                          │
│  │ Sentinel (hooks)  │  │       │  (no Sentinel)           │
│  └─────────┬─────────┘  │       │                          │
│            │ gates       │       │  AI is responsible:      │
│  ┌─────────▼─────────┐  │       │   - PREFLIGHT before act │
│  │ Empirica plugin   │  │       │   - CHECK before praxic  │
│  └─────────┬─────────┘  │       │   - POSTFLIGHT to close  │
└────────────┼────────────┘       └────────────┬─────────────┘
             │ stdio MCP                       │ stdio MCP
             ▼                                 ▼
        ┌────────────────────────────────────────┐
        │  empirica-mcp (stdio server)            │
        │  70 tools → empirica CLI subprocess     │
        └────────────────────────────────────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │ empirica CLI │  same state, same calibration,
                    │ + storage    │  same Qdrant, same cortex mesh
                    └──────────────┘
```

The **same epistemic state** flows through both paths. A finding
logged from Claude Desktop is visible to a Claude Code session in the
same project (and vice versa) — they share the SQLite + Qdrant +
git-notes substrate.

### CLI vs MCP — when to use which

If your harness has shell access (some terminal-based AIs like Aider
do), you can call the `empirica` CLI directly instead of going through
MCP:

| Use CLI | Use MCP |
|---|---|
| Terminal-based AI (Claude Code, Aider) | GUI clients (Claude Desktop) |
| Scripts / CI | IDEs without shell access |
| Performance-sensitive paths (~50ms) | Same-conversation tool routing |
| Direct stdin JSON workflows | Auto-namespaced tool discovery |

MCP adds ~100–300ms latency per call vs direct CLI execution. For
Claude Code, the CLI path is canonical — the plugin's hooks call
`empirica` directly without going through MCP.

## Installation

### Standalone install (any harness)

```bash
pipx install empirica-mcp
```

This puts both `empirica-mcp` and (if you also installed the main
package) `empirica` at `$HOME/.local/bin/`. The mcp.json templates
across all harnesses point at this canonical path.

> **Why pipx?** It isolates empirica's deps in their own venv but
> exposes the console scripts globally — desktop harnesses can find
> them without you fiddling with PATH per shell.

### Combined install (CLI + MCP)

```bash
pipx install empirica
# or
pip install 'empirica[mcp]'
# or via Homebrew
brew install empiricaai/tap/empirica
```

The combined install gives you the `empirica` CLI for interactive use
**and** the `empirica-mcp` server that desktop harnesses spawn over
stdio. Both share the same Python environment so they see the same
artifact state immediately.

### Verify

```bash
$ which empirica-mcp
/home/you/.local/bin/empirica-mcp

$ empirica-mcp --help
Empirica MCP Server
...

$ empirica mcp-list-tools | head -5
🔧 Empirica MCP Tools (70 registered)
=====================================
assess:
  assess_state    assess-state — Get current epistemic state assessment
...
```

`empirica mcp-list-tools` is the CLI command that introspects the
running MCP server's tool registry. Useful for "is this tool exposed?
what params?" without launching a full harness session.

## Per-harness configs

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "empirica": {
      "command": "/Users/YOU/.local/bin/empirica-mcp",
      "env": {
        "EMPIRICA_EPISTEMIC_MODE": "true"
      }
    }
  }
}
```

Restart Claude Desktop. Tools appear with the `empirica:` prefix in
the tool picker.

### Cursor

Cursor reads `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "${HOME}/.local/bin/empirica-mcp",
      "env": { "EMPIRICA_EPISTEMIC_MODE": "true" }
    }
  }
}
```

Tools appear in Cursor's MCP panel; reference them in chat with
`@empirica` (Cursor's mention syntax) or rely on auto-tool-use.

### Gemini CLI

Gemini CLI reads `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "${HOME}/.local/bin/empirica-mcp",
      "env": { "EMPIRICA_EPISTEMIC_MODE": "true" }
    }
  }
}
```

### Codex

Codex's MCP config lives at `~/.codex/mcp.json` (similar shape). Check
the Codex docs for the current schema as it evolves.

### Other MCP-compatible clients

Anything that speaks the standard MCP stdio protocol can talk to
empirica-mcp. The config shape is uniform: `command =
empirica-mcp`, optional `env`, optional `args`. Empirica passes
through whatever the harness sends.

### Per-project credentials override

Add `"env": {"EMPIRICA_CREDENTIALS_PATH": "/path/to/credentials.yaml"}`
to any of the configs above to point at a non-default credentials file.
For workspace-specific configs (VSCode `.vscode/settings.json`,
JetBrains workspace configs), reference the workspace folder so
different repos use different cortex tenants / API keys:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "empirica-mcp",
      "env": {
        "EMPIRICA_CREDENTIALS_PATH": "${workspaceFolder}/.empirica/credentials.yaml"
      }
    }
  }
}
```

## What you get — 70 MCP tools (65 standalone, 5 cortex-orchestrated)

Run `empirica mcp-list-tools` to see the live, grouped list. Tools
marked **🌐** require cortex (the mesh backend); the rest work
standalone on base empirica without any backend service. Highlights:

- **Session lifecycle** — `session_create`, `project_bootstrap`,
  `bootstrap_context` (three-circle artifact graph for chat-start
  context)
- **Epistemic transaction loop** — `submit_preflight_assessment`,
  `submit_check_assessment`, `submit_postflight_assessment`
- **Artifact logging** — `finding_log`, `unknown_log`, `deadend_log`,
  `mistake_log`, `assumption_log`, `decision_log`, `source_add`,
  `log_artifacts` (batch graph)
- **Goals** — `goals_create`, `goals_add_task`, `goals_complete_task`,
  `goals_complete`, `goals_list`, `goals_ready`
- **Project search** — `project_search` (Qdrant semantic across
  findings, decisions, episodic memory)
- **Mesh primitives** (added 2026-06-03)
  - 🌐 `practice_context` — Ambassador addressbook, **verify
    canonical 3-form (`org.tenant.project`) before sending to a peer**
    (requires cortex)
  - `commit_context` — walk artifacts anchored to git commits
    (standalone)
  - 🌐 `listener_on` / `listener_arm` / `listener_off` — listener
    facade (works standalone against any ntfy topic; cortex unlocks
    the mesh-event orchestration)
  - `loop_register` / `loop_heartbeat` / `loop_status` /
    `loop_schedule_next` — adaptive scheduler (standalone)
  - `notify_emit` — multi-backend notification dispatcher
    (standalone — ntfy / macos / dbus / slack / email)
  - 🌐 `mailbox_reply` — atomic propose + complete on cortex mesh
    (requires cortex)
  - 🌐 `mesh_status` — mesh health table (local layer always works;
    cortex-bridge layer needs cortex)

### Where empirica ends and cortex begins

Base empirica is fully usable standalone: epistemic transactions,
artifact graph, calibration, goals, project search, sentinel discipline,
the local sentinel CLI. The 🌐 tools listed above are **client-side
primitives** for a mesh backend — cortex is the reference backend, but
the surface is the same regardless of which mesh you point at.

If you run `empirica mcp-list-tools` without cortex configured, the
🌐 tools still show in the list but will return a clear "cortex config
missing" error when called. They are not hidden — knowing they exist
helps you decide if you want to enable the mesh path later.
- **Calibration** — `calibration_report`, `assess_state`,
  `profile_status`
- **Entity registry** — entity walk, search, show
- **Sync** — `sync_push`, `sync_status` for cross-project propagation

## Self-enforcement: the noetic firewall without hooks

In Claude Code, the Sentinel **blocks** praxic tools (Edit, Write,
Bash execution) until CHECK returns `proceed`. In other harnesses,
nothing physically stops you — discipline is in your hands. The
discipline is:

1. **Start every coherent chunk with PREFLIGHT.**
   ```
   mcp__empirica__submit_preflight_assessment {
     session_id: "...",
     task_context: "what you're about to do",
     work_type: "code",
     vectors: {know: 0.6, uncertainty: 0.4, ...}
   }
   ```

2. **Investigate in the noetic phase.** Read, search, explore — log
   findings/unknowns/dead-ends as you discover them. Stay here until
   you can predict the next action's outcome from session-gathered
   evidence (not from priors).

3. **Submit CHECK before you write code.**
   ```
   mcp__empirica__submit_check_assessment {
     session_id: "...",
     vectors: {know: 0.85, uncertainty: 0.15, ...}
   }
   ```
   Until this returns `proceed`, you should stay noetic.

4. **POSTFLIGHT to close the measurement window.**
   ```
   mcp__empirica__submit_postflight_assessment {
     session_id: "...",
     vectors: {...}
   }
   ```
   Without POSTFLIGHT the transaction stays open and your learning
   delta is lost on the next compaction.

The cognitive cost on a hookless harness is real — you have to
remember the discipline yourself. The payoff is that all your
calibration history, artifact graph, and mesh state flow through the
same substrate as your Claude Code sessions.

## Canonical 3-form addressing for the mesh

If your harness will participate in the empirica mesh (sending /
receiving collab messages between AIs), use the canonical `<org>.<tenant>.<project>` form on every emit:

```
target_claudes = ["empirica.david.empirica-cortex",
                  "empirica.david.empirica-extension"]
```

**Not** the bare basename (`cortex`), 2-form (`david.cortex`), or
alias-prefix-stripped form (`empirica.david.cortex` when the actual
slug is `empirica-cortex`). Cortex bounces non-canonical forms via
`delivery_failed` — your listener gets the bounce so you learn, but
the message is lost in the meantime.

Use `mcp__empirica__practice_context` to look up the exact
`ai_id_mesh` field for any peer:

```
mcp__empirica__practice_context {ai_id: "empirica-cortex"}
→ row.ai_id_mesh = "empirica.david.empirica-cortex"  ← use this verbatim
```

## Mesh-active precondition

If your harness will be on the receiving end of mesh wake events
(another AI propose-ing to you), you need a listener subscribed to the
ntfy stream. The CLI provides `empirica listener on` / `arm` / `off`
to manage this, and `empirica mesh status` to inspect health. On
hookless harnesses you'd typically run these from a separate terminal
since the harness's own MCP session can't manage a background
subprocess for itself.

For an always-on listener that survives across harness sessions,
install the systemd-user / launchd service:

```bash
empirica listener install-request   # AI-task analog
```

(See `docs/architecture/EVENT_LISTENER.md` for the full pipeline.)

## Troubleshooting

| Symptom | Fix |
|---|---|
| Harness can't find `empirica-mcp` | `pipx install empirica-mcp`; verify `which empirica-mcp` returns `~/.local/bin/empirica-mcp`. Restart harness after install. |
| MCP returns errors but the CLI works | The MCP wrapper resolves `empirica` from PATH at call time. The harness process (Claude Desktop, etc.) may launch with a restricted PATH — make sure the same PATH that finds `empirica` is visible to it. |
| Auth errors | The MCP server reads cortex credentials from `~/.empirica/credentials.yaml` (same as the CLI). Point at a different file via `EMPIRICA_CREDENTIALS_PATH` in the client's `env` block. |
| Tools missing from picker | Restart the harness — MCP tool discovery happens at connection time. |
| Empirica CLI errors "no project bound" | Run `empirica project-init` inside a git repo first, then bootstrap. |
| Tool calls hang | Timeout default is 30s (`EMPIRICA_MCP_TIMEOUT`), or 120s for CASCADE commands. Check `~/.empirica/credentials.yaml` for cortex creds if calling mesh primitives. |
| `practice_context` returns empty | Cortex unreachable — check `~/.empirica/credentials.yaml` `cortex:` block. |
| Mesh proposal `status=failed` | Wrong canonical form on `target_claudes`. Run `mcp__empirica__practice_context` to look up the right `ai_id_mesh`. |

## See also

- `docs/human/developers/CLI_COMMANDS_UNIFIED.md` — CLI reference
- `docs/human/developers/MCP_SERVER_REFERENCE.md` — MCP server reference
- `docs/architecture/EVENT_LISTENER.md` — listener architecture
- `~/.claude/empirica-org-prompt.md` (org-specific) — canonical
  addressing convention + doubled-empirica gotcha
- `empirica-mcp/README.md` — package README + tool surface summary
- `empirica mcp-list-tools` — live registry inspection
- MCP spec: https://modelcontextprotocol.io/
