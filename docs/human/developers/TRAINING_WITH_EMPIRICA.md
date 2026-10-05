# Training AIs with Empirica

Empirica's epistemic transactions record a complete belief-update cycle during real work: what the AI believed before, what it believed after, what the Sentinel decided in between, and how those beliefs compared with what deterministic services observed. `empirica training-export` turns those transactions into JSONL that can be used to fine-tune or evaluate models on epistemic self-assessment.

## Why the data is useful

Each exported record is one transaction:

1. **PREFLIGHT:** the AI's self-assessed vectors before work.
2. **CHECK** (zero or more): the vectors and decision at each gate inside the transaction.
3. **POSTFLIGHT:** the AI's self-assessed vectors after work, with its reasoning.
4. **Grounded verification:** the comparison of those beliefs against service observations (tests, git metrics, goal completion, artifact counts), when a verification row exists.

That gives examples of a model assessing its own state, updating on evidence, and being scored against outcomes, on real work with real uncertainty rather than synthetic prompts.

### Background (cited from earlier revisions; not re-checked here)

- OpenAI, "Reasoning Models Struggle to Control their Chains of Thought" (arxiv 2603.05706), cited for the claim that chain-of-thought is hard to fake, so assessments made during reasoning are less likely to be performative.
- Google, "Bayesian teaching enables probabilistic reasoning in LLMs" (Nature Communications, s41467-025-67998-6), cited for the claim that training on an assistant that works through uncertainty calibrates better than training on an oracle.

## The export command

```bash
# Current project, to a file
empirica training-export --output-path epistemic_training.jsonl

# Every project database registered in the workspace
empirica training-export --workspace --output-path full_dataset.jsonl

# Filter by AI id, or by project id prefix
empirica training-export --workspace --ai-id empirica --output-path claude_data.jsonl
empirica training-export --project-id 748a81a2 --output-path one_project.jsonl

# Smaller records
empirica training-export --no-artifacts --no-grounded --output-path vectors_only.jsonl

# Require more vectors per side (default 3)
empirica training-export --min-vectors 8 --output-path high_coverage.jsonl
```

Flags (`empirica training-export --help`): `--output-path`, `--workspace`, `--project-id` (prefix match), `--ai-id`, `--min-vectors` (default 3), `--no-artifacts`, `--no-grounded`, `--output {human,json}`, `--verbose`.

Without `--output-path` the records print to stdout, one JSON object per line, whatever `--output` says. `--output` only changes the summary printed after a file write (`json` gives `exported`, `skipped`, `output_path` and `sources`). A transaction with fewer than `--min-vectors` vectors on either side is skipped and counted.

## How records are built

- **Pairing.** A PREFLIGHT and a later POSTFLIGHT in the `reflexes` table are paired on `transaction_id`; for older rows without one, on `cascade_id`, then on `session_id`. One record per PREFLIGHT.
- **CHECK decisions.** CHECK rows between the two timestamps in the same session.
- **Grounded calibration.** The most recent `grounded_verifications` row for the session created from the POSTFLIGHT time to 300 seconds after it. If a transaction produced several verification rows, only one is exported.
- **Noetic artifacts.** Rows in the same session created inside the transaction window, at most 10 per type.
- **Workspace mode.** `--workspace` reads `~/.empirica/workspace/workspace.db`, takes each non-archived row of `global_projects`, locates its `sessions.db` from `trajectory_path`, and adds `_source_project` to each record.

## JSONL record format

```json
{
  "session_id": "abc-123",
  "ai_id": "empirica",
  "project_id": "<project uuid>",
  "transaction_id": "tx-456",
  "preflight_ts": 1768473000.0,
  "postflight_ts": 1768477500.0,

  "preflight_vectors":  {"know": 0.4, "do": 0.2, "uncertainty": 0.6},
  "postflight_vectors": {"know": 0.8, "do": 0.7, "uncertainty": 0.2},
  "delta":              {"know": 0.4, "do": 0.5, "uncertainty": -0.4},

  "preflight_meta":  {"current_phase": "NOETIC", "notes": "..."},
  "postflight_meta": {"current_phase": "PRAXIC", "notes": "...", "tool_call_count": 47},
  "postflight_reasoning": "...",

  "check_decisions": [
    {
      "timestamp": 1768476000.0,
      "vectors": {"know": 0.7, "uncertainty": 0.3, "completion": 0.5, "clarity": 0.7},
      "decision": "proceed",
      "gate_passed": true
    }
  ],

  "grounded_calibration": {
    "calibration_score": 0.12,
    "grounded_coverage": 0.75,
    "evidence_count": 12,
    "calibration_gaps": {"know": 0.15, "completion": -0.1},
    "sources_available": ["pytest", "git_metrics", "goal_completion"]
  },

  "noetic_artifacts": {
    "findings":  [{"finding": "...", "impact": 0.8, "subject": "auth"}],
    "unknowns":  [{"unknown": "...", "resolved": false, "impact": 0.6}],
    "dead_ends": [{"approach": "...", "why_failed": "...", "impact": 0.5}],
    "mistakes":  [{"mistake": "...", "why_wrong": "...", "prevention": "...", "root_cause_vector": "do"}]
  }
}
```

The vector dictionaries carry all 13 vectors when they were recorded; the example is shortened. Notes:

