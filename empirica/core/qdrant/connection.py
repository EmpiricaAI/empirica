"""
Qdrant connection infrastructure and embedding utilities.

This module is OPTIONAL. Empirica core works without Qdrant.
Set EMPIRICA_ENABLE_EMBEDDINGS=true to enable semantic search features.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Process-level embedding memo.
#
# retrieve_task_patterns issues ~20 search calls per single preflight-submit
# (lessons/dead_ends/mistakes/findings + the enrich_* fan-out over
# eidetic/episodic/goals/assumptions/decisions/docs). Many re-embed the SAME
# query string — the enrich_* calls all reuse the raw task_context. Without
# caching, a cold/idle-evicted embedding backend turns that into ~20 ×
# cold-start latency: the measured ~120s preflight hang
# (mesh-support prop_4pqqclkpcjhnjnszspasu7qvqm).
#
# Memoizing at this single chokepoint (every search path funnels through
# _get_embedding_safe) collapses those ~20 embeds to at most one per DISTINCT
# query string — preserving the per-type prefix biasing ("How to: X",
# "Approach for: X", …) that threading a single shared vector would have
# flattened. Bounded LRU; only SUCCESSFUL (non-None) embeds are cached so a
# transient backend failure can't poison later calls.
_EMBED_MEMO_MAX = int(os.getenv("EMPIRICA_EMBED_MEMO_SIZE", "256"))
_embed_memo: OrderedDict[str, list[float]] = OrderedDict()
_embed_memo_lock = threading.Lock()

# Public API — these underscore-prefixed functions are intentionally imported
# by other qdrant modules (vector_store, calibration, decay, memory, etc.)
__all__ = [
    "_check_qdrant_available",
    "_extract_vector_size",
    "_get_embedding_for_collection",
    "_get_embedding_safe",
    "_get_embeddings_batch",
    "_get_embeddings_batch_for_collection",
    "_get_provider_context",
    "_get_qdrant_client",
    "_get_qdrant_imports",
    "_get_vector_size",
    "_rest_search",
    "get_url_resolver",
    "set_url_resolver",
]


# ── Per-project URL resolver hook (prop_ure7rqfuon, 2026-07-24) ────
#
# Cortex owns per-org Qdrant routing (CORTEX_QDRANT_URLS_BY_ORG env +
# tenant DB → project → org lookup). Empirica-lib doesn't know about
# any of that, and until now had no way to resolve a URL from a
# project_id — it read EMPIRICA_QDRANT_URL env or fell through to
# localhost:6333. Every empirica write (memory/decisions/docs/edges)
# from any process using empirica-lib went to that base URL, silently
# bypassing whatever per-org routing cortex was doing at its own layer.
#
# Root of both the org-nle drift + the org-empirica :7333 orphan
# (mesh-support prop_vdy4h25rlj + prop_2ag3ozdz5b consolidation).
#
# Fix (Path 1 per prop_ure7rqfuon): a module-level resolver hook.
# Hosts install a callable `(project_id: str) -> str | None` that
# maps project → URL; `_get_qdrant_client(project_id=…)` calls it
# when no explicit `qdrant_url` was passed. Cortex on startup
# installs `cortex.qdrant_routing.resolve_qdrant_url`. Standalone
# empirica CLI installs a default that reads tenants.db +
# CORTEX_QDRANT_URLS_BY_ORG env directly.
#
# Backward compat: no resolver installed → old behavior verbatim
# (env → localhost). Set to None to explicitly clear.
_url_resolver: Callable[[str], str | None] | None = None


def set_url_resolver(fn: Callable[[str], str | None] | None) -> None:
    """Install a per-project URL resolver.

    ``fn`` is a callable ``(project_id: str) -> str | None`` — return
    None to fall through to the env default. Called by
    ``_get_qdrant_client`` when the caller passes ``project_id=`` but
    no explicit ``qdrant_url=``. Idempotent (setting twice just replaces).
    Pass ``None`` to clear the resolver (test hygiene).

    Not thread-safe by design — install once at process startup, never
    swap under a running load. Cross-repo boundary: cortex on import
    installs its ``resolve_qdrant_url``; standalone empirica CLI
    installs a tenant-DB-reading default (see
    ``empirica.cli.qdrant_url_resolver_default``).
    """
    global _url_resolver
    _url_resolver = fn


def get_url_resolver():
    """Return the currently installed resolver (None if unset)."""
    return _url_resolver


# Lazy imports - Qdrant is optional
_qdrant_available = None
_qdrant_warned = False


class CollectionDimensionMismatchError(RuntimeError):
    """Raised when an existing Qdrant collection and embeddings provider disagree."""


def _check_qdrant_available(
    qdrant_url: str | None = None,
    *,
    project_id: str | None = None,
) -> bool:
    """Check if Qdrant is available and enabled.

    Args:
        qdrant_url: Optional. Accepted for API parity with `_get_qdrant_client`.
        project_id: Optional. Accepted for API parity too (prop_ure7rqfuon
            2026-07-24) — callers now thread project_id through the whole
            write pipeline so the URL resolver hook can fire.

    Both args currently unused — this function only checks library
    installation + the global enable flag, both URL-agnostic. Reserved
    for future per-URL reachability probe.

    Returns:
        True when qdrant-client is installed and embeddings aren't disabled.
    """
    del qdrant_url, project_id  # reserved for future per-URL reachability probe
    global _qdrant_available, _qdrant_warned

    if _qdrant_available is not None:
        return _qdrant_available

    # Check if embeddings are enabled (default: True if qdrant available)
    enable_flag = os.getenv("EMPIRICA_ENABLE_EMBEDDINGS", "").lower()
    if enable_flag == "false":
        _qdrant_available = False
        return False

    try:
        from qdrant_client import QdrantClient  # noqa: F401 — availability check  # pyright: ignore[reportUnusedImport]

        _qdrant_available = True
        return True
    except ImportError:
        if not _qdrant_warned:
            logger.debug(
                "qdrant-client not installed. Semantic search disabled. Install with: pip install qdrant-client"
            )
            _qdrant_warned = True
        _qdrant_available = False
        return False


def _get_qdrant_imports():
    """Lazy import Qdrant dependencies."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    return QdrantClient, Distance, VectorParams, PointStruct


