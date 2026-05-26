# Data Infrastructure API

**Module:** `empirica.data`
**Category:** Database & Persistence
**Stability:** Production Ready

---

## Overview

The data infrastructure layer provides database abstraction, connection resilience, and domain-specific repositories. All data flows through this layer before reaching SQLite or PostgreSQL.

---

## DatabaseAdapter

**Module:** `empirica.data.db_adapter`

Abstract base class with factory method for database backends.

### Factory Method

```python
from empirica.data.db_adapter import DatabaseAdapter

# SQLite (default)
adapter = DatabaseAdapter.create(
    db_type="sqlite",
    db_path=".empirica/sessions/sessions.db"
)

# PostgreSQL (enterprise)
adapter = DatabaseAdapter.create(
    db_type="postgresql",
    host="localhost", port=5432,
    database="empirica", user="empirica", password="secret"
)
```

### Interface

| Method | Description |
|--------|-------------|
| `execute(sql, params)` | Execute SQL statement |
| `fetchone()` | Fetch single row |
| `fetchall()` | Fetch all rows |
| `commit()` | Commit transaction |
| `close()` | Close connection |

---

## ConnectionPool

**Module:** `empirica.data.connection_pool`

Fixed-size connection pool with retry and telemetry for database resilience.

### Constructor

```python
pool = ConnectionPool(
    factory=lambda: sqlite3.connect("sessions.db"),
    max_connections=5,
    retry_policy=RetryPolicy(max_retries=3)
)
```

### Key Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire()` | Connection | Get a connection from the pool |
| `release(conn)` | None | Return connection to pool |
| `get_telemetry()` | Dict | Pool usage statistics |

---

## CircuitBreaker

**Module:** `empirica.data.connection_pool`

Prevents cascading failures by opening after repeated errors and self-healing after a timeout.

### States

| State | Behavior |
|-------|----------|
| **CLOSED** | Normal operation, counting failures |
| **OPEN** | Rejecting calls, waiting for recovery timeout |
| **HALF_OPEN** | Allowing one test call to check recovery |

### Constructor

```python
breaker = CircuitBreaker(
    failure_threshold=5,    # Open after 5 consecutive failures
    recovery_timeout=30.0   # Try again after 30 seconds
)
```

---

## RetryPolicy

**Module:** `empirica.data.connection_pool`

Exponential backoff with jitter for transient database errors.

### Constructor

```python
policy = RetryPolicy(
    max_retries=5,
    base_delay=0.1,      # 100ms initial delay
    max_delay=10.0,       # Cap at 10 seconds
    strategy=RetryStrategy.EXPONENTIAL,
    jitter=True           # Prevent thundering herd
)
```

### Telemetry

```python
policy.telemetry
# {"total_attempts": 12, "successful_retries": 3, "failed_retries": 1, "circuit_breaks": 0}
```

---

## Repositories

### GoalDataRepository

**Module:** `empirica.data.repositories.goals`

Thin ORM layer for goal and task CRUD. Business logic lives in `GoalRepository` (`empirica.core.goals.repository`).

| Method | Description |
|--------|-------------|
| `create_goal(session_id, objective, ...)` | Create a new goal |
| `update_goal(goal_id, ...)` | Update goal fields |
| `get_goals(session_id)` | List goals for session |
| `create_task(goal_id, ...)` | Add task to goal |

### CommandRepository

**Module:** `empirica.data.repositories.utilities`

Tracks CLI command invocations for usage analytics.

| Method | Description |
|--------|-------------|
| `log_command(session_id, command, args, success)` | Record command execution |
| `get_command_stats(session_id)` | Get command usage statistics |

---

## Implementation Files

- `empirica/data/db_adapter.py` - DatabaseAdapter ABC with factory
- `empirica/data/connection_pool.py` - ConnectionPool, CircuitBreaker, RetryPolicy
- `empirica/data/repositories/goals.py` - GoalDataRepository
- `empirica/data/repositories/utilities.py` - CommandRepository, TokenRepository

---

**API Stability:** Stable
**Last Updated:** 2026-03-04
