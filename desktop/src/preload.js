'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  onStatus:    (cb)   => ipcRenderer.on('status', (_e, d)    => cb(d)),
  onLog:       (cb)   => ipcRenderer.on('log',    (_e, line) => cb(line)),
  onStats:     (cb)   => ipcRenderer.on('stats',  (_e, d)    => cb(d)),
  openBrowser: (url)  => ipcRenderer.invoke('open-browser', url),
  retryDocker: ()     => ipcRenderer.invoke('retry-docker'),
  copyLogs:    (text) => ipcRenderer.invoke('copy-logs', text),
});
