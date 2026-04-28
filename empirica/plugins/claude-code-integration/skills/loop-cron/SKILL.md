---
name: loop-cron
description: "Use when scheduling cron-mode loops with Claude Code's /loop, when registering periodic background work, when the user says 'cron loop', 'periodic loop', 'register a cron', 'schedule recurring work', or when configuring a loop that needs to be visible in `empirica status`. This skill provides the prompt template that wires CC's /loop into Empirica's loop registry — register at start, check pause flag each fire, heartbeat at end. Without this wiring, a /loop cron is invisible to the cockpit and uncontrollable from any other terminal."
version: 1.0.0
---

# Loop-Cron Wiring

Claude Code's built-in `/loop` skill drives periodic work via `CronCreate`.
On its own, those loops are **invisible to other terminals** — you can't
see what's scheduled, can't tell when one last ran, and can't pause them
without killing the process. Empirica's loop registry fixes that with
three CLI calls layered into the cron prompt.

This skill is the prompt template for cron-mode loops that integrates with
the registry.

---

## When to Use

You're about to start a cron-mode loop with `/loop` (or already have one
running) and:

- You want it visible in `empirica status --all`
- You want to pause/resume from any terminal without killing the process
- You want a heartbeat trail for "did it actually run, did it succeed"
- You want it to survive a Claude Code session restart cleanly

If your loop is one-shot or interactive ("dynamic mode"), skip this — it
applies to **scheduled** (cron/interval) loops only.

---

## Cron Prompt Template (self-scheduling)

Per `PROPOSAL_LOOP_SELF_SCHEDULING.md`: the body owns the schedule.
Each fire installs the next fire as a one-shot at the timestamp the
backoff math returns. There is no recurring cron — pause means the
scheduler is silent (no token bleed).

When invoking `/loop` in cron mode, prepend these CLI lines to your
task prompt. Variables: `{NAME}` (loop identifier, no spaces),
`{INTERVAL}` (base cadence, e.g. `15m`), `{DESC}` (one-line description).

```
At start (idempotent — safe to call every fire):
  empirica loop register --name {NAME} --kind cron --interval "{INTERVAL}" \
    --description "{DESC}" \
    --backoff exponential --base-interval {INTERVAL} --max-interval 4h

Check pause — exit silently AND don't schedule next fire if paused:
  PAUSED=$(empirica loop status {NAME} --output json | jq -r .paused)
  if [ "$PAUSED" = "true" ]; then
    empirica loop heartbeat {NAME} --status ok --result paused \
      --message "skipped, paused"
    exit 0   # CRITICAL: exit without scheduling next; loop is genuinely off
  fi

[... your actual work here, capturing $RESULT as found|empty|fail ...]

At end — heartbeat with result, then schedule + install the next fire:
  empirica loop heartbeat {NAME} --status ok --result $RESULT \
    --message "$SUMMARY"

  NEXT_CRON=$(empirica loop schedule-next {NAME} --output json | jq -r .cron_one_shot)

  # Install the next one-shot using your scheduler:
  # Claude Code:    CronCreate(cron=$NEXT_CRON, recurring=false,
  #                            prompt='<this whole template again>')
  # systemd-user:   systemd-run --user --on-active=$INTERVAL ...
  # at-queue:       echo '<command>' | at -t $(date -u -d "$NEXT_FIRE" +%Y%m%d%H%M)

  # Then heartbeat back the scheduler-returned job_id so pause can cancel:
  empirica loop heartbeat {NAME} --status ok --result $RESULT \
    --next-scheduled-job-id "$JOB_ID" --scheduler-kind cron-create

On failure:
  empirica loop heartbeat {NAME} --status fail --result fail \
    --message "{error}"
  # Failure retries at base — schedule-next still returns base interval.
```

The register call is **idempotent** — it preserves runtime state
(last_run, last_status, last_message, last_result, empty_streak,
next_scheduled_job_id) while updating declarative fields. Safe to
re-issue on every fire without losing history.

**Backoff stretches the actual schedule** (not just a body-internal
threshold like the previous spec). `schedule-next` returns:
- streak 0 → base interval (e.g. 15m)
- streak 1 → base × 2 = 30m
- streak 2 → base × 4 = 1h
- ... capped at `--max-interval` (default 4h)

**`--result` is the schedule signal:**
- `found`  → new work happened — reset streak, next fire at base
- `empty`  → fire ran cleanly, nothing to do — advance streak
- `fail`   → errored — reset streak, retry at base (no compound delay)
- `paused` → body short-circuited on pause check — freezes streak

