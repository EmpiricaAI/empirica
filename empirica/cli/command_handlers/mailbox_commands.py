"""CLI handlers for `empirica mailbox` — Cortex AI-mesh interaction.

NEW namespace, distinct from:
  - `empirica message-*` (git-notes-based local agent messaging — different concern)
  - `empirica notify *` (multi-backend event dispatch — different concern)

Verbs:
  reply   Atomic propose + complete in one call. Collapses the AI ack-discipline
          gap surfaced by prop_rau4ymp62fhenavyolejadahtq: today a reply via
          `cortex_propose --parent-id X` requires a SECOND `cortex_complete_proposal`
          call to close the parent — and that second call is the most-skipped
          step per the cortex-mailbox-send skill's own anti-patterns list.

Implements prop_rau4ymp62fhenavyolejadahtq.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path


def _default_resolve_cortex_creds() -> tuple[str | None, str | None]:
    """Resolve Cortex URL + api_key from credentials_loader.

    `(None, None)` means NOT CONFIGURED. A resolution that FAILED — expired
    refresh token, unreadable credentials file, network error reaching the token
    endpoint — now says so on stderr before returning the same tuple.

    Why it matters that these were identical: every caller answers `(None, None)`
    with *"Cortex creds missing — configure cortex.url + cortex.api_key…"*. So a
    transient OAuth failure told the user to configure something already
    configured, and the advice was unfollowable because nothing was missing. Same
    shape as the timeout reported as "not found" that this file's
    `_default_fetch_parent` carried until today, one function below.

    The tuple is unchanged deliberately: `_resolve_cortex_creds` is injected into
    every handler and every test double returns a 2-tuple. The cause reaches the
    user beside the caller's own message rather than through a new return type.
    """
    try:
        # OAuth-first with api_key fallback (empirica auth login) — the
        # resolver never returns a stale token and never raises past here.
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
        if not creds.get("bearer") and creds.get("reason"):
            # The resolver answered without raising and said WHY there is no usable bearer (a revoked key, no credential). Dropping
            # that left every verb telling the user to configure what was configured. It reaches the user beside the caller's message.
            sys.stderr.write(f"  note: {creds['reason']}\n")
        return creds.get("url"), creds.get("bearer")
    except Exception as e:
        sys.stderr.write(
            f"  note: credential resolution FAILED ({type(e).__name__}: {e}) — "
            "this is not the same as 'not configured'. Try `empirica auth login`.\n"
        )
        return None, None


def _default_resolve_ai_id() -> str | None:
    """Read ai_id from .empirica/project.yaml in current project root.

    `None` means no project.yaml was found, or it carries no `ai_id`. A file that
    exists and cannot be PARSED is a different thing and now announces itself:
    callers answer `None` with *"set --ai-id or add ai_id to
    .empirica/project.yaml"*, which on malformed YAML points the reader at a key
    that is already there.
    """
    try:
        import yaml

        # Walk up from cwd looking for .empirica/project.yaml
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            proj_yaml = parent / ".empirica" / "project.yaml"
            if proj_yaml.exists():
                cfg = yaml.safe_load(proj_yaml.read_text()) or {}
                return cfg.get("ai_id")
        return None
    except Exception as e:
        sys.stderr.write(
            f"  note: found .empirica/project.yaml but could not read ai_id from it "
            f"({type(e).__name__}: {e}) — the key may be present but the file unparseable.\n"
        )
        return None


def _default_http_post(url: str, body: dict, api_key: str, timeout: float = 10.0) -> tuple[int, dict]:
    """POST to cortex with Bearer auth. Returns (status, parsed_body)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}


class ParentFetchError(Exception):
    """The fetch did not complete — as distinct from completing and finding nothing.

    Raised for every outcome that is NOT "the server looked and there is no such
    proposal": timeouts, DNS/TLS failures, 401/403, 5xx, malformed bodies. `None`
    is reserved for a real 404.

    Why an exception rather than a richer return type: `_fetch_parent` is injected
    (lines ~289, ~835) and every test double is a lambda returning a dict or None.
    A tuple return would break all of them at once and pressure the next author
    into `[0]`-indexing at the call site, which is how the distinction gets lost
    again. Raising leaves the happy path's type alone.
    """

    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def _is_multi_target(parent: dict) -> bool:
    """Whether the parent is addressed to more than one distinct seat.

    Cortex completes such a proposal per target (97820077, 2026-10-24): `/complete` acknowledges ONE target and the row stays
    accepted until every target has acked, so a seat that did the work acks it instead of leaving the source's outbox stalled.
    Distinct targets are counted, so the same seat listed twice is one; a parent without a target list (an older shape) is not
    multi-target. Cortex needs to be told WHICH target is acking only when the caller owns more than one, and only this count
    decides whether we send `ai_id`.
    """
    targets = parent.get("target_claudes")
    if not isinstance(targets, list):
        return False
    return len({str(t).strip().lower() for t in targets if str(t).strip()}) > 1


def _default_fetch_parent(cortex_url: str, api_key: str, parent_id: str, timeout: float = 5.0) -> dict | None:
    """GET /v1/orchestration/<id> for parent body. Response is the proposal object directly.

    Returns the proposal, or None when cortex answers 404. Raises `ParentFetchError`
    for anything else.

    This used to `except Exception: return None`, so a 5-second timeout reached the
    user as "not found or inaccessible. Check the id and your Cortex tenant scope."
    — a sentence that is wrong twice over: it asserts an outcome the code never
    observed, and it names two innocent suspects. Cortex's nginx logs show the real
    shape: `499` (client closed the connection) at 21:14:43 under a 3-5x request
    spike, then `200` for the same id 25 seconds later. Two peer practices each
    burned an investigation hunting a lookup/visibility defect that did not exist,
    because the message named one.
    """
    url = f"{cortex_url.rstrip('/')}/v1/orchestration/{parent_id}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # the one case that genuinely means "no such proposal"
        if e.code in (401, 403):
            raise ParentFetchError(f"cortex refused the request (HTTP {e.code}) — credentials or tenant scope") from e
        raise ParentFetchError(f"cortex returned HTTP {e.code}", retryable=e.code >= 500) from e
    except TimeoutError as e:
        # socket.timeout is an alias of TimeoutError on py3.10+; urlopen raises it
        # directly on read timeout and wraps it in URLError on connect timeout.
        raise ParentFetchError(f"request timed out after {timeout}s", retryable=True) from e
    except urllib.error.URLError as e:
        inner = e.reason
        if isinstance(inner, TimeoutError):
            raise ParentFetchError(f"request timed out after {timeout}s", retryable=True) from e
        raise ParentFetchError(f"could not reach cortex: {inner}", retryable=True) from e

    try:
        body = json.loads(raw)
    except ValueError as e:
        raise ParentFetchError("cortex returned a body that is not JSON") from e

    if isinstance(body, dict) and (body.get("id") or body.get("title")):
        return body
    # Fallback for wrapped response shape (future-compat)
    if isinstance(body, dict) and body.get("proposal"):
        return body["proposal"]
    raise ParentFetchError("cortex returned 200 with no proposal in the body")


