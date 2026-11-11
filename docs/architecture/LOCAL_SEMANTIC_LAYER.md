# The local semantic layer

Status: shipped in 1.16.0. On a machine with no Cortex, `empirica serve` owns the vector store and the embedding model.

## The problem, measured

On a fresh machine (empty home, no Cortex, no Qdrant, no Ollama), each practice gets its git repository and its SQLite
database, and semantic search is empty. Onboarding sets up neither Qdrant nor an embedding model. With no Ollama,
embeddings fall back to hashed vectors, which are not semantic. Since 1.16.0 the search, the logging warning and
`doctor` say so; this design is what makes it work.

Two constraints rule out the obvious answer of an embedded store in every process:

- qdrant-client's local mode (`QdrantClient(path=...)`) takes an exclusive, non-blocking lock on its folder, so a
  second process fails with "already accessed by another instance".
- An in-process embedding model would load in every hook and CLI process. Empirica runs many of these at once: hooks,
  CLI verbs, the postflight worker, `project-embed`, and several sessions.

Both need one long-lived owner. The serve service is already installed by 1.15.1 onboarding on machines with no Cortex,
so it becomes that owner.

## Shape

### 1. Vector store: a Qdrant-compatible subset, served by `empirica serve`

`serve` holds one `QdrantClient(path="~/.empirica/vectors")` and exposes, on loopback only, the part of Qdrant's REST
API that empirica uses, under `/qdrant`. An inventory of the client code found:

| Method | Path |
|---|---|
| GET | `/` (the client's version check reads `version`) |
| GET | `/collections`, `/collections/{c}`, `/collections/{c}/exists` |
| PUT / DELETE | `/collections/{c}` |
| PUT | `/collections/{c}/points` (upsert) |
| POST | `/collections/{c}/points` (retrieve) |
| POST | `/collections/{c}/points/delete`, `/scroll`, `/count`, `/payload`, `/query`, `/search` |

Each route translates the JSON body into the matching `QdrantLocal` call, all of which exist in qdrant-client 1.18
(checked in source). Empirica uses no payload indexes, named or sparse vectors, aliases or snapshots, so the subset
needs none.

Because the routes speak Qdrant's own REST shape, both client paths work unchanged: `QdrantClient(url=...)` and
`RestQdrant`. They only need the base URL. The alternative, an empirica-specific API, would mean changing every one of
about 40 call sites.

Access from serve's async routes goes through a thread pool, with one lock around the local client.

### 2. Embeddings: Ollama-compatible routes, served by `empirica serve`

`serve` loads a small ONNX embedding model with fastembed (CPU, no GPU, no Ollama) and exposes Ollama's three embedding
routes under `/ollama`: `GET /api/tags`, `POST /api/embeddings` and `POST /api/embed`. The existing `ollama` provider
then works unchanged with its URL pointed at the server. The model is added to `MODEL_DIMENSIONS`.

### 3. Resolution, with nothing to configure

Each process picks the first of these that applies.

- **Qdrant:** an explicit URL, then the per-org resolver for the project, then `EMPIRICA_QDRANT_URL`, then a server
  answering on `localhost:6333`, then the local server, if its `/v1/health` advertises `vectors`. Otherwise there is
  none, and `doctor` and `project-search` say so.
- **Embeddings:** an explicit provider, then Ollama at its URL with an embedding model, then the local server, if its
  health advertises `embeddings`. Otherwise it falls back to hashed vectors, which are reported as not semantic.

A Cortex seat or a machine running Qdrant and Ollama, like this one, is unaffected.

Lessons storage, `qdrant_search_client`, `workspace_db`'s reference check, doctor's secret scrub, setup's check and
PersonaRegistry all use the same resolution. Serve's own health check is the exception: the last step asks serve's
health, which from inside serve would ask itself.

### Upgrading to Cortex

Moving to Cortex should be simple, with the cloud store used instead of the local one or alongside it.

- **Instead of:** this falls out of the resolution order. A Cortex seat configures its Qdrant, through the per-org
  resolver or `EMPIRICA_QDRANT_URL`, and a configured store always wins over the local server. Nothing else changes.
- **Moving what exists:** vectors are derived data and are never copied. Hosted Cortex embeds each artifact it
  receives (`POST /v1/artifacts/import`, the same path as `/log`) with its own model, and its Qdrant is not reachable
  from a user's machine, so the client re-embeds nothing. For a Qdrant
  server a person or organisation runs and can reach, `empirica project-embed` re-embeds each practice from SQLite and
  the git notes into it; the dimension check recreates a collection whose size differs from the local model's 384.
  Client-side `project-embed` into a store Cortex queries would put vectors in a different space and return bad
  results without an error.
- **Alongside:** the local store stays where it is. Clearing or keeping it is the person's choice, and `doctor` shows
  which store is in use.

### 4. Install and first run

- **Install:** fastembed and onnxruntime join the `[api]` extra, which serve already requires.
- **First run:** the model is downloaded on the first serve start. Onboarding says so and gives the size. Offline,
  the embedding routes report themselves unavailable and the signals show it.
- **Where the vectors live:** `~/.empirica/vectors`, one store per machine. Each practice keeps its own collections
  inside it, as on a Qdrant server.
- **Vectors are derived data:** `empirica project-embed` rebuilds them from SQLite and the notes. Moving to a real
  Qdrant server later is a re-embed, and the existing dimension check recreates a collection whose size changed.

### 5. Hot-path budget

The per-prompt path is the tool-router: one embed and three queries. Over loopback, against a CPU model, it should add
no more than 50 ms compared with today's Qdrant-server path. The release measures it, 15 runs each against the last
tag, as the hot-path rule requires. Writes from `*-log` verbs and the postflight worker go through the same routes.

## Not in scope

Cortex machines, Windows-native serve (unsupported), and multi-user machines: one store per user's home.

## Decided

1. **Model:** `intfloat/multilingual-e5-small` (384 dimensions, multilingual), through fastembed. Practices hold Dutch,
   German and Spanish text as well as English. fastembed does not list it, so it is registered as a custom ONNX model,
   using the repository's int8 file: 135 MB against 487 MB fp32, 2.6 ms against 7.2 ms per embed, the same top result
   on a fixed English/German/Spanish/Dutch check (lower ranks reorder). int8 was chosen on those numbers.
2. **Dependency:** fastembed and onnxruntime join the `[api]` extra, which serve already requires.

## How it was checked

Tests run the vector routes against a real local-mode store and the embedding routes against a stub model, so they never
download. A fresh-machine run (provision, log, search) found a German note from an English query, and the per-prompt
search measured about 80 ms, against about 112 ms on the existing Qdrant-and-Ollama path.
