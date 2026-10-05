# Handoff System

**Module:** `empirica.core.handoff`

A handoff report is a short, written summary one session leaves for the next:
what was done, what was found, what is still unknown, what to do first. It is
authored by the practitioner at the end of a session (or after investigating),
combined with the vector deltas the session measured, and stored twice: as git
notes and as a row in `sessions.db`.

It is for **session-to-session continuity inside a practice**. It is not a
message to a peer (see [`MESSAGING_LAYERS.md`](MESSAGING_LAYERS.md)) and it is
not what `project-bootstrap` loads, which draws on the artifact graph.

Three things share the word "handoff"; only the first is this document:

| Name | What it is |
|---|---|
| `handoff-create` / `handoff-query` | the session handoff report described here |
| `project-handoff` | a project-level summary (`--project-id`, `--summary`, key decisions, patterns, remaining work) stored by `SessionDatabase.create_project_handoff`; not a session report |
| `~/.empirica/compact_handoff*.json` | a small file the pre-compact hook writes for the post-compact hook to read (`hooks/pre-compact.py`); unrelated storage |

---

## Flow

```
handoff-create  ──►  EpistemicHandoffReportGenerator  ──►  HybridHandoffStorage
 (stdin JSON or        picks a type from the session's        ├─ git notes  (HEAD of the repo)
  flags)               PREFLIGHT / CHECK / POSTFLIGHT rows    └─ sessions.db handoff_reports
```

### Input

`empirica handoff-create --help` is the flag reference. Input is either flags or
a JSON config on stdin (`handoff-create -`) or from a file. Required:
`task_summary`, `key_findings` (array), `next_session_context`. Optional:
`remaining_unknowns`, `artifacts`, `planning_only`. `session_id` is taken from
the active transaction when absent. Missing required fields exit 1 with a JSON
error.

### Types

Chosen by `_handoff_determine_type` from what the session database holds:

| Type | Needs | Epistemic deltas |
|---|---|---|
| `complete` | PREFLIGHT and POSTFLIGHT | PREFLIGHT to POSTFLIGHT |
| `investigation` | PREFLIGHT and at least one CHECK | PREFLIGHT to the last CHECK |
| `planning` | nothing (`--planning-only`) | none; `epistemic_deltas` is empty |
| `preflight_only` | PREFLIGHT only | none; built with the planning generator and labelled as an aborted session |

With no assessments and no `--planning-only` the command prints the three ways
to proceed and creates nothing.

### Report contents

`generate_handoff_report` returns a dict with: `session_id`, `ai_id`,
`timestamp`, `handoff_subtype`, `task_summary`, `duration_seconds`,
`epistemic_deltas`, `key_findings`, `knowledge_gaps_filled`,
`remaining_unknowns`, `noetic_tools`, `next_session_context`,
`recommended_next_steps`, `artifacts_created`, `calibration_status`,
`overall_confidence_delta`, plus `markdown` (the readable report) and
`compressed_json` (the stored form).

- `recommended_next_steps` is rule-based: elevated uncertainty, open unknowns,
  the calibration label, and a few threshold checks on know, do and completion.
- `calibration_status` is the practitioner's own POSTFLIGHT
  `calibration_accuracy` when recorded, else a heuristic comparing the know
  delta with the uncertainty delta; investigation handoffs report
  `investigation-only`. It is not the grounded calibration computed from
  external evidence; read that from the calibration verbs.
- `compressed_json` is what makes it cheap to load. It uses short keys (`s`,
  `ai`, `ts`, `task`, `dur`, `deltas`, `findings`, `gaps`, `unknowns`, `next`,
  `recommend`, `artifacts`, `tools`, `cal`), truncates text fields, keeps the
  first five findings, five unknowns, three gaps and three recommendations,
  and drops deltas under 0.10 in magnitude. Planning handoffs use a smaller
  variant with `type: planning`.

---

## Storage

`HybridHandoffStorage` writes both backends and reports
`{git_stored, db_stored, fully_synced}`; a failure in one does not stop the
other, and `handoff-create` logs a warning when they diverge.

**Git notes.** `GitHandoffStorage` runs `git notes --ref empirica/handoff/<session_id> add -f`
on `HEAD`, so the report is at `refs/notes/empirica/handoff/<session_id>`
(compressed JSON) and `refs/notes/empirica/handoff/<session_id>/markdown`
(readable). It attaches to whatever commit is `HEAD` when the command runs, and
creates an empty initial commit in a repository that has none. A refused
markdown note is logged, not raised; a refused JSON note raises.
`git notes --ref empirica/handoff/<id> show HEAD` reads it.

**Database.** `DatabaseHandoffStorage` upserts into a `handoff_reports` table in
the session database (WAL mode), indexed on `ai_id` and timestamp. It stores the
full expanded report including markdown; git holds only the compressed form
plus the markdown.

**Reads.** `load_handoff` prefers the database and falls back to git (or the
reverse with `prefer="git"`). `query_handoffs(ai_id, since, limit)` queries the
database and merges git-note handoffs that are not in it, so a clone that has the
notes but not the database still sees them; merged git rows are the compressed
form, with short keys. `check_sync_status` tells you which backend has a given
session.

Whether the handoff refs travel in your notes sync depends on which refs your
push or sync verb includes; this was not checked here.

---

## Using it

```bash
# write: from stdin JSON (preferred) or flags
empirica handoff-create - --output json <<'EOF'
{"task_summary": "...", "key_findings": ["..."], "next_session_context": "...",
 "remaining_unknowns": ["..."], "artifacts": ["path/to/file"]}
EOF

# read
empirica handoff-query --ai-id <ai_id> --limit 5
empirica handoff-query --session-id <id>
empirica query handoffs --ai-id <ai_id> --since 2026-01-01
```

`handoff-query` filters by session or AI only; `query handoffs` also takes
`--since`, `--scope`, `--limit`. Both go through `HybridHandoffStorage`. The MCP
tool `handoff_create` maps to `handoff-create`; there is no MCP tool for the
query side in the tool list.

Human-mode output prints a summary and then the whole report as JSON.

`session-end` was removed in favour of `handoff-create`.

---

## Present in the tree, not wired

- `empirica/core/handoff/auto_generator.py` (`auto_generate_handoff`,
  `close_session`) builds a report from cascade rows. Nothing in the repository
  calls it.
- `empirica/core/validation/handoff_validator.py` (`HandoffValidator`, checks a
  received checkpoint's claims against the git diff) is exported from
  `empirica.core.validation` and has no caller in the CLI or hooks.
- `project-bootstrap` does not read handoff reports. A next session gets the
  report only by running `handoff-query` or `query handoffs`.

---

## Source

- `empirica/core/handoff/report_generator.py`: `EpistemicHandoffReportGenerator`
  (`generate_handoff_report`, `generate_planning_handoff`)
- `empirica/core/handoff/storage.py`: `GitHandoffStorage`,
  `DatabaseHandoffStorage`, `HybridHandoffStorage`
- `empirica/cli/command_handlers/handoff_commands.py`: the two verbs
- `empirica/cli/command_handlers/query_commands.py`: `query handoffs`