def _default_http_get(url: str, api_key: str, timeout: float = 10.0) -> tuple[int, object]:
    """GET from cortex with Bearer auth. Returns (status, parsed_body).

    Deliberately NOT modelled on `_default_fetch_parent`, which swallows every
    exception into a bare `None`. That collapses "not found", "bad credentials" and
    "network unreachable" into one indistinguishable outcome — the shape this whole
    verb exists to stop. Returning the status lets the caller say which happened.
    """
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def _resolve_canonical_ai_id(cortex_url: str, api_key: str, local_ai_id: str | None, _http_get: Callable) -> str | None:
    """Resolve the canonical 3-form `org.tenant.project` from cortex's roster.

    Cortex matches participants on the canonical id. `.empirica/project.yaml` carries only the bare
    project slug (`empirica`), and a bare slug sent where the canonical form is required is a guaranteed
    participant mismatch: a read comes back empty (indistinguishable from "nothing there") and a write is
    refused. So the id is READ from the authority rather than assembled from local slugs (project.yaml has
    no org slug, and guessing one would reintroduce the same class).
    """
    if not local_ai_id:
        return None
    status, body = _http_get(f"{cortex_url.rstrip('/')}/v1/users/me/roster", api_key, 10.0)
    if not (200 <= status < 300) or not isinstance(body, dict):
        return None

    # Scope to the CALLER's tenant. Two tenants legitimately hold the same practice
    # slug — David and Philipp both have an `empirica-autonomy` — so an unscoped walk
    # could return a canonical id belonging to someone else's seat.
    own_tenant = ((body.get("self") or {}).get("tenant_slug")) or ""
    for tenant in (body.get("org") or {}).get("tenants") or []:
        if not isinstance(tenant, dict) or (own_tenant and tenant.get("tenant_slug") != own_tenant):
            continue
        for proj in tenant.get("projects") or []:
            # Project rows key the practice as `slug` / `ai_id_short`; there is no
            # `ai_id` field. Matching the wrong key is how the first version of this
            # resolver silently returned None for a practice that was right there.
            if not isinstance(proj, dict):
                continue
            if local_ai_id in (proj.get("slug"), proj.get("ai_id_short")):
                mesh = proj.get("ai_id_mesh")
                if isinstance(mesh, str) and mesh:
                    return mesh
    return None


