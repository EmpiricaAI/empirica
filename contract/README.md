# contract/

`routes.yaml` is the REST contract between empirica's client (core) and any server it talks to: hosted Cortex, and
empirica-local once it exists. Phase 0 of `docs/specs/EMPIRICA_LOCAL_AND_CORTEX_SPEC.md`; plan in
`docs/specs/EMPIRICA_LOCAL_PHASE0_PLAN.md`.

## What it is

Data, not prose: 21 group-A rows (one user, one org: a local server answers these), 11 group-B rows (identity-crossing:
a local server answers 501), the OAuth discovery document, and the capability vocabulary. Every row names the core call
sites it was read from, the request fields core sends, the response fields core **reads** (required vs optional), the
status codes core treats specially, and any `drift:` against cortex.

## Core's call sites define the contract, not cortex's responses

A field cortex returns that no core call site reads is not contract (for example `users/me` returns `is_service_account`,
`etag` and `version` ride the roster, `notification_wire_mode` and more; core reads none of them). empirica-local is
therefore not obliged to copy them. The other direction is the rule that bites: a field core reads must be there, and a
status core branches on must mean what core thinks it means.

The call sites were re-read at core `4a9379044`; the measurement they descend from is core's
`prop_5d7dy5ysebb4thvroqcjkldtiq` (at `d1e628e96`). Line numbers are that tree's and drift; the file and function are the
stable reference.

## Capabilities

A server lists the capabilities it implements in `GET /v1/health` (`{edition, contract, capabilities}`). A route whose
capability is not listed answers **501 with `{"capability": "<name>"}`, never 404**, so core can degrade per capability
with one clear line instead of a different error at each call site. `health` has no capability: it is always there.
The listener heartbeat's own `capabilities` field is the LISTENER's, an unrelated list. One group-B row
(`GET /v1/orgs/me/mesh_sharing_agreements`) answers 200 with an empty list instead of 501, because a reader runs at every
project-bootstrap.

## Changing it

`contract` is a semantic version string. A new route, a new optional field or a new capability is a **minor** bump. A
removed or renamed field, a changed status meaning or a field moving from optional to required is a **major** bump and
needs core's sign-off first. Rows are added from a core call site, never from a cortex response.

## Drift found (cortex vs what core's code expects)

All six are closed: D1 on cortex (v0.4.2+, 8ed927eb), D2-D6 in core's client (core develop, shipping with core's next release). The rows keep the original measurement as the record.

Findings, not fixes: nothing was changed to make them disappear. C = read in code, M = measured live.

| id | where | what |
|---|---|---|
| D1 | `GET /v1/health` | Fixed (8ed927eb, 2026-11-09): cortex now serves `/v1/health` and `/health` identically, with the capability document. It served only `/health` before (M). `/cortex/health` and `/` are not served; core does not rely on them. |
| D2 | `POST /v1/projects/register` | A different project holding the slug in the tenant is a 409 `slug_taken` (body carries `existing_id`, not `project_id`). Core reads every 409 as "already registered": the single path returns ok with the LOCAL id, the bulk path returns skipped with `project_id` None. A collision reads as success. (C) **Fixed in core develop (656567cfd): a 409 `slug_taken` is an `ok: false` outcome `slug_taken`, not success.** |
| D3 | `POST /v1/orchestration/{id}/archive` | A repeat archive by a recipient who already archived answers 200 `{error: already_archived, is_archived: <global flag, usually false>}`. Core's idempotency test is `is_archived is True`, so the repeat reads as "archive failed". Not exercised live. (C) **Fixed in core develop (eeba97e1b): a repeat archive answering `error: already_archived` counts as success.** |
| D4 | `POST /v1/orchestration/propose` | An identical request mid-flight answers 409 `idempotency_key_in_flight` (`replay_caught: true`). Core special-cases only `idempotency_key_conflict`, so this reads as a rejection (exit 1) though the first attempt lands. Reachable on core's own retry path. (C) **Fixed in core develop (eeba97e1b): a 409 `idempotency_key_in_flight` on the retry means wait and re-ask with the same key, then UNKNOWN, not rejected. (Cortex v0.4.2 made this reachable on first messages too: new threads now key on sender and key.)** |
| D5 | `POST /v1/system/event` | Success is 202; core's only success test is `status == 200`, so every successful emit prints "emit failed (202)". (C) **Fixed in core develop (656567cfd): any 2xx from `/v1/system/event` counts as emitted.** |
| D6 | `POST /v1/sync` | Core's postflight comment says cortex auto-creates an unknown project from `name`/`repo_url`. Cortex answers 400 "Unknown project_id" and ignores both; core swallows the exception, so an unregistered project's sync drops silently. (C) **Fixed in core develop (a696dd0f3, cf30babb8): a 400 Unknown project_id is a POSTFLIGHT warning that names `project-register`; the stale auto-create comment is removed. Cortex does not auto-create.** |

## Open items for core (decisions that are core's, recorded here so they are not lost)

- What `listener on` reads from `notification-channels` on a local server with no ntfy: core derives the orchestration-events
  topic from at least two org-prefixed sibling topics, so a local server must either return them or core must learn a
  non-ntfy path.
- OAuth: a static discovery document for a local server, or a static key and no `auth login`.
- Whether `/v1/sync` on a local server should create the project (core's comment assumes it does) or refuse as cortex does.
- Group-A pair count: core's list says 22; counting `/v1/health`, `/cortex/health` and `/` as one probe it is 21 distinct
  method+path rows here, 23 if the three probe paths are counted separately. This pass re-read the call sites core named
  and did not re-run core's 56-call-site search, so a route outside core's list would not have been found.
