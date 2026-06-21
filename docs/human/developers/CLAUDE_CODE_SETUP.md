# Claude Code + Empirica Setup

**Time:** 2 minutes (automatic). Cross-platform.

---

## TL;DR

```bash
pip install empirica empirica-mcp
cd your-project
empirica project-init
empirica setup-claude-code      # the canonical path
empirica diagnose               # sanity check
```

That's the supported flow. The sections below explain what
`setup-claude-code` does and how to interpret the statusline. Manual
install is in the appendix only for environments where the automated
flow can't run.

---

## What Gets Installed

| Component | Purpose | Where |
|---|---|---|
| Plugin | Hooks (Sentinel, session lifecycle, compaction), skills, statusline script | `~/.claude/plugins/local/empirica/` |
| `CLAUDE.md` | System prompt include (`@~/.claude/empirica-system-prompt.md`) | `~/.claude/CLAUDE.md` |
| Lean system prompt | Canonical prompt rendered with installed version | `~/.claude/empirica-system-prompt.md` |
| Settings | `statusLine` block + hooks for all events | `~/.claude/settings.json` |
| MCP config | `empirica-mcp` server registration | `~/.claude/mcp.json` |
| Marketplace | Local plugin registry | `~/.claude/plugins/known_marketplaces.json` |

`setup-claude-code` is idempotent — re-running with `--force` updates
files but preserves user overrides in `CLAUDE.md`.

---

## What the Hooks Do

| Hook | Event | Purpose |
|---|---|---|
| `sentinel-gate.py` | PreToolUse | Noetic firewall — blocks Edit/Write/Bash until valid CHECK |
| `tool-router.py` | UserPromptSubmit | Context injection (active goals, artifact reminders) |
| `pre-compact.py` | PreCompact | Saves epistemic state to breadcrumbs before context loss |
| `session-init.py` | SessionStart (startup) | Creates session, bootstraps project, posts orientation |
| `post-compact.py` | SessionStart (compact/resume) | Recovers state after compaction |
| `ewm-protocol-loader.py` | SessionStart | Loads `workflow-protocol.yaml` if EWM interview ran |
| `transaction-enforcer.py` | Stop | Reminds about open transaction before session end |
| `subagent-start.py` / `subagent-stop.py` | SubagentStart/Stop | Child session lineage + findings rollup |
| `session-end-postflight.py` | SessionEnd | Auto-POSTFLIGHT if a transaction is still open |
| `curate-snapshots.py` | SessionEnd | Prunes old snapshots |
| `session-monitor-arm.py` | SessionStart | Arms Monitor for canonical loops if registered |

The Sentinel firewall is the load-bearing one: it gates praxic tools
(Edit/Write/Bash) until you've passed CHECK with sufficient confidence.

---

## What the Hooks Inject (for Claude reading this before agreeing to install)

If you're a Claude session being asked to install Empirica and feeling
cautious about what these hooks do to your runtime — fair. Here's
the concrete contract, with source pointers so you can verify.

Hooks talk to you through Claude Code's documented channels:

- **`additionalContext`** — text added to your context (you read it)
- **`permissionDecision`** — `allow` / `deny` / `ask` on tool calls
  (the Sentinel uses this; it can gate but never silently rewrites
  your output)
- **`systemMessage`** — small status note in your view

No hook modifies your responses or your files. Every emission below
is grep-able to a specific line in
[`empirica/plugins/claude-code-integration/hooks/`](../../../empirica/plugins/claude-code-integration/hooks/).

