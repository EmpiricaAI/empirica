# Event-Listener — separate concept from cron loops

**Status:** Shipped in 1.9.0 (proposal authored 2026-04-30, items 1–4 landed same week)
**Author:** David + Claude Code (Opus 4.7 1M)
**Related:**
- `PROPOSAL_LOOP_SELF_SCHEDULING.md` (cron-mode loops, sister concept)
- `docs/architecture/COCKPIT.md` (registry surface, now covers loops + listeners)
- `empirica/plugins/claude-code-integration/skills/loop-cron/SKILL.md` (cron body template, v1.1.0+)
- `empirica/plugins/claude-code-integration/skills/inbox-listener/SKILL.md` (listener body template, v1.0.0)

> This document was the design proposal that landed as the listener subsystem.
> Items 1–4 of the implementation ordering shipped on develop ahead of 1.9.0:
> `e32f7dee8` (pause-cancels-cron), `e656f24c6` (CLI + registry),
> `60b167c2d` (install/uninstall bridge + skill), `df80895a3` + `577207cc3`
> (TUI L/E binary toggle + project.yaml auto-install). Items 5 (cockpit/TUI
> visibility) shipped alongside as part of the L/E work; item 6 (outreach
> migration) is empirica-outreach side, separate repo.

---

## Problem statement

The empirica loop registry currently treats all background work as
cron-style: register a schedule, fire periodically, body decides whether
to do work or skip. This works for genuine periodic tasks (memory
compaction every 4h, daily reports, weekly checkpoints) but is the wrong
model for **event-driven inbox work** like:

- Outreach Claude reading orchestration messages from Cortex
- ECO/ENP proposal acceptance polls
- WhatsApp / Gmail inbox monitors
- Any "wake when something arrives" workflow

Two concrete problems we hit in production with the cron-as-inbox-poll
pattern (outreach-inbox-poll, 2026-04-30):

1. **Empty fires consume tokens.** A 15-min cron with a streak of 7 still
   fired every 15min, each fire running full PREFLIGHT/CHECK/POSTFLIGHT
   even when the inbox had 0 new items. Self-scheduling backoff at the
   registry level (`next≥4h`) is advisory only — the OS-level cron
   keeps firing on its original schedule until the recurring job is
   replaced with one-shots, which the body has to install itself.

2. **Pause doesn't actually stop firing.** `empirica loop pause` writes
   a soft flag. The body's pause-check at next fire exits silently —
   but the underlying CronCreate job keeps firing every 15min,
   triggering a fresh CC session that runs the body just long enough
   to see the flag and exit. Pausing is supposed to mean "this loop is
   off"; today it means "this loop fires silently every period
   forever." The user has no way to actually stop it short of
   `CronDelete` from the owning CC session.

The pause bug is solvable inside the cron-loop concept. The empty-fire
waste isn't — cron is the wrong shape for "wake on event" work, no
amount of backoff math fixes that.

## Two distinct concepts (don't merge them)

| Aspect | Cron-mode loop (existing) | Event-listener (new) |
|---|---|---|
| Purpose | Periodic tasks at fixed cadence | Wake on external event |
| Examples | memory compact 4h, daily report 9am, weekly cleanup | inbox poll, proposal queue, push notifications |
| Wake mechanism | OS cron / CronCreate at scheduled times | Held HTTP connection (ntfy/SSE/WebSocket) → Monitor with `persistent: true` |
| Idle cost | 1 fire per period (token bleed scales with frequency) | Zero — connection holds, no work runs |
| Pause = | Stop scheduled firings | Stop the Monitor / kill the curl |
| Skill | `loop-cron` (existing) | `inbox-listener` (new — to be authored) |

These are different problems with different best primitives. Treating
event-driven work as "cron at high frequency with skip-on-empty" is
why we're seeing token bleed and the pause bug interaction.

## Architecture: event-listener

Three-component model. None involves cron.

```
                     ┌────────────────────────────┐
                     │ Cortex / external publisher │
                     │  publishes on inbox write   │
                     └──────────────┬──────────────┘
                                    │ HTTP POST
                                    ▼
                     ┌────────────────────────────┐
                     │  ntfy server (or any SSE) │
                     │  topic: outreach-claude    │
                     └──────────────┬──────────────┘
                                    │ held HTTP connection
                                    ▼
   Background Bash (run_in_background: true):
     curl -N -u $AUTH https://ntfy/topic/json
                                    │ emits JSON line per published message
                                    ▼
   Claude Code Monitor (persistent: true, watches the curl task):
                                    │ matches a line → wakes parent CC
                                    ▼
                     Claude wakes with <task-notification>
                                    │
                                    ▼
                     processes message → returns to wait state
                                    │
                                    └── Monitor stays armed for next event
```

