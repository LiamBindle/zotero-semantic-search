'use strict';

const msgEl      = document.getElementById('status-message');
const detailEl   = document.getElementById('status-detail');
const spinner    = document.getElementById('spinner');
const iconEl     = document.getElementById('status-icon');
const openBtn    = document.getElementById('open-btn');
const retryBtn   = document.getElementById('retry-btn');
const dockerLink = document.getElementById('docker-link');
const logEl      = document.getElementById('log');
const copyBtn    = document.getElementById('copy-btn');

const STATES = {
  'checking-docker':    { msg: 'Checking Docker...',              detail: '',                                        icon: null, busy: true  },
  'starting-docker':    { msg: 'Starting Docker Desktop...',      detail: 'Waiting up to 60 seconds',               icon: null, busy: true  },
  'pulling':            { msg: 'Pulling latest image...',         detail: '~5–6 GB download on first run',           icon: null, busy: true  },
  'starting':           { msg: 'Starting container...',           detail: 'Waiting for service to be ready',         icon: null, busy: true  },
  'ready':              { msg: 'Ready',                           detail: '',                                        icon: '✓',  busy: false },
  'stopping':           { msg: 'Stopping...',                     detail: '',                                        icon: null, busy: true  },
  'error-no-docker':    { msg: 'Docker is not installed',         detail: 'Install Docker Desktop and click Retry',  icon: '✗',  busy: false },
  'error-no-compose':   { msg: 'Docker Compose is unavailable',   detail: 'Update Docker Desktop and click Retry',   icon: '✗',  busy: false },
  'error-daemon':       { msg: 'Docker failed to start',          detail: 'Start Docker Desktop manually and retry', icon: '✗',  busy: false },
  'error-start-failed': { msg: 'Failed to start',                 detail: 'See logs below for details',              icon: '✗',  busy: false },
};

window.electronAPI.onStatus(({ state, message, detail }) => {
  const s = STATES[state] || { msg: state, detail: '', icon: null, busy: true };
  msgEl.textContent    = message || s.msg;
  detailEl.textContent = detail  || s.detail;

  spinner.classList.toggle('hidden', !s.busy);
  iconEl.classList.toggle('hidden', !s.icon);
  if (s.icon) iconEl.textContent = s.icon;

  openBtn.classList.toggle('hidden',    state !== 'ready');
  retryBtn.classList.toggle('hidden',   !state.startsWith('error'));
  dockerLink.classList.toggle('hidden',
    state !== 'error-no-docker' && state !== 'error-no-compose');
});

// Log panel — append lines, auto-scroll
const MAX_LINES = 800;
const logLines  = [];

window.electronAPI.onLog((line) => {
  logLines.push(line);
  if (logLines.length > MAX_LINES) logLines.shift();
  logEl.textContent = logLines.join('\n');
  logEl.scrollTop   = logEl.scrollHeight;
});

// Buttons
openBtn.addEventListener('click',  () => window.electronAPI.openBrowser());
retryBtn.addEventListener('click', () => window.electronAPI.retryDocker());
dockerLink.addEventListener('click', () =>
  window.electronAPI.openBrowser('https://www.docker.com/products/docker-desktop/'));

copyBtn.addEventListener('click', () => {
  window.electronAPI.copyLogs(logLines.join('\n')).then(() => {
    copyBtn.textContent = 'Copied!';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
  });
});
