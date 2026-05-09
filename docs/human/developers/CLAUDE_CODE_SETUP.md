# Claude Code + Empirica Setup Guide

**Time:** 5 minutes | **Cross-platform** | **Automated or manual**

This guide sets up Empirica for Claude Code users on Linux, macOS, or Windows.

---

## What You're Installing

| Component | Purpose | Location |
|-----------|---------|----------|
| `empirica` | CLI + Python library | pip package |
| `empirica-mcp` | MCP server for Claude Code | pip package |
| Claude Code plugin | Noetic firewall + epistemic transaction workflow | `~/.claude/plugins/local/` |
| System prompt | Teaches Claude how to use Empirica | `~/.claude/CLAUDE.md` |
| Statusline | Real-time epistemic status display | Plugin scripts/ |
| MCP config | MCP server configuration | `~/.claude/mcp.json` |

The plugin (v1.7.1) now bundles everything in one package:
- **Sentinel gate** - Noetic firewall that gates praxic tools until CHECK passes
- **Session hooks** - Auto-creates sessions, bootstraps projects, captures POSTFLIGHT
- **Statusline script** - Shows epistemic state in terminal
- **Templates** - CLAUDE.md, mcp.json, settings snippets

---

## Quick Install (Recommended)

Run the interactive installer from the Empirica repository:

```bash
# Clone or navigate to Empirica repo
git clone https://github.com/Nubaeon/empirica.git
cd empirica

# Run installer
python scripts/install.py
```

The installer will:
- Install the Empirica package if needed
- Ask about autopilot, auto-postflight, sentinel looping preferences
- Configure Qdrant URL (for semantic search)
- Set up Ollama embeddings (recommends `qwen3-embedding`)
- Install the Claude Code plugin and skill
- Update your shell profile with environment variables

**Non-interactive mode** (use defaults):
```bash
python scripts/install.py --non-interactive
```

---

## Manual Installation

If you prefer manual setup or the installer doesn't work:

### Step 1: Install Package

```bash
pip install empirica

pip install empirica-mcp
```

Verify:
```bash
empirica --version
# Should show: 1.7.1 (or later)
```

---

## Step 2: Add System Prompt

The full system prompt teaches Claude how to use Empirica with calibration data, memory commands, and workflow guidance.

**Option A: Copy from plugin (recommended after Step 4):**
```bash
cp ~/.claude/plugins/local/empirica/templates/CLAUDE.md ~/.claude/CLAUDE.md
```

**Option B: Copy from source:**
```bash
# If you have the Empirica repo cloned
cp /path/to/empirica/docs/human/developers/system-prompts/CLAUDE.md ~/.claude/CLAUDE.md
```

**Option C: Manual copy:**

The authoritative system prompt is maintained at:
- **Repo:** `docs/human/developers/system-prompts/CLAUDE.md`
- **Plugin:** `templates/CLAUDE.md` (synced copy)

Copy the full contents of that file to `~/.claude/CLAUDE.md`.

**What the system prompt includes:**
- Calibration data (3,194 observations, bias corrections per vector)
- Epistemic transaction workflow (PREFLIGHT → CHECK → POSTFLIGHT → POST-TEST)
- Core commands with correct flags
- Memory commands (Qdrant integration)
- Cognitive immune system (lessons decay)
- Proactive behaviors (pattern recognition, goal hygiene)
- Epistemic-first task structure

**Quick reference (subset):**
```bash
# Session lifecycle
empirica session-create --ai-id claude-code --output json
empirica project-bootstrap --session-id <ID> --output json

# Transaction phases
empirica preflight-submit -     # Baseline (JSON stdin)
empirica check-submit -         # Gate (JSON stdin)
empirica postflight-submit -    # Learning delta (JSON stdin)

# Breadcrumbs
empirica finding-log --finding "..." --impact 0.7
empirica unknown-log --unknown "..."
empirica deadend-log --approach "..." --why-failed "..."
```

**Readiness gate:** Sentinel computes thresholds dynamically from calibration data.

---

## Step 3: Add Statusline (Recommended)

The statusline shows real-time epistemic status in your Claude Code terminal.

Add to `~/.claude/settings.json` (after installing the plugin in Step 4):
```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/plugins/local/empirica/scripts/statusline_empirica.py",
    "refresh_ms": 5000
  }
}
```

