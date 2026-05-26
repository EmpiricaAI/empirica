# Empirica Cockpit

> Multi-instance state visibility and per-instance controls.
>
> **Version:** 1.9.6
> **Updated:** 2026-04-30
> **History:** loops + cockpit base + event-listener subsystem shipped 1.9.6.

---

## What it is

CLI surfaces on top of one core module:

| Command | Module | Purpose |
|---|---|---|
| `empirica sentinel <pause\|resume\|status>` | `empirica.core.cockpit.sentinel_pause` | Per-instance noetic firewall toggle |
| `empirica loop <register\|heartbeat\|pause\|install-request\|...>` | `empirica.core.cockpit.loop_registry` | Per-instance **periodic** loop registry CRUD |
| `empirica listener <register\|pause\|resume\|record-wake\|fire\|install-request\|list\|status\|unregister>` | `empirica.core.cockpit.listener_registry` | Per-instance **event-driven** listener registry CRUD (sister concept to loop) |
| `empirica instance <kill\|forget\|label\|prune>` | `empirica.core.cockpit.instance_actions` | Destructive lifecycle: terminate / scrub state / rename / bulk-prune-dead |
| `empirica status [--all\|--instance ID] [--pretty\|--json]` | `empirica.core.cockpit.instance_state` | Cockpit overview, all renderers consume the same JSON |
| `empirica tui` | `empirica.cli.tui.cockpit_app` | Interactive Textual app — clickable buttons + keyboard shortcuts for every verb (P/L/E/S/N) |

**Loops are periodic** (cron-mode, `loop-cron` skill); **listeners are
event-driven** (held HTTP connection via ntfy/SSE → Monitor wake,
`inbox-listener` skill). Both share the same install-request → pickup-hook
→ owning Claude executes pattern.

The bespoke TUI is **not** part of v1. The intended dashboard is:

```bash
watch -n 2 empirica status --all --pretty
```

A bespoke `empirica tui` may follow once the watch recipe surfaces concrete
gaps from real use — the threshold is "≥3 documented gaps from real use,
not anticipated."

---

## Principle

**Comprehension before orchestration.** The cockpit exists to make scale
*legible*, not to enable scale past comprehension. Visibility first;
controls second; automation never lives in this surface.

---

## State files

All under `~/.empirica/`. Filenames carry `instance_id` so cross-instance
scanning is one glob.

| File | Owner | Purpose |
|---|---|---|
| `instance_projects/{instance_id}.json` | session-init hook | maps instance → project_path |
| `sentinel_paused_{instance_id}` | `empirica sentinel pause` (and direct hook writes) | empty file; existence = paused |
| `sentinel_paused` | global pause fallback | applies to all instances |
| **Loops (cron / periodic)** | | |
| `loops_{instance_id}.json` | `empirica loop register/heartbeat/...` | declarative registry of loops |
| `loop_paused_{instance_id}_{name}` | `empirica loop pause` | empty file; existence = loop paused |
| `loop_install_pending_{instance_id}_{name}.json` | `empirica loop install-request` | bridge file — owning Claude's `loop-install-pickup.py` UserPromptSubmit hook surfaces it as system-reminder asking for `/loop` + `CronCreate` |
| `loop_uninstall_pending_{instance_id}_{name}.json` | `empirica loop pause` (when scheduler_kind=cron-create) | bridge file — `loop-uninstall-pickup.py` hook asks owning Claude to `CronDelete(<job_id>)` |
| **Listeners (event-driven)** | | |
| `listeners_{instance_id}.json` | `empirica listener register/record-wake/...` | declarative registry of listeners |
| `listener_paused_{instance_id}_{name}` | `empirica listener pause` | empty file; existence = listener paused |
| `listener_active_{instance_id}_{name}.json` | listener body (`inbox-listener` skill) | runtime metadata: `monitor_task_id`, `curl_pid`, `armed_at` |
| `listener_install_pending_{instance_id}_{name}.json` | `empirica listener install-request` | bridge file — `listener-install-pickup.py` hook asks owning Claude to invoke `/inbox-listener` and arm |
| `listener_uninstall_pending_{instance_id}_{name}.json` | `empirica listener pause` (when active runtime present) | bridge file — `listener-uninstall-pickup.py` hook asks owning Claude to `TaskStop(monitor_task_id)` + drop the held curl |
| **Instance lifecycle** | | |
| `instance_label_{instance_id}` | optional, set manually | one-line human label |
| `active_session_{instance_id}` | session-init hook (legacy) | session pointer; fallback for project lookup |
| `<project>/.empirica/active_transaction_{instance_id}.json` | workflow_commands.py | current open transaction |
| `<project>/.empirica/hook_counters_{instance_id}.json` | sentinel-gate.py / hooks | praxic_tool_calls etc. — used to derive phase |

