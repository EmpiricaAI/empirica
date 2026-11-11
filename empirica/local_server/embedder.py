"""An embedding model owned by ``empirica serve``, behind Ollama's embedding routes (goal db2cf3fa, T4.2).

On a machine with no Ollama, embeddings fell back to hashed vectors, which are not semantic. An in-process model would load
in every hook and CLI process, so the one long-lived process holds it: ``intfloat/multilingual-e5-small`` (384 dimensions,
multilingual; David, 2026-11-11) through fastembed, answering ``/ollama/api/tags``, ``/api/embeddings`` and ``/api/embed``
so empirica's existing ``ollama`` provider uses it with only a base URL.

fastembed 0.9 does not list this model, so it is registered as a custom ONNX model (mean pooling, normalized). Measured
here with the int8 file: about 135 MB on disk, under 3 ms per embed warm. e5 expects a ``query: `` or ``passage: `` prefix; the Ollama API carries
no query/passage flag, so every text gets ``query: ``, the form the model's authors give for symmetric use.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Request  # module level: FastAPI resolves the route annotations from these globals

MODEL = "intfloat/multilingual-e5-small"
NAME = "multilingual-e5-small"  # what /api/tags advertises; MODEL_DIMENSIONS knows it as 384
DIM = 384
PREFIX = "query: "
#: The int8 ONNX in the model repository (David, 2026-11-11): 135 MB against 487 MB fp32, 2.6 ms against 7.2 ms per embed,
#: the same top result on a fixed EN/DE/ES/NL check (lower ranks reorder). Same model name and 384 dimensions.
MODEL_FILE = "onnx/model_qint8_avx512_vnni.onnx"


def available() -> bool:
    """Whether this interpreter can hold the model (fastembed installed)."""
    return importlib.util.find_spec("fastembed") is not None


def models_dir() -> Path:
    return Path(os.environ.get("EMPIRICA_LOCAL_MODELS") or Path.home() / ".empirica" / "models").expanduser()


def _load_fastembed():
    from fastembed import TextEmbedding  # pyright: ignore[reportMissingImports]
    from fastembed.common.model_description import ModelSource, PoolingType  # pyright: ignore[reportMissingImports]

    try:
        TextEmbedding.add_custom_model(
            model=MODEL,
            pooling=PoolingType.MEAN,
            normalization=True,
            sources=ModelSource(hf=MODEL),
            dim=DIM,
            model_file=MODEL_FILE,
        )
    except ValueError:
        pass  # already registered in this process
    model = TextEmbedding(MODEL, cache_dir=str(models_dir()))

    def embed(texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in model.embed([PREFIX + t for t in texts])]

    return embed


class Embedder:
    """The model, loaded on first use (the first call downloads it), under a lock. ``load`` is replaceable for tests."""

    def __init__(self, load=None):
        self._load = load or _load_fastembed
        self._embed: Any = None
        self._lock = threading.Lock()

    def __call__(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            if self._embed is None:
                self._embed = self._load()
            return self._embed(texts)


_default: Embedder | None = None


def default_embedder() -> Embedder:
    global _default
    if _default is None:
        _default = Embedder()
    return _default


def warm_in_background(embedder: Embedder | None = None) -> threading.Thread:
    """Load (on a fresh machine, download) the model off the request path when serve starts, so the first search does not
    wait on it inside a hook with a 30 s timeout (goal db2cf3fa, T4.5). A failure here is logged; the routes still answer
    503 for a model that will not load."""
    model = embedder or default_embedder()

    def warm():
        try:
            model(["warm"])
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("embedding model did not load at serve start: %s", e)

    thread = threading.Thread(target=warm, name="empirica-embedder-warm", daemon=True)
    thread.start()
    return thread


def register(r, guard, embedder: Embedder | None = None) -> None:
    """Add Ollama's embedding routes under ``/ollama`` to router ``r`` behind ``guard`` (loopback-only, like the store)."""
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    model = embedder or default_embedder()

    async def run(texts: list[str]):
        try:
            return await run_in_threadpool(model, texts)
        except (
            Exception
        ) as e:  # the download failed (offline), or the model would not load: say so, never fake a vector
            return JSONResponse({"error": f"embedding model unavailable: {type(e).__name__}: {e}"}, status_code=503)

    @r.get("/ollama/api/tags", dependencies=guard)
    async def tags():  # pyright: ignore[reportUnusedFunction]
        return {"models": [{"name": f"{NAME}:latest", "model": f"{NAME}:latest", "details": {"family": "e5"}}]}

    @r.post("/ollama/api/embeddings", dependencies=guard)
    async def embeddings(request: Request):  # pyright: ignore[reportUnusedFunction]
        data = await request.json()
        out = await run([str((data or {}).get("prompt") or "")])
        return out if isinstance(out, JSONResponse) else {"embedding": out[0]}

    @r.post("/ollama/api/embed", dependencies=guard)
    async def embed(request: Request):  # pyright: ignore[reportUnusedFunction]
        data = await request.json() or {}
        raw = data.get("input")
        texts = [str(t) for t in raw] if isinstance(raw, list) else [str(raw or "")]
        out = await run(texts)
        return out if isinstance(out, JSONResponse) else {"model": f"{NAME}:latest", "embeddings": out}
