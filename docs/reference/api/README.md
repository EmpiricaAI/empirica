# Empirica Python API Reference

**Framework Version:** 1.6.6
**Status:** Production Ready

---

## API Categories

### [Core Session Management](core_session_management.md)
- **SessionDatabase** - Central database for all session data
- **Session creation, retrieval, and management**
- **Epistemic vector storage and retrieval**

### [Goals & Tasks](goals_tasks.md)
- **GoalRepository** - Goal creation, tracking, and completion
- **Task management** - Task breakdown and progress tracking
- **Goal-tree operations** - Hierarchical goal management

### [Project Management](project_management.md)
- **ProjectRepository** - Project lifecycle management
- **Handoff reports** - AI-to-AI handoff documentation
- **Project tracking** - Cross-session project management

### [Knowledge Management](knowledge_management.md)
- **BreadcrumbRepository** - Findings, unknowns, dead-ends
- **Epistemic sources** - Source attribution and confidence
- **Reference documents** - Project documentation links

### [Qdrant Vector Storage](qdrant.md)
- **EmbeddingsProvider** - Multi-provider embeddings (Ollama, OpenAI, Jina, Voyage)
- **Vector store operations** - Memory upsert, search, delete
- **Pattern retrieval** - CASCADE hook integration (PREFLIGHT/CHECK)

### [Lessons System](lessons.md)
- **LessonStorageManager** - 4-layer storage for procedural knowledge
- **LessonHotCache** - In-memory graph for nanosecond queries
- **Knowledge graph** - Prerequisites, enables, relations

### [Signaling](signaling.md)
- **DriftLevel** - Traffic light calibration for epistemic drift
- **SentinelAction** - Gate actions (REVISE, BRANCH, HALT, LOCK)
- **CognitivePhase** - Noetic/Threshold/Praxic phase detection
- **VectorHealth** - Health state for individual vectors

### [Identity & Persona](identity_persona.md)
- **AIIdentity** - Cryptographic identity for AI agents
- **PersonaMetadata** - Persona configuration
- **EpistemicConfig** - Priors, thresholds, weights

### [Architecture Assessment](architecture_assessment.md)
- **CouplingAnalyzer** - Dependency and API surface analysis
- **StabilityEstimator** - Git history stability metrics
- **ArchitectureVectors** - Epistemic vectors for code quality

### [System Utilities](system_utilities.md)
- **BranchMapping** - Git branch to goal mapping
- **DocCodeIntegrity** - Documentation-code integrity checking
- **Migration tools** - Schema evolution utilities

### [CASCADE Workflow](cascade_workflow.md)
- **PREFLIGHT/CHECK/POSTFLIGHT** - Epistemic measurement phases
- **Transaction lifecycle** - File-based transaction tracking
- **Grounded calibration** - POST-TEST verification comparing beliefs with service observations

### [Workspace Management](workspace_management.md)
- **workspace-init/list/overview/map** - Cross-project portfolio management
- **Global registry** - Project tracking in workspace.db
- **Instance bindings** - AI instance → project mappings

### [Agents Orchestration](agents_orchestration.md)
- **agent-spawn/parallel** - Parallel investigation agents
- **Budget allocation** - Shannon information gain distribution
- **agent-aggregate/rollup** - Finding consolidation

### [Messaging System](messaging_system.md)
- **message-send/inbox/read** - Asynchronous AI-to-AI communication
- **Channels** - Direct, broadcast, crosscheck messaging
- **Threads** - Conversation threading and handoffs

### [Configuration & Profiles](config_profiles.md)
- **InvestigationProfile** - Complete investigation configuration
- **ActionThresholds** - Sentinel gate thresholds
- **GoalScopeLoader** - Epistemic vector to scope mapping

### [Data Infrastructure](data_infrastructure.md)
- **DatabaseAdapter** - Abstract DB backend (SQLite/PostgreSQL)
- **ConnectionPool** - Connection pooling with retry
- **CircuitBreaker** - Cascading failure prevention

### [Context Budget](context_budget.md)
- **ContextBudgetManager** - Token-level context window management
- **BudgetReport** - Budget state snapshots
- **AttentionStatus** - Attention budget tracking

### [Metrics](metrics.md)
- **FlowStateMetrics** - Session productivity scoring
- **TokenEfficiencyMetrics** - Token reduction validation

---

## API Philosophy

**AI-First Design:** All APIs designed for autonomous AI agent usage with structured return values and comprehensive error handling.

**Epistemic Self-Awareness:** APIs capture and track epistemic state throughout all operations.

**Modular Architecture:** APIs organized in logical modules that can be used independently while maintaining consistency.

**Storage Architecture:** Data flows through SQLite (hot), Git Notes (warm), JSON Logs (audit), Qdrant (search), and MEMORY.md (Claude Code bridge).

---

## Getting Started

For new users, start with:
1. [Core Session Management](core_session_management.md) - Essential session operations
2. [CASCADE Workflow](cascade_workflow.md) - Core reasoning workflow
3. [Goals & Tasks](goals_tasks.md) - Task management operations

---

**Total Modules:** 18 categories
**API Stability:** Production ready
