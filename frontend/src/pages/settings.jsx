/* Settings page — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / SettingsToggle / ConfirmModal / useAutoSave / usePlatformData / fmtN。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';
import Modal from '../components/Modal.jsx';
import { ConfirmModal, SettingsToggle, useAutoSave, usePlatformData, useReactiveUser, publishUser, fmtN, ResizableSplit } from '../platform-app.jsx';
import AgentModelPicker from '../components/AgentModelPicker.jsx';
import GmStyleEditor from '../components/GmStyleEditor.jsx';
import { getCaps as _getCapsImported, normalizeProviderId, credentialToCatalogId, catalogToCredentialId } from '../components/catalog-helpers.js';
import { MODULES as AGENT_MODULES, MODULE_GROUPS, FEATURES as AGENT_FEATURES } from '../agent-modules.js';
import { readScopedPref, readNumberPref } from '../lib/prefs.js';
import { lsGetJSON, lsSetJSON } from '../lib/storage.js';
import { plNavigate } from '../router.js';
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
import CSTabs from '@cloudscape-design/components/tabs';
import CSBadge from '@cloudscape-design/components/badge';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
import CSModal from '@cloudscape-design/components/modal';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';

// Provider 别名表 + 归一化/方向转换全部上提到 components/catalog-helpers.js(语义统一 #16)。
// 本文件保留原名薄别名,内部调用点与 ESM export(ModelConfigInterceptModal 依赖 normalizeApiId)零变化:
//   normalizeApiId            = normalizeProviderId   (走全别名表)
//   credentialApiIdForCatalog = catalogToCredentialId (catalog→credential:vertex_ai→AgentPlatform)
//   catalogApiIdForCredential = credentialToCatalogId (credential→catalog:AgentPlatform→vertex_ai)
const normalizeApiId = normalizeProviderId;
const credentialApiIdForCatalog = catalogToCredentialId;
const catalogApiIdForCredential = credentialToCatalogId;

/* ── 设置页 Cloudscape 统一 primitives(取代 pl-set-group / pl-set-row) ──
   SetGroup = Container + Header(h2)  ·  SetRow = FormField(label 上 / 控件下)。
   各 section 用这两个套,保证全站基线对齐、间距一致。 */
function SetGroup({ title, description, actions, children }) {
  // 用 Header 原生 description 渲染 section 说明(可见副标题、原生预留间隔),
  // 不再塞进标题旁 ⓘ —— 短说明直接展示更易读,也让各 section 基线一致。
  return (
    <CSContainer header={<CSHeader variant="h2" actions={actions} description={description || undefined}>{title}</CSHeader>}>
      {/* React.Children.toArray 给多子元素派稳定 key,避免 SpaceBetween 的 key 警告 */}
      <CSSpaceBetween size="l">{React.Children.toArray(children)}</CSSpaceBetween>
    </CSContainer>
  );
}
function SetRow({ label, description, children }) {
  // 用 FormField 原生 description(label 下方可见副标题):短帮助文字直接显示而非藏进 ⓘ,
  // FormField 自带副标题预留间隔 → 行结构一致、并排字段组控件纵向对齐。
  return (
    <CSFormField label={label} description={description || undefined}>
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
    { id: "account",     label: t('settings.nav.account'),     icon: "user" },
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
      {section === "preferences" && [<PrefSection key="pref" />, <CSContainer key="gmstyle"><GmStyleEditor scope="user" /></CSContainer>, <BlackSwanSection key="bs" />, <ExtractorSection key="ext" />, <ClarifySection key="clar" />]}
      {section === "models" && <ModelsSection />}
      {section === "modelparams" && <ModelParamsSection />}
      {section === "modules" && <ModuleModelsSection />}
      {section === "memory" && <MemorySection />}
      {section === "permissions" && <PermSection />}
      {section === "deploy" && <DeploySection />}
      {section === "account" && <AccountSection />}
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
  const save = useAutoSave(t('settings.extractor.title'), "extractor");
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const profile = await window.api.account.profile();
        if (cancelled) return;
        const p = (profile && profile.preferences) || {};
        if (typeof p["extractor.enabled"] === "boolean") setEnabled(p["extractor.enabled"]);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <SetGroup title={t('settings.extractor.title')}>
      <SetRow label={t('settings.extractor.enable')} description={t('settings.extractor.enable_desc')}>
        <CSToggle checked={enabled} onChange={({ detail }) => { setEnabled(detail.checked); save("enabled", detail.checked); }}>
          {enabled ? t('settings.extractor.enable_on') : t('settings.extractor.enable_off')}
        </CSToggle>
      </SetRow>
      {/* 统一共享组件:与「按模块分配模型」的提取器、scripts 导入流、cards 的 card_import
          同一实现(Provider+Model + 未配 key 警告 + 写 extractor.* prefs)。 */}
      <SetRow label={t('settings.extractor.api')} description={t('settings.extractor.model_desc')}>
        <AgentModelPicker
          prefPrefix="extractor"
          preferProvider="deepseek"
          defaultModel={null}
          variant="bare"
          configHash="settings-models"
        />
      </SetRow>
    </SetGroup>
  );
}

/* BlackSwanSection — 黑天鹅子代理开关：暴露 user_preferences["black_swan.enabled"]。
   后端 _is_black_swan_enabled(api_user) 读此偏好；未设置时退回 env-var(RPG_ENABLE_BLACK_SWAN)。
   useAutoSave("黑天鹅", "black_swan") 让 save("enabled", v) 写到 black_swan.enabled，键对齐。 */
