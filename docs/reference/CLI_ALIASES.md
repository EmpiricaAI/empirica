# CLI Command Aliases

**Generated:** 2026-02-08 | **Updated:** 2026-03-19
**Purpose:** Quick shortcuts for common Empirica commands

---

## Why Aliases?

Aliases reduce typing for frequently used commands. They're especially useful for AI agents who call these commands programmatically.

---

## Alias Reference

### Epistemic Transactions
| Command | Aliases | Description |
|---------|---------|-------------|
| `preflight-submit` | `pre`, `preflight` | Submit PREFLIGHT assessment |
| `postflight-submit` | `post`, `postflight` | Submit POSTFLIGHT assessment |

### Session Management
| Command | Aliases | Description |
|---------|---------|-------------|
| `session-create` | `sc` | Create new session |
| `sessions-list` | `sl`, `session-list` | List all sessions |
| `sessions-show` | `session-show` | Show session details |
| `sessions-export` | `session-export` | Export session to JSON |
| `sessions-resume` | `sr`, `session-resume` | Resume previous session |

### Goals
| Command | Aliases | Description |
|---------|---------|-------------|
| `goals-create` | `gc`, `goal-create` | Create new goal |
| `goals-list` | `gl`, `goal-list` | List goals |
| `goals-complete` | `goal-complete` | Mark goal complete |
| `goals-progress` | `goal-progress` | Check goal progress |
| `goals-add-task` | `goal-add-task` | Add task to goal |
| `goals-complete-task` | `goal-complete-task` | Complete a task |

### Logging (Breadcrumbs)
| Command | Aliases | Description |
|---------|---------|-------------|
| `finding-log` | `fl` | Log a finding |
| `unknown-log` | `ul` | Log an unknown |
| `deadend-log` | `de` | Log a dead-end |

### Messaging
| Command | Aliases | Description |
|---------|---------|-------------|
| `message-send` | `msg-send` | Send message to AI |
| `message-inbox` | `msg-inbox` | Check inbox |
| `message-read` | `msg-read` | Read specific message |
| `message-reply` | `msg-reply` | Reply to message |

### Project
| Command | Aliases | Description |
|---------|---------|-------------|
| `project-bootstrap` | `pb`, `bootstrap` | Bootstrap project context |
| `project-list` | `pl` | List all projects |
| `project-switch` | `ps` | Switch active project |
| `project-search` | — | Semantic search in project context |
| `project-embed` | — | Embed project artifacts to Qdrant |

### Utilities
| Command | Aliases | Description |
|---------|---------|-------------|
| `qdrant-status` | — | Show Qdrant collection inventory and stats |
| `qdrant-cleanup` | — | Remove empty Qdrant collections (dry-run by default) |

---

## Usage Examples

```bash
# Transaction shortcuts:
empirica pre ...           # Instead of preflight-submit
empirica post ...          # Instead of postflight-submit

# Quick goal creation:
empirica gc --objective "Fix bug"

# Quick breadcrumbs:
empirica fl --finding "Found root cause" --impact 0.7
empirica ul --unknown "Need to investigate X"
empirica de --approach "Tried Y" --why-failed "Z"

# Project search (semantic, searches Qdrant — requires --project-id):
empirica project-search --project-id <UUID> --task "isolation bugs"              # Focused: eidetic + episodic
empirica project-search --project-id <UUID> --task "auth middleware" --type all  # Full: all 4 collections
empirica project-search --project-id <UUID> --task "deployment" --global         # Include cross-project learnings

# Project embed (sync artifacts to Qdrant — requires --project-id):
empirica project-embed --project-id <UUID> --output json           # Embed project artifacts
empirica project-embed --project-id <UUID> --global --output json  # Also sync high-impact to global
```

---

## Adding New Aliases

Aliases are defined in two places:
1. **Parser registration:** `empirica/cli/parsers/*.py` - `add_parser(..., aliases=[...])`
2. **Handler mapping:** `empirica/cli/cli_core.py` - `command_handlers` dict

Both must be updated for an alias to work.

---

## See Also

- [CLI Quickstart](../human/end-users/04_QUICKSTART_CLI.md)
- [Full Command Reference](../human/developers/CLI_COMMANDS_UNIFIED.md)
