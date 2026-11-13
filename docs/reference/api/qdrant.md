# Qdrant Vector Storage API

**Module:** `empirica.core.qdrant`
**Category:** Vector Storage & Semantic Search
**Stability:** Beta

**Related docs:**
- [QDRANT_EPISTEMIC_INTEGRATION.md](../../architecture/QDRANT_EPISTEMIC_INTEGRATION.md) - Architecture and collections design
- [STORAGE_ARCHITECTURE_COMPLETE.md](../../architecture/STORAGE_ARCHITECTURE_COMPLETE.md) - Four-layer storage flow

---

## Overview

Qdrant integration provides semantic search and memory embedding for Empirica. This is the 4th storage layer (SEARCH) in the storage architecture:

| Layer | Speed | Purpose | Location |
|-------|-------|---------|----------|
| **HOT** | ns | In-memory graphs | Runtime |
| **WARM** | μs | Metadata queries | SQLite |
| **SEARCH** | ms | Semantic similarity | Qdrant |
| **COLD** | 10ms | Full content | YAML files |

---

## EmbeddingsProvider

Provider-agnostic embeddings adapter. Reads provider/model from env and returns float vectors.

```python
from empirica.core.qdrant.embeddings import EmbeddingsProvider, get_embedding, get_vector_size

# Singleton access (recommended)
embedding = get_embedding("text to embed")
vector_size = get_vector_size()

# Direct instantiation
provider = EmbeddingsProvider()
embedding = provider.embed("text to embed")
print(f"Provider: {provider.provider}, Model: {provider.model}")
```

### Environment Variables

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `EMPIRICA_EMBEDDINGS_PROVIDER` | openai, ollama, jina, voyage, local, auto | auto | Embedding provider |
| `EMPIRICA_EMBEDDINGS_MODEL` | varies | varies by provider | Model name |
| `EMPIRICA_OLLAMA_URL` | URL | http://localhost:11434 | Ollama server URL |
| `OPENAI_API_KEY` | key | required for openai | OpenAI API key |
| `JINA_API_KEY` | key | required for jina | Jina AI API key |
| `VOYAGE_API_KEY` | key | required for voyage | Voyage AI API key |

### Configuration File (v1.6.6+)

In addition to env vars, embeddings can be configured via `~/.empirica/config.yaml`:

```yaml
embeddings:
  provider: ollama
  model: qwen3-embedding    # Use the default tag (0.6B, 1024d) — NOT :8b
  ollama_url: http://localhost:11434
  # jina_api_key: ...
  # voyage_api_key: ...
```

**Priority:** env vars > config.yaml > code defaults.

> **Important:** When using `qwen3-embedding` with Ollama, ensure you pull the correct
> model tag. The default tag (`ollama pull qwen3-embedding`) is 0.6B and produces 1024d
> vectors. The `:8b` tag (`qwen3-embedding:8b`) is a different model that produces 4096d
> vectors. Mixing tags causes Qdrant 500 errors because collection dimensions won't match.
> If this happens, fix the model and run `empirica rebuild --qdrant`.

### Providers

| Provider | Models | Dimensions | Notes |
|----------|--------|------------|-------|
| **auto** | auto-detect | varies | Uses ollama if running, else local |
| **openai** | text-embedding-3-small (default) | 1536 | Requires API key |
| **ollama** | qwen3-embedding (default) | 1024 | Local, no API needed |
| **jina** | jina-embeddings-v3 (default) | 1024 | Multilingual, late-interaction |
| **voyage** | voyage-3-lite (default) | 512 | Fast and cheap |
| **local** | hash-1536 | 1536 | Hash-based fallback for testing |

### Supported Models

```python
# OpenAI
"text-embedding-3-small": 1536
"text-embedding-3-large": 3072
"text-embedding-ada-002": 1536

# Ollama (local)
"qwen3-embedding": 1024      # Default since v1.6.6 (0.6B, default tag)
"qwen3-embedding:0.6b": 1024 # Explicit 0.6B tag — same as default
"qwen3-embedding:8b": 4096   # 8B variant — different dimensions!
"nomic-embed-text": 768
"mxbai-embed-large": 1024
"bge-m3": 1024  # Dense + sparse + colbert

# Jina AI
"jina-embeddings-v3": 1024  # Matryoshka
"jina-colbert-v2": 128  # Late-interaction ColBERT

# Voyage AI
"voyage-3.5": 1024  # Best quality
"voyage-3-lite": 512  # Fast and cheap
"voyage-code-3": 1024  # Code-optimized
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `embed(text)` | `List[float]` | Embed text to vector |
| `vector_size` | `int` | Get vector dimension |

---

## Helper Functions

```python
from empirica.core.qdrant.embeddings import get_embedding, get_vector_size, get_provider_info

