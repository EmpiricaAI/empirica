# Empirica Plugin for Claude Code

Epistemic measurement, a noetic firewall, and grounded calibration for Claude Code.

The plugin makes Claude Code measurably more reliable by recording what the AI believes it knows, holding back actions that change state until the work rests on something it read or ran, and scoring those beliefs against objective evidence afterwards.

## Installation

```bash
pip install empirica
empirica setup          # first install
empirica setup --force  # reinstall or update; keeps other plugins' hooks
```

`empirica setup` (also reachable as `empirica setup-claude-code`, which `install.sh` calls) does four things for Claude Code:

1. Copies this plugin to `~/.claude/plugins/local/empirica/`.
2. Writes the lean core prompt to `~/.claude/empirica-system-prompt.md` and adds an `@~/.claude/empirica-system-prompt.md` include to `~/.claude/CLAUDE.md`.
3. Registers the hooks and the statusline in `~/.claude/settings.json` and pre-authorizes the `mcp__cortex__*` and `mcp__empirica__*` tools.
4. Configures the Empirica MCP server, offers a credentials wizard, and installs the persistent listener service. `--skip-mcp`, `--skip-credentials` and `--skip-listener-service` skip those; `--uninstall` removes the install.

`--force` removes only Empirica's own hook entries and statusline before re-registering, so hooks from Railway, Superpowers and others survive. A plain re-run keeps existing registrations but repoints their interpreter at the one running setup.

---

## What gets installed

| Component | Location in `~/.claude/plugins/local/empirica/` |
|---|---|
| Hooks | `hooks/`: one script per hook below (`hooks/hooks.json` is intentionally empty: registration lives in `settings.json`, with absolute paths) |
| Skills | `skills/`: loaded on demand |
| Commands | `commands/`: `/empirica`, `/chrome-health` |
| Agents | `agents/`: `architecture`, `security`, `performance`, `ux` |
| Statusline | `scripts/statusline_empirica.py` |
| Shared code | `lib/` (project and instance resolution used by the hooks), `templates/` (the lean prompt, `settings-hooks.json`, `settings-statusline.json`, `mcp.json`) |

---

## Hooks

Hooks fire on Claude Code events. They are registered by `_register_all_hooks` in `empirica/cli/command_handlers/setup_claude_code.py`; the table below is that function's output.

