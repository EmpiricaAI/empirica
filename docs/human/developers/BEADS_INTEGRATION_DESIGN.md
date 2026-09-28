# BEADS Integration — Architecture

> **Naming note:** "BEADS" = the external [bd](https://github.com/cased/beads) dependency-graph issue tracker. NOT the retired v0 "bead coordination record" concept (see [MESH_CONCEPTS.md](../end-users/MESH_CONCEPTS.md) for what replaced that).

**Status:** Shipped. The original design ([archived intent](#original-design-intent))
is now reflected in code; this doc tracks where the live pieces live.

---

## Principle

**Integration, not replacement.** BEADS owns task dependencies and ready-work
detection; Empirica owns epistemic state, calibration, and artifact
provenance. They link via a foreign key on the goals table.

```
┌──────────────────────────────────────────────────────────┐
│  Empirica CLI                                            │
│  (goals-create / goals-add-task / goals-claim / …)    │
└────────────────────────┬─────────────────────────────────┘
                         │
        ┌────────────────┴───────────────┐
        │                                │
┌───────▼───────────┐         ┌──────────▼──────────┐
│  BEADS (optional) │         │  Empirica goals     │
│  .beads/          │         │  .empirica/         │
│                   │         │                     │
│  • Task graph     │         │  • Epistemic state  │
│  • Dependencies   │  ←──→   │  • Confidence       │
│  • Ready work     │  FK:    │  • Findings /       │
│  • Hash IDs       │ beads_issue_id │   unknowns / etc │
└───────────────────┘         └─────────────────────┘
```

When BEADS isn't installed: graceful degradation. Goals work fine
without it; `--use-beads` becomes a no-op warning.

---

## Where the Code Lives

| Concern | Module |
|---|---|
| Subprocess adapter | `empirica/integrations/beads/adapter.py` |
| Config + defaults | `empirica/integrations/beads/config.py` |
| Branch creation + mapping | `empirica/integrations/branch_mapping.py` |
| `goals-claim` handler | `empirica/cli/command_handlers/goal_commands.py` (`handle_goals_claim_command`) |
| `goals-complete` handler | `empirica/cli/command_handlers/goal_commands.py` (`handle_goals_complete_command`) |
| Tests | `tests/integrations/test_beads_adapter.py`, `tests/test_branch_mapping.py` |

The adapter is **subprocess-based** — Empirica shells out to `bd` and
parses `--json` output. No Go dependencies in Python; BEADS upgrades
don't break Empirica.

---

## Schema Link

```sql
-- goals table
beads_issue_id TEXT  -- NULL when BEADS not used; FK by convention

CREATE INDEX idx_goals_beads_issue_id ON goals(beads_issue_id);
```

Tasks inherit the parent goal's BEADS pairing if created with
`--use-beads`. BEADS task IDs are hierarchical
(`bd-a1b2.1`, `bd-a1b2.2`, …).

---

## Opt-In Resolution

Order of precedence when deciding whether a new goal gets a BEADS issue:

1. `--use-beads` / `--no-beads` CLI flag (always wins)
2. `data["use_beads"]` in stdin-JSON form
3. `.empirica/project.yaml` → `beads.default_enabled`
4. Default: **opt-in (false)** — explicit choice required

---

## Graceful Degradation

`BeadsAdapter.is_available()` checks for the `bd` CLI on PATH. When
missing:

- `--use-beads` flag → warning logged, goal/task continues normally
- `beads_issue_id` stays `NULL`
- All Empirica features work without modification

The only thing you lose is dependency tracking (`goals-ready`) and
the branch-pairing automation in `goals-claim`.

---

## The `goals-claim` / `goals-complete` git bridge

Branch creation + mapping is live — since the `goals-claim` / `goals-complete`
commands landed (see `goal_commands.py:2140`, `integrations/branch_mapping.py`).
The AI claims a goal, gets a branch named after it, works on it, then completes
→ branch closed / BEADS issue closed.

### `empirica goals-claim`

Start work on a goal — links to BEADS, optionally creates a branch and opens a
PREFLIGHT transaction.

```bash
empirica goals-claim --goal-id <GOAL_ID>
                     [--create-branch]    # default: false — moves HEAD
                     [--run-preflight]    # default: false
```

What happens:
1. Resolves goal + (if BEADS-paired) BEADS issue id
2. Sets BEADS issue status to `in_progress`
3. With `--create-branch`: computes the branch name (see naming below), creates
   and checks it out, persists the mapping to `.empirica/branch_mapping.json`,
   and reports `head_moved: true` (human output: `Switched to branch …`)
4. Optionally opens a PREFLIGHT transaction

Branch creation was the default until 2026-09-28. A verb that reads as
bookkeeping moved HEAD without saying so, and the next commits landed on a branch
the practitioner never chose.

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

### Branch naming

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

The `reasoning` layer is the default — alternative layers (`acting`, `testing`,
etc.) can be specified per-organization convention but aren't enforced by the
bridge.

### Branch mapping file

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

### Example workflow

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

### Bridge configuration

```yaml
# .empirica/project.yaml
beads:
  default_enabled: true        # Auto-use BEADS for new goals
  default_branch_prefix: "epistemic/reasoning"
```

Per-invocation overrides via CLI flags (`--no-create-branch`, `--merge-branch`,
etc.) always win.

### Multi-AI coordination

When multiple AIs work the same project:

- `goals-discover` shows goals (and branch mappings) from other AIs
- `goals-claim` checks the mapping first — refuses if someone else already
  claimed
- `goals-resume --goal-id <ID>` transfers ownership of an existing claim (e.g.,
  handoff scenarios)

The branch mapping is committed under `.empirica/` per-project — push to share,
fetch to receive.

---

## User-Facing Docs

- **End-user setup + ready-work flow:** [../end-users/BEADS_QUICKSTART.md](../end-users/BEADS_QUICKSTART.md)

---

## Original Design Intent

The original integration design (Dec 2025) proposed five phases:

| Phase | Scope | Status |
|---|---|---|
| 1 | Optional subprocess adapter, FK link in goals table | ✅ shipped |
| 2 | `--use-beads` on goals-create / goals-add-task | ✅ shipped |
| 3 | `goals-claim` + branch mapping + `goals-complete` | ✅ shipped |
| 4 | `goals-ready` combining BEADS + epistemic state | ✅ shipped |
| 5 | Sentinel branch watcher (auto-suggest merge/abandon) | ❌ not yet — tracked as a planned goal |

The Sentinel-side automation is the one open piece. The original design proposed
a Sentinel branch-watcher that would auto-suggest merges when confidence is high,
flag abandoned branches, and auto-detect conflicts. **That isn't implemented
today.** Branches are created + mapped + cleaned up — but there's no background
watcher nudging the AI. Until it lands, branch hygiene relies on the AI / user
actively running `goals-complete` when done. If you need that nudge surface
today: poll `goals-discover` + manual review.

---

## See Also

- **BEADS upstream:** https://github.com/cased/beads
- **Code:** `empirica/integrations/beads/`, `empirica/integrations/branch_mapping.py`
- **Tests:** `tests/integrations/test_beads_adapter.py`
