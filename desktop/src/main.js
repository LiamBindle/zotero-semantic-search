'use strict';

const { app, BrowserWindow, ipcMain, shell, clipboard, Menu, WebContentsView, dialog } = require('electron');
const { spawnSync, spawn } = require('child_process');
const path  = require('path');
const fs    = require('fs');
const os    = require('os');
const http  = require('http');

// ── Docker PATH fix (macOS) ──────────────────────────────────────────────────
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
let mainWindow        = null;
let logsWindow        = null;
let monitorWindow     = null;
let composePath       = null;
let isShuttingDown    = false;
let appView           = null;
let bridgeServer      = null;
let bridgePort        = null;
let statsProc         = null;
let statsTimer        = null;
let prevCpuSample     = null;
let dockerRestartItem = null;
let dockerResetItem   = null;

const logBuffer  = [];   // rolling buffer replayed to newly-opened log windows
let   lastStatus = null; // replayed to newly-opened log windows

const APP_URL       = 'http://localhost:8765';
const POLL_INTERVAL = 2000;
const POLL_TIMEOUT  = 3 * 60 * 1000;

const IS_DEV = !app.isPackaged;

function getImageRef() {
  if (IS_DEV) return 'zotero-semantic-search-dev:latest';
  const [maj, min] = app.getVersion().split('.');
  return `ghcr.io/liambindle/zotero-semantic-search:v${maj}.${min}`;
}

// ── Logging ──────────────────────────────────────────────────────────────────
function stripAnsi(str) {
  return str.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '');
}

function sendLog(text) {
  const clean = stripAnsi(text.toString())
    .split('\r').pop()
    .replace(/\n+$/, '');
  if (!clean) return;
  logBuffer.push(clean);
  if (logBuffer.length > 800) logBuffer.shift();
  broadcastToRenderers('log', clean);
}

function logCmd(label) {
  sendLog(`\n$ ${label}`);
}

// ── Broadcast to all renderer windows ────────────────────────────────────────
function broadcastToRenderers(channel, data) {
  for (const win of [mainWindow, logsWindow, monitorWindow]) {
    if (win && !win.isDestroyed()) win.webContents.send(channel, data);
  }
}

// ── Status helper ─────────────────────────────────────────────────────────────
function sendStatus(state, message, detail) {
  lastStatus = { state, message, detail };
  broadcastToRenderers('status', { state, message, detail });
  if (state.startsWith('error')) openLogsWindow();
}

// ── Logs / Monitor windows ────────────────────────────────────────────────────
function openLogsWindow() {
  if (logsWindow && !logsWindow.isDestroyed()) {
    logsWindow.focus();
    return;
  }
  logsWindow = new BrowserWindow({
    width: 560,
    height: 420,
    minWidth: 400,
    minHeight: 300,
    title: 'Logs — Zotero Semantic Search',
    backgroundColor: '#f3f4f6',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  logsWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'), {
    query: { panel: 'logs' },
  });
  logsWindow.webContents.once('did-finish-load', () => {
    logBuffer.forEach(line => logsWindow.webContents.send('log', line));
    if (lastStatus) logsWindow.webContents.send('status', lastStatus);
  });
  logsWindow.on('closed', () => { logsWindow = null; });
}

function openMonitorWindow() {
  if (monitorWindow && !monitorWindow.isDestroyed()) {
    monitorWindow.focus();
    return;
  }
  monitorWindow = new BrowserWindow({
    width: 380,
    height: 500,
    minWidth: 300,
    minHeight: 300,
    title: 'Monitor — Zotero Semantic Search',
    backgroundColor: '#f3f4f6',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  monitorWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'), {
    query: { panel: 'monitor' },
  });
  // Start polling; if container isn't up yet docker stats finds no match
  startStatsStream();
  monitorWindow.on('closed', () => { monitorWindow = null; stopStatsStream(); });
}