| Hook | Event (matcher) | What it does |
|---|---|---|
| **sentinel-gate** | PreToolUse (`Edit\|Write`, `Bash`) | The noetic firewall. Allows reads, safe shell and planning freely; allows a state-changing call only when the transaction certifies it: a CHECK, PREFLIGHT claims grounded by `read` or `ran` (with scope and count), or PREFLIGHT vectors that already clear the threshold. Also counts tool calls and emits nudges. See the [Sentinel Gate Reference](https://github.com/EmpiricaAI/empirica/blob/main/docs/reference/SENTINEL_GATE_REFERENCE.md) |
| **ruling-shape** | PreToolUse (`AskUserQuestion`) | Reminds the model, without blocking, when a single-select question has no option marked "(Recommended)" |
| **session-init** | SessionStart (`startup\|resume`) | Creates an Empirica session, runs `project-bootstrap`, injects the system prompt plus the calibration bias block from `.breadcrumbs.yaml`, and prompts for PREFLIGHT |
| **post-compact** | SessionStart (`compact`) | Re-grounds after compaction: a finished transaction gets a new session and PREFLIGHT, a mid-work one is re-checked on the old session |
| **ewm-protocol-loader** | SessionStart (both) | Loads `workflow-protocol.yaml` (project, then `~/.empirica/`) for personalised collaboration |
| **session-monitor-arm** | SessionStart (both) | Tells the session to arm a Monitor that bridges registered loop fires into it; silent when no loops are enabled |
| **pre-compact** | PreCompact (`auto\|manual`) | Captures vectors, bootstrap context, the last human task and git context into a breadcrumbs git note before compaction |
| **session-end-postflight** | SessionEnd | Captures a POSTFLIGHT from the last known vectors if a transaction is still open |
| **curate-snapshots** | SessionEnd | Prunes pre-compact snapshots by impact and completion |
| **tool-router** | UserPromptSubmit | Assesses each prompt against the epistemic state, recommends agents and skills, injects the `<epp-check>` pointer, and arms the investigation-proportionality budget the Sentinel enforces |
| **context-shift-tracker** | UserPromptSubmit | Classifies a prompt as solicited (answering an `AskUserQuestion`) or unsolicited, for calibration |
| **loop-install-pickup**, **loop-uninstall-pickup**, **listener-install-pickup**, **listener-uninstall-pickup** | UserPromptSubmit | Surface pending loop and listener install and uninstall requests from the cockpit on the next prompt |
| **entity-extractor** | PostToolUse (`Edit\|Write`) | Extracts functions, classes and imports from edited files into the codebase model |
| **truncation-legibility** | PostToolUse (`Bash`) | Says so when output the model just read was partial (for example `head -N` returned exactly N lines) |
| **tool-failure** | PostToolUseFailure | Logs the failed approach and error as a dead-end |
| **subagent-start** | SubagentStart | Creates a child session linked to the parent and looks up (or creates) its attention budget |
| **subagent-stop** | SubagentStop | Rolls findings, unknowns and dead-ends from the subagent's transcript up to the parent session and adds its tool calls to `delegated_tool_calls` |
| **task-completed** | TaskCompleted | Blocks task completion while a transaction with more than 3 tool calls is open; otherwise matches the task subject to an Empirica goal and completes it |
| **transaction-enforcer** | Stop | With a transaction open, reminds after 12 turns and blocks stopping after 20 until POSTFLIGHT. Thresholds: see the [Configuration Reference](https://github.com/EmpiricaAI/empirica/blob/main/docs/reference/CONFIGURATION_REFERENCE.md) |

`epistemic_summarizer.py` in `hooks/` is a helper the compaction hooks import, not a hook.

### ENP (Epistemic Network Protocol), optional

Not registered by `empirica setup`; `empirica enp-setup` prepares the watcher and prints the registration lines.

| Hook | Event | What it does |
|---|---|---|
| **enp-notify** | SessionStart | Surfaces pending ENP notifications (git folder changes found by the cron watcher) |
| **enp-postflight-notify** | PostToolUse (`Bash`) | Surfaces new ENP notifications after `postflight-submit` |

---

## Skills

Skills load on demand when the AI recognises the situation, or by name (`/skill-name`).

| Skill | When | What it does |
|---|---|---|
| **empirica-constitution** | Routing uncertainty; before the first PREFLIGHT of a session | Governance decision tree: routes a situation to the right Empirica mechanism |
| **epistemic-transaction** | Complex work, planning | Decomposes work into measured transactions, PREFLIGHT through POSTFLIGHT. For tasks spanning 3+ files or 2+ goals |
| **epistemic-persistence-protocol** | The user disagrees or pushes back | Calibrated position-holding: classifies the pushback and chooses HOLD, SOFTEN, UPDATE or REFRAME |
| **pre-action-grounding** | A task arrives without its why or a checkable done-condition | Investigates first, asks last, records the ungrounded residue as assumptions, emits a goal with typed criteria |
| **reporting-discipline** | Before reporting finished work, a correction or a release | Turns finished work into done/next bullets keyed to goal ids |
| **code-audit** | `/code-audit` | Structured noetic review of code quality using ruff, radon and pyright, producing Empirica artifacts |
| **code-docs-align** | `/code-docs-align` | Checks that documentation, docstrings and comments match the code |
| **eat-the-broccoli** | `/eat-the-broccoli`, pre-release audit | Tiered quality sweep: deterministic tooling plus a hunt for failures that pass tests |
| **architecture-review** | Reviewing a system architecture | Stress-tests a design for bottlenecks, single points of failure, security and cost gaps, ranked by blast radius |
| **epistemic-gardening** | `/epistemic-gardening`, pre-release | De-weeds the epistemic graph: resolves stale artifacts, closes answered unknowns, prunes dangling edges |
| **epistemic-editing** | Reviewing a document whose claims must hold up | Grounded review pass over claim classes that fail silently; renders flags for the author to accept or reject |
| **dispatch-agent** | Spawning a subagent for non-trivial work | Enriches the agent prompt with findings, dead-ends and anti-patterns from Cortex |
| **ewm-interview** | `/ewm-interview` | Interviews you and writes `workflow-protocol.yaml` |
| **inbox-listener** | Wake-on-event setup | Arms a `Monitor` on a registered listener (load it through ToolSearch first; it is a deferred tool; re-arm at each 30-minute expiry) |
| **loop-cron** | Recurring background work | Registers periodic work in the loop registry; self-throttles while a transaction is open |
| **message-cleanup** | Scheduled daily | Loop body that prunes expired git-notes mesh messages |
| **services-auditor** | `empirica scan --explain` | Reads the scanner snapshot, judges each AI-touching process against the bundled security corpus, and emits findings, assumptions and unknowns |
| **services-audit-cron** | Scheduling the services audit | Wraps `empirica services-audit` for the biweekly cron loop |
| **render** | `/render` | Generates DiagramSpec JSON for ASCII-art blocks in markdown and renders them with mdview |

The mesh skills `cortex-mailbox-poll` and `cortex-mailbox-send` ship with the Cortex bundle, not this plugin. See `docs/reference/SKILLS.md`.

---

## Commands

| Command | What it does |
|---|---|
| `/empirica on\|off\|status` | Pauses or resumes Sentinel enforcement for this terminal instance (`--global` for all), through `empirica on`, `off` and `sentinel status` |
| `/chrome-health [status\|monitor\|stop]` | Checks the Chrome MCP connection and sets up periodic monitoring |

## Agents

Subagents generated from Empirica persona profiles by `scripts/generate_agents.py`, each with its own epistemic profile and `maxTurns: 25`.

| Agent | Domain |
|---|---|
| **architecture** | System design, patterns, modularity, coupling |
| **security** | Authentication, authorization, encryption, vulnerabilities |
| **performance** | Optimization, latency, throughput, memory |
| **ux** | Usability, accessibility, user flows, error messages |

---

## Statusline

One short line of epistemic state in the Claude Code status bar. Sample (a CHECK that has not yet proceeded, 41% of context used):

```
 empirica  │ CHECK 73% │ G3 U5 A2 F2/D1 │ Δ … │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5
```

Practice name, cascade stage (`PRE`, `CHECK`, `POST`, `TEST`) with confidence, open goals, unknowns and assumptions, findings and decisions logged **in this transaction**, a learning mark (`…` while a transaction is open or not yet graded; after POSTFLIGHT grades it: 🔥 very good, ✓ good, `-` average, ✗ below average, `?` too little evidence), context used, then `investigate` or `act` and the model.

The detailed view is one command away, no restart:

```bash
echo expanded > ~/.empirica/statusline_mode   # detailed
echo compact  > ~/.empirica/statusline_mode   # back to the default
```

See the [Statusline Reference](https://github.com/EmpiricaAI/empirica/blob/main/docs/reference/STATUSLINE_REFERENCE.md) for every mode and edge state.

---

## Compliance report

A project-wide quality snapshot mapped to regulatory frameworks (EU AI Act, GDPR, ISO/IEC 42001):

```bash
empirica compliance-report                 # fast checks
empirica compliance-report --tests         # add the test suite
empirica compliance-report --dep-audit     # add a CVE scan
empirica compliance-report --security      # add an OWASP scan
empirica compliance-report --output json   # machine-readable
```

Checks that always run (the `check` ids in the JSON output), with the EU AI Act article each maps to:

| Check | Measures | EU AI Act |
|---|---|---|
| `lint` | ruff violations | Art. 9 |
| `complexity` | cyclomatic complexity (C901) | Art. 15(1) |
| `type_safety` | pyright errors | Art. 15(1) |
| `tech_docs` | documentation coverage (`docs-assess`) | Art. 11 |
| `tech_docs_links` | broken relative links (`docs-link-check`) | Art. 11 |
| `release_chain` | release channels published for the current version | Art. 10 |
| `discipline` | transaction and artifact process discipline | Art. 17 |
| `ai_transparency` | `Co-Authored-By` attribution on recent commits | Art. 50 |
| `decision_transparency` | rationale on logged decisions | Art. 13 |
| `repo_hygiene` | license, changelog, `.gitignore`, release scripts, no tracked secrets, version file | Art. 10 |
| `epistemic_audit` | completeness of the transaction trail | Art. 12 |
| `calibration` | grounded self-assessment accuracy | Art. 14 |
| `governance_integrity` | the compliance control map itself | Art. 17 |

Optional checks are `--tests` (pytest), `--dep-audit` (pip-audit) and `--security` (semgrep OWASP). `--emit` sends the result to Cortex System diagnostics and needs a Cortex credential.

A project can tune the report with `.empirica/compliance.yaml`: `skip_checks` (check ids to drop), `extra_checks` (project-specific runners that print JSON with `passed` and `status`), `repo_hygiene` (`license_required`, `changelog_required`, `release_scripts_required`), and `tech_docs.tool` to choose the documentation checker.

---

## Configuration

**Sentinel control.**

```bash
empirica off / empirica on                    # pause or resume for this terminal (/empirica does the same)
echo "false" > ~/.empirica/sentinel_enabled   # disable gating; read on every call, no restart
echo "true"  > ~/.empirica/sentinel_enabled   # re-enable
```

The file wins over the environment: `EMPIRICA_SENTINEL_LOOPING=false` is consulted only when `~/.empirica/sentinel_enabled` does not exist, so a file holding `true` makes it inert. An unattended runner that must not be gated should run with a scratch `HOME`, or with the plugin off. Outside a git repo and any empirica project there is nothing to measure, so the Sentinel allows and says so; `EMPIRICA_SENTINEL_FAIL_CLOSED` does not turn that into a deny.

**Lean core prompt.** `empirica setup` always installs `templates/empirica-system-prompt-lean.md` as `~/.claude/empirica-system-prompt.md`: a small always-loaded prompt that loads skills on demand. There is no other prompt variant and no flag to select one.

**Workflow protocol.** `/ewm-interview` interviews you and writes `workflow-protocol.yaml`, which the `ewm-protocol-loader` hook loads at session start.

Everything else (thresholds, credentials, paths) is in the [Configuration Reference](https://github.com/EmpiricaAI/empirica/blob/main/docs/reference/CONFIGURATION_REFERENCE.md).

---

## How it works

```
You: "Fix the auth bug"

1. SessionStart hook   creates a session and loads context
2. AI runs PREFLIGHT   opens a transaction with baseline vectors; may declare claims
3. AI investigates     reads and searches; the Sentinel allows noetic work freely
4. AI tries to edit    the Sentinel holds it unless the transaction certifies the edit:
                       - PREFLIGHT claims grounded by read, or ran with scope and count, or
                       - PREFLIGHT vectors that already clear the threshold, or
                       - a CHECK (refused if sent within 30 seconds of PREFLIGHT with nothing logged)
5. AI edits, commits   praxic tools are allowed
6. AI runs POSTFLIGHT  closes the transaction and records the learning delta
7. Post-test           gathers objective evidence (git, tests, artifacts)
8. Grounded calibration compares self-assessment with the evidence
9. Bayesian update     corrects future calibration bias
```

---

## Further reading

- [CLI Reference](https://github.com/EmpiricaAI/empirica/blob/main/docs/human/developers/CLI_COMMANDS_UNIFIED.md)
- [Architecture](https://github.com/EmpiricaAI/empirica/tree/main/docs/architecture/)
- [Training & Guides](https://getempirica.com)
- [Upgrade Guide](https://github.com/EmpiricaAI/empirica/blob/main/docs/guides/UPGRADE_TO_1.14.md)
