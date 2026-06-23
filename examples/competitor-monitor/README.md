# Competitor Monitor

An epistemic agent that checks competitor websites for changes and learns what matters to your specific business over time.

## What Makes This Different

Generic website monitors tell you "the page changed." This agent tells you "the page changed AND here's why it matters (or doesn't) to YOUR business." It gets smarter with each run because Empirica's cross-session memory remembers what you cared about last time.

## Setup

1. Create a `competitors.yaml` in your project root:

```yaml
business:
  name: "Your Business Name"
  focus: "What you sell / your market"
  care_about:
    - pricing changes
    - new features
    - team changes

competitors:
  - name: "Competitor A"
    url: "https://competitor-a.com"
    pages:
      - "/pricing"
      - "/features"
  - name: "Competitor B"
    url: "https://competitor-b.com"
    pages:
      - "/pricing"
```

2. Install the agent:

```bash
cp agent.md ~/.claude/plugins/local/empirica/agents/competitor-monitor.md
```

3. Run it:

```bash
# "Check my competitors for changes"
# Or schedule with Claude Code's /loop: /loop 24h check competitors
```

## How It Learns

| Run | Behavior |
|-----|----------|
| First | Flags everything — establishing baseline |
| Second | Compares to baseline, still broad |
| Third+ | Focuses on what YOU responded to — ignores noise you didn't act on |

This works because Empirica's findings persist across sessions. The agent searches its own history to understand what patterns this business cares about.

## Example Output

```
# Competitor Update: 2026-03-28

## Requires Attention
- **Acme Corp**: Added Enterprise tier at $499/mo (was Free + Pro only).
  Why it matters: Directly competes with our mid-market offering.
  Confidence: 0.95

## Worth Knowing
- **Beta Inc**: Launched blog post about "AI-powered workflows."
  May indicate feature direction. Confidence: 0.6

## No Action Needed
- **Acme Corp**: Minor footer link changes
- **Beta Inc**: Updated team page photos
```

## Requires

- [Empirica](https://github.com/EmpiricaAI/empirica) installed (`pip install empirica`)
- Claude Code with Empirica plugin (`empirica setup-claude-code --force`)
- Web access (for fetching competitor pages)
