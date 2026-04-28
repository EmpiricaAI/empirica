# Epistemic Tracking API Reference

**Version:** 1.8.14
**Module:** `empirica.data.epistemic_snapshot`, `empirica.core.epistemic_trajectory`
**Purpose:** AI epistemic state capture, persistence, and cross-AI transfer

> **1.8.x additions:** Three-vector model (self_assessed/observed/grounded),
> provenance graph (source_refs, evidence_refs, resolution_finding_id),
> work-type vector weight profiles, weight-aware calibration coverage,
> uncertainty excluded from calibration score. See CHANGELOG.md for details.

---

## Overview

Epistemic tracking captures and persists AI self-assessment data across sessions. It provides:

- **95% token reduction** via vector-based context transfer
- **Universal cross-AI transfer** (works with any AI provider)
- **13-dimensional epistemic state** for comprehensive self-assessment
- **Delta tracking** for learning measurement (PREFLIGHT → POSTFLIGHT)
- **Hybrid semantic + narrative context** summaries

---

## CLI Commands

### `epistemics-show`

Show detailed epistemic trajectory for a session.

```bash
# Show all checkpoints for session
empirica epistemics-show --session-id <ID>

# Filter by phase
empirica epistemics-show --session-id <ID> --phase PREFLIGHT

# JSON output
empirica epistemics-show --session-id <ID> --output json
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID (auto-derived from active transaction) |
| `--phase` | No | all | Filter by phase: PREFLIGHT, CHECK, POSTFLIGHT |
| `--output` | No | `human` | Output format: `human` or `json` |

**Output (JSON):**
```json
{
  "ok": true,
  "session_id": "abc123...",
  "ai_id": "claude-code",
  "project_id": "proj-456",
  "checkpoints": [
    {
      "phase": "PREFLIGHT",
      "timestamp": "2026-02-08T10:00:00Z",
      "vectors": {
        "know": 0.6, "uncertainty": 0.4, "context": 0.7,
        "do": 0.5, "completion": 0.0, "engagement": 0.8
      },
      "reasoning": "Initial assessment before investigation"
    },
    {
      "phase": "POSTFLIGHT",
      "timestamp": "2026-02-08T11:30:00Z",
      "vectors": {
        "know": 0.85, "uncertainty": 0.15, "context": 0.9,
        "do": 0.8, "completion": 1.0, "engagement": 0.85
      },
      "reasoning": "Completed investigation, learned X and Y"
    }
  ],
  "delta": {
    "know": 0.25,
    "uncertainty": -0.25,
    "do": 0.30,
    "completion": 1.0
  }
}
```

---

### `epistemics-list`

List all epistemic assessments for a session.

```bash
# List checkpoints
empirica epistemics-list --session-id <ID>

# JSON output
empirica epistemics-list --session-id <ID> --output json
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID (auto-derived from active transaction) |
| `--output` | No | `human` | Output format: `human` or `json` |

**Output (human):**
```
📊 EPISTEMIC CHECKPOINTS: abc123...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] PREFLIGHT - 10:00:00
    know=0.60  uncertainty=0.40  context=0.70

[2] CHECK - 10:45:00
    know=0.80  uncertainty=0.20  context=0.85
    Decision: proceed

[3] POSTFLIGHT - 11:30:00
    know=0.85  uncertainty=0.15  context=0.90
    Delta: know +0.25, uncertainty -0.25
```

---

## Core Components

### EpistemicStateSnapshot

Universal cross-AI context transfer protocol. Compresses full conversation context into epistemic essence.

```python
from empirica.data.epistemic_snapshot import EpistemicStateSnapshot, ContextSummary

snapshot = EpistemicStateSnapshot(
    snapshot_id="uuid",
    session_id="session-123",
    ai_id="claude-code",
    timestamp="2026-02-08T10:00:00Z",
    cascade_phase="POSTFLIGHT",
    vectors={
        "know": 0.85, "uncertainty": 0.15, "do": 0.8,
        "context": 0.9, "completion": 1.0, "engagement": 0.85,
        "clarity": 0.8, "coherence": 0.85, "signal": 0.7,
        "density": 0.5, "state": 0.8, "change": 0.6, "impact": 0.7
    },
    delta={"know": 0.25, "uncertainty": -0.25},
    context_summary=ContextSummary(
        semantic={"domain": "authentication", "files_touched": 5},
        narrative="Investigated JWT implementation, found RS256 signing",
        evidence_refs=["src/auth/jwt.py:45", "docs/security.md"]
    ),
    compression_ratio=0.95,
    fidelity_score=0.92
)

# Export for transfer
json_str = snapshot.to_json()

# Inject into AI prompt
prompt = snapshot.to_context_prompt(level="standard")
```

**Key Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `snapshot_id` | str | Unique identifier |
| `session_id` | str | Parent session |
| `ai_id` | str | AI agent identifier |
| `vectors` | Dict[str, float] | 13-dimensional epistemic state |
| `delta` | Dict[str, float] | Changes from previous snapshot |
| `context_summary` | ContextSummary | Semantic + narrative context |
| `compression_ratio` | float | Token reduction achieved (typically 0.95) |
| `fidelity_score` | float | How well snapshot represents full context |
| `cascade_phase` | str | Transaction phase (PREFLIGHT/CHECK/POSTFLIGHT) |

---

### ContextSummary

Hybrid semantic + narrative context summary for knowledge transfer.

```python
from empirica.data.epistemic_snapshot import ContextSummary

summary = ContextSummary(
    semantic={
        "domain": "authentication",
        "files_touched": 5,
        "goal_id": "goal-123"
    },
    narrative="Completed investigation of JWT implementation. Found RS256 signing with 24h expiry. Refresh tokens stored in Redis.",
    evidence_refs=[
        "src/auth/jwt.py:45",
        "src/auth/refresh.py:112",
        "docs/security.md#jwt-configuration"
    ]
)

# Convert to prompt for AI injection
prompt = summary.to_prompt()
```

