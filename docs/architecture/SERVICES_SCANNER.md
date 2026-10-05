# AI Service Scanner

`empirica scan` takes a one-shot, deterministic inventory of what is running on an AI-touching dev machine: processes, sockets, scheduled tasks, env-var names, plugin manifests and registered MCP servers. It makes no judgment and takes no action. Judgment is a separate step, done by an AI session following the `services-auditor` skill over the saved snapshot. A cron-style loop (`empirica services-audit`) repeats the scan and reports what is new.

This document graduates `PROPOSAL_AI_SERVICE_SCANNER.md`. It describes what the code does today.

## The pieces

```
empirica scan ──► collect_snapshot() ──► Snapshot (JSON)
                       │                     │
        read-surface filter per collector    ├─ --save ─► ~/.empirica/scans/<scan_id>.json
                                             │            ~/.empirica/last_scan_<project_id>.json
                                             │            ~/.empirica/scan_history_<project_id>.jsonl
                                             │
                                             ├─ --explain ─► hand-off to the /services-auditor skill (AI judgment)
                                             └─ cockpit #services panel reads last_scan_<project_id>.json

empirica services-audit ─► scan --save ─► diff vs previous history entry ─► notify on novelty
```

| Path | Role |
|---|---|
| `empirica/core/scanner/snapshot.py` | `Snapshot` dataclass and the `collect_snapshot` orchestrator. A collector that raises is recorded in `Snapshot.errors` and the snapshot still serializes |
| `empirica/core/scanner/read_surface.py` | Parses `cockpit.scanner.read_surface` and intersects it with a hard-coded field universe per collector |
| `empirica/core/scanner/{processes,network,scheduled,env_names,manifests}.py` | The collectors |
| `empirica/core/scanner/report.py` | Markdown renderer |
| `empirica/cli/command_handlers/scan_commands.py`, `empirica/cli/parsers/scan_parsers.py` | `scan`, `scan-history`, `scan-show`, `scan-diff`, `services-audit` |
| `empirica/core/cockpit/services_view.py` | Reads the last snapshot for the cockpit |
| `empirica/data/security-corpus/` | Five framework files the auditor cites |
| `empirica/plugins/claude-code-integration/skills/services-auditor/`, `.../services-audit-cron/` | The judgment skill and the cron wiring |

## Commands

| Verb | What it does |
|---|---|
| `empirica scan [--output markdown\|json] [--save] [--explain] [--project-id ID]` | One snapshot. Markdown by default. `--save` persists it; `--explain` forces `--save` and prints a hand-off pointing the AI at `/services-auditor` |
| `empirica scan-history [--limit N] [--output human\|json]` | Past scans for the project, newest first (`--limit 0` shows all) |
| `empirica scan-show <scan_id> [--output markdown\|json]` | A saved snapshot. Accepts a UUID prefix of 8 or more characters. The markdown form is a compact summary; use `--output json` for the full snapshot |
| `empirica scan-diff <a> <b> [--output human\|json]` | Added, removed and changed processes (by name) and added and removed listeners between two saved scans, plus both coverage blocks |
| `empirica services-audit [--no-notify] [--output human\|json]` | One fire of the audit loop: scan and save, diff against the previous history entry, notify on novelty. JSON output carries `result` (`found`, `empty` or `fail`), `scan_id`, `prior_scan_id` and `novelty` |

The scanner's own process appears in its output with `is_scanner_self: true`, so the cost of scanning is visible. `psutil` is required for the process and network collectors; without it they return empty payloads and record why in their coverage block.

## The read-surface

`cockpit.scanner.read_surface` in `.empirica/project.yaml` declares which fields each collector may emit. The YAML is intersected with the hard-coded universe in `read_surface.py`, so a typo or stray entry cannot widen the surface. When the block is absent, `DEFAULT_READ_SURFACE` applies; a project that wants less overrides only the lists it cares about.

