# Context Budget API

**Module:** `empirica.core.context_budget`
**Checked against:** the source at 1.14.11 (2026-10-17)

The Context Budget Manager tracks what is resident in the model's context window, scores each item by epistemic priority, evicts
low-value items under pressure, and handles a "page fault" (the model needs something that is not resident) by requesting an
injection through one of the injection channels. It is an `EpistemicObserver`: it reacts to events on the global epistemic bus.

## Memory zones

`MemoryZone` has three members. The sizes below are the defaults of `BudgetThresholds`.

| Zone | Meaning | Default size |
|---|---|---|
| `ANCHOR` | Always resident; never evicted | 15,000 tokens (`anchor_reserve`) |
| `WORKING` | Active task context, managed by priority | 150,000 tokens (`working_set_target`) |
| `CACHE` | Preloaded, evicted first under pressure | 35,000 tokens (`cache_limit`) |

`BudgetThresholds.total_capacity` defaults to 200,000 tokens.

## Creating the manager

```python
from empirica.core.context_budget import get_budget_manager

manager = get_budget_manager(session_id="abc-123")   # session_id is required the first time
```

`get_budget_manager` returns a process-wide singleton. The first call must pass `session_id` (it raises `ValueError` without one);
later calls return the same manager. Thresholds come from `empirica/config/mco/context_budget.yaml` when that file exists, else
the `BudgetThresholds` defaults. `ContextBudgetManager(session_id, thresholds=None, auto_subscribe=True, node_id=None)` can also be
constructed directly, for example in tests.

## Methods

| Method | Returns | What it does |
|---|---|---|
| `register_item(item: ContextItem)` | `bool` | Add an item to the inventory |
| `unregister_item(item_id)` | `ContextItem` or `None` | Remove an item |
| `touch_item(item_id)` | None | Mark an item as referenced (updates recency) |
| `find_items(...)` | list of items | Query the inventory |
| `evict_lowest_priority(tokens_needed, reason="pressure")` | `EvictionResult` | Evict the lowest-priority evictable items until `tokens_needed` tokens are freed |
| `request_injection(request: InjectionRequest)` | `bool` | Ask for an item to be put into context |
| `register_injection_handler(channel, handler)` | None | Provide the callable that performs injections for a channel |
| `get_budget_report()` | `BudgetReport` | Snapshot of the current budget |
| `get_inventory_summary()` | `dict` | Summary of the inventory |
| `persist_state(db_path=None)` | `bool` | Write the state to the database |

## Data types

**`ContextItem`**: `id`, `zone`, `content_type`, `source`, `channel`, `label`, `estimated_tokens`, plus `epistemic_value` (0.5),
`recency` (1.0), `reference_count` (0), `injected_at`, `last_referenced`, `evictable` (True), `metadata`. `compute_priority(decay_rate)`
combines these; `touch()` marks a reference.

**`ContentType`**: `calibration`, `protocol`, `finding`, `unknown`, `dead_end`, `goal`, `code`, `conversation`, `skill`, `bootstrap`,
`system_prompt`.

**`InjectionChannel`**: `hook`, `skill`, `mcp`, `direct`, `implicit`.

**`InjectionRequest`**: `content_id`, `reason`, `content_type`, `preferred_channel`, and `estimated_tokens` (0), `epistemic_value` (0.5),
`priority` (`"critical"`, `"normal"` or `"low"`), `metadata`.

**`EvictionResult`**: `evicted_items`, `tokens_freed`, `reason`, `triggered_by`.

### `BudgetReport`

A snapshot of the budget, with `to_dict()` and `to_json()`.

| Field | Type | Meaning |
|---|---|---|
| `timestamp`, `session_id` | `float`, `str` | When and for which session |
| `total_capacity`, `total_used`, `total_available` | `int` | Tokens |
| `utilization` | `float` | Used divided by capacity |
| `anchor_used`, `anchor_limit` | `int` | Anchor zone usage and reserve |
| `working_used`, `working_target` | `int` | Working zone usage and target |
| `cache_used`, `cache_limit` | `int` | Cache zone usage and limit |
| `total_items`, `anchor_items`, `working_items`, `cache_items` | `int` | Item counts |
| `under_pressure` | `bool` | True when utilization passes `pressure_threshold` |
| `eviction_candidates` | `int` | Items that could be evicted |
| `top_items`, `bottom_items` | `list[dict]` | Highest- and lowest-priority items |

## Thresholds

`BudgetThresholds` (dataclass; `from_dict`, `to_dict`). They are set by constructing it, or through the YAML file named above under
a `context_budget:` key; there is no environment-variable override.

| Field | Default |
|---|---|
| `total_capacity` | 200000 |
| `anchor_reserve` | 15000 |
| `working_set_target` | 150000 |
| `cache_limit` | 35000 |
| `eviction_aggressiveness` | 0.5 |
| `decay_rate` | 0.1 |
| `min_priority_threshold` | 0.05 |
| `page_fault_retrieval_limit` | 5 |
| `pressure_threshold` | 0.85 |

## Events

`BudgetEventTypes` are the events the manager publishes on the epistemic bus:
`MEMORY_PRESSURE` (`"memory_pressure"`), `CONTEXT_EVICTED`, `CONTEXT_INJECTED`, `PAGE_FAULT`, `BUDGET_EXHAUSTED`,
`EVICTION_RECOMMENDED`.

The manager itself reacts to these bus events (`handle_event`): `SESSION_STARTED`, `CONFIDENCE_DROPPED`, `POSTFLIGHT_COMPLETE`,
`CALIBRATION_DRIFT_DETECTED`, `GOAL_CREATED`, `GOAL_COMPLETED`.

## Related

`AttentionStatus` lives in `empirica.core.system_dashboard`, not in this module; read its fields there.
`empirica/cli/command_handlers/memory_commands.py` renders `BudgetReport` for the CLI.
