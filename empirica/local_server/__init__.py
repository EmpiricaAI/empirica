"""empirica-local: the one-person, one-machine server that speaks the Cortex REST contract (spec phase 1, goal 92fe2c88).

It runs inside ``empirica serve`` and is mounted only when that daemon is bound to a loopback host: the spec's boundary
(section 5b, David's licensing ruling) is that the local server is never reachable from another machine, so on any other
bind its routes do not exist. The contract is ``routes.yaml`` beside this file, a copy of the cortex-owned
``contract/routes.yaml`` (a test keeps them equal).

What it answers is exactly what it implements: ``GET /v1/health`` lists the implemented capabilities, and every other
contract route answers ``501 {"capability": "<name>"}`` until its capability is built, never a stub that pretends.
"""

import re
from functools import lru_cache
from pathlib import Path

EDITION = "local"
#: Capabilities this server implements so far. Each is added with its routes and only when the conformance suite passes.
IMPLEMENTED: frozenset[str] = frozenset(
    {
        "identity.session",
        "roster",
        "projects.registry",
        "orchestration.mailbox",
        "orchestration.heartbeat",
        "system.event",
    }
)


@lru_cache(maxsize=1)
def contract() -> dict:
    import yaml

    return yaml.safe_load((Path(__file__).with_name("routes.yaml")).read_text(encoding="utf-8"))


def _route_rows() -> list[dict]:
    data = contract()
    return [r for key in ("group_a", "group_b") for r in (data.get(key) or []) if isinstance(r, dict)]


def capability_for(method: str, path: str) -> tuple[bool, str | None]:
    """(known, capability) for a request: whether the contract names this method and path, and the capability it needs."""
    for row in _route_rows():
        if str(row.get("method", "")).upper() != method.upper():
            continue
        segments = str(row.get("path", "")).split("/")
        pattern = (
            "^"
            + "/".join("[^/]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg) for seg in segments)
            + "$"
        )
        if re.match(pattern, path):
            return True, row.get("capability")
    return False, None


def health_document() -> dict:
    return {
        "status": "healthy",
        "edition": EDITION,
        "contract": str(contract().get("contract", "")),
        "capabilities": sorted(IMPLEMENTED),
        "wake": _wake_health(),
        "services": _services(),
    }


def _services() -> dict:
    """The local semantic layer this server holds, for clients choosing where to send vectors (goal db2cf3fa)."""
    from empirica.local_server import embedder, vectors

    return {
        "vectors": "/qdrant" if vectors.available() else None,
        "embeddings": "/ollama" if embedder.available() else None,
    }


def _wake_health() -> dict:
    """Failed wake-line writes since start: a stored proposal nobody was woken for must be visible somewhere."""
    from empirica.local_server.wake import FAILURES

    return {"via": "loop_fires.log", "failures": FAILURES["count"], "last_error": FAILURES["last_error"]}


def router():
    """The FastAPI router serve mounts on a loopback bind."""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    from empirica.local_server import api

    r = APIRouter()
    api.register(r)
    from empirica.local_server import vectors

    if vectors.available():  # the semantic layer's store (goal db2cf3fa); absent without qdrant-client
        from fastapi import Depends

        vectors.register(r, [Depends(api.require_local_caller)])
    from empirica.local_server import embedder

    if embedder.available():  # the semantic layer's model (goal db2cf3fa); absent without fastembed
        from fastapi import Depends

        embedder.register(r, [Depends(api.require_local_caller)])

    @r.get("/v1/health")
    @r.get("/health")
    async def health():  # pyright: ignore[reportUnusedFunction]
        return health_document()

    @r.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def contract_route(rest: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        known, capability = capability_for(request.method, f"/v1/{rest}")
        if not known:
            return JSONResponse({"error": "not a contract route"}, status_code=404)
        if capability and capability not in IMPLEMENTED:
            return JSONResponse({"capability": capability}, status_code=501)
        return JSONResponse({"error": "not implemented"}, status_code=501)

    return r
