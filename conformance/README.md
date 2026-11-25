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
  refuses an anonymous caller" on the edition that has it. For a server whose edition is `local` it also POSTs an
  archive of a proposal that does not exist (it acts on nothing) to check the browser-origin rule: a text/plain POST,
  a foreign or look-alike Origin and a foreign or look-alike Host are refused, a loopback Origin is not, and a control
  request first proves the refusals mean something. That probe needs no `--token`.
- **write**: lives in the empirica-cortex repository with the identity-crossing (group B) contract. Core ships the
  read tier only (David, 2026-11-16: keep group A public, move group B).

## What the suite never does to a server

It sends no write to a server whose edition is not `cortex`, except the group-B probe, which
the contract says answers 501 and acts on nothing, and the browser-origin probe on a `local` edition (a POST that acts
on nothing). On `cortex` the read tier sends writes only without a credential,
where the answer must be 401.

## Rules it enforces that are easy to miss

- `GET /v1/health` carries `{edition, contract, capabilities}` and needs no credential.
- A route outside the server's capabilities answers 501 with `{capability}`, never 404.
- A list answer that reports `matched` must not claim more rows than it holds when `has_more` is false.
