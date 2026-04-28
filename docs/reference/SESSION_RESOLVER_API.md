# Session Resolver API Reference

**Module:** `empirica.utils.session_resolver`
**Version:** 1.8.14-dev
**Purpose:** Session ID resolution, multi-instance isolation, and context management

---

## Overview

The session resolver module provides core infrastructure for:

1. **Multi-Instance Isolation** — Support for parallel Claude instances (tmux, X11, macOS Terminal, TTY)
2. **Session ID Resolution** — Magic aliases (`latest`, `latest:active`, etc.)
3. **Unified Context Resolver** — Canonical functions for project/session lookup
4. **Project Identifier Resolution** — Normalize UUID/path/folder-name inputs

## InstanceResolver (Primary API)

All callers should use `InstanceResolver` as the single entry point. All methods
are `@staticmethod` — no instantiation needed:

```python
from empirica.utils.session_resolver import InstanceResolver as R
```

| Method | Returns | Description |
|--------|---------|-------------|
| `R.instance_id()` | `str \| None` | Current instance ID (tmux, X11, macOS Terminal, TTY) |
| `R.instance_suffix()` | `str` | Sanitized suffix for filenames (e.g. `_x11_77663748`) |
| `R.project_path(csid)` | `str \| None` | Active project path (P0: instance_projects, P1: active_work) |
| `R.session_id(csid)` | `str \| None` | Active Empirica session ID |
| `R.resolve_session(sid, ai_id)` | `str` | Resolve partial ID / alias to full UUID |
| `R.latest_session_id(ai_id, active_only)` | `str \| None` | Most recent session |
| `R.context(csid)` | `dict` | Full active context (project, session, instance) |
| `R.engagement(csid)` | `str \| None` | Active engagement ID |
| `R.transaction_id(csid)` | `str \| None` | Active transaction ID |
| `R.transaction_read(csid)` | `dict \| None` | Full transaction state |
| `R.transaction_write(...)` | `None` | Write/update transaction file |
| `R.transaction_clear(csid)` | `None` | Delete transaction file |
| `R.transaction_increment(csid)` | `dict \| None` | **DEPRECATED** — Increment tool call counter (see note below) |
| `R.tty_key()` | `str \| None` | TTY device key |
| `R.tty_session(warn)` | `dict \| None` | TTY session data |
| `R.tty_write(csid, esid, path)` | `bool` | Write TTY + instance_projects files |
| `R.project_id_from_db(path)` | `str \| None` | Get project_id from sessions.db |
| `R.resolve_workspace_project(id)` | `dict \| None` | Resolve via workspace.db |
| `R.cleanup_stale_instances()` | `int` | Remove orphaned instance_projects |
| `R.cleanup_stale_files(csid)` | `int` | Remove stale active_work files |
| `R.is_headless()` | `bool` | Check headless/container mode |

`csid` = `claude_session_id` (optional, from hook stdin).

The module-level functions below remain as backward-compatible aliases.

---

## TTY-Based Session Isolation

These functions enable multiple Claude instances to run simultaneously without session cross-talk.

### `get_tty_key()`

Get a TTY-based key for session isolation.

```python
def get_tty_key() -> Optional[str]
```

**Returns:** Sanitized TTY identifier like `'pts-2'` or `None` if no TTY found.

**Behavior:**
- Walks up process tree to find controlling TTY
- Handles cases where CLI runs via Bash (no direct TTY)
- **Critical:** No PPID fallback — returns `None` if TTY detection fails

**Example:**
```python
>>> get_tty_key()
'pts-2'

>>> # Outside terminal
>>> get_tty_key()
None
```

---

### `get_tty_session(warn_if_stale=True)`

Read session mapping from TTY-keyed file.

