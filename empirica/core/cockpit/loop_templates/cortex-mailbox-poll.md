<!-- Machine-consumed, not practitioner-facing.

     empirica/core/cockpit/loop_install_request.py reads the fenced block under
     '## Cron Prompt Template' below and bakes it into the canonical loop-install
     request. It lived inside SKILL.md until 2026-07-31, which meant every session
     that loaded the skill carried ~600 words of shell it would never read — the
     cockpit installs a loop rarely, the practitioner reads the skill constantly.

     Split out when the skill was cut to its operational core. The extractor checks
     here first and falls back to SKILL.md, so skills still carrying the section
     inline keep working. -->

## Cron Prompt Template

When invoking `/loop` in cron mode, prepend these CLI lines to your
task prompt. Variables already filled — the canonical preset is
fixed by the catalog entry (30s base, 5m max).

```
At start (idempotent — safe to call every fire):
  empirica loop register --name cortex-mailbox-poll --kind interval \
    --interval 30s \
    --description "Poll Cortex inbox + outbox for orchestration messages (canonical)" \
    --backoff exponential --base-interval 30s --max-interval 5m

Check pause — exit silently AND don't schedule next fire if paused:
  PAUSED=$(empirica loop status cortex-mailbox-poll --output json | jq -r .paused)
  if [ "$PAUSED" = "true" ]; then
    empirica loop heartbeat cortex-mailbox-poll --status ok --result paused \
      --message "skipped, paused"
    exit 0
  fi

Self-throttle — if an empirica transaction is open, the caller is
already engaged. Don't interrupt; just freeze the streak.
  TX_OPEN=$(python3 -c "
from empirica.utils.session_resolver import InstanceResolver as R
tx = R.transaction_read()
print('true' if tx and tx.get('status') == 'open' else 'false')
")
  if [ "$TX_OPEN" = "true" ]; then
    empirica loop heartbeat cortex-mailbox-poll --status ok --result empty \
      --message "self-throttle: transaction open"
    NEXT_CRON=$(empirica loop schedule-next cortex-mailbox-poll --output json | jq -r .cron_one_shot)
    # CronCreate(cron=$NEXT_CRON, recurring=false, prompt='<this template again>')
    exit 0
  fi

Resolve self ai_id from project context:
  AI_ID=$(python3 -c "
import os, re
from pathlib import Path

# 1. Project's CLAUDE.md
for parent in [Path.cwd()] + list(Path.cwd().parents):
    claude_md = parent / 'CLAUDE.md'
    if claude_md.exists():
        text = claude_md.read_text()
        m = re.search(r'(?:^|\n)\*?\*?AI_ID:?\*?\*?\s*[\`\"]?([a-z0-9_-]+)[\`\"]?', text, re.IGNORECASE)
        if m:
            print(m.group(1)); raise SystemExit
        break

# 2. Project name fallback (use directory basename as-is, keep prefix)
project_path = os.getcwd()
name = Path(project_path).name
if name:
    print(name); raise SystemExit

# 3. Env var override
ai_id = os.environ.get('EMPIRICA_AI_ID')
if ai_id:
    print(ai_id); raise SystemExit

raise SystemExit(1)  # unresolved
")
  if [ -z "$AI_ID" ]; then
    empirica loop heartbeat cortex-mailbox-poll --status fail --result fail \
      --message "unresolved ai_id (no CLAUDE.md AI_ID line, no project name fallback, no EMPIRICA_AI_ID env)"
    NEXT_CRON=$(empirica loop schedule-next cortex-mailbox-poll --output json | jq -r .cron_one_shot)
    exit 0
  fi

Poll inbox via MCP — react to new proposals addressed to self.
The api_key for cortex_* MCP tools is read by the MCP server itself
from ~/.empirica/credentials.yaml; no need to pass it explicitly.
  Call mcp__cortex__cortex_inbox_poll(ai_id=$AI_ID)
  INBOX_NEW=<number of new items returned>

  For each new item:
    - If type=collab_brief: AUTO-REACT — read the payload, log a
      finding-log for durability, and post a reply via
      `empirica mailbox reply --parent-id <pid> --result shipped` (the
      atomic propose+complete verb closes the loop). Do NOT
      surface-and-wait. Collab is noetic/ungated — the human is the
      gate ONLY for ECO-gated typed proposals (see below) and for
      your own returning outbox state changes (status=changed/declined).
      If a collab asks you a question, answer it directly; the
      AI-to-AI substrate exists so the human doesn't have to dispatch.
    - If type=spec_updated: ack with cortex_archive_proposal once you've
      consumed the change
    - If type=architecture_decision / code_change_request / publish /
      trust_escalation_request: these are ECO-gated typed proposals.
      Surface to the user — they need explicit human Accept/Decline
      before action. Do not auto-execute the underlying work.
    - For any item with parent_id: link your follow-up via parent_id

Poll outbox via MCP — emit follow-ups for proposals that came back
as 'changed' (peer/user requested a refinement).
  Call mcp__cortex__cortex_outbox_poll(ai_id=$AI_ID, status=changed)
  OUTBOX_CHANGED=<number of changed proposals>

  For each changed proposal:
    - Read the refinement note
    - Compose an updated proposal with parent_id pointing to the original
    - Submit via cortex_propose (parent_id linking back closes the loop)

Determine result for backoff signaling:
  if [ "$INBOX_NEW" -gt 0 ] || [ "$OUTBOX_CHANGED" -gt 0 ]; then
    RESULT=found
    SUMMARY="ai_id=$AI_ID inbox=+$INBOX_NEW outbox-changed=+$OUTBOX_CHANGED"
  else
    RESULT=empty
    SUMMARY="ai_id=$AI_ID no activity"
  fi

At end — heartbeat with result, schedule + install the next fire:
  empirica loop heartbeat cortex-mailbox-poll --status ok --result $RESULT \
    --message "$SUMMARY"

  NEXT_CRON=$(empirica loop schedule-next cortex-mailbox-poll --output json | jq -r .cron_one_shot)
  # CronCreate(cron=$NEXT_CRON, recurring=false, prompt='<this whole template again>')

  # Heartbeat back the scheduler-returned job_id so pause can cancel:
  empirica loop heartbeat cortex-mailbox-poll --status ok --result $RESULT \
    --next-scheduled-job-id "$JOB_ID" --scheduler-kind cron-create

On MCP failure (network, auth, unexpected error):
  empirica loop heartbeat cortex-mailbox-poll --status fail --result fail \
    --message "{error message}"
  # Failure retries at base — schedule-next still returns base interval.
```