Or use the template from the plugin:
```bash
cat ~/.claude/plugins/local/empirica/templates/settings-statusline.json
# Merge this into your settings.json
```

**Display modes** (set via `EMPIRICA_STATUS_MODE` env var):
- `basic`: Just confidence + phase
- `default`: Full status with vectors (recommended)
- `learning`: Focus on vector changes
- `full`: Everything with raw values

### Reading the statusline

A live render at CHECK looks like this:

```
[empirica] ⚡80% │ 🎯28 ❓47/23 │ CHK 🔨88%→ │ 🔎87% │ K:90% C:92% │ Δ ✓ │ 58%ctx
```

Each segment answers a different question:

| Segment | Question answered | Formula |
|---|---|---|
| `[empirica]` | Which project? | `project.yaml display_name` (truncated to 20) |
| `⚡84%` | **Overall confidence** | `0.40·know + 0.30·(1−uncertainty) + 0.20·context + 0.10·completion` |
| `🎯N ❓N/N` | What's still open? | open goals · open unknowns / blocking unknowns |
| `PRE / CHK / POST` | Where am I in the transaction? | `PREFLIGHT` / `CHECK` / `POSTFLIGHT` abbrev |
| `🔍 / 🔨` | Investigating or acting? | `🔍` noetic, `🔨` praxic (work_phase) |
| `XX%` after phase emoji | **Phase composite** — see breakdown below | per-phase formula |
| `→ / …` | CHECK gate decision | `→` proceed (praxic next) · `…` investigate more |
| `🔎XX%` or `🔎XX%·` | **External-grounding share** of confidence | `(search + 0.5·mixed) / (intuition + search + mixed)`. Trailing gray `·` = transaction-scoped (shifts visibly per `*-log --epistemic-source` call across the cascade). No marker = project rolling-window fallback when the transaction has no tagged artifacts yet. |
| `K:X% C:X%` | Individual `know` and `context` vectors | raw vector values, color-coded by tier |
| `Δ ✓ / ⚠ / △` | Learning delta sign at POSTFLIGHT | net positive / negative / neutral |
| `N%ctx` | Context window used | from Claude Code's stdin context block |

**How phase composite (`PRE/CHK/POST XX%`) relates to overall confidence (`⚡XX%`)**

They are *different aggregates over different vector subsets* by design. The phase composite asks "given what this phase is about, how am I doing on the vectors that matter for it?" The overall confidence asks "how confident is the AI overall, weighted by the four most-load-bearing vectors?"

| Phase | Vectors averaged | What it measures |
|---|---|---|
| **CHECK** composite | `know, context, clarity, coherence, signal, density` | Readiness to act |
| **POSTFLIGHT** composite | `state, change, completion, impact` | Did the action deliver |
| **noetic** composite | `clarity, coherence, signal, density` | Investigation quality |
| **⚡ overall** | `know, 1−uncertainty, context, completion` | Weighted confidence band |

So at POSTFLIGHT you can legitimately see `K=95% C=95%` (high know/context — what you investigated landed solidly) yet `POST 🔨70%` (post composite around 70% because state/change/completion/impact averaged to that). Both numbers are correct on their own terms — they answer different questions.

**How `🔎XX%` relates to `⚡XX%`**

`⚡XX%` is the AI's self-reported confidence. `🔎XX%` tells you how much of the underlying evidence base is **externally grounded** (reads, searches, observations, sources logged with `--epistemic-source search`) vs **intuition** (training-data assertions logged with `--epistemic-source intuition`). High `⚡` with low `🔎` is a calibration warning: the AI claims confidence but most of what it's logging is from training data, not from this session's investigation.

The trailing `·` marker tells you which scope the score is reading from. With the dot (`🔎50%·`), you're seeing **cascade progression** — only artifacts logged in the active PRE→CHK→POST transaction count, so each new `*-log --epistemic-source` call shifts the score visibly. Without the dot (`🔎67%`), the transaction has no tagged artifacts yet, so the widget falls back to the **project rolling window** (last 20 tagged artifacts across all transactions) — a stable trend that doesn't go dark at PREFLIGHT.

**What's not on the live statusline**

