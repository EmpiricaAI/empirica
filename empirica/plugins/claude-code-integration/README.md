# Empirica Plugin for Claude Code

Epistemic measurement, noetic firewall, and grounded calibration for Claude Code.

This plugin makes Claude Code measurably more reliable by tracking what the AI knows, preventing action before understanding, and calibrating self-assessment against objective evidence.

## Installation

```bash
pip install empirica
empirica setup        # First install
empirica setup --force # Reset/update (preserves non-Empirica hooks)
```

`--force` replaces existing hooks with Empirica's but preserves hooks from other plugins (Railway, Superpowers, etc.). Use it after upgrading or if the default Claude Code settings are still in place.

---

## What Gets Installed

| Component | Count | Location |
|-----------|-------|----------|
| Hooks | 25 registrations (22 scripts) | `~/.claude/plugins/local/empirica/hooks/` |
| Skills | 19 (plus `cortex-mailbox-poll` / `cortex-mailbox-send` when the Cortex bundle is installed) | `~/.claude/plugins/local/empirica/skills/` |
| Commands | 2 | `~/.claude/plugins/local/empirica/commands/` |
| Agents | 4 | `~/.claude/plugins/local/empirica/agents/` |
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
| **post-compact** | SessionStart (`compact`) | Restores context after compaction — loads bootstrap, calibration, last task, continues open transaction |

### Evidence Collection

| Hook | Event | What It Does |
|------|-------|-------------|
| **tool-router** | UserPromptSubmit | Assesses each prompt against the epistemic state, recommends agents and skills, and injects the `<epp-check>` pointer |
| **entity-extractor** | PostToolUse | Extracts code entities (functions, classes, imports) from file edits into the codebase model |
| **context-shift-tracker** | UserPromptSubmit | Classifies user prompts as solicited (AI-asked) vs unsolicited (human-initiated redirect) for calibration |
| **tool-failure** | PostToolUseFailure | Auto-logs dead-ends from tool failures — captures the failed approach and error for future avoidance |
| **curate-snapshots** | SessionEnd | Prunes pre-compact snapshots using importance-weighted algorithm (impact + completion scoring) |

### ENP (Epistemic Network Protocol) — optional

Not registered by `empirica setup`; `empirica enp-setup` prints the registration lines if you want them.

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
| **session-monitor-arm** | SessionStart | Tells the session to arm a Monitor that bridges registered loop fires (systemd timers) into it; silent when no loops are enabled |
| **loop-install-pickup**, **loop-uninstall-pickup**, **listener-install-pickup**, **listener-uninstall-pickup** | UserPromptSubmit | Surface pending loop and listener install/uninstall requests from the cockpit on the next prompt |
| **ruling-shape** | PreToolUse (`AskUserQuestion`) | Reminds the model, without blocking, when a single-select question has no option marked "(Recommended)" |
| **truncation-legibility** | PostToolUse (`Bash`) | Says so when the output the model just read was partial (e.g. `head -N` returned exactly N lines) |

---

## Skills

Skills load on demand when the AI detects a relevant situation. Invoke with `/skill-name`.

| Skill | Trigger | What It Does |
|-------|---------|-------------|
| **empirica-constitution** | Routing uncertainty, session start | Governance decision tree — routes situations to the right Empirica mechanism. Load before first PREFLIGHT in a session |
| **epistemic-transaction** | Complex work, planning | Guides task decomposition into measured transactions — PREFLIGHT through POSTFLIGHT. Load when task spans 3+ files or 2+ goals |
| **epistemic-persistence-protocol** | Disagreement, pushback | Calibrated position-holding under pushback — classifies pushback type, selects HOLD/SOFTEN/UPDATE/REFRAME response |
| **code-audit** | `/code-audit`, quality review | Structured noetic investigation of code quality — runs ruff, radon, pyright, produces Empirica artifacts |
| **code-docs-align** | `/code-docs-align`, doc accuracy | Verifies documentation matches code reality — bridges code-audit and docs-assess |
| **dispatch-agent** | Agent spawning, complex tasks | Enriches agent prompts with Cortex context (dead-ends, findings, anti-patterns) |
| **ewm-interview** | `/ewm-interview`, workflow setup | Interviews users to create personalized AI collaboration protocol (workflow-protocol.yaml) |
| **inbox-listener** | Wake-on-event setup (held HTTP stream registration) | Sister to `loop-cron` for event-driven background work — arms a `Monitor` on a registered listener (load it via ToolSearch first — it is a deferred tool — and re-arm at each 30-minute expiry) |
| **loop-cron** | Recurring background work setup | Wires periodic tasks into the registry. Self-throttles when an empirica transaction is open. The body skill for cron-mode loops |
| **services-auditor** | `/services-auditor`, compliance review | Phase 2 service-tier auditor — invoked from `empirica scan --explain` to hand off compliance findings to the calling AI session |
| **services-audit-cron** | Recurring services audit | Scheduled wrapper for `services-auditor` — fires the audit on a cron interval |
| **render** | `/render`, diagram rendering | Generates DiagramSpec JSON for ASCII art diagrams, renders via mdview to SVG |
| **reporting-discipline** | Before reporting finished work, a correction or a release | Converts finished work into done/next bullets keyed to goal ids; names the ways replies grow back |
| **pre-action-grounding** | A task arrives without its why or a checkable done-condition | Investigates first, asks last, banks the ungrounded residue as assumptions and emits a goal with typed criteria |
| **message-cleanup** | Scheduled (daily) | Loop body that prunes expired git-notes mesh messages |
| **epistemic-gardening** | `/epistemic-gardening`, pre-release | De-weeds the epistemic graph — resolves stale and superseded artifacts, closes answered unknowns, prunes dangling edges |
| **epistemic-editing** | Reviewing a document whose claims must hold up | Grounded review pass over claim classes that fail silently; renders a galley of flags for the author to accept or reject |
| **eat-the-broccoli** | `/eat-the-broccoli`, pre-release audit | Tiered quality sweep — deterministic tooling plus a hunt for failure classes that pass tests and still ship broken |
| **architecture-review** | Reviewing a system architecture | Stress-tests an architecture for bottlenecks, single points of failure, security and cost gaps, ranked by blast radius |

