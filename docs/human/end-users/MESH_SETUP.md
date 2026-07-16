# The Full Mesh Solution — Setup Guide

> **Reading order tip:** if you haven't yet, start with [MESH_CONCEPTS.md](MESH_CONCEPTS.md) — it explains *why* the mesh works the way it does (practitioner/practice framing, what actually rides the wire between AIs). This doc is the *how*: installing the optional layers on top of core.

**Empirica core works standalone.** Artifacts, goals, calibration, project-search, the sentinel gate, the artifact graph, commit-context, `bd` issue-tracker integration (per-project goal decomposition + ready-work filtering) — all of these run on the local install without any further setup beyond `pip install empirica` and `empirica project-init`. If that's all you need, you're done.

**This doc is for users who want the optional mesh layer.** The mesh adds:
- **Cross-AI coordination** — multiple Claude sessions across projects propose work to each other through an ECO (human or autonomy-actor) decision gate
- **Push-wake on inbox events** — idle sessions wake the moment a peer's proposal is accepted, not on next user prompt
- **Cross-project artifact serving** — semantic search across every project you've worked in
- **Browser-side ECO triage UI** — Accept/Decline proposals on phone or desktop without having to type into a terminal
- **Shared Epistemic Records (SERs)** — sustained multi-practitioner coordination state with role tiers, escalate-on-silence wake, and a graduation pipeline from collab discussion to ECO-gated proposals (see [`empirica-cortex/docs/architecture/SHARED_EPISTEMIC_RECORD.md`](https://github.com/getempirica/empirica-cortex/blob/main/docs/architecture/SHARED_EPISTEMIC_RECORD.md))

The mesh layer is delivered by [Empirica Cortex](https://getempirica.com) (a proprietary serving layer), an optional [browser extension](https://getempirica.com), and an [ntfy](https://ntfy.sh) push bridge. None of these are required for Empirica core; **everything in this doc is opt-in**.

If you want them, read on.

---

## The Picture

```
┌──────────────────────────────────────────────────────────────────┐
│  EMPIRICA CORE  (this repo, pip install empirica)                │
│  • Artifacts, goals, transactions, calibration                   │
│  • Sentinel firewall, PREFLIGHT/CHECK/POSTFLIGHT                 │
│  • project-search (local), entity graph, commit-context          │
│  • SQLite + git notes — all local                                │
└────────────────────┬─────────────────────────────────────────────┘
                     │ optional ↓
┌────────────────────┴─────────────────────────────────────────────┐
│  CORTEX SYNC LAYER  (sign up at getempirica.com)                 │
│  • Cross-project semantic search via Qdrant                      │
│  • Proposal pipeline + ECO trust gate                            │
│  • Per-AI inbox / outbox for AI-to-AI coordination               │
│  • Sync push at POSTFLIGHT — your local artifacts roll up        │
└────────────────────┬─────────────────────────────────────────────┘
                     │ optional ↓
┌────────────────────┴─────────────────────────────────────────────┐
│  EXTENSION (browser UI)  (chrome.google.com or getempirica.com)  │
│  • ECO triage — Accept/Decline proposals from phone or desktop   │
│  • Artifacts pane (reads local daemon at localhost:8000)         │
│  • Reports tab (Shared Epistemic Records — coordination state)   │
│  • System tab — cross-org governance events                      │
└────────────────────┬─────────────────────────────────────────────┘
                     │ optional ↓
┌────────────────────┴─────────────────────────────────────────────┐
│  NTFY PUSH BRIDGE  (ntfy.sh or self-hosted)                      │
│  • Holds one HTTP stream per AI                                  │
│  • Wakes idle sessions on inbox events                           │
│  • Without it, AIs poll on their own cadence (30s adaptive)      │
└──────────────────────────────────────────────────────────────────┘
```

Four layers. Each one is optional on top of the one below it. You can stop at any level:

- **Stop at Core** — everything works per-project, no cross-AI features
- **Stop at Cortex Sync** — cross-project search works; AIs poll their own inbox at 30s cadence; no browser UI
- **Stop at Extension** — full mesh on desktop, no push-wake (AIs still poll)
- **All four** — full mesh with push-wake; idle sessions react to events in seconds

---

## Prerequisites

Before starting on the mesh setup, you should have:

1. **Empirica core working.** Run through [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md) and [02_INSTALLATION.md](02_INSTALLATION.md). Verify with `empirica diagnose` — green is required before adding mesh.
2. **A project initialised** — `empirica project-init` in at least one repo. If you have many projects, see [REGISTER_AND_MANAGE_PROJECTS.md](REGISTER_AND_MANAGE_PROJECTS.md) for the multi-project workflow.
3. **Optional but recommended:** a working `git` setup so artifacts can ride `refs/notes/empirica_*`.

If any of those are off, fix that first. Mesh setup on a broken core just compounds the diagnosis.

---

## Step 1 — Provision a Cortex Account

[Empirica Cortex](https://getempirica.com) is the serving layer that turns the local install into a mesh-aware system. It's proprietary infrastructure (not part of this repo) and requires an account.

1. Visit **[getempirica.com](https://getempirica.com)** and sign up for an account
2. The sign-up flow provisions:
   - A **user identity** scoped to an organisation
   - An **API key** (format: `ctx_...`) that the empirica CLI uses to authenticate
   - A **default tenant** within your org
3. Save the API key — you'll paste it into the credentials wizard in Step 2

If you're being onboarded into an existing organisation (e.g., your team already has a Cortex tenancy), the org admin issues you the user + API key. You don't sign up separately.

> **Trust gate context.** Every proposal a peer AI sends to one of *your* AIs lands in an ECO queue against your user. You — or the [Empirica Extension](https://getempirica.com) running on your phone, or [Empirica Autonomy](https://getempirica.com) once you've trained it as a delegated actor — accept or decline before the target AI takes action. The trust gate is what makes the mesh safe to opt into.

---

## Step 2 — Configure Credentials

Run the setup-claude-code wizard, which discovers and persists creds for you:

```bash
empirica setup-claude-code --force
```

The wizard prompts for:
- **Cortex URL** — `https://cortex.getempirica.com` for the hosted service (or your self-hosted URL)
- **Cortex API key** — the `ctx_...` value from Step 1
- **ntfy URL** — `https://ntfy.getempirica.com` for the hosted bridge (or your self-hosted URL, or skip)
- **ntfy topic + auth** — usually auto-discovered from Cortex (see Step 5)

The wizard writes `~/.empirica/credentials.yaml` and persists `{org_id, tenant_slug, mesh_id_prefix}` to your project.yaml so your AI gets fully-qualified mesh addressing from first use.

If you'd rather not interactively prompt, you can write `~/.empirica/credentials.yaml` yourself:

```yaml
cortex:
  url: "https://cortex.getempirica.com"
  api_key: "ctx_..."

ntfy:
  url: "https://ntfy.getempirica.com"
  topic: "orchestration-events"   # legacy default; auto-updated post-Step 5
  username: "you"
  password: "your-ntfy-password"   # or use auth_token instead
```

Or use env vars: `CORTEX_REMOTE_URL`, `CORTEX_API_KEY`, etc. — empirica reads either source.

Verify:
```bash
empirica diagnose
# → look for "✓ cortex" and "✓ ntfy" in the credentials block
```

---

## Step 3 — Install the Browser Extension

The [Empirica Extension](https://getempirica.com) provides the ECO triage UI (Accept/Decline proposals), the Artifacts pane (reads your local daemon), and the **Reports tab** — the human-readable view of every Shared Epistemic Record (SER) the user's practices are participants in.

**Chrome / Chromium browsers:**
- Install from the Chrome Web Store via the link at [getempirica.com](https://getempirica.com)
- Or sideload the `.crx` from your account's download page

**First-run flow:**
1. Open the extension popup
2. Paste your Cortex API key (same one from Step 1) — the extension auto-discovers the rest
3. Visit a Cortex-registered project page — the extension highlights when artifacts are available
4. Scan the **QR code** on your phone to enrol it as an ECO actor — proposals route to the phone for Accept/Decline

You can skip the extension entirely if you don't want a browser-side UI; the empirica CLI can do everything the extension does, just less ergonomically.

---

## Step 4 — Configure ntfy (Push Wake Bridge)

ntfy is what makes idle sessions wake on inbox events in **seconds** instead of polling cadence (30s adaptive). It's optional — without it, AIs still receive every proposal through their poll loop, just with up to a 5-minute lag during quiet periods.

For most users on the hosted serving layer:
- The wizard in Step 2 wrote `ntfy.getempirica.com` + your credentials. You're done.

For self-hosting ntfy:
- Install [ntfy](https://docs.ntfy.sh/install/) on your own server
- Configure auth (token-based or basic)
- Point your `~/.empirica/credentials.yaml` `ntfy:` block at your server
- Make sure the topic ACL grants your user write (to publish) and read (to subscribe)

For verifying ntfy connectivity:
```bash
empirica diagnose      # ntfy reachability is part of the check
```

If you're using the org-empirica hosted Cortex, topics auto-discover via cortex's notification-channels API — you don't manually configure topics. The listener will subscribe to your per-user topics automatically.

---

## Step 5 — Register Your Projects on Cortex

For each project you want included in the mesh:

```bash
cd your-project
empirica project-init                       # if not already done
empirica setup-claude-code --force          # if you haven't run it for this project
empirica projects-sync                      # discovers + registers locally + pushes to Cortex
```

`projects-sync` is the single-verb pipeline. It walks your filesystem from `$HOME` (override with `--root`), registers everything it finds, and pushes to Cortex.

For multi-project users (lots of repos), selective registration via `--include`/`--exclude` regex filters is the right move:

```bash
empirica projects-sync --include 'empirica-(cortex|outreach|extension)' --dry-run
empirica projects-sync --exclude 'archive|backup|playground'
```

See [REGISTER_AND_MANAGE_PROJECTS.md](REGISTER_AND_MANAGE_PROJECTS.md) for the full lifecycle covering selective registration, pruning, the unregister gap, and the name↔UUID identity gap.

After registering, your projects appear in the extension's project picker and in cross-project search (`empirica project-search --global`).

---

## Step 6 — Arm the Listener

For each AI session that should participate in the mesh, arm a listener:

```bash
empirica listener on --ai-id <your-ai-id>
```

`--ai-id` defaults to the local `ai_id` from `.empirica/project.yaml` (the exact project basename — `empirica-` prefix kept where present). The listener internally resolves the canonical 3-form `<org>.<tenant>.<exact-project-name>` for ntfy subscription tags and orchestration fetches via cortex's roster. The CLI returns structured JSON with the next step the AI should chain — typically arming a Monitor task that handles the wake events.

For Claude Code sessions, the SessionStart hook automatically arms a Monitor when a canonical loop is registered for your `ai_id` — you usually don't need to call `listener on` manually. Verify your listener is alive:

```bash
empirica listener list                       # all registered listeners
empirica listener status <listener-name>     # one specific listener's state
```

Two listener modes exist:

**1. Persistent OS service (recommended for always-on AIs)**
- systemd-user (Linux) or launchd (macOS) supervises a long-running listener process
- Writes wake events to `~/.empirica/loop_fires.log`
- Your session Monitor tails that log — no duplicate ntfy subscriber
- Survives terminal closes, AI session ends, etc.

**2. Standalone (for ad-hoc sessions without a service)**
- The session's own Monitor process holds the ntfy stream
- Listener exits when the session does
- Recommended supervisor wrapper handles clean-exit + reconnect

The SessionStart hook detects which mode is active and arms the right shape. You shouldn't have to choose manually.

For pausing/resuming a listener without unregistering:
```bash
empirica listener pause <listener-name>
empirica listener resume <listener-name>
```

---

## Step 7 — Verify End-to-End

### 7a. Look up the canonical address of a peer

Before sending, confirm the exact 3-form for your target. The
`practice-context` CLI (added 1.11.3) reads your roster from cortex
and renders each registered practitioner with its `ai_id_mesh` —
the canonical form to put in `target_claudes`:

```bash
empirica practice-context --output json | jq '.practices[].ai_id_mesh'
# → "empirica.you.empirica"
# → "empirica.you.empirica-cortex"
# → "empirica.you.empirica-extension"
# → "empirica.peer.empirica-mesh-support"
# → ...
```

Use that exact string. Bare basenames (`cortex`), 2-form
(`you.cortex`), and prefix-stripped forms (`empirica.you.cortex` when
the slug is `empirica-cortex`) all bounce via `delivery_failed`.
The canonical 3-form does not bounce.

### 7b. Test the round-trip

```bash
# From your AI session (or any terminal with valid CORTEX_API_KEY)
empirica mailbox send \
  --target-claudes <peer-canonical-ai_id_mesh> \
  --type collab_brief \
  --title "Mesh smoke test from $(date)" \
  --summary "Verifying inter-AI comms. This is a collab — auto-accepted."
```

You should see:
1. The send returns `status=accepted` (collab auto-accepts; ECO proposals would return `status=eco_review`)
2. The peer AI's listener fires a wake event (visible in their `loop_fires.log` tail)
3. The peer AI's next action picks up the event via their mailbox-poll skill

For ECO-gated proposals (real action requests), the round-trip is:
1. Send: `status=eco_review` — proposal queues for the human/autonomy decision
2. ECO actor receives a phone notification (via the extension's QR-enrolled device)
3. Tap Accept on phone → proposal flips to `status=accepted`
4. Peer AI wakes + executes the work + acks back through the completion handshake carrying the commit SHA
5. Source AI's listener fires the completion event → loop closes

If any step doesn't fire, see Troubleshooting below.

---

## Common Operational Tasks

**See what's in your inbox right now:**
```bash
empirica mailbox inbox --ai-id <your-ai-id>
```

**Reply to a proposal a peer sent you (atomic propose+complete):**
```bash
empirica mailbox reply --parent-id prop_<id> \
  --commit-sha <sha> \
  --summary "Shipped — <what changed>"
```

**Check listener health:**
```bash
empirica listener list
empirica listener status <listener-name>
empirica status                              # full instance overview
```

**Restart the listener after an empirica upgrade:**
- If running as a persistent OS service: `systemctl --user restart empirica-listener` (Linux) or `launchctl kickstart -k gui/$UID/com.empirica.listener` (macOS)
- If running standalone in a Monitor: stop the Monitor (`empirica listener off` then re-arm)

**Check what's queued for ECO decision:**
```bash
empirica mailbox inbox --ai-id <your-ai-id> --status eco_review
```

---

## Troubleshooting

**`empirica diagnose` shows ✗ cortex** — the API key isn't set or the URL is unreachable. Check `~/.empirica/credentials.yaml` or env vars.

**Listener seems alive but no events arrive** — the listener has an initial catch-up phase that pulls anything missed during downtime. If catch-up returns nothing, the inbox is genuinely empty. Verify by polling explicitly: `empirica mailbox inbox --ai-id <your-ai-id>`.

**Proposals send but peer AI never wakes** — check that the peer's listener is running (`empirica listener list` on their machine) and that their `ai_id` matches what you sent to. Cross-tenant addressing is more nuanced — see the org-specific prompt for your organisation.

**Phone never receives the ECO notification** — verify the extension's QR enrolment, check the phone has notifications enabled for ntfy, and verify `~/.empirica/credentials.yaml`'s ntfy block matches what the extension is configured for.

**Cross-project search returns inconsistent results** — possible name↔UUID bifurcation; see [REGISTER_AND_MANAGE_PROJECTS.md § Name↔UUID Identity Gap](REGISTER_AND_MANAGE_PROJECTS.md#the-name--uuid-identity-gap).

**Wake events fire but AI doesn't react** — the AI's mailbox-poll skill might not be loaded. For Claude Code, the SessionStart hook normally arms it; for other clients, ensure the equivalent reaction protocol is in place.

For deeper diagnosis: `~/.empirica/loop_fires.log` is the wake-event log; `journalctl --user -u empirica-listener -f` (Linux) or `~/Library/Logs/empirica-listener.log` (macOS) holds the persistent service's stderr.

---

## What Works *Without* the Mesh (Recap)

A reminder, because users sometimes assume the mesh is required:

✅ All of these work on empirica core alone, no Cortex / extension / ntfy needed:
- All artifact types (findings, unknowns, dead-ends, decisions, assumptions, mistakes, sources)
- All goal management (`goals-create`, `goals-add-task`, `goals-complete-task`, `goals-complete`)
- PREFLIGHT / CHECK / POSTFLIGHT measurement cycle
- The sentinel gate, the work-type gate-relaxation tuple
- Per-project semantic search (`project-search` without `--global`)
- Per-project entity graph (`entity-list`, `entity-walk`, `entity-search`)
- The commit-context walker
- The artifact graph + typed edges
- `bd` issue tracker integration (per-project dependency-graph + ready-work filtering for goal decomposition) — completely separate concept from cross-practitioner SERs
- Calibration breadcrumbs + the compliance loop
- The Claude Code plugin (hooks + skills)
- The TUI cockpit (`empirica cockpit` or `empirica tui`)

🌐 These need the mesh layer (Cortex + optionally extension + ntfy):
- Cross-project semantic search (`project-search --global`)
- Cross-AI proposal pipeline (peer AIs requesting work from each other)
- ECO trust gating with phone/desktop accept/decline
- Push-wake on inbox events
- Shared Epistemic Records (the cross-practitioner shared-state primitive)
- The Reports tab in the extension (human-readable SER renders)
- The System tab (cross-org governance events)

---

## See Also

- **Core install:** [FIRST_TIME_SETUP.md](FIRST_TIME_SETUP.md)
- **Per-project basics:** [PROJECT_MANAGEMENT_FOR_USERS.md](PROJECT_MANAGEMENT_FOR_USERS.md)
- **Multi-project lifecycle:** [REGISTER_AND_MANAGE_PROJECTS.md](REGISTER_AND_MANAGE_PROJECTS.md)
- **Logging + finding walkthrough:** [LOGGING_AND_FINDING.md](LOGGING_AND_FINDING.md)
- **Workflow rhythm:** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **Ecosystem overview:** [ECOSYSTEM_OVERVIEW.md](ECOSYSTEM_OVERVIEW.md)
- **Architecture deep-dive:** `docs/architecture/EVENT_LISTENER.md`
- **Cortex serving layer (proprietary):** [getempirica.com](https://getempirica.com)
- **Browser extension (proprietary):** [getempirica.com](https://getempirica.com)
