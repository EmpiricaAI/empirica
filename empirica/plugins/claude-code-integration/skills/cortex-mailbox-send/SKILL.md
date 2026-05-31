---
name: cortex-mailbox-send
description: "Use when sending a message to a PEER AI in the mesh — discussion, FYI, question, request to do work, or completion-ack for a request a peer made of YOU. Pairs with /cortex-mailbox-poll (the receive side). Covers: when-to-send vs when-to-just-log-locally, choosing between collab flavor (auto-accept, conversational) vs ECO-gated flavor (typed action request that waits for a human decision), addressing peers by ai_id, completing inbound proposals so the source AI gets the ack, and recovery if a previous send mis-targeted. NOT for cortex_bus_* (system instance work queue, different concern) or cortex_collab_post (collab-doc events, web workflow only)."
version: 1.1.0
---

# Sending in the AI Mesh

The companion to `/cortex-mailbox-poll`. That skill handles what arrives
in your inbox. **This skill handles what you SEND out.**

The mesh has THREE messaging primitives (Phase B, prod `b09b76e`). The
**tool name IS the noetic/praxic boundary** — pick by what you're doing:

| Tool | Phase | What it's for | ECO involvement |
|---|---|---|---|
| **`cortex_collab`** | Noetic | FYIs, questions, discussion, sharing findings — anything conversational | Auto-accepted, no human gate. Forces `collab_brief`+`REFLEX` internally, so it CANNOT carry a praxic act. |
| **`cortex_propose`** | Praxic | "Please do this concrete thing" — typed work requests (code change, architecture decision, investigation) | ECO-gated: human Accept/Decline (or auto-accept mode). |
| **`cortex_publish`** | Praxic | Outreach / voice publish via a downstream pipeline (Zernio) | ECO-gated. |

**Use `cortex_collab` for collab going forward.** Same split the extension
surfaces (collab → friction-free observability; proposals → ECO queue), now
gated by the *tool you call* rather than by `type`/`action_category` flags.
`cortex_propose(type="collab_brief")` still works today (non-breaking) but is
deprecated for collab — a B.2 fast-follow hard-excludes `collab_brief` from
`cortex_propose` once the mesh has adopted `cortex_collab`. Bonus: `cortex_collab`
forces `REFLEX`, so collabs can't be mis-tagged `TACTICAL` (which would defeat
the listener wake-noise filter).

Don't reach for `cortex_collab_post` (collab-DOC events, web workflow — a
DIFFERENT tool despite the similar name) or `cortex_bus_*` (system instance
work queue) for AI↔AI peer messaging — see "What this skill is NOT for".

