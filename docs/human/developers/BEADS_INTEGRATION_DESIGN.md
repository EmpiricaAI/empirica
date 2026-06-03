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

## User-Facing Docs

- **End-user setup:** [../end-users/BEADS_QUICKSTART.md](../end-users/BEADS_QUICKSTART.md)
- **Ready-work flow:** [../end-users/BEADS_GOALS_READY_GUIDE.md](../end-users/BEADS_GOALS_READY_GUIDE.md)
- **Branch bridge:** [BEADS_GIT_BRIDGE.md](BEADS_GIT_BRIDGE.md)

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

The Sentinel-side automation is the one open piece. Until it lands,
branch hygiene relies on the AI / user actively running
`goals-complete` when done — there's no background nudge.

---

## See Also

- **BEADS upstream:** https://github.com/cased/beads
- **Code:** `empirica/integrations/beads/`, `empirica/integrations/branch_mapping.py`
- **Tests:** `tests/integrations/test_beads_adapter.py`