---

### EpistemicSnapshotProvider

Creates and persists epistemic snapshots.

```python
from empirica.data.snapshot_provider import EpistemicSnapshotProvider

provider = EpistemicSnapshotProvider()

# Create snapshot from session
snapshot = provider.create_snapshot_from_session(
    session_id="session-123",
    context_summary=ContextSummary(...),
    cascade_phase="POSTFLIGHT"
)

# Save snapshot
provider.save_snapshot(snapshot)

# Load snapshot
loaded = provider.load_snapshot(snapshot_id="uuid")
```

---

### Bayesian Beliefs (Calibration)

Calibration tracking for epistemic vectors using Bayesian updates.

```python
from empirica.data.session_database import SessionDatabase

db = SessionDatabase()

# Get calibration adjustments for AI
beliefs = db.get_bayesian_beliefs(ai_id="claude-code")
# Returns: {"know": -0.03, "uncertainty": 0.04, "completion": 0.15, ...}

# Interpretation:
# - Negative adjustment = AI tends to overestimate this vector
# - Positive adjustment = AI tends to underestimate this vector
# Apply as: calibrated_value = raw_value + adjustment
```

---

## 13 Epistemic Vectors

| Vector | Category | Description | Good Range |
|--------|----------|-------------|------------|
| `know` | Foundation | Domain knowledge level | 0.7-0.9 |
| `do` | Foundation | Ability to execute task | 0.6-0.9 |
| `context` | Foundation | Understanding of current situation | 0.7-0.95 |
| `clarity` | Comprehension | How clear is the understanding | 0.6-0.9 |
| `coherence` | Comprehension | Internal consistency | 0.7-0.95 |
| `signal` | Comprehension | Strength of evidence | 0.5-0.9 |
| `density` | Comprehension | Information richness | 0.3-0.7 |
| `state` | Execution | Current progress state | 0.5-0.9 |
| `change` | Execution | Magnitude of changes made | 0.0-0.6 |
| `completion` | Execution | How complete is work | 0.0-1.0 |
| `impact` | Execution | Effect of actions | 0.3-0.8 |
| `engagement` | Gate | Readiness to proceed | 0.6-0.9 |
| `uncertainty` | Meta | Epistemic uncertainty (lower is better) | 0.1-0.35 |

**Readiness Gate:** Dynamic thresholds from Sentinel (static fallback: know >= 0.70, uncertainty <= 0.35)

---

## Trajectory Search

Search epistemic learning trajectories across sessions (requires Qdrant).

```python
from empirica.core.epistemic_trajectory import search_trajectories

results = search_trajectories(
    project_id="proj-123",
    query="OAuth2 authentication learning",
    min_learning_delta=0.2,  # Only sessions with significant learning
    calibration_quality="good",  # Only well-calibrated sessions
    limit=10
)

for traj in results:
    print(f"Session: {traj['session_id']}")
    print(f"  Learning delta: know={traj['deltas']['know']:+.2f}")
    print(f"  Calibration: {traj['calibration_accuracy']}")
```

---

## Integration with Epistemic Transactions

Epistemic tracking integrates with the epistemic transaction workflow:

1. **PREFLIGHT** → Creates baseline snapshot
2. **CHECK** → Validates readiness gate
3. **POSTFLIGHT** → Creates final snapshot, computes delta
4. **POST-TEST** → Grounds self-assessment in service observations

```
PREFLIGHT (Snapshot A)
    │
    ▼
   Work (noetic + praxic)
    │
    ▼
POSTFLIGHT (Snapshot B)
    │
    ▼
Delta = B - A (learning measurement)
    │
    ▼
POST-TEST (ground delta in evidence)
```

---

## Python API

```python
from empirica.data.session_database import SessionDatabase

db = SessionDatabase()

# Store vectors
db.store_vectors(
    session_id="session-123",
    phase="PREFLIGHT",
    vectors={
        "know": 0.6, "uncertainty": 0.4, "do": 0.5,
        "context": 0.7, "completion": 0.0
    },
    reasoning="Initial assessment"
)

# Get latest vectors
latest = db.get_latest_vectors(session_id="session-123")

# Get vectors by phase
preflight_vectors = db.get_vectors_by_phase(
    session_id="session-123",
    phase="PREFLIGHT"
)

# Compute delta
postflight = db.get_latest_vectors(session_id="session-123", phase="POSTFLIGHT")
preflight = db.get_latest_vectors(session_id="session-123", phase="PREFLIGHT")

if postflight and preflight:
    delta = {
        vector: postflight['vectors'][vector] - preflight['vectors'].get(vector, 0)
        for vector in postflight['vectors']
    }
```

---

## Implementation Files

- `empirica/data/epistemic_snapshot.py` - EpistemicStateSnapshot, ContextSummary
- `empirica/data/snapshot_provider.py` - EpistemicSnapshotProvider
- `empirica/core/epistemic_trajectory.py` - search_trajectories
- `empirica/cli/command_handlers/epistemics_commands.py` - CLI handlers
- `empirica/cli/parsers/epistemics_parsers.py` - Argument parsers

---

## Related Documentation

- [EPISTEMIC_BUS.md](../../architecture/EPISTEMIC_BUS.md) - Event-driven epistemic updates
- [SELF_MONITORING.md](../../architecture/SELF_MONITORING.md) - AI self-awareness architecture
- [Epistemic Transaction Workflow](cascade_workflow.md) - Epistemic measurement phases
- [Signaling API](signaling.md) - Drift levels and vector health

---

**API Stability:** Stable
**Last Updated:** 2026-02-08