function BlackSwanSection() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useStatePL(false);
  const save = useAutoSave(t('settings.black_swan.title'), "black_swan");
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const profile = await window.api.account.profile();
        if (cancelled) return;
        const p = (profile && profile.preferences) || {};
        if (typeof p["black_swan.enabled"] === "boolean") setEnabled(p["black_swan.enabled"]);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);
  return (
    <SetGroup title={t('settings.black_swan.title')}>
      <SetRow label={t('settings.black_swan.enable')} description={t('settings.black_swan.enable_desc')}>
        <CSToggle checked={enabled} onChange={({ detail }) => { setEnabled(detail.checked); save("enabled", detail.checked); }}>
          {enabled ? t('settings.black_swan.enable_on') : t('settings.black_swan.enable_off')}
        </CSToggle>
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
  const isAdminUser = !!(window.RPG_AUTH && window.RPG_AUTH.authed && window.MOCK_PLATFORM?.user?.role === "admin");
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
  const autoSyncedRef = React.useRef(new Set());

  const mapModel = React.useCallback((m) => ({
    id: m.real_name || m.id,
    display: m.display_name || m.real_name || m.id,
    real_name: m.real_name || m.id,
    enabled: m.enabled !== false,
    // 可见性 = enabled(目录里没有独立的 hidden 字段,旧的 m.hidden!==true 恒为 true=可见性弹窗
    // 永远全选、不反映真实隐藏态)。统一用 enabled:与选择器过滤(m.enabled===false 隐藏)和
    // 可见性端点(写 enabled)一致,弹窗重开能正确显示用户隐藏了哪些。
    visible: m.enabled !== false,
    synced: m.synced === true,  // 同步来的(overlay)模型 → 可见性 toggle 走 per-user 端点
    capabilities: m.capabilities || {},
    health: m.health || "untested",
    health_error: m.health_error || "",
    health_latency_ms: m.health_latency_ms,
    health_checked_at: m.health_checked_at,
    health_status_detail: m.status_detail || m.health_status_detail || undefined,
  }), []);

  const loadConfiguredApis = useCallbackPL(async () => {
    const [data, creds] = await Promise.all([
      window.api.models.list(),
      window.api.credentials.list().catch(() => ({ items: [] })),
    ]);
    const credMap = {};
    for (const c of (creds?.items || creds?.credentials || [])) {
      const cid = normalizeApiId(c.api_id || c.id);
      credMap[cid] = {
        has_key: !!c.has_credential || !!c.has_key || !!c.key_hint,
        key_hint: c.key_hint || "",
        enabled: c.enabled !== false,
        base_url_override: c.base_url_override || "",
        proxy_url: c.proxy_url || "",
      };
    }
    const list = data?.models?.apis || data?.apis || [];
    const rows = Array.isArray(list) ? list.map(api => {
      const catalogId = catalogApiIdForCredential(api.api_id || api.id);
      const credentialId = credentialApiIdForCatalog(catalogId);
      const cred = credMap[credentialId] || credMap[normalizeApiId(catalogId)] || {};
      return {
        id: catalogId,
        credential_id: credentialId,
        name: api.display_name || api.name || catalogId,
        // 用户自己的 base_url_override(指向中转站)优先:它来自 list_credentials,不被
        // _redact_catalog 抹掉;而 api.base_url 对非 admin 已被后端 redact 成空。用 override
        // 兜底让 ① 详情/编辑弹窗显示真实中转站地址 ② 重新保存 key 时不会因表单空值把 override
        // 清掉(与生成/同步实际所用一致)③ 同步模型时 body 也带上正确地址。
        base_url: cred.base_url_override || api.base_url || "",
        key_set: !!cred.has_key,
        key_hint: cred.key_hint || t('settings.models.key_set_hint'),
        status: cred.enabled === false ? "disabled" : "configured",
        connectivity: { status: "untested" },
        enabled: cred.enabled !== false,
        proxy_url: cred.proxy_url || "",
        proxy: cred.proxy_url ? "http_proxy" : "direct",
        models: (api.models || api.entries || []).map(mapModel),
      };
    }).filter(api => api.key_set) : [];
    // 中转站: 把不在全局 catalog 里的用户自定义凭证(带 base_url)合成为 provider 行,
    // 否则保存后在列表里看不到、无法选模型。models=[] 由用户点同步从中转站拉取。
    const catalogIds = new Set((Array.isArray(list) ? list : []).map(a => normalizeApiId(catalogApiIdForCredential(a.api_id || a.id))));
    const customRows = Object.entries(credMap)
      .filter(([cid, c]) => c.has_key && c.base_url_override && !catalogIds.has(normalizeApiId(cid)))
      .map(([cid, c]) => ({
        id: cid, credential_id: cid, name: cid,
        base_url: c.base_url_override, key_set: true, key_hint: c.key_hint || '',
        status: c.enabled === false ? "disabled" : "configured",
        connectivity: { status: "untested" }, enabled: c.enabled !== false,
        proxy_url: c.proxy_url || "",
        proxy: c.proxy_url ? "http_proxy" : "direct", models: [], _custom: true,
      }));
    const allRows = [...rows, ...customRows];
    setApis(allRows);
    return allRows;
  }, [mapModel, t]);

  const syncRemoteModels = useCallbackPL(async (api, opts = {}) => {
    if (!api) return null;
    const apiId = catalogApiIdForCredential(api.id);
    setApis(arr => arr.map(a => a.id === apiId ? {
      ...a,
      connectivity: { ...(a.connectivity || {}), status: "checking", error: "" },
    } : a));
    const started = performance.now();
    try {
      const r = await window.api.models.syncRemote({ api_id: apiId, base_url: api.base_url || "" });
      if (!r?.ok) throw new Error(r?.error || "remote model sync failed");
      const elapsed = Math.max(1, Math.round(performance.now() - started));
      const models = (r.models || []).map(mapModel);
      setApis(arr => arr.map(a => a.id === apiId ? {
        ...a,
        models,
        status: "configured",
        connectivity: {
          status: "ok",
          latency_ms: elapsed,
          checked_at: Date.now(),
          remote_total: r.remote_total ?? models.length,
          synced: r.synced ?? models.length,
          error: "",
        },
      } : a));
      if (!opts.silent) {
        window.__apiToast?.(t('settings.models.sync_ok', { count: models.length }), { kind: "ok", duration: 2200 });
      }
      return r;
    } catch (e) {
      setApis(arr => arr.map(a => a.id === apiId ? {
        ...a,
        connectivity: {
          status: "err",
          checked_at: Date.now(),
          error: e?.message || "sync failed",
        },
      } : a));
      if (!opts.silent) {
        window.__apiToast?.(t('settings.models.sync_fail'), { kind: "danger", detail: e?.message });
      }
      return null;
    }
  }, [mapModel, t]);

  useEffectPL(() => {
    if (useMock) return;
    (async () => {
      try { await loadConfiguredApis(); }
      catch (_) {}
      finally { setApisLoading(false); }
    })();
  }, [useMock, loadConfiguredApis]);

  const toggleApi = async (id) => {
    const api = apis.find(a => a.id === id);
    const newEnabled = !api?.enabled;
    setApis(arr => arr.map(a => a.id === id ? { ...a, enabled: newEnabled } : a));
    try {
      await window.api.models.upsertApi({ api_id: id, enabled: newEnabled });
    } catch (_) {}
  };
  const toggleModel = async (apiId, mId) => {
    const api = apis.find(a => a.id === apiId);
    const m = api?.models.find(m => m.id === mId);
    const wasEnabled = m?.enabled ?? true;
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => m.id === mId ? { ...m, enabled: !wasEnabled } : m) }
      : a));
    try {
      await window.api.models.upsertModel({ api_id: apiId, real_name: mId, enabled: !wasEnabled });
    } catch (_) {}
  };
  const renameModel = async (apiId, mId, display) => {
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => m.id === mId ? { ...m, display } : m) }
      : a));
    try { await window.api.models.upsertModel({ api_id: apiId, real_name: mId, display_name: display }); } catch (_) {}
  };
  const setModelVisibility = async (apiId, ids) => {
    const api = apis.find(a => a.id === apiId);
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => ({ ...m, visible: ids.includes(m.id) })) }
      : a));
    if (api) {
      await Promise.all(api.models.map(m => {
        const body = { api_id: apiId, model: m.id, visible: ids.includes(m.id) };
        // 同步来的(overlay)模型走 per-user 端点(任何用户可隐藏自己的、re-sync 不重置);
        // 全局策展模型走全局端点(admin-only)——非 admin 跳过,避免注定失败的 403。
        if (m.synced) return window.api.models.meVisibility(body).catch(() => {});
        if (!isAdminUser) return Promise.resolve();  // 非 admin 无权改全局可见性
        return window.api.models.visibility(body).catch(() => {});
      }));
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

  useEffectPL(() => {
    if (useMock || apisLoading) return;
    configuredApis.forEach(api => {
      if (autoSyncedRef.current.has(api.id)) return;
      autoSyncedRef.current.add(api.id);
      syncRemoteModels(api, { silent: true });
    });
  }, [useMock, apisLoading, configuredApis.map(a => a.id).join("|"), syncRemoteModels]);

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
          // 删除凭证走真正的 delete 端点(无 Base URL 校验);旧实现用 set({api_key:''})
          // 会触发「自定义供应商必须填写 Base URL」的设置态校验,导致自定义中转站删不掉。
          await window.api.credentials.remove({ api_id: credentialApiIdForCatalog(selectedApi.id) });
          window.__apiToast?.(t('settings.models.delete_key_ok'), { kind: 'ok' });
          setSelectedApiId(null);
          setApis(arr => arr.map(a => a.id === selectedApi.id ? { ...a, key_set: false, key_hint: '—' } : a));
          // 删除凭证后从 autoSyncedRef 移除,允许下次重新配置 key 后重新 auto-sync。
          autoSyncedRef.current.delete(selectedApi.id);
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
              { id: 'connectivity', header: t('settings.models.col_connectivity'), cell: (a) => {
                const c = a.connectivity || {};
                const status = a.enabled === false ? "disabled" : (c.status || "untested");
                const label = status === "checking"
                  ? t('settings.models.connectivity_checking')
                  : status === "ok"
                    ? t('settings.models.connectivity_ok')
                    : status === "err"
                      ? t('settings.models.connectivity_err')
                      : status === "disabled"
                        ? t('settings.models.status_disabled')
                        : t('settings.models.connectivity_untested');
                const type = status === "ok" ? "success" : status === "err" ? "error" : status === "checking" ? "in-progress" : "stopped";
                return (
                  <button
                    type="button"
                    className="linklike"
                    title={t('settings.models.connectivity_refresh_tip')}
                    onClick={(e) => { e.stopPropagation(); syncRemoteModels(a); }}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: 0, border: 0, background: "transparent", cursor: "pointer" }}
                  >
                    <CSStatusIndicator type={type}>{label}</CSStatusIndicator>
                    {c.latency_ms ? <span className="mono muted-2">{c.latency_ms}ms</span> : null}
                  </button>
                );
              } },
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
        isAdminUser={isAdminUser}
        onClose={() => { setEditingApi(null); setAddingApi(false); }}
        onConfirm={async (payload) => {
          const credentialId = normalizeApiId(payload.id);
          const catalogId = catalogApiIdForCredential(credentialId);
          const cfg = PROVIDERS_CONFIG.find((p) => catalogApiIdForCredential(p.id) === catalogId || normalizeApiId(p.id) === credentialId);
          const kind = catalogId === "vertex_ai"
            ? "vertex_ai"
            : catalogId === "anthropic"
              ? "anthropic"
              : "openai_compat";
          // 中转站: 普通用户也可添加自定义 OpenAI 兼容端点 — 后端 me.py 放行未知
          // provider(必带 base_url) + set_credential 的 _validate_base_url 做 SSRF 防护。
          try {
            // task: BYOK fix — 普通用户填 API key 不应被 admin 闸住。
            // /api/models/api(upsertApi)写全局 catalog,只有 admin 能调。
            // 普通用户场景:provider 是项目内置的,catalog 已有 → 直接走 credentials.set。
            // 若管理员新加 provider(addingApi=true)或者改 base_url/proxy 这类全局字段,
            // 才尝试 upsertApi。普通用户不保存未知 api_id,避免后续同步模型报 api_id 不存在。
            const existing = apis.find(a => a.id === catalogId);
            // proxy(连接方式/代理 URL)现在是 per-user 凭据字段,走 credentials.set,不再塞全局 catalog。
            const needsCatalogWrite = isAdminUser && (
              addingApi
              || !existing
              || (payload.base_url && payload.base_url !== existing.base_url)
            );
            if (needsCatalogWrite) {
              try {
                await window.api.models.upsertApi({
                  api_id: catalogId,
                  display_name: payload.name || cfg?.name || catalogId,
                  base_url: payload.base_url,
                  kind,
                });
              } catch (e) {
                if (e?.status === 403) {
                  // 普通用户改全局 catalog 被拒,提示但不阻断 key 保存
                  window.__apiToast?.(t('settings.more.edit_api.admin_base_url_warn'), { kind: "warn", duration: 3500 });
                } else {
                  throw e;
                }
              }
            }
            if (payload.api_key && payload.api_key.trim()) {
              try {
                await window.api.credentials.set({
                  api_id: credentialId, api_key: payload.api_key.trim(),
                  base_url_override: payload.base_url || '',
                  proxy: payload.proxy === 'http_proxy' ? (payload.proxy_url || '').trim() : '',
                });
              } catch (e) {
                window.__apiToast?.(t('settings.edit_api.key_save_fail'), { kind: "warn", detail: e?.message, duration: 4000 });
                throw e;
              }
            }
            window.__apiToast?.(addingApi ? t('settings.edit_api.add_ok') : t('settings.edit_api.save_ok'), { kind: "ok" });
            const rows = await loadConfiguredApis();
            const row = rows.find(a => a.id === catalogId) || {
              id: catalogId,
              name: payload.name || cfg?.name || catalogId,
              base_url: payload.base_url,
              key_set: true,
              enabled: true,
              models: [],
            };
            setSelectedApiId(catalogId);
            await syncRemoteModels(row, { silent: false });
          } catch (e) {
            window.__apiToast?.(t('settings.edit_api.save_fail'), { kind: "danger", detail: e?.message });
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
          <span style={{ color: 'var(--muted)' }}>{t('settings.models.base_url_label')}: <span className="mono">{api.base_url || '—'}</span></span>
          <span style={{ color: 'var(--muted)' }}>{t('settings.models.key_label')}: <span className="mono">•••• {api.key_hint || t('settings.models.key_set_hint')}</span></span>
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
                <CSBox fontSize="body-s" color="text-body-secondary">{t('settings.models.usage_detail')} <a href="/usage" onClick={(e) => { e.preventDefault(); plNavigate('usage'); }}>{t('settings.models.usage_page')}</a>。</CSBox>
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
    <Modal
      open
      eyebrow={t('settings.add_model.eyebrow', { api: api.name })}
      title={t('settings.add_model.title')}
      width={560}
      onClose={onClose}
      footer={<>
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
      </>}
    >
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
    </Modal>
  );
}

function EditApiModal({ open, api, isNew, isAdminUser = false, onClose, onConfirm }) {
  const { t } = useTranslation();
  // 新增时供应商走下拉(从 PROVIDERS_CONFIG 选,自动带出 base_url);选「自定义」可手填。
  // 编辑时供应商固定,只改 base_url / key。key 写入后不回显。
  const CUSTOM = '__custom__';
  const [provider, setProvider] = useStatePL('');   // 选中的 provider id(新增用)
  const [form, setForm] = useStatePL({ id: "", name: "", base_url: "", api_key: "", proxy: "direct", proxy_url: "" });
  React.useEffect(() => {
    if (!open) return;
    if (isNew) { setProvider(''); setForm({ id: "", name: "", base_url: "", api_key: "", proxy: "direct", proxy_url: "" }); }
    else if (api) { setProvider(api.id); setForm({ id: api.id, name: api.name, base_url: api.base_url, api_key: "", proxy: api.proxy || "direct", proxy_url: api.proxy_url || "" }); }
  }, [open, api, isNew]);
  if (!open) return null;

  const provOptions = [
    ...PROVIDERS_CONFIG.filter((p) => !p.hidden_in_edit_modal).map((p) => ({ value: p.id, label: p.name, description: p.defaultBase || undefined })),
    { value: CUSTOM, label: t('settings.edit_api.custom_provider'), description: t('settings.edit_api.custom_provider_desc') },
  ];
  const onPickProvider = (val) => {
    setProvider(val);
    if (val === CUSTOM) { setForm((f) => ({ ...f, id: "", name: "", base_url: "" })); return; }
    const p = PROVIDERS_CONFIG.find((x) => x.id === val);
    if (p) setForm((f) => ({ ...f, id: p.id, name: p.name, base_url: p.defaultBase || "" }));
  };
  const isCustom = provider === CUSTOM;
  // Agent Platform (vertex_ai / AgentPlatform) 走 SA JSON — 不需要 base_url，api_key 是 JSON 字符串
  const selectedProviderCfg = PROVIDERS_CONFIG.find((x) => x.id === provider);
  const isAgentPlatform = selectedProviderCfg?.special === 'agent_platform' || api?.kind === 'vertex_ai';
  // SA JSON 校验: 必须能 parse 且含三个必要字段
  const _saJsonValid = (() => {
    if (!isAgentPlatform || !form.api_key.trim()) return false;
    try {
      const sa = JSON.parse(form.api_key.trim());
      return !!(sa.client_email && sa.private_key && sa.project_id);
    } catch { return false; }
  })();
  const canSubmit = isAgentPlatform
    ? (!!form.id && !!form.name && (isNew ? _saJsonValid : true))
    : (!!form.id && !!form.name && !!form.base_url && (isNew ? !!form.api_key.trim() : true));

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
            {/* Agent Platform (Vertex SA JSON) 模式: 隐藏 base_url, api_key 改为 SA JSON textarea */}
            {isAgentPlatform ? (
              <CSFormField
                label="Service Account JSON"
                description={api?.key_set ? t('settings.more.edit_api.sa_desc_set', { hint: api.key_hint || t('settings.more.edit_api.sa_encrypted') }) : t('settings.more.edit_api.sa_desc_new')}
              >
                <textarea
                  rows={6}
                  value={form.api_key}
                  onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                  placeholder={'{"type": "service_account", "project_id": "...", "client_email": "...", "private_key": "<PRIVATE_KEY_PEM_WITH_NEWLINES>"}'}
                  style={{ width: '100%', fontFamily: 'monospace', fontSize: '12px', resize: 'vertical', padding: '8px', boxSizing: 'border-box' }}
                  autoComplete="off"
                  spellCheck={false}
                />
                {form.api_key.trim() && !_saJsonValid && (
                  <div style={{ color: 'var(--color-text-status-error, #d91515)', fontSize: '12px', marginTop: '4px' }}>
                    {t('settings.more.edit_api.sa_json_invalid')}
                  </div>
                )}
                {_saJsonValid && (
                  <div style={{ color: 'var(--color-text-status-success, #1a7e3c)', fontSize: '12px', marginTop: '4px' }}>
                    {t('settings.more.edit_api.sa_json_valid', { project: (() => { try { return JSON.parse(form.api_key).project_id; } catch { return ''; } })() })}
                  </div>
                )}
              </CSFormField>
            ) : (
              <>
                <CSFormField label={t('settings.edit_api.base_url')}>
                  <CSInput value={form.base_url} onChange={({ detail }) => setForm((f) => ({ ...f, base_url: detail.value }))} placeholder="https://your-relay.example.com/v1" />
                </CSFormField>
                <CSFormField label={t('settings.edit_api.api_key')} description={api?.key_set ? t('settings.edit_api.api_key_desc_set', { hint: api.key_hint || t('settings.models.key_set_hint') }) : t('settings.edit_api.api_key_desc_new')}>
                  <CSInput type="password" value={form.api_key}
                    onChange={({ detail }) => setForm((f) => ({ ...f, api_key: detail.value }))}
                    placeholder={api?.key_set ? t('settings.edit_api.api_key_placeholder_keep') : "sk-…"} autoComplete="new-password" />
                </CSFormField>
              </>
            )}
            <CSFormField label={t('settings.edit_api.connection')}
              description={form.proxy === 'http_proxy' ? t('settings.edit_api.proxy_hint') : undefined}>
              <CSSelect
                selectedOption={{ value: form.proxy, label: form.proxy }}
                options={[{ value: 'direct', label: t('settings.edit_api.direct') }, { value: 'http_proxy', label: t('settings.edit_api.http_proxy') }, { value: 'lan', label: t('settings.edit_api.lan') }]}
                onChange={({ detail }) => setForm((f) => ({ ...f, proxy: detail.selectedOption.value }))}
              />
            </CSFormField>
            {form.proxy === 'http_proxy' && (
              <CSFormField label={t('settings.edit_api.proxy_url')}>
                <CSInput value={form.proxy_url}
                  onChange={({ detail }) => setForm((f) => ({ ...f, proxy_url: detail.value }))}
                  placeholder="http://127.0.0.1:7890" />
              </CSFormField>
            )}
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
    <Modal
      open
      eyebrow={t('settings.visibility.eyebrow', { name: api.name })}
      title={t('settings.visibility.title', { selected: selected.size, total: api.models.length })}
      width={640}
      panelStyle={{maxHeight: "88vh"}}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> {t('settings.visibility.info')}
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" onClick={() => onConfirm([...selected])}>
            <Icon name="check" size={12} /> {t('settings.visibility.save')}
          </button>
        </div>
      </>}
    >
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
                        <span key={c} className="pl-cap-tag">{t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}</span>
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
    </Modal>
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
          display_name: m.display || m.name || m.real_name,
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
    <Modal
      open
      eyebrow={t('settings.validate.eyebrow', { name: api.name })}
      title={phase === "sniffing" ? t('settings.validate.sniffing') : t('settings.validate.done')}
      width={560}
      onClose={onClose}
      footer={<>
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
      </>}
    >
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
    </Modal>
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
              data-tip={t('settings.more.model_list.cap_filter_tip', { cap: t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c }) })}
            >
              {t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}
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
                    <span key={c} className="pl-cap-tag" data-tip={t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}>{t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}</span>
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
                    ? <span data-tip={t('settings.more.model_list.price_tip', { input: m.input_cost_per_million, output: m.output_cost_per_million ?? '?' })}>
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
                    <div className="muted-2" style={{fontSize: 10}} data-tip={t('settings.more.model_list.max_output_tip', { n: fmtCtx(m.max_output_tokens) })}>
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
                      <span className="pl-cap-tag" data-tip={`${t('settings.more.source_label_tip')}: ${sourceLabel(m.source, t)}`} style={{fontSize: 10}}>
                        {sourceLabel(m.source, t)}
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
function sourceLabel(source, _t) {
  const MAP = {
    LiveApi:        "Live API",
    StaticCatalog:  "Static",
    UserOverride:   _t ? _t('settings.more.source_user_override') : "User Override",
    OpenRouterProxy:"OpenRouter Proxy",
  };
  return MAP[source] || source || "—";
}

