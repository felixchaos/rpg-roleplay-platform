'use strict';
// supervisor.js —— 本地模式的服务监督器。
// 负责:同步可写后端副本 → 起捆绑 PostgreSQL(首启 initdb)→ 建库 → migrate full
//       → 起 uvicorn(serve API+前端)→ 健康检查 → 优雅停机。
// 对外是 EventEmitter:'status'(状态变化) / 'log'(日志行)。
//
// 设计纪律:
//  - 端口每次启动自动选空闲(避让系统 PG/占用)。
//  - PG 仅监听 127.0.0.1 + trust 认证(本机单用户,无需密码)。
//  - migrate full 走与 PG 的直连(无 PgBouncer),满足 advisory lock 要求。
//  - 停机:先停 uvicorn(SIGTERM)再 pg_ctl stop -m fast,确保无孤儿进程/锁。
//  - 所有相对写入(platform_data/master.key 等)落在可写副本 cwd 下;
//    master key 另经 RPG_MASTER_KEY 注入,双保险。

const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const net = require('net');
const http = require('http');
const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');

const P = require('./paths');
const cfg = require('./config');

const STATES = ['stopped', 'starting', 'running', 'stopping', 'error'];

class Supervisor extends EventEmitter {
  constructor() {
    super();
    this.state = 'stopped';
    this.detail = '';
    this.backendPort = 0;
    this.pgPort = 0;
    this._uvicorn = null;
    this._logRing = [];          // 最近 N 行日志(给 UI)
    this._logMax = 2000;
    this._stopping = false;
  }

  // ── 状态/日志 ──
  _setState(s, detail = '') {
    this.state = s;
    this.detail = detail;
    this.emit('status', this.snapshot());
  }
  snapshot() {
    return { state: this.state, detail: this.detail, backendPort: this.backendPort, pgPort: this.pgPort };
  }
  _log(src, line) {
    const entry = { ts: Date.now(), src, line: String(line).replace(/\s+$/, '') };
    this._logRing.push(entry);
    if (this._logRing.length > this._logMax) this._logRing.shift();
    this.emit('log', entry);
    try {
      fs.appendFileSync(path.join(P.logsDir(), `${src}.log`), entry.line + '\n');
    } catch (_) {}
  }
  recentLogs() { return this._logRing.slice(); }

  // ── 工具 ──
  async _pickPort(preferred) {
    const tryPort = (port) => new Promise((resolve) => {
      const srv = net.createServer();
      srv.once('error', () => resolve(0));
      srv.once('listening', () => srv.close(() => resolve(port)));
      srv.listen(port, '127.0.0.1');
    });
    if (preferred) { const p = await tryPort(preferred); if (p) return p; }
    return await new Promise((resolve) => {
      const srv = net.createServer();
      srv.once('listening', () => { const p = srv.address().port; srv.close(() => resolve(p)); });
      srv.listen(0, '127.0.0.1');
    });
  }

  _pgEnv(extra) {
    return { ...process.env, PGHOST: '127.0.0.1', PGPORT: String(this.pgPort), PGUSER: 'rpg', PGDATABASE: 'postgres', PYTHONNOUSERSITE: '1', ...extra };
  }

  _run(cmd, args, opts = {}) {
    return new Promise((resolve, reject) => {
      const tag = opts.tag || path.basename(cmd);
      const proc = spawn(cmd, args, { ...opts });
      proc.stdout && proc.stdout.on('data', (d) => this._log(tag, d.toString()));
      proc.stderr && proc.stderr.on('data', (d) => this._log(tag, d.toString()));
      proc.once('error', reject);
      proc.once('exit', (code) => code === 0 ? resolve(0) : reject(new Error(`${tag} exited ${code}`)));
    });
  }

  // ── 可写后端副本同步(跨更新刷新源码,保留 platform_data)──
  async _syncAppDir() {
    const tmpl = P.appTemplate();
    const dst = P.appDir();
    const want = (require('../package.json').version) || 'dev';
    let have = '';
    try { have = await fsp.readFile(P.versionStamp(), 'utf8'); } catch (_) {}
    if (have.trim() === want && fs.existsSync(P.backendCwd())) return; // 已是当前版本
    this._log('supervisor', `同步后端源码副本 → ${dst}(版本 ${want})`);
    await fsp.mkdir(dst, { recursive: true });
    // merge-copy:覆盖模板文件,但不删除副本里多出来的运行时产物(platform_data 等)
    await fsp.cp(tmpl, dst, { recursive: true, force: true });
    await fsp.writeFile(P.versionStamp(), want, 'utf8');
  }

  async _ensureDirs() {
    for (const d of [P.userDataRoot(), P.pgData(), P.logsDir(), P.appDir()]) {
      await fsp.mkdir(d, { recursive: true });
    }
  }

  // ── PostgreSQL ──
  async _initdbIfNeeded() {
    if (fs.existsSync(path.join(P.pgData(), 'PG_VERSION'))) return;
    this._setState('starting', '首次初始化数据库…');
    this._log('pg', 'initdb（首次）');
    await this._run(P.pgBin('initdb'), [
      '-D', P.pgData(), '-U', 'rpg', '-A', 'trust',
      '--locale=C', '--encoding=UTF8',
    ], { tag: 'pg', env: this._pgEnv() });
  }

