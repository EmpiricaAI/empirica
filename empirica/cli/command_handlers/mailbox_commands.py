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

    # Structured output
    result = {
        "ok": True,
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
    ok = isinstance(resp, dict) and 200 <= status < 300 and resp.get("error") is None and resp.get("ok") is not False
    if not ok:
        sys.stderr.write(f"mailbox archive: failed (status={status}): {resp}\n")
        return 1

    fmt = getattr(args, "output", "json")
    if fmt == "human":
        sys.stdout.write(f"archived {proposal_id[:24]}…\n")
    else:
        sys.stdout.write(json.dumps({"ok": True, "proposal_id": proposal_id, "archived": True}, indent=2) + "\n")
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
