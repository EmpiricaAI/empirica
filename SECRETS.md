# Secrets

How credentials are handled in this repository and by the Empirica tools. Checked against the source at 1.14.11 (2026-10-17).
An earlier version of this file said the project's own secrets live in a Doppler project named `test-doppler-project`; nothing in
the code supports that, so it was removed.

## The rule

**Never commit a secret, never paste one into an artifact, never put one on a command line.** Each part of that rule has a
mechanism behind it below, and each mechanism exists because the rule was broken once.

## Where Empirica's own credentials live

* `~/.empirica/credentials.yaml` holds the Cortex and ntfy credentials. Keep it owner-only (`chmod 600`); on this box it is. `empirica setup-claude-code` prints which of them are configured and never prints a value.
* Cortex access prefers OAuth over a static key. `empirica auth login` runs the browser flow and stores the token set;
  `empirica auth token` prints the current access token, and `empirica auth status` says whether the token is valid and whether a
  static key is still present. An MCP client can fetch its header at connect time with a `headersHelper` that calls
  `empirica auth token`, so no literal `Authorization` header is stored in a client config.
* In builds after 1.14.11 the notification listener passes its authorization header to `curl` on standard input, not as an argument, so it no longer
  appears in the process list; before that, listeners carried a bearer token on their command line.

## Keeping credentials out of stored artifacts

Artifact text is retrieved into later sessions and embedded in Qdrant, so a credential in it is re-served on every relevant prompt.

* **At write time.** The artifact verbs (`finding-log`, `unknown-log`, `deadend-log`, `mistake-log`, `assumption-log`,
  `decision-log`), the `log-artifacts` batch and the repository writers scrub credential-shaped text before it is stored, noted
  or embedded (`empirica/core/redaction.py`, `redact_for_storage`). It keeps ordinary prose: a value only counts as a credential if
  it looks like one.
* **Already stored.** `empirica doctor --scrub-secrets` reports credential-shaped text in this project's SQLite rows, git notes and
  Qdrant payloads and does nothing else; add `--apply` to remove it. The shared Qdrant collections (`global_learnings`,
  `epistemic_events`) are scrubbed only with `--include-global`, and another practice's collections are never touched.
* **Scan snapshots.** `empirica scan --save` redacts credentials in process command lines and writes its files owner-only.

## Secret references in modules

A module manifest names a secret by reference (`secrets_ref: doppler://...`, also `op` and `vault`), never by value. At run time
`empirica/core/modules/executors.py` resolves the reference through the manager's own CLI; if that CLI is absent it reports
`manager_cli_absent` and does not guess. This is the only place Doppler appears in the code.

## If a credential leaks

1. Rotate it first. Redacting stored text does not make a leaked key safe.
2. Run `empirica doctor --scrub-secrets` in each affected project, then with `--apply`.
3. Tell the owner of the system the credential belongs to; do not test whether it still works.
