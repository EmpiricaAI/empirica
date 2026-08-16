# Empirica

> **We Gave AI a Mirror. Now It Measures What It Believes.**

[![Version](https://img.shields.io/badge/version-1.13.21-blue)](https://github.com/EmpiricaAI/empirica/releases/tag/v1.13.21)
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
| **Optional: coordinates with peer AIs** | Cross-Claude mesh via Cortex (opt-in) — peer AIs propose work, ECO accepts/declines, completion handshakes carry commit SHAs. A persistent listener wakes idle sessions on inbox events. **Empirica core works standalone without this** — see [Cross-AI Mesh](#cross-ai-mesh-optional-ecosystem-layer) below for the ecosystem layer |

---

## How You Use It

You talk to your AI normally. Empirica works in the background:

```
You:      "Fix the authentication bug in the login flow"

Empirica: [AI investigates → logs findings → passes Sentinel gate → implements fix → measures learning]

You see:  ⚡87% ↕70% │ 🎯1 │ POST 🔍85% │ K:88% C:82% │ Δ +K
```

**You direct. The AI measures.**

Empirica's CLI has 280 commands spanning investigation, measurement, calibration, and memory — like a cockpit instrument panel. You don't need to learn any of them. The AI reads the instruments, operates the controls, and reports back in natural language. The statusline gives you the flight data at a glance.

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
docker pull nubaeon/empirica:1.13.21-alpine

# Standard image (Debian slim, ~414MB)
docker pull nubaeon/empirica:1.13.21

# Run
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.13.21 /bin/bash
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

**18 skills ship with the plugin** — the transaction discipline, graph gardening,
the quality sweep, mesh messaging, and more. They are *lazy*: a skill does
nothing until it loads, so knowing when each fires matters more than knowing
what it holds. → **[Skills reference](docs/reference/SKILLS.md)**

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
| **Canonical loops (opt-in)** | Wake-on-event is the standing trigger, so nothing is scheduled by default. Inbox polling (30s adaptive) exists for harnesses that *cannot* do wake-on-event, and daily housekeeping is a cron loop — **both are opt-in**, registered with `empirica loop register`. See [Trigger Model](docs/architecture/TRIGGER_MODEL.md) |

The browser-side ECO surface (Accept/Decline, inbox triage, publish review) lives in the proprietary [Empirica Extension](https://getempirica.com). The full API surface for proposals, listener events, and the trust pipeline is documented at [getempirica.com](https://getempirica.com).

---

## Mesh + Shared Epistemic Record (1.11.0)

> **Requires [Empirica Cortex](https://getempirica.com) (proprietary).** Everything in this section — mesh proposals, the persistent listener, the Shared Epistemic Record, and the `empirica mesh` command cluster — is a Cortex-served layer. It is **not** available in empirica core on its own; without Cortex, empirica is a single-AI measurement layer.

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

**Without Cortex, empirica is a single-AI measurement layer** — the proposals, listener, SER, and `empirica mesh` cluster above are all Cortex-dependent. Core does ship a minimal local `empirica message-*` git-notes primitive for passing notes between your own sessions, but that is note-passing, not the coordinating mesh. Everything that makes empirica valuable on its own — measurement, calibration, artifacts, goals, project-search, sentinel gating — works fully standalone.

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
| **[CLI Reference](docs/human/developers/CLI_COMMANDS_UNIFIED.md)** | All 280 commands documented |
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

## What's New in 1.13.21

- **Sentinel chain classifier over-gated multi-line noetic commands** (two live repros). A `cd <path>` on its own line before a heredoc command fell to single-command classification (saw only the `cd`) and gated — including the dedicated noetic primitive `empirica noetic-batch`. And heredoc detection was a naive substring test, so a *quoted* `<<` in a grep pattern suppressed newline-splitting for a multi-line command of pure reads. Both fixed (cd-normalization + quote-aware detection); `noetic-batch` is additionally in the always-open recovery set — investigation is the remedy every gate prescribes, so the tool that performs it can never be blocked by one. Three negative controls pin that mutating shapes still gate.
- **PREFLIGHT/CHECK injected context served completed goals as live.** The goal reconciler was vacuous: `embed_goal` never wrote `goal_id` into the Qdrant payload, so the id lookup always came up empty and stale `in_progress` payloads sailed through. Payload now carries the id, the reconciler gains an objective-text fallback for pre-fix points (no re-sync needed), subtask rows are type-aware (parent completion drops them; parent status never overwrites theirs), and `goals-complete` now mirrors completion to Qdrant (`update_goal_status` gains its first caller, with `complete`/`completed` normalization). A producer-contract test pins the payload.
- **Gardening nudge in the transaction retrospective.** Open unknowns and unverified assumptions under the goal(s) in play — logged in an earlier transaction — surface at CHECK/POSTFLIGHT (and echo into the next PREFLIGHT), giving the gardening reflex the same structural footing as the weave gate and proposal-ack notes. Three bounds keep it high-signal: goal-scoped (never a whole-graph scan), freshness (this transaction's artifacts are current work, not stale), lifecycle-typed (unknowns + unverified assumptions only).
- **PREFLIGHT unknowns suggestion is scoped + actionable.** Was a session-wide bare count repeating verbatim forever on long sessions; now counts unknowns under in-progress goals and names ids, so it self-clears as goals close.
---

## What's New in 1.12.35

- **`docs-assess` tech_docs rewarded name-dropping.** `_check_if_documented` counted a feature as documented if its NAME substring-matched the concatenated markdown, so the metric was satisfiable by dumping class names into a `.md` and unmovable by real documentation — inverting the EU AI Act Art. 11 / ISO 7.5 intent it is framed against. Measured by empirica-workspace: 137 accurate docstrings moved coverage **0%**, while a generated file listing 256 names took it to **100%**. A feature now counts when it has a substantive docstring OR the docs carry real prose about it (the mention's line must retain ≥8 words once list/table/heading punctuation and the name are stripped). The OR is deliberate: gating on docstrings alone would penalise practices that document in markdown. `check_docstrings` now also returns `documented_symbols` — it already computed that truth to count `documented_items` but never named them, so the docstring half had nothing to consult.
- **A listed source could 404 on `/content`.** The sources LIST reads the whole per-practice DB while the content lookup was still scoped to one `project_id`, so a source with a drifted id appeared in the pane and failed the instant it was opened (10 of 50 on one practice). Both are practice-scoped now.
- **`docs-assess` crashed on a stale project pointer.** `_auto_detect_project_config` called `iterdir()` on a path taken from resolver state without checking it exists — resolver state outlives the directory it names, and a pointer to a deleted project took the whole command down.
- **Review stamping could break the audit.** The new cadence opened a session DB unconditionally, so an environment without one (CI, a bare checkout) failed the check instead of simply not recording.
- **`empirica setup`** — harness-neutral name for `setup-claude-code`, which stays as an alias. The old name leaked "claude-code" into model-facing prose (hook errors, skill docs) that other harnesses vendor verbatim; the instruction text is swept too. One parser, one handler, two names — no new capability.
- **`sources-check` sees LOCAL file rot.** It gated on http(s), so file-backed sources were skipped entirely — one practice reported "all probed source links resolve" while 25 of its 50 sources could not be served. Non-URL sources are now classified `ok` / `missing` / `not_a_locator` (the column holds a title, needing a re-point rather than a file hunt) / `out_of_scope` (a `mailto:`/`ftp:`/`doi:` URI the disk check cannot speak to). Path resolution mirrors the daemon's, pinned by a test.
- **`source-update --url`** — re-point a source whose file MOVED. Gardening is prune *and* replant, but the CLI could only re-fetch, never re-target, so a moved doc had to be archived and re-added — losing its id and with it every `sourced_from` edge pointing at it. `--url` retargets in place, recomputes content identity, and records a `repointed` event in `lifecycle_audit_log` so the move is traceable.
- **A review cadence.** `sources-check` now records a timestamped verdict per source (`last_reviewed_at`, `review_verdict`) — previously nothing ever wrote those columns (0 of 63 reviewed), so "unchecked since X" was unanswerable and a source could never be more than an assertion with a date on it.
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
**Version:** 1.13.21

*Turtles all the way down — built with its own epistemic framework, measuring what it knows at every step.*