// ── docker-compose.yml generation ────────────────────────────────────────────
function generateComposeFile() {
  const zoteroPath = path.join(os.homedir(), 'Zotero').replace(/\\/g, '/');
  const imageRef   = getImageRef();

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
    '      - "8765:8765"',
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
  if (r.error) {
    if (r.error.code === 'ENOENT') {
      sendLog('Error: Docker not found. Install Docker Desktop and make sure it is in your PATH.');
      sendLog(`Searched PATH: ${dockerEnv().PATH}`);
    } else {
      sendLog(`Error: ${r.error.message}`);
    }
    return false;
  }
  if (r.stdout) sendLog(r.stdout.trim());
  if (r.stderr) sendLog(r.stderr.trim());
  if (r.status !== 0) sendLog(`Error: docker --version exited with code ${r.status}`);
  return r.status === 0;
}

function checkDockerComposeInstalled() {
  logCmd('docker compose version');
  const r = spawnSync('docker', ['compose', 'version'], { env: dockerEnv(), timeout: 5000, encoding: 'utf8' });
  if (r.error) {
    sendLog(`Error: ${r.error.message}`);
    return false;
  }
  if (r.stdout) sendLog(r.stdout.trim());
  if (r.stderr) sendLog(r.stderr.trim());
  if (r.status !== 0) {
    sendLog('Error: Docker Compose plugin not available. Update Docker Desktop to a version that bundles Compose.');
  }
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
  setTimeout(updateAppViewBounds, 200);

  appView.webContents.loadURL(`${APP_URL}/?__electron=1&__bridge=${bridgePort}`);

  setDockerMenuEnabled(true);

  // Resume stats polling if the monitor window is already open
  if (monitorWindow && !monitorWindow.isDestroyed()) startStatsStream();

  sendStatus('ready', 'Ready');
}

// ── Main lifecycle ────────────────────────────────────────────────────────────
async function runLifecycle() {
  sendLog(`Platform: ${process.platform}  arch: ${process.arch}`);
  sendLog(`PATH: ${dockerEnv().PATH}`);

  sendStatus('checking-docker', 'Checking Docker installation...');
  if (!checkDockerInstalled()) {
    sendLog('✗ Docker check failed. See above for details.');
    sendStatus('error-no-docker', 'Docker is not installed',
      'Install Docker Desktop and click Retry.');
    return;
  }
  sendLog('✓ Docker found.');

  if (!checkDockerComposeInstalled()) {
    sendLog('✗ Docker Compose check failed. See above for details.');
    sendStatus('error-no-compose', 'Docker Compose is not available',
      'Update Docker Desktop and click Retry.');
    return;
  }
  sendLog('✓ Docker Compose found.');

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
    sendStatus('starting', 'Building container from source...');
    await runCompose(['up', '--build', '-d']);
  } else {
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

  sendStatus('starting', 'Waiting for service...', 'This may take 30–60 seconds');
  sendLog(`Polling ${APP_URL}...`);
  await pollUntilReady();

  launchApp();
}

// ── Stats (polls --no-stream every 2 s; avoids streaming \r issues) ───────────
function getCpuPercents() {
  const cpus = os.cpus();
  if (!prevCpuSample) { prevCpuSample = cpus; return null; }
  const result = cpus.map((cpu, i) => {
    const prev  = prevCpuSample[i];
    const delta = k => cpu.times[k] - prev.times[k];
    const total = delta('user') + delta('nice') + delta('sys') + delta('idle') + delta('irq');
    return total > 0 ? Math.round((total - delta('idle')) / total * 100) : 0;
  });
  prevCpuSample = cpus;
  return result;
}

function startStatsStream() {
  if (statsTimer) return;
  prevCpuSample = null;

  const tick = () => {
    if (statsProc) return; // previous snapshot still in flight
    statsProc = spawn('docker', ['stats', '--no-stream', '--format', '{{json .}}'], {
      env: dockerEnv(),
    });
    let out = '';
    statsProc.stdout.on('data', d => { out += d.toString(); });
    statsProc.on('close', () => {
      statsProc = null;
      if (!statsTimer) return; // stopped while snapshot was running
      for (const line of out.trim().split('\n')) {
        if (!line.trim()) continue;
        try {
          const d = JSON.parse(line);
          const name = d.Name || d.Container || '';
          if (!name.includes('zotero-search')) continue;
          broadcastToRenderers('stats', {
            cpu: d.CPUPerc, mem: d.MemUsage,
            cpus: getCpuPercents(),
          });
        } catch {}
      }
    });
  };

  tick();
  statsTimer = setInterval(tick, 2000);
}

