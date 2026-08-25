# Cortex OAuth — Independent Per-Surface Families

How an Empirica seat authenticates to **Cortex**, the proprietary serving layer
(`getempirica.com`). Empirica core is fully functional without Cortex; this
document covers the optional OAuth path that lights up the mesh.

> **Open-core boundary.** Cortex is proprietary and account-gated. Connecting
> requires a Cortex account, which is what the `empirica auth` verbs
> authenticate. The `auth` CLI surface states this in its help and docstrings.

---

## The model: one identity, many families (Option A)

A user has **one** Cortex identity (a single `sub`). Each *surface* that talks to
Cortex — the CLI, the `empirica serve` daemon, the Chrome extension — holds its
**own independent OAuth token family** (its own DCR client + access/refresh
token pair) under that one identity.

There is **no shared token** and **no CORS-locked bridge**. Surfaces do not read
each other's tokens.

**Why independent families, not one shared token.** Cortex rotates the refresh
token on every use and its reuse-detection **revokes the whole family** if two
processes present the same rotating credential. A single shared token therefore
forces a single-refresher bottleneck and a custody deadlock. But that constraint
is a property of *sharing one family*, not a Cortex limit — Cortex already runs
many concurrent families for one user under distinct clients. Giving each surface
its own family dissolves the deadlock: every surface refreshes only what it owns,
and no surface can revoke another's family.

The design was ratified as **"Option A"** (see the decision log). The earlier
daemon-brokered / shared-token design and the CORS-lock it required were
withdrawn as moot once independent families removed the need to share.

---

## Refresh custody: `refresh_owner`

Every stored family carries a `refresh_owner` field: `cli`, `daemon`, or
`extension`. **Exactly one process refreshes a given family** — the owner. All
other readers of that family are read-only.

`empirica auth login` assigns the owner by probing whether a serve daemon is
running:

```python
owner = "daemon" if _serve_daemon_running() else "cli"
```

- **`_serve_daemon_running()`** — best-effort GET to
  `http://127.0.0.1:{EMPIRICA_SERVE_PORT|8000}/api/v1/health`; `False` on any
  failure.
- **Daemon present** → `daemon`. The always-on `empirica serve` process is the
  sole refresher; the CLI and any other reader consume the daemon-kept token
  read-only. This is what lets the token stay warm **without a browser** (the
  api_key-retirement goal): the daemon carries renewal even when no interactive
  shell is open.
- **Headless box** → `cli`. No competing refresher, so the CLI's own shell
  process refreshes its family on demand.

`cortex_bearer()` (the token accessor) refreshes **only when the caller owns the
family** (`owner == "cli"` on the CLI path); otherwise it reads the current token
without refreshing, so a non-owner can never trigger a rotation that
reuse-detection would punish.

### Dead credentials are suppressed, and the result says so

The api_key fallback does **not** fire for a key already known to be dead. Cortex's
401 bodies carry `credential_status` and `retry`; a terminal status
(`invalid_key` / `invalid_token` / `missing_credential`) is recorded by
`empirica/core/auth/credential_health.py`, and `cortex_bearer()` then skips that
key rather than presenting it again.

This exists because the fallback used to fire unconditionally: `cortex_access_token`
correctly returns `None` on expiry-without-refresh, and the fallback handed back an
api_key that might itself be revoked — forever, with zero `/v1/oauth/token`
attempts. Measured elsewhere at 25h and ~10k requests.

Two things a reader debugging an unauthenticated seat needs:

- **A suppressed credential is not an absent one.** The result carries `reason`
  (`api_key unusable (invalid_key) — re-authenticate with 'empirica auth login'`),
  distinct from `no cortex credential configured`. If you see the first, the seat
  HAS a key and it is dead; provisioning another will not help.
- **`auth login` is the escape path and needs no reset step.** Marks clear the
  moment `credentials.yaml` changes, so re-authenticating un-brickes the seat by
  writing the file.

Absent `credential_status` fields fail **open** — an older cortex, or a proxy that
ate the body, produces no verdict and nothing is marked. `expired_token` is never
marked; that path refreshes.

> The recording side is shipped and **not yet wired to a caller** — no request path
> currently reports its 401s into it, so no credential is marked in practice. It
> fails open, so behaviour is unchanged until a shared cortex request helper exists
> to route 401s through it.

---

## `auth login` flow

`empirica auth login` runs an OAuth **authorization_code + PKCE** flow:

1. **DCR** — register (or reuse) a public client. The `redirect_uris` are a
   fixed loopback **port block** `127.0.0.1:43217–43224` (`LOOPBACK_PORTS`), not
   an ephemeral port, because Cortex matches `redirect_uri` by **exact string** —
   an ephemeral port would be rejected at authorize time. A free port in the
   block is bound for the callback listener.