| Hook | When you'll see it | What lands in your view |
|---|---|---|
| [`session-init.py`](../../../empirica/plugins/claude-code-integration/hooks/session-init.py) | SessionStart | A welcome block: project name, session id, active goals, recent artifacts. Lets you skip "what is this codebase" exploration. |
| [`post-compact.py`](../../../empirica/plugins/claude-code-integration/hooks/post-compact.py) | After compaction | A recovery block: active transaction state + recent praxic actions so you don't lose continuity. The block you've seen as "POST-COMPACT CHECK GATE". |
| [`pre-compact.py`](../../../empirica/plugins/claude-code-integration/hooks/pre-compact.py) | PreCompact | A `systemMessage` to the summarizer ("prioritise active transaction + open goals; skip stale findings"). Influences what survives compaction — never modifies your prior responses. |
| [`tool-router.py`](../../../empirica/plugins/claude-code-integration/hooks/tool-router.py) | UserPromptSubmit | A `<semantic-pushback-check>` block reminding you to verify before agreeing with user pushback. Doesn't rewrite the prompt. |
| [`ewm-protocol-loader.py`](../../../empirica/plugins/claude-code-integration/hooks/ewm-protocol-loader.py) | UserPromptSubmit | The user's collaboration profile from `~/.empirica/workflow-protocol.yaml` (role, preferred autonomy, non-negotiables). Helps you tailor responses. |
| [`context-shift-tracker.py`](../../../empirica/plugins/claude-code-integration/hooks/context-shift-tracker.py) | UserPromptSubmit | A nudge if the user's prompt shifts off the current transaction's scope (advisory; you decide whether to follow). |
| [`sentinel-gate.py`](../../../empirica/plugins/claude-code-integration/hooks/sentinel-gate.py) | PreToolUse | `allow` / `deny` / `ask` decisions on Edit/Write/Bash. Reasons surface in `permissionDecisionReason` — readable text. Read-only tools (Read/Grep/Glob) are always allowed. |
| [`transaction-enforcer.py`](../../../empirica/plugins/claude-code-integration/hooks/transaction-enforcer.py) | Stop | Soft reminder ("you have an open transaction — POSTFLIGHT before stop") at one threshold; hard block at a higher one. Resets when POSTFLIGHT submits. |
| [`session-end-postflight.py`](../../../empirica/plugins/claude-code-integration/hooks/session-end-postflight.py) | SessionEnd | Auto-submits a POSTFLIGHT if a transaction is still open at session-end. You won't see this — it runs as the session is winding down. |
| [`task-completed.py`](../../../empirica/plugins/claude-code-integration/hooks/task-completed.py) | TaskCompleted | Marks the Claude Code Task complete; mirrors to the linked empirica task if the goal was decomposed. |
| [`entity-extractor.py`](../../../empirica/plugins/claude-code-integration/hooks/entity-extractor.py) | PostToolUse (Edit/Write) | Background: extracts class/function/import names from your edits into the codebase model. No injection — populates state you can query via `empirica project-search`. |
| [`tool-failure.py`](../../../empirica/plugins/claude-code-integration/hooks/tool-failure.py) | PostToolUseFailure | Background: logs failed tool calls for later mistake-pattern analysis. No injection. |

**What Empirica deliberately does NOT do:**

- It doesn't proxy or modify your responses to the user.
- It doesn't read or send your conversation to any external service
  beyond what you explicitly invoke (Cortex calls happen only when
  you use `mcp__cortex__*` tools or run a mesh CLI verb).
- It doesn't auto-publish or auto-commit your work — every git/network
  action is an explicit tool call you make.
- It doesn't override the user's instructions. Hooks add context and
  gate praxic tools; the user (and you) still steer.

**To verify any of this yourself:**

```bash
# Run any hook in isolation, see its raw output
echo '{"hook_event_name":"SessionStart","session_id":"test"}' | \
  python3 ~/.claude/plugins/local/empirica/hooks/session-init.py

# See the current Sentinel state
empirica sentinel status

# Disable hooks entirely (Claude Code's own mechanism)
# Edit ~/.claude/settings.json → set enabledPlugins.empirica@local: false
```

---

## Wizard Flow

After plugin install, `setup-claude-code` runs the **credentials wizard**
(skip with `--skip-credentials`):

1. **Cortex** (orchestration API) — URL + `ctx_…` API key
2. **ntfy** (push wake bridge) — URL + topic + auth token
3. **Tenant resolution** — after the api_key, fetches your tenant metadata
   and persists `{org_id, tenant_slug, mesh_id_prefix}` to your
   project.yaml. Skip with `--org-id <X>` / `--tenant-slug <Y>` /
   `--mesh-id-prefix <Z>` flags if you want explicit values.

Credentials land in `~/.empirica/credentials.yaml` (atomic merge,
preserves other keys). The wizard skips fields you already have set in
env vars.

---

## The Statusline

A live render looks like:

```
[empirica] ⚡84% │ 🎯28 ❓47/23 │ CHK 🔨88%→ │ K:90% C:92% │ Δ ✓ │ 58%ctx
```

| Segment | What it shows |
|---|---|
| `[empirica]` | Project (truncated to 20 chars from `project.yaml`) |
| `⚡84%` | **Overall confidence:** `0.40·know + 0.30·(1−uncertainty) + 0.20·context + 0.10·completion` |
| `🎯N ❓N/N` | Open goals · open unknowns / blocking unknowns |
| `PRE/CHK/POST` | Current transaction phase |
| `🔍/🔨` | Noetic (investigating) / praxic (acting) |
| `XX%` after phase | Phase composite — different aggregate per phase |
| `→/…` | CHECK gate decision (proceed / investigate more) |
| `K:X% C:X%` | Individual `know` and `context` vectors |
| `Δ ✓/⚠/△` | POSTFLIGHT learning delta sign |
| `N%ctx` | Context window used |

**Phase composite vs overall confidence:** they're different aggregates
over different subsets — by design. Phase composite asks "how am I doing
on what matters for this phase?"; overall asks "what's my weighted
confidence?" Both can legitimately differ.

