# Semantic Search Architecture

## Overview

```
Zotero SQLite + storage/
  └── indexer.py      reads metadata & attachments, chunks text, embeds, stores in ChromaDB
        └── extractors.py    per-format text extraction

GET /api/search?q=
  └── main.py         embed query (+ HyDE if Ollama), query ChromaDB, rank & filter
        └── ollama.py        HyDE, query expansion, streaming summary (optional)
```

## Indexing pipeline (`indexer.py`)

1. Query `zotero.sqlite` for items with attachments (title, authors, year, collections)
2. `extractors.extract()` → plain text per file
3. Chunk text, embed in batches of 8 with fastembed
4. Store chunks + metadata in ChromaDB (`zotero_docs` collection, cosine space)
5. Upsert item result immediately to `IndexDB` (SQLite, WAL mode) — one row per attachment, status `indexed` / `skipped_*` / `extraction_failed` / `no_attachment_on_disk`
6. Incremental by default — items whose first chunk ID already exists in ChromaDB are skipped

Interrupted runs are marked `interrupted` in the SQLite log on next startup. `GET /api/index/summary` reads the log directly, so the full item list is available across restarts and during an active run.

**Supported formats:** PDF (pymupdf), DOCX, PPTX, XLSX, RTF, HTML/XML

## Collections (`/api/collections`)

Collections are read directly from the Zotero SQLite database (`SELECT DISTINCT collectionName FROM collections`), not from the ChromaDB index. This means the dropdown always reflects the live state of the user's Zotero library, including collections that have never been indexed.

## Search pipeline (`/api/search`)

1. Embed the query with fastembed
2. *If Ollama available:* **HyDE** — generate a hypothetical matching document with the LLM, embed that instead of the raw query (improves recall for short/vague queries)
3. ChromaDB cosine query, over-fetch candidates (`limit × 8`, min 40)
4. Score = `1 - distance/2`; filter by `min_score` (default 0.55), deduplicate by `item_id`, optionally filter by collection
5. Return ranked hits with title, authors, year, collections, matched text snippet, attach path

## Ollama integration

Ollama runs inside the same container and is always available. The app waits for Ollama readiness at startup before serving requests.

| Endpoint | Feature |
|----------|---------|
| `GET /api/search` | HyDE vector substitution |
| `GET /api/expand` | Query expansion (alternative phrasings) |
| `POST /api/summary` | Streaming SSE synthesis of top results |

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZOTERO_DB` | `/zotero/zotero.sqlite` | Zotero database path (container); `~/Zotero/zotero.sqlite` (host dev) |
| `ZOTERO_STORAGE` | `/zotero/storage` | Attachment files root (container); `~/Zotero/storage` (host dev) |
| `CHROMA_PATH` | `/data/chroma` | Vector store (container); `~/.local/share/zotero-private-search/chroma` (host dev) |
| `INDEX_DB_PATH` | `<CHROMA_PATH>/index-log.db` | SQLite indexing log |
| `EMBED_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | fastembed model identifier |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.2` | LLM for HyDE, expansion, summary |

## Data volumes (Docker)

| Container path | Type | Purpose |
|---------------|------|---------|
| `/zotero` | bind mount (read-only) | Zotero library |
| `/data/chroma` | named volume `chroma-data` | Persistent vector index + `index-log.db` |
| `/app/models` | baked into image | fastembed model cache |