def _get_embedding_safe(text: str) -> list[float] | None:
    """Get embedding with graceful fallback + process-level memoization.

    Memoizes successful embeds by query string so the ~20 search calls a
    single preflight fans out never re-embed the same string N times (see the
    _embed_memo note above). Failures are not cached — a transient backend
    hiccup must not poison later calls.
    """
    if not text:
        return None

    with _embed_memo_lock:
        hit = _embed_memo.get(text)
        if hit is not None:
            _embed_memo.move_to_end(text)
            return hit

    try:
        from .embeddings import get_embedding

        vec = get_embedding(text)
    except Exception as e:
        logger.debug(f"Embedding failed: {e}")
        return None

    if vec is not None:
        with _embed_memo_lock:
            _embed_memo[text] = vec
            _embed_memo.move_to_end(text)
            while len(_embed_memo) > _EMBED_MEMO_MAX:
                _embed_memo.popitem(last=False)
    return vec


def _get_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """Batch embed multiple texts. Returns list of vectors (None for failures)."""
    try:
        from .embeddings import get_embedding_provider

        provider = get_embedding_provider()
        return provider.batch_embed(texts)
    except Exception as e:
        logger.debug(f"Batch embedding failed, falling back to sequential: {e}")
        return [_get_embedding_safe(t) for t in texts]


def _get_vector_size() -> int:
    """Get vector size from embeddings provider. Defaults to 1024 on error (matches qwen3-embedding)."""
    try:
        from .embeddings import get_vector_size

        return get_vector_size()
    except Exception as e:
        logger.debug(f"Could not get vector size: {e}, defaulting to 1024")
        return 1024


def _get_provider_context() -> str:
    """Return a short provider/model label for mismatch errors."""
    try:
        from .embeddings import get_provider_info

        info = get_provider_info()
        provider = info.get("provider", "unknown")
        model = info.get("model", "unknown")
        return f"{provider}/{model}"
    except Exception:
        return "current embeddings configuration"


