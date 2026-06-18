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
    // 后端裸 / 返回服务 JSON;应用入口是 Platform.html(desktop 模式免登录,无用户则自动跳 Login 注册)。
    url = `http://127.0.0.1:${supervisor.backendPort}/Platform.html`;
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
// 更新源:GitHub Releases 为主(与 package.json publish 一致),失败回退到我的服务器。
const UPD_GITHUB_FEED = { provider: 'github', owner: 'felixchaos', repo: 'rpg-roleplay-platform' };
function _fallbackFeed() {
  const c = cfg.load();
  const base = (c.updateFallbackUrl
    || `${(c.onlineUrl || 'https://rpg-roleplay.stellatrix.icu').replace(/\/+$/, '')}/updates`).replace(/\/+$/, '');
  // generic provider:base 目录需含 latest.yml(win)/latest-mac.yml(mac)+ 安装包;
  // channel 决定 yml 名(stable→latest / beta→beta)。CI 把同批产物上传到此目录。
  return { provider: 'generic', url: base, channel: (c.updateChannel === 'beta' ? 'beta' : 'latest') };
}

function initUpdater() {
  if (!app.isPackaged) return;
  try { updater = require('electron-updater').autoUpdater; } catch (_) { return; }
  updater.autoDownload = false;
  updater.channel = cfg.load().updateChannel || 'stable';
  const send = (channel, payload) => panelWin && !panelWin.isDestroyed() && panelWin.webContents.send(channel, payload);
  updater.on('checking-for-update', () => send('upd:status', { state: 'checking' }));
  const _notes = (i) => (Array.isArray(i.releaseNotes) ? i.releaseNotes.map((n) => n && n.note || '').join('\n\n') : (i.releaseNotes || ''));
  updater.on('update-available', (i) => send('upd:status', { state: 'available', version: i.version, notes: _notes(i) }));
  updater.on('update-not-available', () => send('upd:status', { state: 'none' }));
  updater.on('error', (e) => send('upd:status', { state: 'error', message: String(e && e.message || e) }));
  updater.on('download-progress', (p) => send('upd:status', { state: 'downloading', percent: Math.round(p.percent) }));
  updater.on('update-downloaded', (i) => send('upd:status', { state: 'downloaded', version: i.version, notes: _notes(i) }));
}

