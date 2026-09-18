# Empirica Database Schema (Unified)

> **Table inventory is generated.** The *Table Inventory* section below is written by
> `scripts/gen_schema_doc.py` from the schema a fresh install materialises
> (`ALL_SCHEMAS` + the full migration registry) and CI fails when it is stale.
> The category prose above it is hand-curated and may lag: the inventory is the
> complete list. For a live database's shape use `PRAGMA table_info(<table>)`; to see
> what a live db holds beyond the registry, `python3 scripts/gen_schema_doc.py --diff-db <sessions.db>`.

**Total Tables:** see the generated inventory below
**Database Type:** SQLite (with PostgreSQL adapter support)
**Architecture:** Modular with unified goal/task system, transaction-first tracking
**Every project (mapped to git repo) has its own SQLite database**
**Hand-authored sections last reviewed:** 2026-09-18 (generated inventory: see `git log` on this file)

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

<!-- BEGIN GENERATED: table inventory (scripts/gen_schema_doc.py) — do not edit by hand -->

## Table Inventory (generated)

**67 tables** (+ 4 FTS5 shadow tables), **131 indexes**, **8 triggers** — the schema a fresh install materialises: `ALL_SCHEMAS` plus every migration in `empirica/data/migrations/migrations.py`, applied in order.

Regenerate with `python3 scripts/gen_schema_doc.py`; CI fails when this block is stale. A live database can hold tables outside this inventory (created lazily by code, or left behind by removed schemas): `python3 scripts/gen_schema_doc.py --diff-db <sessions.db>` lists them.

Alphabetical. Column lines read `name TYPE [PRIMARY KEY|NOT NULL] [DEFAULT x] [(FK: table.col)]`.

#### `artifact_edges`
**5 columns**
- `from_id` TEXT PRIMARY KEY
- `to_id` TEXT PRIMARY KEY
- `relation` TEXT PRIMARY KEY
- `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
- `metadata` TEXT
- *indexes:* `idx_artifact_edges_from`, `idx_artifact_edges_to`

#### `assumptions`
**20 columns**
- `id` TEXT PRIMARY KEY
- `assumption` TEXT NOT NULL
- `description` TEXT
- `confidence` REAL DEFAULT 0.5
- `status` TEXT NOT NULL DEFAULT 'unverified'
- `resolution_finding_id` TEXT
- `entity_type` TEXT NOT NULL DEFAULT 'project'
- `entity_id` TEXT
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `transaction_id` TEXT
- `goal_id` TEXT
- `created_by_ai` TEXT
- `created_timestamp` REAL NOT NULL
- `resolved_timestamp` REAL
- `visibility` TEXT DEFAULT 'shared'
- `epistemic_source` TEXT
- `last_retrieved_at` REAL DEFAULT NULL
- `retrieval_count` INTEGER DEFAULT 0
- `retrieval_count_at_resolution` INTEGER DEFAULT NULL
- *indexes:* `idx_assumptions_entity`, `idx_assumptions_epistemic_source`, `idx_assumptions_status`, `idx_assumptions_visibility`

#### `attention_budgets`
**9 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `total_budget` INTEGER NOT NULL
- `allocated` INTEGER DEFAULT 0
- `remaining` INTEGER NOT NULL
- `strategy` TEXT DEFAULT 'information_gain'
- `domain_allocations` TEXT
- `created_at` REAL NOT NULL
- `updated_at` REAL NOT NULL
- *indexes:* `idx_attention_budgets_session`

#### `auto_captured_issues`
**14 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `severity` TEXT NOT NULL
- `category` TEXT NOT NULL
- `code_location` TEXT
- `message` TEXT NOT NULL
- `stack_trace` TEXT
- `context` TEXT
- `status` TEXT DEFAULT 'new'
- `assigned_to_ai` TEXT
- `root_cause_id` TEXT
- `resolution` TEXT
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- *indexes:* `idx_issues_session_status`

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

#### `beads`
**17 columns**
- `id` TEXT PRIMARY KEY
- `coordination_state` TEXT NOT NULL
- `updated_at` REAL NOT NULL
- `last_transition_actor` TEXT
- `beads_issue_id` TEXT
- `scope` TEXT
- `description` TEXT
- `entity_type` TEXT NOT NULL DEFAULT 'project'
- `entity_id` TEXT
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `transaction_id` TEXT
- `goal_id` TEXT
- `created_by_ai` TEXT
- `created_timestamp` REAL NOT NULL
- `visibility` TEXT
- `epistemic_source` TEXT
- *indexes:* `idx_beads_beads_issue_id`, `idx_beads_coordination_state`, `idx_beads_entity`, `idx_beads_project`, `idx_beads_transaction`

#### `blindspot_events`
**12 columns**
- `id` INTEGER PRIMARY KEY
- `session_id` TEXT
- `transaction_id` TEXT
- `created_timestamp` REAL NOT NULL
- `kind` TEXT
- `goal_id` TEXT
- `subtask_id` TEXT
- `intent` TEXT
- `surfaced_at` TEXT
- `outcome` TEXT NOT NULL DEFAULT 'surfaced'
- `resolved_timestamp` REAL
- `derived_from` TEXT DEFAULT NULL
- *indexes:* `idx_blindspot_events_session`, `idx_blindspot_events_subtask`, `idx_blindspot_events_txn`

#### `calibration_disputes`
**11 columns**
- `dispute_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `vector` TEXT NOT NULL
- `reported_value` REAL NOT NULL
- `expected_value` REAL NOT NULL
- `reason` TEXT NOT NULL
- `evidence` TEXT
- `work_context` TEXT
- `status` TEXT DEFAULT 'open'
- `resolution` TEXT
- `created_at` REAL DEFAULT strftime('%s', 'now')
- *indexes:* `idx_calibration_disputes_session`, `idx_calibration_disputes_vector_status`

