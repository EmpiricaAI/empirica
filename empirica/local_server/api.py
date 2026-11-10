"""empirica-local's group-A routes: identity, roster, project registry, mailbox, heartbeats, system events.

One person on one machine. Addresses use the reserved ``local`` namespace, ``local.<user>.<practice>``; the user is
``EMPIRICA_LOCAL_USER`` or the login name. The roster is the practices registered here plus the active practices in
core's workspace registry, each named by its directory (the ai_id convention). Authentication is off unless a token is
configured (``EMPIRICA_LOCAL_TOKEN`` or ``local_server_token`` in ``~/.empirica/credentials.yaml``); the bind is
loopback-only either way.

Capabilities served here are the package's ``IMPLEMENTED``; anything else keeps answering 501 from the catch-all.
"""

import getpass
import hmac
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from empirica.core.orchestration.schemas import RESULT_TO_STATUS, TERMINAL_STATUSES, Proposal
from empirica.local_server import wake
from empirica.local_server.store import CompletionRefused, IdempotencyConflict, ProposalStore, default_db_path

ORG = "local"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.strip().lower()).strip("-") or "user"


def local_user() -> str:
    return _slug(os.environ.get("EMPIRICA_LOCAL_USER") or getpass.getuser())


def mesh_prefix() -> str:
    return f"{ORG}.{local_user()}"


def configured_token() -> str | None:
    token = os.environ.get("EMPIRICA_LOCAL_TOKEN")
    if token:
        return token
    creds = Path.home() / ".empirica" / "credentials.yaml"
    try:
        import yaml

        data = yaml.safe_load(creds.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return None
    value = data.get("local_server_token") if isinstance(data, dict) else None
    return str(value) if value else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── project registry ──────────────────────────────────────────────────────────────────────────────────────────


class ProjectRegistry:
    """Practices registered with this server, beside the proposals in the same SQLite file."""

    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_projects (id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, "
                "display_name TEXT, repo_url TEXT, created_at TEXT NOT NULL)"
            )

    def register(self, name: str, project_id: str | None, display_name: str | None, repo_url: str | None) -> dict:
        slug = _slug(name)
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT id FROM local_projects WHERE slug = ?", (slug,)).fetchone()
            if row:
                return {"project_id": row[0], "id": row[0], "slug": slug, "already_registered": True}
            pid = project_id or str(uuid.uuid4())
            db.execute(
                "INSERT INTO local_projects (id, slug, display_name, repo_url, created_at) VALUES (?,?,?,?,?)",
                (pid, slug, display_name or name, repo_url, _now()),
            )
        return {"project_id": pid, "id": pid, "slug": slug}

    def unregister(self, project_id: str | None, slug: str | None) -> bool:
        with sqlite3.connect(self.path) as db:
            cur = db.execute(
                "DELETE FROM local_projects WHERE id = ? OR slug = ?", (project_id or "", _slug(slug or ""))
            )
            return cur.rowcount > 0

    def _registered(self) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id, slug, display_name FROM local_projects ORDER BY slug").fetchall()
        return [{"id": r[0], "slug": r[1], "display_name": r[2]} for r in rows]

    @staticmethod
    def _workspace() -> list[dict]:
        """Active practices in core's workspace registry, named by directory; unreadable means none, not an error."""
        ws = Path.home() / ".empirica" / "workspace" / "workspace.db"
        if not ws.is_file():
            return []
        try:
            with sqlite3.connect(f"file:{ws}?mode=ro", uri=True) as db:
                rows = db.execute(
                    "SELECT id, name, trajectory_path FROM global_projects WHERE COALESCE(status, 'active') = 'active'"
                ).fetchall()
        except sqlite3.Error:
            return []
        return [{"id": r[0], "slug": _slug(Path(r[2]).parent.name), "display_name": r[1]} for r in rows if r[2]]

    def projects(self) -> list[dict]:
        """Registered practices first, then workspace ones not already present; one row per slug."""
        out: dict[str, dict] = {}
        for p in [*self._registered(), *self._workspace()]:
            out.setdefault(p["slug"], p)
        prefix = mesh_prefix()
        return [
            {
                **p,
                "project_id": p["id"],
                "name": p["display_name"],
                "ai_id_short": p["slug"],
                "ai_id_tenant": f"{local_user()}.{p['slug']}",
                "ai_id_mesh": f"{prefix}.{p['slug']}",
                "ai_id_canonical": f"{prefix}.{p['slug']}",
            }
            for p in sorted(out.values(), key=lambda p: p["slug"])
        ]

    def by_slug(self, slug: str) -> dict | None:
        return next((p for p in self.projects() if p["slug"] == _slug(slug)), None)


# ── routes ────────────────────────────────────────────────────────────────────────────────────────────────────


def _filter(proposals: list[Proposal], statuses: str | None, since: str | None) -> list[Proposal]:
    wanted = {s.strip() for s in (statuses or "").split(",") if s.strip()}
    return [
        p
        for p in proposals
        if (not wanted or p.status in wanted) and (not since or (p.updated_at or p.created_at) >= since)
    ]


def _page(proposals: list[Proposal], limit: int | None) -> dict[str, Any]:
    n = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    page = proposals[:n]
    return {
        "proposals": [p.to_wire() for p in page],
        "returned": len(page),
        "matched": len(proposals),
        "has_more": len(proposals) > len(page),
    }


class _Ctx:
    """Per-app state: the store and registry for the current db path (tests move HOME between apps)."""

    def __init__(self):
        self.path: Path | None = None
        self._store: ProposalStore | None = None
        self._registry: ProjectRegistry | None = None
        self.beats: dict[str, dict] = {}

    def _open(self) -> tuple[ProposalStore, ProjectRegistry]:
        path = default_db_path()
        if self.path != path or self._store is None or self._registry is None:
            self.path, self._store, self._registry = path, ProposalStore(path), ProjectRegistry(path)
        return self._store, self._registry

    def store(self) -> ProposalStore:
        return self._open()[0]

    def registry(self) -> ProjectRegistry:
        return self._open()[1]


def refuse(error: str, status: int, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error, **extra}, status_code=status)


