# The local semantic layer: design for review

Status: proposal, goal db2cf3fa (T3). David's ruling, 2026-11-11: on a machine with no Cortex, `empirica serve` owns the
vector store and the embedding model. Nothing here is built yet.

## The problem, measured

On a fresh machine (empty home, no Cortex, no Qdrant, no Ollama), each practice gets its git repository and its SQLite
database, and semantic search is empty. Onboarding sets up neither Qdrant nor an embedding model. With no Ollama,
embeddings fall back to hashed vectors, which are not semantic. Since 6f3972fc2 the search, the logging warning and
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
API that empirica uses, under `/qdrant`. The inventory (finding 364b9de9) found:

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

- **Qdrant:** `EMPIRICA_QDRANT_URL`, then the per-org resolver, then a server answering on `localhost:6333`, then the
  local server, if its `/v1/health` advertises `vectors`. Otherwise there is none, and the T1 signals say so.
- **Embeddings:** an explicit provider, then Ollama at its URL with an embedding model, then the local server, if its
  health advertises `embeddings`. Otherwise it falls back to hashed vectors, which are reported as not semantic.

A Cortex seat or a machine running Qdrant and Ollama, like this one, is unaffected.

Seven places bypass the resolver today: lessons storage, `qdrant_search_client`, serve's health check, `workspace_db`,
doctor's secret scrub, setup's check and PersonaRegistry. The build repoints them (goal 6f1e9bb6 tracks the defects
found alongside).

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

## Questions for David

1. **Which model.** All of these are in fastembed's supported list at 384 dimensions. Download sizes are not yet checked
   against the model files; they go into onboarding's message once measured.
   - A: `BAAI/bge-small-en-v1.5`. English only.
   - B: `intfloat/multilingual-e5-small`. Multilingual.
   - C: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Multilingual, Apache-2.0.
   - Prediction: B. Practices hold Dutch, German and Spanish text as well as English, and English-only embeddings
     rank those poorly.
2. **Where the dependency lives.**
   - In `[api]`, which serve already requires, or in a new `[local]` extra.
   - Prediction: `[api]`. A machine with no Cortex runs serve anyway, and a second extra is a second thing to get wrong.

## Build plan (T4), after review

1. The vector routes, with tests against a real local-mode store: create, upsert, query, scroll, count, delete,
   payload.
2. The embedding routes, with a test model fixture so tests never download.
3. Resolution in clients, plus repointing the seven bypasses.
4. A fresh-machine end-to-end run: provision, log, search, with results.
5. Hot-path timing against the last tag.
6. A review sweep until a pass comes back clean.