def handle_mailbox_reply_command(  # noqa: C901 — CLI handler with 7 validation gates + 2 HTTP calls; linear flow is clearer than extracting helpers
    args,
    *,
    _resolve_cortex_creds: Callable[[], tuple] = _default_resolve_cortex_creds,
    _resolve_ai_id: Callable[[], str | None] = _default_resolve_ai_id,
    _http_post: Callable[[str, dict, str, float], tuple] = _default_http_post,
    _http_get: Callable[[str, str, float], tuple] = _default_http_get,
    _fetch_parent: Callable[[str, str, str], dict | None] = _default_fetch_parent,
) -> int:
    """`empirica mailbox reply` — atomic propose + complete.

    Closes the parent automatically unless `--no-close` is set (follow-up
    question case). Smart defaults: title="Re: <parent.title>",
    target_claudes=[parent.source_claude], source_claude from project.yaml.
    """
    parent_id = getattr(args, "parent_id", None)
    summary = getattr(args, "summary", None)
    if not parent_id:
        sys.stderr.write("mailbox reply: --parent-id is required\n")
        return 1
    if not summary:
        sys.stderr.write("mailbox reply: --summary is required\n")
        return 1

    cortex_url, api_key = _resolve_cortex_creds()
    if not cortex_url or not api_key:
        sys.stderr.write(
            "mailbox reply: Cortex creds missing — configure cortex.url + "
            "cortex.api_key in ~/.empirica/credentials.yaml or set "
            "CORTEX_REMOTE_URL + CORTEX_API_KEY env vars.\n"
        )
        return 1

    # Canonicalize like `poll` (which carries the comment "a basename query lies
    # rather than fails") and `archive` do. This verb was the only one of the three
    # taking the raw `.empirica/project.yaml` slug, and the justification for not
    # doing so was already in this file, attached to a different verb.
    #
    # Measured: today a bare-but-RESOLVABLE slug is canonicalized server-side on
    # receipt, so this was not emitting bouncing addresses — a causal chain I built
    # on it and had to withdraw. The reason to fix it anyway is that the safety is
    # a server-side behaviour no comment here records, so it is invisible to the
    # next reader; and a slug that is NOT resolvable would bounce with no local
    # signal at all. An explicit local `--source-claude` is honoured as given.
    source_claude = getattr(args, "source_claude", None)
    canonical_unresolved = False
    if not source_claude:
        source_claude = _resolve_canonical_ai_id(cortex_url, api_key, _resolve_ai_id(), _http_get)
        if not source_claude:
            # Falling back to the bare slug is not a silent equivalent: the own-proposal guard below compares whole ids and cannot
            # match a bare slug against a canonical one. The fallback is remembered so the guard can refuse the ambiguous case.
            canonical_unresolved = True
            source_claude = _resolve_ai_id()
    if not source_claude:
        sys.stderr.write(
            "mailbox reply: source_claude unresolved — set --source-claude or add ai_id to .empirica/project.yaml.\n"
        )
        return 1

    # Fetch parent for smart defaults (title prefix, target_claudes)
    try:
        parent = _fetch_parent(cortex_url, api_key, parent_id)
    except ParentFetchError as e:
        # NOT "not found" — the lookup never completed, so the id and the tenant
        # scope are not suspects and must not be named as if they were.
        retry = " Re-run once cortex is reachable." if e.retryable else ""
        sys.stderr.write(f"mailbox reply: could not fetch parent {parent_id} — {e.reason}.{retry}\n")
        return 1
    if parent is None:
        sys.stderr.write(
            f"mailbox reply: parent {parent_id} not found (cortex answered 404). "
            "Check the id and your Cortex tenant scope.\n"
        )
        return 1

    # Derive title (max 200 chars) and target_claudes
    parent_title = parent.get("title", "")
    raw_title = getattr(args, "title", None) or f"Re: {parent_title}"
    title = raw_title[:197] + "..." if len(raw_title) > 200 else raw_title

    target_claudes_arg = getattr(args, "target_claudes", None)
    if target_claudes_arg:
        target_claudes = [t.strip() for t in target_claudes_arg.split(",") if t.strip()]
    else:
        parent_source = parent.get("source_claude")
        # A reply answers the parent's author. When that author is the caller,
        # the default addresses the caller: the "reply" lands in its own inbox,
        # reaches no peer, and then CLOSES the caller's own open ask. Measured
        # 2026-09-23: two follow-ups to cortex, sent this way, never left the
        # sender, and the response said ok. Refused only for the derived default;
        # an explicit --target-claudes is a deliberate choice and is honoured.
        own_slug = str(source_claude).strip().lower()
        if (
            canonical_unresolved
            and parent_source
            and parent_source.strip().lower() != own_slug
            and parent_source.rsplit(".", 1)[-1].strip().lower() == own_slug
        ):
            # Same slug, different (or unknowable) tenant: with no canonical id the guard cannot tell your proposal from another
            # tenant's practice of the same name. Refuse that one case instead of posting a reply that may land in your own inbox.
            sys.stderr.write(
                f"mailbox reply: your canonical id could not be resolved (the roster lookup failed) and parent {parent_id}'s "
                f"source_claude {parent_source} shares your slug, so this may be your own proposal and the default target could be you. "
                "Re-run when cortex is reachable, or pass --source-claude <your canonical id>.\n"
            )
            return 1
        if parent_source and parent_source.strip().lower() == own_slug:
            sys.stderr.write(
                f"mailbox reply: parent {parent_id} is your own proposal (source_claude {parent_source}), "
                "so the default target would be you: the reply would reach no peer and would close your "
                "own open ask. To chase your own ask, name the peer and keep it open: "
                f"--target-claudes <peer> --no-close (with --parent-id {parent_id} for threading).\n"
            )
            return 1
        target_claudes = [parent_source] if parent_source else []
    if not target_claudes:
        sys.stderr.write(
            "mailbox reply: target_claudes empty — parent has no source_claude and --target-claudes not set.\n"
        )
        return 1

    proposal_type = getattr(args, "type", None) or "collab_brief"
    payload_arg = getattr(args, "payload", None)
    try:
        payload = json.loads(payload_arg) if payload_arg else {}
    except json.JSONDecodeError as e:
        sys.stderr.write(f"mailbox reply: --payload is not valid JSON: {e}\n")
        return 1

    # Step 1: cortex_propose
    propose_url = f"{cortex_url.rstrip('/')}/v1/orchestration/propose"
    propose_body = {
        "api_key": api_key,
        "type": proposal_type,
        "title": title,
        "summary": summary,
        "target_claudes": target_claudes,
        "source_claude": source_claude,
        "parent_id": parent_id,
        "payload": payload,
    }
    # An idempotency key makes the retry below SAFE. Cortex's applied-keys ledger
    # no-ops on a key it has seen and returns the original receipt, so re-sending
    # after "no answer" cannot double-post. Derived from the reply's identity
    # (type + targets + parent + summary), so the original and the retry compute
    # the SAME key — which is the entire property being relied on.
    try:
        import hashlib as _hl

        from empirica.core.mesh_content import idempotency_key

        # `parent_id` and `summary` are BOTH in `_VOLATILE_PARAM_KEYS` — stripped
        # as per-emission ids and free-text prose. That is right for a generic
        # propose and CATASTROPHIC here: passing them as params left `{}`, so
        # every reply of the same type to the same peer computed the IDENTICAL
        # key. The ledger then swallowed genuinely new replies as replays and
        # returned ok:true. Reported 2026-09-08 — a distinct message lost, with
        # the caller's own fields echoed back as if stored.
        #
        # For a REPLY the parent IS the action identity: "reply to X with body Y"
        # is a different action from "reply to Z". So the same semantics ride
        # non-volatile names — and the digest, not the prose, because the volatile
        # list is right that free text should not key anything directly.
        propose_body.setdefault("payload", {})
        propose_body["payload"]["idempotency_key"] = idempotency_key(
            proposal_type,
            ",".join(sorted(target_claudes)),
            {
                "reply_to": parent_id,
                "body_digest": _hl.sha256((summary or "").encode()).hexdigest()[:16],
            },
        )
        retry_is_safe = True
    except Exception as e:  # never block a reply on the key helper
        # The retry below is safe ONLY because the ledger can recognise a
        # replay. Without a key it cannot, so a retry after an unknown
        # outcome is exactly how one reply becomes two. This used to SAY
        # "retry disabled" and then retry anyway — the message was the only
        # thing that changed, which is worse than saying nothing, because an
        # operator reading it rules out the double-post that just happened.
        retry_is_safe = False
        sys.stderr.write(
            f"mailbox reply: could not compute idempotency_key ({e}) — a retry after an unknown "
            "outcome could double-post, so the retry is genuinely skipped. If this call reports no "
            "response, check the outbox before re-sending by hand.\n"
        )

    status, propose_resp = _http_post(propose_url, propose_body, api_key, 10.0)

    # status == -1 is the TRANSPORT branch: no HTTP response arrived at all.
    # That is "I never heard back", NOT "the server said no" — and the server may
    # well have committed. Aborting here is what strands the parent: measured
    # 2026-09-06, a read timeout left the peer holding the reply while the
    # sender's parent stayed accepted with completed_at null, which is precisely
    # the stalled handshake this verb exists to prevent. A second practice hit
    # the other half and double-sent after a "failed" reply that had succeeded.
    #
    # Note the asymmetry this corrects: the step-2 failure below was ALREADY
    # handled gracefully with a precise message. Whoever wrote it thought about a
    # partial apply BETWEEN the steps and not about an unknown outcome WITHIN one.
    #
    # The retry is gated on actually HOLDING a key, because the key is the
    # entire reason it is safe: without one the ledger cannot recognise a
    # replay, and retrying an unknown outcome is how one reply becomes two.
    if status == -1 and retry_is_safe:
        sys.stderr.write(
            f"mailbox reply: no response from cortex ({propose_resp.get('error')}) — "
            "UNKNOWN, not failed. Retrying with the idempotency key; if the first "
            "propose committed, this returns that same proposal rather than a second one.\n"
        )
        status, propose_resp = _http_post(propose_url, propose_body, api_key, 20.0)

    new_proposal_id = propose_resp.get("proposal_id") if isinstance(propose_resp, dict) else None
    propose_ok = (200 <= status < 300) and new_proposal_id is not None
    if not propose_ok:
        if status == -1:
            # Still no answer. Report the state honestly — UNRESOLVED, with the
            # reconcile step — rather than claiming a rejection that may not have
            # happened. Exit 2 so a caller can tell it from a real refusal.
            sys.stderr.write(
                f"mailbox reply: cortex did not respond, twice ({propose_resp.get('error')}). "
                "The reply MAY have been delivered — this is UNKNOWN, not rejected.\n"
                "  Check whether it landed:  empirica mailbox poll --outbox\n"
                f"  If it did, close the parent with cortex_complete_proposal on {parent_id}.\n"
                "  Re-running this command is safe: the idempotency key collapses a duplicate.\n"
            )
            return 2
        if status == 409 and isinstance(propose_resp, dict) and "idempotency_key_conflict" in str(propose_resp):
            # NOT an ordinary refusal. Cortex's ledger stores a fingerprint of
            # parent|title|summary — the fields a genuine retry cannot differ on
            # — and a key hit whose fingerprint differs means OUR key derivation
            # is colliding. Nothing was sent and nothing was replayed.
            #
            # Distinguished because the remedy is entirely different: a 403 is
            # "you may not do this", a 409 here is "this client has the bug that
            # swallowed messages in 1.13.39". Reporting it as a generic rejection
            # would send the operator looking at permissions.
            sys.stderr.write(
                "mailbox reply: cortex REFUSED on an idempotency-key CONFLICT (409). Your message was "
                "NOT sent and nothing was replayed — this is a client-side key-derivation collision, "
                "not a permission problem.\n"
                "  Two different replies computed the same key. Upgrade: 1.13.39 and earlier derived "
                "the key from fields the hasher discards, so every reply of one type to one peer "
                "collided.\n"
                f"  Server detail: {propose_resp}\n"
            )
            return 3
        sys.stderr.write(f"mailbox reply: cortex_propose was REJECTED (status={status}): {propose_resp}\n")
        return 1
    if not new_proposal_id:
        sys.stderr.write(f"mailbox reply: cortex_propose returned no proposal_id: {propose_resp}\n")
        return 1

    # Step 2: cortex_complete_proposal (unless --no-close). On a multi-target parent this acknowledges OUR target only.
    parent_closed = False  # terminal: every target has acked
    parent_acked = False  # our own ack is recorded (true for a terminal close too)
    complete_resp: dict = {}
    no_close = bool(getattr(args, "no_close", False))
    if not no_close:
        complete_url = f"{cortex_url.rstrip('/')}/v1/orchestration/{parent_id}/complete"
        complete_body = {
            "api_key": api_key,
            "result": getattr(args, "result", None) or "shipped",
            "note": f"Replied via {new_proposal_id}",
        }
        commit_sha = getattr(args, "commit_sha", None)
        if commit_sha:
            complete_body["commit_sha"] = commit_sha
        ack_ai_id = None
        if _is_multi_target(parent):
            # Same canonical-form requirement as the archive leg below: a bare slug is a guaranteed participant mismatch, so an
            # unresolvable seat omits the key (cortex infers it when exactly one target is ours) rather than sending a wrong one.
            ack_ai_id = (
                source_claude
                if source_claude.count(".") >= 2
                else _resolve_canonical_ai_id(cortex_url, api_key, source_claude, _default_http_get)
            )
            if ack_ai_id:
                complete_body["ai_id"] = ack_ai_id
        c_status, complete_resp = _http_post(complete_url, complete_body, api_key, 10.0)
        # Cortex returns 2xx on completion success (response shape varies).
        complete_ok = (
            isinstance(complete_resp, dict)
            and 200 <= c_status < 300
            and (complete_resp.get("ok") is not False)  # tolerate missing "ok"
            and complete_resp.get("error") is None
        )
        if not complete_ok:
            sys.stderr.write(
                f"mailbox reply: cortex_propose SUCCEEDED (new={new_proposal_id}) "
                f"but parent close FAILED (status={c_status}): {complete_resp}. "
                f"Run cortex_complete_proposal via MCP to close manually.\n"
            )
            # Propose succeeded — surface the partial result rather than fail hard
        elif (
            ack_ai_id
            and isinstance(complete_resp.get("target_acks"), dict)
            and ack_ai_id not in complete_resp["target_acks"]
        ):
            # A 2xx is not proof that OUR target acked. If cortex returns the per-target acks and this seat is not among them (an older
            # cortex, or a refusal shaped as success), claiming the ack would archive an ask we never answered. Absent target_acks is
            # not contradiction and is accepted as before.
            sys.stderr.write(
                f"mailbox reply: cortex answered {c_status} but its target_acks does not list {ack_ai_id}, so this seat's ack is NOT "
                f"counted and the parent stays in your inbox: {complete_resp}\n"
            )
        else:
            parent_acked = True
            note = complete_resp.get("note")
            pending = complete_resp.get("pending_targets")
            if note == "acked_waiting_for_other_targets" or (isinstance(pending, list) and pending):
                parent_closed = False
            elif note == "already_acked":
                parent_closed = pending == []
            else:
                parent_closed = True

    # Step 3: cortex_archive_proposal on the parent (unless --no-archive or our ack failed)
    # Once the parent is completed, archiving removes it from cortex_inbox_poll's
    # status filters — keeps the AI's inbox view focused on un-actioned work.
    # Opt-out via --no-archive if you want the parent to stay visible in
    # status=accepted polls for audit/review purposes.
    # Archive is per recipient (archived_by[]), so clearing our copy after OUR ack does not hide the open ask from the other targets.
    parent_archived = False
    no_archive = bool(getattr(args, "no_archive", False))
    if parent_acked and not no_archive:
        archive_url = f"{cortex_url.rstrip('/')}/v1/orchestration/{parent_id}/archive"
        archive_body = {
            "api_key": api_key,
            "reason": f"auto-archived after mailbox reply (replied via {new_proposal_id})",
        }
        # Same requirement the standalone `archive` verb hit: cortex refuses to
        # guess WHOSE mailbox to clear when several of the caller's practices
        # participate — and it matches the CANONICAL 3-form, not the bare slug.
        # `source_claude` defaults from project.yaml, which carries only the
        # slug; sending that raw form is a guaranteed participant-mismatch 400
        # (the propose leg succeeds because cortex canonicalizes the SOURCE for
        # routing, so the reply lands and only the archive silently fails —
        # observed live on prop_764q2pdzgzfo5hzn4v6xxopywm). Roster-resolve the
        # slug like the archive verb does; a value already in 3-form passes
        # through; unresolvable omits the key rather than sending a wrong one.
        archive_ai_id = (
            source_claude
            if source_claude.count(".") >= 2
            else _resolve_canonical_ai_id(cortex_url, api_key, source_claude, _default_http_get)
        )
        if archive_ai_id:
            archive_body["ai_id"] = archive_ai_id
        a_status, archive_resp = _http_post(archive_url, archive_body, api_key, 10.0)
        # `no_op: true` is cortex saying it held nothing to archive (id unknown or
        # expired) — a 200 with `ok: true` that changed nothing. Not "archived".
        archive_ok = (
            isinstance(archive_resp, dict)
            and 200 <= a_status < 300
            and (archive_resp.get("ok") is not False)
            and archive_resp.get("error") is None
            and archive_resp.get("no_op") is not True
        )
        if archive_ok:
            parent_archived = True
        else:
            sys.stderr.write(
                f"mailbox reply: archive of parent {parent_id[:18]}… FAILED "
                f"(status={a_status}): {archive_resp}. "
                f"Parent stays in inbox until manually archived via "
                f"cortex_archive_proposal.\n"
            )

    # Structured output.
    # `proposal_id` is the canonical key — it matches `mailbox archive` and is what a
    # caller reaches for when confirming what it just emitted. `new_proposal_id` is the
    # original name, kept as an alias so any out-of-repo consumer keeps working; both
    # always carry the same value. Cortex looked for `proposal_id`, found nothing, and
    # reported the ack path as an unverifiable success (prop_t5tl6noq).
    # WAS THIS A FRESH WRITE OR A REPLAY? A correct key stops collisions; it does
    # not make a legitimate replay distinguishable from a new store, and on the
    # retry path that is exactly the operator's question — did my first propose
    # land, or did this one create a second? Cortex signals it
    # (`status='idempotent_replay'`, `replay_caught`); the client ignored it, so
    # both outcomes rendered as an identical ok:true + proposal_id.
    #
    # `unknown` when the server said nothing: an older cortex does not emit the
    # flag, and reporting "fresh" on its silence would be inventing the answer.
    _replay = None
    if isinstance(propose_resp, dict):
        if propose_resp.get("replay_caught") is not None:
            _replay = bool(propose_resp["replay_caught"])
        elif propose_resp.get("status") == "idempotent_replay":
            _replay = True
        elif propose_resp.get("status"):
            _replay = False

    result = {
        "ok": True,
        "proposal_id": new_proposal_id,
        "new_proposal_id": new_proposal_id,
        # True = the ledger returned an EXISTING proposal (your earlier attempt
        # landed). False = newly stored. None = the server did not say.
        "idempotent_replay": _replay,
        "parent_id": parent_id,
        "parent_closed": parent_closed,
        "parent_acked": parent_acked,
        "pending_targets": complete_resp.get("pending_targets") if isinstance(complete_resp, dict) else None,
        "target_acks": complete_resp.get("target_acks") if isinstance(complete_resp, dict) else None,
        "parent_archived": parent_archived,
        "result": (getattr(args, "result", None) or "shipped") if not no_close else None,
        "target_claudes": target_claudes,
        "title": title,
    }
    fmt = getattr(args, "output", "json")
    if fmt == "human":
        if no_close:
            action = "kept-open (--no-close)"
        elif parent_acked and not parent_closed:
            waiting = len(result["pending_targets"] or [])
            action = f"acked, {waiting} other target(s) still owe an ack" if waiting else "acked (already acked)"
            if parent_archived:
                action += " +archived (own copy)"
        elif parent_closed:
            tag = "+archived" if parent_archived else (" (archive-failed)" if not no_archive else "")
            action = f"closed (result={result['result']}){tag}"
        else:
            action = "complete-failed (see stderr; manual ack needed)"
        sys.stdout.write(f"reply {new_proposal_id[:18]}… sent · parent {parent_id[:18]}… {action}\n")
    else:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