async def body_of(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


#: The names a loopback bind answers to; serve's own loopback test (entity_mint_auth) also accepts the IPv4-mapped form.
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1", "[::1]", "::ffff:127.0.0.1", "[::ffff:127.0.0.1]")


def _hostname(value: str) -> str:
    """The host part of a Host header or an Origin URL, without port, lowercased."""
    from urllib.parse import urlsplit

    value = value.strip().lower()
    if "://" in value:
        return (urlsplit(value).hostname or "").lower()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def require_local_caller(request: Request) -> None:
    """Refuse what loopback alone does not stop: the browser on this machine (1.15.0 sweep).

    - Host must name the loopback interface: a page whose own name was rebound to 127.0.0.1 is same-origin to itself, and
      only its Host header gives it away (403).
    - An Origin, when a browser sends one, must be a loopback origin (403).
    - A POST must be application/json: a text/plain POST is a "simple" request a page on any site can send with no
      preflight (415). Every core client already sends JSON.
    - Then the token, when one is configured (401).
    """
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK_NAMES:
        raise HTTPException(status_code=403, detail="not a loopback host")
    origin = request.headers.get("origin")
    if origin and _hostname(origin) not in _LOOPBACK_NAMES:
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if request.method == "POST" and not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="application/json required")
    require_token(request)


def require_token(request: Request) -> None:
    """401 unless no token is configured or the bearer matches it (compared in constant time)."""
    token = configured_token()
    if not token:
        return
    given = request.headers.get("authorization", "")
    if not (given.startswith("Bearer ") and hmac.compare_digest(given[7:].encode(), token.encode())):
        raise HTTPException(status_code=401, detail="unauthorized")


def register(r) -> None:
    """Add the group-A routes to router ``r``; call before the catch-all so these win."""
    ctx = _Ctx()
    auth = [Depends(require_local_caller)]
    _identity_routes(r, ctx, auth)
    _project_routes(r, ctx, auth)
    _mailbox_read_routes(r, ctx, auth)
    _mailbox_write_routes(r, ctx, auth)
    _mailbox_archive_route(r, ctx, auth)
    _presence_routes(r, ctx, auth)


