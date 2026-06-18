'use strict';
// panel.js —— RPG Roleplay 控制台渲染层(左导航 + 8 面板)。只经 window.sv(preload 白名单)。

const $ = (id) => document.getElementById(id);
const sv = window.sv;
const t = (k) => (window.I18N ? window.I18N.t(k) : k);   // i18n(i18n.js 提供;兜底返回 key)
const CONSENT_TEXT = '我已阅读 AUP §2.J,理解不得包含成人主题节选,同意(此操作记录我的同意)';
const LINKS = {
  landing: 'https://play.stellatrix.icu/legal/terms-of-service',
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
async function copy(text) { try { await sv.copyText(text); return true; } catch (_) { try { await navigator.clipboard.writeText(text); return true; } catch (e) { return false; } } }
// 极简 Markdown → HTML(更新日志用;开发规范:发版 release notes 用 md)
// 按行处理:标题(#)、无序列表(-/*)、段落;连续列表项归入同一 <ul>,
// 标题与紧邻列表(单换行)也能正确渲染。
function mdToHtml(md) {
  const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+?)`/g, '<code>$1</code>').replace(/\[(.+?)\]\((.+?)\)/g, '$1');
  const out = []; let inList = false; let para = [];
  const flushPara = () => { if (para.length) { out.push('<p>' + inline(para.join('<br>')) + '</p>'); para = []; } };
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
  for (const raw of esc(md).split('\n')) {
    const l = raw.replace(/\s+$/, '');
    if (!l.trim()) { flushPara(); closeList(); continue; }
    const h = l.match(/^(#{1,6})\s+(.+)/);
    if (h) { flushPara(); closeList(); out.push('<h4>' + inline(h[2]) + '</h4>'); continue; }
    const li = l.match(/^\s*[-*]\s+(.+)/);
    if (li) { flushPara(); if (!inList) { out.push('<ul>'); inList = true; } out.push('<li>' + inline(li[1]) + '</li>'); continue; }
    para.push(l);
  }
  flushPara(); closeList();
  return out.join('');
}
function flash(btn, txt) { const o = btn.textContent; btn.textContent = txt; btn.disabled = true; setTimeout(() => { btn.textContent = o; btn.disabled = false; }, 1200); }

// ── 标签切换 ──
let _activeTab = 'overview';
function switchTab(tab) {
  _activeTab = tab;
  document.querySelectorAll('.navitem').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tab));
  $('tabTitle').textContent = t('nav.' + tab) || tab;
  if (tab === 'logs') $('logBadge').hidden = true;
  if (tab === 'lan') loadLan();
  if (tab === 'backup') { refreshBackupGate(); loadBackupAccount(); }
  if (tab === 'feedback') loadFeedbackReplies();
}

// 我的反馈 / 回执:按本机 client_id 从中央拉取自己提交过的反馈 + admin 回复。
const FB_DECISION = { ok: '已处理', spam: '已标记垃圾', nsfw_terminate: '违规' };
function _fbTime(iso) { try { const d = new Date(iso); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; } catch (_) { return ''; } }
async function loadFeedbackReplies() {
  const list = $('fbReplyList'), empty = $('fbReplyEmpty');
  if (!list) return;
  let items = [];
  try { const r = await sv.feedbackReplies(); items = (r && r.items) || []; } catch (_) { items = []; }
  list.innerHTML = '';
  if (!items.length) { empty.hidden = false; return; }
  empty.hidden = true;
  for (const it of items) {
    const card = document.createElement('div');
    card.className = 'fbreply';
    const status = it.review_decision ? (FB_DECISION[it.review_decision] || it.review_decision) : '待处理';
    const head = document.createElement('div'); head.className = 'fbreply-h';
    head.innerHTML = `<span class="fbreply-q">${(it.free_text_preview || '').replace(/[<>&]/g, '')}</span><span class="fbreply-s">${status}</span>`;
    card.appendChild(head);
    const meta = document.createElement('div'); meta.className = 'fbreply-m';
    meta.textContent = `提交于 ${_fbTime(it.created_at)}`;
    card.appendChild(meta);
    if (it.admin_reply) {
      const rep = document.createElement('div'); rep.className = 'fbreply-r';
      rep.innerHTML = `<span class="fbreply-rl">官方回复</span>`;
      const body = document.createElement('div'); body.className = 'fbreply-rb'; body.textContent = it.admin_reply;
      rep.appendChild(body);
      card.appendChild(rep);
    }
    list.appendChild(card);
  }
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
  if (s.state === 'error') $('svErrorTitle').textContent = s.detail || t('overview.start_failed');
  if ($('localGuide')) $('localGuide').hidden = !(cfg && cfg.mode === 'local' && s.state === 'stopped');
  // 服务转入运行后,本地账户信息才可读 → 拉一次(_localAcct 缓存,避免重复拉)。
  if (s.state === 'running' && cfg && cfg.mode === 'local' && _localAcct === null) loadLocalAccount();
  refreshBackupGate();
}

// ── 模式 ──
function renderMode() {
  const local = cfg.mode === 'local';
  document.querySelectorAll('.modeseg button').forEach((b) => b.classList.toggle('active', b.dataset.mode === cfg.mode));
  $('localSvc').hidden = !local;
  $('lanShareRow').hidden = !local;   // 局域网分享(复制地址+二维码)仅本地模式
  if ($('magicChkWrap')) $('magicChkWrap').hidden = !local;
  if ($('cloudAccount')) $('cloudAccount').hidden = local;
  if ($('magicLink')) $('magicLink').checked = cfg.magicLink !== false;
  $('modeHint').textContent = local ? t('mode.local_hint') : t('mode.online_hint');
  if (local) loadLocalAccount(); else loadCloudAccount();
  renderStatus(last);
}
async function setMode(mode) { cfg = await sv.setConfig({ mode }); renderMode(); }

// ── 账户(本地 / 云端)──
function _setAvatar(el, name, path, base) {
  if (!el) return;
  if (path) { el.style.backgroundImage = `url("${base || ''}${path}")`; el.textContent = ''; }
  else { el.style.backgroundImage = ''; el.textContent = ((name || '?').trim().charAt(0)) || '?'; }
}
let _localAcct = null;
async function loadLocalAccount() {
  if (!$('localName')) return;
  if (last.state !== 'running') { $('localName').textContent = t('account.start_first'); $('localSub').textContent = ''; _setAvatar($('localAvatar'), '?', null); return; }
  try {
    const r = await sv.localAccount();
    const a = r && r.account;
    if (!a || !a.exists) return;
    _localAcct = a;
    const base = last.backendPort ? `http://127.0.0.1:${last.backendPort}` : '';
    $('localName').textContent = a.display_name || a.username;
    $('localSub').textContent = `@${a.username} · ` + (a.has_password ? t('account.pw_set') : t('account.pw_none'));
    _setAvatar($('localAvatar'), a.display_name || a.username, a.avatar_path, base);
  } catch (_) {}
}
let _cloudUser = null;
async function loadCloudAccount() {
  if (!$('cloudName')) return;
  try {
    const r = await sv.cloudMe();
    const u = r && r.user;
    if (u) {
      _cloudUser = u;
      $('cloudName').textContent = u.display_name || u.username;
      $('cloudSub').textContent = '@' + u.username;
      _setAvatar($('cloudAvatar'), u.display_name || u.username, u.avatar_path, r.base || '');
      $('cloudLoginBtn').hidden = true; $('cloudLogoutBtn').hidden = false;
    } else {
      _cloudUser = null;
      $('cloudName').textContent = t('account.not_logged_in'); $('cloudSub').textContent = '';
      _setAvatar($('cloudAvatar'), '?', null);
      $('cloudLoginBtn').hidden = false; $('cloudLogoutBtn').hidden = true;
    }
  } catch (_) {}
}

