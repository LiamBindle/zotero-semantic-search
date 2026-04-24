# Desktop, Docker & CI Architecture

## System overview

```
Electron (main.js)
  ├── bridge HTTP server           → localhost:<random port> (file-open handler)
  ├── generates docker-compose.yml → app.getPath('userData')
  ├── docker compose up            → container on localhost:8765
  └── WebContentsView              → loads http://localhost:8765/?__electron=1&__bridge=<port>
```

## Electron process model

| Process | File | Responsibility |
|---------|------|----------------|
| Main | `desktop/src/main.js` | Docker lifecycle, stats polling, bridge server, IPC handlers, application menu |
| Preload | `desktop/src/preload.js` | Typed `contextBridge` bridge to renderer windows |
| Renderer | `desktop/src/renderer/` | Splash screen (startup/errors), Logs window, Monitor window |

### Window model

The app uses three `BrowserWindow` instances, all loading the same `renderer/index.html`. A `?panel=` query parameter selects which panel is shown:

| Window | Query param | Shown when |
|--------|-------------|------------|
| Main window | _(none)_ | Always — shows the splash panel during startup, then is covered by the `WebContentsView` once the service is ready |
| Logs window | `?panel=logs` | Opened via **View → Show Logs** (Ctrl+L), or automatically on any error |
| Monitor window | `?panel=monitor` | Opened via **View → Show Monitor** (Ctrl+M) |

`WebContentsView` is added to the main window's content view after the service is ready and fills the full area, showing the web app. The underlying renderer is always present but covered.

### Renderer panels

`renderer.js` reads `new URLSearchParams(location.search).get('panel')` on load to determine its role:

- **splash** (main window) — spinner/status during startup; ✗ icon + retry on error
- **logs** — copy button, optional error bar with retry, scrolling log output
- **monitor** — container CPU/memory stat cards, per-CPU bar chart (host)

Status events (`onStatus`) update the splash panel in the main window and the error bar in any open logs window. Log lines are buffered in main.js (rolling 800-line buffer) and replayed to newly-opened log windows.

### IPC API (`window.electronAPI`)

| Method | Direction | Purpose |
|--------|-----------|---------|
| `onStatus(cb)` | main → renderer | Lifecycle state changes |
| `onLog(cb)` | main → renderer | Docker/lifecycle log lines |
| `onStats(cb)` | main → renderer | Container + host CPU stats (monitor window only) |
| `retryDocker()` | renderer → main | Re-run lifecycle after error |
| `copyLogs(text)` | renderer → main | Write log text to clipboard |
| `openBrowser(url)` | renderer → main | `shell.openExternal` for external links |

### Stats polling

When the Monitor window is open, `startStatsStream()` polls `docker stats --no-stream --format '{{json .}}'` every 2 seconds. The streaming mode is deliberately avoided — it emits `\r`-separated lines which are unreliable when piped. Each poll spawns a fresh process, collects the full output on `close`, then parses and broadcasts the result. Polling stops when the Monitor window closes (`stopStatsStream()` in the `closed` handler).

Fields sent per tick: `{ cpu: string, mem: string, cpus: number[] }` where `cpus` is per-host-CPU utilisation (0–100) sampled via `os.cpus()` delta between ticks.

### Bridge server (open-file)

The web app runs inside Docker and cannot open host files directly. A small HTTP server starts in the main process on a random loopback port before the app loads. The port is passed to the page via `?__bridge=<port>` in the URL.

When the user clicks "↗ Open file", `index.html` calls:
```
GET http://localhost:<port>/open?path=/zotero/storage/…
```

The bridge server maps `/zotero/` → `~/Zotero/` (with path-traversal guard) and calls `shell.openPath()`, which opens the file in the OS default application on all platforms.

The "↗ Open file" button is only rendered when `?__electron=1` is present in the URL, so it never appears when the app is accessed directly in a browser.

### Lifecycle states

```
checking-docker → (starting-docker) → pulling → starting → ready → stopping
                                                                ↑
                                               error-{no-docker, no-compose, daemon, start-failed}
```

On any `error-*` state, the logs window is opened automatically (`openLogsWindow()` is called from `sendStatus`).

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

`entrypoint.sh` optionally applies iptables egress blocking (`DISABLE_NETWORK_ISOLATION=1` skips it), starts Ollama, waits for readiness, then starts uvicorn on port 8765.

## CI/CD

Both workflows trigger on `v*` tag pushes (and `workflow_dispatch`).

### `docker-publish.yml`
- Parallel builds for `linux/amd64` and `linux/arm64`, pushed by digest
- Merged into a multi-arch manifest with tags `vYEAR.N.PATCH` and `vYEAR.N`
- `APP_VERSION` build arg bakes the version string into the image; exposed via `/api/status`

### `desktop-build.yml`
- Stamps `desktop/package.json` from the git tag (`npm version X.Y.Z --no-git-tag-version`)
- Native runners: macOS → universal DMG, Windows → NSIS x64, Linux → AppImage x64
- electron-builder uploads artifacts to the GitHub Release via `GH_TOKEN`

### Release flow

```
/release  →  git push origin vYEAR.N.PATCH
               ├── docker-publish  →  ghcr.io/…:vYEAR.N.PATCH + vYEAR.N
               └── desktop-build  →  GitHub Release (.dmg / .exe / .AppImage)
```
