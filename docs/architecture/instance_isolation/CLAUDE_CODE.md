# Instance Isolation for Claude Code Users

This doc covers instance isolation when using Empirica with Claude Code (Anthropic's official CLI).

## How It Works

Claude Code provides hooks that fire on specific events. These hooks receive `session_id`
(Claude's conversation UUID) via stdin, enabling instance isolation.

### Automatic Session Management

**Sessions are created automatically.** Do NOT run `session-create` manually.

| Event | SessionStart Trigger | Hook | What Happens |
|-------|---------------------|------|--------------|
| New conversation | `startup` | `session-init.py` | Creates Empirica session, writes `active_work` + `instance_projects` |
| Continued conversation | `resume` | `post-compact.py` | Continues transaction OR creates new session, writes isolation files |
| Memory compaction | `compact` | `post-compact.py` | Same as resume — finds open transaction, writes isolation files |
| After `/clear` | `clear` | (none currently) | No hook configured |
| Tool use | n/a (PreToolUse) | `sentinel-gate.py` | Reads isolation files to find correct project |

**Critical:** SessionStart matchers must use the exact trigger strings above.
Claude Code does NOT use "new", "fresh", or "start" as triggers. See [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) 11.18.

### File Ownership

Hooks write these files (you don't need to):

```
~/.empirica/active_work_{claude_session_id}.json    # Links conversation → project
~/.empirica/instance_projects/tmux_N.json           # Links tmux pane → project (if tmux)
```

### The claude_session_id Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Claude Code starts conversation                              │
│                                                              │
│ SessionStart hook fires                                      │
│ ─────────────────────                                        │
│ stdin: {"session_id": "fad66571-...", ...}                  │
│                                                              │
│ Hook:                                                        │
│ 1. Creates Empirica session                                  │
│ 2. Writes active_work_{session_id}.json                     │
│ 3. Writes instance_projects/tmux_N.json (if tmux)           │
│                                                              │
│ All subsequent hooks/commands can resolve project            │
└─────────────────────────────────────────────────────────────┘
```

### Hook Input Structure

Claude Code provides structured JSON to hooks via stdin:

```json
{
  "session_id": "fad66571-1bde-4ee1-aa0d-e9d3dfd8e833",
  "transcript_path": "/home/user/.claude/projects/my-project/fad66571.jsonl",
  "cwd": "/home/user/my-project",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse"
}
```

| Field | Purpose | Notes |
|-------|---------|-------|
| `session_id` | Claude conversation UUID | Maps to `active_work_{id}.json` |
| `transcript_path` | Full transcript file path | Used by pre-compact for state capture |
| `cwd` | Working directory | **UNRELIABLE** - do not use for project resolution |
| `permission_mode` | Claude permission level | Used by Sentinel for gate decisions |
| `hook_event_name` | Hook event type | Conditional hook logic |

**Critical:** The `cwd` field is unreliable because Claude Code can reset it (e.g., after
compaction). Hooks use `get_active_project_path()` which reads `instance_projects` first
(updated by both hooks and project-switch), falling back to `active_work` for non-TMUX.

---

## The instance id: who owns the files

Every per-instance file is keyed by an **instance id**, and two processes that share one
share all of those files (last writer wins). The id is the first of these that is set:

1. `EMPIRICA_INSTANCE_ID` (or `CLAUDE_INSTANCE_ID`): an explicit id
2. `TMUX_PANE` (`tmux_N`)
3. `TERM_SESSION_ID` (macOS Terminal)
4. `WINDOWID` (X11)
5. the controlling TTY

What it keys: `~/.empirica/instance_projects/<id>.json` (which project this instance is in,
and which claude session owns it), `<project>/.empirica/active_transaction_<id>.json`, the
active session, and the listener state. Commands route by that pointer, not by your working
directory, so a claude in project B that carries project A's id writes into A.

The cockpit launcher gives each pane its own id. The way this goes wrong: an id exported in a
long-lived shell, or `claude` restarted inside a shell that already carries one, so the new
process inherits the old practice's id. `empirica doctor` reads each live claude's environment
and reports a mismatch ("Live claude sessions run as their own practice"). The fix is to
restart that claude from a shell that does not carry the id:

```bash
unset EMPIRICA_INSTANCE_ID
EMPIRICA_INSTANCE_ID=<its ai_id> claude --continue
```

`empirica project-switch <project> --claude-session-id "$CLAUDE_CODE_SESSION_ID"` re-points the
project pointer. It cannot change the id of a process that is already running, so it repairs
the pointer, not the sharing.

---

## Running Multiple Instances

### Recommended: tmux or separate windows

For multi-project work, use **tmux panes** or **separate terminal windows**.
Avoid running multiple Claude Code instances in **tabs of the same terminal**
— tabs share the same `WINDOWID` which causes cross-session contamination.
See [ARCHITECTURE.md](./ARCHITECTURE.md#multi-instance-without-tmux) for details.

### tmux Setup

If you run multiple Claude Code instances in tmux panes:

```
┌─────────────────┬─────────────────┐
│ Pane %4         │ Pane %5         │
│ empirica/       │ my-project/     │
│                 │                 │
│ tmux_4.json     │ tmux_5.json     │
│ points to       │ points to       │
│ empirica/       │ my-project/     │
└─────────────────┴─────────────────┘
```

Each pane gets its own (unless an explicit `EMPIRICA_INSTANCE_ID` overrides the pane id, see above):
- `instance_projects/tmux_N.json`
- `active_transaction_tmux_N.json` (in project dir)

**Environment variable availability:**
- **Hooks:** `TMUX_PANE` is reliably available (inherited from Claude Code process)
- **Bash tool subprocesses:** `TMUX_PANE` may NOT be inherited (depends on how Claude Code spawns subprocesses)

When `TMUX_PANE` is unavailable in Bash subprocess, CLI commands use TTY-based resolution
(walking PPID chain to find controlling TTY) and can look up `claude_session_id` from
`tty_sessions/` files that hooks wrote earlier. Additionally, `project-switch` can scan
`instance_projects/tmux_*.json` files to find one with matching `claude_session_id`
and resolve the correct instance.

---

## Transaction Continuity Across Compaction

When Claude's context window fills up, memory compaction occurs:

```
Before Compaction              After Compaction
────────────────────           ────────────────────
pre-compact.py fires           post-compact.py fires
  ↓                              ↓
Captures:                      Reads snapshot:
• active_transaction           • Finds open transaction
• epistemic vectors            • Writes active_work (new session_id)
• breadcrumbs                  • Writes instance_projects
  ↓                              ↓
pre_summary_{ts}.json          Transaction continues!
```

**Key:** The transaction survives compaction via file-based state, not database.

---

## Common Issues

### "Active session in project" Error

You tried to run `session-create` but a session already exists.

**Fix:** Don't run `session-create`. Sessions are automatic.

### Statusline Shows Wrong Phase

The statusline is querying the wrong session.

**Cause:** `instance_projects` has wrong `empirica_session_id` after compaction.

**Fix:** This was fixed in commit `f8d9a82f`. Update your plugin.

### Can't Find Transaction After tmux Restart

tmux died and new panes have different IDs (e.g., `%7` instead of `%4`).

**Automatic:** As of v1.6.21, `session-init.py` auto-detects orphaned open
transactions on startup. It scans the project's `.empirica/` for any
`active_transaction*.json` with `status: open`, adopts the most recent one,
and re-keys it to the new instance suffix. No manual intervention needed.

**Manual fallback:** If auto-adoption fails:
```bash
empirica transaction-adopt --from tmux_4
```

---

## Debugging

### Check Current State

```bash
# What does Claude Code think the session is?
cat ~/.empirica/active_work_*.json | jq .

# What does tmux pane think?
cat ~/.empirica/instance_projects/tmux_$(echo $TMUX_PANE | tr -d '%').json

# What transaction is open?
cat .empirica/active_transaction_tmux_*.json
```

### Check Hook Logs

```bash
# Sentinel logs
cat ~/.claude/plugins/local/empirica/hooks/.empirica_reflex_logs/*.log

# Post-compact output is in Claude's response
```

---

## Related

- [ARCHITECTURE.md](./ARCHITECTURE.md) - Full file taxonomy
- [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) - Bug history and fixes
