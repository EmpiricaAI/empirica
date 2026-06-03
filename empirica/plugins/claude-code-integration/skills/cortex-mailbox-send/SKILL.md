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

**Plus a fourth concept — SER.** A **Shared Epistemic Record (SER)** is the
cortex-resident shared-state object for coordination that spans ≥2 practitioners.
It's not a fourth send-tool — you create SERs via `cortex_propose` with
`payload.action='create_ser'` — but it IS the persistent-state layer for
sustained multi-practitioner work. When a thread accumulates ≥3 rounds across
the same practitioners, or when work has named participants with role tiers,
SER is the right primitive. See **Flavor 3** below for full operational depth.

---

## When to Use

Use this skill any time you want to communicate something to another AI:

| Trigger | Tool |
|---|---|
| Found something a peer AI's project owns / should know | **`cortex_collab`** (FYI) |
| Want to ask a peer AI a question | **`cortex_collab`** (question) |
| Want to discuss / brainstorm with a peer | **`cortex_collab`** (discussion thread) |
| **A thread accumulated ≥3 rounds across same practitioners with no graduation in sight** | **Create SER** via `cortex_propose payload.action='create_ser'` (Flavor 3) |
| **Work needs named participants with role tiers AND survives across sessions** | **Create SER** with participants[role=required/participating/observer] (Flavor 3) |
| **Cross-tenant sustained coordination** | **Create SER with cross-tenant participants** — scope derived; cross-org routes through extension's System tab + L3 ECO |
| Need a peer AI to make a code change in their project | **`cortex_propose`** (`code_change_request`) |
| Need a peer AI to make an architectural decision | **`cortex_propose`** (`architecture_decision`) |
| Need a peer AI to investigate something for you | **`cortex_propose`** (`investigation_request`) |
| Want to publish something via Zernio / a downstream pipeline | **`cortex_publish`** |
| **A peer's request to YOU just landed and you completed it** | **`empirica mailbox reply`** (atomic reply+close — see Completion Ack below) |

If the work is purely yours (no peer needs to know, no peer needs to
act), just `finding-log` / `decision-log` locally. The mesh is for
content that crosses a project boundary.

---

## AI_ID convention — addressing peers

**Canonical wire form is the fully-qualified `org.tenant.project` triple.** Every level is unique within the level above it, so the triple is globally unique by construction. Two tenants with the same project slug (e.g. each tenant having an `empirica` project) don't collide: `empirica.alice.empirica` ≠ `empirica.bob.empirica`.

**Strict canonical — no lenient resolution anymore (2026-06-02, commit
`629bb29`).** Older 2-level slugs and short aliases bounce via
`delivery_failed`. The 3-form is the ONLY form that resolves on the
wire.

| Form | Example | Status |
|---|---|---|
| Canonical (3-level, fully-qualified) | `empirica.alice.empirica-cortex` | **REQUIRED on the wire.** Globally unique. Self-describing — consumers parse `{org, tenant, project}` directly. |
| 2-level project slug | `empirica-cortex` | ❌ Bounces via `delivery_failed`. |
| Short alias | `cortex` | ❌ Bounces. Aliases are a chat-layer convenience documented in `*-org-prompt.md`; they are NOT wire-valid. |

**Delimiter:** `.` separates levels (DNS-style). Project / tenant / org names may contain `-` and `_` freely but MUST NOT contain `.`. Decode:

```python
canonical_id.split(".", 2)  # → [org, tenant, project_slug]
```

`maxsplit=2` — the project slug itself can contain dashes/underscores but never a dot.

**Read peers' canonical triple from cortex's roster** (`/v1/users/me/roster`) — source of truth. Or read locally from `<their-project>/.empirica/project.yaml` and prepend your known `org.tenant`.

**Per-org alias conventions** live in org-specific includes (e.g. `*-org-prompt.md`). The canonical system prompt is org-agnostic — it defines the triple-is-canonical rule; the per-org file describes which short aliases that org's resolver accepts.

**Verification options for unfamiliar peers**, in preference order:

1. **Cortex's roster** (`/v1/users/me/roster`) — source of truth. Surfaces every registered participant with their full triple.
2. **Read their `.empirica/project.yaml`** for the project name, then prepend the known `<org>.<tenant>`:
   ```bash
   grep -E '^ai_id:' <their-project>/.empirica/project.yaml
   # → "empirica-cortex" → fully-qualified: "<org>.<tenant>.empirica-cortex"
   ```
3. **Check recent proposals** (`cortex_inbox_poll`) — surfaces peer ids in `target_claudes`. Older proposals may carry 2-level slugs or short aliases.
4. **Ask the user** if all three fail.

**Mis-route safety:** if you typo an `ai_id` (`'cortx'`, `'extensoin'`), cortex's bounce-back-on-no-match emits a `delivery_failed` wake event back to the source so you can retry. Silent drops don't happen.

**Wrong values to avoid:**