```python
def get_tty_session(warn_if_stale: bool = True) -> Optional[Dict[str, Any]]
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `warn_if_stale` | `bool` | `True` | Log warnings for potentially stale sessions |

**Returns:** Dict with session mapping or `None`:
```python
{
    'claude_session_id': 'abc123...',    # Claude Code conversation UUID
    'empirica_session_id': 'def456...',  # Empirica session UUID
    'project_path': '/home/user/project',
    'tty_key': 'pts-2',
    'timestamp': '2026-02-07T15:30:00'
}
```

---

### `write_tty_session(claude_session_id=None, empirica_session_id=None, project_path=None)`

Write session mapping to TTY-keyed file for CLI commands to read.

```python
def write_tty_session(
    claude_session_id: str = None,
    empirica_session_id: str = None,
    project_path: str = None
) -> bool
```

**Returns:** `True` if successfully written, `False` if no TTY or instance_id available.

**Writes to:**
- `~/.empirica/tty_sessions/{tty_key}.json`
- `~/.empirica/instance_projects/{instance_id}.json` (if instance_id available via `get_instance_id()`)

**Called from:**
- Claude Code hooks (have `claude_session_id`)
- CLI `session-create` (have `empirica_session_id`)

---

### `validate_tty_session(session=None)`

Validate a TTY session for staleness.

```python
def validate_tty_session(session: Dict[str, Any] = None) -> Dict[str, Any]
```

**Checks:**
1. TTY device still exists (`/dev/pts/X`)
2. Timestamp not older than 4 hours

**Returns:**
```python
{
    'valid': True,           # False if TTY device gone
    'warnings': ['...'],     # Warning messages
    'session': {...}         # The session data
}
```

**Note:** PID check is not performed because the hook that writes the session file always exits immediately.

---

### `cleanup_stale_instance_projects()`

Remove instance_projects entries for tmux panes that no longer exist.

```python
def cleanup_stale_instance_projects() -> int
```

**Removes files where:**
- Tmux pane ID no longer exists (verified via `tmux list-panes`)
- Also cleans up stale `compact_handoff` files for dead panes

**Returns:** Number of files removed.

---

### `cleanup_stale_active_work_files(current_claude_session_id=None)`

Remove active_work_{uuid}.json files for ended sessions.

```python
def cleanup_stale_active_work_files(current_claude_session_id: str = None) -> int
```

**Removes files where:**
- Session has `end_time` in DB (ended)
- No open transaction references that session
- Never deletes the current conversation's file

Also cleans up non-tmux instance_projects files (`x11:*`, `term_*`) using the same DB-based check.

**Returns:** Number of files removed.

---

### `get_claude_session_id()`

Get Claude Code session ID for current terminal.

```python
def get_claude_session_id() -> Optional[str]
```

**Returns:** Claude Code conversation UUID or `None`.

Convenience wrapper around `get_tty_session()`.

---

## Instance Identification

### `get_instance_id()`

Get unique instance identifier for multi-instance isolation.

```python
def get_instance_id() -> Optional[str]
```

**Priority order:**
1. `EMPIRICA_INSTANCE_ID` env var (explicit override)
2. `TMUX_PANE` → `tmux_0`, `tmux_1`, etc.
3. `TERM_SESSION_ID` → `term:...` (macOS Terminal.app)
4. `WINDOWID` → `x11:...` (X11 window)
5. `TTY device` → `term_pts-6` (persists across CLI calls in same terminal)
6. `None` (legacy behavior)

**Examples:**
```python
>>> # In tmux pane %0
>>> get_instance_id()
'tmux_0'

>>> # With explicit override
>>> os.environ['EMPIRICA_INSTANCE_ID'] = 'my-instance'
>>> get_instance_id()
'my-instance'

>>> # Outside tmux, in terminal pts/6
>>> get_instance_id()
'term_pts-6'

>>> # No terminal (e.g., cron, Docker without TTY)
>>> get_instance_id()
None
```

---

## Session ID Resolution

### `resolve_session_id(session_id_or_alias, ai_id=None)`

Resolve session ID from alias or UUID.

```python
def resolve_session_id(session_id_or_alias: str, ai_id: Optional[str] = None) -> str
```

**Supported aliases:**
| Alias | Description |
|-------|-------------|
| `latest`, `last`, or `auto` | Most recent session |
| `latest:active` | Most recent active (not ended) session |
| `latest:<ai_id>` | Most recent session for specific AI |
| `latest:active:<ai_id>` | Most recent active session for specific AI |

**Examples:**
```python
>>> resolve_session_id("88dbf132")  # Partial UUID
'88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'

>>> resolve_session_id("latest")
'88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'

>>> resolve_session_id("latest:active:claude-code")
'88dbf132-cc7c-4a4b-9b59-77df3b13dbd2'
```

**Raises:** `ValueError` if alias doesn't match any session.

---

### `get_latest_session_id(ai_id=None, active_only=False)`

Get most recent session ID.

```python
def get_latest_session_id(
    ai_id: Optional[str] = None,
    active_only: bool = False
) -> str
```

Convenience wrapper around `resolve_session_id("latest:...")`.

---

### `is_session_alias(session_id_or_alias)`

Check if string is a session alias (not a UUID).

```python
def is_session_alias(session_id_or_alias: str) -> bool
```

```python
>>> is_session_alias("latest")
True

