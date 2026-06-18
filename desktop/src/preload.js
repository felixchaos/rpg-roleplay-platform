'use strict';
// preload.js —— 安全 IPC 桥。contextIsolation 下,渲染层只能用这里白名单暴露的方法,
// 拿不到 Node/require。控制台 UI(control-panel)通过 window.sv 调用。

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sv', {
  // 版本/状态
  appVersion: () => ipcRenderer.invoke('app:version'),
  status: () => ipcRenderer.invoke('sv:status'),
  logs: () => ipcRenderer.invoke('sv:logs'),

  // 服务生命周期
  start: () => ipcRenderer.invoke('sv:start'),
  stop: () => ipcRenderer.invoke('sv:stop'),
  restart: () => ipcRenderer.invoke('sv:restart'),

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

  // 事件订阅(返回取消函数)
  onStatus: (cb) => { const h = (_e, p) => cb(p); ipcRenderer.on('sv:status', h); return () => ipcRenderer.removeListener('sv:status', h); },
  onLog: (cb) => { const h = (_e, p) => cb(p); ipcRenderer.on('sv:log', h); return () => ipcRenderer.removeListener('sv:log', h); },
  onUpdate: (cb) => { const h = (_e, p) => cb(p); ipcRenderer.on('upd:status', h); return () => ipcRenderer.removeListener('upd:status', h); },
});
