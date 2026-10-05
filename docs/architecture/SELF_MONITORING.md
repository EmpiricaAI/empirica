# Self-Monitoring: Detecting When Claimed State and Evidence Disagree

Self-monitoring in Empirica is measurement against outside evidence, not
introspection. The AI states a belief (vectors, claims, a falsifier); something
independent of the AI later checks it. Where the two disagree, the gap is surfaced
to the AI as a calibration signal. It is not an automatic override.

---

## What this document used to describe

Earlier revisions documented two standalone monitors, a drift monitor (the
"mirror principle": compare present vectors to a past baseline) and a
`MemoryGapDetector` (compare claimed `know` to breadcrumb evidence), with
`DriftReport`, `MemoryGap` and `MemoryGapReport` data models and four enforcement
levels. None of that exists as code now.

- `MirrorDriftMonitor` and its CLI handlers were removed in v1.6.6. The note in
  `empirica/cli/command_handlers/monitor_commands.py` says they were superseded by
  the grounded calibration pipeline, which detects drift "through objective
  evidence rather than vector-to-vector temporal comparison".
- `MemoryGapDetector` was removed as overlapping Qdrant retrieval and the compact
  hooks (comment in `empirica/core/system_dashboard.py`, which now holds only a
  placeholder that reports healthy).
- No class named `DriftReport`, `MemoryGap` or `MemoryGapReport` is defined
  anywhere under `empirica/`.

Leftovers that look like monitoring but are not wired:

- `project_bootstrap_formatter.py` renders a `memory_gap_analysis` section if
  the bootstrap payload has that key. Nothing in `empirica/` produces the key.
- `EventTypes.CALIBRATION_DRIFT_DETECTED` is defined on the epistemic bus and
  `context_budget.py` subscribes to it. Nothing publishes it.
- The `DriftLevel` and `SentinelAction` enums in `empirica/core/signaling.py` are
  presentation vocabulary for the statusline module; the only symbol
  `statusline_empirica.py` imports from it is `format_vectors_compact`.

Treat all three as dead surface, not features.

---

## What does the monitoring now

Five mechanisms, each pairing a stated belief with later evidence.

### 1. Grounded calibration (the core)

PREFLIGHT opens a measurement window, POSTFLIGHT closes it with the AI's
self-assessed vectors, and a post-test step collects objective evidence for the
same window: test results, git metrics, artifact counts, code quality, goal and
task completion. `empirica/core/post_test/grounded_calibration.py` keeps a
second Bayesian track fed by that evidence (lower observation variance than the
self-referential track) and records divergence between the two tracks.

The module docstring is explicit that the grounded track is informative, not
authoritative: the evidence sources are proxies and cannot see everything, so a
divergence is a prompt to ask why, not a command to deflate vectors.

Reading it:

```bash
empirica calibration-report                  # grounded, all history (JSON says window.applied false)
empirica calibration-report --windowed       # adds a `windowed` block over the last --weeks
empirica calibration-report --trajectory     # closing / widening / stable over time
empirica calibration-report --brier          # Brier decomposition per phase
empirica calibration-report --list-disputes  # open and resolved disputes
```

The `windowed` self-versus-grounded gap is a different quantity from the all-time
`divergence` (per-verification means against aggregated belief means). The report
keeps them under separate keys and never merges them.

Supporting modules in `empirica/core/post_test/`:

| Module | What it watches |
|---|---|
| `trajectory_tracker.py` | POSTFLIGHT-to-POSTFLIGHT gap per vector across sessions: closing, widening, stable |
| `calibration_insights.py` | Patterns in recent grounded records: chronic over- or underestimate, evidence gap, phase mismatch, volatile |
| `phase_boundary.py` | Splits a transaction at the last CHECK `proceed`, so noetic and praxic phases are scored separately |

A disputed measurement is recorded with `empirica calibration-dispute --vector
V --reported R --expected E --reason ...`.

### 2. Dynamic CHECK thresholds

`empirica/core/post_test/dynamic_thresholds.py` turns calibration quality into the
bar CHECK uses. Per its docstring, the Brier reliability component raises a
threshold when the AI has been poorly calibrated, and thresholds never go below
the domain baseline: good calibration earns trust in the numbers, not a lower bar.
Noetic and praxic phases are scored independently. It is consumed by the
Sentinel gate hook and by `sentinel_hooks.py`.

### 3. Claims, adjudicated at POSTFLIGHT

`empirica/core/claims.py`: a transaction rests on several claims, and one scalar
`know` averages them. The AI names the load-bearing claims with how each was
grounded (in PREFLIGHT, or at a CHECK), and at POSTFLIGHT gives each a verdict:
`held`, `refuted` or `untested`. A claim declared and never adjudicated is
recorded as `untested` and reported as a gap, never passed silently. Per the
module docstring this is advisory: nothing blocks a POSTFLIGHT.

### 4. Falsifiers that outlive the transaction

`empirica/core/falsifiers.py`: a falsifier names the observation that would refute
a belief and the artifact it falsifies. It stays `registered` and is surfaced at
every later PREFLIGHT until a POSTFLIGHT adjudicates it as `tripped`, `survived`
(needs evidence that the population was examined) or `expired` (the verdict for
silence; a `survived` without evidence is recorded as `expired`).

```bash
empirica falsifier-list --state registered
```

### 5. Blindspot scan

`empirica/core/blindspots/` infers unacknowledged gaps from the practice's own
artifacts. The shipped signal is the intent gap: an open task under a non-terminal
goal with no finding, no unknown and no dead-end covering it. Planned goals are
excluded unless asked for.

```bash
empirica blindspot-scan [--session-id ID] [--include-planned]
```

---

## Context loss across compaction

The old "compaction gap" is no longer a detector. It is handled by making the
durable layer carry the state: `hooks/pre-compact.py` writes a unified breadcrumbs
git note (fresh vectors, bootstrap context anchor, last task, git context) and
`hooks/post-compact.py` re-grounds depending on where the transaction was
interrupted (a completed session gets a new session and PREFLIGHT; an incomplete
one goes through a CHECK on the old session). Both live in
`empirica/plugins/claude-code-integration/hooks/`.

---

## Legacy `check` drift

The older `check` handler (`handle_check_command` in
`empirica/cli/command_handlers/_workflow_check.py`) still computes a drift figure:
the mean absolute vector delta between the baseline and the latest checkpoint,
bucketed low, medium (above 0.1) or high (above 0.3), and feeds it into its
proceed decision with the unknowns count. `check-submit`, the gate in current use,
does not compute it. Whether anything still calls the `check` handler was not
verified.

---

## Principles that survive

- Compare stated state to evidence, not to an earlier statement of state.
- Report the gap to the AI that made the claim; do not overwrite its vectors.
- Prefer advisory reporting to blocking until data shows a gate helps. The claims
  and falsifier modules say so in their own docstrings.
- A missing verdict is recorded as missing (`untested`, `expired`), never as
  "fine".
