# Codebase Onboarder

An epistemic agent that investigates unfamiliar codebases and produces structured understanding with calibrated confidence levels.

## What Makes This Different

Most AI code assistants read your codebase and give you a summary. This agent **investigates** — it forms hypotheses, tests them, tracks what it doesn't know, and tells you exactly how confident it is about each part of its understanding.

The investigation trail is as valuable as the output. You can see HOW the agent understood your codebase, not just WHAT it concluded.

## Usage

```bash
# Copy agent to your Claude Code plugins
cp agent.md ~/.claude/plugins/local/empirica/agents/codebase-onboarder.md

# Or just reference it directly in conversation:
# "Use the codebase-onboarder agent to investigate this repo"
```

## What You Get

- **Architecture map** with confidence levels per component
- **Key patterns** identified with evidence
- **Explicit unknowns** — what the agent couldn't figure out
- **Assumptions** — what it's guessing and needs verification
- **Investigation trail** — findings, dead-ends, and decisions

## Example Output

```
# Codebase Understanding: acme-api

## What This Project Does
REST API for managing customer orders with Stripe payment integration. (confidence: 0.9)

## Architecture
- routes/ — Express route handlers (confidence: 0.95)
- models/ — Sequelize ORM models (confidence: 0.9)
- middleware/ — Auth + rate limiting (confidence: 0.85)
- services/payment.js — Stripe integration (confidence: 0.7 — only read the interface, not the implementation)
- jobs/ — Background workers (confidence: 0.5 — found the directory but couldn't determine the queue system)

## What I Don't Know
- How deployment works (no Dockerfile, no CI config found)
- What queue system the background jobs use
- Whether there's a separate frontend or if this is API-only

## Assumptions Made
- Database is PostgreSQL (based on Sequelize dialect config, confidence: 0.8)
- Auth uses JWT (found jsonwebtoken in package.json, confidence: 0.9)
- No WebSocket support (didn't find socket.io or ws, but might have missed it, confidence: 0.6)
```

## Requires

- [Empirica](https://github.com/EmpiricaAI/empirica) installed (`pip install empirica`)
- Claude Code with Empirica plugin (`empirica setup-claude-code --force`)
