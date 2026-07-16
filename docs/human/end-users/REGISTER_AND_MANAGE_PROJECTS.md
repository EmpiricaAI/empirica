# Register & manage Empirica projects — copy-paste set

Drop-in command blocks for the Empirica extension's copy-prompt UX (and for
humans/AIs at a terminal). Every block is copy-pastable as-is. Built against
real CLI behavior; verified for the v1.12 registration model.

**The one rule that makes this simple:** a project's id is **git-intrinsic** —
it's minted once by `project-init`, committed in `.empirica/project.yaml`, and
travels with the repo. `project-register` then **adopts** that id (it never
mints a second one). Sharing a project across machines or with Cortex never
changes its identity.

---

## Register a project

Pick the block that matches your situation.

### 1. Brand-new project (no `.empirica/` yet)

`project-init` mints the id and commits it to `.empirica/project.yaml`;
`project-register` adopts it and shares to Cortex.

```bash
empirica project-init && empirica project-register .
```

Scripted / non-interactive (extension copy-prompt):

```bash
empirica project-init --non-interactive && empirica project-register .
```

> `project-register .` on a folder that was never init'd will stop and point
> you back to `project-init` — that's why init comes first. It does **not**
> silently mint.

### 2. Already an Empirica project (id rides in the cloned repo)

The committed `.empirica/project.yaml` already carries the id — just register
this machine to it.

```bash
git clone <url> <name> && cd <name> && empirica project-register .
```

Local-only (skip Cortex — offline, or Cortex down):

```bash
empirica project-register . --no-cortex
```

### 3. No local Empirica (pure Cloud / Cowork)

When there's no repo on the machine (Desktop/Cowork seats, bursty practices),
register directly in Cortex via the MCP tool — no CLI install needed:

```
cortex_project_register(name="<display name>", slug="<tenant-scoped slug>"[, project_id="<existing-uuid>"])
```

A client-supplied `project_id` wins on first create (no shadow id). If you omit
it, Cortex mints the id and returns it with `downward_adopt: true`. **If that
project later gains a local repo, write the returned id into
`.empirica/project.yaml`** rather than re-initialising — keeps the
one-id-per-project guarantee. (There's also a no-MCP path: the Desktop
"+ Create a project" door, which registers via the same server endpoint.)

> All three blocks are live today.

---

## Manage projects

### See what's registered

```bash
empirica projects-list --output table      # or json / yaml
```

### Add / sync many at once (the "add many" power-tool)

`projects-sync` walks the filesystem, upserts the local registry, and POSTs each
project to Cortex — one verb for the whole pipeline. Keep it separate in your
head from "register one project" above; this is bulk lifecycle.

```bash
empirica projects-sync --dry-run                 # preview — no writes
empirica projects-sync                            # full sync
empirica projects-sync --include <regex>          # only matching projects
empirica projects-sync --prune                    # drop stale local registry entries
```

`--include` / `--exclude` take regexes and compose; `--prune` only touches the
local registry, never Cortex.

### Update a project's metadata

```bash
empirica project-update --project-description "..."   # edit committed project.yaml fields
```

---

## Lifecycle gaps & edge cases

These are the rough edges of the multi-project + Cortex lifecycle. Most users
never hit them; keep them in your back pocket for when a project moves, a tenant
changes, or cross-project search looks duplicated.

### Unregistering from Cortex (known gap)

**There is no Cortex-side unregister CLI today.** `projects-sync --prune` only
cleans the **local** registry — it drops entries whose path no longer exists, or
whose path exists but no longer contains a `.empirica/` directory. The Cortex
projects table is NOT touched. So if you've registered a project to Cortex and
later want it gone (scope was too broad, project moved tenants, project deleted),
the Cortex row stays.

**Why this matters:** for most users, this is fine — a stale Cortex project entry
is invisible noise rather than a hot bug. It does mean:
- The Cortex projects list grows-only
- Cross-project search may surface archived/abandoned projects
- Users who scoped too broadly on first run can't take it back

**Current workarounds:**
- Filter at query time (`project-search --project-id <only-the-one-you-want>`)
- Cortex maintainers can manually archive on request

**Tracked for build:** the design is scoped (soft archive vs hard delete with
`--purge`, cascade semantics for artifacts under archived projects, authorization
model). When the cortex unregister endpoint lands, empirica will gain a
`projects-unregister` CLI mirroring the existing flag shape:

```bash
# (NOT YET BUILT — design only)
empirica projects-unregister <name-or-id>
empirica projects-unregister <name-or-id> --purge          # hard delete + cascade
empirica projects-unregister --from-discovered --exclude '<keep>'   # bulk
```

Until then, the local lifecycle is fully usable; the Cortex-side hole is in the
cleanup path only.

### The name ↔ UUID Identity Gap

Cortex tracks projects by **two parallel identifier shapes** depending on the
originating client:

| Client shape | Cortex collection key | Where it came from |
|---|---|---|
| **CLI clients** (`empirica project-init` from a terminal) | `project_<name>_*` (the project's `name` field from `project.yaml`) | Default for everyone running empirica locally |
| **Desktop / `.mcpb` clients** (Claude Desktop, etc.) | `project_<uuid>_*` (the tenant's `project_ids[0]`) | When Claude Desktop or similar provisions you via MCP bundle |

If a user has been registering through both surfaces, the **same logical project
can appear as two physical Qdrant collections** — one name-keyed, one UUID-keyed.
Cross-project search treats them as separate.

**Resolution path:** the **canonical-UUID tenant-DB cutover** unified this for
David's tenant — bind every project's `name` to its UUID in `tenants.db`, so the
two collection keys always resolve to the same logical project at query time.

**Who's still affected:** users provisioned before the cutover (MOD CLI users in
particular) may still see bifurcation — same logical project, two collections,
two Qdrant entries. Symptoms:
- `project-search --global` returns suspiciously duplicated hits
- The extension's project picker shows two entries with similar-but-different names/IDs
- Migrations between Cortex tenants reveal stale collection keys

**If you suspect bifurcation:**
1. Compare `empirica projects-list` (local registry) to whatever Cortex returns
2. Note which projects have both name- and UUID-keyed entries
3. Open a Cortex maintainer ticket — for now this needs cortex-side intervention to resolve cleanly

**The fix forward:** every fresh registration after the cutover gets the unified
mapping automatically. Pre-cutover entries are the long tail. The
`projects-unregister --purge` work above will give users a cleaner self-service
path once it ships.

### Tenant migrations

If you're moving a project between Cortex tenants (rare — typically a David ↔
Philipp-shaped scenario):

1. Re-register on the new tenant: `empirica projects-sync --include '<your-project>'`
   with the **new** tenant's `CORTEX_API_KEY`
2. New entry appears on the new tenant; the **old tenant's entry stays** (no
   cross-tenant move primitive yet)
3. Old tenant entry becomes unreachable for you once your credentials switch —
   same effect as the Cortex-side unregister gap above

Until the unregister flow ships, tenant migration leaves a stale entry on the
source tenant. Usually fine in practice — the entry is invisible to the user who
moved, just adds row count to the source tenant.

---

## Addressing a project / practice on the mesh

Peers and proposals address a practice by its **canonical 3-form**:
`<org>.<tenant>.<project-slug>`. Tenant-scoped slugs mean the same slug under
two tenants are distinct by construction — `david.empirica-outreach` and
`philipp.empirica-outreach` never collide (enforced by `UNIQUE(slug,
owner_user_id)` + adopt-id).

**Watch the "doubled empirica".** Repos slugged `empirica-*` produce a 3-form
where `empirica` appears twice — once as the org, once as the project-slug
prefix. This is correct; do not strip the second one:

| Practice | Canonical 3-form |
|---|---|
| David's outreach | `empirica.david.empirica-outreach` |
| Philipp's outreach | `empirica.philipp.empirica-outreach` |
| Philipp's mesh-support | `empirica.philipp.empirica-mesh-support` |
| (no-prefix exception) | `empirica.philipp.usw` |

When unsure of the exact form, `empirica practice-context --ai-id <slug>` prints
the canonical `ai_id_mesh` to emit — no guessing.

---

## What changes with the v1.12 registration model

- `project-register` **adopts** the committed id (already true today); on a
  within-tenant slug collision Cortex returns a clear "slug taken" error instead
  of silently creating a suffixed duplicate.
- Dedup/reconcile is a one-time migration concern, not part of normal use — in
  steady state, registering a project creates no duplicates.