/** @param {number|null|undefined} n context_window 格式化 */
// K/M 缩写统一到 window.__fmt.compact(data-loader.js;语义统一 #30),保留本地别名免改调用点。
function fmtCtx(n) {
  if (window.__fmt && window.__fmt.compact) return window.__fmt.compact(n);
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
      { id: "gpt-5.5", real_name: "gpt-5.5", display: "GPT-5.5 · 标准", capabilities: ["text", "vision", "tool-use", "rpg"], enabled: true, price: "$2.50 / $10.00", context: "400K", health: "ok", visible: true },
      { id: "gpt-5.5-instant", real_name: "gpt-5.5-instant", display: "GPT-5.5 Instant · 低延迟", capabilities: ["fast", "vision"], enabled: true, price: "$1.25 / $5.00", context: "400K", health: "ok", visible: true },
      { id: "gpt-5.5-pro", real_name: "gpt-5.5-pro", display: "GPT-5.5 Pro", capabilities: ["text", "vision", "tool-use"], enabled: false, price: "$5.00 / $20.00", context: "400K", health: "ok", visible: true },
      { id: "gpt-5", real_name: "gpt-5", display: "GPT-5 · 上一代", capabilities: ["text", "vision"], enabled: false, price: "$2.00 / $8.00", context: "400K", health: "ok", visible: true },
    ]
  },
  {
    id: "anthropic", name: "Anthropic", base_url: "https://api.anthropic.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-***", proxy: "直连",
    models: [
      { id: "claude-opus-4-7", real_name: "claude-opus-4-7", display: "Claude Opus 4.7 · 长文", capabilities: ["long", "tool-use", "rpg"], enabled: true, price: "$15 / $75", context: "200K", health: "ok", visible: true },
      { id: "claude-sonnet-4-6", real_name: "claude-sonnet-4-6", display: "Claude Sonnet 4.6", capabilities: ["text", "fast"], enabled: true, price: "$3 / $15", context: "200K", health: "ok", visible: true },
      { id: "claude-haiku-4-5", real_name: "claude-haiku-4-5", display: "Claude Haiku 4.5", capabilities: ["fast"], enabled: false, price: "$1.00 / $5", context: "200K", health: "ok", visible: true },
    ]
  },
  {
    id: "google", name: "Google", base_url: "https://generativelanguage.googleapis.com/v1beta",
    enabled: false, status: "未连接", key_set: false, proxy: "需配置 API key",
    models: [
      { id: "gemini-3.5-flash", real_name: "gemini-3.5-flash", display: "Gemini 3.5 Flash · 当前默认", capabilities: ["fast", "vision", "tool-use"], enabled: false, price: "$1.50 / $9.00", context: "1M", health: "ok", visible: true },
      { id: "gemini-3.1-pro", real_name: "gemini-3.1-pro", display: "Gemini 3.1 Pro", capabilities: ["long", "vision", "tool-use"], enabled: false, price: "$2.00 / $12.00", context: "1M", health: "ok", visible: true },
    ]
  },
  {
    id: "qwen", name: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…c024", proxy: "直连",
    models: [
      { id: "qwen3.7-max", real_name: "qwen3.7-max", display: "Qwen 3.7-Max · 旗舰", capabilities: ["cn", "rpg", "text", "reasoning"], enabled: true, price: "$2.50 / $7.50", context: "1M", health: "ok", visible: true },
      { id: "qwen3.6-flash", real_name: "qwen3.6-flash", display: "Qwen 3.6 Flash", capabilities: ["cn", "fast"], enabled: true, price: "$0.19 / $1.13", context: "131K", health: "ok", visible: true },
      { id: "qwen-turbo", real_name: "qwen-turbo", display: "Qwen Turbo", capabilities: ["cn", "fast"], enabled: false, price: "¥0.04 / ¥0.08", context: "1M", health: "ok", visible: true },
    ]
  },
  {
    id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…a8d2", proxy: "直连",
    models: [
      { id: "deepseek-v4-pro", real_name: "deepseek-ai/DeepSeek-V4-Pro", display: "DeepSeek V4-Pro · 旗舰", capabilities: ["reasoning", "cn", "tool-use"], enabled: true, price: "$1.74 / $3.48", context: "1M", health: "ok", visible: true },
      { id: "deepseek-v4-flash", real_name: "deepseek-ai/DeepSeek-V4-Flash", display: "DeepSeek V4-Flash · 快速", capabilities: ["cn", "fast"], enabled: true, price: "$0.30 / $1.20", context: "1M", health: "ok", visible: true },
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
        ["anthropic/claude-opus-4-7", "Claude Opus 4.7", ["long", "tool-use"], "$15.75 / $78.75", "200K", true],
        ["anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6", ["text", "fast"], "$3.15 / $15.75", "200K", false],
        ["anthropic/claude-haiku-4-5", "Claude Haiku 4.5", ["fast"], "$1.05 / $5.25", "200K", false],
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
    id: "openai",       name: "OpenAI",         kind: "openai_compat",
    defaultBase: "https://api.openai.com/v1",
    keyEnv: "OPENAI_API_KEY",
  },
  {
    id: "openrouter",   name: "OpenRouter",     kind: "openai_compat",
    defaultBase: "https://openrouter.ai/api/v1",
    keyEnv: "OPENROUTER_API_KEY",
    special: "openrouter",
    noteKey: "settings.more.providers.note_openrouter",
  },
  {
    id: "deepseek",     name: "DeepSeek",       kind: "openai_compat",
    defaultBase: "https://api.deepseek.com/v1",
    keyEnv: "DEEPSEEK_API_KEY",
  },
  {
    id: "xai",          name: "xAI (Grok)",     kind: "openai_compat",
    defaultBase: "https://api.x.ai/v1",
    keyEnv: "XAI_API_KEY",
  },
  {
    id: "xiaomi_mimo",   name: "MiMo (Xiaomi)",  kind: "openai_compat",
    defaultBase: "https://chat.d.xiaomi.net/ai/api/v1",
    keyEnv: "XIAOMI_MIMO_API_KEY",
  },
  {
    id: "hunyuan", name: "Hunyuan (Tencent)", kind: "openai_compat",
    defaultBase: "https://api.hunyuan.cloud.tencent.com/v1",
    keyEnv: "TENCENT_HUNYUAN_API_KEY",
  },
  {
    id: "anthropic",    name: "Anthropic",      kind: "native",
    defaultBase: "https://api.anthropic.com",
    keyEnv: "ANTHROPIC_API_KEY",
  },
  {
    id: "google_ai_studio", name: "Google AI Studio", kind: "native",
    // Gemini 的 OpenAI 兼容端点在 /v1beta/openai;少了这段路径会 404「找不到」。
    defaultBase: "https://generativelanguage.googleapis.com/v1beta/openai",
    keyEnv: "GOOGLE_API_KEY",
    // 下架:Google 从 2026-07-04 起封禁本服务器机房 IP(User location is not supported),
    // AI Studio 连不通。从默认候选(添加下拉 + provider 卡片)移除;仅存量已配置用户仍见其卡片
    // (带下架提示,引导改用 Agent Platform)。Gemini 统一走 Vertex。
    hidden_in_edit_modal: true,
    deprecated: true,
  },
  {
    id: "AgentPlatform", name: "Agent Platform (Service Account)", kind: "native",
    defaultBase: "",
    keyEnv: "",
    special: "agent_platform",
    // 用户级 SA 已真接通 (vertex.py / embedding.py / model_probe 全部走用户 SA)
    // EditApiModal 检测 special === 'agent_platform' 时自动隐藏 base_url + api_key 改 SA JSON textarea
    noteKey: "settings.more.providers.note_agent_platform",
  },
  {
    id: "dashscope",  name: "DashScope (Qwen)", kind: "openai_compat",
    defaultBase: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    keyEnv: "DASHSCOPE_API_KEY",
    special: "alibaba_qwen",
    noteKey: "settings.more.providers.note_dashscope",
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
  const isAdminUser = !!(window.RPG_AUTH && window.RPG_AUTH.authed && window.MOCK_PLATFORM?.user?.role === "admin");
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
          const pid = normalizeApiId(c.api_id || c.id);
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
        if (isAdminUser) {
          const cfg = PROVIDERS_CONFIG.find((p) => p.id === providerId);
          const kind = providerId === "AgentPlatform" ? "vertex_ai" : providerId === "anthropic" ? "anthropic" : "openai_compat";
          await window.api.models.upsertApi({ api_id: catalogApiIdForCredential(providerId), base_url: baseUrl, kind, display_name: cfg?.name || providerId });
        } else {
          window.__apiToast?.(t('settings_extra.admin_base_url_only'), { kind: "warn", duration: 3000 });
        }
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
        {PROVIDERS_CONFIG.filter(p => !p.deprecated || creds[p.id]?.has_key).map(p => {
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
              onAlibabaMode={(v) => { setAlibabaMode(v); window.api.models.upsertApi({ api_id: "dashscope", kind: "openai_compat", base_url: v === "openai_compat" ? "https://dashscope.aliyuncs.com/compatible-mode/v1" : "https://dashscope.aliyuncs.com/api/v1" }).catch(() => {}); }}
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
          {p.unavailable && (
            <CSAlert type="warning" header={t('settings.providers.sa.unavailable_title')}>
              {t('settings.providers.sa.unavailable_desc')}
            </CSAlert>
          )}
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <div>
              <CSBox fontWeight="bold">{p.name}</CSBox>
              <CSBox color="text-body-secondary" fontSize="body-s">{p.noteKey ? t(p.noteKey) : p.note}</CSBox>
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
              <CSBox color="text-body-secondary" fontSize="body-s">{p.noteKey ? t(p.noteKey) : p.note}</CSBox>
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
            <CSFormField label={t('settings.edit_api.api_key')} stretch>
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
        {p.deprecated && (
          <CSAlert key="deprecated" type="warning" header={t('settings.providers.deprecated_geo_title')}>
            {t('settings.providers.deprecated_geo_desc')}
          </CSAlert>
        )}
        <CSSpaceBetween key="hdr" direction="horizontal" size="xs" alignItems="center">
          <div>
            <CSBox fontWeight="bold">{p.name}</CSBox>
            {(p.note || p.noteKey) && <CSBox color="text-body-secondary" fontSize="body-s">{p.noteKey ? t(p.noteKey) : p.note}</CSBox>}
          </div>
          {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
        </CSSpaceBetween>
        <CSSpaceBetween key="form" direction="horizontal" size="xs" alignItems="flex-end">
          <CSFormField label={t('settings.edit_api.api_key')} stretch>
            <CSInput
              type="password"
              value={keyVal}
              onChange={({ detail }) => setKeyVal(detail.value)}
              placeholder={cred.has_key ? t('settings.providers.keep_key') : (p.keyEnv ? p.keyEnv : "sk-…")}
              autoComplete="new-password"
            />
          </CSFormField>
          <CSFormField
            label={p.special === "openrouter" ? t('settings.providers.base_url_relay') : t('settings.providers.base_url')}
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

const MODEL_PARAM_DEFAULTS = {
  temperature: 0.78,
  top_p: 0.92,
  top_k: 40,
  repetition_penalty: 1.15,
  frequency_penalty: 0.20,
  presence_penalty: 0.10,
  max_tokens: 4096,
  context_size: 16384,
  seed: -1,
  mirostat_mode: "off",
  mirostat_tau: 5.0,
  mirostat_eta: 0.10,
  stop: "",
};

const MODEL_PARAM_PRESET_VALUES = {
  conservative: { temperature: 0.4, top_p: 0.85, repetition_penalty: 1.05, frequency_penalty: 0.1, presence_penalty: 0.0 },
  balanced: { temperature: 0.78, top_p: 0.92, repetition_penalty: 1.15, frequency_penalty: 0.2, presence_penalty: 0.1 },
  creative: { temperature: 1.0, top_p: 0.98, repetition_penalty: 1.2, frequency_penalty: 0.3, presence_penalty: 0.2 },
  deterministic: { temperature: 0.1, top_p: 0.5, repetition_penalty: 1.0, frequency_penalty: 0.0, presence_penalty: 0.0 },
};

// readScopedPref / readNumberPref 上提到 lib/prefs.js(语义统一 #24);见顶部 import。

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
  // 请求超时(秒):settings.request_timeout。空=自动(本地/桌面 1800s 给慢的本地大模型,服务器 300s)。
  const [reqTimeout, setReqTimeout] = useStatePL("");
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
  const [params, setParams] = useStatePL(MODEL_PARAM_DEFAULTS);
  const [advanced, setAdvanced] = useStatePL(false);
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const prefs = (r && r.preferences) || {};
        const nextParams = { ...MODEL_PARAM_DEFAULTS };
        for (const key of Object.keys(MODEL_PARAM_DEFAULTS)) {
          if (typeof MODEL_PARAM_DEFAULTS[key] === "number") {
            nextParams[key] = readNumberPref(prefs, key, MODEL_PARAM_DEFAULTS[key]);
          } else {
            nextParams[key] = String(readScopedPref(prefs, key, MODEL_PARAM_DEFAULTS[key]) ?? "");
          }
        }
        const nextPreset = String(readScopedPref(prefs, "preset", "balanced") || "balanced");
        if (PRESETS.some((p) => p.key === nextPreset)) setPreset(nextPreset);
        setParams(nextParams);
        setAdvanced(nextParams.mirostat_mode !== "off");

        const legacyNsfw = readScopedPref(prefs, "nsfw", null) || {};
        const nsfwMode = String(readScopedPref(prefs, "nsfw_mode", legacyNsfw.mode || "soft") || "soft");
        const nsfwIntensity = Number(readScopedPref(prefs, "nsfw_intensity", legacyNsfw.intensity ?? 0.5));
        setNsfw({
          mode: ["block", "soft", "open", "explicit"].includes(nsfwMode) ? nsfwMode : "soft",
          intensity: Number.isFinite(nsfwIntensity) ? nsfwIntensity : 0.5,
          extra_prompt: String(readScopedPref(prefs, "nsfw_extra_prompt", legacyNsfw.extra_prompt || legacyNsfw.extra || "") || ""),
        });

        const effort = String(readScopedPref(prefs, "reasoning_effort", "medium") || "medium");
        if (["low", "medium", "high"].includes(effort)) setReasoningEffort(effort);
        setReqTimeout(String(readScopedPref(prefs, "request_timeout", "") ?? ""));
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);
  // task 51 fix: 之前 `save(k)` 只传 1 个参数,useAutoSave 收到 val===undefined
  // 走 toast-only 分支 → 用户改 temperature/top_p/max_tokens 等全无效,刷新即丢。
  // 必须传 v,让 backend 真的落库 user_preferences。
  const u = (k, v) => { setParams(p => ({ ...p, [k]: v })); save(k, v); };

  const applyPreset = (name) => {
    setPreset(name);
    save("preset", name);
    const values = MODEL_PARAM_PRESET_VALUES[name];
    if (values) {
      setParams(p => ({ ...p, ...values }));
      Object.entries(values).forEach(([k, v]) => save(k, v));
    }
  };

  const updateNsfw = (patch) => {
    setNsfw(n => ({ ...n, ...patch }));
    if (Object.prototype.hasOwnProperty.call(patch, "mode")) save("nsfw_mode", patch.mode);
    if (Object.prototype.hasOwnProperty.call(patch, "intensity")) save("nsfw_intensity", patch.intensity);
    if (Object.prototype.hasOwnProperty.call(patch, "extra_prompt")) save("nsfw_extra_prompt", patch.extra_prompt);
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
          onChange={({ detail }) => { setPreset("custom"); u("max_tokens", Number(detail.value)); }} />
      </SetRow>

      <SetRow label={t('settings.modelparams.request_timeout', { defaultValue: '请求超时(秒)' })}
        description={t('settings.modelparams.request_timeout_desc', { defaultValue: '本地大模型(如自己电脑跑千问/Qwen,纯内存/CPU 很慢)等待时间不够会被切断。留空=自动(桌面 1800 秒 / 在线 300 秒);需要更久就填大一点,如 3600。' })}>
        <CSInput type="number" value={reqTimeout} placeholder={t('settings.modelparams.request_timeout_auto', { defaultValue: '自动' })}
          onChange={({ detail }) => { const v = detail.value; setReqTimeout(v); save("request_timeout", v === "" ? "" : Number(v)); }} />
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
        {/* stop 以竖线分隔串存入 user_preferences(如 "player:|system:")。
            Preview JSON 仅展示拆分后的数组形态;后端当前不消费此字段(尚未接入 LLM call),
            存储格式保持竖线串,待后端接入时在 app.py 里 split("|") 转数组传给模型。 */}
        <CSInput value={params.stop} onChange={({ detail }) => u("stop", detail.value)}
          placeholder="player:|system:" />
      </SetRow>

      <SetRow label={t('settings.modelparams.nsfw')} description={t('settings.modelparams.nsfw_desc')}>
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSButton variant={nsfw.mode === "block" ? "primary" : "normal"} onClick={() => updateNsfw({ mode: "block" })}>{t('settings.modelparams.nsfw_block')}</CSButton>
          <CSButton variant={nsfw.mode === "soft" ? "primary" : "normal"} onClick={() => updateNsfw({ mode: "soft" })}>{t('settings.modelparams.nsfw_soft')}</CSButton>
          <CSButton variant={nsfw.mode === "open" ? "primary" : "normal"} onClick={() => updateNsfw({ mode: "open" })}>{t('settings.modelparams.nsfw_open')}</CSButton>
          <CSButton variant={nsfw.mode === "explicit" ? "primary" : "normal"} onClick={() => updateNsfw({ mode: "explicit" })}>{t('settings.modelparams.nsfw_explicit')}</CSButton>
        </CSSpaceBetween>
      </SetRow>

      {nsfw.mode !== "block" && (
        <ParamSlider label={t('settings.modelparams.nsfw_intensity')} desc={t('settings.modelparams.nsfw_intensity_desc')}
          value={nsfw.intensity} min={0} max={1} step={0.05} unit=""
          onChange={(v) => updateNsfw({ intensity: v })} />
      )}

      <SetRow label={t('settings.modelparams.nsfw_extra')} description={t('settings.modelparams.nsfw_extra_desc')}>
        <CSInput value={nsfw.extra_prompt}
          onChange={({ detail }) => updateNsfw({ extra_prompt: detail.value })}
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

/* ModuleModelsSection — 给每个 LLM / RAG / 生图子模块单独选模型。

   ── 统一规范组件 ──────────────────────────────────────────────────────────
   每行的「选模型」一律用 <AgentModelPicker variant="bare"> —— 全站唯一实现,
   不再自造 CSSelect + 数据加载 + 自定义手填。各模块只声明 prefPrefix / persistShape /
   capabilityFilter / allowInherit,组件内部负责:仅展示已配 key 的 provider 的真实模型、
   按 capability 过滤、自定义手填兜底、双 flat key / dict 单 key 落库、「跟随主 GM」清空偏好。

   落库形态:
     · flat(默认)         : <prefPrefix>.api_id + <prefPrefix>.model_real_name
     · dict(sub_agent /    : persistShape="dict" + dictKey = { api_id, model }
        console)
   主 GM 不可继承(它是被继承者);embedder / image_gen allowInherit=false(必须自己选)。
   embedder 还传 platformVertexAllowed(admin/vip 才显示平台 vertex embedding 兜底)。 */
/* FeatureToggle — 引擎特性开关(每用户、默认开)。写 user_preferences["<key>.enabled"];
   后端 core.feature_flags.feature_enabled 读同一键(未设=跟随环境默认开)。键名前后端单一来源。
   initial 由父组件一次性 profile 读出下发,避免每个开关各拉一次 profile。 */
function FeatureToggle({ featureKey, label, desc, initial }) {
  const { t } = useTranslation();
  const [on, setOn] = useStatePL(initial !== false);
  const save = useAutoSave(label, featureKey);
  useEffectPL(() => { setOn(initial !== false); }, [initial]);
  return (
    <SetRow label={label} description={desc}>
      <CSToggle checked={on} onChange={({ detail }) => { setOn(detail.checked); save("enabled", detail.checked); }}>
        {on ? t('settings.features.on', { defaultValue: '已启用' }) : t('settings.features.off', { defaultValue: '已关闭' })}
      </CSToggle>
    </SetRow>
  );
}

function ModuleModelsSection() {
  const { t } = useTranslation();
  // 结构字段(prefPrefix / persistShape / dictKey / capabilityFilter / inherit / …)走单一来源
  // agent-modules.js(语义统一 #19);label / tip 文案为桌面端专属(比移动端详尽,且后续 i18n),
  // 故保留在本地按 id 取 → 显示零变化。
  const LABELS = {
    gm: t('settings.more.modules.label_gm'), sub_agent: t('settings.more.modules.label_sub_agent'), set_parser: t('settings.more.modules.label_set_parser'),
    console: t('settings.more.modules.label_console'), editor: t('settings.more.modules.label_editor'), extractor: t('settings.more.modules.label_extractor'), recorder: t('settings.more.modules.label_recorder'), card_gen: t('settings.more.modules.label_card_gen'),
    card_import: t('settings.more.modules.label_card_import'), critic: t('settings.more.modules.label_critic'), verifier: t('settings.more.modules.label_verifier'),
    phase_digest: t('settings.more.modules.label_phase_digest'), black_swan: t('settings.more.modules.label_black_swan'), agent: t('settings.more.modules.label_agent'),
    embedder: t('settings.more.modules.label_embedder'), image_gen: t('settings.more.modules.label_image_gen'),
  };
  const TIPS = {
    gm: t('settings.more.modules.tip_gm'),
    sub_agent: t('settings.more.modules.tip_sub_agent'),
    set_parser: t('settings.more.modules.tip_set_parser'),
    console: t('settings.more.modules.tip_console'),
    editor: t('settings.more.modules.tip_editor'),
    extractor: t('settings.more.modules.tip_extractor'),
    recorder: t('settings.more.modules.tip_recorder'),
    card_gen: t('settings.more.modules.tip_card_gen'),
    card_import: t('settings.more.modules.tip_card_import'),
    critic: t('settings.more.modules.tip_critic'),
    verifier: t('settings.more.modules.tip_verifier'),
    phase_digest: t('settings.more.modules.tip_phase_digest'),
    black_swan: t('settings.more.modules.tip_black_swan'),
    agent: t('settings.more.modules.tip_agent'),
    embedder: t('settings.more.modules.tip_embedder'),
    image_gen: t('settings.more.modules.tip_image_gen'),
  };
  const MODULES = AGENT_MODULES.map((m) => ({ ...m, label: LABELS[m.id], tip: TIPS[m.id] }));

  // 引擎特性的本地默认文案(i18n 缺键时回退);label/desc 走 settings.features.<i18nKey>.*
  const FEAT_LABELS = {
    ctx_tiered: "分层上下文缓存", recorder_unified: "史官三合一", narrator_slim: "文宗精简(去工具循环)",
    rag_gate: "RAG 检索闸", anchor_pace: "世界线锚点节奏", kb_state: "存档知识库 DB 化",
  };
  const FEAT_DESCS = {
    ctx_tiered: "把稳定前缀与动态内容分层,命中模型前缀缓存,显著省 token。",
    recorder_unified: "状态提取 + 世界线锚点判定合并为一次调用,省一次 LLM。",
    narrator_slim: "主叙事单次成文、不带工具循环(最大 token 乘数),状态交史官落库。",
    rag_gate: "由司命判定本回合是否需要检索原著,不需要则跳过,省检索 token。",
    anchor_pace: "按对话实际节奏推进锚点、同章逐个标记、角色死亡使相关锚点失效 —— 治跳章 / 不按锚点走。",
    kb_state: "存档状态以数据库行存储(单一来源),便于精确检索与维护;关闭则用传统整档存储。",
  };
  const GROUP_DESC = {
    core: "三贤者流水线:文宗叙事、司命规划、史官记录,以及核心 GM 辅助模型。",
    script: "剧本编辑、角色卡生成 / 导入与审校相关的模型。",
    world: "世界线锚点、阶段摘要、黑天鹅事件等世界推进相关。",
    gen: "向量检索(RAG)与图像生成。",
    misc: "未单独配置时的通用兜底模型。",
  };
  const _subHdr = { fontSize: 11, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase", color: "var(--muted)", marginBottom: 8 };

  // task: embedder 兜底状态 — RAG 模型 section banner 文案 + 是否给 admin/vip 平台 vertex 兜底
  const [embedderStatus, setEmbedderStatus] = useStatePL(null);
  useEffectPL(() => {
    fetch('/api/me/embedder/status', { credentials: 'include' })
      .then(r => r.json()).then(es => setEmbedderStatus(es?.ok ? es : null)).catch(() => {});
  }, []);
  // 后端已按 _is_admin gate(admin/vip 才 true);非 vip/admin 不显示平台 vertex embedding。
  const platformVertexAllowed = !!(embedderStatus && embedderStatus.platform_fallback_available);

  // 一次性读取特性偏好(各开关初值),避免每个 FeatureToggle 各拉一次 profile。
  const [featPrefs, setFeatPrefs] = useStatePL({});
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const profile = await window.api.account.profile();
        if (!cancelled && profile && profile.preferences) setFeatPrefs(profile.preferences);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const renderModelRow = (mod) => (
    <div key={mod.id}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{mod.label}</strong>
        <span className="muted" style={{ fontSize: 11 }}>{mod.tip}</span>
      </div>
      <AgentModelPicker
        prefPrefix={mod.prefPrefix}
        persistShape={mod.persistShape || "flat"}
        dictKey={mod.dictKey || null}
        capabilityFilter={mod.capabilityFilter || null}
        allowInherit={!!mod.inherit}
        defaultModel={mod.defaultModel || null}
        preferProvider={mod.preferProvider || null}
        fallbackPrefix={mod.fallbackPrefix || null}
        platformVertexAllowed={mod.id === "embedder" ? platformVertexAllowed : false}
        variant="bare"
        configHash="apis"
      />
      {mod.id === 'embedder' && embedderStatus && (
        <div style={{ marginTop: 6 }}>
          {embedderStatus.fallback_active ? (
            <div style={{ fontSize: 11, color: "#0972d3" }}>{t('settings.more.modules.embedder_fallback_active')}</div>
          ) : embedderStatus.is_admin && embedderStatus.user_configured ? (
            <div style={{ fontSize: 11, color: "#1a7e3c" }}>{t('settings.more.modules.embedder_admin_configured')}</div>
          ) : embedderStatus.is_admin && !embedderStatus.user_configured ? (
            <div style={{ fontSize: 11, color: "#0972d3" }}>{t('settings.more.modules.embedder_admin_fallback')}</div>
          ) : !embedderStatus.user_configured ? (
            <div style={{ fontSize: 11, color: "#d18a00" }}>{t('settings.more.modules.embedder_user_no_key')}</div>
          ) : null}
        </div>
      )}
      {mod.id === "embedder" && (
        <div style={{ marginTop: 6, fontSize: 11, color: "var(--muted)" }}>{t('settings.more.modules.embedder_note')}</div>
      )}
    </div>
  );

  return (
    <SetGroup
      title={t('settings.modules.title')}
      description={t('settings.modules.description')}
    >
      <CSBox>
        <span className="muted" style={{ fontSize: 12 }}>{t('settings.modules.hint')}</span>
      </CSBox>
      {/* 按功能分组卡片:每组先「功能开关」(每用户、默认开)再「模型分配」,强分隔、去杂乱。 */}
      <div style={{ display: "grid", gap: 16, marginTop: 10 }}>
        {MODULE_GROUPS.map(grp => {
          const items = MODULES.filter(m => (m.group || "misc") === grp.id);
          const feats = (AGENT_FEATURES || []).filter(f => f.group === grp.id);
          if (!items.length && !feats.length) return null;
          const groupLabel = t(`settings.more.modules.group_${grp.id}`, {
            defaultValue: { core: "对话核心（三贤者 + GM 管线）", script: "剧本与角色卡", world: "世界模拟与历史", gen: "检索与生成", misc: "通用兜底" }[grp.id] || grp.id,
          });
          const groupDesc = t(`settings.modules.group_desc.${grp.id}`, { defaultValue: GROUP_DESC[grp.id] || "" });
          return (
            <div key={grp.id} style={{ border: "1px solid var(--pl-line, #e9e9ed)", borderRadius: 12, padding: "16px 18px 18px" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pl-accent, #0972d3)" }}>{groupLabel}</div>
              {groupDesc && <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>{groupDesc}</div>}

              {feats.length > 0 && (
                <div style={{ marginTop: 16, marginBottom: items.length ? 18 : 0 }}>
                  <div style={_subHdr}>{t('settings.modules.features_title', { defaultValue: '功能开关' })}</div>
                  <div style={{ display: "grid", gap: 2 }}>
                    {feats.map(f => (
                      <FeatureToggle
                        key={f.key}
                        featureKey={f.key}
                        label={t(`settings.features.${f.i18nKey}.label`, { defaultValue: FEAT_LABELS[f.key] || f.key })}
                        desc={t(`settings.features.${f.i18nKey}.desc`, { defaultValue: FEAT_DESCS[f.key] || "" })}
                        initial={featPrefs[`${f.key}.enabled`]}
                      />
                    ))}
                  </div>
                </div>
              )}

              {items.length > 0 && (
                <div style={{ marginTop: feats.length ? 0 : 16 }}>
                  <div style={_subHdr}>{t('settings.modules.models_title', { defaultValue: '模型分配' })}</div>
                  <div style={{ display: "grid", gap: 14 }}>
                    {items.map(renderModelRow)}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <CSBox>
        <span className="muted" style={{ fontSize: 11 }}>{t('settings.modules.footer')}</span>
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
          const stored = lsGetJSON("perm.custom_whitelist", null);
          if (stored) setCustomWhitelist(stored);
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
    lsSetJSON("perm.custom_whitelist", next);
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
                { value: "qq",       label: t('settings.more.deploy.smtp_qq') },
                { value: "163",      label: t('settings.more.deploy.smtp_163') },
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
                  const smtpTestResp = await window.api.raw?.POST("/api/v1/admin/smtp/test", {});
                  ok = !!(smtpTestResp && smtpTestResp.ok !== false);
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

// ── 账号设置（Beta Co-builders opt-out）──────────────────────────────────────
function AccountSection() {
  const { t } = useTranslation();
  const user = useReactiveUser();
  const isCoBuilder = user?.is_co_builder === true;
  // true = 参加，false = 不参加（co_builder_opt_out=true 表示退出）
  const [checked, setChecked] = useStatePL(() => !user?.co_builder_opt_out);
  const [saving, setSaving] = useStatePL(false);

  // 用户数据就绪后同步初始值
  useEffectPL(() => {
    setChecked(!user?.co_builder_opt_out);
  }, [user?.co_builder_opt_out]);

  const handleToggle = async (newChecked) => {
    setChecked(newChecked);
    setSaving(true);
    try {
      await fetch('/api/me/profile', {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ co_builder_opt_out: !newChecked }),
      });
      publishUser({ co_builder_opt_out: !newChecked });
      window.__apiToast?.(t('settings.account.co_builder_saved'), { kind: 'ok', duration: 1800 });
    } catch (e) {
      window.__apiToast?.(t('settings.account.co_builder_save_fail'), { kind: 'danger', detail: e?.message });
      // 回滚
      setChecked(!newChecked);
    }
    setSaving(false);
  };

  return (
    <CSSpaceBetween size="l">
      <SetGroup title={t('settings.account.title')}>
        {isCoBuilder ? (
          <SetRow
            label={t('settings.account.co_builder_label')}
            description={t('settings.account.co_builder_desc')}
          >
            <CSToggle
              checked={checked}
              onChange={({ detail }) => handleToggle(detail.checked)}
              disabled={saving}
            >
              {checked ? t('settings.account.co_builder_on') : t('settings.account.co_builder_off')}
            </CSToggle>
          </SetRow>
        ) : (
          <SetRow label={t('settings.account.co_builder_label')} description="">
            <span style={{ fontSize: 13, color: 'var(--text-quiet)' }}>{t('settings.account.co_builder_na')}</span>
          </SetRow>
        )}
      </SetGroup>
      <DataMigrationSection />
      <OnlineLibrarySection />
    </CSSpaceBetween>
  );
}

// 账号数据迁移:把个人数据(剧本/存档/角色卡/偏好)整体导出为 zip,在本地自部署实例导入。
function DataMigrationSection() {
  const { t } = useTranslation();
  const [est, setEst] = useStatePL(null);
  const [estErr, setEstErr] = useStatePL("");
  const [includeChunks, setIncludeChunks] = useStatePL(false);
  const [exporting, setExporting] = useStatePL(false);
  const [importing, setImporting] = useStatePL(false);
  const [importFile, setImportFile] = useStatePL(null);
  const [importResult, setImportResult] = useStatePL(null);
  const [importJob, setImportJob] = useStatePL(null);   // {stage, stage_progress, stage_total}
  const fileRef = React.useRef(null);
  const esRef = React.useRef(null);
  const pollRef = React.useRef(null);

  useEffectPL(() => {
    let alive = true;
    window.api?.account?.migrateEstimate?.()
      .then((r) => { if (alive) setEst(r); })
      .catch((e) => { if (alive) setEstErr(e?.message || String(e)); });
    return () => { alive = false; };
  }, []);

  const doExport = () => {
    setExporting(true);
    try {
      // 同源 GET + cookie → 直接触发浏览器下载 zip。
      const url = window.api.account.migrateExportUrl(includeChunks);
      const a = document.createElement('a');
      a.href = url;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.__apiToast?.(t('settings.migrate.export_started', { defaultValue: '已开始下载数据包…' }), { kind: 'ok', duration: 2200 });
    } catch (e) {
      window.__apiToast?.(t('settings.migrate.export_fail', { defaultValue: '导出失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      // 下载是浏览器接管,这里只复位按钮态
      setTimeout(() => setExporting(false), 800);
    }
  };

  const STAGE_LABELS = {
    scripts: t('settings.more.migrate.stage_scripts'),
    saves: t('settings.more.migrate.stage_saves'),
    cards: t('settings.more.migrate.stage_cards'),
    done: t('settings.more.migrate.stage_done'),
  };

  const finishJob = async (jobId) => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (esRef.current) { try { esRef.current.close?.(); } catch {} esRef.current = null; }
    try {
      const s = await window.api.scripts.jobStatus(jobId);
      const job = s?.job || {};
      const summary = (job.usage_actual && job.usage_actual.summary) || {};
      setImportResult({ scripts: summary.scripts ?? 0, saves: summary.saves ?? 0, cards: summary.cards ?? 0, warnings: job.warnings || [] });
      window.__apiToast?.(t('settings.migrate.import_done', { defaultValue: '导入完成' }), { kind: 'ok', duration: 2600,
        detail: t('settings.more.migrate.import_done_detail', { scripts: summary.scripts ?? 0, saves: summary.saves ?? 0, cards: summary.cards ?? 0 }) });
    } catch (e) {
      window.__apiToast?.(t('settings.migrate.import_fail', { defaultValue: '导入失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setImportJob(null); setImporting(false);
    }
  };

  const doImport = async () => {
    if (!importFile) return;
    setImporting(true); setImportResult(null); setImportJob({ stage: 'scripts', stage_progress: 0, stage_total: 0 });
    let jobId = null;
    try {
      const r = await window.api.account.migrateImport(importFile);
      jobId = r?.job_id;
      if (!jobId) throw new Error(r?.error || t('settings.more.migrate.no_job_id'));
    } catch (e) {
      setImporting(false); setImportJob(null);
      window.__apiToast?.(t('settings.migrate.import_fail', { defaultValue: '导入失败' }), { kind: 'danger', detail: e?.payload?.error || e?.message });
      return;
    }
    const isTerminal = (st) => ['done', 'done_with_errors', 'failed', 'cancelled'].includes(st);
    // SSE 主路 + 轮询兜底
    esRef.current = window.api.scripts.streamImport(jobId, {
      on_update: (jb) => { setImportJob(jb); if (isTerminal(jb.status)) finishJob(jobId); },
      on_done: () => finishJob(jobId),
      on_error: () => {
        if (pollRef.current) return;
        pollRef.current = setInterval(async () => {
          try {
            const s = await window.api.scripts.jobStatus(jobId);
            const job = s?.job; if (!job) return;
            setImportJob(job);
            if (isTerminal(job.status)) finishJob(jobId);
          } catch {}
        }, 2000);
      },
    });
  };

  useEffectPL(() => () => {
    if (esRef.current) { try { esRef.current.close?.(); } catch {} }
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  return (
    <SetGroup
      title={t('settings.migrate.title', { defaultValue: '数据迁移(导出 / 导入)' })}
      description={t('settings.migrate.desc', { defaultValue: '把你的全部个人数据打包,迁移到本地自部署实例;或从数据包恢复。不含 API 密钥。' })}
    >
      <CSAlert type="info">
        {t('settings.migrate.note_keys', { defaultValue: '出于安全,导出不含 API 密钥(在服务端加密存储,跨实例无法解密)。迁移到本地后请在「设置 → 模型」重新填写各 provider 的 API key。' })}
      </CSAlert>

      <SetRow
        label={t('settings.migrate.export_label', { defaultValue: '导出我的全部数据' })}
        description={est
          ? t('settings.migrate.export_counts', { defaultValue: '剧本 {{s}} · 存档 {{v}} · 角色卡 {{c}} · 模型条目 {{m}}', s: est.scripts ?? 0, v: est.saves ?? 0, c: est.cards ?? 0, m: est.model_entries ?? 0 })
          : (estErr ? t('settings.migrate.est_fail', { defaultValue: '统计失败:' }) + estErr : t('settings.migrate.estimating', { defaultValue: '正在统计…' }))}
      >
        <CSSpaceBetween size="xs">
          <CSToggle checked={includeChunks} onChange={({ detail }) => setIncludeChunks(detail.checked)}>
            {t('settings.migrate.include_chunks', { defaultValue: '包含原文切片(体积更大,用于本地继续做向量检索)' })}
          </CSToggle>
          <CSButton variant="primary" iconName="download" loading={exporting} onClick={doExport}>
            {t('settings.migrate.export_btn', { defaultValue: '导出数据包(.zip)' })}
          </CSButton>
        </CSSpaceBetween>
      </SetRow>

      <SetRow
        label={t('settings.migrate.import_label', { defaultValue: '导入数据包' })}
        description={t('settings.migrate.import_help', { defaultValue: '选择从在线服务导出的 account-*.zip。导入会在当前账号下新建剧本/存档/角色卡,不覆盖现有数据。' })}
      >
        <CSSpaceBetween size="xs">
          <input
            ref={fileRef}
            type="file"
            accept=".zip,application/zip"
            onChange={(e) => { setImportFile(e.target.files?.[0] || null); setImportResult(null); }}
            style={{ fontSize: 13 }}
          />
          <CSButton iconName="upload" loading={importing} disabled={!importFile || importing} onClick={doImport}>
            {t('settings.migrate.import_btn', { defaultValue: '导入到当前账号' })}
          </CSButton>
        </CSSpaceBetween>
      </SetRow>

      {importJob && (
        <CSBox>
          <div style={{ fontSize: 13, marginBottom: 4 }}>
            {(STAGE_LABELS[importJob.stage] || importJob.stage || t('settings.more.migrate.stage_processing'))}
            {importJob.stage_total ? ` ${importJob.stage_progress || 0}/${importJob.stage_total}` : '…'}
          </div>
          <div style={{ height: 6, background: 'var(--line,#36322d)', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${importJob.stage_total ? Math.round(100 * (importJob.stage_progress || 0) / importJob.stage_total) : 30}%`,
              background: 'var(--accent,#c96442)', transition: 'width .3s' }} />
          </div>
        </CSBox>
      )}

      {importResult && (
        <CSAlert type={(importResult.warnings || []).length ? 'warning' : 'success'} header={t('settings.migrate.import_result', { defaultValue: '导入结果' })}>
          <div>{t('settings.migrate.import_summary', { defaultValue: '剧本 {{s}} · 存档 {{v}} · 角色卡 {{c}}', s: importResult.scripts ?? 0, v: importResult.saves ?? 0, c: importResult.cards ?? 0 })}</div>
          {(importResult.warnings || []).length > 0 && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12 }}>
              {importResult.warnings.slice(0, 20).map((w, i) => <li key={i}>{w}</li>)}
              {importResult.warnings.length > 20 && <li>{t('settings.more.migrate.warnings_truncated', { count: importResult.warnings.length - 20 })}</li>}
            </ul>
          )}
        </CSAlert>
      )}
    </SetGroup>
  );
}

// 功能 B:本地↔在线剧本库联邦。集连接(PAT/设备码)+ 浏览/导入/发布 + 设备授权 + PAT 管理。
const DEFAULT_ONLINE_BASE = 'https://rpg-roleplay.stellatrix.icu';

function OnlineLibrarySection() {
  const { t } = useTranslation();
  const [conn, setConn] = useStatePL(null);            // {connected, base_url}
  const [isProvider, setIsProvider] = useStatePL(false); // 本实例是否为在线库提供方(server 模式)
  const reload = useCallbackPL(async () => {
    try { setConn(await window.api.federation.connectorGet()); } catch { setConn({ connected: false, base_url: DEFAULT_ONLINE_BASE }); }
  }, []);
  useEffectPL(() => { reload(); }, [reload]);
  useEffectPL(() => {
    window.api?.federation?.providerInfo?.().then((r) => setIsProvider(!!r?.provider_enabled)).catch(() => setIsProvider(false));
  }, []);

  // 角色分离:
  //  - 提供方(在线服务,server 模式)只显示「令牌管理」;设备授权在独立 /device 页完成,
  //    不在设置里放配对码填写窗口(避免在线服务器出现「连接到在线服务」这种自连客户端 UI)。
  //  - 客户端(本地自部署)只显示连接器(连接在线服务 / 浏览 / 导入 / 发布)。
  if (isProvider) {
    return (
      <SetGroup
        title={t('settings.more.online_lib.provider_title')}
        description={t('settings.more.online_lib.provider_desc')}
      >
        <PatManager />
      </SetGroup>
    );
  }
  return (
    <SetGroup
      title={t('settings.more.online_lib.client_title')}
      description={t('settings.more.online_lib.client_desc')}
    >
      <ConnectorConnect conn={conn} onChange={reload} />
      {conn?.connected && <OnlineBrowse />}
      {conn?.connected && <OnlinePublish />}
    </SetGroup>
  );
}

function ConnectorConnect({ conn, onChange }) {
  const { t } = useTranslation();
  const [base, setBase] = useStatePL(conn?.base_url || DEFAULT_ONLINE_BASE);
  const [token, setToken] = useStatePL('');
  const [busy, setBusy] = useStatePL(false);
  const [device, setDevice] = useStatePL(null);        // {user_code, verification_uri, device_code, base_url, interval}
  const pollRef = React.useRef(null);
  useEffectPL(() => { setBase(conn?.base_url || DEFAULT_ONLINE_BASE); }, [conn?.base_url]);
  useEffectPL(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const savePat = async () => {
    if (!token.trim()) { window.__apiToast?.(t('settings.more.online_lib.paste_token_warn'), { kind: 'warn' }); return; }
    setBusy(true);
    try {
      await window.api.federation.connectorSet(base.trim(), token.trim());
      window.__apiToast?.(t('settings.more.online_lib.connected_ok'), { kind: 'ok' });
      setToken(''); onChange?.();
    } catch (e) { window.__apiToast?.(t('settings.more.online_lib.connect_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); }
    finally { setBusy(false); }
  };

  const disconnect = async () => {
    setBusy(true);
    try { await window.api.federation.connectorSet(base.trim(), ''); window.__apiToast?.(t('settings.more.online_lib.disconnected'), { kind: 'ok' }); onChange?.(); }
    catch (e) { window.__apiToast?.(t('settings.more.online_lib.op_fail'), { kind: 'danger', detail: e?.message }); }
    finally { setBusy(false); }
  };

  const startDevice = async () => {
    setBusy(true);
    try {
      const d = await window.api.federation.deviceStart(base.trim(), ['library:read', 'library:publish']);
      setDevice(d);
      const iv = Math.max(2, (d.interval || 5)) * 1000;
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const r = await window.api.federation.devicePoll(d.base_url || base.trim(), d.device_code);
          if (r.connected) {
            clearInterval(pollRef.current); pollRef.current = null;
            setDevice(null); window.__apiToast?.(t('settings.more.online_lib.connected_ok'), { kind: 'ok' }); onChange?.();
          } else if (r.status && !['authorization_pending', 'pending'].includes(r.status)) {
            clearInterval(pollRef.current); pollRef.current = null;
            setDevice(null); window.__apiToast?.(t('settings.more.online_lib.auth_incomplete', { status: r.status }), { kind: 'warn' });
          }
        } catch { /* 继续轮询 */ }
      }, iv);
    } catch (e) { window.__apiToast?.(t('settings.more.online_lib.device_start_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); }
    finally { setBusy(false); }
  };

  if (conn?.connected) {
    return (
      <CSSpaceBetween size="s">
        <CSBox>{t('settings.more.online_lib.connected_to')}<strong>{conn.base_url}</strong></CSBox>
        <CSButton iconName="unlocked" loading={busy} onClick={disconnect}>{t('settings.more.online_lib.disconnect')}</CSButton>
      </CSSpaceBetween>
    );
  }

  return (
    <CSSpaceBetween size="m">
      <SetRow label={t('settings.more.online_lib.server_url')} description={t('settings.more.online_lib.server_url_desc')}>
        <CSInput value={base} onChange={({ detail }) => setBase(detail.value)} placeholder={DEFAULT_ONLINE_BASE} />
      </SetRow>

      <SetRow label={t('settings.more.online_lib.device_method')} description={t('settings.more.online_lib.device_method_desc')}>
        {device ? (
          <CSAlert type="info" header={t('settings.more.online_lib.device_auth_header')}>
            <div>{t('settings.more.online_lib.device_step1')} <a href={device.verification_uri_complete || device.verification_uri} target="_blank" rel="noopener noreferrer">{device.verification_uri_complete || device.verification_uri}</a></div>
            <div>{t('settings.more.online_lib.device_step2')} <strong style={{ fontSize: 18, letterSpacing: 2 }}>{device.user_code}</strong></div>
            <div style={{ marginTop: 6, color: 'var(--text-quiet)' }}>{t('settings.more.online_lib.device_waiting')}</div>
          </CSAlert>
        ) : (
          <CSButton variant="primary" iconName="external" loading={busy} onClick={startDevice}>{t('settings.more.online_lib.device_connect_btn')}</CSButton>
        )}
      </SetRow>

      <SetRow label={t('settings.more.online_lib.pat_method')} description={t('settings.more.online_lib.pat_method_desc')}>
        <CSSpaceBetween size="xs">
          <CSInput value={token} type="password" onChange={({ detail }) => setToken(detail.value)} placeholder="rpgpat_…" />
          <CSButton loading={busy} onClick={savePat}>{t('settings.more.online_lib.pat_save_btn')}</CSButton>
        </CSSpaceBetween>
      </SetRow>
    </CSSpaceBetween>
  );
}

function OnlineBrowse() {
  const { t } = useTranslation();
  const [q, setQ] = useStatePL('');
  const [items, setItems] = useStatePL(null);
  const [loading, setLoading] = useStatePL(false);
  const [importing, setImporting] = useStatePL({});
  const load = useCallbackPL(async (query) => {
    setLoading(true);
    try { const r = await window.api.federation.connectorScripts(query); setItems(r?.items || []); }
    catch (e) { window.__apiToast?.(t('settings.more.online_lib.browse_load_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); setItems([]); }
    finally { setLoading(false); }
  }, []);
  const doImport = async (it) => {
    setImporting((p) => ({ ...p, [it.id]: true }));
    try {
      const r = await window.api.federation.connectorImport(it.id);
      window.__apiToast?.(t('settings.more.online_lib.browse_import_ok'), { kind: 'ok', detail: `「${it.title}」→ ${t('settings.more.online_lib.browse_import_local')} #${r.script_id}` });
    } catch (e) { window.__apiToast?.(t('settings.more.online_lib.browse_import_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); }
    finally { setImporting((p) => ({ ...p, [it.id]: false })); }
  };
  return (
    <CSExpandableSection headerText={t('settings.more.online_lib.browse_header')} defaultExpanded
      onChange={({ detail }) => { if (detail.expanded && items == null) load(''); }}>
      <CSSpaceBetween size="s">
        <div style={{ display: 'flex', gap: 8, maxWidth: 460 }}>
          <div style={{ flex: 1 }}>
            <CSInput value={q} type="search" placeholder={t('settings.more.online_lib.browse_search_placeholder')} onChange={({ detail }) => setQ(detail.value)}
              onKeyDown={(e) => { if (e.detail.key === 'Enter') load(q); }} />
          </div>
          <CSButton loading={loading} onClick={() => load(q)}>{t('settings.more.online_lib.browse_search_btn')}</CSButton>
        </div>
        {items && items.length === 0 && <CSBox color="text-body-secondary">{t('settings.more.online_lib.browse_empty')}</CSBox>}
        {(items || []).map((it) => (
          <div key={it.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '8px 10px', border: '1px solid var(--line,#36322d)', borderRadius: 8 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{it.title || t('settings.more.online_lib.untitled')}</div>
              <div style={{ fontSize: 12, color: 'var(--text-quiet)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {it.owner_name ? `by ${it.owner_name} · ` : ''}♥ {it.clone_count || 0}{it.description ? ' · ' + String(it.description).slice(0, 50) : ''}
              </div>
            </div>
            <CSButton variant="primary" loading={!!importing[it.id]} onClick={() => doImport(it)}>{t('settings.more.online_lib.browse_import_btn')}</CSButton>
          </div>
        ))}
      </CSSpaceBetween>
    </CSExpandableSection>
  );
}

function OnlinePublish() {
  const { t } = useTranslation();
  const [scripts, setScripts] = useStatePL([]);
  const [sel, setSel] = useStatePL(null);
  const [busy, setBusy] = useStatePL(false);
  useEffectPL(() => {
    window.api.scripts.list().then((r) => {
      const list = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      setScripts(list.filter((s) => s.is_owner !== false).map((s) => ({ value: String(s.id), label: s.title || `${t('settings.more.online_lib.script_prefix')} #${s.id}` })));
    }).catch(() => {});
  }, []);
  const publish = async () => {
    if (!sel) return;
    setBusy(true);
    try {
      const r = await window.api.federation.connectorPublish(Number(sel.value));
      window.__apiToast?.(t('settings.more.online_lib.publish_ok'), { kind: 'ok', detail: `${t('settings.more.online_lib.online_script_prefix')} #${r.script_id}` });
    } catch (e) { window.__apiToast?.(t('settings.more.online_lib.publish_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); }
    finally { setBusy(false); }
  };
  return (
    <SetRow label={t('settings.more.online_lib.publish_label')} description={t('settings.more.online_lib.publish_desc')}>
      <div style={{ display: 'flex', gap: 8, maxWidth: 460 }}>
        <div style={{ flex: 1 }}>
          <CSSelect selectedOption={sel} options={scripts} placeholder={t('settings.more.online_lib.publish_select_placeholder')}
            onChange={({ detail }) => setSel(detail.selectedOption)} />
        </div>
        <CSButton iconName="upload" loading={busy} disabled={!sel} onClick={publish}>{t('settings.more.online_lib.publish_btn')}</CSButton>
      </div>
    </SetRow>
  );
}

function DeviceApprove() {
  const { t } = useTranslation();
  const [code, setCode] = useStatePL('');
  const [info, setInfo] = useStatePL(null);
  const [busy, setBusy] = useStatePL(false);
  const lookup = async () => {
    setBusy(true); setInfo(null);
    try { const r = await window.api.federation.deviceLookup(code.trim().toUpperCase()); setInfo(r.device); }
    catch (e) { window.__apiToast?.(t('settings.more.online_lib.device_code_not_found'), { kind: 'warn', detail: e?.payload?.error || e?.message }); }
    finally { setBusy(false); }
  };
  const decide = async (deny) => {
    setBusy(true);
    try {
      await window.api.federation.deviceApprove(code.trim().toUpperCase(), deny);
      window.__apiToast?.(deny ? t('settings.more.online_lib.device_denied') : t('settings.more.online_lib.device_approved'), { kind: 'ok' });
      setInfo(null); setCode('');
    } catch (e) { window.__apiToast?.(t('settings.more.online_lib.op_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); }
    finally { setBusy(false); }
  };
  return (
    <CSSpaceBetween size="s">
      <div style={{ display: 'flex', gap: 8, maxWidth: 360 }}>
        <div style={{ flex: 1 }}>
          <CSInput value={code} placeholder="WXYZ-7K9M" onChange={({ detail }) => setCode(detail.value)} />
        </div>
        <CSButton loading={busy} disabled={!code.trim()} onClick={lookup}>{t('settings.more.online_lib.device_lookup_btn')}</CSButton>
      </div>
      {info && (
        <CSAlert type="info" header={t('settings.more.online_lib.device_confirm_header')}>
          <div>{t('settings.more.online_lib.device_confirm_client')}{info.client_name || t('settings.more.online_lib.untitled')} · {t('settings.more.online_lib.device_confirm_scopes')}{(info.scopes || []).join(', ')}</div>
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="primary" loading={busy} onClick={() => decide(false)}>{t('settings.more.online_lib.device_approve_btn')}</CSButton>
            <CSButton loading={busy} onClick={() => decide(true)}>{t('settings.more.online_lib.device_deny_btn')}</CSButton>
          </CSSpaceBetween>
        </CSAlert>
      )}
    </CSSpaceBetween>
  );
}

function PatManager() {
  const { t } = useTranslation();
  const [items, setItems] = useStatePL([]);
  const [name, setName] = useStatePL('');
  const [scopes, setScopes] = useStatePL({ read: true, publish: false });
  const [created, setCreated] = useStatePL(null);
  const [busy, setBusy] = useStatePL(false);
  const reload = useCallbackPL(async () => {
    try { const r = await window.api.federation.patList(); setItems(r?.items || []); } catch { setItems([]); }
  }, []);
  useEffectPL(() => { reload(); }, [reload]);
  const create = async () => {
    const sc = [scopes.read && 'library:read', scopes.publish && 'library:publish'].filter(Boolean);
    if (!sc.length) { window.__apiToast?.(t('settings.more.online_lib.pat_scope_warn'), { kind: 'warn' }); return; }
    setBusy(true);
    try {
      const r = await window.api.federation.patCreate({ name: name.trim(), scopes: sc });
      setCreated(r.token); setName(''); reload();
    } catch (e) { window.__apiToast?.(t('settings.more.online_lib.pat_create_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message }); }
    finally { setBusy(false); }
  };
  const revoke = async (id) => {
    try { await window.api.federation.patRevoke(id); reload(); window.__apiToast?.(t('settings.more.online_lib.pat_revoked'), { kind: 'ok' }); }
    catch (e) { window.__apiToast?.(t('settings.more.online_lib.op_fail'), { kind: 'danger', detail: e?.message }); }
  };
  return (
    <CSSpaceBetween size="s">
      {created && (
        <CSAlert type="success" header={t('settings.more.online_lib.pat_created_header')} dismissible onDismiss={() => setCreated(null)}>
          <code style={{ wordBreak: 'break-all' }}>{created}</code>
        </CSAlert>
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ width: 180 }}>
          <CSInput value={name} placeholder={t('settings.more.online_lib.pat_name_placeholder')} onChange={({ detail }) => setName(detail.value)} />
        </div>
        <CSToggle checked={scopes.read} onChange={({ detail }) => setScopes((s) => ({ ...s, read: detail.checked }))}>{t('settings.more.online_lib.pat_scope_read')}</CSToggle>
        <CSToggle checked={scopes.publish} onChange={({ detail }) => setScopes((s) => ({ ...s, publish: detail.checked }))}>{t('settings.more.online_lib.pat_scope_publish')}</CSToggle>
        <CSButton loading={busy} onClick={create}>{t('settings.more.online_lib.pat_create_btn')}</CSButton>
      </div>
      {items.length === 0 && <CSBox color="text-body-secondary" fontSize="body-s">{t('settings.more.online_lib.pat_empty')}</CSBox>}
      {items.map((p) => (
        <div key={p.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, fontSize: 13 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <CSBadge color={p.source === 'device' ? 'green' : 'grey'}>{p.source === 'device' ? t('settings.more.online_lib.pat_source_device') : t('settings.more.online_lib.pat_source_manual')}</CSBadge>
            <strong>{p.name || t('settings.more.online_lib.untitled')}</strong>
            <span style={{ color: 'var(--text-quiet)' }}>{(p.scopes || []).join(', ')}</span>
            <span style={{ color: 'var(--text-quiet)', fontSize: 12 }}>
              {p.last_used_at ? `· ${t('settings.more.online_lib.pat_last_used')} ` + (window.__fmt?.ago(p.last_used_at) || p.last_used_at) : `· ${t('settings.more.online_lib.pat_never_used')}`}
              {p.revoked_at ? ` · ${t('settings.more.online_lib.pat_revoked_badge')}` : ''}
            </span>
          </span>
          {!p.revoked_at && <CSButton variant="inline-link" onClick={() => revoke(p.id)}>{t('settings.more.online_lib.pat_revoke_btn')}</CSButton>}
        </div>
      ))}
    </CSSpaceBetween>
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
        <Modal
          open
          width={460}
          onClose={closeConfirm}
          header={
            <div>
              <div className="pl-modal-eyebrow" style={{color: "var(--danger)"}}>{t('settings.danger.clear_modal_eyebrow')}</div>
              <h2 className="pl-modal-title">{t('settings.danger.clear_modal_title')}</h2>
            </div>
          }
          footer={<>
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
          </>}
        >
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
        </Modal>
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
  normalizeApiId,
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
  BlackSwanSection,
  ExtractorSection,
  PrefSection,
  PermSection,
  ClarifySection,
  MemorySection,
  DangerSection,
  DeploySection,
  AccountSection,
  AuditLogView,
};

// 过渡期保留 window 注入,等所有 consumer 改完 import 后删除。
