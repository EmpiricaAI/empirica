# Empirica

> **We Gave AI a Mirror. Now It Measures What It Believes.**

[![Version](https://img.shields.io/badge/version-1.9.11-blue)](https://github.com/Nubaeon/empirica/releases/tag/v1.9.11)
[![PyPI](https://img.shields.io/pypi/v/empirica)](https://pypi.org/project/empirica/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Epistemic infrastructure for AI — measurement, memory, and calibration across sessions.**

Empirica tracks what AI knows, gates what it does, and compounds learning across session boundaries. It measures the gap between what AI predicts and what's true — making AI agents measurably more reliable.

**[Training & Guides](https://getempirica.com)** | **[CLI Reference](docs/human/developers/CLI_COMMANDS_UNIFIED.md)** | **[Architecture](docs/architecture/)**

> **Important:** Empirica is an AI measurement framework. It has **no cryptocurrency, token, coin, or blockchain component**. Any token using the Empirica name (including "$EMPIRICA" on Solana) is **unauthorized and not affiliated** with this project or Empirica AI GmbH.

---

## The Problem

AI coding agents today have no self-awareness about what they know:

- **Forgets between sessions** — same questions, same dead ends, every time
- **Acts before understanding** — edits your code without knowing the architecture
- **Can't tell you when it's guessing** — no distinction between knowledge and confabulation
- **No audit trail** — reasoning evaporates with the context window

---

## What Empirica Does

| Capability | What You Experience |
|------------|-------------------|
| **Measures before acting** | AI investigates your codebase before touching it. The Sentinel gate blocks edits until understanding is demonstrated |
| **Remembers across sessions** | Findings, dead-ends, and learnings persist in a 4-layer memory system. Session 3 starts where Session 2 left off |
| **Prevents confident mistakes** | The CHECK gate uses domain-aware thresholds scaled by criticality — cybersec/high is stricter than default/low |
| **Shows confidence in real-time** | Live statusline in your terminal: `[empirica] ⚡94% ↕70% │ 🎯3 │ POST 🔍92% │ K:95% C:92%` |
| **Calibrates against reality** | Three-vector model: self-assessed, observed (from deterministic checks), and AI-reasoned grounded state with rationale. Domain compliance loops iterate until all checks pass |
| **Tracks your codebase** | Temporal entity model auto-extracts functions, classes, and imports from every file edit — the AI knows what's alive and what's stale |
| **Works through natural language** | You describe tasks normally. The AI operates the measurement system automatically |

---

## How You Use It

You talk to your AI normally. Empirica works in the background:

```
You:      "Fix the authentication bug in the login flow"

Empirica: [AI investigates → logs findings → passes Sentinel gate → implements fix → measures learning]

You see:  ⚡87% ↕70% │ 🎯1 │ POST 🔍85% │ K:88% C:82% │ Δ +K
```

**You direct. The AI measures.**

Empirica's CLI has 150+ commands spanning investigation, measurement, calibration, and memory — like a cockpit instrument panel. You don't need to learn any of them. The AI reads the instruments, operates the controls, and reports back in natural language. The statusline gives you the flight data at a glance.

For power users, direct CLI access is always available: `empirica goals-list`, `empirica calibration-report`, `empirica project-search --task "..."`, and more.

**Learn the full workflow:** **[getempirica.com](https://getempirica.com)** has interactive training, guides, and deep explanations of every concept.

---

## Quick Start

### Install + Claude Code (Recommended)

```bash
pip install empirica
empirica setup-claude-code
```

Then just start working. The hooks, Sentinel, system prompt, statusline, and MCP server are all configured automatically. See [Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md) for details.

**Already have Claude Code configured?** Use `--force` to replace your default Claude Code settings with Empirica's epistemic hooks. Without `--force`, setup only writes files that don't already exist — so if you've already used Claude Code, the default internals stay in place and Empirica's hooks won't activate.

```bash
empirica setup-claude-code --force
```

`--force` replaces hooks in `settings.json` but **only removes Empirica's own hooks** — hooks from other plugins (Railway, Superpowers, etc.) are preserved.

### Alternative Installation Methods

<details>
<summary>Homebrew (macOS)</summary>

```bash
brew tap nubaeon/tap
brew install empirica
empirica setup-claude-code
```
</details>

<details>
<summary>Docker</summary>

```bash
# Security-hardened Alpine image (~276MB, recommended)
docker pull nubaeon/empirica:1.9.11-alpine

# Standard image (Debian slim, ~414MB)
docker pull nubaeon/empirica:1.9.11

# Run
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.9.11 /bin/bash
```
</details>

<details>
<summary>Manual / Other AI Platforms</summary>

```bash
pip install empirica
pip install empirica-mcp        # MCP Server (for Cursor, Cline, etc.)
cd your-project && empirica project-init
```

The CLI works standalone on any platform. The full epistemic workflow (epistemic transactions, Sentinel, calibration) requires loading the system prompt into your AI — the easiest path is `empirica setup-claude-code`, which wires the lean prompt into `~/.claude/empirica-system-prompt.md` and references it from your `~/.claude/CLAUDE.md`. See [Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md) for details.
</details>

### First Session

```bash
empirica onboard   # Interactive walkthrough of the full workflow
```

Or just start working — with Claude Code hooks active, the AI manages the epistemic workflow automatically.

---

## The Measurement Architecture

Empirica works through nested abstraction layers:

```
Plan
 └── Transaction 1 (Goal A)
      ├── NOETIC: investigate, search, read → findings, unknowns, dead-ends
      ├── CHECK: Sentinel gate → proceed / investigate more
      ├── PRAXIC: implement, write, commit → goals completed
      └── POSTFLIGHT: measure learning delta → persists to memory
 └── Transaction 2 (Goal B, informed by T1's findings)
      └── ...
```

**Plans** decompose into **transactions** — one per goal or Claude Code task. Each transaction is a **noetic-praxic loop**: investigate first (noetic), then act (praxic), with the Sentinel gating the transition. Along the way, the AI collects and reads **artifacts** (findings, unknowns, assumptions, dead-ends, decisions) while using **semantic search** to surface relevant epistemic patterns and anti-patterns from the project's history. Top artifacts are ranked by confidence and fed into each project's **MEMORY.md** as a hot cache.

### The Epistemic Transaction Cycle

```
PREFLIGHT ────────► CHECK ────────► POSTFLIGHT
    │                 │                  │
 Baseline         Sentinel           Learning
 Assessment        Gate               Delta
    │                 │                  │
 "What do I      "Am I ready      "What did I
  know now?"      to act?"         learn?"
```

**PREFLIGHT:** AI assesses its knowledge state before starting work.
**CHECK:** Sentinel gate validates readiness before allowing code edits.
**POSTFLIGHT:** AI measures what it learned, creating a delta that persists.

---

## Live Statusline

With Claude Code hooks enabled, you see the AI's epistemic state in real-time:

```
[empirica] ⚡94% ↕70% │ 🎯3 ❓12/5 │ POST 🔍92% │ K:95% C:92% │ Δ +K +C
```

| Signal | Meaning |
|--------|---------|
| **⚡94%** | Overall epistemic confidence |
| **↕70%** | Sentinel threshold (know gate) — user-facing only |
| **🎯3 ❓12/5** | Open goals (3), unknowns (12 total, 5 blocking) |
| **POST 🔍92%** | Transaction phase + work state (🔍 investigating / 🔨 acting) with composite score |
| **K:95% C:92%** | Knowledge and Context vectors (color-coded by gap to threshold) |
| **Δ +K +C** | Learning delta (POSTFLIGHT only) — which vectors improved |

---

## The 13 Epistemic Vectors

These vectors emerged from 600+ real working sessions across multiple AI systems. They measure the dimensions that consistently predict success or failure in complex tasks.

| Tier | Vector | What It Measures |
|------|--------|------------------|
| **Gate** | `engagement` | Is the AI actively processing or disengaged? |
| **Foundation** | `know` | Domain knowledge depth |
| | `do` | Execution capability |
| | `context` | Access to relevant information |
| **Comprehension** | `clarity` | How clear is the understanding? |
| | `coherence` | Do the pieces fit together? |
| | `signal` | Signal-to-noise in available information |
| | `density` | Information richness |
| **Execution** | `state` | Current working state |
| | `change` | Rate of progress/change |
| | `completion` | Task completion level |
| | `impact` | Significance of the work |
| **Meta** | `uncertainty` | Explicit doubt tracking |

Deep dive: [Epistemic Vectors Explained](docs/human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md)

---

## How It Works With Claude Code

Empirica doesn't replace or reinvent anything Claude Code already does. Claude Code owns tasks, plans, memory, and projects. Empirica adds the **measurement layer** on top:

| Claude Code Does | Empirica Adds |
|-----------------|--------------|
| Task management | Epistemic goals with measurable completion |
| Plan mode | Investigation phase with Sentinel gating — no edits until understanding is verified |
| MEMORY.md | Auto-curated hot cache ranked by epistemic confidence |
| Context window | 4-layer memory that survives compaction and persists across sessions |
| Code editing | Grounded calibration — was the AI's confidence justified by test results? |
| Subagent spawning | Bounded autonomy with delegated work counting and budget tracking |

The result: Claude Code's native capabilities, enhanced with measurement, gating, and calibration feedback that compounds over time.

---

## Platform Support

| Platform | Integration Level | What You Get |
|----------|------------------|-------------|
| **Claude Code** | Full (production) | Hooks, Sentinel gate, skills, agents, statusline, MCP |
| **Cursor, Cline** | MCP server | Epistemic transaction workflow, memory, calibration via MCP tools |
| **Gemini CLI, Copilot** | Experimental | System prompt + CLI |
| **Any AI** | CLI + prompt | Full measurement via CLI commands and system prompt |

---

## Documentation & Training

| Resource | What It Covers |
|----------|---------------|
| **[getempirica.com](https://getempirica.com)** | Training course, interactive guides, deep explanations |
| **[Natural Language Guide](docs/human/end-users/EMPIRICA_NATURAL_LANGUAGE_GUIDE.md)** | How to collaborate with AI using Empirica |
| **[Getting Started](docs/human/end-users/01_START_HERE.md)** | First-time setup and concepts |
| **[CLI Reference](docs/human/developers/CLI_COMMANDS_UNIFIED.md)** | All 150+ commands documented |
| **[Architecture](docs/architecture/)** | Technical reference for contributors |
| **[Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md)** | Install + system prompt + plugin wiring |

---

## The Empirica Ecosystem

| Project | Description | Status |
|---------|-------------|--------|
| **[Empirica](https://github.com/Nubaeon/empirica)** | Core measurement system — epistemic transactions, Sentinel, calibration, 13 vectors | Open source |
| **[Empirica Iris](https://github.com/Nubaeon/empirica-iris)** | Epistemic browser automation with SVG spatial indexing — Sentinel gating for visual interactions | Open source |
| **[Docpistemic](https://github.com/Nubaeon/docpistemic)** | Epistemic documentation coverage assessment — know what your docs know | Open source |
| **[Breadcrumbs](https://github.com/Nubaeon/breadcrumbs)** | Survive context compacts with git notes — dead simple session continuity | Open source |
| **[Empirica Cortex](https://getempirica.com)** | Cross-project intelligence layer — serves verified predictions and accumulated learnings to condition future work | Proprietary |
| **[Empirica Workspace](https://getempirica.com)** | Entity Knowledge Graph, Epistemic Prompt Engine, CRM, portfolio dashboard | Proprietary |

**Building something with Empirica?** [Open an issue](https://github.com/Nubaeon/empirica/issues) to get listed.

---

## What's New in 1.9.8

- **`cortex-mailbox-send` skill** (`4c09b6174`) — paired to `cortex-mailbox-poll`. Documents
- **Mesh-active skill-load precondition** (`c0fcc071c`) — when a listener Monitor is armed
- **`WHEN TO LOAD SKILLS` section** in both templates (`c0fcc071c`) — behavioral load
- **Goals/tasks worked example** in `TRANSACTION DISCIPLINE` (`c0fcc071c`) —
- **Race-tolerant `create_github_release`** in `scripts/release.py` (`57870621c`). When the
- **Verbose `update_homebrew_tap` diagnostics** (`57870621c`). Per-candidate path logging
- **Lint cleanup**: `S110` noqa-with-reason on the `ai_id` fallback in
- **ntfy tag-filter subscription** (`fcd4ed0fa`, `c9981f35e`). Listener subscribes with
## What's New in 1.9.0

**Goal-criterion bridge — quality gates that auto-evaluate**

- **`criterion_evaluators` package** — validation_method-keyed registry.
  Goals declare `quality_gate:<metric>@<op>:<threshold>` and the bridge
  routes to the right evaluator at POSTFLIGHT.
- **`EvidenceMetricEvaluator`** — auto-evaluates any criterion whose
  metric matches an evidence bundle key (test pass-rate, ruff violations,
  stylometry drift, etc.).
- **Typed criterion parser** — `goals-create --success-criteria
  "quality_gate:test_pass_rate@>=:0.95"` parses to typed
  `CriterionDeclaration`.

**Stylometric drift collector — voice consistency for outreach work**

- 12 prosodic markers (contractions, MTLD, sentence-length stdev, etc.)
- Voice fingerprints at `~/.empirica/voice/<name>.fingerprint.json`
- Drift direction inference (formal_pull / informal_pull / mixed / within_tolerance)

**Content-aware source provenance nudge** — fires at moment of artifact
creation when text shows citation but no `--source`. Closes 0% adoption gap.

**Bulk project-link CLI** — `projects-discover` / `projects-list` /
`projects-bulk-register` (Cortex-dependent).

**Live-scan semantic index** — `semantic_index.json` regenerates when source
docs are newer than the cache.

**Sentinel quote-aware shell parsing** — false-positive `>` in quoted code
fixed (`_has_dangerous_redirects` now uses `_contains_outside_quotes`).

**Template version parameterization (Philipp #100)** — `CLAUDE.md` and
`empirica-system-prompt-lean.md` use `{{ empirica_version }}` and
`{{ generated_date }}` placeholders. Drift cannot recur.

**Documentation refresh** — `UPGRADE_TO_1.9.md` (replaces 1.7), full rewrite
of `PROJECT_SWITCHING_FOR_AIS.md`, `TMUX_MULTI_PANE_GUIDE.md` cockpit section.

## What's New in 1.8.20

- **`empirica commit-context <sha>`** (new CLI). Aggregates artifacts
- **`--depth N` recursive walker.** Walks edges from each artifact's
- **Inline edge declaration on individual `*-log` commands.** All six
- **`edge_density_nudge`** — POSTFLIGHT retrospective +
- **`sources_discipline_nudge`** — same shape, counts artifacts
- **`--status {planned|in_progress|completed|all|drift}`** flag
- **`drift` mode** surfaces rows where the `status` text and
- **Default open count** now uses `is_completed = 0` as the canonical
## What's New in 1.8.17

- **Listener subsystem** — sister to cron loops, event-driven not
  scheduled. `empirica listener register/heartbeat/list` + cockpit
  E binding + project.yaml install hook.
- **Mechanical pause for loops** — pause now cancels the next-fire
  CronCreate token so paused really means silent (no token bleed).
- **Cockpit sweep** — domain·criticality chip per row, compliance
  panel with green/yellow/red glyph, services panel for scanner
  snapshots.

## What's New in 1.8.16

- **#95 root-cause cluster closed** — Cortex sync reads project_id
  from session row (no CWD); `_run_grounded_verification` accepts
  `project_path`; `resolve_project_id` raises `ProjectNotFoundError`
  instead of `sys.exit(1)`. SystemExit-walks-through-Exception hazard
  closed at the source.
- **Per-project compliance.yaml** — projects can `skip_checks`,
  declare `extra_checks` with regulatory mapping, override
  `repo_hygiene` sub-checks. Non-CLI/server projects no longer
  fail tech_docs.
- **KNOWN_ISSUES 11.29 + 11.30** — instance_isolation audit-trail
  entries for the subagent CLI bleed fix and the SystemExit
  propagation chain.

## What's New in 1.8.15

- **Validate-and-heal `session.project_id` at session boundaries** —
  catches the ghost-project_id pattern (cross-project `--resume`,
  ambiguous folder_name match, tmux pane reuse). Heals at post-compact
  CONTINUE_TRANSACTION + NEW_SESSION_PREFLIGHT and at session-init
  resume. Workspace.db `trajectory_path` is the canonical lookup —
  never folder_name (no 11.10/11.27 regression).
- **Voice CLI** — `empirica voice list / show / apply` loads prosodic
  profiles for outreach drafting. Profiles in `~/.empirica/voice/*.yaml`
  with project-local override at `.empirica/voice/`. Voice samples
  themselves stay in Cortex/Qdrant; this CLI is the calling surface.
- **PREFLIGHT `voice_guidance` block** — when `work_type=comms` or
  the new `voice` field/`--voice` flag is set, response includes
  voice tendencies + anti-patterns scoped to platform register
  (mirrors the `noetic_guidance` pattern).
- **Subagent CLI bleed fix (#95 Issue 1)** — `subagent-start` now
  writes `~/.empirica/active_work_<subagent_uuid>.json` with
  `is_subagent: true` so the subagent's CLI calls resolve to their
  own `child_session_id` instead of falling through to the parent's
  via TTY. `sentinel-gate._detect_subagent` reads the flag.
  `subagent-stop` cleans up.
- **POSTFLIGHT pipeline restructure (#95 Issue 3)** — Stage 0
  pre-validates session row + project_id BEFORE any state mutation;
  failure → early return with `loop_state: "open"`. Stages 5-7
  wrapped in `_soft_run` — failures accumulate into
  `result["warnings"]` without erasing the closed-loop reflex.
  No more half-success.

## What's New in 1.8.14

- **Notify dispatcher** — single CLI verb (`empirica notify emit/config/
  backends/test`) every loop and hook calls. Three v1 backends (stdout,
  rotating JSONL log, ntfy) with first-match-wins routing and fail-loud
  fallback to stdout when a backend isn't configured. Always-on audit
  at `~/.empirica/notify-dispatcher.jsonl`. Cockpit + TUI surface 5
  most recent emits, backend status, 24h fallback count, and a failure
  banner. See [`docs/architecture/NOTIFY.md`](docs/architecture/NOTIFY.md).
- **Project-scoped TUI notifications** — per-instance notifications
  strip now reads `~/.empirica/enp/pending.json` (the file the ENP
  watcher actually writes). Top-bar `⊕N` shows total unacked across
  all projects.
- **`empirica goals-prune`** — bulk goal cleanup with four modes
  (test-pollution, planned, auto-stale, duplicates). Dry-run by default.
- **Empirica Cockpit** — multi-instance state visibility +
  per-instance controls. `empirica status [--all]` overview,
  `empirica tui` interactive Textual app, `empirica
  sentinel|loop|instance` subcommand groups. See
  [`docs/architecture/COCKPIT.md`](docs/architecture/COCKPIT.md).
- **Loop exponential backoff** — empty fires lengthen the gap;
  found/fail snap back to base (15m → 30m → 1h → 2h → 4h cap).
- **`noetic-batch` CLI primitive** — bundles N
  reads/greps/globs/`investigate` into one Sentinel-noetic call.

### Sentinel Reframe (1.8.0)

The Sentinel is a **compliance loop coordinator**. Deterministic services produce information; the AI synthesizes the grounded epistemic state.

- **Domain Registry** — `(work_type, domain, criticality)` tuples map to compliance checklists. 4 built-in domains: `default`, `remote-ops`, `cybersec`, `docs`. CLI: `domain-list`, `domain-show`, `domain-resolve`
- **Domain-aware CHECK gate** — uncertainty threshold scales by criticality. `cybersec/high` is stricter than `default/low`
- **Three-vector model** — `self_assessed`, `observed` (from deterministic checks), and AI-reasoned `grounded` state with rationale
- **Compliance loop** — POSTFLIGHT runs domain checklist, reports status, advises on follow-up for failed checks
- **Check-outcome Brier** — AI predicts P(check passes), Brier measures against actual outcomes. Falsifiable calibration
- **Real check runners** — pytest, ruff, and git status execute as subprocess checks (not stubs)
- **Test isolation** — tests no longer pollute live sessions via TMUX_PANE inheritance

### Previous Highlights (1.7.0–1.7.13)

- **Empirica Constitution** — 12-section governance framework routing situations to mechanisms
- **Epistemic Persistence Protocol (EPP)** — Calibrated position-holding under pushback, replacing AAP
- **Lean Core Prompt** — 81% reduction in always-loaded context. `setup-claude-code --lean`
- **Cross-Project Search** — `--global` searches ALL projects' Qdrant collections
- **Cross-Project Artifact Writing** — `finding-log --project-id <name>` writes to another project
- **Plugin Renamed** — `empirica-integration` → `empirica`. Run `setup-claude-code --force`
- **Brier Score Calibration** — Proper scoring rule with dynamic thresholds
- **Profile Management** — `profile-sync`, `profile-prune`, `profile-status`

---

## Privacy & Data

**Your data stays local:**

- `.empirica/` — Local SQLite database (gitignored by default)
- `.git/refs/notes/empirica/*` — Epistemic checkpoints (local unless you push)
- Qdrant runs locally if enabled

No cloud dependencies. No telemetry. Your epistemic data is yours.

---

## Community & Support

- **Website:** [getempirica.com](https://getempirica.com)
- **Issues:** [GitHub Issues](https://github.com/Nubaeon/empirica/issues)
- **Discussions:** [GitHub Discussions](https://github.com/Nubaeon/empirica/discussions)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

**Author:** David S. L. Van Assche
**Version:** 1.9.11

*Turtles all the way down — built with its own epistemic framework, measuring what it knows at every step.*