#### `calibration_insights`
**13 columns**
- `insight_id` TEXT PRIMARY KEY
- `session_id` TEXT
- `transaction_id` TEXT
- `vector` TEXT
- `phase` TEXT
- `pattern` TEXT
- `severity` REAL
- `description` TEXT
- `suggestion` TEXT
- `evidence_sources` TEXT
- `observation_count` INTEGER
- `acted_on` BOOLEAN DEFAULT FALSE
- `created_at` REAL

#### `calibration_trajectory`
**16 columns**
- `point_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `vector_name` TEXT NOT NULL
- `self_assessed` REAL NOT NULL
- `grounded` REAL
- `gap` REAL
- `domain` TEXT
- `goal_id` TEXT
- `timestamp` REAL NOT NULL
- `state_type` TEXT DEFAULT 'grounded'
- `phase` TEXT DEFAULT 'combined'
- `transaction_id` TEXT DEFAULT NULL
- `evidence_count` INTEGER DEFAULT NULL
- `primary_source` TEXT DEFAULT NULL
- `grounded_raw` TEXT DEFAULT NULL
- *indexes:* `idx_calibration_trajectory_ai_vector`, `idx_calibration_trajectory_phase`

#### `cascades`
**24 columns**
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
- `epistemic_delta` TEXT
- `work_type` TEXT DEFAULT NULL

#### `client_projects`
**10 columns**
- `id` TEXT PRIMARY KEY
- `client_id` TEXT NOT NULL
- `project_id` TEXT NOT NULL (FK: projects.id)
- `relationship_type` TEXT DEFAULT 'customer'
- `status` TEXT DEFAULT 'active'
- `started_at` REAL NOT NULL
- `ended_at` REAL
- `notes` TEXT
- `created_at` REAL NOT NULL
- `created_by_ai_id` TEXT
- *indexes:* `idx_client_projects_client`, `idx_client_projects_project`, `idx_client_projects_status`

#### `codebase_constraints`
**12 columns**
- `id` TEXT PRIMARY KEY
- `constraint_type` TEXT NOT NULL
- `rule_name` TEXT NOT NULL
- `file_pattern` TEXT
- `description` TEXT
- `violation_count` INTEGER DEFAULT 0
- `last_violated` REAL
- `examples` TEXT
- `severity` TEXT DEFAULT 'warning'
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `created_at` REAL NOT NULL
- *indexes:* `idx_codebase_constraints_project`, `idx_codebase_constraints_rule`, `idx_codebase_constraints_type`, `idx_codebase_constraints_violations`

#### `codebase_entities`
**10 columns**
- `id` TEXT PRIMARY KEY
- `entity_type` TEXT NOT NULL
- `name` TEXT NOT NULL
- `file_path` TEXT
- `signature` TEXT
- `first_seen` REAL NOT NULL
- `last_seen` REAL
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `metadata` TEXT
- *indexes:* `idx_codebase_entities_file`, `idx_codebase_entities_name`, `idx_codebase_entities_project`, `idx_codebase_entities_type`

#### `codebase_facts`
**11 columns**
- `id` TEXT PRIMARY KEY
- `fact_text` TEXT NOT NULL
- `valid_at` REAL NOT NULL
- `invalid_at` REAL
- `status` TEXT NOT NULL DEFAULT 'canonical'
- `entity_ids` TEXT
- `evidence_type` TEXT
- `evidence_path` TEXT
- `confidence` REAL DEFAULT 1.0
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- *indexes:* `idx_codebase_facts_project`, `idx_codebase_facts_session`, `idx_codebase_facts_status`, `idx_codebase_facts_valid`

#### `codebase_facts_fts`
**1 columns**
- `fact_text` ANY

#### `codebase_relationships`
**9 columns**
- `id` TEXT PRIMARY KEY
- `source_entity_id` TEXT NOT NULL (FK: codebase_entities.id)
- `target_entity_id` TEXT NOT NULL (FK: codebase_entities.id)
- `relationship_type` TEXT NOT NULL
- `weight` REAL DEFAULT 1.0
- `first_seen` REAL NOT NULL
- `last_seen` REAL NOT NULL
- `evidence_count` INTEGER DEFAULT 1
- `project_id` TEXT (FK: projects.id)
- *indexes:* `idx_codebase_rel_project`, `idx_codebase_rel_source`, `idx_codebase_rel_target`, `idx_codebase_rel_type`

#### `compliance_checks`
**13 columns**
- `check_record_id` TEXT PRIMARY KEY
- `transaction_id` TEXT NOT NULL
- `session_id` TEXT NOT NULL
- `check_id` TEXT NOT NULL
- `tool` TEXT NOT NULL
- `passed` INTEGER NOT NULL
- `details` TEXT
- `summary` TEXT NOT NULL
- `duration_ms` INTEGER NOT NULL
- `ran_at` REAL NOT NULL
- `predicted_pass` REAL
- `predicted_at` REAL
- `iteration_number` INTEGER DEFAULT 1
- *indexes:* `idx_compliance_checks_check_id`, `idx_compliance_checks_tx`

#### `concept_clusters`
**9 columns**
- `cluster_id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `cluster_name` TEXT
- `concept_ids` TEXT NOT NULL
- `dominant_concepts` TEXT
- `cohesion` REAL DEFAULT 0.0
- `size` INTEGER DEFAULT 0
- `created_timestamp` REAL NOT NULL
- `updated_timestamp` REAL NOT NULL

