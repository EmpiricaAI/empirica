"""
Provider-agnostic embeddings adapter.
Reads provider/model from env (or defaults) and returns float vectors.

ENV:
- EMPIRICA_EMBEDDINGS_PROVIDER: openai|ollama|jina|voyage|local|auto (default: auto)
- EMPIRICA_EMBEDDINGS_MODEL: model name (default varies by provider)
- EMPIRICA_OLLAMA_URL: Ollama server URL (default: http://localhost:11434)
- OPENAI_API_KEY (for provider=openai)
- JINA_API_KEY (for provider=jina)
- VOYAGE_API_KEY (for provider=voyage)

Providers:
- auto: Auto-detect best available (ollama if running, else local)
- openai: OpenAI API (REST /v1/embeddings, no SDK — just an API key)
- ollama: Local Ollama server (bge-m3, nomic-embed-text, qwen3-embedding, etc.)
- jina: Jina AI API (jina-embeddings-v3, jina-colbert-v2)
- voyage: Voyage AI API (voyage-3.5, voyage-3-lite)
- local: Hash-based fallback for testing (no external deps)
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Cache for Ollama availability check
_ollama_available: bool | None = None

# Default models and their vector dimensions per provider
DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "ollama": "qwen3-embedding",  # 1024-dim, MTEB 64.3 (upgraded from nomic-embed-text 768d)
    "jina": "jina-embeddings-v3",  # 1024-dim, multilingual, late-interaction
    "voyage": "voyage-3-lite",  # 512-dim, fast and cheap
    "local": "hash-1536",
}


# Ollama warmth + fail-fast tuning.
#
# keep_alive: sent on every embed request so the embed model self-heals its
# residency. Default -1 = "keep resident forever", which MATCHES a properly
# configured server (OLLAMA_KEEP_ALIVE=-1) — so on such a box the client never
# DOWNGRADES the server's pin (an earlier 30m default silently overrode a
# server -1 down to 30m per request; that was a latent regression). On a box
# whose server does NOT pin the model, -1 from the client keeps our 6GB embed
# model hot regardless. Server-side OLLAMA_MAX_LOADED_MODELS is what lets it
# coexist with chat models — keep_alive governs idle, not load pressure.
#
# Ollama wants a NUMBER for keep_alive (seconds; -1 = forever) or a duration
# STRING ("30m"). _parse_keep_alive keeps both forms valid so operators can set
# EMPIRICA_EMBED_KEEP_ALIVE=-1 / =1800 / =30m interchangeably.
#
# timeout: a cold qwen3-embedding cold-start on the Strix Halo measures ~6s;
# 20s gives margin for cold-start + tailnet idle-link wake while still failing
# FAST. The old 60s × 3-size-retry could block a single embed for ~180s — the
# measured multi-minute PREFLIGHT/POSTFLIGHT hang. On timeout we degrade to the
# local hash (a valid 1024-d vector — never breaks Qdrant) rather than block, so
# a backend hiccup never stalls the CLI.
def _parse_keep_alive(raw: str) -> int | str:
    """-1/1800 -> int (seconds, -1=forever); '30m' -> duration string. Both valid to Ollama."""
    return int(raw) if raw.lstrip("-").isdigit() else raw


_EMBED_KEEP_ALIVE = _parse_keep_alive(os.getenv("EMPIRICA_EMBED_KEEP_ALIVE", "-1"))
_EMBED_TIMEOUT = float(os.getenv("EMPIRICA_EMBED_TIMEOUT", "20"))
_EMBED_BATCH_TIMEOUT = float(os.getenv("EMPIRICA_EMBED_BATCH_TIMEOUT", "45"))

# Known vector dimensions per model
MODEL_DIMENSIONS = {
    # OpenAI
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Ollama (local models)
    "nomic-embed-text": 768,
    "nomic-embed-text-v2-moe": 768,  # MoE variant
    "mxbai-embed-large": 1024,
    "bge-m3": 1024,  # BGE-M3: dense + sparse + colbert, multilingual
    "bge-large": 1024,
    "qwen3-embedding": 1024,  # Qwen3 embedding model (0.6B default tag)
    "qwen3-embedding:0.6b": 1024,  # Explicit 0.6B tag
    "qwen3-embedding:8b": 4096,  # 8B variant — different dimensions!
    "snowflake-arctic-embed": 1024,
    "snowflake-arctic-embed2": 1024,
    "granite-embedding": 768,
    "all-minilm": 384,
    "phi3": 3072,
    "phi3:latest": 3072,
    "llama3.1:8b": 4096,
    # Jina AI
    "jina-embeddings-v3": 1024,  # Matryoshka: 1024/512/256/128/64
    "jina-embeddings-v2-base-en": 768,
    "jina-colbert-v2": 128,  # Late-interaction ColBERT
    # Voyage AI
    "voyage-3.5": 1024,  # Latest, best quality
    "voyage-3": 1024,
    "voyage-3-lite": 512,  # Fast and cheap
    "voyage-code-3": 1024,  # Code-optimized
    "voyage-finance-2": 1024,  # Finance domain
    "voyage-multilingual-2": 1024,  # Multilingual
    # Local
    "hash-1536": 1536,
}


def _check_ollama_available(ollama_url: str = "http://localhost:11434") -> bool:
    """Check if Ollama server is running and has embedding models."""
    global _ollama_available

    if _ollama_available is not None:
        return _ollama_available

    try:
        import requests

        # Quick health check
        resp = requests.get(f"{ollama_url}/api/tags", timeout=2)
        if resp.status_code == 200:
            # Check if configured embedding model (or any known embedding model) is available
            models = resp.json().get("models", [])
            model_names = [m.get("name", "").split(":")[0] for m in models]
            configured_model = os.getenv("EMPIRICA_EMBEDDINGS_MODEL", DEFAULT_MODELS.get("ollama", "qwen3-embedding"))
            if configured_model in model_names:
                _ollama_available = True
                logger.info(f"Ollama detected with {configured_model} - using semantic embeddings")
                return True
            # Fallback: any known embedding model
            for name in model_names:
                if name in MODEL_DIMENSIONS:
                    _ollama_available = True
                    logger.info(f"Ollama detected with {name} - using semantic embeddings")
                    return True
        _ollama_available = False
        return False
    except Exception:
        _ollama_available = False
        return False


def _resolve_auto_provider(ollama_url: str) -> str:
    """Resolve 'auto' provider to actual provider based on availability."""
    if _check_ollama_available(ollama_url):
        return "ollama"
    return "local"


def _load_config_file() -> dict:
    """Load embeddings config from ~/.empirica/config.yaml (embeddings section)
    or ~/.empirica/embeddings.conf (legacy fallback).

    Env vars take priority over config file values.

    config.yaml example:
        embeddings:
          provider: ollama
          model: qwen3-embedding
          ollama_url: http://empirica-server:11434

    Supported keys: provider, model, ollama_url, jina_api_key, voyage_api_key
    """
    # Primary: ~/.empirica/config.yaml (embeddings section)
    config_yaml_path = os.path.expanduser("~/.empirica/config.yaml")
    try:
        import yaml

        with open(config_yaml_path) as f:
            full_config = yaml.safe_load(f) or {}
        emb_config = full_config.get("embeddings", {})
        if emb_config:
            return emb_config
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"Could not read {config_yaml_path}: {e}")

    # Fallback: ~/.empirica/embeddings.conf (simple key=value)
    conf_path = os.path.expanduser("~/.empirica/embeddings.conf")
    config = {}
    try:
        with open(conf_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"Could not read {conf_path}: {e}")
    return config


class EmbeddingsProvider:
    """
    Multi-provider embeddings generator for Qdrant vector storage.

    Supports multiple embedding backends with automatic fallback:
    - Ollama (local, free)
    - Jina AI (API, good quality)
    - Voyage AI (API, high quality)
    - Local sentence-transformers (fallback)

    Configuration priority: env vars > ~/.empirica/embeddings.conf > code defaults.
    """

    # Type declarations for conditional attributes
    _jina_api_key: str | None = None
    _voyage_api_key: str | None = None

    def __init__(self) -> None:
        """Initialize embeddings provider based on environment configuration.

        Resolution order per setting: env var > config file > code default.
        Config file: ~/.empirica/embeddings.conf (key=value, one per line).
        """
        file_conf = _load_config_file()

        self.ollama_url = os.getenv("EMPIRICA_OLLAMA_URL", file_conf.get("ollama_url", "http://localhost:11434"))

        # Get provider from env, default to "auto"
        provider_env = os.getenv("EMPIRICA_EMBEDDINGS_PROVIDER", file_conf.get("provider", "auto")).lower()

        # Resolve "auto" to actual provider, tracking if we fell back
        self._auto_fallback = False
        if provider_env == "auto":
            self.provider = _resolve_auto_provider(self.ollama_url)
            if self.provider == "local":
                self._auto_fallback = True
        else:
            self.provider = provider_env

        self.model = os.getenv(
            "EMPIRICA_EMBEDDINGS_MODEL", file_conf.get("model", DEFAULT_MODELS.get(self.provider, "qwen3-embedding"))
        )
        self._client = None
        self._vector_size: int | None = None

        if self.provider == "openai":
            # OpenAI uses its REST API directly (no SDK dependency) — the
            # /v1/embeddings endpoint is a plain POST, same shape as jina/voyage.
            self._openai_api_key = os.getenv("OPENAI_API_KEY", file_conf.get("openai_api_key"))
            if not self._openai_api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY env var or openai_api_key in ~/.empirica/embeddings.conf required for provider=openai"
                )
            self._client = None
            self._vector_size = MODEL_DIMENSIONS.get(self.model, 1536)
        elif self.provider == "ollama":
            # Ollama uses REST API - no special client needed
            self._client = None
            # Vector size from MODEL_DIMENSIONS or determined on first embed
            self._vector_size = MODEL_DIMENSIONS.get(self.model)
        elif self.provider == "jina":
            # Jina AI uses REST API
            self._jina_api_key = os.getenv("JINA_API_KEY", file_conf.get("jina_api_key"))
            if not self._jina_api_key:
                raise RuntimeError(
                    "JINA_API_KEY env var or jina_api_key in ~/.empirica/embeddings.conf required for provider=jina"
                )
            self._client = None
            self._vector_size = MODEL_DIMENSIONS.get(self.model, 1024)
        elif self.provider == "voyage":
            # Voyage AI uses REST API
            self._voyage_api_key = os.getenv("VOYAGE_API_KEY", file_conf.get("voyage_api_key"))
            if not self._voyage_api_key:
                raise RuntimeError(
                    "VOYAGE_API_KEY env var or voyage_api_key in ~/.empirica/embeddings.conf required for provider=voyage"
                )
            self._client = None
            self._vector_size = MODEL_DIMENSIONS.get(self.model, 1024)
        elif self.provider == "local":
            # No external dependency; simple hashing-based embedding (for testing)
            self._client = None
            if self._auto_fallback:
                # Match Ollama dimensions so fallback vectors fit existing collections
                configured_model = os.getenv(
                    "EMPIRICA_EMBEDDINGS_MODEL", DEFAULT_MODELS.get("ollama", "qwen3-embedding")
                )
                self._vector_size = MODEL_DIMENSIONS.get(configured_model, 1024)
            else:
                self._vector_size = 1024  # Default to 1024 for consistency
        else:
            raise RuntimeError(
                f"Unsupported provider '{self.provider}'. Set EMPIRICA_EMBEDDINGS_PROVIDER=openai|ollama|jina|voyage|local|auto"
            )

        logger.debug(f"Embeddings provider: {self.provider}, model: {self.model}")

    def embed(self, text: str) -> list[float]:
        """Generate embedding vector for the given text using configured provider."""
        text = text or ""

        if self.provider == "openai":
            return self._embed_openai(text)

        if self.provider == "ollama":
            return self._embed_ollama(text)

        if self.provider == "jina":
            return self._embed_jina(text)

        if self.provider == "voyage":
            return self._embed_voyage(text)

        if self.provider == "local":
            return self._embed_local_hash(text)

        raise RuntimeError(f"Unsupported provider '{self.provider}'.")

    def _prepare_ollama_prompt(self, text: str, max_chars: int) -> str:
        """Normalize and bound prompt size before sending to Ollama."""
        normalized = " ".join((text or "").replace("\x00", " ").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[:max_chars].rsplit(" ", 1)[0] + " ..."

    def _embed_ollama(self, text: str) -> list[float]:
        """Embed using local Ollama server."""
        import requests

        url = f"{self.ollama_url}/api/embeddings"
        prompt_sizes = [1200, 900, 700]

        for attempt, max_chars in enumerate(prompt_sizes, start=1):
            payload = {
                "model": self.model,
                "prompt": self._prepare_ollama_prompt(text, max_chars=max_chars),
                "keep_alive": _EMBED_KEEP_ALIVE,  # self-heal residency — re-arm warmth every call
            }

            try:
                resp = requests.post(url, json=payload, timeout=_EMBED_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                embedding = data.get("embedding", [])

                if not embedding:
                    if attempt < len(prompt_sizes):
                        logger.warning(
                            f"Ollama returned empty embedding for model {self.model} "
                            f"(attempt {attempt}/{len(prompt_sizes)})"
                        )
                        time.sleep(0.4 * attempt)
                        continue
                    logger.warning(f"Ollama returned empty embedding for model {self.model}")
                    return self._embed_local_hash(text)

                # Validate and cache actual vector size from model response
                actual_size = len(embedding)
                if self._vector_size is not None and actual_size != self._vector_size:
                    logger.error(
                        f"DIMENSION MISMATCH: Ollama model '{self.model}' returned {actual_size}d vectors "
                        f"but Empirica expected {self._vector_size}d. This will cause Qdrant upsert failures. "
                        f"Check that you pulled the correct model tag — e.g., 'qwen3-embedding' (1024d) "
                        f"vs 'qwen3-embedding:8b' (4096d). Run 'empirica rebuild --qdrant' after fixing."
                    )
                    raise ValueError(
                        f"Embedding dimension mismatch: model '{self.model}' returned {actual_size}d, "
                        f"expected {self._vector_size}d. Pull the correct model tag or update "
                        f"EMPIRICA_EMBEDDINGS_MODEL to match your Ollama model."
                    )
                if self._vector_size is None:
                    self._vector_size = actual_size
                    logger.info(f"Ollama {self.model} vector size: {self._vector_size}")

                return embedding

            except requests.exceptions.ConnectionError:
                logger.warning(f"Cannot connect to Ollama at {self.ollama_url} - falling back to local hash")
                return self._embed_local_hash(text)
            except requests.exceptions.Timeout:
                # A read-timeout means the model is cold-loading (or the box is under load),
                # NOT that the prompt is too big — so shrinking it and retrying just burns
                # another _EMBED_TIMEOUT for nothing. The request already armed the load
                # server-side (keep_alive re-arms warmth), so the NEXT embed hits the
                # now-resident model. Degrade THIS call to hash immediately: bounds cold-start
                # cost to a single _EMBED_TIMEOUT (not 3×), and the _get_embedding_safe memo
                # means only one distinct query string per process ever pays it.
                logger.warning(
                    f"Ollama embed timed out after {_EMBED_TIMEOUT}s (model cold-loading?) - "
                    f"degrading to local hash; server is warming for next call"
                )
                return self._embed_local_hash(text)
            except Exception as e:
                if attempt < len(prompt_sizes):
                    logger.warning(f"Ollama embedding attempt {attempt}/{len(prompt_sizes)} failed: {e}")
                    time.sleep(0.4 * attempt)
                    continue
                logger.warning(f"Ollama embedding failed: {e} - falling back to local hash")
                return self._embed_local_hash(text)

        # Unreachable in practice (prompt_sizes is non-empty), but satisfies type checker
        return self._embed_local_hash(text)

    def batch_embed(self, texts: list[str], max_chars: int = 1200) -> list[list[float] | None]:
        """Batch embed multiple texts. Returns list of vectors (None for failures)."""
        if self.provider == "ollama":
            return self._batch_embed_ollama(texts, max_chars)
        # Fallback: sequential for non-Ollama providers
        return [self.embed(t) for t in texts]

    def _batch_embed_ollama(self, texts: list[str], max_chars: int = 1200) -> list[list[float] | None]:
        """Batch embed using Ollama /api/embed endpoint (accepts input list)."""
        import requests

        url = f"{self.ollama_url}/api/embed"
        prepared = [self._prepare_ollama_prompt(t, max_chars=max_chars) for t in texts]

        try:
            resp = requests.post(
                url,
                json={
                    "model": self.model,
                    "input": prepared,
                    "keep_alive": _EMBED_KEEP_ALIVE,  # self-heal residency — re-arm warmth every call
                },
                timeout=_EMBED_BATCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])

            if len(embeddings) != len(texts):
                logger.warning(f"Batch embed: expected {len(texts)} vectors, got {len(embeddings)}")
                # Pad with None for missing
                while len(embeddings) < len(texts):
                    embeddings.append(None)

            # Validate dimensions on first non-empty result
            for emb in embeddings:
                if emb and self._vector_size is None:
                    self._vector_size = len(emb)
                    logger.info(f"Ollama {self.model} vector size: {self._vector_size}")
                    break

            return embeddings
        except Exception as e:
            logger.warning(f"Batch embed failed: {e} — falling back to sequential")
            return [self.embed(t) for t in texts]

    def _embed_openai(self, text: str) -> list[float]:
        """Embed using the OpenAI REST API (text-embedding-3-*, etc.).

        Calls /v1/embeddings directly with ``requests`` — no ``openai`` SDK
        dependency. The endpoint shape is identical to jina/voyage, so this
        mirrors those providers. Empirica's default stack runs locally
        (ollama/qwen3-embedding); the openai provider is opt-in.
        """
        import requests

        url = "https://api.openai.com/v1/embeddings"
        headers = {"Authorization": f"Bearer {self._openai_api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "input": text}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("data", [{}])[0].get("embedding", [])

            if not embedding:
                logger.warning(f"OpenAI returned empty embedding for model {self.model}")
                return self._embed_local_hash(text)

            # Cache vector size
            if self._vector_size is None:
                self._vector_size = len(embedding)
                logger.info(f"OpenAI {self.model} vector size: {self._vector_size}")

            return embedding

        except requests.exceptions.RequestException as e:
            logger.warning(f"OpenAI embedding failed: {e} - falling back to local hash")
            return self._embed_local_hash(text)

    def _embed_jina(self, text: str) -> list[float]:
        """Embed using Jina AI API (jina-embeddings-v3, etc.)."""
        import requests

        url = "https://api.jina.ai/v1/embeddings"
        headers = {"Authorization": f"Bearer {self._jina_api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "input": [text], "encoding_type": "float"}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("data", [{}])[0].get("embedding", [])

            if not embedding:
                logger.warning(f"Jina returned empty embedding for model {self.model}")
                return self._embed_local_hash(text)

            # Cache vector size
            if self._vector_size is None:
                self._vector_size = len(embedding)
                logger.info(f"Jina {self.model} vector size: {self._vector_size}")

            return embedding

        except requests.exceptions.RequestException as e:
            logger.warning(f"Jina embedding failed: {e} - falling back to local hash")
            return self._embed_local_hash(text)

    def _embed_voyage(self, text: str) -> list[float]:
        """Embed using Voyage AI API (voyage-3.5, voyage-3-lite, etc.)."""
        import requests

        url = "https://api.voyageai.com/v1/embeddings"
        headers = {"Authorization": f"Bearer {self._voyage_api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "input": [text],
            "input_type": "document",  # or "query" for search queries
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("data", [{}])[0].get("embedding", [])

            if not embedding:
                logger.warning(f"Voyage returned empty embedding for model {self.model}")
                return self._embed_local_hash(text)

            # Cache vector size
            if self._vector_size is None:
                self._vector_size = len(embedding)
                logger.info(f"Voyage {self.model} vector size: {self._vector_size}")

            return embedding

        except requests.exceptions.RequestException as e:
            logger.warning(f"Voyage embedding failed: {e} - falling back to local hash")
            return self._embed_local_hash(text)

    def _embed_local_hash(self, text: str) -> list[float]:
        """Simple hashing embedding for testing/fallback (no external deps).

        Uses the configured vector size so fallback embeddings match
        the collection dimensions created by the primary provider.
        """
        import hashlib
        import math

        # Use the provider's known vector size to avoid dimension mismatches
        # when falling back from Ollama (768) or other providers
        dim = self._vector_size or 768
        vec = [0.0] * dim
        for tok in text.split():
            h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
            idx = h % dim
            vec[idx] += 1.0
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    @property
    def vector_size(self) -> int:
        """Get the vector size for this provider/model."""
        if self._vector_size is None:
            # Determine by doing a test embed
            test_vec = self.embed("test")
            self._vector_size = len(test_vec)
        return self._vector_size


_provider_singleton: EmbeddingsProvider | None = None


def get_embedding(text: str) -> list[float]:
    """Get embedding vector for text using the singleton provider instance."""
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = EmbeddingsProvider()
    return _provider_singleton.embed(text)


def get_embedding_provider() -> EmbeddingsProvider:
    """Get the singleton embeddings provider instance."""
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = EmbeddingsProvider()
    return _provider_singleton


def get_vector_size() -> int:
    """
    Get the vector dimension for the current embeddings provider/model.
    Used by vector_store.py to create collections with correct dimensions.
    """
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = EmbeddingsProvider()
    return _provider_singleton.vector_size


def get_provider_info() -> dict:
    """Get current embeddings provider configuration info."""
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = EmbeddingsProvider()
    return {
        "provider": _provider_singleton.provider,
        "model": _provider_singleton.model,
        "vector_size": _provider_singleton.vector_size,
    }
