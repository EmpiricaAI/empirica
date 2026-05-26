# Metrics API

**Module:** `empirica.metrics`
**Category:** Observability & Analytics
**Stability:** Production Ready

---

## Overview

Metrics modules track productivity patterns and resource efficiency across sessions. Used by the monitoring dashboard and calibration reports.

---

## FlowStateMetrics

**Module:** `empirica.metrics.flow_state`

Calculates a weighted flow score from session behavior patterns.

### Constructor

```python
from empirica.metrics.flow_state import FlowStateMetrics

metrics = FlowStateMetrics(db=session_database)
```

### Flow Score Components

| Component | Weight | Measures |
|-----------|--------|----------|
| `cascade_completeness` | 0.25 | PREFLIGHT→POSTFLIGHT completion |
| `learning_velocity` | 0.20 | Know increase per hour |
| `bootstrap_usage` | 0.15 | Early context loading |
| `goal_structure` | 0.15 | Active goals with tasks |
| `check_usage` | 0.15 | Mid-session confidence checks |
| `session_continuity` | 0.10 | AI naming convention consistency |

### Methods

```python
result = metrics.calculate_flow_score(session_id="abc-123")
# Returns:
# {
#     "flow_score": 0.78,
#     "components": {"cascade_completeness": 1.0, "learning_velocity": 0.6, ...},
#     "recommendations": ["Add CHECK mid-session for better confidence tracking"]
# }
```

---

## TokenEfficiencyMetrics

**Module:** `empirica.metrics.token_efficiency`

Tracks token usage to validate the git-checkpoint token reduction hypothesis (target: 80-90% reduction vs prompt-based history).

### Constructor

```python
from empirica.metrics.token_efficiency import TokenEfficiencyMetrics

metrics = TokenEfficiencyMetrics(
    session_id="abc-123",
    storage_dir=".empirica/metrics"
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `measure_context_load(phase, method, content)` | Record a token measurement |
| `compare_efficiency(baseline_session_id)` | Compare git vs prompt token usage |
| `export_report(format, output_path)` | Export markdown or JSON report |

### TokenMeasurement

| Field | Type | Description |
|-------|------|-------------|
| `phase` | `str` | CASCADE phase (PREFLIGHT, CHECK, etc.) |
| `method` | `str` | Loading method ("git" or "prompt") |
| `tokens` | `int` | Token count |
| `timestamp` | `str` | ISO timestamp |
| `content_type` | `str` | "checkpoint", "diff", "full_history" |
| `metadata` | `Dict` | Additional context |

---

## CLI Commands

```bash
# Metrics are now collected automatically via grounded calibration post-tests.
# See: empirica calibration-report
#
# Removed commands (v1.5.0+):
#   checkpoint-metrics — replaced by post-test evidence collection
#   token-report — replaced by grounded calibration pipeline
```

---

## Implementation Files

- `empirica/metrics/flow_state.py` - FlowStateMetrics
- `empirica/metrics/token_efficiency.py` - TokenEfficiencyMetrics, TokenMeasurement

---

**API Stability:** Stable
**Last Updated:** 2026-03-04