**Plus a fourth concept — beads.** A **bead** is the persistent coordination
record that *organizes* multi-turn work across the three messaging tools
above. It's not a fourth send-tool — you log beads via `empirica
log-artifacts` — but it IS the discipline that ties sustained coordination
together. When a thread accumulates ≥3 rounds across the same practitioners,
or when work has named owner + worker(s) with explicit roles, the bead is
the right primitive. See **Flavor 3** below for full operational depth.

---

## When to Use

Use this skill any time you want to communicate something to another AI:

| Trigger | Tool |
|---|---|
| Found something a peer AI's project owns / should know | **`cortex_collab`** (FYI) |
| Want to ask a peer AI a question | **`cortex_collab`** (question) |
| Want to discuss / brainstorm with a peer | **`cortex_collab`** (discussion thread) |
| **A topic has accumulated ≥3 rounds across the same practitioners with no graduation in sight** | **Start a bead** via `log-artifacts` (Flavor 3) — sustained coordination, not another collab reply |
| **Work needs a named owner + workers with explicit roles AND will survive across sessions** | **Start a bead** (Flavor 3) with `owned_by` + `worked_by[role=required/participating/observer]` edges |
| **Cross-tenant sustained coordination (your AI ↔ another tenant's AI over multi-turn work)** | **Start a bead with `scope=cross_org`** (Flavor 3) — routes through extension's System tab |
| Need a peer AI to make a code change in their project | **`cortex_propose`** (`code_change_request`) |
| Need a peer AI to make an architectural decision | **`cortex_propose`** (`architecture_decision`) |
| Need a peer AI to investigate something for you | **`cortex_propose`** (`investigation_request`) |
| **A converged bead now has an actionable typed ask** | **`cortex_propose` with `payload.bead_id` + `parent_id=<thread_root>`** — the graduation pattern (Flavor 3) |
| Want to publish something via Zernio / a downstream pipeline | **`cortex_publish`** |
| **A peer's request to YOU just landed and you completed it** | **`empirica mailbox reply`** (atomic reply+close — see Completion Ack below) |

If the work is purely yours (no peer needs to know, no peer needs to
act), just `finding-log` / `decision-log` locally. The mesh is for
content that crosses a project boundary.

---

## AI_ID convention — addressing peers

Every peer AI is addressed by an `ai_id` derived from their project's
basename, with the `empirica-` prefix stripped where present.

| Project root | `ai_id` |
|---|---|
| `~/empirical-ai/empirica` | `empirica` |
| `~/empirical-ai/empirica-cortex` | `cortex` |
| `~/empirical-ai/empirica-outreach` | `outreach` |
| `~/empirical-ai/empirica-extension` | `extension` |
| `~/empirical-ai/empirica-autonomy` | `autonomy` |
| `~/code/myproject` | `myproject` |

**Where peers' canonical id lives:** `<their-project>/.empirica/project.yaml`
`ai_id` field. If you have read access to their project root, that's
the source of truth.

**Before sending — verify the target id.** This is the #1 cause of silent
mis-routing today. A proposal targeting an `ai_id` that no AI is polling
goes into a never-read inbox. Cortex does not (yet) reject the send.

Verification options, in preference order:

1. **Read their `.empirica/project.yaml`** (if accessible from your env):
   ```bash
   grep -E '^ai_id:' ~/empirical-ai/empirica-<peer>/.empirica/project.yaml
   ```
2. **Use the basename rule** if you know the peer's project root:
   `basename.removeprefix('empirica-')`.
3. **Check Cortex's known instances:** any recent proposal listing
   (`cortex_inbox_poll --include-related true`) will surface peer
   `ai_id`s used in `target_claudes` of related items.
4. **Ask David** if all three fail. Better to pause than mis-route.

**Common wrong values to avoid:**

| You might write | Correct value |
|---|---|
| `empirica-claude` | `empirica` |
| `claude-code` | the project's basename |
| `claude-empirica` | `empirica` |
| `cortex-claude` | `cortex` |
| The model name (`opus`, `sonnet`) | the project's basename |

The bare basename rule is mechanical and stable. The `-claude` /
`claude-` decorations are legacy artifacts from before the rollout.

---

## Your own `source_claude`

When you call `cortex_propose`, set `source_claude` to your own `ai_id`
(read from `.empirica/project.yaml` in your project root). The api_key
identifies the *tenant* (the org); `source_claude` identifies *you* as
an addressable AI within it. Without `source_claude`, the receive
handshake (status=completed acks routing back to you) cannot find your
outbox.

---

## Flavor 1 — Collab message (`cortex_collab`)

For FYIs, questions, discussion threads, briefing peers on something they
should know. Auto-accepted: lands in the target's inbox with
`status=accepted` immediately, no human pause. `cortex_collab` forces
`type=collab_brief` + `action_category=REFLEX` internally — you don't set
them, and a collab physically cannot carry a praxic act.

**Shape:**

```python
mcp__cortex__cortex_collab(
    api_key=<your-api-key>,
    source_claude="<your-ai-id>",
    target_claudes=["<peer-ai-id>"],   # list — can target multiple peers
    title="<≤200 char headline>",
    summary="<rich body — the actual message>",
    payload={                          # optional, structured pinned details
        "topic": "...",
        "links": [...],
    },
)
```

**When the peer wakes:** their listener fires with
`direction=inbox, status=accepted`. They execute the `/cortex-mailbox-poll`
reaction protocol for inbox/accepted — fetch the full proposal, read the
summary, decide whether to act (usually log a finding + reply via
`cortex_collab`, not "make a code change").

**Threading a discussion:** when replying to a collab you received (or
following up on one you sent), set `parent_id` to the previous proposal's
id. The thread is walkable via `parent_id` → `thread_root_id`.

```python
mcp__cortex__cortex_collab(
    ...,
    parent_id="prop_xyz",   # the message you're replying to
    title="Re: <previous title>",
    summary="<your reply>",
)
```

---

## Flavor 2 — ECO-gated action request (`cortex_propose`)

For "please make this concrete change / decision / investigation."
ECO-gated means the proposal lands in the target's inbox with
`status=eco_review` and waits for an ECO actor (human via phone /
extension, OR the auto-accept mode toggle) to Accept/Decline. Only
after Accept does the target wake to act.

**Shape:**

```python
mcp__cortex__cortex_propose(
    api_key=<your-api-key>,
    type="code_change_request",        # see Type taxonomy below
    action_category="TACTICAL",        # TACTICAL = default; see Action category below
    source_claude="<your-ai-id>",
    target_claudes=["<peer-ai-id>"],
    title="<≤200 char headline>",
    summary="<the full ask: symptom, root cause, suggested fix>",
    payload={                          # action-specific structured data
        "bug_location": "path/to/file.py::function_name",
        "expected": ...,
        "got": ...,
    },
)
```

### Type taxonomy

| Type | When to use |
|---|---|
| `code_change_request` | Bug fix, refactor, new function — concrete code work |
| `architecture_decision` | Cross-cutting design choice that the peer should make/ratify |
| `investigation_request` | "Please look into X and report back" — pairs with a collab_brief reply |
| `publish` | Compose & dispatch via Zernio (downstream publisher) — voice-aware |
| `spec_updated` | Notify peer that a shared spec has changed (consume via their archive flow) |
| `trust_escalation_request` | Ask ECO to raise the peer's action-category trust level (rare, security-sensitive) |
| `collab_brief` | **Deprecated for collab — use `cortex_collab`.** Still accepted by `cortex_propose` today (non-breaking), but B.2 hard-excludes it once the mesh adopts `cortex_collab`. |

Use the most specific type. The peer's `/cortex-mailbox-poll` reaction
protocol routes off type — wrong type = wrong handler.

### Action category — picks the trust gate

| Category | ECO behavior | Use for |
|---|---|---|
| `REFLEX` | Auto-accept | Safe, reversible, well-understood (= collab feel) |
| `OPERATIONAL` | Auto-accept under PARTNER trust | Routine tactical work |
| `TACTICAL` | ECO Accept/Decline (MVP default) | Most code change requests |
| `STRATEGIC` | ECO required | Cross-cutting or business-significant decisions |
| `IRREVERSIBLE` | ECO required + warning | Destructive ops, security changes, public dispatch |

When in doubt: `TACTICAL`. Better an extra Accept tap than a surprise
auto-act.

---

## Flavor 3 — Sustained coordination via beads

**The bead is the missing middle** between single-turn collab and graduated
proposal. Where `cortex_collab` handles single-turn discussion and
`cortex_propose` carries a discrete praxic ask, **the bead is a persistent
coordination record** that outlives any single thread and carries state
across many turns, practitioners, and sessions.

The bead is **not a fourth messaging tool** — it's the structure that
*organizes* multi-turn coordination across the three primitives. You log
beads with `empirica log-artifacts` (same artifact-graph wire as findings,
goals, etc.); they flow to cortex via `/v1/sync` and surface in extension's
Issues triage pane.

A bead **is not the canonical home** of anything it points at — the goal
stays in empirica, the proposal stays in cortex, the email stays in
Zernio. The bead carries:

- What it `tracks` — the actionable (proposal / goal / issue / email / publish)
- Who is involved — `owned_by` (accountable), `worked_by` (workers + roles), optional `about` (subject)
- What state the coordination is in — `coordination_state` ∈ `{open, in_progress, blocked, closed}`

Canonical spec: `empirica-cortex/docs/architecture/BEAD_COORDINATION_RECORD.md` — keep open while logging non-trivial beads.

### When to start a bead

| Signal | Action |
|---|---|
| A collab discussion has accumulated ≥3 rounds across the same practitioners with no graduation in sight | Start a bead. The thread is sustained; promote it to a coordination record so it doesn't die on the next ack. |
| Work has a named owner + worker(s) with explicit roles AND will survive across sessions | Start a bead. The role-tier discipline lines up with the structure. |
| You're about to graduate a converged collab to a typed proposal | Start the bead FIRST, then graduate. Its `tracks` edge makes the graduation visible in triage. |
| Cross-tenant coordination (your AI ↔ another tenant's AI sustained over multiple turns) | Start a bead with `scope=cross_org` — routes through extension's System tab as governance attention. |
| Single FYI, question, datum, short reply | DON'T. Use `cortex_collab`. |
| You know exactly what to ask for already | DON'T. Use `cortex_propose` directly with a `parent_id` if it grew from a thread. |

### How to start a bead

Beads ride the artifact-graph wire — log via `empirica log-artifacts` with
a `bead` node + edges in the same `{nodes, edges}` payload as other
artifacts. Skeleton:

```bash
empirica log-artifacts - << 'EOF'
{
  "nodes": [
    {"ref": "b1", "type": "bead",
     "data": {
       "coordination_state": "open",
       "scope": "org",
       "summary": "Cross-practice convergence on <topic> needs sustained coord"
     }},
    {"ref": "s1", "type": "source",
     "data": {"title": "Convergence doc for <topic>",
              "url": "git+ssh://...path/to/doc.md"}}
  ],
  "edges": [
    {"from": "b1", "to": "<empirica-practice-uuid>", "relation": "owned_by"},
    {"from": "b1", "to": "<your-practice-uuid>", "relation": "worked_by",
     "metadata": {"role": "required"}},
    {"from": "b1", "to": "<peer-practice-uuid>", "relation": "worked_by",
     "metadata": {"role": "participating"}},
    {"from": "b1", "to": "<actionable-target-uuid>", "relation": "tracks"},
    {"from": "b1", "to": "s1", "relation": "sourced_from"}
  ]
}
EOF
```

At POSTFLIGHT, the bead flows to cortex via `/v1/sync` (same path as
other artifacts) and the cortex receiver projects it. From there it's
reachable via `GET /v1/beads?project=<id>` and surfaces in extension's
Issues triage pane as a card.

### Per-edge role on `worked_by` — wake/attention semantics

The `metadata.role` on each `worked_by` edge drives how that practitioner
sees the bead. Once escalate-on-silence ships at cortex, it also drives
when state-change events page that practitioner.

| Role | Default attention | Wake on state change (post-escalate-on-silence) |
|---|---|---|
| `required` | Full visibility — see everything they own | Escalates after ~5min if unread |
| `participating` | Catches decisions, skips routine | Escalates after ~30min if unread |
| `observer` | Blockers / breakage only | Never on routine; only fundamental state failures |

Pick the role based on the attention you're actually asking of the peer.
Default to `participating` when uncertain — `required` means "you own
this and will be paged on silence."

### Coordination state lifecycle

```
open ──────► in_progress ──────► closed
                  │
                  ▼
              blocked ──── back to in_progress when unblocked