// ── 共享 net helper(主进程,绕浏览器 CORS;可选 session 分区带 cookie)──
function _netJson(method, url, body, ses) {
  const { net } = require('electron');
  return new Promise((resolve, reject) => {
    let req;
    try { req = net.request({ method, url, ...(ses ? { session: ses } : {}) }); }
    catch (e) { return reject(e); }
    if (body) req.setHeader('Content-Type', 'application/json');
    let data = '';
    req.on('response', (res) => {
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        let j = {};
        try { j = data ? JSON.parse(data) : {}; } catch (_) { j = { raw: (data || '').slice(0, 200) }; }
        resolve({ status: res.statusCode, ok: res.statusCode < 400, ...(j && typeof j === 'object' ? j : { value: j }) });
      });
    });
    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

// 取二进制(账户导出 zip)。
function _fetchBytes(url, ses) {
  const { net } = require('electron');
  return new Promise((resolve, reject) => {
    let req;
    try { req = net.request({ url, ...(ses ? { session: ses } : {}) }); } catch (e) { return reject(e); }
    const chunks = [];
    req.on('response', (res) => {
      if (res.statusCode >= 400) { res.on('data', () => {}); res.on('end', () => reject(new Error('HTTP ' + res.statusCode))); return; }
      res.on('data', (c) => chunks.push(Buffer.from(c)));
      res.on('end', () => resolve(Buffer.concat(chunks)));
    });
    req.on('error', reject);
    req.end();
  });
}
// 上传 zip(multipart)到账户导入端点。
function _postZip(url, buf, name, ses) {
  const { net } = require('electron');
  const boundary = '----stxform' + process.hrtime.bigint().toString(36);
  const head = Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${name}"\r\nContent-Type: application/zip\r\n\r\n`);
  const tail = Buffer.from(`\r\n--${boundary}--\r\n`);
  const body = Buffer.concat([head, buf, tail]);
  return new Promise((resolve) => {
    let req;
    try { req = net.request({ method: 'POST', url, ...(ses ? { session: ses } : {}) }); } catch (e) { return resolve({ ok: false, error: String(e) }); }
    req.setHeader('Content-Type', `multipart/form-data; boundary=${boundary}`);
    let data = '';
    req.on('response', (res) => { res.on('data', (d) => { data += d; }); res.on('end', () => { try { const j = JSON.parse(data); resolve({ ok: res.statusCode < 400 && j.ok !== false, ...j }); } catch (_) { resolve({ ok: res.statusCode < 400, status: res.statusCode }); } }); });
    req.on('error', (e) => resolve({ ok: false, error: String(e && e.message || e) }));
    req.write(body); req.end();
  });
}

// 云端账户用独立持久化 session 分区:控制台登录的 cookie 与本地后端、系统浏览器都隔离(不串号)。
let _cloudSes = null;
function _cloudSession() {
  if (!_cloudSes) { const { session } = require('electron'); _cloudSes = session.fromPartition('persist:cloud'); }
  return _cloudSes;
}

// 统一重启前置:查本地后端有无正在运行的导入任务(防中断用户长任务)。
async function _activeImportJobs() {
  try {
    if (cfg.load().mode !== 'local' || supervisor.state !== 'running' || !supervisor.backendPort) return [];
    const r = await _netJson('GET', `http://127.0.0.1:${supervisor.backendPort}/api/me/tasks/active`);
    const items = (r && (r.tasks || r.items || r.active)) || [];
    return items.filter((t) => {
      const kind = (t.kind || t.type || '').toLowerCase();
      const st = (t.status || t.state || '').toLowerCase();
      return (kind.includes('import') || kind.includes('rebuild') || kind.includes('extract'))
        && !['done', 'failed', 'cancelled', 'canceled', 'error'].includes(st);
    });
  } catch (_) { return []; }
}

// ── IPC ──
function wireIpc() {
  ipcMain.handle('app:version', () => app.getVersion());
  ipcMain.handle('sv:status', () => supervisor.snapshot());
  ipcMain.handle('sv:logs', () => supervisor.recentLogs());
  ipcMain.handle('sv:start', async () => { await supervisor.start(); return supervisor.snapshot(); });
  ipcMain.handle('sv:stop', async () => { await supervisor.stop(); return supervisor.snapshot(); });
  // 统一重启鉴定:若有正在运行的导入/重建任务,先返回 needsConfirm 让前端弹窗询问;
  // 用户确认(force)才中断并重启。所有重启入口(重启按钮 / 改需重启设置)都走这里。
  ipcMain.handle('sv:restart', async (_e, opts) => {
    const force = !!(opts && opts.force);
    if (!force) {
      const active = await _activeImportJobs();
      if (active.length) return { ok: false, needsConfirm: true, activeTasks: active.map((t) => t.label || t.title || t.kind || '导入任务') };
    }
    await supervisor.restart();
    return { ok: true, ...supervisor.snapshot() };
  });

  ipcMain.handle('cfg:get', () => cfg.load());
  ipcMain.handle('cfg:set', (_e, patch) => {
    const safe = { ...patch };
    delete safe.masterKey;                 // 不允许从 UI 改 master key
    return cfg.save(safe);
  });

  ipcMain.handle('app:open', async () => ({ url: await openAppWindow() }));
  ipcMain.handle('app:openExternal', async () => {
    const c = cfg.load();
    if (c.mode === 'local') {
      if (supervisor.state !== 'running') await supervisor.start();
      const base = `http://127.0.0.1:${supervisor.backendPort}`;
      let target = `${base}/Platform.html`;
      // 免登录魔法链接:铸一次性 token → 打开 desktop-login(浏览器即登录默认账户)。
      // 关闭则直接开 Platform(未设密码=回环自动登录;已设密码=会跳登录页)。
      if (c.magicLink) {
        try {
          const r = await _netJson('POST', `${base}/api/local/account/magic-token`);
          if (r && r.ok && r.token) {
            target = `${base}/api/auth/desktop-login?token=${encodeURIComponent(r.token)}&next=${encodeURIComponent('/Platform.html')}`;
          }
        } catch (_) { /* 兜底直接开 Platform */ }
      }
      await shell.openExternal(target);
      return { url: target };
    }
    const url = c.onlineUrl.replace(/\/+$/, '') + '/';
    await shell.openExternal(url);
    return { url };
  });

  // ── 云端账户(在线模式):控制台侧登录/登出 + 显示头像昵称(独立 cookie 分区,不串号)──
  const _cloudBase = () => (cfg.load().onlineUrl || 'https://rpg-roleplay.stellatrix.icu').replace(/\/+$/, '');
  ipcMain.handle('cloud:me', async () => {
    const base = _cloudBase();
    try { const r = await _netJson('GET', `${base}/api/auth/me`, null, _cloudSession()); return { ok: !!(r.ok && r.user), user: r.user || null, base }; }
    catch (e) { return { ok: false, error: String(e && e.message || e), base }; }
  });
  ipcMain.handle('cloud:login', async (_e, body) => {
    const base = _cloudBase();
    try { const r = await _netJson('POST', `${base}/api/auth/login`, { username: (body && body.username) || '', password: (body && body.password) || '' }, _cloudSession()); return { ok: !!(r.ok && r.user), user: r.user || null, error: r.error, base }; }
    catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  });
  ipcMain.handle('cloud:logout', async () => {
    const base = _cloudBase();
    try { await _netJson('POST', `${base}/api/auth/logout`, {}, _cloudSession()); } catch (_) {}
    try { await _cloudSession().clearStorageData(); } catch (_) {}
    return { ok: true };
  });
  ipcMain.handle('sys:openDataDir', () => { shell.openPath(P.userDataRoot()); });
  ipcMain.handle('sys:openLogsDir', () => { shell.openPath(P.logsDir()); });

  ipcMain.handle('upd:check', async () => {
    if (!updater) return { ok: false, reason: '更新仅在打包版可用' };
    // 优先 GitHub Releases;15s 超时/失败则自动回退到我的服务器(同一批更新包,CI 双分发)。
    // 每次检查都先重置回 GitHub → 保持「GitHub 优先」;下载用最近一次成功的 feed。
    const _race = () => Promise.race([
      updater.checkForUpdates(),
      new Promise((_, rej) => setTimeout(() => rej(new Error('检查更新超时')), 15000)),
    ]);
    try {
      updater.setFeedURL(UPD_GITHUB_FEED);
      const r = await _race();
      return { ok: true, version: r && r.updateInfo && r.updateInfo.version, source: 'github' };
    } catch (ePrimary) {
      try {
        updater.setFeedURL(_fallbackFeed());
        const r = await _race();
        return { ok: true, version: r && r.updateInfo && r.updateInfo.version, source: 'fallback' };
      } catch (eFallback) {
        return { ok: false, reason: `GitHub 与备用源均不可达:${String(eFallback && eFallback.message || eFallback)}` };
      }
    }
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

  // 拉取本机(client_id)提交过的反馈 + admin 回执,让回执在控制台内可见(不只发邮件)。
  ipcMain.handle('feedback:replies', async () => {
    const { net } = require('electron');
    const c = cfg.load();
    const base = (c.onlineUrl || 'https://rpg-roleplay.stellatrix.icu').replace(/\/+$/, '');
    const cid = c.clientId || '';
    if (!cid) return { ok: false, items: [] };
    const url = `${base}/api/feedback/anon/replies?client_id=${encodeURIComponent(cid)}&limit=30`;
    return await new Promise((resolve) => {
      let req;
      try { req = net.request(url); } catch (e) { return resolve({ ok: false, error: String(e && e.message || e), items: [] }); }
      let data = '';
      req.on('response', (res) => {
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try { const j = JSON.parse(data); resolve({ ok: res.statusCode < 400 && !!j.ok, items: j.items || [] }); }
          catch (_) { resolve({ ok: false, status: res.statusCode, items: [] }); }
        });
      });
      req.on('error', (err) => resolve({ ok: false, error: String(err && err.message || err), items: [] }));
      req.end();
    });
  });

  // ── 账号数据迁移(对本地后端;本地模式 default user 无需鉴权)──
  const _localBase = () => `http://127.0.0.1:${supervisor.backendPort}`;
  const _localOk = () => cfg.load().mode === 'local' && supervisor.state === 'running' && !!supervisor.backendPort;

  // ── 本地默认账户(本地模式):读信息 / 改用户名昵称 / 设密码 ──
  ipcMain.handle('local:account', async () => {
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    try { return await _netJson('GET', `${_localBase()}/api/local/account`); }
    catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  });
  ipcMain.handle('local:setProfile', async (_e, body) => {
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    try { return await _netJson('POST', `${_localBase()}/api/local/account/profile`, body || {}); }
    catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  });
  ipcMain.handle('local:setPassword', async (_e, body) => {
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    try { return await _netJson('POST', `${_localBase()}/api/local/account/password`, body || {}); }
    catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  });

  // ── 云端 ↔ 本地 账户数据迁移(导出一端 → 合并导入另一端;两端 import 都按会话用户隔离,IDOR 安全)──
  ipcMain.handle('cloud:syncToLocal', async () => {   // 云端 → 本地
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    const me = await _netJson('GET', `${_cloudBase()}/api/auth/me`, null, _cloudSession());
    if (!me || !me.user) return { ok: false, error: '未登录云端账户' };
    let buf;
    try { buf = await _fetchBytes(`${_cloudBase()}/api/me/account/export`, _cloudSession()); }
    catch (e) { return { ok: false, error: '云端导出失败:' + (e && e.message || e) }; }
    return await _postZip(`${_localBase()}/api/me/account/import`, buf, 'cloud-account.zip', null);
  });
  ipcMain.handle('cloud:syncFromLocal', async () => { // 本地 → 云端
    if (!_localOk()) return { ok: false, error: '需本地模式且服务运行' };
    const me = await _netJson('GET', `${_cloudBase()}/api/auth/me`, null, _cloudSession());
    if (!me || !me.user) return { ok: false, error: '未登录云端账户' };
    let buf;
    try { buf = await _fetchBytes(`${_localBase()}/api/me/account/export`, null); }
    catch (e) { return { ok: false, error: '本地导出失败:' + (e && e.message || e) }; }
    return await _postZip(`${_cloudBase()}/api/me/account/import`, buf, 'local-account.zip', _cloudSession());
  });

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

  // ── 局域网:真·本机 LAN IP(排除 VPN/虚拟口)+ 访问地址 + 按系统的端口放行命令 ──
  ipcMain.handle('lan:info', () => {
    const ip = _lanIp();
    const port = supervisor.backendPort || cfg.load().backendPort || 0;
    const url = ip && port ? `http://${ip}:${port}/` : (ip ? `http://${ip}:<启动服务后端口>/` : '未检测到局域网 IP');
    let firewallCmd;
    if (process.platform === 'win32') {
      firewallCmd = port
        ? `netsh advfirewall firewall add rule name="RPG Roleplay ${port}" dir=in action=allow protocol=TCP localport=${port}`
        : '启动服务后显示(需要端口)';
    } else if (process.platform === 'darwin') {
      // macOS 自带防火墙是「按程序」而非「按端口」,且默认多为关闭。无需端口命令。
      firewallCmd = port
        ? `# macOS 防火墙默认通常关闭,一般无需放行。\n# 若你开了防火墙:首次有设备连入时系统会弹窗,点「允许」即可。\n# 想用命令按端口放行(pf,高级):\necho "pass in proto tcp from any to any port ${port}" | sudo pfctl -ef -`
        : '启动服务后显示(需要端口)';
    } else {
      firewallCmd = port ? `sudo ufw allow ${port}/tcp` : '启动服务后显示(需要端口)';
    }
    return { ip, port, url, firewallCmd };
  });

  // 局域网地址二维码(供手机扫码);qrcode 在主进程生成 data URL。
  ipcMain.handle('lan:qr', async () => {
    const ip = _lanIp();
    const port = supervisor.backendPort || cfg.load().backendPort || 0;
    if (!ip || !port) return { ok: false };
    const url = `http://${ip}:${port}/`;
    try {
      const QR = require('qrcode');
      const dataUrl = await QR.toDataURL(url, { margin: 1, width: 240, color: { dark: '#1a1817', light: '#f4efe6' } });
      return { ok: true, url, dataUrl };
    } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  });

  // 可靠复制:走主进程 clipboard(渲染层 navigator.clipboard 在部分上下文不可用)。
  ipcMain.handle('sys:copyText', (_e, text) => { clipboard.writeText(String(text || '')); return { ok: true }; });

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
      res.on('end', () => { ws.end(); _pruneBackups(dir, cfg.load().backupKeep || 3); resolve({ ok: true, file }); });
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

// 真·本机局域网 IPv4:只认私有段(10/192.168/172.16-31),排除回环/链路本地 +
// VPN/虚拟接口(utun/tun/tap/wg/tailscale/vEthernet/vmnet…),优先物理网卡 → 避免拿到 VPN 代理地址。
function _lanIp() {
  const os = require('os');
  const VPN = /^(utun|ipsec|ppp|tun|tap|wg|nordlynx|tailscale|zt|gpd|awdl|llw|bridge|vmnet|vboxnet|vethernet|vmware|virtualbox|hyper-v|zerotier)/i;
  const PHYS = /^(en0|en1|eth0|eth1|wlan0|wlp|enp|Wi-Fi|以太网|Ethernet)/i;
  const cands = [];
  for (const [name, arr] of Object.entries(os.networkInterfaces() || {})) {
    for (const ni of arr || []) {
      if (ni.family !== 'IPv4' || ni.internal) continue;
      const ip = ni.address;
      if (/^169\.254\./.test(ip)) continue;
      if (!/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(ip)) continue; // 仅私有 LAN 段(VPN 出口公网/非私有自动排除)
      cands.push({ ip, vpn: VPN.test(name), phys: PHYS.test(name) });
    }
  }
  cands.sort((a, b) => (a.vpn - b.vpn) || (b.phys - a.phys));
  return cands.length ? cands[0].ip : '';
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
