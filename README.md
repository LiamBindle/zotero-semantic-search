# Zotero Semantic Search

**Find papers by meaning, not keywords. AI summaries. Fully offline.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#installation)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/your-username/zotero-semantic-search/pkgs/container/zotero-semantic-search)

![Search demo](assets/semantic-search.gif)

Zotero's built-in search only matches exact keywords. Zotero Semantic Search lets you describe what you're looking for in plain language — a concept, a finding, a relationship — and surfaces the most relevant papers from your library by meaning. An optional local LLM then synthesises a cited summary of the results. Everything runs locally in Docker; your documents never leave your machine.

---

## Features

- **Search by meaning** — ask a question or describe a concept in plain English; results are ranked by semantic similarity, not keyword overlap
- **AI summaries** — generate a cited synthesis of the matching papers after a search; each claim links back to its source card
- **Fully private** — all models run locally; network egress is blocked at the kernel level inside the container so no data can leave, even accidentally
- **No Zotero plugin required** — reads directly from your existing Zotero SQLite database and attachment files; no sync, no account, no disruption to your workflow
- **Incremental indexing** — only new files are embedded on each search; PDFs, DOCX, PPTX, XLSX, HTML, and RTF are all supported
- **Collection filtering** — search your whole library or narrow to a specific Zotero collection

---

## Screenshots

### Semantic search across your library
![Search results](assets/semantic-search.gif)

### AI summary with cited references
![AI summary](assets/ai-summary.gif)

---

## Installation

The only dependency is Docker. Supported platforms: **Linux** (x86-64), **macOS** (Intel and Apple Silicon), **Windows**.

### Step 1 — Install Docker

| Platform | Download |
|---|---|
| macOS | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) |
| Windows | [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) (requires WSL2 — the installer sets this up automatically) |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose plugin](https://docs.docker.com/compose/install/) |

### Step 2 — Create a `docker-compose.yml`

Create a folder anywhere on your computer and save the following as `docker-compose.yml` inside it:

```yaml
services:
  zotero-search:
    image: ghcr.io/your-username/zotero-semantic-search:latest
    ports:
      - "8000:8000"
    volumes:
      - ~/Zotero:/zotero:ro
      - chroma-data:/data/chroma
    cap_add:
      - NET_ADMIN
    restart: unless-stopped

volumes:
  chroma-data:
```

This assumes Zotero is installed in its default location (`~/Zotero` on macOS/Linux; `%USERPROFILE%\Zotero` on Windows — Docker resolves `~` correctly on all platforms).

> **macOS:** If the container fails to start, Docker Desktop may not support the `NET_ADMIN` capability on your version. Add `environment: [DISABLE_NETWORK_ISOLATION=1]` to the service as a workaround — see [Privacy & Network Isolation](#privacy--network-isolation) for what this trades away.

### Step 3 — Run it

```bash
docker compose up
```

The first run downloads the prebuilt image (~5–6 GB, includes all model weights). Once started, open **http://localhost:8000**.

The first search on a collection indexes any unindexed attachments — this can take a few minutes depending on library size. Subsequent searches are fast.

### Building from source

To build with different models, or to make code changes:

```bash
git clone https://github.com/your-username/zotero-semantic-search.git
cd zotero-semantic-search
docker compose build   # ~10 min first time
docker compose up
```

---

## Privacy & Network Isolation

Zotero libraries often contain unpublished work and sensitive documents. This tool is designed so that no data can leave your machine, through two independent layers:

**1. Kernel-level egress block** — the container applies iptables rules on startup that DROP all new outbound connections. Only loopback traffic (app ↔ Ollama) and responses to inbound connections (port 8000) are permitted. This is enforced at the Linux kernel level inside the container's network namespace — no library or process can bypass it.

**2. Telemetry opt-outs** — environment variables disable analytics in every component (ChromaDB, fastembed, HuggingFace Hub, Ollama). These apply inside Docker and during local development.

All model weights are baked into the Docker image at build time, so the running container has no reason to make any outbound request.

To verify isolation is active:

```bash
docker compose exec zotero-search curl -s --max-time 5 https://example.com
# Expected: connection timed out — not a response
```

**Disabling isolation** — set `DISABLE_NETWORK_ISOLATION=1` in your `docker-compose.yml` environment if `NET_ADMIN` is unavailable (some Docker Desktop configurations on macOS). With this set, egress is not blocked at the kernel level; only the telemetry opt-outs apply.

---

## Configuration

Set these in the `environment` section of your `docker-compose.yml` to override defaults:

| Variable | Default | Description |
|---|---|---|
| `ZOTERO_DB` | `/zotero/zotero.sqlite` | Path to your Zotero SQLite database |
| `ZOTERO_STORAGE` | `/zotero/storage` | Path to your Zotero attachment storage |
| `CHROMA_PATH` | `/data/chroma` | Where ChromaDB persists the vector index |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2` | LLM used for query expansion and summaries |
| `EMBED_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | Embedding model |

To build with different models baked into the image:

```bash
docker compose build \
  --build-arg OLLAMA_MODEL=llama3.1:8b \
  --build-arg EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5
```

---

## How It Works

```
Query
  │
  ├─ Ollama (HyDE) ──► hypothetical passage ──► embed ──► ChromaDB query
  │                                                              │
  └─ (fallback) ──────────────────────────► embed ──► ChromaDB query
                                                              │
                                                         ranked results
                                                              │
                                                    Ollama (summary) ──► streamed response
```

1. **Indexing** — attachments are extracted and split into ~2000-character chunks, embedded with [nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) (768-dim, 8192-token context), and stored in a ChromaDB cosine-similarity index
2. **Search** — the query is embedded (or a LLM-generated hypothetical document is embedded instead, via [HyDE](https://arxiv.org/abs/2212.10496)) and the nearest chunks are retrieved; results are deduplicated to one card per paper
3. **Summary** — visible result cards are sent to Ollama with a citation prompt; tokens stream back to the browser via Server-Sent Events

---

## Local Development

Requires [pixi](https://pixi.sh).

```bash
pixi run dev          # live-reload dev server on http://localhost:8000
pixi run app          # production server on http://127.0.0.1:8000
pixi run delete-index # wipe the local ChromaDB index
```

Run `ollama serve` separately if you want AI features. Telemetry opt-outs are applied automatically by the pixi environment.

---

## Contributing

Bug reports and pull requests are welcome. For significant changes, please open an issue first to discuss the approach.

---

## License

[GNU Affero General Public License v3.0](LICENSE) — if you deploy a modified version as a network service, you must make the source available.
