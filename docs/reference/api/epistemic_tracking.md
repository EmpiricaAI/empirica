# Epistemic Tracking API Reference

**Version:** 1.14.9 (verified against the code on 2026-10-13)
**Modules:** `empirica.data.epistemic_snapshot`, `empirica.data.snapshot_provider`, `empirica.core.epistemic_trajectory`, `empirica.core.bayesian_beliefs`
**Purpose:** capture an AI's epistemic state, persist it, and read it back across sessions

---

## Overview

Epistemic tracking stores the self-assessment vectors a practitioner reports at PREFLIGHT, CHECK and POSTFLIGHT, and offers three ways to read them back:

- **CLI**: `epistemics-show` and `epistemics-list` print a session's recorded assessments.
- **Snapshots**: `EpistemicStateSnapshot` packages the 13 vectors, a delta and a short semantic + narrative summary so another session or AI can be primed with them.
- **Trajectory search**: `search_trajectories` looks for sessions with a similar learning trajectory (needs Qdrant).

The vectors are beliefs the practitioner holds about its own state. Grounding them in observations (tests, git, artifacts) is the job of the post-test step, not of this module.

---

## CLI Commands

### `epistemics-show`

Show the epistemic trajectory recorded for a session.

```bash
empirica epistemics-show --session-id <ID>
empirica epistemics-show --session-id <ID> --phase PREFLIGHT
empirica epistemics-show --session-id <ID> --output json
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID; derived from the session resolver when omitted |
| `--phase` | No | none (all phases) | Filter to one phase. Free text: the parser does not restrict the value |
| `--output` | No | `human` | `human` or `json` |

**Output (JSON):**

```json
{
  "ok": true,
  "session_id": "abc123...",
  "project_id": "proj-456",
  "ai_id": "claude-code",
  "count": 2,
  "phase_filter": null,
  "trajectories": [
    {
      "phase": "PREFLIGHT",
      "timestamp": 1770544800.0,
      "vectors": {
        "engagement": 0.8, "know": 0.6, "do": 0.5, "context": 0.7,
        "clarity": 0.7, "coherence": 0.7, "signal": 0.6, "density": 0.5,
        "state": 0.5, "change": 0.0, "completion": 0.0, "impact": 0.5,
        "uncertainty": 0.4
      },
      "reasoning": "Initial assessment before investigation"
    }
  ]
}
```

Each entry carries all 13 vectors. There is no `delta` key and no `checkpoints` key: entries are listed oldest first under `trajectories`, and a delta is computed by the caller (see [Python API](#python-api)).

---

### `epistemics-list`

List the assessments recorded for a session.

```bash
empirica epistemics-list --session-id <ID>
empirica epistemics-list --session-id <ID> --output json
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID; derived from the session resolver when omitted |
| `--output` | No | `human` | `human` or `json` |

**Output (human):**

```
📊 Epistemic Trajectories for Session: abc123...
   Project: proj-456
   AI: claude-code
   Count: 2

   Phase: PREFLIGHT
   Time: 1770544800.0
   Know: 0.6, Uncertainty: 0.4
```

The human view prints the phase, the time and the `know` and `uncertainty` values of each entry; use `--output json` for the full vector set.

---

## Core Components

### EpistemicStateSnapshot

A compact, transferable description of one epistemic state (`empirica/data/epistemic_snapshot.py`). It is a dataclass.

```python
from empirica.data.epistemic_snapshot import EpistemicStateSnapshot, ContextSummary

snapshot = EpistemicStateSnapshot(
    snapshot_id="uuid",
    session_id="session-123",
    ai_id="claude-code",
    timestamp="2026-02-08T10:00:00Z",
    cascade_phase="postflight",
    vectors={"know": 0.85, "uncertainty": 0.15, "do": 0.8, "context": 0.9},
    delta={"know": 0.25, "uncertainty": -0.25},
    context_summary=ContextSummary(
        semantic={"domain": "authentication", "files_touched": 5},
        narrative="Investigated JWT implementation, found RS256 signing",
        evidence_refs=["src/auth/jwt.py:45", "docs/security.md"],
    ),
)

json_str = snapshot.to_json()                       # export for transfer
prompt = snapshot.to_context_prompt(level="standard")  # "minimal" (default), "standard" or "full"
```