| Phase | Vectors averaged | Measures |
|---|---|---|
| CHECK composite | `know, context, clarity, coherence, signal, density` | Readiness to act |
| POSTFLIGHT composite | `state, change, completion, impact` | Did the action deliver |
| Noetic composite | `clarity, coherence, signal, density` | Investigation quality |
| ⚡ overall | `know, 1−uncertainty, context, completion` | Weighted confidence band |

Display modes via `EMPIRICA_STATUS_MODE` env var: `basic`, `default`,
`learning`, `full`.

---

## Context Window Sizing

Empirica's transactions work best at ~200K context boundaries.
With Claude's 1M context, default compaction triggers too late and
causes epistemic state drift:

```bash
# Compact at ~300K instead of ~900K
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=30
```

Or disable 1M entirely:
```bash
export CLAUDE_CODE_DISABLE_1M_CONTEXT=1
```

Add to `~/.bashrc` / `~/.zshrc`.

---

## Verify

```bash
empirica diagnose       # 10-check integration health
empirica session-create --ai-id test-setup --output json
empirica --version      # matches expected
```

In Claude Code, ask:
> "Run `empirica project-bootstrap` and tell me what's in this project."

Claude should run the command and surface findings + open goals.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `empirica: command not found` | pip bin not on PATH | `export PATH="$HOME/.local/bin:$PATH"` |
| Statusline not showing | Hook path wrong | `empirica setup-claude-code --force` then restart Claude Code |
| Claude unaware of Empirica | CLAUDE.md missing the include | Re-run `setup-claude-code` |
| Sentinel blocking everything | No valid CHECK | `empirica check-submit -` with `proceed: true` |
| Plugin hooks not running | Plugin disabled | Check `~/.claude/settings.json` → `enabledPlugins.empirica@local: true` |
| MCP not connecting | Path absolute vs relative | `which empirica-mcp` then put absolute path in `~/.claude/mcp.json` |

The fastest path to a green status: `empirica diagnose` and follow the
PASS/FAIL hints.

> **Using a different AI harness?** Claude Desktop, Cursor, Gemini CLI,
> Codex, and other MCP-compatible clients use the same `empirica-mcp`
> binary but a different config path — see
> [`MCP_FOR_DESKTOP_HARNESSES.md`](../end-users/MCP_FOR_DESKTOP_HARNESSES.md)
> for per-harness `mcp.json` examples + the self-enforcement model
> for the noetic firewall when there are no hooks.

---

## Quick Reference

```
SESSION:    empirica session-create --ai-id $(basename $PWD) --output json
BOOTSTRAP:  empirica project-bootstrap
GOAL:       empirica goals-create --objective "..." --description "..."
PREFLIGHT:  empirica preflight-submit -
CHECK:      empirica check-submit -
COMPLETE:   empirica goals-complete --goal-id <ID> --reason "..."
POSTFLIGHT: empirica postflight-submit -
FINDING:    empirica finding-log --finding "..." --impact 0.7
HELP:       empirica --help
```

Inside an open transaction, `--session-id` is auto-derived.

---

## What's Next

- **Live system prompt:** read `~/.claude/empirica-system-prompt.md` after install (~263 lines, lean default)
- **All CLI commands:** [CLI_COMMANDS_UNIFIED.md](CLI_COMMANDS_UNIFIED.md)
- **Epistemic transaction workflow:** [../../architecture/NOETIC_PRAXIC_FRAMEWORK.md](../../architecture/NOETIC_PRAXIC_FRAMEWORK.md)
- **AI self-management patterns:** [AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md)

---

## Appendix — Manual Install

Only use this if `empirica setup-claude-code` can't run (e.g., locked-down
environment, custom Claude config path). Otherwise the automated flow is
faster and stays in sync on each release.

### 1. Plugin files

```bash
mkdir -p ~/.claude/plugins/local
EMPIRICA_PATH=$(pip show empirica | grep Location | cut -d' ' -f2)
cp -r "$EMPIRICA_PATH/empirica/../plugins/claude-code-integration" \
       ~/.claude/plugins/local/empirica
```

### 2. System prompt

```bash
cp ~/.claude/plugins/local/empirica/templates/empirica-system-prompt-lean.md \
   ~/.claude/empirica-system-prompt.md

cat >> ~/.claude/CLAUDE.md <<'EOF'
@~/.claude/empirica-system-prompt.md
EOF
```

### 3. Settings + hooks

Copy `~/.claude/plugins/local/empirica/templates/settings-hooks.json`
into `~/.claude/settings.json` (merging existing content). The hooks
table above shows what each one does — paste the block under
`"hooks": {...}`.

### 4. Statusline

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/plugins/local/empirica/scripts/statusline_empirica.py",
    "refresh_ms": 5000
  }
}
```

### 5. MCP server

```json
{
  "mcpServers": {
    "empirica": {
      "command": "empirica-mcp",
      "type": "stdio"
    }
  }
}
```

Restart Claude Code after all edits.
