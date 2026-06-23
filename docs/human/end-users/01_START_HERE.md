# 🚀 Start Here — Empirica Quick Start

**Welcome to Empirica.** This gets you running in 5 minutes.

---

## What is Empirica?

Empirica is a **measurement layer for AI work**. It tracks what an AI
agent knows, what it's learning, what it's failed at, and whether its
beliefs match observable outcomes — so you can trust the AI more over
time, not less.

The core mechanic:

- **Inspect internal state** — query knowledge, capabilities, and access
- **Make predictions** — assess what you know *before* you start (PREFLIGHT)
- **Validate through outcomes** — compare predictions to reality (POSTFLIGHT)
- **Improve calibration** — grounded observations from deterministic services
  (tests, git, code-quality) surface divergence between belief and outcome

**Core principle:** evidence-based assessment, not pattern matching.

---

## Quick Start (3 Steps)

### Step 1: Install (2 minutes)

```bash
# PyPI (recommended)
pip install empirica

# Or from source
git clone https://github.com/EmpiricaAI/empirica.git
cd empirica && pip install -e .

# Verify
empirica --version
```

### Step 2: Initialize + Wire Up Claude Code

```bash
cd your-project              # any git repo
empirica project-init        # creates .empirica/ — required for every command
empirica setup-claude-code   # Claude Code users only: plugin + statusline + hooks
empirica diagnose            # sanity check — tells you if anything's off
```

`project-init` is the step new users miss most. Without it, every CLI
command fails with `Cannot determine sessions.db path`. Without
`setup-claude-code`, the Empirica statusline + Sentinel hooks won't
show up inside Claude Code. The `diagnose` command walks both checks.

### Step 3: First Transaction (10 minutes)

```bash
# Create a session
empirica session-create --ai-id $(basename $PWD) --output json

# PREFLIGHT — open a measurement window
empirica preflight-submit - << 'EOF'
{
  "task_context": "Your task description",
  "vectors": {"know": 0.5, "uncertainty": 0.5, "context": 0.6},
  "reasoning": "Honest baseline — what you actually know right now"
}
EOF

# Investigate, then log
empirica finding-log --finding "What you learned" --impact 0.7
empirica unknown-log --unknown "What's still unclear"

# POSTFLIGHT — close the window
empirica postflight-submit - << 'EOF'
{
  "vectors": {"know": 0.8, "uncertainty": 0.2, "context": 0.9, "completion": 1.0},
  "reasoning": "Compare to PREFLIGHT — this is your learning delta"
}
EOF
```

Session continuity is handled automatically by Claude Code hooks
(PreCompact saves state, PostCompact recovers it). For multi-AI or
multi-machine handoff, use `empirica handoff-create`.

---

## Interactive Onboarding (Optional)

If you'd rather learn by doing:

```bash
# AI agents run this directly — 6-phase experiential walkthrough
empirica onboard --ai-id $(basename $PWD)
```

This is **not a tutorial**. It runs you through a real measurement
cycle — actual PREFLIGHT, real investigation, genuine POSTFLIGHT
with grounded verification. You'll see your own learning delta
measured live.

For humans: ask an AI agent (Claude in Claude Code, etc.) to run it
through. The agent will walk you through each phase.

---

## How You'll Use Empirica

### 1. CLI (the canonical path)

```bash
empirica preflight-submit -     # JSON via stdin
empirica check-submit -         # gate noetic → praxic
empirica postflight-submit -    # close transaction
```

Best for terminal workflows, scripts, agents with shell access. **This
is the canonical path** — every other interface eventually shells out
to the CLI.

### 2. MCP Server (for GUI clients)

```bash
pip install empirica-mcp
```

```json
{ "mcpServers": { "empirica": { "command": "empirica-mcp" } } }
```

Best for Claude Desktop, Cursor, Windsurf — IDEs that don't shell out
directly. See [MCP_INSTALLATION.md](MCP_INSTALLATION.md).

### 3. Python API (for embedding)

```python
from empirica.cli.command_handlers.workflow_commands import (
    handle_preflight_submit_command,
)
```

Used internally and by integrations. Most users don't need this layer.

---

## Quick Reference: MCP Tool Parameters

When calling Empirica via MCP, parameter names sometimes differ from
CLI flags. Common gotchas:

| Tool | Correct |
|---|---|
| `goals_add_task` | `importance` (not `epistemic_importance`) |
| `goals_complete_task` | `task_id` (not `task_id`) |
| Goal scope | `{"breadth": 0.3, "duration": 0.2, "coordination": 0.1}` |
| Success criteria | array of objects with `description` + `threshold` |
| Vector JSON | flat at root of `vectors`, no tier sub-objects |

Use the MCP client's autocomplete or `empirica mcp-list-tools` to see
exact schemas.

---

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `Cannot determine sessions.db path` | No `.empirica/` | `empirica project-init` |
| `No active transaction` | Missing PREFLIGHT | `empirica preflight-submit -` |
| `No valid CHECK found` | Sentinel blocking praxic | `empirica check-submit -` with `proceed: true` |
| `Statusline not showing` | Hook gap | `empirica setup-claude-code --force` then restart Claude Code |
| `Vector 'know' must be between 0.0 and 1.0` | Nested tier object | Flatten to `vectors: {know: 0.7, ...}` |

For more: [03_TROUBLESHOOTING.md](03_TROUBLESHOOTING.md).

---

## Next Steps

### For AI Agents

- `empirica onboard` — interactive epistemic-tracking practice
- `empirica setup-claude-code` — full Claude Code integration

### For Users

- **[02_INSTALLATION.md](02_INSTALLATION.md)** — install options
- **[04_QUICKSTART_CLI.md](04_QUICKSTART_CLI.md)** — CLI patterns
- **[05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)** — the 13 vectors
- **[EMPIRICA_EXPLAINED_SIMPLE.md](EMPIRICA_EXPLAINED_SIMPLE.md)** — plain-English overview

### For Teams

- **[empirica-workspace](https://github.com/Nubaeon/empirica-workspace)** — cross-project calibration, multi-entity pattern matching, TUI analytics

---

## The Foundation Five

For most tasks, focus on these five vectors (the full 13 are in
[05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)):

| Vector | Meaning | Good signal |
|---|---|---|
| **know** | Domain understanding | 0.8+ = expert, <0.5 = novice |
| **do** | Execution capability | 0.8+ = confident, <0.5 = need help |
| **context** | Situational awareness | 0.8+ = mapped, <0.5 = risky |
| **engagement** | Focused on the task | <0.6 = other vectors questionable |
| **uncertainty** | Explicit unknowns (inverted: higher = more uncertain) | >0.8 = investigate, don't act |

**High uncertainty is good** when appropriate — it's the signal the
Sentinel uses to decide whether to require more investigation.
Hiding it produces silent divergence later.

---

## Core Principles

✅ **NO HEURISTICS** — genuine self-assessment, no pattern matching
✅ **BE HONEST** — acknowledge what you don't know
✅ **TRACK LEARNING** — PREFLIGHT → POSTFLIGHT delta = trajectory
✅ **VALIDATE CALIBRATION** — divergence between belief and observation = discipline signal

---

**Ready?** `empirica project-init` then `empirica setup-claude-code` then `empirica onboard`. 🚀
