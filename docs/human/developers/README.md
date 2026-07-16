# Developer Documentation

**Technical guides for integrating AI assistants with Empirica + building on
the foundation layer.**

---

## Quick Start

| You want to | Start here |
|---|---|
| Set up Claude Code | [CLAUDE_CODE_SETUP.md](CLAUDE_CODE_SETUP.md) |
| Read the live system prompt | `~/.claude/empirica-system-prompt.md` (after `empirica setup-claude-code`) |
| Browse the CLI | [CLI_COMMANDS_UNIFIED.md](CLI_COMMANDS_UNIFIED.md) (see currency disclaimer at top) |
| Build a skill | [skills/SKILL_PIPELINE.md](skills/SKILL_PIPELINE.md) |
| Build on the foundation layer | [EXTENDING_EMPIRICA.md](EXTENDING_EMPIRICA.md) |

---

## AI Integration

| Guide | Purpose |
|---|---|
| [CLAUDE_CODE_SETUP.md](CLAUDE_CODE_SETUP.md) | Hooks, statusline, MCP server — what `setup-claude-code` does + manual fallback |
| [AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md) | Self-serve docs, prompt-improvement protocol, source-of-truth paths |
| [MULTI_SESSION_LEARNING.md](MULTI_SESSION_LEARNING.md) | How learning compounds across sessions + AIs |

---

## System Prompts

The live system prompt is generated from a single lean source and
deployed by `empirica setup-claude-code`:

```
empirica/plugins/claude-code-integration/templates/
└── empirica-system-prompt-lean.md   # the system-prompt template (lean core, @included from CLAUDE.md)
```

After install:

- `~/.claude/empirica-system-prompt.md` — lean prompt, overwritten on each setup
- `~/.claude/CLAUDE.md` — includes the above via `@~/.claude/empirica-system-prompt.md`; preserves any user overrides

The previous `CANONICAL_CORE.md + model_deltas/` pipeline was retired in
1.7 in favor of lean-prompt-by-default. See
[AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md) for the current
source-of-truth paths.

---

## Reference

| Guide | Purpose |
|---|---|
| [CLI_COMMANDS_UNIFIED.md](CLI_COMMANDS_UNIFIED.md) | Full CLI reference (partial currency — see disclaimer) |
| [MCP_SERVER_REFERENCE.md](MCP_SERVER_REFERENCE.md) | MCP server tool catalog + architecture |
| [EPISTEMIC_HEALTH_QUICK_REFERENCE.md](EPISTEMIC_HEALTH_QUICK_REFERENCE.md) | Bootstrap reading + work_type routing one-pager |

---

## Building on Empirica

| Guide | Purpose |
|---|---|
| [EXTENDING_EMPIRICA.md](EXTENDING_EMPIRICA.md) | Foundation layer: EpistemicBus, repositories, plugins |
| [TRAINING_WITH_EMPIRICA.md](TRAINING_WITH_EMPIRICA.md) | Export epistemic transactions for fine-tuning calibration |
| [skills/SKILL_PIPELINE.md](skills/SKILL_PIPELINE.md) | Verbose → condensed skill extraction |

---

## Integrations

| Guide | Purpose |
|---|---|
| [BEADS_INTEGRATION_DESIGN.md](BEADS_INTEGRATION_DESIGN.md) | Architecture for BEADS pairing + the goals-claim / goals-complete git bridge (branch mapping) |

---

## Key Concepts

### CASCADE Workflow

```
PREFLIGHT → CHECK → noetic → praxic → POSTFLIGHT (+ grounded verification)
```

Both investigation and implementation happen within the same transaction.
POSTFLIGHT triggers deterministic services (tests, git, code-quality,
codebase model) which surface divergence between the AI's belief vectors
and observable outcomes.

### The 13 Vectors

| Role | Vectors |
|---|---|
| **Foundation** (feasibility) | `know`, `do`, `context` |
| **Meta** (self-assessment quality) | `engagement`, `uncertainty` |
| **Phase-dependent** (weighted by `work_type`) | `clarity`, `coherence`, `signal`, `density`, `state`, `change`, `completion`, `impact` |

Full reference: [../end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md](../end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md).

### Readiness Gate

The Sentinel computes thresholds dynamically from calibration data in
`.empirica/breadcrumbs.yaml` (per-AI). **There are no fixed cutoffs.**
Good calibration → looser gates → more autonomy. Drift between belief
and grounded observation → tighter gates.

---

## See Also

- **Architecture details:** [../../architecture/](../../architecture/)
- **API reference:** [../../reference/](../../reference/)
- **End-user docs:** [../end-users/](../end-users/)