def _extract_vector_size(vectors_config) -> int | None:
    """Extract vector size from Qdrant's collection config structure."""
    size = getattr(vectors_config, "size", None)
    if isinstance(size, int):
        return size
    if isinstance(vectors_config, dict):
        for params in vectors_config.values():
            nested_size = getattr(params, "size", None)
            if isinstance(nested_size, int):
                return nested_size
    return None


def _get_collection_vector_size(client, collection_name: str) -> int | None:
    """Read the configured vector size for an existing Qdrant collection."""
    try:
        coll_info = client.get_collection(collection_name)
        vectors_config = coll_info.config.params.vectors
        return _extract_vector_size(vectors_config)
    except Exception as e:
        logger.debug(f"Could not read collection dimensions for {collection_name}: {e}")
        return None


def _create_collection_with_size(client, collection_name: str, vector_size: int) -> None:
    """Create a single-vector cosine collection with the resolved dimension."""
    _, Distance, VectorParams, _ = _get_qdrant_imports()
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )


def _ensure_collection_matches_vector(
    client,
    collection_name: str,
    vector_size: int,
    *,
    create_if_missing: bool = False,
) -> bool:
    """Ensure a collection exists with the same dimension as the resolved embeddings."""
    if client.collection_exists(collection_name):
        existing_size = _get_collection_vector_size(client, collection_name)
        if existing_size is not None and existing_size != vector_size:
            provider_context = _get_provider_context()
            raise CollectionDimensionMismatchError(
                f"Qdrant collection '{collection_name}' is configured for {existing_size}d vectors, "
                f"but {provider_context} resolved to {vector_size}d. "
                "Update EMPIRICA_EMBEDDINGS_MODEL (or related provider config) and rebuild Qdrant with "
                "`empirica rebuild --qdrant` before writing semantic data."
            )
        return False

    if create_if_missing:
        _create_collection_with_size(client, collection_name, vector_size)
        logger.info(f"Created Qdrant collection {collection_name} with {vector_size} dimensions")
        return True

    return False


def _get_embedding_for_collection(
    client,
    collection_name: str,
    text: str,
    *,
    create_if_missing: bool = False,
) -> list[float] | None:
    """Embed text and verify the target collection matches the embedding dimension."""
    embedding = _get_embedding_safe(text)
    if embedding is None:
        return None
    _ensure_collection_matches_vector(
        client,
        collection_name,
        len(embedding),
        create_if_missing=create_if_missing,
    )
    return embedding


def _get_embeddings_batch_for_collection(
    client,
    collection_name: str,
    texts: list[str],
    *,
    create_if_missing: bool = False,
) -> list[list[float] | None]:
    """Batch embed texts and verify the target collection matches the batch dimension."""
    vectors = _get_embeddings_batch(texts)
    first_vector = next((vector for vector in vectors if vector is not None), None)
    if first_vector is None:
        return vectors

    vector_size = len(first_vector)
    _ensure_collection_matches_vector(
        client,
        collection_name,
        vector_size,
        create_if_missing=create_if_missing,
    )

    for vector in vectors:
        if vector is not None and len(vector) != vector_size:
            provider_context = _get_provider_context()
            raise CollectionDimensionMismatchError(
                f"Embeddings batch for '{collection_name}' returned mixed dimensions "
                f"under {provider_context}. Rebuild Qdrant after fixing the configured model."
            )
    return vectors


_local_service_cache: dict[str, str | None] = {}


def local_server_service(service: str) -> str | None:
    """The local server's URL for ``"vectors"`` (``.../qdrant``) or ``"embeddings"`` (``.../ollama``), or None.

    On a machine with no Cortex, ``empirica serve`` holds the vector store and the embedding model (goal db2cf3fa,
    docs/architecture/LOCAL_SEMANTIC_LAYER.md). Every resolver asks this LAST, after anything configured (an explicit URL,
    the per-org resolver a Cortex seat installs, ``EMPIRICA_QDRANT_URL``, a server on 6333, Ollama), so moving to Cortex
    needs no change here. One health probe per process: a refused loopback connection costs about a millisecond.
    """
    if service in _local_service_cache:
        return _local_service_cache[service]
    value = None
    try:
        from empirica.core.mesh_endpoint import _probe_health, local_server_url

        base = local_server_url()
        doc = _probe_health(f"{base}/v1/health") or {}
        path = (doc.get("services") or {}).get(service) if doc.get("edition") == "local" else None
        value = f"{base}{path}" if path else None
    except Exception:
        value = None
    _local_service_cache[service] = value
    return value


