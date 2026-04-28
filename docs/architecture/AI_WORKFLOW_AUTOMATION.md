# AI Workflow Automation - Ensuring Empirica Usage

## Problem
AIs forget to:
- Create sessions under the correct project
- Use goals and subtasks for task tracking
- Log findings and unknowns as breadcrumbs
- Complete CASCADE workflow (PREFLIGHT → POSTFLIGHT)

## Solutions (Ordered by Implementation Effort)

### 1. Claude Code Hooks (Easiest - Immediate)

**Use `.claude/hooks/` to intercept tool use:**

```bash
# .claude/hooks/tool-use.sh
#!/bin/bash
# Called after every tool use

TOOL_NAME="$1"
TOOL_RESULT="$2"

# Check if empirica session exists
if ! empirica sessions-list --output json | grep -q "end_time.*null"; then
    echo "⚠️  No active Empirica session detected" >&2
    echo "💡 Create one with: empirica session-create --ai-id claude-code" >&2
fi

# Remind to log findings after significant work
if [[ "$TOOL_NAME" == "Edit" ]] || [[ "$TOOL_NAME" == "Write" ]]; then
    echo "💡 Remember to log findings: empirica finding-log --finding '...'" >&2
fi
```

**Pros:** Works immediately, no code changes
**Cons:** Only works in Claude Code, not other environments

---

### 2. Statusline Integration (Medium - High Visibility)

**Show current session in Claude Code statusline:**

```python
# empirica/integrations/claude_code_statusline.py
def get_statusline():
    """Return current Empirica state for statusline"""
    db = SessionDatabase()
    active_session = db.get_active_session()

    if active_session:
        return f"📊 Session: {active_session['session_id'][:8]}... | Phase: {active_session['phase']}"
    else:
        return "⚠️  No active Empirica session"
```

**Add to Claude Code statusline config:**
```yaml
# ~/.claude/config.yaml
statusline:
  right:
    - command: "empirica statusline"
      refresh: 5s
```

**Pros:** Always visible, passive reminder
**Cons:** Requires Claude Code statusline support

---

### 3. Auto-Session Creation (Medium - Proactive)

**Detect when AI starts work and auto-create session:**

```python
# empirica/cli/auto_session.py
def ensure_session(ai_id: str = None) -> str:
    """
    Ensure active session exists, create if not.
    Returns session_id.
    """
    db = SessionDatabase()

    # Check for active session
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT session_id FROM sessions
        WHERE end_time IS NULL
        ORDER BY start_time DESC
        LIMIT 1
    """)
    row = cursor.fetchone()

    if row:
        return row['session_id']

    # No active session - create one
    if not ai_id:
        ai_id = os.getenv('AI_ID', 'claude-code')

    session_id = db.create_session(ai_id=ai_id)
    print(f"✨ Auto-created Empirica session: {session_id}", file=sys.stderr)
    return session_id
```

**Use in commands:**
```python
def handle_finding_log_command(args):
    # Auto-create session if none exists
    if not args.session_id:
        args.session_id = ensure_session()

    # Continue with logging...
```

**Pros:** Zero friction for AI
**Cons:** Might create unwanted sessions

---

### 4. Command Aliases (Easy - Convenience)

**Create shorthand commands that include boilerplate:**

```bash
# ~/.bashrc or ~/.zshrc
alias ef='empirica finding-log --project-id $EMPIRICA_PROJECT_ID --session-id $EMPIRICA_SESSION_ID --finding'
alias eu='empirica unknown-log --project-id $EMPIRICA_PROJECT_ID --session-id $EMPIRICA_SESSION_ID --unknown'
alias eg='empirica goals-create --session-id $EMPIRICA_SESSION_ID'

# Auto-export current session
export EMPIRICA_SESSION_ID=$(empirica sessions-list --output json | jq -r '.sessions[0].session_id')
export EMPIRICA_PROJECT_ID=$(empirica project-list --output json | jq -r '.projects[] | select(.name=="empirica") | .id')
```

**Pros:** Simple, works everywhere
**Cons:** Requires AI to remember to use aliases

---

### 5. Empirica Wrapper CLI (Medium - Transparent)

**Create `empirica-auto` wrapper that handles session management:**

```bash
#!/bin/bash
# empirica-auto wrapper

# Ensure session exists
SESSION_ID=$(empirica sessions-list --output json | jq -r '.sessions[] | select(.end_time==null) | .session_id' | head -1)

if [ -z "$SESSION_ID" ]; then
    echo "Creating new Empirica session..." >&2
    SESSION_ID=$(empirica session-create --ai-id claude-code --output json | jq -r '.session_id')
fi

# Export for child commands
export EMPIRICA_SESSION_ID="$SESSION_ID"

# Forward command to empirica
empirica "$@"
```

**Usage:**
```bash
empirica-auto finding-log --finding "..."  # Auto-uses active session
```

**Pros:** Transparent, backward compatible
**Cons:** Requires AI to use `empirica-auto` instead of `empirica`

---

### 6. MCP Server Auto-Init (Hard - Seamless)

**When AI calls first MCP tool, auto-setup:**

```python
# empirica-mcp/server.py
class EmpricaMCPServer:
    def __init__(self):
        self.active_session = None

    async def call_tool(self, name, arguments):
        # First call? Auto-create session
        if not self.active_session:
            self.active_session = await self._auto_init_session()

        # Inject session_id into all tool calls
        if 'session_id' not in arguments:
            arguments['session_id'] = self.active_session

        # Execute tool
        return await self._execute_tool(name, arguments)
```

**Pros:** Completely seamless for AI
**Cons:** Requires MCP server changes, session persists across projects

