# Epistemic Bus

**Module:** `empirica.core.epistemic_bus` (persistence in `empirica.core.bus_persistence`)

A small in-process publish/subscribe bus for epistemic events. PREFLIGHT, CHECK
and POSTFLIGHT publish to it; observers react. It is optional: every publish
site is wrapped so a failure is logged and ignored, and Empirica works with no
observers.

**It is not the dispatch bus, and it is not a messaging layer.** It never leaves
the process. See [`MESSAGING_LAYERS.md`](MESSAGING_LAYERS.md) for how Empirica
talks between instances, and [`DISPATCH_BUS.md`](DISPATCH_BUS.md) for the typed
request overlay on git-notes messaging.

---

## Classes

| Class | Role |
|---|---|
| `EpistemicEvent` | `event_type`, `agent_id`, `session_id`, `data`, `timestamp`; `to_dict()` |
| `EpistemicObserver` | abstract; implement `handle_event(event)` |
| `EpistemicBus` | `subscribe`, `unsubscribe`, `publish`, `get_observer_count`, `get_event_count`, `clear_observers` |
| `CallbackObserver`, `LoggingObserver` | wrap a function; log each event |
| `get_global_bus()` / `set_global_bus()` | module-level singleton, created on first use |

Semantics, from `publish`: synchronous, observers called in subscription order,
an exception in one observer is logged and the rest still run. `subscribe`
raises `TypeError` for anything that is not an `EpistemicObserver`.

```python
from empirica.core.epistemic_bus import (
    EpistemicObserver, EpistemicEvent, EventTypes, get_global_bus,
)

class Watch(EpistemicObserver):
    def handle_event(self, event: EpistemicEvent) -> None:
        if event.event_type == EventTypes.CHECK_COMPLETE:
            print(event.data["decision"])

get_global_bus().subscribe(Watch())
```

### Per-process, not per-project

The global bus is a Python module variable. Each CLI invocation, hook and MCP
server process has its own, empty at start. A PREFLIGHT run by `empirica
preflight-submit` publishes to the bus of that short-lived process; an observer
subscribed in a different process never sees it. What crosses process
boundaries is the SQLite table below, not the bus.

---

## What is actually published

`EventTypes` defines a vocabulary. Only some of it has a publisher.

| Event | Published by | `data` |
|---|---|---|
| `preflight_complete` | `preflight-submit` (`_workflow_preflight.py`) | `transaction_id`, `vectors`, `task_context`, `work_type`, `work_context` |
| `check_complete` | `check-submit` (`_workflow_check.py`) | `transaction_id`, `vectors`, `decision`, `round`, `confidence` |
| `postflight_complete` | `postflight-submit` (`_workflow_postflight.py`) | `transaction_id`, `vectors`, `deltas`, `postflight_confidence`, `internal_consistency` |
| `memory_pressure`, `context_evicted`, `context_injected`, `page_fault` | `ContextBudgetManager` | budget details plus `node_id`; `agent_id` is `cbm:<node_id>` |

The three transaction events use `agent_id="claude-code"` regardless of which
`ai_id` the practice runs under; use `node_id` on the stored row (below) or the
session to tell practices apart.

Defined in `EventTypes` with **no publisher in the code**: `investigate_round_complete`,
`act_started`, `act_complete` (legacy names for the praxic phase),
`goal_decision_made`, `goal_*`, `subtask_*`, `session_started`, `session_ended`,
`calibration_complete`, `calibration_drift_detected`, `investigation_spinning`,
`confidence_dropped`, `engagement_gate_failed`. `ContextBudgetManager` has
handlers for several of these (`session_started`, `confidence_dropped`,
`calibration_drift_detected`, `goal_created`, `goal_completed`); they only fire
if something publishes them. `ContextBudgetManager` also defines
`budget_exhausted` and `eviction_recommended` in its own `BudgetEventTypes`.

An older version of this document said the CASCADE commands publish nothing.
That is no longer true: the three above publish on every submit.

---

## Persistence

Each of the three publish sites calls `wire_persistent_observers(session_id)`
first, which subscribes:

- `SqliteBusObserver` (always): inserts a row into `epistemic_events` in
  `sessions.db` (`id`, `session_id`, `event_type`, `agent_id`, `data_json`,
  `timestamp`, `node_id`, `created_at`). `node_id` is `EMPIRICA_AI_ID` from the
  environment, else `unknown`. `SqliteBusObserver.query_events(session_id,
  event_type, since, limit)` reads it back.
- `QdrantBusObserver` (only if Qdrant is reachable): embeds the event into an
  `epistemic_events` collection for semantic lookup.

Failures in either are swallowed (debug log), so a missing table row is not an
error you will see.

The table is the cross-process surface. Readers in the code today:

- `SystemDashboard` (`empirica.core.system_dashboard`) merges persisted events
  into its status; the `monitor` command and the session-init hook construct it
  with `auto_subscribe=False`, so they read the table rather than listen live.
- The cockpit enrichment (`empirica.core.cockpit.enrichment`) reads the latest
  rows of `epistemic_events` for a recent-actions list.

---

## Observers in the repo

| Observer | Reacts to |
|---|---|
| `SystemDashboard` | the four budget events, to keep its memory cache; persisted events via the table |
| `ContextBudgetManager` | its handler table above; `get_budget_manager()` subscribes it, hooks build it with `auto_subscribe=False` |

No sentinel, routing or calibration component subscribes to the bus. Earlier
text described feedback loops from `finding-log` through the bus to an immune
system; `finding-log` publishes no event, and calibration and lesson decay do
not read the bus.

---

## Source

- `empirica/core/epistemic_bus.py`
- `empirica/core/bus_persistence.py`
- `empirica/core/context_budget.py` (budget events and handlers)
- `empirica/core/system_dashboard.py`
- `empirica/data/schema/lazy_tables_schema.py` (`epistemic_events` DDL)
