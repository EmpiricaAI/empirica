# Noetic Batch — Spec / Plan

**Status:** DRAFT — awaiting David's review before implementation
**Date:** 2026-04-26
**Pairs with:** transaction-cycle skill, epistemic-transaction skill, sentinel-gate.py

---

## Problem

Investigation work generates many small tool calls — each evaluated by the Sentinel,
each shown in the TUI, each adding round-trip latency. A typical noetic phase looks like:

```
Read src/auth.py
Read src/middleware.py
Grep "decorator"
Grep "Bearer" (different scope)
Glob "src/**/*auth*"
Read tests/test_auth.py
... (15-30 calls)
```

**Costs:**
- N hook fires per Sentinel evaluation
- N decisions for the AI to plan
- N TUI lines (signal-to-noise drops)
- Sentinel false-positive risk grows linearly with N
- Iteration latency

**Insight (David, 2026-04-26):** Don't make the Sentinel smarter — make Claude make
fewer, better calls. Same architectural move as `cortex_log_artifacts`: a graph schema
replacing N individual logging calls. The Sentinel sees N=1 noetic intent instead of N=30
individual ones.

## Goal

Introduce a single batched investigation primitive that:

1. Lets Claude declare an investigation as one structured intent
2. Fulfils all sub-operations (reads/greps/globs/investigates) in one tool call
3. Returns a merged structured response
4. Is universally classified as noetic by the Sentinel (zero gating overhead)
5. Surfaces itself proactively in PREFLIGHT responses so Claude reaches for it

## Non-Goals

- Replacing individual Read/Grep/Glob — they remain valid for one-shot lookups
- Streaming results (single response, batched)
- Iterative within-batch refinement — use sequential batches if needed
- Web research (WebFetch, scrape_url) — different latency profile, keep separate

---

## Schema

### Input (JSON, required field marked ✱)

```json
{
  "intent": "✱ one-line investigation goal",
  "reads": [
    {"path": "✱ string", "lines": "optional 'N-M' or 'N-' or '-M'"}
  ],
  "greps": [
    {
      "pattern": "✱ string (regex)",
      "glob": "optional path glob (default: '**/*' relative to project root)",
      "context": "optional int (default: 0, max: 5)",
      "case_sensitive": "optional bool (default: false)",
      "max_matches": "optional int (default: 100, hard cap: 500)"
    }
  ],
  "globs": [
    "string pattern OR {pattern: '✱', root: 'optional starting dir'}"
  ],
  "investigate": [
    {
      "query": "✱ string",
      "scope": "optional 'session' | 'project' | 'global' (default: 'project')",
      "limit": "optional int (default: 5, hard cap: 20)"
    }
  ]
}
```

All four arrays optional (must have ≥1 to be meaningful). `intent` always required.

### Output (JSON)

```json
{
  "ok": true,
  "intent": "echoed",
  "reads": [
    {
      "path": "...",
      "lines": "1-150",
      "content": "...",
      "size_bytes": 4823,
      "truncated": false,
      "error": null
    }
  ],
  "greps": [
    {
      "pattern": "...",
      "glob": "...",
      "matches": [
        {"file": "src/auth.py", "line": 42, "text": "def decorator(...)", "context_before": [...], "context_after": [...]}
      ],
      "total_matches": 12,
      "truncated": false,
      "files_scanned": 87,
      "duration_ms": 34
    }
  ],
  "globs": [
    {
      "pattern": "...",
      "matches": ["src/auth.py", "src/middleware.py"],
      "total_matches": 2,
      "truncated": false
    }
  ],
  "investigate": [
    {
      "query": "...",
      "scope": "project",
      "results": [
        {"type": "finding", "summary": "...", "score": 0.78, "id": "..."}
      ],
      "truncated": false
    }
  ],
  "summary": {
    "total_files_read": 3,
    "total_grep_matches": 12,
    "total_globs_resolved": 2,
    "total_investigate_results": 5,
    "duration_ms": 187,
    "approx_tokens": 4200
  }
}
```

### Error handling

- Per-operation errors don't fail the whole batch
- Each result has its own `error` field (null on success)
- Top-level `ok: false` only if input schema invalid or fatal env error

---

## Token / Output Budgets

Critical — a poorly-bounded batch could return 100MB.

| Limit | Default | Hard cap | Override |
|---|---|---|---|
| Bytes per file read | 50KB | 1MB | `--max-file-bytes` |
| Matches per grep | 100 | 500 | per-grep `max_matches` |
| Files per glob | 200 | 1000 | `--max-glob-files` |
| Results per investigate | 5 | 20 | per-query `limit` |
| Total response bytes | 200KB | 2MB | `--max-total-bytes` |

When a limit hits, the corresponding `truncated: true` flag fires + summary notes it.
The AI sees what was truncated and can ask for narrower follow-ups.

---

## CLI: `empirica noetic-batch`

### Surface

