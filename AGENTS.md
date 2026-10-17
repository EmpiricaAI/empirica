
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods, do use Empirica Goals and subtasks.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Auto-syncs to JSONL for version control
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**
```bash
bd ready --json
```

**Create new issues:**
```bash
bd create "Issue title" -t bug|feature|task -p 0-4 --json
bd create "Subtask" --parent <epic-id> --json  # Hierarchical subtask
```

**Complete work:**
```bash
bd update bd-42 --status in_progress --json
bd close bd-42 --reason "Completed" --json
```

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task**: `bd update <id> --status in_progress`
3. **Work on it**: Implement, test, document
4. **Complete**: `bd close <id> --reason "Done"`
5. **Commit together**: Always commit `.beads/issues.jsonl` with code changes

For full details, see output of `bd onboard`.

### Integration with Empirica

**Finding ready work (epistemic + dependency filtering):**
```bash
empirica goals-ready --session-id <SESSION>
```

This combines:
- BEADS dependency tracking (what's unblocked)
- Empirica epistemic state (what you're ready for)
- Returns tasks matching your capability

**Workflow:**
1. Check ready work: `empirica goals-ready`
2. Claim task: `bd update <id> --status in_progress`
3. Create branch: `git checkout -b epistemic/reasoning/issue-<id>`
4. Work and commit: Track progress epistemically
5. Complete: `bd close <id>` + merge branch

For the options, see `empirica goals-ready --help`.

**Branch creation:** `empirica goals-claim --goal-id <ID>` can create the working branch for you (`--create-branch`; `--no-branch` skips it).
