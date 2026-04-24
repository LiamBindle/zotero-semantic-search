'use strict';

const { app, BrowserWindow, ipcMain, shell, clipboard, Menu, WebContentsView } = require('electron');
const { spawnSync, spawn } = require('child_process');
const path  = require('path');
const fs    = require('fs');
const os    = require('os');
const http  = require('http');

// ── Docker PATH fix (macOS) ──────────────────────────────────────────────────
// Docker Desktop on macOS installs docker to locations not on the GUI app PATH.
const EXTRA_PATH = [
  '/usr/local/bin',
  '/usr/bin',
  path.join(os.homedir(), '.docker', 'bin'),
  '/Applications/Docker.app/Contents/Resources/bin',
].join(':');

function dockerEnv() {
  return {
    ...process.env,
    PATH: process.platform === 'win32'
      ? process.env.PATH
      : `${EXTRA_PATH}:${process.env.PATH || ''}`,
  };
}

// ── State ────────────────────────────────────────────────────────────────────
let mainWindow     = null;
let composePath    = null;
let isShuttingDown = false;
let appView        = null;
let appViewShowing = false;
let logsMenuItem   = null;
let bridgeServer   = null;
let bridgePort     = null;

const APP_URL       = 'http://localhost:8000';
const POLL_INTERVAL = 2000;
const POLL_TIMEOUT  = 3 * 60 * 1000;

// In a packaged build, pin to the vX.Y image matching the app's minor version.
// In dev (electron . from source), use a locally-built image instead.
const IS_DEV = !app.isPackaged;

function getImageRef() {
  if (IS_DEV) return 'zotero-semantic-search-dev:latest';
  const [maj, min] = app.getVersion().split('.');
  return `ghcr.io/liambindle/zotero-semantic-search:v${maj}.${min}`;
}

// ── Logging ──────────────────────────────────────────────────────────────────
// Strip ANSI escape codes (docker pull emits colour/cursor sequences)
function stripAnsi(str) {
  return str.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '');
}

function sendLog(text) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  // Docker pull uses \r to overwrite progress lines; keep only the last segment
  const clean = stripAnsi(text.toString())
    .split('\r').pop()
    .replace(/\n+$/, '');
  if (clean) mainWindow.webContents.send('log', clean);
}

function logCmd(label) {
  sendLog(`\n$ ${label}`);
}

// ── docker-compose.yml generation ────────────────────────────────────────────
function generateComposeFile() {
  const zoteroPath = path.join(os.homedir(), 'Zotero').replace(/\\/g, '/');
  const imageRef   = getImageRef();

  // Dev: point compose at the local Dockerfile so the image is always built
  // from source. Release: pull the pinned vX.Y image from GHCR.
  const serviceHeader = IS_DEV
    ? [
        '  zotero-search:',
        `    build:`,
        `      context: "${path.join(__dirname, '..', '..').replace(/\\/g, '/')}"`,
        `    image: ${imageRef}`,
      ]
    : [
        '  zotero-search:',
        `    image: ${imageRef}`,
      ];

  const content = [
    'services:',
    ...serviceHeader,
    '    ports:',
    '      - "8000:8000"',
    '    volumes:',
    `      - "${zoteroPath}:/zotero:ro"`,
    '      - chroma-data:/data/chroma',
    '    environment:',
    '      - DISABLE_NETWORK_ISOLATION=1',
    '    restart: unless-stopped',
    '',
    'volumes:',
    '  chroma-data:',
    '',
  ].join('\n');

  const dest = path.join(app.getPath('userData'), 'docker-compose.yml');
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, content, 'utf8');
  return dest;
}

// ── Docker checks ─────────────────────────────────────────────────────────────
function checkDockerInstalled() {
  logCmd('docker --version');
  const r = spawnSync('docker', ['--version'], { env: dockerEnv(), timeout: 5000, encoding: 'utf8' });
  if (r.stdout) sendLog(r.stdout.trim());
  if (r.stderr) sendLog(r.stderr.trim());
  if (r.error)  sendLog(`Error: ${r.error.message}`);
  return r.status === 0;
}

function checkDockerComposeInstalled() {
  logCmd('docker compose version');
  const r = spawnSync('docker', ['compose', 'version'], { env: dockerEnv(), timeout: 5000, encoding: 'utf8' });
  if (r.stdout) sendLog(r.stdout.trim());
  if (r.stderr) sendLog(r.stderr.trim());
  return r.status === 0;
}

function isDaemonRunning() {
  const r = spawnSync('docker', ['info', '--format', '{{.ServerVersion}}'], {
    env: dockerEnv(), timeout: 10000, encoding: 'utf8',
  });
  return r.status === 0;
}

function isImagePresent() {
  const r = spawnSync('docker', ['image', 'inspect', getImageRef()], {
    env: dockerEnv(), timeout: 10000, encoding: 'utf8',
  });
  return r.status === 0;
}

