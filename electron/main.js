/**
 * electron/main.js — Electron main process
 *
 * - Spawns launch.bat (hidden) on startup
 * - Shows loading screen while polling localhost:3000
 * - Frameless 900×700 window with dark title bar
 * - Menu: Jarvis → Show Logs | Restart Backend | Quit
 * - Cleans up child processes on quit
 */

'use strict';

const {
  app,
  BrowserWindow,
  Menu,
  ipcMain,
  shell,
  dialog,
  session,
} = require('electron');
const path    = require('path');
const { spawn, exec } = require('child_process');
const http    = require('http');

// ─── Globals ──────────────────────────────────────────────────────────────────

const PROJECT_ROOT  = path.resolve(__dirname, '..');
const BACKEND_URL   = 'http://localhost:3000';
const POLL_INTERVAL = 500;   // ms
const POLL_TIMEOUT  = 180_000; // ms — 3 minuti per caricare i modelli pesanti

let mainWindow  = null;
let logWindow   = null;
let backendProc = null;
let kiwixProc   = null;
let stdoutLines = [];

// ─── Backend management ───────────────────────────────────────────────────────

function startBackend() {
  // 1. Avvia kiwix-serve come processo indipendente tracciato se il file ZIM esiste
  const kiwixExe = path.join(PROJECT_ROOT, 'kiwix-serve.exe');
  const fs = require('fs');
  let selectedZim = null;
  try {
    const files = fs.readdirSync(PROJECT_ROOT);
    const zimFiles = files.filter(f => f.startsWith('wikipedia_it_all_nopic_') && f.endsWith('.zim'));
    if (zimFiles.length > 0) {
      selectedZim = path.join(PROJECT_ROOT, zimFiles[0]);
    }
  } catch (e) {
    console.error('Error scanning ZIM files:', e);
  }

  if (selectedZim && fs.existsSync(selectedZim)) {
    console.log(`Starting kiwix-serve with ZIM: ${selectedZim}...`);
    kiwixProc = spawn(kiwixExe, ['--port', '8888', selectedZim], {
      cwd: PROJECT_ROOT,
      windowsHide: true,
      stdio: 'ignore'
    });
    kiwixProc.on('error', err => {
      console.error('Failed to spawn kiwix-serve process:', err);
    });
  } else {
    console.warn('Wikipedia ZIM file not found, kiwix-serve skipped.');
  }

  // 3. Avvia il backend Python usando il python.exe del virtualenv
  const pythonExe = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
  const backendMain = path.join(PROJECT_ROOT, 'backend', 'main.py');
  
  console.log(`Starting python backend: ${pythonExe}...`);
  backendProc = spawn(pythonExe, [backendMain], {
    cwd:   PROJECT_ROOT,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  backendProc.stdout.on('data', chunk => {
    const lines = chunk.toString().split(/\r?\n/).filter(Boolean);
    stdoutLines.push(...lines);
    if (stdoutLines.length > 500) stdoutLines.splice(0, stdoutLines.length - 500);
    if (logWindow && !logWindow.isDestroyed()) {
      logWindow.webContents.send('log-line', lines.join('\n'));
    }
  });

  backendProc.stderr.on('data', chunk => {
    const lines = chunk.toString().split(/\r?\n/).filter(Boolean);
    stdoutLines.push(...lines.map(l => `[ERR] ${l}`));
    if (logWindow && !logWindow.isDestroyed()) {
      logWindow.webContents.send('log-line', lines.map(l => `[ERR] ${l}`).join('\n'));
    }
  });

  backendProc.on('close', code => {
    console.log(`Backend exited with code ${code}`);
  });
}

function killBackend() {
  if (backendProc) {
    console.log(`Killing backend process tree PID: ${backendProc.pid}`);
    exec(`taskkill /F /T /PID ${backendProc.pid}`, () => {});
    backendProc = null;
  }
  if (kiwixProc) {
    console.log(`Killing kiwix-serve process tree PID: ${kiwixProc.pid}`);
    exec(`taskkill /F /T /PID ${kiwixProc.pid}`, () => {});
    kiwixProc = null;
  }
}

// ─── Backend polling ──────────────────────────────────────────────────────────

function pollBackend(resolve, reject, start) {
  http.get(BACKEND_URL + '/api/status', res => {
    res.resume(); // Consuma e rilascia il body della response per liberare la connessione e non bloccare i socket
    if (res.statusCode === 200) return resolve();
    retry(resolve, reject, start);
  }).on('error', () => retry(resolve, reject, start));
}

function retry(resolve, reject, start) {
  if (Date.now() - start > POLL_TIMEOUT) {
    return reject(new Error('Backend did not start within timeout.'));
  }
  setTimeout(() => pollBackend(resolve, reject, start), POLL_INTERVAL);
}

function waitForBackend() {
  return new Promise((resolve, reject) => pollBackend(resolve, reject, Date.now()));
}

// ─── Main window ──────────────────────────────────────────────────────────────

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width:  900,
    height: 700,
    minWidth:  800,
    minHeight: 600,
    frame:  false,
    titleBarStyle: 'hidden',
    backgroundColor: '#050810',
    show: false,
    webPreferences: {
      preload:         path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
  });

  // Show loading page first
  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());

  return mainWindow;
}

