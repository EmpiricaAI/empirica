# Using BEADS — setup, goals & ready work

> **Naming note:** "BEADS" here refers to the external [bd](https://github.com/cased/beads) dependency-graph issue tracker that empirica integrates with for goal decomposition + ready-work filtering. This is unrelated to the v0 "bead coordination record" concept that was retired in 1.11.2 — see [MESH_CONCEPTS.md](MESH_CONCEPTS.md) for the cross-practitioner-coordination story (which now lives in cortex SER, not as a graph node).

[BEADS](https://github.com/cased/beads) is a dependency-aware git-native
issue tracker. Empirica can optionally pair each goal with a BEADS issue
so you get dependency tracking and ready-work detection on top of the
epistemic layer.

**Optional.** Goals work fine without BEADS — `--use-beads` is an
opt-in flag (or set `beads: default_enabled: true` per-project).

---

## Install BEADS

```bash
# Install the bd CLI (uses uv if available, otherwise pip)
curl -fsSL https://raw.githubusercontent.com/cased/beads/main/scripts/install.sh | bash

# Initialize in your project
cd your-project
bd init
```

That creates `.beads/config.yaml` (committed) and `.beads/beads.db`
(gitignored). `bd ready`, `bd close`, etc. work from there.

---

## Use With Goals

### Per-goal opt-in

```bash
empirica goals-create --objective "Implement OAuth2" --use-beads
# → returns goal_id + beads_issue_id (e.g. bd-a1b2)

empirica goals-add-task --goal-id <GOAL_ID> \
  --description "Research OAuth2 spec" --use-beads
# → returns task_id + beads_issue_id (hierarchical, e.g. bd-a1b2.1)
```

### Per-project default

```yaml
# .empirica/project.yaml
beads:
  default_enabled: true     # Every goal gets a BEADS issue unless --no-beads
```

Resolution order: `--use-beads`/`--no-beads` flag > config file >
project default > opt-out.

---

## Find Ready Work — `goals-ready`

Once goals + BEADS are paired, `empirica goals-ready` shows goals you can pick
up right now — combining BEADS dependency state with your current epistemic
state. A goal surfaces when it is:

1. **BEADS-unblocked** — no open blocking dependencies
2. **Epistemically fit** — your current vectors match the task's
   declared requirements (where they exist)

### What it filters on

| Source | Question | Filter |
|---|---|---|
| **BEADS** | Are blockers cleared? | `bd ready` for paired goals |
| **Epistemic state** | Are my current vectors high enough to act? | `--min-confidence`, `--max-uncertainty` |
| **BEADS priority** | Is this important enough? | `--min-priority {1\|2\|3}` |

### Usage

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

### How it works

1. **List active goals** in the current project (status `in_progress` +
   `planned`).
2. **For BEADS-paired goals**, check `bd ready` — drop any with open
   blocking dependencies.
3. **Compute fit** against your latest PREFLIGHT/CHECK vectors:
   - Confidence floor: drop if `overall_confidence < --min-confidence`
   - Uncertainty ceiling: drop if `uncertainty > --max-uncertainty`
4. **Sort + return** — ordered by BEADS priority first, then fit.

If a goal is **not BEADS-paired**, it passes the dependency filter trivially
(no blockers tracked = no blockers detected).

### Example output

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

### Tuning the fit thresholds

Defaults come from the project's compliance configuration. Override
per-invocation with the CLI flags above, or dial the project-level defaults in
`.empirica/project.yaml`:

```yaml
goals_ready:
  default_min_confidence: 0.6
  default_max_uncertainty: 0.4
```

### Multi-AI coordination

Different AIs working the same project see different ready sets depending on
their breadcrumb-calibrated vectors:

- An AI with high `know` on a domain will surface architecture goals
- An AI with high `do` but moderate `know` will surface implementation goals
- Both AIs see the same BEADS unblock state — that's git-shared

### When `goals-ready` earns its keep

- **Multi-session catch-up.** "What was I in the middle of?"
- **Multi-AI handoff.** Another AI's session left work; what's now ready?
- **Triage.** Several goals open; which can I actually act on right now?

**When it's not worth it:** solo single-transaction work (`goals-list` is
enough), or exploratory work where vectors aren't set yet (the fit filter is
noisy).

---

## Example Flow

```bash
# 1. Create goal with BEADS
empirica goals-create --objective "Add OAuth2 support" --use-beads
# → goal_id, beads_issue_id=bd-a1b2

# 2. Decompose
empirica goals-add-task --goal-id <GOAL_ID> \
  --description "Research OAuth2 spec" --use-beads
empirica goals-add-task --goal-id <GOAL_ID> \
  --description "Implement token refresh" --use-beads
# → bd-a1b2.1 (research) + bd-a1b2.2 (token refresh, blocked by research)

# 3. Check what's actionable
bd ready
# → Shows "Research OAuth2 spec" (no blockers)
# → Hides "Implement token refresh" (blocked by research)

# 4. Work
empirica preflight-submit -
# ... investigate, log findings ...
empirica goals-complete-task --task-id <ID> --evidence "commit abc123"

# 5. Close the BEADS issue
bd close bd-a1b2.1 --reason "Research complete"

# 6. Next task becomes ready
bd ready    # → "Implement token refresh"
```

---

## When BEADS Helps

| Use it when | Skip it when |
|---|---|
| Multiple sessions on the same project | Single-session exploratory work |
| Complex dependencies between tasks | No dependencies between tasks |
| Want git-trackable issue history | `bd` CLI isn't installed |
| Need cross-AI handoff via dependency state | Prefer simpler setup |

---

## Graceful Degradation

If the `bd` CLI isn't installed:
- `--use-beads` prints a warning
- Goal/task creation continues normally
- `beads_issue_id` stays `null`
- Everything else works

The integration is genuinely optional — no Empirica feature requires
BEADS.

---

## See Also

- **Goal lifecycle:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Vector meaning:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)
- **BEADS upstream:** https://github.com/cased/beads
- **BEADS design notes (internal):** [../developers/BEADS_INTEGRATION_DESIGN.md](../developers/BEADS_INTEGRATION_DESIGN.md)
- **`bd --help`** for the full BEADS CLI reference