```bash
empirica noetic-batch -                         # JSON on stdin (AI-first, primary)
empirica noetic-batch --intent "..." \          # CLI flag form (less common)
    --read src/foo.py --read src/bar.py \
    --grep "decorator:src/**/*.py:context=2" \
    --glob "src/**/*auth*"
empirica noetic-batch --schema                   # print schema for discovery
empirica noetic-batch --dry-run -                # validate schema, don't fulfil
```

### Output formats

```bash
--output json     # default (machine-readable, what the MCP wraps)
--output text     # human-readable (for terminal use)
```

### Implementation files

- `empirica/core/noetic_batch/__init__.py` — public API
- `empirica/core/noetic_batch/schema.py` — pydantic models for validation
- `empirica/core/noetic_batch/executor.py` — fulfils each operation type
- `empirica/core/noetic_batch/budgets.py` — token/byte caps
- `empirica/cli/command_handlers/noetic_batch_commands.py` — CLI handler
- `empirica/cli/parsers/<wherever>` — parser registration
- `tests/test_noetic_batch_schema.py`
- `tests/test_noetic_batch_executor.py`
- `tests/test_noetic_batch_cli.py`

### Internal fulfilment

| Operation | Backend |
|---|---|
| `reads` | `Path(p).read_text()` with line-range slicing |
| `greps` | `rg` (ripgrep) via subprocess if installed (10x faster), else Python `re` over globbed files |
| `globs` | `glob.iglob(recursive=True)` with depth cap |
| `investigate` | direct call to `handle_project_search_command` (no shell-out) |

### Performance target

- Median batch (3-5 ops): < 200ms
- Worst case (20 ops, large files): < 2s
- Streaming not supported in v1 (single response)

---

## MCP Wrapper: `mcp__empirica__noetic_batch`

### Implementation

Just another entry in `empirica-mcp/empirica_mcp/server.py` `TOOL_REGISTRY`:

```python
"noetic_batch": {
    "cli": "noetic-batch",
    "params": {},
    "required": [],  # JSON validated by handler
    "desc": "Batched investigation: reads + greps + globs + project search in one call. Returns merged structured response.",
    "stdin_json": True,
},
```

Sentinel side: lands in `EMPIRICA_MCP_PREFIX` auto-allow path → silent pass.

### CD coexistence

CD users with both Cortex MCP + Empirica MCP get `mcp__empirica__noetic_batch` (local
investigation) AND can still call `mcp__cortex__investigate` directly (cross-project).
Skill guidance distinguishes:
- noetic_batch = local-project file/code investigation
- Cortex investigate = cross-project / cross-user knowledge graph

---

## Sentinel Integration

### Classification

- Tool name `mcp__empirica__noetic_batch` → matches `EMPIRICA_MCP_PREFIX` → silent allow
- CLI `empirica noetic-batch` → matches `is_safe_empirica_command` (Tier 1 read-only)
- No special-casing needed

### PREFLIGHT response — `noetic_guidance` block

New top-level field in PREFLIGHT response, **conditional on** `work_type in (code, research, debug, audit, docs, infra)`:

```json
{
  "ok": true,
  "transaction_id": "...",
  "sentinel": "investigate",
  "noetic_guidance": {
    "tool": "mcp__empirica__noetic_batch",
    "cli": "empirica noetic-batch -",
    "schema": { ... full schema ... },
    "hint": "Use ONLY when batching ≥3 investigation operations together — the value is one merged result for your conversation, fewer round-trips. Individual Read/Grep/Glob are noetic anywhere (any phase) — use them freely. noetic-batch is NOT a Sentinel bypass; calling it once for a single read is misuse.",
    "skip_if": "Fewer than 3 investigation operations. Use Read/Grep/Glob/investigate directly — they're already noetic and don't need batching."
  },
  "patterns": { ... }
}
```

Suppressed for `work_type in (release, comms)` where investigation isn't expected.

### POSTFLIGHT retrospective (Phase 5)

Track ratio of individual noetic calls to noetic_batch calls per transaction. If
`individual_noetic > 10 AND batch_noetic == 0`, next-PREFLIGHT feedback says:

> "Last transaction: 47 individual noetic calls, 0 batches. Consider noetic_batch — would have been ~3 calls."

### Sentinel ask response (Phase 5)

When sentinel blocks a bash grep pre-CHECK, add to the deny message:
> "Or use `mcp__empirica__noetic_batch` to skip the gate entirely."

---

## Skill Updates

### `epistemic-transaction` (CC plugin)

In Step 4b (Noetic Phase — Investigate), add at top:

> **Prefer `noetic_batch` for multi-step investigation.** Open every coherent
> investigation with a batch: declare intent + reads + greps + globs + investigates
> in one JSON call. Single tool call, no Sentinel gating overhead, structured
> response. Fall back to individual Read/Grep/Glob only for one-shot lookups
> after a batch surfaces something you need to drill into.

Add quick-reference example.

### `transaction-cycle` (CD-side, in empirica-cortex extension corpus)

Same guidance, adapted for Path 1 (Cortex only — `noetic_batch` not available, use
individual cortex tools) vs Path 2 (Cortex + Empirica MCP — use `noetic_batch`).