The mesh mailbox skills (`cortex-mailbox-poll`, `cortex-mailbox-send`) ship with the Cortex bundle, not this plugin. See `docs/reference/SKILLS.md`.

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
| **security** | Auth, encryption, vulnerabilities | Implementation |
| **performance** | Optimization, latency, throughput | Implementation |
| **ux** | Usability, accessibility, user flows | Implementation |

---

## Statusline

Real-time epistemic state in your terminal:

```
 empirica  │ CHECK 78% │ G3 U5 A2 F4/D1 │ Δ ✓ │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5
```

One short line: the practice (black on white), the cascade stage (`PRE`, `CHECK`, `POST`, `TEST`) with confidence, open goals, unknowns and
assumptions, findings and decisions logged in this transaction, a learning mark from the grounded calibration of the last closed
transaction (🔥 very good, ✓ good, `-` average, ✗ below average, `…` not graded yet, `?` too little evidence to rate), context used, then `investigate` or `act` and the model.

The detailed view (vectors, phase composite, deltas) is one command away, no restart:

```bash
echo expanded > ~/.empirica/statusline_mode   # detailed
echo compact  > ~/.empirica/statusline_mode   # back to the default
```

See `docs/reference/STATUSLINE_REFERENCE.md`.

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

The file wins over the environment. `EMPIRICA_SENTINEL_LOOPING=false` is consulted only when
`~/.empirica/sentinel_enabled` does not exist, so a file holding `true` silently ignores it. An
unattended runner that must not be gated should run with a scratch `HOME`, or with the plugin off.

Outside a git repo and any empirica project there is nothing to measure, so the Sentinel allows and
says so ("not applicable"); that is not a crash and `EMPIRICA_SENTINEL_FAIL_CLOSED` does not turn it
into a deny.

### Lean Core Prompt

`empirica setup` always installs the lean core prompt (`templates/empirica-system-prompt-lean.md`) as
`~/.claude/empirica-system-prompt.md`: a small always-loaded context that loads skills on demand. There is no
other prompt variant and no flag to select one.

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
4. AI tries to edit → sentinel BLOCKS (no CHECK yet, and no grounded claims declared at PREFLIGHT)
5. AI runs CHECK → sentinel evaluates vectors → "proceed"
   (or: PREFLIGHT claims grounded `read`, or `ran` with scope and count, certify the
   transaction and no CHECK is needed)
6. AI edits, commits → sentinel allows praxic tools
7. AI runs POSTFLIGHT → captures learning delta
8. Post-test collector → gathers objective evidence (git, tests, artifacts)
9. Grounded calibration → compares self-assessment vs evidence
10. Bayesian update → corrects future calibration biases
```

---

## Further Reading

- [CLI Reference](https://github.com/EmpiricaAI/empirica/blob/main/docs/human/developers/CLI_COMMANDS_UNIFIED.md)
- [Architecture](https://github.com/EmpiricaAI/empirica/tree/main/docs/architecture/)
- [Training & Guides](https://getempirica.com)
- [Upgrade Guide](https://github.com/EmpiricaAI/empirica/blob/main/docs/guides/UPGRADE_TO_1.14.md)