**Required:** `snapshot_id`, `session_id`, `ai_id`, `timestamp` (all `str`).

**Optional attributes:**

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `cascade_phase` | `str \| None` | `None` | Phase the snapshot was taken at (the code's own comment lists lowercase cascade phases) |
| `cascade_id`, `previous_snapshot_id`, `db_session_ref` | `str \| None` | `None` | Links to the cascade, the preceding snapshot and the database session |
| `vectors` | `dict[str, float]` | `{}` | The epistemic vectors |
| `domain_vectors` | dict | none | Domain-specific vectors |
| `delta` | `dict[str, float] \| None` | `None` | Change from the previous snapshot |
| `context_summary` | `ContextSummary \| None` | `None` | Semantic + narrative summary |
| `original_context_tokens`, `snapshot_tokens` | int | 0 | Token estimates used to compute the ratio |
| `compression_ratio` | `float` | `0.0` | Filled by the provider as `1 - snapshot_tokens / original_context_tokens` |
| `information_loss_estimate` | float | | Estimated information lost in compression |
| `fidelity_score` | `float` | `1.0` | How well the snapshot represents the full context |
| `transfer_count`, `created_at` | | | Transfer bookkeeping |

**Methods:** `to_json()`, `from_json()`, `from_dict()`, `to_context_prompt(level)`, `calculate_delta()`, `increment_transfer_count()`, `estimate_memory_reliability()`, `should_refresh()`, `get_refresh_reason()`. The module function `create_snapshot(...)` builds one.

---

### ContextSummary

Hybrid semantic + narrative summary (`empirica/data/epistemic_snapshot.py`).

| Attribute | Type | Description |
|-----------|------|-------------|
| `semantic` | `dict[str, Any]` | Structured facts (domain, files touched, goal id, ...) |
| `narrative` | `str` | A short prose account |
| `evidence_refs` | `list[str]` | Pointers to the evidence (`file:line`, doc anchors) |

`ContextSummary.to_prompt()` renders it for injection into a prompt.

---

### EpistemicSnapshotProvider

Creates and persists snapshots (`empirica/data/snapshot_provider.py`).

```python
from empirica.data.snapshot_provider import EpistemicSnapshotProvider

provider = EpistemicSnapshotProvider()          # __init__(db=None)

snapshot = provider.create_snapshot_from_session(
    session_id="session-123",
    context_summary=ContextSummary(...),
    cascade_phase="postflight",
)
provider.save_snapshot(snapshot)

latest = provider.get_latest_snapshot("session-123")
by_id = provider.get_snapshot_by_id("snapshot-uuid")
history = provider.get_snapshot_history("session-123", limit=10)
```

There is no `load_snapshot`; read snapshots back with the three getters above.

---

### Bayesian beliefs (calibration adjustments)

Per-vector calibration beliefs live in `empirica/core/bayesian_beliefs.py`, not on `SessionDatabase`.

```python
from empirica.core.bayesian_beliefs import BayesianBeliefManager
from empirica.data.session_database import SessionDatabase

db = SessionDatabase()
adjustments = BayesianBeliefManager(db).get_calibration_adjustments("claude-code")
# {"know": -0.03, "completion": 0.15, ...}   (only vectors with at least 3 observations)
```

A **negative** adjustment means the AI tends to overestimate that vector; a **positive** one means it tends to underestimate. Adjustments are weighted by evidence count and capped at `MAX_CORRECTION_MAGNITUDE`, so they cannot grow without bound. Apply as `calibrated = raw + adjustment`.

---

## 13 Epistemic Vectors

| Vector | Tier | Description |
|--------|------|-------------|
| `know` | Foundation | Domain knowledge |
| `do` | Foundation | Ability to execute the task |
| `context` | Foundation | Understanding of the surrounding state |
| `engagement` | Meta | How actively the problem is being worked |
| `uncertainty` | Meta | What is not known (lower means less uncertain). Excluded from the calibration score |
| `clarity` | Phase-dependent | How clear the path forward is |
| `coherence` | Phase-dependent | Internal consistency |
| `signal` | Phase-dependent | Quality of information versus noise |
| `density` | Phase-dependent | Relevant knowledge per unit of context |
| `state` | Phase-dependent | Awareness of the current system state |
| `change` | Phase-dependent | Amount of change made in the transaction |
| `completion` | Phase-dependent | Progress toward the phase goal |
| `impact` | Phase-dependent | Significance of the work |

How much each phase-dependent vector counts depends on the transaction's `work_type`. There is no universal "good range": the readiness gate uses dynamic thresholds from the Sentinel, with static fallbacks of `know >= 0.70` and `uncertainty <= 0.35`.

---

## Trajectory Search

`search_trajectories` finds sessions with a similar learning trajectory (requires Qdrant).

```python
from empirica.core.epistemic_trajectory import search_trajectories

results = search_trajectories(
    project_id="proj-123",
    query="OAuth2 authentication learning",
    limit=10,                  # default 5
)

for traj in results:
    print(traj["session_id"], traj["deltas"]["know"], traj["calibration_accuracy"])
```

> **Known limitation:** `min_learning_delta` and `calibration_quality` are accepted and turned into a filter dict, but the search function (`empirica/core/qdrant/epistemics_store.py`) never applies it, so they do not narrow the results today. Filter the returned list yourself.

---

## Integration with Epistemic Transactions

1. **PREFLIGHT** records the baseline assessment.
2. **CHECK** (when one is submitted) records the readiness assessment.
3. **POSTFLIGHT** records the final assessment; the delta is the difference.
4. **POST-TEST** compares the self-assessment with observations from services (tests, git, artifacts).

```
PREFLIGHT ──► work (noetic + praxic) ──► POSTFLIGHT ──► delta = POSTFLIGHT − PREFLIGHT ──► POST-TEST
```

Whether a snapshot is written at each of those points is decided by the workflow commands, not by this module; call the provider yourself when you need one.

---

## Python API

```python
from empirica.data.session_database import SessionDatabase

db = SessionDatabase()

db.store_vectors(
    session_id="session-123",
    phase="PREFLIGHT",
    vectors={"know": 0.6, "uncertainty": 0.4, "do": 0.5, "context": 0.7, "completion": 0.0},
    reasoning="Initial assessment",
)

# One dict: {"vectors": {...}, "phase": ..., "timestamp": ..., "reasoning": ..., ...}
latest = db.get_latest_vectors(session_id="session-123")

# A LIST of such dicts, oldest first
preflight_rows = db.get_vectors_by_phase(session_id="session-123", phase="PREFLIGHT")

# Compute a delta
postflight = db.get_latest_vectors(session_id="session-123", phase="POSTFLIGHT")
preflight = db.get_latest_vectors(session_id="session-123", phase="PREFLIGHT")
if postflight and preflight:
    delta = {
        v: postflight["vectors"][v] - preflight["vectors"].get(v, 0)
        for v in postflight["vectors"]
    }
```

---

## Implementation Files

- `empirica/data/epistemic_snapshot.py`: `EpistemicStateSnapshot`, `ContextSummary`, `create_snapshot`
- `empirica/data/snapshot_provider.py`: `EpistemicSnapshotProvider`
- `empirica/core/epistemic_trajectory.py`: `search_trajectories`
- `empirica/core/bayesian_beliefs.py`: `BayesianBeliefManager`
- `empirica/cli/command_handlers/epistemics_commands.py`: CLI handlers
- `empirica/cli/parsers/epistemics_parsers.py`: argument parsers

---

## Related Documentation

- [EPISTEMIC_BUS.md](../../architecture/EPISTEMIC_BUS.md): event-driven epistemic updates
- [SELF_MONITORING.md](../../architecture/SELF_MONITORING.md): AI self-awareness architecture
- [Epistemic Transaction Workflow](cascade_workflow.md): measurement phases
- [Signaling API](signaling.md): drift levels and vector health

---

**Last verified:** 2026-10-13 against 1.14.9. Rewritten whole after a claim-by-claim check; the previous version (1.8.14) named two methods that do not exist and a JSON shape the CLI no longer emits.
