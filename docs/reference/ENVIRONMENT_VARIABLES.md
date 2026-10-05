# Empirica Environment Variables Reference

**Version:** 1.6.6
**Total Variables:** 35+
**Status:** Production

This document lists all environment variables that control Empirica's behavior.

---

## Database Configuration

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_DB_TYPE` | Database backend | `sqlite` | No |
| `EMPIRICA_DB_HOST` | PostgreSQL hostname | `localhost` | If PostgreSQL |
| `EMPIRICA_DB_PORT` | PostgreSQL port | `5432` | If PostgreSQL |
| `EMPIRICA_DB_NAME` | PostgreSQL database name | `empirica` | If PostgreSQL |
| `EMPIRICA_DB_USER` | PostgreSQL username | `empirica` | If PostgreSQL |
| `EMPIRICA_DB_PASSWORD` | PostgreSQL password | (empty) | If PostgreSQL |
| `DATABASE_URL` | Full database URL override | (auto) | No |
| `EMPIRICA_SESSION_DB` | Custom session database path (**priority 0** — overrides all resolution) | (auto-detected) | No |

---

## Instance & Session Management

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_AI_ID` | AI identifier override for session lookup (statusline, cockpit, setup) | `project.yaml` `ai_id`, then the project directory name, then `claude-code` | No |
| `EMPIRICA_HEADLESS` | Force (`true`/`1`/`yes`) or forbid (`false`/`0`/`no`) headless mode (no terminal identity: `active_work.json` primary, no statusline) | auto-detected | No |
| `EMPIRICA_ALLOW_PROJECT_MISMATCH` | `1` lets PREFLIGHT run when the working directory's project differs from the session store's | unset | No |
| `EMPIRICA_INSTANCE_ID` | AI instance identifier | (auto-detected from TMUX_PANE) | No |
| `EMPIRICA_CWD_RELIABLE` | Gates CWD cross-check in `get_session_db_path()` — when `true`, detects cross-project DB bleed from stale context by comparing context project against git root. Set automatically by `session-init.py` after `os.chdir()`. Do NOT set globally. | `false` | `true`, `false` |
| `CLAUDE_INSTANCE_ID` | Claude-specific instance ID | (optional) | No |
| `TMUX_PANE` | Tmux pane identifier | (auto-detected) | No |
| `TERM_SESSION_ID` | Terminal session ID | (auto-detected) | No |
| `WINDOWID` | X11 window ID | (optional) | No |

---

## Sentinel & Gate Control

| Variable | Purpose | Default | Values |
|----------|---------|---------|--------|
| `EMPIRICA_SENTINEL_LOOPING` | Enable investigate loops (env var fallback; ignored whenever `~/.empirica/sentinel_enabled` exists) | `true` | `true`, `false` |
| `EMPIRICA_SENTINEL_FAIL_CLOSED` | Deny, instead of allow, when the Sentinel itself crashes (not for "no project", which is a stated allow) | unset | `1`, `true`, `yes` |
| `EMPIRICA_HARNESS` | Which harness hosts the hooks, case-insensitive. Non-`claude-code` values (codex sets `codex`) change the repair hint, the tool-router hints (CLI verbs instead of `mcp__empirica__` tools) and skip the deploy-gap block | `claude-code` | `claude-code`, `codex`, ... |
| `EMPIRICA_KNOW_THRESHOLD` | Minimum KNOW confidence | Model-dependent | 0.0-1.0 |
| `EMPIRICA_UNCERTAINTY_THRESHOLD` | Maximum UNCERTAINTY allowed | Model-dependent | 0.0-1.0 |
| `EMPIRICA_ENFORCE_CASCADE_PHASES` | Enforce strict phase ordering | `false` | `true`, `false` |
| `EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP` | Require `project-bootstrap` before praxic actions | `false` | `true`, `false` |
| `EMPIRICA_SENTINEL_CHECK_EXPIRY` | Enable the 30-minute CHECK expiry | `false` | `true`, `false` |
| `EMPIRICA_SENTINEL_COMPACT_INVALIDATION` | Invalidate the CHECK after context compaction | `false` | `true`, `false` |
| `EMPIRICA_MIN_NOETIC_DURATION` | Minimum seconds between PREFLIGHT and CHECK before the rush guard applies | `30` | seconds |