def _default_fetch_mailbox(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    *,
    outbox: bool,
    statuses: tuple[str, ...],
    since: str | None,
    limit: int | None,
    related: bool,
    timeout: float = 10.0,
    meta_out: dict | None = None,
) -> list[dict]:
    """Wrap content_poll's canonical-resolving inbox/outbox fetchers.

    Reuses the exact GET + `ai_id` → canonical-3-form resolution the listener
    uses (bare basename returns 0 proposals — the silent break that once left
    every listener deaf), so the CLI can't drift from the listener's contract.
    """
    from empirica.core.loop_scheduler.content_poll import (
        _resolve_canonical_ai_id as resolve_canonical,
    )
    from empirica.core.loop_scheduler.content_poll import (
        fetch_cortex_inbox,
        fetch_cortex_outbox,
    )

    # Cortex answers a bare basename with zero proposals, which reads exactly like an empty inbox. Resolve first and refuse to ask
    # under a basename; the envelope says which id was actually queried so a consumer can tell the two cases apart.
    canonical = resolve_canonical(cortex_url, api_key, ai_id)
    if meta_out is not None:
        meta_out["queried_as"] = canonical
        meta_out["canonical_resolved"] = "." in canonical
    if "." not in canonical:
        return []

    fetch = fetch_cortex_outbox if outbox else fetch_cortex_inbox
    return fetch(
        cortex_url,
        api_key,
        ai_id,
        meta_out=meta_out,
        statuses=statuses,
        since=since,
        limit=limit,
        related=related,
        timeout=timeout,
    )