// ── 统一重启(查活跃导入任务 → 弹窗确认强制)──
async function doRestart() {
  let r;
  try { r = await sv.restart(); } catch (_) { return; }
  if (r && r.needsConfirm) {
    const names = (r.activeTasks || []).join('、');
    if (window.confirm(t('restart.confirm').replace('{tasks}', names))) {
      try { await sv.restart({ force: true }); } catch (_) {}
    }
  }
}

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
// 本地部署可调参数(自然语言列表)。值落 cfg.extraEnv → supervisor 注入后端环境。
// 全局 key 走后端 RPG_KEY_<API_ID> 约定(user_credentials.resolve_api_key,仅本地模式)。
const ENV_PARAMS = [
  { key: 'RPG_KEY_OPENAI', label: '全局 OpenAI Key', desc: '自部署全局密钥;留空则各用户在应用内自带(BYOK)。', restart: true, secret: true },
  { key: 'RPG_KEY_ANTHROPIC', label: '全局 Anthropic (Claude) Key', desc: '同上。', restart: true, secret: true },
  { key: 'RPG_KEY_GOOGLE_AI_STUDIO', label: '全局 Google AI Studio (Gemini) Key', desc: '同上。', restart: true, secret: true },
  { key: 'RPG_KEY_DEEPSEEK', label: '全局 DeepSeek Key', desc: '同上。', restart: true, secret: true },
  { key: 'EMBED_API_KEY', label: '平台嵌入向量 Key', desc: '知识库检索用嵌入模型密钥。', restart: true, secret: true },
  { key: 'EMBED_DIM', label: '嵌入向量维度', desc: '默认 768;改后需重嵌知识库。', restart: true, def: '768' },
  { key: 'RPG_VERTEX_EXPLICIT_CACHE', label: 'Vertex 显式缓存', desc: '1=开(默认)/0=关。', restart: true, def: '1' },
];
function renderParams() {
  const box = $('paramsList'); if (!box) return;
  const env = cfg.extraEnv || {};
  box.innerHTML = '';
  for (const p of ENV_PARAMS) {
    const row = document.createElement('div'); row.className = 'paramrow';
    const head = document.createElement('div'); head.className = 'paramhead';
    head.innerHTML = `<span class="paramlabel"></span>${p.restart ? `<span class="paramtag">${t('config.needs_restart')}</span>` : ''}`;
    head.querySelector('.paramlabel').textContent = p.label;
    const desc = document.createElement('div'); desc.className = 'paramdesc muted-2'; desc.textContent = p.desc;
    const inp = document.createElement('input');
    inp.type = p.secret ? 'password' : 'text'; inp.className = 'mono'; inp.dataset.pkey = p.key;
    inp.value = env[p.key] != null ? env[p.key] : '';
    inp.placeholder = p.def ? `默认 ${p.def}` : (p.secret ? '未设置' : '');
    row.appendChild(head); row.appendChild(desc); row.appendChild(inp);
    box.appendChild(row);
  }
}
function fillForm() {
  $('cfgOnlineUrl').value = cfg.onlineUrl || '';
  $('cfgBackendPort').value = cfg.backendPort || 0;
  $('cfgChannel').value = cfg.updateChannel || 'stable';
  if ($('cfgLang')) $('cfgLang').value = cfg.uiLanguage || '';
  $('cfgAutoStart').checked = !!cfg.autoStartLocal;
  const paramKeys = new Set(ENV_PARAMS.map((p) => p.key));
  $('cfgExtraEnv').value = Object.entries(cfg.extraEnv || {}).filter(([k]) => !paramKeys.has(k)).map(([k, v]) => `${k}=${v}`).join('\n');
  renderParams();
}
function parseEnv(text) {
  const out = {};
  for (const raw of String(text).split('\n')) { const line = raw.trim(); if (!line || line.startsWith('#')) continue; const i = line.indexOf('='); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
  return out;
}
async function saveCfg() {
  const prevEnv = cfg.extraEnv || {};
  const env = parseEnv($('cfgExtraEnv').value);   // 高级 raw 作底,参数列表覆盖其上
  let restartNeeded = false;
  document.querySelectorAll('#paramsList input[data-pkey]').forEach((inp) => {
    const k = inp.dataset.pkey, v = (inp.value || '').trim();
    if (v) env[k] = v; else delete env[k];
    const p = ENV_PARAMS.find((x) => x.key === k);
    if (p && p.restart && (prevEnv[k] || '') !== v) restartNeeded = true;
  });
  const lang = $('cfgLang') ? $('cfgLang').value : (cfg.uiLanguage || '');
  const langChanged = (cfg.uiLanguage || '') !== lang;
  cfg = await sv.setConfig({
    onlineUrl: $('cfgOnlineUrl').value.trim() || 'https://rpg-roleplay.stellatrix.icu',
    backendPort: parseInt($('cfgBackendPort').value, 10) || 0,
    updateChannel: $('cfgChannel').value, autoStartLocal: $('cfgAutoStart').checked,
    uiLanguage: lang, extraEnv: env,
  });
  if (langChanged && window.I18N) { window.I18N.setLang(lang); renderMode(); }
  fillForm(); flash($('saveCfgBtn'), t('common.saved')); $('updCurrent').textContent = `当前 v${appVer} · ${cfg.updateChannel}`;
  if (restartNeeded && cfg.mode === 'local' && last.state === 'running') {
    if (window.confirm(t('restart.settings_changed'))) await doRestart();
  }
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
// 备份页显示当前账户 + 云端数据迁移区(避免误解备份归属;在线已登录时可双向同步)。
async function loadBackupAccount() {
  const banner = $('bkAcct'); if (!banner) return;
  const sync = $('cloudSyncSection');
  if (cfg.mode === 'local') {
    banner.hidden = false;
    if (_localAcct) {
      $('bkAcctName').textContent = _localAcct.display_name || _localAcct.username;
      $('bkAcctSub').textContent = '@' + _localAcct.username + ' · ' + t('account.local');
      _setAvatar($('bkAvatar'), _localAcct.display_name || _localAcct.username, _localAcct.avatar_path, last.backendPort ? `http://127.0.0.1:${last.backendPort}` : '');
    } else { $('bkAcctName').textContent = '—'; $('bkAcctSub').textContent = t('account.local'); _setAvatar($('bkAvatar'), 'L', null); }
    if (sync) {
      sync.hidden = false;
      const me = await sv.cloudMe().catch(() => null);
      const logged = me && me.user;
      $('cloudSyncRow').hidden = !logged;
      $('cloudSyncHint').textContent = logged ? `${t('account.cloud')}:${me.user.display_name || me.user.username}` : t('backup.cloud_need_login');
    }
  } else {
    if (sync) sync.hidden = true;
    const me = await sv.cloudMe().catch(() => null);
    if (me && me.user) {
      banner.hidden = false;
      $('bkAcctName').textContent = me.user.display_name || me.user.username;
      $('bkAcctSub').textContent = '@' + me.user.username + ' · ' + t('account.cloud');
      _setAvatar($('bkAvatar'), me.user.display_name || me.user.username, me.user.avatar_path, me.base || '');
    } else banner.hidden = true;
  }
}
function fillBackup() {
  $('backupDir').value = cfg.backupDir || '';
  $('autoBackup').checked = !!cfg.autoBackup;
  $('autoBackupHours').value = cfg.autoBackupHours || 168;
  $('backupKeep').value = cfg.backupKeep || 3;
}

// ── 局域网 ──
async function loadLan() {
  $('lanEnabled').checked = !!cfg.lanEnabled;
  try { const r = await sv.lanInfo(); $('lanUrl').value = r.url || '—'; $('fwCmd').textContent = r.firewallCmd || '—'; } catch (_) {}
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
  if ((u.state === 'available' || u.state === 'downloaded') && u.notes) { $('updNotes').hidden = false; $('updNotesBody').innerHTML = mdToHtml(u.notes); }
  else if (u.state === 'checking' || u.state === 'none' || u.state === 'error') { $('updNotes').hidden = true; }
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
    if (r && r.ok) { $('fbForm').hidden = true; $('fbSuccess').hidden = false; loadFeedbackReplies(); }
    else { $('fbErr').hidden = false; $('fbErr').textContent = '提交失败:' + ((r && (r.detail || r.error)) || '请稍后再试'); fbValidate(); }
  } catch (e) { $('fbErr').hidden = false; $('fbErr').textContent = '提交失败:' + (e && e.message || '网络错误'); fbValidate(); }
}

// ── 首次运行向导 ──
function maybeOnboard() {
  if (cfg.onboarded && cfg.rememberMode) return;   // 记住选择时直接进上次模式;否则每次问
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
  if (window.I18N) window.I18N.setLang(cfg.uiLanguage || '');   // 设语言 + 翻译静态 DOM(在 renderMode 前,t() 可用)
  renderMode(); fillForm(); fillBackup();
  $('rememberMode').checked = !!cfg.rememberMode;
  $('updCurrent').textContent = `当前 v${appVer} · ${cfg.updateChannel || 'stable'}`;
  renderStatus(await sv.status());
  (await sv.logs()).forEach(appendLog);
  maybeOnboard();

  document.querySelectorAll('.navitem').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  document.querySelectorAll('.modeseg button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));

  // overview
  $('openExtBtn').addEventListener('click', () => sv.openAppExternal());
  $('sideOpenBrowser').addEventListener('click', () => sv.openAppExternal());
  $('rememberMode').addEventListener('change', async () => { cfg = await sv.setConfig({ rememberMode: $('rememberMode').checked }); });
  $('copyLanBtn').addEventListener('click', async () => { const r = await sv.lanInfo(); const ok = await copy((r && r.url) || ''); flash($('copyLanBtn'), ok ? '已复制地址' : '复制失败'); });
  // 二维码:hover 右侧 QR 区弹出供手机扫码(延时关闭跨越间隙)
  let _qrLoaded = false, _qrT;
  const _showQr = async () => { clearTimeout(_qrT); $('qrPop').hidden = false; if (!_qrLoaded) { try { const r = await sv.lanQr(); if (r && r.ok) { $('qrImg').src = r.dataUrl; _qrLoaded = true; } } catch (_) {} } };
  const _hideQr = () => { _qrT = setTimeout(() => { $('qrPop').hidden = true; }, 180); };
  $('qrBtn').addEventListener('mouseenter', _showQr);
  $('qrBtn').addEventListener('mouseleave', _hideQr);
  $('qrPop').addEventListener('mouseenter', () => clearTimeout(_qrT));
  $('qrPop').addEventListener('mouseleave', _hideQr);
  $('startBtn').addEventListener('click', async () => { await sv.start().catch(() => {}); loadLocalAccount(); });
  $('stopBtn').addEventListener('click', () => sv.stop().catch(() => {}));
  $('restartBtn').addEventListener('click', doRestart);
  $('retryBtn').addEventListener('click', () => sv.start().catch(() => {}));
  if ($('magicLink')) $('magicLink').addEventListener('change', async () => { cfg = await sv.setConfig({ magicLink: $('magicLink').checked }); });
  // 本地账户:改用户名/昵称
  if ($('localRenameBtn')) $('localRenameBtn').addEventListener('click', () => {
    $('localProfileForm').hidden = !$('localProfileForm').hidden; $('localPwForm').hidden = true;
    if (_localAcct) { $('localUserInput').value = _localAcct.username || ''; $('localNameInput').value = _localAcct.display_name || ''; }
  });
  if ($('localProfileCancel')) $('localProfileCancel').addEventListener('click', () => { $('localProfileForm').hidden = true; });
  if ($('localProfileSave')) $('localProfileSave').addEventListener('click', async () => {
    $('localProfileErr').hidden = true;
    const r = await sv.localSetProfile({ username: $('localUserInput').value.trim(), display_name: $('localNameInput').value.trim() });
    if (r && r.ok) { $('localProfileForm').hidden = true; await loadLocalAccount(); }
    else { $('localProfileErr').hidden = false; $('localProfileErr').textContent = (r && (r.error || r.detail)) || '保存失败'; }
  });
  // 本地账户:设/重置密码
  if ($('localPwBtn')) $('localPwBtn').addEventListener('click', () => { $('localPwForm').hidden = !$('localPwForm').hidden; $('localProfileForm').hidden = true; $('localPwInput').value = ''; $('localPwMsg').hidden = true; });
  if ($('localPwCancel')) $('localPwCancel').addEventListener('click', () => { $('localPwForm').hidden = true; });
  if ($('localPwSave')) $('localPwSave').addEventListener('click', async () => {
    const r = await sv.localSetPassword({ password: $('localPwInput').value });
    $('localPwMsg').hidden = false;
    $('localPwMsg').textContent = r && r.ok ? (r.has_password ? '已设密码' : '已清除密码') : ((r && (r.error || r.detail)) || '失败');
    $('localPwMsg').className = 'hint ' + (r && r.ok ? 'ok' : 'danger');
    if (r && r.ok) { setTimeout(() => { $('localPwForm').hidden = true; }, 900); await loadLocalAccount(); }
  });
  // 云端账户:登录 / 登出
  if ($('cloudLoginBtn')) $('cloudLoginBtn').addEventListener('click', () => { $('cloudLoginForm').hidden = false; $('cloudLoginErr').hidden = true; });
  if ($('cloudLoginCancel')) $('cloudLoginCancel').addEventListener('click', () => { $('cloudLoginForm').hidden = true; });
  if ($('cloudLoginSubmit')) $('cloudLoginSubmit').addEventListener('click', async () => {
    $('cloudLoginErr').hidden = true; $('cloudLoginSubmit').disabled = true;
    const r = await sv.cloudLogin({ username: $('cloudUser').value.trim(), password: $('cloudPass').value });
    $('cloudLoginSubmit').disabled = false;
    if (r && r.ok) { $('cloudLoginForm').hidden = true; $('cloudPass').value = ''; await loadCloudAccount(); refreshBackupGate(); }
    else { $('cloudLoginErr').hidden = false; $('cloudLoginErr').textContent = (r && r.error) || t('account.login_failed'); }
  });
  if ($('cloudLogoutBtn')) $('cloudLogoutBtn').addEventListener('click', async () => { await sv.cloudLogout(); await loadCloudAccount(); refreshBackupGate(); });
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
  $('saveBackupBtn').addEventListener('click', async () => { cfg = await sv.setConfig({ autoBackup: $('autoBackup').checked, autoBackupHours: parseInt($('autoBackupHours').value, 10) || 168, backupKeep: Math.max(1, parseInt($('backupKeep').value, 10) || 3) }); flash($('saveBackupBtn'), '已保存'); });
  $('backupNowBtn').addEventListener('click', async () => { $('backupNowBtn').disabled = true; const r = await sv.backupNow(); flash($('backupNowBtn'), r && r.ok ? '已备份' : '失败'); refreshBackupGate(); });
  // 云端数据迁移(合并导入,新增不覆盖)
  const _cloudHost = () => { try { return new URL(cfg.onlineUrl || 'https://rpg-roleplay.stellatrix.icu').host; } catch (_) { return cfg.onlineUrl || ''; } };
  if ($('syncFromLocalBtn')) $('syncFromLocalBtn').addEventListener('click', async () => {
    // 安全:明确告知数据上传目的地(云端地址用户可改,防被诱导改成攻击者站点而无感)。
    if (!window.confirm(`把本机数据合并上传到云端【${_cloudHost()}】(新增,不覆盖云端已有)。请确认目的地正确,继续?`)) return;
    $('cloudSyncMsg').hidden = false; $('cloudSyncMsg').textContent = '同步中…'; $('syncFromLocalBtn').disabled = true; $('syncToLocalBtn').disabled = true;
    const r = await sv.cloudSyncFromLocal();
    $('syncFromLocalBtn').disabled = false; $('syncToLocalBtn').disabled = false;
    $('cloudSyncMsg').textContent = r && r.ok ? '已提交到云端(后台导入中)' : ('失败:' + ((r && (r.error || r.detail)) || '请重试'));
  });
  if ($('syncToLocalBtn')) $('syncToLocalBtn').addEventListener('click', async () => {
    if (!window.confirm('把云端账户数据合并导入到本机(新增,不覆盖本机已有)。继续?')) return;
    $('cloudSyncMsg').hidden = false; $('cloudSyncMsg').textContent = '同步中…'; $('syncFromLocalBtn').disabled = true; $('syncToLocalBtn').disabled = true;
    const r = await sv.cloudSyncToLocal();
    $('syncFromLocalBtn').disabled = false; $('syncToLocalBtn').disabled = false;
    $('cloudSyncMsg').textContent = r && r.ok ? '已导入到本机' : ('失败:' + ((r && (r.error || r.detail)) || '请重试'));
    refreshBackupGate();
  });

  // lan
  $('lanEnabled').addEventListener('change', async () => { cfg = await sv.setConfig({ lanEnabled: $('lanEnabled').checked }); loadLan(); });
  $('copyLanUrlBtn').addEventListener('click', async () => { await copy($('lanUrl').value); flash($('copyLanUrlBtn'), '已复制'); });
  $('copyFwBtn').addEventListener('click', async () => { await copy($('fwCmd').textContent); flash($('copyFwBtn'), '已复制'); });

  // config
  $('advToggle').addEventListener('click', () => { $('advBox').hidden = !$('advBox').hidden; });
  if ($('cfgLang')) $('cfgLang').addEventListener('change', async () => {
    const lang = $('cfgLang').value;
    cfg = await sv.setConfig({ uiLanguage: lang });
    if (window.I18N) { window.I18N.setLang(lang); renderMode(); fillForm(); $('tabTitle').textContent = t('nav.' + _activeTab); }
  });
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
  if ($('fbReplyRefresh')) $('fbReplyRefresh').addEventListener('click', () => loadFeedbackReplies());

  sv.onStatus(renderStatus); sv.onLog(appendLog); sv.onUpdate(renderUpdate);
}
init().catch((e) => { document.body.insertAdjacentHTML('afterbegin', `<pre style="color:#c8675d;padding:12px">初始化失败: ${e && e.message}</pre>`); });