Discovery walks `instance_projects/`, `sentinel_paused_*`, `loops_*.json`,
`listeners_*.json`, `active_session_*`, `hook_counters_*.json`, and
`context_usage_*.json` and unions the derived instance_ids.

---

## Phase derivation (file-only, no DB)

| `active_transaction.status` | `hook_counters.praxic_tool_calls` | Phase |
|---|---|---|
| absent | — | `no-transaction` |
| `closed` | — | `closed` |
| `open` | `> 0` | `praxic` |
| `open` | `0` or absent | `noetic` |

This is the v1 heuristic. It can be tightened later by writing an explicit
`phase` field to the active_transaction file at `check-submit` time.

---

## State symbol (one glyph per instance)

Computed from transaction status + last-activity age. No process scanning.

| Symbol | State | Rule |
|---|---|---|
| 🟢 | active | open transaction, last activity < 60s |
| 🟡 | idle | open transaction, last activity 60s–30min |
| 🔴 | stuck | open transaction, last activity > 30min |
| ⊘ | closed | no open transaction; instance state files present |
| ⊗ | no-claude | no recent state file in last 24h |

---

## JSON schema (the source of truth)

Every renderer (pretty, future TUI, future Chrome extension panel) consumes
the same dict from `aggregate_all()`:

```json
{
  "generated_at": "2026-04-26T17:42:08+00:00",
  "instances": [
    {
      "instance_id": "tmux_4",
      "label": "outreach",
      "project_path": "/path/to/project",
      "state": "active",
      "phase": "praxic",
      "transaction": {
        "id": "c1223987-...",
        "age_seconds": 142.5,
        "work_type": "code",
        "domain": null
      },
      "last_activity": "2026-04-26T17:39:46+00:00",
      "last_activity_seconds": 142.0,
      "sentinel": {
        "paused": false,
        "scope": "none",
        "since": null,
        "reason": null
      },
      "loops": {
        "eco-inbox-poll": {
          "kind": "cron",
          "cron": "*/5 * * * *",
          "interval": null,
          "description": "ECO inbox digest",
          "registered_at": "2026-04-26T15:30:00+00:00",
          "last_run": "2026-04-26T17:37:02+00:00",
          "last_status": "ok",
          "last_message": "0 new across 4 inboxes",
          "paused": false
        }
      }
    }
  ],
  "summary": {
    "instances": 5,
    "loops_registered": 4,
    "loops_paused": 1,
    "active_tx": 3
  }
}
```

---

## What's deliberately out of scope

These are tracked elsewhere or punted to future proposals. **Do not** add
them to the cockpit on impulse — write a separate proposal first.

- **ntfy event triggers** (push when CHECK blocks, finding logged, etc.) — separate concern
- **Per-project / per-user ntfy topic routing** — separate concern
- **Priority feedback / learning loop** (👍/👎 calibrating thresholds) — separate concern
- **Goal task progress bars on the status row** — defer until "I can't tell what this Claude is working on" actually surfaces
- **tmux-pane scan as primary discovery** — instances are discovered via state files; tmux-pane scan is at most a future enrichment
- **Process-level "is Claude alive in this pane" detection** — defer; transaction-state staleness is a sufficient proxy for v1

---

## Wiring summary

