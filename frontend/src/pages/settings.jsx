/* Settings page — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / SettingsToggle / ConfirmModal / useAutoSave / usePlatformData / fmtN。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';
import { ConfirmModal, SettingsToggle, useAutoSave, usePlatformData, fmtN, ResizableSplit } from '../platform-app.jsx';
import { getCaps as _getCapsImported } from '../components/catalog-helpers.js';
// Cloudscape 原生组件(内容迁移,统一基线对齐)
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSToggle from '@cloudscape-design/components/toggle';
import CSAlert from '@cloudscape-design/components/alert';
import CSTable from '@cloudscape-design/components/table';
import CSBadge from '@cloudscape-design/components/badge';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
import CSModal from '@cloudscape-design/components/modal';

/* ── 设置页 Cloudscape 统一 primitives(取代 pl-set-group / pl-set-row) ──
   SetGroup = Container + Header(h2)  ·  SetRow = FormField(label 上 / 控件下)。
   各 section 用这两个套,保证全站基线对齐、间距一致。 */
function SetGroup({ title, description, actions, children }) {
  return (
    <CSContainer header={<CSHeader variant="h2" description={description} actions={actions}>{title}</CSHeader>}>
      {/* React.Children.toArray 给多子元素派稳定 key,避免 SpaceBetween 的 key 警告 */}
      <CSSpaceBetween size="l">{React.Children.toArray(children)}</CSSpaceBetween>
    </CSContainer>
  );
}
function SetRow({ label, description, children }) {
  return (
    <CSFormField label={label} description={description}>
      {children}
    </CSFormField>
  );
}
/* 简单 <select> → CSSelect 适配:options 为 [{value,label}] */
function SetSelect({ value, options, onChange, disabled }) {
  const sel = options.find((o) => o.value === value) || null;
  return (
    <CSSelect
      selectedOption={sel}
      options={options}
      disabled={disabled}
      onChange={({ detail }) => onChange(detail.selectedOption.value)}
    />
  );
}

/* ---------------------------- SETTINGS ------------------------- */
function SettingsPage({ section: sectionProp } = {}) {
  // 新 IA:section 由模块左栏(路由)驱动。传入 sectionProp 时隐藏内部导航。
  const { t } = useTranslation();
  const [sectionState, setSection] = useStatePL("preferences");
  const external = !!sectionProp;
  const section = sectionProp || sectionState;
  const SECTIONS = [
    { id: "preferences", label: t('settings.nav.preferences'), icon: "settings" },
    { id: "models",      label: t('settings.nav.models'),      icon: "sparkle" },
    { id: "modelparams", label: t('settings.nav.modelparams'), icon: "spark" },
    { id: "modules",     label: t('settings.nav.modules'),     icon: "spark" },
    { id: "memory",      label: t('settings.nav.memory'),      icon: "memory" },
    { id: "permissions", label: t('settings.nav.permissions'), icon: "lock" },
    { id: "deploy",      label: t('settings.nav.deploy'),      icon: "world" },
    { id: "danger",      label: t('settings.nav.danger'),      icon: "warn" },
  ];
  // task 57：助手 navigate_to_setting 触发 cap-navigate-subsection 事件
  // (settings.permissions → section="permissions"，settings.api → section="models")
  useEffectPL(() => {
    const handler = (ev) => {
      const target = ev && ev.detail && ev.detail.target;
      if (!target || typeof target !== "string") return;
      const parts = target.split(".");
      if (parts[0] !== "settings" || parts.length < 2) return;
      const sub = parts[1];
      const ALIASES = { "api": "models" };
      const normalized = ALIASES[sub] || sub;
      if (SECTIONS.some(s => s.id === normalized)) setSection(normalized);
    };
    window.addEventListener("cap-navigate-subsection", handler);
    return () => window.removeEventListener("cap-navigate-subsection", handler);
  }, []);
  const sectionLabel = (SECTIONS.find((s) => s.id === section) || {}).label || t('settings.title');
  return (
    <CSSpaceBetween size="l">
      {!external && (
        <CSHeader variant="h1">{t('settings.title')}</CSHeader>
      )}
      {!external && (
        <CSSpaceBetween direction="horizontal" size="xs">
          {SECTIONS.map((s) => (
            <CSButton key={s.id} variant={section === s.id ? 'primary' : 'normal'} onClick={() => setSection(s.id)}>
              {s.label}
            </CSButton>
          ))}
        </CSSpaceBetween>
      )}
      {external && <CSHeader variant="h1">{sectionLabel}</CSHeader>}
      {section === "preferences" && [<PrefSection key="pref" />, <ExtractorSection key="ext" />, <ClarifySection key="clar" />]}
      {section === "models" && <ModelsSection />}
      {section === "modelparams" && <ModelParamsSection />}
      {section === "modules" && <ModuleModelsSection />}
      {section === "memory" && <MemorySection />}
      {section === "permissions" && <PermSection />}
      {section === "deploy" && <DeploySection />}
      {section === "danger" && <DangerSection />}
    </CSSpaceBetween>
  );
}

function PrefSection() {
  // task 52：从 user_preferences 拉真实初值，改动直接 patch /api/me/preference。
  const { t } = useTranslation();
  const [interfaceLang, setInterfaceLang] = useStatePL("zh-CN");
  const [serif, setSerif] = useStatePL(true);
  const [auto, setAuto] = useStatePL(true);
  const save = useAutoSave(t('settings.nav.preferences'), "pref");
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        if (p["pref.ui_language"]) setInterfaceLang(p["pref.ui_language"]);
        else if (p.ui_language) setInterfaceLang(p.ui_language);
        if (typeof p["pref.serif"] === "boolean") setSerif(p["pref.serif"]);
        else if (typeof p.serif === "boolean") setSerif(p.serif);
        if (typeof p["pref.autosave"] === "boolean") setAuto(p["pref.autosave"]);
        else if (typeof p.autosave === "boolean") setAuto(p.autosave);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);
  return (
    <SetGroup title={t('settings.preferences.title')}>
      <SetRow label={t('settings.preferences.interface_lang')} description={t('settings.preferences.interface_lang_desc')}>
        <SetSelect value={interfaceLang}
          options={[
            { value: 'zh-CN', label: '简体中文' },
            { value: 'zh-TW', label: '繁體中文' },
            { value: 'en', label: 'English (Beta)' },
          ]}
          onChange={(v) => { setInterfaceLang(v); save("ui_language", v); import('../i18n/index.js').then(m => m.changeLanguage(v)); }} />
      </SetRow>
      <SetRow label={t('settings.preferences.serif_font')} description={t('settings.preferences.serif_font_desc')}>
        <CSToggle checked={serif} onChange={({ detail }) => { setSerif(detail.checked); save("serif", detail.checked); }}>
          {serif ? t('settings.preferences.serif_on') : t('settings.preferences.serif_off')}
        </CSToggle>
      </SetRow>
      <SetRow label={t('settings.preferences.autosave')} description={t('settings.preferences.autosave_desc')}>
        <CSToggle checked={auto} onChange={({ detail }) => { setAuto(detail.checked); save("autosave", detail.checked); }}>
          {auto ? t('settings.preferences.autosave_on') : t('settings.preferences.autosave_off')}
        </CSToggle>
      </SetRow>
    </SetGroup>
  );
}

/* ExtractorSection — task 64：暴露后端 task 62/63 的 user_preferences.extractor.*。
   后端读 user_preferences.preferences["extractor.enabled"/"extractor.api_id"/"extractor.model_real_name"]。
   useAutoSave("叙事提取器", "extractor") 让 save("enabled", v) 写到 extractor.enabled，键正好对齐。 */
function ExtractorSection() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useStatePL(false);
  // Wave 11.5-A: 旧默认是 "vertex_ai",改为统一的 "agent_platform"(后端 v024 migration
   //   会把 user_credentials.api_id = 'vertex'/'vertex_ai' 自动改名)。
  const [apiId, setApiId] = useStatePL("agent_platform");
  const [modelRealName, setModelRealName] = useStatePL("gemini-3.5-flash");
  const [apis, setApis] = useStatePL([]);
  const save = useAutoSave(t('settings.extractor.title'), "extractor");
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, models] = await Promise.all([
          window.api.account.profile(),
          window.api.models.list().catch(() => ({ apis: [] })),
        ]);
        if (cancelled) return;
        const p = (profile && profile.preferences) || {};
        if (typeof p["extractor.enabled"] === "boolean") setEnabled(p["extractor.enabled"]);
        if (p["extractor.api_id"]) setApiId(p["extractor.api_id"]);
        if (p["extractor.model_real_name"]) setModelRealName(p["extractor.model_real_name"]);
        // /api/models 真实返回 shape: {ok, models: {apis:[...]}, selected}
        // 旧代码把 models 当扁平对象 → setApis(非数组) → apis.find 崩。
        // 改为先解嵌套 models.models.apis，再兼容历史扁平 .apis。
        const rawApis = models?.models?.apis
          ?? (Array.isArray(models?.apis) ? models.apis : null)
          ?? [];
        setApis(Array.isArray(rawApis) ? rawApis : []);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  // Wave 11.5-A: 兼容老 profile 里仍存 "vertex"/"vertex_ai" 的 api_id —
  // 匹配时把它折成 "agent_platform" 再找 currentApi,避免下拉默认空。
  const _normApi = (id) => (id === "vertex" || id === "vertex_ai") ? "agent_platform" : id;
  const currentApi = apis.find(a => _normApi(a.api_id || a.id) === _normApi(apiId));
  const modelList = (currentApi?.models || currentApi?.entries || []);
  // 推荐 provider 排前，未在 /api/models 出现的兜底也保留（用户可能未配 agent_platform/anthropic 但仍要选）
  // Wave 11.5-A: vertex_ai → agent_platform 统一命名。
  const apiOptions = [];
  const seen = new Set();
  for (const preferred of ["agent_platform", "anthropic"]) {
    apiOptions.push({ id: preferred, name: preferred === "agent_platform" ? "Agent Platform（JSON mode）" : "Anthropic（native tool_use）" });
    seen.add(preferred);
  }
  for (const a of apis) {
    const aid = a.api_id || a.id;
    if (!aid || seen.has(aid)) continue;
    apiOptions.push({ id: aid, name: (a.display_name || a.name || aid) + "（JSON mode）" });
    seen.add(aid);
  }
  return (
    <SetGroup title={t('settings.extractor.title')}>
      <SetRow label={t('settings.extractor.enable')} description={t('settings.extractor.enable_desc')}>
        <CSToggle checked={enabled} onChange={({ detail }) => { setEnabled(detail.checked); save("enabled", detail.checked); }}>
          {enabled ? t('settings.extractor.enable_on') : t('settings.extractor.enable_off')}
        </CSToggle>
      </SetRow>
      <SetRow label={t('settings.extractor.api')} description={t('settings.extractor.api_desc')}>
        <SetSelect
          disabled={!enabled}
          value={apiId}
          options={apiOptions.map(o => ({ value: o.id, label: o.name }))}
          onChange={(val) => { setApiId(val); save("api_id", val); }}
        />
      </SetRow>
      <SetRow label={t('settings.extractor.model')} description={t('settings.extractor.model_desc')}>
        {modelList.length === 0 ? (
          <CSInput
            disabled={!enabled}
            value={modelRealName}
            placeholder="gemini-3.5-flash"
            onChange={({ detail }) => { setModelRealName(detail.value); save("model_real_name", detail.value); }}
          />
        ) : (
          <SetSelect
            disabled={!enabled}
            value={modelRealName}
            options={[
              ...(!modelList.some(m => (m.real_name || m.id) === modelRealName)
                ? [{ value: modelRealName, label: `${modelRealName}${t('settings.extractor.model_not_in_list')}` }]
                : []),
              ...modelList.map(m => ({ value: m.real_name || m.id, label: m.display_name || m.real_name || m.id })),
            ]}
            onChange={(val) => { setModelRealName(val); save("model_real_name", val); }}
          />
        )}
      </SetRow>
    </SetGroup>
  );
}

/* ClarifySection — task 85：暴露 user_preferences.curator.confidence_threshold。
   后端 _clarify_threshold(api_user) 读 preferences["curator.confidence_threshold"]，默认 0.5，
   clamp 到 [0.0, 1.0]。useAutoSave("Curator 反问", "curator") 让 save("confidence_threshold", v)
   写到 curator.confidence_threshold，键正好对齐。 */
