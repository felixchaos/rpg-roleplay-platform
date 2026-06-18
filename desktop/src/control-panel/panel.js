'use strict';
// panel.js —— RPG Roleplay 控制台渲染层(左导航 + 8 面板)。只经 window.sv(preload 白名单)。

const $ = (id) => document.getElementById(id);
const sv = window.sv;
const CONSENT_TEXT = '我已阅读 AUP §2.J,理解不得包含成人主题节选,同意(此操作记录我的同意)';
const LINKS = {
  landing: 'https://play.stellatrix.icu/legal/terms',
  app: 'https://rpg-roleplay.stellatrix.icu/',
  repo: 'https://github.com/felixchaos/rpg-roleplay-platform',
  card: 'https://felixchaos.link/',
  aup: 'https://play.stellatrix.icu/legal/aup#2J',
};
let cfg = null;
let last = { state: 'stopped', detail: '', backendPort: 0, pgPort: 0 };
let importFilePath = null;
let appVer = '—';

async function sha256hex(s) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((x) => x.toString(16).padStart(2, '0')).join('');
}
async function copy(text) { try { await navigator.clipboard.writeText(text); return true; } catch (_) { return false; } }
function flash(btn, txt) { const o = btn.textContent; btn.textContent = txt; btn.disabled = true; setTimeout(() => { btn.textContent = o; btn.disabled = false; }, 1200); }

// ── 标签切换 ──
const TAB_TITLE = { overview: '概览', logs: '日志', backup: '备份 / 恢复', lan: '局域网访问', config: '配置', update: '更新', feedback: '反馈', about: '关于' };
function switchTab(tab) {
  document.querySelectorAll('.navitem').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tab));
  $('tabTitle').textContent = TAB_TITLE[tab] || tab;
  if (tab === 'logs') $('logBadge').hidden = true;
  if (tab === 'lan') loadLan();
  if (tab === 'backup') refreshBackupGate();
}

// ── 状态渲染 ──
const STATE_TEXT = { stopped: '已停止', starting: '启动中', running: '运行中', stopping: '停止中', error: '错误' };
function renderStatus(s) {
  last = s;
  const online = cfg && cfg.mode === 'online';
  const cls = online ? '' : (s.state === 'running' ? 'run' : (s.state === 'error' ? 'err' : (s.state === 'starting' || s.state === 'stopping' ? 'busy' : '')));
  const dotCls = online ? 'dot' : 'dot ' + (s.state === 'running' ? 'ok' : s.state === 'error' ? 'danger' : (s.state === 'starting' || s.state === 'stopping') ? 'accent pulse' : '');
  const label = online ? '在线' : (STATE_TEXT[s.state] || s.state);
  $('statusChip').className = 'chip ' + cls;
  $('chipDot').className = dotCls; $('chipLabel').textContent = label;
  $('sideDot').className = dotCls; $('sideStatus').textContent = label;
  $('sideMode').textContent = online ? (cfg.onlineUrl || '') : (s.backendPort ? `http://127.0.0.1:${s.backendPort}` : '本地部署');
  $('svDetail').textContent = s.detail || '—';
  $('svBackendPort').textContent = s.backendPort || '—';
  $('svPgPort').textContent = s.pgPort || '—';
  const busy = s.state === 'starting' || s.state === 'stopping';
  $('startBtn').disabled = busy || s.state === 'running';
  $('stopBtn').disabled = busy || s.state === 'stopped';
  $('restartBtn').disabled = busy || s.state === 'stopped';
  $('svError').hidden = s.state !== 'error';
  if (s.state === 'error') $('svErrorTitle').textContent = s.detail || '启动失败';
  $('openAppBtn').disabled = busy;
  refreshBackupGate();
}

// ── 模式 ──
function renderMode() {
  const local = cfg.mode === 'local';
  document.querySelectorAll('.modeseg button').forEach((b) => b.classList.toggle('active', b.dataset.mode === cfg.mode));
  $('localSvc').hidden = !local;
  $('modeHint').textContent = local
    ? '在本机启动数据库 + 后端,数据完全离线,NSFW 自主。首次启动需初始化,稍候。'
    : '连接云端服务器,即开即用,数据存于你的云端账号。';
  renderStatus(last);
}
async function setMode(mode) { cfg = await sv.setConfig({ mode }); renderMode(); }

