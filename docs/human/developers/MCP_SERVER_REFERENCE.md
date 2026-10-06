# Empirica MCP Server Reference

**Version:** 1.14.9
**Package:** `empirica-mcp` (source in `empirica-mcp/`, entry point `empirica_mcp.server:run`)
**Architecture:** table-driven CLI wrapper, no middleware
**Tool count:** run `empirica mcp-list-tools`. It reads `TOOL_REGISTRY` from the installed package and prints the live total, split into standalone and cortex-orchestrated tools. This page does not quote a number because the registry changes with the CLI.

---

## Overview

The MCP server exposes Empirica to MCP-capable harnesses (Claude Code, Claude Desktop, Cursor, Gemini CLI, Codex and others). Every tool is a thin mapping from MCP arguments to one `empirica` CLI invocation: the server builds the argv, runs the CLI as a subprocess, and returns its output. There is no epistemic middleware in the server. In Claude Code the Sentinel gates through hooks; on other platforms the discipline is self-enforced. See [MCP_FOR_DESKTOP_HARNESSES.md](../end-users/MCP_FOR_DESKTOP_HARNESSES.md) for harness setup.

Key properties:

- **Transport:** stdio. The server supports both the 1.x and 2.x `mcp` SDK (the handler registration shim is the only SDK-specific code).
- **Single source of truth:** the `empirica` CLI. The server holds no session state.
- **Always JSON:** every call appends `--output json`.
- **No hanging:** non-cascade tools run with `stdin=DEVNULL`; cascade-style tools pass their arguments as JSON on stdin (`empirica <verb> --output json -`).
- **Output cap:** responses over 30000 characters are truncated with a notice.
- **Parity:** `tests/test_cli_parity.py` fails if a mapped flag stops existing on its CLI subcommand.

---

## Setup

### Via `empirica setup-claude-code`

