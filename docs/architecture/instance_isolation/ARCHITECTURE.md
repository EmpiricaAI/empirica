# Instance Isolation Architecture

Core concepts and file taxonomy for multi-instance isolation.

## The One Rule

> **Hooks write. Everything else reads.**

Hooks have full context (`claude_session_id` from stdin + env vars like `TMUX_PANE`, `WINDOWID`).
CLI commands, Sentinel, statusline, and MCP are **readers** — they resolve project
context from files that hooks wrote.

**Exception:** `project-switch` writes to `instance_projects`, `active_work.json`,
`active_session`, and `tty_sessions` as **signals**. It discovers `claude_session_id`
via reverse-lookup of `active_work_*.json` files.

**Critical invariant:** All components resolve `instance_id` via the canonical
`get_instance_id()` function (or `InstanceResolver.instance_id()`). Never construct
instance IDs directly from env vars — this caused 11.20 where CLI commands only
checked `TMUX_PANE`, breaking X11 and other non-tmux environments.

## Unified API

All resolution goes through **`InstanceResolver`** (v1.6.21+). All methods are
`@staticmethod` — no instantiation needed:

```python
from empirica.utils.session_resolver import InstanceResolver as R

R.project_path()       # → "/home/user/my-project" or None
R.session_id()         # → "2bc1da78-..." or None
R.instance_id()        # → "x11:77639996" or None
R.instance_suffix()    # → "_x11_77639996" (sanitized for filenames)
R.transaction_read()   # → {"transaction_id": ..., "status": "open"} or None
R.transaction_id()     # → "abc123..." or None (shorthand)
R.context()            # → {"project_path": ..., "session_id": ..., ...}
R.tty_session()        # → {"claude_session_id": ..., ...} or None
R.is_headless()        # → True/False
```

Hook-side mirror in `project_resolver.py` delegates to canonical with fallback:
```python
from project_resolver import InstanceResolver as R  # hooks can use this too
```

Module-level functions (`get_active_project_path()`, etc.) remain as
backward-compatible aliases but all callers have been migrated to `InstanceResolver`.

## File Taxonomy

### 1. Instance Projects — PRIMARY

**Location:** `~/.empirica/instance_projects/{instance_id}.json`
**Key:** `instance_id` from env (`tmux_4`, `x11:77639996`, `term:ABC`)
**Written by:** Hooks (session-init, post-compact) AND `project-switch` CLI

```json
{
  "project_path": "/home/user/my-project",
  "claude_session_id": "fad66571-...",
  "empirica_session_id": "2bc1da78-...",
  "timestamp": "2026-03-18T12:00:00"
}
```

### 2. Active Work Files — FALLBACK

**Location:** `~/.empirica/active_work_{claude_session_id}.json`
**Key:** Claude Code conversation UUID (from hook stdin)
**Written by:** Hooks (session-init, post-compact); project-switch via reverse-lookup

```json
{
  "project_path": "/home/user/my-project",
  "claude_session_id": "fad66571-...",
  "empirica_session_id": "2bc1da78-...",
  "source": "session-init",
  "timestamp_epoch": 1773832425.76
}
```

### 3. Active Work (Generic) — LAST RESORT

**Location:** `~/.empirica/active_work.json`
**Written by:** project-switch, session-init
**Note:** No time-based staleness check — session-init on `resume` is the protection

### 4. Active Session Files — STATUSLINE

**Location:** `~/.empirica/active_session_{suffix}` (sanitized suffix)
**Written by:** session-create, project-switch
**Read by:** Statusline (to find correct project DB)

### 5. Transaction Files — PER-PROJECT

**Location:** `{project}/.empirica/active_transaction_{suffix}.json` (lifecycle)
**Also:** `{project}/.empirica/hook_counters_{suffix}.json` (hook counters)
**Key:** Sanitized instance suffix (`_x11_77639996`, `_tmux_0`)
**Written by:** Transaction file: PREFLIGHT (opens), POSTFLIGHT (closes). Hook counters: sentinel-gate, context-shift-tracker, subagent-stop (read by POSTFLIGHT, then deleted).

**Suffix sanitization:** `:` → `_`, `%` removed. e.g. `x11:77639996` → `_x11_77639996`.
All readers and writers use `_get_instance_suffix()` for consistency.

**Separation rationale (v1.8.14):** Hooks doing read-modify-write on the transaction file could race with POSTFLIGHT's status=closed write. Splitting into two files gives single-writer semantics per file.

---

## Resolution Priority Chain