function tryStartDockerDesktop() {
  sendLog('Attempting to start Docker Desktop...');
  try {
    if (process.platform === 'darwin') {
      spawn('open', ['-a', 'Docker'], { env: dockerEnv(), detached: true });
    } else if (process.platform === 'win32') {
      spawn('cmd', ['/c', 'start', '', 'C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe'],
        { env: dockerEnv(), detached: true, shell: true });
    }
    // Linux: Docker daemon is a system service — user must start it manually
  } catch (e) {
    sendLog(`Could not auto-start Docker Desktop: ${e.message}`);
  }
}

async function waitForDaemon(timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (isDaemonRunning()) return true;
    sendLog('Waiting for Docker daemon...');
    await new Promise(r => setTimeout(r, 3000));
  }
  return false;
}

// ── Compose runner ────────────────────────────────────────────────────────────
function runCompose(args) {
  return new Promise((resolve, reject) => {
    logCmd(`docker compose ${args.join(' ')}`);
    const proc = spawn('docker', ['compose', '-f', composePath, ...args], {
      env: dockerEnv(),
    });
    let combinedOutput = '';
    const handle = d => {
      const s = d.toString();
      combinedOutput += s;
      sendLog(s);
    };
    proc.stdout.on('data', handle);
    proc.stderr.on('data', handle);
    proc.on('close', code => {
      if (code === 0) { resolve(); return; }
      if (combinedOutput.includes('manifest unknown')) {
        reject(new Error('Image not found in registry (manifest unknown). The image may still be building — try again in a few minutes.'));
      } else {
        reject(new Error(`exited with code ${code}`));
      }
    });
  });
}

// ── Readiness polling ─────────────────────────────────────────────────────────
function pollUntilReady() {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const attempt = () => {
      http.get(APP_URL, (res) => {
        res.resume();
        if (res.statusCode >= 200 && res.statusCode < 400) {
          sendLog(`Service responded with HTTP ${res.statusCode} — ready.`);
          resolve();
          return;
        }
        scheduleNext();
      }).on('error', scheduleNext);
    };
    const scheduleNext = () => {
      if (Date.now() - start >= POLL_TIMEOUT) {
        reject(new Error('Timed out waiting for service to respond'));
        return;
      }
      setTimeout(attempt, POLL_INTERVAL);
    };
    attempt();
  });
}

// ── Status helper ─────────────────────────────────────────────────────────────
function sendStatus(state, message, detail) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('status', { state, message, detail });
  }
}

// ── App view (WebContentsView for the running service) ────────────────────────
function updateAppViewBounds() {
  if (!appView || !mainWindow || mainWindow.isDestroyed()) return;
  const [w, h] = mainWindow.getContentSize();
  appView.setBounds({ x: 0, y: 0, width: w, height: h });
}

function launchApp() {
  mainWindow.setSize(1280, 860);
  mainWindow.center();

  if (appView) {
    mainWindow.contentView.removeChildView(appView);
    appView = null;
  }

  appView = new WebContentsView();
  mainWindow.contentView.addChildView(appView);
  updateAppViewBounds();
  // Re-sync bounds after the WM has processed the resize request
  setTimeout(updateAppViewBounds, 200);

  appView.webContents.loadURL(`${APP_URL}/?__electron=1&__bridge=${bridgePort}`);
  appViewShowing = true;

  if (logsMenuItem) {
    logsMenuItem.enabled = true;
    logsMenuItem.checked = false;
  }

  sendStatus('ready', 'Ready');
}

// ── Main lifecycle ────────────────────────────────────────────────────────────
async function runLifecycle() {
  sendLog(`Platform: ${process.platform}  arch: ${process.arch}`);
  sendLog(`PATH: ${dockerEnv().PATH}`);

  // 1. Docker binary
  sendStatus('checking-docker', 'Checking Docker installation...');
  if (!checkDockerInstalled()) {
    sendStatus('error-no-docker', 'Docker is not installed',
      'Install Docker Desktop to continue.');
    return;
  }

  // 2. Docker Compose subcommand
  if (!checkDockerComposeInstalled()) {
    sendStatus('error-no-compose', 'Docker Compose is not available',
      'Update Docker Desktop to a version that includes Compose.');
    return;
  }

  // 3. Docker daemon
  sendStatus('checking-docker', 'Connecting to Docker daemon...');
  logCmd('docker info');
  if (!isDaemonRunning()) {
    sendStatus('starting-docker', 'Starting Docker Desktop...',
      'Waiting up to 60 seconds');
    tryStartDockerDesktop();
    const started = await waitForDaemon(60000);
    if (!started) {
      sendLog('Timed out — Docker daemon did not start within 60 s.');
      sendStatus('error-daemon', 'Docker failed to start',
        'Start Docker Desktop manually and click Retry.');
      return;
    }
  }
  sendLog('Docker daemon is running.');

  if (IS_DEV) {
    // Dev: build from source every time; Docker layer cache keeps this fast
    // when nothing has changed.
    sendStatus('starting', 'Building container from source...');
    await runCompose(['up', '--build', '-d']);
  } else {
    // Release: pull latest patch for the pinned vX.Y image, then start.
    const imageExists = isImagePresent();
    sendStatus('pulling',
      imageExists ? 'Checking for updates...' : 'Downloading image (~5–6 GB)...',
      imageExists ? '' : 'This will take a few minutes on first run');
    try {
      await runCompose(['pull']);
    } catch (err) {
      if (!imageExists) throw new Error(`Image download failed: ${err.message}`);
      sendLog(`Warning: pull failed (${err.message}) — continuing with existing image.`);
      await new Promise(r => setTimeout(r, 1500));
    }

    sendStatus('starting', 'Starting container...');
    await runCompose(['up', '-d']);
  }

  // Wait for HTTP
  sendStatus('starting', 'Waiting for service...', 'This may take 30–60 seconds');
  sendLog(`Polling ${APP_URL}...`);
  await pollUntilReady();

  launchApp();
}