// ── 日志 ──
const logPane = $('logPane');
let logCount = 0;
function appendLog(e) {
  const atBottom = logPane.scrollTop + logPane.clientHeight >= logPane.scrollHeight - 6;
  const t = new Date(e.ts || Date.now());
  const hh = String(t.getHours()).padStart(2, '0'), mm = String(t.getMinutes()).padStart(2, '0'), ss = String(t.getSeconds()).padStart(2, '0');
  const div = document.createElement('div');
  const isErr = /\b(error|failed)\b|错误|失败/i.test(e.line);
  div.innerHTML = `<span class="lt">${hh}:${mm}:${ss}</span> <span class="ls">[${e.src}]</span> `;
  const msg = document.createElement('span'); if (isErr) msg.className = 'le'; msg.textContent = e.line; div.appendChild(msg);
  logPane.appendChild(div);
  while (logPane.childElementCount > 1000) logPane.removeChild(logPane.firstChild);
  if (atBottom) logPane.scrollTop = logPane.scrollHeight;
  logCount++;
  if (!document.querySelector('.navitem[data-tab="logs"]').classList.contains('active')) { $('logBadge').hidden = false; $('logBadge').textContent = logCount > 999 ? '999+' : logCount; }
}

// ── 配置 ──
function fillForm() {
  $('cfgOnlineUrl').value = cfg.onlineUrl || '';
  $('cfgBackendPort').value = cfg.backendPort || 0;
  $('cfgChannel').value = cfg.updateChannel || 'stable';
  $('cfgAutoStart').checked = !!cfg.autoStartLocal;
  $('cfgExtraEnv').value = Object.entries(cfg.extraEnv || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}
function parseEnv(text) {
  const out = {};
  for (const raw of String(text).split('\n')) { const line = raw.trim(); if (!line || line.startsWith('#')) continue; const i = line.indexOf('='); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
  return out;
}
async function saveCfg() {
  cfg = await sv.setConfig({ onlineUrl: $('cfgOnlineUrl').value.trim() || 'https://rpg-roleplay.stellatrix.icu', backendPort: parseInt($('cfgBackendPort').value, 10) || 0, updateChannel: $('cfgChannel').value, autoStartLocal: $('cfgAutoStart').checked, extraEnv: parseEnv($('cfgExtraEnv').value) });
  fillForm(); flash($('saveCfgBtn'), '已保存'); $('updCurrent').textContent = `当前 v${appVer} · ${cfg.updateChannel}`;
}

// ── 备份 / 恢复 ──
function migAvailable() { return cfg.mode === 'local' && last.state === 'running'; }
function refreshBackupGate() {
  const ok = migAvailable();
  if ($('bkGate')) $('bkGate').textContent = ok ? '' : ' 备份/恢复需切到本地模式并启动服务后可用。';
  ['exportBtn', 'pickImportBtn', 'backupNowBtn'].forEach((id) => { if ($(id)) $(id).disabled = !ok; });
  if ($('startImportBtn')) $('startImportBtn').disabled = !ok || !importFilePath;
  if (ok && $('exportSize') && $('exportSize').textContent === '—') loadEstimate();
}
async function loadEstimate() { try { const r = await sv.accountEstimate(); if (r && r.ok) $('exportSize').textContent = r.size || '—'; } catch (_) {} }
function fillBackup() {
  $('backupDir').value = cfg.backupDir || '';
  $('autoBackup').checked = !!cfg.autoBackup;
  $('autoBackupHours').value = cfg.autoBackupHours || 24;
}

// ── 局域网 ──
async function loadLan() {
  $('lanEnabled').checked = !!cfg.lanEnabled;
  try {
    const r = await sv.lanInfo();
    $('lanIp').textContent = r.ip || '未检测到';
    $('lanUrl').value = r.url || '—';
    $('fwCmd').textContent = r.firewallCmd || '—';
  } catch (_) { $('lanIp').textContent = '—'; }
}

// ── 更新 ──
function renderUpdate(u) {
  const t = $('updText'), dl = $('downloadUpdBtn'), inst = $('installUpdBtn'), prog = $('updProgress'), bar = $('updBar'), dot = $('updDot');
  dl.hidden = inst.hidden = true; prog.hidden = true;
  switch (u.state) {
    case 'checking': t.textContent = '检查中…'; dot.hidden = true; break;
    case 'none': t.textContent = '已是最新版本'; dot.hidden = true; break;
    case 'available': t.textContent = `发现新版本 ${u.version}`; dl.hidden = false; dot.hidden = false; break;
    case 'downloading': t.textContent = `下载中 ${u.percent}%`; prog.hidden = false; bar.style.width = `${u.percent}%`; break;
    case 'downloaded': t.textContent = `新版本 ${u.version} 已就绪`; inst.hidden = false; dot.hidden = false; break;
    case 'error': t.textContent = `更新出错:${u.message || ''}`; dot.hidden = true; break;
    default: t.textContent = '—';
  }
}

// ── 反馈 ──
function fbValidate() { $('fbSubmitBtn').disabled = !($('fbConsent').checked && $('fbText').value.trim()); $('fbConsentHint').hidden = $('fbConsent').checked; }
async function submitFeedback() {
  const text = $('fbText').value.trim();
  if (!text || !$('fbConsent').checked) return;
  $('fbSubmitBtn').disabled = true; $('fbErr').hidden = true;
  try {
    const token = await sha256hex(CONSENT_TEXT);
    const r = await sv.submitFeedback({ freeText: text, email: $('fbEmail').value.trim(), consentToken: token, includeEnv: $('fbAttach').checked });
    if (r && r.ok) { $('fbForm').hidden = true; $('fbSuccess').hidden = false; }
    else { $('fbErr').hidden = false; $('fbErr').textContent = '提交失败:' + ((r && (r.detail || r.error)) || '请稍后再试'); fbValidate(); }
  } catch (e) { $('fbErr').hidden = false; $('fbErr').textContent = '提交失败:' + (e && e.message || '网络错误'); fbValidate(); }
}

// ── 首次运行向导 ──
function maybeOnboard() {
  if (cfg.onboarded) return;
  $('onboard').hidden = false;
  let picked = null;
  document.querySelectorAll('.modecard').forEach((c) => c.addEventListener('click', () => { picked = c.dataset.mode; document.querySelectorAll('.modecard').forEach((x) => x.classList.toggle('sel', x === c)); $('onboardDone').disabled = false; }));
  $('onboardDone').addEventListener('click', async () => { cfg = await sv.setConfig(picked ? { mode: picked, onboarded: true } : { onboarded: true }); $('onboard').hidden = true; renderMode(); });
  $('onboardSkip').addEventListener('click', async () => { cfg = await sv.setConfig({ onboarded: true }); $('onboard').hidden = true; });
}

async function init() {
  appVer = await sv.appVersion();
  $('appVersion').textContent = 'v' + appVer;
  $('aboutVer').textContent = 'v' + appVer;
  $('updCurrent').textContent = `当前 v${appVer} · stable`;
  $('aupLink').href = LINKS.aup;
  $('lnLanding').href = LINKS.landing; $('lnApp').href = LINKS.app; $('lnRepo').href = LINKS.repo; $('lnCard').href = LINKS.card;
  cfg = await sv.getConfig();
  renderMode(); fillForm(); fillBackup();
  $('updCurrent').textContent = `当前 v${appVer} · ${cfg.updateChannel || 'stable'}`;
  renderStatus(await sv.status());
  (await sv.logs()).forEach(appendLog);
  maybeOnboard();

  document.querySelectorAll('.navitem').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  document.querySelectorAll('.modeseg button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));

  // overview
  $('openAppBtn').addEventListener('click', () => sv.openApp());
  $('openExtBtn').addEventListener('click', () => sv.openAppExternal());
  $('sideOpenBrowser').addEventListener('click', () => sv.openAppExternal());
  $('startBtn').addEventListener('click', () => sv.start().catch(() => {}));
  $('stopBtn').addEventListener('click', () => sv.stop().catch(() => {}));
  $('restartBtn').addEventListener('click', () => sv.restart().catch(() => {}));
  $('retryBtn').addEventListener('click', () => sv.start().catch(() => {}));
  $('errLogsBtn').addEventListener('click', () => sv.openLogsDir());
  $('copyDiagBtn').addEventListener('click', async () => { await sv.copyDiagnostics(); flash($('copyDiagBtn'), '已复制'); });

  // logs
  $('clearLogBtn').addEventListener('click', () => { logPane.innerHTML = ''; logCount = 0; });
  $('openLogsDirBtn').addEventListener('click', () => sv.openLogsDir());

  // backup
  $('exportBtn').addEventListener('click', async () => { $('exportBtn').disabled = true; await sv.accountExport($('exportChunks').checked); $('exportBtn').disabled = false; });
  $('pickImportBtn').addEventListener('click', async () => { const r = await sv.accountPickImport(); if (r && r.path) { importFilePath = r.path; $('pickImportBtn').textContent = r.name || '已选择文件'; $('startImportBtn').disabled = !migAvailable(); } });
  $('startImportBtn').addEventListener('click', async () => {
    if (!importFilePath) return;
    $('importIdle').hidden = true; $('importBusy').hidden = false; $('importDone').hidden = true;
    const r = await sv.accountImport(importFilePath); $('importBusy').hidden = true;
    if (r && r.ok) $('importDone').hidden = false; else { $('importIdle').hidden = false; $('importStage').textContent = '导入失败:' + ((r && (r.detail || r.error)) || '请重试'); }
  });
  $('importAgainBtn').addEventListener('click', () => { importFilePath = null; $('importDone').hidden = true; $('importIdle').hidden = false; $('pickImportBtn').textContent = '选择 .zip 文件…'; $('startImportBtn').disabled = true; });
  $('pickBackupDirBtn').addEventListener('click', async () => { const r = await sv.pickBackupDir(); if (r && r.path) { cfg = await sv.setConfig({ backupDir: r.path }); fillBackup(); } });
  $('saveBackupBtn').addEventListener('click', async () => { cfg = await sv.setConfig({ autoBackup: $('autoBackup').checked, autoBackupHours: parseInt($('autoBackupHours').value, 10) || 24 }); flash($('saveBackupBtn'), '已保存'); });
  $('backupNowBtn').addEventListener('click', async () => { $('backupNowBtn').disabled = true; const r = await sv.backupNow(); flash($('backupNowBtn'), r && r.ok ? '已备份' : '失败'); refreshBackupGate(); });

  // lan
  $('lanEnabled').addEventListener('change', async () => { cfg = await sv.setConfig({ lanEnabled: $('lanEnabled').checked }); loadLan(); });
  $('copyLanUrlBtn').addEventListener('click', async () => { await copy($('lanUrl').value); flash($('copyLanUrlBtn'), '已复制'); });
  $('copyFwBtn').addEventListener('click', async () => { await copy($('fwCmd').textContent); flash($('copyFwBtn'), '已复制'); });

  // config
  $('advToggle').addEventListener('click', () => { $('advBox').hidden = !$('advBox').hidden; });
  $('saveCfgBtn').addEventListener('click', saveCfg);
  $('openDataDirBtn').addEventListener('click', () => sv.openDataDir());
  $('wipeBtn').addEventListener('click', () => { $('wipeModal').hidden = false; $('wipeConfirmInput').value = ''; $('wipeConfirm').disabled = true; });
  $('wipeCancel').addEventListener('click', () => { $('wipeModal').hidden = true; });
  $('wipeConfirmInput').addEventListener('input', (e) => { $('wipeConfirm').disabled = e.target.value.trim() !== 'DELETE'; });
  $('wipeConfirm').addEventListener('click', async () => { $('wipeConfirm').disabled = true; await sv.wipeData(); $('wipeModal').hidden = true; });

  // update
  $('checkUpdBtn').addEventListener('click', async () => { renderUpdate({ state: 'checking' }); const r = await sv.checkUpdate(); if (!r.ok) renderUpdate({ state: 'error', message: r.reason }); });
  $('downloadUpdBtn').addEventListener('click', () => sv.downloadUpdate());
  $('installUpdBtn').addEventListener('click', () => sv.installUpdate());

  // feedback
  $('fbText').addEventListener('input', fbValidate);
  $('fbConsent').addEventListener('change', fbValidate);
  $('fbSubmitBtn').addEventListener('click', submitFeedback);
  $('fbAgainBtn').addEventListener('click', () => { $('fbSuccess').hidden = true; $('fbForm').hidden = false; $('fbText').value = ''; $('fbConsent').checked = false; fbValidate(); });

  sv.onStatus(renderStatus); sv.onLog(appendLog); sv.onUpdate(renderUpdate);
}
init().catch((e) => { document.body.insertAdjacentHTML('afterbegin', `<pre style="color:#c8675d;padding:12px">初始化失败: ${e && e.message}</pre>`); });
