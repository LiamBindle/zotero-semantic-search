# Zotero Semantic Search

**Search your research library by meaning, not keywords — completely private, completely offline.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#installation)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/LiamBindle/zotero-semantic-search/pkgs/container/zotero-semantic-search)
[![Build](https://github.com/LiamBindle/zotero-semantic-search/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/LiamBindle/zotero-semantic-search/actions/workflows/docker-publish.yml)

![Search demo](assets/semantic-search.gif)

Zotero's built-in search only finds exact keyword matches. Zotero Semantic Search lets you describe what you're looking for in plain language — a concept, a method, a finding — and surfaces the most relevant papers by meaning. An optional AI assistant can then write a cited summary of the results.

**Everything runs on your own computer.** No account. No internet connection required after setup. Your documents never leave your machine.

---

## Why does local matter?

Most AI-powered search tools work by sending your documents to a server in the cloud. That's fine for public research, but many Zotero libraries contain things that shouldn't leave your computer: unpublished manuscripts, confidential client work, sensitive research data, or proprietary literature.

Zotero Semantic Search runs the AI entirely on your own machine. The app has no way to phone home — network access from the search container is blocked at the system level. You get the benefits of AI search without any of the privacy trade-offs.

---

## Features

- **Search by meaning** — describe what you're looking for in plain English; results are ranked by relevance, not keyword overlap
- **AI summaries** — after a search, generate a cited synthesis of the matching papers; each claim links back to its source
- **Completely private** — all AI models run locally; outbound network traffic from the container is blocked so data cannot leave, even accidentally
- **No Zotero plugin** — reads directly from your existing Zotero library; nothing to install in Zotero, no sync, no account
- **Collection filtering** — search your whole library or narrow to a specific Zotero collection
- **Broad file support** — indexes PDFs, Word documents, PowerPoint, Excel, HTML, and RTF attachments

---

## Screenshots

### Search by meaning
![Search results](assets/semantic-search.gif)

### AI summary with cited references
![AI summary](assets/ai-summary.gif)

---

## Installation

### What you need

- **Docker Desktop** — the app will detect if it's missing and show you a download link
- A Zotero library in its default location (`~/Zotero`)

### Step 1 — Download the app

Go to the [**latest release**](https://github.com/LiamBindle/zotero-semantic-search/releases/latest) and download the installer for your platform:

| Platform | File to download |
|---|---|
| macOS | `.dmg` |
| Windows | `.exe` |
| Linux | `.AppImage` |

Install it like any other application.

### Step 2 — Install Docker Desktop

If you don't already have Docker Desktop installed, download it for your platform:

| Platform | Download |
|---|---|
| macOS | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) |
| Windows | [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) + [Compose plugin](https://docs.docker.com/compose/install/) |

### Step 3 — Open the app

Launch Zotero Semantic Search. On first run it will download the AI models (~5–6 GB) and start automatically. This one-time download can take a few minutes depending on your connection.

Once the startup screen clears, you're ready to search.

> **First search:** indexing your library takes a few minutes the first time. Subsequent searches are fast.

---

## Privacy & network isolation

The container applies iptables rules on startup that block all outbound connections at the kernel level. Only loopback traffic (between the app and the local AI models) is permitted. No library or process running inside the container can bypass this.

In addition, telemetry is disabled in every component (ChromaDB, fastembed, HuggingFace Hub, Ollama) via environment variables. All model weights are baked into the Docker image at build time, so the container has no need to make any network request once running.

To verify isolation yourself:

```bash
docker compose exec zotero-search curl -s --max-time 5 https://example.com
# Expected: connection timed out
```

> **macOS / Windows:** Docker Desktop does not support the `NET_ADMIN` capability required for kernel-level egress blocking. The container automatically falls back to telemetry-only opt-outs on these platforms. Your documents still never leave your machine — there is no code that sends them anywhere — but the network-level hard block is a Linux-only feature.

---

## Configuration

For most users no configuration is needed. The following environment variables can be set in the generated `docker-compose.yml` (found in the app's data directory) to override defaults:

| Variable | Default | Description |
|---|---|---|
| `ZOTERO_DB` | `/zotero/zotero.sqlite` | Path to your Zotero SQLite database |
| `ZOTERO_STORAGE` | `/zotero/storage` | Path to your Zotero attachment storage |
| `CHROMA_PATH` | `/data/chroma` | Where the vector index is stored |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2` | Model used for AI summaries |
| `EMBED_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | Embedding model |

---

## How it works

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

1. **Indexing** — attachments are extracted and split into ~2000-character chunks, embedded with [nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5), and stored in a ChromaDB cosine-similarity index
2. **Search** — the query is embedded (or a LLM-generated hypothetical document is used instead, via [HyDE](https://arxiv.org/abs/2212.10496)) and the nearest chunks are retrieved; results are deduplicated to one card per paper
3. **Summary** — visible result cards are sent to a local Ollama instance with a citation prompt; the response streams back to the browser

---

## Local development

Requires [pixi](https://pixi.sh) (Linux only).

```bash
pixi run dev          # live-reload dev server on http://localhost:8765
pixi run desktop      # Electron app (standard Linux / macOS)
pixi run nix-desktop  # Electron app on NixOS
```

Run `ollama serve` separately if you want AI features during development.

### Releasing

```
/release        # vYEAR.N.0
/release patch  # vYEAR.N.Z
```

---

## Contributing

Bug reports and pull requests are welcome. For significant changes, please open an issue first to discuss the approach.

---

## License

[GNU Affero General Public License v3.0](LICENSE) — if you deploy a modified version as a network service, you must make the source available.
