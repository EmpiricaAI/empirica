# Empirica Example Agents

Practical agents that demonstrate epistemic measurement in action. Each agent investigates before acting, tracks what it knows and doesn't know, and produces results with calibrated confidence.

## What Makes These Different

Most AI agents give you answers. These agents give you **answers + the investigation trail + confidence levels + explicit unknowns**. You know exactly what the agent is sure about and where it's guessing.

## Agents

### For Developers

| Agent | What It Does |
|-------|-------------|
| [Codebase Onboarder](codebase-onboarder/) | Investigates an unfamiliar repo, maps architecture with confidence levels, tells you what it understands AND what it doesn't |
| [Token Budget](token-budget/) | Analyzes your Claude Code session transcripts, identifies context waste patterns, recommends specific optimizations to reduce token usage |

### For Business Operators

| Agent | What It Does |
|-------|-------------|
| [Missed Opportunities](missed-opportunities/) | Investigates your business data, forms hypotheses, tests them, reports opportunities with calibrated confidence scores |
| [Competitor Monitor](competitor-monitor/) | Checks competitor websites for changes, learns what matters to YOUR business over time, gets smarter with each run |

## Quick Start

```bash
# Install Empirica
pip install empirica
empirica setup-claude-code --force

# Copy an agent to your plugin
cp examples/codebase-onboarder/agent.md ~/.claude/plugins/local/empirica/agents/

# Use it in Claude Code
# "Use the codebase-onboarder agent to investigate this repo"
```

## The Epistemic Difference

Every agent uses Empirica's artifact system during investigation:

- **Findings** — what it discovered, with impact scores
- **Unknowns** — what it couldn't determine (honest gaps)
- **Assumptions** — what it's guessing, with confidence levels
- **Dead-ends** — what it tried and didn't work (saves you from repeating)
- **Decisions** — choice points with rationale

This means:
- You can SEE the investigation process, not just the conclusion
- The agent gets smarter across sessions (findings persist)
- You know exactly where to dig deeper (unknowns are explicit)
- Failed approaches are recorded (no repeating mistakes)

## Requires

- [Empirica](https://github.com/EmpiricaAI/empirica) (`pip install empirica`)
- Claude Code with Empirica plugin (`empirica setup-claude-code --force`)
