'use strict';
// panel.js —— 控制台渲染层逻辑。只通过 window.sv(preload 白名单)与主进程通信。

const $ = (id) => document.getElementById(id);
const sv = window.sv;

let cfg = null;

// ── 状态渲染 ──
const STATE_TEXT = { stopped: '已停止', starting: '启动中', running: '运行中', stopping: '停止中', error: '错误' };
function renderStatus(s) {
  const dot = $('statusDot');
  dot.className = 'dot ' + (s.state || 'stopped');
  $('statusText').textContent = STATE_TEXT[s.state] || s.state || '—';
  $('svDetail').textContent = s.detail || '—';
  $('svBackendPort').textContent = s.backendPort || '—';
  $('svPgPort').textContent = s.pgPort || '—';
  const busy = s.state === 'starting' || s.state === 'stopping';
  $('startBtn').disabled = busy || s.state === 'running';
  $('stopBtn').disabled = busy || s.state === 'stopped';
  $('restartBtn').disabled = busy || s.state === 'stopped';
}

// ── 日志 ──
const logPane = $('logPane');
function appendLog(e) {
  const atBottom = logPane.scrollTop + logPane.clientHeight >= logPane.scrollHeight - 4;
  const line = document.createElement('div');
  const t = new Date(e.ts || Date.now());
  const hh = String(t.getHours()).padStart(2, '0'), mm = String(t.getMinutes()).padStart(2, '0'), ss = String(t.getSeconds()).padStart(2, '0');
  line.innerHTML = `<span class="src">${hh}:${mm}:${ss} [${e.src}]</span> `;
  line.appendChild(document.createTextNode(e.line));
  logPane.appendChild(line);
  while (logPane.childElementCount > 1000) logPane.removeChild(logPane.firstChild);
  if (atBottom) logPane.scrollTop = logPane.scrollHeight;
}

// ── 模式 ──
function renderMode() {
  const local = cfg.mode === 'local';
  document.querySelectorAll('.seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.mode === cfg.mode));
  $('localControls').hidden = !local;
  $('modeHint').textContent = local
    ? '本地模式:在本机启动捆绑的数据库 + 后端,数据完全离线,NSFW 自主。首次启动需初始化,稍候。'
    : '在线模式:连接云端服务器,即开即用,数据存于你的云端账号。';
}

async function setMode(mode) {
  cfg = await sv.setConfig({ mode });
  renderMode();
}

// ── 配置表单 ──
function fillForm() {
  $('cfgOnlineUrl').value = cfg.onlineUrl || '';
  $('cfgBackendPort').value = cfg.backendPort || 0;
  $('cfgChannel').value = cfg.updateChannel || 'stable';
  $('cfgAutoStart').checked = !!cfg.autoStartLocal;
  $('cfgExtraEnv').value = Object.entries(cfg.extraEnv || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}
function parseEnv(text) {
  const out = {};
  for (const raw of String(text).split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const i = line.indexOf('=');
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}
async function saveCfg() {
  const patch = {
    onlineUrl: $('cfgOnlineUrl').value.trim() || 'https://play.stellatrix.icu',
    backendPort: parseInt($('cfgBackendPort').value, 10) || 0,
    updateChannel: $('cfgChannel').value,
    autoStartLocal: $('cfgAutoStart').checked,
    extraEnv: parseEnv($('cfgExtraEnv').value),
  };
  cfg = await sv.setConfig(patch);
  fillForm();
  flash($('saveCfgBtn'), '已保存');
}
function flash(btn, txt) {
  const old = btn.textContent; btn.textContent = txt; btn.disabled = true;
  setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 1200);
}

// ── 更新 ──
function renderUpdate(u) {
  const t = $('updText');
  const acts = $('updActions'), dl = $('downloadUpdBtn'), inst = $('installUpdBtn'), prog = $('updProgress'), bar = $('updBar');
  switch (u.state) {
    case 'checking': t.textContent = '检查中…'; acts.hidden = true; break;
    case 'none': t.textContent = '已是最新版本'; acts.hidden = true; break;
    case 'available': t.textContent = `发现新版本 ${u.version}`; acts.hidden = false; dl.hidden = false; inst.hidden = true; prog.hidden = true; break;
    case 'downloading': t.textContent = `下载中 ${u.percent}%`; acts.hidden = false; dl.hidden = true; prog.hidden = false; bar.style.width = `${u.percent}%`; break;
    case 'downloaded': t.textContent = `新版本 ${u.version} 已就绪`; acts.hidden = false; dl.hidden = true; inst.hidden = false; prog.hidden = true; break;
    case 'error': t.textContent = `更新出错:${u.message || ''}`; acts.hidden = true; break;
    default: t.textContent = '—';
  }
}

// ── 绑定 ──
async function init() {
  $('appVersion').textContent = 'v' + await sv.appVersion();
  cfg = await sv.getConfig();
  renderMode();
  fillForm();
  renderStatus(await sv.status());
  (await sv.logs()).forEach(appendLog);

  document.querySelectorAll('.seg-btn').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
  $('openAppBtn').addEventListener('click', () => sv.openApp());
  $('openExtBtn').addEventListener('click', () => sv.openAppExternal());
  $('startBtn').addEventListener('click', () => sv.start().catch(() => {}));
  $('stopBtn').addEventListener('click', () => sv.stop().catch(() => {}));
  $('restartBtn').addEventListener('click', () => sv.restart().catch(() => {}));
  $('clearLogBtn').addEventListener('click', () => { logPane.innerHTML = ''; });
  $('openLogsDirBtn').addEventListener('click', () => sv.openLogsDir());
  $('openDataDirBtn').addEventListener('click', () => sv.openDataDir());
  $('saveCfgBtn').addEventListener('click', saveCfg);
  $('checkUpdBtn').addEventListener('click', async () => { renderUpdate({ state: 'checking' }); const r = await sv.checkUpdate(); if (!r.ok) renderUpdate({ state: 'error', message: r.reason }); });
  $('downloadUpdBtn').addEventListener('click', () => sv.downloadUpdate());
  $('installUpdBtn').addEventListener('click', () => sv.installUpdate());

  sv.onStatus(renderStatus);
  sv.onLog(appendLog);
  sv.onUpdate(renderUpdate);
}

init().catch((e) => { document.body.insertAdjacentHTML('afterbegin', `<pre style="color:#f85149;padding:12px">初始化失败: ${e && e.message}</pre>`); });