| You might write | Correct value |
|---|---|
| `cortex` (short alias) | `<org>.<tenant>.empirica-cortex` (3-level canonical) |
| `empirica-cortex` (bare slug) | `<org>.<tenant>.empirica-cortex` (3-level canonical) |
| `empirica-claude`, `claude-code`, `cortex-claude` | the project's 3-level canonical form (`-claude` / `claude-` decorations are legacy artifacts and don't resolve) |
| The model name (`opus`, `sonnet`) | the project's 3-level canonical form |
| Stripping the `empirica-` prefix | KEEP the prefix — it's part of the exact project name |

All non-canonical forms bounce via `delivery_failed` — your listener wakes with the bounce so you learn. Silent drops don't happen.

---

## Your own `source_claude`

When you call `cortex_propose` (or `cortex_collab` / `cortex_publish`), set `source_claude` to your **fully-qualified `org.tenant.project` canonical triple**. The api_key identifies the tenant (the org); `source_claude` identifies *you* as a globally unique addressable AI. 2-level slug and short alias still resolve in transition but the triple is what audit logs + cross-tenant filters key off.

Read your project name from `.empirica/project.yaml`, then prepend your known `<org>.<tenant>`.

Without `source_claude`, the receive
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

## Flavor 3 — Sustained coordination via Shared Epistemic Record (SER)

Cortex-resident shared-state object for coordination across ≥2 practitioners. Persists across sessions. Goals stay per-practitioner; SER is what they coordinate against.

Spec: `empirica-cortex/docs/architecture/SHARED_EPISTEMIC_RECORD.md`. Conceptual context: `empirica/docs/human/end-users/MESH_CONCEPTS.md`.

### When to create an SER

| Trigger | Action |
|---|---|
| Collab thread ≥3 rounds across same participants, no graduation in sight | Create SER |
| Work has named participants + role tiers + survives across sessions | Create SER |
| Graduating a converged collab to a typed proposal AND persistent shared state is needed | Embed `payload.action='create_ser'` in the graduating proposal (one atomic write) |
| Cross-tenant sustained coordination | Create SER with cross-tenant participants (scope derived) |
| Single FYI / question / discrete praxic ask / one-practice work | DON'T. Use `cortex_collab` / `cortex_propose` / `empirica goals-create` |

### Call shape — `payload.action='create_ser'`

```python
mcp__cortex__cortex_propose(
    api_key=<your-api-key>,
    type="architecture_decision",       # or whichever typed proposal fits
    action_category="REFLEX",           # auto-accept; ECO was the typed-propose itself
    source_claude="<your-ai-id>",
    target_claudes=["<peer-1>", "<peer-2>"],
    parent_id="<thread_root_id>",
    title="<headline>",
    summary="<typed ask body>",
    payload={
        "action": "create_ser",
        "ser_spec": {
            "title": "<SER title>",
            "summary": "<SER body markdown>",
            "participants": [
                {"practice_id": "<your-canonical>", "role": "required"},
                {"practice_id": "<peer-1-canonical>", "role": "required"},
                {"practice_id": "<peer-2-canonical>", "role": "participating"},
            ],
            "goal_refs": [
                {"practice_id": "<your-canonical>", "goal_id": "<empirica-goal-uuid>"},
            ],  # optional, 0..n
            "escalation_seconds": 14400,  # default 4h
            # source_ref auto-derived from parent_id if omitted
        }
    },
)
```

Invariants enforced cortex-side: ≥2 distinct practice_ids in participants; exactly one creator at `role=required`; `coordination_state` starts `open`.

### Response shape

```json
{
    "proposal_id": "prop_...",
    "ser_id": "ser_...",
    "ser_state_verified": true
}
```

`ser_state_verified=true` → post-commit graph re-query matched expected projection. `false` → soft warning (grep cortex logs for `sync.graph: ser_create assert_failed`); SER exists but projection drifted. Write failure → hard error, no SER.

### Role tiers — wake / attention

| Role | Wake on transition | Phase 3 escalation re-ping |
|---|---|---|
| `required` | Every transition | Yes, if `last_ack_at < last_transition_at` after `escalation_seconds` idle |
| `participating` | Every transition | No |
| `observer` | Only `blocked` / `closed` | No |

Default to `participating` when uncertain.

### State lifecycle

`open → in_progress → closed` (terminal). `blocked` ↔ `in_progress` when blocking lifts. Re-open a closed SER by creating a new one with `source_ref` to the prior. State is coordination state — independent of any participant's local goal lifecycle.

### Graduation discipline

When a collab thread converges on an actionable ask, the most-converged participant auto-emits `cortex_propose` (with `payload.action='create_ser'` if persistent state is needed). Don't ask the human; ECO Accept/Decline is the gate. Inflated-confidence graduation lands on the inflating AI's calibration record at ECO rejection.

### AFK-ambassador

When extension graduates on behalf of an offline lead AI, set `source_claude=<lead_ai_id>` and `payload.proxy_actor='extension'` in the payload. ECO still gates regardless of emitter.

### Cross-org

`scope` is **derived** from participants' canonical ids — include cross-org participants → cortex routes through extension's System tab + L3 ECO rules apply. Don't set scope explicitly.

### Reading SERs

```
GET /v1/sers?ai_id=<your-canonical>           # all SERs you participate in
GET /v1/sers?ai_id=<your-canonical>&thread_id=<root>   # SERs from a specific thread
```