`empirica setup-claude-code` finds or installs `empirica-mcp` (pipx if needed) and registers it in two files with an identical entry: `~/.claude.json` (the file Claude Code loads for user-scope MCP servers) and the legacy `~/.claude/mcp.json`. The entry:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "<path to empirica-mcp>",
      "args": [],
      "type": "stdio",
      "tools": ["*"]
    }
  }
}
```

The write is stamped and retried on concurrent modification because Claude Code rewrites `~/.claude.json` continuously. If the existing file cannot be parsed, setup skips it rather than overwrite it.

### Manual (Claude Desktop and others)

```bash
pipx install empirica-mcp        # or: pip install empirica[mcp]
empirica-mcp --help
```

Point the harness's MCP config at the `empirica-mcp` executable as a stdio server.

### Workspace resolution

Server start, in order: the `--workspace` / `-w` flag; else `EMPIRICA_WORKSPACE_ROOT` if already set; else the git root of the current directory when it contains `.empirica/`.

Per call, the working directory for the CLI subprocess is: the call's `project_path` argument; else `EMPIRICA_WORKSPACE_ROOT`; else the active project from `empirica.utils.session_resolver.get_active_project_path()`; else the server's own directory.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `EMPIRICA_WORKSPACE_ROOT` | auto-detected as above | Project root the CLI runs in |
| `EMPIRICA_MCP_TIMEOUT` | `30` | Timeout in seconds for standard tools |
| `EMPIRICA_MCP_CASCADE_TIMEOUT` | `120` | Timeout for tools that send JSON on stdin (PREFLIGHT, CHECK, POSTFLIGHT, batch artifact tools); POSTFLIGHT runs grounded verification and embedding and needs longer |

If the `empirica` CLI is not on `PATH` (or in `~/.local/bin`, `/usr/local/bin`), every tool returns an error naming the install command.

### A timeout on a submit does not mean it failed

`preflight-submit`, `check-submit` and `postflight-submit` commit their row before the slow retrieval tail. On timeout the server returns a hint to run `empirica status` and look for an open transaction before resubmitting, because resubmitting double-opens.

---

## Tool reference

Tool names map to a CLI verb in `TOOL_REGISTRY`. Rows marked stdin send their arguments as JSON on stdin. This table groups the tools as the registry stands; `empirica mcp-list-tools` is the authority for the exact set.

### Session lifecycle

| Tool | CLI verb |
|---|---|
| `session_create` | `session-create` |
| `project_bootstrap` | `project-bootstrap` |
| `bootstrap_context` | `bootstrap-context` (three-circle artifact graph, for harnesses without hooks) |
| `session_snapshot` | `session-snapshot` |
| `resume_previous_session` | `sessions-resume` |

### Transaction (stdin)

| Tool | CLI verb |
|---|---|
| `submit_preflight_assessment` | `preflight-submit` |
| `submit_check_assessment` | `check-submit` |
| `submit_postflight_assessment` | `postflight-submit` (closes the transaction) |

`vectors` is required; `session_id` is optional because the CLI derives it from the active session. PREFLIGHT and CHECK accept `claims` (`{claim, grounding, ref}` with grounding `read`, `ran`, `retrieved` or `assumed`; only `read` and `ran` certify).

### Artifacts

| Tool | CLI verb |
|---|---|
| `finding_log`, `unknown_log`, `deadend_log`, `mistake_log`, `assumption_log`, `decision_log` | the matching `*-log` verb |
| `note` | `note` |
| `source_add`, `source_list` | `source-add`, `source-list` |
| `log_artifacts` (stdin) | `log-artifacts` |
| `resolve_artifacts` (stdin) | `resolve-artifacts` |
| `update_artifacts` (stdin) | `update-artifacts` |
| `delete_artifacts` (stdin) | `delete-artifacts` |
| `finding_resolve`, `unknown_list`, `unknown_resolve` | `finding-resolve`, `unknown-list`, `unknown-resolve` |
| `epistemics_list`, `epistemics_show` | `epistemics-list`, `epistemics-show` |
| `lesson_create` (stdin), `lesson_list`, `lesson_search` | `lesson-create`, `lesson-list`, `lesson-search` |
| `issue_list`, `issue_resolve` | `issue-list`, `issue-resolve` |

### Goals

`goals_create`, `goals_list`, `goals_complete`, `goals_add_task`, `goals_get_tasks`, `goals_complete_task`, `goals_progress`, `goals_search`, `goals_discover`, `goals_ready`, `goals_activate`, `goals_refresh`, `goals_mark_stale`, `goals_add_dependency`, each calling the `goals-*` verb of the same name.

### Search, memory and calibration

| Tool | CLI verb |
|---|---|
| `project_search`, `project_embed` | `project-search`, `project-embed` |
| `investigate` | `investigate` (retrieval over what the practice knows) |
| `noetic_batch` (stdin) | `noetic-batch` |
| `commit_context` | `commit-context` |
| `calibration_report`, `assess_state`, `profile_status` | `calibration-report`, `assess-state`, `profile-status` |
| `memory_compact`, `efficiency_report` | `memory-compact`, `efficiency-report` |

### Checkpoints, handoff, workspace, sync

| Tool | CLI verb |
|---|---|
| `checkpoint_create`, `checkpoint_load` | `checkpoint-create`, `checkpoint-load` |
| `handoff_create` | `handoff-create` |
| `workspace_overview`, `workspace_map` | `workspace-overview`, `workspace-map` |
| `sync_push`, `sync_status` | `sync-push`, `sync-status` |
| `doctor` | `doctor` |

### Dispatch bus, listener, loops, notify

| Tool | CLI verb |
|---|---|
| `bus_register`, `bus_dispatch`, `bus_instances`, `bus_status` | `bus-register`, `bus-dispatch`, `bus-instances`, `bus-status` |
| `bus_poll` | `message-inbox` |
| `listener_on`, `listener_arm`, `listener_off` | `listener on`, `listener arm`, `listener off` |
| `loop_register`, `loop_heartbeat`, `loop_status`, `loop_schedule_next` | `loop register`, `loop heartbeat`, `loop status`, `loop schedule-next` |
| `notify_emit` | `notify emit` |

### Cortex-orchestrated

These carry a `requires` marker in the registry and `empirica mcp-list-tools` flags them. Without a configured cortex they return a "cortex config missing" error; the rest of the server works standalone.

| Tool | CLI verb | Needs cortex |
|---|---|---|
| `practice_context` | `practice-context` | yes (roster) |
| `mailbox_reply` | `mailbox reply` | yes |
| `listener_on` | `listener on` | for mesh events; runs standalone otherwise |
| `mesh_status` | `mesh status` | for the bridge layer; the local layer is standalone |

### Stateless

`get_empirica_introduction` is registered separately from `TOOL_REGISTRY` and calls no CLI; it returns a short framework description and the list of registry tool names. Because it is outside `TOOL_REGISTRY`, `mcp-list-tools` does not show it.

---

## Architecture

```
MCP client (Claude Desktop, IDE, Claude Code)
    ↓ stdio
empirica-mcp server
    ↓ TOOL_REGISTRY lookup
    ↓ subprocess: empirica <verb...> --output json [flags | -]
empirica CLI (single source of truth)
    ↓
SQLite / git notes / Qdrant
```

Each `TOOL_REGISTRY` entry has:

- `cli`: the CLI verb, possibly several tokens (`loop register`)
- `params`: argument name to flag mapping
- `required`: required arguments
- `desc`: the tool description shown to the model
- optional `stdin_json`, `positional`, `list_params` and `requires`

Tool input schemas are generated from the entry: names in the server's numeric and boolean sets become `number` and `boolean`, and a fixed set of enumerated parameters (`reversibility`, `status`, `severity`, `visibility`, `epistemic_source`, and others) become enums.

---

## Removed

- `EpistemicMiddleware`, `VectorRouter`, `EpistemicStateMachine`, and the `EMPIRICA_EPISTEMIC_MODE` variable were removed in the 1.7.5 rewrite; gating moved to the Sentinel hooks.
- Tools for `refdoc-add` and `monitor` that older revisions of this page listed are not in the registry now.
- The server-lifecycle CLI verbs (`mcp-start`, `mcp-stop`, `mcp-status`, `mcp-test`, `mcp-call`) were removed on 2026-06-03; lifecycle belongs to the harness's MCP config. Only `mcp-list-tools` remains.
