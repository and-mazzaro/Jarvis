/**
 * electron/preload.js — Context bridge
 *
 * Exposes a minimal, safe API surface from the main process to renderer pages.
 */

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisIPC', {
  // Window controls (for frameless window)
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close:    () => ipcRenderer.send('window-close'),

  // Log streaming
  onLogLine: (callback) => {
    ipcRenderer.on('log-line', (_event, data) => callback(data));
  },
});