**Key properties:**

- **Zero idle work.** When inbox is quiet, the curl holds connection, the
  CC session is suspended waiting on Monitor. No tokens consumed, no
  PREFLIGHT/CHECK/POSTFLIGHT cycles for empty polls.
- **Wake latency = network latency.** ntfy publishes immediately on
  inbox write; held connection delivers within a round-trip; Monitor
  fires; CC wakes. Sub-second from publish to processing.
- **Reconnect-tolerant.** When the curl drops (network blip, server
  restart), Monitor surfaces EOF; the wake handler restarts the curl.

## Pause/resume semantics

For event-listener the pause is **mechanical, not advisory**:

- `empirica listener pause NAME` — kills the Monitor + the background
  curl for this listener. The listener IS off. No connection held, no
  wake possible.
- `empirica listener resume NAME` — relaunches the curl + re-arms
  Monitor.
- `empirica listener status NAME` — shows: registered, paused/active,
  topic, last wake timestamp, last message.
- `empirica listener unregister NAME` — removes from registry entirely.

Compare this to the cron-loop pause bug we have today (advisory flag
that doesn't cancel firings). For listeners, pause MUST cancel because
"wait for events" without a held connection is incoherent — there's
nothing to be partially-paused.

## Proposed CLI surface

Mirrors `empirica loop` but with verbs that make sense for a listener:

```
empirica listener register --name NAME --topic ntfy:TOPIC \
  --description "..." --on-wake "<prompt template>"
empirica listener pause NAME
empirica listener resume NAME
empirica listener status NAME [--output json]
empirica listener list
empirica listener unregister NAME
empirica listener fire NAME       # manually trigger a wake (testing)
```

**Extension hook**: `--topic` accepts a URL scheme. Initial impl: `ntfy:`
only. Future: `sse:`, `websocket:`, `gmail:` (via gmail watch API),
`whatsapp:` (via Twilio/etc.).

The `--on-wake` arg is the prompt template the listener replays each
time Monitor fires. Like a cron prompt but executed event-driven.

## Where state lives

Mirroring the existing cron-loop registry:

- `~/.empirica/listeners_<instance>.json` — listener definitions per
  instance (parallel to `loops_<instance>.json`)
- `~/.empirica/listener_paused_<instance>_<name>.json` — pause flag
  (used by status output; not the kill mechanism)
- `~/.empirica/listener_active_<instance>_<name>.json` — runtime
  metadata (Monitor task ID, curl PID, last_wake_at)

Pause = remove the `_active_*` file (kill curl, stop Monitor) and write
the `_paused_*` flag. Resume = inverse.

## How the wake actually fires Claude

Two mechanisms depending on session state:

1. **Owning instance is alive (Monitor armed):** Monitor with
   `persistent: true` does the wake directly. This is the happy path.
2. **Owning instance has died (no Monitor):** the listener registry
   knows the listener exists but no live connection holds. Re-bootstrap
   on next CC start: `UserPromptSubmit` hook reads the listeners
   registry and surfaces a system-reminder asking Claude to re-arm
   Monitor + curl. Same pattern as today's `loop install-request`.

For external (non-CC) wake fallback when no CC instance is alive,
Cortex can send a normal ntfy notification to the user's phone — but
that's outside this proposal's scope.

## Cortex-side requirement (one-line addition)

For event-listener to work for Cortex orchestration inboxes, Cortex's
`inbox.write` code path needs to publish to ntfy on every write. Topic
convention:

```
<role>-claude-inbox
```

e.g. `outreach-claude-inbox`, `comms-claude-inbox`,
`empirica-core-claude-inbox`. The same topic is what `empirica
listener register --topic ntfy:outreach-claude-inbox` subscribes to.

If Cortex currently only publishes on ECO/ENP proposal acceptance (not
on every inbox write), this is the change that needs to land in Cortex
before listeners are useful for orchestration messages. Confirm with
Cortex maintainers before relying on it.

## Pause-actually-stops-firing fix (orthogonal but we should ship together)

The cron-loop pause bug is a separate concern but it shares a root
cause with this proposal: pause shouldn't be advisory.

**Fix shape for cron loops:**

`empirica loop pause NAME` should:

1. Write the pause flag (existing behavior — backward compat for body
   pause-checks).
2. Read the recorded `next_scheduled_job_id` from the registry.
3. Write a `loop_uninstall_request_<instance>_<name>.json` file with
   the job ID.
4. The owning instance's `UserPromptSubmit` hook surfaces this as a
   system-reminder on next prompt: "Pause requested for loop X. Run
   `CronDelete(<job_id>)` to actually stop it from firing."
5. Claude in that instance calls `CronDelete`, removes the pending
   file.

This is the SAME pattern as the existing `loop install-request`
mechanism, just inverse. Symmetric: install-request to install,
uninstall-request to uninstall.

**Limitation we'll need to document:** if the owning CC instance is
dead at pause time, the recorded `job_id` is still alive in the OS
cron table. The pending file will sit there until the instance comes
back. As an escape hatch, surface a "stuck pending uninstall" warning
in `empirica status`. As a sledgehammer, expose `empirica loop
force-cancel NAME` that requires the user to confirm — actually goes
to disk, finds the cron daemon's record, removes it directly. Out of
scope for V1; the hook-driven mechanism covers the common case.

## Migration plan

**For outreach-inbox-poll specifically:**

1. (Now) Confirm Cortex publishes to `outreach-claude-inbox` on inbox
   write. If not, schedule that change first.
2. Build `empirica listener` CLI surface (parallel to `empirica loop`
   but for event listeners).
3. Build `inbox-listener` skill (parallel to `loop-cron`).
4. Outreach Claude: unregister the existing 15-min cron. Register the
   listener. The cron stops firing; ntfy push wakes on real messages.
5. Memory compactor / report-style loops STAY on the cron-loop model.

**Pause-fix is independent:**

Ship it before V1 of listeners. Outreach will benefit when migrating;
all other cron-mode loops benefit immediately.

## Open questions

1. **Naming.** "Listener" vs "subscription" vs "watcher" vs "inbox."
   Listener feels neutral and matches mental model ("listening for
   events"). Stick with it unless there's a stronger candidate.
2. **Topic schemes beyond ntfy.** SSE and WebSocket are easy
   extensions; gmail/whatsapp need bespoke poll-or-webhook. V1 is
   ntfy-only; design the URL scheme to extend cleanly.
3. **Wake prompt templating.** Does the prompt include the message
   payload (Monitor would need to surface it), or does the prompt say
   "go check inbox" and the Claude reads it itself? Probably the
   latter for V1 — keep Monitor's job to "wake," not "deliver
   message."
4. **Cockpit visibility.** `empirica status` should show listeners in
   the same view as loops (different kind, different paused
   semantics). TUI needs a listeners panel.
5. **Multi-instance.** Can two CC instances both subscribe to the same
   topic? Probably yes (each holds its own connection). Useful when
   the same inbox is monitored by multiple personas. Out of scope for
   V1 design but worth not foreclosing.

## Implementation ordering (when we get to building)

1. **Pause-actually-cancels-cron fix** — small, scoped,
   independently-valuable. Ship as 1.9.0 alongside the shell-construct
   fix already on develop.
2. **`empirica listener` CLI + registry** — mirrors `empirica loop`
   structure. Pause/resume must kill/restart the Monitor task.
3. **`inbox-listener` skill** — prompt template for the listener
   wake-and-process pattern.
4. **`loop install-request` analog for listeners** — bootstrap when
   instance starts and there's a registered-but-not-armed listener.
5. **Cockpit + TUI integration** — `empirica status` shows listeners,
   TUI gains a panel.
6. **Migrate outreach-inbox-poll.**

## Out of scope for this proposal

- Replacing `loop-cron` with a unified concept. Two concepts is
  correct; cron-mode loops have legitimate use cases.
- Rebuilding self-scheduling. Existing cron-loop self-scheduling stays.
- ECO/ENP proposal-acceptance polling on a different mechanism. That's
  a separate inbox; will use the same listener model when we get to
  it.
- ntfy-server-side changes. We assume ntfy is a fixed-feature
  publisher; we subscribe to it.
