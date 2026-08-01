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
    """Resolve Cortex URL + api_key from credentials_loader."""
    try:
        from empirica.config.credentials_loader import get_credentials_loader

        cfg = get_credentials_loader().get_cortex_config()
        return cfg.get("url"), cfg.get("api_key")
    except Exception:
        return None, None


def _default_resolve_ai_id() -> str | None:
    """Read ai_id from .empirica/project.yaml in current project root."""
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
    except Exception:
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


def _default_fetch_parent(cortex_url: str, api_key: str, parent_id: str, timeout: float = 5.0) -> dict | None:
    """GET /v1/orchestration/<id> for parent body. Response is the proposal object directly."""
    url = f"{cortex_url.rstrip('/')}/v1/orchestration/{parent_id}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if isinstance(body, dict) and (body.get("id") or body.get("title")):
                return body
            # Fallback for wrapped response shape (future-compat)
            if isinstance(body, dict) and body.get("proposal"):
                return body["proposal"]
            return None
    except Exception:
        return None


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

    SER participants are keyed by canonical id. `.empirica/project.yaml` carries only
    the bare project slug (`empirica`), so querying with it returns an empty list —
    and an empty list is indistinguishable from genuine non-participation.

    That is not hypothetical: the first live run of this verb reported "participates in
    none" while the practice held `role=required` on four SERs. The bare id produced a
    confident false negative in the very tool built to stop confident false negatives.

    So the id is READ from the authority rather than assembled from local slugs
    (project.yaml has no org slug, and guessing one would reintroduce the same class).
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


def _ser_rows(body: object) -> list[dict]:
    """Normalise the response into a list of SER dicts.

    Three shapes are tolerated because the envelope is specified in the mesh skills
    rather than observed here: a wrapped `{"sers": [...]}`, a bare list, and a single
    object from the by-id route.
    """
    if isinstance(body, dict) and isinstance(body.get("sers"), list):
        return [s for s in body["sers"] if isinstance(s, dict)]
    if isinstance(body, list):
        return [s for s in body if isinstance(s, dict)]
    if isinstance(body, dict) and body:
        return [body]
    return []


def _ser_line(s: dict) -> str:
    sid = str(s.get("ser_id") or s.get("id") or "?")[:24]
    state = str(s.get("coordination_state") or "?")
    title = str(s.get("title") or "")[:56]
    n = len(s.get("participants") or [])
    return f"  {sid} [{state}] {title}  ({n} participant{'' if n == 1 else 's'})"


def handle_mailbox_sers_command(
    args,
    *,
    _resolve_cortex_creds: Callable = _default_resolve_cortex_creds,
    _resolve_ai_id: Callable = _default_resolve_ai_id,
    _http_get: Callable = _default_http_get,
) -> int:
    """`empirica mailbox sers [<ser_id>]` — read-only view of SER participation.

    Consolidated into the `mailbox` group rather than shipped as a top-level verb.
    This group is already the cortex-HTTP AI-mesh *content* namespace (poll / show /
    reply / archive) and already owns credential and ai_id resolution, and the standing
    guidance is that the default answer to "add a verb" is no. `mesh diagnose --cortex`
    was the other candidate and is wrong: it produces pass/warn/fail health checks, and
    this is a data read, not a verdict.

    Read-only on purpose. Transitions and acks already exist through `cortex_propose`
    payload actions, and prior probes of the write side failed anyway — an unknown
    `cortex_ser_ack` tool, and a POST answered `Method Not Allowed`.

    Why it exists: nothing in the CLI reached `/v1/sers`, so a practitioner could not
    answer "am I a participant on this SER" without MCP or raw HTTP. Three seats spent
    five messages establishing required-tier on two records — and answered at three
    different evidence grades, because only some could reach the store. An unreachable
    read does not merely slow people down; it lowers the quality of what they can assert.
    """
    output_format = getattr(args, "output", "json")
    cortex_url, api_key = _resolve_cortex_creds()
    if not cortex_url or not api_key:
        sys.stderr.write(
            "mailbox sers: no cortex credentials — set cortex.url + cortex.api_key in ~/.empirica/credentials.yaml\n"
        )
        return 1

    ser_id = getattr(args, "ser_id", None)
    ai_id = getattr(args, "ai_id", None)
    if not ai_id and not ser_id:
        # Resolve the CANONICAL id, never the bare project slug — see
        # _resolve_canonical_ai_id for why a basename query lies rather than fails.
        ai_id = _resolve_canonical_ai_id(cortex_url, api_key, _resolve_ai_id(), _http_get)

    if ser_id:
        url = f"{cortex_url.rstrip('/')}/v1/sers/{ser_id}"
    elif ai_id and ai_id.count(".") >= 2:
        url = f"{cortex_url.rstrip('/')}/v1/sers?ai_id={ai_id}"
    elif ai_id:
        # Refuse rather than return a confident empty. A non-canonical id matches no
        # participant row, so the query would succeed and report zero — the exact
        # false negative this verb exists to remove.
        sys.stderr.write(
            f"mailbox sers: {ai_id!r} is not a canonical 3-form (org.tenant.project). "
            "A bare slug matches no participant row and would report zero participation. "
            "Pass the full form, or check it with `empirica practice-context --ai-id <slug>`.\n"
        )
        return 1
    else:
        sys.stderr.write(
            "mailbox sers: could not resolve a canonical ai_id from the roster — "
            "pass --ai-id <org.tenant.project>, or run inside a project\n"
        )
        return 1

    status, body = _http_get(url, api_key, 10.0)
    if status == 404 and ser_id:
        sys.stderr.write(f"mailbox sers: {ser_id} not found, or not visible to this tenant\n")
        return 1
    if not (200 <= status < 300):
        sys.stderr.write(f"mailbox sers: request failed (status={status}): {body}\n")
        return 1

    sers = _ser_rows(body)

    if output_format == "human":
        if not sers:
            # A successful query returning nothing is an ANSWER, not a failure. Saying
            # so is the whole point: the thread that prompted this verb went five
            # messages because absence and unreachability looked the same.
            scope = ser_id or f"ai_id={ai_id}"
            sys.stdout.write(f"No SERs for {scope} — queried successfully, this practice participates in none.\n")
        else:
            sys.stdout.write(f"{len(sers)} SER(s):\n")
            for s in sers:
                sys.stdout.write(_ser_line(s) + "\n")
                for p in s.get("participants") or []:
                    if isinstance(p, dict):
                        sys.stdout.write(
                            f"      {p.get('practice_id') or '?'}  role={p.get('role') or '?'}"
                            f"  last_ack={p.get('last_ack_at') or 'never'}\n"
                        )
    else:
        sys.stdout.write(
            json.dumps({"ok": True, "ai_id": ai_id, "ser_id": ser_id, "count": len(sers), "sers": sers}, indent=2)
            + "\n"
        )
    return 0


