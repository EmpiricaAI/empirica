# Noetic Batch

`empirica noetic-batch` runs several read-only investigation operations (file
reads, greps, globs, semantic searches) in one call and returns one merged
structured result. It shipped as a primitive in v1.8.14; this page describes what
the code does now. Earlier revisions were the pre-implementation spec and carried
a draft status, open questions and a phased plan; those are gone because the work
is built (and in places differs from the plan, noted below).

---

## When to use it, and when not

Individual `Read`, `Grep`, `Glob` and `project-search` are already noetic in any
phase. The Sentinel does not gate them, so batching buys no gating relief. The
value is operational: fewer round-trips, and one merged result in the conversation
for an investigation that has a single intent.

- Use it when you have **three or more** investigation operations that belong to
  one intent.
- Do not use it for a single read. It is not a Sentinel bypass, and the executor
  attaches a `warning` to the result when a batch has fewer than two operations.
- It does not cover web research (`WebFetch`, `scrape_url`) or shell commands.
  There is no `bash` operation in the schema.

It is the same move as `log-artifacts`: one structured payload in place of N
calls.

---

## Input

JSON on stdin. Models are in `empirica/core/noetic_batch/schema.py`
(`SCHEMA_VERSION = "1"`). `empirica noetic-batch --schema` prints the live JSON
schema; trust it over this page.

```json
{
  "schema_version": "1",
  "intent": "one-line goal, 1 to 500 chars (required)",
  "reads":       [{"path": "src/auth.py", "lines": "10-80"}],
  "greps":       [{"pattern": "decorator", "glob": "src/**/*.py", "root": null,
                   "context": 2, "case_sensitive": false, "max_matches": 100}],
  "globs":       ["src/**/*auth*", {"pattern": "tests/**/*.py", "root": null}],
  "investigate": [{"query": "auth flow", "scope": "project", "limit": 5}]
}
```

| Field | Notes |
|---|---|
| `reads[].lines` | `N`, `N-M`, `N-` or `-M`; 1-indexed, inclusive; validated |
| `greps[].glob` | default `**/*`; `root` overrides the project root (for a cross-project grep) |
| `greps[].context` | 0 to 5 lines |
| `greps[].max_matches` | 1 to 500 at the schema |
| `globs[]` | a bare string is shorthand for `{pattern}` |
| `investigate[].scope` | `session`, `project` (default) or `global` |
| `investigate[].limit` | 1 to 20 at the schema |

All four operation arrays default to empty. Unknown fields are not rejected by
the models as written (no `extra="forbid"`); an earlier test plan assumed they
were.

## Output

`NoeticBatchResult`: `ok`, `schema_version`, `intent`, `reads`, `greps`, `globs`,
`investigate`, `summary`, `error` (only on a top-level failure), `warning`.

- Every operation result has its own `error` (null on success). One failing
  operation does not fail the batch.
- Reads return `content`, `size_bytes`, `truncated`. Non-UTF-8 files return an
  error rather than content.
- Greps return `matches` (`file`, `line`, `text`, `context_before`,
  `context_after`), `total_matches`, `truncated`, `files_scanned`, `duration_ms`.
- Globs return relative paths, `total_matches`, `truncated`.
- Investigate returns `results` (whatever shape `project-search --output json`
  gave, normalised to a list of dicts) and `truncated`.
- `summary` totals the above plus `approx_tokens` (bytes divided by 4, a rough
  estimate over reads, grep text and glob paths).
- Each run writes one summary line to **stderr**, so JSON on stdout stays clean.

## Budgets

`empirica/core/noetic_batch/budgets.py`. `BatchBudgets` is a dataclass clamped to
hard caps, but the CLI exposes no flag to change it, so production runs use the
defaults:

| Limit | Default (in effect) | Schema or hard cap |
|---|---|---|
| Bytes per file read | 50 KB | 1 MB |
| Matches per grep | 100 | 500 |
| Files per glob | 200 | 1000 |
| Results per investigate | 5 | 20 |
| Total response bytes | 200 KB | 2 MB |

Two consequences the earlier spec did not state:

- The effective per-grep and per-investigate caps are `min(per-op value, default)`,
  so a `max_matches` of 500 or a `limit` of 20 is accepted by the schema and still
  returns at most 100 or 5. Only `--max-*` overrides would change that, and none
  exist.
- `max_total_bytes` is defined but **not enforced**: nothing in the executor reads
  it, so only the per-operation caps bound the response. When a per-operation cap
  hits, that result's `truncated` is true.