### `empirica-constitution` (CD-side)

Section "WHAT DO I KNOW?" — add as the preferred mechanism for Path 2 users:

```
I don't know something
├── Multiple-step local investigation → mcp__empirica__noetic_batch (PREFERRED)
├── Single one-shot lookup → mcp__cortex__investigate or individual tool
├── Cross-project / org-shared → mcp__cortex__investigate or search_knowledge
...
```

### System prompt (CC-side, `~/.claude/empirica-system-prompt.md`)

Under "Artifact Discipline" or new "Investigation Discipline" section:

> **Batch noetic work.** For any investigation involving 3+ reads/greps/globs, use
> `mcp__empirica__noetic_batch` (or `empirica noetic-batch -`). One tool call,
> structured response, zero Sentinel gating. Reach for individual Read/Grep/Glob
> only for one-shot follow-ups.

---

## Migration / Rollout

- **Backward compatible.** Existing Read/Grep/Glob still work. No deprecation, no
  migration.
- New tool added to `TOOL_REGISTRY` and `__all__`. Existing callers untouched.
- Skills update is additive: existing transaction patterns still valid.
- Ship in v1.8.14 (or 1.9.0 if scope grows).

---

## Test Plan

### Schema tests (`tests/test_noetic_batch_schema.py`)
- Valid minimal batch (just intent + 1 read)
- Invalid: missing intent
- Invalid: empty arrays only (no operations)
- Invalid: unknown field
- Invalid: out-of-range limit (e.g., grep max_matches=10000)
- Optional fields default correctly

### Executor tests (`tests/test_noetic_batch_executor.py`)
- read: valid file, missing file (error per-op), line range slicing, byte cap
- grep: regex pattern, glob scope, context lines, max_matches truncation
- glob: pattern resolution, root override, max_glob_files truncation
- investigate: project_search invocation, scope passthrough, limit enforcement
- partial failures: one op fails, others succeed → batch returns ok with per-op errors
- byte budget enforcement: total response < max_total_bytes

### CLI tests (`tests/test_noetic_batch_cli.py`)
- stdin JSON → JSON output
- stdin JSON → text output
- `--schema` prints schema
- `--dry-run` validates without fulfilling
- Invalid JSON → exit code 2 with clear error

### Integration test
- End-to-end: real files, real grep, real investigate, real output validation
- Performance: 5-op batch under 500ms on this repo

### MCP wrapper test
- Mock subprocess, verify args passed correctly
- JSON round-trip preserves structure

### Sentinel test
- `mcp__empirica__noetic_batch` always silent-allowed (no CHECK required)
- `empirica noetic-batch -` always silent-allowed via Tier 1

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Claude doesn't reach for it | PREFLIGHT surfaces schema; POSTFLIGHT retrospective nudges |
| Batch returns >context budget | Token budgets + truncation flags; AI can do narrower follow-ups |
| Performance regression on small batches | Profile; if overhead > 50ms vs single call, optimize |
| Cortex investigate calls block batch latency | Run in parallel via threadpool when investigate ops > 1 |
| Schema becomes a moving target | Version field in schema; back-compat for one minor version |
| Single-quote vs double-quote regex confusion | Heredoc input via stdin avoids shell quoting entirely |

---

## Sequencing (concrete)

1. **Spec review** ← we are here
2. **Schema + executor + tests** (no CLI surface yet) — `empirica/core/noetic_batch/`
3. **CLI handler + parser registration + tests** — usable as `empirica noetic-batch -`
4. **MCP wrapper** — single TOOL_REGISTRY entry in empirica-mcp
5. **PREFLIGHT response addition** — `noetic_guidance` block in workflow_commands
6. **Skill updates** — epistemic-transaction (CC) + transaction-cycle (CD) + constitution (CD)
7. **System prompt update** — empirica-system-prompt.md
8. **Release** — v1.8.14 (or 1.9.0)
9. **Telemetry phase** — POSTFLIGHT retrospective tracks adoption (deferred 2-3 weeks)

Each step is its own transaction. Steps 2-4 can be one PR; 5-7 a separate PR (less
churn while we're confirming Claude reaches for it).

---

## Open Questions for David

1. **Schema versioning** — include a `schema_version: "1"` field upfront, or defer
   versioning until we need it?
2. **`investigate` scope** — should `noetic_batch.investigate` use `mcp__empirica__project_search`
   only, or also reach `mcp__cortex__investigate` when Cortex is available?
3. **CD-only users** — do we want a `cortex_noetic_batch` mirror in the .mcpb extension,
   so CD users on Cortex-only path get the same batch primitive (with different operation
   types)? Or punt that to a later phase?
4. **Streaming** — confident enough to defer indefinitely, or should we leave a
   `streaming: bool` field in the schema reserved for future use?
5. **`bash` operations in batch** — David said earlier "the same pattern we used for
   log artifacts applies here too". Should `noetic_batch` accept a `safe_bash` array
   for grouped read-only shell? Or keep it strictly Read/Grep/Glob/investigate?
