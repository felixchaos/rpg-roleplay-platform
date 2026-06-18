'use strict';
// main.js —— Electron 主进程。
// 控制台窗口(起停服务/日志/配置/更新)+ 应用窗口(加载在线或本地的 Web UI)。
// 单实例锁、优雅停机、自动更新全在这里。

const { app, BrowserWindow, ipcMain, shell, dialog, clipboard } = require('electron');
const path = require('path');

const P = require('./paths');
const cfg = require('./config');
const supervisor = require('./supervisor');

let panelWin = null;
let appWin = null;
let updater = null;     // electron-updater(惰性载入,dev 环境可能未装)
let tray = null;
let _lastAutoBackup = 0;

function showPanel() { if (panelWin && !panelWin.isDestroyed()) { if (panelWin.isMinimized()) panelWin.restore(); panelWin.show(); panelWin.focus(); } else createPanel(); }

// ── 系统托盘(macOS + Windows)──
function createTray() {
  try {
    const { Tray, Menu, nativeImage } = require('electron');
    let img = nativeImage.createFromPath(path.join(__dirname, 'tray.png'));
    if (!img.isEmpty()) img = img.resize({ width: 18, height: 18 });
    tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img);
    tray.setToolTip('RPG Roleplay 控制台');
    const menu = Menu.buildFromTemplate([
      { label: '打开控制台', click: showPanel },
      { label: '打开应用', click: () => openAppWindow() },
      { type: 'separator' },
      { label: '启动服务', click: () => supervisor.start().catch(() => {}) },
      { label: '停止服务', click: () => supervisor.stop().catch(() => {}) },
      { type: 'separator' },
      { label: '退出', click: () => { _quitting = true; app.quit(); } },
    ]);
    tray.setContextMenu(menu);
    tray.on('click', showPanel);   // Windows 习惯:单击托盘打开
  } catch (e) { console.error('[tray] 创建失败:', e && e.message); }
}

// ── 单实例锁:本机只允许一个服务端,避免抢端口/锁数据目录 ──
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (panelWin) { if (panelWin.isMinimized()) panelWin.restore(); panelWin.focus(); } });
}