## Fulfilment

Operations run sequentially, reads then greps then globs then investigates (not
in parallel).

| Operation | Backend |
|---|---|
| `reads` | read bytes, decode UTF-8, slice lines, cut at the byte cap |
| `greps` | `rg --json` when `rg` is on PATH (run with the root as cwd so a root-relative `--glob` matches); otherwise a Python `re` fallback over `glob.iglob`, up to 10000 files. 30 second timeout on the `rg` call |
| `globs` | `glob.iglob(recursive=True)`, files only, under the root |
| `investigate` | subprocess `empirica project-search --task Q --limit N --output json`, plus `--global` when scope is `global`; 30 second timeout. Scope `session` adds no flag, so it behaves as `project` |

Known limitation in the `rg` path: `context_after` is never populated. After-context
lines from ripgrep are collected into the next match's `context_before`. The
Python fallback fills both sides.

Project root: the `--project-root` flag, else `InstanceResolver.project_path()`,
else cwd. A resolver failure falls back to cwd silently.

---

## CLI

```bash
empirica noetic-batch - <<'EOF'
{"intent": "understand auth surface",
 "reads": [{"path": "src/auth.py"}],
 "greps": [{"pattern": "decorator", "glob": "src/**/*.py", "context": 2}],
 "globs": ["src/**/*auth*"]}
EOF

empirica noetic-batch --intent "..." --read a.py --read b.py \
    --grep "pattern:glob:context=2" --glob "src/**/*auth*" --investigate "query"
empirica noetic-batch --schema          # print the input JSON schema
empirica noetic-batch --dry-run -       # validate, report operation_count, execute nothing
```

Flags (from `--help`): `config` (path, or `-` for stdin), `--intent`, `--read`,
`--grep`, `--glob`, `--investigate` (all repeatable), `--project-root`, `--schema`,
`--dry-run`, `--output {json,text}`. The flag form of `--grep` takes
`pattern[:glob[:context=N|case_sensitive=B|max_matches=N]]`; the flag form of
`--investigate` takes only the query. Exit codes: 0 all operations succeeded, 1 the
batch ran but at least one operation reported an error, 2 invalid input.

## MCP

`noetic_batch` in `empirica-mcp/empirica_mcp/server.py` wraps the same CLI, takes
the payload as stdin JSON, and lists `intent` as required. It is classified under
the `noetic` category in `empirica mcp-list-tools`. Its name carries the
`mcp__empirica__` prefix, which `sentinel-gate.py` treats as epistemic workflow
and always allows.

---

## Sentinel and PREFLIGHT

- The Sentinel needs no special case. `empirica noetic-batch` is in the gate's
  read-only prefix list and in its always-open exemption set, with a comment that
  investigation is the remedy every gate prescribes, so the tool that performs it
  must stay open.
- PREFLIGHT adds a `noetic_guidance` block (tool name, CLI form, a schema sketch,
  a hint and a `skip_if`) when `work_type` is one of `code`, `research`, `debug`,
  `audit`, `docs`, `infra`, `config`, `design` (`_NOETIC_BATCH_WORK_TYPES` in
  `_workflow_shared.py`). Other work types, including `release` and `comms`, get no
  block. The block's schema sketch lists `max_matches` as at most 500 and `limit` as
  at most 20, the schema-level caps rather than the effective ones above.

## Planned, not built

The earlier plan had a Phase 5 that never landed: no POSTFLIGHT retrospective
comparing individual noetic calls to batch calls, and no extra deny-message hint
pointing a blocked grep at `noetic_batch`. A search of `empirica/` for either finds
nothing. `schema_version` exists, but there is no back-compat handling for
more than one version yet.

## Files

| File | Role |
|---|---|
| `empirica/core/noetic_batch/schema.py` | Input and output models |
| `empirica/core/noetic_batch/executor.py` | `run_batch` and the per-operation executors |
| `empirica/core/noetic_batch/budgets.py` | Defaults, hard caps, `BatchBudgets` |
| `empirica/cli/command_handlers/noetic_batch_commands.py` | CLI handler, flag parsing, exit codes |
| `empirica/cli/parsers/monitor_parsers.py` | Parser registration |
| `empirica/cli/command_handlers/_workflow_shared.py` | `_build_noetic_guidance` |
| `tests/test_noetic_batch_schema.py`, `test_noetic_batch_executor.py`, `test_noetic_batch_cli.py` | Tests (not run for this review) |
