# BEADS–Git Bridge

> **Naming note:** "BEADS" = the external [bd](https://github.com/cased/beads) issue tracker. NOT the retired v0 bead coordination record.

**Status:** Shipped. Branch creation + mapping live since the
`goals-claim` / `goals-complete` commands landed (see `goal_commands.py:2140`,
`integrations/branch_mapping.py`).

Linking BEADS issues to git branches automatically. The AI claims a
goal, gets a branch named after it, works on it, then completes →
branch closed / BEADS issue closed.

---

## Commands

### `empirica goals-claim`

Start work on a goal — creates branch, links to BEADS, optionally
opens a PREFLIGHT transaction.

```bash
empirica goals-claim --goal-id <GOAL_ID>
                     [--create-branch]    # default: true
                     [--run-preflight]    # default: false
```

What happens:
1. Resolves goal + (if BEADS-paired) BEADS issue id
2. Computes branch name (see naming below)
3. Creates + checks out the branch
4. Persists mapping to `.empirica/branch_mappings.json`
5. Sets BEADS issue status to `in_progress`
6. Optionally opens a PREFLIGHT transaction

### `empirica goals-complete`

Finish work — closes the goal, optionally merges + cleans up.

```bash
empirica goals-complete --goal-id <GOAL_ID> --reason "..."
                        [--merge-branch]      # merge to main
                        [--run-postflight]    # auto-POSTFLIGHT
```

What happens:
1. Marks goal complete (with reason)
2. If BEADS-paired: closes the BEADS issue
3. If `--merge-branch`: merges the epistemic branch into main
4. Removes branch mapping entry (archives to history)
5. Optionally runs POSTFLIGHT

---

## Branch Naming

**With BEADS pairing:**
```
epistemic/reasoning/issue-<beads_issue_id>
```
Example: `epistemic/reasoning/issue-empirica-a1b2`

**Without BEADS (Empirica-only goal):**
```
epistemic/reasoning/goal-<goal_id_short>
```
Example: `epistemic/reasoning/goal-de7ae57c`

The `reasoning` layer is the default — alternative layers (`acting`,
`testing`, etc.) can be specified per-organization convention but
aren't enforced by the bridge.

---

## Branch Mapping File

Persisted at `.empirica/branch_mappings.json`. Schema:

```json
{
  "mappings": {
    "<branch_name>": {
      "goal_id": "uuid",
      "beads_issue_id": "empirica-a1b2",
      "session_id": "uuid",
      "ai_id": "empirica",
      "started_at": "2026-05-18T08:30:00Z",
      "status": "in_progress",
      "preflight_vectors": {"know": 0.65, "uncertainty": 0.35}
    }
  },
  "archived": [...]    // entries removed by goals-complete
}
```

Used for:
- Quick lookup: branch ↔ goal
- Multi-AI awareness — see who claimed what
- Recovery — if a session crashes, the mapping survives

---

## Example Workflow

```bash
# 1. Find ready work
empirica goals-ready
# 🎯 Ready Work (2):
# 1. [empirica-a1b2] Implement OAuth2 (fit: 0.85)
# 2. [empirica-c3d4] Add unit tests (fit: 0.78)

# 2. Claim one
empirica goals-claim --goal-id <GOAL_ID> --run-preflight
# ✅ Branch created: epistemic/reasoning/issue-empirica-a1b2
# ✅ BEADS status: in_progress
# 🧠 PREFLIGHT opened — know=0.65, uncertainty=0.35

# 3. Work on the branch (already checked out)
# ... edit, commit ...
empirica goals-complete-task --task-id <ID> --evidence "commit abc123"

# 4. Complete
empirica goals-complete --goal-id <GOAL_ID> --reason "Shipped + tested" \
                        --merge-branch --run-postflight
# ✅ POSTFLIGHT closed
# ✅ Merged: epistemic/reasoning/issue-empirica-a1b2 → main
# ✅ BEADS issue closed
# 📦 Branch mapping archived
```

---

## Configuration

`.empirica/project.yaml`:

```yaml
beads:
  default_enabled: true        # Auto-use BEADS for new goals
  default_branch_prefix: "epistemic/reasoning"
```

Per-invocation overrides via CLI flags (`--no-create-branch`,
`--merge-branch`, etc.) always win.

---

## Multi-AI Coordination

When multiple AIs work the same project:

- `goals-discover` shows goals (and branch mappings) from other AIs
- `goals-claim` checks the mapping first — refuses if someone else
  already claimed
- `goals-resume --goal-id <ID>` transfers ownership of an existing
  claim (e.g., handoff scenarios)

The branch mapping is committed under `.empirica/` per-project — push
to share, fetch to receive.

---

## What's NOT (Yet) Shipped

The original design proposed Sentinel branch-watcher integration:
auto-suggest merges when confidence is high, flag abandoned branches,
auto-detect conflicts. **This isn't implemented today.** Branches are
created + mapped + cleaned up — but there's no background watcher
nudging the AI. Tracked as a planned goal.

If you need that nudge surface today: poll `goals-discover` + manual
review.

---

## See Also

- **BEADS basics:** [../end-users/BEADS_QUICKSTART.md](../end-users/BEADS_QUICKSTART.md)
- **goals-ready:** [../end-users/BEADS_GOALS_READY_GUIDE.md](../end-users/BEADS_GOALS_READY_GUIDE.md)
- **Internal design:** [BEADS_INTEGRATION_DESIGN.md](BEADS_INTEGRATION_DESIGN.md)
- **Code:** `empirica/cli/command_handlers/goal_commands.py`, `empirica/integrations/branch_mapping.py`