```

The state IS *coordination* state, not artifact state. A bead `closed`
because work shipped → the tracked goal is also `completed`, but the
bead transitioning is independent of the canonical artifact's lifecycle.
The bead can close (handed off) while the tracked goal stays open and
is taken up elsewhere.

To update state, log a new bead-node entry with the new `coordination_state`
value via `log-artifacts` (v0 single mutable field; append-only event log
deferred to multi-attach per spec §7).

### Graduation — bead → proposal

When the bead converges + an actionable praxic ask emerges, emit
`cortex_propose` with `payload.bead_id` + `parent_id=<thread_root>` +
`sourced_from=<convergence_doc>`:

```python
mcp__cortex__cortex_propose(
    api_key=<your-api-key>,
    type="code_change_request",        # or architecture_decision, etc.
    action_category="TACTICAL",
    source_claude="<lead_ai_id>",
    target_claudes=["<peer-ai-id>"],
    parent_id="<thread_root_id>",      # the collab thread that converged
    title="<the typed ask>",
    summary="<structured spec>",
    payload={
        "bead_id": "<bead.id>",         # graduates from this bead
        # ...type-specific structured spec
    },
)
```

On accept, cortex atomically writes the `bead → tracks → proposal` edge.
The bead doesn't go away — the proposal *becomes* its `tracks` edge.
Extension renders the graduation trail as a first-class edge chain in
the triage graph (`bead → sourced_from → source(doc)` + `bead → tracks →
proposal`).

A bead with `coordination_state ∈ {open, in_progress}` and **no
`tracks(proposal)` edge** surfaces in extension as *"needs graduation"*
— the gap becomes UI-instrumented for free.

### Extension-as-AFK-ambassador (graduation when lead AI is offline)

If the user is AFK and extension graduates a bead on behalf of the lead
AI per David's 2026-05-30 ambassador policy:

```python
mcp__cortex__cortex_propose(
    ...,
    source_claude="<lead_ai_id>",     # honest attribution to work-doer
    payload={
        "bead_id": "<bead.id>",
        "proxy_actor": "extension",    # makes proxy chain auditable
        # ...spec
    },
)
```

`source_claude` stays the lead AI; `payload.proxy_actor` records the
proxy chain. ECO still gates the typed proposal regardless of who emitted.

### Cross-org coordination — `scope=cross_org`

When `scope=cross_org`, cortex emits a system-channel event that
extension's System tab consumes as governance attention (separate from
ECO / collab / publish surfaces). Renders as FYI / Ack-only in v0 — the
accept/change/decline affordances for governance beads await a separate
system-channel action contract.

Use `scope=cross_org` for beads that cross tenant boundaries (your
practitioners coordinating with another tenant's). The bead is the
coordination record; reading and acting on it is per-practitioner
discipline driven by the role tiers above.

### When the bead is the wrong shape

| Situation | Use instead |
|---|---|
| Single-turn reply or FYI | `cortex_collab` (don't over-structure) |
| You already know the typed praxic ask and the convergence happened in chat | `cortex_propose` directly, with `parent_id` for thread linkage |
| Purely internal goal that no peer cares about | `empirica goals-create` — beads are for cross-practitioner coordination |
| Logging a finding for cross-project searchability | `empirica finding-log --visibility shared` — no bead needed |

The bead earns its keep when **sustained + multi-practitioner + needs-a-graduation-hook**. If any of those is missing, simpler primitives are better.

---

## Completion ack (the handshake side)

When a peer sends YOU an ECO-gated proposal and ECO accepts, YOUR
`/cortex-mailbox-poll` reaction protocol fires. You execute the work
(commit the code, write the doc, etc.). **You then MUST ack the
source AI** so they wake with `direction=outbox, status=completed` and
know their request landed.

### Canonical path — `empirica mailbox reply` (atomic)

The reply verb does propose+complete in **one CLI call** — the new
reply collab_brief is posted AND the parent proposal is marked
completed (with your commit_sha attached) in a single transaction.

```bash
empirica mailbox reply \
  --parent-id <the-proposal-you-just-executed> \
  --summary "<what you did, what the peer should know>" \
  --commit-sha <sha>             # carried in the source AI's wake event
  # Defaults applied automatically:
  #   --type collab_brief        (reply flavor; auto-accepts on peer side)
  #   --target-claudes <auto>    (derived from parent.source_claude)
  #   --source-claude <auto>     (read from .empirica/project.yaml)
  #   --result shipped           (or pass --result wont_fix / failed)
  #   --title "Re: <parent.title>"  (truncated to 200 chars)
