# Empirica Serve API Reference

The `empirica serve` daemon is a localhost FastAPI server that provides REST endpoints
for the Empirica Chrome extension. The extension extracts epistemic artifacts client-side
(in TypeScript) and sends them to this daemon for storage in the Empirica database.

**Security model:** Localhost-only by default (`127.0.0.1`). For the same-box extension
case the security boundary is the network interface, not CORS (which is configured to
allow `chrome-extension://` and localhost origins).

Two surfaces change this picture when the daemon is bound **beyond loopback**:

- **`POST /api/v1/entities`** (the contact-mint endpoint) is guarded by an entity-mint
  service token — see [Entity Mint](#post-apiv1entities-entity-mint).
- **`GET /api/v1/listeners`** is guarded by the same token (it exposes listener topic
  names and last-message bodies).

The daemon **refuses to start** (`assert_bind_safe`) when bound to a non-loopback host
with no token configured, so the mint and listener surfaces are never exposed
unauthenticated. Configure the valid-token set via `EMPIRICA_ENTITY_MINT_TOKENS`
(comma-separated `emk_…` tokens). Loopback (same-box) daemons stay auth-free — the guard
is inactive when no token set is configured, so the extension's local reads are unchanged.

**Source:** `empirica/api/serve_app.py`

---

## Starting the Server

```bash
empirica serve
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8000` | Port to listen on |
| `--host` | `127.0.0.1` | Host to bind to. Use `0.0.0.0` for network access. |
| `--reload` | off | Enable auto-reload on code changes (development only) |

### Examples

```bash
# Default: localhost:8000
empirica serve

# Custom port
empirica serve --port 9090

# Bind to all interfaces (use with caution)
empirica serve --host 0.0.0.0 --port 8000

# Development mode with auto-reload
empirica serve --reload
```

### Requirements

The server requires `uvicorn` and `fastapi`. Install with the API extras:

```bash
pip install 'empirica[api]'
```

---

## Endpoints

### GET /api/v1/health

Health check endpoint. Reports daemon status, availability of optional integrations
(Ollama, Qdrant), and the daemon's bound active project (v0.5+).

**Response:** `HealthResponse` (200 OK)

The endpoint probes the **configured** Ollama and Qdrant backends with a 2-second timeout
to determine availability. The URLs are resolved the same way embeddings resolves them —
`EMPIRICA_OLLAMA_URL` / `EMPIRICA_QDRANT_URL` env var → `~/.empirica/config.yaml`
(`embeddings.ollama_url`) → `localhost:11434` / `localhost:6333` — so a remote backend is
probed at its real address rather than always reporting localhost.

The active-project fields (`project_id`, `project_path`, `project_name`,
`project_slug`, `repo_url`) are populated from the daemon's project resolution at
startup (see [Active Project Resolution](#active-project-resolution)). All five are
`null` when the daemon was launched outside any project tree.

#### Example

```bash
curl http://localhost:8000/api/v1/health
```

```json
{
  "ok": true,
  "version": "0.1.0",
  "api_version": "v1",
  "ollama": false,
  "claude_mem": false,
  "qdrant": true,
  "project_id": "748a81a2-ac14-45b8-a185-994997b76828",
  "project_path": "/home/user/code/empirica",
  "project_name": "Empirica",
  "project_slug": "empirica",
  "repo_url": "https://github.com/Nubaeon/empirica"
}
```

---

### POST /api/v1/entities (Entity Mint)

Idempotently mint a **contact** into the workspace `entity_registry`. Re-minting with the
same identity (email first, then a deterministic name/company slug) returns the existing
`entity_id` with `created=false` — so the same call is a safe no-op on repeat. The returned
id is the canonical `contact_id` that same-box consumers (e.g. a CRM MCP server) carry as
their foreign key.

**Auth:** guarded by an entity-mint service-token bearer **when the daemon binds beyond
loopback** (the hosted deployment). On a loopback daemon the guard is inactive and no bearer
is required. See the [Security model](#empirica-serve-api-reference) above and
`empirica/api/entity_mint_auth.py`.

**Request:** `EntityCreateRequest`

| Field | Type | Notes |
|-------|------|-------|
| `type` | str | Must be `"contact"` (v1 mints contacts only; `422` otherwise) |
| `name` | str | Required, contact display name |
| `email` | str? | Primary identity key for idempotency |
| `phone` | str? | |
| `role` | str? | |
| `company_name` | str? | Part of the fallback identity slug when no email |
| `description` | str? | |
| `metadata` | dict? | Extra fields merged into the registry row |

**Response:** `{ok, entity_id, created, matched_by}` (200) — `created` is `false` on a
verified no-op. `422` for a non-contact type; `400` on mint failure; `401` on
missing/invalid token when the guard is active.

```bash
# Non-loopback (guarded) daemon:
curl -X POST http://host:8000/api/v1/entities \
  -H "Authorization: Bearer emk_…" \
  -H "Content-Type: application/json" \
  -d '{"type":"contact","name":"Ada Lovelace","email":"ada@example.com"}'
```

---

### GET /api/v1/listeners

Registered mesh listeners plus heartbeat freshness, merged from the on-disk listener
registry and per-instance health markers. Lets the extension flag silent receive failures
(a listener that's alive but no longer receiving) without reading `~/.empirica/` directly.
Read-only.

**Auth:** guarded by the same entity-mint service token as `/api/v1/entities` — the rows
carry listener `topic` names (which are ntfy subscribe credentials) and `last_message`
bodies, so a network-exposed daemon must not serve them unauthenticated. The guard is
inactive on a loopback daemon, so the extension's local read is unchanged.

**Response:** `ListenersResponse` → `{ok, listeners: [ListenerRow]}`. Each `ListenerRow`:
`instance_id`, `name`, `description`, `topic`, `wake_count`, `last_wake_at`, `last_message`,
`registered_at`, `health_status` (`ok`|`degraded`|`null`), `health_loop`, `health_ts`.

```bash
curl http://localhost:8000/api/v1/listeners
```

---

### POST /api/v1/artifacts/import

Import pre-extracted artifacts from the Chrome extension into the Empirica database.

Artifacts are stored in the appropriate SQLite tables based on their `type` field.
Deduplication is performed when a `contentHash` is provided in the artifact payload:
the endpoint checks for an existing record with identical content before inserting.

All imported artifacts are assigned `project_id = "extension-import"`.

**Request body:** `ArtifactImportRequest` (JSON)

**Response:** `ArtifactImportResponse` (200 OK, or 500 on failure)

#### Artifact Type to Table Mapping

| Artifact Type | Database Table | Notes |
|---------------|----------------|-------|
| `finding` | `project_findings` | Stored directly |
| `decision` | `project_findings` | Content prefixed with `[decision]` |
| `dead_end` | `project_dead_ends` | Uses `metadata.whyFailed` for the `why_failed` column |
| `mistake` | `mistakes_made` | Uses `metadata.whyFailed` and `metadata.prevention` |
| `unknown` | `project_unknowns` | Stored directly |

#### Example

```bash
curl -X POST http://localhost:8000/api/v1/artifacts/import \
  -H "Content-Type: application/json" \
  -d '{
    "artifacts": [
      {
        "type": "finding",
        "content": "React 19 compiler eliminates the need for useMemo in most cases",
        "confidence": 0.8,
        "metadata": {"impact": 0.7}
      },
      {
        "type": "dead_end",
        "content": "Tried using Web Workers for state management",
        "confidence": 0.9,
        "metadata": {"whyFailed": "Serialization overhead negated any parallelism gains"}
      },
      {
        "type": "unknown",
        "content": "Does the new streaming API support backpressure?",
        "confidence": 0.3
      }
    ]
  }'
```

```json
{
  "ok": true,
  "imported": 3,
  "duplicates_skipped": 0,
  "errors": []
}
```

#### Example with Deduplication

```bash
curl -X POST http://localhost:8000/api/v1/artifacts/import \
  -H "Content-Type: application/json" \
  -d '{
    "artifacts": [
      {
        "type": "finding",
        "content": "React 19 compiler eliminates the need for useMemo in most cases",
        "confidence": 0.8,
        "contentHash": "a1b2c3d4e5f6",
        "metadata": {"impact": 0.7}
      }
    ]
  }'
```

```json
{
  "ok": true,
  "imported": 0,
  "duplicates_skipped": 1,
  "errors": []
}
```

---

### GET /api/v1/profile/status

Retrieve the epistemic profile status, including artifact counts across all database
tables and the timestamp of the last sync.

**Response:** `ProfileStatusResponse` (200 OK, or 500 on failure)

The endpoint queries row counts from: `project_findings`, `project_unknowns`,
`project_dead_ends`, `mistakes_made`, and `goals`.

#### Example

```bash
curl http://localhost:8000/api/v1/profile/status
```

```json
{
  "ok": true,
  "artifact_counts": {
    "findings": 42,
    "unknowns": 7,
    "dead_ends": 12,
    "mistakes": 3,
    "goals": 15
  },
  "total_artifacts": 79,
  "last_sync": null
}
```

---

### POST /api/v1/profile/sync

Trigger a profile sync operation. This invokes `empirica profile-sync --import-only`
as a subprocess, which fetches notes from external sources and imports them into the
local SQLite database. The subprocess has a 60-second timeout.

**Request body:** None

**Response:** `SyncResponse` (200 OK, or 500 on failure)

#### Example

```bash
curl -X POST http://localhost:8000/api/v1/profile/sync
```

```json
{
  "ok": true,
  "message": "Sync complete",
  "fetched": 5,
  "imported": 3
}
```

---

## Active Project Resolution

(v0.5+) The daemon resolves a single active project at startup and serves data
scoped to it. Resolution is layered:

1. **`InstanceResolver.project_path()`** — the canonical chain (instance_projects
   from tmux/X11 isolation → `active_work_{claude_session_id}.json` → headless
   `active_work.json`). Picks up the active CC instance's project automatically
   when the daemon is launched in a tmux pane sibling to a Claude Code session.
2. **CWD walk-up** — if canonical returns nothing, walks up from CWD looking for
   `.empirica/project.yaml`. Handles the case where the daemon is launched in a
   project tree without an active CC instance (fresh terminal, headless launch).
3. **Fail-soft** — if neither resolves, the daemon still starts and serves
   `/health` + `/profile/*`, but every per-project endpoint returns 503 with a
   hint to `cd` into a project tree before running `empirica serve`.

The active project is held for the daemon's process lifetime. To switch projects,
restart the daemon.

**Project ID resolution:** `.empirica/project.yaml.project_id` is often a
human-readable slug (e.g. `empirica`) that maps to `projects.name` in the local
sqlite. The daemon performs a slug→UUID lookup against the `projects` table so
the canonical UUID is what surfaces on `/health.project_id` and what filters
artifact rows internally. If the yaml's `project_id` is already UUID-shaped, it
passes through directly.

---

## Per-type Artifact Lists (v0.5+)

Eight read endpoints — one per artifact type plus goals — sharing a common
project-scoping guard and `related_to[]` edge attachment. All scoped to the
daemon's active project.

| Endpoint | Returns | Filters |
|----------|---------|---------|
| `GET /api/v1/findings` | `{findings: [...], project_id}` | `?limit=N` (default 50, max 500) |
| `GET /api/v1/unknowns` | `{unknowns: [...], project_id}` | `?status=open\|resolved\|all` (default `open`), `?limit=N` |
| `GET /api/v1/dead-ends` | `{dead_ends: [...], project_id}` | `?limit=N` |
| `GET /api/v1/mistakes` | `{mistakes: [...], project_id}` | `?limit=N` |
| `GET /api/v1/assumptions` | `{assumptions: [...], project_id}` | `?confidence_min=N`, `?limit=N` |
| `GET /api/v1/decisions` | `{decisions: [...], project_id}` | `?limit=N` |
| `GET /api/v1/sources` | `{sources: [...], project_id}` | `?limit=N` |
| `GET /api/v1/goals` | `{goals: [...], project_id}` | `?status=active\|completed\|planned\|all`, `?limit=N` |

**Common row shape (varies per type):**

```json
{
  "id": "<uuid>",
  "type": "finding",
  "title": "first 100 chars of finding/objective/choice/...",
  "body": "...",
  "impact": 0.7,
  "epistemic_source": "search | intuition | mixed | null",
  "session_id": "<uuid>",
  "goal_id": "<uuid> | null",
  "transaction_id": "<uuid> | null",
  "created_at": "2026-05-06T14:23:00Z",
  "related_to": [
    {"id": "<other-id>", "type": "decision", "relation": "evidence"}
  ]
}
```

Type-specific fields layer on top: `confidence`/`status`/`resolution_finding_id`
on assumptions; `choice`/`rationale`/`alternatives`/`reversibility`/`outcome`/`regret_score`
on decisions; `approach`/`why_failed` on dead_ends; `why_wrong`/`prevention` on
mistakes; `objective`/`tasks[]`/`is_completed` on goals; `url`/`source_type`/`description`
on sources.

**503 contract:** Every per-type endpoint returns 503 with a hint when the daemon
isn't bound to a project. Empty list (200) when project resolves but `project_id`
is null (local-only project, not registered on Cortex).

#### Example

```bash
curl 'http://localhost:8000/api/v1/findings?limit=5'
curl 'http://localhost:8000/api/v1/unknowns?status=open&limit=10'
curl 'http://localhost:8000/api/v1/assumptions?confidence_min=0.7'
```

---

## Source Content (`GET /api/v1/sources/{source_id}/content`)

Companion to `GET /api/v1/sources` (metadata-only): returns the actual content
behind a single source row, so a UI viewer can render inline. Added to close
the source-viewer gap — the daemon previously served metadata only, so viewers
rendered empty when a user clicked through.

### Response shapes (client branches on `kind`)

**URL source (`source_url` starts with `http://` or `https://`):**

```json
{
  "source_id": "<uuid>",
  "kind": "url",
  "url": "https://example.com/spec",
  "title": "RFC 7519",
  "source_type": "url"
}
```

Client fetches the URL directly. The daemon deliberately does NOT proxy — keeps
CORS-on-localhost simple and avoids streaming arbitrary remote bodies through
the daemon.

**File source (`source_url` is a path):**

```json
{
  "source_id": "<uuid>",
  "kind": "file",
  "path": "docs/spec.md",
  "content": "# Spec\n\n…",
  "size_bytes": 1234,
  "encoding": "utf-8",
  "title": "…",
  "source_type": "doc"
}
```

- `path` is always rendered project-root-relative for display.
- `encoding` is `"utf-8"` for text, `"base64"` for files that don't decode as
  UTF-8 (binary, PDFs, images). Client base64-decodes before rendering.
- Content cap: **10 MB**. Larger files return a truncation marker instead of
  inline content (see below).

### Path resolution

`source_url` may be relative, absolute-inside-tree, or a bare filename. The
endpoint walks fallback prefixes against the project root, first match wins:

1. `""` (bare path, relative to project root)
2. `.empirica/sources/` (well-known sources dir)
3. `docs/`
4. `docs/sources/`

Absolute paths are accepted only when they resolve inside the project tree
(defense-in-depth containment check on every candidate).

### Error contract

| Status | Detail | When |
|--------|--------|------|
| `404` | `"source_id … not found in project …"` | source id absent from `epistemic_sources` for the resolved project |
| `404` | `"file source … not found on disk … tried prefixes …"` | source row found but the path doesn't resolve under any fallback prefix |
| `422` | `"… resolves outside project root …"` | candidate path escapes the project tree (refused even if the target exists — defense in depth against `../` traversal) |
| `503` | `"Daemon not bound to a project …"` | no `?project_id`, no `?path`, no daemon-cached project |

### Truncation marker (oversized file)

```json
{
  "source_id": "<uuid>",
  "kind": "file",
  "path": "docs/big.md",
  "size_bytes": 25000000,
  "truncated": true,
  "content": null,
  "title": "…",
  "source_type": "doc",
  "hint": "file exceeds 10485760 byte cap; open it directly in an editor"
}
```

Lets the viewer branch to "open in editor" UX rather than materialize a 25 MB
JSON response.

### Example

```bash
# URL source — viewer follows the URL directly
curl 'http://localhost:8000/api/v1/sources/d6626173-…/content?path=/home/me/project'

# File source — content returned inline
curl 'http://localhost:8000/api/v1/sources/92145785-…/content?path=/home/me/project' \
  | jq -r .content
```

### Companion: List endpoint

`GET /api/v1/sources` returns the metadata batch (no content). The
content endpoint is the per-source drill-down. List + drill is the intended
UI pattern: list to browse, content for the viewer.

---

## Single-Artifact CRUD (v0.5+)

Four endpoints for one-at-a-time UI actions. Polymorphic ID resolution — the
daemon scans all artifact tables to find which one holds `{id}`.

### GET /api/v1/artifacts/{id}

Fetch one artifact + its full edge neighborhood.

**Response:** 200 OK with `{artifact: {...with related_to populated...}}`,
404 if the id isn't in any artifact table.

### PATCH /api/v1/artifacts/{id}/resolve

Mark an artifact as resolved. Per-type semantics:

| Type | Effect |
|------|--------|
| `unknown` | `is_resolved=1`, `resolved_by=<body.resolved_by>`, `resolved_timestamp=now` |
| `assumption` | `status='verified'`, `resolved_timestamp=now` |
| `goal` | `is_completed=1`, `status='completed'`, `completed_timestamp=now` |
| Other types | 422 — no resolve semantics |

**Body:** `{"resolved_by": "..."}` (optional)

### PATCH /api/v1/artifacts/{id}

Partial update. Each artifact type has a whitelist of editable fields:

| Type | Whitelist |
|------|-----------|
| `finding` | `impact`, `subject`, `epistemic_source` |
| `unknown` | `impact`, `subject`, `epistemic_source` |
| `dead_end` | `impact`, `subject`, `epistemic_source` |
| `mistake` | `prevention`, `epistemic_source` |
| `assumption` | `confidence`, `status`, `epistemic_source` |
| `decision` | `outcome`, `regret_score`, `epistemic_source` |
| `source` | `confidence`, `description` |
| `goal` | `objective`, `status` |

Non-whitelisted fields in the body are silently dropped (defensive against
accidental schema mutation). 422 if no whitelisted fields remain after filtering.

### DELETE /api/v1/artifacts/{id}

Three-layer cleanup: sqlite row + dangling edges in `artifact_edges` + Qdrant
vector point + git note ref at `refs/notes/empirica/{type}/{id}`. The CLI's
`empirica delete-artifacts` was extended in the same v0.5 work to do the same
three-layer cleanup, closing a documented gap.

**Response:** 200 with `{ok, type, id, action: "deleted", edges_removed, git_notes_cleaned}`.

#### Example

```bash
# Get one finding by id
curl http://localhost:8000/api/v1/artifacts/abc-123-...

# Mark a goal as completed
curl -X PATCH http://localhost:8000/api/v1/artifacts/goal-id-here/resolve \
  -H "Content-Type: application/json" \
  -d '{"resolved_by": "shipped"}'

# Update a finding's impact
curl -X PATCH http://localhost:8000/api/v1/artifacts/finding-id-here \
  -H "Content-Type: application/json" \
  -d '{"impact": 0.9}'

# Delete a stale unknown
curl -X DELETE http://localhost:8000/api/v1/artifacts/unknown-id-here
```

---

## Graph Endpoint (v0.5+)

### GET /api/v1/artifacts/graph

Bidirectional BFS over the `artifact_edges` table. Returns a connected component
as nodes + edges.

**Query params:**

| Param | Default | Description |
|-------|---------|-------------|
| `seed_id` | none | Start from this artifact and expand outward |
| `session_id` | none | Seed from all artifacts created in this session |
| `depth` | `2` | BFS depth (0–10) |
| `types` | all | Comma-separated type filter, e.g. `finding,decision` |
| `max_nodes` | `500` | Cap (1–2000) |

If neither `seed_id` nor `session_id` is given, returns a project-wide graph
capped at `max_nodes`.

**Response:**

```json
{
  "nodes": [{"id": "<uuid>", "type": "finding", "title": "..."}, ...],
  "edges": [{"from": "<id>", "to": "<id>", "relation": "evidence"}, ...],
  "project_id": "<uuid>"
}
```

Edges are filtered to only those whose endpoints survive the type filter.

#### Example

```bash
# Project-wide graph (capped 500)
curl 'http://localhost:8000/api/v1/artifacts/graph'

# 2-hop neighborhood from a finding, only findings and decisions
curl 'http://localhost:8000/api/v1/artifacts/graph?seed_id=abc-123&depth=2&types=finding,decision'

# Whole session graph
curl 'http://localhost:8000/api/v1/artifacts/graph?session_id=session-uuid-here'
```

---

## Batch Endpoints (v0.5+)

CLI-parity endpoints for batch artifact operations. Useful when emitting a graph
chunk from the extension or from a content script.

### POST /api/v1/artifacts/log

Batch log nodes + edges in one call. Body matches `empirica log-artifacts` CLI
shape: `{nodes: [...], edges: [...]}`.

**Response:** `{ok, created: {ref: id}, nodes_created, edges_wired, errors: []}`

### POST /api/v1/artifacts/resolve

Batch resolve. Body: `{ids: [...], resolved_by?: "..."}` or `{items: [{id, type}, ...]}`.
Per-type semantics match the single resolve endpoint. Types without resolve
semantics are counted as `skipped` rather than errors.

**Response:** `{ok, resolved, skipped, not_found, results: [...]}`

### POST /api/v1/artifacts/delete

Batch delete. Body: `{ids: [...]}` or `{items: [{id, type}, ...]}`.
Each delete fans out to the three-layer cleanup (sqlite + Qdrant + git notes).

**Response:** `{ok, deleted, not_found, failed, results: [...]}`

#### Example

```bash
# Batch resolve unknowns
curl -X POST http://localhost:8000/api/v1/artifacts/resolve \
  -H "Content-Type: application/json" \
  -d '{"ids": ["uuid-1", "uuid-2", "uuid-3"], "resolved_by": "investigation"}'
```

---

## Models

### HealthResponse

Response from the health check endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | No | `true` | Whether the daemon is operational |
| `version` | `str` | No | `"0.1.0"` | Daemon version |
| `api_version` | `str` | No | `"v1"` | API version |
| `ollama` | `bool` | No | `false` | Whether Ollama is reachable at `localhost:11434` |
| `claude_mem` | `bool` | No | `false` | Whether Claude memory integration is available |
| `qdrant` | `bool` | No | `false` | Whether Qdrant is reachable at `localhost:6333` |
| `project_id` | `str` or `null` | No | `null` | (v0.5+) Canonical project UUID resolved from `projects.id`. `null` if no `projects` row matches and yaml had no UUID-shape value (true local-only project) |
| `project_path` | `str` or `null` | No | `null` | (v0.5+) Absolute path to the project root the daemon is bound to |
| `project_name` | `str` or `null` | No | `null` | (v0.5+) Display name (yaml `display_name` → yaml `name` → folder name) |
| `project_slug` | `str` or `null` | No | `null` | (v0.5+) Wire identifier — slugified yaml `project_id` if non-UUID, otherwise slugified project name |
| `repo_url` | `str` or `null` | No | `null` | (v0.5+) `git remote get-url origin` normalized to https form. `null` if no git remote |

---

### ArtifactPayload

A single artifact extracted by the Chrome extension.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `str` | Yes | -- | Artifact type: `finding`, `decision`, `dead_end`, `mistake`, or `unknown` |
| `content` | `str` | Yes | -- | Artifact content text |
| `confidence` | `float` | No | `0.5` | Confidence score, range `[0.0, 1.0]` |
| `confidenceTier` | `str` or `null` | No | `null` | Optional tier label (e.g., `"high"`, `"medium"`, `"low"`) |
| `contentHash` | `str` or `null` | No | `null` | Hash for deduplication. When provided, the server checks for existing records with identical content before inserting. |
| `metadata` | `dict` | No | `{}` | Type-specific metadata. Keys used by the server: `impact` (float, for findings/decisions), `whyFailed` (str, for dead_ends/mistakes), `prevention` (str, for mistakes). |

---

### ArtifactImportRequest

Request body for the artifact import endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `artifacts` | `list[ArtifactPayload]` | Yes | -- | List of pre-extracted artifacts from the extension |

---

### ArtifactImportResponse

Response from the artifact import endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | Yes | -- | Whether the import succeeded |
| `imported` | `int` | No | `0` | Number of artifacts successfully stored |
| `duplicates_skipped` | `int` | No | `0` | Number of artifacts skipped due to deduplication |
| `errors` | `list[str]` | No | `[]` | Error messages for individual artifacts that failed to import |

---

### ProfileStatusResponse

Response from the profile status endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | No | `true` | Whether the status query succeeded |
| `artifact_counts` | `dict` | No | `{}` | Map of artifact type label to count (keys: `findings`, `unknowns`, `dead_ends`, `mistakes`, `goals`) |
| `total_artifacts` | `int` | No | `0` | Sum of all artifact counts |
| `last_sync` | `str` or `null` | No | `null` | ISO 8601 timestamp of the last profile sync, or `null` if never synced |

---

### SyncResponse

Response from the profile sync endpoint.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ok` | `bool` | Yes | -- | Whether the sync succeeded |
| `message` | `str` | No | `""` | Human-readable status message |
| `fetched` | `int` | No | `0` | Number of items fetched from external sources |
| `imported` | `int` | No | `0` | Number of items imported into the local database |

---

## Error Handling

All endpoints return standard HTTP error responses on failure:

- **500 Internal Server Error** -- Returned when an endpoint's internal handler raises an
  exception. The response body contains a JSON `detail` field with the error message.

```json
{
  "detail": "Profile sync failed: command not found"
}
```

The `POST /api/v1/artifacts/import` endpoint handles per-artifact errors gracefully:
individual artifact failures are collected in the `errors` list of the response rather
than causing the entire request to fail. Only unexpected top-level exceptions result in
a 500 response.

---

## CORS Configuration

The daemon enforces security at the network layer (localhost-only by default)
and uses regex-matched origin allowance:

| Setting | Value |
|---------|-------|
| Allowed origin regex | `^(chrome-extension://.*\|http://localhost(:\d+)?\|http://127\.0\.0\.1(:\d+)?)$` |
| Allowed methods | `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS` |
| Allowed headers | `*` (all) |

(v0.5+ change: previously `allow_origins=["chrome-extension://*", ...]` used
literal-string match — Starlette's `allow_origins` does not glob-expand, so the
intended matching never worked. Now uses `allow_origin_regex` so chrome-extension
preflights actually pass.)
