# Missed Opportunities Analyzer

An epistemic agent that investigates your business data to surface patterns, gaps, and opportunities you might be missing — with calibrated confidence scores.

## What Makes This Different

Generic AI data analysis gives you charts and summaries. This agent INVESTIGATES — it forms hypotheses about your business, tests them against your data, and reports what it found AND what it couldn't determine.

A rejected hypothesis ("we checked and this isn't a problem") is as valuable as a confirmed one — it saves you from chasing dead ends.

## Usage

```bash
# Copy agent to your Claude Code plugins
cp agent.md ~/.claude/plugins/local/empirica/agents/missed-opportunities.md

# Then point it at your data:
# "Analyze sales.csv for missed opportunities"
# "What patterns am I missing in my customer data?"
```

## What It Does

1. **Understands your data** — shape, quality, what's there and what's missing
2. **Forms hypotheses** — "weekend sales might be low", "this segment is underserved"
3. **Tests each hypothesis** — runs the numbers, confirms or rejects
4. **Reports with confidence** — "85% sure this is real" vs "50% — need more data"
5. **Logs the entire investigation** — reproducible, auditable, shareable

## Example Output

```
# Opportunity Analysis: Acme E-Commerce

## Opportunities Found

### High Confidence (0.85)
Weekend activation gap — Saturday/Sunday sales are 40% below weekday average.
Saturday 2-5 PM has zero orders. If even 25% of weekday volume shifted to
weekends, estimated impact: $3,200/month.
Action: Extend customer support hours, run weekend-only promotions.

### Medium Confidence (0.6)
Cart abandonment spike on mobile — 68% of mobile sessions end at checkout.
Desktop completion rate is 45%. Possible UX issue on mobile checkout flow.
Caveat: Small sample (N=142 mobile sessions), need another month of data.

### Hypotheses Rejected
- Pareto customer concentration: Tested, distribution is actually even (top 20%
  = 35% of revenue). Not a problem.
- Seasonal pattern: No significant month-over-month variation found.
```

## Data Formats Supported

The agent works with whatever data you point it at:
- CSV / TSV files
- JSON / JSONL
- SQLite databases
- Any text-based data format

## Requires

- [Empirica](https://github.com/EmpiricaAI/empirica) installed (`pip install empirica`)
- Claude Code with Empirica plugin (`empirica setup-claude-code --force`)
- Your business data in an accessible format