_GATED = ("TACTICAL", "STRATEGIC")
_ACTED_STATUSES = ("accepted", "accepted_pending_dispatch", "changed", "completed")


def _decision_gap(p: dict) -> str | None:
    """Warning when gated work reads as accepted but no ECO decision is recorded.

    ``status=accepted`` is not evidence a human accepted: autonomy triage can
    accept TACTICAL/STRATEGIC work with eco_decision null (measured 2026-09-18:
    two code changes to core, trail triage -> handler_on_accept/system). The
    discriminator is ``eco_decision``, NOT ``decided_by_kind``: cortex showed
    the latter reads "system" for publish rows a human decided in the pane,
    because the publish lane's action names are outside its vocabulary. And
    ``actor=David`` proves nothing either (it defaults to the credential's
    user). So the flag says what the record shows — no ECO decision recorded —
    and not that no human was involved. None when the item is ungated, not yet
    acted on, or carries an ECO decision.
    """
    if p.get("type") == "collab_brief":
        return None  # collab is auto-accepted by design and cannot carry a praxic act
    tier = str(p.get("trust_required") or p.get("action_category") or "").upper()
    if tier not in _GATED or p.get("status") not in _ACTED_STATUSES:
        return None
    if p.get("eco_decision") is not None:
        return None
    return (
        f"{tier} work reads as {p.get('status')} with NO ECO decision recorded (eco_decision null). "
        "Before acting, check the audit trail for a human decision, or ask David as a predicted answer "
        "unless he has ruled this trust tier auto-accepts."
    )