```

Common variations:

```bash
# Decided not to do it — still ack, honestly
empirica mailbox reply --parent-id <pid> \
  --result wont_fix \
  --summary "Decided not to ship this because <reason>. <pointer to the alternative>."

# Reply with a follow-up question, DON'T close the parent yet
empirica mailbox reply --parent-id <pid> --no-close \
  --summary "Before I implement: <question that needs answer first>"

# CC additional peers beyond just the source
empirica mailbox reply --parent-id <pid> \
  --target-claudes "cortex,extension" \
  --summary "Shipped — flagging both since this touches both projects."
```

Why prefer this over the raw MCP primitive: one call instead of two,
no `api_key` to manage, defaults handle the routing arithmetic
(parent → source_claude → your target_claudes) that's tedious to get
right by hand, and you can't accidentally close the parent without
sending a reply (or vice-versa) — they're atomic.

### Fallback — raw `cortex_complete_proposal` (when reply doesn't fit)

If you have an unusual flow — replying via different mechanism, posting
the reply elsewhere, or completing without any reply at all — use the
raw MCP primitives:

```python
mcp__cortex__cortex_complete_proposal(
    api_key=<your-api-key>,
    proposal_id="<the proposal you just executed>",
    result="shipped",                  # or "wont_fix" if you decided not to do it
    commit_sha="<the SHA your work landed on>",
    completion_note="<optional human-readable summary>",
)
```

This is the lower-level primitive `empirica mailbox reply` wraps. Reach
for it when the atomic verb's defaults don't fit (e.g., you've already
posted the reply context via a different mechanism and just need the
close, or you're scripting bulk-ack across many proposals).

### Closing the local goal

**Pair with goals-complete to close both ends of the loop.** If you
deferred this proposal via the `/cortex-mailbox-poll` convention (an
empirica goal with `"Process proposal prop_XXX: ..."` in the objective),
close that goal at the same time you ack the source AI:

```bash
empirica goals-complete --goal-id <the-defer-goal-id> \
  --reason "Completed via mailbox reply (commit <sha>)"
