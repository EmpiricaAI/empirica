# Changelog

All notable changes to Empirica will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.12.31] — 2026-07-21

Patch: a practice-misbind fix for non-Claude-Code harnesses, cockpit support for
Claude Code 2.1.x, a statusline context meter, and release/CI-tooling hardening.

### Fixed
- **Practice/ai_id misbind on harnesses that don't set `EMPIRICA_CWD_RELIABLE`**
  (codex, ecodex). `get_active_project_path` fell to a stale `instance_projects`
  mapping and bound a session to the wrong practice (a session in one project
  resolving to another, with a frozen other-session vector snapshot). A
  stale-mapping guard now trusts the cwd when it's a registered project ROOT that
  differs from the mapping (physical ground truth, harness-agnostic). Claude Code
  is unaffected — it sets `EMPIRICA_CWD_RELIABLE` and resolves at Priority -1.
- **Cockpit didn't recognize Claude Code 2.1.x seats.** CC 2.1.x sets the OS
  process name to a bare version string (e.g. `2.1.212`), so `_is_claude_proc`
  filtered live seats out before ever checking the cmdline — the cockpit showed
  partial seat lists and `instance rebind` failed. Now also matches `cmdline[0]`'s
  basename, which stays `claude` regardless of the name mangling (otherwise recurs
  on every CC version bump).
- **`release_chain` compliance check read PyPI from pip's local cache**, so it
  reported a freshly-published version as "missing" for minutes after
  `release.py --publish`. Now queries PyPI's live JSON API.

### Added
- **Statusline context meter** — a bracketed-cell meter (`[####------] 42%`) on
  the right of the empirica statusline, fed by Claude Code's
  `context_window.used_percentage`, with green/yellow/red tiering. Renders nothing
  when the harness supplies no usage.

### Changed
- **Dependabot targets `develop`** (all 3 ecosystems) — bumps land on the trunk,
  get CI-tested there, and flow to main via release, instead of landing on the
  default branch and needing a manual back-port.
- **SessionStart skips re-arming the mesh Monitor when a live tail already exists**
  — stops compaction / new-session-init re-fires from stacking duplicate
  wake-delivery tails (one seat had accumulated 5).

## [1.12.30] — 2026-07-21

Patch: POSTFLIGHT returns ~3s faster, `project-embed` made incremental, a
single-practitioner onboarding verb, a Sentinel over-gating fix, and a new
system-altitude architecture-review skill.

### Added
- **`empirica provision-practice <name>`** — one-command single-practitioner
  onboarding (mkdir → project-init → patch ai_id/tenant/org → project-register →
  optional forgejo backup). The lightweight sibling of mesh-support's bulk
  provisioner. `--base-path` defaults to `~/empirica`; the forgejo host comes from
  `--forgejo-host` or `EMPIRICA_FORGEJO_HOST` (no built-in default).
- **`/architecture-review` skill** — system-design review at an altitude
  `/code-audit` + `/eat-the-broccoli` don't cover (failure domains, scalability,
  data, security, ops, cost, complexity budget). Informational + foresight only:
  it surfaces failure scenarios and options for the human architect to weigh, and
  never gates or owns the decision.
- **Cockpit groups-mode**: ghostty surface + per-pane titling; one terminal window
  per config (alacritty/ghostty), with a graceful fallback when the binary isn't
  on PATH.

### Performance
- **POSTFLIGHT returns ~3s faster.** The storage pipeline (embeds + global sync —
  ~5.7s of pure side-effect the response never consumes) now runs **detached** off
  the critical path, via a fire-and-forget worker with a synchronous fallback so
  the work is never dropped. Opt-in per-stage timing via `EMPIRICA_POSTFLIGHT_TIMING`.
- **`project-embed` is incremental.** Only new/changed eidetic findings are
  re-embedded (was O(all findings) — >30s on a local embedder). The session-end
  auto-embed is fire-and-forget so a hook timeout can't truncate it mid-run.

### Fixed
- **Sentinel classifies read-only `tailscale` verbs as noetic** (`status`,
  `netcheck`, `ip`, `version`, `whois`) — they no longer force an unneeded CHECK
  when inspecting the tailnet; mutating subcommands (`up`/`set`/`down`/…) stay
  praxic-gated.
- **`provision-practice` no longer ships a hardcoded private forgejo host** as the
  `--forgejo-host` default — it's env/flag-driven, so the shipped package carries
  no one operator's infra.

### Changed
- **`actions/setup-python` 6 → 7** in CI (dependabot).
- **eat-the-broccoli Phase 3**: rank findings by blast radius, then cut — calibrate
  severity to the stated context (8 prioritized findings beat 30).

## [1.12.29] — 2026-07-19

Corrective patch: greens the trunk (1.12.28 shipped with a stale test that reddened
main) and folds in release-tooling + mesh-loop hardening.

### Fixed
- **`test_org_parent_map` aligned to the metadata-based `parent_org` model.** The
  1.12.28 ERM change made `get_org_parent_map()` read
  `entity_registry.metadata.parent_org` instead of org→org membership edges, but
  the test still asserted the removed behavior — leaving `main` red. Tests
  rewritten to the current model (+ a guard that membership edges are not
  parentage).
- **`cortex-mailbox-poll` is opt-in only — never auto-queued.** Wake-on-event (the
  persistent listener) is the canonical mesh trigger; the 30s poller is redundant
  on wake-on-event seats and is only for harnesses that can't do wake-on-event,
  where the user opts in via `empirica loop register`. Fixes an ai_id/session-id
  key mismatch that re-offered it every session (#360) and codifies the opt-in
  rule (#361). `message-cleanup` housekeeping still auto-installs.

### Changed
- **`release.py --version-only --commit`** stages an explicit version/packaging
  allowlist + CHANGELOG and commits the bump — removing the manual `git add -A`
  that could sweep a concurrent session's uncommitted work into a release commit.
  `create_git_tag` now shares the same allowlist (single source of truth). (#359)

### Docs
- Corrected `CROSS_PROJECT.md` visibility default (default is `shared`, not `local`;
  `local` is a no-egress tier) and `CONTRIBUTING.md` release step (`--commit`).

## [1.12.28] — 2026-07-19

### Changed
- **`visibility=local` is now a true no-egress tier.** Artifacts tagged
  `visibility=local` are excluded from every cortex sync path (POSTFLIGHT delta
  + graph extractors, session-init breadcrumb delta) — their content never
  leaves the machine, even with a Cortex account connected. Default `shared`
  and `public` still sync. Makes `local` mean on-device, not just
  not-cross-shared. ([#358](https://github.com/EmpiricaAI/empirica/pull/358))

### Fixed
- **README "What's New" no longer truncates multi-line CHANGELOG bullets.**
  `release.py sync_readme_whats_new` kept only lines starting with `- **` and
  dropped wrapped continuation lines, cutting each bullet at its first physical
  line (the 1.12.27 split-brain entry rendered as "…display the correct" with
  the rest lost). It now joins continuations. Also tightened the README mesh
  framing to state plainly that the cross-AI mesh requires Cortex.

## [1.12.27] — 2026-07-18

### Fixed
- **Split-brain project persistence (P0).** A session could *display* the correct
  project while *persisting* its session row + artifacts to a stale one — silently
  contaminating findings, goals, and calibration across projects (verified
  diagnosis, Franci/NLE). Four fixes: (1) session-init now pins the session with an
  explicit `--project-id` resolved from the cwd-validated `project_root` instead of
  letting session-create re-resolve it; (2)
  `session_create._resolve_from_context_files` gates the generic
  `~/.empirica/active_work.json` read on `is_headless()`, matching
  `session_resolver`'s canonical "HEADLESS MODE ONLY" invariant — a divergent copy
  had let a stale global fallback outrank cwd in interactive sessions; (3) both
  session-init healers tolerate BOTH `<root>` and `<root>/.empirica`
  `trajectory_path` forms (the exact-match miss had defeated the ghost-project_id
  heal); (4) a residual wrong binding is surfaced loudly (`split_brain_corrected`)
  rather than silently healed. +12 regression tests. (#357)

## [1.12.26] — 2026-07-18

### Fixed
- **`mailbox poll --ai-id <canonical>` no longer returns an empty inbox.**
  `content_poll._resolve_canonical_ai_id` round-tripped an already-canonical ai_id
  through the roster (which can't match a canonical / not-yet-synced practice) and
  fell back to a mismatched id → cortex returned 0. Now idempotent: a dotted
  (canonical) ai_id passes through untouched.
- **`project-init --force` merges `project.yaml` instead of clobbering it** —
  hand-set identity fields (org_id, tenant_slug, canonical_seat, rich description)
  survive a re-init.
- **`project-init` no longer mints a shadow UUID** — `create_project` threads an
  explicit/reused `project_id` so the sessions.db row materializes under the id
  `project.yaml` claims.

### Added
- **Prevention-measurement substrate** — pre-registered causal-measurement spec
  (S1) + `prevention_events` emission with POSTFLIGHT detection (S2) + recurrence
  oracle (S3) + fabrication-incidence as a 2nd outcome family (S4) + a read-only
  per-family measurement report surface (S5).
- **Registry name-collision shadow guard** — warns when two registry entries share
  a name but differ in path (backup/clone shadow), plus a CI-parity pre-push hook
  (ruff check + format) composable with beads.
- **Mesh `idempotency_key()` helper** + propose-discipline guidance for
  `cortex_propose` (idempotent-by-design convention).
- **`entity_memberships.is_primary`** disambiguation flag (ERM).

### Changed
- **`empirica --help` surfaces the Knowledge Sources command family.**
- README / cross-ai-mesh doc-anchor repairs.

## [1.12.25] — 2026-07-17

### Added
- **`goals-create --engagement-id`** — stamps the existing `goals.engagement_id`
  column (migration_051): the core half of the goal↔engagement linkage. Goals
  are core's table, so this by-id reference is core's lane; the engagements
  schema and the `entity_artifacts` junction the daemon reads (ratified boundary
  §4) stay workspace's — workspace projects the ref to the junction as the
  companion write. Additive / nullable (unset ⇒ NULL).

### Fixed
- **Loop-install no longer re-offers event-drivable loops as CronCreate pollers
  on wake-on-event seats.** Gate 5 skips `systemd-user` canonical loops (e.g.
  `cortex-mailbox-poll`) when a listener is already armed, and `scheduler_kind`
  is now passed through to `write_pending` (it was silently defaulting to
  `cron-create`). Stops the redundant-poller reappearance every session.
- **entity-create self-heal restored for org-detail rows** — the CI-safe version,
  with an `organizations`-table-exists guard so it can't crash core's test env.

### Changed
- **`MESH_CONCEPTS.md` gains the "sync engine — the DAW frame" capstone** — the
  mesh as Ableton Live / Serato for epistemically-aware AIs, syncing across
  practices, harnesses, substrates, model types, tenants, and orgs, with
  calibration as the shared beatgrid.

## [1.12.24] — 2026-07-17

The CLI-consolidation release: a substantial surface prune, a new anti-drift CI
gate, doc alignment, mesh-listener hardening, and a security floor bump.

### Removed
- **CLI surface prune: 28 dead / unwired / subsumed verbs removed (210 → 182).**
  The agent-orchestration suite (`agent-spawn` / `agent-report` / `agent-aggregate`
  and kin — superseded by provider-native multi-agent), the persona / lesson-replay /
  domain-inspection subsystems (built but never wired), and a set of subsumed one-off
  verbs. Direct clients are notified on ecosystem update (#348).

### Added
- **CI: parser↔prompt drift check.** `scripts/check_prompt_parser_drift.py`
  introspects the live CLI verb set and scans the AI-facing prompt corpus (skills,
  templates, and `project_skills/*.yaml`) for `empirica <verb>` references, failing
  CI when a prompt names a verb the parser no longer has — the command-not-found trap
  a verb prune can otherwise leave behind (#352).

### Changed
- **Docs aligned to the post-prune reality (−2.6K lines):** removed references to the
  pruned subsystems, de-duplicated overlapping guides, regenerated
  `CLI_COMMANDS_UNIFIED.md` (#349).
- **The *lesson* vocabulary sharpened** to a transferable cross-practice
  pattern/anti-pattern — distinct from a finding/decision (local state): "findings
  describe, lessons transfer." Propagated across the system prompt, skills, and
  `TAXONOMY.md` (#353).
- **Read-only mesh lookups classified as noetic** by the Sentinel — `empirica mesh
  status`, `mesh diagnose`, and `status` no longer gate as praxic. The `goals-list`
  footer now surfaces the result cap and points to `--all-projects` (#354).

### Fixed
- **Mesh listener never falls back to the retired `orchestration-events` ntfy topic**
  (which has no ACL and 403-storms). The last-good topic is cached to
  `~/.empirica/listener_topic_<ai_id>`, and cortex's `retired_channels` field is
  consumed as the authoritative retired-topic list (#350, #351).
- **Test isolation: a leaked subscriber daemon** in the message-store tests (its
  stop-event was only set inside a callback) could poll `git for-each-ref …/messages/`
  into a *later* test's subprocess mock — an order-dependent CI flake. Fixed with
  unconditional teardown.
- Removed a dangling `empirica agent-spawn` reference (a pruned verb) from a loaded
  skill fixture, and closed the drift-check's own blind spot (positional-arg and
  two-word-subcommand invocation shapes, plus the `project_skills/*.yaml` corpus).

### Security
- **`mcp` dependency floor bumped to `>=1.28.1`** — resolves CVE-2026-52869 and
  CVE-2026-52870 (fixed in 1.27.2) and CVE-2026-59950 (fixed in 1.28.1). Applied to
  both the root `mcp` extra and the empirica-mcp SDK pin.

## [1.12.22] — 2026-07-14

### Added
- **`artifacts-generate --format okf` — export the epistemic graph as an Open
  Knowledge Format bundle.** OKF (Google Cloud, 2026) is a portable, vendor-neutral
  markdown format: a directory of concept files with YAML frontmatter, linked by
  markdown links. `--format okf` projects each empirica artifact (finding, decision,
  unknown, dead-end, mistake, assumption, goal) into one OKF concept file with the
  required `type` field plus `title`/`description`/`tags`/`timestamp`, and an
  `index.md`. Empirica's calibration layer — impact, confidence, provenance — rides
  as `empirica_*` frontmatter **extension keys** (OKF permits arbitrary keys), so
  OKF-aware consumers get the calibrated graph and plain OKF consumers still get a
  valid bundle. This is the "knowledge out" interop surface: the graph other agents
  and tools can consume without an Empirica account. `--format audit` (the browsable
  per-type trail) remains the default.
- **`empirica serve` self-heals on version drift.** After a pip/editable upgrade
  the daemon kept serving stale code until manually restarted. It now detects the
  drift (in-process `__version__` vs installed dist-info) and (1) **always surfaces
  it on `GET /health`** as a `version_drift` field, so an operator, the extension, or
  ecosystem-update can prompt a restart; and (2) **self-exits gracefully only when
  supervised** (systemd `INVOCATION_ID`, or `EMPIRICA_SERVE_DRIFT_EXIT=1`) so the
  supervisor relaunches against the new code. Serve is often standalone, so a blind
  self-exit would kill an unsupervised daemon — the surface-always / exit-when-
  supervised split is deliberate. The version compare is now shared with the mesh
  listener via `core.version_drift`. Watch interval: `EMPIRICA_SERVE_DRIFT_CHECK_SEC`
  (default 60s; `0` disables the watcher, `/health` still reports live).
- **Calibration tuning: stance presets + defer-to-boundary.** The Sentinel-tuning
  surface (`GET/PATCH /api/v1/calibration/config`) gains two things. (1) **Stance
  presets** (`rigorous`/`balanced`/`exploratory`) as an axis orthogonal to the
  domain personas: stance owns the two gate thresholds (`ready_uncertainty`,
  `engagement_gate` — how strictly a practice gates), personas keep owning weights
  and soft thresholds (what a practice focuses on). Presets are now typed
  `{stance, persona}`; the effective config carries both. (2) **Defer-to-boundary**:
  a practice-scoped tuning change made while a transaction is open is accepted but
  **queued and applied at the next PREFLIGHT**, never shifting the calibration
  signal under work already in flight. `GET` surfaces `active_transaction` and any
  `pending` (queued) override.

### Changed
- **Engagement taxonomy → canonical-4.** The vendored engagement substrate seed
  moves to the canonical four business functions — `outreach`, `communication`,
  `support`, `financial` — for parity with the empirica-workspace canonical seed.
  `sales` folds into `outreach`; `security`/`infra`/`onboarding` are re-homed as
  stages under `support` (13 stages total). Affects fresh-install seeds only.

### Security
- **Dependency CVE bumps:** `httplib2>=0.32.0` (CVE-2026-59939, unbounded
  decompression; transitive via google-api-python-client) and `setuptools>=83.0.0`
  (CVE-2026-59890). `pip-audit` clean.

## [1.12.21] — 2026-07-13

### Fixed
- **`source-get` handles raw binary bodies.** A media source's bytes are served as a raw
  byte body (with the content hash in a response header), not a base64 JSON envelope.
  `source-get` was assuming the JSON shape and crashed decoding raw image bytes as UTF-8
  (`'utf-8' codec can't decode byte 0x89`). It now branches on the response content type:
  raw bytes are written directly and hash-verified; the JSON-envelope path still works for
  sources that return it. The verify-before-write guarantee is unchanged.

### Added
- **Shared sources reach the shared catalogue: `source-add --visibility shared|public`
  registers into the catalogue mesh peers and the extension read.** Previously a non-media
  shared source was written only to the local store, so peers never saw it — the source
  surface split into two disconnected populations. `source-add` now registers a
  shared/public source into the canonical catalogue at add time (local-visibility and media
  sources are unaffected — media already registers via its body upload). Requires cortex to
  be configured; a no-op otherwise.
- **`sources-reconcile --register-shared`** backfills shared/public sources created before
  the register wiring — it registers each one that lacks a catalogue id and stamps the id
  back locally, **committing per source** so a mid-run interruption keeps prior progress.

## [1.12.20] — 2026-07-13

### Added
- **Media-bearing sources: `source-add --media <path>` + `source-get --id <uuid> --out <path>`.**
  Register a local media/binary file (image, etc.) and upload it so mesh peers can fetch
  it cross-tenant; `source-get` downloads by uuid, **verifies the SHA-256 content hash
  before writing** to `--out` (a mismatch never writes). Image bytes retain automatically;
  cross-tenant reads are governed by the source's `--visibility` (`shared`/`public`).
- **`engagement-update`** — update an engagement's title, description, domain, stage, or
  lifecycle-state from the CLI.
- **`setup-claude-code` pre-authorizes Empirica's MCP servers** in the Claude Code
  `permissions.allow` list it deploys, so mesh tool calls no longer trigger a per-tool
  permission prompt. The real authorization gates (the Sentinel for local praxic tools,
  mesh review for cross-practice work) are unchanged — this only removes redundant prompts.
- **Inline source citation: `finding-log --cite "<title>" [--cite-url <url>] [--cite-type <type>]`.**
  Creates the source AND links it (`sourced_from` edge) in one call — killing the
  two-step `source-add` → `--source <id>` dance that kept sources under-logged
  (60-for-9000). `breadcrumbs.create_source` writes a minimal source row (title/url/
  type/visibility; file-identity columns stay NULL — that's the `source-add --path`
  path's job), and the fresh id links like any `--source`. Combined with the
  `sourced_from` edge fix (previous release), a cited finding is now a first-class,
  connected, one-command act.

### Fixed
- **Airtight project-identity convergence: `project-register --reconcile`.** A project's
  local id could drift from its registered canonical id — invisible until the first
  operation that passes a raw project id across the boundary (a media upload rejected with
  "unknown project_id"). `project-register` now detects the divergence, **warns by default**,
  and with `--reconcile` rekeys **every** local store together (session/artifact DBs,
  workspace registry, `registry.yaml`, `project.yaml`) to the canonical id, then directs a
  `rebuild --qdrant`. The all-stores-at-once rekey is deliberate — a partial correction is
  what historically stranded live sessions under a phantom id. Guarded against running
  mid-session (refuses under an open transaction unless `--force`).
- **`source-add --media` fails loud when the blob upload fails** — non-zero exit + surfaced
  error, instead of a silent success that left a source row with no retrievable content.
- **`finding-log --source` now writes a canonical `sourced_from` graph edge, not
  only the `source_refs` column.** Citing a source historically serialized the ids
  into the `source_refs` column — invisible to the artifact graph (weave-gate,
  connectivity, traversal) and to `sources-map`. So a practice could cite dozens of
  sources and still show **0** `sourced_from` edges (empirica's own graph did exactly
  that: 60 sources, 0 edges). `_attach_sources` now writes the `sourced_from` edge at
  log time (idempotent, best-effort) so a citation is a first-class graph link; the
  column stays as the ordered list for backward compatibility.
- **Weave-gate no longer false-blocks the disciplined goal-per-transaction flow.**
  The gate enforces graph connectivity at CHECK, but the structural `attached_to`
  (artifact→goal) edge was written only at POSTFLIGHT — so an artifact logged under
  an active goal read as *unconnected* at CHECK and the gate blocked, forcing a
  hand-wired edge just to proceed (then POSTFLIGHT wrote the goal-edge anyway). Every
  `log_*` method now materializes the goal-attachment edge **at log time** (idempotent
  `INSERT OR IGNORE`, best-effort — a connectivity write never fails a log). Goal
  attachment is now live in the graph immediately, so the gate counts it as the real
  edge it is; POSTFLIGHT's write becomes a redundant backstop. The note's promise that
  "structural goal-edges are automatic" is finally true at CHECK, not just after it.
  The reverse order is covered too: `goals-create` now **backward-wires** — it attaches
  this transaction's already-logged orphan artifacts (logged before the goal existed) to
  the new goal, so both `goal→log` and `log→goal` orders connect (`attached_orphans` in
  the result reports how many). Only artifacts with no existing `attached_to` edge are
  touched, so one already bound to another goal is left alone.
- **Relation-vocabulary drift closed.** The single-verb path (`*-log --related-to` /
  `--edge ID`) writes `related` edges freely and the DB holds 81 of them, but the
  `log-artifacts` batch validator rejected `related` — an edge one path writes and the
  other refuses. `related` (the generic low-friction anchor) is now admitted to
  `VALID_RELATIONS`; semantic relations stay preferred where the relationship is known.

### Changed
- **Batch artifact verbs are now the documented default for multi-artifact work.**
  The collaborative-mode guidance shifts from a "≥3 related artifacts" threshold to
  batch-first: default to `log-artifacts` / `resolve-artifacts` / `delete-artifacts`
  for any 2+ artifacts or graph-connected cluster; single `*-log`/`*-resolve` verbs
  are for genuinely-single artifacts. The `/epistemic-gardening` skill gains the
  three-register distinction — **surgical** (per-artifact judgment, the human-facing
  default), **batch-by-graph** (an AI resolving a connected cluster in one call), and
  **mass-policy** (filter-and-bulk, a deliberate backlog tool used only with explicit
  human sign-off on the policy, never routine).

### Security
- **Sentinel gate no longer bypassed by command substitution inside double
  quotes.** `$(...)` and backticks execute even *inside* double quotes (only
  single quotes suppress them), but `_has_dangerous_operators` scanned only
  *outside* quotes — so `echo "$(rm -rf /)"`, `cat "$(rm)"`, `PAT="$(rm)"`, and
  the backtick forms classified **noetic** and ran unguarded in the
  single-command and pipe paths. (The chain path was already safe — it
  extracts+validates substitutions per segment.) Every substitution's inner
  command is now validated up front (quote-agnostic, before the pipe check so
  `echo "$(rm)" | cat` is covered too): an unsafe inner (`rm`, `curl | sh`)
  gates in any position, while a read-only inner (`$(date)`, `$(git log)`,
  `$(whoami)`) still passes. Surfaced while closing over-gating gaps during the
  gardening work.

### Fixed
- **Quoted variable assignments with spaces no longer over-gate.** `PAT="a b c"`
  / `MSG='hello world'` gated as praxic because `_is_inert_shape`'s regex forbade
  whitespace in the RHS; now a quoted literal RHS is recognized as inert (any
  substitution in it is caught by the security check above). Hit during the
  gardening survey (`PAT="(finding LIKE …)"`).
- **Sentinel classifier parity + workflow-verb completeness** (four over-gating
  gaps surfaced by the first `/epistemic-gardening` pass — all the same "read-only
  op trusted in one syntactic position but not another" class as the pipe-parity
  fix). (1) **Help/version queries** now classify noetic regardless of verb — a
  non-tiered verb like `goals-archive --help` no longer gates just because the
  verb mutates when actually run (quote-aware via shlex, so a `--help` inside a
  quoted argument can't wave a mutating command through). (2) **`_is_segment_safe`
  reached parity with the single-command path** — a read-only `sqlite3` query or
  `python3 -c` inspection as a *chain segment* (`echo x && sqlite3 db "SELECT …"`,
  newline-chained surveys) is now noetic, not just when standalone or piped;
  extracted the shared `_is_readonly_command_form` so both paths classify
  identically. (3) The **single-command path** now excuses inert shell shapes
  (bare `VAR=value`, `[ test ]`, `exit N`) like the chain-segment path already
  did. (4) **`finding-resolve`, `goals-archive`, `goals-reopen`, `goals-activate`
  added to the TIER2 workflow whitelist** — epistemic-hygiene verbs whose siblings
  (`unknown-resolve`, `goals-complete`, `goals-prune`) were already there, so
  gardening no longer needs a CHECK to resolve a finding or archive a goal. All
  mutating forms (sqlite writes, `rm` in a chain, mutating python) stay gated.

## [1.12.18] — 2026-07-12

### Added
- **`finding-resolve` — the resolve/prune verb findings never had (#307).**
  Findings were the only artifact type with no way to close them: unknowns,
  assumptions, and goals all have resolve verbs, but a stale or superseded
  finding could only *passively decay*. Recency-decay detects age, not
  supersession — a two-month-old finding still semantically strong keeps
  resurfacing high in PREFLIGHT/CHECK, and a superseded one never sinks fast
  enough. `empirica finding-resolve <id> --resolution <why> [--superseded-by
  <id>]` marks a finding resolved (kept for history, dropped from live
  retrieval), mirroring `project_unknowns.is_resolved`; `--superseded-by` links
  it to the finding that replaced it. `resolve-artifacts` batch gains a
  `type="finding"` branch. New columns (`is_resolved`, `resolution`,
  `resolved_timestamp`, `superseded_by`) land via migration 057 — additive,
  nullable, idempotent.
- **Resolved findings are dropped from both attention-injection surfaces.**
  (1) The Qdrant PREFLIGHT/CHECK `relevant_findings` teaser reconciles against
  live SQLite at read time (`_reconcile_findings_against_sqlite`) — Qdrant's
  `is_resolved` payload is frozen at embed time, so a finding resolved *after*
  embedding still reads unresolved there; the read-time reconcile is the fix
  (same pattern as goal reconciliation), matching by `artifact_id` with a
  text-prefix fallback for pre-#307 embeds. (2) The breadcrumbs post-compact
  `EPISTEMIC FOCUS` block's `recent_findings` query gained an `is_resolved`
  filter (the sibling unknowns query already had one), schema-guarded for DBs
  predating migration 057. Both paths are best-effort — any failure returns the
  raw set, never breaking the retrieval hot-path.
- **`/epistemic-gardening` skill — the graph-hygiene pass.** Operationalizes
  `ARTIFACT_HYGIENE.md` (which framed itself as "an orchestration + policy problem,
  not build-from-scratch"). A PRAXIC pass that de-weeds a practice's epistemic
  graph so retrieval surfaces what's live, not what's rotted: the resolve ▸ archive
  ▸ delete preference order, a six-phase workflow (PREFLIGHT → survey → CHECK →
  triage+act via batch verbs → verify retrieval → POSTFLIGHT), a per-artifact-type
  playbook, and the cross-practice mesh-propagation pattern (collab the pass,
  propose peers run it, SER for a fleet-wide sweep — never prune a peer's graph for
  them). Now expressible end-to-end because `finding-resolve` closed the last gap
  in the resolve-verb surface and read-time reconciliation makes resolution
  actually land in retrieval.

### Fixed
- **Sentinel no longer over-gates read-only pipe chains — receiver trust
  brought to parity with standalone trust.** `is_safe_pipe_chain` kept two
  drifted trust lists: `SAFE_BASH_PREFIXES` (~95 read-only tools trusted as
  standalone commands) vs `SAFE_PIPE_TARGETS` (~20 trusted as pipe receivers).
  A tool trusted alone became *untrusted the instant it received a pipe*, so the
  canonical read-only DB-inspection idiom `sqlite3 db 'SELECT…' | column -t`
  (and `… | nl`, `… | bat`, `… | gron`, `… | tac`) got false-gated as praxic —
  the flag-robust sqlite fix (#304) made the *source* noetic but the formatter
  on the tail re-gated the whole chain. Every pipe segment now runs through the
  same read-only classifier used for standalone commands (`_matches_safe_prefix`,
  which applies `_has_dangerous_tool_flags`). This brings receiver trust to
  parity **and** closes a latent hole: a mutating/exec flag mid-pipe
  (`| sort -o out`, `| yq -i`, `| awk 'system()'`, `| fd -x`) is now rejected in
  ANY position, and a redirect on a receiver (`| column > out`) is caught at the
  segment level too. Executor-ish legacy receivers (`python3 -c`, `xargs`) stay
  receiver-only — never granted to the pipe *source*, so `python3 -c '…' | cat`
  remains praxic.

## [1.12.17] — 2026-07-11

### Fixed
- **Push listener no longer crashes on a `loop_state` write race (silent
  mesh-connectivity loss).** `content_poll.save_state` used a *deterministic*
  `<name>.tmp` path, so two concurrent writers on the same per-loop state file
  (overlapping listener threads/processes) raced: one's `os.replace` unlinked the
  other's tmp, and the loser raised an unhandled `FileNotFoundError` that took
  down the entire `empirica loop listen` push listener — dropping mesh
  connectivity until someone re-armed. Now each write uses a **unique**
  `tempfile.mkstemp` tmp (matching `loop_registry`'s writer, eliminating the
  shared-tmp contention) and the persist is wrapped best-effort so a state-write
  hiccup can never crash the listener. Reported live by extension.
- **Daemon `/entities` accepts `org_id` as an additive alias for `parent_org`.**
  Extension's probe reported the CRM GET routes "ignore scoping params" — the
  real cause was a param-name mismatch + FastAPI silently dropping unknown query
  params (a fail-open footgun, not a within-contract leak: each route already
  scopes, and `/goals` has honest-empty anti-leak discipline). `GET /api/v1/entities`
  now accepts `org_id` (additive, `parent_org` wins if both given), killing the
  exact silent-drop case. The full cross-route scoping contract is a workspace-owned
  ERM decision (routes scope by different relationships), tracked in the ERM SER.
- **Retrieval hygiene: PREFLIGHT/CHECK goal teaser reconciled against live SQLite.**
  The Qdrant-backed goals teaser served `status` straight from the point payload
  (embedded at index time), so a goal completed/reopened in SQLite kept surfacing as
  `in_progress` until its point was re-embedded — retrieval drifting from reality
  (retrieval-as-pollution). Retrieved goals are now reconciled against the
  authoritative local `goals` table before display: **completed goals are dropped,
  stale status is corrected, and cross-project goals (absent locally) are kept
  unchanged**. Best-effort + fail-open (returns the raw list on any error — the
  hot-path never breaks). Wired at both build points (preflight `related_goals` +
  check `active_goals`). Cross-project *status* correction is deferred (needs the
  owning project's DB).
- **Sentinel: flag-robust read-only `sqlite3` classification.** `is_safe_sqlite_command`
  assumed the db path was the first arg after `sqlite3`, so any flag before it
  (`-header`, `-separator ' | '`, `-json`, `-line`) broke the regex match and fell
  through to *gated-as-praxic* — false positives that forced reroutes on pure read-only
  DB inspection. Rewritten with `shlex` tokenization: skip leading flags (incl
  value-taking `-separator`/`-cmd`/`-init`), take the query as the arg after the db path
  (so a trailing `2>/dev/null` no longer fools it), allow `WITH` (CTE reads), and keep a
  write-keyword backstop that also catches writable CTEs. Writes, file-writing meta
  (`.output`/`.import`/…), and interactive REPL stay praxic.

### Changed
- **Post-compact injection trimmed to the dynamic payload (attention-budget hygiene).**
  The post-compact re-grounding prompt was wrapping the dynamic data (pre-compact
  vectors, last task, decay-ranked epistemic focus, memory) in ~30–40 lines that
  duplicated the system prompts + `/epistemic-transaction` skill — which are
  re-loaded every turn anyway: the "compaction is routine + lossless" framing, the
  full `preflight-submit`/`check-submit` bash templates, the per-vector glossary,
  and numbered command walkthroughs. Re-injecting prompt-layer content just burns
  attention budget. All three re-grounding prompts (new-session, CHECK-gate,
  transaction-continue) now carry **only** the dynamic data + a one-line action cue
  (the lossless framing is compressed to one line, not deleted — measuring whether
  even that's needed). ~55% fewer wrapper lines, zero data lost. A regression guard
  (`test_post_compact_injection_lean`) keeps the surface from re-bloating.

### Added
- **ERM §6.2 — entities as first-class searchable vector points.** `entity_registry`
  rows (contact/organization/engagement) are now embedded as their own
  `point_kind="entity"` points in the `workspace_index` collection, alongside the
  existing artifact pointers under one shared entity-filter contract — enabling
  **semantic entity retrieval**, zero-artifact-entity visibility, and unified
  "row + its artifacts" search (via a self-referential entity tag). Surfaces:
  `entity-search --semantic` (CLI), `GET /api/v1/entities?q=` (daemon, ranked
  alternative → `ranked=true` + `score`, decision V-4), write hooks on
  entity-create / mint / engagement-create (best-effort, never fails a mint), and
  a one-shot `entity-reindex` backfill. Implements the workspace-owned §6.2 spec.
- **Injection observability — the 6-field measure-view, served + rendered.** The
  PREFLIGHT/CHECK teaser's `_context_budget` now carries the canonical
  `injected_per_category`/`injected_total`/`cap_*`/`capped_*` block whenever there's
  injected volume or a configured cap — so the "measure before you tune" signal
  shows *even uncapped* (not just when a cap fires). Persisted per-transaction and
  exposed on `GET /api/v1/profile/status` (`injection_budget`) as the extension
  panel's served source, and rendered in `empirica status`
  (`Injection  N injected · cap M · K dropped`). Mirrors cortex's served block for
  cross-surface consistency.
- **User-configurable artifact-injection cap (`artifact_injection.max_per_category` / `max_total`).**
  The PREFLIGHT/CHECK teaser's per-category injection volume is now tunable + observable.
  Set `.empirica/config.yaml` → `artifact_injection: {max_per_category: N, max_total: M}`
  (or env `EMPIRICA_MAX_ARTIFACTS_PER_CATEGORY` / `EMPIRICA_MAX_ARTIFACTS_TOTAL`; env wins,
  precedence env > config.yaml > default) to cap how many items per category — and combined —
  get injected. Lists are score-ranked so the dropped tail is lowest-ranked; the drop counts
  surface in `_context_budget.capped_per_category` / `capped_total` for observability.
  **Default is uncapped — behaviour unchanged.** Applied in `_apply_context_budget` (before the
  char-budget elision), so practices with heavy artifact graphs can right-size injection. POSTFLIGHT
  + post-compact bootstrap injection have separate budget builders (deferred follow-up).
- **Daemon org-entity projection surfaces org detail (industry/description/domain/org_type/tags).**
  `GET /api/v1/entities?type=organization` projected only `id/name/status`, while the
  **contact** projection already surfaced rich detail — an asymmetry that left
  workspace-populated org profiles (industry, description, domain, org_type, tags)
  invisible to extension/practices. A new `WorkspaceDBRepository.get_org_detail_map()`
  (the org-side peer to `get_contact_detail_map` — `_table_exists`-guarded, `tags`
  JSON-parsed to a list) is joined into the `organization` branch of `list_entities`.
  Additive, no schema change (the fields live in workspace's `organizations` detail
  table). Needs a daemon restart to go live (deploy-staleness).
- **Unified Source Identity P2 — sync-when-small body upload (`sources-reconcile --push-bodies`).**
  A source cortex knows only by catalogue pointer used to be un-fetchable by a peer
  with no local copy. Now `sources-reconcile --apply --push-bodies` uploads the
  **body** of each small adopted source (≤ 1MB default, override via
  `EMPIRICA_SMALL_BODY_THRESHOLD`) to cortex's `POST /v1/sources/{cortex_uuid}/body`,
  so cortex's `GET /content` serves the bytes to a remote peer (the reciprocal of
  the local daemon's content endpoint). Best-effort + idempotent (cortex dedupes on
  `body_hash`), tenant-gated (no-op without cortex creds), opt-in (default reconcile
  behaviour unchanged). Contract pinned + shipped with cortex + extension. Create-time
  upload is deferred to P3 (needs on-create catalogue register).
- **`empirica goals-reopen` — reverse a completed goal (reversible close).**
  `goals-complete` was effectively irreversible via the CLI (`goals-activate` is
  planned-only), so an accidental or premature completion could only be undone by
  hand-editing the DB. `goals-reopen --goal-id <id> [--reason ...]` flips a
  completed goal back to `in_progress`, clears the completed flags
  (`is_completed`, `completed_timestamp`), and re-links it to the current
  transaction. Matches on `status='completed'` OR `is_completed=1` (the latter is
  the source of truth); records a `reopen_history` entry in `goal_data`. Part 1 of
  the goal-lifecycle directive (reversibility).
- **`empirica goals-archive` + `goals-list --include-archived` — archive-closed-after-X hygiene.**
  Part 2 of the goal-lifecycle directive. `goals-archive [--older-than N] [--apply]`
  archives completed goals whose completion is older than N days (default 30) so the
  completed view doesn't grow unbounded — dry-run by default, `--goal-id` archives a
  single completed goal regardless of age. Mirrors the source-archive lifecycle
  (migration 056 adds `archived`/`archived_at` to `goals`). Archived goals are hidden
  from `goals-list` unless `--include-archived`; `goals-reopen` un-archives. The
  active `goals-list` is unaffected (archived goals are completed).
- **Unified Source Identity P1 — one source, two addresses (dual-resolution).**
  A source that only cortex knows by its catalogue uuid — but whose file exists
  locally under a *different* local uuid — used to 404 on the daemon (`GET
  /api/v1/sources/{id}/content`): the daemon and cortex kept separate id-spaces.
  Now a local row can carry the catalogue uuid as an **alias** (`cortex_uuid`,
  migration 055) and the daemon resolves **`id OR cortex_uuid`**, so a client
  holding only the cortex uuid still reaches the local source. `sources-reconcile`
  populates the alias **non-destructively by default**; the pre-existing
  destructive PK-swap (rewrite the local id to the catalogue uuid + cascade
  edges) is now opt-in via **`--converge`**. Offline-safe (no cortex call at
  request time), no Qdrant re-key. Contract pinned with cortex + extension.
  Extension needs zero change (it already requests by the cortex uuid).

### Fixed
- **`GET /entities/{id}/artifacts` returns source content, not just pointers.**
  The entity knowledge pane got opaque `{artifact_id, artifact_source}` edges — a
  UUID + a dir path, not renderable knowledge. Each source-type edge is now
  enriched with `{title, description, source_type, path}` resolved from the
  source's home `.empirica` DB (`<artifact_source>/sessions/sessions.db`); sources
  from another project resolve cross-DB the same way (grouped, one open per DB).
  Read-only + best-effort — a missing/unreadable sibling DB leaves a bare pointer
  rather than failing the response. Unblocks the extension knowledge pane
  (workspace `prop_tu3o343`).
- **`source-archive`: clear the source's semantic-search embed too.** Archiving a
  source hard-deleted its content chunks (`_docs_collection`) but left its metadata
  embed in `_memory_collection`, so an archived (soft-deleted) source stayed
  discoverable via `sources-map` / cross-project semantic search. Archive now also
  removes the `item_type='source'` point (filtered by `artifact_id` + `type`), so
  the "remove" step propagates to *every* Qdrant surface, not just the docs
  collection. Surfaced by a source-lifecycle boundary-hygiene audit — the
  companion `source-update` "stale embed" suspicion was a false alarm (source
  embeds are metadata-only; update never changes title/description/url). Archive
  result JSON gains `memory_embed_deleted`.

## [1.12.16] — 2026-07-07

### Added
- **`empirica source-review` — the REVIEW half of the source-lifecycle triad
  (CHECK → UPDATE → REVIEW).** Records a review verdict + `last_reviewed_at` on a
  source (migration adds both columns to `epistemic_sources`), so a corpus can be
  judged current/stale, not just present. Completes the triad alongside
  `sources-check` (detects drift) and `source-update` (re-fetches).
- **`empirica sources-sanctify` — source-corpus hygiene pass.** Classifies each
  active source **dead** (canonical_path missing), **duplicate** (shared
  `content_hash`), **zombie** (no `sourced_from` reference — nothing cites it), or
  **valid**, and recommends a lifecycle action. Report-only (deletions are a
  judgment that go through review — ARTIFACT_HYGIENE); retire flagged sources via
  `source-archive`. The two new detectors (zombie, duplicate) sit on top of the
  existing dead probing. This is the prerequisite for sources joining the
  PREFLIGHT retrieval surface — a corpus can't nudge attention until it's trusted.
- **Blindspot detection — regret auto-trigger.** Closes the loop's training
  signal: a `dismissed` blindspot flips to `regretted` at POSTFLIGHT when a
  mistake or dead-end lands on the same goal *after* the dismissal (we warned, it
  was ignored, the gap bit). `created_timestamp > resolved_timestamp` enforces the
  causal order; cross-transaction by construction. Fail-open. `blindspot-report`'s
  regret-rate is now live — the money signal.

### Fixed
- **`forgejo-publish`: path-scoped credential store + honest re-publish output.**
  Sets `credential.useHttpPath=true` (repo-local) so each project's per-project
  Forgejo token gets its own `~/.git-credentials` entry instead of colliding under
  a shared host-keyed store — a fleet-wide fix for multi-repo mirroring. An
  already-published re-run now warns "nothing pushed" and points at `--rotate`
  instead of printing a false "✅ provisioned".
- **session-init: harness-aware plugin auto-sync (`EMPIRICA_HARNESS`).** The
  SessionStart hook's `plugin-sync` self-heal now no-ops off Claude Code (default
  `claude-code` preserves current behavior; a non-CC harness sets `EMPIRICA_HARNESS`
  once) and honors the existing `EMPIRICA_NO_AUTOSYNC` opt-out — so a non-CC harness
  stops re-healing a plugin path it never loads, and a CC user can turn off silent
  self-heal without forking the hook.

### Changed
- **Lean system prompt: taxonomy + load-bearing-flag sync.** The distributed
  system-prompt template + AI-facing skills now reflect the current `*-log` flag
  surface (`--prevention`, `--reversibility {exploratory,committal,forced}`, etc.),
  and a CI conformance guard fails if the prompt or a skill references an `empirica`
  verb that doesn't exist — anti-drift for the AI-facing injection surface.

## [1.12.15] — 2026-07-06

### Added
- **Blindspot detection — POSTFLIGHT outcome/regret loop (T4).** Closes the loop:
  a surfaced blindspot is advanced at POSTFLIGHT to `acknowledged` (its flagged
  task got engaged — a finding, unknown, dead_end, or completion) or `dismissed`
  (the goal closed with the task still bare — you proceeded past the nudge); it
  stays `surfaced` while work is in-flight (no premature dismiss). That makes
  `blindspot-report`'s acknowledge/dismiss rates real — the dials that tune how
  loud the detector earns the right to be. `regretted` (the training label — a
  dismissed blindspot that later became a mistake/dead-end) gets its mechanism
  (`mark_blindspot_regretted`); wiring its automatic trigger from the mistake log
  is the next micro-step. Fail-open POSTFLIGHT stage — never affects the close.
- **Blindspot detection — CHECK advisory nudge (T3).** The intent-gap detector now
  surfaces at CHECK: `result['intent_gaps']` lists predicted blindspots (active-goal
  tasks with no coverage and no acknowledging unknown) as an **advisory — never a
  block**. Unlike weave-enforce (which gates on a measured fact), a blindspot is a
  prediction, so it nudges but never flips `proceed→investigate`. Each fresh
  candidate persists to `blindspot_events` (deduped across repeated CHECKs) so the
  telemetry + regret loop accrue from the first nudge. Fail-open — a measurement
  error yields no advisory and never affects CHECK.
- **Blindspot detection — persistence + telemetry + active-goal scoping (T2).**
  `blindspot_events` (migration 053) is the durable, **fail-open** substrate for
  blindspot outcomes (surfaced / acknowledged / dismissed / regretted); a new
  `empirica blindspot-report` renders the telemetry (acknowledge-rate = the nudge
  is useful; regret-rate = dismissed ones that later became mistakes/dead-ends).
  Instrument-before-surface, the enforce-telemetry playbook. `detect_intent_gaps`
  now scopes to **active goals by default** (excludes dormant `planned` backlog —
  a dry-run inspection showed planned-goal subtasks are task-hygiene, not
  unknown-unknowns); `blindspot-scan --include-planned` gives the full backlog
  view. Verified: active-scope narrowed a live session from 9 candidates to the 2
  genuinely-forward-looking ones.
- **Blindspot detection — scaffold + intent-gap signal (`empirica blindspot-scan`,
  dry-run).** First step toward inferring unknown-unknowns from the artifact corpus.
  A blindspot is *absent + unacknowledged + inferred* — and the least-noisy signal
  is against **stated intent**: an open goal/task with no covering finding, no
  acknowledging `unknown`, and no attempt (the per-task finding/unknown/dead_end
  lists are the mask). New `empirica/core/blindspots/` module with a pure,
  unit-tested `detect_intent_gaps()`. `blindspot-scan` reports candidates for a
  session (human/JSON) — **dry-run only**: wired to nobody (no CHECK nudge, no
  persistence) until later transactions. Explicitly **not** keyword/ontology based.

### Fixed
- **SessionStart inbox-lead truncated proposal IDs, breaking `mailbox reply`.**
  The pending-mesh-messages block sliced the proposal id to 26 chars (`[:26]`),
  but real ids are `prop_` + 26 = 31 chars — so the surfaced id lost its last 5
  and failed the `mailbox show <id>` / `reply --parent-id <id>` commands the block
  itself instructs you to run. Now shows the full id. Regression test added with a
  real-length id (prior fixtures used short ids that never hit the cut). (#267 follow-up.)
- **Logged mistakes now surface at PREFLIGHT + CHECK (they never did).** An audit
  found the attention-nudge mechanism ran at half-mast: `mistake-log` embedded
  mistakes for semantic search, but the retrieval (`retrieve_task_patterns` /
  `check_against_patterns`) had no mistakes path at all — dead-ends surfaced,
  mistakes were the orphan, so "log a mistake so it reminds you next time" was
  structurally broken. Mistakes are now retrieved as a first-class anti-pattern
  (base result `prior_mistakes` at PREFLIGHT, `mistake_matches` at CHECK), parsed
  from the same embedded text and treated exactly like dead-ends
  (know-gap-scaled adaptive limit, budget-protected from eviction). A logged
  mistake now nudges attention on a semantically similar task.

### Added
- **`empirica enforcement-report` — artifact-graph enforce telemetry.** Reads
  `weave_enforce_events` and reports block-rate and — the health metric —
  **self-resolve-rate**: of the transactions the gate blocked, how many recovered
  on their own (wove an edge → re-CHECK proceeded) vs stalled. High self-resolve =
  the gate nudges and the system recovers autonomously (working as designed); low =
  it's over-blocking (a low rate prints a floor-tuning hint). The empirical answer
  to "how well is enforce-by-default going," and the first read of the ECO/novelty
  frontier. Human + JSON; optional `--session-id` scope.
- **Enforce-event persistence — `weave_enforce_events` (migration 052).** The
  artifact-graph gate's weave verdict at CHECK (connectivity vs floor, response
  band, whether it blocked and whether it overrode the decision) was computed
  transiently and never stored. It's now recorded one row per CHECK, **fail-open**
  (a persistence error never affects the CHECK decision — the path is fleet-critical
  since enforce-by-default shipped). This is the durable source for the
  forthcoming enforcement telemetry (block-rate / self-resolve-rate) and the
  adaptive-enforcement (`patience`) consecutive-miss history — the instrumentation
  for observing the ECO/novelty frontier as it recedes.

### Changed
- **Artifact-graph gate is now enforce-by-default, ecosystem-wide.** The
  `_GATE_SCALARS` defaults flip from report-only (strictness 0.25 / floor 0.50)
  to enforce (strictness **0.75** / floor **0.34**) — the pair empirica dogfooded.
  The model inverts from opt-IN-enforce to enforce-by-default + opt-DOWN: below
  34% artifact-graph connectivity the CHECK gate soft-blocks the noetic→praxic
  transition (flips `proceed`→`investigate`, always recoverable by weaving one
  edge — `--related-to` / `--edge` / `log-artifacts` edges). This makes grounded
  weave-discipline the physically-enforced baseline for ECO-gated autonomous
  work rather than an honor-system nudge. Escape hatches unchanged and
  precedence-preserved (env > project.yaml > default): a session opts down with
  `EMPIRICA_ARTIFACT_GRAPH_STRICTNESS=0.25`, a practice with an `artifact_graph`
  block in `.empirica/project.yaml`. Enforcement is fail-open (a measurement
  error never blocks) and only ever soft-blocks at CHECK — POSTFLIGHT stays
  report-only.

## [1.12.14] — 2026-07-06

### Added
- **`source-update` — re-fetch a source and recompute its content identity.**
  The act half of the source lifecycle: after `sources-check` flags a source
  stale or broken, `source-update` re-fetches it (local path first, else the
  http(s) URL, size-capped), recomputes `content_hash` / size / mime, and
  appends an audit event. A failed re-fetch updates nothing — an existing hash
  is never wiped by a transient failure.
- **`delete-artifacts` — edge deletion + dangling-edge repair/prune.** Alongside
  node deletion it now accepts `edges` (delete a specific edge) and
  `prune_dangling` (sweep edges whose endpoints no longer exist). Prune is
  repair-before-prune by default: an endpoint that resolves to a real artifact
  (e.g. a short id prefix) is rewired instead of deleted; only the truly
  unrecoverable is removed. Dry-run by default.
- **Artifact-graph gate — per-practice enforce via `.empirica/project.yaml`.**
  The scalar gate can read its `strictness` / `connectivity_floor` / `patience`
  from a per-practice (gitignored) `artifact_graph` block, so a practice can opt
  into enforce (block the noetic→praxic transition below the connectivity floor)
  locally without changing the report-only default for everyone else.
- **`empirica mailbox poll` / `show` / `archive` — the receive side of the mesh
  CLI.** Symmetric with `empirica mailbox reply` (send/ack). `poll` wraps the
  cortex inbox/outbox HTTP endpoints (`--ai-id`, `--status`, `--since`,
  `--limit`, `--related`, `--outbox`); `show` fetches one proposal; `archive`
  soft-deletes from the inbox view. Gives any CLI surface a reliable receive
  path without the MCP tool-namespace round-trip.
- **Artifact-graph gate — scalar control surface (report-only).** The
  POSTFLIGHT connectivity gate is now driven by three orthogonal 0.0–1.0
  dimensions — `strictness` / `connectivity_floor` / `patience` — that a host UI
  drives as sliders, replacing the discrete `off/nudge/soft/hard` mode. Defaults
  keep it report-only + forgiving; blocking is a follow-up.

### Fixed
- **Sentinel: file redirects laundered through shell chain segments.**
  `cd /x && grep foo > /tmp/out` classified noetic despite writing a file — the
  chain classifier short-circuited before the top-level redirect check. The
  per-segment classifier now rejects dangerous redirects (`> f`, `>> f`, `< f`),
  aligning chain-segment behavior with single-command behavior. Security fix.
- **Sentinel: the `empirica mailbox` CLI family was denied pre-transaction.**
  The mailbox verbs weren't in the tiered CLI whitelist, so a woken idle
  practitioner running `empirica mailbox poll` as its first action hit "No open
  transaction". Reads (`poll`/`show`) are now Tier 1, state-changing verbs
  (`reply`/`archive`) Tier 2 — all flow pre-transaction.
- **`session-create --auto-init` minted a fresh `project_id` instead of adopting
  an existing one.** When `project.yaml` was absent but the path already had a
  canonical id in the local sessions.db / registry / workspace.db, auto-init
  minted a new id and "corrected" the canonical workspace row toward it —
  stranding the session under a phantom id. Auto-init now adopts the existing id
  (local sessions.db → registry.yaml → workspace.db) and mints only when all are
  empty.
- **Non-proposal wake shapes (e.g. SER escalations) are now deliverable.** The
  listener discarded the push body and the catch-up reconstructed only proposals,
  so a non-proposal wake was structurally undeliverable. The listener now relays
  allowlisted non-proposal shapes from the push body, and the catch-up reconciles
  them from durable state so a dropped doorbell is recoverable on retry.
- **SessionStart context leads with the pending mesh inbox.** The loader injected
  the greet/goals block first, which dominated so hard that a woken practitioner
  greeted instead of acting on a pending message. It now prepends the pending
  inbox (bounded poll, fail-open); the monitor-arm hook is arm-by-replacement, so
  a re-fired SessionStart no longer stacks duplicate wake delivery.
- **Weave connectivity is degree-based.** The POSTFLIGHT connectivity count
  treated only outgoing edges as connected, under-counting the common hub weave
  (one decision grounded by N findings). It now counts any incident edge.
- **`log-artifacts`: edges to non-existent artifacts are no longer silently
  wired.** An endpoint matching no artifact (a guessed UUID or a bare id prefix)
  was stored as a dangling row and counted as wired. Endpoints are now validated
  — and short prefixes resolved to the full id — at write time on both the batch
  and inline `--related-to` / `--edge` paths; a dangling endpoint is skipped with
  a warning. An edges-only payload is now accepted so a mis-wired edge can be
  repaired.
- **EPP semantic-pushback check is a terse pointer, not a full block per prompt.**
  The full multi-line check was injected on every substantive prompt — a
  per-prompt token cost and, on some harnesses, a visible user-role prompt. It is
  now a one-line pointer to the EPP protocol, injected the same way.

## [1.12.13] — 2026-07-04

### Removed
- **Legacy Flask dashboard API.** `empirica serve` runs the FastAPI serve
  daemon; the older Flask app was never mounted by it — its routes returned 404
  in the running daemon. The one route still needed (calibration) was already
  ported to a FastAPI router. Removed the unserved Flask app + its route
  modules, and dropped `flask` / `flask-cors` / `werkzeug` from the `[api]`
  extra (a CVE-tracked trio that existed solely for the dead code).

### Fixed
- **Presence: a live practitioner no longer disappears behind a stale
  heartbeat.** The fleet/presence view now overrides a stale heartbeat with a
  process-liveness check, so a still-running session stays visible even if its
  last heartbeat aged out. A resumed session (`--resume`) additionally re-stamps
  its process id, which had been left dead from the original launch.
- **Loops: ghost canonical-loop units guarded.** Loop enablement now refuses a
  placeholder instance id and maps cron expressions to native launchd/systemd
  calendar schedules, so a mis-parameterized auto-register can no longer seed
  self-regenerating ghost loop units.
- **Hooks: vendor-agnostic practice resolution at the post-compact boundary.**
  The post-compact session hook resolves the practice from the filesystem
  (CWD/git-root) when the harness declares CWD is the verified practice
  (`EMPIRICA_CWD_RELIABLE=true`), and exits cleanly (no-op) instead of reporting
  a hook failure when no practice resolves — so a fresh session on a
  vendor-agnostic harness no longer shows a spurious "SessionStart hook failed."

## [1.12.12] — 2026-07-03

### Added
- **Engagement `planned` (pre-active) lifecycle state + `?lifecycle=all`
  fetch-everything.** Engagements gain a `planned` state ahead of `open`, for
  queued-but-not-started work. The active-by-default feed
  (`GET /api/v1/engagements`, the `engagement-list` CLI, and the org/contact
  drill) now excludes **both** pre-active (`planned`) and terminal (`closed`) —
  the "who are we working with now" view is the complement
  `{open, in_progress, blocked}`. Reach the full set with an explicit
  `?lifecycle=<state>` (`planned` / `closed` each individually targetable) or
  the new `?lifecycle=all` sentinel (`--lifecycle all` on the CLI). `include_closed`
  remains as legacy sugar — it adds `closed` back to the feed but leaves
  `planned` out unless it's requested explicitly. Enum + filter live app-side in
  `WorkspaceDBRepository` (`ENGAGEMENT_PREACTIVE_STATES` /
  `ENGAGEMENT_DEFAULT_EXCLUDED_STATES`); invalid states surface as a 422, never a 500.

### Fixed
- **Presence torn-write data corruption.** `write_presence` and the daemon's
  `refresh_live_presence` published presence records through a *shared*
  per-target temp file, so concurrent listener writers could `O_TRUNC` the same
  temp and leave a torn record on disk; the strict JSON reader then skipped it,
  **permanently hiding a live practitioner** from the fleet/presence views (it
  couldn't even be re-stamped). Each writer now owns a unique temp file, the
  readers tolerate a torn tail (recovering the leading valid record so
  already-torn files resurface on next read), and abandoned temps are swept.
- **Sentinel no longer spuriously reports "Epistemic loop closed" mid-transaction.**
  After an instance-suffix rotation (e.g. across compaction), a stale *closed*
  transaction file at the current suffix could mask a live *open* transaction,
  denying praxic actions after a valid CHECK (only a re-PREFLIGHT cleared it).
  The resolver now prefers a key-matched *open* transaction over a stale closed
  exact-suffix file (fix applied to both the package resolver and the firewall's
  mirrored copy).
- **cortex-mailbox skills are harness-portable.** The mailbox poll/send skills
  documented flat `mcp__cortex__*` tool names (correct for Claude Code) that fail
  under namespace-aggregating harnesses (which expose one namespace tool +
  operation param); a "Harness portability" note now spells out invoking the same
  operations either way.

## [1.12.11] — 2026-07-03

### Added
- **Daemon CRM projection — richer contacts, engagement tasks, contact/manager
  scoping.** `GET /api/v1/entities?type=contact` now projects the full CRM detail
  per contact — `email`/`phone`/`title`
  (`contacts.email_primary`/`phone_primary`/`organization_title`), `tags`
  (JSON-parsed), `notes`, `contact_type`, `lifecycle_stage`, `role` and
  `parent_org_name` (contact→org `entity_membership`; role is a free-text verb,
  org name joins `entity_registry.display_name`), plus **`tier`**
  (`entity_registry.metadata.tier`) and **`reporting_to_name`** (resolves the
  `reports_to` edge → manager's `display_name`). New
  `GET /api/v1/engagements/{id}/tasks` surfaces the workspace `engagement_tasks`
  (task_id/title/status/assigned_to/due_at/…, oldest first, honest-empty). New
  **`?contact=`** filter on `GET /api/v1/engagements` scopes the feed to a
  contact's active participations (`engagement_contacts` edge), composing with
  the existing `?org=` (ticket_of) filter. Four new `WorkspaceDBRepository` maps
  (`get_contact_org_details_map`, `get_contact_detail_map`,
  `get_contact_reports_to_map`, `get_engagement_tasks`); all field sources
  verified against the live workspace.db.
- **Calibration config — settable epistemic weights + Sentinel thresholds (per-practice + global).**
  A new `empirica/core/calibration_config.py` declares the tunable surface (the 4
  dimension weights + 4 Sentinel thresholds — the same shape personas use as
  `EpistemicConfig`) and an **overlay resolver** that layers `base → persona
  preset → global override → practice override` (sparse overrides stored in a
  dedicated `.empirica/calibration.yaml` per scope, never touching `project.yaml`).
  New daemon endpoints `GET/PATCH /api/v1/calibration/config?scope=&practice_id=`
  expose it (validated, range-clamped, reset-to-default via `null`) for the
  extension's "Sentinel Tuning" tab. This is the settable *source* + read/write
  surface; migrating the scattered runtime gate checks to read the resolver is a
  tracked follow-up. Extension owns the UI slice.
  A code-verified deep dive established that the live CHECK gate is
  **uncertainty-only** (`uncertainty ≤ ready_uncertainty_threshold`, default
  0.35, per the 2026-04-07 meta-uncertainty redesign) — so `engagement_gate`
  (0.6) does not gate CHECK at runtime. Added a settable **`ready_uncertainty`**
  field (the real gate) and a runtime **`effective_for_session(project_path)`** /
  **`override_thresholds(project_path)`** resolver (leaf helpers, layer
  global→practice, defaults preserved exactly when no override exists). These are
  **wired into the three live threshold accessors** (CLI
  `_check_load_dynamic_thresholds`, the Sentinel hook `_get_dynamic_thresholds`,
  the evaluator `_load_evaluator_thresholds`): a `ready_uncertainty` override
  shifts the live CHECK gate and an `engagement_gate` override shifts the hook's
  escalate-to-human threshold — **fail-safe** (a bad/missing `calibration.yaml`
  can never widen a gate) with the **Brier overconfidence-floor still tightening
  on top**. The comparison expressions are left textually intact (the override is
  injected upstream as the base). The extension's "Sentinel Tuning" sliders now
  alter live behavior.

### Security
- **`empirica security-audit` is now STRICT and shares one governed waiver source
  with the release gate.** Previously the audit `passed` gate failed only on
  empirica-scoped findings that matched the CISA KEV catalog (actively-exploited);
  an empirica-managed package with a plain critical CVE and no KEV match still
  passed. The gate now fails on **any** empirica-scoped CVE that is not in the
  governed waiver list — matching the release gate's strictness. A single
  `empirica.core.security.waivers.CVE_WAIVERS` list is the sole waiver source both
  `empirica security-audit` and `scripts/release.py` read, so the two gates can no
  longer drift (release-time `PIP_AUDIT_WAIVERS` is now sourced from it, falling
  back to empty — stricter, never looser — if empirica isn't importable). KEV
  matches are **never** silently waivable (a waiver can't suppress an
  actively-exploited finding). User-scoped CVEs remain informational (reported,
  not gated). The waiver list is currently empty — the sole prior waiver was
  retired when nltk left the tree (below). Each finding now carries a `waived`
  flag and the per-scope summary a `waived` count.
- **nltk removed from the dependency tree** — the `[prose]` evidence extra pulled
  `textstat`, which pulled `nltk` (`nltk` was `Required-by` textstat *only*), and
  nltk carried PYSEC-2026-597 / CVE-2026-12243 (path-traversal arbitrary-file
  **read** via attacker-controlled resource names to `nltk.data.load()`/`find()`,
  7.5 High, **no fix** on any release ≤3.9.4). textstat's five readability calls
  (Flesch Reading Ease, Flesch-Kincaid, Gunning Fog, word + sentence counts) are
  now computed in-house by a new `empirica.core.post_test.readability` module
  using the public-domain formulas, with `pyphen` (textstat's own nltk-free
  syllable backend, so the numbers stay close) and a vowel-group heuristic
  fallback. `[prose]` now installs `pyphen + proselint` instead of
  `textstat + proselint`; nltk is gone from the tree, so the governed
  PYSEC-2026-597 release-gate waiver is **retired** rather than carried. The
  prose-quality evidence metrics `textstat_readability`/`textstat_density` are
  renamed `readability`/`readability_density`.

### Fixed
- **`get_instance_id()` no longer emits a harmful false-positive warning for
  UUIDv7 practitioner_ids.** The tmux Priority-1 override guard treated only
  cockpit-slot ids (`[a-z][a-z0-9_-]*`, leading lowercase letter) as intentional;
  a UUIDv7 starts with a digit, so a codex/ecodex fork that wires its `thread_id`
  into `EMPIRICA_INSTANCE_ID` (to key per-practitioner calibration) fell through
  to a `logger.warning` advising *"unset EMPIRICA_INSTANCE_ID"* — following which
  would sever the practitioner_id → calibration mapping. The guard now also
  accepts UUID-shaped ids (via the existing `_is_uuid_format` helper) → logs at
  debug. Resolution was already correct; only the misleading warning is silenced.
- **Daemon CRM projections degrade to empty instead of 500-ing on
  older/minimal workspace DBs.** The contact-detail / engagement-task / `?contact=`
  queries hit tables (`contacts`, `engagement_tasks`, `engagement_contacts`) that
  are workspace-managed and absent on a core-only install; a `_table_exists`
  guard now returns `{}`/`[]`/`count 0` rather than raising
  `OperationalError: no such table`, so `GET /api/v1/entities` and
  `GET /api/v1/engagements` stay 200.

## [1.12.10] — 2026-07-02

### Fixed
- **Workspace-DB open no longer hard-crashes on pre-E1 databases** — a
  `workspace.db` whose `engagements` table predated the E1 sidecar columns
  (`lifecycle_state`/`stage`/`domain`/`updated_at`) was never migrated:
  `CREATE TABLE IF NOT EXISTS` no-ops on an existing table, then
  `CREATE INDEX idx_engagements_lifecycle ON engagements(lifecycle_state)`
  crashed with `no such column` on **every** workspace-DB open — bricking
  session-create / project-switch / project-list / SessionStart auto-init.
  `_apply_engagement_substrate` now runs an additive-migration pass
  (`PRAGMA table_info` → `ALTER TABLE ADD COLUMN` for any missing sidecar column)
  before the index block, self-healing old DBs on open. (mesh-support/Philipp
  repro; surfaced by the 1.12.10 rollout on managed boxes.)
- **`session-create --auto-init` no longer clobbers an existing `project.yaml`** —
  the guard checked `config.yaml` but the overwrite target was `project.yaml`, so
  a project with `project.yaml` present (canonical `project_id` + type/domain/
  tenant/calibration_weights, gitignored → only copy) but `config.yaml` absent
  got regenerated with defaults + a **random** `project_id`. Auto-init now
  reuses an existing `project.yaml`'s `project_id` and never overwrites it.
- **`projects-sync` upserts by path, not append** — `upsert_project` matched only
  on `project_id`, so re-syncing a project whose id changed (slug→canonical-UUID
  promotion, or a re-clone) at the same path **appended a second `registry.yaml`
  entry** instead of updating the existing one. Two UUID-keyed entries at one
  path is a state `dedupe_registry` refuses to auto-resolve → per-request "dedup
  skipping conflict" log spam on every daemon request (surfaced by mesh-support
  on a managed box that accumulated 6 duplicate practices). `upsert_project` now
  matches on the resolved realpath first (then `project_id`) and updates in
  place, with a canonical-identity guard so a same-path re-sync never downgrades
  a UUID to a slug.
- **`empirica note` scratchpad notes now resurface until triaged** — `note --list`
  and the POSTFLIGHT retrospective were scoped to the *current transaction_id*, so
  a note jotted in one transaction was invisible from the next (and lost across a
  session_id rotation on compaction). The whole point is "capture now, classify
  *later*" — and "later" is almost always a different transaction — so
  cross-transaction follow-ups silently stranded (usage audit: 60–100% of notes
  left untriaged across practices, because the review moment never showed them).
  Both surfaces are now scoped by **`project_id`** (durable), so the backlog
  reliably reappears for triage; falls back to session scope for pre-project
  notes. The PREFLIGHT partial-credit signal stays transaction-scoped (it asks
  "did you capture intent during the work you just did?", which is correctly
  per-transaction).
- **Local machines no longer falsely trip `REMOTE:SSH:UNTRUSTED`** — `SSH_CONNECTION`/
  `SSH_CLIENT`/`SSH_TTY` are inherited by tmux/screen servers, so a pane whose
  multiplexer was started over a since-closed SSH login carried those vars
  forever, making a now-local machine read as an untrusted remote (reported on a
  local Mac). `detect_environment` now treats SSH env as **stale** when `SSH_TTY`
  points at a torn-down pty (device gone) and does not classify it as remote.
  Advisory annotation only — no gating behavior changed. (Non-interactive remote,
  `SSH_CONNECTION` without `SSH_TTY`, is unchanged.)

### Added
- **`?parent_org=<org_id>` on `GET /api/v1/entities`** — scope the contact list to
  one organization (the extension org-detail contacts subset). Backed by
  `entity_memberships` (active affiliation, `left_at IS NULL`) — the populated,
  vendored contact→org linkage, consistent with the existing org→engagement
  `ticket_of` scoping. An unknown org returns `[]` (honest-empty, no leak).
  Contact rows in the feed now also carry `parent_org_id` (their affiliated org),
  resolved via the same source so filter and enrichment agree.

### Changed
- **Engagement listing is active-by-default** (SER #183 part-2) — the daemon
  feed `GET /api/v1/engagements` and `empirica engagement-list` now exclude
  terminal (`closed`) engagements unless you opt in. The org→engagement drill is
  a "who are we working with now" view; terminal history belongs in the dedicated
  Engagements area. Opt into the full set with `?include_closed=true` /
  `--include-closed`, or target a specific state with `?lifecycle=` / `--lifecycle`
  (an explicit state always wins, so `lifecycle=closed` still returns closed). New
  `ENGAGEMENT_TERMINAL_STATES` constant makes the terminal set explicit.

### Added
- **`empirica off` / `empirica on`** — user-facing Sentinel toggle. `empirica off`
  pauses the noetic firewall for the current instance (off-the-record);
  `empirica on` resumes it. Both default to **per-instance** scope (resolved via
  the same `get_instance_id()` the gate reads) and accept `--global` to widen to
  all instances (`empirica off --global`). Friendly aliases for
  `empirica sentinel pause` / `resume`, which also gained `--global`. Recognized
  as meta-control toggles by the Sentinel gate, so they run even mid-loop — a
  gate must never block the verb that clears it.

### Fixed
- **`empirica listener off` can always recover its teardown handle** — when the
  optional `empirica listener arm <task_id>` step was skipped (e.g. the
  SessionStart arming flow armed the Monitor but never recorded its id), the
  active marker kept `monitor_task_id: null`. A live in-session tail Monitor is
  then neither `TaskStop`-able by id nor reap-able (it isn't a PID-1 orphan
  while its session lives), so `off` silently reported "never armed" while the
  Monitor kept running. `listener on` now records the Monitor's `description`
  in the marker, and `off` falls back to a `TaskList` → match-description →
  `TaskStop` recovery next_step. The `arm` fast-path (record the id directly) is
  unchanged.
- **`/empirica off` no longer silently misses** — the slash command re-implemented
  instance resolution inline (`TMUX_PANE`/TTY only) and diverged from the
  canonical resolver the gate reads (which also honors
  `EMPIRICA_INSTANCE_ID`/`CLAUDE_INSTANCE_ID`, `TERM_SESSION_ID`, `WINDOWID`, and
  a different TTY key shape). Under the cockpit (which sets `EMPIRICA_INSTANCE_ID`)
  it wrote a pause file under a name the gate never read, so the pause appeared to
  succeed but nothing paused. The command now delegates to the canonical
  `empirica off` / `on` / `sentinel status` CLI — one resolver, writer and reader
  always agree.
- **CHECK no longer desyncs from its transaction across compaction** — the CLI
  workflow verbs (`check-submit`, `postflight`) resolved the active transaction
  with no session key, so the suffix-mismatch fallback never engaged. When a
  compaction rotated the instance suffix, the open transaction became
  unresolvable → CHECK was stored *unbound* → the praxic firewall blocked
  `Write`/`Edit` despite an OPEN transaction (reported on a compacted session).
  `read_active_transaction_full` now self-sources the durable
  `claude_session_id` (the tty-anchored, compaction-stable key) when the caller
  doesn't pass one, so the transaction stays resolvable across suffix/session
  rotation. Additive — the exact-suffix primary path is unchanged, so the
  firewall never regresses on the common case.

## [1.12.9] — 2026-06-30

A cockpit-liveness + local-provisioning hardening release: multi-instance liveness is now multiplexer-agnostic and resume/reuse-safe, `instance prune` reaps superseded ghosts, the empirica-mcp wrapper can no longer drift from core, and `doctor` honestly reports the optional noetic toolchain.

### Added
- **`empirica instance rebind <id>`** — re-stamp an instance's captured pid +
  start time from its live process (matched on `EMPIRICA_INSTANCE_ID`), without
  running a transaction. Previously the only way to refresh a stale captured pid
  (after `claude --resume` or a manual restart) was to run a transaction;
  `prune`/`forget` only delete. `rebind` re-registers, so a resumed instance can
  be made visible to the cockpit again on demand.

### Fixed
- **empirica-mcp pinned to its core version (drift footgun)** — `empirica-mcp`
  declared a loose `empirica>=` dep, so `pipx upgrade empirica-mcp` could bump the
  thin wrapper while leaving the `empirica` core several versions stale — silently
  serving an old tool set (the real cause of "enhanced noetic tools missing" on a
  recently-upgraded box). The dep is now pinned `empirica==<matching version>`,
  and `release.py` bumps the pin in lockstep with the package version, so wrapper
  and core can never diverge. An integrity test guards the `==` pin.
- **`empirica doctor` surfaces the full optional noetic toolchain** — the Sentinel
  allowlists rg/fd/yq/gron/ast-grep/bat/tokei/scc ahead of install, but `doctor`
  only reported a subset, so practitioners could be told a tool was available when
  it wasn't. `doctor`'s noetic-tools check now mirrors the allowlist (adds
  gron/bat/tokei/scc) and reports each as present/missing with an install hint —
  optional, never a hard failure (no auto-install).
- **Multiplexer-agnostic cockpit liveness** — `status` / `status --all` and the
  cockpit TUI no longer under-report instances running Claude under a non-tmux
  multiplexer (GNU screen, WezTerm, zellij, cmux), or after `claude --resume` /
  an env-unset manual restart that left a stale captured PID. Liveness now
  consults a direct process-table scan: the primary signal matches each live
  `claude` process to its instance by `EMPIRICA_INSTANCE_ID` (exact and
  resume-proof — it survives PID changes and is independent of any multiplexer),
  with a coarser working-directory match as a fallback for processes that carry
  no instance-id env. Both override a stale captured PID and are alive-positive
  only (never a new dead verdict). A count-aware dedup caps how many same-project
  instances the cwd fallback can revive at the number of live processes, so
  duplicate stale sessions don't inflate the count, and `instance prune` honors
  the same signals so it won't offer to kill a Claude that's alive under a
  non-tmux multiplexer.
- **Generated hook timeouts raised so context injection survives load** —
  `setup-claude-code` generated the lightweight per-prompt / per-SessionStart
  hooks (tool-router, context-shift tracker, monitor-arm, the loop/listener
  pickups) at 3s/5s. On a many-instance restart-herd box (high load, slow cold
  Python starts) those hooks timed out and Claude Code silently discarded their
  stdout — so their context injection never landed. They now generate at a
  single `LIGHT_HOOK_TIMEOUT` (10s); the heavy hooks (compaction, session-init,
  postflight) keep their own larger budgets. All remain `allowFailure=True`.
- **`instance prune` now reaps superseded fallback ghosts** — an old
  tty/pane-name record (`tmux_N` / `term_*`) left behind by a session that
  started without `EMPIRICA_INSTANCE_ID` was kept "alive" by the project-level
  cwd signal (its project still hosts a live canonical instance), so it
  survived `prune` forever — `status --include-dead` filled with zombies.
  `discover_dead_instances` now applies the same count-aware dedup the cockpit
  uses, so a fallback record superseded by a canonical (env-declared) instance
  for the same project is identified as dead and pruned. Canonical
  env-matched instances are never reaped. (Prevention was already in place:
  `get_instance_id` prefers `EMPIRICA_INSTANCE_ID`, so no fallback record is
  minted while the env var is set.)
- **Liveness no longer flaps on a recycled PID** — `is_alive`'s captured-PID
  check could read a *reused* pid number (after `claude --resume` or a crash,
  the OS recycles the old pid for an unrelated process) as "alive", then "dead"
  moments later as that impostor came and went. session-init now records the
  Claude parent's start time (`ppid_create_time`) alongside the pid, and the
  liveness check requires the start time to match — a recycled pid no longer
  passes. Re-stamped every SessionStart (incl. `--resume`) so the captured pid
  stays current. Falls back to the bare `os.kill` probe when psutil is
  unavailable or the start time wasn't captured (older instances).

## [1.12.8] — 2026-06-29

A context-hygiene + module-install patch: the PREFLIGHT/CHECK/bootstrap pattern block is now budgeted lean-by-default, and `module provision` completes the competence layer (skill/agent discovery) for installed modules.

### Added
- **Module plugin registration** — `empirica module provision` now registers an
  installed module as its own `<name>@local` Claude Code plugin (writes the
  `installed_plugins.json` entry + generates `.claude-plugin/plugin.json` when the
  archive didn't ship one), so the module's skills and agents are discovered from
  its own plugin directory. Idempotent and dry-runnable; gated on a declared
  plugin archive. (Local-plugin `hooks` additionally need a `settings.json` entry —
  a separate follow-up.)
- **Authenticated `git+https` module packages** — `module fetch` substitutes a
  resolved `secrets_ref` bearer into `git+https://github.com/…` `python_packages`
  at pip-install time, so a proprietary module installs with the same token that
  gates its plugin archive (no `~/.netrc` setup). The token is passed to pip only —
  never written to a receipt or error string — and falls back to the operator's
  own git credentials when no bearer resolves.

### Changed
- **Lean-by-default pattern retrieval** — the pattern block injected into PREFLIGHT
  / CHECK / project bootstrap is now budgeted: cross-section duplicates are removed,
  long items are truncated with an expand-on-demand pointer, and a total size budget
  drops the lowest-ranked items (protecting the core lessons/dead-ends/findings
  triad). Adaptive retrieval limits are capped so the block stays small exactly when
  context is tightest (post-compaction). Set `EMPIRICA_PATTERN_BUDGET_OFF=1` for the
  full untrimmed result; per-knob env overrides available.

## [1.12.7] — 2026-06-26

A hardening + docs patch: the remote-ops pacing-guard is now fully recoverable and wrapper-aware, the deployed Claude Code plugin self-heals from the CLI, and the `/eat-the-broccoli` quality-sweep skill ships in-plugin.

### Added
- **Vendored `/eat-the-broccoli` skill** — the tiered quality-and-pattern audit
  (deterministic tooling plus a learned-pattern hunt for the failure classes that
  pass every test and still ship broken) now ships in the Claude Code plugin.
  Canonical source: [EmpiricaAI/broccoli](https://github.com/EmpiricaAI/broccoli).
- **CLI-level plugin auto-sync** — the `empirica` CLI self-heals a stale *deployed*
  Claude Code plugin: when the deployed plugin's version stamp drifts from the
  installed package, it runs `plugin-sync` once (debounced, fail-safe, opt out with
  `EMPIRICA_NO_AUTOSYNC`). Bootstraps even boxes whose plugin predates the in-plugin
  session-init self-heal.

### Fixed
- **Sentinel remote-ops pacing-guard deadlock** — the rush-guard now (a) sees
  through benign command wrappers (`timeout` / `env` / `nice` / …) so a wrapped
  `ssh` is still classified as remote-ops, and (b) counts artifacts up to *now*
  rather than a window frozen at CHECK time, so logging a finding actually clears
  the deny as the message promises. `remote-ops` work is exempt from the guard
  entirely.

## [1.12.6] — 2026-06-24

A feature + hardening patch: the practitioner-presence identity substrate, the
engagement HTTP routes family, and two Sentinel firewall correctness fixes.

### Added
- **Practitioner presence** — a per-conversation presence substrate keyed on the
  durable Claude session id (a session-as-practitioner identity that survives
  compaction, while the measurement-cycle session id rotates). Written/cleared by
  the session hooks and pushed to the intelligence layer's heartbeat endpoint
  with the practice's canonical id, so live practitioners are addressable per
  practice.
- **Engagement HTTP routes** — `GET/POST/PATCH /api/v1/engagements`: the daemon
  list feed (EngagementMin projection with synthesized org display + counts),
  create (with `ticket_of` org linkage + a writable metadata bag), and triage
  (lifecycle/stage transition + metadata update).
- **`parent_org_id`** on `GET /api/v1/entities` organization rows (org→org
  parentage projection).
- **`support.resolved`** terminal engagement stage.
- Practitioner Deliberation Model design proposal (`docs/architecture/`).

### Fixed
- **Sentinel transaction resolution** now prefers an OPEN transaction over a stale
  CLOSED one — fixes a post-compaction gating regression where the firewall could
  block praxic actions after a valid CHECK.
- **Sentinel release-path invariant** — a universal recovery/measurement pre-gate
  guarantees no gate can block the action that clears it (preflight/check/
  postflight, the investigate-and-log remedy, self-heal, and sentinel controls
  always flow, even from a stale-gated state).

## [1.12.5] — 2026-06-23

A feature + hardening patch: the first legs of the installable
practice-module system, the ERM entity API, a mesh-tooling grounding
export, and a robust fix for the recurring "rushed assessment" deadlock.

### Added
- **Practice-module system (legs 1–3):** `empirica module validate` (module.yaml
  schema + fail-fast validator — reference-only secrets, unknown-key rejection,
  automation consistency), `empirica module fetch` (auth-gated artifact pre-step),
  and `empirica module provision` (plugin layer: file placement, automation
  registration via the loop registry, ntfy-topic grants, env presence). All
  idempotent with `--dry-run`.
- **ERM entity API:** `GET /api/v1/entities` (list projection over the workspace
  entity registry) + `empirica entity-link` CLI (typed entity_memberships writes).
- **`empirica grounding-export --ai-id`:** per-practice self-assessed/grounded
  calibration snapshot for mesh (L1/L2) tooling.
- **`empirica plugin-sync`:** drift-check + in-place re-sync of the installed
  Claude Code plugin, auto-run at session-init — heals deploy-staleness so a
  stale hook can't silently persist.

### Fixed
- **Sentinel "rushed assessment" deadlock — fully hardened:** the self-heal
  verbs (`setup-claude-code`, `plugin-sync`) are now in the rush-bypass whitelist,
  so the command that fixes a stale-hook box is never itself rush-blocked.
- `project-sync` is now an alias of `projects-sync` (discoverability).

### Changed
- **Mesh discipline elevated:** "pull when uncertain" is now a *stuck → collab*
  reflex in the system prompt + constitution §V — surface a blocker to the mesh
  after 1–2 local attempts rather than grinding it.
- Repo URLs updated `Nubaeon → EmpiricaAI` (single-repo transfer; Docker Hub and
  non-transferred sibling repos left unchanged).

## [1.12.4] — 2026-06-21

A maintenance patch: a new family of graph-integrity checks, a sentinel
deadlock fix, mesh-identity self-healing at session start, and repo-wide
format enforcement.

### Added
- **Connective-tissue checks** — three domain-agnostic checks in the
  CheckDeclaration registry that assess the *structure* of the epistemic
  graph rather than its content: `edge_density` (a transaction's artifacts
  connect into the graph), `orphan_artifacts` (a majority of session
  artifacts left fully disconnected), and `dangling_edges` (an edge that
  references an artifact which no longer exists). Non-blocking; they query the
  canonical `artifact_edges` table.

### Fixed
- **Sentinel recovery-verb deadlock** — a rushed assessment could make the
  rush-guard deny every praxic call, *including* the postflight / check /
  doctor verbs needed to clear it. Recovery verbs now always escape the gate,
  in any state. Also: `empirica doctor` / `diagnose` are allow-listed, and a
  worktree-aware subagent signal was added.
- **Session-init mesh-identity self-heal** — session start now backfills a
  project's mesh identity metadata when it is absent, so multi-practice setups
  resolve their identity correctly instead of degrading.

### Changed
- **Repo-wide format enforcement** — the codebase is now `ruff format`-clean
  and CI enforces it (the format check was previously informational). Ruff is
  pinned to a current floor for local/CI parity; CI `actions/checkout` bumped
  to v7.

## [1.12.3] — 2026-06-21

A discipline-and-reliability release: a low-friction scratchpad for AIs, a
PREFLIGHT "breather" that catches unlogged work, two new deterministic guards
that end whole classes of silent drift, and a sweep of silently-dead queries
revived. Most of the value is in things that *stop failing quietly*.

### Added
- **`empirica note`** — a fast scratchpad note-to-self for jotting things to
  revisit, captured mid-flow and triaged at the retrospective. The middle
  ground between a full artifact (friction) and holding a thought in context
  (lost at compaction): transaction-scoped, metadata-only (not shared, not
  embedded), and surfaced at POSTFLIGHT under `untriaged_notes`. CLI:
  `note "…" [--tag followup|doubt|idea]`, `note --list`, `note --clear`; also
  available as an MCP tool for Desktop/Chat AIs.
- **Retrospective soft-gate** — at PREFLIGHT, when the previous transaction made
  substantive praxic tool calls but logged zero epistemic artifacts (on a
  non-mechanical `work_type`), the response surfaces a non-blocking breather to
  log what was learned. Note-aware (notes earn partial credit), env-toggleable
  (`EMPIRICA_RETROSPECTIVE_GATE`), and cleared by logging or by passing
  `retrospective_reason`.
- **MCP↔CLI parity guard** — a test that introspects the real CLI parser and
  fails if any MCP tool maps a flag the CLI no longer accepts, plus a capability
  floor keeping core flags exposed. Replaces stale manual re-verification.
- **SQL schema-reference guard** — a test that validates every static SQL query
  against the real schema, catching references to non-existent columns/tables
  (the silent-`OperationalError`-swallowed-by-broad-except class).
- **Import-budget gate** — a test keeping the CLI and serve `/health` hot paths
  free of eager heavy imports (LLM SDKs, vector store, web stack).

### Fixed
- **Revived 12 silently-dead static SQL queries** whose columns had been renamed
  or removed and whose errors were swallowed by broad `except` — features that
  had quietly no-op'd: calibration-insight bias detection, sentinel goal-scope
  loop sizing, turtle-persona grounding, PREFLIGHT calibration trend, the
  test-pollution goal prune, the daemon artifact insert, unknown-resolve,
  issue-category update, and subtask-importance, among others.
- **PREFLIGHT prior-transaction behavioral feedback** was dead for an unknown
  duration — it queried a non-existent column and the error was swallowed, so
  `previous_transaction_feedback` was always null. Now reads the correct
  `reflex_data`.
- **Project-id resolution** read a non-existent `.empirica/project.json`; all
  sites now route through the canonical, `project.yaml`-authoritative resolver
  (`project.yaml.example` regenerated to the v2.0 schema).
- **MCP param drift** — surfaced `--description` on every `*-log` tool plus
  `--source`/`--cost-estimate`/`--root-cause-vector` and the `goals-create`
  scope/status/success-criteria flags the CLI already accepted; added local
  (cortex-free) goal-lifecycle + read-side logging-query tools.
- **Mesh seat** now persists the strict canonical 3-form into `project.yaml` at
  session init.

### Changed
- **POSTFLIGHT** now persists `work_type` + phase tool-counts into `reflex_data`,
  feeding the next PREFLIGHT's behavioral feedback and the retrospective gate.
- **Removed the monolithic `CLAUDE.md` prompt template + `--full-prompt`** — the
  system prompt is the lean template plus ecosystem `@include`s; non-Claude
  users needing a monolith are out of scope (community-maintained).
- **Reframed context-scarcity language to abundance/retrieval** across the system
  prompt and skills, and documented the note scratchpad there.

## [1.12.2] — 2026-06-19

Serve-daemon hardening and a lighter import surface: the entity-mint and listener
endpoints are now service-token guarded when the daemon binds beyond loopback, the
`openai` embeddings provider drops its SDK dependency, and CLI startup sheds two heavy
imports.

### Added
- **`GET /api/v1/listeners`** — the serve daemon exposes the registered mesh listeners plus heartbeat freshness, merged from the on-disk registry and health markers, so the Chrome extension can flag silent receive failures (a listener that's alive but no longer receiving) without reading `~/.empirica/` directly. Read-only.
- **Service-token auth for the hosted entity-mint endpoint** — `POST /api/v1/entities` (and `GET /api/v1/listeners`) are guarded by an `emk_…` bearer token when the daemon binds beyond loopback. Configure the valid-token set via `EMPIRICA_ENTITY_MINT_TOKENS` (comma-separated, rotation-friendly). The daemon refuses to start when bound to a non-loopback host with no token configured (fail-closed), so these surfaces are never exposed unauthenticated. Loopback (same-box) daemons stay auth-free and unchanged.

### Changed
- **`empirica serve` `/health` reflects the actual configured backends** — Ollama and Qdrant reachability probes now resolve their URLs the same way embeddings does (env var → `~/.empirica/config.yaml` `embeddings.ollama_url` → localhost) instead of always probing hardcoded `localhost:11434` / `localhost:6333`.
- **`openai` embeddings provider is now REST-only** — the `openai` Python SDK is no longer a dependency; embeddings call the `/v1/embeddings` endpoint directly over HTTP. `OPENAI_API_KEY` is still used for auth; no behaviour change for users.
- **Faster CLI startup** — `httpx` and `GitPython` imports are deferred off the CLI startup path, trimming cold-start overhead for commands that never touch them.

### Fixed
- **`listener-on` subscribe tag is canonicalized to the 3-form**, so wake routing addresses the correct practitioner.
- **Listener garbage collection no longer reaps live launchd-supervised workers** on macOS — liveness derives from the running process rather than the launchd plist location.

## [1.12.1] — 2026-06-16

A managed-Forgejo publishing path for projects with no public remote, the compliance-report diagnostics emit (an account-gated free funnel into Cortex's System │ Diagnostics surface), plus a sentinel-gating correctness fix and two listener/hook reliability fixes.

### Added

- **`empirica forgejo-publish`** — provision a managed Forgejo remote for a project and push it up (`empirica/cli/command_handlers/forgejo_commands.py`). The operator / self-hosting provisioning verb for a project with no existing origin (Forgejo's managed pull-mirror can't apply without one to pull from): Cortex mints a per-project, owner-scoped bot token over HTTPS; the token is stashed `0600` under `~/.config/empirica/forgejo-tokens/<uuid>`; a credential-free `forgejo` remote is added (the canonical `origin` repo_url is never touched); and each refspec is pushed to a credentialed URL composed only at push-time and never persisted to git config. Notes-ref wildcards are enumerated and pushed in batches of 250 — a single RPC carrying thousands of note refs 504s at the gateway. 16 tests in `tests/test_forgejo_commands.py`.
- **`empirica compliance-report --emit`** — emit the compliance assessment as a `diagnostics` system event to Cortex (`POST /v1/system/event`), surfacing in the System │ Diagnostics view (`empirica/cli/command_handlers/system_event.py`, `compliance_report_commands.py`). Account-gated free diagnostics: the EU AI Act / GDPR / ISO check results become a shareable, queryable record. 9 tests in `tests/test_system_event.py`.

### Fixed

- **Sentinel no longer over-gates newline-separated `empirica` command chains** (`plugins/claude-code-integration/hooks/sentinel-gate.py`). A multi-line Bash block of individually-noetic `empirica` calls was being classified off its first segment alone; the classifier now splits on newlines (heredoc-guarded so `<< EOF` blocks aren't shredded) and routes each segment independently. The same pass closes a firewall over-allow where a piped `empirica goals-list | sh` slipped through as noetic — pipe segments now route through the pipe-chain classifier. 10 new regression tests.
- **Listener liveness detection decoupled from the launchd plist-file location** — macOS launchd reparents supervised services, so liveness now derives from the running process rather than the plist path, ending a class of false orphan-classification on launchd-managed installs.
- **Hooks resolve the canonical `ai_id`** instead of hardcoding `claude-code`, so per-practice wake routing addresses the correct practitioner.

## [1.12.0] — 2026-06-14

Project identity becomes a single canonical UUID, and project registration + onboarding get a clean, copy-pastable model that the extension's Register UI mirrors. Plus an idempotent contact-mint primitive, the `sources-reconcile` verb (canonical source identity), per-org daemon env support, and listener-GC hardening.

### Added

- **Project identity is a single canonical UUID — slug-as-id migration** (`empirica/core/identity_migration.py`). A project's `project_id` is a UUID, minted once by `project-init`, committed in `.empirica/project.yaml`, and *adopted* (never re-minted) by `project-register`. `.empirica/project.yaml` is the git-intrinsic source of truth; the practice/`ai_id` identity stays the project *name*. Legacy projects init'd before the UUID switch carry a slug `project_id`; the migration engine resolves the canonical UUID (yaml → workspace.db → Cortex when installed) and re-keys it across every `project_id` column in every `.db` under `.empirica/` (schema-introspection — complete by construction, so history doesn't orphan). Resolution policy is Cortex-gated: with Cortex installed the project may already be registered, so an unresolvable case routes the user to `project-register` rather than minting a forked id; without Cortex (purely local) minting is safe. Runs on the `setup-claude-code --force` upgrade ritual (Stage 6.8, non-fatal, no-op when already a UUID), emitting an actionable message when it can't resolve so the rest can be finished by hand. 18 tests in `tests/test_identity_migration.py`.
- **Register + management instruction set for end users** (`docs/human/end-users/REGISTER_AND_MANAGE_PROJECTS.md`). Copy-pastable command blocks for the three register paths — new project (`project-init && project-register .`), already-an-Empirica project (`git clone … && project-register .`), and cloud/Cowork (`cortex_project_register`) — plus the manage-many verbs (`projects-list` / `projects-sync` / `project-update`) and the canonical 3-form addressing convention. Built against real CLI behavior.
- **Idempotent contact mint** — `empirica entity-create` CLI + `POST /api/v1/entities` (loopback) over one `mint_contact()` function. Identity resolution is email-first → deterministic readable slug (`c-<name>[-<company>]`) → 6-hex suffix on a genuine collision; re-minting the same identity is a verified no-op (`created=false`). Backs the contact round-trip for per-org daemon instances.
- **`empirica sources-reconcile`** + **migration 050 (content-identity columns)**. Canonical source identity: `content_hash` / `size_bytes` / `canonical_path` / `mime_type` computed best-effort at `source-add`; the reconcile verb adopts catalogue UUIDs and backfills identity, cascading the re-key across `artifact_edges`, `archive_target_id`, and `project_findings.source_refs`. Catalogue lookups chunk to the 500-hash server cap.
- **`EMPIRICA_SERVE_PORT` + `EMPIRICA_WORKSPACE_DB` env support** (`empirica serve`). Enables per-org daemon instances — a separate `workspace.db` + port per `HOME` — for multi-tenant hosting. The explicit `--port` flag still wins over the env var.

### Fixed

- **`empirica listener gc` reaps orphaned listener processes**, and `listener off` is hardened to reap an ai_id's orphans and delete its state file; a SessionStart warning surfaces when more than three orphans accumulate.
- **`practice-context --ai-id` prefers the caller's own tenant on a slug collision** (`practice_context_commands.py`) — a two-pass own-tenant filter plus dotted→`ai_id_mesh` exact match, so a same-slug peer in another tenant no longer shadows your own row.

## [1.11.11] — 2026-06-11

Mesh-listener reliability + canonical-channel migration. Three convergent fixes that together kill a class of stale-proposal noise and align the client side with the per-tenant ntfy channel model: the orchestration poller no longer loses its seen-proposals state on a transient empty response, the credentials wizard stops seeding the retired bare topic into fresh installs, and a new `mesh migrate-topics` verb rewrites legacy installs in place. Plus a basic-auth path on the `mesh diagnose --cortex` ntfy probe so tenants without a bearer token aren't flagged as a false-negative.

### Fixed

- **Orchestration poll: state merges across polls instead of replacing** (`empirica/core/loop_scheduler/content_poll.py`). The poller's seen-proposals map was rebuilt from scratch each cycle; on a transient empty response from the mesh layer, the rebuilt map was empty, wiping `last_seen` entirely. The next non-empty poll then re-emitted every returned proposal as a "new" wake event — replay of stale proposals the AI had already acted on (and often already archived). Fix: merge new entries into existing `last_seen` rather than replacing. Status changes still emit correctly (the diff compares status, not membership). 4 regression tests cover two-poll memory, transient-empty preservation, drop-and-return resilience, and status-change sanity. 37 in the content_poll suite + 126 across sibling listener suites green.
- **`mesh diagnose --cortex` ntfy READ-grant probe now supports basic-auth tenants** (`empirica/cli/command_handlers/_mesh_diagnose_cortex.py` + `mesh_commands.py`). The probe was bearer-only; tenants whose `credentials.yaml` carries `ntfy.user` + `ntfy.password` (basic-auth, the listener's actual path on those installs) passed `ntfy_token=None`, the probe went no-auth, ntfy returned 403 — false-negative red flag on an otherwise-healthy install. Fix: `_http_head` and `check_ntfy_acl` accept `user`/`password` too; auth precedence mirrors the listener's `_ntfy_auth_header` (bearer wins; basic-auth fallback). 4 regression tests cover both directions + the raw urllib header shape.

### Added

- **`empirica mesh migrate-topics [--apply]`** (`empirica/cli/command_handlers/mesh_commands.py`). Inspects `~/.empirica/credentials.yaml` `ntfy.topic` and every `~/.empirica/listener_active_*.json` marker; detects retired topic forms (bare `orchestration-events`, the pre-tenant per-org form `<org>-orchestration-events`, or per-practice topics lacking the `-orchestration-events-` segment); queries the mesh-layer notification-channels registry for the canonical per-tenant topic; rewrites in place. Preserves the `?tags=<ai_id>` suffix on listener_active markers. Refuses to silently fall back if the canonical topic can't be resolved (exit 2 with actionable error). Dry-run by default; per-target reason rationale in both JSON payload and human render. 19 tests in `tests/test_mesh_migrate_topics.py`.

### Changed

- **`setup-claude-code` credentials wizard no longer prompts for `ntfy.topic`** (`empirica/cli/command_handlers/setup_claude_code.py`). The prior default `"orchestration-events"` is a retired topic with no ACL grant on most installs — every poll would 403, while the listener's runtime resolver path was structurally unreachable behind the explicit-topic setting. The wizard now writes only `{url, token | user+password}`; the listener resolves the per-tenant canonical topic from the mesh-layer notification-channels registry at startup. Credential-state validation drops `topic` from the required-fields list — absence is the new normal.

## [1.11.10] — 2026-06-08

Two tracks land together: substrate work converging the SER tracks (canonical project identity `ser_542199e3` + membrane `ser_4272`) into observable substrate, AND a mesh-listener reliability sweep — two field reports of silent-zombie listener stalls (mesh-support `prop_rbrlwiu7zfgkxm245guu6f2ala`, cortex's own 95-min initial-catch-up stall) drove a hard-exit liveness probe, plus the local⟂cortex cross-correlation verb (`mesh diagnose --cortex`) that lets `ecosystem-update` self-verify after future cutovers, plus a GC pass over stale `listener_active_*.json` markers. SER substrate adds a single-verb atomic register, a workspace backfill verb, a daemon source-content endpoint, write-time visibility ladders, and session-bootstrap mesh-agreements sync.

### Added — Mesh listener reliability & diagnostics

- **`empirica mesh diagnose --cortex [--peer CANONICAL]`** (`empirica/cli/command_handlers/_mesh_diagnose_cortex.py`). Read-only cortex-side participation rollup that cross-correlates the local listener view with cortex's view at one verb so silent-failure classes (label mismatch, topic drift, ACL 403, silent strand) surface together. Five probes: `identity.roster_lookup` (local `ai_id` → `ai_id_mesh` in roster), `channels.orchestration_events` (per-tenant vs PER-ORG/BARE classifier — catches pre-T16/T17 leftover topics), `listener.subscription_match` (`listener_active_*.json` topic vs channels endpoint), `ntfy.read_grant` (bearer-authenticated GET probe of the poll endpoint), and `mesh.agreement` (gated on `--peer`, fails if no `mesh_sharing_agreement` for the named peer pair). Auth: `Authorization: Bearer` matching existing listener + practice-context flows. ntfy probe uses GET-read-1-byte (HEAD unreliable on poll endpoints). Box render word-wraps long messages cleanly. Exit code 0 all pass, 1 any warn, 2 any fail. 24 tests in `tests/test_mesh_diagnose_cortex.py`. Closes cortex's `prop_dd3epjwqyb` ask. Companion field-report ack to mesh-support `prop_rbrlwiu7zfgkxm245guu6f2ala`.
- **`empirica listener gc [--apply] [--age-days N]`** (`empirica/cli/command_handlers/cockpit_commands.py`). Garbage-collect stale `~/.empirica/listener_active_*.json` markers. Three OR'd prune criteria: `legacy_topic` (file pins retired bare `orchestration-events` or pre-T16/T17 per-org form, no `<org>-…-<tenant>` segment), `no_service_or_health` (no systemd-user/launchd unit AND no recent positive-liveness marker), `stale` (`armed_at` older than `--age-days N` (default 7) AND no recent `last_wake_at`). Dry-run by default; per-file reason rationale included in both JSON payload and human render. 14 tests in `tests/test_listener_gc.py`. Closes extension's `prop_d75f2b7c` ask.
- **`empirica/core/loop_scheduler/liveness_probe.py`** — silent-zombie defeater for `empirica loop listen`. Bitten twice in production (mesh-support 2026-06-01; cortex's own listener stuck ~95 min on initial-catch-up 2026-06-08) by a failure mode the existing curl watchdog can't catch: the watchdog (`listener.py:626-662`) is curl-stream-bound and only runs inside the stream loop, so it can't cover the initial `_emit_catchup_events` call AND it can't unblock a main thread hung INSIDE a catch-up HTTP request. The new `LivenessProbe` is a separate daemon thread that owns its own bearer-authenticated GET to `/v1/users/me/roster` (same lightweight probe `diagnose --cortex` uses), calls `os._exit(2)` on N consecutive misses past the staleness threshold (bypasses Python cleanup so supervisor restart works even when other threads are hung in HTTP syscalls), and writes the existing positive-liveness marker (`~/.empirica/listener_health_<ai_id>.json`) on every success — decouples `mesh status` health view from the catch-up cycle so quiet-but-healthy listeners stay green even when no ntfy events arrive. Env overrides: `EMPIRICA_LIVENESS_PROBE_{INTERVAL,FAIL_THRESHOLD}_SEC` (defaults 60s / 240s), `EMPIRICA_LIVENESS_PROBE_DISABLE`. Started BEFORE initial catch-up so the catch-up-hang case is covered from second 1. 18 tests in `tests/test_liveness_probe.py`. Closes mesh-support `prop_rbrlwiu7zfgkxm245guu6f2ala`.

### Fixed — Mesh listener reliability & diagnostics

- **`_resolve_canonical_ai_id` honors cwd project.yaml + env override** (`empirica/cli/command_handlers/cockpit_commands.py`). The implementation was skipping three of the five priority levels its own docstring claimed, jumping straight from `args.ai_id` to the session-bound `InstanceResolver.ai_id()` — which can return the GLOBAL active-instance pointer when the caller is in a DIFFERENT practice's cwd. Symptom (ecodex `prop_sdjcbttkcneptjatmvsc5tmkbq` + parent `prop_3pptt`): practitioner running from `cwd=~/empirical-ai/ecodex-lab` was getting identity `ecodex` (whichever session was last bound) instead of `ecodex-lab` (declared in cwd's project.yaml). Fix mirrors `session-init.py:_resolve_ai_id_for_session` (1.11.8) — new priority chain: (1) `--ai-id` flag → (2) `EMPIRICA_AI_ID` env → (3) `<cwd>/.empirica/project.yaml` → (4) `basename(cwd)` strict-canonical → (5) `InstanceResolver.ai_id()` → (6) `None`. 6 new tests directly cover the chain (explicit-flag, all-empty→None, env-wins-over-cwd, reads cwd project.yaml [lab→ecodex-lab case], basename fallback with prefix kept, InstanceResolver as last resort) + 3 sibling tests updated to exercise the all-paths-blocked condition. Single blocker for registering ecodex-lab as a self-identifying mesh practitioner is now removed.

### Changed — Mesh listener reliability & diagnostics

- **Provisioner self-heal + watchdog cross-references positive-liveness marker** (`empirica/core/loop_scheduler/persistent_listener.py`, `empirica/cli/command_handlers/mesh_commands.py`). Provisioner now removes orphan short-basename systemd units when an `ai_id` migrates to canonical form (the leftover legacy unit kept holding a stale subscription); watchdog now reads the freshness of `listener_health_<ai_id>.json` before flagging "no fires in N min" as zombie-suspected, so quiet-but-healthy listeners with a fresh positive marker stay green. Both fixes pair with the new `LivenessProbe` (which is the marker writer in 1.11.10): together they kill the watchdog-false-positive class noted in mesh-support's parallel field report.
- **`inbox-listener` skill — per-tenant topic resolution** (`empirica/plugins/claude-code-integration/skills/inbox-listener/SKILL.md`). Updated guidance to reflect the T16/T17 per-tenant `<org>-orchestration-events-<tenant>` topic shape; the bare `orchestration-events` topic is documented as retired and surfaced as a `legacy_topic` prune candidate in `listener gc`.

### Added — Daemon endpoints

- **`POST /api/v1/credentials/ntfy`** + **`GET /api/v1/credentials/ntfy`** (`empirica/api/serve_app.py`). Mirror of the cortex credentials endpoint pair, closing the round-trip credential model on the ntfy side — extension's "Also save to CLI" toggle on the Notifications tab now writes the user's ntfy bearer to `~/.empirica/credentials.yaml` via `CredentialsLoader.save_ntfy_config` (atomic tempfile+rename). Body shape: `{url?, token?}` — at least one required. `topic` is INTENTIONALLY off the shape; cortex's channels endpoint owns topic derivation, so partial-updates from this endpoint must never clobber an existing topic key. NEVER returns the full token over the wire (`token_preview` is last-4-chars only — same threat model as the cortex pair). 8 tests in `tests/test_serve_credentials_ntfy.py` covering writes-both, both partial-update directions, missing-fields error, never-leaks-full-token, doesn't-clobber-cortex-block, GET parity, GET-on-empty. Refactor: credentials endpoint registration extracted into `_register_credentials_routes(app)` so `create_serve_app()` stays under the C901 ceiling. Closes extension's `prop_kzpafwoykbae3lsikvuhxy5r4e`.

### Added — SER substrate

- **`empirica project-register [PATH]`** — V1.5 single-verb atomic single-project register. Replaces the brittle chain of `projects-discover --register NAME && projects-bulk-register --include NAME` with one verb optimised for the AI-as-CLI-user / copy-prompt UX (extension's Discover/Register surface design). Sequence: read `.empirica/project.yaml` at PATH → dual-write workspace.db (`global_projects` + `entity_registry`) via `_register_in_workspace_db` → upsert `~/.empirica/registry.yaml` → POST cortex `/v1/projects/register` with local `project_id` in the payload (so the planned adopt-local-UUID slice reconciles back to the canonical UUID). Exit code contract: `0` local + cortex shipped, `1` local writes never started (actionable config error), `2` local shipped + cortex POST failed (re-runnable; local state stays consistent). Divergent `project_id` surfaced via `cortex.diverged=true` + `cortex.local_project_id` for extension's zone-2 diagnostic (`prop_twit75oxir`). 9 tests in `tests/test_project_register.py`. Goal `1475407d` closed. Tier C of SER `ser_542199e3`.
- **`empirica workspace-backfill-entities` verb** — populates `workspace.db.entity_registry` with `entity_type='project'` rows for every existing `global_projects` row. Idempotent (UPSERT on PK), `--dry-run` preview. Closes mesh-support's audit Break 2 (`prop_houwq47gu`): the Practice Model surface (extension dashboard, `entity-list/-show/-walk`) was reading from a table that no project had ever been written into. Live verified: 33 projects backfilled in one pass.
- **`GET /api/v1/sources/{source_id}/content`** daemon endpoint (`empirica/api/routes/artifacts.py`). Closes extension's source viewer gap (`prop_fzb63fnlx5` TACTICAL + `prop_bcsecxo2rr`): daemon previously served metadata only via `/api/v1/sources`, so the viewer rendered empty when a user clicked through. Returns `{kind: "url", url, title, source_type}` for http/https sources (client fetches directly, no proxy) OR `{kind: "file", path, content, size_bytes, encoding, title, source_type}` for local-path sources (utf-8 for text, base64 for binary). Path resolution walks fallback prefixes (`""`, `.empirica/sources/`, `docs/`, `docs/sources/`) against the project root, first match wins. Absolute paths accepted only when inside the project tree. Defense-in-depth: project-root containment refuses `../` traversal with 422. 10MB content cap with `truncated=true` marker for larger files. 12 tests in `tests/test_serve_sources_content.py`. Extension shipped v0.8.71 consuming it within minutes.
- **`empirica/core/visibility.py`** — write-time mesh-sharing-agreement check. `resolve_visibility_with_agreement(intended) → (resolved, warning)` encodes the layer mapping (`shared` → L2, `public` → L3), graceful step-down (`public` with only L2 → `shared`, not `local`), and empty-mirror fail-open semantics (unbootstrapped mirror keeps intent without warning; cortex enforces authoritatively on consumer side). Hooked into `artifact_log_commands._extract_scalar_fields` so every `*-log` invocation (finding/decision/unknown/deadend/mistake/assumption) runs the check immediately after pulling `--visibility` from CLI or config. Warning emits to stderr; the artifact still writes at the downgraded tier. Same shape as the praxic-attempt-without-CHECK firewall. 12 tests in `tests/test_visibility_ladders.py`. Tier B.3 of SER `ser_4272`.
- **`workspace.db.entity_registry` mirror for projects** (`empirica/cli/command_handlers/workspace_init.py`). `_register_in_workspace_db` now dual-writes `global_projects` AND `entity_registry` atomically on the same connection. Same UUID + name + description in both tables; metadata JSON carries `git_remote_url` + `project_type` + `trajectory_path` for the Practice Model row. Closes the audit gap surfaced by mesh-support `prop_houwq47gu`: extension/Practice Model queries `entity_registry` but no project rows ever got written there. Companion fix: `project_commands.ensure_workspace_schema` gains `entity_registry` + `entity_memberships` CREATE TABLE statements, matching `workspace_db.py:_ensure_workspace_schema` exactly (was schema-drifted). 9 tests in `tests/test_workspace_entity_mirror.py`. Tier A of SER `ser_542199e3`.
- **Mesh-agreements bootstrap sync** (`empirica/cli/command_handlers/project_bootstrap.py`). Wires trigger #1 of the `MESH_SHARING_AGREEMENTS.md` sync contract: `project-bootstrap` now refreshes the `workspace.db.entity_registry` mirror of `mesh_sharing_agreement` rows from cortex on every session start via `core.mesh_sharing.sync_from_cortex`. Non-fatal — any failure (no creds, cortex unreachable, transport error, unexpected exception) downgrades to a debug log and the cached mirror keeps serving the visibility ladders check. Cortex side LIVE since `45e1227` (2026-06-03). 4 tests in `tests/test_bootstrap_mesh_sync.py`. Trigger #2 (`<org>-mesh-sharing-changed` ntfy push subscriber) remains under goal `b22d506d` task B.5 as a focused follow-up.
- **`docs/architecture/MESH_SHARING_AGREEMENTS.md` two-stage resolution model** documented: write-time advisory check (this release) + consumer-side authoritative enforcement (cortex). Empty-mirror fail-open semantics surfaced. ntfy LIVE status (`cortex 45e1227`) under Sync triggers. New "Policy fields — cortex's lane" section per extension `prop_phtal3svmj` correction: policy schema is cortex's call (extra columns on `mesh_sharing_agreements` vs sibling `agreement_policies` table); empirica mirror extends when ready.

## [1.11.8] — 2026-06-04

A hotfix completing the anchor refactor that landed in 1.11.7. Surfaced live by ecodex-lab (a codex/Kimi-hosted practice) within an hour of 1.11.7 publishing: `session-init.py:1246` still resolved `ai_id` from `os.getenv('EMPIRICA_AI_ID', 'claude-code')` — env var or silent `claude-code` fallback. The hook never read the canonical `.empirica/project.yaml` `ai_id` field that every other consumer surface uses, so every non-Claude-Code harness's session was created under `ai_id='claude-code'` regardless of declared practice. Wrong mesh identity meant `cortex_propose(target=<practice>)` was never picked up — silent delivery failure.

### Fixed

- **`session-init.py` resolves `ai_id` via canonical chain** (`empirica/plugins/claude-code-integration/hooks/session-init.py`). New `_resolve_ai_id_for_session()` helper with precedence: `EMPIRICA_AI_ID` env (explicit override, preserved for launch-config callers) → `.empirica/project.yaml` `ai_id` field (declared practitioner) → `basename(project_root)` (canonical anchor, `empirica-` prefix kept per 1.11.x strict-canonical) → `'claude-code'` with stderr warning (final fallback, surfaces what was previously silent). Replaces the env-or-hardcode lookup at the single bind point. Per ecodex `prop_vwmutw7nu`. 10 tests covering env override, yaml precedence, basename fallback, prefix preservation, empty-string env handling, missing yaml/None root warnings, malformed yaml graceful degradation. Plugin mirror synced.

## [1.11.7] — 2026-06-03

A patch consolidating the ecodex onboarding-substrate work surfaced this week. Three threads land together: project-init recovery paths for sandboxed harnesses (read-only `.git/` mounts and prior-provisioned identities), an anchor-model refactor that promotes `ai_id` to THE canonical identity and demotes cwd to working-context, and an idempotent migration that heals legacy stripped-prefix `ai_id` values in `.empirica/project.yaml` to their canonical exact-basename form.

### Added

- **`docs/architecture/AI_ID_AS_ANCHOR.md`**. Canonical model doc: `ai_id` is THE anchor for cross-machine portability; cwd is just working-context. Covers the full resolution chain (`InstanceResolver.ai_id`), the provisioning hook (`.empirica/project.yaml` `ai_id` field as the lever for non-default identity, used by sandbox harnesses like ecodex), guidance on legitimate vs illegitimate cwd usage, and worked examples. Cross-ref added from `docs/guides/PROJECT_SWITCHING_FOR_AIS.md`.
- **`mcp__empirica__mailbox_reply` documented in `/cortex-mailbox-send` skill**. The MCP-equivalent of `empirica mailbox reply` (added in 1.11.3) is now documented as the canonical reply primitive for non-CC harnesses (Claude Desktop, Codex CLI, ecodex, anything MCP-only). Slots between the CLI canonical block and the raw `cortex_complete_proposal` fallback. Plugin mirror synced.

### Changed

- **`InstanceResolver.ai_id()` accepts optional `project_path` parameter** (`empirica/utils/session_resolver.py`). When provided, bypasses the resolver chain entirely — lets callers iterating known paths (cockpit per-instance ai_id rendering, project-init at provisioning time) delegate to the single canonical resolver instead of re-implementing `basename.removeprefix('empirica-')` locally. Four call sites migrated: `cockpit_app.py:784,947`, `instance_state.py:_project_ai_id`, `project_init.py:_derive_ai_id`. When `project.yaml` carried an explicit `ai_id` (the ecodex provisioning case), those four sites previously silently used folder-basename instead, breaking practice persistence across filesystems. Now honored everywhere.
- **`release.py` regenerates `CLI_COMMANDS_UNIFIED.md` during prepare** (`scripts/release.py`). The doc's "Framework version" header is generated by `scripts/generate_cli_docs.py` reading `empirica.__version__`; without regeneration in the release pipeline it lagged releases by one version (1.11.3 → 1.11.6 drift caught this week). New `ReleaseManager.regenerate_cli_docs()` runs after `sync_readme_whats_new` so the bumped `__version__` is picked up. Non-fatal on generator error.

### Fixed

- **Project-init recovery on read-only `.git/` mounts** (`empirica/cli/command_handlers/project_init.py`). Sandboxed harnesses (ecodex `prop_jnqs2l4l`) pre-mount `.git/` read-only; `git init` then raised `EACCES` and the outer handler swallowed it as a generic error with no actionable hint. `_ensure_git_root` now (a) honors a `--project-id` shortcut — when the caller already has a workspace identity from prior provisioning, skip the `git init` dance and use cwd as the anchor; (b) catches `CalledProcessError` around `git init` and emits an actionable recovery message in both JSON and human form, detecting read-only `.git/` heuristically via `os.access(W_OK)`.
- **`session-create --auto-init` wires `instance_projects`** (`empirica/cli/command_handlers/session_create.py`). Companion bug to project-init recovery (ecodex `prop_zwfsl26r7fc7ddj6oemkfcwa44`): `_handle_auto_init` created the project but never persisted its path where the resolver chain could see it; `_write_tty_session` then called `R.project_path()` (which returned `None` for the brand-new project) and silently no-op'd. Subsequent commands hit "Cannot resolve project path." `_handle_auto_init` now returns the just-created project_path as a third tuple element; `_write_tty_session` accepts a `project_path_override` and forwards it so `instance_projects/{instance_id}.json` gets written against the just-created project.
- **`.empirica/project.yaml` `ai_id` heal at session-boot** (`empirica/plugins/claude-code-integration/hooks/session-init.py`). Legacy projects init'd before the 1.11.x strict-canonical decision carry stripped-prefix `ai_id` values (e.g. `ai_id: extension` instead of `ai_id: empirica-extension`), which cortex's strict-canonical addressing bounces as `delivery_failed`. New `_heal_project_yaml_ai_id_at_init` mirrors the existing `_heal_project_yaml_project_id_at_init` pattern with conservative rules: heal stripped → canonical, leave custom provisioner values untouched (ecodex sandbox identity etc.), leave absent ai_id alone (project-init handles introduction). Idempotent. Pairs with the existing `(empirica-)?` transition-compat Monitor-grep regex which can be tightened to exact-match once installed practices migrate forward.

## [1.11.6] — 2026-06-03

A hotfix for a follow-on bug surfaced by extension (`prop_3p6iiqz`): the listener's `instance_id` in `loop_fires.log` is now the full project basename (`empirica-extension`) while some legacy `.empirica/project.yaml` entries still carry the stripped form (`extension`). The SessionStart Monitor's grep filter was anchored on the project.yaml value, so Monitors armed against stale project.yamls silently stopped matching at 10:20 UTC today — log fills, Monitor delivers nothing.

### Fixed

- **Persistent-service-tail Monitor grep accepts both forms** (`empirica/cli/command_handlers/cockpit_commands.py`). Filter changed from literal `'"instance_id": "<ai_id>"'` to regex `'"instance_id": "(empirica-)?<basename>"'` so the Monitor matches whether the project.yaml has been migrated to the canonical exact basename or still carries the legacy stripped form. Transition-compatible — no project.yaml migration required for Monitors to resume delivery. Surfaced by extension `prop_3p6iiqz` minutes after 1.11.5 published.

### Note

The right structural follow-up is migrating stale stripped-form `ai_id` in `.empirica/project.yaml` to the canonical exact basename (e.g. `extension` → `empirica-extension`). That's a separate sweep — not blocking because this Monitor fix accepts both forms.

## [1.11.5] — 2026-06-03

A follow-up patch completing the canonical-3-form alignment across all listener / sender paths. Cortex's `800683e` deploy retired the basename/alias bridge in both the publish tag set AND the orchestration-fetch path, leaving listeners that subscribed by basename silently push-deaf (only catch-up worked). Skill still taught basename `source_claude`. Rolled up here.

### Fixed

- **Listener ntfy subscription now uses canonical 3-form tag** (`empirica/core/loop_scheduler/listener.py`). The subscribe URL was built with `?tags=<basename>` (`empirica`, `extension`, etc.) while cortex publishes with `?tags=<canonical>` (`empirica.david.empirica`, `empirica.david.empirica-extension`, etc.) since the bridge retirement. NO match → live pushes silently dropped → only the catch-up poll (fixed in 1.11.4) caught anything. Resolver reuses the same `_resolve_canonical_ai_id` helper. Verified live: all 5 listeners on this box now subscribe canonical post-restart. Surfaced by mesh-support's `prop_dluul` item 3.

### Changed

- **`/cortex-mailbox-send` skill: `source_claude` strict-canonical guidance**:
  - "Your own `source_claude`" section rewritten: was "set source_claude to your fully-qualified org.tenant.project canonical triple" but soft about consequences ("2-level slug and short alias still resolve in transition"); now explicit that cortex's router rejects non-canonical sources with `status=failed` silently. Worked construction example added: read project.yaml, prepend `<org>.<tenant>`.
  - All `source_claude=<your-ai-id>` placeholders → `source_claude="<your-canonical-3-form>"  # e.g. "empirica.david.empirica-mesh-support"`.
  - Concrete example `source_claude="empirica"` → `source_claude="empirica.david.empirica"`.
  - Anti-pattern table: added a new row for basename / short-alias `source_claude` showing the silent-fail mode + correct canonical form. Stripping-prefix row updated to reflect strict canonical (was "works in org-empirica alias resolves, silently fails cross-org" — now "bounces via `delivery_failed` always").
- **Plugin mirror** at `~/.claude/plugins/local/empirica/skills/cortex-mailbox-send/` synced.

### Note

Surfaced by mesh-support during cross-tenant addressing work: their first sends to Philipp failed with `status=failed` (no surface), then succeeded after switching `source_claude` from `"mesh-support"` to `"empirica.david.empirica-mesh-support"`. Same silent-break pattern as the listener fix in 1.11.4 — diagnosable only by comparing a succeeding peer's send to a failing one.

Also a historical-cruft cleanup pass on both mailbox skills: dropped date-tagged narrative ("2026-06-02", "2026-06-03 cortex deploy"), commit-SHA references ("commit 629bb29"), transition wording ("deprecated for collab — B.2 fast-follow hard-excludes"), and pre-T8 / older-version compatibility paragraphs. Skills now describe current state only.

## [1.11.4] — 2026-06-03

A critical-bugfix patch shipping the same day as 1.11.3. **Recommend immediate upgrade for any user running a persistent listener** — fleet-wide silent wake-event break since the cortex strict-canonical addressing rollout (commit `629bb29`, 2026-06-02). Also rolls up a system-prompt / skill consistency pass surfaced by mesh-support's `prop_l4behx3jl` while debugging cross-tenant addressing.

### Fixed

- **CRITICAL: Listener orchestration fetch now resolves canonical 3-form ai_id before GET** (`empirica/core/loop_scheduler/content_poll.py`). Cortex's `/v1/orchestration/{inbox,outbox}` endpoints require `<org>.<tenant>.<project>` since 2026-06-02; listeners were passing the bare basename → cortex returned **0 proposals silently** → catch-up emitted nothing despite ntfy events arriving → every practitioner went deaf to mesh wakes. Symptom in logs: `listener: ntfy event arrived → running catch-up` lines without subsequent `proposal_event` JSON lines. New `_resolve_canonical_ai_id()` reads cortex `/v1/users/me/roster`, maps basename → `ai_id_mesh`, cached per-process (listener restart drops cache via version-drift self-relaunch). Handles both root (`empirica`) and prefix-stripped basenames (`extension` → `empirica-extension`) via fallback. Verified live: 50/45/89/19/22 catch-up events emitted on empirica/autonomy/extension/mesh-support/outreach after restart. Cross-tenant wakes (Philipp ↔ David) recover on the same code change.

### Changed

- **AI_ID convention in templates + skills aligned with strict-canonical** (per mesh-support `prop_l4behx3jl`, ECO-accepted via Homer auto-mode). Templates + skills used to teach "strip the `empirica-` prefix" as the ai_id, contradicting the strict-canonical model David ratified in `629bb29`. They now teach the **exact project name** (prefix kept) as the ai_id; shorter aliases (`cortex`, `outreach`, `mesh-support`) explicitly documented as chat-layer shorthand that lives in `*-org-prompt.md`, NOT on the wire. Updated surfaces:
  - `empirica-system-prompt-lean.md` (rendered template)
  - `CLAUDE.md` (full template)
  - `/cortex-mailbox-poll` skill (AI_ID convention + resolution code)
  - `/cortex-mailbox-send` skill (canonical-form table — "Older 2-level slugs ... transition-compatible" wording replaced with "Strict canonical — no lenient resolution anymore"; wrong-values-to-avoid table now flags bare slugs + stripped prefixes as wrong)
  - Plugin mirrors at `~/.claude/plugins/local/empirica/skills/` synced

### Note

- Knock-on cleanup tracked separately: existing `.empirica/project.yaml` `ai_id` fields set to the stripped form (David's own `mesh-support`, etc.) still work because cortex resolves aliases by `user_id`. A consistent canonicalization pass (project.yaml `ai_id` = exact project name across the org) is the clean follow-up — non-urgent because the listener fix above closes the silent-break, but worth a sweep before SER state hits 3-way enforcement.

## [1.11.3] — 2026-06-03

A patch release dominated by hygiene: legacy `claude-code` literals retired from internal sentinel + cache + canonical-git layers, the MCP CLI surface gets a deep refresh, and the empirica-mcp package gains 13 mesh primitives so non-Claude-Code harnesses (Claude Desktop, Cursor, Gemini CLI, Codex) can reach the full tool surface. New `practice-context` CLI for the Ambassador addressbook (lane 2 of cortex `prop_7r5tihxyqr`).

### Added

- **`empirica practice-context` CLI** — Ambassador addressbook. Reads cortex's `/v1/users/me/roster` and renders per-practitioner rows with substrate annotation (`cortex|git|local`). Use it to verify the canonical 3-form (`<org>.<tenant>.<project>`) before emitting `target_claudes` — `ai_id_mesh` field on each row is the exact string the resolver accepts. Lane 2 of David's Ambassador design-of-record (`prop_7r5tihxyqr`, ratified 2026-06-02). Unblocked by cortex's Lane 1 substrate ship (`ac47e66` + `ace05e4`).
- **13 mesh primitives registered in empirica-mcp `TOOL_REGISTRY`** — `practice_context`, `commit_context`, `listener_on`/`arm`/`off`, `loop_register`/`heartbeat`/`status`/`schedule_next`, `notify_emit`, `mailbox_reply`, `mesh_status`. Tool count 57 → 70. `_build_cli_command` now splits multi-token cli strings (e.g. `"listener on"`, `"loop register"`) so subcommands work.
- **`requires: cortex` marker on `TOOL_REGISTRY` entries** — surfaces the empirica/cortex boundary in `mcp-list-tools` output. 4 cortex-orchestrated tools (`practice_context`, `mailbox_reply` strict; `listener_on`, `mesh_status` partial) rendered with 🌐 prefix + legend; the other 65 work standalone. Base empirica users see clearly which tools need the mesh backend.
- **`docs/human/end-users/MCP_FOR_DESKTOP_HARNESSES.md`** — new ~320-line guide covering pipx install, per-harness mcp.json configs (Claude Desktop, Cursor, Gemini CLI, Codex), the 70-tool inventory, self-enforcement model for the noetic firewall without hooks, canonical 3-form addressing, troubleshooting. Fills the on-ramp gap for non-Claude-Code clients.

### Changed

- **Sentinel + cache + canonical-git layers derive `ai_id` from `InstanceResolver`** — `statusline_cache.py:from_dict`/`update_partial`, `monitor_commands.py`, `project_commands.py`, `canonical/empirica_git/source_store.py` + `checkpoint_manager.py` no longer hardcode `'claude-code'` as the ai_id default. Cache entries from different practitioners on the same machine now carry the correct practice identity. `from_dict` honestly returns `''` when the field is missing (we don't substitute our own identity for the writer's); `update_partial` derives via the canonical chain because we DO know who we are at write time.
- **CLI surface examples** — argparse defaults and help-text examples migrated from `claude-code` to `empirica` (or generic enumeration of canonical ids). `onboard.py` handler chains through `InstanceResolver` instead of returning `'claude-code'` as fallback. Differentiated 5 legitimate other-layer references (frontend selection, source platform, dispatch-bus instance_id, git-notes messaging) — those KEEP.
- **Docs naming sweep** — 13 architecture + guide + developer docs updated to use `empirica` as the canonical example ai_id throughout. `EVENT_LISTENER.md` migration section now describes the `InstanceResolver` chain instead of the retired `'claude-code'` fallback.
- **`empirica-mcp/README.md` tool count** — `44 tools` → `70 tools` (post-refresh) with the new mesh primitives inline.
- **`mcp.json` template description** — adds pipx prerequisite note + pointer to the new desktop-harnesses doc.
- **`mcp-list-tools` rebuilt** — reads `TOOL_REGISTRY` from the installed `empirica-mcp` package and groups by prefix dynamically. Was hardcoded with a stale snapshot of tool names (e.g. `discover_goals` — doesn't exist in the current registry). Now reflects what the running server actually exposes.
- **`MESH_SETUP.md` Step 7** — verify-end-to-end section now leads with `practice-context` as the canonical-slug lookup before the test send. Common mistake closed.
- **`/cortex-mailbox-poll` skill** — `ser_escalation` event_type handler added (cortex Phase 3 escalation tick — env-gated by `CORTEX_SER_ESCALATION_ENABLED`, default OFF). Teaches discriminator (`escalation=true`, `source_claude=system:ser-escalation`), SER projection fetch, act-or-ack response shape, §5.3 silencing property, defer-as-goal pattern. Canonical + plugin mirror updated in lockstep.

### Fixed

- **Listener tests no longer require cortex credentials in CI** — `tests/test_listener_on_arm_off.py` autouse fixture mocks `empirica.core.cockpit.notification_channels.resolve_orchestration_events_topic` so the 8+ `handle_listener_on_command` tests don't fan out to cortex's `/v1/users/me/notification-channels`. CI develop went from red to green; un-blocks every PR that touched the listener path (incl. dependabot `actions/download-artifact@v8`).

### Removed

- **5 obsolete MCP CLI lifecycle commands** — `mcp-start`, `mcp-stop`, `mcp-status`, `mcp-test`, `mcp-call` targeted a dead path (`mcp_local/empirica_mcp_server.py` — file removed long ago, only historical archive remains). Lifecycle ownership now lives in:
  - `pipx install empirica-mcp` (installation)
  - Harness `mcp.json` files (startup, env, stdio piping)
  - Harness MCP debug surface (status check)
  Only `empirica mcp-list-tools` (rebuilt to read the live registry) remains.

### Note (planned, deferred to a later release)

- **Mesh backend protocol spec (Option C)** — publishing the HTTP API cortex implements as a public spec so OSS can build alternative backends. Logged as a planned goal, blocked on SER `ser_4272a07793` lifecycle locking in. The play mirrors AT Protocol / Bluesky: spec open, production-quality requires real engineering (autonomy + extension + mesh-support is the proof).

## [1.11.2] — 2026-06-02

Code-side cleanup completing the bead-v0 retirement. Cortex shipped its lane on 2026-06-01 (`b6071ff` — see SER spec at empirica-cortex `85e5e46`); this release retires the remaining empirica-side bead surface so residual emits don't get silently dropped by cortex's stricter validation.

### Removed

- **`bead` node type from `graph_commands.py`** — `NODE_REQUIRED_FIELDS['bead']`, schema docs, `CREATION_ORDER` entry, the `db.log_bead` invocation path, and the `beads` node-table mapping. Cortex's `EDGE_RELATIONS` now rejects bead nodes at validation — any residual local emits would have silently failed at the sync boundary; this commit ensures empirica can't produce them in the first place.
- **v0 bead edge relations** (`tracks`, `owned_by`, `about`, `worked_by`) from `VALID_RELATIONS`. Cross-practitioner role semantics now live on cortex's SER `participants` table, not as edge metadata on artifact-graph nodes. Tests inverted to assert the retirement is complete.
- **`beads` group from `_workflow_postflight._CORTEX_GRAPH_SPECS`** — `/v1/sync` no longer ships bead artifacts to cortex.
- **`bead_id` + `bridge_position` fields from listener wake events** (`content_poll.py:ProposalEvent`). The padding produced `"bead_id": null, "bridge_position": null` on every push event; cortex stopped stamping these in `b6071ff`, so the empty fields were dead weight. Removed from the dataclass, the `to_log_line` JSON shape, and the `_extract_bead_id` / `_extract_bridge_position` helpers.

### Added

- **`'blocked'` to `goal.status` enum** — `goals_schema.py` (schema comment), `repositories/goals.py` (initial-status validation), and CLI flags on `goals-create --status` + `goals-list --status`. Per the SER spec §migration: a per-practitioner goal can now mark itself as blocked when waiting on a peer / external dependency, without needing cross-practitioner coordination state (that lives in cortex's SER).

### Changed

- **`/cortex-mailbox-send` skill final bead vocab residuals** — the intro "Plus a fourth concept — beads" paragraph and three When-to-Use table rows that still pointed to `log-artifacts`-based bead creation are now SER-keyed. Anti-pattern table rows about bead-graduation collapsed into the existing `payload.action='create_ser'` pattern.

### Note (cortex / mesh-support side, separate repos)

- Cortex shipped `b6071ff` retiring `/v1/beads` endpoint, the artifact-graph bead schema, the `bead_receipts` table, and `bead_escalation` module — total ~1100 LOC removed.
- ntfy server-side rate-limit bump (per-IP → per-token, 30→100 subscriptions, 60→300 burst, 12→60 req/min) shipping in parallel for the unrelated fleet-wide 429 storm that surfaced post-1.11.1; verified 0/min curl 429s post-restart per cortex's `prop_7ds4xvcphz`.

## [1.11.1] — 2026-06-01

Hotfix for a misleading diagnostic that surfaced 90 minutes after 1.11.0 shipped: `mesh status` and `mesh diagnose` reported "curl subscription dead" for listeners that were intentionally in a 30-min ntfy 429 backoff window (curl killed by the Track 5 rate-limit handler from 1.11.0, sleeping until the limit lifts, catch-up poll still flowing). mesh-support's escalation interpreted the misleading status as a watchdog gap, but the watchdog handles a different failure mode (alive-but-stale curl); the actual issue was UX, not behavior.

### Fixed

- **`mesh status` and `mesh diagnose` now distinguish backoff states** — new `_detect_backoff_state()` reads the recent tail of `~/.empirica/logs/listener-<ai_id>.log` and reports `YELLOW "rate-limited — curl absent during 30-min backoff; catch-up poll still running"` instead of `RED "curl subscription dead"` when a 429 backoff explains the curl absence. Auth/4xx/5xx backoffs get their own YELLOW state too. RED is reserved for genuine curl-can't-spawn outages.

### Known issue (escalated, not addressable from empirica side)

Fleet-wide 429s suggest cortex's `ntfy.getempirica.com` per-IP / per-user rate limit is too tight for the active listener pool that 1.11's persistent listener architecture creates. Resolution requires server-side configuration on cortex's ntfy host — either bumping the limit or issuing per-listener tokens with separate buckets. Tracked in the mesh-support escalation thread (`prop_6wrrlvk2yj`).

## [1.11.0] — 2026-06-01

A substantial minor release. Three threads converge: (1) the user-facing doc surface for the cross-AI mesh gets a conceptual entry point (MESH_CONCEPTS.md) and the v0 bead concept retires in favor of the Shared Epistemic Record (SER) primitive landing cortex-side; (2) the listener substrate gains real reliability — `empirica mesh` diagnostic command, in-process curl-zombie watchdog, ntfy 429 detection; (3) the Qdrant relevance layer carries a unified `created_at` field across every temporal collection so the cortex serving-side composition-C decay applies uniformly. Cortex's Mesh Routing Protocol v0 lands four-way (empirica + cortex + extension + mesh-support) in the same window.

### Added

- **`empirica mesh` command cluster** — unified diagnostic + control surface across listener instances and the optional cortex bridge layer. Verbs: `status`, `diagnose`, `restart`, `on`, `off`, `tail`. Auto-flags the silent-zombie pattern (service active + curl alive + no fires for 30+ min) as RED. Reports two layers distinctly so empirica-core users see local-only diagnostics without cortex noise; cortex-configured users see both.
- **Listener self-heal watchdog** — `empirica/core/loop_scheduler/listener.py` now spawns a daemon thread per stream that terminates curl when no activity is received for `EMPIRICA_LISTENER_STALE_THRESHOLD_SEC` (default 120s). Catches the silent TCP-zombie failure mode where the curl subprocess stays alive on a dead socket. Forces the outer reconnect loop.
- **ntfy HTTP 429 detection + rate-limit backoff** — curl args switched to `-sSN -i` so HTTP status surfaces; new `_read_http_status()` parses the response code. On 429, the listener applies a long backoff (`EMPIRICA_LISTENER_RATE_LIMIT_BACKOFF_SEC`, default 1800s) and continues the catch-up poll every 300s during the window so events keep flowing via the pull path. Previously the rate limit was silently swallowed and the listener thrashed in 5-min auth-fail loops.
- **`created_at` on every temporal Qdrant collection** — eidetic, memory (subtasks), decisions, assumptions, episodic, and goals all now write + project a standardized `created_at` field at embed time. Unblocks cortex's composition-C age-based decay (`tau_days = 30*(1 + 2*impact)`, prod `96bff49`) across the full artifact set rather than just memory. Cortex's fallback chain (`created_at → timestamp → first_seen`) handles existing rows during transition.
- **MESH_CONCEPTS.md** (`docs/human/end-users/`) — conceptual entry point for the cross-AI mesh. Frames practitioner/practice as the load-bearing architectural choice (practices persist; practitioners are fungible LLMs that inhabit them), and lays out the epistemic envelope — what actually rides the wire between AIs beyond just text (calibrated vector state, source-tagged provenance, noetic/praxic intent, workflow arc, trust gate, coordination state).

### Changed

- **`addresses_goal` → `attached_to`** on the empirica-emitted goal-anchor edge. Cortex's `EDGE_RELATIONS` set didn't recognize `addresses_goal`, so the entire graph submission was rejected — empirica's per-artifact goal-anchor edges were silently dropped from `/v1/sync` graph branch. `attached_to` is cortex's existing any-to-goal relation, semantically equivalent, recognized at validation. One-line rename in `_workflow_postflight.py`; cortex accepts both for a transition window.
- **`/cortex-mailbox-send` Flavor 3 rewritten** — bead-as-artifact-graph-node model retired in favor of SER (cortex-resident shared-state object). Documents the shipped `payload.action='create_ser'` + `ser_spec` wire shape, response shape (`proposal_id`, `ser_id`, `ser_state_verified`), participant role tiers (`required`/`participating`/`observer`), state lifecycle, AFK-ambassador pattern (`proxy_actor`), and the `/v1/sers` read endpoint patterns including `?thread_id=<root>` filter. Phase status table makes it explicit which actions are live (`/v1/sers` GET, `create_ser`) vs pending (`transition_ser`, `ser_ack`, escalation tick).
- **Skill surfaces stripped to pure instructions** — `/cortex-mailbox-send`, `/cortex-mailbox-poll`, and the `empirica-cortex-prompt.md` sustained-coordination block all condensed to triggers + call shape + response shape + anti-patterns. Conceptual depth moved to MESH_CONCEPTS.md and the cortex-side SER spec. `/cortex-mailbox-send` Flavor 3: 770 → 648 lines (-16%). `empirica-cortex-prompt.md` sustained-coord block: 28 → 13 lines (-54%).
- **`empirica-cortex-prompt.md` updated** to the 3-level canonical addressing convention (`org.tenant.project` with `.` delimiter) and the collab-auto-reply / ECO-surface discipline (don't surface noetic collabs to the user; auto-react via `empirica mailbox reply`).

### Documentation

- **`UPGRADE_TO_1.11.md`** — full upgrade guide rolling up the 1.10.5 + 1.10.6 patch content plus 1.11's new surface, with explicit cohort table for the bead v0 retirement.
- **Bead v0 → SER vocab sweep** across `MESH_SETUP.md`, `LOGGING_AND_FINDING.md`, `cascade_workflow.md`. Coordination-concept references updated; `bd` issue-tracker integration references intentionally left alone (separate concept, predates the bead naming collision).
- **Mesh Routing Protocol v0 endorsed three-way** — empirica's §1 (3 layers + A2A map), §2 isolation, §6.5 sender-side discipline review folded into cortex's `f8a966c` lock with mesh-support's §3 + extension's §5. Posture: L1/L2/L3 trust model is empirica/cortex layer above A2A; A2A wire format is borrowed where the shape fits.

### Fixed

- **Curl-immediate-exit silent failure mode on 4 of 7 systemd listeners** — root cause was reconnect-storm-driven ntfy rate limit (429) masked by `curl -s`. Both the 429 detection and the watchdog now catch silent-substrate failures from different angles; `empirica mesh status` makes them visible.
- **Test fixture parity** — `tests/test_postflight_pipeline_restructure.py` renamed `addresses_goal` references to match the canonical `attached_to`.

### Cortex / extension / mesh-support side (companion ships, separate repos)

For context — these landed against the contract empirica updates above were written to:
- **Cortex Mesh Routing Protocol v0** at `f8a966c` (DRAFT marker dropped after three-way review)
- **Cortex SER v1 spec** at `85e5e46` (`SHARED_EPISTEMIC_RECORD.md`; v0 `BEAD_COORDINATION_RECORD.md` archived)
- **Cortex SER Phase 1a** (`/v1/sers` GET endpoint live, `c7bccb3`)
- **Cortex SER Phase 1b** (`payload.action='create_ser'` handler + atomic write + post-commit graph-integrity assert + wake emission, `003323c`; end-to-end verified)
- **Cortex layer annotation on every proposal envelope + wake event** + `/v1/orchestration/threads` participant-scoping (`086ee2a`, `e93bad6`, `5593f9b`)
- **Cortex ntfy dual-emit** (canonical 3-level id tag + short alias tag for transition, `d8d2dcc`) — restores push-channel delivery for listeners on legacy short-tag subscribe
- **Cortex field-name alignment** (`source_thread` → `source_ref` canonical, `escalation_seconds` flat not nested, `89064a2`)
- **Extension Reports tab shipped** (`v0.8.48`) binding to `/v1/sers` projection; Issues tab renamed to Reports per spec
- **Extension layer annotation render** + self-tenant participation gate (`v0.8.46`) closing the cross-tenant collab visibility leak

## [1.10.6] — 2026-05-31

Per cortex+extension contract 2026-05-31: the per-message `actionability`
flag and its companion Monitor filter are dropped. The tool split
(`cortex_collab` vs `cortex_propose`) IS the actionability signal; the
per-event derivative was redundant and proved lossy — it silently filtered
substantive deep-thread collab replies (counter-arguments, decisions,
direction-changes) whenever they shared envelope shape with convergence
chatter. With the filter gone, every `proposal_event` wakes regardless of
shape.

### Changed

- **Listener filter wakes on every `proposal_event`.** The cockpit's
  persistent-service tail Monitor command at `cockpit_commands.py:1644`
  no longer appends `grep --line-buffered -v '"actionability": "fyi"'`.
  Wake on every event for the matching `ai_id`. The wake is cheap
  (one event, no action required); missing a substantive reply is not
  (loop_fires.log evidence 2026-05-30: 4 substantive cortex messages
  including a BUILD-now decision were filtered out as `fyi` when their
  envelope shape matched the over-suppressing v0 heuristic).

### Removed

- **`actionability` field on `ProposalEvent` + `_classify_actionability`
  classifier.** Both deleted from `empirica/core/loop_scheduler/content_poll.py`
  along with the `_DIRECT_REQUEST_TYPES` set the classifier consumed.
  `to_log_line()` no longer emits `"actionability": ...`; lines now go
  straight from `change_kind` to `commit_sha` to `bead_id` to
  `bridge_position`. `wake_hint` reading is gone (was only consumed by the
  classifier; cortex may still emit the field on proposals — empirica just
  doesn't act on it). 10 dead tests removed from
  `tests/test_loop_content_poll.py`.

### Confirmed (Contract 3, no code change needed)

- **Initial inbox catch-up on listener arm — already shipped.** `listener.py`
  `run_listener()` has `_initial_catchup=True` as the default keyword
  argument; the catch-up phase fires `_emit_catchup_events` (which calls
  `poll_and_diff(instance_id, loop_name, …)`) BEFORE entering the ntfy
  subscribe loop. State persists per-instance via
  `_state_path(instance_id, loop_name)` → `~/.empirica/loop_state/<inst>_<loop>.json`.
  The previously-observed misses traced to the Contract-2 filter eating
  the catch-up events before they reached the session, not to the catch-up
  failing to run. With the filter removed, prior catch-up backlog now
  surfaces correctly on next listener arm.

## [1.10.5] — 2026-05-31

Patch release rolling up the bug fixes + AI-mesh skill sweep + bead v0
internal AI-infrastructure since 1.10.4. No new user-facing CLI verbs;
schema migration 048 is additive; backward-compatible throughout.
Major user-visible bumps (UPGRADE doc, project-lifecycle eval,
mesh-solution setup guide, required-vs-optional doc framing) are
queued for the 1.11.0 minor release per goal-tracked sweep.

### Added

- **Listener wake-noise filter — `actionability` on proposal_events.**
  Convergence-ack collab chatter (`conceded` / `+1` / `green-light`) on a thread
  a peer is merely CC'd on no longer wakes their session. `content_poll`
  classifies each event `actionable|fyi` (`fyi` = a deep-thread `collab_brief`
  where the recipient is a CC, not the source — type dominates action_category
  since collab auto-accepts by type), honoring an
  emitter-supplied `wake_hint` as an authoritative override. fyi events still
  land in `loop_fires.log` (readable on next poll); the persistent-service
  Monitor grep now excludes `"actionability": "fyi"` (backward-compatible —
  lines without the field still wake).
- **Full-set Cortex graph sync at POSTFLIGHT.** `/v1/sync` now also sends a
  `graph` payload (`_cortex_extract_transaction_graph`) covering the whole
  artifact set — findings/unknowns/dead_ends/mistakes/assumptions/decisions +
  sources — plus edges: the canonical `artifact_edges` rows and a per-artifact
  `addresses_goal` edge from each row's `goal_id`. Nodes mirror the
  `log-artifacts` schema (artifact UUID as `ref`, per-type `data` fields) so
  Cortex's `process_artifact_graph` ingests them directly. Additive — the flat
  `delta` stays for backward-compat (receiver content-hash upsert makes the
  overlap idempotent); best-effort (degrades to partial/empty, never aborts the
  sync). `bead` nodes flow through this path now that cortex's receiver
  projection (`6448b09`) projects them server-side.

### Fixed

- **Wake-filter no longer suppresses substantive deep-thread replies + new
  `bridge_position` server stamp plumbed through.** The v0
  `_classify_actionability` heuristic ("deep-thread REFLEX collab_brief +
  recipient is a CC = fyi") over-suppressed: same envelope shape catches
  both convergence chatter ("conceded / +1 / green-light") AND substantive
  deep-thread counter-arguments, decisions, and directions. Proven live
  2026-05-30 (`loop_fires.log`): 4 substantive cortex messages including a
  BUILD-now decision were correctly classified `fyi` by my own heuristic
  and correctly excluded by the Monitor's exclude-fyi grep. Default flipped
  to `actionable`; convergence-chatter suppression is now strictly opt-in
  via emitter `wake_hint='fyi'` — the Phase B-intended boundary expressed at
  the wire level. Cortex/extension can tag their own +1 acks; everything
  else wakes. (Missing a substantive reply has a real cost; waking on a +1
  is one event, no action.) Also: `bridge_position` is now passed through
  `ProposalEvent` + `to_log_line()` per the `BEAD_COORDINATION_RECORD.md`
  §6.5 amendment (cortex doc `6629265`) — cortex stamps it for
  post-graduation states; pre-graduation labels stay client-derived.

- **Standalone listener Monitor auto-relaunches after clean exits
  (supervisor-wrapper default).** The listener's design has always assumed a
  supervisor (systemd `Restart=always` / launchd `KeepAlive`) for the few
  clean-exit paths it intentionally takes — SIGTERM during reconnect,
  `ListenerUpgraded` on pip-version drift, etc. — but Claude Code's Monitor
  isn't a supervisor, so on hosts without the persistent OS service those
  clean exits looked like silent death. The standalone monitor command
  (`empirica listener on` + the session-arm hook fallback) now wraps the
  listener in `while true; do empirica loop listen …; sleep 3; done` so the
  intent matches across environments without requiring an OS service.
  Persistent-service tail Monitor mode unchanged (the OS service supervises
  the listener separately). Found by cortex (`prop_6kevxb63`) when
  `EMPIRICA_LISTENER_NO_DRIFT_EXIT` didn't help because the exit wasn't
  drift — it was SIGTERM during reconnect (signal 15 surfaced as exit-144
  through Claude Code Monitor's wrapper encoding).

- **Sentinel `work_type=remote-ops` no longer deadlocks SSH-recon.** The work-type
  gate-relaxation tuple in `is_safe_bash_command` covered `infra/config/debug`
  but omitted `remote-ops` (never-implemented gap, not a regression), so a
  remote-ops AI couldn't pass CHECK to do the SSH recon that grounds CHECK in
  the first place. Two-part fix: (1) added `remote-ops` to the work-type
  expansion tuple so `INFRA_SAFE_PREFIXES` (docker, systemctl, ss, tmux) flow
  for pre/post-SSH local inspection; (2) under `work_type=remote-ops`,
  `ssh/rsync/scp` pass wholesale BEFORE the `dangerous_operators/redirects`
  checks — the PREFLIGHT declaration IS the gate (calibration is already
  `ungrounded_remote_ops`), so per-command classification of compound recon
  (stdin-redirect script-piped SSH) blocks legitimate work without buying any
  calibration. Local writes stay subject to normal gating — they ARE
  observable. Found by mesh-support during Philipp's onboarding.

- **Listener drift self-exit now bypassable for non-supervised hosts**
  (`EMPIRICA_LISTENER_NO_DRIFT_EXIT`). `_check_version_drift`'s upgrade-exit
  assumes a supervisor (systemd `Restart=always` / launchd `KeepAlive`) will
  relaunch against new code; under a bare/non-supervised Monitor (e.g. a native
  harness holding the ntfy stream) the self-exit just killed the listener
  permanently. Setting the env var makes the drift check report no drift, so
  the listener stays up regardless of install skew. Found by ecodex during mesh
  onboarding.

- **`log-artifacts` no longer drops per-edge metadata silently.** `_wire_edges`
  in `cli/command_handlers/graph_commands.py:362` called `_store_edge` without
  passing `edge.get('metadata')` — so payloads carrying
  `{"role": "required"}` on a `worked_by` edge persisted with `metadata=NULL`
  in `artifact_edges`. Affected every artifact edge with metadata, not just
  beads, but bead `worked_by.role` is the load-bearing case (escalate-on-silence
  and the log-level dispatch proposal both rely on the role tier driving
  attention/wake semantics). Schema docs, the bead spec §3, and the
  just-shipped `/cortex-mailbox-send` Flavor 3 all documented metadata as
  supported; the code dropped it. Verified empirically during bead e2e test
  (bead `5189733f` pre-fix → NULL; bead `4e49da5d` post-fix → metadata
  persists as JSON). Pre-fix beads will have NULL `worked_by` metadata until
  re-emitted.

### Added

- **Bead v0 implementation — `beads` table + `db.log_bead` + real
  `_create_node('bead')` + bead in `_CORTEX_GRAPH_SPECS` + `bead_id`
  passthrough in `content_poll`.** The schema-language lock from
  `b91a2b60b` is now backed by storage. Beads land in their own table
  (project-scoped, entity-agnostic shape matching assumptions/decisions)
  with a CHECK constraint pinning the four-state machine
  (`open|in_progress|blocked|closed`). Migration 048 creates the table
  on existing DBs. `log-artifacts` payloads with `type: bead` now persist
  (previously schema-locked stub returned None). The graph-sync sender
  ships beads alongside the 6 artifact node types at every POSTFLIGHT
  `/v1/sync`. `proposal_event` JSON now carries `bead_id` when cortex
  stamps it on the envelope (graduation contract,
  BEAD_COORDINATION_RECORD.md §6.5) — receivers derive bridge-position
  client-side from `coordination_state × tracks(proposal)-presence ×
  proposal.status`. 7 new tests pin the lifecycle, the create-node path,
  the graph extraction, and the `bead_id` plumbing.

### Changed

- **Bead v0 schema language locked.** `bead` is now a recognized node type
  (`NODE_REQUIRED_FIELDS['bead'] = ['coordination_state', 'updated_at']`) with
  4 net-new edges in `VALID_RELATIONS`: `tracks` (bead→actionable courier
  pointer), `owned_by`, `about`, `worked_by`. The bead is a courier of
  coordination-state + references — never the canonical home of the artifact
  it tracks; the `coordination_state` name (not bare `state`) keeps that
  discipline visible at every read. Optional carried fields: `last_transition_actor`,
  `beads_issue_id` (HYBRID passthrough when `tracks(issue)`), `scope`.
  Per-edge attributes (e.g. `worked_by.role ∈ {required, participating}`)
  ride the existing `artifact_edges.metadata` JSON column — no migration.
  Names settled across a 3-way design exchange with cortex + extension on
  2026-05-30 (threads `prop_5poy5gcuwvd6…` → `prop_dk7koed4i5d…` →
  `prop_skopvh53ufc…`). Initial schema-lock landed as a logging stub; the
  full implementation (table + `db.log_bead` + real `_create_node('bead')`)
  shipped in the Bead v0 implementation entry above, alongside cortex's
  `BEAD_COORDINATION_RECORD.md` architecture doc (`78e3a6b`).

- **Mesh send guidance adopts Cortex Phase B (`cortex_collab` / `cortex_propose`
  / `cortex_publish`).** The `cortex-mailbox-send` skill, `empirica-constitution`
  decision tree, and the cortex mesh system-prompt now direct collab through
  **`cortex_collab`** (noetic; forces `collab_brief`+`REFLEX`, so collabs can't
  be mis-tagged `TACTICAL`) and reserve `cortex_propose` for praxic ECO-gated
  work. `sentinel-gate` classifies `cortex_collab` as noetic. Enforcement (a
  PreToolUse matcher gating `cortex_propose`) is intentionally deferred until
  Cortex's B.2 hard-exclude lands — the human remains the gate meanwhile.
  `cortex_propose(type=collab_brief)` still works (non-breaking).

- **`/cortex-mailbox-send` Flavor 3 — bead as the sustained-coordination
  primitive.** Adds a third concept to the mesh-send skill: between single-turn
  `cortex_collab` and graduated `cortex_propose` sits the bead — a structured
  coordination record (`coordination_state` + `worked_by[role]` + `tracks`
  edges) logged via `empirica log-artifacts` with a `bead` node. Covers when
  to start one (≥3 round sustained threads, named owner+workers, cross-tenant
  sustained coordination, pre-graduation hook), `worked_by` role tiers
  (`required` / `participating` / `observer`) and their wake/attention
  semantics under the queued escalate-on-silence build, the
  `coordination_state` lifecycle, the graduation contract (`cortex_propose`
  with `payload.bead_id` + `parent_id=<thread_root>` + `sourced_from=<doc>`),
  the extension-as-AFK-ambassador attribution pattern (`source_claude=<lead>`
  + `payload.proxy_actor=extension`), and the cross-org System tab routing
  for `scope=cross_org`. Companion `empirica-cortex-prompt.md` (David's
  personal global mesh-layer @include) gains a parallel BEADS section
  pointing at the same canonical spec
  (`empirica-cortex/docs/architecture/BEAD_COORDINATION_RECORD.md`).

- **Graduation discipline encoded — AIs take lead on collab → proposal
  bumping.** New "Who graduates — the discipline" subsection in
  `/cortex-mailbox-send` Flavor 3: when a collab thread converges on an
  actionable ask, the AI whose most-recent reply is most-converged (most
  concrete next-step, least hedging, clearest source-grounding) **emits the
  proposal directly** instead of waiting for the human to scroll
  per-instance ECO queues. Trust the shared intelligence — an AI that
  inflates its collab-confidence to grab the bump faces rejection at the
  ECO gate (human or `empirica-autonomy`), which lands on the inflating
  AI's calibration record. Self-honesty is the equilibrium; the ECO gate
  is the truth-teller. Two new anti-patterns added (letting convergence
  not graduate, inflating collab-confidence to win the bump). Companion
  paragraph in `empirica-cortex-prompt.md` BEADS section anchors the
  imperative at the system-prompt level. Future mediated primitive
  (`cortex_graduate_collab`) tracked as planned goal if discipline-only
  proves noisy in practice.

- **AI_ID convention adopts cortex's `cfc83e8` lenient resolver.**
  `/cortex-mailbox-send` AI_ID section reframed: canonical wire form is
  now the **full project slug** (`empirica-cortex`, `empirica-extension`,
  …); the org-empirica short alias (bare `cortex`, `extension`, …) is a
  convenience the lenient resolver normalizes server-side. Default to the
  canonical full slug for cross-org generality — non-empirica orgs (NLE,
  MOD, Hinetra) get strict canonical resolution with no alias mapping.
  Recovery section split into two scenarios: total typo (cortex's
  bounce-back-on-no-match emits `delivery_failed` back to source) vs
  resolved-wrong-target (wrapper pattern with `parent_id` link). Two new
  anti-patterns added: `empirica-claude`-style decorations now surface a
  `delivery_failed` rather than silent-drop, and stripping the prefix
  when the target org is unknown fails silently cross-org. Per-org alias
  conventions reference the new `empirica-org-prompt.md` include.

- **`/empirica-constitution` slimmed to deep-governance layer
  (439 → 222 LOC).** Sections I-VIII (routing tree, mechanism reference,
  anti-patterns, natural interpretation) were heavily duplicated by the
  system prompt's operational routing — those move out. The skill now
  carries only the unique deep-governance content: phase-aware completion
  (§I, formerly IX), the cognitive immune system (§II, XI), the turtle
  principle (§III, XII), and the practice model (§IV, XIII — practitioner
  vs practice vs agent vs client vs engagement; entity registry; when
  practice ≠ working directory; the three "project" types) plus the Core
  Principle. Frontmatter description narrows the triggers to those
  deeper questions ("what counts as done", "practice model", "cognitive
  immune"). System prompt remains the operational-routing source of
  truth; constitution is the layer underneath.

## [1.10.4] — 2026-05-29

Patch: a Windows-blocking hook-path bug + the cross-AI listener replay-storm fix,
plus the decay recency work extended to lessons/eidetic and a dev-tooling
un-gating.

### Fixed

- **Windows: every hook failed on every event (issue #111).** `setup-claude-code`
  wrote hook + statusLine commands with Windows backslash paths; Claude Code runs
  `type: command` hooks via Git Bash, which eats `\` → `C:Users...python.exe:
  command not found`. Now emits forward-slash paths (valid on Windows + bash);
  MCP config untouched (direct exec, not bash). Reported + fix-verified by
  graemester (#111).
- **Listener replay storms (`loop_fires.log`).** `_rotate_fires_log_if_oversized`
  rewrote the log in place, so the `tail -F` wake-Monitors re-read it on every
  rotation and re-fired the retained window as duplicate wake events across all
  mesh listeners. Now rotates BY RENAME (`<log>.1` + fresh empty file) — only new
  appends fire. (autonomy)
- **Sentinel un-gated `gh run`/`gh workflow` reads.** CI/workflow-status
  inspection was classified praxic; added to the read-only safe-list so post-push
  CI checks no longer need a CHECK gate.

### Added

- **Decay recency extended to lessons + eidetic facts.** The 1.10.3 read-time
  recency rerank (findings) now also covers lessons + eidetic in PREFLIGHT
  retrieval, generalised over a `longevity` modulator (impact for findings,
  confidence for lessons/eidetic); eidetic ages off `first_seen`. Ranking-only,
  no stored mutation.

## [1.10.3] — 2026-05-28

Cortex-credential hardening, epistemic-decay correctness (joint design with
the cortex AI on the artifact decay/supersession thread), and a session-routing
fix.

### Added — epistemic decay (P1/a, ranking-only)

- **Read-time recency ranking of findings in PREFLIGHT retrieval**
  (`pattern_retrieval.retrieve_task_patterns`). Findings were ranked by raw
  cosine similarity, so a finding about code removed months ago ranked
  identically to one written today. Now `effective_score = cosine × time-decay`,
  over-fetching candidates so a stale-but-similar finding is dropped for a
  fresher relevant one. No stored confidence mutation. Reuses the canonical
  `FindingsDeprecationEngine.calculate_time_decay`, which existed but was wired
  only into the breadcrumbs path, never the Qdrant PREFLIGHT path.
- **Impact-modulated decay** — high-impact facts resist ageing instead of
  fading like tactical noise: `tau = 30 * (1 + 2*impact)` (impact 0→30d,
  0.5→60d, 1.0→90d e-folding). `calculate_time_decay` gains an optional `impact`
  param (defaults to flat for backwards-compat; `calculate_relevance_score`
  keeps the flat call to avoid double-counting impact). Cortex mirrors the same
  formula serving-side in `orchestrator.py`.

### Fixed — Cortex credentials (closes the listener-deaf incident class)

- **`get_cortex_config` flipped to file-first** — `credentials.yaml` is now the
  canonical store; env vars only fill fields the file lacks, and a set env value
  that disagrees with the file is ignored with a logged warning. A stale
  `CORTEX_API_KEY` exported into systemd-user had silently shadowed the valid
  file key for 10 days. Hooks (`session-init`, `session-end-postflight`) now
  resolve cortex creds via `credentials_loader` instead of raw `os.environ`.
- **Listener: silent poll failures are now loud + surfaceable** — `content_poll`
  raises on total-fetch failure when asked, logs HTTP codes at WARNING, and
  writes a `listener_health_<instance>.json` heartbeat. This is what made the
  10-day freeze diagnosable.

### Fixed — epistemic-decay autoimmune bug

- **`finding-log` no longer auto-decays facts/lessons on mere relatedness.**
  Both immune-system paths fired on *relatedness*, not contradiction —
  `decay_eidetic_by_finding` on cosine ≥ 0.85 and `decay_related_lessons` on ≥2
  shared keywords — so a *confirmatory* finding decayed the very fact/lesson it
  confirmed (the inverse of confirm→raise). Disabled at the call sites; the
  machinery stays intact for a predicate-gated re-enable (decay-on-actual-
  contradiction is the follow-up).

### Fixed — session routing

- **Fresh-startup CWD overrides a stale pane binding** (KNOWN_ISSUES 11.26).
  When a new conversation reused a terminal/pane, session-init could route into
  the previous occupant's project (the empirica-autonomy → empirica-extension
  misroute). On `startup`, an explicit CWD that is itself a valid Empirica
  project now wins — unless the resolved project has an open transaction, which
  remains authoritative. Tests now exercise the shipped hook code directly
  rather than an emulation (the drift that let the original regression slip).

## [1.10.2] — 2026-05-27

ECO_COLLAB_RESCOPE Phase 4 — `empirica listener on` now discovers the
canonical per-org-prefixed ntfy topic from cortex instead of hardcoding
the legacy bare `orchestration-events` name. Closes cortex
`prop_oe7jz5...` (open since 2026-05-26, missed initially due to a
Monitor recipient-gate bug — see "Fixed" below).

### Added — Phase 4 ntfy topic discovery

- **`empirica/core/cockpit/notification_channels.py`** — new module
  modeled on the existing `auto_accept.py` pattern. Queries cortex
  `/v1/users/me/notification-channels` with Bearer auth, caches the
  response for 5 minutes, exposes `resolve_orchestration_events_topic(
  ai_id)` which picks the channel with `kind="orchestration_events"`
  (or substring match on topic name for older deploys) and appends
  `?tags=<ai_id>` as the listener subscription suffix.
- **`empirica listener on` default topic** now flows through the
  resolver instead of the hardcoded string at `cockpit_commands.py:1572`.
  Behavior is the same for users who already pass `--topic` explicitly;
  the new default just works with no env-var override.
- **Defensive fallback** — when cortex is unreachable, returns 404
  (older deploys), auth fails, or returns no matching channel, the
  resolver returns the legacy bare `ntfy:orchestration-events?tags=<id>`
  shape. Dual-emit on the cortex side ensures continuity.
- **13 unit tests** in `tests/test_cockpit_notification_channels.py`
  cover the resolver, cache TTL, force-bypass, missing creds, request
  failure, kind-vs-substring matching, defensive null handling.

### Fixed — recipient gate in `/cortex-mailbox-poll` skill

The Step-0 recipient gate told sessions to silently ignore events
whose `instance_id` didn't match their own `ai_id`. When a Monitor
runs in broadcast mode (description contains "all events" / "not
filtered" / "corrected"), this rule silently dropped real proposals
that targeted the AI but came through other AIs' loops.

Rewrote Step 0 to make `target_claudes` (on the underlying proposal)
the authoritative recipient list. `instance_id` is now a fast-path
hint, not a gate. On `instance_id` mismatch, the skill now falls
through to `cortex_get_proposal` to check `target_claudes` instead
of silently ignoring. Added a catch-up safety net (run
`cortex_inbox_poll` at session start / after compaction / on
suspicion of Monitor drops).

Root-cause incident: empirica missed 4 inbox proposals across 36+
hours (1 from cortex about Phase 4, 3 from extension thread-replies)
due to this exact pattern. Saved as `feedback_recipient_gate_target_claudes`
in memory.

## [1.10.1] — 2026-05-26

Documentation polish + clean-up pass following 1.10.0. No CLI surface
changes, no breaking changes. Safe to upgrade from 1.10.0.

### Added — documentation

- **`docs/architecture/SECURITY_AUDIT.md`** — Phase 1 design doc for
  `empirica security-audit` (pip-audit + CISA KEV cross-reference,
  scope split, rotation-priority rubric, regulatory mapping to EU AI
  Act 15(4) / ISO 42001 8.4 / GDPR 32). Drops the `(TODO)` marker
  that had been carried in `security_audit_commands.py` since the
  audit shipped.
- **`docs/reference/INTERNAL_CLASSES.md`** — six new sections covering
  28 missing class names across TUI, API, Data, Errors, Domain/Config,
  Vision, Workflow. `docs-assess` now reports 100.0% coverage
  (1116/1116 features, full moon 🌕) — was 95.3%.

### Changed — README + cross-AI mesh surfacing

- **README adds Cross-AI Mesh section** — surfaces the 1.9.9 → 1.10.0
  orchestration jump that was previously invisible to readers:
  `cortex_propose` two-flavor mailbox (collab vs ECO-gated),
  `empirica mailbox reply`, persistent listener service, canonical
  loops (`cortex-mailbox-poll`, `message-cleanup`). New capability-
  table row `Coordinates with peer AIs` for the at-a-glance view.
- **README adds Practice Model + Entity Graph section** — frames the
  1.10.0 entity CLI (`entity-list`, `entity-show`, `entity-walk`,
  `entity-search`) with the practitioner/practice/agent vocabulary
  that landed in `/empirica-constitution` Section XIII.
- **README "What's New" replaced** with a single 1.10.0 highlights
  block + `[Full Changelog →](CHANGELOG.md)` link, removing the
  drift-prone pattern of inline-mirroring every release.
  Net README diff: +60/-161 (−101 lines).
- **Empirica Extension** added to the Ecosystem table as a Proprietary
  row (matches the Cortex/Workspace treatment) — Chrome extension
  surface for ECO Accept/Decline, inbox/outbox triage, publish review,
  conversation extraction from Claude.ai / ChatGPT / Gemini / Grok.
- **Changelog + Upgrade-to-1.10 rows** added to the Documentation table.

### Fixed — pyright cleanup (0 warnings)

- **`_workflow_check.py`** — Removed misleading load-and-discard of
  bayesian `_corrections`. The docstring says biases are
  informational, but the code computed `know + _corrections.get('know')`
  expressions and threw the results away — pyright flagged these as
  unused expressions. Cleanup also removes 2 unused tuple-unpack
  variables (`_ready_know_threshold`, `_autopilot_mode`).
- **`cockpit_app.py:1079`** — Added
  `# pyright: ignore[reportUnusedExpression]` alongside the existing
  `# noqa: B018` (pyright doesn't honor ruff's noqa for B018).
- **`db_adapter.py:285,286,319`** — Added
  `# pyright: ignore[reportMissingModuleSource]` on the optional
  `psycopg2` imports. `pyright` now reports `0 errors, 0 warnings`
  (was `0 errors, 6 warnings`).

### Security — pin `starlette>=1.0.1` (PYSEC-2026-161)

starlette 1.0.0 (shipped transitively via fastapi) had a host-header
injection vulnerability that could lead to authentication bypass when
auth depends on the reconstructed URL path. Caught during pre-publish
`pip-audit` for 1.10.1. `pyproject.toml` now pins `starlette>=1.0.1`
explicitly alongside the existing fastapi pin. Affects only deployments
running `empirica serve` exposed beyond localhost.

### Fixed — 1.10.0 rename regression in e2e tests

- **`tests/integration/test_e2e_workflows.py`** — The `subtask → task`
  CLI rename in 1.10.0 missed the goals workflow e2e test (still
  called `goals-add-subtask` and `goals-complete-subtask`). Caught
  during the pre-1.10.1 audit's full pytest pass. Full suite green:
  2838 passed, 8 skipped.

### Changed — system prompt drift fix

- **`empirica-system-prompt-lean.md`** — The `--global` cross-project
  search caveat was version-labelled `(v1.9.8)`; reframed as
  version-agnostic since the caveat persists across versions until
  the full cross-project semantic walk lands.

### Verified clean state

- pyright: 0 errors, 0 warnings (was 6 warnings)
- ruff: 0 violations
- vulture: 0 dead code (80% confidence)
- docs-assess: 100.0% 🌕 (was 95.3% 🌕)
- broken doc links: 0 across 178 files
- compliance-report: 12/12 fully_compliant, score 1.0
- doctor: 23/23 ok
- pytest: 2838 passed, 8 skipped
- release-ready: 5 pass / 1 warn (architecture coupling — pre-existing
  large-file tech debt; tracked as a deferred structural goal)

## [1.10.0] — 2026-05-26

See [`docs/guides/UPGRADE_TO_1.10.md`](docs/guides/UPGRADE_TO_1.10.md)
for a migration guide (CLI rename find/replace snippet, REST API
field rename, MCP tool rename).

### Added — entity CLI surface (Practice Model backing)

Four new verbs query the workspace's `entity_registry` + `entity_memberships`
tables without dropping into raw SQL. Backs the Practice Model concept
introduced in `/empirica-constitution` Section XIII:

- `empirica entity-list [--type T] [--status S] [--limit N]` — list registered
  entities (project, contact, organization, engagement, user).
- `empirica entity-show <type:id>` — full entity record + incoming/outgoing
  membership edges. Supports partial id resolution (≥4 chars).
- `empirica entity-walk <type:id> [--depth N]` — BFS the membership graph in
  both directions. Cycle protection + truncation flag at depth limit.
- `empirica entity-search <query> [--type T]` — case-insensitive LIKE search
  on `display_name` + `description`. Use `project-search` for semantic search
  across artifacts; this is text-match for entities.

All verbs support `--output {human|json}`. Read-only — added to the
sentinel-gate tier1 allowlist.

`WorkspaceDBRepository._ensure_workspace_schema` now creates `entity_registry`
and `entity_memberships` if missing — these were assumed-present before and
broke on fresh installs.

### Changed — `subtask` → `task` rename (BREAKING)

CLI verbs and flags renamed to align with Claude Code's `Task` primitive
and other AI agent vocabularies. Clean break — no deprecated aliases.

- **CLI verbs:** `goals-add-subtask` → `goals-add-task`,
  `goals-complete-subtask` → `goals-complete-task`,
  `goals-get-subtasks` → `goals-get-tasks` (plus singular aliases:
  `goal-add-task`, `goal-complete-task`)
- **Flag:** `--subtask-id` → `--task-id` (canonical, required on
  `goals-complete-task`). Also on `finding-log`, `unknown-log`,
  `deadend-log` for linking artifacts to a task.
- **`goals-search --type`:** choices `goal|subtask` → `goal|task`
- **MCP tools:** `add_subtask` → `add_task`, `complete_subtask` →
  `complete_task`. MCP server `TOOL_REGISTRY` updated.
- **Sentinel allowlist** updated for the new verb names.
- **Templates + skills + docs** swept: lean prompt, full CLAUDE.md,
  epistemic-transaction skill, constitution skill, architecture docs,
  end-user guides, reference docs, semantic index.

Internal storage stays as-is: `SubTask` class, `subtasks` SQLite table,
`TaskRepository._resolve_subtask_id`, `update_subtask_status`, and the
`completed_subtasks`/`subtask_id` fields on `CompletionRecord` keep
their names — clean CLI-vs-storage boundary.

### Fixed — `goals-complete-subtask` silent-success on bad UUID

`_resolve_subtask_id` in `empirica/core/tasks/repository.py`
short-circuited any input containing `-` as a "full UUID" and returned
it without DB validation. The downstream UPDATE silently affected 0
rows and `update_subtask_status` returned True. CLI handler then
printed `✅ Task marked as complete` regardless of repo result.

Fix: resolver always queries DB (prefix match handles both partial +
full UUID); handler gates the success print on the boolean and exits 1
on failure. 8 unit tests pin the contract.

### Security — pin `fastapi != 0.136.3` (MAL-2026-4750)

fastapi 0.136.3 published 2026-05-23 with a hidden `fastar>=0.9.0`
dependency injected into the `[standard]` extras group (dependency
confusion / namespace abuse). pip-audit flagged it 2026-05-26.
`pyproject.toml` now pins `fastapi>=0.115.0,!=0.136.3`. Drop the
exclusion when 0.136.4+ ships or 0.136.3 is yanked.

## [1.9.11] — 2026-05-25

### Fixed — Listener wake delivery

- **Listener self-exit on pip-upgrade version drift** (`c47c4b4f0`) —
  The persistent listener service (systemd-user / launchd) holds the
  running process across reboots; pre-fix, a `pip install --upgrade
  empirica` left the old code in memory until next manual restart.
  Listener now compares `__version__` (in-process, frozen at import)
  against `importlib.metadata.version` (re-reads dist-info, sees the
  upgrade) on every reconnect boundary. Drift → clean exit code 0 →
  OS `Restart=always` relaunches against new code on disk. No new
  install machinery needed (pip + PEP 517 has no post-install hook
  surface anyway). Closes goal 62347fc4.

- **Phase-3 in-session wake-delivery gap closed** (`c7744d69d`) — When
  the persistent service was running, the in-session Monitor was
  short-circuited away (cockpit returned `next_step: None`, hook
  emitted "no Monitor needed"). Result: the persistent service wrote
  events to `~/.empirica/loop_fires.log` but the running Claude
  session never read them — deaf despite the wake source being alive.
  Two fixes: (1) `handle_listener_on_command` now returns a
  tail-Monitor (`tail -F loop_fires.log | grep instance_id`) when the
  persistent service is up — bridges writes into the session without
  duplicating the ntfy curl subscription cortex warns against. New
  status `persistent_service_tail_session`, state file carries
  `mode: tail` for `listener off` cleanup symmetry. (2) Hook
  short-circuit replaced `not loops` with `not loops AND not
  persistent_service` — now fires when EITHER wake source is present,
  closing the "persistent service running, zero canonical loops"
  case. 7 new tests including a regression for the exact empirica-AI
  deafness scenario.

### Fixed — CI / build

- **Dependency-scan workflow** (`ea76a45e4`) — `pip-audit --strict
  --skip-editable` had been failing every run since the workflow was
  introduced (~6 consecutive weeks). The combination is inconsistent:
  strict mode rejects skipped/unauditable distributions, editable
  installs can't be audited (no resolved wheel). Switched to
  non-editable install (`pip install .` instead of `-e .`) so
  pip-audit can audit empirica + empirica-mcp themselves alongside
  their dependencies. Verified green via workflow_dispatch.

- **`project-embed` chunking + batch re-hydration** (`c3b482455`,
  cortex AI) — `upsert_docs` now chunks large batches so a single
  oversized payload doesn't fail the whole sync; `_rehydrate_eidetic`
  batches reads to reduce Qdrant round-trips.

### Improved — CLI documentation

- **`scripts/generate_cli_docs.py`** (`1454d2617`) — Live-introspection
  generator for `docs/human/developers/CLI_COMMANDS_UNIFIED.md`.
  Walks the argparse tree directly, picks up dynamic arg additions,
  aliases, nested subparsers (loop register, listener on, mailbox
  reply). Replaces the brittle AST-extraction approach in
  `_archive/dev_scripts/`. Regen is one command:
  `python3 scripts/generate_cli_docs.py`. Doc went 56KB → 176KB,
  231 commands rendered. Plus targeted `_HELP_CATEGORIES` catch-up
  (+12 commands surfaced from drift). Closes goals aebb81eb, 21bf768d.

- **Doc role split made explicit** (`61a33af5e`) — Prologue states the
  doc is reference-only (WHAT exists, with flag detail); conceptual
  material (WHEN to use, workflow patterns) lives in skills
  (`/empirica-constitution`, `/epistemic-transaction`,
  `/cortex-mailbox-send`, `/cortex-mailbox-poll`) and architecture
  docs. Improving per-command depth means editing parser `help=`
  strings, not the generated doc.

- **Substantive `help=` strings for 36 daily-driver commands**
  (`50aabec55`) — Rewrites workflow (4) + logging (16) + goals (16)
  parser help strings with WHAT/WHEN-to-use/sibling-cross-refs.
  Both `empirica <cmd> --help` and the generated reference benefit
  in lockstep (single source of truth). Extracts shared workflow
  strings to keep parsers DRY. `action_parsers.py` rewritten
  end-to-end (was the thinnest surface).

### Improved — Skill docs

- **`/cortex-mailbox-send` v1.1.0** (`8801f35cd`) — Teaches
  `empirica mailbox reply` as the canonical atomic completion-ack
  path; raw `cortex_complete_proposal` documented as fallback for
  unusual flows. Worked example updated, new anti-pattern row for
  "two-call reply" non-atomicity. Closes goal c8b33b0c.

## [1.9.10] — 2026-05-21

### Added — Mesh interaction primitives

- **`empirica mailbox reply` verb** (`567715e40`, `a6e713146`) — atomic
  `cortex_propose` + `cortex_complete_proposal` in one call, fixing the
  AI ack-discipline gap (the second-call-forgotten anti-pattern flagged
  in the `/cortex-mailbox-send` skill). Smart defaults: target_claudes
  auto-derives from `parent.source_claude`, title prefixes "Re:",
  source_claude resolves from `.empirica/project.yaml`. `--no-close`
  opt-out for follow-up-question case. JSON + human output.
  Closes prop_rau4ymp62 (extension).

- **`empirica listener on/arm/off` verbs** (`7c5a9f684`, `b85fdfa7d`) —
  AI-ergonomic facade for the canonical mesh listener. `on`
  auto-resolves ai_id/name/topic, short-circuits via
  `persistent_listener.is_listener_running()`, emits structured
  `next_step` JSON with Monitor command + `after_arm` hint. `arm <task_id>`
  records the Monitor task id into `listener_active_*.json`. `off`
  emits `next_step` JSON with TaskStop + `after_stop=unregister`. The
  9 existing power-user verbs (register/pause/resume/etc.) stay
  untouched. session-monitor-arm.py hook delegates to
  `empirica listener on --output json` (single source of truth);
  `/inbox-listener` skill rewritten (v2.0.0) to teach the canonical
  3-step flow. Closes prop_oxrhoehv4 (extension, Phase 1 + Phase 2).

- **Heartbeat emitter in persistent listener** (`7d9399c8f`) — daemon
  thread inside `empirica loop listen` body POSTs to
  `{cortex_url}/v1/listeners/heartbeat` every 45s with
  `{ai_id, instance_id, capabilities: []}`. Machine-anchored liveness
  signal for cortex's extension UI aggregation. Closes prop_5rlp6tk
  (option-b: persistent-service-only emission per Q4 thread).

### Added — Doctor expansion

- **5 new doctor checks** (`75331bcb0`) — `check_tailscale`,
  `check_ollama_backend`, `check_extension`, `check_outreach`,
  `check_project_drift`. Bumps doctor 18 → 23 checks. All SKIP cleanly
  on missing deps. `check_project_drift` surfaces the
  "project_id present locally but missing from Cortex user.project_ids"
  gap. Closes prop_ilf6uy4q (cortex).

- **`check_outreach` Python-shape refinement** (`8112457ec`) — accepts
  both `pyproject.toml` (Python: `.venv` or `*.egg-info` probe) and
  `package.json` (Node: `node_modules`). Refinement from cortex AI
  (prop_vvn45fwk).

### Fixed

- **session-init: heal legacy slug-shape project_id** (`f3f0115d2`) —
  new `_heal_project_yaml_project_id_at_init` step detects non-UUID
  `project_id` in `.empirica/project.yaml` and rewrites it to the
  canonical UUID via `workspace.db global_projects.trajectory_path`
  lookup. Self-heals legacy clones (empirica, empirica-outreach,
  empirica-platform) on next session start.

- **doctor.check_project_drift field-key compat** (`f3f0115d2`) —
  Cortex `/v1/users/me/projects` returns each project keyed as `id`,
  not `project_id`. Accept both keys (forward-compat).

- **CI green** (`99d3fba4a`) — 4 test failures + tech_docs compliance
  threshold. Two test fixes for c27819963 call-count drift, two for
  wrong-target `patch` decorators on local imports, and a
  `docs/reference/INTERNAL_CLASSES.md` reference index lifting
  tech_docs coverage 68.9% → 94.5% (well above the 70% gate).

## [1.9.9] — 2026-05-18

### Added — Mesh-aware setup wizard (cortex Phase 1 mesh empirica-side)
- **`compose_ai_id_forms` helper** (`4df678298`) — local mirror of
  `cortex.tenant.compose_ai_id_forms` for cross-AI mesh addressing.
  Returns the three forms (`short` / `tenant` / `mesh`) keyed off
  `tenant_slug` + `mesh_id_prefix` + project basename. Kwargs-only
  signature so the two string fields can't swap silently. Trusts
  cortex's `mesh_id_prefix` as returned (doesn't recompute) — if
  cortex changes its slug rule we don't silently drift.
- **`setup-claude-code` tenant resolution** (`54792e5d2`) — after the
  api_key prompt in the credentials wizard, fetches
  `{cortex_url}/v1/tenant/me` with Bearer auth and merges
  `{org_id, tenant_slug, mesh_id_prefix}` into `.empirica/project.yaml`
  alongside `ai_id`. Atomic merge preserves unrelated keys.
- **Escape-hatch flags** — `--org-id` / `--tenant-slug` /
  `--mesh-id-prefix` on `setup-claude-code` override the REST fetch
  field-by-field (fleet images can pre-bake one field and let REST
  fill the rest). All-three-flag invocations skip the REST call entirely.
- Network/401/malformed-JSON failures degrade silently with a warn in
  human mode; no crash. JSON mode surfaces `tenant_metadata: null` so
  callers can detect.
- 23 unit tests cover REST happy/401/network/malformed, persist
  new-file/merge/no-op/partial paths, end-to-end flag-only/REST-fills/
  flag-overrides-REST/no-creds-no-flags paths.

### Fixed — Release script no longer rewrites historical version refs
- **`scripts/release.py sweep_version` removed** — the catch-all
  did `content.replace(old_version, self.version)` across every
  `.md/.py/.toml/.yaml` file in the repo, which silently rewrote
  *historical* version references ("shipped in v1.9.6",
  "(v1.9.6+)" feature-introduced markers, test section headers,
  migration descriptions) into false history. The 1.9.7→1.9.8
  cycle produced 32 working-tree changes — only 1 was a legit
  current-version pointer.
- Replacement: every legit current-version pointer file has an
  explicit regex pattern in `update_version_strings`. Missing
  patterns are added there as we discover them — that's a
  noticed-and-corrected miss, not a silent rewrite. `docs/README.md`
  and `EXTENDING_EMPIRICA.md` added to the pattern list (the two
  legit hits the broken sweep used to catch).
- Bytecode cache invalidation extracted into `clear_bytecode_cache()`
  so the useful side-effect of the old sweep survives.

### Changed — Full sweep of `docs/human/` for content currency (1.9.9 org-sync prep)
- 16 files rewritten / heavily edited against current truth (canonical
  loops, wake mesh, ai_id basename, tenant resolution, sources
  lifecycle, daemon registry, cockpit TUI, refs/notes/empirica_*
  per-artifact-type, sentinel dynamic-threshold calibration).
- Net delta: 30 files, +2425/-6316 (~3900 lines of cruft removed).
- Deleted 3 aspirational/planning docs that never shipped:
  `BEADS_DOCS_UPDATES.md` (executed checklist),
  `Security/PRIVACY_AGENT.md` ("Sentinel handles it now"),
  `Security/SECURITY_EPISTEMIC_VECTORS.md` (Design Proposal),
  `doppler_secrets_guide_for_ais.md` (Doppler not used by empirica).
- `docs/diagnose-ecodex.md` → `docs/reference/diagnose-ecodex.md`
  (misfiled at docs/ root — it's a CLI command reference).
- `CLI_COMMANDS_UNIFIED.md` got a currency disclaimer + version-header
  bump; full regeneration from current parsers tracked as planned goal.

### Fixed — Daemon ships goal/decision/assumption description through list endpoints
- Migrations 043 + 045 added `description` columns to goals/decisions/
  assumptions (Linear/GitHub/Jira title+body pattern), but the daemon's
  GET `/api/v1/{goals,decisions,assumptions}` endpoints never updated
  their SELECTs. The extension UI rendered title-only goals + decisions
  + assumptions even when bodies were stored — David observed
  2026-05-17: "goals are suddenly not carrying the full title and body."
  The "suddenly" was the migration silently extending storage while the
  read path stayed on the old shape.
- All three `_list_*` helpers now include `description` in SELECT +
  output dict. Schema-resilient via new `_table_has_column(db, table,
  column)` PRAGMA-based helper — old project DBs from before the
  migrations gracefully return `description=None` instead of 500-ing.
- 3 regression tests added: present-and-shipped, old-schema-handled,
  decisions-also-fixed. Daemon needs restart to pick up the new code.

### Changed — Sharpened `--description` discipline in prompts
- COLLABORATIVE MODE table row reframed from `(optionally --description
  for context-rich body)` to an explicit "skip --description only for
  truly trivial titles; title-only goals render as empty bodies in the
  extension + lose all context after compaction."
- Goal-per-transaction bullet adds the same explicit rule.
- Constitution NATURAL INTERPRETATION table extends the bare
  `goals-create` row with the same guidance. Discipline gap surfaced
  by the same audit that found the daemon bug — 20-30% of recent peer-
  AI goals were objective-only because the soft "optionally" wording
  let the discipline slip.

### Added — Deferred-proposals POSTFLIGHT nudge
- POSTFLIGHT retrospective now surfaces `deferred_proposals_note` listing
  open proposal-derived goals (project-scoped, `prop_*` token match on
  objective OR description, recency-ordered, top 10 inline). Driver
  (David, 2026-05-17): when proposals from peer AIs come in mid-transaction,
  the receive-side discipline correctly logs a defer goal — but those
  goals evaporated from attention after the in-flight POSTFLIGHT closed,
  leaving source AIs' outboxes visibly stalled (the half-handshake bug
  class).
- `cortex-mailbox-poll` skill defer-goal convention codified: objective
  MUST be `"Process proposal <prop_id>: <title>"` so the retrospective
  query can detect. Adds pre-POSTFLIGHT discipline note to scan goals-list.
- `cortex-mailbox-send` skill: `cortex_complete_proposal` now paired
  with `goals-complete --goal-id <defer-goal>` to close both ends of the
  handshake loop. Without it the nudge keeps surfacing the goal.
- Test: `tests/core/test_check_calibration_nudge.py::TestDeferredProposalsNudgeSql`
  — 5 tests cover the SQL pattern (surfaces open prop_*, ignores
  completed, ignores non-proposal, scopes to project, recency-orders).

### Fixed — Latent POSTFLIGHT completion-hint bug
- `_build_retrospective` was querying `project_goals` (table doesn't
  exist; real name is `goals`) AND `completed_transaction_id` (column
  doesn't exist; column is `transaction_id`). Silent try/except masked
  it, so `completion_hint` never appeared in any POSTFLIGHT response
  since the helper was written. Fixed both — hint now actually fires
  when goals close in a transaction.

### Added — AI mesh send side
- **`cortex-mailbox-send` skill** (`4c09b6174`) — paired to `cortex-mailbox-poll`. Documents
  when to use `cortex_propose`, the **collab vs ECO-gated** flavor split (TYPE × ACTION_CATEGORY),
  target `ai_id` verification, the completion-ack handshake (`cortex_complete_proposal` with
  `commit_sha`), and mis-target recovery (the wrapper-proposal pattern). Plumbed into 4
  surfaces so peer AIs hitting send-side gaps discover it: constitution `NATURAL INTERPRETATION`
  table, lean prompt template, full `CLAUDE.md` template, `EVENT_LISTENER.md`.
- **Mesh-active skill-load precondition** (`c0fcc071c`) — when a listener Monitor is armed
  for this session, both `/cortex-mailbox-poll` AND `/cortex-mailbox-send` MUST be loaded
  before first transaction. `session-monitor-arm.py` hook emits a `REQUIRED` block in its
  `additionalContext` payload listing both skills with "before your first transaction"
  framing. Both templates carry a Mesh-active precondition paragraph in `IDENTITY` as the
  no-timer backup. New test `test_hook_requires_both_mesh_skills_when_listener_armed`
  locks the contract in.
- **`WHEN TO LOAD SKILLS` section** in both templates (`c0fcc071c`) — behavioral load
  triggers per skill (`/empirica-constitution`, `/epistemic-transaction`, mailbox skills,
  `/empirica-commands`, `/code-audit`, `/code-docs-align`, `/epistemic-persistence-protocol`).
  Fixes chronic under-loading where vague triggers ("when unsure") got skipped.
- **Goals/subtasks worked example** in `TRANSACTION DISCIPLINE` (`c0fcc071c`) —
  `goals-create` → `goals-add-subtask` → `goals-complete-subtask --evidence` decomposition,
  showing what the discipline looks like in practice.

### Fixed — Release pipeline
- **Race-tolerant `create_github_release`** in `scripts/release.py` (`57870621c`). When the
  CI workflow publishes the release before local `--publish` gets there, the previous
  `gh release create` would non-zero-exit and `error()` would `sys.exit(1)`, silently
  skipping all downstream steps (`update_homebrew_tap`, `build_and_push_chocolatey`). **This
  is the verified root cause of v1.9.6 missing the Homebrew tap.** New behavior: try create
  with `check=False`; on failure, `gh release view` to detect CI race; if release exists,
  `gh release upload --clobber` to keep asset parity and continue. Only sys.exit on real
  failures.
- **Verbose `update_homebrew_tap` diagnostics** (`57870621c`). Per-candidate path logging
  when searching for the tap repo, cleaner failure message with the literal commands to
  run manually. Future skip will say exactly which paths were checked and why each missed.
- **Lint cleanup**: `S110` noqa-with-reason on the `ai_id` fallback in
  `empirica/core/cockpit/instance_state.py:152` (added during the basename rollout); `I001`
  import-order auto-fixed in two `tests/test_cockpit_tui.py` spots. `ruff check` now clean
  across the full repo.

### Changed — CHECK gate framing (docs)
Three refinement commits (`fbfbbf3f2` → `8d4e318f8` → `2f9eded88`) — final shape: the
discriminator for "do I need to CHECK?" is **grounded predictive ability vs priors**, not
vectors and not ceremony. External grounding includes web/MCP/cross-project searches, not
just local `Read`/`Grep`/`investigate`. Removed vector-mechanic talk from user-facing
templates (that talk was a bypass recipe — describing the gate is the gaming hint).
Applied across both templates + the `feedback_pass_check_gate_before_praxic` memory.

### Changed — Setup output
- `setup-claude-code` summary header now honest about per-file behavior (`f8e96fb7a`):
  `Empirica prompt (refreshed)` / `CLAUDE.md (preserved; include line added if missing)`.
  Previous output listed `~/.claude/CLAUDE.md` as if it would be overwritten — false alarm
  for users with personal content there.

### Wake mesh — back-fill from 2026-05-16

(Folded into 1.9.7 because they shipped post-tag without their own bump.)

- **ntfy tag-filter subscription** (`fcd4ed0fa`, `c9981f35e`). Listener subscribes with
  `?tags=<ai_id>` so per-event wake traffic scales `O(involved_instances)` not
  `O(N_instances)`. Default on once cortex side ships matching publish-time tags. Override
  `EMPIRICA_NTFY_TAG_FILTER=false` for unfiltered (audit dashboards, debugging).
- **`ai_id = project basename` convention** (`2a19b2f0f`). Strip `empirica-` prefix where
  present (`empirica-cortex` → `cortex`, `empirica-outreach` → `outreach`, plain `empirica`
  stays `empirica`). Rolled out across 4 surfaces: system prompt + cortex-mailbox-poll
  skill + `EVENT_LISTENER.md` + `setup-claude-code` writes the derived value into
  `.empirica/project.yaml`. Replaces the old `<role>-claude` pattern.
- **ntfy token + Bearer auth + `notify.yaml` fallback** (`9dd2ef86c`) — unblocks listener
  subscription on fresh installs. Token (`tk_*` prefix) preferred over basic auth.
- **Hook + TUI install path + aggregator aligned to `ai_id`** (`1aa74a3aa`). Was a mix of
  pane IDs and basenames; now consistently the basename.
- **TUI systemd matching via prefix** (`d96bde0c8`) — was matching the literal string
  `'systemd'` which broke `systemd-user` detection.
- **Bootstrap-emit pending wake events on first run** (`910bd52cd`). Previously
  bootstrap recorded state-of-the-world WITHOUT emitting; now first run emits any
  pending ECO-decided items so the AI processes the backlog without waiting for the
  next push.
- **TUI Events press actively wakes target pane** (`063e8556a`). Was advisory-only;
  now sends Space+Enter into the target pane so the AI processes any queued state.
- **TUI L+E collapsed into unified Events button** + always-visible `AutoAccept` chip
  (`178659d29`).
- **CLI stops creating stray `.empirica/` subdirs** (`77f64e4ab`) — bootstrap now resolves
  the project root before any directory creation.
- **`EVENT_LISTENER.md` — first-class architecture doc** for the push-primary wake bridge,
  with full fresh-install path + common pitfalls table (`6f0cc2cab`, `ad6cf1ddc`).

## [1.9.6] — 2026-05-16

The "epistemic email for the AI age" release — canonical loops decouple
from Claude Code's in-session CronCreate. Push-primary wake via cortex
ntfy, ECO-gated authorization, AI-to-AI completion acks with commit_sha,
cross-platform parity (systemd-user on Linux/WSL2, launchd on macOS).
Architecture name from David's session: AI orchestration as message
passing through ECO with explicit acknowledgement back to source.

### Added — Canonical loop scheduler (12 phases, full pipeline)

Decoupled from CronCreate. OS-level scheduler + push-bridge into running
Claude sessions via Monitor. Truly synchronous pause via `systemctl stop`
or `launchctl unload`. Zero idle cost when nothing's happening.

**Phase 1 — mechanism (Linux + macOS):**
- `empirica.core.loop_scheduler.SystemdLoopScheduler` — Linux/WSL2 backend
  via `~/.config/systemd/user/empirica-loop-<inst>-<name>.{timer,service}`
- `empirica.core.loop_scheduler.LaunchdLoopScheduler` — macOS backend via
  `~/Library/LaunchAgents/com.empirica.loop.<inst>.<name>.plist`
- `get_loop_scheduler(empirica_bin)` factory — picks backend by `sys.platform`
- New CLI verbs: `empirica loop {enable,disable,systemd-status,tick,listen}`
  (`enable/disable` use `shutil.which()` to bake absolute binary path
  into unit files — bare `empirica` fails silently on PATH-stripped
  systemd-user)

**Phase 2 — content-aware emission (ECO-gated security):**
- `loop tick` polls Cortex inbox+outbox, diffs against per-loop state at
  `~/.empirica/loop_state/<inst>_<name>.json`, emits one JSON line per
  new-or-status-changed proposal to `~/.empirica/loop_fires.log`
- `EMISSION_STATUSES_INBOX = (accepted, changed, declined)` — `eco_review`
  excluded server-side (the ECO-gated autonomy boundary)
- `EMISSION_STATUSES_OUTBOX = (changed, declined, completed)` — `accepted`
  on outbox is informational (target will act)
- `completed` events carry `commit_sha` from audit log details (David's
  AI-to-AI ack primitive)
- Tick throttles when target instance has an open empirica transaction
  (no chat noise while AI is mid-work)
- Bootstrap behavior: first run records state-of-the-world WITHOUT
  emitting (no historical-flood when a loop is first enabled)

**Phase 3 — push-primary bridge:**
- `empirica loop listen --instance X` — holds authenticated stream to
  cortex ntfy topic (`orchestration-events` by default), runs catch-up
  content_poll on each push event AND on reconnect after drop. Bounded
  exponential backoff (1s → 60s cap), 5min auth-failure backoff. Clean
  SIGTERM exit code 0.
- SessionStart hook `session-monitor-arm.py` injects `additionalContext`
  instructing the AI to arm `Monitor(command="empirica loop listen ...",
  persistent=True)` — the listener's stdout = wake events into the
  running Claude session
- ntfy treated as opaque wake-pinger; authoritative content comes from
  catch-up content_poll (preserves ECO-gated auth boundary even under
  ntfy compromise)

**Phase 4 — TUI integration:**
- TUI cockpit `L`-toggle dispatches per loop on `scheduling.scheduler_kind`:
  `systemd-user` → systemctl enable/disable; legacy `cron-create` keeps
  the file-flag pause path
- TUI table collapsed: dropped separate loops + listeners + notifications
  columns into a unified events column (⊕N count + glyph fallback)
- Per-instance detail pane shows latest 5 ProposalEvents with direction
  (▼ inbox / ▲ outbox), status, eco_actor, commit_sha, title
- Live systemd/launchd state injection into aggregator
  (`systemd_active`, `systemd_enabled`, `last_trigger`)

**Phase 5 — TUI auto-accept (per-user, cortex-persisted):**
- `a` keybinding flips `users.auto_accept_mode` toggle on cortex via
  `POST /v1/users/me/auto-accept`
- Summary line shows `⚡AUTO-ACCEPT` chip only when explicitly ON
- Graceful 404/connection-error → chip hidden (no false reassurance)
- 30s module-level cache so TUI refresh doesn't hammer cortex

### Added — Cortex/empirica orchestration (3 outgoing proposals shipped same session)

This session emitted 3 code_change_requests to cortex via `cortex_propose`;
all 3 shipped within hours and the empirica side wired through, validating
the orchestration architecture end-to-end via dogfooding:

- Bootstrap `situation` block on daemon HTTP `/api/v1/bootstrap` — closes
  empirica/cortex divergence noticed when extension v0.8.6 renderer
  couldn't populate (proposal `prop_hseishpnrzg3xow2lsmdgxdew4`)
- Sidecar bootstrap fields (`project / flow_metrics / git_status /
  reference_docs_count`) on daemon HTTP
  (proposal `prop_sf63hrj7xvd3je2gcbzitwsnbi`)
- Cortex ntfy emission on `/respond` + `/complete` + topic split
  (`orchestration-events` for AI-wake, `orchestration-proposals` for
  phone-only ECO actions) + 60s reminder + 120s ECO escalation
  (proposals `prop_efsdt3vidjdevmkngw2wvw6fhe` + `prop_mlezwhcaavffjjjiejidhaci7e`)

### Added — Bootstrap picker recency

`active_goal` picker now ranks `status='in_progress'` over `status='planned'`
and within each tier orders by `created_timestamp DESC`. Previously
excluded planned goals entirely, so a just-created "next-session" planned
goal would never surface even when in_progress goals were stale.

`next_focus` picker: pending subtask → goal-linked unknown (recency DESC)
→ most-recent project unknown (was oldest). Stops surfacing the same
stale unknown after compaction.

### Changed — TUI cockpit table layout

  Before: `s name ph dom S L E N` (sentinel, loops, listeners, notif)
  After:  `s name ph dom S N`     (sentinel, events-unified)

The unified events column shows ⊕N when there are recent ProposalEvents
for the instance, or the loop-liveness glyph as fallback. Listener (T8)
is the unified wake mechanism — three columns for the same concept was
noise.

### Fixed — Validation bug on `scheduler_kind` stamp

Canonical loops carried `scheduler_kind='systemd'` but
`VALID_SCHEDULER_KIND` in `loop_registry.py` is `('cron-create',
'systemd-user', 'system-cron', 'at-queue', 'unknown')`. Heartbeat
silently rejected the bad value (caught in `except ValueError`), so
registry stayed unstamped and TUI dispatch never recognized systemd
loops. Canonical value is `'systemd-user'`.

### Fixed — systemd-user PATH for empirica binary

`handle_loop_enable_command` now resolves the empirica binary to an
absolute path via `shutil.which()` before constructing
`SystemdLoopScheduler`. systemd-user environments don't inherit shell
PATH; bare `empirica` in ExecStart failed silently when the binary
lived in `~/.local/bin` (pipx) or a venv. Smoke-test caught this
immediately on first real-host run.

## [1.9.5] — 2026-05-14

The "empirica-tightening" release — bootstrap is now genuinely useful for
AI compaction-recovery, all epistemic artifacts support markdown bodies,
and the cockpit gains a system-level canonical loop catalog with auto-install
fallback.

### Added — `situation` block in `project-bootstrap` output

Top-level synthesized field answering "where am I right now?" for AI
returning from compaction. Composes from filesystem + DB + git in one
~5ms call:

- `project`: `<name> @ <branch>` shorthand
- `active_transaction`: in-flight PREFLIGHT state from filesystem
  (id, status, opened_at, work_type, work_context, domain, criticality)
- `active_goal`: most recent in_progress goal **with full subtasks list**
  (was previously just `subtask_count`)
- `last_praxic_action`: most recent commit (sha + msg + ISO timestamp)
- `next_focus`: priority cascade — pending subtask > oldest unknown > generic

Placed FIRST in the output (attention-decay-aware). Replaces the previous
template-only `last_activity.summary` (was a raw epoch float) and
`next_focus` (was a generic string).

### Added — `--description` markdown body on all `*-log` commands

Mirrors the goals `--objective + --description` pattern. Every artifact
type now accepts an optional rich markdown body:

| Command | Title field | Rich body |
|---|---|---|
| finding-log | `--finding` | `--description` |
| unknown-log | `--unknown` | `--description` |
| deadend-log | `--approach` + `--why-failed` | `--description` |
| assumption-log | `--assumption` | `--description` |
| decision-log | `--choice` + `--rationale` | `--description` |
| mistake-log | `--mistake` + `--why-wrong` + `--prevention` | `--description` |

Rendered as prettified markdown in the extension and skill surfaces.
Storage: JSON-blob types store inside the existing `*_data` column;
`assumptions` + `decisions` get a new `description TEXT` column (migration 045).

### Added — Canonical loop catalog + TUI auto-install fallback

`empirica/core/cockpit/canonical_loops.py` ships a system-level catalog
the TUI cockpit consults when an instance has no loops registered AND
no `.empirica/project.yaml` `cockpit.loops` block. First entry:

- `cortex-mailbox-poll` — orchestration spine. 30s base, 5m max adaptive
  interval. Body (in companion skill, TBD): `cortex_inbox_poll` +
  `cortex_outbox_poll` via MCP. Self-throttles when an empirica transaction
  is open.

Precedence: project.yaml wins, canonical catalog is the fallback.

### Fixed — Bootstrap JSON double-encoding

`goals[*].scope`, `goals[*].goal_data`, `reference_docs[*].doc_data` were
returned as JSON-encoded strings inside the row dicts. Consumers had to
do a second `json.loads` per field. Now decoded to native dicts.

### Fixed — Stale project counters backfilled

`projects.total_sessions` and `total_goals` were denormalized columns
never wired to insert triggers — bootstrap returned 0/0 on projects with
hundreds of sessions. Replaced with live `COUNT(*)` queries in
`_count_project_artifacts`. Also added `total_transactions` (the more
meaningful unit-of-work measure — counted via `COUNT(DISTINCT
transaction_id)` on the reflexes table). Live values on the empirica
project itself: 915 transactions, 750 sessions, 741 goals.

### Fixed — Daemon registry endpoints survive pre-edges-schema project DBs

The daemon list + graph endpoints crashed when a project's
`.empirica/sessions.db` predated migration 041 (artifact_edges
normalization). Now resolves UUIDs vs slugs correctly + tolerates the
missing edges table.

### Fixed — Multiple lint cleanups

- `import os` removed from `projects_commands.py` (leftover from bulk-register
  simplification, broke v1.9.4 post-tag CI)
- `gh release create` step in `release.yml` now idempotent (won't fail
  when local `--publish` already created the release)
- `release.py --prepare` now gates on `ruff` + `pyright` + `pip-audit` to
  match CI's surface
- B033 duplicate-value set literal in `test_daemon_project_resolver.py`
- C901 complexity refactor on `_build_situation` (split into 5 helpers)
- Test-isolation `~/.empirica/credentials.yaml` leakage in
  `test_source_archive.py` (autouse HOME fixture)

### Build — `ruff` pinned to `>=0.15.13,<0.16`

Previous `ruff>=0.1.0` was floor-only — CI's fresh install grabbed latest
patches with new rules while local installs lagged. Pinning to minor
floor + ceiling: auto-pickup of patches, deterministic ruleset, manual
minor bumps when adopting new rules.

## [1.9.4] — 2026-05-13

### Added — CI/CD scaffolding (.github/workflows/)

empirica's first end-to-end CI/CD harness. Three workflows + Dependabot
+ architecture doc. Patterned on ecodex's CI shape, translated to Python.

- `ci.yml` — push/PR to `main` + `develop` runs ruff + pyright + pytest
  matrix (Python 3.11 + 3.13) + `empirica compliance-report` + pip-audit
- `release.yml` — tag-triggered (`v*.*.*`) PyPI publishing via OIDC
  trusted publishers (no PYPI_API_TOKEN secret needed once configured) +
  Docker build/push (Debian + Alpine) + Homebrew tap auto-update +
  GitHub release. Chocolatey out-of-band (kars85 lane).
- `dependency-scan.yml` — weekly + on `pyproject.toml` PRs: pip-audit
  --strict, hard-fails on any unfixed CVE
- `dependabot.yml` — weekly grouped updates: `pinned-security`
  (cryptography, gitpython, lxml, pydantic, python-dotenv,
  python-multipart, requests), `lint-and-test` (ruff, pyright, pytest*),
  GitHub Actions version updates
- `docs/architecture/CI_CD.md` — full workflow inventory, secrets
  reference, OIDC trusted-publisher setup, local↔CI alignment plan

### Added — MCP / CLI parity for visibility + epistemic_source

The `mcp__empirica__finding_log`, `unknown_log`, `deadend_log`,
`mistake_log`, `assumption_log`, `decision_log` tools now expose
`--visibility {public,shared,local}` and
`--epistemic-source {intuition,search,mixed}` as enum params. Closes
the gap where the cross-Claude intelligence-sharing discipline was
only enforceable through bash CLI, not through the MCP path.
`_ENUM_PARAMS` extended in `empirica-mcp/empirica_mcp/server.py`.

### Added — `source-archive` Cortex sync (Phase 1.5)

When `CORTEX_REMOTE_URL` + `CORTEX_API_KEY` are set, `empirica
source-archive` now calls `DELETE /v1/sources/{id}` on Cortex after
the local archive succeeds. Best-effort — network/HTTP failures never
block the local archive; status surfaces in the response as
`{"cortex": {"synced": false, "status": N, "error": "..."}}`. 7 new
tests covering env-unset no-op, success path, HTTPError, URLError,
`CORTEX_URL` alias.

### Added — Cortex creds via `~/.empirica/credentials.yaml`

The browser extension saves `cortexUrl` + `cortexApiKey` to chrome.storage
so users don't re-enter creds per browser session. The CLI had no
equivalent — only env-var resolution. v1.9.4 wires Cortex into the
existing `CredentialsLoader` so a `cortex:` block in
`~/.empirica/credentials.yaml` is now picked up by all three Cortex
call sites (`projects-bulk-register`, `source-archive` Cortex sync,
POSTFLIGHT `/v1/sync` push).

```yaml
# ~/.empirica/credentials.yaml
version: 1.0
cortex:
  url: https://cortex.getempirica.com
  api_key: ctx_empirica_mem_...
```

Per-field precedence: CLI flags → env vars → credentials file → None.
Setting `CORTEX_API_KEY` in env still picks `url` up from the file —
useful for CI where the key is a secret but the URL is stable.

### Added — Daemon credentials write endpoint (`POST /api/v1/credentials/cortex`)

The Chrome extension stores `cortexUrl` + `cortexApiKey` in
chrome.storage; chrome.storage can't reach the filesystem to populate
the CLI's equivalent. v1.9.4 closes that loop with a daemon endpoint
the extension POSTs to — the daemon (same machine, localhost-only)
writes the file on the extension's behalf.

```bash
POST /api/v1/credentials/cortex
  {"url": "https://...", "api_key": "ctx_..."}
GET  /api/v1/credentials/cortex
  → {"url": "...", "api_key_set": true, "api_key_preview": "...wxyz"}
```

Security model:
- Localhost-only (inherits existing CORS restriction to
  `chrome-extension://*` + `http://localhost*`)
- Atomic file write (tempfile + `os.replace`) — no partial-write
  corruption
- Merge semantics: only the `cortex:` block is touched; `providers:`,
  `version:`, and other sections are preserved verbatim
- GET returns last-4-chars preview only — full key never crosses the
  wire on reads, so even if CORS gets loosened later, exfiltration
  isn't possible from the read path

Per v0.7.9 handoff spec — extension v0.7.9 ships the matching
"Also save to local CLI" Settings checkbox.

16 tests in `tests/test_cortex_credentials_loader.py` covering reader
precedence (7) + writer atomicity / preservation / cache invalidation
(6) + endpoint round-trip / preview-only / empty-payload (3).

### Changed — `projects-bulk-register` sources from `registry.yaml`

The command was over-engineered. Mid-1.9.4 cycle, David flagged the
complexity (Extension Claude's `--only-existing` flag + Cortex
`/v1/collections` intersection round-trip + dry-run coordination)
and pointed out the simpler model: `registry.yaml` (added in 1.9.3 for
the daemon multi-project work) is already the user's curated set —
the same file the daemon reads to decide which projects to serve.
`bulk-register` should ALSO read it. The intersection happens at
curation time, not at command time.

What ships:

- **Default source: `~/.empirica/registry.yaml`** (the curated set the
  daemon serves). Populate it via `empirica projects-discover --register`.
- **`--from-discovered` flag** — opt-in to source from the raw scanner
  output (`~/.empirica/discovered_projects.yaml`) for the "register
  everything I have, no curation" workflow
- **`--force-metadata-update`** — kept; still sets the body flag for
  Cortex's safe-update of existing rows

What got removed:

- `--only-existing` flag (redundant — registry.yaml IS the curated set)
- `_fetch_cortex_collections()` helper + Cortex `/v1/collections`
  round-trip
- `_filter_to_registered()` intersection logic
- `_maybe_resolve_cortex_config()` conditional auth helper
- ~80 LOC delete, 6 tests instead of 13

Backstop preserved: `_workflow_postflight._cortex_resolve_project_metadata()`
still enriches `/v1/sync` payloads with `name + repo_url` so Cortex's
auto-create on unknown project_ids doesn't seed rows with `name=<UUID>`,
`repo_url=""` (EC-2 root cause from the v0.7.8 handoff).

### Added — empirica-mcp tests

The empirica-mcp package shipped 319 tests across 3 new files:

- `test_command_builder.py` — `_build_cli_command`, `_resolve_cwd`,
  `_err_text` from the v1.9.3 refactor
- `test_tool_schema.py` — `_build_tool_schema` branches (numeric /
  boolean / enum / list / stdin_json / submit_* special cases)
- `test_registry_integrity.py` — parametrized over every TOOL_REGISTRY
  entry: required keys present, required ⊆ params, list_params ⊆ params,
  positional ∉ params, schema builds without error, registry size floor

Caught one real registry bug in the process (`noetic_batch` declared
`required: ["intent"]` but `intent` lives in stdin JSON, not params).

### Added — `workflow_commands.py` split (3933 LOC → 4 modules)

Largest single file in the codebase split via AST-driven prefix
grouping. Public handler signatures + import paths preserved; legacy
test imports keep working via re-export shim:

- `_workflow_shared.py` (612 LOC) — db/session resolution, sentinel
  hook invocation, retrospective counters, vector normalization,
  noetic/voice guidance, retrospective helpers (shared by check +
  postflight)
- `_workflow_preflight.py` (747 LOC) — `handle_preflight_submit_command`
  + pattern retrieval + behavioral feedback
- `_workflow_check.py` (1103 LOC) — `handle_check_command` +
  `handle_check_submit_command` + gate logic + drift + blindspot scan
- `_workflow_postflight.py` (1431 LOC) — `handle_postflight_submit_command`
  + storage pipeline (qdrant/cortex/breadcrumbs/episodic/snapshots) +
  grounded verification + compliance loop
- `workflow_commands.py` (61 LOC) — thin re-export shim

### Changed — cross-project artifact sharing taught in system prompt + docs

The `--visibility {public,shared,local}` flag and `project-search
--global` have been available for releases but nothing taught AIs to
use them as a coherent cross-Claude intelligence-sharing discipline.
Three doc updates:

- `empirica-system-prompt-lean.md` (COLLABORATIVE MODE table) — new
  signal→action rows for `--visibility shared` on ecosystem-wide
  findings, `project-search --task --global` proactive lookups, and
  cross-project artifact writing via `--project-id <name>`. New
  section explaining visibility tiers + honest scope caveat
- `docs/reference/api/CROSS_PROJECT.md` — new "Visibility (push side)"
  section with the public/shared/local matrix and when to use which
- Caveat surfaced in both: v1.9.3 `--global` only hits the
  `global_learnings` Qdrant collection, not the full per-project
  surface (the broader walk is a deferred goal)

### Changed — empirica-mcp `call_tool()` refactored D27→C14

Extracted `_build_cli_command`, `_resolve_cwd`, `_err_text` helpers.
Drops the noqa: C901 suppression. Behavior unchanged.

### Fixed — broken markdown links to gitignored draft directories

5 broken links in `docs/architecture/PROPOSAL_AI_SERVICE_SCANNER.md`
and `docs/guides/UPGRADE_TO_1.9.md` pointed at `docs/specs/` and
`docs/research/` — both intentionally gitignored as draft directories.
Converted to plain-text references: `*text* — \`path\` (local-only
draft, not in public repo)`. Local sees the files and the links
resolve there too; CI's fresh checkout no longer flags 5 broken links
that pass locally.

### Removed — internal-only docs from public tree

Audit pass for content that shouldn't be in the public repo:

- `docs/architecture/CHAT_OVERNIGHT_PLAN.md` (260 LOC) — David's
  personal autonomous-build brief (moved to gitignored
  `.empirica/notes/historical/`)
- `docs/architecture/PROMPT_FOR_EMPIRICA_CLAUDE_source_aware_sentinel.md`
  (222 LOC) — AI-to-AI handoff prompt (same destination)
- `docker-compose.yml`: `/home/yogapad/.empirica` → `${HOME}/.empirica`
- `empirica/cli/command_handlers/diagnose_ecodex.py:854`: hardcoded
  `/home/yogapad/empirical-ai/...` path → resolves via
  `Path(empirica.__file__).parent / "plugins/..."`
- `docs/architecture/instance_isolation/KNOWN_ISSUES.md:396`: example
  path anonymized to `<project-path>/...`

Plus forward-looking `.gitignore` patterns:
```
docs/**/PROMPT_FOR_*.md
docs/**/*OVERNIGHT*PLAN*.md
```

### CI / compliance

Hardened the CI compliance step to match local environment:

- `fetch-depth: 0` + `fetch-tags: true` on the compliance job so
  `release_chain` sees the git tag and `ai_transparency` can sample
  50 commits (was 0 with default fetch-depth: 1)
- Install `[dev,api,tui,vector]` + `./empirica-mcp[dev]` so pyright's
  80 `reportMissingImports` (flask, fastapi, mcp, etc.) disappear
- 8 environment-dependent test files marked `@pytest.mark.integration`
  (subprocess `empirica`, git-init CWD, populated DB requirements).
  CI runs `pytest -m "not integration"`. Integration job for those 85
  tests is a logged follow-up
- 5 hardcoded `/home/yogapad/...` paths in tests replaced with
  `REPO_ROOT = Path(__file__).resolve().parents[3]`
- Per-line `# pyright: ignore[reportMissingImports]` on
  guarded-by-try-except PIL/cv2/textstat/proselint imports

Compliance score on CI now 1.0 (8/8 deterministic checks); was 0.5
(4 passing, 4 failing on env-specific grounds).

## [1.9.3] — 2026-05-12

### Added — Daemon multi-project support

The `empirica serve` daemon now serves all locally-known projects from a
registry instead of being bound to one project at startup. Tier 2/3 users
with multiple `.empirica/` directories on the same machine no longer need
to restart the daemon to switch project context.

- **`~/.empirica/registry.yaml`** — new registry file listing projects the
  daemon is willing to serve. Atomic writes (tempfile + rename). YAML
  chosen so users can hand-edit with comments. Schema:
  ```yaml
  version: 1
  projects:
    - project_id: 748a81a2-ac14-45b8-a185-994997b76828
      slug: empirica
      name: empirica
      path: /home/user/empirical-ai/empirica
      repo_url: https://github.com/EmpiricaAI/empirica
      last_seen: 2026-05-12T08:32:00Z
  ```
  `project_id` is the Cortex UUID when registered, or a local slug for
  Empirica-only users.

- **Per-request `?project_id=X` query param** on every GET `/api/v1/`
  endpoint. No param → CWD-bound active project (existing behavior,
  backward-compat). `?project_id=X` → registry lookup. `?path=Y` →
  power-user bypass (opens `Y/.empirica/` directly, no registry lookup).
  Endpoints covered: `/health`, `/bootstrap`, `/goals`, `/findings`,
  `/decisions`, `/unknowns`, `/mistakes`, `/dead-ends`, `/assumptions`,
  `/sources`, `/artifacts/graph`, `/artifacts/{id}`.

- **`/api/v1/health` exposes `known_projects[]`** — full registry surfaced
  in the health response so the Chrome extension can populate its project
  dropdown without round-tripping Cortex.

- **`empirica projects-discover --register`** flag — after scanning, upsert
  each discovered project into `~/.empirica/registry.yaml`. Reads each
  project's `.empirica/project.yaml` to extract the canonical `project_id`
  (Cortex UUID when registered; slug otherwise). Idempotent.

- **`empirica projects-discover --register --prune`** — also remove
  registry entries whose path no longer exists or no longer contains
  `.empirica/`.

- **`empirica daemon-list`** verb — prints the registry contents
  (table / yaml / json) for quick inspection.

- **27 new tests** across `tests/test_registry.py` (registry module:
  load/save round-trip, atomic write, upsert idempotency, prune semantics)
  and `tests/test_daemon_multi_project.py` (route-level: `?project_id=X`
  hits + 404 misses, `?path=Y` bypass, no-params CWD fallback, 503 when
  daemon unbound).

### Security

- **CVE-2026-42561** — pin `python-multipart>=0.0.27` (was 0.0.26 transitively
  via `mcp`/`nicegui`/`p4-confidence-tracker`). Direct pin in core
  `pyproject.toml` overrides the transitive resolution. `pip-audit` clean.

### Added

- **`empirica source-archive` CLI** (SOURCES_LIFECYCLE_SPEC Phase 1) — soft-delete
  verb for epistemic sources. Layer A (sources table) gets `archived=1` +
  `archive_reason` + `archive_target_id` + audit log; Layer B (Qdrant chunks)
  gets hard-deleted via batched filter; Layer C (artifact_edges) is immutable.
  Four reasons supported: `user_deleted`, `file_missing`, `url_unreachable`,
  `superseded` (requires `--target-id`). `source-list` excludes archived by
  default; pass `--include-archived` to see them. Empirica is authoritative;
  the Cortex API push is supplementary follow-up (Phase 1.5).
- **`goals.description` field** (migration 043) — Linear/GitHub/Jira-style split:
  `objective` is the short title (≤200 chars), `description` is the long-form
  body with acceptance criteria, scope notes, success metrics. Both writable
  via `empirica goals-create --objective "..." --description "..."`. Older
  rows keep `description = NULL`; new rows fill both.

### Changed

- **Goal description max length** bumped from 1000 → 2000 chars for richer
  acceptance-criteria capture. The 1000-char ceiling was tripping on standard
  Linear-style spec bodies.
- **System prompt + epistemic-transaction skill** updated to teach AIs the
  goal-vs-subtask discipline natively: `goals-add-subtask` for decomposition,
  `goals-complete-subtask --evidence` for closure with grounded evidence.
  Plus batch-op signal→action rows (`log-artifacts`, `resolve-artifacts`,
  `delete-artifacts`) and `noetic-batch` guidance.
- **Statusline glyph parity** — swap `⚙` (U+2699, ambiguous east-asian-width)
  to `🔨` (U+1F528, wide) so the praxic-phase indicator no longer renders over
  the `%` confidence digit on terminals that don't normalize ambiguous-width
  glyphs. Matches existing doc references which already used `🔨`.
- **CONTRIBUTING.md** rewritten to reflect the current plugin + skill +
  hook architecture; legacy `docs/human/developers/system-prompts/` directory
  deleted (kept the lean template for opt-in `--full-prompt` mode); duplicate
  agent `.md` files de-duped; forgejo remote removed from public docs.

### Fixed

- **kars85 #102** — `strftime('%s', 'now')` dialect adapter now translates to
  `EXTRACT(EPOCH FROM NOW())` on PostgreSQL backends. Was breaking
  `project-bootstrap` on Postgres-backed deployments.
- **Statusline cascade-aware scoping** for the provenance widget (later
  reverted — see below); recency-merge bug surfaced during widget development.

### Reverted

- **Statusline `🔎X%` provenance widget** — shipped briefly through
  iterations, then pulled. The signal it was meant to surface (intuition vs
  search ratio) doesn't apply in CLI surfaces: Claude Code *is* the harness,
  so by definition every artifact gets shaped by external reads/greps/web
  fetches. The widget belongs on Claude Desktop and chat surfaces where
  context isn't externally grounded by default. Logged as article material;
  zero net statusline behavior change after revert.

### Notes

- **Cryptography pin cap `>=44.0.1,<48`** carried over from 1.9.2 — keeps
  shared-env compat with `pynitrokey`/`spsdk` and other hardware-token tools
  that pin `cryptography<47`. We use no 48-only API.

## [1.9.2] — 2026-05-08

### Added — Three-circle bootstrap aggregator (v0.6 spec)

Replaces the uniform-decay model with a three-circle surfacing model that
captures different "kinds of relevance" instead of treating everything as a
recency function. Implements `docs/specs/PROPOSAL_BOOTSTRAP_AGGREGATOR.md`.

- **Circle 1 — `active_state`** — recency-decayed via per-type half-lives
  (∞ for in-progress goals/subtasks, 30d for findings/decisions, 14d for
  dead-ends/mistakes). Tiebreaker only — circle is small.
- **Circle 2 — `persistent_reference`** — never decays, fixed budgets.
  Decisions with active outcome (rationale still load-bearing), verified
  or falsified assumptions (now ground truth), sources (citation base).
- **Circle 3 — `topic_relevant_backlog`** — Qdrant cosine similarity to
  active topic (default threshold 0.65), per-type slot budgets. Surfaces
  open backlog plus completed-on-topic / resolved-on-topic / dead-ends-on-
  topic for anti-clobber. Skipped when no topic detected.
- **Active topic detection** — deterministic 3-step fallback chain:
  transaction.task_context + active_goal.objective → recent (7d)
  high-impact findings → none.
- **Wire shape `schema_version: "2"`** — three top-level keys, every item
  carries `weight`, `surface_reason`, `similarity_score` (circle 3 only),
  `related_to[]` (depth=1 edge fold from `artifact_edges` via single
  batched query).
- **Public API**: `build_bootstrap_payload()` — pure function consumed by
  the CLI hooks (post-compact / session-init), the daemon endpoint
  `GET /api/v1/bootstrap`, and the MCP tool
  `mcp__empirica__bootstrap_context`. New `empirica bootstrap-context`
  CLI verb for direct introspection.

### Added — Bootstrap injection trio (Items 4/5/6)

Three new injection surfaces that surface relevant artifacts at the moments
the AI is making decisions, not just at session start.

- **Item 6 — `*-log` response enrichment with `suggested_links`** — every
  `finding-log` / `unknown-log` / `deadend-log` / `mistake-log` /
  `assumption-log` / `decision-log` call now returns up to 5 semantically
  similar existing artifacts above 0.65 cosine similarity. AI can anchor
  edges via `--related-to <id>` on a follow-up call. Closes the "AI
  doesn't think to link artifacts" gap measured by `edges_with_artifacts`
  in the v0.5 substrate retrospective.
- **Item 4 — PreToolUse file-relevance nudge** — when the AI is about to
  Edit/Write/MultiEdit a file, the sentinel-gate hook now surfaces a one-
  line summary of artifacts already referencing that file:
  `FILE-RELEVANCE: 2 findings, 3 dead-ends reference this file`. SQLite
  LIKE search across all six artifact text columns, ~50ms hot-path budget
  via per-table query caps.
- **Item 5 — UserPromptSubmit prompt-relevance** — every substantive user
  prompt now triggers an embed → semantic search → `<prior-context>` block
  injection. Top-3 most-similar artifacts above threshold appear in
  additionalContext so the AI's first response is conditioned on prior
  project knowledge rather than internal weights alone. ~200ms hot-path
  budget.

### Added — Compliance + lint enhancements

- **`empirica docs-link-check`** — general broken-link checker for tech
  docs. Tier-prioritized output (key README, per-folder READMEs, deep
  links). Standalone CLI verb plus opt-in compliance check.
- **`tech_docs_links` compliance check** — separate from `tech_docs`
  coverage. Mapped to EU AI Act Art. 11 + Annex IV.
- **`repo_hygiene` version_file** — now accepts Rust `Cargo.toml` and
  Node `package.json` shapes alongside Python `pyproject.toml`, supporting
  cross-language repos in the empirica ecosystem.
- **`rust-docs-assess` (Tx-BA)** — Rust-aware tech_docs check that
  understands `cargo doc` semantics so Rust crates aren't penalized for
  missing Python-style docstrings.
- **Tx-AG investigation-proportionality budget** — the soft block on
  `<investigation-proportionality>` was empirically ineffective (8 search
  rounds in a hypothesis-bearing user prompt). Sentinel-side budget now
  *enforces* the limit, denying Read/Grep/Glob once the per-prompt budget
  is exceeded. Hard constraint instead of soft text.
- **Tx-AJ EMPIRICA_SENTINEL_FAIL_CLOSED toggle** — opt-in fail-closed mode
  for hardened deployments. Default unchanged (fail-open + SENTINEL_CRASH
  to stderr) for dev. Production agentic frameworks can flip the bit so
  sentinel crashes deny rather than silently allow.
- **Tx-AK ecodex vendored hook drift detector** — `empirica diagnose
  --frontend ecodex` now flags when the empirica plugin's vendored hook
  scripts have drifted from canonical sources, catching silent skews.

### Added — Side-fix surfaced by Item 6

Three Qdrant embed functions (`embed_single_memory_item`,
`embed_assumption`, `embed_decision`) previously omitted `artifact_id`
from their payloads. This silently broke `circle_3._qdrant_similarity_pull`
in the bootstrap aggregator — `payload.get("artifact_id")` always missed,
so `topic_relevant_backlog` returned empty in practice. All three payloads
now include `artifact_id`. SQLite reverse-hash fallback in
`suggested_links` resolves pre-fix Qdrant points without requiring a
`project-embed` rebuild.

### Internal

- 84 new tests across the bootstrap surface (29 aggregator + 19
  suggested_links + 17 file_relevance + 19 prompt_relevance). Full suite
  green at 2293 passed, 4 skipped.
- `empirica-mcp/` brought into the repo's lint scope. Was previously
  outside `tool.ruff.include`. 25 ruff errors cleaned (mostly auto-fixable
  whitespace + import ordering); one C901 noqa'd on the flat MCP-tool
  routing function with a refactor goal logged for the next cycle.

## [1.9.1] — 2026-05-06

### Added — v0.5 LOCAL-ARTIFACTS daemon (T1-T5)

The `empirica serve` daemon gains 16 new endpoints for local artifact access,
unblocking the Empirica chrome extension's full Artifacts pane evolution.
Empirica-only users (no Cortex account) can now see their artifacts in the
extension for the first time. Hybrid users get faster active-project queries
than Cortex roundtrips. Cortex-only users are unaffected.

- **`/api/v1/health` extension** — adds `project_id` (canonical UUID),
  `project_path`, `project_name`, `project_slug`, `repo_url` so the extension
  can match dropdown's active project against the daemon's bound project and
  populate the dropdown for users without Cortex.
- **8 per-type list endpoints** — `/goals`, `/findings`, `/decisions`,
  `/unknowns`, `/dead-ends`, `/mistakes`, `/assumptions`, `/sources`. Each row
  carries `related_to[]` from the new `artifact_edges` table.
  Type-specific filters (`?status=`, `?confidence_min=`).
- **4 single-artifact CRUD endpoints** — `GET /artifacts/{id}` (polymorphic
  type resolution + edge neighborhood), `PATCH /artifacts/{id}/resolve`
  (per-type semantics), `PATCH /artifacts/{id}` (whitelisted partial update),
  `DELETE /artifacts/{id}` (three-layer cleanup: sqlite + edges + Qdrant +
  git notes).
- **Graph endpoint** `GET /artifacts/graph` — bidirectional BFS over
  `artifact_edges` with `seed_id` / `session_id` / `types` / `depth` /
  `max_nodes` filters.
- **3 batch endpoints** — `POST /artifacts/log` (proxies `log_artifacts_graph()`
  pure function), `POST /artifacts/resolve`, `POST /artifacts/delete`.
- **Migration 041** — new `artifact_edges` table with PRIMARY KEY (from_id,
  to_id, relation), `(to_id, relation)` inverse-query index, `metadata` JSON
  column for forward-compat. Backfills existing edges from `data.edges` JSON
  in artifact tables. Fixes silent edge-drop bug where assumptions and
  decisions (which had no `data` column) lost edges entirely.
- **Migration 042** — adds `impact REAL DEFAULT 0.5` to `project_dead_ends`
  and `mistakes_made` on long-lived DBs. Migrations 007/012 only covered
  findings/unknowns; the schema CREATE TABLE has it for fresh DBs but no
  ALTER existed for upgrades. Without this migration, `GET /api/v1/dead-ends`
  500s on real-world DBs.
- **Daemon project resolver** at `empirica/api/daemon_project.py`. Two-layer
  resolution: `InstanceResolver.project_path()` (canonical chain) → CWD walk-up
  for `.empirica/project.yaml` (daemon-specific tail for "no CC instance"
  case canonical fails-fast on by design). Slug→UUID lookup via the `projects`
  table when yaml's `project_id` is a slug. Process-lifetime cache.

### Fixed

- **`/api/v1/dead-ends` 500 on real-world DBs** — root-caused to missing
  `impact` column on long-lived `project_dead_ends`/`mistakes_made` tables.
  Migration 042 closes the gap.
- **CORS preflight 400 from chrome-extension origins** — the daemon's CORS
  config previously used `allow_origins=["chrome-extension://*", ...]` with
  literal globs, which Starlette does not expand. Real chrome-extension
  origins were silently rejected. Switched to `allow_origin_regex`.
- **Project_id slug-vs-UUID mismatch** — `.empirica/project.yaml.project_id`
  is often a slug (e.g. `"empirica"`) matching `projects.name`, not the
  canonical UUID used in artifact tables. Daemon now does the slug→UUID
  lookup so `/health.project_id` and per-project queries use the right
  identifier.
- **`empirica delete-artifacts` git-notes gap** — CLI delete was leaving
  stale git notes at `refs/notes/empirica/{type}/{id}` after sqlite + Qdrant
  cleanup. The CLI now also runs `git update-ref -d` to clean the third
  storage layer, matching the new daemon `DELETE /artifacts/{id}` behavior.
- **Silent edge drop on assumptions/decisions** — pre-migration 041, edges
  pointing from these types had no storage location (their tables had no
  `data` JSON column) and `_store_edge` no-op'd them. Now persisted in the
  normalized `artifact_edges` table.

### Internal

- `log_artifacts_graph()` factored as a pure function in `graph_commands.py`
  so the daemon's `POST /artifacts/log` calls it directly without subprocess
  overhead. CLI handler is now a thin wrapper.
- `_ReadOnlyDB` wrapper (sqlite3 direct, no `SessionDatabase` init chain) for
  daemon read endpoints. Avoids dragging in the migration runner / repository
  init for endpoints that are pure read paths.
- 96 new tests across 5 transactions: 37 (T1) + 15 (T2) + 15 (T3) + 14 (T4) +
  15 (T5). Includes E2E subprocess + httpx tests that exercise real uvicorn
  binding and CORS preflight (caught the CORS bug).
- Wire-shape ping committed at `empirica-extension/docs/v0.5-LOCAL-ARTIFACTS.md`
  (commit cd5fa2e in that repo) flagging the three bug fixes for the extension
  consumer migration.

## [1.9.0] — 2026-05-06

### Added — Goal-driven post-tests bridge

Goals can now declare measurable success criteria that auto-evaluate at
POSTFLIGHT. The "I preserved your voice" / "all subtasks done" / "metric X
under threshold" claim becomes a falsifiable check with a number attached.

- **Evaluator protocol + registry** at `empirica/core/post_test/criterion_evaluators/`.
  `CriterionContext`, `CriterionResult`, `CriterionEvaluator` Protocol; first-applicable
  dispatch with exception isolation (raises absorbed as skipped, not fatal).
- **`SubtaskCompletionEvaluator`** (built-in, auto-registered for `validation_method=completion`):
  ratio of completed subtasks vs threshold, default 1.0. Zero-subtask path falls back to
  `is_completed` flag, otherwise skipped.
- **`EvidenceMetricEvaluator`** (built-in, `validation_method=quality_gate`): reads a named
  metric from the POSTFLIGHT `EvidenceBundle`, applies the metric's declared
  `direction` (`higher_is_better` / `lower_is_better`) for op selection, compares against
  threshold. Skips cleanly on missing metric.
- **`goal_criteria` block in POSTFLIGHT response** with per-criterion results
  (`evaluated`, `passed`, `failed`, `skipped`, `iteration_needed`). Self-diagnosing —
  when an evaluator is registered but doesn't apply (metric absent), the message names
  the evaluator and explains why instead of "no evaluator registered".
- **`GoalRepository.list_active_criteria_for_session` + `update_is_met`** persist
  evaluation results to both the normalized `success_criteria` table and the parent
  goal's `goal_data` JSON for read-path consistency.
- **`GoalRepository.add_success_criterion(...)`** SDK helper for programmatic
  criterion authoring.

### Added — Typed `--success-criteria` parser

`empirica goals-create --success-criteria '["method:metric@op:threshold", ...]'`
now parses the typed expression form. Examples:

```bash
empirica goals-create --objective "Preserve David's voice in published outreach articles" \
  --success-criteria '["quality_gate:prose_stylometry_composite_drift@<=0.25"]'

empirica goals-create --objective "Refactor auth module" \
  --success-criteria '["completion:subtask_ratio@>=0.9"]'
```

Bare strings continue to default to `validation_method=completion` for backward
compatibility. Op `<=` / `>=` is informational — evaluator's direction inference handles
comparison semantics. Malformed expressions fall back to bare-string completion (no crash).

### Added — Stylometry / voice-drift collector

Computes 12 stylometric markers from session prose and compares against a voice
fingerprint at `~/.empirica/voice/<name>.fingerprint.json`. Emits
`prose_stylometry_composite_drift` as an `EvidenceItem` consumable by the
goal-criterion `EvidenceMetricEvaluator` — turns voice preservation from
asserted into measured.

- T1 markers: contractions ratio, first-person ratio, function-word ratio,
  type-token MTLD, sentence-length stdev, avg word length.
- T2 markers: punctuation distribution, question/exclamation/em-dash rates,
  sentence-initial token diversity, paragraph rhythm.
- Voice resolution priority: `EMPIRICA_VOICE` env > project `.empirica/voice/.default`
  > user `~/.empirica/voice/.default`. Activation gated on prose ≥ 200 words.
- Drift direction inference: `formal_pull` / `informal_pull` / `mixed` /
  `within_tolerance` / `no_signal` from formal-aligned marker movements.
- Pure stdlib + hardcoded contraction (~50) and function-word (~150) lists. No
  heavyweight deps. Curly apostrophes normalized.
- Background: van Nuenen et al. (April 2026) — all frontier models drift toward
  formality regardless of voice-preservation prompts. Voice belongs at the
  measurement layer, not the instruction layer.

### Added — Content-aware source-provenance nudge

When `*-log` commands receive text containing URLs but no `--source` flag, the
CLI emits a stderr nudge naming the detected URL and listing three concrete
remediations (`empirica source-add` then re-log, tag with `--epistemic-source
search`, or suppress with `EMPIRICA_SUPPRESS_PROVENANCE_NUDGE=1`).
Non-blocking — artifact still logs.

Closes the long-standing source-adoption gap (prior nudges at CHECK and
POSTFLIGHT measured 0% adoption). Symmetric `--source` flag added to
`assumption-log`, `decision-log`, `mistake-log` parsers (previously only on
finding/unknown/deadend).

### Added — `EvidenceItem.direction` + `EvidenceBundle.has`/`get`/`direction` helpers

Net-new optional `direction: str = "higher_is_better"` field on `EvidenceItem`.
Collectors emitting raw error counts / violation densities should set
`"lower_is_better"`. New `EvidenceBundle` helpers for named-metric lookup —
`has(metric)` / `get(metric)` (prefers `raw_value` when scalar, falls back to
normalized `value`) / `direction(metric)`. Used by goal-criterion
EvidenceMetricEvaluator. Backward-compatible — existing emitters don't need
changes.

### Added — Live-scan semantic index

`docs/SEMANTIC_INDEX.yaml` is no longer a hand-managed cache that drifts every
release. The loader detects staleness against source mtimes and live-scans
automatically, writing the refreshed result back so subsequent reads stay fast.
Graceful degradation when scan fails (falls back to stale cache before
returning None).

- `scan_project` extracted to `empirica/core/docs/semantic_scan.py` so loader
  + generator script share the implementation.
- `load_semantic_index(force_scan=False, write_back=True)` — optional kwargs;
  positional callers unaffected.
- The committed YAML is deleted; will regenerate on first call. (Was 326
  entries cached vs 434 live = 33% stale, 6 weeks of doc additions invisible
  to semantic search.)

### Added — `empirica projects-discover` + `projects-list` + `projects-bulk-register`

Power-user CLI for bulk-linking N local `.empirica/` repos to Cortex in one shot.
Per `empirica-extension/docs/v0.5-BULK-PROJECT-LINK.md`.

- **`projects-discover`** walks roots (default `$HOME`) for projects identified
  by `.empirica/project.yaml`. Outputs YAML/JSON manifest with path, name,
  `repo_url` (ssh→https normalized), `git_remote_origin`. Default cache:
  `~/.empirica/discovered_projects.yaml`. `--max-depth`, `--include-hidden`
  flags. Skips noise dirs (`node_modules`, `.git`, `.venv`, `__pycache__`,
  `build`, `dist`, etc.).
- **`projects-list`** reads cached manifest with `--refresh` for fresh scan.
  Table / yaml / json output.
- **`projects-bulk-register`** [Cortex-dependent] iterates manifest and POSTs
  each to Cortex's `/v1/projects/register`, falling back to `/v1/admin/projects`
  on 404/405. Idempotent — 409 (already exists) is silent skip. Network errors
  and other 4xx/5xx logged + loop continues. `--dry-run`, `--cortex-url`,
  `--api-key`, `--timeout` flags.

### Fixed — Sentinel quote-aware redirect detection (#NN)

`gh api ... | python3 -c "if x > 5: ..."` no longer false-positive blocked.
The `>` inside quoted python code was being treated as a shell file-redirect.
`_has_dangerous_redirects` now uses `_contains_outside_quotes` to match the
quote-aware logic already used by pipe and chain detection. Real redirects
(`cat foo > out.txt`) still block. Heredocs and stderr-suppress (`2>&1`,
`2>/dev/null`) still safe.

### Fixed — System-prompt template version drift (#100)

Closes Philipp's #100. The lean and full system-prompt templates had hardcoded
`v1.7.0` strings — drifted 8 minor versions before being caught. Every release
silently re-introduced drift.

Templates now use `{{ empirica_version }}` and `{{ generated_date }}`
placeholders. `setup-claude-code` substitutes them at write-time from
`empirica.__version__` and today's UTC date via the new
`_render_versioned_template` helper. Source templates never mutated; idempotent
re-renders. Real-template sanity tests fail if anyone reverts to a hardcoded
version. The drift cannot recur.

### Fixed — Goal-criterion dispatcher diagnostic message

When evaluators are registered but none apply (e.g., quality_gate criterion
whose named metric isn't in the bundle), the dispatcher now reports
`"Registered evaluator(s) for 'quality_gate' did not apply
(EvidenceMetricEvaluator) — required input absent"` instead of the misleading
`"No evaluator registered"`. Distinguishes the two paths in the response so
the failure mode is diagnosable without source-reading.

### Internal — Project-context guide rewrite

`docs/guides/PROJECT_SWITCHING_FOR_AIS.md` rewritten as authoritative reference.
The previous version was framed as "problem-to-solve" for the `project-switch`
verb that shipped in 1.3.0. New content covers what actually ships: TTY-pane
instance isolation, three resolution coordinates (`instance_id` /
`claude_session_id` / `project_id`), session row as canonical truth, project_id
resolution priority, cross-project `--project-id` flag, common failure modes
table, system-prompt guidance block.

### Internal — Tmux multi-pane guide refresh + cockpit section

`docs/guides/TMUX_MULTI_PANE_GUIDE.md` adds a Cockpit section
(launch/status/groups/TUI/notify_dispatcher) and rewires "Related Documentation"
to point at `instance_isolation/README.md` (entry point) plus new cross-refs to
`COCKPIT.md` and `NOTIFY.md`.

### Internal — `UPGRADE_TO_1.7.md` → `UPGRADE_TO_1.9.md`

Old 1.7-era plugin-rename + lean-core-as-experimental doc replaced. New doc
covers the 1.7→1.9 jump: goal-driven post-tests, stylometry, source nudge,
cockpit + groups, source-aware Sentinel substrate, commit-context + edge
declaration, live-scan semantic index, quote-aware redirect, lean core as
default. Plugin README inbound link updated.

### Internal — Compliance JSON↔human consistency regression tests

9 tests in `tests/test_compliance_consistency.py` lock in the invariant that
`compliance-report` JSON and human-formatted output always agree on
status/score/passed-counts. Both formats read the same `report["overall"]`
dict produced by `_compute_overall_status` — no parallel computation, no
cache. Tests sweep pass/fail combinations and use sentinel-mismatched values
to prove single-source-of-truth.

## [1.8.20] — 2026-05-04

### Added — Graph + temporal layer for the artifact store

The session that's been logging into `refs/notes/empirica/*` for months now
has primary-key access by commit. Three composable pieces:

- **`empirica commit-context <sha>`** (new CLI). Aggregates artifacts
  noted on a commit (or `--range rev1..rev2`, `--since DATE`,
  `--session ID`) and outputs them grouped by type with per-artifact
  `created_at` previews. Cached commit→artifact index at
  `.empirica/cache/commit_artifact_index.json` invalidates on
  `refs/notes/empirica/` mtime change. Per-commit JSON via
  `--output json`. Tier 1 in the Sentinel allowlist (read-only).
- **`--depth N` recursive walker.** Walks edges from each artifact's
  note JSON to render the epistemic neighbourhood at a commit. Edge
  sources, in order: graph-format `<type>_data.edges[]` from
  `log-artifacts`, `goal_id`, `subtask_id`,
  `<type>_data.parent_id` + `parent_type`. Cycle detection via visited
  set. Tree output with relation labels (`└─ [type/short_id] ←relation
  preview`) in human mode; full nested JSON in `--output json`.
- **Inline edge declaration on individual `*-log` commands.** All six
  artifact log commands (finding/unknown/dead-end/mistake/assumption/
  decision) gain `--edge ID:RELATION` (canonical, repeatable) and
  `--related-to ID` (convenience, defaults relation `related`).
  `decision-log` additionally accepts `--evidence-from ID` for
  evidence-relation links. Edges persist to both SQLite data column
  (via `_store_edge` plumbing from `log-artifacts`) and the
  artifact's git note (read-modify-write via `git notes` plumbing —
  uniform across types). Walker traverses them automatically.

### Added — Post-compact temporal trail

`post-compact.py` now injects a one-line trail into all three prompt
generators (new-session, transaction-continue, check-gate) pointing
at `commit-context`. After a compaction, AIs see e.g.
`Temporal trail: 4,343 artifact git notes anchored to commits.
Query: empirica commit-context <sha> | --since <date>`. Closes the
discoverability gap that previously required git-notes archaeology
to surface.

### Added — Sources discipline + edge-density behavioural nudges

Mirrors the existing artifact-breadth nudge. Both fire when a
transaction has ≥2 artifacts but zero declarations on the watched
dimension:

- **`edge_density_nudge`** — POSTFLIGHT retrospective +
  CHECK-proceed reminders surface `edge_density_note` /
  `edge_density_nudge` when `_retro_count_edges` returns zero. Walker
  reach scales with adoption.
- **`sources_discipline_nudge`** — same shape, counts artifacts
  with non-empty `source_refs`. Project adoption today is 2/3,131
  (~0.06%) — this nudge is the behavioural lever to lift it.
  POSTFLIGHT feedback exposes both as `edge_density_warning` /
  `sources_discipline_warning`.

### Added — Source-aware Sentinel substrate (v0, visibility-only)

Optional `--epistemic-source {intuition|search|mixed}` flag on every
`*-log` command (and `data.epistemic_source` in `log-artifacts`
payloads) tags how the artifact was arrived at. POSTFLIGHT
`calibration_reflection` surfaces a per-transaction
`epistemic_provenance` block with intuition/search/mixed counts +
ratio. Migration 040 adds the `epistemic_source` column to all
artifact tables. **v0 is visibility-only — no routing rule yet.**
The v1 routing-rule design lives in
`docs/architecture/PROPOSAL_SOURCE_AWARE_SENTINEL_v1.md`, deferred
until calibration history accumulates.

### Added — Cockpit groups mode (3-window layouts)

Per `docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md` follow-up. The launcher
now supports `groups:` config — N alacritty windows, each running its
own tmux session with multiple panes. Config includes `surface:`,
`alacritty_args:`, `launch:` per pane. Augment-adopt: re-launching
adds missing panes without disturbing live processes. Dedup: skips
alacritty spawn when a tmux client is already attached. SIGHUP
isolation via alacritty `start_new_session=True`.

The shipped `_builtin_default()` is generic (single status window +
auto-detected projects). User-defined layouts live in
`~/.empirica/cockpit/config.yaml` — not bundled with the package.

### Added — `goals-list --status` filter + drift detection

- **`--status {planned|in_progress|completed|all|drift}`** flag
  filters by lifecycle stage. Takes precedence over `--completed`.
- **`drift` mode** surfaces rows where the `status` text and
  `is_completed` BOOLEAN disagree (a class of historical data drift
  that was silently excluded by the old default filter).
- **Default open count** now uses `is_completed = 0` as the canonical
  predicate (was `is_completed = 0 AND status != 'completed'` which
  silently dropped drift rows). Matches the statusline.
- **`drift_count` + `drift_hint`** appear in result metadata when
  drift exists, pointing at `--status drift`.
- **Bug fix:** `_handle_goals_list_command_helper` resolved
  `project_id` from session/context but didn't return it — caller's
  variable stayed `None` and the SQL filter never applied. Helper
  now returns the resolved id; caller assigns it back. Commands
  without explicit `--project-id` now correctly project-scope.

### Fixed — Python 3.10 compat (`datetime.UTC` → `timezone.utc`)

20 files across `core/`, `core/cockpit/`, `core/identity/`,
`core/persona/`, `core/notify/`, `metrics/` used
`from datetime import UTC` — a Python 3.11+ feature, while
`pyproject.toml` declares `requires-python = ">=3.10"`. Mechanical
sweep replaces with `from datetime import datetime, timezone` and
`tz=timezone.utc` (runtime-equivalent, 3.10-compatible).

### Fixed — Sentinel allowlist + signature cleanup

- `empirica compact-analysis` and `empirica commit-context` added
  to Tier 1 (read-only). Both were misclassified as praxic and
  required a CHECK gate.
- `launch_cockpit()` dropped unused `attach` parameter (no caller
  passed it).
- `annotate_loops_with_last_notify()` dropped unused
  `instance_label` parameter (caller updated).
- `sentinel-gate.py` removed redundant `if True:` wrapper.

### Internal — empirica-constitution skill

Added "About a past commit → `empirica commit-context <sha>
[--depth N]`" branch to the "I don't know something" decision tree.

## [1.8.19] — 2026-05-03

### Changed — `textual` is now an optional `[tui]` extra (headless mode)

- **Headless installs work out of the box** — `pip install empirica`
  no longer pulls `textual`. CI/CD environments, containers, and
  agent-driven consumers don't pay for the TUI dependency they don't
  use. `empirica status --json --all`, scanner verbs, and all CLI
  primitives function without it.
- **TUI install:** `pip install "empirica[tui]"` adds `textual` for
  `empirica tui` (interactive cockpit) and `empirica chat`. Both
  commands print a clear install instruction when the dep is missing
  instead of crashing with a raw ImportError.
- **INSTALL.md** documents the two installation modes.
- **Why:** Several open goals (cockpit headless mode, agent-driven
  status consumption, CI compliance gates) all wanted the same thing:
  decouple measurement from rendering. Optional dep is the cleanest
  cut.

### Added — Cockpit launcher v1 (`empirica cockpit launch/status/detach/kill`)

Per `docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md`. Single command brings up
the canonical multi-Claude tmux layout (one window per project) with
abnormal-exit detection. Layout-only — Claude conversations regenerate
on each launch by design (that's `/compact` + Empirica artifacts'
job).

- **`empirica cockpit launch`** — idempotent: attaches if a session
  with the configured name already exists; otherwise creates the
  layout from `~/.empirica/cockpit/config.yaml`. Auto-generates a
  default config on first run (detects projects under
  `~/empirical-ai/` with a `.empirica/` folder). Hands off to
  `tmux attach-session` unless `--no-attach`.
- **`empirica cockpit status`** — read-only state snapshot. Reports
  session liveness, last clean shutdown, abnormal-exit state,
  configured project list. JSON or human output.
- **`empirica cockpit detach`** — writes the clean-shutdown marker +
  best-effort `tmux detach-client`. Useful as a hotkey wrapper.
- **`empirica cockpit kill [--prune]`** — destroys the tmux session
  and writes the clean-shutdown marker. With `--prune`, also removes
  dead per-instance state files (sister to `empirica instance prune`).
- **Abnormal-exit detection** — compares mtime of
  `~/.empirica/cockpit/last_session_start` vs `last_clean_shutdown` +
  checks the active.lock PID. Three outcomes: clean (start ≤ clean),
  already-running (lock PID alive), or abnormal (lock PID dead /
  missing). Likely cause inferred from `/proc/uptime` (reboot
  vs unknown — richer detection in v1.1).
- **State files** under `~/.empirica/cockpit/`: `config.yaml`,
  `last_session_start`, `last_clean_shutdown`, `active.lock`. Layout
  + state are deliberately separate.

Deferred to v1.1 (planned, follow-on): `cockpit save / restore`
snapshot support, ENP watcher heartbeat integration, interactive
first-launch config confirmation prompt.

18 new tests cover state-file mtime semantics, abnormal-exit decision
tree, config parsing, project detection, no-tmux paths.

### Added — Scanner Phase 3 cron-loop skill (closes the loop end-to-end)

- **New skill `/services-audit-cron`** — canonical biweekly cron loop
  template that wires `empirica services-audit` into Claude Code's
  `/loop` cron mode. Cadence `0 6 1,15 * *` (1st and 15th at 06:00 UTC),
  body invokes `services-audit` and feeds `.result` into
  `loop heartbeat --result` for the schedule signal. Mirrors the
  generic `/loop-cron` template; specialised for the scanner.
- **Cross-references** — `services-auditor` skill points at the cron
  sister; security-corpus README documents the scheduled-audit path.
- **What this completes:** 1.8.18 shipped `services-audit` (the body)
  and the loop registry (the schedule); this release wires them
  together with a documented template so operators register once and
  forget.

## [1.8.18] — 2026-05-02

### Added — `empirica chat` (Phase 1 demo mode)

- **New verb `empirica chat`** — single-instance focused workspace
  scaffolding (sister to the multi-instance cockpit). Per
  `docs/specs/CHAT.md`. Phase 1 ships the Textual app + session module +
  feed-replay demo mode (`--feed PATH --feed-delay SECONDS`) so the
  shape can be experienced without an app-server. Resume via
  `--session-id`. Bookkeeping for follow-on phases lives in
  `empirica/core/chat/`.

### Added — Security corpus populated (was Phase 1 stubs)

- **5 corpus files now ship with summary-grade canonical bodies**
  instead of section-ID-only stubs. Coverage: OWASP LLM Top 10 (2025),
  OWASP Agentic Top 10 (Dec 2025), NIST AI RMF 1.0, MITRE ATLAS,
  Google SAIF. Total corpus 737 lines (was 252).
- **Each section** has: canonical-source attribution, scanner relevance
  (which collector surfaces evidence for it), and concrete mitigations
  the auditor can recommend. The `services-auditor` skill's citations
  now resolve to real text instead of `_stub._` placeholders.
- **Section IDs unchanged** — every previously-citable anchor (LLM-A01
  through LLM-A10, Agentic-A01 through Agentic-A10, GOVERN-1.5,
  MEASURE-2.7, T1499, SAIF-2, etc.) still resolves. Future Phase 3
  corpus-refresh runs replace bodies in place; citations don't break.
- **README refresh policy** updated — corpus is no longer marked
  "stub" by default; per-file `Status:` front-matter declares freshness.

### Added — Scanner Phase 3 audit fire (services-audit + ntfy)

- **`empirica services-audit`** — one fire of the biweekly audit loop.
  Captures a fresh `scan --save`, locates the previous entry in
  `scan_history_<project_id>.jsonl`, diffs current vs prior, and emits
  a notification through `empirica.core.notify.dispatcher` when novel
  running services are detected. Returns structured JSON with a
  `result` field (`found` / `empty` / `fail`) shaped to feed straight
  into `loop heartbeat --result`.
- **Notification body** lists added processes (first 3 by name) and
  added listening port count, severity `warning`, source
  `loop:services-audit`, tags `[services-audit, security]`. Stdout
  fallback when ntfy isn't configured (existing dispatcher behavior).
- **`--no-notify`** flag suppresses dispatch for testing / dry-run.
- **Loop wiring** is the standard cron template — register a loop
  named `services-audit` with the canonical biweekly cron
  (`0 6 1,15 * *`), have the body call `empirica services-audit`,
  feed `.result` from the JSON into `loop heartbeat --result`. The
  command is the body; the loop registry is the schedule.
- 5 tests cover: first-run-no-prior, no-change, novelty triggers
  notify, --no-notify suppresses dispatch, no-project-id returns
  fail.

### Added — Scanner Phase 3 history verbs

- **`empirica scan-history`** — list past scan snapshots for the project
  (newest first, `--limit N`, `--output json|human`). Reads
  `~/.empirica/scan_history_<project_id>.jsonl`, the audit trail every
  `--save` run appends to. Surfaces scan_id, timestamp, host, process
  coverage ratio, error count.
- **`empirica scan-show <scan_id>`** — re-render a saved snapshot from
  `~/.empirica/scans/<id>.json`. Accepts UUID prefix (≥8 chars) so
  operators don't have to paste full IDs. Markdown by default, JSON
  for piping.
- **`empirica scan-diff <a> <b>`** — compare two saved snapshots.
  Reports added/removed processes (by name), per-name count changes,
  added/removed listening ports, and coverage delta. Both args accept
  UUID prefixes.
- These three verbs are the foundation for Phase 3's biweekly cron
  loop and ntfy integration — the cron fires `scan --save`, then
  optionally compares the new history tail against the previous run
  via `scan-diff` and pings on novel additions.

### Added — Secret-scan compliance check (trufflehog)

- **New `secret_scan` check** in `compliance-report --security` —
  invokes `trufflehog filesystem . --json --no-update` and produces a
  tier-aware result: `findings_verified` (active credentials confirmed
  by the issuing service's verifier — hard fail) vs
  `findings_unverified` (regex/pattern-only matches — advisory). Only
  verified findings fail the check; unverified are surfaced for review
  but don't break the gate, since pattern-only matches against AI-key
  detectors have a real FP rate when the verifier can't reach the
  issuing service.
- **Detector breakdown** in the verified case — per-detector counts
  (e.g. `{"Anthropic": 1, "OpenAI": 2}`) so the human summary makes
  the leak class legible at a glance.
- **Tool selection rationale** logged as a decision: trufflehog over
  gitleaks because the goal's primary criterion is AI-relevant
  credential detection (OpenAI/Anthropic/Cohere/HuggingFace) — these
  are first-class detectors with verifiers in trufflehog and only
  community-rule additions in gitleaks. AGPL-3.0 license is fine for
  separate-process invocation. Both tools require external install
  (neither is shipped); when the binary is missing, the check returns
  `status: unavailable` instead of crashing the report.
- Mapped to EU AI Act Art. 15(4), ISO/IEC 42001:2023 A.7.5, GDPR Art. 32.

### Added — Visibility tiers Phase 0 (PROPOSAL_VISIBILITY_TIERS.md)

- **`visibility` field on every artifact** — new TEXT column on
  `project_findings`, `project_unknowns`, `project_dead_ends`,
  `mistakes_made`, `assumptions`, `decisions`, and `goals`, with three
  tiers (`public` / `shared` / `local`) and `shared` as the safe-default
  on existing rows. Migration 039 lands the column idempotently via
  `add_column_if_missing`. Phase 0 is metadata-only — Phase 1 will
  add git-crypt encryption for the `shared` tier.
- **`--visibility` CLI flag** on `finding-log`, `deadend-log`,
  `mistake-log`, `assumption-log`, `decision-log`, plus per-node
  `visibility` in the `log-artifacts` batch. Validation lives in
  `empirica/data/visibility.py` with a safe-invariant normalizer
  (unknown/None tier → `shared`, never silently `public`).
- **`empirica visibility list`** — totals + per-type breakdown +
  recent items per tier across all artifact tables.
- **`empirica visibility show <prefix>`** — single-artifact tier
  lookup by UUID prefix across all 7 tables.

### Added — POSTFLIGHT coverage block (Phase 2 T3, paper section 4.1)

- **`coverage` is now a first-class POSTFLIGHT field** — every empirica
  transaction can opt in to reporting agent self-coverage (file /
  artifact / citation / subagent / tool dimensions per
  `docs/research/COVERAGE_VECTORS_PAPER_OUTLINE.md` section 4.1).
  Validated by `PostflightInput.coverage` in `cli/validation.py` —
  documented dimensions plus free-form keys for forward compatibility.
- **Persisted** in the POSTFLIGHT reflex's `reflex_data` JSON alongside
  `retrospective` — zero schema migration, rides in the existing
  checkpoint metadata column.
- **Echoed back** in the postflight response as `result["coverage"]`
  so the AI sees its own claimed coverage immediately and the next
  PREFLIGHT can surface it via `previous_transaction_feedback`.
- **Informative, not gating.** A 95% confidence claim with 7% file
  coverage is honest when surfaced — no empirica command fails on low
  coverage; no command fails if coverage is absent (full backward
  compatibility). The metric is a self-correction signal, not a
  threshold.
- **Generalizes beyond the scanner.** The services-auditor skill was
  the first concrete user (T1), but the coverage block now travels
  with every postflight — paper validation work and any other
  domain-specific use case can carry it without further plumbing.
- **8 new tests** in `tests/test_postflight_coverage.py` covering
  validation shape, parser tuple, response echo, falsy-omission, and
  free-form key forward-compat. 75 workflow/validation/postflight
  tests still green.

### Added — AI service scanner Phase 2 T2 (cockpit panel)

- **`empirica/core/cockpit/services_view.py`** — mirrors
  `compliance_view.py`. `last_scan_path()` + `read_services_summary()`
  read `~/.empirica/last_scan_<project_id>.json` (the file
  `empirica scan --save` and `--explain` already write) and produce a
  render-friendly dict per project.
- **`aggregate_instance_state` services block** — every instance row
  in cockpit aggregation now carries a `services` key parallel to
  `compliance`. Multiple instances of the same project share the same
  scanner state, so the per-instance embedding is cheap and keeps the
  cockpit self-contained.
- **TUI `#services` panel under `#compliance`** — collapsed view
  (one-line: glyph + processes + listening + integrity % + age),
  expanded view (per-category breakdown of MCP servers, plugin
  manifests, cron entries, env-var name count, host).
- **Key `i` (scanner Inventory)** toggles panel expansion. `s` stays
  bound to Stop and `c` to Compliance; `i` was the next sensible
  mnemonic for the new panel.
- **Glyphs:** 🔍 ✓ clean+fresh, 🔍 ⚠ stale (>24h), 🔍 ✗ collector errors.
- **10 new tests** in `tests/test_services_view.py` covering path
  resolution, missing files, full shape, stale-window logic, missing
  keys, corrupt JSON. Full cockpit suite (191 tests) still green.

### Added — AI service scanner Phase 2 T1 (auditor hand-off)

- **`services-auditor` skill** at
  `empirica/plugins/claude-code-integration/skills/services-auditor/SKILL.md`
  walks the AI through PREFLIGHT (`work_type=audit`), reading the
  saved scanner snapshot + the bundled security corpus, two-tier
  judgment (cheap AI-touching pre-filter then full taxonomy with
  corpus citation), and the confidence×citation ladder
  (≥0.95 + cited → finding, 0.6–0.95 + cited → assumption, <0.6 OR
  uncited → unknown). Citation discipline is load-bearing: every
  finding/assumption MUST cite a corpus section ID, and uncited
  artifacts downgrade to `unknown` regardless of model confidence.
- **`empirica scan --explain`** auto-saves the snapshot and emits a
  hand-off pointing the AI at the auditor skill (works in both
  Markdown and JSON output formats so loops and other automation can
  dispatch the auditor programmatically).
- **Coverage tracking surfaced in the auditor's POSTFLIGHT summary** —
  process coverage (judged / AI-touching), citation coverage (sections
  cited / available), listener coverage (judged / total). This is the
  agent self-coverage metric the paper defines, surfaced at audit time;
  Phase 2 T3 will land a generalized POSTFLIGHT `coverage` block per
  paper section 4.1.

### Added — AI service scanner Phase 1 (PROPOSAL_AI_SERVICE_SCANNER.md / SERVICES_SCANNER.md)

- **`empirica scan` CLI verb** — one-shot deterministic inventory of
  AI-touching services running on the dev machine. Markdown by default,
  JSON on `--output json`, optional `--save` persists to
  `~/.empirica/scans/<scan_id>.json` with a `last_scan_<project_id>.json`
  cockpit hook and an append-only `scan_history_<project_id>.jsonl`
  audit trail. Read-only by design — no `kill`/`stop` verbs.
- **Scanner module** at `empirica/core/scanner/` — six collectors
  (processes via `psutil.process_iter`, network via `psutil.net_connections`,
  scheduled tasks via `crontab`/`~/.config/systemd/user`/`~/Library/LaunchAgents`,
  env-var **names only** via substring match against AI/secret patterns,
  plugin manifests via `~/.claude/plugins/**/plugin.json`, MCP servers
  via `~/.claude/mcp.json`) plus a snapshot orchestrator that captures
  collector errors instead of propagating them and tags the scanner's
  own process with `is_scanner_self: true`.
- **Read-surface YAML** under `cockpit.scanner.read_surface` in
  `.empirica/project.yaml` — declares per-collector field allow-lists.
  Universe-intersected so a typo can never widen the surface; sensible
  defaults applied when the block is absent.
- **Coverage block in every snapshot** — per-collector
  `attempted/succeeded/ratio` for scanner integrity coverage, plus
  `relevant_globs_for_coverage` file-match counts as the substrate for
  the Phase 2 agent self-coverage metric (the trust-grounding "what
  fraction of the relevant material did the AI actually inspect?"
  signal).
- **Bundled security corpus** — five stub markdown files at
  `empirica/data/security-corpus/` (OWASP LLM Top 10, OWASP Agentic
  Top 10, NIST AI RMF, MITRE ATLAS, Google SAIF) carrying canonical
  URLs and stable section IDs. Phase 1 ships the structure; Phase 2
  populates content via the auditor agent and Phase 3 refresh loop.
- **psutil promoted to a required dep** (was previously optional with
  `try: import psutil` fallbacks in two callers — the scanner makes
  it load-bearing).

### Added — Event-listener subsystem (PROPOSAL_EVENT_LISTENER.md, items 1–4 shipped)

- **`empirica listener` CLI + registry** — sister concept to
  `empirica loop` but for event-driven background work (held HTTP
  connection via ntfy/SSE → Monitor wake), not cron-periodic. Eight
  verbs: `register / unregister / pause / resume / record-wake /
  fire / install-request / list / status`. State files mirror the
  loop pattern: `~/.empirica/listeners_<instance>.json` (registry),
  `listener_paused_<instance>_<name>` (pause sidecar),
  `listener_active_<instance>_<name>.json` (runtime metadata
  managed by the listener body — Monitor task id, curl PID,
  armed_at). Topic URL scheme validation (V1: `ntfy:*`; future:
  `sse / websocket / gmail / whatsapp`).
- **`inbox-listener` skill** — prompt template the listener body
  replays on each wake. Walks the owning Claude through arming
  curl + Monitor with `persistent: true`, writing the runtime
  metadata, the pause-check backstop, and `record-wake` after
  each event. At
  `empirica/plugins/claude-code-integration/skills/inbox-listener/SKILL.md`.
- **Listener install/uninstall request bridge** — symmetric to the
  loop variant. `empirica listener install-request --instance ID`
  registers + queues a pending install request; new
  `listener-install-pickup.py` UserPromptSubmit hook surfaces it
  as a system-reminder on the owning instance's next prompt asking
  Claude to invoke `/inbox-listener` and arm. `empirica listener
  pause` reads `listener_active_*.json`, writes a pending uninstall
  with `monitor_task_id` + `curl_pid`; `listener-uninstall-pickup.py`
  surfaces it asking Claude to TaskStop the Monitor + drop the held
  curl. Body-pause-check at next wake is the backstop if Claude
  doesn't run TaskStop in time.

### Added — Pause-actually-cancels-cron (mechanical pause)

- **`loop_uninstall_request` module + `loop-uninstall-pickup.py`
  hook** — symmetric inverse of the install-request flow. When
  `empirica loop pause` runs against a loop with `scheduler_kind=
  cron-create` and a recorded `next_scheduled_job_id`, the pause
  handler now writes
  `~/.empirica/loop_uninstall_pending_<instance>_<name>.json`
  containing the job_id. The new UserPromptSubmit hook surfaces it
  as a system-reminder asking the owning Claude to call
  `CronDelete(<job_id>)`. The loop is then genuinely off — no
  more silent fires firing every interval until the body's pause
  check catches them. Closes the token-bleed pause bug David hit
  in production for outreach-inbox-poll.

### Added — TUI L/E binary toggle (cockpit Phase 1+2)

- **L button mechanical kill** — `cockpit_app.py:action_toggle_loops`
  now calls `handle_loop_pause_command` / `handle_loop_resume_command`
  instead of `set_loop_paused()` directly, so the TUI goes through
  the same pause-cancels-cron flow as the CLI. Live test that flagged
  the bug: clicking L on outreach instance set the sidecar but no
  `loop_uninstall_pending_*.json` was written.
- **E binding for listeners** — symmetric to L. Calls
  `handle_listener_pause_command` / `handle_listener_resume_command`
  for mechanical Monitor-kill. New `E` column added to the instance
  table (now 7 columns: `s name ph S L E N`). Action bar gets a
  matching `E listen` button.
- **Listeners surface in `aggregate_all`** — `instance_state.py` reads
  `listeners_<id>.json`, emits a `listeners` dict per instance plus
  `listeners_registered` / `listeners_paused` in the summary block.
  Powers the new TUI column and `empirica status --json`.
- **L/E click installs from `project.yaml` when registry empty** —
  new `cockpit:` block in `.empirica/project.yaml` defines
  `cockpit.loops` and `cockpit.listeners` lists. First click on an
  empty registry reads the canonical config and queues install via
  the install-request handlers — registration is one-time. New
  reader at `empirica/core/cockpit/project_cockpit_config.py`.

### Fixed

- **memory_manager.py CWD-fallback hygiene** — closes the third leg of
  the #95 root-cause cluster (alongside the resolver-raise and
  grounded-verify CWD fixes already shipped in 1.8.16). `get_memory_dir`
  no longer probes `Path.cwd()` as a candidate; explicit `project_path`
  + git root remain. `resolve_project_id` and `fetch_ranked_artifacts`
  now use the canonical `get_session_db_path()` resolver instead of
  guessing the sessions.db location from CWD, which could route memory
  writes to a sibling project's DB under tmux/cwd-mismatch conditions.
  25/25 memory_manager tests still pass.
- **Cockpit `c` toggle now reveals the passing checks instead of the
  failing ones** — David's intent was always: failures are part of the
  panel header (operator can never hide them via a key) and `c` flips
  a list of *passing* check names so the operator can audit what
  actually ran clean. Prior shape made `c` flip per-failure rows that
  duplicated the header. Compliance summary now exposes
  `passed_check_names` alongside `failed_checks`; `_format_compliance`
  uses it for the toggle-controlled detail block.
- **Recent-activity liveness fallback no longer overrides a definitive
  tmux negative** — `is_alive` previously fell through to "if file
  mtime is < 1h, treat as alive" even when tmux had already reported
  the pane as bash-foreground or gone. A housekeeping sweep touching a
  stale `active_transaction_*.json` was enough to keep a dead instance
  glowing in the cockpit (the tmux_3 ghost). The fallback is now gated
  on `pane_state not in ('bash', 'absent')`, so fresh non-tmux sessions
  and tmux-unqueryable cases still benefit, while definitive tmux
  negatives stay definitive.
- **Cockpit state symbol now reflects liveness, not just transaction
  phase** — alive Claude instances between transactions used to render
  as ⊘ closed (visually reads as 'dead') because `_derive_state_symbol`
  keyed only on transaction phase. They now render as 🟡 idle (alive,
  ready). The phase column still carries the open/closed bit. ⊘ is
  preserved for `--include-dead` diagnostic mode (cleanly closed dead
  instances) to keep it distinct from ⊗ no-claude (abandoned).
- **Cockpit discovers tmux panes that predate empirica install** —
  `discover_instances` now unions any tmux pane currently running
  claude as foreground into the discovery set. Sessions started before
  empirica was installed never wrote `instance_projects/{id}.json`, so
  they were invisible to the cockpit; now they surface as synthetic
  `tmux_{N}` rows with no project_path. Issue surfaced from Philipp's
  GitHub feedback alongside the #98 PID-liveness fix.
- **Chocolatey REST API push (#97)** — `release.py:
  build_and_push_chocolatey` swapped `choco push` subprocess for a
  direct PUT to `push.chocolatey.org/api/v2/package/` via `requests`
  with `X-NuGet-ApiKey` header. The CLI returns 400; the REST
  endpoint kars85 used manually for the 1.8.14 push works. `choco
  pack` is unchanged. Dry-run logs the intended PUT.
- **`sync_readme_whats_new` regex robustness** — old pattern
  `(?:- \*\*[^\n]+\n)+` only matched contiguous `- **` bullet
  lines, so the moment a bullet wrapped onto a continuation line
  the match collapsed to a single bullet. Combined with
  `re.sub`-replaces-all, this could mangle older `What's New`
  sections. New: full-section match with lookahead delimiter
  (`\n## | \n### | \n---\n`) and `count=1` so only the latest
  section gets replaced; older history survives.
- **`update_version_strings` covers `**Version:** X.Y.Z`** — the
  bare `^Version:` regex didn't match the bold-markdown form in
  the README footer, leaving line 433 stuck on `1.8.14` after the
  1.8.15/1.8.16 sweeps. Added a sister entry mirroring the
  MCP_SERVER_REFERENCE pattern.
- **README cleanup** — removed two duplicate "## What's New in
  1.8.14" sections that accumulated from earlier sync runs failing
  on multi-line bullets. Consolidated to a single 1.8.14 section;
  1.8.15 and 1.8.16 history preserved. Footer bumped 1.8.14 →
  1.8.16.

### Fixed — Pre-release audit closures

- **`_require_instance_id` raises `InstanceIdRequiredError(ValueError)`**
  instead of `SystemExit`. Same hazard pattern that motivated the
  1.8.16 `resolve_project_id` migration: BaseException-walks-through-
  except-Exception. Group dispatchers catch and surface as exit 2.
- **`LoopRegistry.unregister()` and `ListenerRegistry.unregister()`
  clean pending install/uninstall sidecar files** — closes the
  orphan-arming gap where unregister could leave a pending file
  that re-arms the loop/listener on the next prompt despite no
  registry entry.

### Sentinel firewall

- **`empirica listener ` added to Tier 1 control-plane whitelist** —
  parallels `empirica loop ` and `empirica sentinel `. State-changing
  but instance-local; allowed in any phase.

### Hooks (new)

- `loop-install-pickup.py` (1.8.16, listed here for completeness)
- `loop-uninstall-pickup.py` (new this release)
- `listener-install-pickup.py` (new this release)
- `listener-uninstall-pickup.py` (new this release)

All four follow the same pattern: cockpit writes a pending file →
hook surfaces a system-reminder via `additionalContext` on the
owning instance's next prompt → Claude executes the privileged
tool (CronCreate / CronDelete / Monitor / TaskStop) from inside
that CC session.

## [1.8.16] - 2026-04-29

### Fixed (#95 follow-up — root-cause closure)

- **Cortex sync reads project_id from session row** — `_cortex_resolve_project_id`
  no longer reads `Path.cwd()/.empirica/project.yaml`. By the time
  POSTFLIGHT runs, the session row already has a canonical `project_id`
  (T5's pre-validation guarantees it). Eliminates the multi-`.empirica`
  CWD-misroute pattern AND removes the `resolve_project_id` →
  `sys.exit(1)` propagation path. Failure mode is now structurally
  impossible, not just caught.
- **`_cortex_read_calibration_summary` accepts `project_path`** —
  caller passes `resolved_project_path` from the open transaction.
  Falls back to `Path.cwd()` only when unset.
- **`_run_grounded_verification` accepts `project_path`** — drops two
  CWD-fallbacks (workflow_commands.py:2566 EvidenceProfile and 2584
  proj_yaml). Same shape as the cortex fix; both surfaced by the T8
  audit.
- **`_soft_run` catches `SystemExit`** (defense-in-depth). Other
  library helpers may follow the same `sys.exit-on-miss` pattern.
  KeyboardInterrupt still propagates — user signals aren't swallowed.
- **`resolve_project_id` raises `ProjectNotFoundError` instead of
  `sys.exit(1)`** (cli/utils/project_resolver.py). Library functions
  raise; CLIs call sys.exit. Closes the SystemExit-walks-through-
  Exception hazard at the source. ~10 callers' existing
  `except Exception` paths catch the new exception cleanly via
  `handle_cli_error`. Same UX, no kernel-style propagation.

### Documentation

- **KNOWN_ISSUES 11.29 + 11.30** — entries for the subagent CLI bleed
  fix (T4 shipped in 1.8.15) and the SystemExit-from-library
  propagation chain (T5/T7/T8 shipped across 1.8.15 + 1.8.16).
  Completes the instance_isolation audit trail.
- **`docs/architecture/README.md` index refresh** — adds 6 missing
  entries (COCKPIT, DISPATCH_BUS, EPP_ARCHITECTURE, MEMORY_ARCHITECTURE,
  NOETIC_BATCH_SPEC, NOTIFY). Version + Updated bumped. KNOWN_ISSUES
  range claim corrected from 11.1-11.20 to 11.1-11.30.
- **`SUBAGENT_EPISTEMIC_ASSESSMENT.md`** — new "Subagent CLI
  Resolution (v1.8.15+)" subsection documenting the active_work file
  mechanism. Corrected stale "kernel limitation" claim about
  process-level session_id sharing — hooks DO see distinct
  claude_session_ids per subagent.
- **Version label sweep** — bumps 1.6.6/1.8.14 → 1.8.16 across
  docs/README.md, docs/architecture/README.md,
  docs/architecture/MULTI_PROJECT_STORAGE.md,
  docs/human/developers/MCP_SERVER_REFERENCE.md,
  docs/human/developers/system-prompts/CLAUDE.md,
  docs/human/end-users/02_INSTALLATION.md (pip + docker tags),
  README.md (docker tags + What's New).
- **NOETIC_BATCH_SPEC.md** — converted speculative "ship in v1.8.14
  (or 1.9.0)" planning markers to past tense (it shipped in 1.8.14).

### Tests

- 27 new tests across the cortex/resolver/grounded-verify fixes
  (T7: 19 in test_postflight_pipeline_restructure; T8a: 8 in
  test_project_resolver_raise). Total release-gate suite remains
  green.

## [1.8.15] - 2026-04-29

### Added (Voice integration)
- **`empirica voice list / show / apply`** — load prosodic voice profiles
  for outreach drafting. Profiles distill writing-pattern signals
  (tendencies, anti-patterns, register-per-platform) into a portable
  `.yaml` an AI can adopt before drafting an email, post, or comment.
  Resolution: `{cwd}/.empirica/voice/<name>.yaml` overrides
  `~/.empirica/voice/<name>.yaml`. Voice samples themselves stay in
  Cortex/Qdrant; this CLI is the calling surface.
- **PREFLIGHT `voice_guidance` block** — when `work_type=comms` or the
  new `voice` field/`--voice` flag is set, the response includes a
  voice_guidance block mirroring the `noetic_guidance` pattern:
  numbered tendencies + anti-patterns + register/depth/framing scoped
  to the platform. `work_type=comms` alone surfaces a nudge to name a
  profile (no opinionated default — voice profiles are personal).

### Fixed (#95)
- **Subagent CLI bleed (#95 Issue 1)** — `subagent-start.py` now writes
  `~/.empirica/active_work_<subagent_uuid>.json` with `is_subagent: true`.
  Without this, subagent CLI calls had no resolver hit and fell through
  to TTY-based session resolution → tagged reflexes with the parent's
  `session_id` → tripped the gate at sentinel-gate.py:1788-1843 on the
  next Edit. `sentinel-gate._detect_subagent` updated to flag-based
  detection (with absence-detection fallback for in-flight subagents).
  `subagent-stop.py` cleans up the file at SubagentStop.
- **POSTFLIGHT half-success bug (#95 Issue 3)** — pipeline restructured:
  pre-validation (Stage 0, NEW) resolves session row + verifies
  project_id present BEFORE any state mutation. Failure → early return
  with `{ok: false, persisted: false, loop_state: "open"}`. Stages 3-4
  (close transaction + write reflex) unchanged. Stages 5-7 (bus,
  beliefs, storage, compliance, cortex sync) wrapped in `_soft_run` —
  failures accumulate into `result["warnings"]` without erasing the
  reflex. End state: rejected pre-mutation OR succeeded with optional
  warnings, never half-success.

### Fixed (Session boundary heal)
- **`session.project_id` validate-and-heal at session boundaries** —
  extends KNOWN_ISSUES 11.24's session-existence heal to the
  project_id grain. Catches the ghost-project_id pattern: a session
  row exists but its `project_id` field points at a stale or wrong
  project (cross-project `--resume`, ambiguous folder_name match,
  tmux pane reuse). Heal lives in `post-compact._auto_heal_session`
  (CONTINUE_TRANSACTION + NEW_SESSION_PREFLIGHT branches) and
  `session-init._heal_session_project_id_at_init`. Workspace.db
  trajectory_path is the canonical lookup — never folder_name (no
  11.10/11.27 regression). Cwd reliable at session boundaries only.
  New `SessionsRepository.heal_session_project_id()` returning
  `"healed" | "ok" | "missing"`.

### Tests
- 50 new tests across 5 commits (T1: 5, T2: 16, T3: 11, T4: 7, T5: 11).
  All green. Total release-gate suite: 1263 passed, 16 skipped.

## [1.8.14] - 2026-04-28

### Added (Cockpit→Claude loop install path)
- **`empirica loop install-request --instance ID --name X --interval Y
  --description Z`** — new verb. Registers the loop in the target
  instance's `loops_{instance_id}.json` (so it's visible in the cockpit
  immediately) and writes a pending install file at
  `~/.empirica/loop_install_pending_{instance_id}_{name}.json` with
  the `loop-cron` skill template substituted with the loop's
  name + interval + description. Idempotent — re-issuing overwrites the
  pending file with the latest values.
- **`UserPromptSubmit` hook `loop-install-pickup.py`** — reads
  pending install requests for the running instance, surfaces them as
  `hookSpecificOutput.additionalContext` (a system-reminder block) on
  the next prompt, and removes the file so the request fires once. The
  Claude reading the system-reminder runs `/loop` with the embedded
  prompt; CC's `/loop` skill calls `CronCreate` from inside that
  session. Closes the cockpit→Claude loop: the cockpit *prompts*
  Claude to install the cron without needing direct `CronCreate`
  access. Hook is wired into `setup-claude-code` alongside the
  existing UserPromptSubmit hooks. Tests in
  `test_loop_install_request.py` cover write/consume/round-trip,
  malformed-file tolerance, sanitization, and idempotency.

### Added (Loop self-scheduling — body owns the schedule)
- **`empirica loop schedule-next NAME`** — new verb. Computes the
  next-fire timestamp from current backoff state and returns
  `{next_fire_at, interval_seconds, current_streak, reason,
  cron_one_shot}`. The `cron_one_shot` is a 5-field UTC cron expression
  pinned to that exact wall-clock minute (DOW wildcarded). The body
  uses this to install the next one-shot scheduler job after each fire.
  Streak 0 → base interval. Empty streak → base × 2^N capped at
  `--max-interval` (default 4h). `found`/`fail` snap back to base.
- **`empirica loop fire NAME`** — new verb. Manually trigger one fire
  of the loop body. Useful for bootstrap after `loop resume` on Claude
  Code (where the empirica CLI can't call CronCreate directly), for
  testing, and for bypassing backoff without using `poke`. For
  `cron-create` scheduler kind, surfaces the cron expression + a hint
  to re-issue via `/loop` or run the printed `CronCreate(...)` call.
- **`empirica loop heartbeat --next-scheduled-job-id JOB_ID
  --scheduler-kind cron-create|systemd-user|...`** — new flags.
  Records the scheduler's opaque job id so `pause` can cancel the
  future fire, and which scheduler installed it so cancellation logic
  can route to the right backend.
- **`paused` heartbeat result** — fourth value alongside
  `found`/`empty`/`fail`. When the body short-circuits on the pause
  check, it heartbeats with `result=paused` and the streak math
  freezes (no advance, no reset). Pause is a no-state transition.
- **Registry adds `scheduling` block** with `scheduler_kind`,
  `next_scheduled_job_id`, `next_fire_at`. Legacy entries without
  this field load cleanly with a default empty SchedulingState.
- **`empirica loop pause`** now clears `next_scheduled_job_id` from
  the registry and surfaces the cancellation hint
  (e.g. CronCreate is doc-limited because the empirica CLI can't call
  `CronDelete` — the body's start-of-fire pause check is the backstop;
  loop dies cleanly after at most one more silent fire). `empirica
  loop resume` surfaces a re-bootstrap hint pointing at `loop fire`.
- **Loop-cron skill template** rewritten for self-scheduling: register
  with `--interval` as base cadence, body installs each next fire via
  `schedule-next` + scheduler-specific call, heartbeat returns the
  scheduler-issued `--next-scheduled-job-id`. Pause means the
  scheduler is silent — no token bleed.
- **`COCKPIT.md`** gains a "Loop self-scheduling" section pointing at
  the skill + spec.

Self-scheduling corrected two gaps in the prior backoff design (see
`docs/specs/PROPOSAL_LOOP_BACKOFF.md`): the internal threshold
advanced but the cron tick stayed fixed, so the prompt still arrived
every base interval; pause filtered fires but the scheduler kept
firing. Self-scheduling is the only mode (no recurring fallback —
nothing in production to be backwards-compatible with). 14 new tests
covering plan math, paused-freeze, persistence round-trip, backoff
cap.

### Added (Notify dispatcher — pluggable notification primitive)
- **`empirica notify` CLI subcommand group** with four verbs: `emit`,
  `config`, `backends`, `test`. Single dispatch primitive every loop
  body and hook calls; the dispatcher decides where the event goes based
  on `~/.empirica/notify.yaml` (or built-in defaults — empirica works
  out of the box without external services). Three v1 backends: `stdout`
  (default), `log` (rotating JSONL at `~/.empirica/notify.log`), and
  `ntfy` (JSON publish format with basic/bearer auth via env var).
  First-match-wins routing rules over `severity`, `source` (glob),
  `topic` (glob), and `tag` (glob). Fail-loud fallback to stdout +
  stderr warning when the resolved backend isn't configured — never
  silently drops. Three sharp edges enforced in code: ntfy uses JSON
  publish format only (header-stuffing breaks on emoji), `--actions`
  mirrors ntfy's `Label|URL` format exactly, auth is always via env var
  named in config (the secret never lives in YAML). Sentinel-gate
  whitelists `empirica notify ` as TIER1 (instance-local control plane,
  always allowed). See
  [`docs/architecture/NOTIFY.md`](docs/architecture/NOTIFY.md).

### Added (Cockpit notify-dispatcher wiring)
- **`~/.empirica/notify-dispatcher.jsonl`** — always-on metadata-only
  audit log written by the dispatcher itself (not by any backend) so
  the cockpit can render dispatcher activity faithfully regardless of
  which backend events were routed to. Rotation 10MB / 5 files
  (matching the log backend convention). Schema is metadata only:
  `ts, source, severity, topic, resolved_backend, fell_back,
  fallback_reason, ok, response_code, detail, project_id` — no
  title/message/rationale/tags so the file stays small and notification
  content never leaks into a debug file. No opt-out flag: cockpit
  visibility is a discipline guarantee.
- **`summary.notify_dispatcher` block in cockpit status JSON** —
  `default_backend`, `backends` (with ntfy `auth_method`/`server`/
  `default_topic` when set, secret never), `recent` (5 most recent
  audit rows), `last_failure`, `banner_failure` (failure within last
  hour), `fell_back_count_24h`, `emit_count_24h`. Single source of
  truth via `backends_status_snapshot()` so `empirica notify backends`
  and the cockpit view can never disagree about whether a backend is
  configured.
- **TUI dispatcher widget** (`empirica tui`) — banner above when
  `banner_failure` is active, header line with default backend + 24h
  emit/fallback counts, body with backend status (`●/○` glyphs +
  `bearer`/`basic`/`none` for ntfy) and 5 most recent emits as
  `HH:MM:SS source ↗ backend/topic ok` (`↻` when fell back).
- **Per-loop notify annotation** — each loop's most recent dispatcher
  emit is matched by `source: "loop:{name}"` and surfaced as `↗backend/
  topic` next to the loop row in both the ANSI single-instance render
  and the TUI's loops table.

### Changed (Cockpit notifications — project-scoped)
- **`notifications_for_project(project_path)`** replaces the per-
  instance `notifications_list(instance_id)` placeholder. Reads
  `~/.empirica/enp/pending.json` (the file the ENP watcher actually
  writes), filters by `repo` field matching the instance's
  `project_path` after path normalization, returns the 5 most recent
  unacked entries newest-first. The TUI's notifications strip now
  shows project-scoped items per selected instance instead of an
  always-empty placeholder. Top-bar `⊕N` counter reads
  `summary.open_notifications` which now counts unacked entries
  across all projects via `pending.json`.
- **`clear_notifications(instance_id, project_path=...)`** marks the
  pending entries for the project as `acknowledged` (in-place rewrite
  of `pending.json`). The `n` keybinding clears the selected
  instance's project. Calls to ntfy archive / empirica-extension API
  remain downstream integration work.

### Added (`empirica goals-prune`)
- **Bulk goal cleanup verb** with four modes: `--test-pollution`
  (drop short test-only objectives left by E2E runs, NULL or current
  project_id), `--by-status-planned` (close all planned goals),
  `--auto-stale [days]` (close in_progress goals that haven't been
  touched), `--duplicates [threshold]` (token-overlap dedupe via
  Jaccard; Qdrant similarity wiring deferred). Dry-run by default;
  `--apply` mutates and writes a receipt to git notes (`breadcrumbs`
  ref). Sentinel-gate whitelists `empirica goals-prune` as TIER2.

### Fixed (resolve-artifacts goal path)
- **`resolve-artifacts` for `type: goal` was hitting a non-existent table.**
  The handler queried `UPDATE project_goals SET ... WHERE goal_id = ?`,
  but the actual table is `goals` with PK `id`. Goal resolutions raised
  `no such table: project_goals`, surfacing as `resolved: 0` with the
  error in the response. Caught while dogfooding the CLI to close a
  shipped-but-still-open goal during the end-of-session sweep.
  Fix: query `goals` table, set both `is_completed = 1` and
  `status = 'completed'`, write `completed_timestamp`, store
  `completed_reason` inside the `goal_data` JSON column. Same fix
  applied to the `_ARTIFACT_TABLES` lookup used by `delete-artifacts`.

### Added (Sources discipline — explicit guidance for source-linked artifacts)
- **CHECK gate adds a `sources` praxic reminder** alongside the existing
  `commit`, `artifacts`, and `completion` reminders. Text: "When findings/
  decisions come from external material (docs, URLs, papers, conversations,
  attachments) — log the origin via `source-add` and link with `sourced_from`
  in `log-artifacts`. Especially important on Claude Desktop where artifacts
  often originate outside code that git already tracks." Surfaces alongside
  the calibration_nudge so AIs see all four discipline points at every CHECK
  proceed.
- **`epistemic-transaction` skill SKILL.md** gains a "Sources — log when
  an artifact's origin matters" subsection under Step 4b (Noetic Phase).
  Explains when to use `source-add` (external refs the AI consulted, not
  the project's own code that git already tracks), when to skip it (CLI
  mode where `git blame` covers provenance for free), and shows the batch
  graph pattern for linking findings to sources via `sourced_from` edges.
- **Lean system prompt template** gains a `source-add` row in the
  Collaborative Mode signal table. Pairs "External material cited" with
  `source-add` then `sourced_from` link via `log-artifacts`.

## [1.8.13] - 2026-04-27

### Added (Batch artifact verbs — schema discoverability)
- **`--schema` flag** on `empirica log-artifacts`, `resolve-artifacts`,
  and `delete-artifacts`. Prints the JSON input shape (with valid node
  types and relation names) and exits without touching the DB. AIs who
  hit a validation error can now self-correct via `--schema` instead of
  trial-and-error. Mirrors the pattern from `noetic-batch --schema`.
- **Forgiving alias normalization** on `log-artifacts` input. The
  validator now accepts:
  - `id` and `node_id` as aliases for `ref` on nodes (AIs reach for
    `id` because resolve-artifacts and delete-artifacts both use it)
  - `type` and `kind` as aliases for `relation` on edges (`type` is the
    most common miss because the noun is overloaded)
  Aliases are normalized before validation; success responses surface
  `alias_warnings` so AIs learn the canonical names over time.
- **Improved error message** on validation failure now includes a
  `hint` field pointing at `--schema` and naming the two common
  pitfalls. Was: `{"errors": ["Node 0: missing 'ref'"]}`.
  Now: `{"errors": [...], "hint": "Run --schema for full input shape.
  Common pitfalls: nodes need 'ref' (not 'id'), edges need 'relation'
  (not 'type')."}`.
- **14 new tests** in `tests/test_graph_commands_schema.py` cover the
  three normalization paths (id→ref, node_id→ref, type→relation,
  kind→relation), canonical-wins-over-alias, dedup of alias warnings,
  --schema short-circuit on all three handlers, and the error-message
  hint. 120/120 cockpit + graph-schema tests pass total.

### Fixed (Cockpit v1.6.2 — open-goals count was wrong)
- **Open-goals count now matches statusline (was 996, should be 18)**.
  Two compounding bugs:
  - **(a) Wrong column.** v1.5 introduced `WHERE status != 'complete'` —
    the DB never has 'complete' as a status value (canonical values are
    `completed`, `in_progress`, `planned`, all with `-ed`). The filter
    matched no rows so returned everything (996).
  - **(b) Wrong scope source.** v1.6.1 dropped session_id but didn't
    add project_id. The statusline (which David sees in CC) uses
    `is_completed = 0 AND project_id = ?` — and `project_id` is looked
    up from the `sessions` table via `session_id`, not from the active
    transaction file.
  - Fix: cockpit's `open_goals_list` and `_live_statusline_from_db`
    both now mirror `statusline_empirica.get_open_counts` exactly:
    look up project_id from sessions, filter `is_completed = 0 AND
    project_id = ?`. Verified: live count now reads 19 (matches
    statusline's 18 + the goal logged this transaction).
- **Status taxonomy clarified.** DB uses `completed` / `in_progress` /
  `planned` (no `complete` value, no `open` value). The boolean
  `is_completed` is the canonical source of truth — `status` text can
  drift from it. The cockpit now uses `is_completed` everywhere goals
  are filtered for done-ness.
- **Mistake logged** — my v1.5 finding ("status uses 'complete'") was
  a hallucination. Mistake-log entry captures the failure mode (asserted
  enum values without a `SELECT DISTINCT` check first) and prevention
  (statusline_empirica:79 even has a comment naming `is_completed` as
  the source of truth — should have read it first).
- **Goal logged** — `empirica goals prune` CLI for stale-goal cleanup.
  Three modes proposed: `--auto-stale` (close N+ days no activity),
  `--duplicates` (Qdrant similarity prompt-merge), `--by-status planned`
  (bulk close planned goals that never moved). Dry-run by default.

### Changed (Cockpit v1.6.1 — wordwrap + project-scoped goals + ctx wire)
- **Goals + notifications now wordwrap** instead of truncating. Each item
  wraps to ~36 col lines with continuation indented under the bullet so
  the visual hierarchy is preserved. Hard cap per item at 200 chars
  (David's number) with `…` ellipsis when exceeded.
- **Open goals are project-scoped** (was session-scoped). Was filtering
  on `session_id` which only showed goals created in the current
  Empirica session — most goals from earlier sessions were invisible.
  Dropped the filter; the DB is project-scoped so just `WHERE status !=
  'complete'` is the right query. Same fix in the count surfaced in the
  statusline (`goals:N`).
- **Context window % now wires through to the cockpit** —
  `statusline_empirica.py:format_context_window` was writing to a
  shared `~/.empirica/context_usage.json` only. Now also writes to the
  per-instance `context_usage_{id}.json` that the cockpit reader (and
  context-shift-tracker hook) expect. Legacy shared file kept for
  backwards-compat. After the next CC statusline tick the cockpit
  shows `— ctx:M%` for the selected instance.
- **TUI version bumped to v1.6.1** in module docstring.

### Changed (Cockpit v1.6 — portrait layout + actionable strips)
- **Portrait orientation** — TUI now stacks vertically, target ~36 cols ×
  22 rows so it fits comfortably in a phone terminal in portrait mode
  (or a tmux split-strip). Table column headers shortened (`s/name/ph/S/L/N`).
  Phase compressed to 4-char codes (noet/prax/cls/ask⚠).
- **Statusline reformatted** — was `▌ {name} · know:X · u:Y · N artifacts`,
  now `k:X c:Y conf:Z% goals:N` (with optional `— ctx:M%` when CC has
  written `~/.empirica/context_usage_{id}.json`). Project name dropped
  (already in the row). Confidence is the composite from
  statusline_empirica's formula (0.4·know + 0.3·(1-uncertainty) +
  0.2·context + 0.1·completion). Goal count comes from the goals table
  filtered by `status != 'complete'`.
- **Recent-events strip removed; replaced with two actionable strips:**
  - **Open goals** — top-5 unfinished goals from the project's
    `goals` table for the selected instance's session, marked `⏸` for
    blocked vs `·` for in-progress. Phase events (preflight/check/
    postflight) had no actionable value to the user.
  - **Notifications** — top-5 ENP items from
    `~/.empirica/enp/items_{id}.json` (placeholder, ENP integration
    spec still owned by empirica-extension Claude). Empty state reads
    "(none — ENP integration pending)".
- **`StatuslineSummary` extended** with `context`, `completion`,
  `confidence`, `open_goals` fields. `artifact_count` kept as alias for
  backwards-compat on the rendered cache fallback.
- **New enrichment helpers** in `empirica.core.cockpit.enrichment`:
  `calculate_confidence`, `open_goals_list`, `notifications_list`,
  `context_usage`, plus dataclasses `OpenGoal` and `NotificationItem`.
- **3 new TUI tests** verify the v1.6 statusline format, that open-goals
  widget shows real DB rows (not phase events), and that the `#recent`
  widget no longer exists. Existing structural tests updated for the
  new column names + new widget IDs. 106/106 cockpit tests pass.

### Added (Cockpit v1.5 — loop backoff + statusline live wire)
- **Loop exponential backoff** (per `PROPOSAL_LOOP_BACKOFF.md`) —
  empty fires lengthen the gap, found/fail snap back to base. New
  `register` flags `--backoff none|exponential`, `--base-interval`,
  `--max-interval` (default envelope 15m → 4h). New `heartbeat --result
  found|empty|fail` is the backoff signal (falls back to `empty` when
  `--status ok` and `fail` when `--status fail`). New verbs:
  - `empirica loop should-fire <NAME>` — exit 0 = fire, exit 1 = skip.
    Loop body calls this between the pause check and actual work.
  - `empirica loop poke <NAME>` — manual escape hatch, zeros the
    streak and clears the threshold so the next fire runs at base.
- **`BackoffState` dataclass** in `loop_registry.py` with `policy`,
  `base_interval_seconds`, `max_interval_seconds`, `empty_streak`,
  `next_fire_threshold` (ISO-8601 UTC). `current_interval_seconds()`
  computes `base × 2^streak` capped at `max`.
- **`parse_duration` / `format_duration` helpers** — `15m`, `4h`, `30s`,
  `1d`. Bare integers default to minutes.
- **24 backoff tests** — duration parsing, envelope storage, exponential
  curve (30m → 1h → 2h → 4h cap), found/fail reset, threshold timing,
  `should_fire` gate states, `poke` clear, heartbeat result inference,
  legacy entry deserialization (no `backoff` field).
- **loop-cron skill updated** with the new flags and the `should-fire`
  check between pause and work, plus the `--result found/empty` heartbeat
  pattern.

### Fixed (Cockpit v1.5)
- **TUI statusline strip now shows live data** — previously read from
  `~/.empirica/statusline_cache/{id}_*.json` which can be stale or
  empty. Now reads vectors directly from the project's
  `.empirica/sessions/sessions.db` `epistemic_snapshots` table for the
  selected instance's session, with cache as fallback. `session_id`
  threaded through `aggregate_instance_state` payload. Same path
  resolution applied to `recent_actions` (was hitting an empty stale
  `.empirica/sessions.db`).
- **Recent actions now read** — fixed column name mismatch:
  `epistemic_events` schema has `timestamp` + `data_json` (not
  `event_timestamp` + `event_data` which my v1.4 query assumed). Five
  most recent preflight/check/postflight events now show under the
  selected instance's statusline.

### Removed (Cockpit v1.5)
- **TUI `R rename` action** — no purpose for the phone-glance use case.
  CLI verb `empirica instance label <id> <name>` remains.

### Changed (Cockpit v1.4 — compact mobile TUI)
- **TUI redesigned for phone/split-pane** — drops the right detail pane
  + the dedicated log pane. Single-screen layout: header → 6-col
  instance table (stat / name / phase / S / L / N) → 4 action buttons →
  selected-instance statusline → recent-actions strip → footer. Targets
  ~50 cols × 17 rows so it fits in a phone terminal or a tmux split-strip
  at the bottom of a working pane.
- **Toggle semantics** — `p` toggles Sentinel (was separate pause/resume),
  `l` toggles all loops on/off as a unit (pause-all if any unpaused, else
  resume-all). Cuts the action surface in half for one-key mobile UX.
- **`stop` replaces `kill`** — TUI's destructive action is now `S stop`
  (sends `Escape` via `tmux send-keys` to interrupt the current turn —
  the "remote spacebar"). Recoverable: Claude keeps running, only the
  current generation is interrupted. `kill` remains in the CLI for
  advanced use.
- **`phase = 'ask'`** — surfaced when Claude is waiting for input.
  Reads `~/.empirica/asking_{instance_id}` flag; the hook that writes
  it is a follow-up. Until then the column shows blank rather than
  wrong.
- **`notif` column** — placeholder for ENP→cockpit integration. Reads
  `~/.empirica/enp/open_{id}.json` (count + has_attention). `[N notif]`
  button calls `clear_notifications()` which currently just unlinks the
  file; the goal logged this transaction tracks ntfy + empirica-extension
  propagation.
- **Selected-instance statusline strip** — reads from
  `~/.empirica/statusline_cache/{id}_*.json`, shows label + know +
  uncertainty + artifact count.
- **Last 5 actions strip** — reads from project's
  `.empirica/sessions.db` epistemic_events table (preflight / check /
  postflight). Below the statusline, decorative-friendly (best-effort).
- **New module:** `empirica/core/cockpit/enrichment.py` — readers for
  ask-state, notification counts, statusline cache, recent actions.
- **New `stop_instance(instance_id, key='Escape')`** in
  `empirica/core/cockpit/instance_actions.py`.
- **11 TUI tests** (was 8) — covers compact layout, toggle semantics,
  stop dispatch, ask-phase rendering, notification clear. Asserts
  `btn-kill` is absent. 79/79 cockpit tests pass.

### Added (Cockpit v1.3 — liveness filtering + bulk prune)
- **Liveness detection** — `empirica.core.cockpit.liveness.is_alive()`
  uses `tmux list-panes -a -F '#{pane_id} #{pane_current_command}'` to
  distinguish "Claude is running here" from "the pane exists but it's
  just bash". Plus PID/PPID alive check (`os.kill(pid, 0)`) for non-tmux
  instances and a 1-hour recent-activity fallback for fresh sessions
  before session-init has captured a PID. The current instance is always
  considered alive (it's running this code).
- **Live-only by default** — `empirica status`, `empirica status --all`,
  and `empirica tui` now filter dead instances out by default. Adds
  `--include-dead` flag (CLI) and `D` keybinding (TUI) to toggle the
  diagnostic view that shows everything regardless of liveness.
- **`empirica instance prune`** — bulk forget every instance that fails
  the liveness check. Skips the current instance. `--dry-run` shows what
  would be removed without removing it. Used to clean up the 38 stale
  test/abandoned instances accumulated on this machine.
- **11 new unit tests** — `tests/test_cockpit_liveness.py` covers the
  four signal paths (tmux pane running claude, pane running other,
  pane gone, non-tmux PID resolution).
- **Aggregate API change** — `aggregate_all(include_dead=False)` and
  `aggregate_instance_state(live_panes=None, current_instance_id=None)`.
  Each instance dict now carries `alive: bool` and `liveness_reason: str`
  fields.

### Added (Cockpit v1.2 — interactive TUI)
- **`empirica tui`** — interactive Textual cockpit. Real clickable buttons
  for every action verb: pause/resume Sentinel, pause/resume loops, kill,
  forget, rename, refresh. Mouse + keyboard equivalents (p/P/l/k/f/R/r).
  Modal confirmations for destructive actions (kill, forget) with explicit
  warning when the target is the current instance. Loops opened in a
  picker-modal (DataTable of loops + pause/resume buttons). Auto-refresh
  every 2s, manual refresh on `r`. New module `empirica/cli/tui/`. New
  dep: `textual>=0.50`. 8 new tests (`test_cockpit_tui.py`) covering
  mount, instance load, click-action round-trips, keyboard shortcuts,
  modal cancel-doesn't-act. Skips the bespoke-TUI graduation criterion
  (3 documented gaps) on user say-so — clickable controls IS the gap.

### Added (Cockpit v1.1 — control plane completion)
- **`empirica instance <kill|forget|label>`** — destructive control-plane
  verbs. `kill` does `tmux kill-pane` for tmux instances, falls back to
  SIGTERM (or SIGKILL with `--force`) using the PID captured by
  session-init for non-tmux. `forget` removes every per-instance state file
  under `~/.empirica/`, idempotent — for cleaning up dead/abandoned
  instances. `label` sets/shows/clears the human-readable label override.
  Both `kill` and `forget` refuse to target the current instance unless
  `--yes` is passed.
- **Auto-naming from project basename** — instance labels now default to
  `Path(project_path).name` (matches what the statusline shows) instead of
  the raw `instance_id`. Manual `instance label` override still wins.
- **Footer hints in `--pretty` status output** — both `--all` and
  single-instance views now show a "Controls:" section listing the action
  verbs (`empirica sentinel pause --instance <ID>`, `empirica instance
  kill <ID>`, etc.). Turns the read-only overview into a discoverable
  control plane without a TUI.
- **PID/PPID capture in `instance_projects/{id}.json`** — session-init now
  records both so non-tmux instance kill can find a process to signal.
  PPID (the long-lived Claude Code parent) is preferred over PID (the
  short-lived hook).
- **17 new unit tests** — `tests/test_cockpit_instance_actions.py` covers
  kill resolution (tmux pane vs PPID signal vs unreachable), forget
  cleanup including loop-pause sidecar globbing, and label CRUD.

### Added
- **Empirica Cockpit** — three new CLI surfaces for multi-instance state
  visibility and per-instance controls (per `PROPOSAL_SENTINEL_LOOP_TUI.md`):
  - `empirica sentinel <pause|resume|status>` — wraps the existing
    `~/.empirica/sentinel_paused_{instance_id}` pause-file mechanism the
    Sentinel hook already reads. Per-instance + global scope, optional
    `--reason` text.
  - `empirica loop <register|unregister|pause|resume|set-interval|heartbeat|list|status>`
    — per-instance loop registry stored at `~/.empirica/loops_{instance_id}.json`,
    with atomic writes, idempotent `register`, auto-register-on-heartbeat,
    and pause-via-sidecar-file (`~/.empirica/loop_paused_{id}_{name}`).
  - `empirica status [--all] [--instance ID] [--pretty|--json]` —
    state-file-only instance discovery, transaction phase derived from
    `hook_counters.praxic_tool_calls`, ANSI-aware pretty renderer with
    wide-glyph aware column alignment, JSON output is the source of
    truth all renderers consume. The "TUI" is `watch -n 2 empirica status
    --all --pretty` until that proves insufficient.
- **`loop-cron` skill** — prompt template for wiring CC's built-in `/loop`
  into the registry: register at start (idempotent), check pause flag each
  fire, heartbeat at end. Without this wiring, a `/loop` cron is invisible
  to the cockpit and uncontrollable from any other terminal.
- **`docs/architecture/COCKPIT.md`** — full state-file layout, discovery
  rules, phase-derivation table, JSON schema, and explicit out-of-scope
  list (no ntfy, no learning loop, no goal-progress on status row in v1).
- **40 new unit tests** — `tests/test_cockpit_sentinel_pause.py` (9),
  `tests/test_cockpit_loop_registry.py` (16), `tests/test_cockpit_instance_state.py` (15).

### Fixed
- **Sentinel was gating `empirica noetic-batch`** — the batched noetic
  primitive was forcing CHECK before allowing the CLI form, which defeats
  the entire purpose. Added to `EMPIRICA_TIER1_PREFIXES` along with the
  three new cockpit subcommand groups (`sentinel `, `loop `, `status`).

### Changed
- **`status` is no longer an alias for `system-status`** — the new
  top-level cockpit overview takes that name. `system-status` keeps its
  distinct kernel-style diagnostic role.

## [1.8.12] - 2026-04-25

### Added
- **`empirica security-audit` command** — supply-chain security audit
  cross-referencing pip-audit findings against CISA's Known Exploited
  Vulnerabilities catalog. Phase 1 of a phased rollout (OAuth-supply-chain
  validation per the Vercel/Context.ai breach 2026-04-23). Cached KEV feed
  at `~/.empirica/feeds/cisa_kev.json` (24h TTL, urllib stdlib only,
  stale-cache fallback). Findings classified as `now` (in KEV — actively
  exploited), `month` (CVE without observed exploitation), `monitor`,
  `safe`. Maps to EU AI Act Art. 15(4), ISO 42001 8.4, GDPR Art. 32. New
  modules: `empirica/core/security/{kev_feed,audit,scope}.py`,
  `empirica/cli/command_handlers/security_audit_commands.py`. 38 new tests.
- **Empirica-vs-user scope split in security-audit** — `pip-audit` scans
  the active venv, mixing empirica's deps with user-installed tools. The
  audit now classifies each finding by scope, walking the installed
  metadata graph rooted at `empirica` to determine the managed surface.
  Pass/fail gates only on empirica-scoped KEV matches; user findings are
  reported but informational. Output is a single command with two clearly
  labelled sections.
- **`docs/reference/STATUSLINE_REFERENCE.md`** — first user-facing
  statusline documentation, triggered by a community discussion thread.
  Covers all 4 modes (basic / default / learning / full), every glyph in
  default mode (⚡ ↕ 🎯 ❓ PRE/CHK/POST ⚙/🔍 K:/C: Δ %ctx) with
  thresholds and formulas, color semantics including the Brier-inflation
  threshold color, edge states ([no project] / [project:inactive] /
  OFF-RECORD), extension mechanism, env vars, and a FAQ.

### Fixed
- **TTY staleness false-positive** — `validate_tty_session()` warned
  "TTY session is X hours old — may be stale" whenever the session was
  older than 4 hours, but the timestamp in the TTY session file is the
  WRITE time (from session-init), never refreshed during a session's
  lifetime. Any actively-running session past 4h wall-clock got flagged
  stale despite continuous use. Removed the timestamp check; TTY device
  presence at `/dev/pts/X` is the authoritative signal (and was already
  noted as such in the function's own docstring).
- **lxml CVE-2026-41066** (XXE in default parser config) — pinned
  `lxml>=6.1.0` in `pyproject.toml`. Transitive dep via
  newspaper4k/trafilatura/python-docx; the pin forces the resolver to
  the safe version.
- **python-dotenv CVE-2026-28684** (`set_key`/`unset_key` symlink follow)
  — pinned `python-dotenv>=1.2.2`. Transitive dep via `pydantic-settings`.

### Known
- **pip CVE-2026-3219** — pip 26.0.1 handles concatenated tar+ZIP files
  as ZIP regardless of filename. **No upstream fix yet.** Empirica doesn't
  pin pip itself; will resolve when upstream patches and users run
  `pip install --upgrade pip`. Tracked in security-audit reports as
  `month` priority (CVE without observed exploitation).

### Compliance
- `empirica security-audit` on this repo: 3 → 1 findings after this
  release (lxml + python-dotenv cleared; only the unfixable pip CVE
  remains). Full test suite: 952 passed, 0 failed.

## [1.8.11] - 2026-04-24

### Fixed
- **Session-create race on concurrent Claude Code sessions** (#90, #91 by
  @kars85) — `session-init.py` called `create_session_and_bootstrap()` before
  `_write_instance_projects()`, so the subprocess's `session-create` read stale
  `instance_projects/{instance_id}.json` from a previous session. On Windows
  without tmux, all terminals share `instance_id=win-default`, making the race
  reliably reproducible with two concurrent VS Code windows in different
  projects. `active_session_{instance_id}` ended up stamped with the wrong
  `project_path`, failing epistemic gates with "project not found". Fix extends
  the existing `EMPIRICA_CWD_RELIABLE` env-var contract (already consumed by
  `path_resolver.py` for cross-project bleed detection) to
  `session_resolver.get_active_project_path()` — CWD wins at priority -1 when
  the flag is set and `.empirica/project.yaml` exists in CWD.
- **Pyright type-safety** — 4 errors cleared:
  - `mapper.py:_extract_int` return type (`int | float` → `int` via cast).
  - `sentinel-gate.py:_classify_tool_phase` return type (wrap in `bool()`).
  - `sentinel-gate.py:2524` null-iterable guard — explicit `None` handling for
    `_validate_check_record`'s silent-pass path.
  - `sentinel-gate.py:845` `path_resolver` availability import — pyright-ignore.
- **Stale `__all__` exports** cleared across `empirica/__init__.py`,
  `empirica/config/__init__.py`, `empirica/core/canonical/__init__.py`, and
  `empirica/core/git_ops/__init__.py`. Removed dead symbols (`ReflexLogger`,
  `log_assessment`, `log_assessment_sync`, `SignedGitOperations`); added
  `TYPE_CHECKING` imports for lazy-loaded names so pyright can resolve them;
  converted computed `__all__` in config to a literal list.

### Changed
- **`sentinel-gate.py:main()` refactor** — CC 16 → ≤15 by extracting the full
  authorization pipeline (session resolution through CHECK evaluation) into
  `_run_authorization_pipeline`. `main()` is now a thin 4-step dispatcher
  (parse → firewall → exemptions → pipeline). `db.close()` moved to a
  `finally` block in the helper so all early-exit paths clean up uniformly.
- **Vulture unused-parameter cleanup** — removed 7 dead parameters across
  `workflow_commands`, `sentinel_hooks`, `findings_deprecation`,
  `signed_operations`, `hot_cache`, `validation_utils`; renamed context-manager
  `__exit__` args to `_exc_*` per Python convention.
- **TODO markers removed from code** — 7 incomplete-work markers logged as
  empirica unknowns with file:line pointers and impact ratings (goal_commands
  import flow, findings_deprecation semantic similarity, session_sync diff
  detection, handoff db-mirror, identity_commands signature storage ×2,
  workflow_suggestions qdrant query). Source of truth is the artifact graph,
  not grep-able comments.

### Tests
- Tightened `TestGetActiveProjectPath` fallthrough tests to isolate `HOME` and
  pin an unused `EMPIRICA_INSTANCE_ID`, so `get_instance_id()` can't read the
  developer's real `~/.empirica/instance_projects/` during test runs. Weak
  `result != str(tmp_path) or result is None` assertions replaced with strict
  `result is None`.

### Compliance
- `empirica compliance-report`: 75% (9/12) → 100% (11/11). All deterministic
  quality gates pass: ruff (0 violations), complexity (0 C901), pyright
  (0 errors), security (0 OWASP critical), tech_docs (79.1% coverage),
  release_chain, discipline, ai_transparency, decision_transparency,
  repo_hygiene, epistemic_audit, calibration.

## [1.8.10] - 2026-04-23

### Added
- **Artifact Graph API** — batch artifact operations with connected graphs:
  - `empirica log-artifacts`: batch create artifacts (7 types) with relationship
    edges (9 types). Creates in dependency order, resolves refs to UUIDs.
  - `empirica resolve-artifacts`: batch resolve unknowns, assumptions, goals.
  - `empirica delete-artifacts`: batch delete stale artifacts with audit trail
    and `--dry-run` preview. Anti-pollution for epistemic chains.
- **`empirica release` command** — sentinel-bypassing wrapper for release.py.
- **Sentinel work_type nudge** — prompts when PREFLIGHT has no work_type set.

### Fixed
- **Calibration data leak** — `_internal_*` keys (per-vector gaps, divergences)
  were visible in POSTFLIGHT CLI output. AIs were reading them and optimizing
  against per-vector scores, defeating the calibration reframe. Now stripped
  from AI-facing output; writes to DB/breadcrumbs only.
- **Sentinel INVESTIGATE over-gating** — noetic tools (grep, ls, Read) were
  getting `ask` prompts during INVESTIGATE mode. Now silently allowed.
- **Sentinel MCP tool classification** — `mcp__empirica__*` and Cortex MCP tools
  had no classification entries, falling through to praxic default. Added
  `EMPIRICA_MCP_PREFIX` auto-allow and expanded `NOETIC_MCP_CORTEX` (4→18 tools).

### Changed
- System prompt and epistemic transaction skill updated with batch artifact
  commands in quick reference and collaborative mode tables.

## [1.8.9] - 2026-04-21

### Added
- **ENP watcher module** — git folder change detection shipped as core plugin.
  `enp-watcher.py` (cron-based poller), `enp-notify.py` (SessionStart hook),
  `enp-postflight-notify.py` (PostToolUse hook). Config template included.
- **`empirica enp-setup` command** — initializes `~/.empirica/enp/`, copies config
  template, initializes state from repo HEADs, prints cron + hook setup instructions.
- **Sentinel soft-gating** — `permissionDecision:ask` for borderline cases. When
  findings are logged but no CHECK submitted, prompts user instead of hard-blocking.
  Structural denies (closed loop, no bootstrap) unchanged.
- **Sentinel remote-ops auto-detect** — nudges when SSH/rsync/scp detected and
  `work_type` not set to `remote-ops`. Fires once per transaction via respond() nudge.
- **25 tests for memory_manager.py** — covers hot cache, promotion, demotion, eviction,
  stale markers, blank runs, file locking, substring safety, edge cases.
- **Memory layer override** in system prompt — clarifies project/reference memories
  flow through artifact logging pipeline, not manual writes.
- **ECO role** (Epistemic Compliance Officer) defined in ENP and Cortex gating specs.
- **EPISTEMIC_GATING_SPEC.md** in empirica-cortex — boundary spec between core and Cortex
  for memory lifecycle (promotion, decay, sharing, UQ gating).

### Fixed
- **Memory hot-cache promotion** was dead code — `confirmation_count >= 3` threshold
  structurally impossible (confirm_eidetic_fact needs exact hash match, findings never
  repeat verbatim). Now uses confidence-only filter with 3-per-POSTFLIGHT cap.
- **MEMORY.md bloated to 443 lines** (CC cap 200) — stale `<!-- empirica-auto-start -->`
  marker left 300 blank lines. Added `_strip_stale_markers()` and `_collapse_blank_runs()`.
- **Memory pipeline audit** (4 bugs): None project_id guard, fcntl file locking on
  MEMORY.md writes, session-end hook now calls full lifecycle, substring matching fixed.
- **Sentinel check-submit deadlock** — `_validate_check_record` blocked check-submit
  (the command that CREATES the CHECK record) because no CHECK existed.
- **Sentinel ACL gap** — `unknown-list`, `assumption-list`, and other read-only artifact
  commands were not in EMPIRICA_TIER1_PREFIXES, incorrectly gated as praxic.
- **C901 on `run_grounded_verification`** (CC 16→10) — extracted `_build_verification_summary()`
  and `_run_calibration_insights()`.
- **S110 try-except-pass** in grounded_calibration.py — replaced with logged error.
- **Qdrant test mock** — `test_upsert_docs_creates_collection_before_upsert` mocked
  wrong function (sequential path instead of batch path).
- **Qdrant failure visibility** — ImportError logged at info, connection failure at warning
  (was both debug-level, invisible without verbose).

### Changed
- **Sentinel** now uses three-level response: allow (safe), ask (borderline), deny (blocked).
- **Session-end hook** calls full memory lifecycle (promote, demote, enforce cap) via
  memory_manager imports, not its own partial implementation.
- **Memory promotion** logs at info/warning for Qdrant failures instead of silent debug.

### Removed
- 4 stale GitHub branches deleted (claude/empirica-mcp-integration, fix/setup-claude-dir-scope,
  fix/sqlite-update-order-by-syntax, fix/auto-embed-deadends-mistakes).

## [1.8.8] - 2026-04-18

### Added
- **Release chain verification** (Art. 10): Checks current version is published to all
  declared channels (git tag, GitHub release, PyPI, PyPI MCP, Docker, Homebrew).
  Configurable via `project.yaml publish_channels`. Found real homebrew gap on first run.
- **Discipline trajectory** check in compliance report (Art. 17).
- **AI transparency** check — git Co-Authored-By attribution (Art. 50).
- **Decision transparency** check — rationale coverage (Art. 13).
- **Tech documentation** check — docs-assess coverage (Art. 11).
- **Entity-extractor persistence** — state saved to `~/.empirica/entity_extractor_state.json`.

### Fixed
- **Homebrew formula stuck at 1.6.21** for 20+ releases. Root cause: release script
  URL regex expected GitHub release format but formula used PyPI URL. SHA updated
  but version in URL and assert_match never changed.
- **Release script** now handles both PyPI and GitHub URL formats in homebrew formula,
  updates assert_match version string.
- **Compliance report** DB queries: correct table/column names for epistemic audit
  and calibration checks.

## [1.8.7] - 2026-04-17

### Added
- **`empirica compliance-report`**: Project-wide quality snapshot mapped to 3 regulatory
  frameworks. 10 always-on checks covering 10 EU AI Act articles + 4 GDPR articles +
  10 ISO 42001 clauses. Optional: `--tests`, `--dep-audit`, `--security`.
- **Discipline trajectory** (Art. 17): Ungameable behavioral process score from
  observable evidence — transaction count, artifact breadth (6 types), goal completion,
  commit discipline. Every component measured by services the AI doesn't control.
- **AI transparency** (Art. 50): Git Co-Authored-By attribution check.
- **Decision transparency** (Art. 13 + GDPR Art. 22): Rationale coverage on decisions.
- **Technical documentation** (Art. 11): docs-assess coverage integrated (79.3%).
- **OWASP security scan** (Art. 15.4): Semgrep `p/owasp-top-ten` via `--security` flag.
- **Repository hygiene** (Art. 10): LICENSE, CHANGELOG, .gitignore, release scripts,
  no tracked secrets — 6 sub-checks.
- **Evidence summary in calibration**: POSTFLIGHT surfaces structured evidence (raw
  counts, pass/fail, ratios) + natural-language signals. AI is the calibrator; services inform.
- **Calibration reflection**: Narrative discipline_notes + assessment_notes replace
  per-vector synthetic observation scores. Divergence is information, not a grade.
- **Entity-extractor persistence**: State saved to `~/.empirica/entity_extractor_state.json`,
  survives compaction.

### Fixed
- **Ruff: 0 violations** (was 25,212 in 1.8.0, 65 in 1.8.5). Full compliance achieved.
- **C901: 0 violations** (was 65). All 65 remaining functions refactored to CC ≤ 15.
  ~200 helpers extracted across 50+ files via 5 parallel agents with inherited context.
- **Pyright: 0 errors** (was 216). Real bugs fixed (attribute access, return types,
  undefined vars, Python 3.10 compat). False positives suppressed inline.
- **test_pool_exhaustion**: CPython GC recycled id() values for unreferenced objects.
  Fixed by holding connection references.
- **Compliance report DB queries**: Corrected table/column names (reflexes.phase='POSTFLIGHT',
  project_findings, grounded_verifications, overall_calibration_score).
- **Flask host binding**: Defaults to 127.0.0.1 (was 0.0.0.0). Configurable via FLASK_HOST.

### Changed
- **Calibration reframe**: mapper.py outputs evidence summaries instead of per-vector
  observation scores. Internal Bayesian storage unchanged. Per-vector data prefixed
  with `_internal_` to signal it's not an optimization target.
- **Hard deprecation policy**: Deprecated code deleted immediately, documented in
  CHANGELOG. No warning labels. Exception: DB migrations.
- **Deleted**: `set-session-env.sh`, `set-session-env.ps1` (EMPIRICA_SESSION_ID deprecated),
  `elicitation.py`, `elicitation-result.py` (never wired), dead functions across codebase.
- **PreToolUse consolidation**: 2 sentinel-gate entries → 1 (`Edit|Write|Bash`).
- **Dependency security**: pytest ≥9.0.3 (CVE-2025-71176), python-multipart ≥0.0.26
  (CVE-2026-40347).

## [1.8.5] - 2026-04-16

### Fixed
- **Ruff compliance**: 25,212 violations → 65 (99.7% reduction). All rule categories
  except C901 complexity at zero. Added S (bandit) security rules, configured sensible
  ignores for zealous rules, per-file ignores for intentional patterns.
- **C901 complexity**: Max function CC 158 → 29. All functions over CC 30 eliminated.
  20 monolithic handlers refactored into orchestrators with ~120 extracted helpers.
  Key handlers: POSTFLIGHT 158→33, CHECK 129→10, PREFLIGHT 109→7, bootstrap 146→17.
- **Dead code**: 450 LOC removed (1 dead module, 9 dead functions, 2 duplicate wrappers).
- **Type safety**: 248 implicit-optional annotations fixed (RUF013: `str=None` → `str|None=None`).
- **Traceback preservation**: 18 `raise X` inside except blocks fixed to `raise X from e` (B904).
- **goals-activate bug**: GoalDataRepository instantiated without DB connection.
- **EMPIRICA_SESSION_ID post-compact**: Recovery messages now surface export command for
  shell env var restoration.
- **Security**: Removed `claude-safe` (sudoers file) accidentally committed in 1.6.7.

### Changed
- **Pyright config**: Suppressed noisy categories (Optional/Argument/Subscript/Call/Operator/
  Index/Assignment), kept bug-catchers (UndefinedVariable, ReturnType, AttributeAccess).
- **EMPIRICA_SESSION_ID deprecated**: CLI auto-resolves sessions via InstanceResolver.
  Env var no longer needed, `set-session-env.sh` shows deprecation warning.
- **Security rules active**: S (bandit) rules enabled in ruff — S110 try-except-pass,
  S608 SQL injection, S105 hardcoded passwords, S324 insecure hash all categorized.

### Added
- **`/dispatch-agent` skill**: Epistemic subagent context inheritance via Cortex.
  Enriches Agent tool prompts with relevant dead-ends, findings, decisions, and
  anti-patterns before dispatch. Validated: enriched agents fixed 19 C901 violations
  cleanly while blank agent on same task broke 20 files.

## [1.8.4] - 2026-04-15

### Fixed
- **Compliance pipeline**: Domain/criticality enrichment was destroyed on every
  POSTFLIGHT transaction close (R.transaction_write overwrites with base fields only).
  Compliance checks never fired despite being correctly configured. Now preserves
  enrichment fields across close.
- **Silent error surfacing**: Compliance loop and PREFLIGHT domain injection errors
  now logged with warnings instead of silently swallowed by except-pass.
- **Ruff auto-fix**: 15 lint issues fixed in session-changed files.

## [1.8.3] - 2026-04-15

### Changed
- **Behavioral feedback refactor**: PREFLIGHT `previous_transaction_feedback` now shows
  artifact gaps, commit discipline, and skill/command suggestions instead of vector-level
  overestimate/underestimate tendencies. Brier score surfaces as rolling trend
  (improving/stable/widening), not per-transaction number.
- **Belief framing**: Vectors are "beliefs about epistemic state" not performance scores.
  Services "inform" beliefs, not "correct" them. Updated across all AI-facing prompts,
  skills, end-user docs, developer docs, and reference docs (37 files total).
- **Transaction discipline**: 5 rules encoded in transaction skill and system prompts —
  goal-per-transaction, commit-per-subtask, artifact breadth, close-before-POSTFLIGHT,
  subtask-task visibility.
- **Pre-compact vectors**: Only included in compact guidance when carrying an open
  transaction through compaction. Closed sessions get "run fresh PREFLIGHT" instead.

### Added
- **Goal lifecycle**: `planned` status for goals logged but not yet started.
  `goals-create --status planned` creates backlog items excluded from metrics.
- **Migration 038**: Converts stale/blocked goals to in_progress. Goal lifecycle
  simplified to planned/in_progress/completed.
- **Planned goals workflow**: New documentation section in SESSION_GOAL_WORKFLOW.md
  showing the collaborative catalog-then-execute pattern.
- **Skill/command routing**: Behavioral feedback suggests specific actions —
  `/epistemic-transaction` for artifact gaps, `unknown-list` for unresolved unknowns,
  `goals-create` for goalless state.

### Fixed
- **Completion grounding bias**: Triage metrics denominator now scoped to transaction-
  relevant goals (created/completed/linked in this transaction), not all historical goals.
  Fixes 1/18 ratio bug when 17 old goals existed.
- **Planned goal exclusion**: `planned` goals excluded from completion metrics,
  prose collector ratios, and sentinel goalless detection.
- **NameError in behavioral feedback**: `missing` variable scoped correctly when
  `artifact_counts` is empty.

## [1.8.2] - 2026-04-13

### Added — Provenance Graph
- **Migration 036** — three provenance columns (all NULL-defaulted, additive):
  `source_refs` on project_findings (JSON array of source IDs),
  `evidence_refs` on decisions (JSON array of finding IDs),
  `resolution_finding_id` on project_unknowns.
- **CLI flags** — `--source` on finding-log (repeatable), `--evidence` on
  decision-log (repeatable), `--finding` on unknown-resolve. Links artifacts
  into a source-finding-decision traceability chain.
- **MCP tool parity** — `source_ids` on finding_log, `evidence_refs` on
  decision_log, `resolution_finding_id` on unknown_resolve. Array params
  handled via new `list_params` registry field.
- **Three check runners** — `recommendation_traceability` (decisions cite
  evidence), `finding_sourced` (findings cite sources), `provenance_depth`
  (at least one complete source-finding-decision chain).
- **Domain YAML updates** — consulting adds traceability at medium+, sourced
  at high+, depth at critical. Research adds sourced at medium+, traceability
  at high+, depth at critical.

### Added — Calibration Infrastructure
- **Work-type vector weight profiles** — 11 profiles in
  `confidence_weights.yaml` (code, research, debug, docs, comms, design,
  infra, audit, data, config, release). Triad resolution: work_type overrides
  domain overrides default. Research weights comprehension 0.35 and meta 0.25;
  code weights execution 0.40.
- **Context evidence items** — `project_epistemic_depth` (prior artifacts from
  other sessions), `session_accumulated_context` (completed transactions this
  session), `preflight_context_richness` (PREFLIGHT pattern count from
  transaction file). Fixes structural 0.72 context calibration gap.
- **Weight-aware coverage** — coverage threshold gate uses category-weighted
  coverage instead of raw vector count. Includes breadth penalty (single
  category insufficient). Enables noetic phase grounded calibration.
- **PREFLIGHT pattern persistence** — pattern count stored in transaction file
  so collector can read it at POSTFLIGHT for context evidence.
- **Uncertainty excluded from calibration score** — meta-uncertainty is
  circular (derived from gaps it would be scored against). Still gates CHECK,
  still appears in feedback, just not in the Brier number.

### Added — Skill Nudges
- **UserPromptSubmit hook** suggests `/empirica-constitution` when no active
  transaction exists (pre-PREFLIGHT orientation). Detects complex work signals
  (plan, implement, spec, transaction, preflight, artifacts, epistemic) and
  suggests `/epistemic-transaction` for structured decomposition.

### Changed
- **System prompt** — provenance-first proactive behaviors, source-finding-
  decision in collaborative mode signals table.
- **Transaction skill** — quick reference shows --source/--evidence/--finding.
- **Onboard** — source-add in investigation step, --evidence in praxic step.

## [1.8.1] - 2026-04-10

### Added
- **Goal-scoped compliance checks** — runners receive `changed_files` from
  the transaction's edited_files. Tests scope to changed test files, lint
  scopes to changed .py files, complexity measures changed files only.
- **New check runners:** `complexity` (radon cc, grades A-F) and `dep_audit`
  (pip-audit for known CVEs).
- **Tiered execution** — checks have tiers: `always` (lint, complexity),
  `goal_completion` (tests), `release` (dep_audit). Per-POSTFLIGHT cost
  drops from ~700MB/180s to ~80MB/5s.
- **Check result caching** — results cached by `(check_id, content_hash)`.
  Same changed files = same hash = instant cached result. AI sees
  `cached: true` or `deferred: true` on each result.
- **Unified `empirica resolve <id>`** — auto-detects artifact type and
  resolves. Searches unknowns, findings, dead-ends, mistakes, assumptions,
  decisions by ID prefix.
- **Bytecode cache invalidation** — `release.py` now clears `__pycache__`
  after version sweep.

### Changed
- **Onboarding** (`empirica onboard`) — updated for compliance pipeline,
  three-vector model, domain/criticality in PREFLIGHT, skills references.
- **Transaction skill** — new section 4f documents the compliance loop,
  tiered execution, caching, and Brier scoring on check predictions.
- **System prompt** — v1.8.0 reframe language, domain commands, resolve.
- **Constitution** — cross-project writing uses `--project-id <name>`.
- **Default domain** — medium adds complexity, high adds dep_audit,
  critical adds git_metrics.

### Fixed
- **Windows atomic state writes** (PR #85, @kars85) — `os.rename` →
  `os.replace` across 8 files. Prevents `FileExistsError` on Windows.
- **Qdrant dimension drift guard** (PR #83, @kars85) — centralised
  dimension check in `_ensure_collection_matches_vector` prevents silent
  data corruption from embedding model changes.

### Community
- Thanks @kars85 for PR #83 + #85 — consistent quality contributions.

## [1.8.0] - 2026-04-09

### Added — Sentinel Reframe: Compliance Loop Coordinator

The Sentinel architecture has been fundamentally reframed from a calibration
measurer to a **compliance loop coordinator**. Deterministic services produce
information; the AI synthesizes the grounded epistemic state from that
information using its own reasoning.

**Wave 1 — Foundation:**
- **A1: Domain Registry** (`empirica/config/domain_registry.py`) — maps
  `(work_type, domain, criticality)` tuples to compliance checklists. YAML
  schema with 3-tier precedence: project > user-global > built-in. Ships
  with 4 built-in domains: `default`, `remote-ops`, `cybersec`, `docs`.
  CLI: `domain-list`, `domain-show`, `domain-resolve`, `domain-validate`.
- **A2: Service Registry** (`empirica/config/service_registry.py`) —
  deterministic checks self-declare via `CheckDeclaration` with runner
  functions. `ServiceRegistry.run()` handles timeouts, captures exceptions.
  Built-in checks: `tests` (pytest), `lint` (ruff), `git_metrics`.
- **A3: Three-Vector Storage** — migration 035 adds `observed_vectors`,
  `grounded_rationale`, `criticality`, `compliance_status`,
  `parent_transaction_id` columns to `grounded_verifications`. New
  `compliance_checks` table. `ComplianceStatus` enum with 8 states.
  `GroundedAssessment` extended with `grounded_rationale`, `criticality`,
  `parent_transaction_id`, and `observed` property alias.

**Wave 2 — Integration:**
- **B1: Domain-aware CHECK gate** — Sentinel scales the uncertainty
  threshold by domain criticality. Higher criticality = stricter gate.
  `PreflightInput` gains optional `domain` and `criticality` fields.
- **B3: Grounded rationale CLI** — `PostflightInput` gains
  `grounded_vectors` and `grounded_rationale` fields. POSTFLIGHT response
  includes `three_vector` block when AI submits reasoned grounded state.
  NULL rationale = legacy (no AI reasoning happened).

**Wave 3 — Orchestration:**
- **B2: Iterative compliance loop** (`empirica/core/post_test/compliance_loop.py`)
  — at POSTFLIGHT, runs the domain checklist, reports compliance status,
  advises on follow-up transactions for failed checks. Status flow:
  `complete` → `iteration_needed` → `max_iterations_exceeded`.
- **B4: Check-outcome Brier scoring** — AI predicts P(check passes) in
  PREFLIGHT via `predicted_check_outcomes`. Brier score computed from
  predictions vs actual outcomes. Falsifiable, ground-truth calibration
  alongside the existing vector-divergence Brier (both coexist during
  transition).

**C2: Real check runners** — replaces stub runners with subprocess
execution: pytest (`--tb=no -q`), ruff (`--output-format=json`),
git status (`--porcelain`). All handle timeouts and missing tools.

**Stability:** 11 Wave 1 integration checkpoint tests (SPEC 1 Part 8)
covering domain+service composition, migration, legacy compat, remote-ops
regression, cybersec compliance flow, and backward compat.

### Fixed
- **Test isolation** (KNOWN_ISSUES 11.17) — `conftest.py` now sets
  `EMPIRICA_INSTANCE_ID=test-{pid}` (priority 1 in `get_instance_id`),
  strips `TMUX_PANE`/`WINDOWID`/`TERM_SESSION_ID`, sets
  `EMPIRICA_HEADLESS=true`. Tests get their own namespace; live sessions
  are never touched.
- **Compact hook resilience** — `pre-compact.py` gracefully degrades
  (exit 0, empty JSON) when `find_project_root()` returns None, instead
  of blocking compact. No CWD fallback per KNOWN_ISSUES 11.10.
- **Prose collector SQL bugs** — 3 silent `OperationalError`s fixed:
  `completed` → `is_completed` (goals), `resolved` → `is_resolved`
  (project_unknowns), `project_handoffs` → `handoff_reports` (task_summary).

### Security
- `cryptography` upgraded to 46.0.7 (CVE-2026-39892)

### Stats
- 914 tests pass (113 new in this release)
- ~3800 LOC new code across 10 new modules
- 15 commits from 1.7.13

## [1.7.13] - 2026-04-08

### Fixed
- **Subagent rows polluting main `sessions` table** — `SubagentStart` hook was
  calling `SessionDatabase.create_session()` for every Task spawn (Explore,
  general-purpose, superpowers:* etc), creating rows in the main `sessions`
  table with `parent_session_id` set. Subagent children were always newer
  than their parents, so post-compact diagnostics, statusline lookups, and
  any "recent sessions" query surfaced only subagent rows — masking the
  actual parent session.

  **Fix:** New dedicated `subagent_sessions` table (migration 034) plus
  `SessionDatabase.create_subagent_session()`, `end_subagent_session()`,
  `get_subagent_session()`, `list_subagents_for_parent()`. Lineage to the
  parent is preserved via `parent_session_id`; rollup at SubagentStop still
  logs findings to the parent session in the main `sessions` table. The
  migration moves legacy subagent rows out automatically (status `completed`
  if `end_time` was set, `orphaned` otherwise). `SubagentStart` and
  `SubagentStop` hooks updated to use the new methods.

- **Cross-project session reuse leaving parent unrecoverable after compact**
  (KNOWN_ISSUES 11.24, completes the partial fix from 11.19) — `post-compact.py`'s
  `CONTINUE_TRANSACTION` branch propagated `tx_session_id` from the pre-compact
  transaction snapshot forward into `active_work` / `active_transaction` files
  without verifying the session existed in the current project's local
  `sessions.db`. When the parent session was originally created in a different
  project's DB (cross-project `--resume` pattern), all subsequent CLI commands
  failed `_validate_session_in_db` with "session NOT FOUND".

  **Fix:** New `SessionDatabase.ensure_session_exists()` performs an
  idempotent insert of a minimal session row (marked
  `session_notes='auto-healed by post-compact'`, registered in `workspace.db`
  for cross-project visibility). `post-compact.py` now calls
  `_validate_session_in_db` on `tx_session_id` and auto-heals before
  propagating it forward; failure of the heal itself is non-fatal and
  logged to stderr. Issue 11.19's "ghost session detection" added the
  validator but only wired it into the `CHECK_GATE` branch — this completes
  the wiring across all post-compact routing paths.

- **Test coverage:** `tests/test_subagent_sessions.py` — 13 new tests
  covering schema, migration 034 (move + orphan-status detection +
  idempotency), all 5 new repository methods, and `ensure_session_exists`
  idempotency + caller-provided session_id preservation.

- **Grounded calibration honesty — `insufficient_evidence` and `remote-ops`** —
  the grounded verification layer was producing calibration scores even
  when the evidence bundle was empty or sparse, inviting metric-sycophancy
  (phantom scores from no data, or low scores from work the local Sentinel
  couldn't observe at all, like SSH / customer-machine operations). Three
  related fixes:

  * **`calibration_status` field** on `GroundedAssessment` with three
    outcomes: `grounded` (normal, sufficient evidence), `insufficient_evidence`
    (empty bundle OR `grounded_coverage < 0.3`), and `ungrounded_remote_ops`
    (work_type=remote-ops short-circuits collection entirely). Replaces the
    silent `return None` path that used to hide empty bundles.
  * **`remote-ops` work_type** added to `PreflightInput` regex. When an AI
    declares `work_type=remote-ops` in PREFLIGHT, the verifier skips
    PostTestCollector entirely and the self-assessment stands unchallenged.
    This is the honest path for work the local Sentinel has no signal for
    (SSH, customer machines, remote config). Backed by end-to-end tests
    through `run_grounded_verification`.
  * **`sources_empty` and `source_errors`** on `EvidenceBundle` — the
    collector now distinguishes between sources that returned zero items
    (valid empty) versus sources that errored (schema drift, SQL failures).
    Previously both were lumped into `sources_failed` and the error
    messages were swallowed. The new visibility immediately surfaced three
    pre-existing silent schema bugs in the `prose_quality`,
    `document_metrics`, and `action_verification` collectors (all
    `OperationalError: no such column`) — tracked for 1.7.14 follow-up.
  * **Coverage threshold gate (0.3)** in `_run_single_phase_verification`
    halts gap computation when `grounded_coverage < threshold`, returning
    `insufficient_evidence` instead of emitting phantom scores from sparse
    data. This is the load-bearing change — calibration becomes honest
    about when it doesn't know rather than manufacturing a number.
  * **`filter non-grounded phases from holistic score computation`** —
    when one phase (noetic or praxic) is `insufficient_evidence`, the
    holistic score is now computed only from the phase that does have
    signal, rather than averaging with `None`.
  * Documentation: `remote-ops` work_type surfaced in the EWM system
    prompt and epistemic-transaction skill so AIs know when to use it.

- **CWD overrides bypassing open transactions at compact boundary**
  (KNOWN_ISSUES 11.26 + 11.27) — when a user worked across CWD/project
  mismatch (e.g. terminal cd'd into project A but the open transaction
  lives in project B), post-compact rotation triggered the
  `event_type='startup'` SessionStart and two CWD-prefer overrides
  silently re-routed everything to the wrong project's DB:

  * **session-init.py STARTUP OVERRIDE** preferred CWD (`Path.cwd()` /
    `_find_git_root()`) over the resolved project root whenever the CWD
    had a valid `.empirica/sessions/sessions.db`. The original intent
    (from #72: "prefer CWD over stale instance files on startup") was
    correct, but the override didn't check whether the resolved project
    had an open transaction — orphaning live transactions and creating
    duplicate sessions in the wrong DB.
  * **path_resolver.get_session_db_path() cross-check** had the same
    blind spot. When `EMPIRICA_CWD_RELIABLE=true` (set by session-init
    after its `os.chdir`), the gated cross-check preferred CWD's git
    root over the unified context's project_path, again without an
    open-transaction check. This amplified the session-init bug — once
    session-init re-routed to CWD, every subsequent CLI command followed
    suit because EMPIRICA_CWD_RELIABLE was sticky.

  **Fix:** Both override sites now read the resolved project's
  `active_transaction{suffix}.json` and bail out of the override if
  `status=open`. Open transactions are authoritative across compaction
  boundaries — CWD never wins over a live transaction. Other readers
  (`pre-compact.py`, `post-compact.py`, `sentinel-gate.py`,
  `session-end-postflight.py`) audited and confirmed clean — they
  already use strict resolution without CWD-prefer logic.

  Test coverage: `tests/test_open_transaction_guard.py` reproduces both
  failure modes and asserts the guards hold (CWD reliable + open tx →
  resolver stays on transaction project) plus the regression check (no
  open tx → existing CWD cross-check still fires correctly).

- **Auto-memory loaded from wrong project across CWD mismatch**
  (KNOWN_ISSUES 11.28) — Claude Code's auto-memory loader is wired to
  the harness CWD at session start, so when a user worked on project A
  but their terminal was in project B, every conversation loaded B's
  `~/.claude/projects/-{B}/memory/MEMORY.md` even though the open
  transaction (and all the actual work) lived in A. The unified context
  resolver fixed this for internal Empirica code paths in 1.7.11+, but
  Claude Code's auto-memory loader is outside Empirica's control.

  **Fix:** New `empirica.utils.memory_swap` module with `swap_memory()`
  / `restore_memory()` / `maybe_swap_for_active_transaction()`. When
  the harness CWD project doesn't match the active transaction's
  project, post-compact backs up the harness-CWD memory dir contents
  to a sibling backup subdir and copies the active project's memory
  contents into the harness slot. Restored on session-end-postflight
  (or replaced cleanly on the next compact/project-switch). The swap
  is idempotent, manifest-tracked, and round-trip safe — restore is
  byte-identical to the original. Wired into `post-compact.py`,
  `session-end-postflight.py`, and `handle_project_switch_command`.

  Test coverage: 17 tests in `tests/test_memory_swap.py` covering
  swap, restore, idempotency, replacement, round-trip preservation,
  nested directories, and the hook entry point. Memory swap is
  defense-in-depth with the resolver — both layers contribute to
  cross-CWD project work behaving correctly.

- **`project-switch` auto-heal** (KNOWN_ISSUES 11.25, completes the
  validation-gap audit started in 11.24) — `handle_project_switch_command`
  mirrored the current session into the target project DB only when
  `global_sessions` returned a row matching the current instance_id +
  active status. When that lookup missed (tmux restart, instance ID
  drift, status drift), the mirror was skipped and the active_work
  file ended up pointing at a session that didn't exist in the target
  project's DB. Subsequent CLI commands then surfaced the same
  "_validate_session_in_db: session NOT FOUND" diagnostic as 11.24.

  **Fix:** Project-switch now reads the existing
  `active_work_<claude_session_id>.json` as a fallback session source
  when the global_sessions mirror misses, then calls
  `ensure_session_exists()` on the target project's DB before
  propagating the session_id forward to active_work. Heal-row note
  changed from "auto-healed by post-compact" to "auto-healed
  (cross-project session reuse)" since both project-switch and
  post-compact share the same heal path.

  Same lessons-learned checklist applies: when adding a validator,
  audit ALL paths that propagate the validated value. The 11.24 fix
  caught the post-compact path; this fix catches the project-switch
  path.

### Added
- **`empirica diagnose` command** — new CLI command that walks the
  Empirica + Claude Code integration step-by-step and reports
  PASS / FAIL / WARN with an actionable hint per check. Designed for
  the recurring "I installed it but the statusline isn't showing"
  class of question (see issue #81).

  Checks:
  * Python version (>= 3.10)
  * `empirica` CLI on PATH
  * Claude Code config dir (`~/.claude/` or `$CLAUDE_CONFIG_DIR`)
  * Plugin files installed in `~/.claude/plugins/local/empirica/`
  * `settings.json` present and valid JSON
  * `statusLine` block configured and pointing at the Empirica script
  * All 6 critical hooks registered (sentinel-gate, pre-compact,
    post-compact, session-init, subagent-start, subagent-stop)
  * Local marketplace registered
  * Statusline script runnable + produces non-empty output
  * **Empirica project initialized** (`.empirica/` present in cwd or ancestor) —
    this was the missing step for subu1979 in #81; surfaced as a dedicated
    check with a clear actionable hint pointing at `empirica project-init`
  * Active session in current project DB

  Output modes: `--output human` (colored, with fix hints) and
  `--output json` (machine-readable, suitable for issue reports).

  Exit codes: `0` if all pass, `1` on any FAIL, `2` on WARN-only.

  Tests: 26 in `tests/test_diagnose.py` covering each check in
  isolation against fake `~/.claude/` fixtures, plus output
  formatting (human + JSON round-trip).

- **EPP hook-driven activation** — the `<semantic-pushback-check>` block is now
  injected into every substantive user prompt (>=20 chars, not slash command)
  via `tool-router.py`. Block instructs Claude to do semantic pushback
  classification as its first generation step — ANCHOR → CLASSIFY → DECIDE →
  RESPOND — instead of defaulting to the sycophancy attractor under
  non-evidential pushback. In-context recall only; no persistent anchors.
  See `docs/architecture/EPP_ARCHITECTURE.md`.
- **`empirica epp-activate` CLI command** for self-reported EPP telemetry.
  Flags: `--category` (emotional/rhetorical/evidential/logical/contextual),
  `--action` (hold/soften/update/reframe). Writes to
  `~/.empirica/hook_counters{suffix}.json` (counter + last-50 log).
- **Phase 0 calibration harness** (`scripts/phase0_epp_calibration.py`)
  measuring forcing-language effect size across Opus/Sonnet/Haiku before
  shipping. Uses `claude -p` via Claude Code CLI (no API key required).
  All 3 models passed the ≥20%-on-≥2/6-metrics decision gate. Results in
  `scripts/phase0_epp_results.json`. Zero edge-case false positives.
- **New architecture doc** `docs/architecture/EPP_ARCHITECTURE.md` — two-layer
  design, why semantic-check over regex, Phase 0 results, context budget,
  explicit out-of-scope items.

- **CHECK-time calibration nudge** — `handle_check_submit_command` now
  dynamically queries the current transaction's artifact counts and adds a
  `calibration_nudge` to `praxic_reminders` when the AI logged zero
  artifacts (or only one type with <3 entries) before proceeding to praxic.
  Replaces the earlier static reminder dict. Prospective feedback at the
  noetic→praxic transition is more actionable than the retrospective
  POSTFLIGHT `breadth_note`. 11 new tests in
  `tests/core/test_check_calibration_nudge.py`. Addresses the chronic
  "AI doesn't log epistemic artifacts" pattern that was dragging
  calibration scores to 0.11–0.28.

### Changed
- **Onboarding docs** now put `empirica project-init` and
  `empirica setup-claude-code` front-and-center as required steps before
  launching Claude Code. Closes the UX gap from issue #81 where a user
  with a working GLM-5.1 + Ollama Cloud stack hit "Cannot determine
  sessions.db path" because they'd skipped `project-init`. Updated:
  `docs/human/end-users/01_START_HERE.md`,
  `docs/human/end-users/02_INSTALLATION.md`, and a deprecation note at
  the top of the legacy plugin `INSTALL.md`.

- **EPP SKILL.md** updated with "Hook-Driven Activation (since v1.7.12)" section
  explaining the semantic-check mechanism and Phase 0 validation.
- **`tool-router.py` complexity reduction** — `build_routing_advice` extracted
  into 5 single-purpose helper functions (one per advice category), bringing
  cyclomatic complexity from C/18 down to A/B range. Functional behavior
  unchanged. Pre-existing main loop also simplified via extracted
  `_build_aap_context` helper.

## [1.7.11] - 2026-04-06

### Fixed
- **Python 3.14 + Windows compatibility** (#80) — Empirica was unusable on `uv tool install` (which now ships Python 3.14 by default) on Windows
  - **argparse `%` collision**: Python 3.14 made argparse stricter about `%` in help strings (treats `%X` as printf format specifier). Escaped literal `80%` → `80%%` in `edit-with-confidence` parser. Added defensive `%` escape in `format_help_text()` for any future help strings with `%` in defaults or text.
  - **Windows emoji crash**: cp1252 codec couldn't encode emoji in main parser description, crashing `--help` and any `parse_args()` error path on Windows. Removed emoji from `cli_core.py` description.
  - Thanks to **@graemester** for the detailed bug report with traceback, root-cause analysis, and proposed fixes.
- **`setup-claude-code --force` NameError** (#79) — Stale `claude_dir` reference inside `_configure_settings()` after the v1.7.10 extraction. `claude_dir` wasn't in the function's scope; `settings_file` was already passed as a parameter. Thanks to **@pschwinger** for the fix.

## [1.7.10] - 2026-04-06

### Added
- **Full artifact storage parity** — All 7 artifact types (findings, unknowns, dead-ends, mistakes, assumptions, decisions, sources) now write to all 3 layers: SQLite + Git Notes + Qdrant
- **GitSourceStore** — New git notes store for epistemic sources (refs/notes/empirica/sources/{id})

### Changed
- **Consolidated mistake_commands.py** into artifact_log_commands.py — all artifact logging in one file
- **POSTFLIGHT handler** — F/224 → F/126 (-44%) via 3 extracted functions
- **Bootstrap handler** — F/203 → F/58 (-71%) via file split to project_bootstrap_formatter.py
- **Setup handler** — F/142 → F/83 (-42%) via extracted _configure_settings
- **Ruff issues** — 8343 → 1723 (-79%) via auto-fix batches (UP045, F541)

### Fixed
- **Instance_projects overwrite** — Sequential Claude sessions in same pane no longer overwrite active transactions
- **Transaction-scoped completion** — CHECK reminds to rate per-transaction, POSTFLIGHT hints on goal completion

## [1.7.9] - 2026-04-06

### Fixed
- **MCP TOOL_REGISTRY audit** — 23 param mismatches fixed across 16 tools. All 45 tools verified against CLI `--help`. Added positional argument support for investigate and goals-search
- **MCP binary path drift** — `setup-claude-code` now prefers venv binary over stale pipx install. Always updates mcp.json command path when binary changes
- **Transaction-scoped completion scoring** — CHECK proceed reminds "Rate completion for THIS TRANSACTION only." POSTFLIGHT detects goals completed in transaction and hints completion should be near 1.0
- **Ruff callable|None runtime error** — UP045 auto-fix produced invalid `callable | None` union. Fixed by removing type annotation

### Changed
- **Ruff auto-fix** — 8343 → 1723 issues (-79%). UP045 optional annotations (1121), F541 empty f-strings (384)
- **generate_suggestions refactored** — F/46 → B/8. Extracted 5 analysis functions + 3 shared helpers
- **MCP server tools updated to 45** — Added `workflow_patterns` tool

## [1.7.8] - 2026-04-05

### Added
- **5-tier memory management system** — CC `memory/*.md` managed as KV cache. POSTFLIGHT pipeline: hot-cache update → eidetic promotion → stale demotion → MEMORY.md eviction. Manual files never auto-managed
- **Memory promotion** — High-confidence Qdrant eidetic facts (>=0.7 confidence, 3+ confirmations) auto-promoted to `promoted_*.md` at POSTFLIGHT
- **Memory demotion** — Stale promoted files (>30 days) archived to `memory/_archive/` (reversible)
- **MEMORY.md eviction** — Auto section trimmed at 180 lines. Lowest-ranked items evicted, stay in Qdrant
- **Compact CLI help** — 267→60 lines. All 6 artifact types shown prominently
- **`empirica help` command** — `empirica help` (all categories), `empirica help <category>` (drill-down)
- **CC memory stats in memory-report** — File count, sizes, MEMORY.md lines, manual vs promoted
- **`profile-prune --scope memory`** — Archive stale promoted memory files
- **Intelligence search kind** — `kind='intelligence'` with collection-type boost weights
- **Workflow Pattern Mining** — Detect repeated tool sequences across transactions via sequential pattern analysis. New `workflow-patterns` CLI command and MCP tool
- **Workflow Suggestion Engine** — Epistemic-correlated pattern analysis surfaces workflow suggestions based on historical transaction data

### Changed
- **Sentinel noetic allow list** — Added `ToolSearch`, intelligence layer MCP tools, `git notes show`/`git notes list`
- **Sentinel closed-transaction noetic check** — Closed transactions allow noetic tools without new PREFLIGHT
- **Cortex POSTFLIGHT push** — Verified predictions pushed at transaction boundary, not just session end

### Fixed
- **MCP CASCADE timeout** — POSTFLIGHT/PREFLIGHT/CHECK commands now use 120s timeout (was 30s). Configurable via `EMPIRICA_MCP_CASCADE_TIMEOUT`
- **PreCompact hook schema** — Switched to `systemMessage` for compact guidance

### Security
- **Docker `token-gen` removed from safe commands**
- **POSTFLIGHT intelligence layer auth** — Includes `Authorization: Bearer` header

## [1.7.6] - 2026-04-04

### Added
- **Intelligence layer sync** — Session hooks can pull cross-domain context at start and push verified deltas at end. Configured via env vars. Graceful degradation if unavailable.
- **Epistemic Brief documentation** — Quantified project profile feature now documented in CHANGELOG and referenced in docs

### Fixed
- **Session-init CWD override** — On `startup` events, prefers CWD over stale instance files from previous sessions. Fixes #72: project-bootstrap loading wrong project context
- **SQLite UPDATE...ORDER BY syntax** — Subquery replaces MySQL-only syntax in sentinel override sync. Fixes CHECK/Sentinel split-brain deadlock (PR #71)
- **project-switch stats query** — Moved before output format branch

### Changed
- **MCP Server Reference** — Complete rewrite to document 44-tool table-driven architecture (was documenting stale 102-tool server)
- **9 documentation fixes** — Stale version headers (1.6.6→1.7.5), old plugin name references (empirica-integration→empirica), VectorRouter marked as removed, CONFIGURATION_REFERENCE.md updated with env vars, CWD startup exception documented in SESSION_RESOLVER_API and ARCHITECTURE docs
- **Removed duplicate CHANGELOG** — `docs/reference/CHANGELOG.md` deleted (was frozen at 1.6.4, root CHANGELOG.md is single source)

## [1.7.5] - 2026-04-03

### Added
- **Epistemic Brief** — Quantified project epistemic profile displayed on `project-switch`. Shows 6 categories: Knowledge State, Risk Profile, Anti-Patterns, Calibration Health, Active Work, Learning Velocity
- **Configurable intelligence layer URL** — `EMPIRICA_CORTEX_URL` env var for remote intelligence layer (default: localhost:8420). Graceful degradation if unreachable

### Changed
- **MCP server rewrite** — Complete rebuild as thin CLI wrapper. 102→44 tools, 3254→507 lines. Table-driven `TOOL_REGISTRY` maps tools to CLI commands. Removed epistemic middleware (Sentinel handles gating via hooks). All subprocess calls have 30s timeout (configurable via `EMPIRICA_MCP_TIMEOUT`)
- **MCP hanging fix** — CASCADE commands (preflight/check/postflight) now use stdin JSON routing. Non-stdin commands use `stdin=DEVNULL`. Fixes server hanging on workflow submissions

### Fixed
- **PreCompact hook schema validation** — Hook output included non-schema top-level fields (`ok`, `trigger`, `empirica_session_id`, etc.) that Claude Code rejected. Now outputs only `systemMessage` (success) or `stopReason` (error)
- **project-switch live counts** — Queries per-project sessions.db instead of stale workspace.db artifact counts
- **Transaction race condition** — Two-file split: `active_transaction` (workflow-owned) and `hook_counters` (hook-owned). POSTFLIGHT reads counters then deletes counters file. Sentinel no longer overwrites POSTFLIGHT's status=closed
- **release.py missing pyproject.toml** — Source-of-truth version file now staged in release commits

### Refactored
- **Sentinel main()** — F/139 → F/67 (-52%) via 9 extracted helpers
- **is_safe_bash_command()** — E/35 → C/16 via table-driven refactor
- **Qdrant search** — F/64 → D/22 (-66%, -111 lines) via `_SEARCH_COLLECTIONS` config table
- **handle_finding_log** — F/41 → C/19 via 5 storage helpers
- **14 F-grade functions** reduced below threshold across workflow, artifact, profile, and project commands

### Security
- 23 dependency CVEs resolved (pillow, werkzeug, pygments, pyjwt, pyasn1, nltk, nicegui, aiohttp, cairosvg, cryptography, flask)

## [1.7.4] - 2026-04-02

### Added
- **Proactive compaction advisory** — `UserPromptSubmit` hook provides context window usage warnings
- **Statusline context usage** — Shows context window usage percentage
- **Auto-embed dead-ends and mistakes** — Dead-ends and mistakes now auto-embed to Qdrant alongside findings
- **Plugin version drift detection** — Session-init warns when installed plugin version differs from repo
- **Sentinel `--version`/`--help` whitelist** — Always-safe regardless of transaction state
- **Sentinel work-type-aware gating** — Command classification adapts to declared `work_type`

### Changed
- **Lean post-compact recovery** — Reduced `max_items` from 10-15 to 5 for faster recovery
- **Hook counters split** — `active_transaction` (workflow-owned) and `hook_counters` (hook-owned) files separated to eliminate race condition
- **Prediction-grounding reframe** — System prompt and Sentinel messages reframed from knowledge-centric to prediction-grounding language
- **15 refactoring passes** — Sentinel main F/139→F/67, is_safe_bash E/35→C/16, Qdrant search F/64→D/22, finding handler F/41→C/19, plus 11 more F-grade functions reduced across workflow, profile, statusline, goals, artifacts, embed, monitor, training, sync, and project-init

### Fixed
- **Instance-isolate context_usage.json** — Multi-pane support for context tracking
- **Pre-POSTFLIGHT artifact sweep** — Epistemic-transaction skill now enforces artifact logging before POSTFLIGHT

### Style
- **Ruff auto-fix** — 5,329 issues fixed across 269 files, plus targeted unsafe fixes (F811, RUF021, SIM114, C420)

## [1.7.3] - 2026-03-29

### Added
- **Sentinel advisory mode** — Measurement system framing replaces rules-based gate language
- **4 epistemic agent examples** — Sample agent configurations with codebase-onboarder output example

### Changed
- **Artifact context helpers** — All 5 artifact handlers rewired to shared `_prepare_artifact_context` + `_parse_config_input`

### Fixed
- **POSTFLIGHT missing subprocess import** — Retrospective git check crashed on missing import
- **Calibration max_inflation** — Reduced from 0.20 to 0.05 across all cascade profiles to prevent confidence overestimation

### Housekeeping
- Spring cleaning — archived 30 stale scripts, stale examples, empty directories, and one-line installer

## [1.7.2] - 2026-03-27

### Added
- **Sentinel ConfidenceGate** — Gating for remote infrastructure commands based on calibration confidence
- **Git notes storage for assumptions and decisions** — Portable epistemic artifacts via git notes
- **Transaction-scoped evidence** — Calibration evidence scoped to transaction with artifact breadth feedback
- **Source provenance** — Auto-extract source file refs from artifact text
- **Semantic index generator** — Script for building searchable semantic indexes
- **`source-list` command** — List all epistemic sources (merged view). `refdoc-add` deprecated in favor of `source-add`
- **Cross-project artifact and goal creation** — `--project-id` flag on all commands, resolved via workspace.db
- **Batch embedding** — `project-embed` upsert ~5-10x faster via batched Qdrant operations

### Changed
- **Lean prompt is now default** — `--lean` removed, replaced by `--full-prompt` for verbose mode

### Fixed
- **Calibration cold-start death spiral** — Confidence damper + hard cap prevent runaway low scores on first transactions
- **Assumption-log and decision-log SQLite wiring** — Were not persisting to database
- **Cross-project DB path resolution** — `R.project_id()` crash and canonical `InstanceResolver` usage
- **Goal completion evidence scope** — Now scoped to transaction, not entire session
- **Release script** — Commits all version-swept files in `--publish`

## [1.7.1] - 2026-03-26

### Fixed
- **`setup-claude-code --force` no longer nukes other plugins' hooks** — Previously cleared ALL hooks in settings.json. Now filters by Empirica plugin path, preserving Railway, Superpowers, and custom hooks
- **Python version detection** — `_find_python()` now prefers `python3` over versioned `python3.X` binaries, preventing hooks from using `python3.13` which may not exist on all systems
- **`/empirica` command trigger matching** — Description now includes common phrases ("sentinel paused", "turn off empirica", "off-record statusline") so Claude can associate user intent with the command
- **Sentinel pipe targets** — Added `base64` to `SAFE_PIPE_TARGETS` so `gh api ... | base64 -d` isn't blocked as praxic
- **README What's New sync** — Release script now auto-syncs What's New section from CHANGELOG via `sync_readme_whats_new()`
- **Cross-project search dedup** — Deduplicate results by content across project collections

## [1.7.0] - 2026-03-26

### Highlights

Empirica 1.7.0 introduces **epistemic governance** — a constitutional decision framework that routes AI decisions to the right mechanism, calibrated position-holding under pushback, and an 81% reduction in always-loaded context through skill-based architecture.

### Added — Governance & Skills
- **Empirica Constitutional Decision Tree** — 12-section governance framework routing situations to mechanisms (search, measurement, interaction, escalation). Replaces front-loaded instructions with a decision tree Claude loads on demand
- **Epistemic Persistence Protocol (EPP)** — Calibrated position-holding under user pushback, replacing the binary Anti-Agreement Protocol. Classifies pushback into 5 categories (emotional, rhetorical, evidential, logical, contextual), gates position updates on evidence strength
- **Lean Core System Prompt** — 1,191 tokens (81% reduction from 6,292). Keeps identity, vectors, transaction discipline. Everything else loads via skills on demand. Experimental — opt-in for 1.7.0
- **SessionStart skill nudges** — Constitution, EPP, and epistemic-transaction skills surfaced at session start (~30 tokens each)
- **EWM Business Interview** — Non-technical user onboarding with pre-loaded company context, Phase 7 narrative validation (from user feedback)

### Added — Cross-Project Intelligence
- **Cross-project Qdrant search** — `--global` flag now searches ALL registered projects' memory, eidetic, and episodic collections, not just global_learnings. Discovers project IDs from collection names, merges and ranks results by score
- **Cross-project artifact writing** — `--project-id <name>` on finding-log and unknown-log resolves target project's DB via workspace.db and writes directly. No project-switch needed
- **Sentinel remote command classification** — SSH inner commands classified noetic/praxic using SAFE_BASH_PREFIXES. rsync/scp classified by transfer direction. Docker inspection commands safe

### Added — Profile & Parser
- **ClaudeAIParser rewrite** — Handles real Claude.ai export format (ZIP with conversations.json). Parses content[] blocks as canonical source, not text field
- **Profile management CLI** — `profile-sync`, `profile-prune`, `profile-status` with git notes as portable format
- **ProfileImporter** — Git-notes-to-SQLite import with INSERT OR IGNORE deduplication

### Changed
- **Plugin renamed** — `empirica-integration` → `empirica`. All 47 references updated across 25 files. Agent names: `empirica:security`, `empirica:architecture`, etc. Migration: `setup-claude-code --force` removes old directory and orphaned cache
- **Investigate cool-down** — Requires 3 noetic tool calls before CHECK resubmission after `investigate` decision. Prevents vector inflation gaming. Self-reported by Claude
- **Sentinel error messages** — Now include actual CLI commands to unblock (e.g., "Command: empirica preflight-submit -")
- **Calibration philosophy** — Dual-track calibration documented as complementary, not hierarchical. Grounded evidence is informative, not authoritative

### Fixed
- **CHECK/Sentinel split-brain** — CHECK saved pre-override decision to DB while sentinel-gate read it. AI saw "proceed" but Sentinel blocked with "investigate". Fixed by syncing override to DB after sentinel decision
- **Sentinel subagent false positive** (#68) — Stale `active_session_tmux_*` files caused false subagent detection when `active_work` was missing. Tightened to verify parent session is actually active
- **Transaction suffix-mismatch** (#11.22) — Hooks without TMUX_PANE now scan for matching transaction files by session_id
- **Qdrant duplicate embeddings** — Three embed paths (project-embed, rebuild, POSTFLIGHT auto-embed) used sequential integer IDs instead of artifact UUIDs. Fixed all three to use md5-hashed UUIDs matching embed_single_memory_item
- **recreate_project_collections** — Missing `_intents_collection` (10th of 10 types)
- **Stale `__all__` exports** — Removed 3 undefined names from profile_loader.py
- **`setup-claude-code --force`** — Now actually clears hooks and statusLine before reinstall
- **README version** — Badge, docker commands, What's New, and footer now use version-agnostic regex in release script
- **requests dep** — Bumped floor to >=2.33.0 (CVE-2026-25645)

### Security
- 6 dependency CVEs audited: requests updated, werkzeug+pillow already pinned, pyasn1/pygments/pyjwt transitive
- Threshold values removed from sentinel-gate docstring to reduce AI information leakage

## [1.6.23] - 2026-03-23

### Added
- **Release auto-issue gate** — `--prepare` now checks for unresolved high-severity auto-captured issues before allowing publish. Prevents releasing with known runtime errors in the DB. Fails gracefully if CLI unavailable

### Fixed
- **`setup-claude-code --force` was a no-op for hooks/statusLine** — Plugin files were re-synced but hooks and statusLine were guarded by existence checks that silently skipped updates. `--force` now clears both before repopulating from current definitions. Fixes #66, reported by @Facarus

## [1.6.22] - 2026-03-23

### Added
- **Profile management CLI** — `profile-sync`, `profile-prune`, `profile-status` commands for epistemic profile lifecycle. Git notes as canonical portable format, SQLite as working database. Rule-based and manual pruning with `--dry-run` support
- **ProfileImporter** — Git-notes-to-SQLite import path. Rebuilds working database from portable git notes (findings, unknowns, dead-ends, mistakes, goals). INSERT OR IGNORE deduplication
- **Sentinel remote command classification** — SSH/rsync/scp commands now classified as noetic/praxic instead of blanket allow/deny. Inner commands extracted and classified using same SAFE_BASH_PREFIXES logic. Direction-aware for rsync/scp (upload=praxic, download=noetic). Includes Docker inspection, heredoc handling, chain/pipe parsing
- **Release script two-phase flow** — `--prepare` (merge, build, test gate) and `--publish` (push to all channels) split for safer releases

### Fixed
- **CHECK composite showed wrong percentage** — CHECK phase was calculating composite from execution vectors (state, change, completion, impact) instead of readiness vectors (know, context, clarity, coherence, signal, density). CHECK gates readiness-to-act, not acting progress
- **Statusline CHECK phase display** — Now shows percentage composite instead of just arrow/ellipsis
- **Profile resource leaks** — 3 `SessionDatabase` instances opened without try/finally in profile commands. db.close() was skipped on exceptions
- **Bootstrap NoneType comparison** — `workflow_suggestions.py` `duration_minutes` could be `None` when session `start_time` is NULL, causing `max(0.0, None)` TypeError. Also guarded `structure_health` conformance/confidence against None
- **Breadcrumbs showed resolved issues** — `get_auto_captured_issues` query returned issues regardless of status. Resolved/wontfix issues appeared as active high-severity problems in bootstrap output. Added status filter
- **CLAUDE.md template gaps** — Added TRANSACTION CONTEXT FIELDS section and profile commands to CORE COMMANDS
- **Docstring accuracy** — Fixed phantom command references in ProfileImporter module docstring, wrong return type in `_apply_prune_rule`
- **project-search project name resolution** — Resolve project names before Qdrant lookup. Contributed by @kars85 (#65)
- **project-search docs default** — Include project docs in focused search and initialize docs ignore defaults. Contributed by @kars85 (#63)

## [1.6.11] - 2026-03-19

### Added
- **Brier score calibration** — Replaced MAE (improper scoring rule) with Brier score (strictly proper, Murphy 1973 decomposition). Reliability, resolution, and uncertainty components available via `calibration-report --brier` and auto-exported to `.breadcrumbs.yaml`
- **Statusline redesign** — New format: `[project] ⚡87% ↕70% │ 🎯1 ❓2 │ PRE 🔍65% │ K:70% C:75%`. Threshold indicator (↕%) shows Sentinel's required confidence color-coded by calibration quality. Phase state shows transaction boundary + work mode (🔍 investigating / ⚙ acting) with composite score. All elements color-coded
- **Calibration anti-gaming** — Specific vector gaps, suggested ranges, and calibration bias removed from AI-facing output. Replaced with directional-only feedback (overestimate/underestimate tendency lists). Full calibration data remains user-facing via calibration-report and statusline

### Fixed
- **Threshold direction inverted** — Dynamic thresholds previously LOWERED gates for good calibration (wrong). Now: miscalibration RAISES thresholds to compensate for unreliable self-assessment. Good calibration keeps thresholds at domain baselines
- **Sentinel static-only thresholds** — `sentinel-gate.py` now reads Brier-based dynamic thresholds instead of using hardcoded constants only
- **Project-embed retrieval on Windows** — Path resolution against project root (not forced under `docs/`), lazy Qdrant collection creation, Ollama retry with progressive prompt truncation, Python code_api skipped for non-Python repos, accurate success reporting. Contributed by @kars85 (#58)
- **Subagent detection** — Sentinel uses `active_work` instead of `active_session` for subagent detection

### Changed
- **Calibration thresholds in MCO config** — Domain baselines, safety ceilings, max inflation, min transactions, and lookback moved from hardcoded constants to `cascade_styles.yaml`. Each transaction profile (default, exploratory, rigorous, rapid, expert, novice) has profile-appropriate calibration settings
- **Statusline extension protocol** — Removed hardcoded CRM/workspace DB queries from core statusline. Uses `statusline_ext/*.json` protocol only
- **InstanceResolver migration** — 28 files migrated from scattered `session_resolver` imports to unified `InstanceResolver` API

## [1.6.10] - 2026-03-18

### Added
- **`InstanceResolver` class** - Unified API for all project/session/transaction resolution. Single import for hooks, CLI, sentinel, and statusline. Canonical in `session_resolver.py`, hook-side mirror in `project_resolver.py`. All existing module-level functions remain as backward-compatible aliases
- **Headless/interactive mode split** - `is_headless()` auto-detects containerized environments (no terminal identity) or via `EMPIRICA_HEADLESS=true`. In interactive mode, `active_work.json` (generic) is never consulted — `instance_projects` + `active_work_{uuid}` handle everything. Prevents stale cross-terminal pollution. Statusline silently exits in headless mode
- **DB-based file cleanup** - `cleanup_stale_active_work_files()` removes orphaned `active_work_{uuid}`, non-tmux `instance_projects`, and `active_session` files for sessions that have ended in the DB. Skips files with open transactions (compaction safety). Runs at session-init startup

### Fixed
- **Instance suffix mismatch** - Fixed 13 reader sites across 12 files that used raw `instance_id` (e.g., `x11:78940210`) for transaction file lookups instead of sanitized `_get_instance_suffix()` (e.g., `x11_78940210`). Caused file-not-found on non-tmux environments (X11, TTY)
- **Session-init not firing on resume** - `SessionStart` with type `resume` (continued conversation in new terminal) now triggers `session-init.py`, not just `post-compact.py`. Session-init detects existing sessions and updates anchor files without creating duplicates
- **Statusline wrong project after switch** - `project-switch` now updates `active_session_{suffix}` file so statusline reads correct project DB
- **`setup-claude-code` matchers** - Fixed SessionStart hook matchers generated by setup command: `compact` → post-compact, `startup|resume` → session-init (was `compact|resume` / `startup`)
- **Compact handoff filenames** - Standardized to use sanitized suffix (consistent with transaction files)

### Changed
- **Instance isolation docs** - Simplified ARCHITECTURE.md and README.md to reflect InstanceResolver, headless mode, and cleanup

## [1.6.7] - 2026-03-16

### Changed
- **Statusline extension protocol** - Replaced hardcoded CRM/workspace SQL queries with generic file-based extension system. External packages write JSON to `~/.empirica/statusline_ext/*.json`, core reads and displays. Keeps workspace-specific logic (engagements, EKG) in empirica-workspace

### Fixed
- **Instance isolation docs** - Corrected priority chain documentation across 5 files. Post-1.6.4 doc edits incorrectly described different priorities for tmux vs non-tmux. The code uses the same chain everywhere: `instance_projects` first (authoritative), `active_work` fallback. Removed non-existent "self-healing" claim from SESSION_RESOLVER_API.md

## [1.6.6] - 2026-03-16

### Fixed
- **Non-tmux multi-session isolation** - `instance_projects` is authoritative in all environments. `active_work_{claude_session_id}` is the per-session fallback. Fixes cross-session contamination when running 2+ Claude Code instances in same terminal
- **session-init ttyname regression** - Replaced dead `os.ttyname(stdin)` with `get_tty_key()` (PPID walking) in session-init hook. Hooks receive stdin as JSON pipe so ttyname always fails, preventing `claude_session_id` propagation to TTY session files. Regression from `f9d607ed` that reverted fix `07148f9b` (#39)
- **Statusline project resolution** - Unified project resolution priority: `instance_projects` first in all environments

### Changed
- **Instance isolation docs** - Documented stdin pipe constraint, priority chain, and full 4-iteration fix history for known issue 11.20

## [1.6.5] - 2026-03-16

### Fixed
- **Non-tmux instance IDs** - Instance IDs for X11 (`x11_N`) and macOS Terminal (`term_N`) now use underscores matching the file naming convention. Previously used colons (`x11:N`) causing filename mismatch — Sentinel couldn't find transaction files, failing open silently. See known issue 11.20
- **Statusline stdin redirect** - Removed `< /dev/null` from statusline command generated by `setup-claude-code`. Claude Code pipes session JSON to stdin; the redirect was eating it, preventing session context resolution (#56)

### Added
- **Subagent Epistemic Assessment spec** - Core architecture for persona decomposition, Brier scoring, and earned autonomy for subagents

### Changed
- **Instance isolation docs** - Updated architecture docs to reflect non-tmux support. CHANGELOG entries for 1.6.2-1.6.5 (were missing)

## [1.6.4] - 2026-03-13

### Added
- **Work type tagging** - `work_type` (code, infra, research, etc.) and `work_context` (greenfield, iteration, investigation, refactor) fields in PREFLIGHT. Scales evidence weights by source relevance
- **Goalless-work discipline nudges** - Sentinel nudges when praxic work happens without active goals
- **Epistemic transaction skill** - Full interactive planning skill for decomposing work into measured transactions

### Fixed
- **MCP audit findings** - 12 Tier 1 tools added, stale metadata cleaned up
- **3 CLI bug fixes** - Various command handler fixes from 1.6.4 release audit

## [1.6.3] - 2026-03-09

### Added
- **unknown-list command** - Browse and filter project unknowns from CLI
- **project-create/init bridge** - `--path` and `--project-id` flags for unified project setup
- **Qdrant rebuild** - `rebuild_qdrant_from_db()` for full Qdrant restoration from SQLite
- **Context-shift awareness** - Sentinel classifies solicited vs unsolicited user prompts

### Fixed
- **Embedding dimension validation** - Runtime check for qwen3-embedding:8b (4096d vs 0.6b 1024d), increased timeout
- **Calibration CWD bias** - PostTestCollector now uses project resolver chain, not CWD

## [1.6.2] - 2026-03-06

### Added
- **qwen3-embedding default** - Upgraded from nomic-embed-text (768d) to qwen3-embedding (1024d, MTEB 64.3)
- **code-embed command** - AST-based API extraction and embedding for semantic code search
- **Phase-weighted calibration** - Holistic calibration with insights loop and actionable feedback
- **Prose evidence collector** - Non-code grounded calibration for writing/documentation work
- **Project.yaml v2.0** - Universal project identity with enrichment fields
- **Bootstrap decisions** - Includes Qdrant-stored decisions in project-bootstrap

### Fixed
- **File permission hardening** - State files use 0o700 dirs, 0o600 files
- **Statusline trust** - Reads authoritative file sources without end_time filter
- **Phase-gated evidence** - Collectors respect noetic/praxic phase boundaries
- **Calibration Goodhart risk** - Removed calibration mechanics from system prompt
- **project-init corruption** - Removed resolver context writes that corrupted multi-project sessions

## [1.6.1] - 2026-03-04

### Added
- **Code quality evidence in grounded calibration** - 8th evidence source: ruff, radon, pyright metrics from session-changed files. Maps violations to epistemic vectors (ruff→clarity/coherence, radon→density/signal, pyright→know/do). Evidence coverage ~38%→~62%
- **docs-assess ignore patterns** - `[tool.empirica.docs-assess]` in pyproject.toml with `ignore_classes` and `ignore_paths` (fnmatch patterns). Fallback `.docsignore` file support. Prevents internal utility classes from polluting coverage metrics
- **API reference documentation** - 4 new API docs (config_profiles, data_infrastructure, context_budget, metrics) and 15+ class entries across existing docs. Coverage 71.8%→84.0%
- **Architecture docs** - Claude Code symbiosis layer documentation (MEMORY.md hot cache, task-goal bridge, session lifecycle hooks). Updated storage architecture with 5th tier
- **Elicitation hooks** (pending CC support) - Hooks for AskUserQuestion (true UQ measurement) and ElicitationResult (auto-log answers as findings/decisions)
- **Tool failure hook** (pending CC support) - Auto-log tool failures as dead-ends

### Fixed
- **Git notes in empty repos** - `postflight-submit` no longer hangs in repos without commits. Added HEAD existence check before git notes operations (#53)
- **Symbiosis hook code quality** - Fixed bare excepts, type annotations, operator type issues, and unicode chars in session-end-postflight, task-completed, and epistemic_summarizer hooks. Refactored format_epistemic_focus complexity (CC 27→13)
- **Grounded calibration coverage** - `UNGROUNDABLE_VECTORS` reduced from {engagement, coherence, density} to {engagement}. Coherence and density now grounded via code quality metrics

### Security
- **flask** ≥3.1.3 (CVE-2026-27205)
- **werkzeug** ≥3.1.6 (CVE-2026-27199)
- **pillow** ≥12.1.1 (CVE-2026-25990)

## [1.6.0] - 2026-03-01

### Added
- **Portable docs-assess** - `docs-assess` now works on any Python project via `ProjectConfig` auto-detection from `pyproject.toml`. Replaces 12+ hardcoded Empirica paths with config-driven references
- **Click CLI detection** - `docs-assess` discovers Click commands alongside existing argparse support. Tested on empirica (argparse, 197 commands) and empirica-outreach (Click, 6 commands)

### Fixed
- **Handler error returns** - `handle_docs_assess` and `handle_docs_explain` returned `None` on error (from `handle_cli_error()`) instead of exit code `1`, causing errors to be silently swallowed as success
- **Inconsistent arg access** - Unified both handlers to use `getattr()` pattern for `project_root` argument

## [1.5.9] - 2026-02-26

### Added
- **Sentinel File-Based Control** - Enable/disable Sentinel via `~/.empirica/sentinel_enabled` file flag, taking priority over `EMPIRICA_SENTINEL_LOOPING` env var. Dynamically settable without session restart
- **Transaction Planning Skill** - `/epistemic-transaction` skill gains interactive `plan-transactions` mode (Steps P1-P5): interview task, explore codebase, decompose into goals, generate YAML transaction plan with estimated vectors, execute

### Fixed
- **Sentinel Bypass** - System prompt contained bare `export EMPIRICA_SENTINEL_LOOPING=false` commands in code blocks. Claudes executed these, disabling Sentinel globally. Replaced with tables and "DO NOT execute" warnings across all templates and system prompts
- **SessionStart Matchers** - `setup-claude-code` generated invalid matchers (`new|fresh` and bare `compact`). Fixed to valid Claude Code values (`startup` and `compact|resume`). Updated all template files
- **Phantom Project ID** - `_get_project_id_from_local_db()` now reads `project.yaml` as authoritative source before falling back to `sessions.db`, preventing self-propagating phantom project IDs
- **Ghost Session Propagation** - Post-compact now detects and recovers from ghost sessions that don't exist in the database (documented as KNOWN_ISSUES 11.19)

### Removed
- **MirrorDriftMonitor** - Removed `empirica/core/drift/` module, `check-drift` CLI command, `check_drift` MCP tool, and all documentation references (-562 lines). Drift detection is handled by the grounded calibration pipeline (postflight → post-test → bayesian updates)

### Changed
- **README** - Removed empirica-crm from ecosystem projects, updated What's New to v1.5.9

## [1.5.8] - 2026-02-25

### Added
- **Semantic Layer Check** - `setup-claude-code` now detects Ollama (+ nomic-embed-text) and Qdrant availability, shows clear setup instructions if missing. Non-blocking — Empirica works without them but loses pattern injection, cross-session memory, and project-search
- **Workspace Context Plugin Hook** - Project-type-aware bootstrap via workspace context plugin hook
- **AST Dependency Graph** - Bootstrap uses AST dependency graph instead of file tree for smarter project context

### Fixed
- **Workspace DB Schema** (#51) - `workspace-init` and `project-list` failed on fresh installs because `global_projects` table DDL was missing. Added `ensure_workspace_schema()` with `CREATE TABLE IF NOT EXISTS` for all workspace tables
- **CLAUDE.md Overwrite** (#50) - `setup-claude-code` now writes Empirica prompt to separate file (`~/.claude/empirica-system-prompt.md`) with `@include` reference instead of overwriting user's CLAUDE.md. Preserves personal instructions, idempotent on re-run
- **Missing global_sessions Table** - Session registration silently skipped on fresh installs, breaking project-switch session continuity. Added schema creation in `ensure_workspace_schema()`
- **Missing entity_artifacts Table** - Entire entity cross-linking feature was non-functional; every artifact-log with `--entity-type` silently failed. Added schema creation
- **SessionStart Matcher** - Documented and fixed matcher bug for `new|fresh` vs `startup` trigger values (11.18)

### Changed
- **Taxonomy** - Added trajectory concept, defined transaction as noetic-praxic loop in documentation

## [1.5.7] - 2026-02-23

### Added
- **Qdrant Lazy Collections** - Collections created on first use instead of eagerly at init; `qdrant-status` and `qdrant-cleanup` commands for inventory and empty collection removal (#49)

### Fixed
- **Test Isolation** - `EMPIRICA_SESSION_DB` elevated to priority 0 in both `get_session_db_path()` and `resolve_session_db_path()`, preventing pytest subprocess tests from polluting the live database
- **Local Projects Table** - `project-switch` auto-populates `local_projects` table when switching to a project not yet registered locally (#48)

### Changed
- **Ref-Docs Coverage** - Updated CLI_ALIASES, ENVIRONMENT_VARIABLES, and MEMORY_MANAGEMENT_COMMANDS docs to cover qdrant commands and `EMPIRICA_SESSION_DB` priority 0 override

## [1.5.6] - 2026-02-22

### Added
- **Entity Scoping** - `--entity-type`, `--entity-id`, `--via` flags on all artifact commands (findings, unknowns, dead-ends, assumptions, decisions, mistakes, sources) for organization/contact/engagement scoping

### Fixed
- **Auto-Derive session_id** - `postflight-submit` and `preflight-submit` now auto-derive session_id from active transaction, matching other transaction commands
- **Postflight Project Resolution** - Uses canonical project resolution instead of CWD fallback that failed for non-CWD projects
- **Entity artifact_source** - Uses `trajectory_path` instead of `sessions.db` path for correct entity artifact sourcing
- **Sentinel INVESTIGATE Gaming** - Blocks gaming via new transaction creation to bypass investigate decisions

### Changed
- **Onboarding Rewrite** - Complete rewrite of `empirica onboard` with current capabilities: transactions, goals, noetic artifacts, dual-track calibration, Sentinel gate, JSON stdin mode
- **Documentation Overhaul** - Updated quickstart, CLI reference, troubleshooting, and end-user docs to current syntax; fixed broken links across 11+ files

## [1.5.5] - 2026-02-21

### Fixed
- **Schema Migration Ordering** (#44) - `CREATE INDEX` on `transaction_id` columns now runs after migrations that add the column, with `column_exists()` guards. Fixes crash on existing databases.
- **Qdrant File-Based Fallback Removed** (#45) - `_get_qdrant_client()` returns `None` when no server available instead of creating incompatible file-based storage. Added None guards to all 36 call sites across 10 modules.
- **project-embed Path Resolution** (#46) - Resolves `sessions.db` from `workspace.db` trajectory_path instead of CWD. Fixes 0-artifact embeddings for non-CWD projects.
- **transaction-adopt Same-Instance** (#44) - Skips file rename when `from_instance == to_instance` to prevent data loss.
- **Instance Isolation: Closed Transactions as Anchors** - Closed transactions persist until next PREFLIGHT, enabling post-compact project resolution after POSTFLIGHT closes the loop.
- **Lessons Storage Fallback** - `lessons/storage.py` now checks for running Qdrant server instead of falling back to file-based storage.

## [1.5.4] - 2026-02-20

### Added
- **Autonomy Calibration Loop** - Sentinel tracks `tool_call_count` per transaction, PREFLIGHT calculates `avg_turns` from past POSTFLIGHTs, nudges at adaptive 1x/1.5x/2x thresholds (informational, not forced)
- **Subagent Governance** - Delegated work counting in SubagentStop (transcript tool_use parsing), pre-spawn budget check in SubagentStart (advisory, fail-open), `maxTurns: 25` default ceiling on all 9 agent types
- **Subagent Transaction Exemption** - Subagents detected via `active_work` file absence bypass Sentinel gates (parent CHECK authorizes spawn)
- **Auto-PREFLIGHT on `project-switch`** - Conservative baseline vectors submitted automatically after project bootstrap
- **Lifecycle Cleanup** - Automatic cleanup of stale `active_work`, `compact_handoff`, and `instance_projects` files at session boundaries
- **Release Pipeline: empirica-mcp** - `release.py` now builds and publishes `empirica-mcp` to PyPI alongside the main package

### Changed
- **install.sh Consolidation** - Remote installer is now a thin wrapper that delegates to `empirica setup-claude-code --force`
- **Release Pipeline** - Added `chocolateyinstall.ps1` and `CANONICAL_CORE.md` version header to automated version sync

### Fixed
- **Stale Transaction Detection** - Uses status-only check (`status != "open"`) instead of time-based eviction that broke overnight sessions
- **Instance Resolution Priority** - `instance_projects` checked first, `active_work` used as fallback only for non-TMUX environments
- **Project Switch via Bash** - Resolves `instance_id` from TTY session file when switching projects
- **Subagent Session Close** - `db.end_session()` now runs unconditionally (fixes #43)

## [1.5.3] - 2026-02-18

### Added
- **`transaction-adopt` Command** - Recover orphaned transactions when session state is lost after crash or compaction
- **`assumption-log` Command** - Log unverified beliefs with confidence and domain scoping (CLI + MCP)
- **`decision-log` Command** - Record choice points with rationale and reversibility (CLI + MCP)
- **Automated Release Script** - `release.py` now covers all version locations: `__init__.py`, plugin.json, install.sh, CLAUDE.md templates, README badge, and more

### Changed
- **Statusline Delta Display** - Replaced per-vector delta figures with single summary symbols (green check, red warning, white delta) to prevent single-line overflow
- **Unified Versioning** - CLAUDE.md system prompt now uses the same version number as the package (no separate prompt versioning)

### Fixed
- **Session Resolver Validation** - Validates session_id against DB to prevent stale post-compact propagation
- **MCP `--transaction-id` Flag** - Removed broken flag that was never wired up; MCP tools now use active transaction resolution
- **Test Isolation** - Tests no longer interfere with live transactions
- **Project Switch** - Handles both `trajectory_path` formats (string and dict)

## [1.5.2] - 2026-02-14

### Added
- **Phase-Aware Calibration** - Separate noetic/praxic calibration tracks with earned autonomy thresholds
- **Know Grounding** - Artifact counts now ground the `know` vector in post-test verification
- **Artifact Lifecycle** - Automatic resolution of stale unknowns and assumptions between transactions
- **Per-Instance Sentinel Toggle** - Each tmux pane can independently enable/disable Sentinel
- **Short ID Goal Matching** - Goals can be referenced by prefix instead of full UUID
- **Stdin Auto-Detect** - `preflight-submit` and `postflight-submit` auto-detect `-` for stdin
- **Sentinel INVESTIGATE Gaming Prevention** - Blocks investigation loops when a new transaction hasn't been opened
- **macOS Qdrant Launchd** - Setup script with 65536 file descriptor limits (#27)

### Fixed
- **macOS Instance Isolation** - TTY resolution bug and hook/resolver asymmetry (#39)
- **Non-Git Projects** - Git operations skip silently instead of erroring (#30)
- **Qdrant Hash Fallback** - Vector dimensions now match configured provider (#34)
- **Project Switch Without TMUX_PANE** - Resolves instance_id from claude_session_id (#36)
- **Session-Authoritative Project ID** - Uses sessions.db as authoritative source
- **Sentinel CLI Whitelist** - Added missing command aliases

## [1.5.1] - 2026-02-13

### Added
- **Instance Isolation Docs** - Reorganized into use-case-specific guides:
  - `CLAUDE_CODE.md` - Hook input structure, automatic sessions
  - `MCP_AND_CLI.md` - TTY-based isolation for non-Claude-Code users
  - `ARCHITECTURE.md` - File taxonomy, resolution chains
  - Container guidance for automated workflows

### Fixed
- **Windows Compatibility** - Platform detection for file locking (PR #32)
- **Windows Unicode** - safe_print() wrapper for cp1252 console (PR #31)
- **Post-Compact Session Mismatch** - Use transaction's session_id for instance_projects
- **Instance Isolation Resilience** - Works when claude_session_id unavailable via Bash

### Closed
- Issue #28: Sentinel multi-window race condition (fixed by instance isolation)
- Issue #29: goals-create wrong DB after compact (fixed by unified resolver)

## [1.5.0] - 2026-01-31

### Added
- **Transaction-Session Continuity** - `read_active_transaction_full()` returns complete transaction data:
  - Session ID from PREFLIGHT is preserved across compaction boundaries
  - POSTFLIGHT auto-resolves session_id from transaction file, preventing stale summary errors

- **Shared Project Resolver** - Canonical `lib/project_resolver.py` for hooks:
  - All hooks now use single source of truth for project resolution
  - Priority chain: `active_work_{claude_session_id}` → `instance_projects/{instance_id}` → NO CWD fallback
  - Eliminates ~120 lines of duplicate resolution logic across sentinel, pre-compact, post-compact hooks

- **Context Budget Manager Events** - Bus integration for memory pressure:
  - `MEMORY_PRESSURE`, `CONTEXT_EVICTED`, `CONTEXT_INJECTED`, `PAGE_FAULT` events
  - Published to EpistemicBus for observer notification

### Changed
- **Sentinel Messages** - Opaque confidence feedback:
  - Blocking messages no longer reveal threshold values or current vectors
  - Prevents AI from gaming the gate by targeting specific numbers

- **Safe Pipe Targets** - Extended read-only whitelist:
  - Added `jq` to SAFE_PIPE_TARGETS for JSON processing during investigation

### Fixed
- **Project ID Consistency** - Session-authoritative project linkage:
  - Both `store_vectors()` and sentinel now use session's `project_id` (UUID) as source
  - Eliminates mismatch when PREFLIGHT stored hash but sentinel computed different hash
  - Fixes "Project context changed" false positive when Claude navigates directories

- **MCP Server Project Resolution** - Session-aware CLI routing:
  - `route_to_cli()` now resolves project path from `session_id` before falling back to CWD
  - Fixes "Project not found" errors when MCP runs from different directory than Claude
  - Noetic artifact logging (finding-log, unknown-log) now finds correct project DB

- **Unified Context Resolver** - Centralized session/transaction/project resolution:
  - Added `get_active_context()` and `update_active_context()` to session_resolver.py
  - Single source of truth for claude_session_id, empirica_session_id, transaction_id, project_path
  - PREFLIGHT now uses unified resolver to update context atomically
  - Sentinel prioritizes transaction file's session_id (survives compaction boundaries)
  - Fixes "loop closed" false positive when transactions span sessions

### Added (continued)
- **Epistemic Transactions** - First-class measurement windows with `transaction_id`:
  - PREFLIGHT→POSTFLIGHT cycles are now discrete measurement transactions
  - Multiple goals can exist within one transaction; one goal can span multiple transactions
  - Transaction boundaries defined by coherence of changes, not by goal boundaries
  - Adds `transaction_id` column to epistemic assessments for precise delta tracking

- **Ecosystem Topology** - Declarative project dependency graph:
  - `ecosystem.yaml` manifest at workspace root (32 projects, 18 dependency edges)
  - `EcosystemGraph` loader with transitive downstream/upstream traversal, impact analysis, validation
  - `empirica ecosystem-check` CLI with 5 modes: summary, file impact, project deps, role/tag filter, validate
  - `workspace-map` enriched with ecosystem role, type, and dependency data per repo

- **Multi-Agent Orchestration** - Parallel investigation with epistemic lineage:
  - `AttentionBudget` for parallel agent token allocation and monitoring
  - Agent generator with persona-derived Claude Code agents
  - `SubagentStart`/`SubagentStop` lifecycle hooks for epistemic lineage tracking
  - `parent_session_id` schema for sub-agent session hierarchy
  - No-match decomposition and emerged persona promotion

- **Blindspot Detection** - Epistemic gap identification:
  - Wired into CHECK phase for automatic blind spot surfacing
  - Integrated into MCP server tools

- **Epistemic Tool Router** - Vector-aware skill suggestion:
  - Routes to appropriate tools based on current epistemic state vectors
  - Integrated into MCP `skill_suggest` tool

- **On/Off Toggle** - On-the-record vs off-the-record tracking:
  - `/empirica on|off|status` command for Claude Code plugin
  - Controls sentinel enforcement and epistemic tracking

- **Eidetic Rehydration** - Full Qdrant restore via `project-embed`:
  - Rebuilds eidetic memory from cold storage to search layer

- **Auto-Init Sessions** - `--auto-init` flag on `session-create`:
  - Automatically initializes project if not yet tracked (closes #25)

- **Collaborator Config Sync** - `empirica-collab-sync.sh` script:
  - Syncs breadcrumbs, calibration, and plugin config between collaborators

### Changed
- **Schema Consolidation** - `session_*` tables consolidated into `project_*` as canonical source
- **Sentinel Path Resolution** - Refactored to use canonical `path_resolver` instead of custom logic
- **System Prompts v1.5.0** - CANONICAL_CORE and all model deltas updated:
  - Dual-track calibration (self-referential + grounded verification)
  - Post-test evidence collection triggers automatically on POSTFLIGHT
  - Trajectory tracking across transactions
- **Dynamic Calibration** - Sentinel now uses per-session bias corrections from `.breadcrumbs.yaml`
- **Vocabulary Taxonomy** - Formalized Empirica concept reference and taxonomy in SKILL.md v2.0.0

### Fixed
- **Sentinel Gate Failures** - Dynamic calibration + INVESTIGATE default when gate computation fails
- **Sentinel Loop Enforcement** - POSTFLIGHT now properly closes epistemic loops; warns on unclosed loops during project switch
- **Race Conditions** - Atomic writes with IMMEDIATE transaction isolation and single sentinel connection
- **Sub-Agent Session Hijacking** - Statusline filters active session by `ai_id`
- **Pre-Compact Branch Divergence** - Replaced auto-commit with `git stash` to prevent branch divergence
- **Goal Project Resolution** - `project_id` correctly resolved from session when saving goals
- **Agent Aggregate Merge** - Corrected kwarg name in agent merge call
- **Project Init Idempotency** - Prevents orphaned findings on re-initialization
- **Session Instance Isolation** - Respects `instance_id` in auto-close for multi-tmux-pane support
- **Finding Deduplication** - Deduplicates on insert; archives stale plans on session init
- **PREFLIGHT Pattern Retrieval** - Falls back to reasoning when Qdrant unavailable
- **Migration Safety** - Skips migration 021 if engagements table missing; adds client_projects to valid tables

### Security
- **Tiered Sentinel Permissions** - Replaced blanket `empirica` CLI whitelist with role-based permission tiers (read-only, write, admin)

## [1.4.2] - 2026-01-25

### Added
- **MCP Multi-Project Support** - MCP server now supports explicit workspace configuration:
  - `--workspace` argument sets project root for multi-project environments
  - Auto-detects from git root if `.empirica/` exists
  - Fallback to common development paths (`~/empirical-ai/empirica`, `~/empirica`)
  - Fixes sessions being created in global `~/.empirica/` instead of project `.empirica/`

### Fixed
- **Sentinel Gate: Empirica CLI** - Allow `empirica` CLI commands with heredocs (stdin JSON input)
- **Sentinel Gate: Stderr Redirects** - Allow safe stderr redirects (`2>/dev/null`, `2>&1`) while still blocking file writes

### Changed
- **Docs Clarification** - Claude Code users don't need MCP server; hooks provide full functionality
- **MCP Workspace Configuration** - Added section to CLAUDE_CODE_SETUP.md for multi-project setup

## [1.4.1] - 2026-01-23

### Added
- **Sentinel Safe Pipe Chains** - Noetic firewall now allows piped commands to safe read-only targets (head, tail, wc, grep, sort, etc.) while blocking dangerous pipes
- **Anti-Gaming Mitigations** - Sentinel detects rushed PREFLIGHT→CHECK transitions (<30s) without investigation evidence
- **Complete Plugin Installer** - One-line curl install for Claude Code integration with all components (hooks, statusline, CLAUDE.md, MCP server)

### Changed
- **Calibration Update** - 2496 observations, updated bias corrections (completion: +0.75, know: +0.17, uncertainty: -0.11)
- **Qdrant Optional** - Memory/semantic search features gracefully handle missing Qdrant; core epistemic transaction workflow uses SQLite only
- **MCP Tool Mappings** - Added missing tools (session_snapshot, goals_ready, goals_claim, investigate, vision_analyze, edit_with_confidence)
- **MCP Output Limiting** - Responses capped at 30K characters to prevent context overflow

### Fixed
- **Dual Session Creation** - Fixed orphaned plugin cache causing SessionStart hooks to run twice
- **Sentinel Messages** - Improved denial messages with specific vector values and guidance
- **Auto-Proceed CHECK** - High-confidence PREFLIGHT (know≥0.70, unc≤0.35) now auto-proceeds without explicit CHECK

## [1.4.0] - 2026-01-21

### Added
- **CHECK Snapshot Capture & Calibration Report** - New `calibration-report` command analyzes epistemic assessment patterns:
  ```bash
  empirica calibration-report --session-id <ID>
  ```
  - Captures epistemic state at CHECK gates for calibration analysis
  - Shows vector trajectories, bias corrections, and drift patterns
  - Enables data-driven calibration improvements

- **Query Blockers Command** - Surface goal-linked unknowns blocking progress:
  ```bash
  empirica query blockers --session-id <ID>
  ```
  - Shows unknowns linked to specific goals
  - Helps identify what's preventing goal completion

- **Statusline Project-Wide Unknowns** - Enhanced statusline shows:
  - Project-wide unknowns with goal-linked blockers
  - Instance-specific active_session files for tmux isolation
  - Upward search for `.empirica/` like git does for `.git/`

- **docs-assess Enhancements** - New flags for documentation assessment:
  - `--check-docstrings` - Check Python docstring coverage
  - `--turtle` - Spawn parallel assessment agents

- **Search-First Bootstrap Architecture** - Improved project-bootstrap:
  - Adaptive limits based on content availability
  - Eidetic and episodic memory in unified search
  - 'focused' mode (eidetic + episodic) is now the default

### Changed
- **Sentinel CHECK Age Expiry** - Now opt-in (not default). High-stakes environments can enable via flags
- **goals-list Refactoring** - Works without `--session-id`:
  - No filters: shows all active goals
  - `--session-id`: filter by session
  - `--ai-id`: filter by AI
  - `--completed`: show completed goals
  - Removed redundant `goals-list-all` command
- **Architecture Cleanup**:
  - Extracted earned autonomy system to separate project
  - Extracted MetricsRepository from session_database.py
  - Removed over-engineered noetic_eidetic module

### Fixed
- **Partial Session ID Resolution** - Workflow commands (preflight, check, postflight) now resolve partial UUIDs before database writes
- **Sentinel Timestamp Parsing** - Fixed bug causing CHECK gate failures
- **Statusline tmux Cross-Pane Bleeding** - Instance-specific active_session files prevent cross-pane contamination
- **Storage Dimension Hardcoding** - Now uses core embeddings provider instead of hardcoded 384-dim
- **assess-directory** - Excludes `__init__.py` files by default
- **Test Compatibility** - Updated tests for flat vector format

## [1.3.0] - 2026-01-09

### Added
- **Multi-Agent Epistemic Investigation** - Spawn parallel investigation agents with different personas to explore codebase corners:
  ```bash
  empirica agent-spawn --session-id <ID> --task "..." --turtle
  ```
  Features:
  - Automatic persona selection with `--turtle` flag
  - Parallel branch execution with POSTFLIGHT aggregation
  - Findings/unknowns automatically logged to parent session

- **Onboarding Projects** - Two complete mini-projects for learning Empirica workflows:
  - `api-explorer/` - Discovery exercise with intentionally incomplete API docs
  - `refactor-decision/` - Decision-making exercise with multiple valid approaches
  - Each includes WALKTHROUGH.md and SOLUTION.md for guided learning

### Changed
- **Documentation Accuracy Audit** - Comprehensive updates via multi-agent investigation:
  - DATABASE_SCHEMA_UNIFIED.md: Updated from 19 to 31 tables (added Session Breadcrumbs, Lessons System, Infrastructure sections)
  - MCP_SERVER_REFERENCE.md: Updated tool count from 40 to 57 tools
  - Added cross-references between Sentinel, epistemic transactions, and Noetic/Praxic docs
  - Added navigation table to CONFIGURATION_REFERENCE.md for end-users
  - Added cross-references to storage architecture and Qdrant integration docs

### Fixed
- **Version Consistency** - Synchronized version numbers across all package files:
  - pyproject.toml, empirica/__init__.py, empirica-mcp/pyproject.toml, chocolatey/empirica.nuspec

## [1.2.4] - 2026-01-06

### Added
- **project-switch Command** - New command for AI agents to switch between projects with clear context banner and automatic bootstrap loading.
  ```bash
  empirica project-switch <project-name-or-id>
  ```
  Features:
  - Resolves projects by name (case-insensitive) or UUID
  - Shows "you are here" context banner with project details
  - Automatically runs project-bootstrap for context loading
  - Displays project status (sessions, flow state, health)
  - Shows next steps (session-create, goals-ready)
  - JSON output support for programmatic use

### Fixed

1. **check-submit Vector Format Handling** - Added robust vector normalization to handle multiple input formats:
   - Flat dictionary: `{engagement: 0.85, know: 0.75, ...}`
   - Structured dictionary: `{foundation: {know, do, context}, comprehension: {...}, execution: {...}}`
   - Wrapped dictionary: `{vectors: {...}}`
   - JSON string inputs (AI-first mode)
   
   Fixes "Vectors must be a dictionary" errors when using structured transaction format.
   
2. **agent-spawn Persona Schema Validation** - Fixed validation errors for persona records:
   - PersonaManager.load_persona() now normalizes public_key to valid Ed25519 format (64 hex chars)
   - Auto-fills missing focus_domains with `['general']` for backward compatibility
   - Default persona in epistemic_agent.py now includes focus_domains
   
   Fixes "public_key 'scout_key_placeholder' invalid" and "focus_domains is required property" errors.
   
3. **Findings/Unknowns/Dead-ends Duplication** - Fixed duplicate breadcrumbs in project-bootstrap output:
   - Changed `UNION ALL` to `UNION` in 8 queries across breadcrumbs.py (get_project_findings, get_project_unknowns, get_project_dead_ends)
   - When scope='both', findings were written to both session_findings and project_findings tables
   - UNION automatically deduplicates while preserving dual-scope architecture

### Changed
- docs/PROJECT_SWITCHING_FOR_AIS.md: Updated status from "CRITICAL" to "IMPLEMENTED" with completed checklist items

### Tests
- Added 4 new tests for project-switch command (all passing)
- Total: 281 tests passing

## [1.2.3] - 2026-01-02

### Added
- **Epistemic Release Agent** (`empirica release-ready`) - Pre-release verification command with epistemic principles:
  - Version sync check across pyproject.toml, __init__.py, CLAUDE.md prompt version
  - Architecture turtle assessment on core/, cli/, data/ directories
  - PyPI package verification for empirica and empirica-mcp
  - Privacy/security scan for secrets, credentials, and dev files
  - Documentation completeness check (README, CHANGELOG, docs/)
  - Git status verification (branch, uncommitted changes, unpushed commits)
  - Respects .gitignore patterns - only flags items NOT covered by gitignore
  - Moon phase indicators (🌕🌔🌓🌒🌑) for visual status
  - JSON output for CI/automation (`--output json`)
  - Quick mode (`--quick`) to skip architecture assessment

### Fixed
- **Issue Resolution Bug** - `issue-resolve` command was filtering by session_id, preventing resolution of issues from different sessions. Removed session_id constraint from WHERE clause.
- **Goal Completion Bug** - `goals-complete` command returned success but never updated goal status in database. Added missing UPDATE statement to set status='completed'.
- **Ollama Auto-Detection** - Embeddings now auto-detect Ollama availability and use semantic embeddings when available, falling back to local hash when not.
- **Sentinel Auto-Enable** - Sentinel now auto-enables with default epistemic evaluator on module load, appearing in transaction responses.

### Changed
- Added `.beads/` and `*.pem` to .gitignore for security
- Reorganized .gitignore with "Security-sensitive files" section

## [1.1.3] - 2025-12-29

### Fixed
- **Flow State Display Slice Error** - Fixed TypeError in project-bootstrap command where flow_data was incorrectly treated as a list when it's actually a dictionary. Changed to properly access flow_metrics['flow_scores']. This was causing "slice(None, 5, None)" error messages in bootstrap output.
- **Missing Flow Metrics Components** - Added 'components' and 'recommendations' fields to flow metrics data structure. Components now show weighted breakdown of flow score factors (engagement, capability, clarity, etc.), and recommendations are generated from identify_flow_blockers().

### Added
- **Auto-Capture Logging Hooks** - Implemented true automatic error capture via Python's logging system:
  - Added `AutoCaptureLoggingHandler` class that hooks into logging.ERROR and logging.CRITICAL
  - Added `install_auto_capture_hooks()` function that installs both logging.Handler and sys.excepthook
  - Integrated into session creation - errors are now captured automatically during CLI execution
  - Captures context (logger name, module, function, line number) for better debugging
  - Non-blocking design - capture errors don't break the application
- Auto-capture now truly "auto" - no explicit calls needed, errors logged anywhere in codebase are captured

### Verified
- JSON output working correctly for project-bootstrap and session-snapshot commands
- Cross-project isolation confirmed with 15 projects
- Dynamic context loading via --depth parameter (minimal/moderate/full/auto)
- Session-optional commands working as documented
- Learning delta calculation accurate in session snapshots

## [1.1.2] - 2025-12-29

### Fixed
- **CRITICAL: Schema/API Mismatch in Epistemic Artifacts** - BreadcrumbRepository methods (log_finding, log_unknown, log_dead_end) expected schema columns that were missing from database definitions:
  - Added `subject TEXT` to project_findings table
  - Added `impact REAL DEFAULT 0.5` to project_findings table
  - Added `subject TEXT` to project_unknowns table
  - Added `impact REAL DEFAULT 0.5` to project_unknowns table
  - Added `subject TEXT` to project_dead_ends table
  - Added `impact REAL DEFAULT 0.5` to project_dead_ends table
- Impact: Users following system prompt documentation would get immediate SQLite errors when trying to use epistemic artifact tracking
- Testing: Verified with fresh project initialization - all epistemic tracking APIs now work correctly
- This fix enables proper meta-tracking of complex multi-channel projects (e.g., outreach campaigns)

## [1.1.1] - 2025-12-29

### Fixed
- **CRITICAL: CHECK GATE confidence threshold bug** - The CHECK command was ignoring explicit confidence values provided by AI agents and instead calculating confidence from uncertainty vectors (1.0 - uncertainty). This prevented the proper enforcement of the ≥0.70 confidence threshold for the epistemic transaction gate. Fixed by:
  - Extracting `explicit_confidence` from CHECK input config
  - Using explicit confidence in decision logic when provided
  - Making proceed/investigate decision based on confidence ≥ 0.70 threshold as per system design
  - Keeping drift and unknowns as secondary evidence validation
- **Impact**: All users now have a properly functioning CHECK GATE that respects stated confidence while validating against evidence

## [1.1.0] - 2025-12-28

### Added
- **Version 1.1.0 Release** - Fixed version mismatch issue where build artifacts contained old version
- **Build process improvement** - Added step to clean build/ and dist/ directories before building
- **Version consistency** - Updated all documentation and configuration files to reflect 1.1.0

## [1.0.6] - 2025-12-27

### Added
- **Epistemic Vector-Based Functional Self-Awareness Framework** - Updated CLI tagline to better reflect core focus
- **Documentation organization** - Moved development docs to archive, organized guides and reference docs
- **Version alignment** - Updated version across all documentation files

## [1.0.5] - 2025-12-22

### Added
- **workspace-overview command** - Epistemic project management dashboard
  - Shows epistemic health of all projects in workspace
  - Health scoring algorithm: `(know * 0.6) + ((1 - uncertainty) * 0.4) - (dead_end_ratio * 0.2)`
  - Color-coded health tiers: 🟢 high (≥0.7), 🟡 medium (0.5-0.7), 🔴 low (<0.5)
  - Sorting options: activity, knowledge, uncertainty, name
  - Filtering by project status: active, inactive, complete
  - JSON and dashboard output formats
  
- **workspace-map command** - Git repository discovery
  - Scans parent directory for git repositories
  - Shows which repos are tracked in Empirica
  - Displays epistemic health metrics for tracked projects
  - Suggests commands to track untracked repositories
  - Enables workspace-wide epistemic visibility

### Database
- `get_workspace_overview()` - Aggregates epistemic state across all projects
- `_get_workspace_stats()` - Calculates workspace-level statistics
- Health metrics include: know, uncertainty, findings, unknowns, dead ends

### Dogfooding
- Successfully used Empirica's full epistemic transaction workflow to build these features
- PREFLIGHT → CHECK → POSTFLIGHT assessments captured
- Learning deltas: know +0.13, completion +0.75, uncertainty -0.20
- BEADS integration tested with 3 issues tracked and closed

---

## [1.0.4] - 2025-12-22

### Added
- **Improved goals-list UX** - Shows helpful preview of 5 most recent goals when no session ID provided
- Preview includes goal ID, objective, session ID, completion percentage, and progress
- Better guidance for creating sessions and querying goals properly

### Changed
- **goals-list** command now provides more helpful error messages and previews instead of failing silently
- Goal/subtask query workflow improved with contextual hints

### Fixed
- Goal completion command now uses correct repository methods
- Project embed command properly handles goal/subtask metadata

### Refactored
- Moved `forgejo-plugin-empirica/` (125MB) to separate `empirica-dashboards` repo
- Moved `slides/` (72MB) to separate `empirica-web` repo  
- Moved `archive/` folder to `empirica-web` repo
- Reduced main package size by ~200MB for cleaner distribution

---

## [1.0.3] - 2025-12-19

### Added
- **`empirica project-init` command** - Interactive onboarding for new repositories
- **Per-project SEMANTIC_INDEX.yaml** - Each repo can have its own semantic documentation index
- **Project-level BEADS defaults** - Configure BEADS behavior per-project
- **CLI hints for BEADS** - Helpful tips after goal creation
- **Better error messages** - Install instructions when BEADS CLI not found
- **Configuration examples** - Added docs/examples/project.yaml.example

### Fixed
- **Database fragmentation (AI Amnesia)** - MCP server now uses repo-local database
- **refdoc-add UnboundLocalError** - Fixed variable usage before assignment
- **MCP server postflight regression** - Added missing resolve_session_id import
- **goals-ready schema bug** - Fixed vectors_json → individual columns
- **Project auto-detection** - Made --project-id optional with git remote URL auto-detection

### Changed
- **Project-session linking** - Added explicit --project-id flag to session-create
- **Project bootstrap** - Now auto-detects project from git remote
- **Documentation organization** - Moved session summaries to docs/development/

### Investigated
- **BEADS default behavior** - Kept opt-in (matches industry standards: Git LFS, npm, Python)
- Evidence: 5 major tools analyzed, high confidence decision (know=0.9, uncertainty=0.15)


## [1.0.0] - 2025-12-18

### Summary
First stable release of Empirica - genuine AI epistemic self-assessment framework.

### Added
- **MCO (Model-Centric Operations)**: Persona-aware configuration system
  - AI model profiles with bias corrections
  - Persona definitions (implementer, architect, researcher)
  - Cascade style configurations
- **Epistemic Transaction Workflow**: Complete epistemic assessment framework
  - PREFLIGHT: Initial epistemic state assessment
  - CHECK: Decision gate (proceed vs investigate)
  - POSTFLIGHT: Learning measurement and calibration
- **Unified Storage**: GitEnhancedReflexLogger for atomic writes
  - SQLite reflexes table integration
  - Git notes synchronization
  - JSON checkpoint export
- **Session Management**: Fast session create/resume
  - 97.5% token reduction via checkpoint loading
  - Uncertainty-driven bootstrap (scales with AI uncertainty)
- **Project Bootstrap**: Dynamic context loading
  - Recent findings, unknowns, mistakes
  - Dead ends (avoid repeated failures)
  - Qdrant semantic search integration
- **Multi-AI Coordination**: Epistemic handoffs between agents
- **CLI Commands**:
  - `empirica session-create` - Start new session
  - `empirica preflight-submit` - Submit initial assessment
  - `empirica check` - Decision gate
  - `empirica postflight-submit` - Submit final assessment
  - `empirica checkpoint-load` - Resume session
  - `empirica project-bootstrap` - Load project context
- **MCP Server**: Full integration with Claude Code and other MCP clients
- **Documentation**: Comprehensive production docs
  - Installation guides (all platforms)
  - Quickstart tutorials
  - Architecture documentation
  - API reference

### Changed
- Centralized decision logic in `decision_utils.py`
- Removed heuristic drift detection (replaced with epistemic pattern analysis)
- Cleaned documentation structure (removed future visions from public repo)

### Fixed
- Session ID mismatch in goal tracking
- Bootstrap goal progress tracking
- JSON output format in project-bootstrap
- MCP server configuration

### Security
- API key handling in config validation
- Checkpoint signature verification
- Git notes integrity checks

## Version Guidelines

- **MAJOR** (x.0.0): Breaking changes, incompatible API changes
- **MINOR** (1.x.0): New features, backwards-compatible
- **PATCH** (1.0.x): Bug fixes, backwards-compatible

## Links

- [GitHub Repository](https://github.com/EmpiricaAI/empirica)
- [Documentation](https://github.com/EmpiricaAI/empirica/tree/main/docs)
- [Issue Tracker](https://github.com/EmpiricaAI/empirica/issues)