- Timestamps are epoch seconds (floats), not ISO strings. `project_id` is a UUID.
- `preflight_meta` and `postflight_meta` hold only the keys `current_phase`, `notes` and `tool_call_count` when present in the stored reflex data, so they can be empty.
- `check_decisions`, `grounded_calibration` and `noetic_artifacts` are absent when there is nothing to put in them.
- `delta` is postflight minus preflight, rounded to four places.
- The record carries no work type, claims, or falsifiers.

### Known gap: decisions are not exported

The artifact collector queries a table named `decisions_made`. The schema's table is `decisions` (`empirica/data/schema/projects_schema.py`), and I found no `decisions_made` anywhere else in the code. The query error is swallowed, so `noetic_artifacts.decisions` never appears: an export of this repository's own database held findings, unknowns, dead-ends and mistakes and no decisions. Do not build a decision-conditioned dataset from this export until the table name is fixed.

## Dataset structure

| Field | Description | Training signal |
|---|---|---|
| `preflight_vectors` | Self-assessment before work | input: the state at the start |
| `postflight_vectors` | Self-assessment after work | target: the state at the end |
| `delta` | Vector differences | learning magnitude per dimension |
| `check_decisions` | Gate vectors and decision mid-work | decision-making under uncertainty |
| `grounded_calibration` | Self-assessment against observation | how far beliefs diverged from evidence |
| `noetic_artifacts` | What was discovered, failed, got wrong | context for the belief update |

### Reading `calibration_score`

`grounded_calibration.calibration_score` is a belief divergence metric: a category-weighted mean of the absolute gaps between self-assessed and grounded vectors (`_compute_weighted_calibration` in `empirica/core/post_test/mapper.py`, whose docstring says lower is better). Zero means beliefs matched observation. It excludes the `uncertainty` vector, and the weighting depends on work type. It is not a reward given by Empirica; where the practice uses it, the gap is a prompt to change work discipline, not to adjust numbers.

`calibration_gaps` is per vector and signed as self-assessed minus grounded: positive means the AI claimed more than the evidence supported. `grounded_coverage` says how much of the vector set had evidence at all, so filter on it before trusting a score: a low score over thin coverage is weak evidence of calibration.

## Dataset size

Count it from your own data rather than quoting a figure; it grows with use and varies by deployment:

```bash
empirica training-export --workspace --output-path /tmp/ds.jsonl --output json
```

The JSON summary reports `exported`, `skipped` and per-project `sources`. To see how many records carry grounded calibration or CHECK decisions, count the keys in the JSONL (for example with `jq`).

## Training approaches

### 1. Supervised fine-tuning

Train on the transaction record.

- **Input:** task context (`preflight_meta.notes`), preflight vectors, and optionally the noetic artifacts.
- **Target:** postflight vectors, delta and `postflight_reasoning`.

### 2. Calibration-aware preference training

Build preference pairs from `grounded_calibration`.

- **Preferred:** low `calibration_score` with healthy `grounded_coverage`.
- **Rejected:** high `calibration_score` over comparable coverage, or large positive gaps on `know` and `completion` (overconfidence).

Because the score is a divergence and not a reward, choose thresholds from your own distribution and keep the coverage filter.

### 3. Sentinel imitation

Train a small model to reproduce the CHECK decision from `check_decisions`: given partial vectors mid-work, proceed or keep investigating.

### 4. Self-distillation

Use transactions from a strong model to fine-tune a smaller one on epistemic self-assessment. Filter with `--ai-id`; the practitioner model is not a column in the export, so mixed-model practices need another way to separate them (the `practitioner_model` column exists on `calibration_trajectory` and `grounded_verifications`, not in this export).

## Validation

- **BullshitBench** ([github.com/petergpt/bullshit-benchmark](https://github.com/petergpt/bullshit-benchmark)) measures pushback against nonsense prompts. The expectation that an epistemically trained model scores higher is a hypothesis, not a measured result here.
- **Calibration trajectory.** Compare `empirica calibration-report --trajectory` (and `--windowed`, `--brier`) before and after fine-tuning. Look for a lower average gap, less overconfidence on `know` and `completion`, and higher uncertainty on novel tasks.

## Privacy and data handling

Records contain:

- vector measurements (numeric)
- session, transaction and project ids, and the AI id (anonymize before sharing)
- `postflight_reasoning` and `notes` (free text from the work)
- noetic artifacts: findings, mistakes and dead-ends can contain domain-specific content

For external use, export with `--no-artifacts` and `--no-grounded`, and review `postflight_reasoning` and `*_meta.notes` before sharing; there is no flag that strips them.

## Architecture

```
<project>/.empirica/sessions/sessions.db
  ├── reflexes               PREFLIGHT / CHECK / POSTFLIGHT vectors, reasoning, reflex_data
  ├── sessions               ai_id
  ├── grounded_verifications objective calibration (SQLite-only, not mirrored to git notes)
  └── project_findings, project_unknowns, project_dead_ends, mistakes_made   noetic artifacts

~/.empirica/workspace/workspace.db
  └── global_projects        trajectory_path -> each project's sessions.db

empirica training-export
  ├── single project: the resolved sessions.db
  └── --workspace: every project database listed in global_projects
       -> matched (PREFLIGHT, POSTFLIGHT) pairs as JSONL
```

Source: `empirica/cli/command_handlers/training_commands.py`.

## Next steps

- Cross-model datasets: export per `--ai-id` and compare calibration patterns across models.
- Domain-specific sets: filter by `--project-id`.
- Fix the decisions export (see the known gap above).
- Automate benchmark runs before and after fine-tuning.
