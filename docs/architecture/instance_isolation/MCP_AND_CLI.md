# Instance Isolation for MCP and CLI Integrations

This doc covers instance isolation when using Empirica WITHOUT Claude Code hooks
(MCP servers, custom CLI tools, direct terminal usage).

## Key Difference from Claude Code

| Aspect | Claude Code | MCP / CLI |
|--------|-------------|-----------|
| Hooks available | ✅ Yes | ❌ No |
| `claude_session_id` | ✅ From stdin | ❌ Not available |
| Primary isolation | `active_work` files | `tty_sessions` files |
| Session creation | Automatic (hooks) | **Manual** (`session-create`) |

**You MUST run `session-create` manually** - there are no hooks to do it for you.

---

## Setup Flow

### 1. Create Session

```bash
# In your terminal / MCP context
empirica session-create --ai-id my-mcp-agent --output json
```

This writes `~/.empirica/tty_sessions/pts-N.json` linking your terminal to the session.

### 2. Work Normally

```bash
# Load project context
empirica project-bootstrap --output json

# Create goals, log findings, etc.
empirica goals-create --objective "My task"
empirica finding-log --finding "Discovered X"

# CASCADE workflow
empirica preflight-submit - <<< '{"vectors": {...}, "reasoning": "..."}'
empirica check-submit - <<< '{"vectors": {...}, "decision": "proceed"}'
empirica postflight-submit - <<< '{"vectors": {...}, "task_outcome": "..."}'
```

### 3. Switch Projects

```bash
# Switch to different project
empirica project-switch my-other-project

# This updates tty_sessions/pts-N.json
```

---

## TTY-Based Isolation

The TTY device (`/dev/pts/6` → `pts-6`) is your isolation key.

```
Terminal 1 (pts-6)              Terminal 2 (pts-7)
──────────────────              ──────────────────
tty_sessions/pts-6.json         tty_sessions/pts-7.json
  project: empirica/              project: my-app/
  session: abc123...              session: def456...
```

### How It Works

1. `session-create` runs `tty` command to get device name
2. Writes `~/.empirica/tty_sessions/{tty_key}.json`
3. Subsequent commands read this file to find project context

### Limitations

- **No TTY in non-interactive contexts** (cron, some Docker setups) — set
  `EMPIRICA_INSTANCE_ID` (below) or pass `--session-id` explicitly
- **TTY can be reused** after terminal closes (staleness detection helps)
- **No cross-terminal isolation** without tmux

### Explicit instance id (CI, containers, cron)

`EMPIRICA_INSTANCE_ID=<name>` is the first thing `get_instance_id()` checks,
ahead of `TMUX_PANE`, `TERM_SESSION_ID`, `WINDOWID` and the TTY device. Set it
where no terminal identity exists, or where two processes share one and must
not share a session. It overrides `TMUX_PANE` when both are set, so leave it
unset in interactive tmux.

---

## tmux Support

If running in tmux, you get BOTH TTY and tmux pane isolation:

```bash
# tmux pane %4, TTY pts-6
# Both files are written:
~/.empirica/tty_sessions/pts-6.json
~/.empirica/instance_projects/tmux_4.json
```

The `TMUX_PANE` environment variable provides additional isolation.

---

## MCP Server Pattern

For MCP servers, the pattern is:

```python
# In your MCP server startup
import subprocess
import json

# Create session once at startup
result = subprocess.run(
    ["empirica", "session-create", "--ai-id", "my-mcp-server", "--output", "json"],
    capture_output=True, text=True
)
session = json.loads(result.stdout)
session_id = session["session_id"]

# Use session_id in subsequent calls
subprocess.run([
    "empirica", "finding-log",
    "--session-id", session_id,
    "--finding", "Discovered something"
])
```

**Note:** Pass `--session-id` explicitly since MCP doesn't have the automatic
resolution that Claude Code hooks provide.

---

## Staleness Detection

TTY sessions can become stale (terminal closed, process died). Empirica detects this:

```python
# In session_resolver.py
def validate_tty_session(session_data):
    # 1. TTY device check - does /dev/pts-N exist?
    # 2. Timestamp check - is session > 4 hours old?
    # Note: PID check is NOT performed - the hook that writes the session
    # file always exits immediately, so PID would always be stale.
```

Stale sessions are:
- Warned about (may still be valid)
- Overwritten on next `session-create` in same terminal
- Cleaned up by periodic cleanup (>24h old)

---

## Transaction Files

Transactions use `instance_id` suffix, which works for both tmux and TTY:

```
# tmux pane %4
{project}/.empirica/active_transaction_tmux_4.json

# Non-tmux terminal pts-6
{project}/.empirica/active_transaction_term_pts_6.json

# No TTY available (fallback)
{project}/.empirica/active_transaction_default.json
```

---

## Debugging

### Check TTY Session

```bash
# What terminal am I in?
tty
# /dev/pts/6

# What session is linked?
cat ~/.empirica/tty_sessions/pts-6.json
```

### Check tmux State (if applicable)

```bash
# What pane am I in?
echo $TMUX_PANE
# %4

# What's linked?
cat ~/.empirica/instance_projects/tmux_4.json
```

### Resolution Chain

Two chains, both in `empirica/utils/session_resolver.py` (`InstanceResolver`):

**Project path** — `instance_projects/{instance_id}.json` →
`active_work_{claude_session_id}.json` → `active_work.json`. Returns None (not
CWD) when nothing resolves.

**Session id** — `--session-id` flag → the open transaction's file
(`active_transaction_{instance_id}.json`) → `active_work_{claude_session_id}.json`
→ `instance_projects/{instance_id}.json` → `tty_sessions/{tty}.json` →
`active_work.json` → most recent session in the DB.

`instance_id` itself comes from `EMPIRICA_INSTANCE_ID` → `TMUX_PANE` →
`TERM_SESSION_ID` → `WINDOWID` → TTY device.

---

## Common Issues

### "Not a tty" Error

You're in a context without a TTY (cron, Docker without `-t`, etc.).

**Fix:** Set `EMPIRICA_INSTANCE_ID` for the process, or pass `--session-id`
explicitly on every command.

### Wrong Project After Switching

The `tty_sessions` file wasn't updated.

**Fix:** Run `empirica project-switch <project>` to update it.

### Session Appears Stale

```
Warning: TTY session may be stale (created 6 hours ago)
```

**Fix:** Run `empirica session-create` to refresh, or ignore if session is still valid.

---

## Related

- [ARCHITECTURE.md](./ARCHITECTURE.md) - Full file taxonomy
- [CLAUDE_CODE.md](./CLAUDE_CODE.md) - Claude Code specific patterns
