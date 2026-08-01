# Trigger Model — wake-on-event, interval loops, cron loops

**What causes a practice to do something when nobody typed anything.**

Three mechanisms exist. They are not interchangeable, and the choice between them
is a policy decision, not a tuning knob. This document says which to reach for,
what the opt-in rules are, and why they are what they are.

Companion docs: [`EVENT_LISTENER.md`](EVENT_LISTENER.md) for how the push pipeline
works end to end, and [`COCKPIT.md`](COCKPIT.md) for loop state files, pause
semantics and the pickup hook.

---

## The three mechanisms

| | **Wake-on-event** | **Interval loop** | **Cron loop** |
|---|---|---|---|
| Fires when | something actually happened | every N seconds, forever | at wall-clock times |
| Cost when idle | nothing | one tick per interval | one run per schedule |
| Latency | seconds | up to one interval | up to one schedule gap |
| Installed by default | **yes** — the listener is the standing mechanism | no | **never** |
| `kind` in the registry | n/a (listener + Monitor, not a loop) | `interval` | `cron` |

### Wake-on-event — the default, and the preferred answer

A persistent listener holds a stream to Cortex and writes one line per
ECO-decided proposal event. A Monitor armed at SessionStart tails that log and
bridges each line into the running session as a `<task-notification>`.

**Reach for this whenever the harness supports it.** It costs nothing while
nothing is happening, it fires within seconds when something does, and there is
no schedule to get wrong. Every mesh trigger should be this unless it genuinely
cannot be.

### Interval loop — polling, when there is no event to wait for

A repeating tick with an optional exponential backoff envelope: `base_interval`
when active, stretching toward `max_interval` on consecutive empty results, and
snapping back on any non-empty one.

Justified when the thing you care about does not emit an event you can subscribe
to. Not justified as a "safety net" beside a working listener — a poll running
next to wake-on-event is redundant work on every seat that has both, and
`cortex-mailbox-poll` is marked `opt_in_only` for exactly that reason. It exists
for harnesses that cannot do wake-on-event at all (cron-only VMs, isolated cloud
runners).

### Cron loop — wall-clock schedules, opt-in only

A schedule expression, not an interval. Use it when the work is genuinely
calendar-shaped: "prune expired messages at 03:17 daily" has no event to wait for
and no meaningful interval.

---

## The opt-in rule

> **No cron loop is ever installed by default.** Wake-on-event is what we always
> want where it is possible.
> — David, 2026-08-01

**Why.** A cron job is a standing scheduled process on someone's machine. It
outlives the session that created it, it runs whether or not anyone is watching,
and it keeps running long after whatever justified it. Installing one without
being asked is a side effect the user did not consent to. Wake-on-event has none
of those properties: it is inert until something real happens.

**What this means in practice:** the canonical catalogue currently contains
nothing auto-installable. A fresh practice acquires **no scheduled jobs at all**.
That is the intended state, not a gap.

**Accepted consequence, stated plainly:** `message-cleanup` no longer runs unless
a seat opts in, so expired mesh messages are not pruned there. That is the cost
of the rule and it was accepted knowingly.

### How to opt in

```bash
empirica loop register --name message-cleanup --kind cron \
  --cron "17 3 * * *" \
  --description "Daily cleanup of expired git-notes mesh messages"
```

Registering by hand *is* the opt-in. There is no flag that makes auto-install
resume.

### How the rule is enforced

Two layers, and only one of them is load-bearing:

1. **The structural gate (load-bearing).** `maybe_queue_canonical_install` skips
   every entry whose `kind == "cron"`, regardless of any flag.
2. **The `opt_in_only` flag (documentation).** Set on cron entries so the
   catalogue is self-describing, and on non-cron entries that are opt-in for
   other reasons — `cortex-mailbox-poll` carries it because wake-on-event
   supersedes it, not because it is cron.

The gate keys on `kind` rather than on the flag because **the entry that broke
this rule was the one that never set the flag.** `message-cleanup` auto-installed
for months under a comment explicitly exempting "genuine housekeeping crons" from
the opt-in carve-out. A per-entry flag protects only the entries whose author
remembered it; a gate on the class protects the ones they didn't.

This is the same shape as the id-prefix guards elsewhere in the codebase: guard
the class, not the instance, because the instance is what gets forgotten.

---

## Choosing

```
Is there an event you can subscribe to?
├── yes → wake-on-event.                      Stop here. This is the answer.
└── no
    ├── Is the work calendar-shaped
    │   ("every day at 03:00")?
    │   └── yes → cron loop, OPT-IN ONLY. Never auto-install it.
    └── Is it "check periodically until something shows up"?
        └── yes → interval loop with exponential backoff.
                  If a listener already covers this, you do not need it.
```

---

## Registry fields that change behaviour

| Field | Values | What it decides |
|---|---|---|
| `kind` | `interval` · `cron` · `monitor` | The scheduling shape. **`cron` also means never auto-installed.** |
| `opt_in_only` | `true` | Excluded from auto-install. Advisory for cron (the gate already excludes it); load-bearing for non-cron entries. |
| `body_kind` | `cli` · `claude-react` | **The autonomy discriminator.** `cli` runs the verb directly with no AI in the loop. Anything else is tick-only by construction — the timer appends a heartbeat and a live session reacts. Only `cli` grants autonomous execution. |
| `body_command` | e.g. `message-cleanup --output json` | ExecStart target for `body_kind: cli`. Meaningless without it. |
| `scheduler_kind` | `cron-create` · `systemd-user` · `system-cron` · `at-queue` · `unknown` | Which scheduler installs the timer. |
| `backoff` | `none` · `exponential` | With `base_interval`/`max_interval`, the stretch envelope for interval loops. |

**`body_kind` is worth reading twice.** A loop that needs the AI to think must
not be given a `body_command`, or the timer will run it autonomously with no
session attached. Loops without `body_kind: "cli"` stay tick-only *by
construction* rather than by convention.

---

## A note on `schedule_next` for cron loops

`empirica loop schedule-next` returns an interval-derived plan for interval
loops. For a cron loop it returns the **expression** and omits every field that
would imply a computed next fire — no `next_fire_at`, no `interval_seconds`, no
`cron_one_shot`.

That omission is deliberate. Computing a true next fire from a cron expression
needs cron arithmetic this package does not carry, and `cron_one_shot` pins a
one-shot to `fire_at` — which for a cron loop would be *now*. A caller handing
that to `CronCreate` would fire immediately while believing it had scheduled
tomorrow's 09:00 run. Absent beats plausible-but-wrong.

Full cron arithmetic is tracked in #396.