>>> is_session_alias("88dbf132-cc7c-...")
False
```

---

## Unified Context Resolver

These are the **canonical functions** that all components should use.

### `get_active_project_path(claude_session_id=None)`

**CANONICAL function for project resolution.**

```python
def get_active_project_path(claude_session_id: str = None) -> Optional[str]
```

**Priority chain (no CWD fallback in mid-session):**
1. `instance_projects/{instance_id}.json` — **AUTHORITATIVE** (updated by project-switch)
2. `active_work_{claude_session_id}.json` — fallback (may be stale)

> **Startup exception (v1.8.14):** On `startup` events, `session-init.py` validates the resolved project against CWD's git root. If CWD is a valid Empirica project and differs from the stale instance file, CWD wins. On `resume`/`compact`/`clear`, instance files remain authoritative.

**No self-healing:** If both exist but disagree (e.g. after project-switch), instance_projects wins. The disagreement resolves naturally when the next hook fires and writes both files consistently.

**Returns:** Absolute path to project, or `None` (fails explicitly rather than falling back to CWD).

---

### `get_active_empirica_session_id(claude_session_id=None)`

**CANONICAL function for session_id resolution.**

```python
def get_active_empirica_session_id(claude_session_id: str = None) -> Optional[str]
```

**Priority chain (TRANSACTION-FIRST):**
1. Active transaction (`active_transaction_{suffix}.json`, status==open) — survives compaction
2. `active_work_{claude_session_id}.json` (requires claude_session_id)
3. `instance_projects/{instance_id}.json` (tmux, X11, macOS Terminal, TTY)
4. `tty_sessions/{tty_key}.json` (written by session-create, project-switch)
5. `active_work.json` (headless mode only)
6. DB fallback (latest session for same project)

**Returns:** Empirica session UUID or `None`.

**Usage in CLI commands:**
```python
session_id = get_active_empirica_session_id()
if not session_id:
    print("No active transaction - run PREFLIGHT first")
    return
```

---

### `get_active_context(claude_session_id=None)`

Get complete active epistemic context.

```python
def get_active_context(claude_session_id: str = None) -> dict
```

**Returns:**
```python
{
    'claude_session_id': 'abc123...',     # Claude Code UUID
    'empirica_session_id': 'def456...',   # Empirica session UUID
    'transaction_id': 'ghi789...',        # Active transaction (if any)
    'project_path': '/home/user/project',
    'instance_id': 'tmux_0'
}
```

Missing fields are `None`, not absent.

---

### `update_active_context(claude_session_id, empirica_session_id=None, project_path=None, **extra_fields)`

Update the active_work file with new context values.

```python
def update_active_context(
    claude_session_id: str,
    empirica_session_id: str = None,
    project_path: str = None,
    **extra_fields
) -> bool
```

Only updates provided (non-`None`) fields. Existing values preserved for fields not specified.

---

## Transaction Tracking

### `write_active_transaction(transaction_id, session_id=None, preflight_timestamp=None, status="open", project_path=None)`

Write active transaction state to JSON file.

```python
def write_active_transaction(
    transaction_id: str,
    session_id: str = None,
    preflight_timestamp: float = None,
    status: str = "open",
    project_path: str = None
) -> None
```

**File location:** `{project_path}/.empirica/active_transaction_{suffix}.json`

**Critical:** Uses instance suffix for multi-instance isolation.

---

### `read_active_transaction_full(claude_session_id=None)`

Read full active transaction data.

```python
def read_active_transaction_full(claude_session_id: str = None) -> Optional[dict]
```

**Returns:**
```python
{
    'transaction_id': 'abc123...',
    'session_id': 'def456...',      # Session where PREFLIGHT was run
    'preflight_timestamp': 1707318600.0,
    'status': 'open',               # or 'closed'
    'project_path': '/home/user/project'
}
```

---

### `read_active_transaction(claude_session_id=None)`

Read just the transaction ID.

```python
def read_active_transaction(claude_session_id: str = None) -> Optional[str]
```

For full data, use `read_active_transaction_full()`.

---

### `clear_active_transaction(claude_session_id=None)`

Remove active transaction file (called on POSTFLIGHT).

```python
def clear_active_transaction(claude_session_id: str = None) -> None
```

---

### `increment_transaction_tool_count` / `R.transaction_increment` — DEPRECATED (v1.7.4+)

> **Deprecated.** This function still exists in `session_resolver.py` but is no longer called by any consumer. As of v1.7.4, the sentinel and other hooks write tool call counts to the separate **hook_counters** file instead. The hook_counters split was introduced to eliminate a race condition where the transaction lifecycle (owned by PREFLIGHT/POSTFLIGHT) and hook-side counters (owned by sentinel/context-shift-tracker/subagent-stop) were competing for the same `active_transaction` file. See the Hook Counters API below for the replacement.

---

### Hook Counters API (v1.8.14+)

Hook counters are stored separately from the transaction lifecycle to prevent race conditions. Hooks (sentinel, context-shift-tracker, subagent-stop) write counters here; POSTFLIGHT reads then deletes.

**File location:** `{project_path}/.empirica/hook_counters{suffix}.json`

#### `read_hook_counters(claude_session_id=None)`

Read the hook counters file. Returns dict or None.

#### `write_hook_counters(data, claude_session_id=None)`

Atomically write the hook counters file. Returns True on success.

#### `clear_hook_counters(claude_session_id=None)`

Delete the hook counters file (called by POSTFLIGHT after reading).

**InstanceResolver methods:** `R.counters_read()`, `R.counters_write(data)`, `R.counters_clear()`

---

## Project Identifier Resolution

### `resolve_project_identifier(identifier)`

**CANONICAL function for project resolution from user input.**

```python
def resolve_project_identifier(identifier: str) -> Optional[dict]
```

**Accepts:**
- UUID: `"748a81a2-ac14-45b8-a185-994997b76828"`
- Folder name: `"empirica"`, `"my-project"`
- Path: `"/home/user/projects/empirica"`

**Resolution priority:**
1. If UUID format: validate in `workspace.db` or local `sessions.db`
2. If path: extract `folder_name` and resolve
3. If `folder_name`: lookup in `workspace.db`

**Returns:**
```python
{
    'project_id': '748a81a2-...',          # Canonical UUID
    'folder_name': 'empirica',              # Project folder name
    'project_path': '/home/user/empirica',  # Absolute path
    'source': 'workspace'                   # 'workspace', 'local', or 'path'
}
```

**Example:**
```python
>>> resolve_project_identifier("empirica")
{'project_id': '748a81a2-...', 'folder_name': 'empirica',
 'project_path': '/home/user/empirica', 'source': 'workspace'}
