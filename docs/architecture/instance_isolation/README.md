# Instance Isolation Documentation

Multiple Claude/AI instances can run simultaneously in different terminals or tmux
panes. This folder documents how Empirica keeps them isolated.

## Which doc do I need?

| You are... | Read this |
|------------|-----------|
| Understanding the architecture | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| Using Claude Code (Anthropic's CLI) | [CLAUDE_CODE.md](./CLAUDE_CODE.md) |
| Building an MCP server or custom CLI | [MCP_AND_CLI.md](./MCP_AND_CLI.md) |
| Asking who the practitioner IS across sessions | [PRACTITIONER_IDENTITY.md](./PRACTITIONER_IDENTITY.md) |
| Debugging isolation issues | [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) |

## Quick Summary

**Problem:** Multiple AI instances share `ai_id=claude-code`. CWD gets reset
unpredictably. Which project am I working on?

**Solution:** File-based isolation + `InstanceResolver` (since v1.6.21):

```python
from empirica.utils.session_resolver import InstanceResolver as R
R.project_path()      # Resolves active project
R.session_id()        # Resolves active Empirica session
R.instance_suffix()   # Sanitized suffix for file naming
```

**Resolution priority:** `instance_projects` → `active_work_{uuid}` →
`active_work.json` → None (fail explicitly).

## Key Principles

1. **Hooks write, everything else reads.** Exception: `project-switch` writes signals.
2. **`instance_projects` is authoritative** — writable by both hooks and CLI.
3. **Anchors are refreshed on every session entry.** `session-init.py` is registered
   on `startup|resume` and `post-compact.py` on `compact`, so a resumed session and a
   compacted one both re-anchor. (This section used to say `session-init` fired on
   `startup` only, with `resume` handled by post-compact; the registration in
   `setup_claude_code.py` has carried `startup|resume` for longer than that claim did.)
4. **Suffix sanitization** — `:` → `_`, `%` removed. All file operations use
   `_get_instance_suffix()`.
5. **Fail explicitly** — return None rather than silently using the wrong project.

## Two resolution chains, one identity

The instance suffix keys per-instance state (`active_work_*.json`,
`active_transaction*.json`, `hook_counters*.json`, `listener_active_*.json`), and the
same helper resolves it for hooks and for the CLI — a divergence there splits a
writer from its reader. `MCP_AND_CLI.md` covers the instance-id override and both
chains in detail.