#### `concept_edges`
**10 columns**
- `edge_id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `source_concept_id` TEXT NOT NULL (FK: concept_nodes.concept_id)
- `target_concept_id` TEXT NOT NULL (FK: concept_nodes.concept_id)
- `relationship_type` TEXT DEFAULT 'co_occurs'
- `weight` REAL DEFAULT 0.0
- `co_occurrence_count` INTEGER DEFAULT 1
- `session_ids` TEXT
- `first_seen_timestamp` REAL NOT NULL
- `last_seen_timestamp` REAL NOT NULL
- *indexes:* `idx_concept_edges_source`, `idx_concept_edges_target`, `idx_concept_edges_weight`

#### `concept_nodes`
**12 columns**
- `concept_id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `concept_text` TEXT NOT NULL
- `normalized_text` TEXT NOT NULL
- `source_type` TEXT NOT NULL
- `frequency` INTEGER DEFAULT 1
- `total_impact` REAL DEFAULT 0.0
- `avg_impact` REAL DEFAULT 0.0
- `first_seen_timestamp` REAL NOT NULL
- `last_seen_timestamp` REAL NOT NULL
- `session_ids` TEXT NOT NULL
- `source_ids` TEXT NOT NULL
- *indexes:* `idx_concept_nodes_normalized`, `idx_concept_nodes_project`

#### `context_budget_state`
**8 columns**
- `session_id` TEXT PRIMARY KEY
- `node_id` TEXT
- `inventory_json` TEXT
- `thresholds_json` TEXT
- `page_faults` INTEGER
- `evictions` INTEGER
- `created_at` REAL
- `updated_at` REAL

#### `cross_project_finding_links`
**8 columns**
- `id` TEXT PRIMARY KEY
- `finding_id` TEXT NOT NULL (FK: project_findings.id)
- `source_project_id` TEXT NOT NULL (FK: projects.id)
- `target_project_id` TEXT NOT NULL (FK: projects.id)
- `relevance` REAL DEFAULT 1.0
- `notes` TEXT
- `created_at` REAL NOT NULL
- `created_by_ai_id` TEXT
- *indexes:* `idx_xproj_finding_id`, `idx_xproj_finding_src`, `idx_xproj_finding_tgt`

#### `decisions`
**23 columns**
- `id` TEXT PRIMARY KEY
- `choice` TEXT NOT NULL
- `description` TEXT
- `alternatives` TEXT
- `rationale` TEXT NOT NULL
- `confidence_at_decision` REAL
- `reversibility` TEXT DEFAULT 'committal'
- `entity_type` TEXT NOT NULL DEFAULT 'project'
- `entity_id` TEXT
- `project_id` TEXT (FK: projects.id)
- `session_id` TEXT (FK: sessions.session_id)
- `transaction_id` TEXT
- `goal_id` TEXT
- `outcome` TEXT
- `outcome_assessed_at` REAL
- `regret_score` REAL
- `created_by_ai` TEXT
- `created_timestamp` REAL NOT NULL
- `evidence_refs` TEXT
- `visibility` TEXT DEFAULT 'shared'
- `epistemic_source` TEXT
- `last_retrieved_at` REAL DEFAULT NULL
- `retrieval_count` INTEGER DEFAULT 0
- *indexes:* `idx_decisions_entity`, `idx_decisions_epistemic_source`, `idx_decisions_visibility`