def _default_store_url() -> str:
    """A Qdrant server on 6333 when one answers, else the local server's store when it holds one, else 6333 (to report)."""
    default_url = "http://localhost:6333"
    try:
        import urllib.request

        with urllib.request.urlopen(urllib.request.Request(f"{default_url}/collections", method="GET"), timeout=1):
            return default_url
    except Exception:
        return local_server_service("vectors") or default_url


def semantic_layer_status(project_id: str | None = None) -> dict:
    """Whether semantic search can work here, and why not: ``{semantic, qdrant, qdrant_url, embeddings, reason}``.

    ``_check_qdrant_available`` only says the library is installed and not disabled; it never asks a server. On a fresh
    machine with no Qdrant running, a logged artifact warned "Qdrant is available; embedding failed" and project-search
    answered ok with nothing, which reads as "nothing relevant" (fresh-machine probe, 2026-11-11, goal db2cf3fa). This
    asks the server and names the embedding provider: ``local`` is hashed vectors, which are not semantic. It makes a
    network call, so it is for the surfaces that report (search, the embed warning, doctor), never the per-call hot path.
    """
    out: dict = {"semantic": False, "qdrant": "off", "qdrant_url": None, "embeddings": None, "reason": ""}
    if not _check_qdrant_available():
        out["reason"] = "semantic search is off: qdrant-client is not installed, or embeddings are disabled"
        return out
    url = _service_url(project_id=project_id) or _default_store_url()
    out["qdrant_url"] = url
    try:
        import urllib.request

        with urllib.request.urlopen(urllib.request.Request(f"{url}/collections", method="GET"), timeout=1.5) as r:
            out["qdrant"] = "reachable" if r.status == 200 else f"answered {r.status}"
    except Exception as e:
        out["qdrant"] = f"unreachable ({type(e).__name__})"
    try:
        from .embeddings import get_provider_info

        info = get_provider_info()
        out["embeddings"] = f"{info.get('provider')}/{info.get('model')}"
        hashed = info.get("provider") == "local"
    except Exception as e:
        out["embeddings"] = f"unavailable ({type(e).__name__})"
        hashed = True
    if out["qdrant"] != "reachable":
        out["reason"] = f"semantic search is off: no Qdrant answers at {url}"
    elif hashed:
        out["reason"] = (
            "semantic search is degraded: embeddings are hashed vectors (provider 'local'), not a semantic model; "
            "configure Ollama with an embedding model"
        )
    else:
        out["semantic"] = True
    return out


def qdrant_search_client():
    """A read-only similarity client over REST (``empirica.core.qdrant.rest``), or None when no server answers.

    Same server resolution as :func:`_get_qdrant_client` without a project id (``EMPIRICA_QDRANT_URL``, else a local
    server on 6333), without importing ``qdrant_client``: for hot paths that only query.
    """
    from empirica.core.qdrant.rest import RestQdrant

    url = os.getenv("EMPIRICA_QDRANT_URL")
    if url:
        return RestQdrant(url)
    default_url = "http://localhost:6333"
    try:
        import urllib.request

        with urllib.request.urlopen(urllib.request.Request(f"{default_url}/collections", method="GET"), timeout=1) as r:
            if r.status == 200:
                return RestQdrant(default_url)
    except Exception:
        pass  # no server on 6333: the local server's store, when it holds one
    local = local_server_service("vectors")
    return RestQdrant(local) if local else None