# Get embedding for text (uses singleton)
vec = get_embedding("search query")  # List[float]

# Get vector dimension for collection creation
dim = get_vector_size()  # int

# Get provider configuration
info = get_provider_info()
# {"provider": "ollama", "model": "qwen3-embedding", "vector_size": 1024}
```

---

## Vector Store Operations

```python
from empirica.core.qdrant.vector_store import (
    init_collections,
    upsert_memory,
    search,
    delete_memory
)

# Initialize Qdrant collections for project
init_collections(project_id="my-project")

# Store memory items
items = [
    {"id": "f1", "kind": "finding", "text": "Important discovery", "metadata": {...}},
    {"id": "u1", "kind": "unknown", "text": "Open question", "metadata": {...}},
]
upsert_memory(project_id="my-project", items=items)

# Semantic search
results = search(
    project_id="my-project",
    query="what did we learn?",
    kind="finding",  # Optional: filter by kind
    limit=5,
    threshold=0.7
)
# Returns: {"findings": [...], "query": "...", "count": 5}

# Delete memory
delete_memory(project_id="my-project", ids=["f1"])
```

---

## Pattern Retrieval (CASCADE Integration)

Used by PREFLIGHT and CHECK hooks to retrieve procedural knowledge.

```python
from empirica.core.qdrant.pattern_retrieval import (
    retrieve_task_patterns,
    check_against_patterns
)

# PREFLIGHT: Retrieve relevant patterns for task
patterns = retrieve_task_patterns(
    project_id="my-project",
    task_context="implement authentication"
)
# Returns: {
#   "lessons": [...],      # Procedural knowledge
#   "dead_ends": [...],    # Failed approaches to avoid
#   "relevant_findings": [...],  # Related discoveries
# }

# CHECK: Validate approach against known patterns
warnings = check_against_patterns(
    project_id="my-project",
    approach="use JWT tokens",
    vectors={"know": 0.7, "uncertainty": 0.3}
)
# Returns warnings if approach matches dead_ends or mistake patterns
```

---

## Memory Kinds

| Kind | Description | Used By |
|------|-------------|---------|
| `finding` | Discoveries and learnings | finding-log |
| `unknown` | Open questions | unknown-log |
| `dead_end` | Failed approaches | deadend-log |
| `lesson` | Procedural knowledge | lesson-create |
| `docs` | Documentation chunks | docs-explain |
| `episodic` | Session narratives | postflight-submit |
| `trajectory` | Session vector trajectories | postflight-submit |
| `code_api` | Python API surfaces (AST-extracted) | code-embed, project-embed |

---

## Claude Code Bridge

Qdrant is reached on demand through `project-search`; what Claude Code starts with comes from SQLite. The session-start
and post-compact hooks build the project's bootstrap picture (goals, findings, unknowns, decisions, assumptions,
mistakes, dead ends, links, each with an id, within a token budget) and inject it as hook context. Nothing is written
to Claude Code's `MEMORY.md`, which Claude Code owns.

**See also:** [claude-code-symbiosis.md](../../architecture/claude-code-symbiosis.md)

---

## Code API Embeddings (v1.6.6+)

AST-based extraction of Python API surfaces, stored in the eidetic collection as `fact_type="code_api"`.

```bash
# Standalone command
empirica code-embed --project-id <UUID> [--path <dir>]

# Also runs automatically as part of:
empirica project-embed --project-id <UUID>
```

Extracts public functions and classes via AST (no runtime imports), embeds searchable summaries
including signatures, parameters, return types, and docstrings.

```python
from empirica.core.qdrant.code_embeddings import search_code_api

# Semantic search over code API surfaces
results = search_code_api(project_id, "how to create a session")
for r in results:
    print(f"{r['module_path']} (score: {r['score']:.2f})")
    print(r['content'])
```

**Auto-sync:** At session end, `session-end-postflight.py` runs `project-embed` which
includes code API embedding (30s timeout, best-effort).

---

## Implementation Files

- `empirica/core/qdrant/embeddings.py` - EmbeddingsProvider, get_embedding, get_vector_size
- `empirica/core/qdrant/code_embeddings.py` - Code API extraction and embedding
- `empirica/core/qdrant/vector_store.py` - init_collections, upsert_memory, search, delete_memory
- `empirica/core/qdrant/pattern_retrieval.py` - retrieve_task_patterns, check_against_patterns

---

**API Stability:** Beta
**Last Updated:** 2026-03-10