#### `epistemic_events`
**8 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL
- `event_type` TEXT NOT NULL
- `agent_id` TEXT
- `data_json` TEXT
- `timestamp` REAL NOT NULL
- `node_id` TEXT
- `created_at` TEXT DEFAULT datetime('now')

#### `epistemic_snapshots`
**20 columns**
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

#### `epistemic_sources`
**29 columns**
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
- `archived` BOOLEAN DEFAULT 0
- `archive_reason` TEXT
- `archive_target_id` TEXT
- `archived_at` REAL
- `lifecycle_audit_log` TEXT
- `visibility` TEXT DEFAULT 'local'
- `content_hash` TEXT
- `size_bytes` INTEGER
- `canonical_path` TEXT
- `mime_type` TEXT
- `entity_type` TEXT DEFAULT 'project'
- `entity_id` TEXT
- `last_reviewed_at` REAL DEFAULT NULL
- `review_verdict` TEXT DEFAULT NULL
- `cortex_uuid` TEXT DEFAULT NULL
- *indexes:* `idx_epistemic_sources_confidence`, `idx_epistemic_sources_content_hash`, `idx_epistemic_sources_cortex_uuid`, `idx_epistemic_sources_project`, `idx_epistemic_sources_session`, `idx_epistemic_sources_type`, `idx_epistemic_sources_visibility`

#### `goal_dependencies`
**5 columns**
- `id` TEXT PRIMARY KEY
- `goal_id` TEXT NOT NULL (FK: goals.id)
- `depends_on_goal_id` TEXT NOT NULL (FK: goals.id)
- `dependency_type` TEXT NOT NULL
- `description` TEXT

#### `goals`
**21 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `objective` TEXT NOT NULL
- `description` TEXT
- `scope` TEXT NOT NULL
- `estimated_complexity` REAL
- `created_timestamp` REAL NOT NULL
- `completed_timestamp` REAL
- `is_completed` BOOLEAN DEFAULT 0
- `goal_data` TEXT NOT NULL
- `status` TEXT DEFAULT 'in_progress'
- `beads_issue_id` TEXT
- `project_id` TEXT
- `transaction_id` TEXT
- `entity_type` TEXT DEFAULT 'project'
- `entity_id` TEXT
- `visibility` TEXT DEFAULT 'shared'
- `engagement_id` TEXT
- `archived` BOOLEAN DEFAULT 0
- `archived_at` REAL DEFAULT NULL
- `completion_reason` TEXT DEFAULT NULL
- *indexes:* `idx_goals_archived`, `idx_goals_engagement_id`, `idx_goals_transaction`, `idx_goals_transaction_id`, `idx_goals_visibility`

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
- `phase` TEXT DEFAULT 'combined'
- *indexes:* `idx_grounded_beliefs_ai_vector`

#### `grounded_verifications`
**20 columns**
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
- `observed_vectors` TEXT
- `grounded_rationale` TEXT
- `criticality` TEXT
- `compliance_status` TEXT
- `parent_transaction_id` TEXT
- `created_at` REAL DEFAULT strftime('%s', 'now')
- `phase` TEXT DEFAULT 'combined'
- *indexes:* `idx_grounded_verifications_session`

#### `handoff_reports`
**18 columns**
- `session_id` TEXT PRIMARY KEY (FK: sessions.session_id)
- `ai_id` TEXT NOT NULL
- `timestamp` TEXT NOT NULL
- `task_summary` TEXT
- `duration_seconds` REAL
- `epistemic_deltas` TEXT
- `key_findings` TEXT
- `knowledge_gaps_filled` TEXT
- `remaining_unknowns` TEXT
- `noetic_tools` TEXT
- `next_session_context` TEXT
- `recommended_next_steps` TEXT
- `artifacts_created` TEXT
- `calibration_status` TEXT
- `overall_confidence_delta` REAL
- `compressed_json` TEXT
- `markdown_report` TEXT
- `created_at` REAL NOT NULL

#### `investigation_branches`
**18 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `branch_name` TEXT NOT NULL
- `investigation_path` TEXT NOT NULL
- `git_branch_name` TEXT NOT NULL
- `preflight_vectors` TEXT NOT NULL
- `postflight_vectors` TEXT
- `transaction_id` TEXT
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

#### `knowledge_graph`
**9 columns**
- `id` TEXT PRIMARY KEY
- `source_type` TEXT NOT NULL
- `source_id` TEXT NOT NULL
- `relation_type` TEXT NOT NULL
- `target_type` TEXT NOT NULL
- `target_id` TEXT NOT NULL
- `weight` REAL DEFAULT 1.0
- `created_timestamp` REAL NOT NULL
- `metadata` TEXT
- *indexes:* `idx_kg_relation`, `idx_kg_source`, `idx_kg_target`