function ClarifySection() {
  const { t } = useTranslation();
  const DEFAULT = 0.5;
  const [threshold, setThreshold] = useStatePL(DEFAULT);
  const save = useAutoSave("Curator", "curator");
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const profile = await window.api.account.profile();
        if (cancelled) return;
        const p = (profile && profile.preferences) || {};
        const raw = p["curator.confidence_threshold"];
        if (raw !== undefined && raw !== null) {
          const v = Number(raw);
          if (Number.isFinite(v)) {
            setThreshold(Math.max(0, Math.min(1, v)));
          }
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const commit = (v) => {
    let n = Number(v);
    if (!Number.isFinite(n)) n = DEFAULT;
    n = Math.max(0, Math.min(1, n));
    // 量化到 0.05 步进，避免 slider 浮点尾巴写库
    n = Math.round(n * 20) / 20;
    setThreshold(n);
    save("confidence_threshold", n);
  };

  return (
    <SetGroup title={t('settings.clarify.title')}>
      <SetRow label={t('settings.clarify.threshold')} description={t('settings.clarify.threshold_desc')}>
        <div style={{flexDirection: "row", alignItems: "center", display: "flex", gap: 8}}>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={threshold}
            onChange={(e) => { setThreshold(Number(e.target.value)); }}
            onMouseUp={(e) => commit(e.target.value)}
            onTouchEnd={(e) => commit(e.target.value)}
            onKeyUp={(e) => commit(e.target.value)}
            style={{flex: 1, minWidth: 120}}
          />
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={threshold}
            onChange={(e) => { setThreshold(Number(e.target.value)); }}
            onBlur={(e) => commit(e.target.value)}
            style={{width: 72}}
          />
          <span className="muted" style={{fontSize: 12, minWidth: 90}}>
            {threshold.toFixed(2)}
          </span>
        </div>
      </SetRow>
    </SetGroup>
  );
}

function ModelsSection() {
  const { t } = useTranslation();
  // task 51：登录态零 mock。原 useState(MODELS_DATA) 首屏闪过 OpenAI/Anthropic/
  // Google/通义千问/DeepSeek/OpenRouter (35 模型)/local 七个假供应商和它们
  // 的假"key_hint = ·sk-…c024"。改成登录用户初始 []；匿名访客（设计预览）
  // 仍可看到 MODELS_DATA 作为 demo。
  // A5: 正常登录用户首屏显示 skeleton 占位（不展示 mock 数据），fetch 完成后
  // setLoading(false)；仅 URL ?demo=1 或匿名访客才使用 MODELS_DATA。
  const IS_DEMO = new URLSearchParams(location.search).get('demo') === '1';
  const IS_ANON_M = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const useMock = IS_ANON_M || IS_DEMO;
  const [apis, setApis] = useStatePL(useMock ? MODELS_DATA : []);
  // A5: loading 初始 true 对登录用户，false 对 demo/anon（已有 mock 数据）
  const [apisLoading, setApisLoading] = useStatePL(!useMock);
  const [expanded, setExpanded] = useStatePL({ openai: true, anthropic: true });
  const [editingApi, setEditingApi] = useStatePL(null);
  const [addingApi, setAddingApi] = useStatePL(false);
  const [visibilityApi, setVisibilityApi] = useStatePL(null);
  const [validateApi, setValidateApi] = useStatePL(null);
  const [selectedApiId, setSelectedApiId] = useStatePL(null);

  // task 42: 用 health cache 把所有 model 的 health 字段刷新成最新状态。
  // 不重新 probe,只读 backend 内存 cache。轻量,可频繁 poll。
  const refreshHealthFromCache = React.useCallback(async () => {
    try {
      const base = (typeof window !== "undefined" && window.__API_BASE) || "";
      const r = await fetch(`${base}/api/models/health`, { credentials: "include" });
      const j = await r.json();
      const hmap = (j && j.health) || {};
      setApis(arr => arr.map(api => ({
        ...api,
        models: api.models.map(m => {
          const h = hmap[`${api.id}::${m.real_name || m.id}`];
          if (!h) return m;
          return {
            ...m,
            health: h.status || "untested",
            health_error: h.error || "",
            health_latency_ms: h.latency_ms,
            health_checked_at: h.checked_at,
            // A4: status_detail 由后端 model_probe 区分 401 → key_expired / forbidden
            health_status_detail: h.status_detail || m.health_status_detail || undefined,
          };
        }),
      })));
    } catch (_) {}
  }, []);

  // 进入 settings 触发后台 probe sweep,刷一次所有 enabled API 的 health,
  // probe 是 fire-and-forget,UI 不阻塞;然后定期 poll cache 拉结果。
  const triggerHealthSweep = React.useCallback(async (apiId) => {
    try {
      const base = (typeof window !== "undefined" && window.__API_BASE) || "";
      const body = apiId ? { api_id: apiId } : {};
      await fetch(`${base}/api/models/health/refresh-all`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_) {}
  }, []);

  // 自动 probe + 定期 poll health cache,UI 总能看到最新可达状态。
  useEffectPL(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await refreshHealthFromCache();
    };
    // 进入页面立即 fire 一次 sweep + poll;之后每 8s poll cache 拿最新结果
    (async () => {
      await triggerHealthSweep();
      // 等 1s 让 sweep 至少推一两条结果,然后第一次拉
      await new Promise(r => setTimeout(r, 1500));
      await tick();
    })();
    const iv = setInterval(tick, 8000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [refreshHealthFromCache, triggerHealthSweep]);

  // Hydrate from backend /api/models + 并行拉 /api/me/credentials 合并 key_set/key_hint
  useEffectPL(() => {
    if (useMock) return; // A5: demo/anon 模式不 fetch，直接用 MODELS_DATA
    (async () => {
      try {
        // task 52：之前只看 catalog 的 credential_ref/env，user-level key
        // 走 /api/me/credentials 不在 catalog 里。导致用户配过 key 但 UI 仍
        // 显示"未配置"。并行拉 credentials.list 做 api_id → {has_key, key_hint} 映射。
        const [data, creds] = await Promise.all([
          window.api.models.list(),
          window.api.credentials.list().catch(() => ({ items: [] })),
        ]);
        // 后端字段是 has_credential（不是 has_key）；key_hint 一般不返回 → fallback 文案
        const credMap = {};
        for (const c of (creds?.items || creds?.credentials || [])) {
          credMap[c.api_id || c.id] = {
            has_key: !!c.has_credential || !!c.has_key || !!c.key_hint,
            key_hint: c.key_hint || "",
            base_url_override: c.base_url_override || "",
          };
        }
        // /api/models 返回 {ok, models: {apis:[...]}, selected}。先取嵌套 .models.apis；
        // 兼容旧扁平 shape 用 data.apis；最后非数组兜底成 []。
        const list = data?.models?.apis || data?.apis || [];
        if (Array.isArray(list) && list.length) {
          setApis(list.map(api => {
            const aid = api.api_id || api.id;
            const cred = credMap[aid] || {};
            return {
              id: aid,
              name: api.display_name || api.name,
              base_url: api.base_url || "",
              key_set: cred.has_key || !!api.credential_ref || !!api.credential_env,
              key_hint: cred.key_hint || api.key_hint || "—",
              status: api.enabled ? "online" : "offline",
              enabled: !!api.enabled,
              proxy: api.proxy || "direct",
              models: (api.models || api.entries || []).map(m => ({
                id: m.real_name || m.id,
                display: m.display_name || m.real_name,
                real_name: m.real_name,
                enabled: m.enabled !== false,
                visible: m.hidden !== true,
                capabilities: m.capabilities || {},
                // task 42: 把 backend /api/models 注入的 health 状态透传到 UI,
                // HealthDot 显示 ok/err/untested 圆点,picker 灰掉 err 项
                health: m.health || "untested",
                health_error: m.health_error || "",
                health_latency_ms: m.health_latency_ms,
                health_checked_at: m.health_checked_at,
                // A4: status_detail 字段（后端 model_probe 区分 key_expired / forbidden）
                health_status_detail: m.status_detail || m.health_status_detail || undefined,
              })),
            };
          }));
        }
      } catch (_) {}
      finally {
        setApisLoading(false); // A5: fetch 完成（含出错）后关闭 loading
      }
    })();
  }, [useMock]);

  const toggleApi = async (id) => {
    setApis(arr => arr.map(a => a.id === id ? { ...a, enabled: !a.enabled } : a));
    try {
      const api = apis.find(a => a.id === id);
      await window.api.models.upsertApi({ api_id: id, enabled: !api?.enabled });
    } catch (_) {}
  };
  const toggleModel = async (apiId, mId) => {
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => m.id === mId ? { ...m, enabled: !m.enabled } : m) }
      : a));
    try {
      const api = apis.find(a => a.id === apiId);
      const m = api?.models.find(m => m.id === mId);
      await window.api.models.upsertModel({ api_id: apiId, real_name: mId, enabled: !m?.enabled });
    } catch (_) {}
  };
  const renameModel = async (apiId, mId, display) => {
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => m.id === mId ? { ...m, display } : m) }
      : a));
    try { await window.api.models.upsertModel({ api_id: apiId, real_name: mId, display_name: display }); } catch (_) {}
  };
  const setModelVisibility = async (apiId, ids) => {
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => ({ ...m, visible: ids.includes(m.id) })) }
      : a));
    const api = apis.find(a => a.id === apiId);
    if (api) {
      await Promise.all(api.models.map(m =>
        window.api.models.visibility({ api_id: apiId, model: m.id, visible: ids.includes(m.id) }).catch(() => {})
      ));
    }
  };
  const removeModels = async (apiId, ids) => {
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.filter(m => !ids.includes(m.id)) }
      : a));
    await Promise.all(ids.map(id =>
      window.api.models.deleteModel({ api_id: apiId, real_name: id }).catch(() => {})
    ));
  };
  const toggleExpand = (id) => setExpanded(e => ({ ...e, [id]: !e[id] }));

  const enabledTotal = apis.reduce((a, x) => a + x.models.filter(m => m.enabled).length, 0);
  const totalModels = apis.reduce((a, x) => a + x.models.length, 0);

  // 只显示「已配置 API Key」的供应商(对齐剧本/存档:没有就显示添加按钮,不堆砌)
  const configuredApis = apis.filter(a => a.key_set);
  const selectedApi = configuredApis.find(a => a.id === selectedApiId) || null;

  const detailEl = selectedApi ? (
    <ApiDetailPanel
      api={selectedApi}
      onEdit={() => setEditingApi(selectedApi.id)}
      onVisibility={() => setVisibilityApi(selectedApi.id)}
      onValidate={() => setValidateApi(selectedApi.id)}
      onToggleModel={(mId) => toggleModel(selectedApi.id, mId)}
      onRenameModel={(mId, display) => renameModel(selectedApi.id, mId, display)}
      onDeleteKey={async () => {
        if (!await window.__confirm({ title: t('settings.models.delete_key_title'), message: t('settings.models.delete_key_confirm', { name: selectedApi.name }), danger: true, confirmText: t('settings.models.delete_key_btn') })) return;
        try {
          await window.api.credentials.set({ api_id: selectedApi.id, api_key: '' });
          window.__apiToast?.(t('settings.models.delete_key_ok'), { kind: 'ok' });
          setSelectedApiId(null);
          setApis(arr => arr.map(a => a.id === selectedApi.id ? { ...a, key_set: false, key_hint: '—' } : a));
          if (typeof window.__refreshPlatform === 'function') { try { await window.__refreshPlatform(); } catch (_) {} }
        } catch (e) { window.__apiToast?.(t('settings.models.delete_key_fail'), { kind: 'danger', detail: e?.message }); }
      }}
    />
  ) : null;

  // A5: skeleton 占位 — 登录用户首次进入时，fetch 完成前不展示表格
  if (apisLoading) {
    return (
      <CSSpaceBetween size="l">
        <CSHeader variant="h1" description={t('settings.models.description')}>{t('settings.models.title')}</CSHeader>
        {[1, 2, 3].map(i => (
          <CSContainer key={i}>
            <CSSpaceBetween size="s">
              {[1, 2].map(j => (
                <div key={j} style={{ height: 18, borderRadius: 4, background: 'var(--color-background-control-disabled, #3a3a3a)', opacity: 0.5 + j * 0.15, width: j === 1 ? '40%' : '70%' }} />
              ))}
            </CSSpaceBetween>
          </CSContainer>
        ))}
      </CSSpaceBetween>
    );
  }

  return (
    <CSSpaceBetween size="l">
      <CSHeader
        variant="h1"
        counter={`(${configuredApis.length})`}
        description={t('settings.models.description')}
        actions={<CSButton variant="primary" iconName="add-plus" onClick={() => setAddingApi(true)}>{t('settings.models.add_key')}</CSButton>}
      >{t('settings.models.title')}</CSHeader>

      {configuredApis.length === 0 ? (
        <CSContainer>
          <CSBox textAlign="center" color="inherit" padding={{ vertical: 'xxl' }}>
            <CSSpaceBetween size="s" alignItems="center">
              <CSBox variant="h3">{t('settings.models.empty_title')}</CSBox>
              <CSBox color="text-body-secondary">{t('settings.models.empty_desc')}</CSBox>
              <CSButton variant="primary" iconName="add-plus" onClick={() => setAddingApi(true)}>{t('settings.models.empty_add')}</CSButton>
            </CSSpaceBetween>
          </CSBox>
        </CSContainer>
      ) : (() => {
        const apiTableEl = (
          <CSTable
            variant="container"
            trackBy="id"
            selectionType="single"
            items={configuredApis}
            selectedItems={selectedApi ? [selectedApi] : []}
            onSelectionChange={({ detail }) => { const x = detail.selectedItems[0]; if (x) setSelectedApiId(x.id); }}
            onRowClick={({ detail }) => setSelectedApiId(detail.item.id)}
            columnDefinitions={[
              { id: 'name', header: t('settings.models.col_provider'), cell: (a) => (
                <div><CSBox fontWeight="bold">{a.name}</CSBox><CSBox fontSize="body-s" color="text-body-secondary"><span className="mono">{a.id}</span></CSBox></div>
              ) },
              { id: 'key', header: 'API Key', cell: (a) => <span className="mono">•••• {a.key_hint || t('settings.models.key_set_hint')}</span> },
              { id: 'models', header: t('settings.models.col_models'), cell: (a) => `${a.models.filter(m => m.enabled).length} / ${a.models.length}` },
              { id: 'status', header: t('settings.models.col_status'), cell: (a) => (
                a.enabled
                  ? <CSStatusIndicator type={a.status === 'online' ? 'success' : 'warning'}>{a.status}</CSStatusIndicator>
                  : <CSStatusIndicator type="stopped">{t('settings.models.status_disabled')}</CSStatusIndicator>
              ) },
              { id: 'go', header: '', cell: (a) => (
                <span onClick={(e) => e.stopPropagation()}>
                  <SettingsToggle on={a.enabled} set={() => toggleApi(a.id)} />
                </span>
              ) },
            ]}
          />
        );
        return selectedApi
          ? <ResizableSplit storageKey="apikey" top={apiTableEl} bottom={detailEl} />
          : apiTableEl;
      })()}

      <EditApiModal
        open={!!editingApi || addingApi}
        api={apis.find(a => a.id === editingApi)}
        isNew={addingApi}
        onClose={() => { setEditingApi(null); setAddingApi(false); }}
        onConfirm={async (payload) => {
          // task 51：之前只调 /api/models/api 并把 api_key 当字段塞进去，但
          // upsert_api() 根本不接收 api_key（只读 credential_ref/env），
          // 所以用户在 EditApiModal 输入的 key 永远落不下。
          // 现在分两步：
          //   1. /api/models/api 保存 catalog 元数据（display_name / base_url）
          //   2. /api/me/credentials 保存加密的用户级 API key（如果用户填了）
          try {
            await window.api.models.upsertApi({
              api_id: payload.id,
              display_name: payload.name,
              base_url: payload.base_url,
              proxy: payload.proxy,
            });
            if (payload.api_key && payload.api_key.trim()) {
              try {
                await window.api.credentials.set({ api_id: payload.id, api_key: payload.api_key.trim() });
              } catch (e) {
                window.__apiToast?.(t('settings.edit_api.key_save_fail'), { kind: "warn", detail: e?.message, duration: 4000 });
                throw e;
              }
            }
            window.__apiToast?.(addingApi ? t('settings.edit_api.add_ok') : t('settings.edit_api.save_ok'), { kind: "ok" });
          } catch (e) {
            window.__apiToast?.(t('settings.edit_api.save_fail'), { kind: "danger", detail: e?.message });
          }
          if (addingApi) {
            setApis(arr => [...arr, { ...payload, models: [], enabled: true, status: "untested", key_set: !!payload.api_key }]);
          } else {
            setApis(arr => arr.map(a => a.id === editingApi ? { ...a, ...payload, key_set: a.key_set || !!payload.api_key } : a));
          }
          setEditingApi(null); setAddingApi(false);
          // 刷新让真实 key_set / key_hint 由后端权威
          if (typeof window.__refreshPlatform === "function") {
            try { await window.__refreshPlatform(); } catch (_) {}
          }
        }}
      />
      <VisibilityModal
        open={!!visibilityApi}
        api={apis.find(a => a.id === visibilityApi)}
        onClose={() => setVisibilityApi(null)}
        onConfirm={(visibleIds) => { setModelVisibility(visibilityApi, visibleIds); setVisibilityApi(null); }}
      />
      <ValidateModal
        open={!!validateApi}
        api={apis.find(a => a.id === validateApi)}
        onClose={() => setValidateApi(null)}
        onConfirm={(toRemove) => { removeModels(validateApi, toRemove); setValidateApi(null); }}
      />
    </CSSpaceBetween>
  );
}

/* API 详情面板 —— 选中某个已配置 Key 后在列表下方展开。
   Tabs:模型列表(ApiModelsList)/ API 用量(简略)。头部:编辑 / 管理显示 / 校验 / 删除 Key。 */
function ApiDetailPanel({ api, onEdit, onVisibility, onValidate, onDeleteKey, onToggleModel, onRenameModel }) {
  const { t } = useTranslation();
  const [tab, setTab] = useStatePL('models');
  const [usage, setUsage] = useStatePL(null);
  useEffectPL(() => { setTab('models'); setUsage(null); }, [api.id]);
  useEffectPL(() => {
    if (tab !== 'usage' || usage != null) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.usage(30);
        if (cancelled) return;
        const byApi = (r?.by_api || r?.apis || []).find(x => (x.api_id || x.id) === api.id);
        setUsage(byApi || {});
      } catch (_) { if (!cancelled) setUsage({}); }
    })();
    return () => { cancelled = true; };
  }, [tab, api.id]);

  return (
    <CSContainer header={
      <CSHeader variant="h2"
        description={<span style={{ display: 'inline-flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono">{api.id}</span>
          <span style={{ color: 'var(--muted)' }}>Base URL: <span className="mono">{api.base_url || '—'}</span></span>
          <span style={{ color: 'var(--muted)' }}>Key: <span className="mono">•••• {api.key_hint || t('settings.models.key_set_hint')}</span></span>
        </span>}
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton iconName="edit" onClick={onEdit}>{t('settings.models.detail_edit')}</CSButton>
            <CSButton iconName="view-full" onClick={onVisibility}>{t('settings.models.detail_manage')}</CSButton>
            <CSButton iconName="refresh" onClick={onValidate}>{t('settings.models.detail_validate')}</CSButton>
            <CSButton iconName="remove" onClick={onDeleteKey}>{t('settings.models.detail_delete_key')}</CSButton>
          </CSSpaceBetween>
        }
      >{api.name}</CSHeader>
    }>
      <CSTabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
        { id: 'models', label: t('settings.models.tab_models', { count: api.models.length }), content: (
          <ApiModelsList api={api} onToggleModel={onToggleModel} onRenameModel={onRenameModel} />
        ) },
        { id: 'usage', label: t('settings.models.tab_usage'), content: (
          usage == null
            ? <CSBox color="text-body-secondary">{t('common.loading')}</CSBox>
            : <CSSpaceBetween size="m">
                <CSKeyValuePairs columns={4} items={[
                  { label: t('settings.models.usage_requests'), value: usage.requests != null ? Number(usage.requests).toLocaleString() : '—' },
                  { label: t('settings.models.usage_input_tokens'), value: usage.input_tokens != null ? Number(usage.input_tokens).toLocaleString() : '—' },
                  { label: t('settings.models.usage_output_tokens'), value: usage.output_tokens != null ? Number(usage.output_tokens).toLocaleString() : '—' },
                  { label: t('settings.models.usage_cost'), value: usage.cost_usd != null ? `$${Number(usage.cost_usd).toFixed(2)}` : '—' },
                ]} />
                <CSBox fontSize="body-s" color="text-body-secondary">{t('settings.models.usage_detail')} <a href="#usage">{t('settings.models.usage_page')}</a>。</CSBox>
              </CSSpaceBetween>
        ) },
      ]} />
    </CSContainer>
  );
}

