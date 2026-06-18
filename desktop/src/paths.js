'use strict';
// paths.js —— 解析「只读捆绑资源」与「用户可写数据目录」的全部路径。
//
// 布局约定:
//   只读资源(随 .app/安装包,签名后不可写):
//     <resources>/runtime/python/...      便携 Python(python-build-standalone)+ 已装依赖
//     <resources>/pg/{bin,lib,share}/...  便携 PostgreSQL + pgvector
//     <resources>/app-template/rpg/...     后端源码(app.py 在此)
//     <resources>/app-template/frontend/dist/...  前端构建产物(app.py 用 parent.parent/frontend 找它)
//   用户可写数据(app.getPath('userData'),跨更新保留):
//     <userData>/app/rpg + <userData>/app/frontend   运行时同步出的可写副本(cwd 在此)
//     <userData>/pgdata/                  PostgreSQL 数据目录
//     <userData>/logs/                    后端 + PG + 更新日志
//     <userData>/config.json              桌面配置(模式/端口/在线URL/master key 等)
//
// 为什么要把后端源码同步到 userData:macOS 签名后的 .app 只读,后端会往 cwd 相对路径写
// (platform_data/master.key、上传资产等)。把轻量源码(非 runtime/pg)复制到可写副本,
// cwd 设在那里,所有相对写入落到 userData,且保持 rpg/ 与 frontend/ 的 sibling 布局。

const path = require('path');
const os = require('os');

let _app = null;
try { _app = require('electron').app; } catch (_) { /* 允许在非 electron 环境(测试)引入 */ }

const isPackaged = !!(_app && _app.isPackaged);

// 只读资源根:打包后是 process.resourcesPath;开发时指向 desktop/resources-staged
function resourcesRoot() {
  if (isPackaged) return process.resourcesPath;
  return path.join(__dirname, '..', 'resources-staged');
}

// 用户可写数据根
function userDataRoot() {
  if (_app) return _app.getPath('userData');
  // 测试回退
  return path.join(os.homedir(), '.stellatrix-desktop');
}

function pyExeName() {
  return process.platform === 'win32' ? 'python.exe' : 'bin/python3';
}

const P = {
  isPackaged,
  resourcesRoot,
  userDataRoot,

  // ── 只读捆绑 ──
  runtimePython() {
    // python-build-standalone:win 解到 python/python.exe;unix 解到 python/bin/python3
    return path.join(resourcesRoot(), 'runtime', 'python', pyExeName());
  },
  pgBin(name) {
    const exe = process.platform === 'win32' ? `${name}.exe` : name;
    return path.join(resourcesRoot(), 'pg', 'bin', exe);
  },
  appTemplate() {
    return path.join(resourcesRoot(), 'app-template');
  },

  // ── 用户可写 ──
  appDir() { return path.join(userDataRoot(), 'app'); },
  backendCwd() { return path.join(userDataRoot(), 'app', 'rpg'); },   // app.py 所在目录 = uvicorn cwd
  pgData() { return path.join(userDataRoot(), 'pgdata'); },
  logsDir() { return path.join(userDataRoot(), 'logs'); },
  configFile() { return path.join(userDataRoot(), 'config.json'); },
  versionStamp() { return path.join(userDataRoot(), 'app', '.synced-version'); },
};

module.exports = P;