function stopStatsStream() {
  if (statsTimer) { clearInterval(statsTimer); statsTimer = null; }
  if (statsProc)  { statsProc.kill(); statsProc = null; }
}

// ── Docker menu actions ───────────────────────────────────────────────────────
function setDockerMenuEnabled(on) {
  if (dockerRestartItem) dockerRestartItem.enabled = on;
  if (dockerResetItem)   dockerResetItem.enabled   = on;
}

function hideViewPanels() {
  if (appView) appView.setVisible(false);
}

async function restartContainer() {
  setDockerMenuEnabled(false);
  stopStatsStream();
  hideViewPanels();
  sendStatus('starting', 'Restarting container...');
  await runCompose(['restart']);
  sendStatus('starting', 'Waiting for service...');
  await pollUntilReady();
  launchApp();
}

async function resetData() {
  const { response } = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['Cancel', 'Reset'],
    defaultId: 0,
    cancelId: 0,
    title: 'Reset search index',
    message: 'Delete the search index?',
    detail: 'This permanently deletes all indexed vectors. Your Zotero library files are not affected, but you will need to re-index from scratch.',
  });
  if (response === 0) return;

  setDockerMenuEnabled(false);
  stopStatsStream();
  hideViewPanels();
  sendStatus('stopping', 'Removing container and data...');
  await runCompose(['down', '-v']);
  runLifecycle().catch(err => {
    sendLog(`Fatal: ${err.message}`);
    sendStatus('error-start-failed', 'Failed to start', err.message);
  });
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
    label: 'Docker',
    submenu: [
      {
        id: 'docker-restart',
        label: 'Restart Container',
        enabled: false,
        click() {
          restartContainer().catch(err => {
            sendLog(`Fatal: ${err.message}`);
            sendStatus('error-start-failed', 'Restart failed', err.message);
          });
        },
      },
      { type: 'separator' },
      {
        id: 'docker-reset',
        label: 'Reset Data…',
        enabled: false,
        click() {
          resetData().catch(err => {
            sendLog(`Fatal: ${err.message}`);
            sendStatus('error-start-failed', 'Reset failed', err.message);
          });
        },
      },
    ],
  });

  template.push({
    label: 'View',
    submenu: [
      {
        label: 'Show Logs',
        accelerator: 'CmdOrCtrl+L',
        click() { openLogsWindow(); },
      },
      {
        label: 'Show Monitor',
        accelerator: 'CmdOrCtrl+M',
        click() { openMonitorWindow(); },
      },
    ],
  });

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
  dockerRestartItem = menu.getMenuItemById('docker-restart');
  dockerResetItem   = menu.getMenuItemById('docker-reset');
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
    backgroundColor: '#f3f4f6',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.on('resize', updateAppViewBounds);
  mainWindow.on('closed', () => {
    mainWindow = null;
    if (!isShuttingDown) app.quit();
  });
}

// ── IPC ───────────────────────────────────────────────────────────────────────
ipcMain.handle('open-browser', (_event, url) => {
  if (url) shell.openExternal(url);
});

ipcMain.handle('retry-docker', () => {
  stopStatsStream();
  hideViewPanels();
  setDockerMenuEnabled(false);
  runLifecycle().catch(err => {
    sendLog(`Fatal: ${err.message}`);
    sendStatus('error-start-failed', 'Failed to start', err.message);
  });
});

ipcMain.handle('copy-logs', (_event, text) => {
  clipboard.writeText(text);
});

// ── Bridge server (open-file for the WebContentsView) ─────────────────────────
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
  stopStatsStream();
  app.quit();
});

app.on('window-all-closed', () => {
  app.quit();
});
