// 全局命令面板 / 统一搜索。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { credApiIdSet } from '../catalog-helpers.js';
import { MODELS_DATA } from '../../pages/settings.jsx';
import {
  usePlatformData, useReactiveUser,
} from './shared.jsx';

function UnifiedSearch({ open, onClose, setPage }) {
  const { t: tSearch } = useTranslation();
  const [q, setQ] = useStatePL("");
  const [activeIdx, setActiveIdx] = useStatePL(0);
  const searchUser = useReactiveUser();
  const isAdmin = searchUser && searchUser.role === 'admin';
  const inputRef = React.useRef(null);
  React.useEffect(() => {
    if (open) { setQ(""); setActiveIdx(0); setTimeout(() => inputRef.current?.focus(), 30); }
  }, [open]);

  // Fix 2: 接后端 /api/search — 300ms debounce,401 时静默兜底空数组
  const [apiResults, setApiResults] = useStatePL({ groups: [] });
  useEffectPL(() => {
    if (!q || q.length < 1) { setApiResults({ groups: [] }); return; }
    const t = setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(q)}&scope=all`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : { groups: [] })
        .then(data => setApiResults(data && Array.isArray(data.groups) ? data : { groups: [] }))
        .catch(() => setApiResults({ groups: [] }));
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  // 模型/Provider 搜索条目:登录用户读真实目录 /api/models(只列已配 key 的 provider,
  // 与 AgentModelPicker 收敛行为一致),不再把 settings.jsx 的 MODELS_DATA 假目录
  // (GPT-5.5/Claude Opus 4.7/35 条 OpenRouter 假模型名…)无条件塞进 Spotlight。
  // 仅 ?demo=1 或匿名访客(设计预览)才回退 MODELS_DATA,与 settings.jsx 的 useMock 同闸。
  const _IS_DEMO = new URLSearchParams(location.search).get('demo') === '1';
  const _IS_ANON = !(searchUser && searchUser.id);
  const _useMockModels = _IS_DEMO || _IS_ANON;
  const [modelCatalog, setModelCatalog] = useStatePL(null); // null=未加载;数组=真实 provider 目录
  useEffectPL(() => {
    if (_useMockModels) { setModelCatalog(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const [models, creds] = await Promise.all([
          window.api.models.list().catch(() => ({})),
          window.api.credentials.list().catch(() => ({ items: [] })),
        ]);
        if (cancelled) return;
        const list = models?.models?.apis || (Array.isArray(models?.apis) ? models.apis : []) || [];
        // 只保留用户已配凭据的 provider(AgentPlatform→vertex_ai canonical,与 AgentModelPicker 一致)。
        const credIds = credApiIdSet(creds);
        const filtered = (Array.isArray(list) ? list : []).filter(a => credIds.has((a.api_id || a.id || '').trim()));
        setModelCatalog(filtered);
      } catch (_) { if (!cancelled) setModelCatalog([]); }
    })();
    return () => { cancelled = true; };
  }, [_useMockModels]);

  const platform = usePlatformData();  // task 45

  const pages = [
    { id: "profile",  label: tSearch('platform.nav.profile'),         kind: "page", icon: "home",     keywords: "home dashboard" },
    { id: "me",       label: tSearch('platform.nav.me'),              kind: "page", icon: "user",     keywords: "me profile account" },
    { id: "me-edit",  label: tSearch('platform.nav.me_edit_full'),    kind: "page", icon: "edit",     keywords: "edit profile avatar" },
    { id: "me-settings", label: tSearch('platform.nav.me_settings_full'), kind: "page", icon: "settings", keywords: "privacy security 2fa" },
    { id: "scripts",  label: tSearch('platform.nav.scripts'),         kind: "page", icon: "book",     keywords: "scripts import" },
    { id: "cards",    label: tSearch('platform.nav.cards'),           kind: "page", icon: "cards",    keywords: "characters card user npc" },
    { id: "cards-npc", label: tSearch('platform.nav.cards_npc_full'), kind: "page", icon: "cards",   keywords: "npc characters" },
    { id: "saves",    label: tSearch('platform.nav.saves'),           kind: "page", icon: "play",     keywords: "saves continue" },
    { id: "saves-branches", label: tSearch('platform.nav.saves_branches_full'), kind: "page", icon: "branch", keywords: "branches tree fork" },
    // W3-C2: 文件库开放
    { id: "library",  label: tSearch('platform.nav.library'),         kind: "page", icon: "folder",   keywords: "library files assets 文件库" },
    { id: "settings", label: tSearch('platform.nav.settings'),        kind: "page", icon: "settings", keywords: "settings preferences" },
    { id: "usage",    label: tSearch('platform.nav.usage'),           kind: "page", icon: "usage",    keywords: "usage tokens cost" },
    { id: "plugins",  label: tSearch('platform.nav.plugins'),         kind: "page", icon: "plug",     keywords: "plugins extensions" },
    { id: "mcp",      label: "MCP",                                    kind: "page", icon: "diamond",  keywords: "mcp server" },
    { id: "skills",   label: "Skill",                                  kind: "page", icon: "spark",    keywords: "skills hooks" },
    { id: "apis",     label: "API",                                    kind: "page", icon: "braces",   keywords: "api endpoints" },
  ];

  const adminLabel = tSearch('platform.nav.admin');
  const settingsLabel = tSearch('platform.nav.settings');
  const settingsItems = [
    { id: "preferences", label: tSearch('platform.nav.settings_preferences'), parent: settingsLabel, hash: "settings", keywords: "language font density theme" },
    { id: "models",      label: tSearch('platform.nav.settings_models'),       parent: settingsLabel, hash: "settings", keywords: "openai anthropic models api" },
    { id: "memory",      label: tSearch('platform.nav.settings_memory'),       parent: settingsLabel, hash: "settings", keywords: "memory recall context" },
    { id: "permissions", label: tSearch('platform.nav.settings_permissions'),  parent: settingsLabel, hash: "settings", keywords: "permission write structured" },
    { id: "danger",      label: tSearch('platform.nav.settings_danger'),       parent: settingsLabel, hash: "settings", keywords: "danger reset delete" },
    // 部署配置已拆到「系统管理」,仅 admin 可见
    ...(isAdmin ? [
      { id: "deploy",        label: tSearch('platform.nav.admin_deploy'),        parent: adminLabel, hash: "admin-deploy",        keywords: "host port cors upload deploy admin" },
      { id: "admin-users",   label: tSearch('platform.nav.admin_users'),         parent: adminLabel, hash: "admin-users",         keywords: "users ban role deactivate admin" },
      { id: "admin-usage",   label: tSearch('platform.nav.admin_usage'),         parent: adminLabel, hash: "admin-usage",         keywords: "global usage token cost admin" },
      { id: "admin-audit",   label: tSearch('platform.nav.admin_audit'),         parent: adminLabel, hash: "admin-audit",         keywords: "audit log admin action" },
      { id: "admin-health",  label: tSearch('platform.nav.admin_health'),        parent: adminLabel, hash: "admin-health",        keywords: "health db memory disk process" },
      { id: "admin-logs",    label: tSearch('platform.nav.admin_logs'),          parent: adminLabel, hash: "admin-logs",          keywords: "logs system stderr stdout" },
      { id: "admin-reg",     label: tSearch('platform.nav.admin_registration'),  parent: adminLabel, hash: "admin-registration",  keywords: "registration invite code signup" },
      { id: "admin-sec",     label: tSearch('platform.nav.admin_security'),      parent: adminLabel, hash: "admin-security",      keywords: "ip blocklist rate limit password policy" },
      { id: "admin-maint",   label: tSearch('platform.nav.admin_maintenance'),   parent: adminLabel, hash: "admin-maintenance",   keywords: "maintenance mode announcement restart" },
      { id: "admin-dmca-td", label: tSearch('platform.nav.admin_dmca_takedowns'),parent: adminLabel, hash: "admin-dmca-takedowns",keywords: "dmca takedown notice copyright admin" },
      { id: "admin-dmca-sk", label: tSearch('platform.nav.admin_dmca_strikes'),  parent: adminLabel, hash: "admin-dmca-strikes",  keywords: "dmca strike repeat offender admin" },
      { id: "admin-csam",    label: tSearch('platform.nav.admin_csam_reports'),  parent: adminLabel, hash: "admin-csam-reports",  keywords: "csam report child abuse admin" },
      { id: "admin-aup",     label: tSearch('platform.nav.admin_aup_actions'),   parent: adminLabel, hash: "admin-aup-actions",   keywords: "aup suspend ban terminate policy admin" },
      { id: "admin-feedback",label: tSearch('platform.nav.admin_feedback'),      parent: adminLabel, hash: "admin-feedback",      keywords: "feedback review user report admin" },
      { id: "admin-achv",    label: tSearch('platform.nav.admin_achievements'),  parent: adminLabel, hash: "admin-achievements",  keywords: "achievement badge milestone catalog 成就 徽章 admin" },
    ] : []),
  ];

  const scripts = (platform.scripts || []).map(s => ({
    id: "scr-" + s.id, label: s.title, kind: "script",
    sub: `${Number(s.chapter_count || 0).toLocaleString()} ${tSearch('platform.search.unit_chapters', '章')} · ${((s.word_count || 0) / 10000).toFixed(1)}${tSearch('platform.search.unit_wan_chars', '万字')}`,
    icon: "book", keywords: s.uid + " " + s.description,
    hash: "scripts",
  }));

  const saves = (platform.saves || []).map(s => ({
    id: "sv-" + s.id, label: s.title, kind: "save",
    sub: `${s.branch_count} ${tSearch('platform.search.unit_nodes', '节点')} · ${s.updated_at}`,
    icon: "play", keywords: s.uid,
    hash: "saves",
  }));

  // Fix 2: 用户数据从 /api/search 后端取,此处不再硬编码角色卡/世界书/记忆静态数据
  const _apiKindMeta = {
    scripts:   { kind: "script",    icon: "book",    hash: "scripts" },
    saves:     { kind: "save",      icon: "play",    hash: "saves" },
    cards:     { kind: "character", icon: "cards",   hash: "cards" },
    npc_cards: { kind: "character", icon: "cards",   hash: "cards-npc" },
    worldbook: { kind: "world",     icon: "world",   hash: "scripts" },
    memories:  { kind: "memory",    icon: "pin",     hash: "settings" },
  };
  const apiItems = (apiResults.groups || []).flatMap(g => {
    const meta = _apiKindMeta[g.kind] || { kind: g.kind, icon: "file", hash: "profile" };
    return (g.items || []).map((item, i) => ({
      id: `api-${g.kind}-${item.id ?? i}`,
      label: item.label || item.name || String(item.id),
      sub: item.sub || undefined,
      kind: meta.kind,
      icon: meta.icon,
      hash: meta.hash,
      keywords: "",
    }));
  });

  // 登录用户用真实目录(modelCatalog,只含已配 key 的 provider);demo/anon 才用 MODELS_DATA。
  // 真实条目字段(/api/models):provider 用 api_id/display_name/base_url/models[]{real_name,display_name}。
  const _modelSrc = _useMockModels
    ? MODELS_DATA.map(a => ({
        id: a.id, name: a.name, base_url: a.base_url,
        models: (a.models || []).map(m => ({ real_name: m.real_name, display: m.display })),
      }))
    : (Array.isArray(modelCatalog) ? modelCatalog.map(a => ({
        id: a.api_id || a.id, name: a.display_name || a.name || (a.api_id || a.id),
        base_url: a.base_url || '',
        models: (a.models || a.entries || []).map(m => ({
          real_name: m.real_name || m.id,
          display: m.display_name || m.real_name || m.id,
        })),
      })) : []);

  const models = [];
  _modelSrc.forEach(api => {
    (api.models || []).slice(0, 3).forEach(m => {
      models.push({
        id: "m-" + api.id + "-" + m.real_name, label: m.display, kind: "model",
        sub: `${api.name} · ${m.real_name}`,
        icon: "sparkle", keywords: m.real_name + " " + api.name,
        hash: "settings",
      });
    });
  });

  const apis = _modelSrc.map(a => ({
    id: "api-" + a.id, label: a.name, kind: "api",
    sub: `${(a.models || []).length} ${tSearch('platform.search.unit_models', '模型')}${a.base_url ? ' · ' + a.base_url : ''}`,
    icon: "braces", keywords: a.id,
    hash: "settings",
  }));

  // task 48: library 读真 platform.recent_assets(未纳入 /api/search,保留本地派生)
  const lib = (() => {
    const recent = (platform && Array.isArray(platform.recent_assets)) ? platform.recent_assets : [];
    return recent.slice(0, 8).map((f, i) => ({
      id: `f-${f.id || i}`, label: f.name || f.path,
      sub: `${window.__fmt?.bytes ? window.__fmt.bytes(f.size || 0) : (f.size || 0) + " B"} · ${window.__fmt?.ago ? window.__fmt.ago(f.updated_at) : ""}`,
      kind: "library", icon: f.kind === "folder" ? "folder" : f.kind === "image" ? "image" : "file", hash: "library",
    }));
  })();

  // Fix 2: allItems = 静态导航(pages + settings + models + apis) + 后端搜索结果(apiItems) + library
  const allItems = [
    ...pages, ...settingsItems, ...models.slice(0, 8), ...apis,
    ...apiItems, ...lib,
  ];

  const lower = q.toLowerCase();
  const filtered = !q ? [] : allItems.filter(it =>
    it.label.toLowerCase().includes(lower) ||
    (it.sub || "").toLowerCase().includes(lower) ||
    (it.keywords || "").toLowerCase().includes(lower) ||
    (it.parent || "").toLowerCase().includes(lower)
  );

  const groups = {};
  const order = ["page", "script", "save", "character", "world", "memory", "model", "api", "library"];
  const labels = {
    page: tSearch('platform.search.category_pages'),
    script: tSearch('platform.search.category_scripts'),
    save: tSearch('platform.search.category_saves'),
    character: tSearch('platform.search.category_cards'),
    world: tSearch('platform.search.category_worldbook'),
    memory: tSearch('platform.search.category_memories'),
    model: tSearch('platform.search.category_models'),
    api: tSearch('platform.search.category_apis'),
    library: tSearch('platform.search.category_library'),
  };
  filtered.forEach(it => {
    const key = it.kind === "page" && it.parent ? "page" : it.kind;
    (groups[key] = groups[key] || []).push(it);
  });

  const flatList = order.flatMap(k => groups[k] || []);
  const cursor = Math.max(0, Math.min(activeIdx, flatList.length - 1));

  const pick = React.useCallback((it) => {
    if (it.kind === "page") { setPage(it.id); }
    else if (it.hash) { setPage(it.hash); }
    onClose();
  }, [setPage, onClose]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, flatList.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, 0)); }
      else if (e.key === "Enter") { e.preventDefault(); if (flatList[cursor]) pick(flatList[cursor]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flatList, cursor, pick, onClose]);

  if (!open) return null;

  let flatIdx = -1;
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pl-search-head">
          <Icon name="search" size={14} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); setActiveIdx(0); }}
            placeholder={tSearch('platform.search.placeholder')} aria-label={tSearch('platform.search.placeholder')}
          />
          <span className="pl-search-kbd">
            <span className="kbd">Esc</span>
          </span>
        </div>
        <div className="pl-search-body">
          {!q && (
            <div className="pl-search-empty">
              <div className="muted-2" style={{fontSize: 11, textTransform: "uppercase", letterSpacing: "0.14em", padding: "10px 16px 6px"}}>{tSearch('platform.search.suggestions')}</div>
              {pages.slice(0, 6).map((p, i) => (
                <button key={p.id} className={`pl-search-row ${i === 0 ? "active" : ""}`} onClick={() => pick(p)}>
                  <span className="pl-search-icon"><Icon name={p.icon} size={14} /></span>
                  <span className="pl-search-label">{p.label}</span>
                  <span className="pl-search-meta muted-2">{tSearch('platform.search.category_pages')}</span>
                </button>
              ))}
            </div>
          )}
          {q && flatList.length === 0 && (
            <div className="pl-model-empty" style={{margin: 16}}>
              {tSearch('platform.search.no_results', { q, defaultValue: `未匹配「${q}」 · 试试 GPT、雾港、记忆、claude、API` })}
            </div>
          )}
          {q && order.map(kind => {
            const items = groups[kind];
            if (!items || items.length === 0) return null;
            return (
              <div key={kind} className="pl-search-group">
                <div className="pl-search-group-head">
                  {labels[kind]} <span className="muted-2 mono">{items.length}</span>
                </div>
                {items.map(it => {
                  flatIdx++;
                  const active = flatIdx === cursor;
                  return (
                    <button key={it.id} className={`pl-search-row ${active ? "active" : ""}`}
                      onClick={() => pick(it)}
                      onMouseEnter={() => setActiveIdx(flatIdx)}>
                      <span className="pl-search-icon"><Icon name={it.icon} size={14} /></span>
                      <span className="pl-search-label">
                        <Highlight text={it.label} q={q} />
                        {it.sub && <span className="muted-2" style={{marginLeft: 8, fontSize: 11.5}}>
                          <Highlight text={it.sub} q={q} />
                        </span>}
                      </span>
                      <span className="pl-search-meta muted-2">{labels[kind]}</span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
        <footer className="pl-search-foot">
          <div className="pl-search-kbds">
            <span><span className="kbd">↑↓</span> {tSearch('platform.search.kbd_select', '选择')}</span>
            <span><span className="kbd">⏎</span> {tSearch('platform.search.kbd_open', '打开')}</span>
            <span><span className="kbd">Esc</span> {tSearch('platform.search.kbd_close', '关闭')}</span>
          </div>
          <span className="muted-2" style={{fontSize: 11}}>
            GET /api/v1/search?q={encodeURIComponent(q) || "..."} · 全文 · 模糊
          </span>
        </footer>
      </div>
    </div>
  );
}

function Highlight({ text, q }) {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark style={{background: "var(--accent-soft)", color: "var(--accent)", padding: "0 2px", borderRadius: 2}}>
        {text.slice(i, i + q.length)}
      </mark>
      {text.slice(i + q.length)}
    </>
  );
}

export { UnifiedSearch };
