'use strict';
// config.js —— 桌面配置的读写(userData/config.json)。跨更新保留。
//
// 字段:
//   mode            'online' | 'local'        默认 online(装包小、即开即用)
//   onlineUrl       云端地址                   默认 https://play.stellatrix.icu
//   backendPort     本地后端端口               0 = 每次启动自动选空闲端口
//   pgPort          本地 PG 端口               0 = 自动选(默认从 15432 起避让)
//   masterKey       32 字节 hex                首启生成,经 RPG_MASTER_KEY 注入后端(避免后端往只读区写 master.key)
//   extraEnv        {KEY:VAL}                  用户在控制台填的额外环境变量
//   updateChannel   'stable' | 'beta'         更新渠道
//   autoStartLocal  bool                       本地模式下打开 app 即自动起服务

const fs = require('fs');
const crypto = require('crypto');
const P = require('./paths');

const DEFAULTS = {
  mode: 'online',
  onlineUrl: 'https://rpg-roleplay.stellatrix.icu',
  backendPort: 0,
  pgPort: 0,
  masterKey: '',
  clientId: '',
  extraEnv: {},
  updateChannel: 'stable',
  autoStartLocal: true,
  onboarded: false,        // 首启向导完成标记
  lanEnabled: false,       // 局域网访问:后端绑 0.0.0.0(同网可访问)
  backupDir: '',           // 本地备份目录(自动备份导出 zip 存放处)
  autoBackup: false,       // 自动备份开关
  autoBackupHours: 24,     // 自动备份间隔(小时)
};

let _cache = null;

function load() {
  if (_cache) return _cache;
  let data = {};
  try {
    data = JSON.parse(fs.readFileSync(P.configFile(), 'utf8'));
  } catch (_) { /* 首次无文件 */ }
  _cache = { ...DEFAULTS, ...data };
  // 首启生成 master key + 设备 client_id(一次性,持久化)
  let _dirty = false;
  if (!_cache.masterKey) { _cache.masterKey = crypto.randomBytes(32).toString('hex'); _dirty = true; }
  if (!_cache.clientId) { _cache.clientId = crypto.randomUUID(); _dirty = true; }
  if (_dirty) save(_cache);
  return _cache;
}

function save(patch) {
  _cache = { ...load_noinit(), ...patch };
  try {
    fs.mkdirSync(require('path').dirname(P.configFile()), { recursive: true });
    fs.writeFileSync(P.configFile(), JSON.stringify(_cache, null, 2), 'utf8');
  } catch (e) {
    // 配置写失败不致命,但要让上层知道
    console.error('[config] save failed:', e.message);
  }
  return _cache;
}

// save() 内部用,避免 load() 在生成 masterKey 时递归 save
function load_noinit() {
  if (_cache) return _cache;
  let data = {};
  try { data = JSON.parse(fs.readFileSync(P.configFile(), 'utf8')); } catch (_) {}
  _cache = { ...DEFAULTS, ...data };
  return _cache;
}

module.exports = { load, save, DEFAULTS };
