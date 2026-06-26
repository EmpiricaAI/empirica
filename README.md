# Empirica

> **We Gave AI a Mirror. Now It Measures What It Believes.**

[![Version](https://img.shields.io/badge/version-1.12.7-blue)](https://github.com/EmpiricaAI/empirica/releases/tag/v1.12.7)
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
| **Optional: coordinates with peer AIs** | Cross-Claude mesh via Cortex (opt-in) — peer AIs propose work, ECO accepts/declines, completion handshakes carry commit SHAs. A persistent listener wakes idle sessions on inbox events. **Empirica core works standalone without this** — see [Cross-AI Mesh](#cross-ai-mesh) below for the ecosystem layer |

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

Then just start working. The hooks, Sentinel, system prompt, statusline, and MCP server are all configured automatically. See [Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md) for details — including a **["What the hooks inject"](docs/human/developers/CLAUDE_CODE_SETUP.md#what-the-hooks-inject-for-claude-reading-this-before-agreeing-to-install) section** for Claude sessions that want to see the contract (which hook fires when, what it adds to the AI's context, source pointers for every emission) before agreeing to install.

**Already have Claude Code configured?** Use `--force` to replace your default Claude Code settings with Empirica's epistemic hooks. Without `--force`, setup only writes files that don't already exist — so if you've already used Claude Code, the default internals stay in place and Empirica's hooks won't activate.

```bash
empirica setup-claude-code --force
```

`--force` replaces hooks in `settings.json` but **only removes Empirica's own hooks** — hooks from other plugins (Railway, Superpowers, etc.) are preserved.

### Alternative Installation Methods

<details>
<summary>Homebrew (macOS)</summary>

```bash
brew tap empiricaai/tap
brew install empirica
empirica setup-claude-code
```
</details>

<details>
<summary>Docker</summary>

```bash
# Security-hardened Alpine image (~276MB, recommended)
docker pull nubaeon/empirica:1.12.7-alpine

# Standard image (Debian slim, ~414MB)
docker pull nubaeon/empirica:1.12.7

# Run
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.12.7 /bin/bash
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

## Cross-AI Mesh (Optional Ecosystem Layer)

**This section describes an optional layer.** Empirica core — measurement, calibration, artifacts, goals, project-search, sentinel gating — works fully standalone. The mesh is an opt-in capability for users who run multiple Claude sessions across projects and want them to coordinate as peers. If you only use one AI in one repo, skip this section.

The mesh runs on top of [Empirica Cortex](https://getempirica.com) (proprietary serving layer) plus an optional [browser extension](https://getempirica.com) for ECO triage. At a high level:

```
empirica AI ── proposes work ──► ECO Accept/Decline ──► peer AI wakes + acts
                                                             │
                              completion handshake (commit SHA)
                                                             │
empirica AI ◄────────── outbox/completed event ──────────────┘
```

| Capability | What it does |
|------------|-------------|
| **Mesh proposals (two flavors)** | A noetic flavor is auto-accepted (FYI / question / discussion). Praxic flavors (code change / architecture / investigation) are **ECO-gated** — they wait for an Accept/Decline decision before the target AI acts |
| **`empirica mailbox reply`** | One CLI verb closes the AI-to-AI handshake atomically — single-step completion ack instead of two |
| **Persistent listener service** | systemd-user / launchd daemon holds a push stream open. Idle sessions wake the moment a peer's proposal is decided, not on next user prompt |
| **Canonical loops** | Inbox polling (30s adaptive) and daily housekeeping auto-install per AI — no per-project config needed |

The browser-side ECO surface (Accept/Decline, inbox triage, publish review) lives in the proprietary [Empirica Extension](https://getempirica.com). The full API surface for proposals, listener events, and the trust pipeline is documented at [getempirica.com](https://getempirica.com).

---

## Mesh + Shared Epistemic Record (1.11.0)

The cross-AI coordination layer. Practitioners in different practices coordinate not via text-only chat but via **epistemic envelopes** that carry calibrated state, source-tagged provenance, noetic/praxic intent, and workflow position.

- **Practitioner / practice** framing — practices are calibrated epistemic specializations that persist; practitioners (the LLMs) are fungible. See [MESH_CONCEPTS.md](docs/human/end-users/MESH_CONCEPTS.md).
- **Shared Epistemic Record (SER)** — cortex-resident shared-state object for coordination across ≥2 practitioners. Goals stay per-practitioner; SER carries the *joint* state (`coordination_state`, role-tiered participants, escalate-on-silence). Three actions: `create_ser` / `transition_ser` / `ser_ack`. Spec at `empirica-cortex/docs/architecture/SHARED_EPISTEMIC_RECORD.md`.
- **`empirica mesh` command cluster** (1.11.0) — unified diagnostic + control surface across listener instances + the optional cortex bridge:
  ```bash
  empirica mesh status              # per-instance health (local + cortex bridge)
  empirica mesh diagnose <ai_id>    # deep diagnostic + suggested fix command
  empirica mesh restart <ai_id>     # systemd/launchd restart + verify
  empirica mesh on|off <ai_id>      # install + start | stop the listener
  empirica mesh tail [<ai_id>]      # live-tail loop_fires.log
  ```
- **Listener self-heal** — in-process watchdog terminates stale curl streams (TCP-zombie detection at 120s by default); HTTP 429 detection applies long backoff with catch-up poll continuing during the window.
- **Mesh Routing Protocol v0** locked four-way with cortex + extension + mesh-support. L1/L2/L3 trust model, server-stamped layer annotation, participant-scoped thread reads.

The full mesh requires cortex + extension; **empirica core works standalone** for single-tenant multi-practitioner coordination via local git-notes messaging + goals + workspace.

---

## Practice Model + Entity Graph (1.10.0)

Empirica's workspace stores entities (projects, contacts, organisations, engagements, users) in `entity_registry` with typed edges in `entity_memberships`. The **Practice Model** frames this consistently:

| Term | Maps to |
|------|---------|
| **Practitioner** | the AI working on the project (you) |
| **Practice** | the empirica project itself |
| **Agent** | a subagent spawned during the work |

Four CLI verbs query the graph without raw SQL:

```bash
empirica entity-list [--type project|contact|organization|engagement|user]
empirica entity-show <type:id>          # full record + incoming/outgoing edges
empirica entity-walk <type:id> --depth 3 # BFS membership graph, cycle-safe
empirica entity-search "query" [--type T]
```

All read-only, all support `--output json`. Backs cross-project orchestration, CRM workflows, and the entity-aware POSTFLIGHT retrospective.

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
| **[Changelog](CHANGELOG.md)** | Full release history — every version since 1.0 |
| **[Upgrade to 1.11](docs/guides/UPGRADE_TO_1.11.md)** | Migration guide rolling up 1.10.5+1.10.6+1.11.x — bead v0 → SER, mesh substrate hardening, MESH_CONCEPTS framing |

---

## The Empirica Ecosystem

| Project | Description | Status |
|---------|-------------|--------|
| **[Empirica](https://github.com/EmpiricaAI/empirica)** | Core measurement system — epistemic transactions, Sentinel, calibration, 13 vectors | Open source (MIT) |
| **[Empirica Iris](https://github.com/Nubaeon/empirica-iris)** | Epistemic browser automation with SVG spatial indexing — Sentinel gating for visual interactions | Open source (MIT) |
| **[Docpistemic](https://github.com/Nubaeon/docpistemic)** | Epistemic documentation coverage assessment — know what your docs know | Open source (MIT) |
| **[Breadcrumbs](https://github.com/Nubaeon/breadcrumbs)** | Survive context compacts with git notes — dead simple session continuity | Open source (MIT) |
| **[Ecodex](https://github.com/EmpiricaAI/ecodex)** | Codex-based Rust harness — the epistemic firewall and pattern hunt, native to Rust/cargo (clippy, `cargo check`/`test`/`audit`) | Open source (Apache-2.0) |
| **[Eat the Broccoli](https://github.com/EmpiricaAI/broccoli)** | Portable quality-and-pattern audit — deterministic tooling plus a learned-pattern hunt for the failure classes that pass every test and still ship broken | Open source (MIT) |
| **[Empirica Cortex](https://getempirica.com)** | Cross-project intelligence layer — serves verified predictions and accumulated learnings to condition future work | Proprietary |
| **[Empirica Workspace](https://getempirica.com)** | Entity Knowledge Graph, Epistemic Prompt Engine, CRM, portfolio dashboard | Proprietary |
| **[Empirica Extension](https://getempirica.com)** | Chrome extension — desktop face of the mesh. ECO Accept/Decline, inbox/outbox triage, publish review, conversation extraction from Claude.ai / ChatGPT / Gemini / Grok | Proprietary |
| **[Empirica Outreach](https://getempirica.com)** | Voice-aware outreach + publishing — prosody-matched content generation and multi-channel dispatch | Proprietary |

**Building something with Empirica?** [Open an issue](https://github.com/EmpiricaAI/empirica/issues) to get listed.

---

## The Empirica Foundation

The **Empirica Foundation** stewards the open ecosystem — the public projects above and the community growing around them — keeping the commons healthy as it scales.

The open-source projects are free for everyone. What the Foundation adds is a **seat at the table**: contributors who help build the community get to use the rest of the ecosystem too — the otherwise-paid layers (Cortex, Workspace, the Extension) and the collaborative mesh that lets practitioners coordinate as peers. Build the commons, use the whole thing.

**Want in?** Open an [issue](https://github.com/EmpiricaAI/empirica/issues) or a PR with your reasons — that's the whole application. Everyone who wants to help shape where this goes is welcome.

---

## What's New in 1.12.7

- **Vendored `/eat-the-broccoli` skill** — the tiered quality-and-pattern audit
- **CLI-level plugin auto-sync** — the `empirica` CLI self-heals a stale *deployed*
- **Sentinel remote-ops pacing-guard deadlock** — the rush-guard now (a) sees
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
- **Issues:** [GitHub Issues](https://github.com/EmpiricaAI/empirica/issues)
- **Discussions:** [GitHub Discussions](https://github.com/EmpiricaAI/empirica/discussions)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

**Author:** David S. L. Van Assche
**Version:** 1.12.7

*Turtles all the way down — built with its own epistemic framework, measuring what it knows at every step.*
