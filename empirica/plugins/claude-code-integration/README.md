# Empirica Plugin for Claude Code

Epistemic measurement, noetic firewall, and grounded calibration for Claude Code.

This plugin makes Claude Code measurably more reliable by tracking what the AI knows, preventing action before understanding, and calibrating self-assessment against objective evidence.

## Installation

```bash
pip install empirica
empirica setup-claude-code        # First install
empirica setup-claude-code --force # Reset/update (preserves non-Empirica hooks)
```

`--force` replaces existing hooks with Empirica's but preserves hooks from other plugins (Railway, Superpowers, etc.). Use it after upgrading or if the default Claude Code settings are still in place.

---

## What Gets Installed

| Component | Count | Location |
|-----------|-------|----------|
| Hooks | 18 | `~/.claude/plugins/local/empirica/hooks/` |
| Skills | 10 | `~/.claude/plugins/local/empirica/skills/` |
| Commands | 2 | `~/.claude/plugins/local/empirica/commands/` |
| Agents | 9 | `~/.claude/plugins/local/empirica/agents/` |
| Statusline | 1 | `scripts/statusline_empirica.py` |
| System Prompt | 1 | `~/.claude/empirica-system-prompt.md` |

---

## Hooks

Hooks fire automatically on Claude Code events. No manual invocation needed.

### Core Transaction Lifecycle

| Hook | Event | What It Does |
|------|-------|-------------|
| **sentinel-gate** | PreToolUse | Noetic firewall — blocks praxic tools (Edit, Write, Bash) until CHECK returns `proceed`. Classifies tools as noetic/praxic, enforces investigate cool-down, tracks tool call counts for autonomy calibration |
| **session-init** | SessionStart | Auto-creates Empirica session, runs `project-bootstrap`, loads calibration from `.breadcrumbs.yaml`, injects context for new/resumed/post-compact sessions |
| **session-end-postflight** | SessionEnd | Auto-captures POSTFLIGHT if transaction is open, prevents lost measurement data |
| **transaction-enforcer** | Stop | Warns if session ends with open transaction (PREFLIGHT without POSTFLIGHT) |

### Context Preservation

| Hook | Event | What It Does |
|------|-------|-------------|
| **pre-compact** | PreCompact | Captures epistemic state before context compaction — vectors, recent findings, git context, active transaction state |
| **post-compact** | PostCompact | Restores context after compaction — loads bootstrap, calibration, last task, continues open transaction |

### Evidence Collection

| Hook | Event | What It Does |
|------|-------|-------------|
| **tool-router** | PostToolUse | Routes tool results to appropriate handlers — currently drives entity extraction |
| **entity-extractor** | PostToolUse | Extracts code entities (functions, classes, imports) from file edits into the codebase model |
| **context-shift-tracker** | UserPromptSubmit | Classifies user prompts as solicited (AI-asked) vs unsolicited (human-initiated redirect) for calibration |
| **tool-failure** | PostToolUseFailure | Auto-logs dead-ends from tool failures — captures the failed approach and error for future avoidance |
| **curate-snapshots** | SessionEnd | Prunes pre-compact snapshots using importance-weighted algorithm (impact + completion scoring) |

### ENP (Epistemic Network Protocol)

| Hook | Event | What It Does |
|------|-------|-------------|
| **enp-notify** | SessionStart | Surfaces pending ENP notifications (git folder changes detected by cron watcher) |
| **enp-postflight-notify** | PostToolUse | Surfaces new ENP notifications at POSTFLIGHT boundaries — only fires after postflight-submit |

### Subagent Governance

| Hook | Event | What It Does |
|------|-------|-------------|
| **subagent-start** | SubagentStart | Creates linked Empirica session for sub-agents, validates attention budget |
| **subagent-stop** | SubagentStop | Rolls up epistemic findings from sub-agent transcripts to parent session, adds delegated tool call counts |
| **task-completed** | TaskCompleted | Bridges Claude Code tasks to Empirica goals — blocks task completion if transaction is open |

### Integration

| Hook | Event | What It Does |
|------|-------|-------------|
| **ewm-protocol-loader** | SessionStart | Loads user's workflow protocol (`~/.empirica/workflow-protocol.yaml`) for personalized AI collaboration |

---

## Skills

Skills load on demand when the AI detects a relevant situation. Invoke with `/skill-name`.

| Skill | Trigger | What It Does |
|-------|---------|-------------|
| **empirica-constitution** | Routing uncertainty, session start | 12-section governance decision tree — routes situations to the right Empirica mechanism |
| **epistemic-persistence-protocol** | Disagreement, pushback | Calibrated position-holding under pushback — classifies pushback type, selects HOLD/SOFTEN/UPDATE/REFRAME response |
| **epistemic-transaction** | Complex work, planning | Guides task decomposition into measured transactions — PREFLIGHT through POSTFLIGHT |
| **code-audit** | `/code-audit`, quality review | Structured noetic investigation of code quality — runs ruff, radon, pyright, produces Empirica artifacts |
| **code-docs-align** | `/code-docs-align`, doc accuracy | Verifies documentation matches code reality — bridges code-audit and docs-assess |
| **dispatch-agent** | Agent spawning, complex tasks | Enriches agent prompts with Cortex context (dead-ends, findings, anti-patterns) |
| **ewm-interview** | `/ewm-interview`, workflow setup | Interviews users to create personalized AI collaboration protocol (workflow-protocol.yaml) |
| **render** | `/render`, diagram rendering | Generates DiagramSpec JSON for ASCII art diagrams, renders via mdview to SVG |
| **inkscape** | `/inkscape`, image generation | SVG generation with design patterns, Inkscape CLI for rendering and tracing |
| **devto** | `/devto`, content publishing | Dev.to article pipeline — epistemic sources to validated publication |

