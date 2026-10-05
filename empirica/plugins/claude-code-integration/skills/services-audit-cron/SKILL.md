---
name: services-audit-cron
description: "Use when scheduling the canonical biweekly services-audit cron loop for Empirica's AI service scanner (Phase 3). The auditor body is `empirica services-audit` — captures a fresh scan, diffs against the previous, and emits a notification when novel running services appear. This skill provides the prompt template that wires the body into Claude Code's `/loop` cron mode + Empirica's loop registry. Recommended cadence: `0 6 1,15 * *` (1st and 15th of each month at 06:00 UTC — biweekly)."
version: 1.0.0
---

# Services-audit cron loop wiring

The Phase 3 services-audit loop runs `empirica services-audit` on a
biweekly cadence, captures novelty between runs, and notifies the
operator when something new is running.

This skill is a thin wrapper over `/loop-cron` — same self-scheduling
template, with `services-audit` plugged in as the body.

---

## When to Use

Register the canonical biweekly services-audit cron when:

- You've just shipped Phase 1 + Phase 2 of the scanner (one-shot scan +
  auditor skill) and want the audit to run unattended on a schedule
- You want novelty notifications (new processes, new listening ports,
  new MCP servers) without remembering to scan manually
- You want a durable audit trail in `~/.empirica/scan_history_<project_id>.jsonl`

If you only want a one-shot audit, run `empirica services-audit`
directly — no loop needed.

---

## Cron Prompt Template

When invoking `/loop` in cron mode, prepend these CLI lines to your
task prompt. The body itself is one command — the rest is registry
wiring.

```
At start (idempotent — safe to call every fire):
  empirica loop register --name services-audit --kind cron \
    --cron "0 6 1,15 * *" \
    --description "Biweekly AI services audit (scan + diff + notify on novelty)" \
    --backoff none

Check pause — exit silently AND don't schedule next fire if paused:
  PAUSED=$(empirica loop status services-audit --output json | jq -r .paused)
  if [ "$PAUSED" = "true" ]; then
    empirica loop heartbeat services-audit --status ok --result paused \
      --message "skipped, paused"
    exit 0   # CRITICAL: exit without scheduling next; loop is genuinely off
  fi

Run the audit body — single command, returns structured JSON.
The .result field is shaped to feed straight into heartbeat:
  AUDIT=$(empirica services-audit --output json)
  RESULT=$(echo "$AUDIT" | jq -r .result)              # found | empty | fail
  SCAN_ID=$(echo "$AUDIT" | jq -r .scan_id)
  PROC_NEW=$(echo "$AUDIT" | jq -r '.novelty.processes_added | length')
  PORT_NEW=$(echo "$AUDIT" | jq -r '.novelty.listeners_added | length')
  SUMMARY="scan ${SCAN_ID:0:8} → $RESULT (+$PROC_NEW procs, +$PORT_NEW listeners)"

  empirica loop heartbeat services-audit --status ok --result $RESULT \
    --message "$SUMMARY"

Schedule + install the next fire:
  NEXT_CRON=$(empirica loop schedule-next services-audit --output json | jq -r .cron_one_shot)
  # CronCreate(cron=$NEXT_CRON, recurring=false, prompt='<this whole template again>')

  # Heartbeat back the scheduler-returned job_id so pause can cancel:
  empirica loop heartbeat services-audit --status ok --result $RESULT \
    --next-scheduled-job-id "$JOB_ID" --scheduler-kind cron-create

On failure (collect_snapshot threw, scan dir unwritable, etc.):
  empirica loop heartbeat services-audit --status fail --result fail \
    --message "{error message}"
```

---

## Cadence

The default cron `0 6 1,15 * *` fires at 06:00 UTC on the 1st and 15th
of each month — closest stable approximation of "biweekly" that cron
expression syntax allows. Adjust the hour/day for your timezone or
operational rhythm.

For more frequent monitoring (e.g. weekly): `0 6 * * 1` (every Monday).

For less frequent (monthly): `0 6 1 * *` (1st of each month).

`services-audit` is cheap on a typical dev machine (~1-2 seconds for
the snapshot + diff), so cadence is purely a noise-budget question, not
a resource one.

---

## Reading the result

`empirica services-audit --output json` returns:

```json
{
  "ok": true,
  "project_id": "...",
  "scan_id": "...",
  "prior_scan_id": "...",      // null on first run
  "result": "empty",            // "found" / "empty" / "fail"
  "novelty": {
    "processes_added": [],      // names appearing for the first time
    "processes_removed": [],    // names that were there last time
    "listeners_added": [],      // host:port pairs
    "listeners_removed": []
  },
  "saved": {...},
  "notify": {
    "emitted": false,
    "reason": "no novelty"      // or "skipped" / "dispatch failed: ..."
  }
}
```

**Result mapping for `loop heartbeat --result`:**
- `found` — novel processes or listeners detected. Notification fired
  via the configured backend (stdout / log / ntfy). Backoff resets to
  base.
- `empty` — no novelty since the previous scan. Quiet success. Backoff
  advances streak (if `--backoff exponential` is set; the default
  template uses `none` since biweekly is already a slow cadence).
- `fail` — `services-audit` errored (no project context, scan crashed).
  Backoff resets to base; retry next interval.

---

## Operator workflow

After installing the loop:

1. **Review history:** `empirica scan-history --limit 10` — last 10
   audit fires
2. **Compare two snapshots:** `empirica scan-diff <a> <b>` — spot-check
   what changed between two cycles
3. **Re-render a snapshot:** `empirica scan-show <scan_id>` — full
   markdown report
4. **Pause if noisy:** `empirica loop pause services-audit` — body
   exits cleanly on next fire, no next-cron installed
5. **Resume:** `empirica loop resume services-audit` then
   `empirica loop fire services-audit` to bootstrap (CronCreate-mode
   only emits a hint).

---

## Notification routing

The `services-audit` notification has:
- `severity: warning`
- `source: loop:services-audit`
- `tags: [services-audit, security]`

To route audit notifications to a specific backend (e.g. ntfy on a
dedicated topic), add a routing rule to `~/.empirica/notify.yaml`:

```yaml
routing:
  - match: { source: "loop:services-audit" }
    backend: ntfy
    topic: empirica-security
```

Without a routing rule, the dispatcher's default backend handles them
(stdout, by default — visible in your tmux scrollback or the cockpit's
notification panel).

---

## See also

- `/loop-cron` — the generic cron template this skill specialises
- `/services-auditor` — the AI-judgment skill (Phase 2) that runs
  alongside the deterministic scanner; complementary not redundant
- `docs/architecture/SERVICES_SCANNER.md` — Phase 1/2/3 architecture
- `docs/specs/PROPOSAL_LOOP_BACKOFF.md` — backoff math (services-
  audit doesn't use it — biweekly is already slow — but the option is
  there if you switch to a tighter cadence)
