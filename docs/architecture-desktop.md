# Desktop, Docker & CI Architecture

## System overview

```
Electron (main.js)
  ├── generates docker-compose.yml → app.getPath('userData')
  ├── docker compose up            → container on localhost:8000
  └── WebContentsView              → loads http://localhost:8000 inside the window
```

## Electron process model

| Process | File | Responsibility |
|---------|------|----------------|
| Main | `desktop/src/main.js` | Docker lifecycle, IPC handlers, application menu |
| Preload | `desktop/src/preload.js` | Typed `contextBridge` bridge to renderer |
| Renderer | `desktop/src/renderer/` | Status/log UI; covered by `WebContentsView` once ready |

`WebContentsView` is a child view added to the main window after the service is ready. It fills the full content area. **View → Show Logs** (Ctrl+L) hides/shows it, exposing the renderer log panel underneath.

### IPC API (`window.electronAPI`)

| Method | Direction | Purpose |
|--------|-----------|---------|
| `onStatus(cb)` | main → renderer | Lifecycle state changes |
| `onLog(cb)` | main → renderer | Docker log lines |
| `retryDocker()` | renderer → main | Re-run lifecycle after error |
| `copyLogs(text)` | renderer → main | Write log text to clipboard |
| `openBrowser(url)` | renderer → main | `shell.openExternal` for external links |

### Lifecycle

```
checking-docker → (starting-docker) → pulling → starting → ready → stopping
                                                                ↑
                                               error-{no-docker, no-compose, daemon, start-failed}
```

### Dev vs packaged

`IS_DEV = !app.isPackaged` — `true` when running `electron .` from source.

| | Dev | Packaged |
|---|---|---|
| Image ref | `zotero-semantic-search-dev:latest` | `ghcr.io/…:vYEAR.N` (from `app.getVersion()`) |
| Compose | `build: context: <repo-root>` | `image:` only |
| Start | `up --build -d` (layer cache = fast restarts) | `pull` + `up -d` |

## Docker image

Base: `debian:bookworm-slim`. Layers from most to least stable:

1. System packages (`libgomp1`, `iptables`, `curl`)
2. Ollama install + model bake (~4 GB)
3. pixi + Python deps
4. fastembed model bake (~270 MB)
5. App source code

`entrypoint.sh` optionally applies iptables egress blocking (`DISABLE_NETWORK_ISOLATION=1` skips it), starts Ollama, waits for readiness, then starts uvicorn.

## CI/CD

Both workflows trigger on `v*` tag pushes (and `workflow_dispatch`).

### `docker-publish.yml`
- Parallel builds for `linux/amd64` and `linux/arm64`, pushed by digest
- Merged into a multi-arch manifest with tags `vYEAR.N.PATCH` and `vYEAR.N`

### `desktop-build.yml`
- Stamps `desktop/package.json` from the git tag (`npm version X.Y.Z --no-git-tag-version`)
- Native runners: macOS → universal DMG, Windows → NSIS x64, Linux → AppImage x64
- electron-builder uploads artifacts to the GitHub Release draft via `GH_TOKEN`

### Release flow

```
/release  →  git push origin vYEAR.N.PATCH
               ├── docker-publish  →  ghcr.io/…:vYEAR.N.PATCH + vYEAR.N
               └── desktop-build  →  GitHub Release draft (.dmg / .exe / .AppImage)
```