- `↕XX%` Sentinel threshold (know gate) was removed in 1.9.2 — Sentinel-scoped, not actionable mid-tool-call. Available via `empirica sentinel-status` for debug.

---

## Step 4: Install Empirica Plugin (Recommended)

The plugin (v1.7.1) enforces the epistemic transaction workflow and preserves epistemic state automatically.

**What it includes:**
- **Noetic firewall** (`sentinel-gate.py`): Gates praxic tools (Edit/Write/Bash) until CHECK passes
- **Session hooks** (`session-init.py`, `post-compact.py`): Auto-creates session, bootstraps projects, detects git repos
- **POSTFLIGHT capture** (`session-end-postflight.py`): Auto-captures learning at session end
- **Tool router** (`tool-router.py`): Assesses each prompt against epistemic state and recommends tools/agents
- **Transaction enforcer** (`transaction-enforcer.py`): Ensures open transactions get POSTFLIGHT before session ends
- **Subagent lifecycle** (`subagent-start.py`, `subagent-stop.py`): Creates child sessions and rolls up findings from sub-agents
- **EWM protocol** (`ewm-protocol-loader.py`): Loads personalized workflow protocol from `workflow-protocol.yaml`
- **Pre-compact** (`pre-compact.py`): Saves epistemic state to git notes before memory compaction
- **Templates**: CLAUDE.md, mcp.json, statusline config - ready to copy
- **Statusline script**: Real-time epistemic state display

### Option A: Full Plugin (Recommended)

1. **Copy plugin to Claude plugins directory:**
```bash
# Create plugin directory
mkdir -p ~/.claude/plugins/local

# From Empirica source (if cloned)
cp -r /path/to/empirica/plugins/claude-code-integration ~/.claude/plugins/local/empirica

# Or if installed via pip:
EMPIRICA_PATH=$(pip show empirica | grep Location | cut -d' ' -f2)
cp -r "$EMPIRICA_PATH/empirica/../plugins/claude-code-integration" ~/.claude/plugins/local/empirica
```

2. **Copy templates to Claude config:**
```bash
# System prompt
cp ~/.claude/plugins/local/empirica/templates/CLAUDE.md ~/.claude/CLAUDE.md

# MCP server config (merge with existing if you have one)
cp ~/.claude/plugins/local/empirica/templates/mcp.json ~/.claude/mcp.json
```

3. **Register local marketplace** (create `~/.claude/plugins/known_marketplaces.json`):
```json
{
  "local": {
    "source": {
      "source": "directory",
      "path": "~/.claude/plugins/local"
    },
    "installLocation": "~/.claude/plugins/local"
  }
}
```

4. **Add to installed plugins** (`~/.claude/plugins/installed_plugins.json`):
```json
{
  "version": 2,
  "plugins": {
    "empirica@local": [
      {
        "scope": "user",
        "installPath": "~/.claude/plugins/local/empirica",
        "version": "1.7.1",
        "isLocal": true
      }
    ]
  }
}
```

5. **Enable in settings** (`~/.claude/settings.json`):
```json
{
  "enabledPlugins": {
    "empirica@local": true
  }
}
```

6. **Add hooks to settings.json** (CRITICAL for Sentinel firewall):

