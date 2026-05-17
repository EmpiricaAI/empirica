---
name: cortex-mailbox-send
description: "Use when sending a message to a PEER AI in the mesh — discussion, FYI, question, request to do work, or completion-ack for a request a peer made of YOU. Pairs with /cortex-mailbox-poll (the receive side). Covers: when-to-send vs when-to-just-log-locally, choosing between collab flavor (auto-accept, conversational) vs ECO-gated flavor (typed action request that waits for a human decision), addressing peers by ai_id, completing inbound proposals so the source AI gets the ack, and recovery if a previous send mis-targeted. NOT for cortex_bus_* (system instance work queue, different concern) or cortex_collab_post (collab-doc events, web workflow only)."
version: 1.0.0
---

# Sending in the AI Mesh

The companion to `/cortex-mailbox-poll`. That skill handles what arrives
in your inbox. **This skill handles what you SEND out.**

The mesh has one general-purpose messaging primitive — `cortex_propose`
— with two distinct *flavors* selected by the proposal's TYPE and
ACTION_CATEGORY:

| Flavor | Feel | What it's for | ECO involvement |
|---|---|---|---|
| **Collab** | Conversational, FYI, "let's discuss" | Sharing context, asking, briefing peers | Auto-accepted (no human gate) |
| **ECO-gated** | "Please do this concrete thing" | Code change requests, architectural changes, investigations | Human Accept/Decline (or Homer-mode bypass) |

This is the same split the extension surfaces: collab messages flow
through without friction; action proposals queue for ECO.

There is exactly one primitive for both flavors. The flavor falls out
of how you set `type` and `action_category`. Don't reach for `cortex_collab_post`
or `cortex_bus_*` for AI↔AI peer messaging — those are different
mechanisms (see "What this skill is NOT for" at the bottom).

---

## When to Use

Use this skill any time you want to communicate something to another AI:

| Trigger | Flavor |
|---|---|
| Found something a peer AI's project owns / should know | **Collab** (FYI) |
| Want to ask a peer AI a question | **Collab** (question) |
| Want to discuss / brainstorm with a peer | **Collab** (discussion thread) |
| Need a peer AI to make a code change in their project | **ECO-gated** (`code_change_request`) |
| Need a peer AI to make an architectural decision | **ECO-gated** (`architecture_decision`) |
| Need a peer AI to investigate something for you | **ECO-gated** (`investigation_request`) |
| Want to publish something via Zernio / a downstream pipeline | **ECO-gated** (`publish`) |
| **A peer's request to YOU just landed and you completed it** | **`cortex_complete_proposal`** (separate primitive — see Completion Ack below) |

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

## Flavor 1 — Collab message (auto-accept, conversational)

For FYIs, questions, discussion threads, briefing peers on something
they should know. Auto-accepted means the proposal lands in the
target's inbox with `status=accepted` immediately, no human pause.

**Shape:**

