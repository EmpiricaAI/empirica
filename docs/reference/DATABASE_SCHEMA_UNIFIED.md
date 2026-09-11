# Empirica Database Schema (Unified)

> ⚠️ **This file is a hand-maintained snapshot and it has drifted. Verify
> against the database before relying on it.** Measured 2026-09-11 against a
> live practice db: the schema has **82 tables**, this file documents 33, and
> **16 of those 33 state the wrong column count** (e.g. `goals` documented at
> 14 columns, actually 22). Nothing regenerates it, so every migration since
> 2026-02-11 has widened the gap silently.
>
> Authoritative instead: `sqlite3 <db> ".schema <table>"` /
> `PRAGMA table_info(<table>)` for shape, and
> `empirica/data/schema/*.py` + `empirica/data/migrations/migrations.py` for
> the definitions. Generating this file from those is a logged goal.

**Total Tables:** 36 (active) — *stale, see banner above*
**Database Type:** SQLite (with PostgreSQL adapter support)
**Architecture:** Modular with unified goal/task system, transaction-first tracking
**Every project (mapped to git repo) has its own SQLite database**
**Last Updated:** 2026-02-11

> **Note on table naming.** The `subtasks` table name predates the
> 1.10.0 CLI rename. The CLI surface and external API use `task` since
> 1.10.0; storage tables keep `subtasks` by deliberate CLI-vs-storage
> boundary. See [UPGRADE_TO_1.10.md § subtask → task rename](../guides/UPGRADE_TO_1.10.md#breaking-change--subtask--task-rename)
> for the full mapping. References below to `subtasks` are accurate
> to the on-disk schema; the CLI never exposes that term.

---

## Table Categories

### 1. Core Session Management (3 tables)
- **sessions** - AI sessions with metadata (ai_id, project_id, timestamps)
- **cascades** - Reasoning cascade executions (task, context, goal tracking)
- **reflexes** - Epistemic checkpoints (PREFLIGHT, CHECK, POSTFLIGHT)

**Relationships:**
```
sessions (1) ──> (N) cascades
sessions (1) ──> (N) reflexes
cascades (1) ──> (N) reflexes (via cascade_id)
```

### 2. Epistemic Tracking (1 table)
- **epistemic_snapshots** - Point-in-time epistemic state captures

**Relationships:**
```
sessions (1) ──> (N) epistemic_snapshots
```

> **Deprecated:** `divergence_tracking` and `drift_monitoring` tables were removed in v1.2.0. Drift detection now uses the signaling system with moon phase indicators.

### 3. Bayesian & Belief Tracking (1 table)
- **bayesian_beliefs** - Evidence-based belief evolution

**Relationships:**
```
cascades (1) ──> (N) bayesian_beliefs
```

### 3a. Verification & Grounded Calibration (5 tables, v1.5.0+)
- **grounded_beliefs** - Parallel to bayesian_beliefs but evidence-based (objective grounding)
- **verification_evidence** - Raw deterministic service observation records per session
- **grounded_verifications** - Per-session self-assessed vs grounded comparison results
- **calibration_trajectory** - POSTFLIGHT-to-POSTFLIGHT tracking points for long-term calibration
- **calibration_insights** - Systemic calibration patterns detected across verification history *(v1.5.10)*

**Relationships:**
```
sessions (1) ──> (N) grounded_beliefs
sessions (1) ──> (N) verification_evidence
sessions (1) ──> (N) grounded_verifications
sessions (1) ──> (N) calibration_trajectory
sessions (1) ──> (N) calibration_insights
```

### 4. Goals & Tasks System (6 tables)
- **goals** - Goals with success criteria and status tracking
- **subtasks** - Individual tasks associated with goals
- **goal_dependencies** - Dependencies between goals
- **subtask_dependencies** - Dependencies between subtasks
- **success_criteria** - Measurable success criteria for goals
- **task_decompositions** - Task breakdown hierarchy

**Relationships:**
```
sessions (1) ──> (N) goals
goals (1) ──> (N) subtasks
goals (1) ──> (N) goal_dependencies
subtasks (1) ──> (N) subtask_dependencies
goals (1) ──> (N) success_criteria
goals (1) ──> (N) task_decompositions
```

### 5. Project Management (8 tables)
- **projects** - Multi-session projects (name, description, repos)
- **project_handoffs** - AI-to-AI handoffs within project
- **handoff_reports** - Session handoff reports
- **project_findings** - Cross-session discoveries
- **project_unknowns** - Unresolved questions
- **project_dead_ends** - Failed approaches
- **project_reference_docs** - Documentation links
- **epistemic_sources** - Source attribution (docs, URLs, code)

**Relationships:**
```
projects (1) ──> (N) sessions
projects (1) ──> (N) project_handoffs
projects (1) ──> (N) project_findings
projects (1) ──> (N) project_unknowns
projects (1) ──> (N) project_dead_ends
projects (1) ──> (N) project_reference_docs
projects (1) ──> (N) epistemic_sources
sessions (1) ──> (N) handoff_reports
```

### 6. Investigation & Branching (2 tables)
- **investigation_branches** - Multi-branch investigations
- **merge_decisions** - Branch merge outcomes

**Relationships:**
```
sessions (1) ──> (N) investigation_branches
investigation_branches (1) ──> (N) merge_decisions
```

> **Deprecated:** `investigation_tools`, `investigation_logs`, and `act_logs` tables were removed in v1.2.0. Action logging now uses the `reflexes` table with structured JSON payloads.

### 7. Learning & Mistakes (1 table)
- **mistakes_made** - Error tracking with root cause analysis

**Relationships:**
```
sessions (1) ──> (N) mistakes_made
goals (1) ──> (N) mistakes_made
```

### 8. Efficiency Tracking (1 table)
- **token_savings** - Git notes compression metrics

**Relationships:**
```
sessions (1) ──> (N) token_savings
```

### ~~9. Session-Level Breadcrumbs~~ *(REMOVED v1.5.0)*

> **Dropped in v1.5.0 (Migration 027):** `session_findings`, `session_unknowns`, `session_dead_ends`, `session_mistakes` were removed. Session-scoped queries now use the project-level tables with `session_id` + `transaction_id` filters. This simplifies the schema and avoids data duplication.

### 10. Lessons System (6 tables)
- **lessons** - Reusable learning units with procedural knowledge
- **lesson_steps** - Individual steps within a lesson
- **lesson_epistemic_deltas** - Expected vector changes per lesson
- **lesson_prerequisites** - Lesson dependencies
- **lesson_corrections** - Corrections applied to lessons over time
- **lesson_replays** - Records of lesson application

**Relationships:**
```
lessons (1) ──> (N) lesson_steps
lessons (1) ──> (N) lesson_epistemic_deltas
lessons (1) ──> (N) lesson_prerequisites
lessons (1) ──> (N) lesson_corrections
sessions (1) ──> (N) lesson_replays
lessons (1) ──> (N) lesson_replays
```

### 11. Auto-Capture System (1 table)
- **auto_captured_issues** - Issues auto-captured from CLI errors and exceptions

**Relationships:**
```
sessions (1) ──> (N) auto_captured_issues
projects (1) ──> (N) auto_captured_issues
```

### 12. Infrastructure (2 tables)
- **knowledge_graph** - Concept relationships for semantic linking
- **schema_migrations** - Database migration version tracking

**Relationships:**
```
(standalone tables)
```

---

## Detailed Table Schemas

### Core Tables

#### `sessions`
**13 columns**
- `session_id` TEXT PRIMARY KEY
- `ai_id` TEXT NOT NULL
- `user_id` TEXT
- `start_time` TIMESTAMP NOT NULL
- `end_time` TIMESTAMP
- `components_loaded` INTEGER NOT NULL
- `total_turns` INTEGER DEFAULT 0
- `total_cascades` INTEGER DEFAULT 0
- `avg_confidence` REAL
- `drift_detected` BOOLEAN DEFAULT 0
- `session_notes` TEXT
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `cascades`
**22 columns**
- `cascade_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `task` TEXT NOT NULL
- `context_json` TEXT
- `goal_id` TEXT
- `goal_json` TEXT
- `preflight_completed` BOOLEAN DEFAULT 0
- `think_completed` BOOLEAN DEFAULT 0
- `plan_completed` BOOLEAN DEFAULT 0
- `investigate_completed` BOOLEAN DEFAULT 0
- `check_completed` BOOLEAN DEFAULT 0
- `act_completed` BOOLEAN DEFAULT 0
- `postflight_completed` BOOLEAN DEFAULT 0
- `final_action` TEXT
- `final_confidence` REAL
- `investigation_rounds` INTEGER DEFAULT 0
- `duration_ms` INTEGER
- `started_at` TIMESTAMP NOT NULL
- `completed_at` TIMESTAMP
- `engagement_gate_passed` BOOLEAN
- `bayesian_active` BOOLEAN DEFAULT 0
- `drift_monitored` BOOLEAN DEFAULT 0

#### `reflexes`
**24 columns**
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `cascade_id` TEXT (FK: cascades.cascade_id)
- `phase` TEXT NOT NULL
- `round` INTEGER DEFAULT 1
- `timestamp` REAL NOT NULL
- `engagement` REAL
- `know` REAL
- `do` REAL
- `context` REAL
- `clarity` REAL
- `coherence` REAL
- `signal` REAL
- `density` REAL
- `state` REAL
- `change` REAL
- `completion` REAL
- `impact` REAL
- `uncertainty` REAL
- `reflex_data` TEXT
- `reasoning` TEXT
- `evidence` TEXT
- `project_id` TEXT *(v1.4.0 — project context tracking)*
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*

---

### Epistemic Tables

#### `epistemic_snapshots`
**23 columns**
- `snapshot_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `timestamp` TEXT NOT NULL
- `cascade_phase` TEXT
- `cascade_id` TEXT (FK: cascades.cascade_id)
- `vectors` TEXT NOT NULL
- `delta` TEXT
- `previous_snapshot_id` TEXT (FK: epistemic_snapshots.snapshot_id)
- `context_summary` TEXT
- `evidence_refs` TEXT
- `db_session_ref` TEXT
- `domain_vectors` TEXT
- `original_context_tokens` INTEGER DEFAULT 0
- `snapshot_tokens` INTEGER DEFAULT 0
- `compression_ratio` REAL DEFAULT 0.0
- `information_loss_estimate` REAL DEFAULT 0.0
- `fidelity_score` REAL DEFAULT 1.0
- `transfer_count` INTEGER DEFAULT 0
- `created_at` TEXT DEFAULT CURRENT_TIMESTAMP

#### `divergence_tracking`
**17 columns**
- `divergence_id` TEXT PRIMARY KEY
- `cascade_id` TEXT NOT NULL (FK: cascades.cascade_id)
- `turn_number` INTEGER NOT NULL
- `delegate_perspective` TEXT
- `trustee_perspective` TEXT
- `divergence_score` REAL NOT NULL
- `divergence_reason` TEXT
- `synthesis_needed` BOOLEAN NOT NULL
- `delegate_weight` REAL
- `trustee_weight` REAL
- `tension_acknowledged` BOOLEAN
- `final_response` TEXT
- `synthesis_strategy` TEXT
- `user_alerted` BOOLEAN DEFAULT 0
- `sycophancy_reset` BOOLEAN DEFAULT 0
- `recorded_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `drift_monitoring`
**15 columns**
- `drift_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `analysis_window_start` TIMESTAMP
- `analysis_window_end` TIMESTAMP
- `sycophancy_detected` BOOLEAN DEFAULT 0
- `delegate_weight_early` REAL
- `delegate_weight_recent` REAL
- `delegate_weight_drift` REAL
- `tension_avoidance_detected` BOOLEAN DEFAULT 0
- `tension_rate_early` REAL
- `tension_rate_recent` REAL
- `tension_rate_drift` REAL
- `recommendation` TEXT
- `severity` TEXT
- `analyzed_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

---

### Bayesian Tables

#### `bayesian_beliefs`
**9 columns**
- `belief_id` TEXT PRIMARY KEY
- `cascade_id` TEXT NOT NULL (FK: cascades.cascade_id)
- `vector_name` TEXT NOT NULL
- `mean` REAL NOT NULL
- `variance` REAL NOT NULL
- `evidence_count` INTEGER DEFAULT 0
- `prior_mean` REAL NOT NULL
- `prior_variance` REAL NOT NULL
- `last_updated` TIMESTAMP

---

### Verification & Grounded Calibration Tables (v1.5.0)

#### `grounded_beliefs`
**13 columns**
- `belief_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `vector_name` TEXT NOT NULL
- `mean` REAL NOT NULL
- `variance` REAL NOT NULL
- `evidence_count` INTEGER DEFAULT 0
- `last_observation` REAL
- `last_observation_source` TEXT
- `self_referential_mean` REAL
- `divergence` REAL
- `last_updated` REAL
- `phase` TEXT DEFAULT 'combined' *(v1.5.1 — noetic/praxic/combined)*

> Parallel to `bayesian_beliefs` but evidence-based. Tracks grounded belief distributions per AI per vector, with divergence from self-assessed values. Phase-aware: noetic beliefs (investigation-grounded), praxic beliefs (action-grounded), or combined. Indexed on `(ai_id, vector_name)`.

#### `verification_evidence`
**10 columns**
- `evidence_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `source` TEXT NOT NULL
- `metric_name` TEXT NOT NULL
- `raw_value` TEXT
- `normalized_value` REAL NOT NULL
- `quality` TEXT NOT NULL
- `supports_vectors` TEXT NOT NULL
- `collected_at` REAL NOT NULL
- `metadata` TEXT

> Raw deterministic service observation records per session. Each record identifies which epistemic vectors it supports via `supports_vectors` (JSON array). Sources include test results, artifact counts, goal completion rates. Indexed on `(session_id)`.

#### `grounded_verifications`
**14 columns**
- `verification_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `self_assessed_vectors` TEXT NOT NULL
- `grounded_vectors` TEXT
- `calibration_gaps` TEXT
- `grounded_coverage` REAL
- `overall_calibration_score` REAL
- `evidence_count` INTEGER DEFAULT 0
- `sources_available` TEXT
- `sources_failed` TEXT
- `domain` TEXT
- `goal_id` TEXT
- `phase` TEXT DEFAULT 'combined' *(v1.5.1 — noetic/praxic/combined)*
- `created_at` REAL DEFAULT (strftime('%s', 'now'))

> Per-session comparison results: self-assessed vectors vs objectively grounded vectors, with calibration gap analysis, coverage metrics, and source diagnostics. Phase-aware: noetic verifications use CHECK vectors, praxic use POSTFLIGHT vectors. Indexed on `(session_id)`.

#### `calibration_trajectory`
**11 columns**
- `point_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `vector_name` TEXT NOT NULL
- `self_assessed` REAL NOT NULL
- `grounded` REAL
- `gap` REAL
- `domain` TEXT
- `goal_id` TEXT
- `phase` TEXT DEFAULT 'combined' *(v1.5.1 — noetic/praxic/combined)*
- `timestamp` REAL NOT NULL

> POSTFLIGHT-to-POSTFLIGHT trajectory points. Tracks how self-assessed vs grounded values evolve over time per vector, enabling long-term calibration trend analysis. Phase-aware: noetic trajectory (investigation calibration), praxic trajectory (action calibration). Used by dynamic thresholds to compute earned autonomy. Indexed on `(ai_id, vector_name, timestamp)` and `(ai_id, phase, vector_name, timestamp)`.

#### `calibration_insights`
**13 columns** *(v1.5.10)*
- `insight_id` TEXT PRIMARY KEY
- `session_id` TEXT
- `transaction_id` TEXT
- `vector` TEXT
- `phase` TEXT *(noetic/praxic/both)*
- `pattern` TEXT *(chronic_overestimate, chronic_underestimate, evidence_gap, phase_mismatch, volatile)*
- `severity` REAL *(0.0-1.0)*
- `description` TEXT
- `suggestion` TEXT *(machine-readable improvement hint)*
- `evidence_sources` TEXT *(JSON array)*
- `observation_count` INTEGER
- `acted_on` BOOLEAN DEFAULT FALSE
- `created_at` REAL

> Systemic calibration patterns detected by `CalibrationInsightsAnalyzer` across recent grounded verifications. Each insight identifies a recurring measurement issue (e.g., chronic overestimation of a vector, evidence gap for a phase) and suggests an improvement. The `acted_on` flag closes the feedback loop: when an insight leads to an actual improvement in evidence collection, mark it acted_on to track method improvement over time.

---

### Goals & Tasks Tables

#### `goals`
**14 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `objective` TEXT NOT NULL
- `scope` TEXT NOT NULL
- `estimated_complexity` REAL
- `created_timestamp` REAL NOT NULL
- `completed_timestamp` REAL
- `is_completed` BOOLEAN DEFAULT 0
- `goal_data` TEXT NOT NULL
- `status` TEXT DEFAULT 'in_progress'
- `beads_issue_id` TEXT
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*
- `entity_type` TEXT DEFAULT 'project' *(v1.5.1 — entity-agnostic intent layer)*
- `entity_id` TEXT *(v1.5.1 — entity-agnostic intent layer)*

#### `subtasks`
**13 columns**
- `id` TEXT PRIMARY KEY
- `goal_id` TEXT NOT NULL (FK: goals.id)
- `description` TEXT NOT NULL
- `status` TEXT NOT NULL DEFAULT 'pending'
- `epistemic_importance` TEXT NOT NULL DEFAULT 'medium'
- `estimated_tokens` INTEGER
- `actual_tokens` INTEGER
- `completion_evidence` TEXT
- `notes` TEXT
- `created_timestamp` REAL NOT NULL
- `completed_timestamp` REAL
- `subtask_data` TEXT NOT NULL

---

### Project Management Tables

#### `projects`
**15 columns**
- `id` TEXT PRIMARY KEY
- `name` TEXT NOT NULL
- `description` TEXT
- `repos` TEXT
- `created_timestamp` REAL NOT NULL
- `last_activity_timestamp` REAL
- `status` TEXT DEFAULT 'active'
- `metadata` TEXT
- `total_sessions` INTEGER DEFAULT 0
- `total_goals` INTEGER DEFAULT 0
- `total_epistemic_deltas` TEXT
- `project_data` TEXT NOT NULL

#### `project_handoffs`
**13 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `created_timestamp` REAL NOT NULL
- `project_summary` TEXT NOT NULL
- `sessions_included` TEXT NOT NULL
- `total_learning_deltas` TEXT
- `key_decisions` TEXT
- `patterns_discovered` TEXT
- `mistakes_summary` TEXT
- `remaining_work` TEXT
- `repos_touched` TEXT
- `next_session_bootstrap` TEXT
- `handoff_data` TEXT NOT NULL

#### `handoff_reports`
**17 columns**
- `session_id` TEXT PRIMARY KEY (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `timestamp` TEXT NOT NULL
- `task_summary` TEXT
- `duration_seconds` REAL
- `epistemic_deltas` TEXT
- `key_findings` TEXT
- `knowledge_gaps_filled` TEXT
- `remaining_unknowns` TEXT
- `investigation_tools` TEXT
- `next_session_context` TEXT
- `recommended_next_steps` TEXT
- `artifacts_created` TEXT
- `calibration_status` TEXT
- `overall_confidence_delta` REAL
- `compressed_json` TEXT
- `markdown_report` TEXT
- `created_at` REAL NOT NULL

#### `project_findings`
**11 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `subtask_id` TEXT (FK: subtasks.id)
- `finding` TEXT NOT NULL
- `created_timestamp` REAL NOT NULL
- `finding_data` TEXT NOT NULL
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*
- `entity_type` TEXT DEFAULT 'project' *(v1.5.1 — entity-agnostic intent layer)*
- `entity_id` TEXT *(v1.5.1 — entity-agnostic intent layer)*

#### `project_unknowns`
**14 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `subtask_id` TEXT (FK: subtasks.id)
- `unknown` TEXT NOT NULL
- `is_resolved` BOOLEAN DEFAULT FALSE
- `resolved_by` TEXT
- `created_timestamp` REAL NOT NULL
- `resolved_timestamp` REAL
- `unknown_data` TEXT NOT NULL
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*
- `entity_type` TEXT DEFAULT 'project' *(v1.5.1 — entity-agnostic intent layer)*
- `entity_id` TEXT *(v1.5.1 — entity-agnostic intent layer)*

#### `project_dead_ends`
**12 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `subtask_id` TEXT (FK: subtasks.id)
- `approach` TEXT NOT NULL
- `why_failed` TEXT NOT NULL
- `created_timestamp` REAL NOT NULL
- `dead_end_data` TEXT NOT NULL
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*
- `entity_type` TEXT DEFAULT 'project' *(v1.5.1 — entity-agnostic intent layer)*
- `entity_id` TEXT *(v1.5.1 — entity-agnostic intent layer)*

#### `project_reference_docs`
**7 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `doc_path` TEXT NOT NULL
- `doc_type` TEXT
- `description` TEXT
- `created_timestamp` REAL NOT NULL
- `doc_data` TEXT NOT NULL

#### `epistemic_sources`
**16 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `source_type` TEXT NOT NULL
- `source_url` TEXT
- `title` TEXT NOT NULL
- `description` TEXT
- `confidence` REAL DEFAULT 0.5
- `epistemic_layer` TEXT
- `supports_vectors` TEXT
- `related_findings` TEXT
- `discovered_by_ai` TEXT
- `discovered_at` TIMESTAMP NOT NULL
- `source_metadata` TEXT
- `entity_type` TEXT DEFAULT 'project' *(v1.5.1 — entity-agnostic intent layer)*
- `entity_id` TEXT *(v1.5.1 — entity-agnostic intent layer)*

---

### Investigation Tables

#### `investigation_tools`
**11 columns**
- `tool_execution_id` TEXT PRIMARY KEY
- `cascade_id` TEXT NOT NULL (FK: cascades.cascade_id)
- `round_number` INTEGER NOT NULL
- `tool_name` TEXT NOT NULL
- `tool_purpose` TEXT
- `target_vector` TEXT
- `success` BOOLEAN NOT NULL
- `confidence_gain` REAL
- `information_gained` TEXT
- `duration_ms` INTEGER
- `executed_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `investigation_logs`
**10 columns**
- `log_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `cascade_id` TEXT (FK: cascades.cascade_id)
- `round_number` INTEGER NOT NULL
- `tools_mentioned` TEXT
- `findings` TEXT
- `confidence_before` REAL
- `confidence_after` REAL
- `summary` TEXT
- `assessed_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `act_logs`
**8 columns**
- `act_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `cascade_id` TEXT (FK: cascades.cascade_id)
- `action_type` TEXT NOT NULL
- `action_rationale` TEXT
- `final_confidence` REAL
- `goal_id` TEXT
- `assessed_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `investigation_branches`
**21 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `branch_name` TEXT NOT NULL
- `investigation_path` TEXT NOT NULL
- `git_branch_name` TEXT NOT NULL
- `preflight_vectors` TEXT NOT NULL
- `postflight_vectors` TEXT
- `tokens_spent` INTEGER DEFAULT 0
- `time_spent_minutes` INTEGER DEFAULT 0
- `merge_score` REAL
- `epistemic_quality` REAL
- `is_winner` BOOLEAN DEFAULT FALSE
- `created_timestamp` REAL NOT NULL
- `checkpoint_timestamp` REAL
- `merged_timestamp` REAL
- `status` TEXT DEFAULT 'active'
- `branch_metadata` TEXT
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*

#### `merge_decisions`
**11 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `investigation_round` INTEGER NOT NULL
- `winning_branch_id` TEXT NOT NULL (FK: investigation_branches.id)
- `winning_branch_name` TEXT
- `winning_score` REAL NOT NULL
- `other_branches` TEXT
- `decision_rationale` TEXT NOT NULL
- `auto_merged` BOOLEAN DEFAULT TRUE
- `created_timestamp` REAL NOT NULL
- `decision_metadata` TEXT

---

### Learning & Efficiency Tables

#### `mistakes_made`
**14 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `mistake` TEXT NOT NULL
- `why_wrong` TEXT NOT NULL
- `cost_estimate` TEXT
- `root_cause_vector` TEXT
- `prevention` TEXT
- `created_timestamp` REAL NOT NULL
- `mistake_data` TEXT NOT NULL
- `transaction_id` TEXT *(v1.5.0 — epistemic transaction scoping)*
- `entity_type` TEXT DEFAULT 'project' *(v1.5.1 — entity-agnostic intent layer)*
- `entity_id` TEXT *(v1.5.1 — entity-agnostic intent layer)*

#### `token_savings`
**6 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `saving_type` TEXT NOT NULL
- `tokens_saved` INTEGER NOT NULL
- `evidence` TEXT
- `logged_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

---

### Codebase Model Tables (v1.6.6)

*Temporal entity tracking adapted from [world-model-mcp](https://github.com/SaravananJaichandar/world-model-mcp). Migration 033.*

#### `codebase_entities`
**11 columns** — Functions, classes, APIs, imports with temporal validity
- `id` TEXT PRIMARY KEY
- `entity_type` TEXT NOT NULL — function, class, api, import, file, constant, type_alias
- `name` TEXT NOT NULL
- `file_path` TEXT
- `signature` TEXT — function signature, class bases, import path
- `first_seen` REAL NOT NULL — timestamp when entity was first extracted
- `last_seen` REAL — NULL while active, set when entity disappears from file
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `metadata` TEXT — JSON blob for language-specific details
- Indexes: entity_type, file_path, name, project_id

#### `codebase_facts`
**11 columns** — Temporal assertions about the codebase
- `id` TEXT PRIMARY KEY
- `fact_text` TEXT NOT NULL
- `valid_at` REAL NOT NULL — when the fact became true
- `invalid_at` REAL — NULL while valid, set when invalidated
- `status` TEXT NOT NULL DEFAULT 'canonical' — canonical, retracted, superseded
- `entity_ids` TEXT — JSON array of related entity IDs
- `evidence_type` TEXT — code_analysis, test_result, user_assertion
- `evidence_path` TEXT — file path or test name
- `confidence` REAL DEFAULT 1.0
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- Indexes: status, valid_at+invalid_at, project_id, session_id

#### `codebase_relationships`
**9 columns** — Directional links between entities
- `id` TEXT PRIMARY KEY
- `source_entity_id` TEXT NOT NULL (FK: codebase_entities.id)
- `target_entity_id` TEXT NOT NULL (FK: codebase_entities.id)
- `relationship_type` TEXT NOT NULL — calls, imports, depends_on, inherits
- `weight` REAL DEFAULT 1.0
- `first_seen` REAL NOT NULL
- `last_seen` REAL NOT NULL
- `evidence_count` INTEGER DEFAULT 1
- `project_id` TEXT (FK: projects.id)
- Indexes: source_entity_id, target_entity_id, relationship_type, project_id

#### `codebase_constraints`
**12 columns** — Learned patterns from corrections (extends lessons)
- `id` TEXT PRIMARY KEY
- `constraint_type` TEXT NOT NULL — naming, import_order, pattern, convention
- `rule_name` TEXT NOT NULL
- `file_pattern` TEXT — glob pattern for affected files
- `description` TEXT
- `violation_count` INTEGER DEFAULT 0
- `last_violated` REAL
- `examples` TEXT — JSON array of violation examples
- `severity` TEXT DEFAULT 'warning' — info, warning, error
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `created_at` REAL NOT NULL
- Indexes: constraint_type, rule_name, project_id, violation_count DESC

---

## Key Foreign Key Relationships

```
                    ┌─────────────┐
                    │  projects   │
                    └──────┬──────┘
                           │
                           │ (1:N)
                           │
                    ┌──────▼──────┐
                    │  sessions   │───────────────────┐
                    └──────┬──────┘                    │
                           │                          │
              ┌────────────┼──────────┐               │
              │            │          │               │(1:N)
         (1:N)│       (1:N)│     (1:N)│               │
              │            │          │               │
       ┌──────▼───┐  ┌────▼────┐  ┌─▼───▼───┐  ┌────▼──────────────────┐
       │ cascades │  │ reflexes│  │  goals   │  │ verification (v1.5.0) │
       └──────┬───┘  └─────────┘  └─────┬────┘  ├───────────────────────┤
              │                          │       │ grounded_beliefs      │
         (1:N)│                     (1:N)│       │ verification_evidence │
              │                          │       │ grounded_verifications│
    ┌─────────▼────────┐          ┌──────▼────┐  │ calibration_trajectory│
    │ bayesian_beliefs │          │  subtasks  │  └───────────────────────┘
    └──────────────────┘          └───────────┘
```

---

## Access Patterns

**SessionDatabase** (main facade):
- Direct SQL for sessions, cascades, reflexes
- data.repositories for goals, subtasks, projects
- Lazy-loaded core.goals.repository and core.tasks.repository for advanced queries

**Repository Methods:**
- `query_goals()` → data.repositories.GoalRepository
- `query_subtasks()` → data.repositories.SubtaskRepository
- `get_project_findings()` → data.repositories.ProjectRepository
- `track_epistemic_state()` → data.repositories.VectorRepository

---

## Storage Locations

- Project-local: `./.empirica/sessions/sessions.db`
- Global config: `~/.empirica/config.yaml`
- Git notes: Compressed checkpoints (~97.5% token reduction)

---

## Notes

- **Dual Goal System Resolved**: The unified schema now uses a single goals/subtasks system with proper foreign key relationships
- **Modular Architecture**: Tables are organized in logical modules (sessions, epistemic, goals, projects, tracking)
- **Indexing**: All tables have appropriate indexes for performance
- **Every project has its own SQLite database** mapped to the git repository
- **Grounded Calibration (v1.5.0)**: Four verification tables provide post-test observation grounding for belief calibration, enabling comparison of belief vectors vs service-observed values
- **Transaction-First Architecture (v1.5.0)**: `transaction_id` column added to reflexes and all artifact tables (findings, unknowns, dead-ends, mistakes, goals, branches) for epistemic transaction scoping across compaction boundaries
- **Entity-Agnostic Intent Layer (v1.5.1)**: `entity_type` + `entity_id` columns on artifact tables enable cross-entity tracking beyond project scope
- **Phase-Aware Calibration (v1.5.1)**: `phase` column on grounded verification tables splits calibration into noetic (investigation) and praxic (action) tracks with dynamic thresholds

---

Generated: 2026-02-11