  async _pgStart() {
    this.pgPort = await this._pickPort(15432);
    const sockDir = process.platform === 'win32' ? '' : `-c unix_socket_directories='${P.pgData()}'`;
    const pgOpts = `-p ${this.pgPort} -c listen_addresses=127.0.0.1 ${sockDir}`.trim();
    this._log('pg', `pg_ctl start :${this.pgPort}`);
    await this._run(P.pgBin('pg_ctl'), [
      '-D', P.pgData(), '-l', path.join(P.logsDir(), 'pg-server.log'),
      '-o', pgOpts, '-w', '-t', '60', 'start',
    ], { tag: 'pg', env: this._pgEnv() });
    // 双保险:轮询 pg_isready
    for (let i = 0; i < 120; i++) {
      try { await this._run(P.pgBin('pg_isready'), ['-h', '127.0.0.1', '-p', String(this.pgPort), '-U', 'rpg', '-q'], { tag: 'pg', env: this._pgEnv() }); break; }
      catch (_) { await new Promise(r => setTimeout(r, 500)); }
    }
  }

  async _pgStop() {
    try {
      await this._run(P.pgBin('pg_ctl'), ['-D', P.pgData(), '-m', 'fast', '-w', '-t', '30', 'stop'], { tag: 'pg', env: this._pgEnv() });
    } catch (e) { this._log('pg', `pg_ctl stop 警告: ${e.message}`); }
  }

  async _createDb() {
    // 幂等:已存在则忽略
    try {
      await this._run(P.pgBin('createdb'), ['-h', '127.0.0.1', '-p', String(this.pgPort), '-U', 'rpg', 'rpg'], { tag: 'pg', env: this._pgEnv() });
      this._log('pg', '建库 rpg');
    } catch (_) { /* 已存在 */ }
  }

  _databaseUrl() {
    // trust 认证,无需密码;直连(无 PgBouncer),满足 migrate advisory lock
    return `postgresql://rpg@127.0.0.1:${this.pgPort}/rpg`;
  }

  _backendEnv(extra) {
    const c = cfg.load();
    return {
      ...process.env,
      DATABASE_URL: this._databaseUrl(),
      RPG_DEPLOYMENT_MODE: 'desktop',
      RPG_MASTER_KEY: c.masterKey,
      RPG_SKIP_AUTO_MIGRATE: '1',      // 我们自己跑 migrate full,后端启动不再自动迁移
      PYTHONNOUSERSITE: '1',
      PYTHONUTF8: '1',
      ...(c.extraEnv || {}),
      ...extra,
    };
  }

  // ── 迁移 ──
  async _migrate() {
    this._setState('starting', '应用数据库迁移…');
    this._log('migrate', 'python -m platform_app.migrate full');
    await this._run(P.runtimePython(), ['-m', 'platform_app.migrate', 'full'], {
      tag: 'migrate', cwd: P.backendCwd(), env: this._backendEnv(),
    });
  }

  // ── uvicorn ──
  async _startBackend() {
    this.backendPort = await this._pickPort(cfg.load().backendPort || 7860);
    this._setState('starting', `启动后端 :${this.backendPort}…`);
    this._log('backend', `uvicorn app:app :${this.backendPort}`);
    this._uvicorn = spawn(P.runtimePython(), [
      '-m', 'uvicorn', 'app:app',
      '--host', '127.0.0.1', '--port', String(this.backendPort),
      '--workers', '1', '--no-access-log', '--log-level', 'info',
    ], { cwd: P.backendCwd(), env: this._backendEnv() });

    this._uvicorn.stdout.on('data', (d) => this._log('backend', d.toString()));
    this._uvicorn.stderr.on('data', (d) => this._log('backend', d.toString()));
    this._uvicorn.once('exit', (code) => {
      this._uvicorn = null;
      if (!this._stopping) {
        this._setState('error', `后端意外退出(code ${code})`);
      }
    });

    await this._waitHealth(this.backendPort, 60);
  }

  _waitHealth(port, seconds) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + seconds * 1000;
      const probe = () => {
        const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 2000 }, (res) => {
          if (res.statusCode === 200) { res.resume(); return resolve(); }
          res.resume(); retry();
        });
        req.once('error', retry);
        req.once('timeout', () => { req.destroy(); retry(); });
      };
      const retry = () => {
        if (Date.now() > deadline) return reject(new Error('后端健康检查超时'));
        setTimeout(probe, 600);
      };
      probe();
    });
  }

  // ── 对外:启停 ──
  async start() {
    if (this.state === 'running' || this.state === 'starting') return;
    this._stopping = false;
    try {
      this._setState('starting', '准备中…');
      await this._ensureDirs();
      await this._syncAppDir();
      await this._initdbIfNeeded();
      await this._pgStart();
      await this._createDb();
      await this._migrate();
      await this._startBackend();
      this._setState('running', `就绪 http://127.0.0.1:${this.backendPort}`);
    } catch (e) {
      this._log('supervisor', `启动失败: ${e.message}`);
      this._setState('error', e.message);
      await this.stop().catch(() => {});
      throw e;
    }
  }

  async stop() {
    this._stopping = true;
    this._setState('stopping', '停止中…');
    if (this._uvicorn) {
      try {
        this._uvicorn.kill('SIGTERM');
        await new Promise((r) => { const t = setTimeout(() => { try { this._uvicorn && this._uvicorn.kill('SIGKILL'); } catch (_) {} r(); }, 8000); this._uvicorn.once('exit', () => { clearTimeout(t); r(); }); });
      } catch (_) {}
      this._uvicorn = null;
    }
    await this._pgStop();
    this._setState('stopped', '已停止');
  }

  async restart() { await this.stop(); await this.start(); }
}

module.exports = new Supervisor();
module.exports.STATES = STATES;
