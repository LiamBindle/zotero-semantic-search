# Zotero Private Search

**Verifiably private semantic search for your Zotero library.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#installation)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/LiamBindle/zotero-private-search/pkgs/container/zotero-private-search)
[![Build](https://github.com/LiamBindle/zotero-private-search/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/LiamBindle/zotero-private-search/actions/workflows/docker-publish.yml)

![Search demo](assets/semantic-search.gif)

---

## Who this is for

Zotero Private Search is for researchers who need a *verifiable* guarantee that documents never leave their machine — indigenous knowledge interviews, IRB-bound human-subjects research, privileged legal materials, unpublished manuscripts, and other contexts where "trust me, it runs locally" isn't a strong enough claim.

If you just want better search inside Zotero, [ZotSeek](https://github.com/introfini/ZotSeek) is an excellent Zotero plugin with a broader feature set and is a better fit for general use. This tool exists for the narrower case where the privacy guarantee itself is the requirement.

---

## How the privacy guarantee works

Most "local" AI tools run on your machine but have no enforced network boundary — they could phone home, accidentally or otherwise, the moment a dependency updates or a misconfiguration slips through.

Zotero Private Search runs inside a Docker container that applies iptables rules at startup to block all outbound network traffic at the kernel level. Only loopback traffic (between the app and the local AI models) is permitted. No library or process running inside the container can bypass this — not the embedding model, not Ollama, not a future dependency you didn't audit.

In addition:
- All model weights are baked into the Docker image at build time, so the container has no need to make a network request after first launch
- Telemetry is disabled in every component (ChromaDB, fastembed, HuggingFace Hub, Ollama) via environment variables as a defense-in-depth measure
- An active probe runs from inside the container on startup and during use; the result is surfaced in the UI as a status badge (`blocked` / `fallback` / `breach`) so the privacy guarantee is demonstrated rather than asserted
- The source is AGPL-3.0 so your IT department, ethics board, or collaborators can verify these claims independently

To verify the network block yourself:

```bash
docker compose exec zotero-private-search curl -s --max-time 5 https://example.com
# Expected: connection timed out
```

See [SECURITY.md](SECURITY.md) for the full threat model, including what's out of scope.

> **macOS / Windows note:** Docker Desktop does not support the `NET_ADMIN` capability required for kernel-level egress blocking. On these platforms the container falls back to telemetry-only opt-outs, and the airgap badge will read `fallback` rather than `blocked`. Your documents still never leave your machine — there is no code that sends them anywhere — but the network-level hard block is a Linux-only feature. If verifiable network isolation is the reason you're here, run this on Linux.

---

## Features

- **Private by construction** — kernel-level network egress block on Linux with an active in-UI probe to verify it; telemetry-disabled fallback on macOS/Windows
- **Search by meaning** — describe what you're looking for in plain English; results ranked by semantic relevance
- **AI summaries with citations** — optional local Ollama generates a cited synthesis of matching papers; each claim links back to its source
- **Verifiable indexing** — every run produces an index summary listing exactly which files were indexed, skipped, or failed (see [Index summary](#index-summary))
- **Broad file support** — PDF, Word, PowerPoint, Excel, ODT, EPUB, HTML, RTF, plain text, Markdown
- **No Zotero plugin** — reads your existing Zotero library directly; nothing to install in Zotero, no account, no sync
- **Collection filtering** — search your whole library or scope to a specific Zotero collection

---

## Installation

### What you need

- **Docker Desktop** — the app will detect if it's missing and show you a download link
- A Zotero library in its default location (`~/Zotero`)

### Step 1 — Download the app

Go to the [**latest release**](https://github.com/LiamBindle/zotero-private-search/releases/latest) and download the installer for your platform:

| Platform | File to download |
| --- | --- |
| macOS | `.dmg` |
| Windows | `.exe` |
| Linux | `.AppImage` |

Install it like any other application.

### Step 2 — Install Docker Desktop

If you don't already have Docker Desktop installed, download it for your platform:

| Platform | Download |
| --- | --- |
| macOS | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) |
| Windows | [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) + [Compose plugin](https://docs.docker.com/compose/install/) |

### Step 3 — Open the app

Launch Zotero Private Search. On first run it will download the AI models (~5–6 GB) and start automatically. This one-time download can take a few minutes depending on your connection.

Once the startup screen clears, you're ready to search.

> **First search:** indexing your library takes a few minutes the first time. Subsequent searches are fast.

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

1. **Indexing** — attachments are extracted and split into ~2000-character chunks, embedded with [nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5), and stored in a ChromaDB cosine-similarity index. After each run, an [index summary](#index-summary) records exactly which files were indexed, skipped, or failed.
2. **Search** — the query is embedded (or a LLM-generated hypothetical document is used instead, via [HyDE](https://arxiv.org/abs/2212.10496)) and the nearest chunks are retrieved; results are deduplicated to one card per paper.
3. **Summary** — visible result cards are sent to a local Ollama instance with a citation prompt; the response streams back to the browser.

### Index summary

Trusting that a privacy-sensitive corpus was actually indexed matters more than trusting a progress bar, so every run produces a structured summary covering each attachment:

- `indexed` — text was extracted and vectors were stored
- `skipped_unsupported` — file extension not in the supported list
- `skipped_empty` — no extractable text (e.g. a scanned PDF with no OCR layer)
- `extraction_failed` — extractor raised an error (with the message)
- `no_attachment_on_disk` — Zotero has the metadata but the file is missing

The summary is written to `<chroma-parent>/index-summary.json` and is also reachable via `GET /api/index/summary`. The frontend surfaces it through the "N / M indexed" counter — clicking it opens a citation-style list of every item, so you can verify completeness without grepping logs.

---

## Configuration

For most users no configuration is needed. The following environment variables can be set in the generated `docker-compose.yml` (found in the app's data directory) to override defaults:

| Variable | Default | Description |
| --- | --- | --- |
| `ZOTERO_DB` | `/zotero/zotero.sqlite` | Path to your Zotero SQLite database |
| `ZOTERO_STORAGE` | `/zotero/storage` | Path to your Zotero attachment storage |
| `CHROMA_PATH` | `/data/chroma` | Where the vector index is stored |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2` | Model used for AI summaries |
| `EMBED_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | Embedding model |

---

## Scope

This tool is intentionally focused on one thing: verifiably private semantic search over your existing Zotero library. To keep the privacy guarantee strong and the surface area small, several things are deliberately out of scope:

- It is not a Zotero plugin (use ZotSeek if you want one)
- It requires Docker — the container is the security boundary
- It does not OCR scanned PDFs; they appear in the index summary as `skipped_empty` so you can verify what was and wasn't indexed
- It does not currently provide find-similar, hybrid keyword+semantic search, or reranking. These may be added in the future

If you need those features today, ZotSeek or [deep-zotero](https://github.com/ccam80/deep-zotero) are likely better fits.

---

## Local development

Requires [pixi](https://pixi.sh) (Linux only).

```
pixi run dev          # live-reload dev server on http://localhost:8765
pixi run desktop      # Electron app (standard Linux / macOS)
pixi run nix-desktop  # Electron app on NixOS
```

Run `ollama serve` separately if you want AI features during development.

---

## Contributing

Bug reports and pull requests are welcome. For significant changes, please open an issue first to discuss the approach.

If you find a security issue, please follow the disclosure process in [SECURITY.md](SECURITY.md) rather than opening a public issue.

---

## License

[GNU Affero General Public License v3.0](LICENSE) — if you deploy a modified version as a network service, you must make the source available.
 
If the AGPL doesn't fit your use case (commercial deployment, institutional integration, or other reasons), reach out at to discuss alternative licensing.
