# Transaction export

`empirica grounding-export --ai-id <practice> --transactions [--since <when>] [--limit <n>]` writes a practice's transaction
history as JSON with **structure only**: ids, timestamps, numbers and enum-shaped tokens. It never reads a text column, so the
output can leave the practice without a content review. Goal objectives, artifact titles, reasoning and retrospectives carry
people's and clients' names; none of them is in it.

Without `--transactions` the verb is unchanged: a single state snapshot (`self_assessed_13`, `grounded_13`, `divergence`).

Schema name: `empirica.transaction_export.v1`. Implementation: `empirica/core/transaction_export.py`.

## Flags

| Flag | Meaning |
|---|---|
| `--ai-id` | The practice, as the canonical three-form or the bare basename. Resolves only practices present on this host (`not_local` otherwise). |
| `--transactions` | Export per-transaction history. |
| `--since` | Only transactions that started at or after this: epoch seconds, `YYYY-MM-DD`, or `YYYY-MM-DDTHH:MM:SSZ` (UTC). Anything else is an error, never an empty export. |
| `--limit` | Newest N transactions. Default 50, maximum 1000. |

## Envelope

| Field | Meaning |
|---|---|
| `ok`, `schema`, `ai_id` | |
| `since`, `limit` | What was applied. |
| `returned`, `total_matching`, `truncated` | `total_matching` counts every transaction that matches `--since`, so a capped page says it is a page. |
| `skipped_without_transaction_id` | Legacy reflex rows with no transaction id are counted, not emitted. |
| `dropped_unsafe_values` | Values that failed their field's check (id shape, vocabulary, ref shape, range) and were left out. Non-zero means a field stopped being what it was. |
| `skipped_unsafe_transaction_ids` | Transactions left out because their own id is not UUID- or hex-shaped. |
| `unavailable` | Sections that could not be read because the store predates a table or column. |
| `transactions` | Newest first. |

## Transaction record

| Field | Source | Notes |
|---|---|---|
| `transaction_id`, `session_id`, `ai_id` | `reflexes.transaction_id`, `.session_id`, `sessions.ai_id` | |
| `preflight` | `reflexes` row, phase `PREFLIGHT` | `timestamp`, `vectors` (the 13), `git_commit_sha`, `git_notes_ref`. `null` if the PREFLIGHT row is missing. |
| `checks` | `reflexes` rows, phase `CHECK`, in time order | One entry per CHECK, so multiplicity is the list length. Each has `timestamp`, `vectors`, `decision` (`proceed` or `investigate`), `confidence`, `cycle`, and `auto_checkpoint: true` on the phantom rows an old auto-checkpoint wrote. |
| `postflight` | `reflexes` row, phase `POSTFLIGHT` (the last one) | `timestamp`, `vectors`, `work_type`, `internal_consistency`, `postflight_confidence`, `tool_call_count`, `auto_closed`, `git_commit_sha`, `git_notes_ref`. `null` for an open transaction. |
| `grounded` | `grounded_verifications.transaction_id` | One entry per verification (`phase` is `combined`, `noetic` or `praxic`). See below. |
| `goals` | `goals.transaction_id` | `id`, `status`, `created_timestamp`, `completed_timestamp`. |
| `artifacts` | `project_findings`, `project_unknowns`, `project_dead_ends`, `mistakes_made`, `assumptions`, `decisions`, each by `transaction_id` | `id`, `type` (`finding`, `unknown`, `dead_end`, `mistake`, `assumption`, `decision`) and `goal_id` when set. Nothing else. |

`vectors` holds only the vectors that have a value, out of `engagement, know, do, context, clarity, coherence, signal, density,
state, change, completion, impact, uncertainty`.

### `grounded[]`

`phase`, `created_at`, `grounded_coverage`, `overall_calibration_score`, `evidence_count`, `practitioner_model`,
`compliance_status`, and `vectors`, where each vector present carries `self`, `grounded`, `gap`, `confidence`,
`evidence_count` and `source` (the grounding source name, for example `git` or `artifacts`).

`gap` is `self - grounded` (positive means the practice read higher than the evidence). `uncertainty` is derived from the other
vectors' gaps and coverage, not measured. Git-sourced `do`, `state` and `change` values from before v1.13.51 may be graded over
a window far wider than the transaction; see `calibration_exclusions` in the configuration reference.

## How "structure only" is held

- No text column is selected. Reflex JSON is read through a whitelist of keys per phase.
- Every string that survives is checked against what that field can legitimately be, because a single word fits any token
  pattern (a name, a client, a lowercased slug), so a pattern alone would not be a contract:
  - ids (`transaction_id`, `session_id`, goal and artifact ids) must be UUID- or hex-shaped;
  - `decision`, `work_type`, `internal_consistency`, goal `status`, grounded `phase`, `compliance_status` and `source`
    must be a member of a closed vocabulary (`compliance_status` from the `ComplianceStatus` enum);
  - `git_notes_ref` must be exactly `empirica/session/<uuid>/<PHASE>/<n>`; `git_commit_sha` must be hex;
  - numbers must be finite and below 1e12;
  - `practitioner_model` is the one field that is only shape-checked: a known model-family prefix, lowercase, no spaces.
- Anything else is left out and counted in `dropped_unsafe_values`. A transaction whose own id is not safely shaped is left out
  and counted in `skipped_unsafe_transaction_ids`. When a vocabulary grows, the count says so instead of the new value passing.
- Reflex, grounded-verification, goal and artifact reads are for this practice (`ai_id`); one live transaction id spans two
  practices in core's store and the other practice's rows are not exported.
- The tests plant a marker in every free-text field the store has, plant names under every enum and id field, and require
  none of it in the output.

A goal's `transaction_id` is one column, so a goal that spans many transactions links to the one it was created or activated
in; tasks and findings link by their own `transaction_id`.