The Sentinel gate (noetic firewall) requires PreToolUse hooks. Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/sentinel-gate.py", "timeout": 10}]
      },
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/sentinel-gate.py", "timeout": 10}]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/tool-router.py", "timeout": 5}]
      }
    ],
    "PreCompact": [
      {
        "matcher": "auto|manual",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/pre-compact.py", "timeout": 30}]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact|resume",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/post-compact.py", "timeout": 30}]
      },
      {
        "matcher": "startup",
        "hooks": [
          {"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/session-init.py", "timeout": 30},
          {"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/ewm-protocol-loader.py", "timeout": 10, "allowFailure": true}
        ]
      }
    ],
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/transaction-enforcer.py", "timeout": 5, "allowFailure": true}]
      }
    ],
    "SubagentStart": [
      {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/subagent-start.py", "timeout": 10, "allowFailure": true}]
      }
    ],
    "SubagentStop": [
      {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/subagent-stop.py", "timeout": 15, "allowFailure": true}]
      }
    ],
    "SessionEnd": [
      {
        "matcher": ".*",
        "hooks": [
          {"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/session-end-postflight.py", "timeout": 20},
          {"type": "command", "command": "python3 ~/.claude/plugins/local/empirica/hooks/curate-snapshots.py --output json", "timeout": 15, "allowFailure": true}
        ]
      }
    ]
  }
}
```

**Hook pipeline summary:**

| Hook | Event | Purpose |
|------|-------|---------|
| `sentinel-gate.py` | PreToolUse | Gates Edit/Write/Bash until valid CHECK |
| `tool-router.py` | UserPromptSubmit | Routes prompts to appropriate tools/agents based on epistemic state |
| `pre-compact.py` | PreCompact | Saves epistemic state to git notes before compaction |
| `session-init.py` | SessionStart:startup | Auto-creates session, bootstraps project, detects git repo |
| `ewm-protocol-loader.py` | SessionStart:startup | Loads personalized workflow protocol |
| `post-compact.py` | SessionStart:compact | Recovers session state after memory compaction |
| `transaction-enforcer.py` | Stop | Ensures POSTFLIGHT before session ends if transaction is open |
| `subagent-start.py` | SubagentStart | Creates child session with parent lineage |
| `subagent-stop.py` | SubagentStop | Rolls up findings from sub-agent to parent session |
| `session-end-postflight.py` | SessionEnd | Auto-captures POSTFLIGHT and cleans up |
| `curate-snapshots.py` | SessionEnd | Prunes old snapshots to prevent data bloat |

**Note:** Use absolute paths (replace `~` with your actual home directory like `/home/username`).

See `templates/settings-hooks.json` for reference.

7. **Restart Claude Code**

### Option B: Simple Shell Hooks (Lightweight Alternative)

If you prefer minimal setup without the full plugin:

```bash
mkdir -p ~/.claude/hooks
```

**Pre-compact hook** (`~/.claude/hooks/pre-compact.sh`):
```bash
cat > ~/.claude/hooks/pre-compact.sh << 'EOF'
#!/bin/bash
# Empirica pre-compact hook - saves epistemic state before memory compact
empirica session-snapshot "$(empirica sessions-list --output json 2>/dev/null | jq -r '.sessions[0].id // empty')" --output json 2>/dev/null || true
EOF
chmod +x ~/.claude/hooks/pre-compact.sh
```

**Post-compact hook** (`~/.claude/hooks/post-compact.sh`):
```bash
cat > ~/.claude/hooks/post-compact.sh << 'EOF'
#!/bin/bash
# Empirica post-compact hook - reminds Claude to restore context
echo "POST-COMPACT: Run 'empirica project-bootstrap' to restore epistemic context"
EOF
chmod +x ~/.claude/hooks/post-compact.sh
```

---

## Step 5: Configure MCP Server (Optional)

The MCP server gives Claude direct access to Empirica tools.

**Note:** Claude Code users typically don't need the MCP server—the CLI + hooks provide full functionality. The MCP server is primarily for Claude Desktop and Claude.ai integration where hooks aren't available.

**If you used the Quick Install:** `~/.claude/mcp.json` is auto-configured with the correct path.

**Manual configuration:** Edit `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "/home/YOUR_USER/.local/bin/empirica-mcp",
      "args": ["--workspace", "/path/to/your/project"],
      "type": "stdio",
      "env": {
        "EMPIRICA_EPISTEMIC_MODE": "true"
      },
      "tools": ["*"],
      "description": "Empirica epistemic framework"
    }
  }
}
```

### Multi-Project Workspace Configuration (v1.5.0+)

The MCP server needs to know which project's `.empirica/` directory to use. Without this, sessions may be created in the wrong location.

**Option A: Explicit workspace (recommended for multi-project setups):**
```json
{
  "args": ["--workspace", "/home/user/my-project"]
}
```

**Option B: Auto-detection (works if MCP starts from project directory):**
The server will auto-detect from:
1. Git root (if `.empirica/` exists there)
2. Common paths: `~/empirical-ai/empirica`, `~/empirica`

**Option C: Environment variable:**
```json
{
  "env": {
    "EMPIRICA_WORKSPACE_ROOT": "/home/user/my-project"
  }
}
```

**IMPORTANT:** Use the **full absolute path** to `empirica-mcp`. Find it with:
```bash
which empirica-mcp
# Usually: ~/.local/bin/empirica-mcp (pipx) or ~/.local/bin/empirica-mcp (pip --user)
```

**If installed from source**, use the venv path:
```json
{
  "mcpServers": {
    "empirica": {
      "command": "/path/to/empirica/.venv-mcp/bin/empirica-mcp",
      "args": [],
      "type": "stdio",
      "env": {
        "PYTHONPATH": "/path/to/empirica",
        "EMPIRICA_EPISTEMIC_MODE": "true"
      },
      "tools": ["*"]
    }
  }
}
```

**Verify MCP is working** (in Claude Code):
```
/mcp
# Should show: empirica (connected)
```

---

## Step 6: Set Context Window (Recommended)

Empirica's epistemic transaction workflow is designed for ~200K context boundaries. With the
1M context window, compaction triggers too late — causing epistemic state drift
and degraded measurement quality.

```bash
# Add to ~/.bashrc or ~/.zshrc:
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=30
```

This compacts at ~300K of the 1M window, giving room for deep investigation
while keeping epistemic transaction boundaries manageable.

**Alternative:** Disable 1M entirely and stay on 200K:
```bash
export CLAUDE_CODE_DISABLE_1M_CONTEXT=1
```

---

## Step 7: Verify Setup

```bash
# Test CLI
empirica session-create --ai-id test-setup --output json