def _poll_human_line(p: dict) -> str:
    """One compact line per proposal for `--output human`."""
    pid = str(p.get("id", ""))[:24]
    status = p.get("status", "?")
    title = str(p.get("title", ""))[:68]
    src = p.get("source_claude", "?")
    flag = "  ⚠ NOT HUMAN-DECIDED" if _decision_gap(p) else ""
    return f"  {pid}… [{status}] {title}  <from {src}>{flag}"


def _default_poll_statuses(outbox: bool) -> tuple[str, ...]:
    """Default filters. `accepted` is on BOTH sides, and that is the fix.

    The outbox default was ("completed", "changed", "declined") — reasoned from
    "status changes on your emissions", which is right for a WAKE filter and
    wrong for a report. `accepted` is not a transient state on the outbox: it is
    the TERMINAL state of every collab. Excluding it hid the only status most
    emissions will ever hold.

    Measured by empirica-cortex against real rows: 182 emissions from one
    source, 21 visible under the old default. The "newest visible" timestamp
    matched a reported cutoff to the second — it was not a date bound at all,
    it was the last non-collab emission, after which everything was invisible
    by kind.

    NOTE: `content_poll.EMISSION_STATUSES_OUTBOX` still excludes plain
    `accepted` deliberately — waking on every outbox accept is noise, and that
    is documented there. Reporting and waking are different questions; this
    default answers the reporting one.
    """
    if outbox:
        return ("accepted", "accepted_pending_dispatch", "changed", "declined", "completed")
    return ("accepted", "changed")


def _resolve_poll_statuses(status_arg: str | None, *, outbox: bool) -> tuple[str, ...] | None:
    """Resolve --status into the statuses to query.

    Returns the tuple to filter on, `POLL_NO_FILTER` (empty) to send no filter at
    all, or None meaning "rejected, already reported" — which now happens only for
    an empty `--status` value, never for an unrecognised one.

    Two defects lived here and they pulled in opposite directions.

    A filter that selects nothing because the FILTER is wrong must not look like a
    filter that selects nothing because there IS nothing. That is why unrecognised
    values were rejected rather than passed through.

    But the list they were checked against is hand-maintained over cortex's
    vocabulary, so it went stale and the rejection blocked real work: outreach
    could not enumerate `targets_pending`, and `failed`/`wont_fix` were unreachable
    by any means while the mailbox protocol instructs practitioners to act on
    exactly those states — so an emitter could not count their own undelivered
    sends, and "sender owns delivery" had no client-side mechanism.

    Both are now satisfied without a local gate. Cortex validates its own
    vocabulary and answers an unknown value with a 400 naming the valid set, so a
    typo is loud at the authority. Against an OLDER cortex that does not validate,
    the note below is what keeps it from being silent. The client no longer
    arbitrates a vocabulary it does not own.
    """
    from empirica.cli.parsers.mailbox_parsers import (
        POLL_NO_FILTER,
        POLL_STATUS_ALL,
        VALID_POLL_STATUSES,
    )

    if not status_arg or not tuple(s.strip() for s in status_arg.split(",") if s.strip()):
        return _default_poll_statuses(outbox)

    requested = tuple(s.strip() for s in status_arg.split(",") if s.strip())

    if POLL_STATUS_ALL in requested:
        # NO filter — not an expansion of the known list. Expanding it is what made
        # `--status all` answer with 7 of 13 while promising "every status".
        return POLL_NO_FILTER

    unrecognised = [s for s in requested if s not in VALID_POLL_STATUSES]
    if unrecognised:
        # A NOTE, not a rejection. Passing through is what lets a status newer than
        # this CLI work at all; the note is what stops a typo returning a confident
        # empty set on a cortex too old to validate.
        sys.stderr.write(
            f"mailbox poll: note — {', '.join(unrecognised)} "
            f"{'is' if len(unrecognised) == 1 else 'are'} not in this CLI's known "
            "status list, which may simply be out of date. Passing through; cortex "
            "will reject it if it is not a real status. An empty result here may "
            "mean the value is wrong rather than that nothing matched.\n"
        )
    return requested


def _attach_poll_completeness(result: dict, poll_meta: dict) -> None:
    """Completeness, when cortex reports it. `count` is what THIS poll returned; `matched` is how many exist. Without the pair, a
    truncated poll is indistinguishable from a complete one, which made "have I replied to everything?" unanswerable at the CLI layer
    and pushed backlog triage onto memory. Keys are omitted entirely when cortex does not send them, so an older cortex yields the
    previous envelope byte-for-byte. `queried_as` names the id the inbox was actually asked for."""
    if poll_meta.get("queried_as"):
        result["queried_as"] = poll_meta["queried_as"]
    if poll_meta.get("matched") is not None:
        result["matched"] = poll_meta["matched"]
    if poll_meta.get("has_more") is not None:
        result["has_more"] = poll_meta["has_more"]
        if poll_meta["has_more"]:
            result["truncated_hint"] = "more proposals match than were returned — raise --limit or page with --since"


