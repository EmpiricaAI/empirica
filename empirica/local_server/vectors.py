"""The part of Qdrant's REST API that empirica uses, over one embedded store owned by ``empirica serve`` (goal db2cf3fa).

On a machine with no Cortex and no Qdrant server, semantic search was empty: nothing set up a vector store. qdrant-client's
embedded mode (``QdrantClient(path=...)``) needs no server, but it takes an exclusive lock on its folder, so only one
process may open it, and empirica runs many (hooks, CLI verbs, the postflight worker, several sessions). serve is that one
long-lived owner: it holds the store and answers the same REST calls a Qdrant server would, under ``/qdrant`` on its
loopback bind, so every client keeps working with only a base URL (``QdrantClient(url=".../qdrant")``, ``RestQdrant``,
the raw REST probes). docs/architecture/LOCAL_SEMANTIC_LAYER.md has the design and the inventory behind the route list.

Only what empirica calls is served: collections (list, get, exists, create, delete) and points (upsert, retrieve, delete,
scroll, count, set_payload, query, search). Anything else is a 404, as on a server without that route.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Request  # module level: FastAPI resolves the route annotations from these globals

#: Qdrant answers errors as ``{"status": {"error": ...}}``; the REST client raises on any non-2xx.
_NOT_FOUND = ("not found", "doesn't exist", "does not exist")


def store_path() -> Path:
    """Where the embedded store lives: ``EMPIRICA_LOCAL_VECTORS``, else ``~/.empirica/vectors``."""
    return Path(os.environ.get("EMPIRICA_LOCAL_VECTORS") or Path.home() / ".empirica" / "vectors").expanduser()


def available() -> bool:
    """Whether this interpreter can hold the embedded store (qdrant-client installed)."""
    return importlib.util.find_spec("qdrant_client") is not None


class _Store:
    """One embedded client for the process, opened on first use, every call under one lock (local mode is not
    thread-safe, and serve answers from a thread pool)."""

    def __init__(self, path: Path):
        self.path = path
        self._client: Any = None
        self._lock = threading.Lock()

    def run(self, fn):
        with self._lock:
            if self._client is None:
                from qdrant_client import QdrantClient

                self.path.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(self.path))
                import atexit

                atexit.register(self.close)  # flush and unlock before interpreter teardown, not from a late __del__
            return fn(self._client)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("qdrant-client")
    except PackageNotFoundError:
        return "0.0.0"


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    return value


def _flag(value: Any, default: Any) -> Any:
    """A REST ``with_payload``/``with_vector`` (bool, list of keys, or a selector), or the server's default when absent."""
    return default if value is None else value


class _Answer:
    """Runs a store call off the event loop and shapes the reply as Qdrant does."""

    def __init__(self, store: _Store):
        self.store = store

    async def __call__(self, fn) -> Any:
        from fastapi.responses import JSONResponse
        from starlette.concurrency import run_in_threadpool

        try:
            return {"result": _dump(await run_in_threadpool(self.store.run, fn)), "status": "ok", "time": 0.0}
        except ValueError as e:  # local mode's "Collection x not found" and invalid requests
            text = str(e)
            code = 404 if any(s in text.lower() for s in _NOT_FOUND) else 400
            return JSONResponse({"status": {"error": text}, "result": None, "time": 0.0}, status_code=code)
        except Exception as e:
            return JSONResponse(
                {"status": {"error": f"{type(e).__name__}: {e}"}, "result": None, "time": 0.0}, status_code=500
            )


async def _body(request: Request) -> dict:
    data = await request.json()
    return data if isinstance(data, dict) else {}


def register(r, guard) -> None:
    """Add the ``/qdrant`` routes to router ``r`` behind ``guard`` (serve's loopback Host/Origin check). No bearer: the
    Qdrant clients send their own ``api-key`` header or none, and the bind is loopback-only."""
    answer = _Answer(_Store(store_path()))
    _collection_routes(r, guard, answer)
    _point_write_routes(r, guard, answer)
    _point_read_routes(r, guard, answer)


