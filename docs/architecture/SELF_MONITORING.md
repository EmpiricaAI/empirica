# Self-Monitoring Systems - Drift & Memory Gap Detection

These systems enable functional self-awareness - the ability to detect when knowledge has degraded, context is stale, or claims exceed evidence.

**Note:** The standalone `memory_gap_detector` module has been removed. Memory gap detection
concepts are now handled by the grounded calibration pipeline (`empirica calibration-report`).
The data models below are retained as architectural reference.

## Philosophy

Self-monitoring is not introspection - it's measurement:
- **Drift Detection**: Compare present state to historical baselines
- **Gap Detection**: Compare claims to breadcrumb evidence
- **No heuristics**: Pure temporal and evidential comparison
- **Configurable enforcement**: User controls consequences

---

## Drift Detection

### The Mirror Principle

Past-self validates present-self through temporal comparison. If knowledge drops without investigation, that's drift (memory corruption). If knowledge drops with clarity increase, that's learning (discovering complexity).

### DriftReport

Result of drift detection with pattern-aware analysis.

```python
@dataclass
class DriftReport:
    drift_detected: bool
    severity: str  # 'none' | 'low' | 'medium' | 'high' | 'critical'
    recommended_action: str  # 'continue' | 'monitor_closely' | 'investigate' | 'stop_and_reassess'
    drifted_vectors: List[Dict[str, Any]]
    pattern: Optional[str]  # 'TRUE_DRIFT' | 'LEARNING' | 'SCOPE_DRIFT' | None
    pattern_confidence: float  # 0.0-1.0
    baseline_timestamp: Optional[float]
    checkpoints_analyzed: int
    reason: Optional[str]
```

**Severity levels:**
- `none` - No drift detected
- `low` - Drift < 0.2, single vector
- `medium` - Drift 0.2-0.3, or 2 vectors
- `high` - Drift 0.3-0.5, or 3+ vectors
- `critical` - Drift > 0.5, or 4+ vectors

**Patterns detected:**
- `TRUE_DRIFT` - Memory loss (KNOW↓ + CLARITY↓ + CONTEXT↓ together)
- `LEARNING` - Discovering complexity (KNOW↓ + CLARITY↑)
- `SCOPE_DRIFT` - Task expansion (KNOW↓ + scope indicators↑)

**Note:** Drift detection is now handled by the grounded calibration pipeline
(see `empirica calibration-report`). The `DriftReport` data model above
is retained for reference but is no longer produced by a standalone monitor class.

---

## Memory Gap Detection

### Evidence-Based Self-Assessment

Detects when AI claims knowledge without supporting evidence from breadcrumbs. Prevents confabulation - claiming to know more than the evidence supports.

### MemoryGap

A detected gap between claimed and realistic knowledge.

```python
@dataclass
class MemoryGap:
    gap_id: str
    gap_type: str  # 'unreferenced_findings' | 'unincorporated_unknowns' | 'file_unawareness' | 'confabulation' | 'compaction'
    content: str
    severity: str  # 'low' | 'medium' | 'high' | 'critical'
    gap_score: float  # 0.0-1.0
    evidence: Dict[str, Any]
    affects_vector: Optional[str]  # Which vector this impacts
    realistic_value: Optional[float]  # What the vector should be
    resolution_action: str  # How to fix
```

**Gap types:**
- `unreferenced_findings` - Findings exist but weren't read
- `unincorporated_unknowns` - Resolved unknowns not incorporated
- `file_unawareness` - File changes not acknowledged
- `compaction` - Memory compaction caused detail loss
- `confabulation` - Claiming more knowledge than evidence supports

### MemoryGapReport

Complete memory gap analysis.

```python
@dataclass
class MemoryGapReport:
    detected: bool
    gaps: List[MemoryGap]
    overall_gap: float  # Difference between claimed and realistic
    expected_know: float  # Realistic knowledge estimate
    claimed_know: float  # What AI claimed
    enforcement: Dict[str, Any]  # Enforcement decisions per gap
    actions: List[str]  # Recommended actions
```

### MemoryGapDetector (Removed)

The standalone `MemoryGapDetector` class has been removed. Gap detection is now part of the
grounded calibration pipeline. The API below is retained as architectural reference.

```python
detector = MemoryGapDetector(policy={
    'enforcement': 'warn',  # 'inform' | 'warn' | 'strict' | 'block'
    'scope': {
        'findings': 'warn',
        'unknowns': 'inform',
        'file_changes': 'inform',
        'compaction': 'strict',
        'confabulation': 'block'
    },
    'thresholds': {
        'findings': 10,      # Flag if >10 unread
        'unknowns': 5,
        'file_changes': 0,
        'compaction': 0.4,   # 40% detail loss
        'confabulation': 0.3 # Claimed 0.3 more than realistic
    }
})

report = detector.detect_gaps(
    current_vectors={'know': 0.8, 'clarity': 0.7},
    breadcrumbs=project_bootstrap_result,
    session_context={'breadcrumbs_loaded': True, ...}
)

# Apply enforcement
result = detector.apply_enforcement(report, vectors)
if not result['ok']:
    # Blocked - must resolve gaps before proceeding
    print(result['required_actions'])
```

**Enforcement levels:**
- `inform` - Show gaps, no penalty (default)
- `warn` - Show gaps + recommendations
- `strict` - Show gaps + adjust vectors to realistic values
- `block` - Show gaps + prevent proceeding until resolved

---

## Integration

### With CASCADE Workflow

```
PREFLIGHT ──────────► CHECK ──────────► POSTFLIGHT ──────────► POST-TEST
    │                   │                   │                      │
    ▼                   ▼                   ▼                      ▼
MemoryGapDetector   Grounded            Update baseline       Grounded Verification
(validate claims)   Calibration          for future drift     (evidence-based
                    Pipeline             detection             calibration against
                                                               actual outcomes)
```

### With EpistemicBus

Both systems can publish events to the EpistemicBus:

```python
from empirica.core.epistemic_bus import get_global_bus, EventTypes, EpistemicEvent

# Drift detected
if drift_report.drift_detected:
    bus.publish(EpistemicEvent(
        event_type=EventTypes.CALIBRATION_DRIFT_DETECTED,
        agent_id="empirica",
        session_id=session_id,
        data={"severity": drift_report.severity, "pattern": drift_report.pattern}
    ))
```

---

## Source Files

- Drift detection and memory gap detection are now handled by the grounded calibration pipeline (see `empirica calibration-report`)