def _poll_unresolved_response(args, ai_id: str, direction: str, poll_meta: dict) -> int:
    """Report that the canonical id could not be resolved: the box was NOT checked, which an empty result would have hidden."""
    message = (
        f"canonical id for {ai_id!r} could not be resolved (the roster lookup failed), so the {direction} was NOT checked; "
        "an empty result here would have meant 'unknown', not 'no mail'. Retry when cortex is reachable."
    )
    if getattr(args, "output", "json") == "human":
        sys.stderr.write(f"mailbox poll: {message}\n")
    else:
        body = {"ok": False, "error": message, "ai_id": ai_id, "queried_as": poll_meta.get("queried_as")}
        sys.stdout.write(json.dumps(body, indent=2) + "\n")
    return 1


def handle_mailbox_poll_command(
    args,
    *,
    _resolve_cortex_creds: Callable[[], tuple] = _default_resolve_cortex_creds,
    _resolve_ai_id: Callable[[], str | None] = _default_resolve_ai_id,
    _fetch_mailbox: Callable[..., list[dict]] = _default_fetch_mailbox,
) -> int:
    """`empirica mailbox poll` — the receive side, symmetric with `reply`.

    Wraps `GET /v1/orchestration/{inbox,outbox}` so ANY CLI surface gets a
    reliable receive path (no MCP namespace gymnastics — the blocker for
    tool-aggregating harnesses like codex/ecodex). Implements prop_jdldx2pz,
    shape endorsed by cortex prop_bbtqnc.

    Default `--status accepted,changed` (the wake-react actionable set) — this
    DIVERGES from the `cortex_inbox_poll` MCP default of `eco_review` by design:
    the CLI's purpose is reacting to ECO-decided wakes, not reviewing pending.
    """
    # Argument validation FIRST — before creds, before ai_id, before any I/O.
    # A malformed --status is a usage error, and whether it is malformed does
    # not depend on whether this box is configured. Validating after the creds
    # check meant a typo reported "Cortex creds missing" on an unconfigured box:
    # the wrong diagnosis, pointing the reader at an unrelated thing to fix.
    #
    # It also made the tests for it environment-dependent — they passed here
    # (creds present) and failed on CI (creds absent), which is how this
    # ordering got noticed at all.
    outbox = bool(getattr(args, "outbox", False))
    statuses = _resolve_poll_statuses(getattr(args, "status", None), outbox=outbox)
    if statuses is None:
        return 1

    cortex_url, api_key = _resolve_cortex_creds()
    if not cortex_url or not api_key:
        sys.stderr.write(
            "mailbox poll: Cortex creds missing — configure cortex.url + "
            "cortex.api_key in ~/.empirica/credentials.yaml or set "
            "CORTEX_REMOTE_URL + CORTEX_API_KEY env vars.\n"
        )
        return 1

    ai_id = getattr(args, "ai_id", None) or _resolve_ai_id()
    if not ai_id:
        sys.stderr.write("mailbox poll: ai_id unresolved — set --ai-id or add ai_id to .empirica/project.yaml.\n")
        return 1

    since = getattr(args, "since", None)
    limit = getattr(args, "limit", None)
    related = bool(getattr(args, "related", False))

    _poll_meta: dict = {}
    try:
        proposals = _fetch_mailbox(
            cortex_url,
            api_key,
            ai_id,
            outbox=outbox,
            statuses=statuses,
            since=since,
            limit=limit,
            related=related,
            meta_out=_poll_meta,
        )
    except Exception as e:  # network / auth / parse — surface, don't crash
        sys.stderr.write(f"mailbox poll: fetch failed: {type(e).__name__}: {e}\n")
        return 1

    direction = "outbox" if outbox else "inbox"
    if _poll_meta.get("canonical_resolved") is False:
        return _poll_unresolved_response(args, ai_id, direction, _poll_meta)
    result = {
        "ok": True,
        "ai_id": ai_id,
        "direction": direction,
        # An empty tuple means no filter was sent. Echoing `[]` would read as
        # "filtered to nothing" — the opposite of what happened — so say so.
        "statuses": list(statuses) if statuses else "all (no filter sent)",
        "count": len(proposals),
        "proposals": proposals,
    }
    ungated = [str(p.get("id")) for p in proposals if isinstance(p, dict) and _decision_gap(p)]
    if ungated:
        result["not_human_decided"] = ungated
    _attach_poll_completeness(result, _poll_meta)
    # Printed here, not returned, so the CLI core never sees it: record the
    # partial page for the truncation hook ourselves.
    from empirica.utils import partial_view

    partial_view.record("mailbox poll", result)

    fmt = getattr(args, "output", "json")
    if fmt == "human":
        # The completeness pair belongs in BOTH formats. It was surfaced in the
        # JSON envelope and nowhere else, so `--output human` went on printing
        # only the page size — and a page of N is indistinguishable from a
        # backlog of N, which is the whole defect the `matched` field was added
        # to remove. Measured on a peer's sweep: they read `--limit 60 -> 60` as
        # 60 unarchived when the real figure was 154, then burned nine no-op
        # cleanup passes proving a negative because nothing told them when they
        # were done. Fixing one format and leaving the other is how a fixed
        # defect keeps being encountered.
        header = f"{direction}: {len(proposals)} proposal(s)"
        matched = _poll_meta.get("matched")
        if matched is not None and matched != len(proposals):
            header += f" · {matched} match — SHOWING A PAGE, raise --limit or archive"
        elif matched is not None:
            header += f" · {matched} match (complete)"
        if not proposals:
            sys.stdout.write(f"{direction}: no proposals (status={','.join(statuses)})")
            sys.stdout.write(f" · {matched} match\n" if matched else "\n")
        else:
            sys.stdout.write(header + "\n")
            for p in proposals:
                sys.stdout.write(_poll_human_line(p) + "\n")
    else:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


