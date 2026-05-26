# AI Service Scanner — Phase 1 (Deterministic Inventory) + Phase 2 T1 (Auditor Hand-off)

**Status:** Phase 1 shipped + Phase 2 T1 shipped (this document graduates `PROPOSAL_AI_SERVICE_SCANNER.md`).
**Phases:**
- **1 (shipped):** deterministic data collection, read-surface YAML, Markdown report, bundled corpus stubs
- **2 T1 (shipped):** `services-auditor` skill + `empirica scan --explain` hand-off
- **2 T2 (shipped):** cockpit `#services` panel
- **2 T3 (shipped):** POSTFLIGHT coverage block (paper section 4.1)
- **2 T2.5 (next):** ntfy hook for high-confidence audit findings
- **3:** biweekly loop + history/diff verbs + corpus-refresh
- **4:** RAG over corpus, dynamic CVE feed, fleet view

---

## What Phase 1 ships

`empirica scan` runs a one-shot deterministic snapshot of the running
state of an AI-touching dev machine and emits Markdown (default) or
JSON. No persistent daemon, no AI judgment, no actions taken.

```bash
empirica scan                        # Markdown report
empirica scan --output json          # JSON envelope, suitable for piping
empirica scan --save                 # Persist to ~/.empirica/scans/<scan_id>.json
```

The scanner's own process appears in its output, tagged with
`is_scanner_self: true`, so you always see what the scanner cost.

## Architecture

```
empirica/
├── core/scanner/                    # All collection lives here
│   ├── snapshot.py                  # Snapshot dataclass + collect_snapshot orchestrator
│   ├── read_surface.py              # YAML parser + universe-intersection
│   ├── processes.py                 # psutil process_iter + per-row read-surface filter
│   ├── network.py                   # psutil net_connections, listening + established
│   ├── scheduled.py                 # crontab + systemd-user + launchd
│   ├── env_names.py                 # env var NAMES only (no values, ever)
│   ├── manifests.py                 # plugin.json + ~/.claude/mcp.json
│   └── report.py                    # Markdown renderer
├── data/security-corpus/            # 5 bundled stubs (used by Phase 2)
└── cli/parsers/scan_parsers.py
└── cli/command_handlers/scan_commands.py
```

## Read-surface — the agent's read permission boundary

`cockpit.scanner.read_surface` in `.empirica/project.yaml` declares
which fields each collector is allowed to emit. The scanner intersects
the user's YAML with a hard-coded universe per collector, so a typo or
stray entry can never silently widen the surface.

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
      process_env: [var_names_only]   # the only legal emission — values never read
      scheduled: [cron_entries, systemd_user_units, launchd_agents]
      mcp: [registered_servers, active_connections]
    relevant_globs_for_coverage:
      code:  ["empirica/**/*.py"]
      docs:  ["docs/**/*.md"]
      audit: ["**/*.json"]