- `empirica/core/cockpit/__init__.py` — public surface
- `empirica/core/cockpit/sentinel_pause.py` — wraps the existing pause file
- `empirica/core/cockpit/loop_registry.py` — registry CRUD with atomic writes
- `empirica/core/cockpit/instance_state.py` — discovery + aggregation
- `empirica/core/cockpit/instance_actions.py` — kill / forget / label
- `empirica/core/cockpit/liveness.py` — alive/dead detection (tmux pane introspection + PID check)
- `empirica/core/cockpit/render.py` — ANSI-aware pretty + JSON renderers, footer hints
- `empirica/cli/tui/cockpit_app.py` — Textual interactive app (empirica tui)
- `empirica/cli/command_handlers/cockpit_commands.py` — handler wrappers
- `empirica/cli/parsers/cockpit_parsers.py` — argparse subcommand groups
- `empirica/plugins/claude-code-integration/skills/loop-cron/SKILL.md` — `/loop` integration template
- `empirica/plugins/claude-code-integration/hooks/session-init.py` — PID/PPID capture for kill

Sentinel whitelist additions in `sentinel-gate.py:EMPIRICA_TIER1_PREFIXES`:

- `empirica noetic-batch` (was the open bug — IS a noetic operation)
- `empirica sentinel ` (subcommand group)
- `empirica loop ` (subcommand group)
- `empirica instance ` (subcommand group)
- `empirica status` (cockpit overview)
- `empirica tui` (interactive cockpit — destructive ops are modal-confirmed)

## Liveness — what counts as "alive"

By default `status` and `tui` only show instances where Claude is actually
running. The signal hierarchy:

| Signal | Verdict |
|---|---|
| Instance is the current one (running this code) | alive (always) |
| `tmux_N` and `tmux list-panes` shows pane running `claude` (or `node`) | alive |
| `tmux_N` and pane exists but runs another command (e.g. `bash`) | dead — Claude exited |
| `tmux_N` and pane doesn't exist | dead — terminal closed |
| Non-tmux, captured PPID is alive | alive |
| Non-tmux, captured PPID is dead | dead |
| Non-tmux, no PID, but state file touched < 1h ago | alive (fresh-session benefit-of-doubt) |
| Otherwise | dead |

Override with `--include-dead` (CLI) or `D` keybinding (TUI) to surface
everything regardless of liveness — useful for diagnosing what's about to
get pruned, or for forensics on abandoned instances.

## Kill semantics

| Instance shape | Method | Notes |
|---|---|---|
| `tmux_N` | `tmux kill-pane -t %N` | Closes the pane, kills the CC process inside |
| anything else, PPID alive | `kill -TERM <ppid>` (or `-KILL` with `--force`) | PPID is the long-lived CC parent, captured by session-init |
| anything else, no PID | error: "no tracked PID" | User must close the terminal manually, then `instance forget` |

`kill` and `forget` refuse to target the current instance unless `--yes`
is passed — the cockpit shouldn't be the way you accidentally kill the
shell you're typing into.

`forget` only touches `~/.empirica/*_{id}*` files. Project-tree state
(`<project>/.empirica/active_transaction_*.json`) is the project's record,
not the instance's, and is left alone.

The previous `status` alias on `system-status` was removed — the new
top-level `status` command takes that name; `system-status` keeps its
distinct kernel-style diagnostic role.

## Notification primitive

Cockpit views surface state. To push state to external channels (ntfy,
log files, stdout) loops and hooks call `empirica notify emit ...` —
the dispatcher resolves which backend to use from
`~/.empirica/notify.yaml` (or built-in defaults). Loops never need to
know about ntfy specifically; backends can be swapped without touching
call sites. See [`NOTIFY.md`](NOTIFY.md) for the full spec.

## Loop self-scheduling

Loops are self-scheduling: the body owns the schedule, the scheduler
is dumb. Each fire heartbeats its result (`found` / `empty` / `fail` /
`paused`), calls `empirica loop schedule-next` to compute the next-fire
timestamp from backoff state, and installs a one-shot scheduler job at
that timestamp. The returned `next_scheduled_job_id` flows back via
heartbeat so `empirica loop pause` can cancel it.

### Mechanical pause via pickup hook (1.9.6+)

Pause now means the scheduler is **mechanically** silent — not just a
sidecar flag the body checks. When `empirica loop pause` runs against
a loop with `scheduler_kind=cron-create` and a recorded
`next_scheduled_job_id`, the pause handler:

1. Writes the `loop_paused_*` sidecar (advisory layer — body backstop).
2. Clears `next_scheduled_job_id` from the registry.
3. Writes `~/.empirica/loop_uninstall_pending_{instance_id}_{name}.json`
   containing the recorded job_id.