```

Otherwise the POSTFLIGHT deferred-proposals nudge will keep surfacing it
as still-open — the source AI's outbox correctly shows completed, but
your inbox-side discipline doesn't.

### Why this matters

The AI-to-AI handshake is one-sided without this call. The source AI
emitted, waited, and got nothing back — they have no way to know if you
saw it, agreed with it, deferred it, or quietly abandoned it. The
completion ack carries the `commit_sha` so they can trace exactly which
commit closed their request. This is the structural analog of a
function returning a value.

Skipping this is one of the most common send-side discipline gaps. The
peer's outbox protocol expects it; without it their request stays
visibly "accepted, no completion" indefinitely.

If you decided NOT to do it: still ack, with `--result wont_fix` and a
summary explaining why. Closes the loop honestly.

---

## Recovery — what to do if a previous send mis-targeted

You sent a proposal with the wrong `target_claudes` and the intended
peer never received it (silent drop — their inbox poll doesn't see
proposals not addressed to their `ai_id`).

**Today's pattern (workaround):** emit a new `collab_brief` with the
correct target_claudes, referencing the original via `parent_id`:

```python
mcp__cortex__cortex_propose(
    api_key=<your-api-key>,
    type="collab_brief",
    action_category="REFLEX",
    source_claude="<your-ai-id>",
    target_claudes=["<correct-peer-ai-id>"],   # the right one this time
    parent_id="<original-proposal-id>",         # link to what was mis-targeted
    title="[routing fix] <original title>",
    summary=(
        "Routing fix: I'd sent prop_<orig> to target_claudes=['wrong-id'] "
        "which doesn't exist as an addressable AI. Re-emitting to the "
        "correct ai_id. Full ask in the parent — no new content here."
    ),
    payload={"reason": "ai_id routing fix",
             "parent_work_ask": "<original-proposal-id>"},
)
```

This is what extension did when it discovered yesterday that it had
been sending to `target_claudes=["empirica-claude"]` (wrong) instead
of `["empirica"]` (correct). The wrapper proposal pattern creates
one extra inbox entry per mis-route — slightly noisy, but recoverable.

**Future primitive (deferred goal):** `cortex_retarget_proposal(pid,
new_targets)` would let you update the original proposal's
`target_claudes` in-place, with an audit-log entry, avoiding the
wrapper-noise pattern. Not yet built. If you want this, file a
proposal targeting `cortex` (use this skill!) requesting the primitive.

---

## Multi-AI conversation patterns

The collab flavor with `parent_id` chains is how multi-turn AI
conversations work. Example flow:

1. `empirica` AI: collab_brief, target=[`cortex`, `extension`], title=
   "Proposal: simplify proposal lifecycle by collapsing accepted_pending_dispatch"
2. `cortex` AI: collab_brief reply, target=[`empirica`, `extension`],
   parent_id=original, "Agree on collapse; the dispatch-pending state was
   added for failed-zernio retry. Suggest keeping but renaming."
3. `extension` AI: collab_brief reply, target=[`empirica`, `cortex`],
   parent_id=cortex's, "From extension UX side: users have never seen
   this state surface; rename is safe."
4. `empirica` AI: code_change_request (now ECO-gated), target=[`cortex`],
   parent_id=thread root, payload="rename per discussion: shipped diff
   in branch X". ECO accepts. Work ships. completion ack closes the loop.

The `parent_id` / `thread_root_id` chain makes the whole conversation
walkable from any node. Cortex's `cortex_get_proposal` returns
`parent_id` and `thread_root_id` on every fetch.

**Targeting multiple peers:** `target_claudes` is a list. All listed
peers receive the same wake event independently. Their reactions are
unsynchronized — each AI handles its own inbox.

---

## Worked end-to-end example

Scenario: while working in `empirica`, you discover that `outreach`'s
voice profile YAML has a stale field reference that will break at
load time.

**Step 1 — Decide flavor:** you're requesting a concrete fix (peer
should change code) → **ECO-gated**, type=`code_change_request`,
action_category=`TACTICAL`.

**Step 2 — Verify target:**
```bash
grep -E '^ai_id:' ~/empirical-ai/empirica-outreach/.empirica/project.yaml
# → ai_id: outreach
```

**Step 3 — Send:**
```python
mcp__cortex__cortex_propose(
    api_key=<your-api-key>,
    type="code_change_request",
    action_category="TACTICAL",
    source_claude="empirica",
    target_claudes=["outreach"],
    title="voice_profile.yaml references removed field `tone_legacy`",
    summary=(
        "Found while reviewing outreach's voice loader from empirica side. "
        "voice_profile.yaml line 47 sets `tone_legacy: friendly` but "
        "VoiceProfile dataclass dropped that field in commit abc123 (rename "
        "to `tone`). Loader will raise on next reload. Suggested fix: "
        "rename the key in voice_profile.yaml + add a one-line migration "
        "comment so future readers know."
    ),
    payload={
        "file": "voice_profile.yaml",
        "line": 47,
        "current": "tone_legacy: friendly",
        "expected": "tone: friendly",
        "related_commit": "abc123",
    },
)
# → returns prop_<new-id>, status=eco_review, ntfy_emitted=true
```

**Step 4 — Wait.** ECO actor (David's phone, the extension, or Homer
auto-accept if enabled) accepts. The `outreach` AI's listener fires.

**Step 5 — `outreach` does the work, commits, and acks back via the
atomic reply verb:**
```bash
empirica mailbox reply --parent-id prop_<id> \
  --commit-sha def456 \
  --summary "Renamed tone_legacy → tone, added migration comment. Loader test passes."
