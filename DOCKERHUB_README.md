# Empirica

**Cognitive OS for AI Agents — Epistemic self-awareness framework**

Teaching AI to know what it knows—and what it doesn't.

[![GitHub](https://img.shields.io/badge/GitHub-EmpiricaAI%2Fempirica-blue)](https://github.com/EmpiricaAI/empirica)
[![PyPI](https://img.shields.io/pypi/v/empirica)](https://pypi.org/project/empirica/)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/EmpiricaAI/empirica/blob/main/LICENSE)

---

## Quick Start

```bash
# Recommended: Security-hardened Alpine
docker pull nubaeon/empirica:1.6.6-alpine

# Alternative: Debian slim (bash/glibc compatibility)
docker pull nubaeon/empirica:1.6.6

# Run with persistent data
docker run -it -v $(pwd)/.empirica:/data/.empirica nubaeon/empirica:1.6.6-alpine /bin/sh
```

---

## The Problem

AI agents exhibit **confident ignorance** — they generate plausible-sounding responses about topics they don't actually understand.

- Hallucinated facts presented as truth
- Same mistakes repeated across sessions
- Knowledge lost on context window reset
- No way to tell genuine confidence from bluffing

## The Solution

Empirica introduces **epistemic vectors** — quantified measures of knowledge state (0.0-1.0) that AI agents track in real-time. Actions are gated until confidence thresholds are met.

**Readiness Gate:** The Sentinel assesses readiness holistically — investigation mode when confidence is low, action mode when ready.

---

## The 13 Epistemic Vectors

Emerged from 600+ real working sessions across Claude, GPT-4, Gemini, Qwen. Universal pattern regardless of model.

| Tier | Vector | What It Measures |
|------|--------|------------------|
| **Gate** | `engagement` | Is the AI actively processing or disengaged? |
| **Foundation** | `know` | Domain knowledge depth |
| | `do` | Execution capability |
| | `context` | Access to relevant information |
| **Comprehension** | `clarity` | How clear is the understanding? |
| | `coherence` | Do the pieces fit together? |
| | `signal` | Signal-to-noise ratio |
| | `density` | Information richness |
| **Execution** | `state` | Current working state |
| | `change` | Rate of progress |
| | `completion` | Task completion level |
| | `impact` | Significance of the work |
| **Meta** | `uncertainty` | Explicit doubt (tracked separately because AI underreports it) |

---

## CASCADE Workflow

Every significant task follows this loop:

```
PREFLIGHT ────────► CHECK ────────► POSTFLIGHT
    │                 │                  │
 Baseline         Sentinel           Learning
 Assessment         Gate              Delta
    │                 │                  │
 "What do I      "Am I ready       "What did I
  know now?"      to act?"          learn?"
```

**Learning compounds across sessions:**

```
Session 1: know=0.40 → know=0.65  (Δ +0.25)
    ↓ (findings persisted)
Session 2: know=0.70 → know=0.85  (Δ +0.15)
    ↓ (compound learning)
Session 3: know=0.82 → know=0.92  (Δ +0.10)
```

No more re-investigating the same questions.

---

## Model Agnostic

Works with any LLM: Claude, GPT, Mistral, Llama, Qwen, or local models.

Session handoffs between different models preserve epistemic state. Start in Claude Code, continue in a local Mistral — the framework doesn't care.

---

## Storage Architecture

**Local-first. No cloud dependencies. No telemetry.**

| Layer | Storage | Purpose |
|-------|---------|---------|
| Hot | SQLite | Active sessions, vectors |
| Warm | Git Notes | Checkpoints, handoffs |
| Search | Qdrant (optional) | Semantic memory retrieval |
| Cold | YAML | Procedural lessons |

Data in `.empirica/` directory, gitignored by default.

---

## Docker Usage

```bash
# CLI help
docker run --rm nubaeon/empirica:1.6.6-alpine empirica --help

# Interactive shell
docker run -it --rm nubaeon/empirica:1.6.6-alpine /bin/sh

# With persistent data volume
docker run -it \
  -v $(pwd)/.empirica:/data/.empirica \
  nubaeon/empirica:1.6.6-alpine \
  empirica session-create --ai-id docker-agent

# Check version
docker run --rm nubaeon/empirica:1.6.6-alpine empirica --version
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EMPIRICA_HOME` | Data directory | `/data/.empirica` |
| `EMPIRICA_QDRANT_URL` | Qdrant server for semantic memory | (none) |
| `EMPIRICA_WORKSPACE_ROOT` | Workspace root for multi-agent | (none) |

## Image Variants

| Tag | Base | Size | Use Case |
|-----|------|------|----------|
| `1.6.6-alpine` | Alpine 3.23 | ~85MB | Production, security-hardened |
| `1.6.6` | Debian Bookworm | ~150MB | Development, glibc compatibility |

---

## Key Features in 1.6.6

- **Dynamic Sentinel Control** — File-based flag for runtime enable/disable without restart
- **Epistemic Transaction Planning** — Interactive plan-transactions mode for structured task decomposition
- **Grounded Calibration** — Dual-track self-assessment vs objective evidence verification
- **Multi-Platform Support** — Claude Code, Gemini CLI, Cline, Copilot CLI, Kiro, Cursor
- **Cognitive Immune System** — New learnings decay stale knowledge
- **Semantic Memory** — Qdrant-backed eidetic and episodic retrieval

---

## Why Docker?

- **Isolated epistemic environments** — Each container has its own knowledge state
- **Multi-agent orchestration** — Spawn investigation agents in parallel containers
- **CI/CD integration** — Epistemic testing in pipelines
- **Reproducible sessions** — Same container, same baseline

---

## Documentation

| Resource | Link |
|----------|------|
| Full Documentation | [github.com/EmpiricaAI/empirica/docs](https://github.com/EmpiricaAI/empirica/tree/main/docs) |
| CLI Reference | [CLI_COMMANDS_UNIFIED.md](https://github.com/EmpiricaAI/empirica/blob/main/docs/human/developers/CLI_COMMANDS_UNIFIED.md) |
| CASCADE Workflow | [CASCADE_WORKFLOW.md](https://github.com/EmpiricaAI/empirica/blob/main/docs/architecture/CASCADE_WORKFLOW.md) |
| Getting Started | [getempirica.com](https://getempirica.com) |

---

## License

MIT License

**GitHub:** [github.com/EmpiricaAI/empirica](https://github.com/EmpiricaAI/empirica)

**Website:** [getempirica.com](https://getempirica.com)
