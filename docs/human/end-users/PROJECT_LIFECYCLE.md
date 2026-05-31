# Project Lifecycle

**Companion to [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md).** That doc covers per-project basics — what files Empirica writes, how to init/switch/inspect a single project. This doc covers the **multi-project lifecycle**: discovering projects on disk, registering them with the optional Cortex serving layer, syncing them in bulk, selecting subsets, pruning stale entries, and the known gaps in the picture.

If you only have one or two Empirica projects, you probably don't need this doc — `empirica project-init` in each repo and you're done. This is for the case where you have N projects scattered across your filesystem and want them coherent.

---

## The Picture

```
                 ┌────────────────────────────────────────┐
                 │  Filesystem                            │
                 │  ~/code/projectA/.empirica/            │
                 │  ~/code/projectB/.empirica/            │
                 │  ~/work/projectC/.empirica/            │
                 │  ~/.empirica/discovered_projects.yaml  │
                 └────────────┬───────────────────────────┘
                              │
                  projects-discover (walks roots, finds .empirica/)
                              │
                              ▼
                 ┌────────────────────────────────────────┐
                 │  Local registry                        │
                 │  ~/.empirica/registry.yaml             │
                 │  (the daemon's served project set)     │
                 └────────────┬───────────────────────────┘
                              │
                  projects-bulk-register (POST to /v1/projects/register)
                              │
                              ▼
                 ┌────────────────────────────────────────┐
                 │  Cortex projects table (OPTIONAL)      │
                 │  Enables cross-project search, mesh    │
                 │  coordination, the browser extension's │
                 │  project picker, etc.                  │
                 └────────────────────────────────────────┘
```

Three layers, each separately maintained. The local filesystem is authoritative for "what projects exist." The local registry is what the local daemon serves over HTTP. The Cortex projects table is the **optional** cross-project surface.

---

## The Single-Verb Path: `projects-sync`

For most multi-project users, **one verb does everything**:

```bash
empirica projects-sync --dry-run    # preview
empirica projects-sync              # commit
```

`projects-sync` is the master pipeline. It does:

1. **Discover** — walk your filesystem from `$HOME` (override with `--root`) looking for `.empirica/` directories
2. **Cache the manifest** — write to `~/.empirica/discovered_projects.yaml` so future runs are fast
3. **Upsert local registry** — register every discovered project in `~/.empirica/registry.yaml` (the daemon's served set)
4. **Push to Cortex** — `POST /v1/projects/register` for each (skipped if you don't have Cortex creds; idempotent on re-run)

That's the recommended primary entry point. The lower-level verbs (`projects-discover`, `projects-bulk-register`, etc.) stay available for fine-grained control — covered below for the cases that need them.

---

## Discovery

When you want to scan for `.empirica/` projects without committing anything:

```bash
empirica projects-discover                          # scan from $HOME, write manifest
empirica projects-discover --root ~/code --root ~/work   # multiple roots
empirica projects-discover --max-depth 3            # shallower walk
empirica projects-discover --include-hidden          # include dot-directories
```

Output is YAML by default (override with `--output json`); writes to `~/.empirica/discovered_projects.yaml` unless you pass `--manifest -` for stdout-only.

To both scan AND upsert the local registry in one pass:
```bash
empirica projects-discover --register
```

That's `projects-sync` without the Cortex push — useful for fully-local setups that don't have or want cross-project Cortex sync.

---

## Local-Only Registration

The local registry (`~/.empirica/registry.yaml`) is what the **empirica daemon** serves over HTTP at `localhost:8000`. The browser extension's Artifacts pane, cross-project searches inside the TUI cockpit, and other local-data surfaces read from here.

```bash
empirica projects-discover --register   # discover + add to registry, no Cortex push
empirica projects-list                  # show registered set
empirica daemon-list                    # what the daemon is currently serving
```

You can run a useful Empirica install with **only** local registration — Cortex push is an opt-in for cross-project + mesh features.

---

## Pushing to Cortex

If you do have Cortex credentials (`~/.empirica/credentials.yaml` or `CORTEX_REMOTE_URL` + `CORTEX_API_KEY` env vars) and want cross-project Cortex search, mesh-aware project picker, etc., add the push step:

```bash
empirica projects-bulk-register                       # push registry to Cortex
empirica projects-bulk-register --dry-run             # preview without HTTP calls
empirica projects-bulk-register --from-discovered     # source from raw scanner output
```

Idempotent: projects already on Cortex (matched by name) are skipped. Failures on individual projects are logged and the loop continues — no partial rollback.

`projects-sync` is the single-verb path that wraps this. Reach for `projects-bulk-register` directly when you want fine control over the source list (`--from MANIFEST_PATH`) or when you've discovered separately and don't want to re-scan.

---

## Selective Registration

Big workspace, want only a subset on Cortex? Use `--include` / `--exclude` regex filters:

```bash
# Only the cortex/outreach/extension cluster
empirica projects-sync --include 'empirica-(cortex|outreach|extension)' --dry-run

# Everything except archive/backup/playground/sandbox repos
empirica projects-sync --exclude 'archive|backup|playground|sandbox'

# Combined: include some, then exclude a subset
empirica projects-sync \
  --include 'empirica-' \
  --exclude 'archive|backup'
```

Both flags are **repeatable** and **OR-combined**: a project passes `--include` if ANY include pattern matches its name or path; it's dropped if ANY exclude pattern matches. Filters apply to name AND path, so you can target by either.

Always pair with `--dry-run` on first invocation — it prints the resulting set so you can verify before committing.

---

## Adding a New Project Later

Just re-run `projects-sync` (or `projects-bulk-register`):

```bash
empirica projects-sync
```

Both verbs are idempotent: existing entries are skipped; new `.empirica/` directories get registered. No special "add one" verb needed.

For a single explicit add (skipping the filesystem scan), use the lower-level CLI: `cd` into the new project, run `empirica project-init` (if not already), then `empirica projects-sync`.

---

## Updating Stale Metadata

If Cortex has out-of-date metadata on existing projects (UUID-shaped placeholder names, empty repo_url) — usually a sign of an earlier mis-registered batch — refresh with:

```bash
empirica projects-bulk-register --force-metadata-update
```

Sets `force_metadata_update: true` in each request body; Cortex's safe-update logic then backfills the metadata against your local registry. Available since v1.9.6.

---

## Pruning the Local Registry

When a project moves, gets deleted from disk, or its `.empirica/` directory disappears, the local registry still has the stale entry. Clean it up:

```bash
empirica projects-discover --register --prune
# or via the master verb:
empirica projects-sync --prune
```

`--prune` removes registry entries whose **path no longer exists** OR whose path exists but no longer contains a `.empirica/` directory. Safe — only mechanically-stale entries get dropped.

⚠️ **`--prune` cleans the LOCAL registry only.** The Cortex projects table is NOT touched. See the next section for that gap.

---

## Unregistering from Cortex (Known Gap)

**There is no Cortex-side unregister CLI today.** If you've registered a project to Cortex and later want it gone (scope was too broad, project moved tenants, project deleted), `--prune` only handles the local side. The Cortex projects table keeps the row.

**Why this matters:** for most users, this is fine — a stale Cortex project entry is invisible noise rather than a hot bug. It does mean:
- The Cortex projects list grows-only
- Cross-project search may surface archived/abandoned projects
- Users who scoped too broadly on first run can't take it back

**Current workarounds:**
- Filter at query time (`project-search --project-id <only-the-one-you-want>`)
- Cortex maintainers can manually archive on request

**Tracked for build:** the design is scoped (soft archive vs hard delete with `--purge`, cascade semantics for artifacts under archived projects, authorization model). When the cortex `/v1/projects/unregister` endpoint lands, empirica will gain a `projects-unregister` CLI mirroring the existing flag shape:

```bash
# (NOT YET BUILT — design only)
empirica projects-unregister <name-or-id>
empirica projects-unregister <name-or-id> --purge          # hard delete + cascade
empirica projects-unregister --from-discovered --exclude '<keep>'   # bulk
```

Until then, the local lifecycle is fully usable; the Cortex-side hole is in the cleanup path only.

---

## The name ↔ UUID Identity Gap

Cortex tracks projects by **two parallel identifier shapes** depending on the originating client:

| Client shape | Cortex collection key | Where it came from |
|---|---|---|
| **CLI clients** (`empirica project-init` from a terminal) | `project_<name>_*` (the project's `name` field from `project.yaml`) | Default for everyone running empirica locally |
| **Desktop / `.mcpb` clients** (Claude Desktop, etc.) | `project_<uuid>_*` (the tenant's `project_ids[0]`) | When Claude Desktop or similar provisions you via MCP bundle |

If a user has been registering through both surfaces, the **same logical project can appear as two physical Qdrant collections** — one name-keyed, one UUID-keyed. Cross-project search treats them as separate.

**Resolution path:** the **canonical-UUID tenant-DB cutover** unified this for David's tenant — bind every project's `name` to its UUID in `tenants.db`, so the two collection keys always resolve to the same logical project at query time.

**Who's still affected:** users provisioned before the cutover (MOD CLI users in particular) may still see bifurcation — same logical project, two collections, two Qdrant entries. Symptoms:
- `project-search --global` returns suspiciously duplicated hits
- The extension's project picker shows two entries with similar-but-different names/IDs
- Migrations between Cortex tenants reveal stale collection keys

**If you suspect bifurcation:**
1. Compare `empirica projects-list` (local registry) to whatever Cortex returns
2. Note which projects have both name- and UUID-keyed entries
3. Open a Cortex maintainer ticket — for now this needs cortex-side intervention to resolve cleanly

**The fix forward:** every fresh registration after the cutover gets the unified mapping automatically. Pre-cutover entries are the long tail. The `projects-unregister --purge` work above will give users a cleaner self-service path once it ships.

---

## Tenant Migrations

If you're moving a project between Cortex tenants (rare — typically a David ↔ Philipp-shaped scenario):

1. Re-register on the new tenant: `empirica projects-bulk-register --include '<your-project>'` with the **new** tenant's `CORTEX_API_KEY`
2. New entry appears on the new tenant; the **old tenant's entry stays** (no cross-tenant move primitive yet)
3. Old tenant entry becomes unreachable for you once your credentials switch — same effect as the Cortex-side unregister gap above

Until the unregister flow ships, tenant migration leaves a stale entry on the source tenant. Usually fine in practice — the entry is invisible to the user who moved, just adds row count to the source tenant.

---

## Troubleshooting

**`projects-discover` finds projects but `projects-list` is empty** — you discovered without registering. Re-run with `--register` or use `projects-sync`.

**`projects-bulk-register` skips everything** — they're already registered (idempotent — name-match). Check `empirica projects-list` to confirm.

**`projects-bulk-register` fails with "Cortex unreachable"** — verify `CORTEX_REMOTE_URL` + `CORTEX_API_KEY` or `~/.empirica/credentials.yaml`. If you don't want Cortex at all, use `projects-discover --register` instead — that path is purely local.

**Daemon serving stale set after registry update** — the daemon reads `registry.yaml` on startup. Restart: `pkill -f 'empirica serve'` then `empirica serve &`.

**Cross-project search returns duplicates** — possible name↔UUID bifurcation (see above). Compare your local registry to what Cortex returns; flag for cortex-side resolution.

---

## See Also

- **Per-project basics:** [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
- **First-time setup:** [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)
- **Daemon details:** the local HTTP daemon (`empirica serve`) serves the local registry at `localhost:8000` for surfaces like the browser extension's Artifacts pane
- **Cross-project queries:** `docs/reference/api/CROSS_PROJECT.md`
- **Architecture:** `docs/architecture/MULTI_PROJECT_STORAGE.md`