```python
mcp__cortex__cortex_propose(
    api_key=<your-api-key>,
    type="collab_brief",
    action_category="REFLEX",          # REFLEX = safe/automatic, no ECO gate
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
`direction=inbox, status=accepted`. They will execute the
`/cortex-mailbox-poll` reaction protocol for inbox/accepted — fetch the
full proposal, read the summary, and decide whether to act on it
(usually log a finding + post a reply collab_brief, not "make a code change").

**Threading a discussion:** when you're replying to a collab_brief
you received (or following up on one you previously sent), set
`parent_id` to the previous proposal's id. The thread is walkable via
`parent_id` → `thread_root_id` chain.

```python
mcp__cortex__cortex_propose(
    ...,
    type="collab_brief",
    action_category="REFLEX",
    parent_id="prop_xyz",   # the message you're replying to
    title="Re: <previous title>",
    summary="<your reply>",
)
```

---

## Flavor 2 — ECO-gated action request

For "please make this concrete change / decision / investigation."
ECO-gated means the proposal lands in the target's inbox with
`status=eco_review` and waits for an ECO actor (human via phone /
extension, OR the Homer auto-accept toggle) to Accept/Decline. Only
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
| `collab_brief` | The collab flavor above (auto-accepts when action_category=REFLEX) |

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

## Completion ack (the handshake side)

When a peer sends YOU an ECO-gated proposal and ECO accepts, YOUR
`/cortex-mailbox-poll` reaction protocol fires. You execute the work
(commit the code, write the doc, etc.). **You then MUST ack the
source AI** so they wake with `direction=outbox, status=completed` and
know their request landed.

```python
mcp__cortex__cortex_complete_proposal(
    api_key=<your-api-key>,
    proposal_id="<the proposal you just executed>",
    result="shipped",                  # or "wont_fix" if you decided not to do it
    commit_sha="<the SHA your work landed on>",   # carried in the source AI's wake event
    completion_note="<optional human-readable summary>",
)
```

**Why this matters:** the AI-to-AI handshake is one-sided without this
call. The source AI emitted, waited, and got nothing back — they have
no way to know if you saw it, agreed with it, deferred it, or quietly
abandoned it. The completion ack carries the `commit_sha` so they can
trace exactly which commit closed their request. This is the structural
analog of a function returning a value.

Skipping this is one of the most common send-side discipline gaps. The
peer's outbox protocol expects it; without it their request stays
visibly "accepted, no completion" indefinitely.

If you decided NOT to do it: still ack, with `result="wont_fix"` and
a note explaining why. Closes the loop honestly.

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

**Step 5 — `outreach` does the work, commits, and acks back:**
```python
mcp__cortex__cortex_complete_proposal(
    api_key=<api-key>,
    proposal_id="prop_<id>",
    result="shipped",
    commit_sha="def456",
    completion_note="Renamed tone_legacy → tone, added migration comment.",
)
```

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
| `type="code_change_request"` for an FYI | Triggers ECO gate for what should be auto-accepted | Use `type="collab_brief"` + `action_category="REFLEX"` |
| `type="collab_brief"` for "please change file X" | Auto-accepts a real action request without ECO review | Use the typed action request with `action_category="TACTICAL"` |
| Forgetting to ack a completed proposal | One-sided handshake; source AI never knows you delivered | Always call `cortex_complete_proposal` with `commit_sha` |
| Wrapping a discussion in `architecture_decision` to "make it serious" | ECO has to gate every chat turn; discussion stalls | Discussion is `collab_brief`; only the final decision is `architecture_decision` |
| Sending the same proposal to a wrong target and then `cortex_propose`-ing a "v2" with same content | Duplicate inbox entries, no audit trail of the re-route | Use the recovery pattern above — parent_id link + "[routing fix]" prefix |
| Guessing a peer's `ai_id` | Silent mis-route | Verify via their project.yaml or ask David |

---

## Related

- **`/cortex-mailbox-poll`** — the receive side. Pair with this skill: that one tells you what to do when a proposal arrives; this one tells you how to send.
- **`docs/architecture/EVENT_LISTENER.md`** — the full pipeline (publisher → ntfy → listener → Monitor → reaction).
- **`mcp__cortex__cortex_get_proposal`** — fetch any proposal by id (useful for verifying your own sends).
- **`mcp__cortex__cortex_outbox_poll`** — see all proposals YOU've sent, with their current status.

---

## What this skill is NOT for

- **`cortex_collab_post`** — events on a collab DOC (modified, commented, submitted). Only spawns a proposal when `action=submitted` against an existing doc. Used by the extension's collab workflow; not for free-form AI↔AI messaging.
- **`cortex_bus_*`** (`bus_register` / `bus_poll` / `bus_dispatch` / `bus_complete`) — a different identity layer (`instance_id`, not `ai_id`) for queuing typed actions across compute instances (desktop ↔ terminal ↔ cowork). Different concern entirely. Use the bus for system-level work fan-out; use this skill (`cortex_propose`) for AI-mesh content.

Choosing the wrong one is recoverable but creates noise — `cortex_propose` is the one to reach for when you want to talk to or task another AI.
