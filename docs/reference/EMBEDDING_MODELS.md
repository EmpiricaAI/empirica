# Embedding models: which one for your hardware

Empirica's semantic search turns every artifact into a vector with an embedding model. Which model you use depends on
what your machine has. Empirica picks one automatically, in this order: a provider you set explicitly, then Ollama when it
has an embedding model, then the local server's model on a machine with no Cortex, then hashed vectors (not semantic:
`empirica doctor` warns about them). A Cortex seat uses whatever Cortex is configured with.

## The chart

| Your setup | Model | How to get it | Dimensions | Download | Measured |
|---|---|---|---|---|---|
| **Any laptop or desktop, CPU only, no Cortex** (the default) | `multilingual-e5-small` (int8), held by the local server | Automatic: onboarding sets it up, nothing to configure | 384 | about 135 MB, once | 2.6 ms per embed; about 80 ms per prompt including three searches |
| **A machine already running Ollama** (a GPU, Apple silicon or a recent CPU) | `qwen3-embedding:0.6b` | `ollama pull qwen3-embedding:0.6b`; picked automatically | 1024 | not measured here | not measured here |
| **A large local GPU running Ollama** | `qwen3-embedding:8b` | `ollama pull qwen3-embedding:8b`, then `EMPIRICA_EMBEDDINGS_MODEL=qwen3-embedding:8b` | 4096 | not measured here | not measured here |
| **No local compute, an API key** | `text-embedding-3-small` (OpenAI), `voyage-3-lite` (Voyage) or `jina-embeddings-v3` (Jina) | `EMPIRICA_EMBEDDINGS_PROVIDER=openai`, `voyage` or `jina`, plus that provider's key | 1536, 512, 1024 | none | not measured here |
| **Cortex** | whatever your Cortex seat is configured with | Automatic once `cortex.url` is set | as configured | none locally | n/a |

"Measured" means measured on one machine (a Ryzen AI MAX+ 395, CPU only for the local model), not a guarantee for yours.
Rows marked "not measured here" give the model's published dimensions, which Empirica's own table uses; their speed and
size depend on your hardware and were not checked.

## Notes

- **Language.** `multilingual-e5-small` matches across languages: an English question finds a note written in German.
  Check whether another model you choose does the same.
- **Privacy.** The API rows send the text of every artifact to that provider. The local server and Ollama keep it on
  your machine.
- **Bigger is not automatically better here.** Search uses the top few results above a similarity threshold. On a fixed
  check, the int8 local model put the same result first as the full-precision one, at a quarter of the size.

## Changing model

Each model has its own dimensions, and the vectors of one model cannot be read by another. After changing the model or
provider, re-embed each practice:

```bash
empirica project-embed        # in each practice: rebuilds its vectors from SQLite and the git notes
empirica doctor               # the semantic-search check names the store and the model in use
```

Vectors are derived data: re-embedding loses nothing, because the artifacts themselves live in SQLite and the git notes.
The same command fills a Qdrant server you run yourself after you point empirica at it (`EMPIRICA_QDRANT_URL`).
Cortex is different: it embeds the artifacts it receives with its own model, on its own server, so nothing is
re-embedded from your machine.
