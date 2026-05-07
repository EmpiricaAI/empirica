# BEADS Integration Design for Empirica

**Status:** Design Phase  
**Date:** 2025-12-15  
**Author:** Empirica AI (Copilot)  
**Session:** 0ca9d791-3ada-44a6-ab65-be36146005d4

## Executive Summary

Integration strategy for BEADS (git-native issue tracker) with Empirica's epistemic tracking system. **Key principle: Integration not replacement** - BEADS handles task dependencies, Empirica handles epistemic state.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│ Empirica CLI (goals-create, goals-add-subtask) │
└────────────┬────────────────────────────────────┘
             │
             ├──────────────────────────────┐
             │                              │
    ┌────────▼──────────┐         ┌────────▼─────────┐
    │ BEADS (Optional)  │         │ Empirica Goals   │
    │ .beads/           │         │ .empirica/       │
    │                   │         │                  │
    │ • Task graph      │         │ • Epistemic data │
    │ • Dependencies    │         │ • Confidence     │
    │ • Hash IDs        │         │ • Learning       │
    │ • Ready work      │         │ • Findings       │
    └───────────────────┘         └──────────────────┘
             │                              │
             └──────────────┬───────────────┘
                            │
                   Foreign Key Link
                   (goals.beads_issue_id)
```

---

## Why Integration (Not Replacement)

| Feature | BEADS | Empirica Goals |
|---------|-------|----------------|
| **Task Structure** | ✅ Dependency graph, ready work | ❌ Flat goal→subtasks |
| **Multi-AI Coordination** | ✅ Hash IDs, collision-free | ⚠️ UUID but no dependency tracking |
| **Epistemic Tracking** | ❌ No confidence/learning | ✅ Scope vectors, deltas, findings |
| **Investigation** | ⚠️ discovered-from dep type | ✅ findings/unknowns/dead_ends |
| **CASCADE Integration** | ❌ Not aware of PREFLIGHT/CHECK | ✅ Integrated with workflow |
| **Git Sync** | ✅ JSONL + SQLite, 5s debounce | ✅ Git notes + SQLite |

**Verdict:** Both systems excel at different things. Integration preserves strengths of each.

---

## Integration Pattern: Subprocess Adapter (Recommended)

**How it works:**
- Empirica calls `bd` CLI via subprocess
- Parse JSON output with `--json` flags
- Store BEADS issue ID in Empirica goals table

**Pros:**
- ✅ No Go dependencies in Python codebase
- ✅ BEADS upgrades don't break Empirica
- ✅ Clear separation of concerns
- ✅ Easy to make optional

**Implementation:**
```python
# empirica/integrations/beads/adapter.py
import subprocess
import json
from typing import Optional, Dict, List

