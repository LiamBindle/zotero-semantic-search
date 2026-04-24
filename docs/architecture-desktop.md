# Desktop, Docker & CI Architecture

## System overview

```
Electron (main.js)
  ├── bridge HTTP server           → localhost:<random port> (file-open handler)
  ├── generates docker-compose.yml → app.getPath('userData')
  ├── docker compose up            → container on localhost:8000
  └── WebContentsView              → loads http://localhost:8000/?__electron=1&__bridge=<port>
```

## Electron process model

| Process | File | Responsibility |
|---------|------|----------------|
| Main | `desktop/src/main.js` | Docker lifecycle, bridge server, IPC handlers, application menu |
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

### Bridge server (open-file)

The web app runs inside Docker and cannot open host files directly. A small HTTP server starts in the main process on a random loopback port before the app loads. The port is passed to the page via `?__bridge=<port>` in the URL.

When the user clicks "↗ Open file", `index.html` calls:
```
GET http://localhost:<port>/open?path=/zotero/storage/…
```

The bridge server maps `/zotero/` → `~/Zotero/` (with path-traversal guard) and calls `shell.openPath()`, which opens the file in the OS default application on all platforms.

The "↗ Open file" button is only rendered when `?__electron=1` is present in the URL, so it never appears when the app is accessed directly in a browser.

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