**File-based control (preferred):** Write `true` or `false` to `~/.empirica/sentinel_enabled`.
This takes priority over the env var and is dynamically settable without restarting the session.

There is no Sentinel observer/controller mode switch: a CHECK that falls short of the threshold is an advisory allow, and the Sentinel's blocks are structural (no PREFLIGHT, loop closed, rushed CHECK).

---

## Embeddings & Vector Store

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_ENABLE_EMBEDDINGS` | Set to `false` to disable semantic embeddings; any other value or unset leaves them on when `qdrant-client` is installed | on if `qdrant-client` is installed | No |
| `EMPIRICA_EMBEDDINGS_MODEL` | Embedding model name | per provider: `qwen3-embedding:0.6b` (ollama), `text-embedding-3-small` (openai), `jina-embeddings-v3`, `voyage-3-lite` | No |
| `EMPIRICA_EMBEDDINGS_PROVIDER` | Embedding provider | `auto` | No |
| `EMPIRICA_QDRANT_URL` | Qdrant vector store URL | (optional) | If remote |
| ~~`EMPIRICA_QDRANT_PATH`~~ | **Removed.** File-based Qdrant storage was dropped in #45 (incompatible on-disk formats, lock conflicts between concurrent processes, CWD-relative paths). Nothing in the codebase reads this variable; setting it has no effect. Run a Qdrant server instead. | — | — |
| `EMPIRICA_OLLAMA_URL` | Ollama server URL | `http://localhost:11434` | If Ollama |
| `JINA_API_KEY` | Jina embedding API key | (empty) | If Jina |
| `VOYAGE_API_KEY` | Voyage embedding API key | (empty) | If Voyage |

**Provider priority:** `auto` uses Ollama when it is reachable with a known embedding model, otherwise the `local` hash fallback. Jina and Voyage are used only when selected explicitly.

---

## Pattern Retrieval (Context Budget)

The pattern block injected into PREFLIGHT / CHECK / project bootstrap is budgeted
**lean-by-default** — duplicates removed across sections, long items truncated, and
a total size cap that drops the lowest-ranked items. These knobs tune or disable it.

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_PATTERN_BUDGET_OFF` | Set to `1` to emit the full untrimmed pattern block (escape hatch) | (off) | No |
| `EMPIRICA_PATTERN_MAX_ITEM_CHARS` | Max chars per retrieved item before truncation | `280` | No |
| `EMPIRICA_PATTERN_MAX_PER_SECTION` | Cap on items per section (also bounds adaptive growth) | `5` | No |
| `EMPIRICA_PATTERN_MAX_TOTAL_CHARS` | Total char budget across all sections | `8000` | No |
| `EMPIRICA_RETRIEVAL_BUDGET_S` | Wall-clock budget in seconds for pattern retrieval; phases past it are skipped and named in `_retrieval_budget` | `30` | No |

The full context stays retrievable on demand via `empirica investigate` /
`empirica project-search` / `empirica commit-context` — the injected block is a
ranked teaser, not the whole store.

`EMPIRICA_PATTERN_MAX_ITEM_CHARS` and `EMPIRICA_PATTERN_BUDGET_OFF` additionally
govern the **global-learnings** (cross-project) block that `project-bootstrap`
injects every session — a distinct block from the pattern block above, but
truncated per-item by the same knobs (and capped at 5 items). `BUDGET_OFF=1`
restores its full untrimmed text too.

---

## Paths & Directories

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_WORKSPACE_ROOT` | Workspace root directory | `~/.empirica` | No |
| `EMPIRICA_DATA_DIR` | Data directory override | (auto-detected) | No |
| `EMPIRICA_PROJECT_PATH` | Force specific project | (auto-detected) | No |
| `EMPIRICA_CREDENTIALS_PATH` | Custom credentials file path | (auto-detected) | No |

---

## Calibration

| Variable | Purpose | Default | Values |
|----------|---------|---------|--------|
| `EMPIRICA_CALIBRATION_FEEDBACK` | Gate all calibration feedback in workflow output | `true` | `true`, `false` |

