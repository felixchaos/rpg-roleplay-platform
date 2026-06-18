'use strict';
// main.js —— Electron 主进程。
// 控制台窗口(起停服务/日志/配置/更新)+ 应用窗口(加载在线或本地的 Web UI)。
// 单实例锁、优雅停机、自动更新全在这里。

const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');

const P = require('./paths');
const cfg = require('./config');
const supervisor = require('./supervisor');

let panelWin = null;
let appWin = null;
let updater = null;     // electron-updater(惰性载入,dev 环境可能未装)

// ── 单实例锁:本机只允许一个服务端,避免抢端口/锁数据目录 ──
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (panelWin) { if (panelWin.isMinimized()) panelWin.restore(); panelWin.focus(); } });
}

function createPanel() {
  panelWin = new BrowserWindow({
    width: 760, height: 620, minWidth: 560, minHeight: 460,
    title: 'Stellatrix 控制台',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,    // preload 需要 require('electron')
    },
  });
  panelWin.loadFile(path.join(__dirname, 'control-panel', 'index.html'));
  panelWin.on('closed', () => { panelWin = null; });

  // 监督器事件 → 控制台渲染层
  const fwd = (channel) => (payload) => { if (panelWin && !panelWin.isDestroyed()) panelWin.webContents.send(channel, payload); };
  supervisor.on('status', fwd('sv:status'));
  supervisor.on('log', fwd('sv:log'));
}

// 打开「应用窗口」(真正的游戏/创作 Web UI)
async function openAppWindow() {
  const c = cfg.load();
  let url;
  if (c.mode === 'local') {
    if (supervisor.state !== 'running') await supervisor.start();
    url = `http://127.0.0.1:${supervisor.backendPort}/`;
  } else {
    url = c.onlineUrl.replace(/\/+$/, '') + '/';
  }
  if (appWin && !appWin.isDestroyed()) { appWin.loadURL(url); appWin.focus(); return url; }
  appWin = new BrowserWindow({
    width: 1280, height: 860, minWidth: 900, minHeight: 600,
    title: 'Stellatrix',
    webPreferences: { partition: 'persist:stellatrix', contextIsolation: true, nodeIntegration: false },
  });
  appWin.loadURL(url);
  appWin.on('closed', () => { appWin = null; });
  return url;
}

// ── 自动更新(仅打包后)──
function initUpdater() {
  if (!app.isPackaged) return;
  try { updater = require('electron-updater').autoUpdater; } catch (_) { return; }
  updater.autoDownload = false;
  updater.channel = cfg.load().updateChannel || 'stable';
  const send = (channel, payload) => panelWin && !panelWin.isDestroyed() && panelWin.webContents.send(channel, payload);
  updater.on('checking-for-update', () => send('upd:status', { state: 'checking' }));
  updater.on('update-available', (i) => send('upd:status', { state: 'available', version: i.version }));
  updater.on('update-not-available', () => send('upd:status', { state: 'none' }));
  updater.on('error', (e) => send('upd:status', { state: 'error', message: String(e && e.message || e) }));
  updater.on('download-progress', (p) => send('upd:status', { state: 'downloading', percent: Math.round(p.percent) }));
  updater.on('update-downloaded', (i) => send('upd:status', { state: 'downloaded', version: i.version }));
}

// ── IPC ──
function wireIpc() {
  ipcMain.handle('app:version', () => app.getVersion());
  ipcMain.handle('sv:status', () => supervisor.snapshot());
  ipcMain.handle('sv:logs', () => supervisor.recentLogs());
  ipcMain.handle('sv:start', async () => { await supervisor.start(); return supervisor.snapshot(); });
  ipcMain.handle('sv:stop', async () => { await supervisor.stop(); return supervisor.snapshot(); });
  ipcMain.handle('sv:restart', async () => { await supervisor.restart(); return supervisor.snapshot(); });

  ipcMain.handle('cfg:get', () => cfg.load());
  ipcMain.handle('cfg:set', (_e, patch) => {
    const safe = { ...patch };
    delete safe.masterKey;                 // 不允许从 UI 改 master key
    return cfg.save(safe);
  });

  ipcMain.handle('app:open', async () => ({ url: await openAppWindow() }));
  ipcMain.handle('app:openExternal', async () => {
    const c = cfg.load();
    let url = c.mode === 'local'
      ? (supervisor.state === 'running' ? `http://127.0.0.1:${supervisor.backendPort}/` : null)
      : c.onlineUrl;
    if (!url) { await supervisor.start(); url = `http://127.0.0.1:${supervisor.backendPort}/`; }
    await shell.openExternal(url);
    return { url };
  });
  ipcMain.handle('sys:openDataDir', () => { shell.openPath(P.userDataRoot()); });
  ipcMain.handle('sys:openLogsDir', () => { shell.openPath(P.logsDir()); });

  ipcMain.handle('upd:check', async () => {
    if (!updater) return { ok: false, reason: '更新仅在打包版可用' };
    try { const r = await updater.checkForUpdates(); return { ok: true, version: r && r.updateInfo && r.updateInfo.version }; }
    catch (e) { return { ok: false, reason: String(e && e.message || e) }; }
  });
  ipcMain.handle('upd:download', async () => { if (updater) await updater.downloadUpdate(); return { ok: !!updater }; });
  ipcMain.handle('upd:install', () => { if (updater) updater.quitAndInstall(); });
}

app.whenReady().then(() => {
  wireIpc();
  createPanel();
  initUpdater();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createPanel(); });
});

// 关窗不退出(控制台是常驻服务管理器);仅当用户显式退出才走停机
app.on('window-all-closed', () => { /* 保持后台,由托盘/再次打开恢复;mac 习惯也不退 */ });

let _quitting = false;
app.on('before-quit', async (e) => {
  if (_quitting) return;
  if (supervisor.state === 'running' || supervisor.state === 'starting') {
    e.preventDefault();
    _quitting = true;
    try { await supervisor.stop(); } catch (_) {}
    app.quit();
  }
});

module.exports = { openAppWindow };
