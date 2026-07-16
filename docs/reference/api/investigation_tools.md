# Investigation Tools API Reference

**Version:** 1.11.0
**Module:** `empirica.cli.command_handlers.workflow_commands`
**Purpose:** NOETIC phase tools for exploration, hypothesizing, and evidence gathering

---

## Overview

Investigation tools support the **NOETIC phase** - exploring, hypothesizing, and gathering evidence before action. They provide:

- Target-based investigation (file, directory, concept, comprehensive)
- Multi-persona parallel investigation
- Git branch isolation for exploratory work
- Structured logging of investigation results
- Noetic artifact capture (findings, unknowns, dead-ends)

> **Verified at 1.11.0.** The `investigate*` and artifact-log verbs documented below remain present (`investigate-log` has since been removed). Sister surfaces shipped since this doc was first written: `noetic-batch` (`empirica noetic-batch -`) bundles ≥3 reads/greps/globs/investigates into one merged result (see the system prompt's noetic-batch section); the artifact graph + typed edges + `commit-context` walker are covered in the user-facing discovery-side walkthrough at [LOGGING_AND_FINDING.md](../../human/end-users/LOGGING_AND_FINDING.md). For semantic search across artifacts use `project-search --task "..."` (local) or `project-search --task "..." --global` (cross-project).

---

## Commands

### `investigate`

Launch investigation with automatic type detection.

```bash
# Auto-detect target type
empirica investigate src/auth/jwt.py

# Specify investigation type
empirica investigate --type comprehensive src/auth/

# With session context (loads project bootstrap)
empirica investigate --session-id <ID> --type concept "authentication patterns"

# Detailed/verbose output
empirica investigate src/auth/ --detailed
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `target` | Yes | - | Target to investigate (file, directory, or concept) |
| `--session-id` | No | auto | Session ID (loads context via project-bootstrap) |
| `--type` | No | `auto` | Investigation type: `auto`, `file`, `directory`, `concept`, `comprehensive` |
| `--context` | No | - | JSON context data |
| `--detailed` | No | `false` | Show detailed investigation output |
| `--verbose` | No | `false` | Alias for --detailed |

**Investigation Types:**

| Type | Description | Use Case |
|------|-------------|----------|
| `auto` | Auto-detect from target | Default behavior |
| `file` | Single file deep analysis | Understanding a specific file |
| `directory` | Directory structure analysis | Understanding a module |
| `concept` | Semantic concept search | "How does auth work?" |
| `comprehensive` | Full deep analysis (replaces analyze) | Complete understanding |

**Output (JSON):**
```json
{
  "ok": true,
  "target": "src/auth/jwt.py",
  "type": "file",
  "investigation": {
    "summary": "JWT token handling module",
    "key_findings": [
      "Uses RS256 signing algorithm",
      "Token expiry set to 24 hours",
      "Refresh tokens stored separately"
    ],
    "dependencies": ["cryptography", "pyjwt"],
    "exports": ["create_token", "verify_token", "refresh_token"],
    "complexity": "medium"
  },
  "epistemic_impact": {
    "know_delta": 0.15,
    "uncertainty_delta": -0.10
  }
}
```

---

### `investigate-multi`

Spawn parallel investigations with multiple personas.

```bash
empirica investigate-multi \
  --task "Analyze authentication system security" \
  --personas "security,ux,performance" \
  --session-id <ID> \
  --aggregate-strategy epistemic-score
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--task` | Yes | - | Task for all personas to investigate |
| `--personas` | Yes | - | Comma-separated persona IDs (e.g., `security,ux,performance`) |
| `--session-id` | Yes | - | Parent session ID |
| `--context` | No | - | Additional context from parent investigation |
| `--aggregate-strategy` | No | `epistemic-score` | How to merge results |
| `--output` | No | `human` | Output format: `human` or `json` |

**Aggregation Strategies:**

| Strategy | Description |
|----------|-------------|
| `epistemic-score` | Weight by epistemic confidence (default) |
| `consensus` | Only include findings agreed by multiple personas |
| `all` | Include all findings without filtering |

**Available Personas:**

| Persona | Focus |
|---------|-------|
| `security` | Security vulnerabilities, auth, encryption |
| `ux` | User experience, accessibility, usability |
| `performance` | Latency, throughput, resource usage |
| `architecture` | Design patterns, coupling, cohesion |
| `reliability` | Error handling, resilience, recovery |

**Output (JSON):**
```json
{
  "ok": true,
  "task": "Analyze authentication system security",
  "personas_used": ["security", "ux", "performance"],
  "aggregate_strategy": "epistemic-score",
  "results": {
    "security": {
      "confidence": 0.85,
      "findings": ["JWT uses RS256 (good)", "No rate limiting on login"],
      "unknowns": ["Token revocation strategy?"],
      "vectors": {"know": 0.82, "uncertainty": 0.18}
    },
    "ux": {
      "confidence": 0.78,
      "findings": ["Login flow is 3 steps", "No password strength indicator"],
      "vectors": {"know": 0.75, "uncertainty": 0.25}
    },
    "performance": {
      "confidence": 0.72,
      "findings": ["JWT validation is 2ms", "Token refresh adds latency"],
      "vectors": {"know": 0.70, "uncertainty": 0.30}
    }
  },
  "aggregated": {
    "top_findings": [
      {"finding": "JWT uses RS256 (good)", "confidence": 0.85},
      {"finding": "No rate limiting on login", "confidence": 0.85}
    ],
    "critical_unknowns": ["Token revocation strategy?"],
    "overall_confidence": 0.78
  }
}
```

---

### `investigate-create-branch`

Create isolated git branch for exploratory work.

```bash
empirica investigate-create-branch \
  --session-id <ID> \
  --name "explore-auth-refactor"
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | Yes | - | Session ID |
| `--name` | Yes | - | Branch name (will be prefixed with `investigate/`) |

**Output (JSON):**
```json
{
  "ok": true,
  "branch_name": "investigate/explore-auth-refactor",
  "parent_branch": "develop",
  "session_id": "abc123..."
}
```

---

### `investigate-checkpoint-branch`

Save investigation state as checkpoint.

```bash
empirica investigate-checkpoint-branch \
  --session-id <ID> \
  --message "Found auth flow, need to explore token refresh"
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | Yes | - | Session ID |
| `--message` | Yes | - | Checkpoint message |

**Output (JSON):**
```json
{
  "ok": true,
  "checkpoint_id": "uuid",
  "commit_hash": "abc123",
  "message": "Found auth flow, need to explore token refresh",
  "vectors_at_checkpoint": {
    "know": 0.65,
    "uncertainty": 0.35
  }
}
```

---

### `investigate-merge-branches`

Merge investigation findings back to main branch.

```bash
empirica investigate-merge-branches \
  --session-id <ID> \
  --branch "investigate/explore-auth-refactor"
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | Yes | - | Session ID |
| `--branch` | Yes | - | Branch to merge |

**Output (JSON):**
```json
{
  "ok": true,
  "merged_branch": "investigate/explore-auth-refactor",
  "target_branch": "develop",
  "merge_commit": "def456",
  "findings_preserved": 5
}
```

---

## Noetic Artifact Logging

Detailed logging of individual investigation artifacts.

### `finding-log`

Log a single finding discovered during investigation.

```bash
empirica finding-log \
  --session-id <ID> \
  --finding "JWT tokens use RS256 signing with 24h expiry" \
  --impact 0.8 \
  --tags "security,authentication"
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID |
| `--finding` | Yes | - | Finding description |
| `--impact` | No | 0.5 | Impact score (0.0-1.0) |
| `--tags` | No | - | Comma-separated tags |
| `--subject` | No | - | Subject area |
| `--goal-id` | No | - | Associated goal |

---

### `unknown-log`

Log an unknown or unresolved question.

```bash
empirica unknown-log \
  --session-id <ID> \
  --unknown "What is the token revocation strategy?" \
  --tags "security,architecture"
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID |
| `--unknown` | Yes | - | Unknown/question description |
| `--tags` | No | - | Comma-separated tags |
| `--subject` | No | - | Subject area |
| `--goal-id` | No | - | Associated goal |

---

### `deadend-log`

Log a failed approach to prevent re-exploration.

```bash
empirica deadend-log \
  --session-id <ID> \
  --approach "Using file-based session storage" \
  --why-failed "No cluster support, doesn't scale"
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--session-id` | No | auto | Session ID |
| `--approach` | Yes | - | Failed approach description |
| `--why-failed` | Yes | - | Explanation of failure |
| `--tags` | No | - | Comma-separated tags |
| `--subject` | No | - | Subject area |
| `--goal-id` | No | - | Associated goal |

---

## Pattern Check

Check approach against known dead-ends before proceeding.

```bash
empirica pattern-check \
  --session-id <ID> \
  --approach "Use JWT tokens without refresh mechanism"
```

**Output (JSON):**
```json
{
  "ok": true,
  "approach": "Use JWT tokens without refresh mechanism",
  "warnings": [
    {
      "type": "dead_end",
      "match": "Using JWT without refresh causes frequent re-auth",
      "similarity": 0.89,
      "session_id": "prev-session-id",
      "timestamp": "2026-01-15T10:00:00Z"
    }
  ],
  "recommendation": "Consider refresh token mechanism"
}
```

---

## Python API

```python
from empirica.cli.command_handlers.workflow_commands import (
    handle_investigate_command,
    handle_finding_log_command,
    handle_unknown_log_command,
    handle_deadend_log_command
)

from empirica.data.repositories.breadcrumbs import BreadcrumbRepository

# Log findings programmatically
breadcrumbs = BreadcrumbRepository()

finding_id = breadcrumbs.log_finding(
    project_id="proj-123",
    session_id="sess-456",
    finding="JWT uses RS256 signing",
    tags=["security", "authentication"],
    goal_id="goal-789"
)

unknown_id = breadcrumbs.log_unknown(
    project_id="proj-123",
    session_id="sess-456",
    unknown="Token revocation strategy?",
    tags=["security"]
)

deadend_id = breadcrumbs.log_dead_end(
    project_id="proj-123",
    session_id="sess-456",
    approach="File-based sessions",
    why_failed="No cluster support"
)
```

---

## Investigation Workflow

```
1. Start Investigation
   └── empirica investigate <target>

2. Create Branch (optional)
   └── empirica investigate-create-branch --name explore-X

3. Log Artifacts
   ├── empirica finding-log --finding "..."
   ├── empirica unknown-log --unknown "..."
   └── empirica deadend-log --approach "..." --why-failed "..."

4. Check Patterns
   └── empirica pattern-check --approach "..."

5. Checkpoint Progress (optional)
   └── empirica investigate-checkpoint-branch --message "..."

6. Merge Findings (optional)
   └── empirica investigate-merge-branches --branch X

7. Submit to transaction
   └── empirica check-submit -  # Gate to praxic phase
```

---

## Integration with Epistemic Transactions

Investigation tools feed into the epistemic transaction workflow:

- **PREFLIGHT** retrieves relevant dead-ends and lessons
- **investigate** commands produce noetic artifacts
- **pattern-check** validates approaches against history
- **CHECK** gates the transition to praxic phase
- **POSTFLIGHT** captures investigation deltas

---

## Implementation Files

- `empirica/cli/command_handlers/workflow_commands.py` - Core handlers
- `empirica/cli/command_handlers/investigation_commands.py` - Branch operations
- `empirica/data/repositories/breadcrumbs.py` - Artifact storage

---

## Related Documentation

- [NOETIC_PRAXIC_FRAMEWORK.md](../../architecture/NOETIC_PRAXIC_FRAMEWORK.md) - Thinking phases
- [HANDOFF_SYSTEM.md](../../architecture/HANDOFF_SYSTEM.md) - Investigation handoffs
- [Knowledge Management](knowledge_management.md) - Breadcrumb repository

---

**API Stability:** Stable
**Last Updated:** 2026-02-08