```yaml
cockpit:
  scanner:
    read_surface:
      process: [pid, cmdline, parent_pid, age_seconds, working_dir,
                num_open_files, cpu_percent, memory_mb, is_scanner_self]
      network: [pid, peer_host, peer_port, listening_ports,
                local_address, local_port, status]
      filesystem: [plugin_manifest_paths, recently_touched_model_weights,
                   env_files_present]
      process_env: [var_names_only]
      scheduled: [cron_entries, systemd_user_units, launchd_agents]
      mcp: [registered_servers, active_connections]
    relevant_globs_for_coverage:
      code:  ["empirica/**/*.py"]
      docs:  ["docs/**/*.md"]
```

The universe is wider than the default: `name`, `username`, `family` and `type` can be added to `process` and `network`. `recently_touched_model_weights` is accepted but the collector currently emits an empty list for it.

## What each collector reads

| Collector | Reads | Never reads |
|---|---|---|
| processes | per-process fields through `psutil`, cmdline clipped to a length cap | environment values |
| network | `psutil.net_connections(kind="inet")`: endpoints, status, and a sorted `listening_ports` list of port numbers | packet contents, headers |
| scheduled | `crontab -l` lines (verbatim, they are the user's own config), systemd user units, launchd agents | unit and plist contents |
| process_env | environment variable names matching AI and secret patterns (`API_KEY`, `TOKEN`, and others in `env_names.py`) | values, ever |
| filesystem / mcp | plugin manifest paths under `~/.claude/plugins`, `.env` file presence, MCP servers registered in `~/.claude.json` first, then `~/.claude/mcp.json` and `~/.claude/settings.json`, de-duplicated by name and scope | file contents, MCP traffic |

## Snapshot shape

Top level: `scan_id`, `started_at`, `finished_at`, `host`, `platform`, `scanner_pid`, `snapshot`, `errors`. Under `snapshot`:

- `processes`: list of rows, filtered to the read-surface
- `network`: `connections` and `listening_ports`
- `scheduled`: `cron_entries`, `systemd_user_units`, `launchd_agents`
- `process_env`: `var_names_only`
- `filesystem`: `plugin_manifest_paths`, `env_files_present`, `mcp_registered_servers`, as enabled by the surface
- `read_surface_summary`: the effective field lists, so a reader can tell what was deliberately not collected
- `coverage`: integrity per collector (see below)

## Scanner integrity coverage vs agent self-coverage

The word "coverage" means two different things here.

| Concept | Question | Where it lives |
|---|---|---|
| Scanner integrity coverage | Did the collector capture every row the OS offered? | `snapshot.coverage`: `attempted`, `succeeded`, `ratio`, `skip_reasons` for processes and network; source counts for scheduled; counts for process_env and filesystem; per-glob match counts under `relevant_globs` |
| Agent self-coverage | Of the relevant material, how much did the AI actually inspect before stating its confidence? | the optional `coverage` block on `postflight-submit` |

The scanner fills only the first. The second is reported by whoever does the judging.

### The POSTFLIGHT `coverage` block

`postflight-submit` accepts an optional `coverage` object (`PostflightInput.coverage` in `empirica/cli/validation.py`). Documented dimensions are `files_inspected` / `files_relevant`, `artifacts_inspected` / `artifacts_relevant`, `citations_made` / `citations_available`, `subagents_dispatched` / `subagents_relevant`, `tools_invoked` / `tools_available`, `scalar` (0.0 to 1.0) and `notes`. Other keys pass through untouched. The block is stored with the POSTFLIGHT reflex record and echoed back as `coverage` in the response.

It is informative, not gating: nothing fails when coverage is low or absent. Its purpose is to make "95% confidence on 7% of the files" visible so the next transaction can correct for it. It is not specific to the scanner; any transaction may send it.

## Judgment: the services-auditor skill

`empirica scan --explain` saves the snapshot and prints the hand-off (or, with `--output json`, a structured envelope for automation). An AI session then loads `/services-auditor`, which runs an ordinary transaction with `work_type=audit`:

1. Read the saved snapshot and the corpus in `empirica/data/security-corpus/` (the skill also names `~/.empirica/security-corpus/` as a user-editable copy if present; nothing in the code I found creates that directory).
2. Tier 1: a cheap pre-filter for AI-touching entries (cmdline, env-var names, ports, MCP registry).
3. Tier 2: full judgment per AI-touching entry against the corpus.
4. Emit artifacts under the confidence and citation ladder below.
5. Report process, citation and listener coverage in POSTFLIGHT.

| Confidence | Cites a corpus section | Artifact |
|---|---|---|
| 0.95 or more | yes | finding |
| 0.6 to 0.95 | yes | assumption |
| below 0.6 | any | unknown |
| any | no | unknown (uncited downgrades) |

The skill is read-only by design: it emits `recommended_action` strings and never kills a process or edits configuration.

### The corpus

`empirica/data/security-corpus/` holds `owasp-llm-top10.md`, `owasp-agentic-top10.md`, `nist-ai-rmf.md`, `mitre-atlas.md` and `google-saif.md`, with section bodies populated at summary grade. Section IDs follow the source frameworks, so a citation stays valid when a body is refreshed. Each file declares its own freshness in a `Status:` line. The corpus README mentions a scheduled corpus-refresh loop; I found no code that implements one.

## Scheduled audits: `services-audit`

`empirica services-audit` is the body of the loop registered by the `services-audit-cron` skill (recommended cadence `0 6 1,15 * *`, wired through `empirica loop register`, `loop status` and `loop heartbeat`). One fire:

1. `collect_snapshot()` and persist (same files as `scan --save`).
2. Take the second-to-last entry of `scan_history_<project_id>.jsonl` as the previous scan and diff the two.
3. `result` is `found` if the diff added any process name or listener, else `empty`. `fail` if no project resolves or the scan raises.
4. On novelty, and unless `--no-notify`, dispatch a `warning` `NotifyEvent` through `empirica.core.notify`. A failed dispatch is reported in the output and is not fatal.

### Known limits of the diff

Read from `_compute_scan_diff` and the collectors, not observed in a run:

- Processes are grouped by the row's `name` (falling back to `comm`). The default process surface does not include `name`, so under the default every row groups as `?` and the process diff only reflects count changes of that one bucket. Add `name` to `read_surface.process` for a meaningful process diff.
- Listeners are diffed as `host:port` from dict entries, but the collector emits `listening_ports` as a list of integers. As written, the listener diff finds nothing, so `listeners_added` stays empty.

Both limits mean `services-audit` can report `empty` while something new is running. Treat the result as a floor, not a clean bill.

## Cockpit `#services` panel

`read_services_summary` in `services_view.py` reads `last_scan_<project_id>.json` and returns counts (processes, listening ports, MCP servers, plugin manifests, cron entries, env-var names, collector errors), the process integrity ratio, age and a `fresh` flag (younger than 24 hours). `aggregate_instance_state` embeds it per instance under `services`.

In the TUI (`empirica/cli/tui/cockpit_app.py`) the panel sits under `#compliance`, and the `i` key toggles detail. The head line is always shown: a glyph, process count, listening count, integrity percentage and age.

| State | Glyph |
|---|---|
| no errors, snapshot under 24h | check |
| snapshot 24h or older | warning |
| the scan recorded collector errors | cross; detail expands by default |

The panel reports inventory only. It does not show auditor findings.

## Privacy posture

- Environment values are never read; only names that match the patterns appear.
- Network data is endpoint metadata only.
- File contents are never read: paths, `.env` presence and MCP registration only.
- `crontab -l` lines are recorded verbatim; systemd unit and launchd plist contents are not.
- Scans are written to the user's home under `~/.empirica/`, not into the project, and no scan data leaves the machine except through the notification you configure.

## Not built

- Multi-host fleet view.
- Inventory of hosted agents on cloud accounts.
- Packet inspection (explicit non-goal).
- Any action layer that stops processes.
- The corpus-refresh loop and any retrieval over the corpus.