---

## Commands

Slash commands available to the user.

| Command | What It Does |
|---------|-------------|
| `/empirica on\|off\|status` | Toggle epistemic tracking on/off per terminal instance. Controls sentinel enforcement without ending the session |
| `/chrome-health` | Check Chrome MCP connection health and set up monitoring |

---

## Agents

Specialized sub-agents with epistemic profiles and calibrated confidence thresholds.

| Agent | Domain | Type |
|-------|--------|------|
| **architecture** | System design, patterns, modularity | Implementation |
| **security** / **security-expert** | Auth, encryption, vulnerabilities | Implementation |
| **performance** | Optimization, latency, throughput | Implementation |
| **ux** / **ux-specialist** | Usability, accessibility, user flows | Implementation |
| **outreach-scout** | Topic identification, quick assessment | Investigation |
| **outreach-search** | Semantic search, memory retrieval | Investigation |
| **outreach-factscorer** | Fact verification, confidence scoring | Investigation |

---

## Statusline

Real-time epistemic state in your terminal:

```
[empirica] P:87% U:15% | G:3 | POST K:92% C:88% | delta +K+D
```

Shows: postflight confidence, uncertainty, active goals, grounded calibration scores, and learning deltas.

---

## Compliance Report

Project-wide quality snapshot mapped to regulatory frameworks:

```bash
empirica compliance-report                    # Fast checks only
empirica compliance-report --tests            # Include test suite
empirica compliance-report --dep-audit        # Include CVE scan
empirica compliance-report --security         # Include OWASP scan
empirica compliance-report --output json      # Machine-readable
```

**10 always-on checks:**

| Check | What it measures | EU AI Act |
|-------|-----------------|-----------|
| Lint (ruff) | Code quality | Art. 9 |
| Complexity (C901) | Maintainability | Art. 15(1) |
| Type safety (pyright) | Correctness | Art. 15(1) |
| Tech documentation | Doc coverage (docs-assess) | Art. 11 |
| Discipline trajectory | Process discipline (behavioral) | Art. 17 |
| AI transparency | Git Co-Authored-By attribution | Art. 50 |
| Decision transparency | Rationale coverage | Art. 13 |
| Repo hygiene | License, changelog, secrets | Art. 10 |
| Epistemic audit trail | Transaction + artifact history | Art. 12 |
| Grounded calibration | Self-assessment accuracy | Art. 14 |

**3 optional checks:** tests (pytest, `--tests`), dep audit (pip-audit, `--dep-audit`), OWASP scan (semgrep, `--security`).

**Mapped frameworks:** EU AI Act (10 articles), GDPR (4 articles), ISO/IEC 42001 (10 clauses).

---

## Configuration

### Sentinel Control

```bash
# File-based (dynamic, no restart needed)
echo "false" > ~/.empirica/sentinel_enabled   # Disable sentinel gating
echo "true" > ~/.empirica/sentinel_enabled    # Re-enable

# Or use the command
/empirica off   # Pause tracking for this terminal
/empirica on    # Resume
```

### Lean Core Prompt

81% reduction in always-loaded context. Loads skills on demand:

```bash
empirica setup-claude-code --lean
```

### EWM Protocol

Personalized AI collaboration preferences:

```bash
/ewm-interview   # Interactive interview → generates workflow-protocol.yaml
```

---

## How It Works

```
You: "Fix the auth bug"

1. SessionStart hook → creates session, loads context
2. AI runs PREFLIGHT → baseline vectors
3. AI investigates (reads, searches) → sentinel allows noetic tools
4. AI tries to edit → sentinel BLOCKS (no CHECK yet)
5. AI runs CHECK → sentinel evaluates vectors → "proceed"
6. AI edits, commits → sentinel allows praxic tools
7. AI runs POSTFLIGHT → captures learning delta
8. Post-test collector → gathers objective evidence (git, tests, artifacts)
9. Grounded calibration → compares self-assessment vs evidence
10. Bayesian update → corrects future calibration biases
```

---

## Further Reading

- [CLI Reference](https://github.com/Nubaeon/empirica/blob/main/docs/human/developers/CLI_COMMANDS_UNIFIED.md)
- [Architecture](https://github.com/Nubaeon/empirica/tree/main/docs/architecture/)
- [Training & Guides](https://getempirica.com)
- [Upgrade Guide](https://github.com/Nubaeon/empirica/blob/main/docs/guides/UPGRADE_TO_1.9.md)