```

If the block is absent, the default surface (everything in the proposal)
is used. Projects that want a narrower surface override only the
relevant collector lists.

## Two coverage concepts — keep them distinct

Coverage is the methodological pillar this product rides on, but the
word means two different things at different layers. Phase 1 ships the
first; Phase 2 layers the second on top of the same `relevant_globs`
substrate.

| Concept | Question | Phase | What it grounds |
|---|---|---|---|
| **Scanner integrity coverage** | _"Did the deterministic collector successfully capture every row available to it?"_ | Phase 1 (now) | Snapshot truthfulness — were 491 of 491 processes read, or did permission denials skip some? |
| **Agent self-coverage** | _"Of the relevant material that exists, how much did the AI agent actually inspect before claiming its confidence?"_ | Phase 2 (next) | Human trust — divide claimed confidence by reading coverage to get an effective trust score |

Both surface in the snapshot's `coverage` block. Phase 1 fills in the
integrity metrics (`processes.attempted/succeeded/ratio`,
`network.attempted/succeeded/ratio`, etc.) and counts files matching
`relevant_globs_for_coverage` patterns. Phase 2 will add a parallel
`agent_coverage` block recording which of those matched files the
auditor agent actually read, plus which of the 5 bundled corpus files
the agent cited from before emitting each finding.

The grounding ladder this enables (per the proposal):

- ≥ 0.95 confidence + cited corpus section → `finding`
- 0.6–0.95 confidence + cited → `assumption`
- < 0.6 confidence OR uncited → `unknown`

## Snapshot schema (Phase 1 deterministic only)

```jsonc
{
  "scan_id": "uuid",
  "started_at": 1746098451.93,
  "finished_at": 1746098456.94,
  "host": "machine-name",
  "platform": "Linux 6.17.0-22-generic",
  "scanner_pid": 2958649,
  "snapshot": {
    "processes": [{"pid": 2958649, "is_scanner_self": true, ...}, ...],
    "network": {"connections": [...], "listening_ports": [22, 53, ...]},
    "scheduled": {"cron_entries": [...], "systemd_user_units": [...], "launchd_agents": [...]},
    "process_env": {"var_names_only": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", ...]},
    "filesystem": {"plugin_manifest_paths": [...], "mcp_registered_servers": [...]},
    "coverage": {
      "processes": {"attempted": 491, "succeeded": 491, "ratio": 1.0, ...},
      "network": {"attempted": 143, "succeeded": 143, "ratio": 1.0, ...},
      "scheduled": {"sources_checked": 3, "sources_yielding": 1, "total_entries": 2},
      "process_env": {"total_env_vars": 104, "interesting_matches": 9, "ratio": 0.087},
      "filesystem": {...},
      "relevant_globs": {
        "code": {"patterns": {"empirica/**/*.py": 404}, "total_matches": 404},
        "docs": {"patterns": {"docs/**/*.md": 210}, "total_matches": 210}
      }
    }
  },
  "errors": []
}
```

Persistence (when `--save` is passed):

| Path | Purpose |
|---|---|
| `~/.empirica/scans/<scan_id>.json` | Full snapshot |
| `~/.empirica/last_scan_<project_id>.json` | Latest scan, for cockpit |
| `~/.empirica/scan_history_<project_id>.jsonl` | Append-only audit trail (one line per scan, summary-only) |

## Bundled security corpus

Five stub markdown files at `empirica/data/security-corpus/`:

- `owasp-llm-top10.md`
- `owasp-agentic-top10.md`
- `nist-ai-rmf.md`
- `mitre-atlas.md`
- `google-saif.md`

Phase 1 ships them as title + canonical URL + section-header skeleton.
The Phase 2 services-auditor will cite their section IDs (`Agentic-A06`,
`MEASURE-2.7`, `T1499`, etc.) — those IDs are stable across revisions
even when section bodies are stubs. Real content fills in via the
Phase 3 monthly corpus-refresh loop.

## Privacy posture

- **Process env values:** never read. Only names that match conventional
  AI / secret patterns (`*_API_KEY`, `*_TOKEN`, `OPENAI*`, etc.) appear
  in the snapshot.
- **Network:** metadata only — connection 5-tuple + listening ports.
  No packet contents, no header inspection.
- **Filesystem:** paths only — `.env` file presence, plugin manifest
  locations, MCP server registry. File contents never read.
- **Scheduled tasks:** crontab / unit / plist filenames only —
  `crontab -l` lines are recorded verbatim because they are the user's
  own configuration; systemd unit and launchd plist contents never read.

## Out of scope for Phase 1

| | Why deferred |
|---|---|
| AI judgment of "is this dangerous" | Phase 2 — needs the auditor persona + corpus content |
| Multi-host fleet view | Separate product (`empirica fleet`) |
| Hosted-agent inventory (cloud operators on your account) | Phase 4+ — needs API token introspection |
| Network packet inspection | Explicit non-goal — read-only on metadata only |
| Action layer that kills processes | Read-only by design; recommended_action strings only (Phase 2) |

## Acceptance criteria — Phase 1

- [x] `empirica scan` produces structured JSON snapshot of running processes + sockets + scheduled tasks + env-var names + plugin manifests
- [x] Read-surface YAML in `project.yaml` honored (universe-intersected)
- [x] Markdown report renders cleanly for human consumption
- [x] Scanner shows itself in its own output (self-row tagged)
- [x] No persistent daemon (one-shot only)
- [x] Tests cover: snapshot shape, read-surface enforcement, scanner-self-detection, env-value-leak prevention, coverage roll-up
- [x] `docs/architecture/SERVICES_SCANNER.md` written (this file)
- [x] CHANGELOG entry under `[Unreleased]`

Phase 1 does not yet wire the cockpit `#services` panel, ntfy alerts, or
the biweekly loop. Those are Phase 2 T2+ / Phase 3.

---

## Phase 2 T1 — Auditor hand-off (shipped)

The deterministic Phase 1 snapshot is the ground truth; **judgment**
happens in a separate empirica transaction run by an AI session
(typically Claude Code) following the `services-auditor` skill.

### `empirica scan --explain`

```bash
empirica scan --explain
```

Auto-saves the snapshot (forces `--save`), then emits a hand-off
pointing the AI at the auditor skill:

```
🔍 Scanner snapshot ready for AI judgment (Phase 2).

   scan_id: 823d2f2f-...
   saved to: /home/.../.empirica/last_scan_empirica.json
   processes captured: 496 of 496 (100.0%)
   listening ports: 15
   project_id: empirica

Next: invoke `/services-auditor` to read the snapshot, judge each
AI-touching entry against the bundled security corpus, and emit
findings/assumptions/unknowns with confidence + cited corpus sections.
Citation coverage and process coverage are tracked explicitly in the
auditor's POSTFLIGHT summary.
```

`--output json` returns the same hand-off as a structured envelope so
loops and other automation can dispatch the auditor programmatically.

### `services-auditor` skill

Location: `empirica/plugins/claude-code-integration/skills/services-auditor/SKILL.md`.

Walks the AI through:

1. **PREFLIGHT** with `work_type=audit`, `domain=default`, `criticality=medium`.
2. **Read inputs** — the saved snapshot + the bundled corpus (or the
   user-customizable copy at `~/.empirica/security-corpus/` if present).
3. **Two-tier judgment:**
   - *Tier 1* — cheap AI-touching pre-filter (cmdline / env-var / port /
     MCP-registry signals) drops ~hundreds of processes to ~10–30.
   - *Tier 2* — full taxonomy per AI-touching process: confidence ladder
     gates emission as `finding` / `assumption` / `unknown`.
4. **Citation discipline (load-bearing)** — every finding and
   assumption MUST cite at least one corpus section ID. Uncited
   findings downgrade to `unknown` regardless of model confidence.
5. **Coverage tracking** — process coverage, citation coverage, and
   listener coverage surfaced explicitly in the POSTFLIGHT summary.
   This is the agent self-coverage metric the paper defines.
6. **POSTFLIGHT** with grounded vectors that reflect what was inspected.

### Confidence × citation ladder

| Confidence | Citation present? | Artifact type | Behavior |
|---|---|---|---|
| ≥ 0.95 | yes | `finding-log` | high-trust |
| 0.6 – 0.95 | yes | `assumption-log` | medium-trust |
| < 0.6 | _any_ | `unknown-log` | needs human review |
| _any_ | **no** | `unknown-log` | uncited downgrades |

### Where coverage lives now

Per-finding citation coverage rides in the artifact's `data` JSON
column (no schema change). The auditor's POSTFLIGHT summary aggregates
across the transaction. Phase 2 T3 will land a top-level POSTFLIGHT
`coverage` block (paper section 4.1) that generalizes beyond the
scanner — every empirica transaction gets an inspected/relevant
ratio across files, artifacts, and citations.

### Acceptance criteria — Phase 2 T1

- [x] `services-auditor` SKILL.md drafted with two-tier judgment + citation discipline + coverage tracking
- [x] `empirica scan --explain` flag wired (parser + handler), auto-saves snapshot, emits hand-off
- [x] Hand-off works in both Markdown (human) and JSON (programmatic) output formats
- [x] Tests cover: `--explain` exit code + Markdown hand-off content + JSON envelope shape
- [x] CHANGELOG entry under `[Unreleased]`

---

## Phase 2 T2 — Cockpit `#services` panel (shipped)

The Phase 1 + T1 path writes ``~/.empirica/last_scan_<project_id>.json``
on every `--save` / `--explain` run. The new `#services` panel reads
that file and surfaces a one-line summary in the cockpit, with the
same ergonomics as the existing `#compliance` panel.

### Module shape

`empirica/core/cockpit/services_view.py` mirrors `compliance_view.py`:

- `last_scan_path(project_id)` — resolves the persisted snapshot path
- `read_services_summary(project_path)` — reads the snapshot, returns
  a render-friendly dict with process count, listening-ports count,
  MCP server count, plugin-manifest count, cron-entries count,
  integrity ratio, env-var names count, errors count, fresh flag,
  age seconds, project_id

`aggregate_instance_state` (in `instance_state.py`) embeds the
summary as the `services` key per-instance — same pattern as
`compliance`. Multiple instances of the same project share the same
scanner state, so the per-instance embedding is cheap and keeps the
cockpit row self-contained.

### TUI surface

CSS + compose entries land directly under `#compliance`:

```
#services-header { height: 1; padding: 0 1; color: $text-muted; }
#services { height: auto; min-height: 1; max-height: 8; padding: 0 1; }
```

**Key binding: `i`** (mnemonic: scanner *Inventory*). `s` is bound to
Stop and `c` is bound to Compliance, so `i` was the next sensible
mnemonic. Pressing `i` flips the panel between collapsed (one-line
summary) and expanded (per-category breakdown).

**Failures are always shown via the head line.** The glyph + the
errors-count summary stay visible in the one-line head regardless of
toggle state — operators never lose sight of a problem. `i` toggles
the *additional* detail breakdown (per-category counts), which
default-expands when errors are present and default-collapses when
clean. The toggle is always a real toggle (mirrors compliance's `c`),
but it never hides the existence of an error — the head line carries
that signal in either state.

### Glyphs

| Status | Glyph | Trigger |
|---|---|---|
| Clean + fresh | 🔍 ✓ | no errors, age < 24h |
| Stale | 🔍 ⚠ | age ≥ 24h |
| Errors | 🔍 ✗ | scan recorded collector errors |

### Collapsed view

```
🔍 ✓ 491 procs · 15 listening · integrity 100% (5m ago)
```

### Expanded view (after `i`)

```
🔍 ✓ 491 procs · 15 listening · integrity 100% (5m ago)

  · MCP servers: 3
  · Plugin manifests: 44
  · Cron entries: 2
  · Interesting env-var names: 9
  · Host: AIworkhorse
  (press `i` to collapse)
```

When the auditor lands judgments (Phase 2 T2.5+), this expanded view
will gain `findings_count` / `assumptions_count` / `unknowns_count`.

### Acceptance criteria — Phase 2 T2

- [x] `services_view.py` mirroring `compliance_view.py`'s read/write
      pattern with `last_scan_path` + `read_services_summary`
- [x] `aggregate_instance_state` embeds the `services` block per-instance
- [x] TUI `#services` panel composed under `#compliance`
- [x] `i` keybinding wired to `action_toggle_services`
- [x] Glyphs surface clean / stale / error states
- [x] Tests: 10 covering path resolution, missing files, full shape,
      stale window, missing keys, corrupt JSON
- [x] SERVICES_SCANNER.md updated

---

## Phase 2 T3 — POSTFLIGHT coverage block (shipped)

The agent self-coverage metric is now a first-class field on every
empirica POSTFLIGHT — generalized beyond the scanner. This is the
paper's central observability artifact: confidence × coverage > confidence
alone is meaningful precisely because every transaction can carry a
coverage report the human can read.

### How to use it

`postflight-submit` accepts an optional `coverage` dict alongside
`vectors` / `reasoning` / `grounded_vectors`:

```bash
empirica postflight-submit - <<'EOF'
{
  "vectors": {"know": 0.95, "completion": 1.0, "do": 0.9, ...},
  "reasoning": "Audit complete. 12 of 24 AI-touching processes judged.",
  "coverage": {
    "files_inspected": 12,
    "files_relevant": 491,
    "artifacts_inspected": 5,
    "artifacts_relevant": 50,
    "citations_made": 3,
    "citations_available": 52,
    "subagents_dispatched": 0,
    "subagents_relevant": 0,
    "tools_invoked": 7,
    "tools_available": 12,
    "scalar": 0.07,
    "notes": "auditor over scanner snapshot"
  }
}
EOF
```

### What happens to it

1. **Validated** at `cli/validation.py:PostflightInput.coverage` —
   accepts free-form keys for forward compatibility.
2. **Persisted** into the POSTFLIGHT reflex's `reflex_data` JSON
   alongside `retrospective` (zero schema migration — rides in the
   existing checkpoint metadata column).
3. **Echoed back** in the postflight response as `result["coverage"]`
   so the AI sees its own claimed coverage immediately:

   ```jsonc
   {
     "ok": true,
     "postflight_confidence": 0.95,
     "internal_consistency": "good",
     "coverage": {                          // <-- echoed here
       "files_inspected": 12,
       "files_relevant": 491,
       "scalar": 0.07,
       "...": "..."
     },
     "retrospective": {...},
     "...": "..."
   }
   ```

### Informative, not gating

Per paper section 4.1: this is a **self-correction signal**, not a
threshold. A 95% confidence claim with 7% file coverage is honest
when surfaced; the next PREFLIGHT can see it via `previous_transaction_feedback`
and the AI can self-correct ("I claimed high confidence on thin
inspection — should look at more this time"). No empirica command
fails if coverage is low. No empirica command fails if coverage is
absent — backward compatibility is preserved for every transaction
that doesn't opt in.

### Documented dimensions

| Dimension | What it counts |
|---|---|
| `files_inspected` / `files_relevant` | Files the agent read vs files matching task scope |
| `artifacts_inspected` / `artifacts_relevant` | Knowledge artifacts referenced vs surfaced in PREFLIGHT recall |
| `citations_made` / `citations_available` | Ground-truth sources cited vs sources available in domain corpus |
| `subagents_dispatched` / `subagents_relevant` | Delegated investigations vs decomposable tasks |
| `tools_invoked` / `tools_available` | Tools the agent invoked vs tools available for task type |
| `scalar` | Optional aggregate coverage 0.0–1.0 (typically derived) |
| `notes` | Free-form prose; what the AI wants the human to know |

Free-form keys beyond this list pass through untouched — the schema
is intentionally permissive so coverage definitions can evolve in
specific work types without churning the validator.

### Acceptance criteria — Phase 2 T3

- [x] `coverage` field on `PostflightInput` validation
- [x] `_postflight_parse_config_or_legacy` returns coverage as 6th tuple element
- [x] `_build_postflight_result` echoes coverage (omits when absent or empty)
- [x] Coverage persists to checkpoint metadata in `reflexes.reflex_data`
- [x] Backward-compat verified: transactions without coverage behave identically
- [x] 8 unit tests covering validation shape + parser + echo + omission + persistence
- [x] CHANGELOG entry under `[Unreleased]`
- [x] SERVICES_SCANNER.md updated (this section)