def _identity_routes(r, ctx: _Ctx, auth) -> None:
    @r.get("/v1/orgs/me/mesh_sharing_agreements", dependencies=auth)
    async def agreements():  # pyright: ignore[reportUnusedFunction]
        # The contract's one group-B exception (local_status 200_empty_list): one person has no cross-org agreements,
        # and an empty list is a true answer where 501 would read as "cannot tell".
        return {"agreements": []}

    @r.get("/v1/users/me", dependencies=auth)
    async def users_me():  # pyright: ignore[reportUnusedFunction]
        return {
            "org_id": ORG,
            "org_name": ORG,
            "tenant_slug": local_user(),
            "mesh_id_prefix": mesh_prefix(),
            "user_id": "local",
            "user_name": local_user(),
            "role": "owner",
            "is_service_account": False,
        }

    @r.get("/v1/users/me/roster", dependencies=auth)
    async def roster():  # pyright: ignore[reportUnusedFunction]
        projects = ctx.registry().projects()
        keys = ("id", "slug", "display_name", "ai_id_short", "ai_id_tenant", "ai_id_mesh")
        tenant = {
            "tenant_slug": local_user(),
            "user_name": local_user(),
            "is_admin": True,
            "projects": [{k: p[k] for k in keys} for p in projects],
        }
        return {
            "self": {"user_id": "local", "tenant_slug": local_user(), "ai_ids": [p["ai_id_mesh"] for p in projects]},
            "org": {"id": ORG, "slug": ORG, "name": ORG, "tenants": [tenant]},
        }