Controls PREFLIGHT enrichment (grounded gaps, calibration warnings), CHECK enrichment (calibration bias detection). Does NOT affect POSTFLIGHT data collection, Sentinel gating (raw vectors), or learning trajectory (informational).

> **Cross-project calibration, multi-entity pattern matching, TUI analytics, and API integrations** are available in [empirica-workspace](https://github.com/EmpiricaAI/empirica-workspace).

---

## Claude Code Context Window

| Variable | Purpose | Default | Values |
|----------|---------|---------|--------|
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Context % threshold for auto-compaction | ~85% | `1`-`100` |
| `CLAUDE_CODE_DISABLE_1M_CONTEXT` | Disable 1M window, stay on 200K | `0` | `0`, `1` |

**Recommended for Empirica:** Set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=30` to compact at ~300K
of the 1M window. This gives enough room for deep investigation while keeping epistemic
transactions bounded. The 200K boundary was the original design target — 300K provides
headroom for research-heavy sessions while still forcing measurement checkpoints.

```bash
# Add to ~/.bashrc or ~/.zshrc
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=30
```

**Tuning:** Monitor belief calibration trend across sessions. If grounded calibration degrades
past 300K, reduce to 20% (200K). If transactions consistently close well before compact,
you can increase. The Empirica calibration-report tracks this automatically.

Without this, the 1M window delays compaction until very late, causing:
- Epistemic state drift (vectors become meaningless without POSTFLIGHT checkpoints)
- Degraded recall quality in later context
- Harder recovery when compact finally triggers

---

## Automation & Workflow

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_AUTOPILOT_MODE` | Autonomous operation mode | `false` | No |
| `EMPIRICA_AUTO_POSTFLIGHT` | **REMOVED** — Auto-POSTFLIGHT from CHECK removed in 1.6.6 | N/A | No |

---

## Display & Statusline

| Variable | Purpose | Default | Values |
|----------|---------|---------|--------|
| `EMPIRICA_STATUS_MODE` | Statusline display mode (`~/.empirica/statusline_mode` overrides it) | `compact` | `compact`, `expanded`, `basic`, `learning`, `full` |
| `EMPIRICA_STATUS_JSON` | Output statusline as JSON | `false` | `true`, `false` |
| `EMPIRICA_STATUS_TMUX` | Compact tmux output | `false` | `true`, `false` |
| `EMPIRICA_STATUS_MODEL` | Hide the `🧠 model` tag with `0`, `false` or `off` | shown | `0`, `false`, `off` |
| `EMPIRICA_CTX_METER` | Render context use as a bar instead of `%ctx` | off | `1`, `true`, `bar` |

---

## Usage Examples

### Development Setup

```bash
# SQLite (default)
export EMPIRICA_DB_TYPE=sqlite
```

### Production Setup

```bash
# PostgreSQL
export EMPIRICA_DB_TYPE=postgresql
export EMPIRICA_DB_HOST=db.example.com
export EMPIRICA_DB_NAME=empirica_prod
export EMPIRICA_DB_USER=empirica
export EMPIRICA_DB_PASSWORD=secret

# Embeddings with Qdrant
export EMPIRICA_ENABLE_EMBEDDINGS=true
export EMPIRICA_QDRANT_URL=http://qdrant:6333
export EMPIRICA_EMBEDDINGS_PROVIDER=ollama
export EMPIRICA_OLLAMA_URL=http://ollama:11434
```

### CI/CD Override

```bash
# Force specific session database (useful in CI)
export EMPIRICA_SESSION_DB=/tmp/test_sessions.db

# Disable sentinel for automated tests (env var — requires restart)
export EMPIRICA_SENTINEL_LOOPING=false

# Preferred: file-based toggle (takes effect immediately)
echo "false" > ~/.empirica/sentinel_enabled   # disable
echo "true" > ~/.empirica/sentinel_enabled    # re-enable
```

---

## Related Documentation

- [Configuration Reference](./CONFIGURATION_REFERENCE.md) — YAML config files
- [Database Schema](./DATABASE_SCHEMA_UNIFIED.md) — Database structure
- [Multi-Instance Isolation](../architecture/instance_isolation/ARCHITECTURE.md) — Instance management
