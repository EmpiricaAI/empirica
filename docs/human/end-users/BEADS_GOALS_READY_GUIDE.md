# `goals-ready` — Finding Actionable Work

> **Naming note:** "BEADS" here is the external [bd](https://github.com/cased/beads) issue tracker (dependency-graph), not the retired v0 bead coordination record (see [MESH_CONCEPTS.md](MESH_CONCEPTS.md) for what replaced that — Shared Epistemic Records in cortex).

`empirica goals-ready` shows goals you can pick up right now —
combining BEADS dependency state with your current epistemic state.

## What It Filters On

| Source | Question | Filter |
|---|---|---|
| **BEADS** | Are blockers cleared? | `bd ready` for paired goals |
| **Epistemic state** | Are my current vectors high enough to act? | `--min-confidence`, `--max-uncertainty` |
| **BEADS priority** | Is this important enough? | `--min-priority {1\|2\|3}` |

## Usage

```bash
# Default — show all ready goals
empirica goals-ready

# Filter to high-confidence-needed work only
empirica goals-ready --min-confidence 0.7 --max-uncertainty 0.3

# Only show P1 BEADS issues
empirica goals-ready --min-priority 1

# JSON for scripting
empirica goals-ready --output json
```

`--session-id` is optional — auto-detected from the active session.

---

## How It Works

1. **List active goals** in the current project (status `in_progress` +
   `planned`).
2. **For BEADS-paired goals**, check `bd ready` — drop any with open
   blocking dependencies.
3. **Compute fit** against your latest PREFLIGHT/CHECK vectors:
   - Confidence floor: drop if `overall_confidence < --min-confidence`
   - Uncertainty ceiling: drop if `uncertainty > --max-uncertainty`
4. **Sort + return** — ordered by BEADS priority first, then fit.

If a goal is **not BEADS-paired**, it passes the dependency filter
trivially (no blockers tracked = no blockers detected).

---

## Example Output

```
🎯 Ready Work (3 goals):

1. ✅ Implement OAuth2 client          [bd-a1b2, P1]
   fit: 0.85 | uncertainty: 0.18 | confidence: 0.82
   no open blockers

2. ⚠️  Debug token refresh             [bd-c3d4, P2]
   fit: 0.65 | uncertainty: 0.42 | confidence: 0.71
   suggest: more investigation before acting

3. ⏸  Refactor auth module            [bd-e5f6, P3]
   below threshold — uncertainty 0.55 > 0.30 ceiling
```

---

## Multi-AI Coordination

Different AIs working on the same project will see different ready
sets depending on their breadcrumb-calibrated vectors:

- An AI with high `know` on a domain will surface architecture goals
- An AI with high `do` but moderate `know` will surface implementation
  goals
- Both AIs see the same BEADS unblock state — that's git-shared

---

## Configuration

The default fit thresholds come from the project's compliance
configuration. To override per-invocation, use the CLI flags above.

To dial defaults at the project level, edit
`.empirica/project.yaml`:

```yaml
goals_ready:
  default_min_confidence: 0.6
  default_max_uncertainty: 0.4
```

---

## When It's Useful

- **Multi-session catch-up.** "What was I in the middle of?"
- **Multi-AI handoff.** Another AI's session left work; what's now ready?
- **Triage.** Several goals open; which can I actually act on right now?

## When It's Not

- **Solo single-transaction work.** `goals-list` is enough.
- **Exploratory work.** Vectors aren't set yet, so the fit filter is
  noisy.

---

## See Also

- **BEADS basics:** [BEADS_QUICKSTART.md](BEADS_QUICKSTART.md)
- **Goal lifecycle:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Vector meaning:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
