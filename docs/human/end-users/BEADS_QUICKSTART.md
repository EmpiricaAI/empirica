# BEADS Integration — Quick Start

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

## Find Ready Work

Once goals + BEADS are paired, `goals-ready` shows tasks that are:

1. **BEADS-unblocked** — no open blocking dependencies
2. **Epistemically fit** — your current vectors match the task's
   declared requirements (where they exist)

```bash
empirica goals-ready
```

See [BEADS_GOALS_READY_GUIDE.md](BEADS_GOALS_READY_GUIDE.md) for how
the fit calculation works.

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

- **goals-ready details:** [BEADS_GOALS_READY_GUIDE.md](BEADS_GOALS_READY_GUIDE.md)
- **BEADS upstream:** https://github.com/cased/beads
- **BEADS design notes (internal):** [../developers/BEADS_INTEGRATION_DESIGN.md](../developers/BEADS_INTEGRATION_DESIGN.md)
- **`bd --help`** for the full BEADS CLI reference