#### `lesson_corrections`
**9 columns**
- `id` TEXT PRIMARY KEY
- `lesson_id` TEXT NOT NULL (FK: lessons.id)
- `step_order` INTEGER NOT NULL
- `original_action` TEXT NOT NULL
- `corrected_action` TEXT NOT NULL
- `reason` TEXT NOT NULL
- `corrector_type` TEXT NOT NULL
- `corrector_id` TEXT
- `created_timestamp` REAL NOT NULL
- *indexes:* `idx_lesson_corrections_lesson`

#### `lesson_epistemic_deltas`
**4 columns**
- `id` TEXT PRIMARY KEY
- `lesson_id` TEXT NOT NULL (FK: lessons.id)
- `vector_name` TEXT NOT NULL
- `delta_value` REAL NOT NULL
- *indexes:* `idx_lesson_deltas_lesson`, `idx_lesson_deltas_vector`

#### `lesson_prerequisites`
**6 columns**
- `id` TEXT PRIMARY KEY
- `lesson_id` TEXT NOT NULL (FK: lessons.id)
- `prereq_type` TEXT NOT NULL
- `prereq_id` TEXT NOT NULL
- `prereq_name` TEXT NOT NULL
- `required_level` REAL DEFAULT 0.5
- *indexes:* `idx_lesson_prereqs_lesson`

