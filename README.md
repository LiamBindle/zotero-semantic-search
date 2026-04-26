# Zotero Private Search

**Verifiably private semantic search for your Zotero library.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#installation)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/LiamBindle/zotero-private-search/pkgs/container/zotero-private-search)
[![Build](https://github.com/LiamBindle/zotero-private-search/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/LiamBindle/zotero-private-search/actions/workflows/docker-publish.yml)

![Search demo](assets/semantic-search.gif)

---

## Who this is for

Zotero Private Search is for researchers who need a *verifiable* guarantee that documents never leave their machine — not just a claim, but something you can independently confirm. If "trust me, it runs locally" is good enough, [ZotSeek](https://github.com/introfini/ZotSeek) is a better fit.

---

## How the privacy guarantee works

Most "local" AI tools run on your machine but have no enforced network boundary — they could phone home, accidentally or otherwise, the moment a dependency updates or a misconfiguration slips through.

Zotero Private Search uses a Docker sidecar architecture to enforce a hard network boundary. Two containers run side by side: the API container (FastAPI, Ollama, all model weights) is attached *only* to an isolated Docker network with `internal: true`, giving it no route to any external IP. A minimal nginx proxy sits in front, owns the published port, and forwards requests in — it has no outbound access of its own. No library or process inside the API container can reach the internet, not the embedding model, not Ollama, not a future dependency you didn't audit, and not a supply-chain compromise — there is no kernel capability to modify and no gateway to route through.

In addition:
- All model weights are baked into the Docker image at build time, so the container has no reason to make a network request after first launch
- Telemetry is disabled in every component (ChromaDB, fastembed, HuggingFace Hub, Ollama) via environment variables as a defense-in-depth measure
- An active TCP probe runs from inside the container on startup and during use; the result is surfaced in the UI as a green ✓ badge (no internet connection confirmed) so the privacy guarantee is demonstrated rather than asserted
- The source is AGPL-3.0 so your IT department, ethics board, or collaborators can verify these claims independently
- This works on Linux, macOS, and Windows — no platform-specific caveats

See [SECURITY.md](SECURITY.md) for the full threat model and independent verification steps.

---

## Features

- **Private by construction** — Docker network isolation gives the API container no route to the internet on Linux, macOS, and Windows; an active in-UI probe confirms it with a green ✓ badge
- **Search by meaning** — describe what you're looking for in plain English; results ranked by semantic relevance
- **AI summaries with citations** — local Ollama generates a cited synthesis of matching papers; each claim links back to its source
- **Verifiable indexing** — every document indexed, skipped, or failed is recorded and visible in the UI so you can verify completeness without grepping logs
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

1. **Indexing** — attachments are extracted and split into ~2000-character chunks, embedded with [nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5), and stored in a ChromaDB cosine-similarity index.
2. **Search** — the query is embedded (or an LLM-generated hypothetical document is used instead, via [HyDE](https://arxiv.org/abs/2212.10496)) and the nearest chunks are retrieved; results are deduplicated to one card per paper.
3. **Summary** — visible result cards are sent to a local Ollama instance with a citation prompt; the response streams back to the browser.

---

## Local development

Requires [pixi](https://pixi.sh) (Linux and macOS).

```
pixi run dev          # live-reload dev server on http://localhost:8765
pixi run desktop      # Electron app (standard Linux / macOS)
pixi run nix-desktop  # Electron app on NixOS
```

Run `ollama serve` separately — required for search and summaries to work.

---

## License

[GNU Affero General Public License v3.0](LICENSE) — if you deploy a modified version as a network service, you must make the source available.

If the AGPL doesn't fit your use case (commercial deployment, institutional integration, or other reasons), reach out to discuss alternative licensing. For security issues please follow the disclosure process in [SECURITY.md](SECURITY.md) rather than opening a public issue.