# Should return JSON with session_id

# Verify statusline (if configured)
python3 /path/to/empirica/scripts/statusline_empirica.py
# Should show: [empirica] ⚡84% ↕70% │ 🎯3 ❓2 │ PRE 🔍65% │ K:90% C:85%
#                                 ↑     ↑
#                           open goals  open unknowns (project-wide)
```

In Claude Code, ask:
> "Do you have access to Empirica? Try running `empirica --help`"

Claude should now know about Empirica from the system prompt.

---

## Troubleshooting

### "empirica: command not found"
```bash
# Add pip bin to PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Claude doesn't know about Empirica
- Check `~/.claude/CLAUDE.md` exists and has content
- Restart Claude Code to reload system prompt

### Statusline not showing
- Check the path to `statusline_empirica.py` is correct
- Verify: `python3 /path/to/empirica/scripts/statusline_empirica.py`
- Check `~/.claude/settings.json` has valid JSON

### Plugin hooks not running
- Verify plugin is enabled: check `~/.claude/settings.json` → `enabledPlugins`
- Check hook logs: `.empirica/ref-docs/pre_summary_*.json`
- Ensure `EMPIRICA_AI_ID` env var matches your session's ai_id

### MCP server not working
```bash
# Verify MCP server is installed
which empirica-mcp

# Check mcp.json config syntax
python3 -c "import json; json.load(open('$HOME/.claude/mcp.json'))" && echo "Valid JSON"

# Test underlying CLI (MCP wraps this)
empirica --version
```
Note: `empirica-mcp` runs as stdio server, not CLI with --help.

---

## What's Next?

- **Full system prompt:** [CLAUDE.md](system-prompts/CLAUDE.md) (179 lines)
- **All CLI commands:** [CLI Reference](CLI_COMMANDS_UNIFIED.md)
- **Epistemic transaction workflow:** [Workflow Guide](../../architecture/NOETIC_PRAXIC_FRAMEWORK.md)

---

## Quick Reference Card

**Transaction-first:** After PREFLIGHT, most commands auto-derive `--session-id` from the active transaction.

```
SESSION:    empirica session-create --ai-id claude-code --output json
BOOTSTRAP:  empirica project-bootstrap --session-id <ID> --output json
GOAL:       empirica goals-create --objective "..."        # session auto-derived after PREFLIGHT
PREFLIGHT:  empirica preflight-submit -
CHECK:      empirica check-submit -
COMPLETE:   empirica goals-complete --goal-id <ID> --reason "..."
POSTFLIGHT: empirica postflight-submit -
FINDING:    empirica finding-log --finding "..." --impact 0.7  # session auto-derived
UNKNOWN:    empirica unknown-log --unknown "..."               # session auto-derived
HELP:       empirica --help
```

---

**Setup complete!** Claude Code now has Empirica integration.