#### `lesson_replays`
**13 columns**
- `id` TEXT PRIMARY KEY
- `lesson_id` TEXT NOT NULL (FK: lessons.id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT
- `started_timestamp` REAL NOT NULL
- `completed_timestamp` REAL
- `success` BOOLEAN
- `steps_completed` INTEGER DEFAULT 0
- `total_steps` INTEGER NOT NULL
- `error_message` TEXT
- `epistemic_before` TEXT
- `epistemic_after` TEXT
- `replay_data` TEXT
- *indexes:* `idx_lesson_replays_lesson`, `idx_lesson_replays_session`

#### `lesson_steps`
**14 columns**
- `id` TEXT PRIMARY KEY
- `lesson_id` TEXT NOT NULL (FK: lessons.id)
- `step_order` INTEGER NOT NULL
- `phase` TEXT NOT NULL
- `action` TEXT NOT NULL
- `target` TEXT
- `code` TEXT
- `critical` BOOLEAN DEFAULT 0
- `expected_outcome` TEXT
- `error_recovery` TEXT
- `timeout_ms` INTEGER
- `query_pattern` TEXT
- `cache_tier` TEXT
- `requires_auth` TEXT
- *indexes:* `idx_lesson_steps_lesson`

#### `lessons`
**36 columns**
- `id` TEXT PRIMARY KEY
- `name` TEXT NOT NULL
- `version` TEXT NOT NULL
- `description` TEXT
- `domain` TEXT
- `tags` TEXT
- `source_confidence` REAL NOT NULL
- `teaching_quality` REAL NOT NULL
- `reproducibility` REAL NOT NULL
- `step_count` INTEGER DEFAULT 0
- `prereq_count` INTEGER DEFAULT 0
- `replay_count` INTEGER DEFAULT 0
- `success_rate` REAL DEFAULT 0.0
- `suggested_tier` TEXT DEFAULT 'free'
- `suggested_price` REAL DEFAULT 0.0
- `created_by` TEXT
- `created_timestamp` REAL NOT NULL
- `updated_timestamp` REAL NOT NULL
- `lesson_data` TEXT NOT NULL
- `abstraction_level` TEXT DEFAULT 'personal'
- `sharing_policy` TEXT DEFAULT 'private'
- `abstract_pattern` TEXT
- `parent_lesson_id` TEXT
- `entity_ids` TEXT
- `project_id` TEXT
- `org_id` TEXT
- `user_id` TEXT
- `trigger_type` TEXT
- `trigger_config` TEXT
- `output_format` TEXT DEFAULT 'markdown'
- `output_renderer` TEXT DEFAULT 'template'
- `output_config` TEXT
- `execution_count` INTEGER DEFAULT 0
- `feedback_score` REAL DEFAULT 0.0
- `last_executed` REAL
- `last_feedback` REAL
- *indexes:* `idx_lessons_abstraction`, `idx_lessons_created`, `idx_lessons_domain`, `idx_lessons_org`, `idx_lessons_pattern`, `idx_lessons_project`, `idx_lessons_sharing`, `idx_lessons_tier`

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

#### `mistakes_made`
**25 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `project_id` TEXT (FK: projects.id)
- `mistake` TEXT NOT NULL
- `why_wrong` TEXT NOT NULL
- `cost_estimate` TEXT
- `root_cause_vector` TEXT
- `prevention` TEXT
- `created_timestamp` REAL NOT NULL
- `mistake_data` TEXT NOT NULL
- `transaction_id` TEXT
- `entity_type` TEXT DEFAULT 'project'
- `entity_id` TEXT
- `visibility` TEXT DEFAULT 'shared'
- `epistemic_source` TEXT
- `impact` REAL DEFAULT 0.5
- `is_invalidated` BOOLEAN DEFAULT 0
- `invalidated_at` REAL DEFAULT NULL
- `invalidated_by` TEXT DEFAULT NULL
- `invalidation_reason` TEXT DEFAULT NULL
- `last_revisited_at` REAL DEFAULT NULL
- `last_retrieved_at` REAL DEFAULT NULL
- `retrieval_count` INTEGER DEFAULT 0
- `retrieval_count_at_resolution` INTEGER DEFAULT NULL
- *indexes:* `idx_mistakes_made_epistemic_source`, `idx_mistakes_made_invalidated`, `idx_mistakes_made_visibility`, `idx_mistakes_transaction`

#### `notes`
**9 columns**
- `note_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `transaction_id` TEXT
- `project_id` TEXT
- `ai_id` TEXT
- `text` TEXT NOT NULL
- `tag` TEXT
- `created_at` REAL NOT NULL
- `triaged` INTEGER DEFAULT 0

#### `prevention_events`
**18 columns**
- `id` INTEGER PRIMARY KEY
- `session_id` TEXT
- `transaction_id` TEXT
- `created_timestamp` REAL NOT NULL
- `pattern_key` TEXT
- `subject_key` TEXT
- `goal_id` TEXT
- `subtask_id` TEXT
- `author_practice` TEXT
- `beneficiary_practice` TEXT
- `exposed_at` REAL
- `acknowledged` INTEGER NOT NULL DEFAULT 0
- `shadow` INTEGER NOT NULL DEFAULT 0
- `outcome` TEXT NOT NULL DEFAULT 'exposed'
- `outcome_at` REAL
- `window_s` INTEGER
- `provenance_ref` TEXT
- `outcome_family` TEXT DEFAULT 'prevention'
- *indexes:* `idx_prevention_events_family`, `idx_prevention_events_pattern`, `idx_prevention_events_session`, `idx_prevention_events_subject`

#### `project_dead_ends`
**25 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `subtask_id` TEXT (FK: subtasks.id)
- `approach` TEXT NOT NULL
- `why_failed` TEXT NOT NULL
- `created_timestamp` REAL NOT NULL
- `dead_end_data` TEXT NOT NULL
- `subject` TEXT
- `impact` REAL DEFAULT 0.5
- `transaction_id` TEXT
- `entity_type` TEXT DEFAULT 'project'
- `entity_id` TEXT
- `visibility` TEXT DEFAULT 'shared'
- `epistemic_source` TEXT
- `is_invalidated` BOOLEAN DEFAULT 0
- `invalidated_at` REAL DEFAULT NULL
- `invalidated_by` TEXT DEFAULT NULL
- `invalidation_reason` TEXT DEFAULT NULL
- `last_revisited_at` REAL DEFAULT NULL
- `domain` TEXT DEFAULT NULL
- `last_retrieved_at` REAL DEFAULT NULL
- `retrieval_count` INTEGER DEFAULT 0
- `retrieval_count_at_resolution` INTEGER DEFAULT NULL
- *indexes:* `idx_dead_ends_transaction`, `idx_project_dead_ends_epistemic_source`, `idx_project_dead_ends_invalidated`, `idx_project_dead_ends_project`, `idx_project_dead_ends_visibility`

#### `project_findings`
**24 columns**
- `id` TEXT PRIMARY KEY
- `project_id` TEXT NOT NULL (FK: projects.id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `goal_id` TEXT (FK: goals.id)
- `subtask_id` TEXT (FK: subtasks.id)
- `finding` TEXT NOT NULL
- `created_timestamp` REAL NOT NULL
- `finding_data` TEXT NOT NULL
- `subject` TEXT
- `impact` REAL DEFAULT 0.5
- `transaction_id` TEXT
- `is_resolved` BOOLEAN DEFAULT FALSE
- `resolution` TEXT
- `resolved_timestamp` REAL
- `superseded_by` TEXT
- `resolution_kind` TEXT
- `entity_type` TEXT DEFAULT 'project'
- `entity_id` TEXT
- `source_refs` TEXT
- `visibility` TEXT DEFAULT 'shared'
- `epistemic_source` TEXT
- `last_retrieved_at` REAL DEFAULT NULL
- `retrieval_count` INTEGER DEFAULT 0
- `retrieval_count_at_resolution` INTEGER DEFAULT NULL
- *indexes:* `idx_findings_transaction`, `idx_project_findings_epistemic_source`, `idx_project_findings_project`, `idx_project_findings_resolution_kind`, `idx_project_findings_resolved`, `idx_project_findings_session`, `idx_project_findings_visibility`

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

#### `project_relationships`
**8 columns**
- `id` TEXT PRIMARY KEY
- `source_project_id` TEXT NOT NULL (FK: projects.id)
- `target_project_id` TEXT NOT NULL (FK: projects.id)
- `relationship_type` TEXT NOT NULL
- `weight` REAL DEFAULT 1.0
- `notes` TEXT
- `created_at` REAL NOT NULL
- `created_by_ai_id` TEXT
- *indexes:* `idx_proj_rel_source`, `idx_proj_rel_target`, `idx_proj_rel_type`

#### `project_unknowns`
**22 columns**
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
- `subject` TEXT
- `impact` REAL DEFAULT 0.5
- `transaction_id` TEXT
- `entity_type` TEXT DEFAULT 'project'
- `entity_id` TEXT
- `resolution_finding_id` TEXT
- `visibility` TEXT DEFAULT 'shared'
- `epistemic_source` TEXT
- `last_retrieved_at` REAL DEFAULT NULL
- `retrieval_count` INTEGER DEFAULT 0
- `retrieval_count_at_resolution` INTEGER DEFAULT NULL
- *indexes:* `idx_project_unknowns_epistemic_source`, `idx_project_unknowns_project`, `idx_project_unknowns_resolved`, `idx_project_unknowns_visibility`, `idx_unknowns_transaction`

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
- `project_type` TEXT DEFAULT 'product'
- `project_tags` TEXT
- `parent_project_id` TEXT
- *indexes:* `idx_projects_parent`, `idx_projects_type`

#### `reflexes`
**24 columns**
- `id` INTEGER PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `cascade_id` TEXT
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
- `project_id` TEXT
- `transaction_id` TEXT
- *indexes:* `idx_reflexes_project`, `idx_reflexes_transaction`

#### `rollup_logs`
**12 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `budget_id` TEXT (FK: attention_budgets.id)
- `agent_name` TEXT NOT NULL
- `finding_hash` TEXT NOT NULL
- `finding_text` TEXT
- `score` REAL NOT NULL
- `accepted` BOOLEAN NOT NULL
- `reason` TEXT
- `novelty` REAL
- `domain_relevance` REAL
- `timestamp` REAL NOT NULL
- *indexes:* `idx_rollup_logs_budget`, `idx_rollup_logs_hash`, `idx_rollup_logs_session`

#### `schema_migrations`
**3 columns**
- `migration_id` TEXT PRIMARY KEY
- `applied_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `description` TEXT

#### `sessions`
**17 columns**
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
- `bootstrap_level` INTEGER DEFAULT 1
- `parent_session_id` TEXT
- `project_id` TEXT
- `subject` TEXT
- `instance_id` TEXT
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- *indexes:* `idx_sessions_instance`, `idx_sessions_parent`

#### `subagent_sessions`
**10 columns**
- `session_id` TEXT PRIMARY KEY
- `agent_name` TEXT NOT NULL
- `parent_session_id` TEXT NOT NULL
- `project_id` TEXT
- `instance_id` TEXT
- `start_time` TIMESTAMP NOT NULL
- `end_time` TIMESTAMP
- `status` TEXT NOT NULL DEFAULT 'active'
- `rollup_summary` TEXT
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `subtask_dependencies`
**2 columns**
- `subtask_id` TEXT PRIMARY KEY (FK: subtasks.id)
- `depends_on_subtask_id` TEXT PRIMARY KEY (FK: subtasks.id)

#### `subtasks`
**12 columns**
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

#### `success_criteria`
**7 columns**
- `id` TEXT PRIMARY KEY
- `goal_id` TEXT NOT NULL (FK: goals.id)
- `description` TEXT NOT NULL
- `validation_method` TEXT NOT NULL
- `threshold` REAL
- `is_required` BOOLEAN DEFAULT 1
- `is_met` BOOLEAN DEFAULT 0

#### `suggestions`
**14 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `project_id` TEXT (FK: projects.id)
- `suggestion` TEXT NOT NULL
- `domain` TEXT
- `confidence` REAL NOT NULL
- `rationale` TEXT
- `status` TEXT DEFAULT 'pending'
- `reviewed_by` TEXT
- `review_notes` TEXT
- `review_outcome` TEXT
- `created_timestamp` REAL NOT NULL
- `reviewed_timestamp` REAL
- `suggestion_data` TEXT

#### `task_decompositions`
**4 columns**
- `goal_id` TEXT PRIMARY KEY (FK: goals.id)
- `total_estimated_tokens` INTEGER
- `created_timestamp` REAL NOT NULL
- `decomposition_data` TEXT NOT NULL

#### `token_savings`
**6 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `saving_type` TEXT NOT NULL
- `tokens_saved` INTEGER NOT NULL
- `evidence` TEXT
- `logged_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

#### `trajectory_patterns`
**13 columns**
- `pattern_id` TEXT PRIMARY KEY
- `pattern_name` TEXT NOT NULL
- `description` TEXT
- `signature_json` TEXT
- `typical_duration` TEXT
- `occurrence_count` INTEGER DEFAULT 0
- `success_rate` REAL DEFAULT 0.0
- `avg_duration_seconds` REAL
- `learned_from_count` INTEGER DEFAULT 0
- `last_matched_at` TIMESTAMP
- `confidence_threshold` REAL DEFAULT 0.7
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `updated_at` TIMESTAMP

#### `trajectory_snapshots`
**24 columns**
- `snapshot_id` INTEGER PRIMARY KEY
- `trajectory_id` TEXT NOT NULL (FK: vector_trajectories.trajectory_id)
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `phase` TEXT NOT NULL
- `round_num` INTEGER DEFAULT 1
- `timestamp` REAL NOT NULL
- `engagement` REAL
- `know` REAL
- `do_vector` REAL
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
- `vectors_json` TEXT
- `concept_tags` TEXT
- `reasoning` TEXT
- `meta_json` TEXT
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- *indexes:* `idx_snapshots_phase`, `idx_snapshots_trajectory`

#### `transaction_claims`
**13 columns**
- `id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL
- `transaction_id` TEXT
- `claim_index` INTEGER NOT NULL
- `claim` TEXT NOT NULL
- `grounding` TEXT
- `ref` TEXT
- `verdict` TEXT
- `verdict_evidence` TEXT
- `declared_timestamp` REAL NOT NULL
- `adjudicated_timestamp` REAL
- `scope` TEXT DEFAULT NULL
- `measured_count` INTEGER DEFAULT NULL
- *indexes:* `idx_transaction_claims_session`, `idx_transaction_claims_tx`, `idx_transaction_claims_verdict`

#### `vector_trajectories`
**17 columns**
- `trajectory_id` TEXT PRIMARY KEY
- `session_id` TEXT NOT NULL (FK: sessions.session_id)
- `ai_id` TEXT
- `project_id` TEXT
- `snapshot_count` INTEGER DEFAULT 0
- `first_timestamp` REAL
- `last_timestamp` REAL
- `duration_seconds` REAL
- `pattern` TEXT
- `pattern_confidence` REAL DEFAULT 0.0
- `phase_detected` TEXT
- `start_vectors` TEXT
- `end_vectors` TEXT
- `vector_deltas` TEXT
- `analyzed_at` TIMESTAMP
- `analysis_version` TEXT DEFAULT '1.0'
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- *indexes:* `idx_trajectories_pattern`, `idx_trajectories_session`

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
- *indexes:* `idx_verification_evidence_session`

#### `weave_enforce_events`
**11 columns**
- `id` INTEGER PRIMARY KEY
- `session_id` TEXT
- `transaction_id` TEXT
- `created_timestamp` REAL NOT NULL
- `connectivity_ratio` REAL
- `connectivity_floor` REAL
- `strictness` REAL
- `response_band` TEXT
- `enforced` INTEGER NOT NULL DEFAULT 0
- `decision_in` TEXT
- `decision_out` TEXT
- *indexes:* `idx_weave_events_session`, `idx_weave_events_txn`

<!-- END GENERATED -->

## Legacy tables you may find in a long-lived database

Not in the registry, not created by current code, not read by current code.
`python3 scripts/gen_schema_doc.py --diff-db <sessions.db>` names them for a
given store. Measured 2026-09-18 on a practice database that dates from the
first release:

| table | why it is there | rows here | safe to drop? |
|---|---|---|---|
| `act_logs`, `investigation_logs`, `investigation_tools` | early cascade logging, schema removed | 0 | yes when empty |
| `client_findings`, `client_interactions`, `client_unknowns`, `clients` | CRM prototype, moved to empirica-workspace (`o-<slug>` organizations) | 0 / 0 / 0 / 1 | needs a ruling — `clients` holds a row |
| `divergence_tracking`, `drift_monitoring` | removed in v1.2.0 (drift now uses the signaling system) | 0 | yes when empty |
| `project_reference_docs` | dropped by migration 047 (data moved to `epistemic_sources`); recreated empty by an older binary's `ALL_SCHEMAS` opening the same store | 0 | yes when empty |
| `engagements` | vendored by `data/repositories/workspace_db.py`; workspace's lane, not core's | 0 | not core's call |

No migration drops these. A drop is a destructive operation on someone's
history and takes an explicit ruling per table; the empty ones can go in one
migration once that ruling exists. Until then they cost nothing but a line
in `--diff-db`.

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