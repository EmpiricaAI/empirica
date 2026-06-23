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
| `EMPIRICA_SENTINEL_MODE` | Gate enforcement level | `controller` | `observer`, `controller` |
| `EMPIRICA_SENTINEL_LOOPING` | Enable investigate loops (env var fallback) | `true` | `true`, `false` |

**File-based control (preferred):** Write `true` or `false` to `~/.empirica/sentinel_enabled`.
This takes priority over the env var and is dynamically settable without restarting the session.
| `EMPIRICA_KNOW_THRESHOLD` | Minimum KNOW confidence | Model-dependent | 0.0-1.0 |
| `EMPIRICA_UNCERTAINTY_THRESHOLD` | Maximum UNCERTAINTY allowed | Model-dependent | 0.0-1.0 |
| `EMPIRICA_ENFORCE_CASCADE_PHASES` | Enforce strict phase ordering | `false` | `true`, `false` |

**Sentinel modes:**
- `observer` — Log decisions but don't block
- `controller` — Actively block based on vectors (default)

---

## Embeddings & Vector Store

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_ENABLE_EMBEDDINGS` | Enable semantic embeddings | `false` | No |
| `EMPIRICA_EMBEDDINGS_MODEL` | Embedding model name | `qwen3-embedding` | No |
| `EMPIRICA_EMBEDDINGS_PROVIDER` | Embedding provider | `auto` | No |
| `EMPIRICA_QDRANT_URL` | Qdrant vector store URL | (optional) | If remote |
| `EMPIRICA_QDRANT_PATH` | Local Qdrant data directory | `./.qdrant_data` | No |
| `EMPIRICA_OLLAMA_URL` | Ollama server URL | `http://localhost:11434` | If Ollama |
| `JINA_API_KEY` | Jina embedding API key | (empty) | If Jina |
| `VOYAGE_API_KEY` | Voyage embedding API key | (empty) | If Voyage |

**Provider priority:** `auto` tries: Ollama → Jina → Voyage → fallback

---

## Paths & Directories

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `EMPIRICA_WORKSPACE_ROOT` | Workspace root directory | `~/.empirica` | No |
| `EMPIRICA_DATA_DIR` | Data directory override | (auto-detected) | No |
| `EMPIRICA_PROJECT_PATH` | Force specific project | (auto-detected) | No |
| `EMPIRICA_CREDENTIALS_PATH` | Custom credentials file path | (auto-detected) | No |
| `EMPIRICA_CRM_DB` | CRM database path | `~/.empirica/crm.db` | No |

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
| `EMPIRICA_ENABLE_MODALITY_SWITCHER` | Enable adaptive model routing | `false` | No |

---

## Display & Statusline

| Variable | Purpose | Default | Values |
|----------|---------|---------|--------|
| `EMPIRICA_STATUS_MODE` | Statusline display mode | `default` | `default`, `balanced`, `compact`, `minimal` |
| `EMPIRICA_STATUS_JSON` | Output statusline as JSON | `false` | `true`, `false` |
| `EMPIRICA_STATUS_TMUX` | Compact tmux output | `false` | `true`, `false` |

---

## API & CORS

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `CORS_ORIGIN` | API CORS origin | `*` | No |

---

## Usage Examples

### Development Setup

```bash
# SQLite (default)
export EMPIRICA_DB_TYPE=sqlite
export EMPIRICA_SENTINEL_MODE=observer  # Log-only during dev
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

# Sentinel in controller mode
export EMPIRICA_SENTINEL_MODE=controller
```

### CI/CD Override

```bash
# Force specific session database (useful in CI)
export EMPIRICA_SESSION_DB=/tmp/test_sessions.db

# Disable sentinel for automated tests (env var — requires restart)
export EMPIRICA_SENTINEL_MODE=observer
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
