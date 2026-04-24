'use strict';

// ── Which panel is this window showing? ───────────────────────────────────────
// Main window: no param → 'splash'
// Logs window: ?panel=logs
// Monitor window: ?panel=monitor
const panelMode = new URLSearchParams(location.search).get('panel') || 'splash';

const splashPanel  = document.getElementById('splash-panel');
const logsPanel    = document.getElementById('logs-panel');
const monitorPanel = document.getElementById('monitor-panel');

function showPanel(name) {
  splashPanel.classList.toggle('hidden',  name !== 'splash');
  logsPanel.classList.toggle('hidden',    name !== 'logs');
  monitorPanel.classList.toggle('hidden', name !== 'monitor');
}

showPanel(panelMode);

// ── Splash panel elements ─────────────────────────────────────────────────────
const spinner      = document.getElementById('spinner');
const iconEl       = document.getElementById('status-icon');
const msgEl        = document.getElementById('status-message');
const detailEl     = document.getElementById('status-detail');
const splashRetry  = document.getElementById('splash-retry');
const splashDocker = document.getElementById('splash-docker');

// ── Logs panel elements ───────────────────────────────────────────────────────
const errorBar    = document.getElementById('error-bar');
const errorMsgEl  = document.getElementById('error-msg');
const logsRetry   = document.getElementById('logs-retry');
const logsDocker  = document.getElementById('logs-docker');
const logEl       = document.getElementById('log');
const copyBtn     = document.getElementById('copy-btn');

// ── Monitor panel elements ────────────────────────────────────────────────────
const mCpu      = document.getElementById('m-cpu');
const mMem      = document.getElementById('m-mem');
const cpuBarsEl = document.getElementById('cpu-bars');

// ── Status handler ────────────────────────────────────────────────────────────
const STATES = {
  'checking-docker':    { msg: 'Checking Docker...',              detail: '',                                        busy: true  },
  'starting-docker':    { msg: 'Starting Docker Desktop...',      detail: 'Waiting up to 60 seconds',               busy: true  },
  'pulling':            { msg: 'Pulling latest image...',         detail: '~5–6 GB download on first run',           busy: true  },
  'starting':           { msg: 'Starting container...',           detail: 'Waiting for service to be ready',         busy: true  },
  'stopping':           { msg: 'Stopping...',                     detail: '',                                        busy: true  },
  'error-no-docker':    { msg: 'Docker is not installed',         detail: 'Install Docker Desktop and click Retry',  busy: false },
  'error-no-compose':   { msg: 'Docker Compose is unavailable',   detail: 'Update Docker Desktop and click Retry',   busy: false },
  'error-daemon':       { msg: 'Docker failed to start',          detail: 'Start Docker Desktop manually and retry', busy: false },
  'error-start-failed': { msg: 'Failed to start',                 detail: 'See logs for details',                    busy: false },
};

window.electronAPI.onStatus(({ state, message, detail }) => {
  const s       = STATES[state] || { msg: state, detail: '', busy: true };
  const isError = state.startsWith('error');
  const isDlErr = state === 'error-no-docker' || state === 'error-no-compose';

  if (panelMode === 'splash') {
    msgEl.textContent    = message || s.msg;
    detailEl.textContent = detail  || s.detail;
    spinner.classList.toggle('hidden', isError || !s.busy);
    iconEl.classList.toggle('hidden', !isError);
    if (isError) iconEl.textContent = '✗';
    splashRetry.classList.toggle('hidden',  !isError);
    splashDocker.classList.toggle('hidden', !isDlErr);
  }

  if (panelMode === 'logs') {
    errorBar.classList.toggle('hidden', !isError);
    if (isError) {
      errorMsgEl.textContent = message || s.msg;
      logsRetry.classList.toggle('hidden',  false);
      logsDocker.classList.toggle('hidden', !isDlErr);
    }
  }
});

// ── Log — append lines, auto-scroll ──────────────────────────────────────────
const MAX_LINES = 800;
const logLines  = [];

function formatLine(line) {
  const s = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  if (s.startsWith('$ ') || s.startsWith('\n$ '))  return `<span class="cmd">${s}</span>`;
  if (/^(Warning|Timed out)/i.test(s))             return `<span class="warn">${s}</span>`;
  if (/^(Fatal|Error|✗)/i.test(s))                 return `<span class="err">${s}</span>`;
  return s;
}

window.electronAPI.onLog((line) => {
  logLines.push(line);
  if (logLines.length > MAX_LINES) logLines.shift();
  logEl.innerHTML = logLines.map(formatLine).join('\n');
  logEl.scrollTop = logEl.scrollHeight;
});

// ── Monitor — container stats + per-CPU bars ──────────────────────────────────
window.electronAPI.onStats(({ cpu, mem, cpus }) => {
  mCpu.textContent = cpu;
  mMem.textContent = mem;
  if (cpus) updateCpuBars(cpus);
});

function updateCpuBars(percents) {
  if (cpuBarsEl.children.length !== percents.length) {
    cpuBarsEl.innerHTML = percents.map((_, i) =>
      `<div class="cpu-bar-row">` +
      `<span class="cpu-bar-label">${i}</span>` +
      `<div class="cpu-bar-track"><div class="cpu-bar-fill" id="cpu-bar-${i}"></div></div>` +
      `<span class="cpu-bar-pct" id="cpu-pct-${i}">0%</span>` +
      `</div>`
    ).join('');
  }
  percents.forEach((pct, i) => {
    const fill  = document.getElementById(`cpu-bar-${i}`);
    const label = document.getElementById(`cpu-pct-${i}`);
    if (fill)  fill.style.width  = `${pct}%`;
    if (label) label.textContent = `${pct}%`;
  });
}

// ── Buttons ───────────────────────────────────────────────────────────────────
splashRetry.addEventListener('click',  () => window.electronAPI.retryDocker());
splashDocker.addEventListener('click', () =>
  window.electronAPI.openBrowser('https://www.docker.com/products/docker-desktop/'));

logsRetry.addEventListener('click',  () => window.electronAPI.retryDocker());
logsDocker.addEventListener('click', () =>
  window.electronAPI.openBrowser('https://www.docker.com/products/docker-desktop/'));

copyBtn.addEventListener('click', () => {
  window.electronAPI.copyLogs(logLines.join('\n')).then(() => {
    copyBtn.textContent = 'Copied!';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
  });
});