def _collection_routes(r, deps, answer: _Answer) -> None:
    from qdrant_client.http import models as m

    @r.get("/qdrant", dependencies=deps)
    @r.get("/qdrant/", dependencies=deps)
    async def root():  # pyright: ignore[reportUnusedFunction]
        # The client's compatibility check reads `version`; this store is the client library itself, so it matches.
        return {"title": "qdrant - vector search engine (empirica local)", "version": _version()}

    @r.get("/qdrant/collections", dependencies=deps)
    async def collections():  # pyright: ignore[reportUnusedFunction]
        return await answer(lambda c: c.get_collections())

    @r.get("/qdrant/collections/{name}", dependencies=deps)
    async def collection(name: str):  # pyright: ignore[reportUnusedFunction]
        return await answer(lambda c: c.get_collection(name))

    @r.get("/qdrant/collections/{name}/exists", dependencies=deps)
    async def exists(name: str):  # pyright: ignore[reportUnusedFunction]
        return await answer(lambda c: {"exists": c.collection_exists(name)})

    @r.put("/qdrant/collections/{name}", dependencies=deps)
    async def create(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        spec = m.CreateCollection.model_validate(await _body(request))
        return await answer(lambda c: c.create_collection(name, vectors_config=spec.vectors))

    @r.delete("/qdrant/collections/{name}", dependencies=deps)
    async def drop(name: str):  # pyright: ignore[reportUnusedFunction]
        return await answer(lambda c: c.delete_collection(name))


def _point_write_routes(r, deps, answer: _Answer) -> None:
    from qdrant_client.http import models as m

    @r.put("/qdrant/collections/{name}/points", dependencies=deps)
    async def upsert(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        data = await _body(request)
        points = (
            m.PointsList.model_validate(data).points if "points" in data else m.PointsBatch.model_validate(data).batch
        )
        return await answer(lambda c: c.upsert(name, points=points))

    @r.post("/qdrant/collections/{name}/points/delete", dependencies=deps)
    async def delete(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        data = await _body(request)
        selector = m.PointIdsList.model_validate(data) if "points" in data else m.FilterSelector.model_validate(data)
        return await answer(lambda c: c.delete(name, points_selector=selector))

    @r.post("/qdrant/collections/{name}/points/payload", dependencies=deps)
    async def set_payload(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        req = m.SetPayload.model_validate(await _body(request))
        target = req.points if req.points is not None else req.filter
        return await answer(lambda c: c.set_payload(name, payload=req.payload, points=target, key=req.key))


def _point_read_routes(r, deps, answer: _Answer) -> None:
    from qdrant_client.http import models as m

    @r.post("/qdrant/collections/{name}/points", dependencies=deps)
    async def retrieve(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        req = m.PointRequest.model_validate(await _body(request))
        return await answer(
            lambda c: c.retrieve(
                name,
                ids=req.ids,
                with_payload=_flag(req.with_payload, True),
                with_vectors=_flag(req.with_vector, False),
            )
        )

    @r.post("/qdrant/collections/{name}/points/scroll", dependencies=deps)
    async def scroll(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        req = m.ScrollRequest.model_validate(await _body(request))

        def run(c):
            points, nxt = c.scroll(
                name,
                scroll_filter=req.filter,
                limit=req.limit or 10,
                offset=req.offset,
                with_payload=_flag(req.with_payload, True),
                with_vectors=_flag(req.with_vector, False),
            )
            return {"points": _dump(points), "next_page_offset": nxt}

        return await answer(run)

    @r.post("/qdrant/collections/{name}/points/count", dependencies=deps)
    async def count(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        req = m.CountRequest.model_validate(await _body(request))
        return await answer(lambda c: c.count(name, count_filter=req.filter, exact=req.exact is not False))

    @r.post("/qdrant/collections/{name}/points/query", dependencies=deps)
    async def query(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        req = m.QueryRequest.model_validate(await _body(request))
        return await answer(
            lambda c: c.query_points(
                name,
                query=req.query,
                query_filter=req.filter,
                limit=req.limit or 10,
                offset=req.offset,
                score_threshold=req.score_threshold,
                with_payload=_flag(req.with_payload, False),
                with_vectors=_flag(req.with_vector, False),
            )
        )

    @r.post("/qdrant/collections/{name}/points/search", dependencies=deps)
    async def search(name: str, request: Request):  # pyright: ignore[reportUnusedFunction]
        req = m.SearchRequest.model_validate(await _body(request))
        return await answer(
            lambda c: (
                c.query_points(
                    name,
                    query=req.vector,
                    query_filter=req.filter,
                    limit=req.limit or 10,
                    score_threshold=req.score_threshold,
                    with_payload=_flag(req.with_payload, False),
                    with_vectors=_flag(req.with_vector, False),
                ).points
            )
        )
