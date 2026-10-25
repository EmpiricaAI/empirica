# Empirica

> **We Gave AI a Mirror. Now It Measures What It Believes.**

[![Version](https://img.shields.io/badge/version-1.14.16-blue)](https://github.com/EmpiricaAI/empirica/releases/tag/v1.14.16)
[![PyPI](https://img.shields.io/pypi/v/empirica)](https://pypi.org/project/empirica/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Epistemic infrastructure for AI — measurement, memory, and calibration across sessions.**

Empirica tracks what AI knows, gates what it does, and compounds learning across session boundaries. It measures the gap between what AI predicts and what's true — making AI agents measurably more reliable.

**[Training & Guides](https://getempirica.com)** | **[CLI Reference](docs/human/developers/CLI_COMMANDS_UNIFIED.md)** | **[Architecture](docs/architecture/)**

> **Important:** Empirica is an AI measurement framework. It has **no cryptocurrency, token, coin, or blockchain component**. Any token using the Empirica name (including "$EMPIRICA" on Solana) is **unauthorized and not affiliated** with this project or Empirica AI GmbH.

---

## The Problem

**AI can now do real work: write and ship code, run pipelines, research, coordinate with other agents. What it cannot do by itself is show how well it knew what it was doing, so people either review everything and lose the speed, or trust it blindly and find out later.** The gap is not a bigger model. It is a record of what the work rests on, a check of that record against what actually happened, and a way to give an AI more freedom as it earns it. Empirica sits beside the model for exactly that, and does not compete with it.

- **Memory without judgment.** Most agents now remember something between sessions. What they keep is a lossy summary retrieved by similarity: it cannot tell which caveat or dead end the current task depends on, and it stores a guess with the same weight as a measurement.
- **Nothing is ever retracted.** Wrong or finished items keep coming back as current, because remembered things have no lifecycle.
- **It acts before it understands.** Edits land before anyone knows the architecture they touch.
- **Confidence is not evidence.** A model sounds as sure when it is guessing as when it is right, and its stated confidence is rarely graded against what held.
- **It passes every check and still ships broken.** Clean lint, green tests, a confident review, and a defect that lives where none of them looked.
- **No audit trail an auditor can use.** Logs record what happened, not what the AI believed, assumed or checked.
- **Autonomy is all-or-nothing.** There is no way to scope what a workload may do alone from its track record.
- **Plans cannot be wrong.** A roadmap with no confidence, evidence or falsifier cannot be graded, so nobody learns whether the planning was any good.
- **Humans in the loop at the wrong places.** Attention is spread evenly instead of going where impact is high and confidence is low.

---

## What Empirica Does

| Capability | What You Experience |
|------------|-------------------|
| **Measures before acting** | AI investigates your codebase before touching it. The Sentinel gate blocks edits until understanding is demonstrated |
| **Remembers what the work depends on** | Findings, dead-ends, assumptions and mistakes persist in a 4-layer memory with provenance and confidence, are retracted or superseded when wrong, and are retrieved by relevance to the task, so the next session starts with what matters rather than a summary |
| **Prevents confident mistakes** | The CHECK gate uses domain-aware thresholds scaled by criticality, tightened by the calibration of the model doing the work — cybersec/high is stricter than default/low |
| **Shows confidence in real-time** | Live statusline in your terminal, compact by default: ` empirica  │ CHECK 78% │ G3 U5 A2 F4/D1 │ Δ ✓ │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5` (stage and confidence, open goals/unknowns/assumptions, what you logged this transaction, a learning mark, context, and investigate or act). One command switches to the detailed view |
| **Calibrates against reality** | Three-vector model: self-assessed, observed (from deterministic checks), and AI-reasoned grounded state with rationale. Domain compliance loops iterate until all checks pass |
| **Tracks your codebase** | Temporal entity model auto-extracts functions, classes, and imports from every file edit — the AI knows what's alive and what's stale |
| **Forecasts, not just plans** | Directions are predictions with a confidence, the kind of evidence behind it and what would prove them wrong, graded when they ship or miss (see What's Coming below) |
| **Keeps humans where they matter** | The AI is instructed to bring you a predicted answer rather than an open question, `/forecast` ranks which directions deserve your attention by impact and confidence, and proposals to peer practices wait for an explicit accept or decline |
| **Works through natural language** | You describe tasks normally. The AI operates the measurement system automatically |
| **Optional: coordinates with peer AIs** | Cross-AI mesh via Cortex (opt-in) — peer AIs propose work, ECO accepts/declines, completion handshakes carry commit SHAs. A persistent listener delivers inbox events to running sessions, and an opt-in wake service nudges idle ones. **Empirica core works standalone without this** — see [Cross-AI Mesh](#cross-ai-mesh-optional-ecosystem-layer) below |

---

## How You Use It

You talk to your AI normally. Empirica works in the background:

```
You:      "Fix the authentication bug in the login flow"

Empirica: [AI investigates → logs findings → passes Sentinel gate → implements fix → measures learning]

You see:   empirica  │ POST 85% │ G1 U2 A0 F3/D1 │ Δ … │ 41%ctx │ 🔨 act - 🧠 Sonnet 5.5
```

**You direct. The AI measures.**

Empirica's CLI has 240+ commands spanning investigation, measurement, calibration, and memory — like a cockpit instrument panel. You don't need to learn any of them. The AI reads the instruments, operates the controls, and reports back in natural language. The statusline gives you the flight data at a glance.

For power users, direct CLI access is always available: `empirica goals-list`, `empirica calibration-report`, `empirica project-search --task "..."`, and more.

**Learn the full workflow:** **[getempirica.com](https://getempirica.com)** has interactive training, guides, and deep explanations of every concept.

---

## Quick Start

### Install + Claude Code (Recommended)

```bash
pip install empirica
empirica setup
```

Then just start working. The hooks, Sentinel, system prompt, statusline, and MCP server are all configured automatically.

> **`empirica setup --harness` picks the harness.** It resolves from
> `--harness`, else `$EMPIRICA_HARNESS`, else `claude-code`. An unsupported
> harness is **refused by name and nothing is written** — previously it wrote
> `~/.claude/` regardless and reported success, which on any other harness
> configured a path that harness never reads.
>
> Codex is refused deliberately: ecodex is self-provisioning (it vendors the
> plugin into its own binary), so there is nothing here to write, and the
> refusal points you at that pipeline. `setup-claude-code` remains as an alias
> and pins `claude-code` regardless of environment.

See [Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md) for details — including a **["What the hooks inject"](docs/human/developers/CLAUDE_CODE_SETUP.md#what-the-hooks-inject-for-claude-reading-this-before-agreeing-to-install) section** for Claude sessions that want to see the contract (which hook fires when, what it adds to the AI's context, source pointers for every emission) before agreeing to install.

**Already have Claude Code configured?** Use `--force` to replace your default Claude Code settings with Empirica's epistemic hooks. Without `--force`, setup only writes files that don't already exist — so if you've already used Claude Code, the default internals stay in place and Empirica's hooks won't activate.

```bash
empirica setup --force
```

`--force` replaces hooks in `settings.json` but **only removes Empirica's own hooks** — hooks from other plugins (Railway, Superpowers, etc.) are preserved.

### Alternative Installation Methods

<details>
<summary>Homebrew (macOS)</summary>

```bash
brew tap empiricaai/tap
brew install empirica
empirica setup
```
</details>

<details>
<summary>Docker</summary>

```bash
# Security-hardened Alpine image (~276MB, recommended)
docker pull nubaeon/empirica:1.14.16-alpine

# Standard image (Debian slim, ~414MB)
docker pull nubaeon/empirica:1.14.16

# Run
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.14.16 /bin/bash
```
</details>

<details>
<summary>Manual / Other AI Platforms</summary>

```bash
pip install empirica
pip install empirica-mcp        # MCP Server (for Cursor, Cline, etc.)
cd your-project && empirica project-init
```

The CLI works standalone on any platform. The full epistemic workflow (epistemic transactions, Sentinel, calibration) requires loading the system prompt into your AI — the easiest path is `empirica setup`, which wires the lean prompt into `~/.claude/empirica-system-prompt.md` and references it from your `~/.claude/CLAUDE.md`. See [Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md) for details.
</details>

### First Session

```bash
empirica onboard   # Interactive walkthrough of the full workflow
```

Or just start working — with Claude Code hooks active, the AI manages the epistemic workflow automatically.

### Removing it

`empirica setup` writes to eleven locations, and six of them are files Claude Code
owns and writes continuously. So uninstall shows you the plan first and only
removes what is actually ours:

```bash
empirica setup --uninstall           # the plan — names every location, changes nothing
empirica setup --uninstall --apply   # remove, reporting anything it refused
```

| | |
|---|---|
| **Removed** | the plugin directory, our system-prompt file, `active_work.json`, the listener service |
| **Edited** | `settings.json`, `~/.claude.json`, the plugin registries — **our keys only**, yours untouched |
| **Reported, never touched** | the `@include` line in your own `~/.claude/CLAUDE.md` |

That last row is deliberate. Setup appended one line to a file **you** author. If
you have reorganised around it, we cannot tell which edit is safe — so uninstall
tells you the line number and leaves the decision to you.

Two more things it will not do: it refuses rather than writing a shared config it
could not parse, and it refuses if Claude Code modified the file while uninstall
was preparing its write. In both cases nothing is removed and it says why. Your
`.empirica/` project data — findings, goals, git notes — is never touched; this
removes the *integration*, not your epistemic record.

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

**Plans** decompose into **transactions** — one per goal or Claude Code task. Each transaction is a **noetic-praxic loop**: investigate first (noetic), then act (praxic), with the Sentinel gating the transition. Along the way, the AI collects and reads **artifacts** (findings, unknowns, assumptions, dead-ends, decisions, mistakes, falsifiers) while using **semantic search** to surface relevant epistemic patterns and anti-patterns from the project's history. Top artifacts are ranked by confidence and fed into each project's **MEMORY.md** as a hot cache.

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

With Claude Code hooks enabled, you see the AI's epistemic state in real time. The default is one compact line:

```
 empirica  │ CHECK 80% │ G3 U5 A2 F4/D1 │ Δ … │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5
```

| Cell | Meaning |
|------|---------|
| ` empirica ` (white on black) | The practice (project) |
| **CHECK 80%** | Cascade stage and overall confidence. The stage runs `PRE` → `CHECK` → `POST` → `TEST`, and moves to `TEST` once the post-test has graded the transaction |
| **G3 U5 A2** | Open goals, open unknowns, open assumptions |
| **F4/D1** | Findings and decisions logged in *this transaction* |
| **Δ …** | Learning mark from the grounded post-test: `🔥` very good, `✓` good, `-` average, `✗` below average, `…` not graded yet, `?` too little evidence to rate |
| **41%ctx** | Context window used |
| **🔍 investigate / 🔨 act - model** | Whether the work is noetic or praxic right now, then the model |

One command switches to a detailed view, and back:

```bash
echo expanded > ~/.empirica/statusline_mode   # confidence, threshold, phase composite, K/C vectors, Δ
echo compact  > ~/.empirica/statusline_mode   # the default
```

The modes are `compact` (default), `expanded`, `basic`, `learning` and `full`; `EMPIRICA_STATUS_MODE` sets one when the file is absent. All of them are described in [the statusline reference](docs/reference/STATUSLINE_REFERENCE.md).

---

## The 13 Epistemic Vectors

These vectors emerged from 600+ real working sessions across multiple AI systems. They measure the dimensions that consistently predict success or failure in complex tasks.

| Tier | Vector | What It Measures |
|------|--------|------------------|
| **Foundation** | `know` | Domain knowledge depth |
| | `do` | Execution capability |
| | `context` | Access to relevant information |
| **Meta** | `engagement` | Is the AI actively processing or disengaged? |
| | `uncertainty` | Explicit doubt tracking |
| **Phase-dependent** | `clarity` | How clear is the understanding? |
| | `coherence` | Do the pieces fit together? |
| | `signal` | Signal-to-noise in available information |
| | `density` | Information richness |
| | `state` | Current working state |
| | `change` | Rate of progress/change |
| | `completion` | Task completion level |
| | `impact` | Significance of the work |

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

**26 skills ship with the plugin.** They are *lazy*: a skill does nothing until it loads, so what matters is when each one fires. The eight a new reader meets first:

| Skill | Fires when | What it gives you |
|---|---|---|
| `/epistemic-transaction` | Work spans several files or goals | The measured loop: vectors, goals, tasks, and the move from investigating to changing things |
| `/pre-action-grounding` | A request arrives without its why or a checkable done-condition | Investigate first, ask last, then a goal with a done-condition |
| `/epistemic-gardening` | A logged claim is stale or false, or before a release | Resolve, retract and prune, so the graph reflects what is live |
| `/artifact-graph` | About to log several artifacts, or unsure of a type | Typing and connecting findings, assumptions, decisions and mistakes |
| `/forecast` | Deciding what comes next | Directions as graded predictions, with questions only where the evidence is thin |
| `/pre-publish` | About to push, tag or publish | The release-gate sequence, with an independent and traced sweep |
| `/eat-the-broccoli` | Before a release, after a refactor, or when something smells off | A quality sweep plus a hunt for the defects that pass every test |
| `/empirica-constitution` | You are unsure which skill fits | The index: routes a situation to the skill that covers it |

The mesh skills (sending and receiving between practices) ship with the Cortex bundle. All 26, grouped by job: **[Skills reference](docs/reference/SKILLS.md)**

---

## Cross-AI Mesh (Optional Ecosystem Layer)

> **Requires [Empirica Cortex](https://getempirica.com) (proprietary).** Mesh proposals, the persistent listener, the wake service and the `empirica mesh` command cluster all depend on Cortex. They are **not** available in empirica core on its own. If you use one AI in one repo, skip this section: measurement, calibration, artifacts, goals, project-search and Sentinel gating work fully standalone.

The mesh lets practitioners in different practices coordinate as peers. Instead of text-only chat they exchange **epistemic envelopes** that carry calibrated state, source-tagged provenance, noetic/praxic intent and workflow position. It runs on [Empirica Cortex](https://getempirica.com) plus an optional [browser extension](https://getempirica.com) for ECO triage:

```
empirica AI ── proposes work ──► ECO Accept/Decline ──► peer AI wakes + acts
                                                             │
                              completion handshake (commit SHA)
                                                             │
empirica AI ◄────────── outbox/completed event ──────────────┘
```

| Capability | What it does |
|------------|-------------|
| **Practitioner / practice framing** | Practices are epistemic specializations that persist; practitioners (the LLMs) inhabit them. Artifacts accrue to the practice, calibration to the practitioner inhabiting it. See [MESH_CONCEPTS.md](docs/human/end-users/MESH_CONCEPTS.md) |
| **Two proposal flavors** | A noetic flavor (FYI / question / discussion) is auto-accepted. Praxic flavors (code change / architecture / investigation) are **ECO-gated**: they wait for an Accept/Decline decision before the target AI acts |
| **Multi-target proposals** | One proposal addressed to several practices. Each target acknowledges its own part with `empirica mailbox reply`, the proposal closes when every target has, and `pending_targets` shows who still owes. Goals stay per-practitioner. This replaced the Shared Epistemic Record, retired 2026-10-24 |
| **`empirica mailbox reply`** | One verb closes the AI-to-AI handshake atomically: a single-step completion ack carrying the commit SHA, instead of two steps |
| **Persistent listener service** | A systemd-user / launchd daemon holds a push stream open and writes each decided proposal to `loop_fires.log`. A Monitor armed in the session tails that log, so a running session hears the event at once. Claude Code caps a Monitor at 30 minutes, so the session re-arms it and runs `empirica mailbox poll` after each re-arm to cover the gap |
| **Wake service (opt-in)** | `EMPIRICA_LISTENER_WAKE=1` makes the listener nudge a practice's *idle* tmux panes on a mesh event, and every two hours when mail is waiting. A busy pane, a half-typed draft or a dialog is refused, each message wakes at most once, and the woken turn polls the inbox. Off by default |
| **Canonical loops (opt-in)** | Wake-on-event is the standing trigger, so nothing is scheduled by default. Inbox polling exists for harnesses that *cannot* do wake-on-event, and daily housekeeping is a cron loop. Both are opt-in, registered with `empirica loop register`. See [Trigger Model](docs/architecture/TRIGGER_MODEL.md) |
| **Mesh Routing Protocol v0** | Locked four-way with cortex, extension and mesh-support: L1/L2/L3 trust model, server-stamped layer annotation, participant-scoped thread reads |

The `empirica mesh` cluster is the diagnostic and control surface across listener instances and the optional Cortex bridge:

```bash
empirica mesh status              # per-instance health (local + cortex bridge)
empirica mesh diagnose <ai_id>    # deep diagnostic + suggested fix command
empirica mesh restart <ai_id>     # systemd/launchd restart + verify
empirica mesh on|off <ai_id>      # install + start | stop the listener
empirica mesh tail [<ai_id>]      # live-tail loop_fires.log
```

The listener heals itself: an in-process watchdog terminates stale curl streams (TCP-zombie detection at 120 s by default), and HTTP 429 triggers a long backoff while the catch-up poll keeps running.

The browser-side ECO surface (Accept/Decline, inbox triage, publish review) lives in the proprietary [Empirica Extension](https://getempirica.com). The full API for proposals, listener events and the trust pipeline is documented at [getempirica.com](https://getempirica.com).

**Without Cortex, empirica is a single-AI measurement layer.** Core does ship a minimal local `empirica message-*` git-notes primitive for passing notes between your own sessions, but that is note-passing, not the coordinating mesh.

---

## Practice Model + Entity Graph

Empirica's workspace registers the entities it works with (projects, users and the sharing agreements between them) in `entity_registry`, with typed edges in `entity_memberships`. Organisations, contacts and engagements are identity data: the canonical CRM store holds them (see [Empirica Workspace](#the-empirica-ecosystem)), and the registry's copies of those rows are no longer maintained. The **Practice Model** frames the registry consistently:

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

All read-only, all support `--output json`. They back cross-project orchestration and the entity-aware POSTFLIGHT retrospective. For the current state of an organisation, contact or engagement, ask the CRM, not the registry.

---

## Platform Support

**Two harnesses are supported.** Everything else is untested — prompt and rules
files may exist for other tools, but their presence is not support, and we would
rather say so than let you find out mid-project.

| Harness | Status | What you get |
|---|---|---|
| **Claude Code** | **Supported** | Full integration — plugin, hooks, Sentinel gate, skills, agents, statusline, MCP |
| **Codex** (via [ecodex](https://github.com/EmpiricaAI/ecodex)) | **Supported** | Self-provisioning: ecodex vendors the plugin into its own binary and loads hooks natively. `empirica setup` refuses codex **by name** and points you at ecodex's pipeline — that refusal is correct, not a gap |
| Antigravity (Google), Vibe (Mistral AI) | **Not yet** | Possible future support. Not now |
| Everything else | **Untested** | The CLI works anywhere Python does, but no harness integration is verified |

### MCP is a fallback, not a second path

`empirica-mcp` exists for harnesses that **cannot** run the CLI integration —
Claude Desktop, Gipitee-style clients. Use it when you have no alternative.

The reason matters: an MCP surface cannot enforce the Sentinel gate the way a
blocking pre-tool hook does. So an MCP-only deployment gives you the measurement
layer with a **weaker guarantee** — the noetic/praxic firewall becomes advisory.
That is a real difference in what the system promises, and it should be a
deliberate choice rather than something you infer from a feature table.

---

## Documentation & Training

| Resource | What It Covers |
|----------|---------------|
| **[getempirica.com](https://getempirica.com)** | Training course, interactive guides, deep explanations |
| **[Natural Language Guide](docs/human/end-users/EMPIRICA_NATURAL_LANGUAGE_GUIDE.md)** | How to collaborate with AI using Empirica |
| **[Getting Started](docs/human/end-users/01_START_HERE.md)** | First-time setup and concepts |
| **[CLI Reference](docs/human/developers/CLI_COMMANDS_UNIFIED.md)** | All 240+ commands documented |
| **[Cockpit guide](docs/guides/COCKPIT.md)** | One command brings up a tmux layout of all your practices, keeps each one's identity, and restarts any that died |
| **[Architecture](docs/architecture/)** | Technical reference for contributors |
| **[Claude Code Setup](docs/human/developers/CLAUDE_CODE_SETUP.md)** | Install + system prompt + plugin wiring |
| **[Changelog](CHANGELOG.md)** | Full release history — every version since 1.0 |
| **[Upgrade to 1.14](docs/guides/UPGRADE_TO_1.14.md)** | Migration guide for the 1.13.x → 1.14 jump — one behaviour change that can refuse work, and it refuses in the safe direction. Older guides (1.9–1.13) live in [docs/guides/](docs/guides/) |

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

## What's New in 1.14.16

- **A vector nobody assessed is stored as "not assessed", not as 0.5.** `store_vectors` filled every missing vector with 0.5, and every reader then took the 0.5 for a measurement. Measured on this project's last 60 days: PREFLIGHT and POSTFLIGHT supply all 13 vectors, but CHECK supplies 7.0 on average (as few as 2), and all 53 CHECKs with missing vectors had the gaps stored as 0.5.
- **Every reader of those columns was audited first, and the ones that broke are fixed.** Two read-only audits covered about 80 read sites, and three independent reviews of the change found the ones they missed. The writer change was applied last, after the readers could take a NULL.
- **A missing value no longer turns into a permissive recommendation.** A handoff for a CHECK that skipped uncertainty said "Ready for execution"; it now asks for uncertainty to be assessed. The dashboard gate fails closed when the latest check did not assess uncertainty, and stagnation is no longer detected from values nobody assessed.
- **Where a value is needed, the last value actually recorded is used.** Session end, session create's auto-close, the pre-compact snapshot, the statusline and the dashboard take each vector's newest recorded value instead of a default. The auto-close also stopped rewriting a genuine 0.0 to a default.
- **Commands that crashed on a sparse CHECK now work:** `goals-ready`, the compact analysis, the text form of `epistemics-show`, `project-bootstrap` after an auto-closed session, and `handoff-create`.

All 12 entries for this release, with the measurements behind each highlight, are in the [CHANGELOG](CHANGELOG.md).

**Earlier releases**

- **1.14.15** — A code-review sweep of the data layer, hooks and mailbox is fixed. Each defect was reproduced on the current code before the fix and has a test that failed…
- **1.14.14** — Idle sessions can be woken (opt-in). A listener flag nudges a practice's idle tmux panes when mail arrives and every two hours while mail waits.

## What's Coming

Empirica's own forecast, made in 1.14.14 from its open work, the demand it has measured and its known weak spots. Each prediction carries a confidence, the evidence behind it and the observation that would prove it wrong, and is graded when it ships or is dropped. The word beside the confidence says how the basis is known: **measured** by a command, **reviewed** in the code, **reported** by someone else, or **inferred**; weaker evidence cannot carry a higher confidence. **planned** means a goal for it already existed when we predicted it: those are scored separately, because delivering planned work is not foresight.

| Direction | Confidence | Why we expect it | What would change our mind |
|---|---|---|---|
| **A run of correctness releases**: The next two releases fix at least a third of a backlog of 78 reproduced defects, and their changelogs say how many. | 85% (measured, planned) | A systematic sweep reproduced 78 medium-or-high defects, summed from seven open fix goals covering the data layer, the transaction handlers, resolvers, sync, chat, the Sentinel and the mailbox. None of the seven has a completed task yet. | The next two changelogs together cite fewer than 26 of them. |
| **Session start reports a skipped budget save**: When session start cannot save the context-budget state, the banner says so instead of only recording it. | 80% (reviewed, planned) | Read in the session-init hook, the skip reason is recorded in the budget summary but the banner prints only the budget line. The deploy-gap check already skips outside Claude Code, so that half of the gap is closed. | A session start with no resolvable sessions.db prints a banner that does not mention the unsaved budget state. |
| **Bootstrap shows the whole picture**: project-bootstrap lists open assumptions, includes the links between artifacts, and says when it capped findings, decisions, dead ends or mistakes. | 75% (measured, planned) | Measured on this project today, it shows 10 of 3,173 findings, 10 of 758 decisions, 5 of 835 dead ends and 5 of 447 mistakes with no notice (goals and unknowns do get one), none of the 115 open assumptions, and only project-level relations, not the links between artifacts. | A bootstrap run on a graph larger than its caps that omits open assumptions or caps findings without saying so. |
| **Listener memory stays flat**: Within three releases a listener's memory stops growing with uptime, and a dead notification stream is restarted. | 65% (reported, planned) | Operators of long-running listeners report about 30 MB an hour in one case and over 1 GB at 29 hours in another, with a stream process that was not respawned after a drop. We have not yet reproduced it on a fresh unit. | On a release that claims the fix, a listener's memory grows by more than 50 MB between hour 4 and hour 24 of uptime. |
| **Restarted cockpit panes rejoin the mesh unprompted**: After a cockpit restart, resumed sessions re-arm their listener and catch up on mail without anyone typing, using the wake and inbox poll added in 1.14.14. | 55% (reported, planned) | Reported after a restart, 1 of about 12 resumed sessions armed its listener and another first armed 68 minutes after resume. Read in the code, cockpit refresh does not yet call the wake or the inbox poll. | After a cockpit restart on a release that claims it, more than 2 of 12 resumed sessions are still unarmed 5 minutes later. |
| **Wake service on by default**: The wake service stops being opt-in once a few weeks of live use show no wake into a busy pane. | 35% (measured) | It passed a live trial on two seats and refuses busy panes, drafts and dialogs. Open edge cases are an image-only prompt that can read as blank and a spinner scrolled out of view that reads as idle. | A report of a wake that interrupted a running turn or submitted a half-typed draft. |

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
**Version:** 1.14.16

*Turtles all the way down — built with its own epistemic framework, measuring what it knows at every step.*
