# goals-ready Command Guide

## Overview

The `goals-ready` command combines BEADS dependency tracking with Empirica epistemic state to show tasks you're ACTUALLY ready to work on.

## Concept

**BEADS answers:** "What tasks are unblocked?" (no blocking dependencies)  
**Empirica answers:** "What tasks am I ready for?" (sufficient epistemic state)  
**`goals-ready` combines both:** "What should I work on RIGHT NOW?"

## Usage

```bash
# Basic usage
empirica goals-ready --session-id <SESSION_ID>

# With filters
empirica goals-ready --session-id <SESSION_ID> \
  --min-confidence 0.7 \
  --max-uncertainty 0.3 \
  --output json
```

## How It Works

1. **Query BEADS:** Get unblocked issues (no blocking dependencies)
2. **Get Epistemic State:** Fetch latest PREFLIGHT/CHECK vectors
3. **Calculate Fitness:** Match task requirements to current capability
4. **Filter & Rank:** Return tasks above fitness threshold

## Epistemic Fitness Score

```
fitness = 1.0 - (know_gap + do_gap + context_gap + uncertainty_penalty) / 4.0

Where:
- know_gap = max(0, required_know - current_know)
- do_gap = max(0, required_do - current_do)  
- context_gap = max(0, required_context - current_context)
- uncertainty_penalty = current_uncertainty * 0.3
```

## Task Requirements

Tasks can specify epistemic requirements in the goals table:
```sql
required_know: 0.8    -- Minimum knowledge level
required_do: 0.7      -- Minimum execution capability
required_context: 0.7 -- Minimum context understanding
```

## Example Output

```
🎯 Ready Work (3 tasks):

1. ✅ Implement OAuth2 client [bd-a1b2]
   Epistemic fit: 0.85
   - Requires: know=0.8 (you have: 0.9) ✅
   - Requires: do=0.7 (you have: 0.85) ✅
   - Dependencies: 0 blockers
   
2. ⚠️  Debug token refresh [bd-c3d4]
   Epistemic fit: 0.65
   - Requires: know=0.9 (you have: 0.9) ✅
   - Requires: context=0.8 (you have: 0.7) ⚠️
   - Suggest: Quick investigation first
   
3. ❌ Refactor auth module [bd-e5f6]
   Epistemic fit: 0.45 (below threshold)
   - Requires: know=0.95 (you have: 0.9) ⚠️
   - Suggest: Investigate before starting
```

## Multi-AI Coordination

Different AIs can query ready work and get tasks matching their capabilities:

**Reasoning AI (high know, low do):**
```bash
$ empirica goals-ready --session-id reasoning-sess
→ Gets: Architecture, design, code review tasks
```

**Acting AI (high do, moderate know):**
```bash
$ empirica goals-ready --session-id acting-sess  
→ Gets: Implementation, testing, refactoring tasks
```

## Integration with BEADS

```bash
# Create goal with BEADS integration
empirica goals-create \
  --session-id sess-123 \
  --objective "Implement OAuth2" \
  --use-beads \
  --required-know 0.8 \
  --required-do 0.7

# Query ready work
empirica goals-ready --session-id sess-123

# Complete and sync
bd close bd-a1b2 --reason "Completed"
bd sync
```

## Benefits

1. **Prevents Premature Work:** Won't suggest tasks you're not ready for
2. **Triggers Investigation:** Low fitness → investigate first
3. **Multi-AI Routing:** Automatic task allocation by capability
4. **Calibration Feedback:** Track prediction accuracy over time

## Configuration

Fitness thresholds can be configured per-profile in `empirica/config/investigation_profiles.yaml`:

```yaml
exploratory:
  min_epistemic_fitness: 0.5  # Willing to learn on the job
  
rigorous:
  min_epistemic_fitness: 0.8  # Only high-confidence tasks
  
rapid:
  min_epistemic_fitness: 0.6  # Balanced approach
```

## See Also

- [BEADS Integration Design](../developers/BEADS_INTEGRATION_DESIGN.md)
- [Session Continuity (Bootstrap Recovery)](FIRST_TIME_SETUP.md)
- [Empirica Natural Language Guide](EMPIRICA_NATURAL_LANGUAGE_GUIDE.md)