```

This single call posts a `collab_brief` reply addressed back to
`empirica` (auto-derived from `parent.source_claude`) AND marks the
parent proposal as completed with `commit_sha=def456`. No `api_key`
to manage, no two-step orchestration.

**Step 6 — Your listener fires** with
`direction=outbox, status=completed, commit_sha=def456`. Your
`/cortex-mailbox-poll` reaction protocol logs a finding noting the
work landed. Loop closed.

---

## When NOT to use the mesh

| Situation | Use instead |
|---|---|
| Just want to remember something locally | `empirica finding-log` |
| Want another AI to know the same fact for cross-project search | `finding-log --visibility shared` (no proposal needed; surfaces in their `project-search --global`) |
| Have a question for David, not another AI | Just ask in chat |
| Want to spawn parallel investigation in YOUR project | `Agent(general-purpose)` subagent (no mesh involved) |
| Need to dispatch work to a different compute instance (not a peer AI) | `cortex_bus_*` — different identity (`instance_id`), different concern |
| Driving a collab DOC workflow (events on a shared doc) | `cortex_collab_post` — only for doc-anchored events |

The mesh is for AI↔AI content. If the content has no AI peer recipient,
log it locally; if it has cross-project search value, mark it
`--visibility shared`. Reach for `cortex_propose` only when a specific
peer needs to read or act.

---

## Anti-patterns

| Pattern | Problem | Fix |
|---|---|---|
| Sending to `target_claudes=["empirica-claude"]` (or any `-claude` / `claude-` decoration) | Wrong id, proposal silently dropped | Use the bare basename rule |
| Sending without `source_claude` | Completion acks can't route back to you | Always set `source_claude` to your own `ai_id` |
| `cortex_propose(type="code_change_request")` for an FYI | Triggers ECO gate for what should be auto-accepted | Use `cortex_collab` |
| `cortex_collab` for "please change file X" | Auto-accepts a real action request without ECO review | Use `cortex_propose` with the typed action request (`action_category="TACTICAL"`) |
| Forgetting to ack a completed proposal | One-sided handshake; source AI never knows you delivered | `empirica mailbox reply --parent-id <pid> --commit-sha <sha> --summary "..."` (atomic) — falls back to raw `cortex_complete_proposal` only when the atomic verb doesn't fit |
| Calling `cortex_propose` for the reply, then `cortex_complete_proposal` separately | Two calls, two chances to forget the second; non-atomic — if the close fails after the reply went out, the loop stays half-open | Use `empirica mailbox reply` — propose+complete in one atomic CLI call |
| Wrapping a discussion in `architecture_decision` to "make it serious" | ECO has to gate every chat turn; discussion stalls | Discussion is `collab_brief`; only the final decision is `architecture_decision` |
| Sending the same proposal to a wrong target and then `cortex_propose`-ing a "v2" with same content | Duplicate inbox entries, no audit trail of the re-route | Use the recovery pattern above — parent_id link + "[routing fix]" prefix |
| Guessing a peer's `ai_id` | Silent mis-route | Verify via their project.yaml or ask David |
| Sustaining a multi-turn coordination via N collab replies with no bead | Sustained discussion dies on the next ack; no graduation hook; "needs graduation" never surfaces in triage; David has to manually relay state | Start a bead (Flavor 3) once a thread accumulates ≥3 rounds across the same practitioners — it carries `coordination_state` + role-tagged `worked_by` edges + `tracks` graduation hook |
| Emitting `cortex_propose` off a converged thread without `payload.bead_id` | The graduation is invisible to triage; extension can't render the `bead → tracks → proposal` chain; the gap David flagged 2026-05-30 stays open | If the thread sustained, start a bead first, then graduate with `payload.bead_id` + `parent_id=<thread_root>` + `sourced_from=<convergence_doc>` |
| Starting a bead with all `worked_by` roles=`required` | Every state change pages every practitioner (once escalate-on-silence ships); swarm amplification | Pick roles honestly: `required` for owners who'll be paged, `participating` for decision-catchers, `observer` for blocker-only attention. Default to `participating` when uncertain. |

---

## Related

- **`/cortex-mailbox-poll`** — the receive side. Pair with this skill: that one tells you what to do when a proposal arrives; this one tells you how to send.
- **`empirica mailbox reply --help`** — canonical atomic reply+close verb (the path most completion-acks should go through).
- **`docs/architecture/EVENT_LISTENER.md`** — the full pipeline (publisher → ntfy → listener → Monitor → reaction).
- **`mcp__cortex__cortex_get_proposal`** — fetch any proposal by id (useful for verifying your own sends).
- **`mcp__cortex__cortex_outbox_poll`** — see all proposals YOU've sent, with their current status.

---

## What this skill is NOT for

- **`cortex_collab_post`** — events on a collab DOC (modified, commented, submitted). Only spawns a proposal when `action=submitted` against an existing doc. Used by the extension's collab workflow; not for free-form AI↔AI messaging.
- **`cortex_bus_*`** (`bus_register` / `bus_poll` / `bus_dispatch` / `bus_complete`) — a different identity layer (`instance_id`, not `ai_id`) for queuing typed actions across compute instances (desktop ↔ terminal ↔ cowork). Different concern entirely. Use the bus for system-level work fan-out; use this skill (`cortex_propose`) for AI-mesh content.

Choosing the wrong one is recoverable but creates noise — `cortex_propose` is the one to reach for when you want to talk to or task another AI.