---

### 7. System Prompt Enhancement (Easy - Documentation)

**Update CLAUDE.md to be more explicit:**

```markdown
## MANDATORY Empirica Workflow

**BEFORE starting ANY task:**
```bash
# 1. Create session (ALWAYS)
empirica session-create --ai-id claude-code --output json

# 2. Run PREFLIGHT (ALWAYS)
empirica preflight-submit config.json
```

**DURING work:**
```bash
# Log discoveries (EVERY time you learn something)
empirica finding-log --finding "..."

# Log unknowns (EVERY time you're uncertain)
empirica unknown-log --unknown "..."
```

**AFTER work:**
```bash
# Run POSTFLIGHT (ALWAYS)
empirica postflight-submit config.json
```

**CRITICAL: If you skip these steps, you're not using Empirica properly.**
```

**Pros:** Clear, explicit requirements
**Cons:** Relies on AI reading and following instructions

---

### 8. Pre-Flight Checklist (Medium - Interactive)

**Before allowing work, require checklist:**

```python
# empirica/cli/preflight_checklist.py
def run_preflight_checklist():
    """Interactive checklist before AI starts work"""
    checks = [
        ("Active session exists?", check_active_session),
        ("Project context loaded?", check_project_bootstrap),
        ("PREFLIGHT assessment done?", check_preflight_exists),
    ]

    for question, check_fn in checks:
        result = check_fn()
        status = "✅" if result else "❌"
        print(f"{status} {question}")

        if not result:
            print(f"   Fix: {check_fn.fix_hint}")
            response = input("   Fix now? [Y/n]: ")
            if response.lower() != 'n':
                check_fn.auto_fix()
```

**Triggered by:**
- First empirica command in a new terminal
- Claude Code hook on project open
- MCP server initialization

**Pros:** Ensures compliance, educational
**Cons:** Adds friction, might be annoying

---

## Implementation Status (Updated 2026-01-08)

### ✅ Phase 1: COMPLETE - Claude Code Hooks

**Location:** `~/.claude/plugins/local/empirica/hooks/`

| Hook | Trigger | Function |
|------|---------|----------|
| `sentinel-gate.py` | PreToolCall (Edit/Write/Bash) | Blocks without valid CHECK |
| `session-init.py` | SessionStart (new) | Auto session + bootstrap + PREFLIGHT prompt |
| `post-compact.py` | SessionStart (compact) | Recovery with session + bootstrap |
| `session-end-postflight.py` | SessionEnd | Auto-captures POSTFLIGHT |

**Hook config:** `~/.claude/plugins/local/empirica/hooks/hooks.json`

### ✅ Phase 2: COMPLETE - MCP Epistemic Mode

**Config:** `~/.claude/mcp.json`
```json
{
  "env": {
    "EMPIRICA_EPISTEMIC_MODE": "true",
    "EMPIRICA_PERSONALITY": "balanced_architect"
  }
}
```

~~Enables VectorRouter for MCP tools.~~ **Removed in 1.8.14** — Sentinel hooks handle gating.

### ✅ Phase 3: COMPLETE - Statusline Integration

**Config:** `~/.claude/settings.json`
```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /path/to/statusline_empirica.py",
    "refresh_ms": 5000
  }
}
```

Shows: `[empirica] ⚡75% | 🔬NOETIC | PREFLIGHT | K:80% U:25% C:85%`

### Enforced Workflow

```
SessionStart (new) ──► session-init.py
    │
    ├── Creates session automatically
    ├── Runs bootstrap automatically
    └── Prompts: PREFLIGHT
         │
         ▼
    PreToolCall (Edit/Write/Bash) ──► sentinel-gate.py
         │
         ├── Valid CHECK (proceed, <30min)? → Allow
         │
         └── No/Invalid CHECK? → Block + prompt CHECK
              │
              ▼
    SessionEnd ──► session-end-postflight.py
         │
         └── Auto-submits POSTFLIGHT with final vectors
```

### Future Improvements
- Add LSP integration for catching naming disconnects
- Extend PreToolCall to more tool patterns
- Add drift detection in hooks

---

## Environment Variable Convention

**Define standard env vars for session persistence:**

```bash
# Set in shell rc file or Claude Code config
export EMPIRICA_AI_ID="claude-code"
export EMPIRICA_AUTO_SESSION=true  # Enable auto-session creation
export EMPIRICA_PROJECT_ID="ea2f33a4-d808-434b-b776-b7246bd6134a"
```

**Commands check these:**
```python
def get_current_session():
    if os.getenv('EMPIRICA_AUTO_SESSION') == 'true':
        return ensure_session(ai_id=os.getenv('EMPIRICA_AI_ID'))
    return None
```

---

## Success Metrics

Track adoption with:
```sql
-- Sessions with complete CASCADE workflow
SELECT COUNT(*) FROM sessions
WHERE EXISTS(SELECT 1 FROM reflexes WHERE session_id = sessions.session_id AND phase = 'PREFLIGHT')
  AND EXISTS(SELECT 1 FROM reflexes WHERE session_id = sessions.session_id AND phase = 'POSTFLIGHT');

-- Findings logged per session
SELECT AVG(finding_count) FROM (
    SELECT session_id, COUNT(*) as finding_count
    FROM project_findings
    GROUP BY session_id
);

-- Sessions with goals/subtasks
SELECT COUNT(*) FROM sessions
WHERE EXISTS(SELECT 1 FROM goals WHERE session_id = sessions.session_id);
```

**Target metrics:**
- 90%+ sessions have PREFLIGHT + POSTFLIGHT
- 5+ findings per session average
- 60%+ sessions use goals/subtasks