// ── Application menu ──────────────────────────────────────────────────────────
function setupMenu() {
  const template = [];

  if (process.platform === 'darwin') {
    template.push({
      label: app.getName(),
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    });
  }

  template.push({
    label: 'View',
    submenu: [
      {
        id: 'toggle-logs',
        label: 'Show Logs',
        type: 'checkbox',
        checked: false,
        enabled: false,
        accelerator: 'CmdOrCtrl+L',
        click(item) {
          // item.checked is already toggled; true = show logs, false = show app
          appViewShowing = !item.checked;
          if (appView) appView.setVisible(appViewShowing);
        },
      },
    ],
  });

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
  logsMenuItem = menu.getMenuItemById('toggle-logs');
}

// ── Window ────────────────────────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 500,
    height: 560,
    minWidth: 400,
    minHeight: 400,
    resizable: true,
    fullscreenable: false,
    title: 'Zotero Semantic Search',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.on('resize', updateAppViewBounds);
}

// ── IPC ───────────────────────────────────────────────────────────────────────
ipcMain.handle('open-browser', (_event, url) => {
  if (url) shell.openExternal(url);
});

ipcMain.handle('retry-docker', () => {
  if (appView) appView.setVisible(false);
  if (logsMenuItem) {
    logsMenuItem.enabled = false;
    logsMenuItem.checked = false;
  }
  runLifecycle().catch(err => {
    sendLog(`Fatal: ${err.message}`);
    sendStatus('error-start-failed', 'Failed to start', err.message);
  });
});

ipcMain.handle('copy-logs', (_event, text) => {
  clipboard.writeText(text);
});

// ── Bridge server (open-file for the WebContentsView) ─────────────────────────
// A tiny HTTP server on a spare loopback port. The page fetches it directly —
// no IPC, no preload, no custom scheme. Port is passed via ?__bridge=N in URL.
function startBridgeServer() {
  return new Promise((resolve) => {
    const zoteroBase = path.join(os.homedir(), 'Zotero');
    bridgeServer = http.createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*');
      const filePath = new URL(req.url, 'http://127.0.0.1').searchParams.get('path') || '';
      if (filePath.startsWith('/zotero/')) {
        const hostPath = path.resolve(
          path.join(zoteroBase, filePath.slice('/zotero/'.length))
        );
        if (hostPath.startsWith(zoteroBase + path.sep)) {
          shell.openPath(hostPath).then(err => {
            if (err) sendLog(`Warning: could not open file: ${err}`);
          });
        }
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    });
    bridgeServer.on('error', err => sendLog(`Bridge server error: ${err.message}`));
    bridgeServer.listen(0, '127.0.0.1', () => {
      bridgePort = bridgeServer.address().port;
      resolve();
    });
  });
}

// ── App events ────────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  await startBridgeServer();
  setupMenu();
  composePath = generateComposeFile();
  createWindow();
  mainWindow.webContents.once('did-finish-load', () => {
    runLifecycle().catch(err => {
      sendLog(`Fatal: ${err.message}`);
      sendStatus('error-start-failed', 'Failed to start', err.message);
    });
  });
});

app.on('before-quit', (event) => {
  if (isShuttingDown) return;
  isShuttingDown = true;
  event.preventDefault();
  sendStatus('stopping', 'Stopping container...');
  logCmd('docker compose down');
  const r = spawnSync('docker', ['compose', '-f', composePath, 'down'], {
    env: dockerEnv(), timeout: 30000, encoding: 'utf8',
  });
  if (r.stdout) sendLog(r.stdout.trim());
  if (r.stderr) sendLog(r.stderr.trim());
  if (bridgeServer) bridgeServer.close();
  app.quit();
});

app.on('window-all-closed', () => {
  app.quit();
});
