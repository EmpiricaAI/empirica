# Empirica Cockpit

> Multi-instance state visibility and per-instance controls.
>
> Spec: `OutreachShared/empirica-final-docs/PROPOSAL_SENTINEL_LOOP_TUI.md`

---

## What it is

Three CLI surfaces on top of one core module:

| Command | Module | Purpose |
|---|---|---|
| `empirica sentinel <pause\|resume\|status>` | `empirica.core.cockpit.sentinel_pause` | Per-instance noetic firewall toggle |
| `empirica loop <register\|heartbeat\|pause\|...>` | `empirica.core.cockpit.loop_registry` | Per-instance loop registry CRUD |
| `empirica instance <kill\|forget\|label>` | `empirica.core.cockpit.instance_actions` | Destructive lifecycle: terminate / scrub state / rename |
| `empirica status [--all\|--instance ID] [--pretty\|--json]` | `empirica.core.cockpit.instance_state` | Cockpit overview, all renderers consume the same JSON |
| `empirica tui` | `empirica.cli.tui.cockpit_app` | Interactive Textual app — clickable buttons + keyboard shortcuts for every verb |

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
| `loops_{instance_id}.json` | `empirica loop register/heartbeat/...` | declarative registry of loops |
| `loop_paused_{instance_id}_{name}` | `empirica loop pause` | empty file; existence = loop paused |
| `instance_label_{instance_id}` | optional, set manually | one-line human label |
| `active_session_{instance_id}` | session-init hook (legacy) | session pointer; fallback for project lookup |
| `<project>/.empirica/active_transaction_{instance_id}.json` | workflow_commands.py | current open transaction |
| `<project>/.empirica/hook_counters_{instance_id}.json` | sentinel-gate.py / hooks | praxic_tool_calls etc. — used to derive phase |

Discovery walks `instance_projects/`, `sentinel_paused_*`, `loops_*.json`,
`active_session_*`, `hook_counters_*.json`, and `context_usage_*.json` and
unions the derived instance_ids.

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
- **Goal subtask progress bars on the status row** — defer until "I can't tell what this Claude is working on" actually surfaces
- **tmux-pane scan as primary discovery** — instances are discovered via state files; tmux-pane scan is at most a future enrichment
- **Process-level "is Claude alive in this pane" detection** — defer; transaction-state staleness is a sufficient proxy for v1

---

## Wiring summary

- `empirica/core/cockpit/__init__.py` — public surface
- `empirica/core/cockpit/sentinel_pause.py` — wraps the existing pause file
- `empirica/core/cockpit/loop_registry.py` — registry CRUD with atomic writes
- `empirica/core/cockpit/instance_state.py` — discovery + aggregation
- `empirica/core/cockpit/instance_actions.py` — kill / forget / label
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
