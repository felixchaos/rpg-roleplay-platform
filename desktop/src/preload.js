'use strict';
// preload.js —— 安全 IPC 桥。contextIsolation 下,渲染层只能用这里白名单暴露的方法,
// 拿不到 Node/require。控制台 UI(control-panel)通过 window.sv 调用。

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sv', {
  // 版本/状态
  appVersion: () => ipcRenderer.invoke('app:version'),
  status: () => ipcRenderer.invoke('sv:status'),
  logs: () => ipcRenderer.invoke('sv:logs'),

  // 服务生命周期(restart 走统一鉴定:有活跃导入任务时返回 needsConfirm;force=true 强制)
  start: () => ipcRenderer.invoke('sv:start'),
  stop: () => ipcRenderer.invoke('sv:stop'),
  restart: (opts) => ipcRenderer.invoke('sv:restart', opts || {}),

  // 配置
  getConfig: () => ipcRenderer.invoke('cfg:get'),
  setConfig: (patch) => ipcRenderer.invoke('cfg:set', patch),

  // 打开应用 / 系统目录
  openApp: () => ipcRenderer.invoke('app:open'),
  openAppExternal: () => ipcRenderer.invoke('app:openExternal'),
  openDataDir: () => ipcRenderer.invoke('sys:openDataDir'),
  openLogsDir: () => ipcRenderer.invoke('sys:openLogsDir'),

  // 更新
  checkUpdate: () => ipcRenderer.invoke('upd:check'),
  downloadUpdate: () => ipcRenderer.invoke('upd:download'),
  installUpdate: () => ipcRenderer.invoke('upd:install'),

  // 反馈(发到中央服务器匿名端点)
  submitFeedback: (payload) => ipcRenderer.invoke('feedback:submit', payload),
  feedbackReplies: () => ipcRenderer.invoke('feedback:replies'),

  // 本地默认账户(本地模式):信息 / 改用户名昵称 / 设密码
  localAccount: () => ipcRenderer.invoke('local:account'),
  localSetProfile: (body) => ipcRenderer.invoke('local:setProfile', body),
  localSetPassword: (body) => ipcRenderer.invoke('local:setPassword', body),

  // 云端账户(在线模式):控制台侧登录 / 登出 / 当前用户
  cloudMe: () => ipcRenderer.invoke('cloud:me'),
  cloudLogin: (body) => ipcRenderer.invoke('cloud:login', body),
  cloudLogout: () => ipcRenderer.invoke('cloud:logout'),

  // 云端 ↔ 本地 账户数据迁移
  cloudSyncToLocal: () => ipcRenderer.invoke('cloud:syncToLocal'),
  cloudSyncFromLocal: () => ipcRenderer.invoke('cloud:syncFromLocal'),

  // 账号数据迁移(对本地后端;仅本地模式+运行时有效)
  accountEstimate: () => ipcRenderer.invoke('account:estimate'),
  accountExport: (includeChunks) => ipcRenderer.invoke('account:export', includeChunks),
  accountPickImport: () => ipcRenderer.invoke('account:pickImport'),
  accountImport: (filePath) => ipcRenderer.invoke('account:import', filePath),

  // 系统:清除本地数据 / 复制诊断
  wipeData: () => ipcRenderer.invoke('sys:wipeData'),
  copyDiagnostics: () => ipcRenderer.invoke('sys:copyDiagnostics'),

  // 局域网
  lanInfo: () => ipcRenderer.invoke('lan:info'),
  lanQr: () => ipcRenderer.invoke('lan:qr'),

  // 可靠复制(主进程 clipboard)
  copyText: (text) => ipcRenderer.invoke('sys:copyText', text),

  // 备份目录 / 立即备份
  pickBackupDir: () => ipcRenderer.invoke('backup:pickDir'),
  backupNow: () => ipcRenderer.invoke('backup:now'),

  // 事件订阅(返回取消函数)
  onStatus: (cb) => { const h = (_e, p) => cb(p); ipcRenderer.on('sv:status', h); return () => ipcRenderer.removeListener('sv:status', h); },
  onLog: (cb) => { const h = (_e, p) => cb(p); ipcRenderer.on('sv:log', h); return () => ipcRenderer.removeListener('sv:log', h); },
  onUpdate: (cb) => { const h = (_e, p) => cb(p); ipcRenderer.on('upd:status', h); return () => ipcRenderer.removeListener('upd:status', h); },
});