function createPanel() {
  panelWin = new BrowserWindow({
    width: 820, height: 640, minWidth: 600, minHeight: 480,
    title: 'RPG Roleplay 控制台',
    icon: path.join(__dirname, 'tray.png'),
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
    title: 'RPG Roleplay',
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

  // 反馈接服务器:始终发到中央收集服务器(onlineUrl)的匿名端点,与运行模式无关。
  // 走 Electron net(主进程,不受浏览器 CORS 限制);留邮箱则后端按重名归并到登录账户。
  ipcMain.handle('feedback:submit', async (_e, payload) => {
    const { net } = require('electron');
    const c = cfg.load();
    const base = (c.onlineUrl || 'https://rpg-roleplay.stellatrix.icu').replace(/\/+$/, '');
    const p = payload || {};
    const body = JSON.stringify({
      free_text: p.freeText || '',
      contact_email: p.email || '',
      consent_token: p.consentToken || '',
      client_id: c.clientId || '',
      app_version: app.getVersion(),
      // 仅当用户勾选「附带运行环境信息」时上报环境快照
      env_snapshot: p.includeEnv === false ? {} : {
        os: process.platform, arch: process.arch,
        os_version: require('os').release(),
        app_version: app.getVersion(), electron: process.versions.electron,
        mode: c.mode,
      },
    });
    return await new Promise((resolve) => {
      let req;
      try { req = net.request({ method: 'POST', url: `${base}/api/feedback/anon` }); }
      catch (e) { return resolve({ ok: false, error: String(e && e.message || e) }); }
      req.setHeader('Content-Type', 'application/json');
      let data = '';
      req.on('response', (res) => {
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try { const j = JSON.parse(data); resolve({ ok: res.statusCode < 400 && !!j.ok, status: res.statusCode, ...j }); }
          catch (_) { resolve({ ok: false, status: res.statusCode, error: (data || '').slice(0, 200) }); }
        });
      });
      req.on('error', (err) => resolve({ ok: false, error: String(err && err.message || err) }));
      req.write(body);
      req.end();
    });
  });

  // ── 账号数据迁移(对本地后端;本地模式 default user 无需鉴权)──
  const _localBase = () => `http://127.0.0.1:${supervisor.backendPort}`;
  const _localOk = () => cfg.load().mode === 'local' && supervisor.state === 'running' && !!supervisor.backendPort;

  ipcMain.handle('account:estimate', async () => {
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    const { net } = require('electron');
    return await new Promise((resolve) => {
      const req = net.request(`${_localBase()}/api/me/account/export/estimate`);
      let data = '';
      req.on('response', (res) => { res.on('data', (d) => data += d); res.on('end', () => {
        try { const j = JSON.parse(data); resolve({ ok: true, size: j.size_human || j.human || (j.bytes ? Math.round(j.bytes / 1048576) + ' MB' : (j.size || '—')) }); }
        catch (_) { resolve({ ok: false }); }
      }); });
      req.on('error', () => resolve({ ok: false }));
      req.end();
    });
  });

  ipcMain.handle('account:export', async (_e, includeChunks) => {
    if (!_localOk()) return { ok: false };
    await shell.openExternal(`${_localBase()}/api/me/account/export${includeChunks ? '?include_chunks=1' : ''}`);
    return { ok: true };
  });

  ipcMain.handle('account:pickImport', async () => {
    const r = await dialog.showOpenDialog({ title: '选择账号数据 .zip', filters: [{ name: 'Zip', extensions: ['zip'] }], properties: ['openFile'] });
    if (r.canceled || !r.filePaths[0]) return { ok: false };
    return { ok: true, path: r.filePaths[0], name: require('path').basename(r.filePaths[0]) };
  });

  ipcMain.handle('account:import', async (_e, filePath) => {
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    const { net } = require('electron');
    let buf;
    try { buf = require('fs').readFileSync(filePath); } catch (_) { return { ok: false, error: '读取文件失败' }; }
    const boundary = '----stxform' + process.hrtime.bigint().toString(36);
    const name = require('path').basename(filePath);
    const head = Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${name}"\r\nContent-Type: application/zip\r\n\r\n`);
    const tail = Buffer.from(`\r\n--${boundary}--\r\n`);
    const body = Buffer.concat([head, buf, tail]);
    return await new Promise((resolve) => {
      const req = net.request({ method: 'POST', url: `${_localBase()}/api/me/account/import` });
      req.setHeader('Content-Type', `multipart/form-data; boundary=${boundary}`);
      let data = '';
      req.on('response', (res) => { res.on('data', (d) => data += d); res.on('end', () => {
        try { const j = JSON.parse(data); resolve({ ok: res.statusCode < 400 && j.ok !== false, ...j }); }
        catch (_) { resolve({ ok: res.statusCode < 400, status: res.statusCode }); }
      }); });
      req.on('error', (err) => resolve({ ok: false, error: String(err && err.message || err) }));
      req.write(body);
      req.end();
    });
  });

  // ── 清除本地数据(危险区:停服务 + 删 pgdata/app 可写副本;保留 config)──
  ipcMain.handle('sys:wipeData', async () => {
    try { await supervisor.stop(); } catch (_) {}
    const fsp = require('fs/promises');
    for (const d of [P.pgData(), P.appDir()]) { try { await fsp.rm(d, { recursive: true, force: true }); } catch (_) {} }
    return { ok: true };
  });

  // ── 复制诊断(脱敏:不含 env 值/密钥)──
  ipcMain.handle('sys:copyDiagnostics', () => {
    const c = cfg.load();
    const recent = supervisor.recentLogs().slice(-50).map((l) => `${new Date(l.ts).toISOString()} [${l.src}] ${l.line}`).join('\n');
    clipboard.writeText([
      `RPG Roleplay ${app.getVersion()}`,
      `platform: ${process.platform} ${process.arch} / os ${require('os').release()}`,
      `electron: ${process.versions.electron} / node ${process.versions.node}`,
      `mode: ${c.mode} / state: ${supervisor.state} / backend: ${supervisor.backendPort} / pg: ${supervisor.pgPort}`,
      '--- recent logs ---', recent,
    ].join('\n'));
    return { ok: true };
  });

  // ── 局域网:本机 IP + 访问地址 + 按当前系统的放行防火墙命令 ──
  ipcMain.handle('lan:info', () => {
    const os = require('os');
    let ip = '';
    const ifs = os.networkInterfaces();
    for (const name of Object.keys(ifs)) {
      for (const ni of ifs[name] || []) { if (ni.family === 'IPv4' && !ni.internal && !/^169\.254\./.test(ni.address)) { ip = ni.address; break; } }
      if (ip) break;
    }
    const port = supervisor.backendPort || cfg.load().backendPort || 0;
    const url = ip && port ? `http://${ip}:${port}/` : (ip ? `http://${ip}:<启动服务后的端口>/` : '未检测到局域网 IP');
    let firewallCmd;
    if (process.platform === 'win32') {
      firewallCmd = port
        ? `netsh advfirewall firewall add rule name="RPG Roleplay LAN ${port}" dir=in action=allow protocol=TCP localport=${port}`
        : '启动本地服务后这里会显示放行命令(需要后端端口)。';
    } else if (process.platform === 'darwin') {
      const py = P.runtimePython();
      firewallCmd = 'macOS 应用防火墙按「程序」放行(若已关防火墙则无需操作):\n'
        + `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "${py}"\n`
        + `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "${py}"`;
    } else {
      firewallCmd = port ? `sudo ufw allow ${port}/tcp   # 或 firewalld: sudo firewall-cmd --add-port=${port}/tcp` : '启动本地服务后这里会显示放行命令(需要后端端口)。';
    }
    return { ip, port, url, firewallCmd };
  });

  // ── 备份目录选择 + 立即备份到目录 ──
  ipcMain.handle('backup:pickDir', async () => {
    const r = await dialog.showOpenDialog({ title: '选择备份目录', properties: ['openDirectory', 'createDirectory'] });
    if (r.canceled || !r.filePaths[0]) return { ok: false };
    return { ok: true, path: r.filePaths[0] };
  });
  ipcMain.handle('backup:now', async () => {
    const c = cfg.load();
    if (!c.backupDir) return { ok: false, error: '未设置备份目录' };
    if (!(c.mode === 'local' && supervisor.state === 'running' && supervisor.backendPort)) return { ok: false, error: '需本地模式且服务运行' };
    return await _exportToDir(c.backupDir);
  });
}