```
Project Path (get_active_project_path):
  P0: instance_projects/{instance_id}.json   (tmux, x11, tty)
  P1: active_work_{claude_session_id}.json   (requires claude_session_id)
  P2: active_work.json                       (generic fallback, no time rejection)
  ❌ NO CWD FALLBACK — return None (mid-session)
  ⚠️  STARTUP EXCEPTION (v1.8.14): session-init prefers CWD over stale instance files on 'startup' events

Session ID (get_active_empirica_session_id):
  P1: active_transaction file                (survives compaction)
  P2: active_work_{claude_session_id}.json
  P3: instance_projects/{instance_id}.json
  P4: tty_sessions/{tty_key}.json
  P5: active_work.json
  P6: DB fallback (latest session for project)

Instance ID (get_instance_id):
  1: EMPIRICA_INSTANCE_ID env var
  2: TMUX_PANE (e.g. %4 → tmux_4)
  3: TERM_SESSION_ID (macOS Terminal)
  4: WINDOWID (X11, e.g. → x11:77639996)
  5: TTY device (e.g. → term_pts_6)
  6: None

Database Path (get_session_db_path):
  P0: EMPIRICA_SESSION_DB env var (explicit override — tests, CI, Docker)
  P1: Unified context (transaction → active_work → TTY → instance_projects)
      ↳ Cross-check (only when EMPIRICA_CWD_RELIABLE=true):
        If context_project ≠ git_root AND git_root has .empirica → prefer git root
        Prevents stale context from writing to wrong-project DB
  P2: workspace.db lookup (git root → project via global registry)
  P3: Git root based (.empirica/sessions/sessions.db)
  ❌ NO CWD FALLBACK — raise ValueError if unresolvable
```

---

## SessionStart Hook Routing

```
SessionStart event type:
  "startup"  → session-init.py (creates new session + anchor files)
  "resume"   → post-compact.py (detects existing session, updates anchors for new terminal)
  "compact"  → post-compact.py (context recovery after memory compaction)
```

On **resume** (continued conversation in new terminal), session-init:
1. Detects existing session via active_work, active_session, or DB scan
2. Updates anchor files (instance_projects, active_work) for the new WINDOWID
3. Does NOT create a duplicate session

On **startup** after machine/terminal/tmux restart:
1. `find_project_root()` resolves via CWD/git root (allow_cwd_fallback=True)
2. Scans project's `.empirica/` for orphaned open transactions (any suffix)
3. If found, adopts the orphaned transaction: re-keys to new instance suffix, returns session_id
4. Updates anchor files for the new instance, reuses existing session
5. Falls through to create new session only if no adoptable transaction exists

---

## Ownership Model

| Component | Writes | Has claude_session_id? |
|-----------|--------|------------------------|
| **Hooks** (session-init, post-compact) | active_work, instance_projects, active_session, tty_sessions | Yes (stdin) |
| **CLI** (project-switch) | instance_projects, active_session, active_work.json, active_work_{uuid} (via reverse-lookup) | Via reverse-lookup |
| **CLI** (session-create) | active_session, tty_sessions | No |
| **PREFLIGHT/POSTFLIGHT** | active_transaction | No |
| **Statusline** | Nothing | No |
| **Sentinel** | Nothing | Yes (stdin) |

---

## Multi-Instance Environments

| Setup | Isolation | Key |
|-------|-----------|-----|
| **tmux panes** | Full | `TMUX_PANE` → unique instance_projects |
| **Separate terminal windows** | Full | `WINDOWID` → unique instance_projects |
| **Tabs in same terminal** | Shared | Same `WINDOWID` → last writer wins |
| **Single terminal** | N/A | Only one instance |

**Tabs limitation:** Multiple Claude Code instances in tabs of the same terminal
share the same `WINDOWID`. Use separate windows or tmux for full isolation.

---

## Key Functions (all in `session_resolver.py`)

| Function | Purpose |
|----------|---------|
| `InstanceResolver` | **Unified class API** — groups all below |
| `get_active_project_path()` | Project resolution (canonical) |
| `get_active_empirica_session_id()` | Session resolution (canonical) |
| `get_instance_id()` | Instance identity |
| `_get_instance_suffix()` | Sanitized filename suffix |
| `write_active_transaction()` | Transaction file write (atomic) |
| `read_active_transaction_full()` | Transaction file read |

---

## Related Documentation

- [CLAUDE_CODE.md](./CLAUDE_CODE.md) - Claude Code specific patterns
- [MCP_AND_CLI.md](./MCP_AND_CLI.md) - MCP/CLI integration patterns
- [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) - Bug history and debugging
- [SESSION_RESOLVER_API.md](../../reference/SESSION_RESOLVER_API.md) - Full API reference
