# Conformance suite for the empirica-local contract

A server-agnostic pytest suite. It takes a URL and a credential, reads `routes.yaml` (the contract, derived from what
core's client actually calls) and checks that the server behaves as that file says. It imports nothing from cortex and
needs only `pytest`, `httpx` and `pyyaml`, so core can vendor it unchanged and run it against empirica-local.

```
pytest conformance --url https://cortex.example --token "$TOKEN"
pytest conformance --url http://127.0.0.1:8420            # a local server with auth off
```

`CONFORMANCE_URL`, `CONFORMANCE_TOKEN` and `CONFORMANCE_CONTRACT` are read when the options are absent. Without a URL
every test is skipped, so a plain `pytest` in a repo that vendors this directory stays green.

## Tiers

- **read** (this directory today): safe against any live server. Health and the capability document, auth refusal,
  every GET route core calls (required fields, with `[]` meaning "each element of this list"), the inbox and outbox
  honesty rules, and the group-B routes: `501 {capability}` on an edition that lacks the capability, "served, and
  refuses an anonymous caller" on the edition that has it.
- **write** (`--allow-writes`, opt-in): propose with an idempotency key (the same key twice is one proposal; a changed
  body under a used key is refused), complete, archive, unknown ids never reported as success, the cross-tenant 501,
  register and unregister, auto-accept set and restored. Disposable servers only: cortex runs the whole suite against a
  disposable cortex from `tests/test_conformance_write_cortex.py`.

## What the suite never does to a server

Without `--allow-writes` it sends no write to a server whose edition is not `cortex`, except the group-B probe, which
the contract says answers 501 and acts on nothing. On `cortex` the read tier sends writes only without a credential,
where the answer must be 401. With `--allow-writes` it creates and cleans up its own proposals, one project and the
auto-accept setting: never point that at a server that is not disposable.

## Rules it enforces that are easy to miss

- `GET /v1/health` carries `{edition, contract, capabilities}` and needs no credential.
- A route outside the server's capabilities answers 501 with `{capability}`, never 404.
- A list answer that reports `matched` must not claim more rows than it holds when `has_more` is false.