// 把本地后端的账号导出保存到指定目录(自动备份/立即备份共用);保留最近 7 份。
function _exportToDir(dir) {
  const { net } = require('electron');
  const fs = require('fs');
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const file = require('path').join(dir, `rpg-backup-${ts}.zip`);
  const url = `http://127.0.0.1:${supervisor.backendPort}/api/me/account/export`;
  return new Promise((resolve) => {
    let req;
    try { req = net.request(url); } catch (e) { return resolve({ ok: false, error: String(e && e.message || e) }); }
    req.on('response', (res) => {
      if (res.statusCode >= 400) { res.resume(); return resolve({ ok: false, status: res.statusCode }); }
      const ws = fs.createWriteStream(file);
      res.on('data', (d) => ws.write(d));
      res.on('end', () => { ws.end(); _pruneBackups(dir); resolve({ ok: true, file }); });
      res.on('error', (e) => { ws.end(); resolve({ ok: false, error: String(e) }); });
    });
    req.on('error', (err) => resolve({ ok: false, error: String(err && err.message || err) }));
    req.end();
  });
}
function _pruneBackups(dir, keep = 7) {
  try {
    const fs = require('fs'), path = require('path');
    fs.readdirSync(dir).filter((f) => /^rpg-backup-.*\.zip$/.test(f))
      .map((f) => ({ f, t: fs.statSync(path.join(dir, f)).mtimeMs })).sort((a, b) => b.t - a.t)
      .slice(keep).forEach((x) => { try { fs.unlinkSync(path.join(dir, x.f)); } catch (_) {} });
  } catch (_) {}
}

app.whenReady().then(() => {
  wireIpc();
  createPanel();
  createTray();
  initUpdater();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createPanel(); });

  // 自动备份:每 30 分钟检查一次,到点(本地模式+运行中+已设目录)就导出到备份目录。
  setInterval(() => {
    const c = cfg.load();
    if (c.autoBackup && c.backupDir && c.mode === 'local' && supervisor.state === 'running' && supervisor.backendPort) {
      if (Date.now() - _lastAutoBackup >= (c.autoBackupHours || 24) * 3600 * 1000) {
        _lastAutoBackup = Date.now();
        _exportToDir(c.backupDir).catch(() => {});
      }
    }
  }, 30 * 60 * 1000);
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