def handle_mailbox_reply_command(  # noqa: C901 — CLI handler with 7 validation gates + 2 HTTP calls; linear flow is clearer than extracting helpers
    args,
    *,
    _resolve_cortex_creds: Callable[[], tuple] = _default_resolve_cortex_creds,
    _resolve_ai_id: Callable[[], str | None] = _default_resolve_ai_id,
    _http_post: Callable[[str, dict, str, float], tuple] = _default_http_post,
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

    source_claude = getattr(args, "source_claude", None) or _resolve_ai_id()
    if not source_claude:
        sys.stderr.write(
            "mailbox reply: source_claude unresolved — set --source-claude or add ai_id to .empirica/project.yaml.\n"
        )
        return 1

    # Fetch parent for smart defaults (title prefix, target_claudes)
    parent = _fetch_parent(cortex_url, api_key, parent_id)
    if parent is None:
        sys.stderr.write(
            f"mailbox reply: parent {parent_id} not found or inaccessible. Check the id and your Cortex tenant scope.\n"
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
    status, propose_resp = _http_post(propose_url, propose_body, api_key, 10.0)
    # Cortex returns 2xx + proposal_id on success (no wrapper "ok" field).
    # Treat HTTP 2xx with a proposal_id as success regardless of "ok" presence.
    new_proposal_id = propose_resp.get("proposal_id") if isinstance(propose_resp, dict) else None
    propose_ok = (200 <= status < 300) and new_proposal_id is not None
    if not propose_ok:
        sys.stderr.write(f"mailbox reply: cortex_propose failed (status={status}): {propose_resp}\n")
        return 1
    if not new_proposal_id:
        sys.stderr.write(f"mailbox reply: cortex_propose returned no proposal_id: {propose_resp}\n")
        return 1

    # Step 2: cortex_complete_proposal (unless --no-close)
    parent_closed = False
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
        else:
            parent_closed = True

    # Step 3: cortex_archive_proposal on the parent (unless --no-archive or close failed)
    # Once the parent is completed, archiving removes it from cortex_inbox_poll's
    # status filters — keeps the AI's inbox view focused on un-actioned work.
    # Opt-out via --no-archive if you want the parent to stay visible in
    # status=accepted polls for audit/review purposes.
    parent_archived = False
    no_archive = bool(getattr(args, "no_archive", False))
    if parent_closed and not no_archive:
        archive_url = f"{cortex_url.rstrip('/')}/v1/orchestration/{parent_id}/archive"
        archive_body = {
            "api_key": api_key,
            "reason": f"auto-archived after mailbox reply (replied via {new_proposal_id})",
        }
        a_status, archive_resp = _http_post(archive_url, archive_body, api_key, 10.0)
        archive_ok = (
            isinstance(archive_resp, dict)
            and 200 <= a_status < 300
            and (archive_resp.get("ok") is not False)
            and archive_resp.get("error") is None
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
    result = {
        "ok": True,
        "proposal_id": new_proposal_id,
        "new_proposal_id": new_proposal_id,
        "parent_id": parent_id,
        "parent_closed": parent_closed,
        "parent_archived": parent_archived,
        "result": (getattr(args, "result", None) or "shipped") if not no_close else None,
        "target_claudes": target_claudes,
        "title": title,
    }

    fmt = getattr(args, "output", "json")
    if fmt == "human":
        if no_close:
            action = "kept-open (--no-close)"
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
) -> list[dict]:
    """Wrap content_poll's canonical-resolving inbox/outbox fetchers.

    Reuses the exact GET + `ai_id` → canonical-3-form resolution the listener
    uses (bare basename returns 0 proposals — the silent break that once left
    every listener deaf), so the CLI can't drift from the listener's contract.
    """
    from empirica.core.loop_scheduler.content_poll import (
        fetch_cortex_inbox,
        fetch_cortex_outbox,
    )

    fetch = fetch_cortex_outbox if outbox else fetch_cortex_inbox
    return fetch(
        cortex_url,
        api_key,
        ai_id,
        statuses=statuses,
        since=since,
        limit=limit,
        related=related,
        timeout=timeout,
    )


def _poll_human_line(p: dict) -> str:
    """One compact line per proposal for `--output human`."""
    pid = str(p.get("id", ""))[:24]
    status = p.get("status", "?")
    title = str(p.get("title", ""))[:68]
    src = p.get("source_claude", "?")
    return f"  {pid}… [{status}] {title}  <from {src}>"


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

    outbox = bool(getattr(args, "outbox", False))
    status_arg = getattr(args, "status", None)
    if status_arg:
        statuses = tuple(s.strip() for s in status_arg.split(",") if s.strip())
    else:
        # inbox → what you act on; outbox → status changes on your emissions.
        statuses = ("completed", "changed", "declined") if outbox else ("accepted", "changed")
    since = getattr(args, "since", None)
    limit = getattr(args, "limit", None)
    related = bool(getattr(args, "related", False))

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
        )
    except Exception as e:  # network / auth / parse — surface, don't crash
        sys.stderr.write(f"mailbox poll: fetch failed: {type(e).__name__}: {e}\n")
        return 1

    direction = "outbox" if outbox else "inbox"
    result = {
        "ok": True,
        "ai_id": ai_id,
        "direction": direction,
        "statuses": list(statuses),
        "count": len(proposals),
        "proposals": proposals,
    }

    fmt = getattr(args, "output", "json")
    if fmt == "human":
        if not proposals:
            sys.stdout.write(f"{direction}: no proposals (status={','.join(statuses)})\n")
        else:
            sys.stdout.write(f"{direction}: {len(proposals)} proposal(s)\n")
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

    proposal = _fetch_parent(cortex_url, api_key, proposal_id)
    if proposal is None:
        sys.stderr.write(
            f"mailbox show: {proposal_id} not found or inaccessible. Check the id and your Cortex tenant scope.\n"
        )
        return 1

    fmt = getattr(args, "output", "json")
    if fmt == "human":
        sys.stdout.write(f"{proposal.get('id', '?')} [{proposal.get('status', '?')}]\n")
        sys.stdout.write(f"  {proposal.get('title', '')}\n")
        sys.stdout.write(f"  from {proposal.get('source_claude', '?')} → {proposal.get('target_claudes', [])}\n\n")
        sys.stdout.write(f"{proposal.get('summary', '')}\n")
    else:
        sys.stdout.write(json.dumps({"ok": True, "proposal": proposal}, indent=2) + "\n")
    return 0


def handle_mailbox_archive_command(
    args,
    *,
    _resolve_cortex_creds: Callable[[], tuple] = _default_resolve_cortex_creds,
    _http_post: Callable[[str, dict, str, float], tuple] = _default_http_post,
) -> int:
    """`empirica mailbox archive <proposal_id>` — POST /v1/orchestration/{id}/archive.

    Soft-delete from the inbox view (same primitive `reply` auto-invokes on close).
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

    archive_url = f"{cortex_url.rstrip('/')}/v1/orchestration/{proposal_id}/archive"
    reason = getattr(args, "reason", None) or "archived via empirica mailbox archive"
    status, resp = _http_post(archive_url, {"api_key": api_key, "reason": reason}, api_key, 10.0)
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

    fmt = getattr(args, "output", "json")
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
    if action == "sers":
        return handle_mailbox_sers_command(args)
    sys.stderr.write("Usage: empirica mailbox <reply|poll|show|archive|sers>\n")
    return 1