4. The owning instance's `loop-uninstall-pickup.py` UserPromptSubmit
   hook surfaces it as a system-reminder asking the owning Claude to
   call `CronDelete(<job_id>)`. That Claude executes from inside its CC
   session and the cron is genuinely off.
5. **Backstop:** if Claude doesn't run `CronDelete` in time, the body's
   pause check at the next fire still exits without scheduling the next
   one. Loop dies cleanly after at most one more silent fire.

Backoff stretches the actual fire interval — empty streaks compound,
found/fail snap back to base. Pause-as-mechanical-cancel was the
1.9.6 fix for the token-bleed bug where `empirica loop pause` left
the recurring CronCreate job firing every interval.

### The pickup-hook pattern (loop + listener install/uninstall)

Four UserPromptSubmit hooks share the same shape:

| Hook | Trigger | Asks owning Claude to |
|---|---|---|
| `loop-install-pickup.py` | `empirica loop install-request` writes `loop_install_pending_*` | invoke `/loop` with substituted `loop-cron` prompt → `CronCreate` |
| `loop-uninstall-pickup.py` | `empirica loop pause` writes `loop_uninstall_pending_*` | call `CronDelete(<job_id>)` |
| `listener-install-pickup.py` | `empirica listener install-request` writes `listener_install_pending_*` | invoke `/inbox-listener` with substituted prompt → arm curl + Monitor |
| `listener-uninstall-pickup.py` | `empirica listener pause` writes `listener_uninstall_pending_*` (when active runtime present) | `TaskStop(<monitor_task_id>)` + drop held curl + clean `listener_active_*.json` |

Each hook reads pending files for the running instance, surfaces them as
`additionalContext` (a system-reminder on the next prompt), and removes
the file so the request only fires once. The cockpit prompts Claude to
execute privileged tools (CronCreate / CronDelete / Monitor / TaskStop)
that only work inside a CC session; the empirica CLI never calls them
directly.

### Adding a loop from the cockpit

`empirica loop install-request --instance <ID> --name <NAME>
--interval <INTERVAL>` registers the loop in
`loops_{instance_id}.json` (visible in cockpit immediately) and queues
the install via the hook above. The TUI's L button does the same when
clicked on an instance with an empty registry, reading the canonical
loop config from `<project>/.empirica/project.yaml`'s `cockpit.loops`
block.

### Adding a listener from the cockpit

`empirica listener install-request --instance <ID> --name <NAME>
--topic ntfy:<TOPIC>` registers the listener in
`listeners_{instance_id}.json` and queues the install. The TUI's E
button does the same, reading `<project>/.empirica/project.yaml`'s
`cockpit.listeners` block. Once armed, the listener body holds an HTTP
connection (curl -N) and wakes when an event arrives — sub-second
latency from publish, zero token cost at idle. The listener body
implementation lives in `empirica/core/loop_scheduler/listener.py`;
the install-request flow mirrors loops via
`empirica/core/cockpit/listener_install_request.py`.

### Project-canonical loop/listener config

`.empirica/project.yaml` accepts a `cockpit:` block defining the
project's canonical loops and listeners. The TUI's L/E click on an
empty registry reads this block and queues install for each entry
— first click installs, subsequent clicks toggle pause/resume.

```yaml
cockpit:
  loops:
    - name: outreach-inbox-poll
      kind: cron
      cron: "8,23,38,53 * * * *"
      description: "Outreach Claude self-poll"
  listeners:
    - name: outreach-inbox
      topic: ntfy:outreach-claude-inbox
      description: "Cortex orchestration inbox"
      on_wake: "Process new orchestration message"
```

Reader at `empirica.core.cockpit.project_cockpit_config` — non-raising
on missing file / malformed YAML; entries that don't pass shape
validation (loop needs `name`; listener needs `name`+`topic`) are
silently filtered. Strict business validation happens downstream in
the install-request handlers.

Resume after pause uses the same pickup path: `empirica loop fire
NAME` is for manual one-shot fires; for resuming a paused loop, the
operator re-issues the install-request and the next prompt in the
target instance bootstraps the cron.

Skill template: `loop-cron`.