// ─── Log window ───────────────────────────────────────────────────────────────

function createLogWindow() {
  if (logWindow && !logWindow.isDestroyed()) {
    logWindow.focus();
    return;
  }
  logWindow = new BrowserWindow({
    width:  720,
    height: 480,
    title:  'Jarvis — Backend Logs',
    backgroundColor: '#050810',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  logWindow.loadFile(path.join(__dirname, 'log-window.html'));
  logWindow.on('closed', () => { logWindow = null; });

  logWindow.webContents.once('did-finish-load', () => {
    // Send buffered lines
    if (stdoutLines.length) {
      logWindow.webContents.send('log-line', stdoutLines.join('\n'));
    }
  });
}

// ─── Application menu ─────────────────────────────────────────────────────────

function buildMenu() {
  const template = [
    {
      label: 'Jarvis',
      submenu: [
        {
          label: 'Show Logs',
          accelerator: 'CmdOrCtrl+L',
          click: createLogWindow,
        },
        {
          label: 'Restart Backend',
          click: async () => {
            killBackend();
            startBackend();
            if (!mainWindow || mainWindow.isDestroyed()) return;
            mainWindow.loadFile(path.join(__dirname, 'loading.html'));
            try {
              await waitForBackend();
              if (!mainWindow || mainWindow.isDestroyed()) return;
              mainWindow.loadURL(BACKEND_URL);
            } catch (e) {
              dialog.showErrorBox('Jarvis', 'Backend failed to restart. Check logs.');
            }
          },
        },
        { type: 'separator' },
        {
          label: 'Quit',
          accelerator: 'CmdOrCtrl+Q',
          click: () => app.quit(),
        },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { type: 'separator' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ─── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.on('window-minimize', () => mainWindow && mainWindow.minimize());
ipcMain.on('window-maximize', () => {
  if (!mainWindow) return;
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on('window-close',    () => app.quit());

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  buildMenu();
  
  // Imposta una Content Security Policy (CSP) per bloccare caricamento codice arbitrario ed XSS
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self' http://localhost:3000 ws://localhost:8765; " +
          "script-src 'self' 'unsafe-inline' 'unsafe-eval'; " +
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
          "font-src 'self' https://fonts.gstatic.com; " +
          "img-src 'self' data:; " +
          "connect-src 'self' http://localhost:3000 ws://localhost:8765 ws://localhost:3000;"
        ]
      }
    });
  });

  createMainWindow();

  // Se JARVIS_CONSOLE è settato, il backend è già stato avviato dallo script .bat
  if (!process.env.JARVIS_CONSOLE) {
    console.log('Starting backend from Electron...');
    startBackend();
  } else {
    console.log('Backend managed by external console.');
  }

  try {
    await waitForBackend();
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.loadURL(BACKEND_URL);
  } catch (e) {
    dialog.showErrorBox(
      'Jarvis — Backend Error',
      'The Python backend did not start in time.\n\n' +
      'Assicurati di aver eseguito setup.bat e di aver configurato DEEPSEEK_API_KEY nel file .env.'
    );
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => killBackend());

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});
