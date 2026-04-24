# CLAUDE.md

Local semantic search for Zotero libraries. FastAPI + ChromaDB service in Docker, with an Electron desktop launcher.

## Development commands

Uses [pixi](https://pixi.sh) (Linux only — pixi has no macOS/Windows platform entries in this repo).

```bash
pixi run dev          # FastAPI dev server on :8765 with hot-reload
pixi run app          # FastAPI production server on :8765
pixi run desktop      # Electron app (standard Linux / macOS)
pixi run nix-desktop  # Electron app on NixOS (uses nix shell for Electron binary)
pixi run delete-index # wipe the local ChromaDB index
```

Electron requires Docker to be running. In dev mode it builds the image from source.

## Releasing

Use the `/release` Claude skill. It computes the next CalVer version, asks for confirmation, then updates `desktop/package.json`, commits, tags, and pushes — triggering both CI workflows.

```
/release        # new release: vYEAR.N.0 (bumps N)
/release patch  # patch:       vYEAR.N.Z (bumps Z)
```

**CalVer format:** `vYEAR.N.PATCH` — e.g. `v2026.1.0`. Docker images are tagged `vYEAR.N.PATCH` (fixed) and `vYEAR.N` (floating). Packaged desktop apps pin to `vYEAR.N`.

## Key constraints

- **pymupdf** must stay in `[tool.pixi.pypi-dependencies]`, not conda deps. The conda-forge build dynamically links libmupdf, which is absent on NixOS; the pip wheel bundles it.
- **`DISABLE_NETWORK_ISOLATION=1`** is set in the generated `docker-compose.yml`. Docker Desktop on macOS/Windows lacks `NET_ADMIN`, so iptables egress blocking is skipped. Production Linux Docker (e.g. CI) does enforce it.
- **`IS_DEV = !app.isPackaged`** in the Electron main process controls whether compose uses `build:` (local source) or pulls the pinned GHCR image.

## Architecture docs

- [Desktop + Docker + CI/CD](docs/architecture-desktop.md)
- [Semantic search](docs/architecture-search.md)
