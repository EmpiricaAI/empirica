# Upgrading to Empirica 1.11

This guide covers the 1.10.x → 1.11.0 jump. **There are no breaking changes** — the bump rolls up two months of patches (1.10.5 + 1.10.6) into a coherent minor along with a substantial documentation refresh that surfaces capabilities the patches added but didn't user-document at the time.

If you're already on 1.10.6, upgrading to 1.11.0 is `pip install --upgrade empirica` + `empirica setup-claude-code --force` with nothing to do post-upgrade. The headline value of 1.11.0 is the new docs.

If you're on 1.10.4 or earlier, you'll also pick up the intermediate patch content described in the [Highlights since 1.10.4](#highlights-since-1104) section below.

---

## Quick Upgrade

```bash
pip install --upgrade empirica empirica-mcp
empirica setup-claude-code --force          # Refresh hooks + plugin skills
empirica diagnose                            # Sanity check — green = ready
```

If you run a persistent listener as an OS service (systemd-user / launchd), restart it after the upgrade so it picks up the new code:

```bash
# Linux
systemctl --user restart empirica-listener

# macOS
launchctl kickstart -k gui/$UID/com.empirica.listener
```

The listener has a clean-exit reconnect contract — restart is the supported upgrade path.

---

## What's New in 1.11

Four entirely new user-facing docs, every one of them surfacing an existing capability that had been operational-only or skill-only previously:

### `MESH_SETUP.md` — comprehensive opt-in mesh setup
The full-stack walkthrough for users who want cross-AI coordination: provisioning Cortex, configuring credentials, installing the browser extension, configuring the ntfy push bridge, registering projects, arming listeners, end-to-end smoke test. Explicit framing throughout that empirica core works standalone — every layer of the mesh is opt-in. Includes a recap of what works **without** the mesh so users understand they can stop at any level.

→ [docs/human/end-users/MESH_SETUP.md](../human/end-users/MESH_SETUP.md)

### `PROJECT_LIFECYCLE.md` — multi-project narrative
The companion to `PROJECT_MANAGEMENT_FOR_USERS.md`. Covers the full discover → register → sync → prune → unregister story for users with N projects scattered across their filesystem: `projects-sync` as the single-verb master path, selective `--include`/`--exclude` regex filters, `--dry-run` patterns, the `--prune` flow, the **known cortex-side-unregister gap** (with current workarounds + queued design), and the **name↔UUID identity gap** for users provisioned through both CLI and Desktop clients.

→ [docs/human/end-users/PROJECT_LIFECYCLE.md](../human/end-users/PROJECT_LIFECYCLE.md)

### `LOGGING_AND_FINDING.md` — discovery-side walkthrough
A concrete OAuth2 worked example that threads the entire lifecycle (PREFLIGHT + goal + tasks + 5 artifact types + CHECK + completion + POSTFLIGHT) but spends the majority of its real estate on the **finding side**: semantic search (local + `--global`), the artifact graph + typed edges, entity discovery (`entity-list` / `entity-walk` / `entity-search`), the commit-context walker (single SHA, ranges, `--session`, `--only-with-artifacts`), and how to thread it all together a day or week later.

→ [docs/human/end-users/LOGGING_AND_FINDING.md](../human/end-users/LOGGING_AND_FINDING.md)

### Required-vs-Optional framing audit
Five existing user-facing docs got their mesh-vs-core framing sharpened (README, `MCP_INSTALLATION.md`, `FIRST_TIME_SETUP.md`, `ECOSYSTEM_OVERVIEW.md`, `PROJECT_MANAGEMENT_FOR_USERS.md`). The headline correction: `MCP_INSTALLATION.md`'s Tools Available table was listing cortex MCP tools as part of the `empirica-mcp` surface — fixed against the actual `empirica mcp-list-tools` output, plus a top-of-doc callout clarifying that **`empirica-mcp` and the Cortex MCP are two different servers** that can coexist under different names in a client config.

---

## Patch series after 1.11.0

If you're upgrading mid-stream and want a single-doc summary of what
each 1.11.x patch added on top of 1.11.0, here it is. Full details
remain in `CHANGELOG.md`.

- **1.11.1 (2026-06-01)** — `mesh status` and `mesh diagnose` now
  distinguish `RED "curl subscription dead"` from
  `YELLOW "rate-limited — curl absent during 30-min backoff"`. UX-only
  fix; no behavior change. Surfaced 90 minutes after 1.11.0 by
  mesh-support escalation when a listener entered intentional 429
  backoff and the previous status was misleading.
- **1.11.2 (2026-06-02)** — code-side completion of the bead v0 →
  SER migration. `bead` node type, the 4 v0 edges
  (`tracks`/`owned_by`/`about`/`worked_by`), the `_workflow_postflight`
  beads-sync group, and `bead_id`/`bridge_position` listener event
  fields all removed. `'blocked'` added to the goal status enum.
  `/cortex-mailbox-send` skill bead vocab residuals retired.
- **1.11.3 (2026-06-03)** — naming hygiene + MCP refresh + new
  `practice-context` CLI:
  - New `empirica practice-context` CLI for verifying canonical 3-form
    (`<org>.<tenant>.<project>`) addresses before mesh sends.
  - 13 mesh primitives added to `empirica-mcp` `TOOL_REGISTRY` (now
    70 tools total, up from 57). Desktop harnesses (Claude Desktop,
    Cursor, Gemini CLI, Codex) can now reach the full surface.
  - `requires: cortex` marker + 🌐 prefix in `mcp-list-tools` makes
    the empirica/cortex boundary visible (65 standalone, 4
    cortex-orchestrated).
  - 5 obsolete MCP CLI commands deleted
    (`mcp-start`/`stop`/`status`/`test`/`call` — targeted a dead path);
    `mcp-list-tools` rebuilt to read the live registry.
  - Internal sentinel + cache + canonical-git layers retired the
    `'claude-code'` hardcoded ai_id default in favor of the
    `InstanceResolver` chain — important for multi-practice setups
    where the same machine runs several ai_ids.
  - Listener tests no longer require cortex creds in CI (autouse mock).
  - New end-user doc: `docs/human/end-users/MCP_FOR_DESKTOP_HARNESSES.md`.

---

## Highlights since 1.10.4

If you skipped 1.10.5 + 1.10.6 patches and are jumping straight from 1.10.4, you pick up the rolled-up patch content. (If you've already been running 1.10.6 these are review only.)

### Bead v0 → Shared Epistemic Record (SER) — coordination concept relocated (1.10.5 ship → 1.11 retire)

**Short version:** the v0 `bead` artifact type that landed in 1.10.5 has been retired. Its replacement — the **Shared Epistemic Record (SER)** — lives at the cortex layer, not in the empirica artifact graph. If you're not running mesh, nothing changes. If you are, see below.

**What happened.** 1.10.5 shipped a `beads` table + a `bead` artifact type with `coordination_state` + 4 new edge relations (`owned_by` / `worked_by` / `tracks` / `about`) as the v0 design for cross-practitioner coordination. Three-way review across cortex / empirica / extension surfaced two problems with that shape:

1. It put cross-practitioner shared state inside per-project empirica DBs — but the only place every participating practice can read AND write is the cortex layer, not their individual project DBs
2. The word "bead" collided with `bd` (the external dependency-graph issue tracker that empirica has integrated since 1.0) — every reader had to disambiguate constantly

The retirement decision and new design landed in [`empirica-cortex/docs/architecture/SHARED_EPISTEMIC_RECORD.md`](https://github.com/getempirica/empirica-cortex/blob/main/docs/architecture/SHARED_EPISTEMIC_RECORD.md). The v0 design is preserved at the same repo under `docs/archive/BEAD_COORDINATION_RECORD_v0.md` for historical reference.

**What stays in empirica.** The `beads` table from migration 048 is left in place — non-destructive. Any v0 beads you emitted during the 1.10.5 window stay readable. As of 1.11.2 no current code path emits `bead` artifacts and the v0 edges (`tracks` / `owned_by` / `about` / `worked_by`) have been retired from `VALID_RELATIONS`. Cleanup of the unused table itself is deferred to a future release.

**What you do.**

| If you are… | Action |
|---|---|
| Not running mesh | Nothing. The bead concept was invisible to you in 1.10.5 and stays invisible |
| Running mesh, never emitted a v0 bead | Nothing. SER (Phase 1a + 1b live as of 2026-06-01) handles cross-practitioner coordination; the `/cortex-mailbox-send` skill (1.11.0+) directs your AIs through `payload.action='create_ser'` |
| Emitted v0 beads during 1.10.5 | The rows stay readable. Don't re-emit. SER replaces the concept at the cortex layer |
| Building against the v0 bead API (custom code) | Migrate to the cortex SER actions: `cortex_propose` with `payload.action='create_ser'` (LIVE), and the read endpoint `GET /v1/sers?ai_id=<canonical>` (LIVE). State transitions (`transition_ser`, `ser_ack`) + escalation tick still PENDING cortex-side |

**Why this matters for the 1.11 conceptual story.** SER is the cross-practitioner primitive; **goals** stay the per-practitioner work primitive. They live at different altitudes and don't compete. See [`MESH_CONCEPTS.md`](../human/end-users/MESH_CONCEPTS.md) for the full framing of why this split is intentional and what each layer carries.

### Phase B mesh — noetic / praxic primitive split (1.10.5)

The Cortex MCP layer split its single mesh primitive into two — one for noetic conversation (auto-accepted), one for praxic action requests (ECO-gated). The empirica skills + system prompt now route AI-to-AI sends through this split. **Backwards-compatible**: the older single-primitive form still works, but it's deprecated for the conversational case. AIs running the updated skill will pick the right primitive automatically.

If you've trained custom AI agents against the older API, expect them to keep working through 1.11; deprecation hard-cutover lands in a later release.

### Graduation discipline — AIs take lead on collab → proposal bumping (1.10.6 skill update)

The `/cortex-mailbox-send` skill's Flavor 3 gains a "Who graduates — the discipline" subsection. The behaviour change: when a collab thread converges on an actionable ask, the AI whose reply is most-converged emits the typed proposal directly instead of waiting for the human to scroll per-instance ECO queues. Trust-the-shared-intelligence + the ECO gate as the truth-teller (rejection on inflation lands on the inflating AI's calibration record).

This is a discipline update in the skill; nothing to install separately. Loaded AIs pick it up on next session start after `empirica setup-claude-code --force`.

### Edge metadata persistence fix (1.10.5)

A real bug in `log-artifacts` was silently dropping per-edge `metadata` JSON. Code path: `_wire_edges` called `_store_edge(db, from_id, to_id, relation)` without passing `edge.get('metadata')`, so payloads carrying e.g. `{"role": "required"}` on a `worked_by` edge landed with `metadata=NULL` in `artifact_edges`. Affected every artifact edge with metadata.

The fix stands on its own — `log-artifacts` now persists edge metadata correctly for all artifact relations. (The v0 bead use case that motivated the fix is retired; the underlying bug fix is independently useful for any artifact edge that carries metadata.)

### Listener stability cluster (1.10.5 + 1.10.6)

A series of operational fixes for users running the persistent listener:

- **Supervisor wrapper** (1.10.5) — standalone Monitor on hosts without systemd/launchd now auto-relaunches after clean exits (SIGTERM during reconnect, `ListenerUpgraded` on pip-version drift, etc.). The OS-supervisor mode is unchanged.
- **`work_type=remote-ops` SSH passthrough** (1.10.5) — the sentinel no longer deadlocks SSH-recon when the work-type is declared `remote-ops`. PREFLIGHT declaration IS the gate; calibration is `ungrounded_remote_ops` and self-assessment stands.
- **Listener drift bypass** (1.10.5) — `EMPIRICA_LISTENER_NO_DRIFT_EXIT` env var disables the upgrade-self-exit for non-supervised hosts that don't have automatic relaunch.
- **Wake-noise filter then removed** (1.10.5 added → 1.10.6 removed) — per cortex+extension contract: the per-message actionability flag was redundant + lossy. 1.10.6 drops both the field on `ProposalEvent` and the listener's `grep -v "actionability": "fyi"` filter. The tool split (collab vs propose) IS the actionability signal at the wire level.

### Lenient `ai_id` resolver (1.10.6)

Cortex MCP shipped a lenient alias-aware `ai_id` resolver, and empirica's `/cortex-mailbox-send` skill adopted the new convention: **canonical wire form is the full project slug** (e.g. `empirica-cortex`); the org-specific short alias (e.g. `cortex`) is a convenience that resolves server-side. Default to the canonical full slug for cross-org generality.

The empirica skill update is purely documentation — no behaviour change beyond what the cortex resolver enables.

---

## Action items for upgraders

- [ ] `pip install --upgrade empirica empirica-mcp`
- [ ] `empirica setup-claude-code --force` (refresh hooks + plugin skills)
- [ ] `empirica diagnose` — green = ready
- [ ] If you run a persistent listener, restart it (see Quick Upgrade above)
- [ ] **Mesh users who emitted v0 beads in the 1.10.5 window**: nothing to migrate — those rows stay readable, no further bead emissions happen, SER replaces the concept at the cortex layer (see [Bead v0 → SER](#bead-v0--shared-epistemic-record-ser--coordination-concept-relocated-1105-ship--111-retire))
- [ ] **Multi-project users**: skim [`PROJECT_LIFECYCLE.md`](../human/end-users/PROJECT_LIFECYCLE.md) — covers the new `projects-sync` master verb and the selective `--include`/`--exclude` filters
- [ ] **Mesh users**: skim [`MESH_SETUP.md`](../human/end-users/MESH_SETUP.md) — covers the post-1.10.6 listener arming pattern + the Contract 2 wake-noise simplification. New to the conceptual story: read [`MESH_CONCEPTS.md`](../human/end-users/MESH_CONCEPTS.md) first
- [ ] **New to the discovery side**: walk through [`LOGGING_AND_FINDING.md`](../human/end-users/LOGGING_AND_FINDING.md) — the OAuth2 worked example threads search + entity-walk + commit-context together

---

## Cross-references

- [CHANGELOG.md](../../CHANGELOG.md) — full release notes for 1.10.5 + 1.10.6 + 1.11.0
- [UPGRADE_TO_1.10.md](./UPGRADE_TO_1.10.md) — prior minor upgrade guide (covers the `subtask` → `task` rename)
- [`MESH_SETUP.md`](../human/end-users/MESH_SETUP.md) — full optional-mesh setup
- [`PROJECT_LIFECYCLE.md`](../human/end-users/PROJECT_LIFECYCLE.md) — multi-project narrative
- [`LOGGING_AND_FINDING.md`](../human/end-users/LOGGING_AND_FINDING.md) — discovery-side walkthrough