The body is its own next-fire scheduler — every fire installs the next
one. `empirica loop pause` clears the recorded `next_scheduled_job_id`;
the body's pause check at the start of the next fire is the final
backstop (it sees the pause flag and exits without scheduling).

**Manual escape hatches:**

```
empirica loop poke {NAME}
```
Resets the streak to 0 and clears the threshold. The next fire (when
it arrives) runs at base.

```
empirica loop fire {NAME}
```
Computes the next-fire schedule and emits the cron expression + a hint
to install it. Bootstraps after `empirica loop resume` on Claude Code
(the empirica CLI can't call CronCreate directly — re-issue via
`/loop` or run the printed `CronCreate(...)` invocation).

---

## Concrete Example

A 15-minute inbox poll with backoff:

```
/loop --cron "*/15 * * * *" --prompt "
At start:
  empirica loop register --name inbox-poll --kind cron \
    --cron '*/15 * * * *' --description 'ECO inbox digest' \
    --backoff exponential --base-interval 15m --max-interval 4h

Check pause + backoff:
  if [ \"\$(empirica loop status inbox-poll --output json | jq -r .paused)\" = \"true\" ]; then
    exit 0
  fi
  if ! empirica loop should-fire inbox-poll; then
    exit 0
  fi

Then: read each connected inbox, summarize unread mail, capture findings.

At end:
  if [ \"\$NEW_COUNT\" -gt 0 ]; then
    empirica loop heartbeat inbox-poll --status ok --result found \
      --message \"\$INBOXES_CHECKED inboxes, \$NEW_COUNT new\"
  else
    empirica loop heartbeat inbox-poll --status ok --result empty \
      --message \"\$INBOXES_CHECKED inboxes, none new\"
  fi

On failure:
  empirica loop heartbeat inbox-poll --status fail --result fail \
    --message \"\$ERROR_MESSAGE\"
"
```

---

## Pausing and Resuming

From any terminal, on any machine that shares `~/.empirica/`:

```
empirica loop pause inbox-poll      # cancels next fire (no token bleed)
empirica loop resume inbox-poll     # clears flag; bootstrap with `loop fire`
empirica loop status inbox-poll     # show last run + paused state
empirica loop list                  # all loops on this instance
empirica loop unregister inbox-poll # remove from registry entirely
```

`pause` clears the recorded `next_scheduled_job_id` and surfaces a
hint about scheduler-specific cancellation. For CronCreate loops the
body's pause check at the next fire is the backstop — it sees the
flag and exits without scheduling the next fire, so the loop dies
cleanly after at most one more silent fire. `CronDelete` itself is a
CC tool; if you want to cancel the already-installed job immediately
you can call it from the running CC session, or just let the body's
pause check end it.

`resume` clears the pause flag. The next prompt in the target instance
will pick up any pending install request and the body will re-arm
itself. To reissue the install (bootstrap the cron) from any other
terminal:

```
empirica loop install-request --instance {INSTANCE_ID} \
  --name {NAME} --interval {INTERVAL}
```

That registers in the target's registry and queues a pending request
that the target instance's `UserPromptSubmit` hook surfaces as a
system-reminder on the next prompt — Claude in that instance runs
`/loop` and `CronCreate` fires. Use `empirica loop fire {NAME}` for
single manual fires (testing, ad-hoc poke).

| State | Cron firing | Body running | Tokens |
|---|---|---|---|
| Active, no backoff | every base | every fire | every base |
| Active, in backoff | every backoff_interval | every fire | every backoff_interval |
| Paused | never | never | none |

---

## Visibility

Once registered, the loop appears in:

```
empirica status              # current instance
empirica status --all        # every Claude across every terminal
watch -n 2 empirica status --all --pretty   # live cockpit
```

…showing kind, schedule, last fire, last status, and pause state in one
table. This is the comprehension principle: you shouldn't run loops you
can't read.

---

## Migration from Bespoke Pause Files

Old patterns like `touch /tmp/inbox-poll.disabled` should migrate to
`empirica loop pause inbox-poll`. The standard pattern wins because:

- Visible to other instances (any tmux pane can pause/resume)
- Surfaces in the cockpit (`empirica status` shows pause state)
- Survives reboots (`~/.empirica/` persists)
- Scoped per-instance (no cross-talk between Claudes)

Once an old `/tmp/*.disabled` loop is re-issued through this template,
the bespoke flag file becomes dead code and can be deleted.

---

## Related

- `empirica status --all --pretty` — cockpit overview
- `empirica sentinel pause/resume` — per-instance noetic firewall control
- `docs/architecture/COCKPIT.md` — full state-file layout and discovery rules