def _project_routes(r, ctx: _Ctx, auth) -> None:
    @r.get("/v1/users/me/projects", dependencies=auth)
    async def my_projects():  # pyright: ignore[reportUnusedFunction]
        return {"projects": ctx.registry().projects()}

    @r.post("/v1/users/me/projects", dependencies=auth)
    async def link_project(request: Request):  # pyright: ignore[reportUnusedFunction]
        pid = (await body_of(request)).get("project_id")
        if not any(p["id"] == pid for p in ctx.registry().projects()):
            return refuse("unknown project_id", 404)
        return {"ok": True, "linked": True, "project_id": pid, "already_linked": True}

    @r.post("/v1/projects/register", dependencies=auth)
    async def project_register(request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        name = str(body.get("name") or "").strip()
        if not name:
            return refuse("name is required", 400)
        return ctx.registry().register(name, body.get("project_id"), body.get("display_name"), body.get("repo_url"))

    @r.post("/v1/projects/unregister", dependencies=auth)
    async def project_unregister(request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        if not ctx.registry().unregister(body.get("project_id"), body.get("slug")):
            return refuse("no registered project matches", 404, reason="not_registered")
        return {"ok": True}

    @r.get("/v1/projects/by-slug/{slug}", dependencies=auth)
    async def project_by_slug(slug: str):  # pyright: ignore[reportUnusedFunction]
        found = ctx.registry().by_slug(slug)
        return {"ok": True, "project": found} if found else refuse("not found", 404)


def _mailbox_read_routes(r, ctx: _Ctx, auth) -> None:
    def listing(kind: str, ai_id: str, status: str | None, since: str | None, limit: int | None) -> dict:
        # A bare name is not an address: the contract says it returns nothing, not a guess.
        rows = getattr(ctx.store(), kind)(ai_id) if ai_id.count(".") >= 2 else []
        return _page(_filter(rows, status, since), limit)

    @r.get("/v1/orchestration/inbox", dependencies=auth)
    async def inbox(  # pyright: ignore[reportUnusedFunction]
        ai_id: str = "", status: str | None = None, since: str | None = None, limit: int | None = None
    ):
        return listing("inbox", ai_id, status, since, limit)

    @r.get("/v1/orchestration/outbox", dependencies=auth)
    async def outbox(  # pyright: ignore[reportUnusedFunction]
        ai_id: str = "", status: str | None = None, since: str | None = None, limit: int | None = None
    ):
        return listing("outbox", ai_id, status, since, limit)

    @r.get("/v1/orchestration/{proposal_id}", dependencies=auth)
    async def get_one(proposal_id: str):  # pyright: ignore[reportUnusedFunction]
        found = ctx.store().get(proposal_id)
        return found.to_wire() if found else refuse("not found", 404)


def address_problem(body: dict) -> JSONResponse | None:
    """The refusal for a propose whose addresses this server cannot take, or None."""
    prefix = mesh_prefix() + "."
    source = str(body.get("source_claude") or "")
    targets = [str(t) for t in body.get("target_claudes") or []]
    bare = [a for a in [source, *targets] if a and a.count(".") < 2]
    if bare:
        return refuse("addresses must be the canonical 3-form org.tenant.practice", 400, addresses=bare)
    if source and not source.startswith(prefix):
        return refuse(f"source_claude must be in this server's namespace {prefix}*", 400)
    if any(not t.startswith(prefix) for t in targets):
        return JSONResponse({"capability": "orchestration.cross_tenant"}, status_code=501)
    return None


def _mailbox_write_routes(r, ctx: _Ctx, auth) -> None:
    @r.post("/v1/orchestration/propose", dependencies=auth)
    async def propose(request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        if (problem := address_problem(body)) is not None:
            return problem
        proposal = Proposal.from_request(body, org_id=ORG)
        if invalid := proposal.validate():
            return refuse(invalid, 400)
        key = (body.get("payload") or {}).get("idempotency_key") or body.get("idempotency_key")
        try:
            saved, replayed = ctx.store().propose(proposal, idempotency_key=key)
        except IdempotencyConflict as e:
            return refuse("idempotency_key_conflict", 409, proposal_id=e.original_id)
        if not replayed:
            wake.proposed(saved)
        return {"ok": True, "proposal_id": saved.id, "status": saved.status, "replay_caught": replayed}

    @r.post("/v1/orchestration/{proposal_id}/complete", dependencies=auth)
    async def complete(proposal_id: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        result = body.get("result") or "shipped"
        if result not in RESULT_TO_STATUS:
            return refuse(f"result must be one of {sorted(RESULT_TO_STATUS)}", 400)
        current = ctx.store().get(proposal_id)
        if current is None:
            return refuse("not found", 404)
        acker = body.get("ai_id") or (current.distinct_targets() or [""])[0]
        if acker not in current.distinct_targets():
            return refuse("only a target of this proposal can complete it", 403, targets=current.distinct_targets())
        if current.status in TERMINAL_STATUSES:
            return {"ok": True, "proposal_id": current.id, "status": current.status, "note": "already_completed",
                    "target_acks": current.target_acks, "pending_targets": current.pending_targets()}  # fmt: skip
        try:
            done = ctx.store().complete(
                proposal_id, acker, result=result, note=body.get("note"), commit_sha=body.get("commit_sha"),
                artifact_ids=list(body.get("artifact_ids") or []),
            )  # fmt: skip
        except CompletionRefused as e:
            return refuse(str(e), e.status_code)
        if done is None:
            return refuse("not found", 404)
        if done.status in TERMINAL_STATUSES:  # this call made it terminal: the check above returns for one already so
            wake.finished(done)
        return {
            "ok": True,
            "proposal_id": done.id,
            "status": done.status,
            "target_acks": done.target_acks,
            "pending_targets": done.pending_targets(),
        }


def _mailbox_archive_route(r, ctx: _Ctx, auth) -> None:
    @r.post("/v1/orchestration/{proposal_id}/archive", dependencies=auth)
    async def archive(proposal_id: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return refuse("reason is required", 400)
        current = ctx.store().get(proposal_id)
        if current is None:
            return refuse("not found", 404)
        ai_id = body.get("ai_id") or (current.distinct_targets() or [""])[0]
        if ai_id not in (*current.distinct_targets(), current.source_claude):
            return refuse("only a target or the source can archive its copy", 403)
        _, already = ctx.store().archive(proposal_id, ai_id)
        return {"ok": True, "proposal_id": proposal_id, "reason": reason, "no_op": already, "is_archived": True}


def _presence_routes(r, ctx: _Ctx, auth) -> None:
    @r.post("/v1/listeners/heartbeat", dependencies=auth)
    async def listener_beat(request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        seen = _now()
        ctx.beats[f"listener:{body.get('ai_id')}:{body.get('instance_id')}"] = {**body, "last_seen": seen}
        return {"ok": True, "ai_id": body.get("ai_id"), "instance_id": body.get("instance_id"), "mode": "local",
                "last_seen": seen, "ttl_seconds": 120}  # fmt: skip

    @r.post("/v1/practitioners/heartbeat", dependencies=auth)
    async def practitioner_beat(request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        if not body.get("machine") or not body.get("session_id"):
            return refuse("machine and session_id are required", 400)
        ctx.beats[f"practitioner:{body['machine']}:{body['session_id']}"] = {**body, "last_seen": _now()}
        return {"ok": True, "presence": body.get("status") or "active"}

    @r.post("/v1/system/event", dependencies=auth)
    async def system_event(request: Request):  # pyright: ignore[reportUnusedFunction]
        body = await body_of(request)
        log = ctx.store().path.with_name("local_server_events.jsonl")
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**body, "received_at": _now()}, default=str) + "\n")
        return {"ok": True}