def handle_mailbox_show_command(
    args,
    *,
    _resolve_cortex_creds: Callable[[], tuple] = _default_resolve_cortex_creds,
    _fetch_parent: Callable[[str, str, str], dict | None] = _default_fetch_parent,
) -> int:
    """`empirica mailbox show <proposal_id>` — GET /v1/orchestration/{id}.

    Companion to poll: full body of one proposal. Reuses `_default_fetch_parent`
    (the same GET `reply` uses for smart defaults).
    """
    proposal_id = getattr(args, "proposal_id", None)
    if not proposal_id:
        sys.stderr.write("mailbox show: <proposal_id> is required\n")
        return 1

    cortex_url, api_key = _resolve_cortex_creds()
    if not cortex_url or not api_key:
        sys.stderr.write(
            "mailbox show: Cortex creds missing — configure cortex.url + "
            "cortex.api_key in ~/.empirica/credentials.yaml.\n"
        )
        return 1

    try:
        proposal = _fetch_parent(cortex_url, api_key, proposal_id)
    except ParentFetchError as e:
        retry = " Re-run once cortex is reachable." if e.retryable else ""
        sys.stderr.write(f"mailbox show: could not fetch {proposal_id} — {e.reason}.{retry}\n")
        return 1
    if proposal is None:
        sys.stderr.write(
            f"mailbox show: {proposal_id} not found (cortex answered 404). Check the id and your Cortex tenant scope.\n"
        )
        return 1

    gap = _decision_gap(proposal)
    if gap:
        sys.stderr.write(f"⚠ mailbox show {proposal_id}: {gap}\n")
    fmt = getattr(args, "output", "json")
    if fmt == "human":
        sys.stdout.write(f"{proposal.get('id', '?')} [{proposal.get('status', '?')}]\n")
        if gap:
            sys.stdout.write(f"  ⚠ {gap}\n")
        sys.stdout.write(f"  {proposal.get('title', '')}\n")
        sys.stdout.write(f"  from {proposal.get('source_claude', '?')} → {proposal.get('target_claudes', [])}\n\n")
        sys.stdout.write(f"{proposal.get('summary', '')}\n")
    else:
        out = {"ok": True, "proposal": proposal}
        if gap:
            out["decision_warning"] = gap
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    return 0


def handle_mailbox_archive_command(
    args,
    *,
    _resolve_cortex_creds: Callable[[], tuple] = _default_resolve_cortex_creds,
    _http_post: Callable[[str, dict, str, float], tuple] = _default_http_post,
    _http_get: Callable[[str, str, float], tuple] = _default_http_get,
    _resolve_ai_id: Callable[[], str | None] = _default_resolve_ai_id,
) -> int:
    """`empirica mailbox archive <proposal_id>` — POST /v1/orchestration/{id}/archive.

    Soft-delete from the inbox view (same primitive `reply` auto-invokes on close).

    Sends `ai_id` because cortex REQUIRES it whenever more than one of the
    caller's practices participates in the proposal, and refuses rather than
    guessing — archiving all of them would hide the thread from practitioners
    that never acted. That refusal is correct and the client had no way to
    satisfy it: the flag did not exist and the body never carried the field, so
    every broadcast thread (the normal shape for cortex and mesh-support
    announcements) was permanently unarchivable, with the error telling the
    operator to pass something nothing could pass.

    Resolved from the roster rather than assembled locally: `project.yaml` holds
    the bare slug and cortex keys mailboxes by the canonical 3-form.
    """
    proposal_id = getattr(args, "proposal_id", None)
    if not proposal_id:
        sys.stderr.write("mailbox archive: <proposal_id> is required\n")
        return 1

    cortex_url, api_key = _resolve_cortex_creds()
    if not cortex_url or not api_key:
        sys.stderr.write(
            "mailbox archive: Cortex creds missing — configure cortex.url + "
            "cortex.api_key in ~/.empirica/credentials.yaml.\n"
        )
        return 1

    ai_id = getattr(args, "ai_id", None) or _resolve_canonical_ai_id(cortex_url, api_key, _resolve_ai_id(), _http_get)

    archive_url = f"{cortex_url.rstrip('/')}/v1/orchestration/{proposal_id}/archive"
    reason = getattr(args, "reason", None) or "archived via empirica mailbox archive"
    body: dict = {"api_key": api_key, "reason": reason}
    # Omitted rather than sent as None when unresolvable: a null ai_id is not the
    # same request as an absent one, and single-participant archives must keep
    # working on a box that cannot reach the roster.
    if ai_id:
        body["ai_id"] = ai_id
    status, resp = _http_post(archive_url, body, api_key, 10.0)
    # Archiving is idempotent, so an already-archived proposal is the desired state,
    # not an error. Cortex says so precisely — HTTP 200 with `is_archived: true` —
    # and the old check still failed it, because `error` was set. A caller archiving
    # a batch would see "failed" on every proposal it had already handled and could
    # not tell those apart from a genuine failure. Same false-signal family as the
    # rest of this surface, in the other direction: a false negative on a no-op.
    already = isinstance(resp, dict) and resp.get("is_archived") is True
    ok = (
        isinstance(resp, dict)
        and 200 <= status < 300
        and (already or (resp.get("error") is None and resp.get("ok") is not False))
    )
    if not ok:
        sys.stderr.write(f"mailbox archive: failed (status={status}): {resp}\n")
        return 1

    # Cortex answers an id it does not hold with HTTP 200, `ok: true` AND
    # `no_op: true` + a reason — nothing was archived. The old rendering dropped
    # both fields and printed `archived: true`, so 76 archives of the literal id
    # "null" (a caller's jq slip) each reported success, and the inbox count was
    # the only thing that disagreed. A no-op is not the requested state; say so
    # and exit non-zero so a batch caller's per-item check sees it.
    no_op = resp.get("no_op") is True
    reason_code = resp.get("reason") if no_op else None
    fmt = getattr(args, "output", "json")
    if no_op:
        if fmt == "human":
            sys.stdout.write(f"not archived {proposal_id[:24]}… — cortex: {reason_code or 'no_op'}\n")
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "ok": True,
                        "proposal_id": proposal_id,
                        "archived": False,
                        "already_archived": False,
                        "no_op": True,
                        "reason": reason_code,
                    },
                    indent=2,
                )
                + "\n"
            )
        return 2

    if fmt == "human":
        suffix = " (already archived)" if already else ""
        sys.stdout.write(f"archived {proposal_id[:24]}…{suffix}\n")
    else:
        sys.stdout.write(
            json.dumps(
                {"ok": True, "proposal_id": proposal_id, "archived": True, "already_archived": already},
                indent=2,
            )
            + "\n"
        )
    return 0


def handle_mailbox_group_command(args) -> int:
    """Dispatch `empirica mailbox <action>`."""
    action = getattr(args, "mailbox_action", None)
    if action == "reply":
        return handle_mailbox_reply_command(args)
    if action == "poll":
        return handle_mailbox_poll_command(args)
    if action == "show":
        return handle_mailbox_show_command(args)
    if action == "archive":
        return handle_mailbox_archive_command(args)
    sys.stderr.write("Usage: empirica mailbox <reply|poll|show|archive>\n")
    return 1
