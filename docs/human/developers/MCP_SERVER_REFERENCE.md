# Empirica MCP Server Reference

**Last Updated:** 2026-05-18
**Version:** 1.12.16
**Total Tools:** 44
**Architecture:** Table-driven CLI wrapper (no middleware)

---

## Overview

The Empirica MCP server exposes Empirica functionality through MCP (Model Context Protocol) for AI assistants in Claude Desktop, IDEs, and other MCP-compatible environments.

**Architecture:** Single `TOOL_REGISTRY` dict maps tool names to CLI commands. No epistemic middleware — gating is handled by the Sentinel via hooks in Claude Code, or self-enforced on other platforms.

**Key Properties:**
- **Package:** `empirica-mcp` (PyPI)
- **Command:** `empirica-mcp`
- **Transport:** stdio
- **Timeout:** 30s per command (configurable via `EMPIRICA_MCP_TIMEOUT`)
- **No hanging:** CASCADE commands use stdin JSON, others use `stdin=DEVNULL`

---

## Setup

### Via `empirica setup-claude-code`

The setup command auto-configures MCP in `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "empirica-mcp",
      "args": [],
      "type": "stdio",
      "tools": ["*"]
    }
  }
}
```

### Manual (Claude Desktop / other environments)

```bash
pip install empirica-mcp
```

Configure your MCP client to run `empirica-mcp` as a stdio server.

### Workspace Resolution

The server auto-detects the project workspace:
1. `--workspace` CLI flag
2. `EMPIRICA_WORKSPACE_ROOT` env var
3. Git repo root (if `.empirica/` exists)
4. Common paths (`~/empirical-ai/empirica`, CWD)

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EMPIRICA_WORKSPACE_ROOT` | auto-detect | Project workspace root |
| `EMPIRICA_MCP_TIMEOUT` | `30` | CLI command timeout in seconds |

---

## Tool Reference (44 tools)

### Session Lifecycle

| Tool | CLI Command | Description |
|------|------------|-------------|
| `session_create` | `session-create` | Create new Empirica session |
| `project_bootstrap` | `project-bootstrap` | Load project context (findings, goals, unknowns, calibration) |
| `session_snapshot` | `session-snapshot` | Create snapshot of current session state |
| `resume_previous_session` | `sessions-resume` | Resume a previous session |

### CASCADE Workflow

These tools send full JSON via stdin to the CLI (no hanging).

| Tool | CLI Command | Description |
|------|------------|-------------|
| `submit_preflight_assessment` | `preflight-submit` | Submit PREFLIGHT self-assessment (13 vectors) |
| `submit_check_assessment` | `check-submit` | Submit CHECK gate assessment |
| `submit_postflight_assessment` | `postflight-submit` | Submit POSTFLIGHT — closes transaction |

### Noetic Artifacts

| Tool | CLI Command | Description |
|------|------------|-------------|
| `finding_log` | `finding-log` | Log a finding (what was learned) |
| `unknown_log` | `unknown-log` | Log an unknown (what needs investigation) |
| `deadend_log` | `deadend-log` | Log a dead-end (approach that didn't work) |
| `mistake_log` | `mistake-log` | Log a mistake (error to avoid) |
| `assumption_log` | `assumption-log` | Log an unverified assumption |
| `decision_log` | `decision-log` | Log a decision with rationale |
| `source_add` | `source-add` | Add an epistemic source reference |

### Goals

| Tool | CLI Command | Description |
|------|------------|-------------|
| `goals_create` | `goals-create` | Create a new goal |
| `goals_list` | `goals-list` | List goals |
| `goals_complete` | `goals-complete` | Mark a goal as complete |
| `goals_add_task` | `goals-add-task` | Add a task to a goal |
| `goals_complete_task` | `goals-complete-task` | Mark a task as complete |
| `goals_progress` | `goals-progress` | Get goal progress details |
| `goals_search` | `goals-search` | Search goals by text |
| `goals_ready` | `goals-ready` | List goals ready for work |

### Unknowns

| Tool | CLI Command | Description |
|------|------------|-------------|
| `unknown_list` | `unknown-list` | List unknowns |
| `unknown_resolve` | `unknown-resolve` | Resolve an unknown |

### Search & Memory

| Tool | CLI Command | Description |
|------|------------|-------------|
| `project_search` | `project-search` | Semantic search over project knowledge (Qdrant) |
| `project_embed` | `project-embed` | Embed project artifacts to Qdrant |

### Calibration & State

| Tool | CLI Command | Description |
|------|------------|-------------|
| `calibration_report` | `calibration-report` | Get calibration report |
| `assess_state` | `assess-state` | Get current epistemic state |
| `profile_status` | `profile-status` | Show artifact counts and calibration summary |

### Lessons

| Tool | CLI Command | Description |
|------|------------|-------------|
| `lesson_create` | `lesson-create` | Create a reusable lesson |
| `lesson_list` | `lesson-list` | List available lessons |
| `lesson_search` | `lesson-search` | Search lessons by text |

### Issues

| Tool | CLI Command | Description |
|------|------------|-------------|
| `issue_list` | `issue-list` | List auto-captured issues |
| `issue_resolve` | `issue-resolve` | Resolve an issue |

### Investigation & Handoff

| Tool | CLI Command | Description |
|------|------------|-------------|
| `investigate` | `investigate` | Run structured investigation |
| `handoff_create` | `handoff-create` | Create handoff report |

### Workspace

| Tool | CLI Command | Description |
|------|------------|-------------|
| `workspace_overview` | `workspace-overview` | Show workspace overview |
| `workspace_map` | `workspace-map` | Show knowledge map across projects |

### Utilities

| Tool | CLI Command | Description |
|------|------------|-------------|
| `checkpoint_create` | `checkpoint-create` | Create git checkpoint with epistemic metadata |
| `checkpoint_load` | `checkpoint-load` | Load a checkpoint |
| `refdoc_add` | `refdoc-add` | Register a reference document |
| `memory_compact` | `memory-compact` | Compact session memory |
| `efficiency_report` | `efficiency-report` | Generate efficiency report |
| `monitor` | `monitor` | Session monitoring dashboard |

### Stateless

| Tool | Description |
|------|-------------|
| `get_empirica_introduction` | Get framework introduction (no CLI call) |

---

## Architecture

```
MCP Client (Claude Desktop, IDE)
    ↓ stdio
empirica-mcp server
    ↓ TOOL_REGISTRY lookup
    ↓ subprocess (stdin=DEVNULL or stdin_json)
empirica CLI (single source of truth)
    ↓
SQLite / Qdrant / Git
```

The `TOOL_REGISTRY` is a Python dict mapping each tool name to:
- `cli`: The CLI command to run
- `params`: Parameter-to-flag mapping
- `required`: Required parameters
- `stdin_json`: Whether to pipe arguments as JSON via stdin (CASCADE tools)

All tools include `--output json` automatically.

---

## Removed in 1.7.5 (MCP server rewrite)

The following were removed in the MCP server rewrite:

- **Epistemic middleware** (`EpistemicMiddleware`, `VectorRouter`, `EpistemicStateMachine`) — replaced by Sentinel hooks
- **58 tools** — agent-*, persona-*, vision-*, identity-*, memory-prime/scope/value/report, session-rollup, multi-AI coordination tools. These remain available via the CLI directly.
- **`EMPIRICA_EPISTEMIC_MODE`** env var — no longer has any effect