```

---

## Helper Functions

### ~~`get_session_empirica_root(session_id)`~~ — Removed

This function was documented but never existed in the codebase. Removed from this reference.

---

### `_get_instance_suffix()`

Get instance-specific filename suffix for file-based tracking.
Sanitizes special characters (`:`→`_`, `%` removed) for cross-platform filename safety.

```python
def _get_instance_suffix() -> str
```

**Sanitization:** `instance_id.replace(":", "_").replace("%", "")`

| Instance ID | Suffix |
|-------------|--------|
| `tmux_0` | `_tmux_0` |
| `x11:78940210` | `_x11_78940210` |
| `term:ABC123` | `_term_ABC123` |
| `None` | `""` |

**Used by:** `active_transaction`, `active_session`, `compact_handoff` file names.
Also mirrored in `plugins/.../lib/project_resolver.py` for hooks that can't import empirica.

---

## File Locations

| File | Purpose | Keyed By |
|------|---------|----------|
| `~/.empirica/tty_sessions/{tty_key}.json` | TTY → session mapping | TTY device |
| `~/.empirica/instance_projects/{instance_id}.json` | Instance → project mapping | instance_id |
| `~/.empirica/active_work_{claude_session_id}.json` | Claude session → Empirica context | Claude UUID |
| `{project}/.empirica/active_transaction_{suffix}.json` | Transaction lifecycle (workflow-owned) | Instance |
| `{project}/.empirica/hook_counters_{suffix}.json` | Hook counters (hook-owned, deleted by POSTFLIGHT) | Instance |

---

## Design Principles

1. **TTY is lookup key (ephemeral)** — `claude_session_id` is persistence key (durable)
2. **instance_projects is AUTHORITATIVE** — Updated by project-switch, wins over stale active_work
3. **Transaction-first session resolution** — CLI commands auto-derive session from transaction
4. **No CWD fallback (mid-session)** — Fail explicitly rather than risk wrong project context. Exception: `startup` events prefer CWD over stale instance files (v1.8.14)
5. **No self-healing** — Stale files resolve naturally when the next hook fires and writes both files consistently

---

## Related Documentation

- [Multi-Instance Isolation Architecture](../architecture/instance_isolation/ARCHITECTURE.md)
- [Environment Variables](./ENVIRONMENT_VARIABLES.md) — `EMPIRICA_INSTANCE_ID`, `TMUX_PANE`
- [Database Schema](./DATABASE_SCHEMA_UNIFIED.md) — `sessions`, `projects` tables
