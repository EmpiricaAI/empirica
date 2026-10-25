# Empirica

> **We Gave AI a Mirror. Now It Measures What It Believes.**

[![Version](https://img.shields.io/badge/version-1.14.14-blue)](https://github.com/EmpiricaAI/empirica/releases/tag/v1.14.14)
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
| **Prevents confident mistakes** | The CHECK gate uses domain-aware thresholds scaled by criticality, tightened by the calibration of the model doing the work — cybersec/high is stricter than default/low |
| **Shows confidence in real-time** | Live statusline in your terminal, compact by default: ` empirica  │ CHECK 78% │ G3 U5 A2 F4/D1 │ Δ ✓ │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5` (stage and confidence, open goals/unknowns/assumptions, what you logged this transaction, a learning mark, context, and investigate or act). One command switches to the detailed view |
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
docker pull nubaeon/empirica:1.14.14-alpine

# Standard image (Debian slim, ~414MB)
docker pull nubaeon/empirica:1.14.14

# Run
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.14.14 /bin/bash
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

**25 skills ship with the plugin** — the transaction discipline, graph gardening,
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

## Mesh

> **Requires [Empirica Cortex](https://getempirica.com) (proprietary).** Everything in this section — mesh proposals, the persistent listener, and the `empirica mesh` command cluster — is a Cortex-served layer. It is **not** available in empirica core on its own; without Cortex, empirica is a single-AI measurement layer.

The cross-AI coordination layer. Practitioners in different practices coordinate not via text-only chat but via **epistemic envelopes** that carry calibrated state, source-tagged provenance, noetic/praxic intent, and workflow position.

- **Practitioner / practice** framing — practices are epistemic specializations that persist, and practitioners (the LLMs) inhabit them. Artifacts accrue to the practice; calibration accrues to the practitioner inhabiting it. See [MESH_CONCEPTS.md](docs/human/end-users/MESH_CONCEPTS.md).
- **Multi-target proposals** — one proposal addressed to several practices; each target acknowledges its own part (`empirica mailbox reply`), the proposal closes when every target has, and `pending_targets` shows who still owes. Goals stay per-practitioner. (This replaced the Shared Epistemic Record, retired 2026-10-24.)
- **`empirica mesh` command cluster** — unified diagnostic + control surface across listener instances + the optional cortex bridge:
  ```bash
  empirica mesh status              # per-instance health (local + cortex bridge)
  empirica mesh diagnose <ai_id>    # deep diagnostic + suggested fix command
  empirica mesh restart <ai_id>     # systemd/launchd restart + verify
  empirica mesh on|off <ai_id>      # install + start | stop the listener
  empirica mesh tail [<ai_id>]      # live-tail loop_fires.log
  ```
- **Listener self-heal** — in-process watchdog terminates stale curl streams (TCP-zombie detection at 120s by default); HTTP 429 detection applies long backoff with catch-up poll continuing during the window.
- **Mesh Routing Protocol v0** locked four-way with cortex + extension + mesh-support. L1/L2/L3 trust model, server-stamped layer annotation, participant-scoped thread reads.

**Without Cortex, empirica is a single-AI measurement layer** — the proposals, listener, and `empirica mesh` cluster above are all Cortex-dependent. Core does ship a minimal local `empirica message-*` git-notes primitive for passing notes between your own sessions, but that is note-passing, not the coordinating mesh. Everything that makes empirica valuable on its own — measurement, calibration, artifacts, goals, project-search, sentinel gating — works fully standalone.

---

## Practice Model + Entity Graph

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

## What's New in 1.14.14

- **`/pre-publish`**, the release-gate sequence as a loadable skill: scope the diff, deterministic gates, an independent read-only sweep whose every finding you trace, hot-path timing, a clean re-run on the exact commit, then the cut. It carries the rules the 1.14.13 sweep earned (a second pass must be clean; grep for an existing guard before writing one; sweep a new destructive command before it touches real data) and has empirica's release steps in a reference file.
- **The POSTFLIGHT response carries a `reply_shape` line** (Done / Next / Waiting with predicted answers), because `/reporting-discipline` was loaded 3 times against about 4,970 POSTFLIGHTs.
- **`/artifact-graph`, `/practice-model`, `/authoring-guidance`**: the constitution split by the moment each part is needed.
- **`wake_instance` only types into a pane that is waiting for input.** It reads the pane first (`pane_is_idle`) and refuses a running turn, a half-typed draft, a permission dialog or an unreadable pane, because Space+Enter accepts a dialog's default option and submits a draft. The Events button reports a refusal instead of a failure; `require_idle=False` keeps the old unconditional send. The pane is read with its styling, so Claude Code's dim prompt suggestion is named as a suggestion and a typed draft as a draft; a wake lands over a suggestion only with `allow_suggestion` (`EMPIRICA_WAKE_OVER_SUGGESTION=1` for the listener service), never over a draft.
- **A wake turn polls the inbox.** `wake_instance` types a short visible marker, `[wake]`, and Enter (a blank Space+Enter starts no turn: tmux reports success and Claude Code ignores it, measured live), and on that prompt `tool-router` injects the practice's pending mesh messages and spells out three steps (react to the messages, re-arm the Monitor via `listener on --dry-run` and `listener arm`, poll once more), then tells the session to stop: a woken session otherwise keeps working (measured: 59 s of unrequested work). Any other prompt costs nothing extra. Wake log lines carry a UTC timestamp, because the listener's stderr is a file and an untimed line cannot be compared with `armed_at`.
- **An opt-in wake service in the listener** (`EMPIRICA_LISTENER_WAKE=1`, off by default). On a mesh event, and every two hours when the inbox holds mail it has not already surfaced, it wakes the practice's idle panes (a busy pane, a draft or a dialog is refused), and the woken turn polls the inbox. Each message causes at most one successful wake; the listener's per-event cost with the flag off is a dictionary lookup.
- **The listener and practitioner heartbeats outlast a cortex restart** (cortex, 2026-10-23: cortex now QUEUES connections for the ~5 s a restart takes instead of refusing them). Their post timeout is 12 s, up from exactly 5.0 s, and the practitioner emitter stops its cycle at the first no-answer instead of timing out once per live session, then retries on the next interval. The liveness probe (8 s timeout, exits only after 240 s without a success) and the catch-up fetch (10 s) already complied.
- **`provision-practice` and the cockpit work for practices that do not run Claude** (ecodex, David's rulings 2026-10-24). `--dry-run` narration (`[dry-run] would run:` and `would patch`) goes to stderr, so `--output json` stdout is exactly the JSON. New `--launch` and `--resume` (they need `--cockpit-profile` and are refused without it, not ignored) say what the pane runs and how `cockpit refresh` brings it back; the profile no longer hardcodes `claude`. `cockpit refresh` resumes a `codex` or `ecodex` pane with `<prog> resume --last` instead of starting a fresh session (checked by ecodex against its v0.164.1 source: right for flag-only launches; a launch ending in a prompt or wrapped in a shell needs an explicit `--resume`). `--no-cortex` no longer requires `--tenant`/`--org`: a local practice has no tenant to name, and none is written as an empty key.
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
**Version:** 1.14.14

*Turtles all the way down — built with its own epistemic framework, measuring what it knows at every step.*
