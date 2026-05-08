# Notify Dispatcher

> **Version:** 1.9.2 (no dispatcher changes; doc stamp added 2026-04-30)

Single CLI verb every loop body and hook calls to send notifications.
The dispatcher decides where the event goes based on user config
(`~/.empirica/notify.yaml`). Loops and listeners never need to know
about ntfy specifically — backends can be swapped without touching
call sites.

**Spec:** `../specs/PROPOSAL_NOTIFY_DISPATCHER.md`
**Cockpit integration:** the cockpit `summary.notify_dispatcher` block
(in `empirica status --json`) and the TUI dispatch-status row both
surface 5 most recent emits, backend status, and 24h fallback count.
See [`COCKPIT.md`](COCKPIT.md).

---

## CLI

```bash
empirica notify emit --severity <info|warning|critical> --title "..." --message "..." [flags]
empirica notify config         # print effective config (secrets redacted)
empirica notify backends       # list registered backends + configured-status
empirica notify test           # send a test event end-to-end
```

`emit` flags:

| Flag                  | Purpose                                                                 |
| --------------------- | ----------------------------------------------------------------------- |
| `--severity`          | Required. `info` / `warning` / `critical`. Drives default routing.      |
| `--title`             | Required. One-line title.                                               |
| `--message`           | Required. Body text.                                                    |
| `--rationale`         | Why this event is being raised (surfaces in detail-capable backends).   |
| `--tags`              | Comma-separated, e.g. `"clipboard,empirica"`.                           |
| `--click-url`         | Primary tap-through URL.                                                |
| `--actions`           | ntfy `Label1\|URL1,Label2\|URL2` format. No DSL invented.               |
| `--source`            | Opaque emitter id. Convention: `loop:<name>` / `hook:<event>` / `manual`. |
| `--topic-override`    | Explicit topic (bypasses routing).                                      |
| `--backend-override`  | Explicit backend (`stdout`, `log`, `ntfy`) — bypasses routing.          |
| `--dry-run`           | Print resolved decision; do not emit.                                   |
| `--output`            | `json` (default) or `human`.                                            |

Exit codes: `0` emitted (or dry-run completed), `1` config error, `2` backend rejected (4xx/5xx), `3` backend unavailable (network/timeout).

---

## Three sharp edges

The implementation enforces these because they bit us before:

1. **ntfy uses JSON publish format only.** Headers can't carry emoji
   (latin-1 codec error). Both David and Cortex Claude paid for this
   bug. The JSON body form is UTF-8 native and avoids it entirely.
2. **`--actions` mirrors ntfy's `Label|URL` format exactly.** No DSL.
   When ntfy adds new action types we forward without renegotiating.
3. **Auth via env var.** Config names the env var (`auth_env`); the
   secret stays in the environment. Inline secrets in YAML are
   redacted defensively.

---

## Out of scope (by design)

- Hook auto-triggers, severity inference, action-callback receivers
- claude.ai chat backend
- Anything that turns this primitive into a workflow

These belong to user-side automation and downstream services. The
dispatcher stays a tiny, pluggable verb so it doesn't accumulate
opinions.

---

## Architecture

```
empirica/core/notify/
├── __init__.py        # Re-exports public API
├── event.py           # NotifyEvent, EmitResult, parse_tags, parse_actions
├── config.py          # NotifyConfig, RoutingRule, load_config, redact_config
├── dispatcher.py      # _resolve, dispatch (with fallback path)
└── backends.py        # StdoutBackend, LogBackend, NtfyBackend
```

### Resolution order (per emit)

1. `--backend-override` if set.
2. First matching routing rule (severity / source-glob / topic-glob / tag-glob).
3. `default_backend`.

If the resolved backend has `is_configured() == False` (e.g. ntfy auth
env unset), the dispatcher **falls back to stdout AND emits a warning
to stderr**. Notifications are never silently dropped.

### Built-in defaults

Empirica works out of the box without `notify.yaml`:

- `default_backend: stdout`
- `log` backend pre-configured at `~/.empirica/notify.log` with rotation (10 MB / 5 files).

Drop a YAML to add ntfy or rewire routing — missing top-level keys
fall back to the built-in defaults. File errors are non-fatal.

### Severity → ntfy priority

| Severity   | ntfy priority |
| ---------- | ------------- |
| `info`     | 3 (default)   |
| `warning`  | 4 (high)      |
| `critical` | 5 (max)       |

---

## Example `~/.empirica/notify.yaml`

```yaml
default_backend: ntfy

backends:
  ntfy:
    server: https://ntfy.sh
    auth_method: bearer        # basic | bearer | none
    auth_env: NTFY_TOKEN       # env var holding the secret
    default_topic: empirica-david
    default_priority: 3

routing:
  - match: {severity: critical}
    backend: ntfy
    topic: empirica-critical
  - match: {source: "loop:metrics"}
    backend: log              # high-volume, log-only
  - match: {tag: "oncall"}
    backend: ntfy
    topic: empirica-oncall

defaults:
  click_url_base: https://empirica.localhost/cockpit
```

---

## Caller patterns

**From a loop body:**

```bash
empirica notify emit \
  --severity warning \
  --title "Drift detected" \
  --message "Calibration drift exceeds threshold" \
  --tags "drift,calibration" \
  --source "loop:drift-monitor" \
  --click-url "https://cockpit/calibration"
```

**From a hook (postflight summary):**

```bash
empirica notify emit \
  --severity info \
  --title "🔔 POSTFLIGHT closed" \
  --message "$summary" \
  --tags "empirica,postflight" \
  --source "hook:postflight"
```

**Dry-run (CI / debugging):**

```bash
empirica notify emit --severity info --title "x" --message "y" --dry-run
```

---

## Tests

`tests/core/test_notify_dispatcher.py` covers:

- `parse_tags` / `parse_actions` edge cases (incl. ntfy `Label|URL` shape)
- Built-in defaults + YAML merge
- Routing: severity match, source/topic/tag globs, first-match-wins
- Backend overrides bypass routing
- Fallback paths: unknown backend, not-configured
- Dry-run skips emit
- Backend interface contracts (stdout, log JSONL, ntfy JSON payload shape + emoji safety)
