# Cross-Project Intelligence

## Configuring Cortex creds (CLI)

Empirica's CLI talks to Cortex for `projects-bulk-register`,
`source-archive` sync, and POSTFLIGHT artifact push. Three resolution
sources (highest precedence first):

1. **CLI flags** — `--cortex-url`, `--api-key` (per-invocation override)
2. **Env vars** — `CORTEX_REMOTE_URL` (or `CORTEX_URL`), `CORTEX_API_KEY`
3. **`~/.empirica/credentials.yaml`** — `cortex:` block:

```yaml
# ~/.empirica/credentials.yaml
version: 1.0

cortex:
  url: https://cortex.getempirica.com
  api_key: ctx_empirica_mem_...

# Other AI provider creds (optional)
providers:
  openai:
    api_key: sk-...
```

The browser extension stores the same `cortexUrl` + `cortexApiKey` in
chrome.storage. The CLI file is the equivalent for terminal/agent
sessions — saves having to `export CORTEX_API_KEY=...` in every shell.

Resolution is per-field: setting `CORTEX_API_KEY` in env still picks
up `url` from the credentials file. Useful for CI environments where
the key comes from a secret store but the URL is stable.

---

Empirica is multi-project by design. Three mechanisms compose:

| Mechanism | Direction | Use |
|---|---|---|
| `--visibility {public,shared,local}` on `*-log` commands | Push (opt-in) | Mark an artifact as shareable when logged |
| `project-search --global` | Pull | Query the global-learnings pool across projects |
| `--project-id <name>` on `*-log` commands | Cross-write | Log artifacts to OTHER projects without `project-switch` |

The default workflow: **AIs log liberally with `--visibility shared` when an artifact
has ecosystem-wide value, and call `project-search --global` proactively at
session-start / topic-start to find what other Claudes have learned.**

## Visibility (push side)

Every `*-log` command accepts `--visibility {public,shared,local}`:

```bash
# Local: project-scoped only (default)
empirica finding-log --finding "..." --impact 0.6

# Shared: visible across projects in the same org/Cortex tenancy
empirica finding-log --finding "Cross-codebase pattern: ..." --impact 0.7 \
  --visibility shared

# Public: visible to anyone with a Cortex account
empirica finding-log --finding "Security note on dep X..." --impact 0.8 \
  --visibility public
```

**MCP parity (v1.9.4+):** the `mcp__empirica__finding_log` (and the other 5
`*_log` MCP tools — `unknown_log`, `deadend_log`, `mistake_log`,
`assumption_log`, `decision_log`) expose `visibility` and `epistemic_source`
as enum params. The CLI/MCP surfaces are at parity — discipline is
enforceable through either interface.

**When to use `shared` vs `local`:**

| Pattern | Default |
|---|---|
| Bug fix specific to this codebase's logic | `local` |
| Bug pattern that recurs across multiple repos | `shared` |
| Tactical workflow note ("I tried X, doesn't work in this codebase") | `local` |
| Cross-cutting lesson ("X library has Y gotcha in 0.4+") | `shared` |
| CVE in a shared dep, security advisory | `public` |
| Internal architecture decision for this project | `local` |
| Reusable agent pattern, prompt template, framework | `shared` or `public` |

Liberally-shared work compounds across the AI ecosystem. Over-sharing tactical
chatter dilutes the signal. Calibrate by asking: *"would future-me, working in
another project, want this to surface in a project-search?"*

---

## Cross-Project Intelligence (history)

The original 1.7.0 cross-project capabilities below predate the visibility
flag and the v1.9.4 MCP parity work.

## Cross-Project Search

### CLI Usage

```bash
# Search current project + all other projects
empirica project-search --project-id empirica --task "sentinel bypass" --global

# Output includes new section:
# 🔗 Cross-project (other projects' knowledge):
#   1. [memory] Gap in Sentinel gate model... (proj: a76ef65b, score: 0.658)
```

### How It Works

`--global` queries the `global_learnings` Qdrant collection — high-impact
artifacts that have been promoted via the visibility flag (or fed in via
older sync paths).

**Caveat (v1.9.4 state):** the original 1.7.0 design described a "cross-
project scan" that iterates ALL `project_{id}_{collection}` collections.
That broader walk is implemented in `search_cross_project` (`empirica.core.qdrant.global_sync`)
but the `--global` CLI flag currently only hits `global_learnings`. True
cross-project semantic search across every registered project's full memory/
eidetic/episodic surface is a logged goal — until it lands, the practical
guidance is: log liberally with `--visibility shared` so your artifacts
reach `global_learnings`, and call `--global` proactively to read it back.

### API

```python
from empirica.core.qdrant.global_sync import search_cross_project

results = search_cross_project(
    query_text="sentinel bypass detection",
    exclude_project_id="748a81a2-...",  # current project
    collections_to_search=["memory", "eidetic", "episodic"],
    limit=5,
    min_points=1,  # skip empty collections
)
# Returns: List[Dict] with score, project_id, collection_type, text/content/narrative
```

## Cross-Project Artifact Writing

### CLI Usage

```bash
# Write a finding to another project by name
empirica finding-log --project-id empirica-cortex --finding "Ingestor handles 91+ formats" --impact 0.6

# Write an unknown to another project
empirica unknown-log --project-id empirica-workspace --unknown "Does EKG support project entities?"
```

### How It Works

When `--project-id` is a project **name** (not UUID):
1. `_resolve_db_for_artifact()` detects it's not a UUID
2. `_get_db_for_project()` queries `workspace.db` → `global_projects.trajectory_path`
3. Opens `{trajectory_path}/.empirica/sessions/sessions.db`
4. Artifact is written to the TARGET project's database

Falls back to local DB if resolution fails.

### Supported Commands

Currently enabled on:
- `finding-log`
- `unknown-log`

Other artifact commands (`deadend-log`, `assumption-log`, `decision-log`) support
`--project-id` as a UUID but don't yet resolve names to cross-project DBs.
Follow the same pattern in `artifact_log_commands.py` to add.

## Architecture

```
User: empirica finding-log --project-id empirica-cortex --finding "..."
         │
         ▼
_resolve_db_for_artifact("empirica-cortex")
         │
         ├─ _is_uuid("empirica-cortex") → False
         │
         ├─ _get_db_for_project("empirica-cortex")
         │     │
         │     ├─ workspace.db: SELECT trajectory_path FROM global_projects WHERE name = ?
         │     │
         │     └─ Returns: SessionDatabase("/path/to/empirica-cortex/.empirica/sessions/sessions.db")
         │
         └─ db.log_finding(...)  →  Written to empirica-cortex's DB
```