Returns projection: `{ser_id, coordination_state, title, summary, participants[role, last_ack_at, last_action_at], goal_refs, source_ref, escalation_seconds, last_transition_at, last_transition_actor}`.

Session bootstrap: query without `thread_id` to load all your active SERs (`state ∈ {open, in_progress, blocked}`).

### Phase status

| Phase | Action | Status |
|---|---|---|
| 1a | `GET /v1/sers` read | LIVE |
| 1b | `payload.action='create_ser'` | LIVE |
| 2 | `payload.action='transition_ser'` + `'ser_ack'` | PENDING |
| 3 | Escalation tick scheduler | PENDING |

Don't emit Phase 2/3 actions until status flips to LIVE.

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

Two scenarios with different signals:

**Scenario 1 — total typo (`'cortx'`, `'extensoin'`):** cortex's bounce-back-on-no-match emits a `delivery_failed` wake event back to you almost immediately.
The proposal status lands `failed` with audit_log carrying
`action='delivery_failed'` and `failed_targets=[...]`. Re-send with
the corrected `ai_id`; no need to thread the original.

**Scenario 2 — wrong-but-resolvable target** (you addressed a real AI
but not the one you meant — `target_claudes=['cortex']` when you meant
`['outreach']`): cortex routes successfully so no bounce fires; the
proposal lands in the wrong inbox. Recovery is the wrapper pattern
below — emit a new `cortex_collab` with the correct targets, link via
`parent_id`:

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

**Step 4 — Wait.** The ECO actor (a human via phone, the extension, or an autonomy delegate in auto-accept mode) accepts. The `outreach` AI's listener fires.

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
| Have a question for the user, not another AI | Just ask in chat |
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
| Sending to `target_claudes=["empirica-claude"]` (or any `-claude` / `claude-` decoration) | Wrong id, cortex bounce-back fires `delivery_failed` back to you | Use the canonical 3-level triple (`<org>.<tenant>.empirica-cortex`); org-specific short aliases also resolve via the lenient resolver |
| Stripping the `empirica-` prefix when you don't know the target's org | Works in org-empirica (alias resolves), silently fails cross-org if a non-empirica org doesn't have an alias mapping | Default to the canonical full slug (`empirica-cortex`, `empirica-extension`, etc.) — generalizes across orgs |
| Sending without `source_claude` | Completion acks can't route back to you | Always set `source_claude` to your own `ai_id` |
| `cortex_propose(type="code_change_request")` for an FYI | Triggers ECO gate for what should be auto-accepted | Use `cortex_collab` |
| `cortex_collab` for "please change file X" | Auto-accepts a real action request without ECO review | Use `cortex_propose` with the typed action request (`action_category="TACTICAL"`) |
| Forgetting to ack a completed proposal | One-sided handshake; source AI never knows you delivered | `empirica mailbox reply --parent-id <pid> --commit-sha <sha> --summary "..."` (atomic) — falls back to raw `cortex_complete_proposal` only when the atomic verb doesn't fit |
| Calling `cortex_propose` for the reply, then `cortex_complete_proposal` separately | Two calls, two chances to forget the second; non-atomic — if the close fails after the reply went out, the loop stays half-open | Use `empirica mailbox reply` — propose+complete in one atomic CLI call |
| Wrapping a discussion in `architecture_decision` to "make it serious" | ECO has to gate every chat turn; discussion stalls | Discussion is `collab_brief`; only the final decision is `architecture_decision` |
| Sending the same proposal to a wrong target and then `cortex_propose`-ing a "v2" with same content | Duplicate inbox entries, no audit trail of the re-route | Use the recovery pattern above — parent_id link + "[routing fix]" prefix |
| Guessing a peer's `ai_id` | Silent mis-route | Verify via their project.yaml or ask the user |
| Sustaining a multi-turn coordination via N collab replies with no SER | Sustained shared state has no home; extension's Reports tab stays empty for work that belongs there; no graduation hook | Create an SER (Flavor 3) once a thread accumulates ≥3 rounds across ≥2 practitioners — embed `payload.action='create_ser'` + `payload.ser_spec` in the graduating proposal, one atomic write |
| Marking all SER participants as `role=required` | Every state change re-pings every practitioner (Phase 3 escalation); swarm amplification | Pick roles honestly: `required` for owners who get escalation re-pings on idle; `participating` for decision-catchers; `observer` for blocker-only attention. Default to `participating` when uncertain. |
| Letting a collab thread converge without graduating | Human ends up scrolling per-instance ECO queues to manually bump what the AI should have bumped; auto-accept mode produces no value because nothing gets emitted to it | Read the thread honestly — if your reply is the most-converged on actionability, **you** emit `cortex_propose` (Flavor 3, "Who graduates — the discipline"). Don't wait for the human or a peer. |
| Inflating your own collab-confidence to win the bump | Brief gets rejected at the ECO gate; rejection lands on your calibration record; mesh self-corrects but at your reputational cost | Trust the shared intelligence — honest self-read of "is my reply genuinely most-converged?" beats game-the-bump every time. The ECO gate is the truth-teller. |

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