def _get_qdrant_client(
    qdrant_url: str | None = None,
    *,
    project_id: str | None = None,
):
    """Get Qdrant client with lazy imports.

    Args:
        qdrant_url: Optional per-request URL override. When provided,
            connect to that URL instead of the module default.
        project_id: Optional project id. When provided AND no explicit
            qdrant_url AND a resolver is installed via
            ``set_url_resolver()``, the resolver is called with the
            project_id to look up the target URL. Cortex installs
            ``resolve_qdrant_url`` on startup; standalone empirica CLI
            installs a default reading tenants.db (prop_ure7rqfuon,
            2026-07-24). Falls through to env if resolver returns None.

    Priority:
    1. ``qdrant_url`` argument (explicit per-request URL)
    2. Installed resolver on ``project_id`` (per-org routing hook)
    3. ``EMPIRICA_QDRANT_URL`` environment variable (module default)
    4. localhost:6333 if Qdrant server is running

    Returns None if no Qdrant server is available. File-based storage was
    removed (#45) because it creates incompatible storage formats, causes
    lock conflicts with concurrent processes, and uses CWD-relative paths.
    """
    QdrantClient, _, _, _ = _get_qdrant_imports()

    # Priority 1: Per-request URL (cortex per-org routing, explicit)
    if qdrant_url:
        return QdrantClient(url=qdrant_url)

    # Priority 2: Resolver hook (per-org routing via project_id lookup).
    # Cortex on startup installs ``resolve_qdrant_url`` from
    # ``cortex.qdrant_routing``; standalone empirica CLI installs a
    # default resolver reading tenants.db + CORTEX_QDRANT_URLS_BY_ORG.
    # Backward compat: no resolver installed → skip this priority.
    if project_id and _url_resolver is not None:
        try:
            resolved = _url_resolver(project_id)
        except Exception as e:  # never let a resolver blow up a write
            logger.warning(
                f"url_resolver({project_id!r}) raised {type(e).__name__}: {e} — falling through to env",
            )
            resolved = None
        if resolved:
            return QdrantClient(url=resolved)

    # Priority 3: Module default from env
    url = os.getenv("EMPIRICA_QDRANT_URL")
    if url:
        return QdrantClient(url=url)

    # Priority 4: Check if Qdrant server is running on localhost:6333
    default_url = "http://localhost:6333"
    try:
        import urllib.request

        req = urllib.request.Request(f"{default_url}/collections", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200:
                return QdrantClient(url=default_url)
    except Exception:
        pass  # Server not available

    # Priority 5: the local server's embedded store, on a machine with no Cortex (goal db2cf3fa)
    local = local_server_service("vectors")
    if local:
        return QdrantClient(url=local)

    # No Qdrant server available — skip gracefully
    logger.debug("Qdrant server not available. Start with: qdrant")
    return None


def _service_url(
    qdrant_url: str | None = None,
    *,
    project_id: str | None = None,
) -> str | None:
    """Resolve the service URL used by the REST search path.

    Args:
        qdrant_url: Optional per-request URL override. When provided, that URL
            wins.
        project_id: Optional project id — consults the installed URL resolver
            (per-org routing hook, prop_ure7rqfuon 2026-07-24) when
            qdrant_url is absent.

    Priority matches ``_get_qdrant_client``: explicit URL → resolver hook →
    ``EMPIRICA_QDRANT_URL`` env.
    """
    if qdrant_url:
        return qdrant_url
    if project_id and _url_resolver is not None:
        try:
            resolved = _url_resolver(project_id)
        except Exception:
            resolved = None
        if resolved:
            return resolved
    return os.getenv("EMPIRICA_QDRANT_URL")


def _rest_search(
    collection: str,
    vector: list[float],
    limit: int,
    qdrant_url: str | None = None,
) -> list[dict]:
    """REST-based search.

    Args:
        collection: Qdrant collection name.
        vector: Query vector.
        limit: Max results.
        qdrant_url: Optional per-request URL override (cortex per-org routing).
            None preserves the existing env-based behavior.

    Returns empty list when no URL is resolvable (offline-safe).
    """
    try:
        import requests

        url = _service_url(qdrant_url=qdrant_url)
        if not url:
            return []
        resp = requests.post(
            f"{url}/collections/{collection}/points/search",
            json={"vector": vector, "limit": limit, "with_payload": True},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])
    except Exception as e:
        logger.debug(f"REST search failed: {e}")
        return []