class BeadsAdapter:
    """Subprocess-based BEADS integration"""
    
    @staticmethod
    def is_available() -> bool:
        """Check if bd CLI is installed"""
        try:
            subprocess.run(['bd', '--version'], 
                         capture_output=True, check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
    
    @staticmethod
    def create_issue(title: str, description: str = "", 
                    priority: int = 2, issue_type: str = "task") -> Optional[str]:
        """Create BEADS issue, return hash ID (e.g., bd-a1b2)"""
        if not BeadsAdapter.is_available():
            return None
        
        cmd = ['bd', 'create', title, '-p', str(priority), 
               '-t', issue_type', '--json']
        if description:
            cmd.extend(['-d', description])
        
        result = subprocess.run(cmd, capture_output=True, 
                              text=True, check=True)
        issue = json.loads(result.stdout)
        return issue['id']
    
    @staticmethod
    def add_dependency(child_id: str, parent_id: str, 
                      dep_type: str = 'blocks'):
        """Add dependency between BEADS issues"""
        subprocess.run(['bd', 'dep', 'add', child_id, parent_id, 
                       '--type', dep_type], check=True)
    
    @staticmethod
    def get_ready_work(limit: int = 10) -> List[Dict]:
        """Get ready work from BEADS"""
        result = subprocess.run(['bd', 'ready', '--json', 
                               '--limit', str(limit)],
                              capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
```

---

## Database Schema Changes

```sql
-- Migration: Add beads_issue_id column
ALTER TABLE goals ADD COLUMN beads_issue_id TEXT;
CREATE INDEX IF NOT EXISTS idx_goals_beads_issue_id ON goals(beads_issue_id);
```

---

## CLI Command Modifications

### `goals-create` (Enhanced)

```bash
# With BEADS integration
empirica goals-create \
  --session-id <ID> \
  --objective "Implement OAuth2" \
  --scope-breadth 0.3 \
  --use-beads  # Optional flag
```

**Behavior:**
- If `--use-beads` + `bd` available → Create BEADS issue + Empirica goal (linked)
- If `--use-beads` + `bd` missing → Warning + Empirica goal only
- If no `--use-beads` → Empirica goal only (current behavior)

### New Command: `goals-ready`

Query BEADS for ready work, filtered by Empirica epistemic criteria:

```bash
empirica goals-ready \
  --session-id <ID> \
  --min-confidence 0.7 \
  --max-uncertainty 0.3
```

---

## Empirica-BEADS Workflow

```
1. PREFLIGHT → Assess epistemic state
2. Create Goal + BEADS Issue (optional)
   ├─ Empirica: goal_id
   └─ BEADS: bd-a1b2
3. Add Subtasks
   ├─ Subtask 1 → bd-a1b2.1 (discovered-from)
   ├─ Subtask 2 → bd-a1b2.2 (blocks)
   └─ Subtask 3 → bd-a1b2.3 (blocks)
4. Query Ready Work
   └─ BEADS: bd ready → bd-a1b2.1 (no blockers)
5. CHECK Phase → Proceed or investigate?
6. ACT → bd update bd-a1b2.1 --status in_progress
7. Log Findings → Empirica investigation tracking
8. Complete → bd close + goals-complete-subtask
9. POSTFLIGHT → Measure learning deltas
```

---

## Configuration

```yaml
# .empirica/config.yaml
integrations:
  beads:
    enabled: true  # or false
    auto_detect: true  # Check if bd CLI available
    use_agent_mail: false  # Optional for multi-AI
    agent_mail_url: "http://127.0.0.1:8765"
    agent_name: "copilot-alpha"
```

---

## Implementation Plan

### Phase 1: Core Integration
1. ✅ Design document
2. ⬜ Create `empirica/integrations/beads/adapter.py`
3. ⬜ Add `beads_issue_id` column migration
4. ⬜ Modify `goals-create` command
5. ⬜ Modify `goals-add-subtask` command
6. ⬜ Integration tests

### Phase 2: Enhanced Features
1. ⬜ `goals-ready` command (query + epistemic filter)
2. ⬜ `goals-sync` command (sync BEADS → Empirica)
3. ⬜ discovered-from dependency for findings
4. ⬜ Documentation

### Phase 3: Multi-AI (Optional)
1. ⬜ Agent Mail adapter
2. ⬜ Collision detection tests
3. ⬜ Performance benchmarks

---

## Success Metrics

1. **Optional:** Goals work with/without BEADS (100% backward compatible)
2. **Performance:** Subprocess overhead < 50ms
3. **Multi-AI:** No collisions with hash IDs
4. **Adoption:** Clear docs, examples work

---

## References

- [BEADS Repository](https://github.com/steveyegge/beads)
- [BEADS Quickstart](https://github.com/steveyegge/beads/blob/main/docs/QUICKSTART.md)
- [BEADS Extension Pattern](https://github.com/steveyegge/beads/blob/main/docs/EXTENDING.md)
- [Empirica Noetic-Praxic Framework](../../architecture/NOETIC_PRAXIC_FRAMEWORK.md)

---

## Next Steps

**Waiting for user approval to proceed with Phase 1 implementation.**