function AddModelModal({ open, api, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [form, setForm] = useStatePL({
    real_name: "",
    display: "",
    capabilities: [],
    price: "",
    context: "128K",
  });
  React.useEffect(() => {
    if (open) setForm({ real_name: "", display: "", capabilities: [], price: "", context: "128K" });
  }, [open]);
  if (!open || !api) return null;
  const toggleCap = (c) => setForm(f => ({ ...f, capabilities: f.capabilities.includes(c) ? f.capabilities.filter(x => x !== c) : [...f.capabilities, c] }));
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(560px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('settings.add_model.eyebrow', { api: api.name })}</div>
            <h2 className="pl-modal-title">{t('settings.add_model.title')}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form">
          <div className="pl-field">
            <label>{t('settings.add_model.real_name')} <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{t('settings.add_model.real_name_hint', { api: api.name })}</span></label>
            <input className="mono" value={form.real_name} onChange={(e) => setForm(f => ({ ...f, real_name: e.target.value }))} placeholder="gpt-4o-mini-2024-07-18" autoFocus />
          </div>
          <div className="pl-field">
            <label>{t('settings.add_model.display')} <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{t('settings.add_model.display_hint')}</span></label>
            <input value={form.display} onChange={(e) => setForm(f => ({ ...f, display: e.target.value }))} placeholder="GPT-4o · RPG" />
          </div>
          <div className="pl-field">
            <label>{t('settings.add_model.caps')} <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{t('settings.add_model.caps_hint')}</span></label>
            <div className="pl-rules">
              {Object.keys(CAP_LABEL).map(c => (
                <button key={c} className={`pl-rule-chip ${form.capabilities.includes(c) ? "active" : ""}`} onClick={() => toggleCap(c)}>{CAP_LABEL[c]}</button>
              ))}
            </div>
          </div>
          <div className="pl-import-grid" style={{gridTemplateColumns: "1fr 1fr"}}>
            <div className="pl-field">
              <label>{t('settings.add_model.price')}</label>
              <input className="mono" value={form.price} onChange={(e) => setForm(f => ({ ...f, price: e.target.value }))} placeholder="$0.15 / $0.60" />
            </div>
            <div className="pl-field">
              <label>{t('settings.add_model.context')}</label>
              <input className="mono" value={form.context} onChange={(e) => setForm(f => ({ ...f, context: e.target.value }))} placeholder="128K" />
            </div>
          </div>
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> POST <span className="mono">/api/v1/models/model</span>
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
            <button className="btn primary" disabled={!form.real_name || !form.display}
              onClick={() => onConfirm({ id: form.real_name, ...form })}>
              <Icon name="check" size={12} /> {t('settings.add_model.add_btn')}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function EditApiModal({ open, api, isNew, onClose, onConfirm }) {
  const { t } = useTranslation();
  // 新增时供应商走下拉(从 PROVIDERS_CONFIG 选,自动带出 base_url);选「自定义」可手填。
  // 编辑时供应商固定,只改 base_url / key。key 写入后不回显。
  const CUSTOM = '__custom__';
  const [provider, setProvider] = useStatePL('');   // 选中的 provider id(新增用)
  const [form, setForm] = useStatePL({ id: "", name: "", base_url: "", api_key: "", proxy: "direct" });
  React.useEffect(() => {
    if (!open) return;
    if (isNew) { setProvider(''); setForm({ id: "", name: "", base_url: "", api_key: "", proxy: "direct" }); }
    else if (api) { setProvider(api.id); setForm({ id: api.id, name: api.name, base_url: api.base_url, api_key: "", proxy: api.proxy || "direct" }); }
  }, [open, api, isNew]);
  if (!open) return null;

  const provOptions = [
    ...PROVIDERS_CONFIG.map((p) => ({ value: p.id, label: p.name, description: p.defaultBase || undefined })),
    { value: CUSTOM, label: t('settings.edit_api.custom_provider'), description: t('settings.edit_api.custom_provider_desc') },
  ];
  const onPickProvider = (val) => {
    setProvider(val);
    if (val === CUSTOM) { setForm((f) => ({ ...f, id: "", name: "", base_url: "" })); return; }
    const p = PROVIDERS_CONFIG.find((x) => x.id === val);
    if (p) setForm((f) => ({ ...f, id: p.id, name: p.name, base_url: p.defaultBase || "" }));
  };
  const isCustom = provider === CUSTOM;
  const canSubmit = !!form.id && !!form.name && !!form.base_url && (isNew ? !!form.api_key.trim() : true);

  return (
    <CSModal
      visible
      onDismiss={onClose}
      header={isNew ? t('settings.edit_api.add_title') : t('settings.edit_api.edit_title', { name: api?.name || '' })}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={onClose}>{t('common.cancel')}</CSButton>
            <CSButton variant="primary" disabled={!canSubmit} onClick={() => onConfirm(form)}>{isNew ? t('settings.edit_api.add_btn') : t('settings.edit_api.save_btn')}</CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="l">
        {isNew && (
          <CSFormField label={t('settings.edit_api.provider')} description={t('settings.edit_api.provider_desc')}>
            <CSSelect
              selectedOption={provOptions.find((o) => o.value === provider) || null}
              options={provOptions}
              placeholder={t('settings.edit_api.provider_placeholder')}
              filteringType="auto"
              onChange={({ detail }) => onPickProvider(detail.selectedOption.value)}
            />
          </CSFormField>
        )}
        {(isCustom || !isNew) && (
          <CSColumnLayout columns={2}>
            <CSFormField label={t('settings.edit_api.id_field')}>
              <CSInput value={form.id} disabled={!isNew}
                onChange={({ detail }) => setForm((f) => ({ ...f, id: detail.value }))} placeholder="openai" />
            </CSFormField>
            <CSFormField label={t('settings.edit_api.display_name')}>
              <CSInput value={form.name} onChange={({ detail }) => setForm((f) => ({ ...f, name: detail.value }))} placeholder="OpenAI" />
            </CSFormField>
          </CSColumnLayout>
        )}
        {(provider || !isNew) && (
          <>
            <CSFormField label={t('settings.edit_api.base_url')}>
              <CSInput value={form.base_url} onChange={({ detail }) => setForm((f) => ({ ...f, base_url: detail.value }))} placeholder="https://api.openai.com/v1" />
            </CSFormField>
            <CSFormField label={t('settings.edit_api.api_key')} description={api?.key_set ? t('settings.edit_api.api_key_desc_set', { hint: api.key_hint || t('settings.models.key_set_hint') }) : t('settings.edit_api.api_key_desc_new')}>
              <CSInput type="password" value={form.api_key}
                onChange={({ detail }) => setForm((f) => ({ ...f, api_key: detail.value }))}
                placeholder={api?.key_set ? t('settings.edit_api.api_key_placeholder_keep') : "sk-…"} autoComplete="new-password" />
            </CSFormField>
            <CSFormField label={t('settings.edit_api.connection')}>
              <CSSelect
                selectedOption={{ value: form.proxy, label: form.proxy }}
                options={[{ value: 'direct', label: t('settings.edit_api.direct') }, { value: 'http_proxy', label: t('settings.edit_api.http_proxy') }, { value: 'lan', label: t('settings.edit_api.lan') }]}
                onChange={({ detail }) => setForm((f) => ({ ...f, proxy: detail.selectedOption.value }))}
              />
            </CSFormField>
          </>
        )}
      </CSSpaceBetween>
    </CSModal>
  );
}

function VisibilityModal({ open, api, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useStatePL(new Set());
  const [q, setQ] = useStatePL("");
  React.useEffect(() => {
    if (open && api) {
      setSelected(new Set(api.models.filter(m => m.visible !== false).map(m => m.id)));
      setQ("");
    }
  }, [open, api]);
  if (!open || !api) return null;
  const toggle = (id) => setSelected(s => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const filtered = api.models.filter(m => {
    if (!q) return true;
    const v = q.toLowerCase();
    return m.display.toLowerCase().includes(v) || m.real_name.toLowerCase().includes(v);
  });
  const allVisible = filtered.every(m => selected.has(m.id));
  const toggleAll = () => setSelected(s => {
    const n = new Set(s);
    if (allVisible) filtered.forEach(m => n.delete(m.id));
    else filtered.forEach(m => n.add(m.id));
    return n;
  });
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)", maxHeight: "88vh"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('settings.visibility.eyebrow', { name: api.name })}</div>
            <h2 className="pl-modal-title">{t('settings.visibility.title', { selected: selected.size, total: api.models.length })}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-model-search" style={{flex: "0 0 auto"}}>
          <Icon name="search" size={12} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('settings.visibility.search_placeholder', { count: api.models.length })} autoFocus />
          {q && <button className="iconbtn" onClick={() => setQ("")} style={{width: 18, height: 18}}>
            <Icon name="close" size={10} />
          </button>}
        </div>
        <div className="pl-vis-toolbar">
          <button className="btn ghost" onClick={toggleAll}>
            {allVisible ? <><Icon name="eye_off" size={12} /> {t('settings.visibility.hide_all')}</> : <><Icon name="eye" size={12} /> {t('settings.visibility.show_all')}</>}
          </button>
          <span className="muted-2 mono" style={{marginLeft: "auto", fontSize: 11}}>
            {t('settings.visibility.matched', { count: filtered.length, selected: filtered.filter(m => selected.has(m.id)).length })}
          </span>
        </div>
        <div className="pl-vis-list">
          {filtered.length === 0 ? (
            <div className="pl-model-empty">{t('settings.visibility.no_match')}</div>
          ) : filtered.map(m => (
            <label key={m.id} className={`pl-vis-row ${selected.has(m.id) ? "on" : ""}`}>
              <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggle(m.id)} />
              <HealthDot health={m.health} statusDetail={m.health_status_detail} />
              <div className="pl-vis-row-body">
                <strong>{m.display}</strong>
                <span className="muted-2 mono">{m.real_name}</span>
              </div>
              <div className="pl-vis-row-meta">
                <div style={{display: "flex", gap: 3}}>
                  {(() => {
                    const caps = getCaps(m);
                    return (<>
                      {caps.slice(0, 2).map(c => (
                        <span key={c} className="pl-cap-tag">{CAP_LABEL[c] || c}</span>
                      ))}
                      {caps.length > 2 && <span className="muted-2" style={{fontSize: 11}}>+{caps.length - 2}</span>}
                    </>);
                  })()}
                </div>
                <span className="mono muted-2" style={{fontSize: 11}}>
                  {m.context_window != null ? fmtCtx(m.context_window) : (m.context || "—")}
                </span>
              </div>
            </label>
          ))}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> {t('settings.visibility.info')}
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
            <button className="btn primary" onClick={() => onConfirm([...selected])}>
              <Icon name="check" size={12} /> {t('settings.visibility.save')}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function ValidateModal({ open, api, onClose, onConfirm }) {
  const { t } = useTranslation();
  // task 50：之前 setTimeout 1400ms 后假装 "done"，newSniffed 是写死的
  // gpt-4.5-turbo / gpt-4o-realtime-preview（只在 api.id === "openai" 时显示）。
  // 整个嗅探过程 zero API call。现在改为：
  //   1. 真打 GET /api/models/diff?api_id=... 得到 added / removed / kept
  //   2. 「全部添加」走 POST /api/models/model 真的把每个 added 持久化
  //   3. 「删除 N 个」走原 onConfirm（沿用旧 path：调用方 ApiCardList 处理）
  const [phase, setPhase] = useStatePL("idle");
  const [diff, setDiff] = useStatePL(null);
  const [err, setErr] = useStatePL("");
  const [removeIds, setRemoveIds] = useStatePL(new Set());
  const [adding, setAdding] = useStatePL(false);
  React.useEffect(() => {
    if (!open || !api) return;
    setPhase("sniffing"); setErr(""); setDiff(null); setRemoveIds(new Set());
    (async () => {
      try {
        const r = await window.api.models.diff({ api_id: api.id });
        setDiff(r || {});
      } catch (e) {
        setErr(e?.message || "probe failed");
      } finally {
        setPhase("done");
      }
    })();
  }, [open, api?.id]);
  if (!open || !api) return null;
  // 后端 diff 返回 {local_only, remote_only, matching} 都是字符串数组（real_name）。
  // 统一映射为 {real_name, display} 对象数组，给 UI / addAll 用。
  const wrap = (arr) => (arr || []).map(s => typeof s === "string" ? { real_name: s, display: s } : s);
  const remoteOnly = wrap(diff && (diff.added || diff.remote_only));
  const localOnly = wrap(diff && (diff.removed || diff.local_only));
  const kept = wrap(diff && (diff.kept || diff.matching || diff.common));
  const unreachable = api.models.filter(m => m.health === "err");
  const toRemoveList = [...localOnly, ...unreachable.filter(u => !localOnly.some(r => r.real_name === u.real_name))];
  const toggleRemove = (id) => setRemoveIds(s => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const addAll = async () => {
    if (adding || remoteOnly.length === 0) return;
    setAdding(true);
    let ok = 0, fail = 0;
    for (const m of remoteOnly) {
      try {
        await window.api.models.upsertModel({
          api_id: api.id,
          real_name: m.real_name || m.id,
          display: m.display || m.name || m.real_name,
          enabled: true,
        });
        ok++;
      } catch (_) { fail++; }
    }
    setAdding(false);
    window.__apiToast?.(fail ? t('settings.validate.add_ok_fail', { ok, fail }) : t('settings.validate.add_ok', { ok }), { kind: ok ? "ok" : "danger", duration: 3000 });
    if (typeof window.__refreshPlatform === "function") { try { await window.__refreshPlatform(); } catch (_) {} }
    onClose();
  };
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(560px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('settings.validate.eyebrow', { name: api.name })}</div>
            <h2 className="pl-modal-title">
              {phase === "sniffing" ? t('settings.validate.sniffing') : t('settings.validate.done')}
            </h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        {phase === "sniffing" ? (
          <div className="pl-validate-progress">
            <div className="pl-validate-step done"><span className="dot ok" /> {t('settings.validate.step1')}</div>
            <div className="pl-validate-step running"><Icon name="spinner" size={12} className="spin" /> {t('settings.validate.step2')}</div>
          </div>
        ) : err ? (
          <div className="pl-model-empty" style={{padding: "24px 16px"}}>
            <Icon name="warn" size={18} style={{color: "var(--danger)"}} />
            <div>{t('settings.validate.fail_title', { err })}</div>
            <div className="muted" style={{marginTop: 8, fontSize: 12}}>{t('settings.validate.fail_hint')}</div>
          </div>
        ) : (
          <div className="pl-validate-result">
            <div className="pl-validate-stat-row">
              <div className="pl-validate-stat">
                <span className="pl-stat-label">{t('settings.validate.stat_existing')}</span>
                <span className="pl-stat-value" style={{fontSize: 20}}>{api.models.length}</span>
              </div>
              <div className="pl-validate-stat">
                <span className="pl-stat-label">{t('settings.validate.stat_remote')}</span>
                <span className="pl-stat-value" style={{fontSize: 20}}>{remoteOnly.length + kept.length}</span>
              </div>
              <div className="pl-validate-stat">
                <span className="pl-stat-label accent">{t('settings.validate.stat_new')}</span>
                <span className="pl-stat-value accent" style={{fontSize: 20}}>{remoteOnly.length}</span>
              </div>
              <div className="pl-validate-stat">
                <span className="pl-stat-label danger">{t('settings.validate.stat_local_extra')}</span>
                <span className="pl-stat-value danger" style={{fontSize: 20}}>{localOnly.length}</span>
              </div>
            </div>

            {remoteOnly.length > 0 && (
              <div className="pl-validate-section">
                <div className="pl-validate-section-head">
                  <span className="dot accent" /> {t('settings.validate.new_models', { count: remoteOnly.length })}
                  <button className="btn ghost" style={{height: 22, padding: "0 8px", fontSize: 11, marginLeft: "auto"}}
                    disabled={adding} onClick={addAll}>
                    {adding ? <><Icon name="spinner" size={11} className="spin" /> {t('settings.validate.adding')}</> : <><Icon name="plus" size={11} /> {t('settings.validate.add_all')}</>}
                  </button>
                </div>
                <ul className="pl-validate-list">
                  {remoteOnly.map(m => (
                    <li key={m.real_name || m.id} className="pl-validate-new">
                      <span className="dot accent" style={{flexShrink: 0}} />
                      <div style={{display: "grid", gap: 1, minWidth: 0}}>
                        <strong>{m.display || m.name || m.real_name}</strong>
                        <span className="muted-2 mono">{m.real_name || m.id}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {toRemoveList.length > 0 && (
              <div className="pl-validate-section">
                <div className="pl-validate-section-head">
                  <span className="dot danger" /> {t('settings.validate.local_extra', { count: toRemoveList.length })}
                  <span className="muted-2" style={{marginLeft: 6, fontSize: 11}}>{t('settings.validate.local_extra_hint')}</span>
                </div>
                <ul className="pl-validate-list">
                  {toRemoveList.map(m => (
                    <li key={m.id || m.real_name} className={removeIds.has(m.id || m.real_name) ? "marked" : ""}>
                      <input type="checkbox" checked={removeIds.has(m.id || m.real_name)} onChange={() => toggleRemove(m.id || m.real_name)} />
                      <HealthDot health={m.health} statusDetail={m.health_status_detail} />
                      <div style={{display: "grid", gap: 1, minWidth: 0, flex: 1}}>
                        <strong>{m.display || m.name || m.real_name}</strong>
                        <span className="muted-2 mono">{m.real_name || m.id}</span>
                      </div>
                      <span className="pill danger" style={{fontSize: 10.5}}>
                        {m.health === "err" ? t('settings.validate.unreachable') : t('settings.validate.remote_missing')}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {remoteOnly.length === 0 && toRemoveList.length === 0 && (
              <div className="pl-model-empty" style={{padding: "24px 16px"}}>
                <Icon name="check" size={18} style={{color: "var(--ok)"}} />
                <div>{t('settings.validate.in_sync')}</div>
              </div>
            )}
          </div>
        )}
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> GET /api/models/diff · POST /api/models/model
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>{phase === "done" ? t('common.close') : t('common.cancel')}</button>
            {phase === "done" && removeIds.size > 0 && (
              <button className="btn danger" onClick={() => onConfirm([...removeIds])}>
                <Icon name="trash" size={12} /> {t('settings.validate.delete_btn', { count: removeIds.size })}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

function ApiModelsList({ api, onToggleModel, onRenameModel }) {
  const { t } = useTranslation();
  const [q, setQ] = useStatePL("");
  const [capFilter, setCapFilter] = useStatePL(null);
  const [statusFilter, setStatusFilter] = useStatePL("all");
  const [showAll, setShowAll] = useStatePL(false);
  const [sortKey, setSortKey] = useStatePL("smart");
  const PAGE = 6;

  // Only models marked visible — visibility is controlled via the API card's
  // "编辑显示" modal, not per-row.
  const visibleModels = api.models.filter(m => m.visible !== false);

  // helpers to normalize capabilities (Wave 11.5-A: 复用 components/catalog-helpers.js,
  // 老 array / 新 typed object 两种 shape 都兼容)
  const getCaps = window.getCaps || _getCapsImported;

  const filtered = visibleModels.filter(m => {
    if (q) {
      const s = q.toLowerCase();
      if (!m.display.toLowerCase().includes(s) && !m.real_name.toLowerCase().includes(s)) return false;
    }
    if (capFilter && !getCaps(m).includes(capFilter)) return false;
    if (statusFilter === "enabled" && !m.enabled) return false;
    if (statusFilter === "disabled" && m.enabled) return false;
    if (statusFilter === "ok" && m.health !== "ok") return false;
    if (statusFilter === "err" && m.health !== "err") return false;
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "smart") {
      if (a.enabled !== b.enabled) return b.enabled - a.enabled;
      return a.display.localeCompare(b.display, "zh-CN");
    }
    if (sortKey === "name") return a.display.localeCompare(b.display, "zh-CN");
    if (sortKey === "context") {
      // Wave 11-C: 优先用 context_window 数值,兼容旧 context 字符串
      const getCtx = (m) => m.context_window ?? parseInt(m.context) ?? 0;
      return getCtx(b) - getCtx(a);
    }
    if (sortKey === "health") {
      const order = { ok: 0, degraded: 1, untested: 2, err: 3 };
      return (order[a.health] ?? 4) - (order[b.health] ?? 4);
    }
    return 0;
  });

  const visible = showAll ? sorted : sorted.slice(0, PAGE);
  const hasMore = sorted.length > visible.length;
  const filtersActive = q || capFilter || statusFilter !== "all";
  const allCaps = [...new Set(visibleModels.flatMap(m => getCaps(m)))];
  const showSearch = visibleModels.length > 5;
  const hiddenCount = api.models.length - visibleModels.length;

  return (
    <>
      {showSearch && (
        <div className="pl-model-toolbar">
          <div className="pl-model-search">
            <Icon name="search" size={12} />
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); setShowAll(true); }}
              placeholder={t('settings.model_list.search_placeholder', { count: visibleModels.length })}
            />
            {q && <button className="iconbtn" onClick={() => setQ("")} style={{width: 18, height: 18}}>
              <Icon name="close" size={10} />
            </button>}
          </div>
          <div className="seg" style={{flexShrink: 0}}>
            <button className={statusFilter === "all" ? "active" : ""} onClick={() => setStatusFilter("all")}>
              {t('settings.model_list.filter_all')} <span className="muted-2" style={{marginLeft: 4, fontSize: 10.5}}>{visibleModels.length}</span>
            </button>
            <button className={statusFilter === "enabled" ? "active" : ""} onClick={() => setStatusFilter("enabled")}>
              {t('settings.model_list.filter_enabled')} <span className="muted-2" style={{marginLeft: 4, fontSize: 10.5}}>{visibleModels.filter(m => m.enabled).length}</span>
            </button>
            <button className={statusFilter === "err" ? "active" : ""} onClick={() => setStatusFilter("err")}>
              {t('settings.model_list.filter_err')} <span className="muted-2" style={{marginLeft: 4, fontSize: 10.5}}>{visibleModels.filter(m => m.health === "err").length}</span>
            </button>
          </div>
          <select
            value={sortKey} onChange={(e) => setSortKey(e.target.value)}
            style={{height: 26, fontSize: 11.5, padding: "0 8px", width: "auto", flexShrink: 0}}
          >
            <option value="smart">{t('settings.model_list.sort_smart')}</option>
            <option value="name">{t('settings.model_list.sort_name')}</option>
            <option value="context">{t('settings.model_list.sort_context')}</option>
            <option value="health">{t('settings.model_list.sort_health')}</option>
          </select>
        </div>
      )}
      {showSearch && allCaps.length > 0 && (
        <div className="pl-model-caps-row">
          <span className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", marginRight: 4}}>{t('settings.model_list.caps_label')}</span>
          {allCaps.map(c => (
            <button
              key={c}
              className={`pl-cap-tag clickable ${capFilter === c ? "active" : ""}`}
              onClick={() => setCapFilter(capFilter === c ? null : c)}
              data-tip={`筛选含『${CAP_LABEL[c] || c}』能力的模型`}
            >
              {CAP_LABEL[c] || c}
            </button>
          ))}
          {capFilter && (
            <button className="pl-cap-tag clickable clear" onClick={() => setCapFilter(null)}>
              <Icon name="close" size={9} /> {t('settings.model_list.clear_filter')}
            </button>
          )}
        </div>
      )}
      {sorted.length === 0 ? (
        <div className="pl-model-empty">
          <Icon name="search" size={16} style={{color: "var(--muted-2)"}} />
          <div>{t('settings.model_list.no_match', { count: visibleModels.length })}</div>
          {filtersActive && <button className="btn ghost" onClick={() => { setQ(""); setCapFilter(null); setStatusFilter("all"); }}>{t('settings.model_list.clear_filter')}</button>}
        </div>
      ) : (
        <CSTable
          variant="embedded"
          trackBy="id"
          items={visible}
          columnDefinitions={[
            {
              id: "health",
              header: "",
              width: 32,
              // A4: 传 statusDetail；无字段时 undefined → 向后兼容
              cell: (m) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <HealthDot health={m.health} statusDetail={m.health_status_detail} />
                  <StatusDetailBadge statusDetail={m.health_status_detail} />
                </span>
              ),
            },
            {
              id: "name",
              header: t('settings.model_list.col_name'),
              cell: (m) => <ModelNameCell m={m} onRename={(v) => onRenameModel?.(m.id, v)} deprecated={!!m.deprecated_at} />,
            },
            {
              id: "caps",
              header: t('settings.model_list.col_caps'),
              cell: (m) => (
                <div style={{display: "flex", gap: 4, flexWrap: "wrap"}}>
                  {getCaps(m).map(c => (
                    <span key={c} className="pl-cap-tag" data-tip={CAP_LABEL[c] || c}>{CAP_LABEL[c] || c}</span>
                  ))}
                </div>
              ),
            },
            {
              id: "price",
              header: t('settings.model_list.col_price'),
              cell: (m) => (
                <span className="mono muted">
                  {/* Wave 11-C: 优先展示 typed ModelInfo pricing(per million),兼容旧 price 字符串 */}
                  {m.input_cost_per_million != null
                    ? <span data-tip={`输入 $${m.input_cost_per_million}/M · 输出 $${m.output_cost_per_million ?? "?"}/M`}>
                        {fmtPrice(m.input_cost_per_million)} / {fmtPrice(m.output_cost_per_million)}
                      </span>
                    : (m.price || "—")}
                </span>
              ),
            },
            {
              id: "context",
              header: t('settings.model_list.col_context'),
              cell: (m) => (
                <span className="mono muted">
                  {/* Wave 11-C: 优先展示 typed context_window,兼容旧 context 字符串 */}
                  {m.context_window != null ? fmtCtx(m.context_window) : (m.context || "—")}
                  {m.max_output_tokens != null && (
                    <div className="muted-2" style={{fontSize: 10}} data-tip={`最大输出 ${fmtCtx(m.max_output_tokens)} tokens`}>
                      ↑{fmtCtx(m.max_output_tokens)}
                    </div>
                  )}
                </span>
              ),
            },
            {
              id: "source",
              header: t('settings.model_list.col_source'),
              width: 70,
              cell: (m) => {
                const isDeprecated = !!m.deprecated_at;
                return (
                  <span style={{fontSize: 11}} className="muted-2">
                    {/* Wave 11-C: catalog 数据来源 */}
                    {m.source ? (
                      <span className="pl-cap-tag" data-tip={`数据来源: ${sourceLabel(m.source)}`} style={{fontSize: 10}}>
                        {sourceLabel(m.source)}
                      </span>
                    ) : "—"}
                    {isDeprecated && (
                      <span className="pl-cap-tag" data-tip={`deprecated: ${m.deprecated_at}`} style={{marginLeft: 2, color: "var(--warn)", fontSize: 10, borderColor: "var(--warn)"}}>
                        {t('settings.model_list.deprecated')}
                      </span>
                    )}
                  </span>
                );
              },
            },
            {
              id: "toggle",
              header: "",
              width: 48,
              cell: (m) => <SettingsToggle on={m.enabled} set={() => onToggleModel(m.id)} />,
            },
          ]}
        />
      )}
      {hasMore && (
        <button className="pl-model-more" onClick={() => setShowAll(true)}>
          <Icon name="chevron_down" size={12} />
          {t('settings.model_list.expand_all', { count: sorted.length, shown: visible.length })}
        </button>
      )}
      {showAll && filtered.length > PAGE && (
        <button className="pl-model-more" onClick={() => setShowAll(false)}>
          <Icon name="chevron_up" size={12} /> {t('settings.model_list.collapse')}
        </button>
      )}
      {hiddenCount > 0 && (
        <div className="pl-model-hidden-note muted-2">
          {t('settings.model_list.hidden_note', { count: hiddenCount })}
        </div>
      )}
    </>
  );
}

function ModelNameCell({ m, onRename, deprecated }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useStatePL(false);
  const [val, setVal] = useStatePL(m.display);
  React.useEffect(() => { setVal(m.display); }, [m.display]);
  const apply = () => {
    const v = val.trim();
    if (v && v !== m.display) onRename?.(v);
    setEditing(false);
  };
  const cancel = () => { setVal(m.display); setEditing(false); };
  if (editing) {
    return (
      <div className="pl-title-cell pl-model-edit">
        <div className="pl-model-edit-row">
          <input
            autoFocus
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); apply(); }
              else if (e.key === "Escape") { e.preventDefault(); cancel(); }
            }}
            style={{fontSize: 13, padding: "4px 8px", fontFamily: "var(--font-serif)"}}
          />
          <button className="iconbtn pl-edit-confirm" onClick={apply}>
            <Icon name="check" size={12} />
          </button>
          <button className="iconbtn pl-edit-cancel" onClick={cancel}>
            <Icon name="close" size={12} />
          </button>
        </div>
        <span className="muted-2 mono">{m.real_name}</span>
      </div>
    );
  }
  return (
    <div className="pl-title-cell">
      <strong
        style={{fontSize: 13.5, cursor: "text", textDecoration: deprecated ? "line-through" : "none", opacity: deprecated ? 0.7 : 1}}
        onDoubleClick={() => setEditing(true)}
        data-tip={deprecated ? `deprecated · ${m.deprecated_at || ""}` : t('settings.model_list.tip_double_click')}
      >
        {m.display}
        {deprecated && <span style={{marginLeft: 4, fontSize: 11, color: "var(--warn)"}}><Icon name="warn" size={10} /></span>}
      </strong>
      <span className="muted-2 mono">{m.real_name}</span>
    </div>
  );
}

// A4: status_detail 徽标 — 如后端返回 key_expired / forbidden，展示对应橙/红徽标
function StatusDetailBadge({ statusDetail }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useStatePL(false);
  if (!statusDetail) return null;
  if (statusDetail === 'key_expired') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span
          className="pl-cap-tag"
          style={{ background: 'rgba(200,100,0,0.15)', color: 'var(--warn,#d4823c)', borderColor: 'var(--warn,#d4823c)', cursor: 'pointer', fontSize: 10.5 }}
          onClick={() => setExpanded(e => !e)}
        >
          {t('settings.model_list.key_expired')}
        </span>
        {expanded && (
          <span style={{ fontSize: 11, color: 'var(--warn,#d4823c)', background: 'rgba(200,100,0,0.10)', padding: '2px 6px', borderRadius: 4 }}>
            {t('settings.model_list.key_expired_detail')}
          </span>
        )}
      </span>
    );
  }
  if (statusDetail === 'forbidden') {
    return (
      <span
        className="pl-cap-tag"
        style={{ background: 'rgba(200,40,40,0.12)', color: 'var(--danger,#d44)', borderColor: 'var(--danger,#d44)', fontSize: 10.5 }}
      >
        {t('settings.model_list.no_permission')}
      </span>
    );
  }
  return null;
}

function HealthDot({ health, statusDetail }) {
  const { t } = useTranslation();
  const map = {
    ok:       { color: "ok",      label: t('settings.model_list.health_ok') },
    degraded: { color: "warn",    label: t('settings.model_list.health_degraded') },
    err:      { color: "danger",  label: t('settings.model_list.health_err') },
    untested: { color: "muted-2", label: t('settings.model_list.health_untested') },
  };
  // A4: status_detail 优先覆盖 label
  const detail = statusDetail; // 向后兼容：没有字段则 undefined
  const labelSuffix = detail === 'key_expired' ? ` · ${t('settings.model_list.key_expired')}`
    : detail === 'forbidden' ? ` · ${t('settings.model_list.no_permission')}`
    : '';
  const v = map[health] || map.untested;
  return (
    <span className="pl-health" data-tip={v.label + labelSuffix}>
      <span className={`dot ${v.color}`} />
    </span>
  );
}

// Wave 11-C: typed map 对齐 ModelCapabilities struct 字段
// import type { ModelInfo } from "@/types/rust/catalog/ModelInfo"
// import type { ProviderId } from "@/types/rust/catalog/ProviderId"
// import type { ModelCapabilities } from "@/types/rust/catalog/ModelCapabilities"
// import type { CatalogSource } from "@/types/rust/catalog/CatalogSource"
/** @type {Record<keyof import("../types/rust/catalog/ModelCapabilities").ModelCapabilities, string>} */
// Wave 11.5-A: CAP_LABEL / capFlags 抽到 components/catalog-helpers.js,
// 这里只读 window 上的副本(由 entries/platform.jsx 提前 import 注册)。
const CAP_LABEL = window.CAP_LABEL;
const capFlags = window.capFlags;

/** @param {import("../types/rust/catalog/CatalogSource").CatalogSource} source */
function sourceLabel(source) {
  const MAP = {
    LiveApi:        "Live API",
    StaticCatalog:  "Static",
    UserOverride:   "用户覆盖",
    OpenRouterProxy:"OpenRouter Proxy",
  };
  return MAP[source] || source || "—";
}

/** @param {number|null|undefined} n context_window 格式化 */
function fmtCtx(n) {
  if (!n) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

/** @param {number|null|undefined} v 每百万 token 价格 → 格式化 */
function fmtPrice(v) {
  if (v === null || v === undefined) return null;
  return `$${v.toFixed(3)}`;
}

const MODELS_DATA = [
  {
    id: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…3a9f", proxy: "直连",
    models: [
      { id: "gpt-4o-mini-rpg", real_name: "gpt-4o-mini-2024-07-18", display: "GPT-4o · RPG 调优", capabilities: ["fast", "rpg", "vision"], enabled: true, price: "$0.15 / $0.60", context: "128K", health: "ok", visible: true },
      { id: "gpt-4o-2024-11-20", real_name: "gpt-4o-2024-11-20", display: "GPT-4o · 标准", capabilities: ["text", "vision", "tool-use"], enabled: true, price: "$2.50 / $10.00", context: "128K", health: "ok", visible: true },
      { id: "o3-mini", real_name: "o3-mini", display: "o3-mini · 推理", capabilities: ["reasoning"], enabled: false, price: "$1.10 / $4.40", context: "200K", health: "ok", visible: true },
      { id: "gpt-4-turbo", real_name: "gpt-4-turbo-2024-04-09", display: "GPT-4 Turbo", capabilities: ["text", "vision"], enabled: false, price: "$10 / $30", context: "128K", health: "ok", visible: true },
    ]
  },
  {
    id: "anthropic", name: "Anthropic", base_url: "https://api.anthropic.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-ant-…b211", proxy: "直连",
    models: [
      { id: "claude-opus-4-1", real_name: "claude-opus-4-1", display: "Claude Opus 4.1 · 长文", capabilities: ["long", "tool-use", "rpg"], enabled: true, price: "$15 / $75", context: "200K", health: "degraded", visible: true },
      { id: "claude-sonnet-4", real_name: "claude-sonnet-4", display: "Claude Sonnet 4", capabilities: ["text", "fast"], enabled: true, price: "$3 / $15", context: "200K", health: "err", visible: true },
      { id: "claude-haiku-3-5", real_name: "claude-haiku-3-5", display: "Claude Haiku 3.5", capabilities: ["fast"], enabled: false, price: "$0.80 / $4", context: "200K", health: "ok", visible: true },
    ]
  },
  {
    id: "google", name: "Google", base_url: "https://generativelanguage.googleapis.com/v1beta",
    enabled: false, status: "未连接", key_set: false, proxy: "需配置 API key",
    models: [
      { id: "gemini-3-flash", real_name: "gemini-3.0-flash-exp", display: "Gemini 3 Flash · 实验", capabilities: ["fast", "vision"], enabled: false, price: "—", context: "1M", health: "ok", visible: true },
    ]
  },
  {
    id: "qwen", name: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…c024", proxy: "直连",
    models: [
      { id: "qwen-max", real_name: "qwen-max-2024-09-19", display: "Qwen-Max · 中文 RPG", capabilities: ["cn", "rpg", "text"], enabled: true, price: "¥0.04 / ¥0.12", context: "32K", health: "untested", visible: true },
      { id: "qwen-plus", real_name: "qwen-plus", display: "Qwen-Plus", capabilities: ["cn", "fast"], enabled: true, price: "¥0.004 / ¥0.012", context: "131K", health: "ok", visible: true },
    ]
  },
  {
    id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…a8d2", proxy: "直连",
    models: [
      { id: "deepseek-r1", real_name: "deepseek-reasoner", display: "DeepSeek R1 · 推理", capabilities: ["reasoning", "cn"], enabled: true, price: "¥4 / ¥16", context: "64K", health: "ok", visible: true },
    ]
  },
  {
    id: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-or-…f72e", proxy: "直连",
    models: ((() => {
      const data = [
        ["openai/gpt-4o", "GPT-4o", ["text", "vision", "tool-use"], "$2.50 / $10.00", "128K", true],
        ["openai/gpt-4o-mini", "GPT-4o mini", ["fast", "vision"], "$0.15 / $0.60", "128K", true],
        ["openai/o3-mini", "o3-mini", ["reasoning"], "$1.10 / $4.40", "200K", false],
        ["openai/o1", "o1", ["reasoning"], "$15 / $60", "200K", false],
        ["anthropic/claude-opus-4-1", "Claude Opus 4.1", ["long", "tool-use"], "$15 / $75", "200K", true],
        ["anthropic/claude-sonnet-4", "Claude Sonnet 4", ["text", "fast"], "$3 / $15", "200K", false],
        ["anthropic/claude-haiku-3-5", "Claude Haiku 3.5", ["fast"], "$0.80 / $4", "200K", false],
        ["google/gemini-pro-1.5", "Gemini Pro 1.5", ["long", "vision"], "$1.25 / $5", "2M", false],
        ["google/gemini-flash-1.5", "Gemini Flash 1.5", ["fast", "vision"], "$0.075 / $0.30", "1M", false],
        ["google/gemini-2.0-flash-exp", "Gemini 2.0 Flash", ["fast", "vision"], "free", "1M", false],
        ["meta-llama/llama-3.1-405b", "Llama 3.1 405B", ["text"], "$2.70 / $2.70", "131K", false],
        ["meta-llama/llama-3.1-70b", "Llama 3.1 70B", ["text"], "$0.40 / $0.40", "131K", false],
        ["meta-llama/llama-3.3-70b", "Llama 3.3 70B", ["text"], "$0.13 / $0.40", "131K", false],
        ["mistralai/mistral-large", "Mistral Large", ["text", "tool-use"], "$2 / $6", "128K", false],
        ["mistralai/mistral-nemo", "Mistral Nemo", ["fast"], "$0.13 / $0.13", "128K", false],
        ["mistralai/codestral", "Codestral", ["text"], "$0.30 / $0.90", "32K", false],
        ["deepseek/deepseek-r1", "DeepSeek R1", ["reasoning", "cn"], "¥4 / ¥16", "64K", false],
        ["deepseek/deepseek-chat", "DeepSeek Chat", ["cn", "fast"], "¥1 / ¥2", "64K", false],
        ["qwen/qwen-2.5-72b", "Qwen 2.5 72B", ["cn", "long"], "$0.35 / $0.40", "131K", false],
        ["qwen/qwen-2.5-coder-32b", "Qwen 2.5 Coder 32B", ["text"], "$0.18 / $0.18", "33K", false],
        ["x-ai/grok-2", "Grok 2", ["text"], "$2 / $10", "128K", false],
        ["x-ai/grok-2-vision", "Grok 2 Vision", ["vision"], "$2 / $10", "8K", false],
        ["nousresearch/hermes-3-llama-3.1-70b", "Hermes 3 70B", ["rpg"], "$0.40 / $0.40", "131K", true],
        ["nousresearch/hermes-3-llama-3.1-405b", "Hermes 3 405B", ["rpg"], "$1.79 / $2.49", "131K", false],
        ["cohere/command-r-plus", "Command R+", ["tool-use"], "$2.50 / $10", "128K", false],
        ["cohere/command-r", "Command R", ["fast"], "$0.15 / $0.60", "128K", false],
        ["perplexity/llama-3.1-sonar-large", "Sonar Large", ["text"], "$1 / $1", "127K", false],
        ["microsoft/phi-3.5-mini", "Phi-3.5 mini", ["fast"], "$0.10 / $0.10", "128K", false],
        ["amazon/nova-pro", "Amazon Nova Pro", ["vision"], "$0.80 / $3.20", "300K", false],
        ["amazon/nova-lite", "Amazon Nova Lite", ["fast", "vision"], "$0.06 / $0.24", "300K", false],
        ["01-ai/yi-large", "Yi Large", ["cn"], "$3 / $3", "32K", false],
        ["zhipu/glm-4-plus", "GLM-4 Plus", ["cn"], "¥0.05 / ¥0.05", "128K", false],
        ["moonshot/kimi-k1.5", "Kimi K1.5", ["cn", "long", "reasoning"], "¥0.30 / ¥3", "200K", false],
        ["minimax/abab-7-preview", "MiniMax abab-7", ["cn"], "¥10 / ¥10", "245K", false],
        ["aetherwiing/mn-starcannon-12b", "Starcannon 12B", ["rpg"], "$0.80 / $1.20", "8K", false],
        ["sao10k/l3-euryale-70b", "Euryale 70B", ["rpg"], "$1.48 / $1.48", "16K", false],
      ];
      const _h = ["ok","ok","ok","ok","degraded","err","ok","ok","untested","ok","ok","ok","ok","err","ok","ok","ok","ok","ok","degraded","ok","ok","ok","ok","ok","ok","err","ok","untested","ok","ok","ok","ok","ok","ok","ok"];
      return data.map(([rn, disp, caps, price, ctx, en], i) => ({
        id: rn, real_name: rn, display: disp, capabilities: caps, price, context: ctx, enabled: en,
        health: _h[i % _h.length], visible: true,
      }));
    })()),
  },
  {
    id: "local", name: "本地 vLLM", base_url: "http://127.0.0.1:8000/v1",
    enabled: false, status: "未启动", key_set: false, proxy: "局域网",
    models: [
      { id: "qwen-72b", real_name: "Qwen2.5-72B-Instruct", display: "Qwen2.5-72B · 本地", capabilities: ["cn", "long"], enabled: false, price: "本地", context: "128K", health: "ok", visible: true },
    ]
  },
];

// Wave 11-C: 10 provider typed 配置表
// /** @type {Array<{id: import("../types/rust/catalog/ProviderId").ProviderId, name: string, kind: "openai_compat"|"native", defaultBase: string, keyEnv: string, note?: string, special?: "agent_platform"|"alibaba_qwen"|"openrouter"}>} */
const PROVIDERS_CONFIG = [
  {
    id: "OpenAI",       name: "OpenAI",         kind: "openai_compat",
    defaultBase: "https://api.openai.com/v1",
    keyEnv: "OPENAI_API_KEY",
  },
  {
    id: "OpenRouter",   name: "OpenRouter",     kind: "openai_compat",
    defaultBase: "https://openrouter.ai/api/v1",
    keyEnv: "OPENROUTER_API_KEY",
    special: "openrouter",
    note: "可填中转站 OpenAI-compat 端点（如 https://your-proxy.com/v1），鉴权方式不变（Bearer）",
  },
  {
    id: "DeepSeek",     name: "DeepSeek",       kind: "openai_compat",
    defaultBase: "https://api.deepseek.com/v1",
    keyEnv: "DEEPSEEK_API_KEY",
  },
  {
    id: "XAi",          name: "xAI / Grok",     kind: "openai_compat",
    defaultBase: "https://api.x.ai/v1",
    keyEnv: "XAI_API_KEY",
  },
  {
    id: "XiaomiMimo",   name: "Xiaomi MiMo",    kind: "openai_compat",
    defaultBase: "https://chat.d.xiaomi.net/ai/api/v1",
    keyEnv: "XIAOMI_MIMO_API_KEY",
  },
  {
    id: "TencentHunyuan", name: "腾讯 Hunyuan", kind: "openai_compat",
    defaultBase: "https://api.hunyuan.cloud.tencent.com/v1",
    keyEnv: "TENCENT_HUNYUAN_API_KEY",
  },
  {
    id: "Anthropic",    name: "Anthropic",      kind: "native",
    defaultBase: "https://api.anthropic.com",
    keyEnv: "ANTHROPIC_API_KEY",
  },
  {
    id: "GoogleAIStudio", name: "Google AI Studio", kind: "native",
    defaultBase: "https://generativelanguage.googleapis.com",
    keyEnv: "GOOGLE_API_KEY",
  },
  {
    id: "AgentPlatform", name: "Agent Platform (Service Account)", kind: "native",
    defaultBase: "",
    keyEnv: "",
    special: "agent_platform",
    note: "上传 Service Account JSON（含 client_email / private_key / project_id）",
  },
  {
    id: "AlibabaQwen",  name: "阿里 DashScope / Qwen", kind: "native",
    defaultBase: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    keyEnv: "DASHSCOPE_API_KEY",
    special: "alibaba_qwen",
    note: "支持 OpenAI-compat 模式（/compatible-mode/v1）或 native DashScope 协议",
  },
];

/**
 * Wave 11-C: 10 provider 配置卡片
 * 每家 provider 独立一卡:API Key 输入 + base_url 可改(中转站)
 * Agent Platform:JSON 文件上传, 解析验证字段后 POST credentials.set
 * 阿里 DashScope:mode toggle (OpenAI-compat vs native)
 */
function ProviderConfigSection() {
  const { t } = useTranslation();
  const [creds, setCreds] = useStatePL({});
  const [saving, setSaving] = useStatePL({});
  const [agentPlatformJson, setAgentPlatformJson] = useStatePL(null);
  const [agentPlatformError, setAgentPlatformError] = useStatePL("");
  const [alibabaMode, setAlibabaMode] = useStatePL("openai_compat");

  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.credentials.list().catch(() => ({ items: [] }));
        if (cancelled) return;
        const map = {};
        for (const c of (r?.items || r?.credentials || [])) {
          const pid = c.api_id || c.id;
          map[pid] = { has_key: !!c.has_credential || !!c.has_key, key_hint: c.key_hint || "", base_url: c.base_url_override || "" };
        }
        setCreds(map);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const saveKey = async (providerId, apiKey, baseUrl) => {
    setSaving(s => ({ ...s, [providerId]: true }));
    try {
      if (apiKey && apiKey.trim()) {
        await window.api.credentials.set({ api_id: providerId, api_key: apiKey.trim() });
      }
      if (baseUrl !== undefined) {
        await window.api.models.upsertApi({ api_id: providerId, base_url: baseUrl });
      }
      window.__apiToast?.(t('settings.providers.save_ok'), { kind: "ok", duration: 1800 });
      setCreds(s => ({ ...s, [providerId]: { ...s[providerId], has_key: !!(apiKey?.trim() || s[providerId]?.has_key), base_url: baseUrl ?? s[providerId]?.base_url } }));
    } catch (e) {
      window.__apiToast?.(t('settings.providers.save_fail'), { kind: "danger", detail: e?.message });
    } finally {
      setSaving(s => ({ ...s, [providerId]: false }));
    }
  };

  const handleAgentPlatformFile = async (file) => {
    setAgentPlatformError("");
    setAgentPlatformJson(null);
    if (!file) return;
    try {
      const text = await file.text();
      const json = JSON.parse(text);
      const missing = ["client_email", "private_key", "project_id"].filter(k => !json[k]);
      if (missing.length > 0) {
        setAgentPlatformError(`JSON missing required fields: ${missing.join(", ")}`);
        return;
      }
      setAgentPlatformJson(json);
    } catch (e) {
      setAgentPlatformError("JSON parse error: " + (e?.message || "unknown"));
    }
  };

  const saveAgentPlatform = async () => {
    if (!agentPlatformJson) return;
    setSaving(s => ({ ...s, AgentPlatform: true }));
    try {
      await window.api.credentials.set({
        api_id: "AgentPlatform",
        api_key: JSON.stringify(agentPlatformJson),
      });
      window.__apiToast?.(t('settings.providers.save_cred_ok'), { kind: "ok", duration: 2000 });
      setCreds(s => ({ ...s, AgentPlatform: { ...s.AgentPlatform, has_key: true } }));
      setAgentPlatformJson(null);
    } catch (e) {
      window.__apiToast?.(t('settings.providers.save_fail'), { kind: "danger", detail: e?.message });
    } finally {
      setSaving(s => ({ ...s, AgentPlatform: false }));
    }
  };

  return (
    <SetGroup
      title={t('settings.providers.title')}
      description={t('settings.providers.description')}
      data-cap-anchor="settings.providers"
    >
      <CSSpaceBetween size="m">
        {PROVIDERS_CONFIG.map(p => {
          const cred = creds[p.id] || {};
          const isSaving = !!saving[p.id];
          return (
            <ProviderCard
              key={p.id}
              provider={p}
              cred={cred}
              isSaving={isSaving}
              agentPlatformJson={agentPlatformJson}
              agentPlatformError={agentPlatformError}
              alibabaMode={alibabaMode}
              onSaveKey={saveKey}
              onAgentPlatformFile={handleAgentPlatformFile}
              onSaveAgentPlatform={saveAgentPlatform}
              onAlibabaMode={(v) => { setAlibabaMode(v); window.api.models.upsertApi({ api_id: "AlibabaQwen", base_url: v === "openai_compat" ? "https://dashscope.aliyuncs.com/compatible-mode/v1" : "https://dashscope.aliyuncs.com/api/v1" }).catch(() => {}); }}
            />
          );
        })}
      </CSSpaceBetween>
    </SetGroup>
  );
}

function ProviderCard({ provider: p, cred, isSaving, agentPlatformJson, agentPlatformError, alibabaMode, onSaveKey, onAgentPlatformFile, onSaveAgentPlatform, onAlibabaMode }) {
  const { t } = useTranslation();
  const [keyVal, setKeyVal] = useStatePL("");
  const [baseVal, setBaseVal] = useStatePL(cred.base_url || p.defaultBase || "");
  useEffectPL(() => { setBaseVal(cred.base_url || p.defaultBase || ""); }, [cred.base_url, p.defaultBase]);

  // Agent Platform 走专用 UI
  if (p.special === "agent_platform") {
    return (
      <CSContainer>
        <CSSpaceBetween size="s">
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <div>
              <CSBox fontWeight="bold">{p.name}</CSBox>
              <CSBox color="text-body-secondary" fontSize="body-s">{p.note}</CSBox>
            </div>
            {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
          </CSSpaceBetween>
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <label className="btn ghost" style={{cursor: "pointer", position: "relative"}}>
              <Icon name="upload" size={12} /> {t('settings.providers.select_json')}
              <input
                type="file"
                accept="application/json,.json"
                style={{position: "absolute", opacity: 0, width: 0, height: 0}}
                onChange={(e) => onAgentPlatformFile(e.target.files?.[0] || null)}
              />
            </label>
            {agentPlatformJson && (
              <CSBox color="text-status-success" fontSize="body-s">
                <Icon name="check" size={11} /> {agentPlatformJson.client_email}
              </CSBox>
            )}
          </CSSpaceBetween>
          {agentPlatformError && (
            <CSAlert type="error">{agentPlatformError}</CSAlert>
          )}
          {agentPlatformJson && !agentPlatformError && (
            <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
              <CSBox color="text-body-secondary" fontSize="body-s">
                project_id: <span className="mono">{agentPlatformJson.project_id}</span>
              </CSBox>
              <CSButton variant="primary" loading={isSaving} disabled={isSaving} onClick={onSaveAgentPlatform}>
                {t('settings.providers.save_cred')}
              </CSButton>
            </CSSpaceBetween>
          )}
        </CSSpaceBetween>
      </CSContainer>
    );
  }

  // 阿里 DashScope 带 mode toggle
  if (p.special === "alibaba_qwen") {
    return (
      <CSContainer>
        <CSSpaceBetween size="s">
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <div>
              <CSBox fontWeight="bold">{p.name}</CSBox>
              <CSBox color="text-body-secondary" fontSize="body-s">{p.note}</CSBox>
            </div>
            {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
          </CSSpaceBetween>
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <div className="seg" style={{display: "flex"}}>
              <button className={alibabaMode === "openai_compat" ? "active" : ""} onClick={() => onAlibabaMode("openai_compat")}>OpenAI-compat</button>
              <button className={alibabaMode === "native" ? "active" : ""} onClick={() => onAlibabaMode("native")}>Native DashScope</button>
            </div>
            <CSBox color="text-status-inactive" fontSize="body-s">
              <span className="mono">{alibabaMode === "openai_compat" ? "/compatible-mode/v1" : "/api/v1"}</span>
            </CSBox>
          </CSSpaceBetween>
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="flex-end">
            <CSFormField label="API Key" stretch>
              <CSInput
                type="password"
                value={keyVal}
                onChange={({ detail }) => setKeyVal(detail.value)}
                placeholder={cred.has_key ? t('settings.providers.keep_key') : "sk-…"}
                autoComplete="new-password"
              />
            </CSFormField>
            <CSButton
              variant="primary"
              loading={isSaving}
              disabled={isSaving || (!keyVal.trim() && !baseVal)}
              onClick={() => onSaveKey(p.id, keyVal, baseVal)}
            >
              {t('common.save')}
            </CSButton>
          </CSSpaceBetween>
        </CSSpaceBetween>
      </CSContainer>
    );
  }

  // OpenRouter 带 base_url hint（及其它普通 provider）
  return (
    <CSContainer>
      <CSSpaceBetween size="s">
        <CSSpaceBetween key="hdr" direction="horizontal" size="xs" alignItems="center">
          <div>
            <CSBox fontWeight="bold">{p.name}</CSBox>
            {p.note && <CSBox color="text-body-secondary" fontSize="body-s">{p.note}</CSBox>}
          </div>
          {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
        </CSSpaceBetween>
        <CSSpaceBetween key="form" direction="horizontal" size="xs" alignItems="flex-end">
          <CSFormField label="API Key" stretch>
            <CSInput
              type="password"
              value={keyVal}
              onChange={({ detail }) => setKeyVal(detail.value)}
              placeholder={cred.has_key ? t('settings.providers.keep_key') : (p.keyEnv ? p.keyEnv : "sk-…")}
              autoComplete="new-password"
            />
          </CSFormField>
          <CSFormField
            label={p.special === "openrouter" ? t('settings.providers.base_url_relay') : "Base URL"}
            stretch
          >
            <CSInput
              value={baseVal}
              onChange={({ detail }) => setBaseVal(detail.value)}
              placeholder={p.defaultBase || "https://…"}
            />
          </CSFormField>
          <CSButton
            variant="primary"
            loading={isSaving}
            disabled={isSaving || (!keyVal.trim() && baseVal === (cred.base_url || p.defaultBase || ""))}
            onClick={() => onSaveKey(p.id, keyVal, baseVal)}
          >
            {t('common.save')}
          </CSButton>
        </CSSpaceBetween>
      </CSSpaceBetween>
    </CSContainer>
  );
}

function ModelParamsSection() {
  const { t } = useTranslation();
  const PRESETS = [
    { key: "balanced",     label: t('settings.modelparams.preset_balanced') },
    { key: "conservative", label: t('settings.modelparams.preset_conservative') },
    { key: "creative",     label: t('settings.modelparams.preset_creative') },
    { key: "deterministic",label: t('settings.modelparams.preset_deterministic') },
    { key: "custom",       label: t('settings.modelparams.preset_custom') },
  ];
  const [preset, setPreset] = useStatePL("balanced");
  const save = useAutoSave(t('settings.modelparams.title'), "settings");
  const [nsfw, setNsfw] = useStatePL({
    mode: "soft",
    intensity: 0.5,
    extra_prompt: "",
  });
  const [reasoningEffort, setReasoningEffort] = useStatePL("medium");
  // 从 catalog 获取当前选中模型的 capabilities,用于条件展示 reasoning_effort
  const [selectedModelCaps, setSelectedModelCaps] = useStatePL([]);
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const models = await window.api.models.list().catch(() => ({}));
        if (cancelled) return;
        const sel = models?.models?.selected ?? models?.selected ?? null;
        if (sel) {
          // sel.capabilities 可能是 array 或 object
          const caps = Array.isArray(sel.capabilities)
            ? sel.capabilities
            : (sel.capabilities ? Object.keys(sel.capabilities) : []);
          setSelectedModelCaps(caps);
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);
  const showReasoningEffort = selectedModelCaps.includes("reasoning");
  const [params, setParams] = useStatePL({
    temperature: 0.78,
    top_p: 0.92,
    top_k: 40,
    repetition_penalty: 1.15,
    frequency_penalty: 0.20,
    presence_penalty: 0.10,
    max_tokens: 1024,
    context_size: 16384,
    seed: -1,
    mirostat_mode: "off",
    mirostat_tau: 5.0,
    mirostat_eta: 0.10,
    stop: "",
  });
  const [advanced, setAdvanced] = useStatePL(false);
  // task 51 fix: 之前 `save(k)` 只传 1 个参数,useAutoSave 收到 val===undefined
  // 走 toast-only 分支 → 用户改 temperature/top_p/max_tokens 等全无效,刷新即丢。
  // 必须传 v,让 backend 真的落库 user_preferences。
  const u = (k, v) => { setParams(p => ({ ...p, [k]: v })); save(k, v); };

  const applyPreset = (name) => {
    setPreset(name);
    save("preset · " + name);
    if (name === "conservative") setParams(p => ({ ...p, temperature: 0.4, top_p: 0.85, repetition_penalty: 1.05, frequency_penalty: 0.1, presence_penalty: 0.0 }));
    else if (name === "balanced") setParams(p => ({ ...p, temperature: 0.78, top_p: 0.92, repetition_penalty: 1.15, frequency_penalty: 0.2, presence_penalty: 0.1 }));
    else if (name === "creative") setParams(p => ({ ...p, temperature: 1.0, top_p: 0.98, repetition_penalty: 1.2, frequency_penalty: 0.3, presence_penalty: 0.2 }));
    else if (name === "deterministic") setParams(p => ({ ...p, temperature: 0.1, top_p: 0.5, repetition_penalty: 1.0, frequency_penalty: 0.0, presence_penalty: 0.0 }));
  };

  return (
    <SetGroup title={t('settings.modelparams.title')} description={t('settings.modelparams.description')}>
      <SetRow label={t('settings.modelparams.preset')} description={t('settings.modelparams.preset_desc')}>
        <CSSpaceBetween direction="horizontal" size="xs">
          {PRESETS.map(p => (
            <CSButton key={p.key} variant={preset === p.key ? "primary" : "normal"} onClick={() => applyPreset(p.key)}>{p.label}</CSButton>
          ))}
        </CSSpaceBetween>
      </SetRow>

      <ParamSlider label="Temperature" desc="Higher = more random; 0 = most deterministic; recommended 0.4–1.0"
        value={params.temperature} min={0} max={2} step={0.05} unit=""
        onChange={(v) => { setPreset("custom"); u("temperature", v); }} />

      {showReasoningEffort && (
        <SetRow label={t('settings.modelparams.reasoning_effort')} description={t('settings.modelparams.reasoning_desc')}>
          <CSSpaceBetween direction="horizontal" size="xs">
            {["low", "medium", "high"].map(lv => (
              <CSButton key={lv} variant={reasoningEffort === lv ? "primary" : "normal"}
                onClick={() => { setReasoningEffort(lv); save("reasoning_effort", lv); }}>
                {lv === "low" ? t('settings.modelparams.effort_low') : lv === "medium" ? t('settings.modelparams.effort_medium') : t('settings.modelparams.effort_high')}
              </CSButton>
            ))}
          </CSSpaceBetween>
        </SetRow>
      )}

      <ParamSlider label="Top-p" desc="Cumulative probability cutoff; 0.9–0.95 is typical"
        value={params.top_p} min={0} max={1} step={0.01} unit=""
        onChange={(v) => { setPreset("custom"); u("top_p", v); }} />

      <ParamSlider label="Top-k" desc="Sample only from the top K tokens; 0 = disabled"
        value={params.top_k} min={0} max={200} step={1} unit=""
        onChange={(v) => { setPreset("custom"); u("top_k", v); }} />

      <ParamSlider label="Repetition Penalty" desc="Suppresses recently used tokens; 1.0 = no effect; 1.15–1.2 typical"
        value={params.repetition_penalty} min={1} max={2} step={0.01} unit=""
        onChange={(v) => { setPreset("custom"); u("repetition_penalty", v); }} />

      <ParamSlider label="Frequency Penalty" desc="OpenAI-style: adjusts based on token frequency so far"
        value={params.frequency_penalty} min={-2} max={2} step={0.05} unit=""
        onChange={(v) => { setPreset("custom"); u("frequency_penalty", v); }} />

      <ParamSlider label="Presence Penalty" desc="OpenAI-style: adjusts based on whether token has appeared"
        value={params.presence_penalty} min={-2} max={2} step={0.05} unit=""
        onChange={(v) => { setPreset("custom"); u("presence_penalty", v); }} />

      <SetRow label={t('settings.modelparams.max_tokens')} description={t('settings.modelparams.max_tokens_desc')}>
        <CSInput type="number" value={String(params.max_tokens)}
          onChange={({ detail }) => u("max_tokens", Number(detail.value))} />
      </SetRow>

      <SetRow label={t('settings.modelparams.context_size')} description={t('settings.modelparams.context_size_desc')}>
        <SetSelect
          value={String(params.context_size)}
          options={[
            { value: "4096",    label: "4K" },
            { value: "8192",    label: "8K" },
            { value: "16384",   label: "16K" },
            { value: "32768",   label: "32K" },
            { value: "65536",   label: "64K" },
            { value: "131072",  label: "128K" },
            { value: "1048576", label: "1M" },
          ]}
          onChange={(val) => u("context_size", Number(val))}
        />
      </SetRow>

      <SetRow label={t('settings.modelparams.seed')} description={t('settings.modelparams.seed_desc')}>
        <CSInput type="number" value={String(params.seed)}
          onChange={({ detail }) => u("seed", Number(detail.value))}
          placeholder="-1" />
      </SetRow>

      <SetRow label={t('settings.modelparams.stop')} description={t('settings.modelparams.stop_desc')}>
        <CSInput value={params.stop} onChange={({ detail }) => u("stop", detail.value)}
          placeholder="player:|system:" />
      </SetRow>

      <SetRow label={t('settings.modelparams.nsfw')} description={t('settings.modelparams.nsfw_desc')}>
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSButton variant={nsfw.mode === "block" ? "primary" : "normal"} onClick={() => setNsfw(n => ({ ...n, mode: "block" }))}>{t('settings.modelparams.nsfw_block')}</CSButton>
          <CSButton variant={nsfw.mode === "soft" ? "primary" : "normal"} onClick={() => setNsfw(n => ({ ...n, mode: "soft" }))}>{t('settings.modelparams.nsfw_soft')}</CSButton>
          <CSButton variant={nsfw.mode === "open" ? "primary" : "normal"} onClick={() => setNsfw(n => ({ ...n, mode: "open" }))}>{t('settings.modelparams.nsfw_open')}</CSButton>
          <CSButton variant={nsfw.mode === "explicit" ? "primary" : "normal"} onClick={() => setNsfw(n => ({ ...n, mode: "explicit" }))}>{t('settings.modelparams.nsfw_explicit')}</CSButton>
        </CSSpaceBetween>
      </SetRow>

      {nsfw.mode !== "block" && (
        <ParamSlider label={t('settings.modelparams.nsfw_intensity')} desc={t('settings.modelparams.nsfw_intensity_desc')}
          value={nsfw.intensity} min={0} max={1} step={0.05} unit=""
          onChange={(v) => { setNsfw(n => ({ ...n, intensity: v })); save("nsfw_intensity", v); }} />
      )}

      <SetRow label={t('settings.modelparams.nsfw_extra')} description={t('settings.modelparams.nsfw_extra_desc')}>
        <CSInput value={nsfw.extra_prompt}
          onChange={({ detail }) => setNsfw(n => ({ ...n, extra_prompt: detail.value }))}
          placeholder="All characters must be 18+ · No extreme gore" />
      </SetRow>

      <SetRow label={t('settings.modelparams.mirostat')} description={t('settings.modelparams.mirostat_desc')}>
        <CSToggle checked={advanced} onChange={({ detail }) => setAdvanced(detail.checked)}>
          {advanced ? t('settings.modelparams.mirostat_on') : t('settings.modelparams.mirostat_off')}
        </CSToggle>
      </SetRow>

      {advanced && (
        <>
          <SetRow label={t('settings.modelparams.mirostat_mode')} description={t('settings.modelparams.mirostat_mode_desc')}>
            <CSSpaceBetween direction="horizontal" size="xs">
              {["off", "v1", "v2"].map(m => (
                <CSButton key={m} variant={params.mirostat_mode === m ? "primary" : "normal"}
                  onClick={() => u("mirostat_mode", m)}>{m === "off" ? t('settings.modelparams.mirostat_off_btn') : m}</CSButton>
              ))}
            </CSSpaceBetween>
          </SetRow>
          <ParamSlider label="Mirostat τ (tau)" desc="Target perplexity; 5 is a common value" value={params.mirostat_tau} min={0} max={10} step={0.1} unit="" onChange={(v) => u("mirostat_tau", v)} />
          <ParamSlider label="Mirostat η (eta)" desc="Learning rate" value={params.mirostat_eta} min={0} max={1} step={0.01} unit="" onChange={(v) => u("mirostat_eta", v)} />
        </>
      )}

      <SetRow label={t('settings.modelparams.preview_json')} description={t('settings.modelparams.preview_json_desc')}>
        <pre className="mono" style={{
          margin: 0, padding: "10px 12px",
          background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
          borderRadius: "var(--r-2)", fontSize: 11, lineHeight: 1.6, color: "var(--text-quiet)",
          overflow: "auto", maxHeight: 180,
        }}>
{JSON.stringify({
  temperature: params.temperature,
  top_p: params.top_p,
  top_k: params.top_k,
  repetition_penalty: params.repetition_penalty,
  frequency_penalty: params.frequency_penalty,
  presence_penalty: params.presence_penalty,
  max_tokens: params.max_tokens,
  context_size: params.context_size,
  seed: params.seed,
  stop: params.stop.split("|").filter(Boolean),
  nsfw: nsfw.mode === "block" ? null : { mode: nsfw.mode, intensity: nsfw.intensity, extra: nsfw.extra_prompt },
  ...(advanced ? { mirostat_mode: params.mirostat_mode, mirostat_tau: params.mirostat_tau, mirostat_eta: params.mirostat_eta } : {})
}, null, 2)}
        </pre>
      </SetRow>
    </SetGroup>
  );
}

function ParamSlider({ label, desc, value, min, max, step, unit, onChange }) {
  return (
    <SetRow label={label} description={desc}>
      <div style={{display: "flex", alignItems: "center", gap: 8}}>
        <input type="range" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{flex: 1, minWidth: 120}} />
        <input type="number" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="mono" style={{width: 70, textAlign: "right"}} />
      </div>
    </SetRow>
  );
}

/* ModuleModelsSection — task 56：让用户给每个 LLM 子模块单独选模型。

   8 个模块,key 命名跟后端 _resolve_preferred_* 函数对齐:
     · 主 GM                   gm.api_id           + gm.model_real_name
     · Sub-GM (Context Agent)  sub_agent_model_override = {api_id, model}
     · Command Agent (/set)    set_parser.api_id   + set_parser.model_real_name
     · Console Assistant       console_assistant_model_override = {api_id, model}
     · Extractor               extractor.api_id    + extractor.model_real_name
     · Character Card Generator character_card_generator.api_id + .model_real_name
     · Critic (一致性评分)      critic.api_id       + critic.model_real_name
     · Acceptance Verifier     acceptance_verifier.api_id + .model_real_name

   特殊形态:
     sub_agent_model_override / console_assistant_model_override 后端读 dict
     {api_id, model};未配置 = 跟主 GM。删除该 dict (POST {key, value: null}) 即
     "重置为跟随主 GM"。其它模块用扁平 *.api_id / *.model_real_name 两个 key。

   下拉里展示所有 catalog.apis[*].models[*],格式 "<api_id> · <real_name>",
   disabled (model.enabled === false) 仍显示但禁选。 */
function ModuleModelsSection() {
  const { t } = useTranslation();
  const MODULES = [
    { id: "gm",            label: "主 GM",                  shape: "flat", apiKey: "gm.api_id",                     modelKey: "gm.model_real_name",                     tip: "玩家对话主响应模型。在『API 设置』里选当前模型,这里只展示。" },
    { id: "sub_agent",     label: "Sub-GM (Context Agent)", shape: "dict", overrideKey: "sub_agent_model_override", tip: "整理玩家意图 + 检索计划的子代理;空 = 跟主 GM 共享实例。" },
    { id: "set_parser",    label: "Command Agent",          shape: "flat", apiKey: "set_parser.api_id",             modelKey: "set_parser.model_real_name",             tip: "/set 命令自然语言解析子代理。" },
    { id: "console",       label: "Console Assistant",      shape: "dict", overrideKey: "console_assistant_model_override", tip: "侧栏控制台助手专用模型;空 = 跟主 GM。" },
    { id: "extractor",     label: "Extractor",              shape: "flat", apiKey: "extractor.api_id",              modelKey: "extractor.model_real_name",              tip: "GM 叙事二次解析抽 ops (两步式 GM 第二步)。" },
    { id: "card_gen",      label: "Character Card Generator", shape: "flat", apiKey: "character_card_generator.api_id", modelKey: "character_card_generator.model_real_name", tip: "侧栏创意工具:生成 / 微调角色卡。" },
    { id: "critic",        label: "Critic (一致性评分)",    shape: "flat", apiKey: "critic.api_id",                 modelKey: "critic.model_real_name",                 tip: "角色卡生成的一致性评分子代理 (0-1 阈值 0.6)。" },
    { id: "verifier",      label: "Acceptance Verifier",    shape: "flat", apiKey: "acceptance_verifier.api_id",    modelKey: "acceptance_verifier.model_real_name",    tip: "GM 输出是否满足 curator 设置的 acceptance 条件。" },
    { id: "embedder",      label: "Embedding (RAG 检索)",   shape: "flat", apiKey: "embedder.api_id",               modelKey: "embedder.model_real_name",               tip: "向量嵌入模型，用于 RAG 召回；空 = 跟主 GM（若主 GM 支持 embedding）。" },
  ];

  const [prefs, setPrefs] = useStatePL({});
  const [catalog, setCatalog] = useStatePL({ apis: [], selected: null });
  const [savingId, setSavingId] = useStatePL(null);

  const reload = React.useCallback(async () => {
    try {
      const [profile, models] = await Promise.all([
        window.api.account.profile(),
        window.api.models.list().catch(() => ({})),
      ]);
      setPrefs((profile && profile.preferences) || {});
      const apis = models?.models?.apis ?? (Array.isArray(models?.apis) ? models.apis : []) ?? [];
      const sel = models?.models?.selected ?? models?.selected ?? null;
      setCatalog({ apis: Array.isArray(apis) ? apis : [], selected: sel });
    } catch (_) {}
  }, []);
  useEffectPL(() => { reload(); }, [reload]);

  // 把所有可选模型扁平成 [{api_id, real_name, display, enabled}]
  const flatModels = useMemoPL(() => {
    const out = [];
    for (const api of (catalog.apis || [])) {
      const aid = api.api_id || api.id;
      const mods = api.models || api.entries || [];
      for (const m of mods) {
        out.push({
          api_id: aid,
          real_name: m.real_name || m.id,
          display: m.display_name || m.real_name || m.id,
          enabled: m.enabled !== false,
        });
      }
    }
    return out;
  }, [catalog]);

  const mainCurrent = useMemoPL(() => {
    // 用户偏好优先,否则取 catalog selected
    const a = prefs["gm.api_id"];
    const m = prefs["gm.model_real_name"];
    if (a && m) return { api_id: a, real_name: m };
    if (catalog.selected) return { api_id: catalog.selected.api_id, real_name: catalog.selected.model_id || catalog.selected.real_name };
    return null;
  }, [prefs, catalog]);

  /** 返回当前模块"生效中"的 {api_id, real_name} 或 null = 跟主 GM */
  const currentFor = (mod) => {
    if (mod.shape === "dict") {
      const v = prefs[mod.overrideKey];
      if (v && typeof v === "object" && (v.api_id || v.model)) {
        return { api_id: v.api_id || mainCurrent?.api_id, real_name: v.model || mainCurrent?.real_name };
      }
      return null;
    }
    // flat
    const a = prefs[mod.apiKey];
    const m = prefs[mod.modelKey];
    if (mod.id === "gm") {
      return mainCurrent;
    }
    if (a || m) return { api_id: a || mainCurrent?.api_id, real_name: m || mainCurrent?.real_name };
    return null;
  };

  /** 把下拉选中的 "api_id/real_name" or "__inherit__" 写回后端 */
  const handleChange = async (mod, value) => {
    setSavingId(mod.id);
    try {
      const calls = [];
      if (value === "__inherit__") {
        if (mod.shape === "dict") {
          calls.push(window.api.account.preferences({ [mod.overrideKey]: null }));
        } else {
          calls.push(window.api.account.preferences({ [mod.apiKey]: null }));
          calls.push(window.api.account.preferences({ [mod.modelKey]: null }));
        }
      } else {
        const sep = value.indexOf("/");
        if (sep < 0) return;
        const api_id = value.slice(0, sep);
        const real_name = value.slice(sep + 1);
        if (mod.shape === "dict") {
          calls.push(window.api.account.preferences({ [mod.overrideKey]: { api_id, model: real_name } }));
        } else {
          calls.push(window.api.account.preferences({ [mod.apiKey]: api_id }));
          calls.push(window.api.account.preferences({ [mod.modelKey]: real_name }));
        }
      }
      await Promise.all(calls);
      await reload();
      window.toast?.(t('settings.modules.save_ok', { label: mod.label }), { kind: "ok", duration: 1800 });
    } catch (e) {
      window.toast?.(t('settings.modules.save_fail', { label: mod.label }), { kind: "danger", detail: e?.message, duration: 3200 });
    } finally {
      setSavingId(null);
    }
  };

  const resetAll = async () => {
    setSavingId("__all__");
    const keys = [];
    for (const m of MODULES) {
      if (m.id === "gm") continue;  // 主 GM 不走 override,跳过
      if (m.shape === "dict") keys.push(m.overrideKey);
      else { keys.push(m.apiKey); keys.push(m.modelKey); }
    }
    try {
      const batch = {};
      keys.forEach(k => { batch[k] = null; });
      await window.api.account.preferences(batch);
      await reload();
      window.toast?.(t('settings.modules.reset_ok'), { kind: "ok", duration: 2000 });
    } catch (e) {
      window.toast?.(t('settings.modules.reset_fail'), { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setSavingId(null);
    }
  };

  return (
    <SetGroup
      title={t('settings.modules.title')}
      description={t('settings.modules.description')}
      actions={
        <CSButton variant="normal" disabled={savingId === "__all__"} onClick={resetAll}>
          {t('settings.modules.reset_all')}
        </CSButton>
      }
    >
      <CSBox>
        <span className="muted" style={{fontSize: 12}}>
          {t('settings.modules.hint')}
        </span>
      </CSBox>
      <div style={{overflowX: "auto"}}>
        <table className="pl-table" style={{width: "100%", fontSize: 13, marginTop: 8}}>
          <colgroup>
            <col style={{width: "26%"}} />
            <col style={{width: "32%"}} />
            <col style={{width: "42%"}} />
          </colgroup>
          <thead>
            <tr>
              <th style={{textAlign: "left", padding: "6px 8px"}}>{t('settings.modules.col_module')}</th>
              <th style={{textAlign: "left", padding: "6px 8px"}}>{t('settings.modules.col_current')}</th>
              <th style={{textAlign: "left", padding: "6px 8px"}}>{t('settings.modules.col_override')}</th>
            </tr>
          </thead>
          <tbody>
            {MODULES.map(mod => {
              const cur = currentFor(mod);
              const isInherit = !cur && mod.id !== "gm";
              const value = (mod.shape === "dict")
                ? (() => {
                    const v = prefs[mod.overrideKey];
                    return v && (v.api_id || v.model) ? `${v.api_id || ""}/${v.model || ""}` : "__inherit__";
                  })()
                : (mod.id === "gm")
                  ? (cur ? `${cur.api_id}/${cur.real_name}` : "")
                  : ((prefs[mod.apiKey] || prefs[mod.modelKey])
                      ? `${prefs[mod.apiKey] || ""}/${prefs[mod.modelKey] || ""}`
                      : "__inherit__");
              return (
                <tr key={mod.id} style={{borderTop: "1px solid var(--pl-line, #eee)"}}>
                  <td style={{padding: "8px 8px", verticalAlign: "top"}}>
                    <div style={{display: "flex", alignItems: "center", gap: 6}}>
                      <strong>{mod.label}</strong>
                      <span className="muted-2" data-tip={mod.tip} style={{cursor: "help", fontSize: 11}}>ⓘ</span>
                    </div>
                    <div className="muted" style={{fontSize: 11, marginTop: 2}}>{mod.tip}</div>
                  </td>
                  <td style={{padding: "8px 8px", verticalAlign: "top"}} className="mono">
                    {isInherit ? (
                      <span className="muted-2" data-tip={t('settings.modules.inherit_tip')}>{t('settings.modules.follow_main')}</span>
                    ) : cur ? (
                      <span>{cur.api_id} · {cur.real_name}</span>
                    ) : (
                      <span className="muted-2">{t('common.unknown')}</span>
                    )}
                  </td>
                  <td style={{padding: "8px 8px", verticalAlign: "top"}}>
                    {/* B3: 原生 <select> 改为 CSSelect，视觉与其他 section 一致 */}
                    {(() => {
                      const opts = [];
                      if (mod.id !== "gm") opts.push({ value: "__inherit__", label: t('settings.modules.follow_main') });
                      // fallback: 当前 value 不在 catalog 里时补一条
                      if (value !== "__inherit__" && value && !flatModels.some(m => `${m.api_id}/${m.real_name}` === value)) {
                        opts.push({ value, label: `${value} ${t('settings.modules.not_in_catalog')}` });
                      }
                      for (const m of flatModels) {
                        opts.push({
                          value: `${m.api_id}/${m.real_name}`,
                          label: `${m.api_id} · ${m.real_name}${m.enabled ? "" : ` ${t('settings.modules.disabled_model')}`}`,
                          disabled: !m.enabled,
                        });
                      }
                      const selectedOpt = opts.find(o => o.value === value) || null;
                      return (
                        <CSSelect
                          selectedOption={selectedOpt}
                          options={opts}
                          disabled={savingId === mod.id || savingId === "__all__" || mod.id === "gm"}
                          onChange={({ detail }) => handleChange(mod, detail.selectedOption.value)}
                        />
                      );
                    })()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <CSBox>
        <span className="muted" style={{fontSize: 11}}>
          {t('settings.modules.footer')}
        </span>
      </CSBox>
    </SetGroup>
  );
}


function MemorySection() {
  const { t } = useTranslation();
  // A6.2: useAutoSave namespace 改为 "memory" 让 save(k, v) 写 memory.k
  const save = useAutoSave(t('settings.nav.memory'), "memory");

  // ── 召回行为字段 ──
  const [recallDepth, setRecallDepth] = useStatePL(6);
  const [summaryWindow, setSummaryWindow] = useStatePL(8);
  const [tokenBudget, setTokenBudget] = useStatePL(800);
  const [autoArchiveAfter, setAutoArchiveAfter] = useStatePL(50);

  // ── 记忆桶配置字段 ──
  const [pinnedMax, setPinnedMax] = useStatePL(20);
  const [bucketPinnedEnabled, setBucketPinnedEnabled] = useStatePL(true);
  const [bucketWorldEnabled, setBucketWorldEnabled] = useStatePL(true);
  const [bucketCharacterEnabled, setBucketCharacterEnabled] = useStatePL(true);

  // A6.2: loadOrFallback — 读新 key 优先,不存在再读旧 key
  const loadOrFallback = (p, newKey, oldKey) => {
    if (p[newKey] !== undefined && p[newKey] !== null) return p[newKey];
    if (oldKey && p[oldKey] !== undefined && p[oldKey] !== null) return p[oldKey];
    return undefined;
  };

  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        // A6.2: 读新 key，兼容旧中文 key
        const rd = loadOrFallback(p, "memory.recall_depth", "settings.召回深度");
        if (rd !== undefined) setRecallDepth(Number(rd));
        const sw = loadOrFallback(p, "memory.summary_window", "settings.摘要窗口");
        if (sw !== undefined) setSummaryWindow(Number(sw));
        // pinned_max 同时对应旧 "settings.固定记忆上限"
        const pm = loadOrFallback(p, "memory.pinned_max", "settings.固定记忆上限");
        if (pm !== undefined) setPinnedMax(Number(pm));
        // 新字段 — 无旧 key
        if (p["memory.token_budget"] !== undefined) setTokenBudget(Number(p["memory.token_budget"]));
        if (p["memory.auto_archive_after_turns"] !== undefined) setAutoArchiveAfter(Number(p["memory.auto_archive_after_turns"]));
        if (typeof p["memory.bucket_pinned_enabled"] === "boolean") setBucketPinnedEnabled(p["memory.bucket_pinned_enabled"]);
        if (typeof p["memory.bucket_world_enabled"] === "boolean") setBucketWorldEnabled(p["memory.bucket_world_enabled"]);
        if (typeof p["memory.bucket_character_enabled"] === "boolean") setBucketCharacterEnabled(p["memory.bucket_character_enabled"]);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <CSSpaceBetween size="l">
      {/* A6.3 — 组 1: 召回行为 */}
      <SetGroup title={t('settings.memory.title_recall')}>
        <SetRow label={t('settings.memory.recall_depth')} description={t('settings.memory.recall_depth_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={2} max={20} step={1} value={recallDepth}
              onChange={(e) => setRecallDepth(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 2 && n <= 20) save("recall_depth", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 2 && n <= 20) save("recall_depth", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={2} max={20} step={1} value={recallDepth}
              onChange={(e) => setRecallDepth(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 2 && n <= 20) save("recall_depth", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.summary_window')} description={t('settings.memory.summary_window_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={3} max={20} step={1} value={summaryWindow}
              onChange={(e) => setSummaryWindow(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 3 && n <= 20) save("summary_window", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 3 && n <= 20) save("summary_window", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={3} max={20} step={1} value={summaryWindow}
              onChange={(e) => setSummaryWindow(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 3 && n <= 20) save("summary_window", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.token_budget')} description={t('settings.memory.token_budget_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={200} max={2000} step={50} value={tokenBudget}
              onChange={(e) => setTokenBudget(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 200 && n <= 2000) save("token_budget", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 200 && n <= 2000) save("token_budget", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={200} max={2000} step={50} value={tokenBudget}
              onChange={(e) => setTokenBudget(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 200 && n <= 2000) save("token_budget", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.auto_archive')} description={t('settings.memory.auto_archive_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={10} max={200} step={5} value={autoArchiveAfter}
              onChange={(e) => setAutoArchiveAfter(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 10 && n <= 200) save("auto_archive_after_turns", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 10 && n <= 200) save("auto_archive_after_turns", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={10} max={200} step={5} value={autoArchiveAfter}
              onChange={(e) => setAutoArchiveAfter(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 10 && n <= 200) save("auto_archive_after_turns", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
      </SetGroup>

      {/* A6.3 — 组 2: 记忆桶配置 */}
      <SetGroup title={t('settings.memory.title_buckets')}>
        <SetRow label={t('settings.memory.pinned_max')} description={t('settings.memory.pinned_max_desc')}>
          <CSInput type="number" value={String(pinnedMax)}
            onChange={({ detail }) => {
              setPinnedMax(detail.value);
              const n = Number(detail.value);
              if (detail.value !== '' && n >= 5 && n <= 100) save("pinned_max", n);
            }} />
        </SetRow>
        <SetRow label={t('settings.memory.bucket_pinned')} description={t('settings.memory.bucket_pinned_desc')}>
          <CSToggle checked={bucketPinnedEnabled}
            onChange={({ detail }) => { setBucketPinnedEnabled(detail.checked); save("bucket_pinned_enabled", detail.checked); }}>
            {bucketPinnedEnabled ? t('common.enabled') : t('common.disabled')}
          </CSToggle>
        </SetRow>
        <SetRow label={t('settings.memory.bucket_world')} description={t('settings.memory.bucket_world_desc')}>
          <CSToggle checked={bucketWorldEnabled}
            onChange={({ detail }) => { setBucketWorldEnabled(detail.checked); save("bucket_world_enabled", detail.checked); }}>
            {bucketWorldEnabled ? t('common.enabled') : t('common.disabled')}
          </CSToggle>
        </SetRow>
        <SetRow label={t('settings.memory.bucket_character')} description={t('settings.memory.bucket_character_desc')}>
          <CSToggle checked={bucketCharacterEnabled}
            onChange={({ detail }) => { setBucketCharacterEnabled(detail.checked); save("bucket_character_enabled", detail.checked); }}>
            {bucketCharacterEnabled ? t('common.enabled') : t('common.disabled')}
          </CSToggle>
        </SetRow>
      </SetGroup>
    </CSSpaceBetween>
  );
}

const _HIGH_RISK_DEFAULTS = ["timeline.pending_jump", "player.background", "world.constraints"];
const _HIGH_RISK_ALL = ["timeline.pending_jump", "player.background", "world.constraints", "relationships.*.tone"];

// B1: 自定义白名单输入校验 regex
const _CUSTOM_WL_RE = /^[a-zA-Z_][a-zA-Z0-9_.*]*$/;

function PermSection() {
  const { t } = useTranslation();
  // task 52：从 user_preferences 拉真实值，改动 patch /api/me/preference
  const [defaultMode, setDefaultMode] = useStatePL("review");
  const [highRiskWhitelist, setHighRiskWhitelist] = useStatePL(_HIGH_RISK_DEFAULTS);
  // B1: 自定义白名单
  const [customWhitelist, setCustomWhitelist] = useStatePL([]);
  const [customInput, setCustomInput] = useStatePL("");
  const [customInputError, setCustomInputError] = useStatePL("");
  const save = useAutoSave(t('settings.nav.permissions'), "perm");

  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const v = p["perm.default_mode"] || p.default_perm_mode;
        if (v) setDefaultMode(v);
        const wl = p["perm.high_risk_whitelist"];
        if (Array.isArray(wl)) setHighRiskWhitelist(wl);
        // B1: 读自定义白名单
        const cwl = p["permissions.custom_whitelist"];
        if (Array.isArray(cwl)) setCustomWhitelist(cwl);
        else {
          // localStorage 兜底
          try {
            const stored = localStorage.getItem("perm.custom_whitelist");
            if (stored) setCustomWhitelist(JSON.parse(stored));
          } catch (_) {}
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleWhitelist = (field) => {
    const next = highRiskWhitelist.includes(field)
      ? highRiskWhitelist.filter(f => f !== field)
      : [...highRiskWhitelist, field];
    setHighRiskWhitelist(next);
    save("high_risk_whitelist", next);
  };

  // B1: 保存自定义白名单（尝试后端，兜底 localStorage）
  const saveCustomWhitelist = async (next) => {
    setCustomWhitelist(next);
    try {
      await window.api.account.preferences({ "permissions.custom_whitelist": next });
    } catch (_) {
      // 后端不支持则 localStorage 兜底
    }
    try { localStorage.setItem("perm.custom_whitelist", JSON.stringify(next)); } catch (_) {}
  };

  const addCustomEntry = () => {
    const val = customInput.trim();
    if (!val) { setCustomInputError(t('settings.permissions.err_empty')); return; }
    if (val.length > 80) { setCustomInputError(t('settings.permissions.err_too_long')); return; }
    if (!_CUSTOM_WL_RE.test(val)) { setCustomInputError(t('settings.permissions.err_invalid')); return; }
    if (_HIGH_RISK_ALL.includes(val)) { setCustomInputError(t('settings.permissions.err_in_builtin')); return; }
    if (customWhitelist.includes(val)) { setCustomInputError(t('settings.permissions.err_duplicate')); return; }
    const next = [...customWhitelist, val];
    saveCustomWhitelist(next);
    setCustomInput("");
    setCustomInputError("");
  };

  const removeCustomEntry = (entry) => {
    const next = customWhitelist.filter(e => e !== entry);
    saveCustomWhitelist(next);
  };

  return (
    <SetGroup title={t('settings.permissions.title')}>
      <SetRow label={t('settings.permissions.default_mode')} description={t('settings.permissions.default_mode_desc')}>
        <SetSelect
          value={defaultMode}
          options={[
            { value: "default",     label: t('settings.permissions.mode_default') },
            { value: "review",      label: t('settings.permissions.mode_review') },
            { value: "full_access", label: t('settings.permissions.mode_full') },
          ]}
          onChange={(val) => { setDefaultMode(val); save("default_mode", val); }}
        />
      </SetRow>
      <SetRow label={t('settings.permissions.high_risk')} description={t('settings.permissions.high_risk_desc')}>
        <CSSpaceBetween direction="horizontal" size="xs">
          {_HIGH_RISK_ALL.map(field => (
            <CSButton
              key={field}
              variant={highRiskWhitelist.includes(field) ? "primary" : "normal"}
              onClick={() => toggleWhitelist(field)}
            >{field}</CSButton>
          ))}
        </CSSpaceBetween>
      </SetRow>

      {/* B1: 自定义高风险白名单 */}
      <SetRow label={t('settings.permissions.custom_whitelist')} description={t('settings.permissions.custom_whitelist_desc')}>
        <CSSpaceBetween size="s">
          <div style={{display: "flex", gap: 8, alignItems: "flex-start"}}>
            <div style={{flex: 1}}>
              <CSInput
                value={customInput}
                placeholder={t('settings.permissions.custom_placeholder')}
                onChange={({ detail }) => { setCustomInput(detail.value); if (customInputError) setCustomInputError(""); }}
                onKeyDown={(e) => { if (e.detail?.key === "Enter" || e.key === "Enter") addCustomEntry(); }}
                invalid={!!customInputError}
              />
              {customInputError && (
                <div style={{color: "var(--danger, #c8675d)", fontSize: 12, marginTop: 4}}>{customInputError}</div>
              )}
            </div>
            <CSButton variant="primary" onClick={addCustomEntry}>{t('settings.permissions.add_entry')}</CSButton>
          </div>
          {customWhitelist.length > 0 && (
            <div style={{display: "flex", flexWrap: "wrap", gap: 6}}>
              {customWhitelist.map(entry => (
                <div key={entry} style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "3px 8px", borderRadius: 4,
                  background: "var(--bg-deep, #f0f0f2)", border: "1px solid var(--line-soft, #ddd)",
                  fontSize: 13, fontFamily: "ui-monospace, monospace",
                }}>
                  <span>{entry}</span>
                  <button
                    onClick={() => removeCustomEntry(entry)}
                    style={{
                      border: "none", background: "none", cursor: "pointer",
                      color: "var(--danger, #c8675d)", fontSize: 14, padding: "0 2px", lineHeight: 1,
                    }}
                    title={t('common.delete')}
                  >×</button>
                </div>
              ))}
            </div>
          )}
          {customWhitelist.length === 0 && (
            <span className="muted" style={{fontSize: 12}}>{t('settings.permissions.no_entries')}</span>
          )}
        </CSSpaceBetween>
      </SetRow>

      <AuditLogView />
    </SetGroup>
  );
}

// AuditLogView — task 65：把 state.permissions.audit_log 暴露给用户。
// 后端在多处写 audit 条目：
//   - kind=write           普通写入留痕（state.py:798）
//   - kind=parse_error     LLM 输出标签解析失败（task 60）
//   - kind=rejected        权限闸门拒绝（low/medium/high）
//   - kind=hard_forbidden  permissions.x / history.x 黑名单
//   - kind=extractor_error GM 第二步失败（task 65 新增）
//   - kind=question_skip   pending_question 玩家跳过
// 现在前端能看见这些，便于排查 GM 行为异常。
function AuditLogView() {
  const { t } = useTranslation();
  const [entries, setEntries] = useStatePL([]);
  const [loading, setLoading] = useStatePL(false);
  const [hasState, setHasState] = useStatePL(true);
  const [error, setError] = useStatePL("");
  const [kindFilter, setKindFilter] = useStatePL("all");
  const refresh = React.useCallback(async () => {
    setLoading(true); setError("");
    try {
      const s = await window.api.game.state();
      const perms = (s && (s.permissions || s.state?.permissions)) || {};
      const log = Array.isArray(perms.audit_log) ? perms.audit_log : [];
      // 倒序展示，最近的在前
      setEntries(log.slice().reverse());
      setHasState(!!s);
    } catch (e) {
      setError(e?.message || t('settings.permissions.audit_log'));
      setHasState(false);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffectPL(() => { refresh(); }, []);

  // 用 .ok / .danger（来自 tokens.css 的全局色类）+ 内联色给 warning/muted
  const KIND_META = {
    write:             { label: t('settings.permissions.kind_write'),            color: "var(--ok, #7eb88e)",      desc: "" },
    parse_error:       { label: t('settings.permissions.kind_parse_error'),      color: "var(--warning, #d4a857)", desc: "" },
    rejected:          { label: t('settings.permissions.kind_rejected'),         color: "var(--danger, #c8675d)",  desc: "" },
    hard_forbidden:    { label: t('settings.permissions.kind_hard_forbidden'),   color: "var(--danger, #c8675d)",  desc: "" },
    extractor_error:   { label: t('settings.permissions.kind_extractor_error'),  color: "var(--warning, #d4a857)", desc: "" },
    set_parser_error:  { label: t('settings.permissions.kind_set_parser_error'), color: "var(--warning, #d4a857)", desc: "" },
    clarify_yield:     { label: t('settings.permissions.kind_clarify_yield'),    color: "var(--ok, #7eb88e)",      desc: "" },
    acceptance_unmet:  { label: t('settings.permissions.kind_acceptance_unmet'), color: "var(--warning, #d4a857)", desc: "" },
    question_skip:     { label: t('settings.permissions.kind_question_skip'),    color: "var(--muted, #888)",      desc: "" },
  };
  const kinds = ["all", ...Object.keys(KIND_META)];
  const filtered = kindFilter === "all" ? entries : entries.filter(e => e.kind === kindFilter);

  return (
    <>
      <SetRow
        label={t('settings.permissions.audit_log')}
        description={t('settings.permissions.audit_log_desc')}
      >
        <CSSpaceBetween direction="horizontal" size="s">
          <CSButton variant="normal" onClick={refresh} disabled={loading}>
            {loading ? t('settings.permissions.audit_loading') : t('settings.permissions.audit_refresh')}
          </CSButton>
          {error && <CSAlert type="error">{error}</CSAlert>}
        </CSSpaceBetween>
      </SetRow>
      <SetRow label={t('settings.permissions.audit_filter')} description="">
        <CSSpaceBetween direction="horizontal" size="xs">
          {kinds.map(k => {
            const meta = KIND_META[k];
            const count = k === "all" ? entries.length : entries.filter(e => e.kind === k).length;
            return (
              <CSButton
                key={k}
                variant={kindFilter === k ? "primary" : "normal"}
                onClick={() => setKindFilter(k)}
                title={meta?.desc || ""}
              >
                {k === "all" ? t('settings.permissions.audit_all') : (meta?.label || k)} · {count}
              </CSButton>
            );
          })}
        </CSSpaceBetween>
      </SetRow>
      {!hasState ? (
        <CSAlert type="info">{t('settings.permissions.audit_no_state')}</CSAlert>
      ) : filtered.length === 0 ? (
        <CSAlert type="info">
          {entries.length === 0 ? t('settings.permissions.audit_empty') : t('settings.permissions.audit_empty_filter', { kind: kindFilter })}
        </CSAlert>
      ) : (
        <div style={{maxHeight: 360, overflowY: "auto", border: "1px solid var(--pl-line, #eee)", borderRadius: 6}}>
          <table className="pl-table" style={{width: "100%", fontSize: 12, borderCollapse: "collapse"}}>
            <thead>
              <tr style={{background: "var(--pl-bg-soft, #f7f7f9)"}}>
                <th style={{textAlign: "left", padding: "6px 8px", width: 130}}>{t('settings.permissions.audit_col_time')}</th>
                <th style={{textAlign: "left", padding: "6px 8px", width: 90}}>{t('settings.permissions.audit_col_type')}</th>
                <th style={{textAlign: "left", padding: "6px 8px", width: 80}}>{t('settings.permissions.audit_col_source')}</th>
                <th style={{textAlign: "left", padding: "6px 8px"}}>{t('settings.permissions.audit_col_detail')}</th>
                <th style={{textAlign: "right", padding: "6px 8px", width: 50}}>{t('settings.permissions.audit_col_turn')}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, idx) => {
                const meta = KIND_META[e.kind] || { label: e.kind, color: "var(--muted, #888)", desc: "" };
                const detail = e.path
                  ? `${e.path} = ${typeof e.value === "string" ? e.value : JSON.stringify(e.value)}`
                  : (e.raw_spec || e.hint || "—");
                return (
                  <tr key={idx} style={{borderTop: "1px solid var(--pl-line, #eee)"}}>
                    <td style={{padding: "4px 8px", fontFamily: "ui-monospace, monospace"}}>{(e.ts || "").replace("T", " ")}</td>
                    <td style={{padding: "4px 8px"}}>
                      <span className="pl-rule-chip" style={{fontSize: 11, color: meta.color, borderColor: meta.color}}>{meta.label}</span>
                    </td>
                    <td style={{padding: "4px 8px"}} className="muted">{e.source || "—"}</td>
                    <td style={{padding: "4px 8px", wordBreak: "break-word"}}>
                      <div>{detail}</div>
                      {e.hint && e.path && (
                        <div className="muted" style={{fontSize: 11, marginTop: 2}}>· {e.hint}</div>
                      )}
                    </td>
                    <td style={{padding: "4px 8px", textAlign: "right"}} className="muted">{e.turn ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function DeploySection() {
  const { t } = useTranslation();
  // 部署配置通过 POST /api/admin/deployment-config 存 app_config 表。
  // 监听地址 / CORS 等网络级配置需要重启才能生效，UI 有明确提示。
  const timerRef = React.useRef(null);
  const pendingRef = React.useRef({});
  const saveDeployConfig = React.useCallback((patch) => {
    Object.assign(pendingRef.current, patch);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      const batch = pendingRef.current;
      pendingRef.current = {};
      try {
        await window.api.admin.saveDeploymentConfig(batch);
        window.toast?.(t('settings.deploy.save_ok'), { kind: "ok", duration: 2000 });
      } catch (e) {
        window.toast?.(t('settings.deploy.save_fail'), { kind: "danger", detail: e?.message || "", duration: 3000 });
      }
    }, 300);
  }, []);

  const [listenAddr, setListenAddr] = useStatePL("127.0.0.1:7860");
  const [corsOrigins, setCorsOrigins] = useStatePL("http://127.0.0.1:5173,http://localhost:3000");
  const [uploadLimit, setUploadLimit] = useStatePL("12 MB");
  const [uploadLimitError, setUploadLimitError] = useStatePL("");
  const [smtpEnabled, setSmtpEnabled] = useStatePL(false);
  const [smtpHost, setSmtpHost] = useStatePL("smtp.example.com");
  const [smtpPort, setSmtpPort] = useStatePL("587");
  const [smtpTls, setSmtpTls] = useStatePL("starttls");
  const [smtpUser, setSmtpUser] = useStatePL("noreply@example.com");
  const [smtpPass, setSmtpPass] = useStatePL("");
  const [smtpFromName, setSmtpFromName] = useStatePL("RPG Roleplay");
  const [smtpFromEmail, setSmtpFromEmail] = useStatePL("noreply@rpgroleplay.app");
  const [smtpTesting, setSmtpTesting] = useStatePL(false);
  // task 49：原"最近测试：12 分钟前"是硬编码。改成本地状态：只有用户实际
  // 点过"发送测试邮件"按钮后才记录时间戳并显示，否则显示"尚未测试"。
  const [smtpLastTestAt, setSmtpLastTestAt] = useStatePL(null);
  const [smtpLastTestOk, setSmtpLastTestOk] = useStatePL(null);
  const [captchaProvider, setCaptchaProvider] = useStatePL("off");
  // task 56：之前 6 个 captcha 子选项是 dead button（recaptcha 版本 3 个 +
  // turnstile widget 模式 3 个，没 onClick），UI 看着能切实际只是装饰。
  const [recaptchaVer, setRecaptchaVer] = useStatePL("v3");
  const [recaptchaSiteKey, setRecaptchaSiteKey] = useStatePL("");
  const [recaptchaSecretKey, setRecaptchaSecretKey] = useStatePL("");
  const [recaptchaScore, setRecaptchaScore] = useStatePL(0.5);
  const [turnstileMode, setTurnstileMode] = useStatePL("non_interactive");
  const [turnstileSiteKey, setTurnstileSiteKey] = useStatePL("");
  const [turnstileSecretKey, setTurnstileSecretKey] = useStatePL("");
  const [hcaptchaSiteKey, setHcaptchaSiteKey] = useStatePL("");
  const [hcaptchaSecretKey, setHcaptchaSecretKey] = useStatePL("");
  // S2: CAPTCHA 触发位置多选，默认注册/找回密码/登录重试已选中
  const [captchaTriggers, setCaptchaTriggers] = useStatePL(["register", "password_reset", "login_retry"]);

  // 从 backend 拉取已保存的部署配置
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.admin.deploymentConfig();
        if (cancelled) return;
        const c = (r && r.config) || {};
        if (c.listen_address) setListenAddr(c.listen_address);
        if (c.cors_origins) setCorsOrigins(c.cors_origins);
        if (c.upload_limit) setUploadLimit(c.upload_limit);
        if (c.smtp_enabled !== undefined) setSmtpEnabled(!!c.smtp_enabled);
        if (c.smtp_host) setSmtpHost(c.smtp_host);
        if (c.smtp_port) setSmtpPort(String(c.smtp_port));
        if (c.smtp_tls) setSmtpTls(c.smtp_tls);
        if (c.smtp_user) setSmtpUser(c.smtp_user);
        // smtp_pass not pre-filled for security
        if (c.smtp_from_name) setSmtpFromName(c.smtp_from_name);
        if (c.smtp_from_email) setSmtpFromEmail(c.smtp_from_email);
        if (c.captcha_provider) setCaptchaProvider(c.captcha_provider);
        if (c.recaptcha_ver) setRecaptchaVer(c.recaptcha_ver);
        if (c.recaptcha_site_key) setRecaptchaSiteKey(c.recaptcha_site_key);
        if (c.recaptcha_score !== undefined) setRecaptchaScore(Number(c.recaptcha_score));
        if (c.turnstile_mode) setTurnstileMode(c.turnstile_mode);
        if (c.turnstile_site_key) setTurnstileSiteKey(c.turnstile_site_key);
        if (c.hcaptcha_site_key) setHcaptchaSiteKey(c.hcaptcha_site_key);
        if (Array.isArray(c.captcha_triggers)) setCaptchaTriggers(c.captcha_triggers);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <SetGroup title={t('settings.deploy.title')}>
      <CSAlert type="warning">
        <strong>{t('settings.deploy.warning')}</strong>
      </CSAlert>
      <SetRow label={t('settings.deploy.listen_addr')} description={t('settings.deploy.listen_addr_desc')}>
        <CSInput value={listenAddr} onChange={({ detail }) => { setListenAddr(detail.value); saveDeployConfig({ listen_address: detail.value }); }} />
      </SetRow>
      <SetRow label={t('settings.deploy.cors')} description={t('settings.deploy.cors_desc')}>
        <CSInput value={corsOrigins} onChange={({ detail }) => { setCorsOrigins(detail.value); saveDeployConfig({ cors_origins: detail.value }); }} />
      </SetRow>
      <SetRow label={t('settings.deploy.upload_limit')} description={t('settings.deploy.upload_limit_desc')}>
        <div>
          <CSInput
            value={uploadLimit}
            invalid={!!uploadLimitError}
            onChange={({ detail }) => {
              const v = detail.value.trim();
              setUploadLimit(detail.value);
              if (!v || /^\d+\s*(MB|GB|KB|B)?$/i.test(v)) {
                setUploadLimitError("");
                if (v) saveDeployConfig({ upload_limit: v });
              } else {
                setUploadLimitError(t('settings.deploy.upload_limit_error'));
              }
            }}
            placeholder="12MB"
          />
          {uploadLimitError && (
            <div style={{color: "var(--danger)", fontSize: 11.5, marginTop: 4}}>{uploadLimitError}</div>
          )}
        </div>
      </SetRow>

      <SetRow label={t('settings.deploy.smtp')} description={t('settings.deploy.smtp_desc')}>
        <CSToggle checked={smtpEnabled} onChange={({ detail }) => { setSmtpEnabled(detail.checked); saveDeployConfig({ smtp_enabled: detail.checked }); }}>
          {smtpEnabled ? t('settings.deploy.smtp_on') : t('settings.deploy.smtp_off')}
        </CSToggle>
      </SetRow>
      {smtpEnabled && (
        <>
          <SetRow label={t('settings.deploy.smtp_preset')} description={t('settings.deploy.smtp_preset_desc')}>
            <SetSelect
              value="custom"
              options={[
                { value: "custom",   label: t('settings.deploy.smtp_custom') },
                { value: "gmail",    label: "Gmail（smtp.gmail.com:587 · STARTTLS）" },
                { value: "qq",       label: "QQ 邮箱（smtp.qq.com:465 · SSL）" },
                { value: "163",      label: "163 邮箱（smtp.163.com:465 · SSL）" },
                { value: "aws",      label: "AWS SES（email-smtp.us-east-1.amazonaws.com:587）" },
                { value: "resend",   label: "Resend（smtp.resend.com:587）" },
                { value: "sendgrid", label: "SendGrid（smtp.sendgrid.net:587）" },
              ]}
              onChange={(val) => {
                const PRESETS = {
                  gmail:    { smtp_host: "smtp.gmail.com",                          smtp_port: "587", smtp_tls: "starttls" },
                  qq:       { smtp_host: "smtp.qq.com",                             smtp_port: "465", smtp_tls: "ssl" },
                  "163":    { smtp_host: "smtp.163.com",                            smtp_port: "465", smtp_tls: "ssl" },
                  aws:      { smtp_host: "email-smtp.us-east-1.amazonaws.com",      smtp_port: "587", smtp_tls: "starttls" },
                  resend:   { smtp_host: "smtp.resend.com",                         smtp_port: "587", smtp_tls: "starttls" },
                  sendgrid: { smtp_host: "smtp.sendgrid.net",                       smtp_port: "587", smtp_tls: "starttls" },
                };
                const p = PRESETS[val];
                if (p) { setSmtpHost(p.smtp_host); setSmtpPort(p.smtp_port); setSmtpTls(p.smtp_tls); saveDeployConfig(p); }
              }}
            />
          </SetRow>
          <SetRow label={t('settings.deploy.smtp_host_port')} description={t('settings.deploy.smtp_host_port_desc')}>
            <div style={{display: "grid", gridTemplateColumns: "1fr 90px 110px", gap: 6}}>
              <CSInput value={smtpHost} placeholder={t('settings.deploy.smtp_host_placeholder')} onChange={({ detail }) => { setSmtpHost(detail.value); saveDeployConfig({ smtp_host: detail.value }); }} />
              <CSInput value={smtpPort} placeholder={t('settings.deploy.smtp_port_placeholder')} onChange={({ detail }) => { setSmtpPort(detail.value); saveDeployConfig({ smtp_port: detail.value }); }} />
              <SetSelect
                value={smtpTls}
                options={[
                  { value: "none",     label: t('settings.deploy.smtp_tls_none') },
                  { value: "starttls", label: "STARTTLS" },
                  { value: "ssl",      label: "SSL / TLS" },
                ]}
                onChange={(val) => { setSmtpTls(val); saveDeployConfig({ smtp_tls: val }); }}
              />
            </div>
          </SetRow>
          <SetRow label={t('settings.deploy.smtp_auth')} description={t('settings.deploy.smtp_auth_desc')}>
            <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6}}>
              <CSInput value={smtpUser} placeholder={t('settings.deploy.smtp_user_placeholder')} onChange={({ detail }) => { setSmtpUser(detail.value); saveDeployConfig({ smtp_user: detail.value }); }} />
              <CSInput type="password" value={smtpPass} placeholder={t('settings.deploy.smtp_pass_placeholder')} onChange={({ detail }) => { setSmtpPass(detail.value); saveDeployConfig({ smtp_pass: detail.value }); }} />
            </div>
          </SetRow>
          <SetRow label={t('settings.deploy.smtp_from')} description={t('settings.deploy.smtp_from_desc')}>
            <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6}}>
              <CSInput value={smtpFromName} placeholder={t('settings.deploy.smtp_from_name_placeholder')} onChange={({ detail }) => { setSmtpFromName(detail.value); saveDeployConfig({ smtp_from_name: detail.value }); }} />
              <CSInput value={smtpFromEmail} placeholder={t('settings.deploy.smtp_from_email_placeholder')} onChange={({ detail }) => { setSmtpFromEmail(detail.value); saveDeployConfig({ smtp_from_email: detail.value }); }} />
            </div>
          </SetRow>
          <SetRow label={t('settings.deploy.smtp_test')} description={t('settings.deploy.smtp_test_desc')}>
            <CSSpaceBetween direction="horizontal" size="s">
              <CSButton variant="normal" disabled={smtpTesting} onClick={async () => {
                setSmtpTesting(true);
                window.toast?.(t('settings.deploy.smtp_testing_toast'), { kind: "info", duration: 1200 });
                let ok = false;
                try {
                  const r = await window.api.admin.saveDeploymentConfig({});
                  void r;
                  const t = await window.api.raw?.POST("/api/v1/admin/smtp/test", {});
                  ok = !!(t && t.ok !== false);
                } catch (_) { ok = false; }
                setSmtpTesting(false);
                setSmtpLastTestAt(new Date().toISOString());
                setSmtpLastTestOk(ok);
                window.toast?.(ok ? t('settings.deploy.smtp_test_ok') : t('settings.deploy.smtp_test_fail'), { kind: ok ? "ok" : "danger", duration: 3000 });
              }}>
                {smtpTesting ? t('settings.deploy.smtp_testing') : t('settings.deploy.smtp_test_btn')}
              </CSButton>
              <span className="muted-2" style={{fontSize: 11}}>
                {smtpLastTestAt
                  ? (smtpLastTestOk ? t('settings.deploy.smtp_last_ok', { time: window.__fmt?.ago(smtpLastTestAt) || smtpLastTestAt }) : t('settings.deploy.smtp_last_fail', { time: window.__fmt?.ago(smtpLastTestAt) || smtpLastTestAt }))
                  : t('settings.deploy.smtp_not_tested')}
              </span>
            </CSSpaceBetween>
          </SetRow>
        </>
      )}

      <SetRow label={t('settings.deploy.captcha')} description={t('settings.deploy.captcha_desc')}>
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSButton variant={captchaProvider === "off" ? "primary" : "normal"} onClick={() => { setCaptchaProvider("off"); saveDeployConfig({ captcha_provider: "off" }); }}>{t('settings.deploy.captcha_off')}</CSButton>
          <CSButton variant={captchaProvider === "recaptcha" ? "primary" : "normal"} onClick={() => { setCaptchaProvider("recaptcha"); saveDeployConfig({ captcha_provider: "recaptcha" }); }}>Google reCAPTCHA</CSButton>
          <CSButton variant={captchaProvider === "turnstile" ? "primary" : "normal"} onClick={() => { setCaptchaProvider("turnstile"); saveDeployConfig({ captcha_provider: "turnstile" }); }}>Cloudflare Turnstile</CSButton>
          <CSButton variant={captchaProvider === "hcaptcha" ? "primary" : "normal"} onClick={() => { setCaptchaProvider("hcaptcha"); saveDeployConfig({ captcha_provider: "hcaptcha" }); }}>hCaptcha</CSButton>
        </CSSpaceBetween>
      </SetRow>
      {captchaProvider === "recaptcha" && (
        <>
          <SetRow label={t('settings.deploy.captcha_recaptcha_ver')} description={t('settings.deploy.captcha_recaptcha_ver_desc')}>
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton variant={recaptchaVer === "v3" ? "primary" : "normal"} onClick={() => { setRecaptchaVer("v3"); saveDeployConfig({ recaptcha_ver: "v3" }); }}>{t('settings.deploy.captcha_recaptcha_v3')}</CSButton>
              <CSButton variant={recaptchaVer === "v2c" ? "primary" : "normal"} onClick={() => { setRecaptchaVer("v2c"); saveDeployConfig({ recaptcha_ver: "v2c" }); }}>{t('settings.deploy.captcha_recaptcha_v2c')}</CSButton>
              <CSButton variant={recaptchaVer === "v2i" ? "primary" : "normal"} onClick={() => { setRecaptchaVer("v2i"); saveDeployConfig({ recaptcha_ver: "v2i" }); }}>{t('settings.deploy.captcha_recaptcha_v2i')}</CSButton>
            </CSSpaceBetween>
          </SetRow>
          <SetRow label="Site Key" description={t('settings.deploy.captcha_site_key_desc')}>
            <CSInput value={recaptchaSiteKey} placeholder="6L···Y9" onChange={({ detail }) => { setRecaptchaSiteKey(detail.value); saveDeployConfig({ recaptcha_site_key: detail.value }); }} />
          </SetRow>
          <SetRow label="Secret Key" description={t('settings.deploy.captcha_secret_key_desc')}>
            <CSInput type="password" value={recaptchaSecretKey} placeholder="6L···Z3" onChange={({ detail }) => { setRecaptchaSecretKey(detail.value); saveDeployConfig({ recaptcha_secret_key: detail.value }); }} />
          </SetRow>
          <SetRow label={t('settings.deploy.captcha_score')} description={t('settings.deploy.captcha_score_desc')}>
            <CSInput type="number" value={String(recaptchaScore)}
              onChange={({ detail }) => { setRecaptchaScore(Number(detail.value)); saveDeployConfig({ recaptcha_score: Number(detail.value) }); }} />
          </SetRow>
        </>
      )}
      {captchaProvider === "turnstile" && (
        <>
          <SetRow label="Site Key" description={t('settings.deploy.captcha_turnstile_site_desc')}>
            <CSInput value={turnstileSiteKey} placeholder="0x4A···AAAA" onChange={({ detail }) => { setTurnstileSiteKey(detail.value); saveDeployConfig({ turnstile_site_key: detail.value }); }} />
          </SetRow>
          <SetRow label="Secret Key" description={t('settings.deploy.captcha_turnstile_secret_desc')}>
            <CSInput type="password" value={turnstileSecretKey} placeholder="0x4A···AAAA" onChange={({ detail }) => { setTurnstileSecretKey(detail.value); saveDeployConfig({ turnstile_secret_key: detail.value }); }} />
          </SetRow>
          <SetRow label={t('settings.deploy.captcha_widget_mode')} description={t('settings.deploy.captcha_widget_desc')}>
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton variant={turnstileMode === "non_interactive" ? "primary" : "normal"} onClick={() => { setTurnstileMode("non_interactive"); saveDeployConfig({ turnstile_mode: "non_interactive" }); }}>{t('settings.deploy.captcha_non_interactive')}</CSButton>
              <CSButton variant={turnstileMode === "interactive" ? "primary" : "normal"} onClick={() => { setTurnstileMode("interactive"); saveDeployConfig({ turnstile_mode: "interactive" }); }}>{t('settings.deploy.captcha_interactive')}</CSButton>
              <CSButton variant={turnstileMode === "invisible" ? "primary" : "normal"} onClick={() => { setTurnstileMode("invisible"); saveDeployConfig({ turnstile_mode: "invisible" }); }}>{t('settings.deploy.captcha_invisible')}</CSButton>
            </CSSpaceBetween>
          </SetRow>
        </>
      )}
      {captchaProvider === "hcaptcha" && (
        <>
          <SetRow label="Site Key">
            <CSInput value={hcaptchaSiteKey} placeholder="xxxxxxxx-xxxx-xxxx" onChange={({ detail }) => { setHcaptchaSiteKey(detail.value); saveDeployConfig({ hcaptcha_site_key: detail.value }); }} />
          </SetRow>
          <SetRow label="Secret Key">
            <CSInput type="password" value={hcaptchaSecretKey} placeholder="0x···" onChange={({ detail }) => { setHcaptchaSecretKey(detail.value); saveDeployConfig({ hcaptcha_secret_key: detail.value }); }} />
          </SetRow>
        </>
      )}
      {captchaProvider !== "off" && (
        <SetRow label={t('settings.deploy.captcha_triggers')} description={t('settings.deploy.captcha_triggers_desc')}>
          <CSSpaceBetween direction="horizontal" size="xs">
            {[
              { key: "register",       label: t('settings.deploy.trigger_register') },
              { key: "password_reset", label: t('settings.deploy.trigger_password_reset') },
              { key: "login_retry",    label: t('settings.deploy.trigger_login_retry') },
              { key: "every_login",    label: t('settings.deploy.trigger_every_login') },
              { key: "api_key_create", label: t('settings.deploy.trigger_api_key_create') },
            ].map(({ key, label }) => {
              const active = captchaTriggers.includes(key);
              return (
                <CSButton key={key} variant={active ? "primary" : "normal"} onClick={() => {
                  const next = active
                    ? captchaTriggers.filter(t => t !== key)
                    : [...captchaTriggers, key];
                  setCaptchaTriggers(next);
                  saveDeployConfig({ captcha_triggers: next });
                }}>{label}</CSButton>
              );
            })}
          </CSSpaceBetween>
        </SetRow>
      )}
    </SetGroup>
  );
}

function DangerSection() {
  const { t } = useTranslation();
  const [confirm, setConfirm] = useStatePL(null);
  // task 49：原 confirm body 写死 "全部 12 个存档"。改成真实拉 /api/saves 计数。
  const { saves = [] } = usePlatformData();
  const nSaves = saves.length;
  // S3/S4: 文字二次确认 state
  const [confirmText, setConfirmText] = useStatePL("");
  // S5: 清空进度 state
  const [clearProgress, setClearProgress] = useStatePL(null); // {done, total} | null

  const openConfirm = (which) => { setConfirmText(""); setConfirm(which); };
  const closeConfirm = () => { setConfirm(null); setConfirmText(""); };

  return (
    <SetGroup title={t('settings.danger.title')}>
      <SetRow label={t('settings.danger.clear_saves')} description={t('settings.danger.clear_saves_desc')}>
        <CSButton variant="normal" onClick={() => openConfirm("clear")}>{t('settings.danger.clear_saves_btn')}</CSButton>
      </SetRow>
      <SetRow label={t('settings.danger.reset_platform')} description={t('settings.danger.reset_platform_desc')}>
        <CSSpaceBetween direction="horizontal" size="s">
          <CSButton variant="normal" disabled>{t('settings.danger.reset_cli_btn')}</CSButton>
          <span className="muted-2" style={{fontSize: 11}}>
            {t('settings.danger.reset_cli_hint')}<code style={{userSelect: "all"}}>python -m rpg.platform_app.migrate reset --confirm</code>
          </span>
        </CSSpaceBetween>
      </SetRow>

      {/* S3/S5: 清空存档 Modal — 文字确认 + 进度条 */}
      {confirm === "clear" && (
        <div className="pl-modal-backdrop" onClick={closeConfirm}>
          <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(460px, 100%)"}}>
            <header className="pl-modal-head">
              <div>
                <div className="pl-modal-eyebrow" style={{color: "var(--danger)"}}>{t('settings.danger.clear_modal_eyebrow')}</div>
                <h2 className="pl-modal-title">{t('settings.danger.clear_modal_title')}</h2>
              </div>
              <button className="iconbtn" onClick={closeConfirm} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
            </header>
            <div style={{fontSize: 13.5, lineHeight: 1.65, color: "var(--text-quiet)"}}>
              {t('settings.danger.clear_modal_desc', { count: nSaves })}
            </div>
            <div style={{marginTop: 14}}>
              <label style={{fontSize: 12.5, color: "var(--text-quiet)", display: "block", marginBottom: 6}}>
                {t('settings.danger.clear_confirm_label')} <strong style={{color: "var(--danger)"}}>{t('settings.danger.clear_confirm_word')}</strong> {t('settings.danger.clear_confirm_suffix')}
              </label>
              <input
                className="pl-input"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={t('settings.danger.clear_confirm_word')}
                autoFocus
                style={{width: "100%", boxSizing: "border-box"}}
              />
            </div>
            {clearProgress && (
              <div style={{marginTop: 10, fontSize: 12.5, color: "var(--text-quiet)"}}>
                {t('settings.danger.clear_progress', { done: clearProgress.done, total: clearProgress.total })}
                <div style={{height: 4, background: "var(--bg-deep)", borderRadius: 2, marginTop: 6}}>
                  <div style={{
                    height: "100%",
                    width: `${Math.round(clearProgress.done / clearProgress.total * 100)}%`,
                    background: "var(--danger)",
                    borderRadius: 2,
                    transition: "width 0.2s",
                  }} />
                </div>
              </div>
            )}
            <footer className="pl-modal-foot">
              <span></span>
              <div style={{display: "flex", gap: 8}}>
                <button className="btn ghost" onClick={closeConfirm}>{t('common.cancel')}</button>
                <button
                  className="btn danger"
                  disabled={confirmText !== t('settings.danger.clear_confirm_word') || !!clearProgress}
                  onClick={async () => {
                    if (nSaves === 0) { window.__apiToast?.(t('settings.danger.clear_empty'), { kind: "info", duration: 1600 }); closeConfirm(); return; }
                    setClearProgress({ done: 0, total: nSaves });
                    let done = 0, fail = 0;
                    for (const s of saves) {
                      try { await window.api.saves.remove(s.id); } catch (_) { fail++; }
                      done++;
                      setClearProgress({ done, total: nSaves });
                    }
                    setClearProgress(null);
                    closeConfirm();
                    window.__apiToast?.(fail ? t('settings.danger.clear_ok_fail', { count: done - fail, fail }) : t('settings.danger.clear_ok', { count: done - fail }), { kind: fail ? "warn" : "ok", duration: 3000 });
                    try { window.dispatchEvent(new CustomEvent("rpg-saves-updated")); } catch (_) {}
                  }}
                >
                  <Icon name="trash" size={12} /> {t('settings.danger.clear_saves_btn')}
                </button>
              </div>
            </footer>
          </div>
        </div>
      )}
    </SetGroup>
  );
}

// ── ESM export(W12 重构修复 Vite 迁移后的跨文件作用域断裂)──
// platform-app.jsx 用到 MODELS_DATA / PROVIDERS_CONFIG;原 babel-script 时代它们
// 是全局 const 自然可见,Vite ESM 下变成 module-local 必须显式 export 出来。
export {
  SettingsPage,
  MODELS_DATA,
  PROVIDERS_CONFIG,
  CAP_LABEL,
  ApiModelsList,
  AddModelModal,
  EditApiModal,
  ValidateModal,
  VisibilityModal,
  ProviderCard,
  ProviderConfigSection,
  ParamSlider,
  ModelNameCell,
  HealthDot,
  ModelsSection,
  ModuleModelsSection,
  ModelParamsSection,
  ExtractorSection,
  PrefSection,
  PermSection,
  ClarifySection,
  MemorySection,
  DangerSection,
  DeploySection,
  AuditLogView,
};

// 过渡期保留 window 注入,等所有 consumer 改完 import 后删除。
