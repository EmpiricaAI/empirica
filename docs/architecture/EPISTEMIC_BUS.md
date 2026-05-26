# Epistemic Bus - The Nervous System

**Module:** `empirica.core.epistemic_bus`

The EpistemicBus is the nervous system of Empirica's cognitive architecture. Events propagate through it, allowing components to observe and react to epistemic state changes.

## Philosophy

- Empirica publishes epistemic events (no routing logic)
- External systems (Sentinels, MCO, plugins) can observe
- AI decides how to respond to observations
- Completely optional - Empirica works without it

Unix metaphor: System bus for epistemic events.

---

## Core Classes

### EpistemicEvent

The fundamental event structure. Events are just data - no behavior, no routing logic.

```python
event = EpistemicEvent(
    event_type="preflight_complete",
    agent_id="claude-code",
    session_id="abc123",
    data={"vectors": {"know": 0.7, "uncertainty": 0.3}},
    timestamp=time.time()
)
```

**Fields:**
- `event_type` - String identifying the event (see EventTypes)
- `agent_id` - Which AI agent generated this event
- `session_id` - Current session context
- `data` - Event-specific payload
- `timestamp` - When the event occurred

### EpistemicBus

Simple pub/sub bus for epistemic events.

```python
bus = EpistemicBus()
bus.subscribe(MyObserver())
bus.publish(event)
```

**Design principles:**
- Synchronous and simple (no queues, no async complexity)
- Observers are called in order
- Errors in observers are logged but don't block
- Optional - system works without any observers

**Methods:**
- `subscribe(observer)` - Register an observer
- `unsubscribe(observer)` - Remove an observer
- `publish(event)` - Send event to all observers
- `get_observer_count()` - Number of registered observers
- `get_event_count()` - Total events published

### EpistemicObserver

Abstract interface for systems that observe epistemic events.

```python
class MyObserver(EpistemicObserver):
    def handle_event(self, event: EpistemicEvent) -> None:
        if event.event_type == EventTypes.CHECK_COMPLETE:
            # React to CHECK gate completion
            pass
```

**Example observers:**
- RoutingSentinel: Suggests routing based on epistemic state
- MCO: Coordinates multiple agents
- Monitor: Logs/visualizes epistemic patterns

### CallbackObserver

Observer that calls a function for each event. Useful for testing and simple integrations.

```python
def my_handler(event):
    print(f"Got event: {event.event_type}")

bus.subscribe(CallbackObserver(my_handler))
```

### LoggingObserver

Simple observer that logs all events. Useful for debugging and development.

```python
from empirica.core.epistemic_bus import LoggingObserver
import logging

bus.subscribe(LoggingObserver(log_level=logging.DEBUG))
```

All events will be logged with format: `[EpistemicBus] {event_type}: agent={agent_id}, session={session_id}`

---

## EventTypes - Standard Event Vocabulary

### CASCADE Phase Events

| Event | When Published |
|-------|----------------|
| `PREFLIGHT_COMPLETE` | After preflight-submit |
| `INVESTIGATE_ROUND_COMPLETE` | After each investigation round |
| `CHECK_COMPLETE` | After check-submit |
| `ACT_STARTED` | When entering praxic phase (legacy name, retained for compatibility) |
| `ACT_COMPLETE` | After praxic phase execution (legacy name, retained for compatibility) |
| `POSTFLIGHT_COMPLETE` | After postflight-submit |

### Goal/Task Events

| Event | When Published |
|-------|----------------|
| `GOAL_DECISION_MADE` | CASCADE decision logic output |
| `GOAL_CREATED` | New goal created |
| `GOAL_UPDATED` | Goal modified |
| `GOAL_COMPLETED` | Goal marked complete |
| `SUBTASK_CREATED` | Task added to goal |
| `SUBTASK_COMPLETED` | Task marked done |

### Session Events

| Event | When Published |
|-------|----------------|
| `SESSION_STARTED` | New session created |
| `SESSION_ENDED` | Session closed |

### Calibration & Alert Events

| Event | When Published |
|-------|----------------|
| `CALIBRATION_COMPLETE` | Bayesian calibration updated |
| `CALIBRATION_DRIFT_DETECTED` | Systematic bias detected |
| `INVESTIGATION_SPINNING` | Too many investigation rounds |
| `CONFIDENCE_DROPPED` | Unexpected confidence decrease |
| `ENGAGEMENT_GATE_FAILED` | Engagement below threshold |

### Context Budget Manager Events

| Event | When Published |
|-------|----------------|
| `MEMORY_PRESSURE` | Context budget approaching limits |
| `CONTEXT_EVICTED` | Items evicted from context budget |
| `CONTEXT_INJECTED` | Items injected into context |
| `PAGE_FAULT` | Context item requested but not in hot memory |

---

## Integration Pattern

```python
from empirica.core.epistemic_bus import (
    get_global_bus,
    EpistemicObserver,
    EpistemicEvent,
    EventTypes
)

# Get the global bus instance
bus = get_global_bus()

# Create a custom observer
class CalibrationObserver(EpistemicObserver):
    def handle_event(self, event: EpistemicEvent) -> None:
        if event.event_type == EventTypes.CALIBRATION_DRIFT_DETECTED:
            # Alert, log, or take corrective action
            drift_data = event.data
            print(f"Drift detected: {drift_data}")

# Subscribe
bus.subscribe(CalibrationObserver())
```

---

## Current Usage

**Active Publishers:**
- `ContextBudgetManager` - Publishes MEMORY_PRESSURE, CONTEXT_EVICTED, CONTEXT_INJECTED, PAGE_FAULT

**Active Subscribers:**
- `SystemDashboard` - Observes all events for monitoring
- `ContextBudgetManager` - Reacts to memory events

**Gap:** CASCADE commands (preflight-submit, check-submit, postflight-submit) do NOT publish events despite having EventTypes defined. This is a future integration opportunity.

---

## Causal Role

The EpistemicBus enables the feedback loops that distinguish Empirica from simple prompting:

```
finding-log ──► EpistemicBus ──► Immune System (decay lessons)
     │                │
     └──► Eidetic Memory    └──► Calibration Observer
```

Events are the signals. The bus is how those signals reach the components that need them. Without the bus, components would be isolated - no coordination, no feedback, no emergence.

---

## Source

- `empirica/core/epistemic_bus.py`
- `empirica/core/bus_persistence.py` - SQLite + Qdrant persistence layer
- `empirica/core/context_budget.py` - Active publisher
