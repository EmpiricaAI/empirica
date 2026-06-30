---
description: "Toggle Empirica tracking on/off, check sentinel status, fix OFF-RECORD statusline. Use when: 'sentinel paused', 'sentinel off', 'turn off empirica', 'pause tracking', 'go off record', 'why is statusline showing off-record', 'empirica status', '/empirica on', '/empirica off'. Controls sentinel_paused file (NOT sentinel_enabled)."
allowed-tools: ["Bash(empirica *)"]
---

# /empirica - Epistemic Tracking Toggle (Per-Instance)

**Arguments:** `on` | `off` | `status` (add `--global` to `on`/`off` to affect ALL instances)

**This command delegates to the canonical `empirica` CLI** (`empirica off` /
`empirica on` / `empirica sentinel status`). The CLI resolves the instance
exactly the way the Sentinel gate does — via `session_resolver.get_instance_id()`
(`EMPIRICA_INSTANCE_ID` → `TMUX_PANE` → `TERM_SESSION_ID` → `WINDOWID` → TTY).
Do **NOT** re-implement instance resolution inline: a divergent resolver writes a
pause file under a name the gate never reads, so the pause silently misses (the
exact bug this command used to have).

**Scope:**
- Default = **this instance only** (`~/.empirica/sentinel_paused_{instance_id}`).
- `--global` = **all instances** (`~/.empirica/sentinel_paused`).

These verbs are recognized as meta-control toggles by `sentinel-gate.py`, so they
run even mid-loop / when the gate is otherwise holding (a gate must never block
the verb that clears it).

## For `/empirica off`:

Pause this instance (or all instances with `--global`). Pass through `--global`
only if the user explicitly asked to turn Empirica off everywhere.

```bash
# this instance only (default)
empirica off --reason "User requested /empirica off"

# OR, if the user said "turn off empirica everywhere / globally":
# empirica off --global --reason "User requested /empirica off --global"
```

Then confirm to the user: **Empirica is now OFF-THE-RECORD (this instance — or
"all instances" if `--global`).** Sentinel enforcement paused. Use `/empirica on`
to resume.

## For `/empirica on`:

Resume this instance (or clear the global pause with `--global`).

```bash
# this instance only (default)
empirica on

# OR, to clear a global pause:
# empirica on --global
```

The CLI reports whether anything was actually paused (and, when resuming an
instance while a global pause still applies, that the global pause is still in
effect). Relay that to the user.

Confirm: **Empirica is now ON-THE-RECORD.** Sentinel enforcement resumed. Run
PREFLIGHT to start a new epistemic loop.

## For `/empirica status`:

Show the current pause state (instance + global) for this instance.

```bash
empirica sentinel status
```

Relay the reported scope (`instance` / `global` / `none`) and, if paused, how
long it has been off-the-record.
