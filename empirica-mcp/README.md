# Empirica MCP Server

**AI measurement and calibration tools via Model Context Protocol.**

Exposes Empirica's tool surface to Claude Desktop, IDEs, and any
MCP-compatible environment (Cursor, Gemini CLI, Codex, etc.). Track
what AI knows, gate what it does, and compound learning across
sessions — without needing Claude Code or Bash access.

> **Tool surface (70 tools as of 1.11.2)** covers session lifecycle,
> the epistemic transaction loop, artifact logging, goals, project
> search, calibration, lessons, sync, entity registry, and (added
> 2026-06-03) the mesh primitives: `practice_context` (Ambassador
> addressbook), `commit_context` (temporal trail), `listener_on/arm/off`
> (listener facade), `loop_*` (adaptive scheduler), `notify_emit`
> (multi-backend dispatcher), `mailbox_reply` (atomic propose+complete),
> `mesh_status` (mesh health). Run `empirica mcp-list-tools` to see
> the live registry against your installed package.

[![PyPI](https://img.shields.io/pypi/v/empirica-mcp)](https://pypi.org/project/empirica-mcp/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

---

## Installation

```bash
pip install empirica-mcp
```

**Note:** The MCP server requires the full Empirica package for stateful operations:

```bash
pip install empirica  # Recommended - includes empirica-mcp
```

### Verify Installation

```bash
empirica --version      # CLI
empirica-mcp --help     # MCP server
```

---

## Quick Start

### 1. Standard Mode

```bash
empirica-mcp
```

Works as a standard MCP tool provider. No epistemic layer.

### 2. Epistemic Mode

```bash
export EMPIRICA_EPISTEMIC_MODE=true
empirica-mcp
```

Every tool call now includes epistemic self-awareness - the server maintains vector state and routes behavior based on confidence/uncertainty.

### 3. Personality Profiles

```bash
# Cautious (investigates early)
export EMPIRICA_PERSONALITY=cautious_researcher

# Pragmatic (action-oriented)
export EMPIRICA_PERSONALITY=pragmatic_implementer

# Balanced (default)
export EMPIRICA_PERSONALITY=balanced_architect

# Adaptive (learns over time)
export EMPIRICA_PERSONALITY=adaptive_learner
```

---

## Claude Desktop Configuration

### Standard Mode

```json
{
  "mcpServers": {
    "empirica": {
      "command": "empirica-mcp"
    }
  }
}
```

### Epistemic Mode

```json
{
  "mcpServers": {
    "empirica-epistemic": {
      "command": "bash",
      "args": [
        "-c",
        "EMPIRICA_EPISTEMIC_MODE=true EMPIRICA_PERSONALITY=balanced_architect empirica-mcp"
      ]
    }
  }
}
```

After editing config, restart Claude Desktop completely.

---

## Available Tools

The MCP server exposes 100+ Empirica CLI commands as MCP tools:

**Session Management:**
- `session_create` - Create new session
- `session_list` - List sessions
- `session_show` - Show session details

**CASCADE Workflow:**
- `preflight_submit` - Submit PREFLIGHT assessment
- `check_submit` - Execute CHECK gate
- `postflight_submit` - Submit POSTFLIGHT assessment

**Goals & Findings:**
- `goals_create` - Create goals
- `goals_list` - List goals
- `finding_log` - Log findings
- `unknown_log` - Log unknowns

**And many more...**

---

## Epistemic Responses

### Standard Response

```json
{
  "ok": true,
  "session_id": "abc123",
  "message": "Session created"
}
```

### Epistemic Response

```json
{
  "ok": true,
  "session_id": "abc123",
  "message": "Session created",

  "epistemic_state": {
    "vectors": {
      "know": 0.60,
      "uncertainty": 0.40,
      "context": 0.70,
      "clarity": 0.85
    },
    "routing": {
      "mode": "confident_implementation",
      "confidence": 0.85,
      "reasoning": "Know=0.60 >= 0.6, Uncertainty=0.40 < 0.5"
    }
  }
}
```

---

## Behavioral Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **clarify** | clarity < 0.6 | Ask questions before proceeding |
| **load_context** | context < 0.5 | Load project data first |
| **investigate** | uncertainty > 0.6 | Systematic research |
| **confident_implementation** | know >= 0.7, uncertainty < 0.4 | Direct action |
| **cautious_implementation** | Moderate vectors | Careful, incremental steps |

---

## Troubleshooting

### "empirica CLI not found"

```bash
# Check if empirica is in PATH
which empirica

# If not, install full package
pip install empirica
```

### "Module not found: empirica"

```bash
# Install full package (not just MCP server)
pip install empirica
```

### Claude Desktop not connecting

1. Verify JSON syntax (no trailing commas)
2. Quit Claude Desktop completely
3. Restart Claude Desktop
4. Check logs for errors

---

## Docker

```bash
docker pull nubaeon/empirica:1.6.6
docker run -p 3000:3000 nubaeon/empirica:1.6.6 empirica-mcp
```

---

## Requirements

- Python 3.11+
- empirica >= 1.5.0
- mcp >= 1.0.0

---

## Documentation

- [Empirica Documentation](https://github.com/Nubaeon/empirica/tree/main/docs)
- [MCP Protocol](https://modelcontextprotocol.io)

## License

MIT License - See [Empirica repository](https://github.com/Nubaeon/empirica) for details.