2. **Client reuse guard (the client_id-reuse trap fix).** Login reuses the
   stored `client_id` **only when the stored family is the CLI's own**
   (`refresh_owner` in `cli`/`daemon`). Over a *foreign* or unknown-owner block
   (e.g. the extension's), it registers its **own** client instead — otherwise
   the incoming id would always equal the stored one and the one-family
   replace-guard could never fire, minting the CLI's tokens under another party's
   client (two clients presenting one rotating family → Cortex revokes it).
3. **Authorize** — the browser is opened to the authorize URL. **The URL is
   always printed to stderr first** (see *Browserless boxes* below).
4. **Callback** — the loopback listener receives the code (validating `state`);
   the code is exchanged for the token set at the token endpoint.
5. **Persist** — `save_cortex_oauth(...)` writes the family (access + refresh
   token, `expires_at`, `token_endpoint`, `client_id`, `refresh_owner`).
6. **Verify at the consumer surface** — a real `/v1/users/me` call is made with
   the new token only (no api_key fallback). "Stored" is not "works"; a token
   that persists but fails an authenticated call must not be trusted for
   api_key retirement.

### Browserless boxes (WSL2, headless, containers)

`login()` prints the authorize URL to **stderr** *before* attempting to open a
browser, then fires the opener best-effort in a daemon thread. This is
load-bearing:

- A box that cannot open a browser (WSL2 with Windows-interop off, a headless
  server, a container) would otherwise **hang silently** until timeout with
  nothing on screen — the URL was previously only in the return value, printed
  after the callback.
- **Never gate on the opener's result:** `webbrowser.open` returns `True` for a
  `BROWSER='echo %s'` shim, and `webbrowser.get()` *raises* on a genuinely
  browserless box. Printing unconditionally sidesteps both.
- stderr (not stdout) keeps `auth login --output json` parseable.

The user pastes the printed URL into any reachable browser; on WSL2 the loopback
callback returns via `localhostForwarding`. (That forwarding hop is
environment-dependent and must be verified per box, not assumed.)

---

## The daemon refresh loop

`empirica serve` runs `_oauth_refresh_loop` as a background thread (started in the
ASGI lifespan; interval from `EMPIRICA_SERVE_OAUTH_REFRESH_SEC`, default **300s**;
`<=0` disables it). Each tick:

- Skips unless the stored family's `refresh_owner == "daemon"` (a `cli`/absent
  family is the headless-fallback case its own shell refreshes).
- Skips if there is no refresh token.
- Calls `cortex_access_token(refresh=..., leeway_s=300.0)` — refreshes only when
  the token is within 5 minutes of expiry, and persists the rotated set.
- Fail-open: a refresh error logs and retries next tick; it never crashes the
  daemon.

**Effect:** the short-lived access token (≈24 h) is silently rotated ~5 minutes
before each expiry, indefinitely, with no browser — as long as the daemon stays
alive. The 24 h `expires_at` is the *access* token's life by design, not a
re-login cadence; the long-lived refresh token is what renews it.

---

## Storage & normalization

Families live in `~/.empirica/credentials.yaml` under `cortex.oauth`. The
`credentials_loader`:

- **One-family merge guard:** `save_cortex_oauth` **replaces** the oauth block
  when the incoming `client_id` differs from the stored one — the file holds one
  family (one dict), so a different client is a different family, not a merge.
- **Epoch normalization (the ms/s trap):** `expires_at` is normalized to
  **seconds** at write, at read, and in `_expires_at` (`v / 1000.0 if v > 1e11
  else v`). A value bridged in JavaScript milliseconds, read as seconds, becomes
  a year ~58,600 — permanently "valid", never refreshed, silently dead at the
  24 h mark. Defense-in-depth at every layer.
- **Concurrent-write reload:** the loader tracks the credentials file mtime and
  reloads on change, so the always-on daemon sees a concurrent `auth login`
  write without a restart.

---

## `auth status` and retirement

`auth status` reports the seat's credential state: oauth token validity, refresh
custody, api_key presence, and a **retirement-ready** verdict
(`valid token && has refresh_token`). Retiring the `api_key` is a separate,
per-seat act gated on a per-surface survival check — `logout` revokes the refresh
token and drops the oauth block but **never touches the api_key**; logout is
never a lockout.

---

## Failure modes worth knowing

| Symptom | Cause |
|---|---|
| Token "valid" but never refreshes, dies at 24 h | ms-vs-seconds `expires_at` (fixed by normalization) |
| Whole family suddenly revoked | two processes refreshed one shared family (Cortex reuse-detection) — the reason for independent families |
| `auth login` hangs with nothing on screen | browserless box, URL not surfaced (fixed by the stderr print) |
| CLI mints tokens under the extension's client | client_id-reuse trap (fixed by the owner-scoped reuse guard) |
| Daemon serves a stale token after a concurrent login | loader not reloading on mtime change (fixed by the mtime reload) |

---

## Code map

- `empirica/core/auth/cortex_oauth.py` — `login()`, DCR, PKCE, loopback,
  `cortex_bearer()`, `_serve_daemon_running()`, `_expires_at()`.
- `empirica/config/credentials_loader.py` — `save_cortex_oauth`, the one-family
  merge guard, `_as_epoch_seconds`, mtime reload.
- `empirica/api/serve_app.py` — `_oauth_refresh_loop`, the daemon refresh tick.
- `empirica/cli/command_handlers/auth_commands.py` — `login`/`status`/`logout`
  handlers and the rung-4 `/v1/users/me` verification.
