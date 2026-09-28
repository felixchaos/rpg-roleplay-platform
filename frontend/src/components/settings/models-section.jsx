// 模型 / API 配置区主组件 ModelsSection + 二次拆分后子模块的 export 转发壳。
// 子弹窗→model-modals.jsx;详情/列表→model-list.jsx;供应商配置→provider-config.jsx;
// 目录数据/ID 别名/格式化→models-catalog.js。ModelsSection 主体逐字节不动。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { SettingsToggle, ResizableSplit } from '../../platform-app.jsx';
import {
  normalizeApiId,
  credentialApiIdForCatalog,
  catalogApiIdForCredential,
  MODELS_DATA,
  PROVIDERS_CONFIG,
} from './models-catalog.js';
import { ApiDetailPanel, ApiModelsList, ModelNameCell, HealthDot } from './model-list.jsx';
import { AddModelModal, EditApiModal, VisibilityModal, ValidateModal } from './model-modals.jsx';
import { ProviderCard, ProviderConfigSection } from './provider-config.jsx';
import { setModelEnabled, removeModel, renameModel as renameModelWrite, removeTargetsFor, isAdminOnlyModelEdit } from '../../lib/model-overlay-write.js';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSTable from '@cloudscape-design/components/table';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';


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
  const [addModelApi, setAddModelApi] = useStatePL(null);
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
      const _hasKey = !!c.has_credential || !!c.has_key || !!c.key_hint;
      credMap[cid] = {
        has_key: _hasKey,
        auth_mode: c.auth_mode || 'api_key',
        // configured = 「这条凭据算配好了」。免鉴权(本地/自托管)凭据没有 key,若继续用
        // has_key 当可见性判据,用户勾了「免 API Key」保存后 provider 会**直接从列表消失**。
        // 后端 list_credentials 已给出同名字段,这里兜底再算一次(老后端没有该字段时不至于全空)。
        configured: (c.configured != null) ? !!c.configured : (_hasKey || c.auth_mode === 'none'),
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
        // 凭据里真正存的覆盖地址(可能为空 = 跟随目录)。只改连接方式时原样回写它,
        // 不能拿上面那个「显示用」的 base_url 顶上去,否则会把目录地址钉死成覆盖。
        base_url_override: cred.base_url_override || "",
        key_set: !!cred.has_key,
        auth_mode: cred.auth_mode || 'api_key',
        configured: !!cred.configured,
        key_hint: cred.key_hint || t('settings.models.key_set_hint'),
        status: cred.enabled === false ? "disabled" : "configured",
        connectivity: { status: "untested" },
        enabled: cred.enabled !== false,
        proxy_url: cred.proxy_url || "",
        proxy: cred.proxy_url ? "http_proxy" : "direct",
        models: (api.models || api.entries || []).map(mapModel),
      };
      // 可见性按 configured 而非 key_set —— 免鉴权 provider 没 key 但确实配好了。
    }).filter(api => api.configured) : [];
    // 中转站: 把不在全局 catalog 里的用户自定义凭证(带 base_url)合成为 provider 行,
    // 否则保存后在列表里看不到、无法选模型。models=[] 由用户点同步从中转站拉取。
    const catalogIds = new Set((Array.isArray(list) ? list : []).map(a => normalizeApiId(catalogApiIdForCredential(a.api_id || a.id))));
    const customRows = Object.entries(credMap)
      .filter(([cid, c]) => c.configured && c.base_url_override && !catalogIds.has(normalizeApiId(cid)))
      .map(([cid, c]) => ({
        id: cid, credential_id: cid, name: cid,
        base_url: c.base_url_override, base_url_override: c.base_url_override,
        key_set: !!c.has_key, key_hint: c.key_hint || '',
        auth_mode: c.auth_mode || 'api_key', configured: true,
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
    // 演示数据不是谁的真实配置:不去拉远端(管理员带 ?demo=1 时会拿他自己的凭据真打一遍)。
    if (useMock) return null;
    // 停用的供应商不同步:后端拉取入口判断「有没有凭据」时跳过停用的行,必然失败,弹出来的是
    // 「需要先配置该 provider」—— 用户刚存过 key,这句话是错的。连通性一栏本来就显示「已停用」。
    if (api.enabled === false) {
      if (!opts.silent) {
        window.__apiToast?.(t('settings.models.sync_skip_disabled'), { kind: 'info', duration: 3000 });
      }
      return null;
    }
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
  }, [mapModel, t, useMock]);

  useEffectPL(() => {
    if (useMock) return;
    (async () => {
      try { await loadConfiguredApis(); }
      catch (_) {}
      finally { setApisLoading(false); }
    })();
  }, [useMock, loadConfiguredApis]);

  // 供应商总开关 = 用户自己这条凭据的启用态(列表里 enabled 就是从 credentials.list 读的)。
  // 以前写的是管理员专用的全局目录(models.upsertApi):普通用户 403 被吞、刷新就回弹,
  // 管理员一点则把全平台的这个供应商关了。失败回滚并提示。
  const toggleApi = async (id) => {
    const api = apis.find(a => a.id === id);
    const wasEnabled = api?.enabled !== false;
    setApis(arr => arr.map(a => a.id === id ? { ...a, enabled: !wasEnabled } : a));
    if (useMock) return;
    try {
      await window.api.credentials.setEnabled({ api_id: api?.credential_id || credentialApiIdForCatalog(id), enabled: !wasEnabled });
    } catch (e) {
      setApis(arr => arr.map(a => a.id === id ? { ...a, enabled: wasEnabled } : a));
      window.__apiToast?.(t('settings.models.provider_toggle_fail'), { kind: 'danger', detail: e?.message || '' });
      return;
    }
    // 重新打开:停用期间不同步(停用时重填的 key 也没拉过模型),这里静默补一次。
    if (!wasEnabled && api) syncRemoteModels({ ...api, enabled: true }, { silent: true });
  };
  // 单个模型启停:自己的模型(synced)走按用户的端点,内置目录模型只有管理员能改(见
  // lib/model-overlay-write.js)。以前一律打管理员专用的全局端点,普通用户 403 被吞、开关是摆设。
  const setOneModelEnabled = (apiId, mId, enabled) => setApis(arr => arr.map(a => a.id === apiId
    ? { ...a, models: a.models.map(m => m.id === mId ? { ...m, enabled, visible: enabled } : m) }
    : a));
  const toggleModel = async (apiId, mId) => {
    const api = apis.find(a => a.id === apiId);
    const m = api?.models.find(m => m.id === mId);
    const wasEnabled = m?.enabled ?? true;
    setOneModelEnabled(apiId, mId, !wasEnabled);
    // 演示数据(匿名访客 / ?demo=1)不是谁的真实配置:只在本地翻转,不发请求。
    if (useMock) return;
    try {
      await setModelEnabled(window.api, { apiId, model: m || { id: mId }, enabled: !wasEnabled, isAdmin: isAdminUser });
    } catch (e) {
      setOneModelEnabled(apiId, mId, wasEnabled);
      window.__apiToast?.(isAdminOnlyModelEdit(e) ? t('settings.models.builtin_admin_only') : t('settings.models.model_update_fail'),
        { kind: isAdminOnlyModelEdit(e) ? 'warn' : 'danger', detail: isAdminOnlyModelEdit(e) ? '' : (e?.message || '') });
    }
  };
  // 改显示名:按归属路由(见 lib/model-overlay-write.js)。以前一律打管理员端点,普通用户 403
  // 被吞,名字改了刷新又变回来。失败回滚成原名并提示。
  const setOneModelDisplay = (apiId, mId, display) => setApis(arr => arr.map(a => a.id === apiId
    ? { ...a, models: a.models.map(m => m.id === mId ? { ...m, display } : m) }
    : a));
  const renameModel = async (apiId, mId, display) => {
    const api = apis.find(a => a.id === apiId);
    const m = api?.models.find(x => x.id === mId);
    const prev = m?.display;
    setOneModelDisplay(apiId, mId, display);
    if (useMock) return;
    try {
      await renameModelWrite(window.api, { apiId, model: m || { id: mId }, display, isAdmin: isAdminUser });
    } catch (e) {
      setOneModelDisplay(apiId, mId, prev);
      window.__apiToast?.(isAdminOnlyModelEdit(e) ? t('settings.models.builtin_admin_only') : t('settings.models.model_update_fail'),
        { kind: isAdminOnlyModelEdit(e) ? 'warn' : 'danger', detail: isAdminOnlyModelEdit(e) ? '' : (e?.message || '') });
    }
  };
  const setModelVisibility = async (apiId, ids) => {
    const api = apis.find(a => a.id === apiId);
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.map(m => ({ ...m, visible: ids.includes(m.id) })) }
      : a));
    // 演示数据:只改本地。演示模型的 id 与真实目录同名,管理员带 ?demo=1 时发出去就按演示勾选
    // 改写了全局目录里真实模型的启停(影响所有用户)。
    if (useMock) return;
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
  // 手填模型 → **当前用户自己的** overlay(POST /api/me/models/model)。
  // ⚠️ 绝不能走 window.api.models.upsertModel —— 那是 admin-only 的全局 catalog 写入:
  //    普通用户直接 403「需要管理员权限」,写成功了还会把私人模型塞进所有人的目录。
  //    (v1.76.0 就是这么翻的车,已整条回滚重写。)
  // 标 synced:true 让可见性 toggle 也走 per-user 端点,与同步来的模型一致。
  const addModel = async (apiId, m) => {
    const real = String(m.id || m.real_name || "").trim();
    if (!apiId || !real) return;
    const display = (m.display || "").trim() || real;
    const row = { id: real, real_name: real, display, capabilities: m.capabilities || [],
                  enabled: true, visible: true, synced: true, health: "unknown" };
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: [...(a.models || []).filter(x => x.id !== real), row] } : a));
    if (useMock) return;  // 演示数据:只加到本地列表,不写登录用户真实的 overlay
    try {
      await window.api.models.meUpsertModel({
        api_id: credentialApiIdForCatalog(apiId), real_name: real,
        display_name: display, capabilities: row.capabilities,
      });
      window.__apiToast?.(t('settings.models.add_model_ok'), { kind: 'ok' });
    } catch (e) {
      setApis(arr => arr.map(a => a.id === apiId
        ? { ...a, models: (a.models || []).filter(x => x.id !== real) } : a));
      window.__apiToast?.(t('settings.models.add_model_fail'), { kind: 'danger', detail: e?.message || '' });
    }
  };
  // meta = 校验弹窗传回的 { base, localOnly }(diff 的对比基准),决定删目录条目还是自己的模型。
  const removeModels = async (apiId, ids, meta) => {
    if (useMock) {  // 演示数据:只在本地移除,不发请求
      setApis(arr => arr.map(a => a.id === apiId
        ? { ...a, models: a.models.filter(m => !ids.includes(m.id)) }
        : a));
      return;
    }
    const api = apis.find(a => a.id === apiId);
    // 目标按 id 构造:校验弹窗给的下线模型来自全局目录,自动同步后多半不在当前视图里,
    // 按视图过滤会把它们静默丢掉;按全局目录比时,同名的自己的模型也不能被当成删除目标
    // (见 lib/model-overlay-write.js removeTargetsFor)。
    const targets = removeTargetsFor(api?.models, ids, meta);
    // 只从列表里拿掉真删成功的;删不掉的(普通用户碰内置模型 / 请求失败)留在原位并提示,
    // 不再乐观删掉、刷新又冒出来。
    const results = await Promise.all(targets.map(m =>
      removeModel(window.api, { apiId, model: m, isAdmin: isAdminUser })
        .then(() => ({ id: m.id, ok: true }), (e) => ({ id: m.id, ok: false, adminOnly: isAdminOnlyModelEdit(e) }))
    ));
    const removed = results.filter(r => r.ok).map(r => r.id);
    setApis(arr => arr.map(a => a.id === apiId
      ? { ...a, models: a.models.filter(m => !removed.includes(m.id)) }
      : a));
    const failed = results.filter(r => !r.ok);
    if (failed.length) {
      window.__apiToast?.(failed.some(r => r.adminOnly) ? t('settings.models.builtin_admin_only') : t('settings.models.model_update_fail'),
        { kind: 'warn', detail: failed.map(r => r.id).join(', ') });
    }
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
      onAddModel={() => setAddModelApi(selectedApi.id)}
      onToggleModel={(mId) => toggleModel(selectedApi.id, mId)}
      onRenameModel={(mId, display) => renameModel(selectedApi.id, mId, display)}
      onDeleteKey={async () => {
        if (!await window.__confirm({ title: t('settings.models.delete_key_title'), message: t('settings.models.delete_key_confirm', { name: selectedApi.name }), danger: true, confirmText: t('settings.models.delete_key_btn') })) return;
        if (useMock) {  // 演示数据:只在本地移除(演示行的 id 与真实凭据同名,别删到登录用户自己的 key)
          setSelectedApiId(null);
          setApis(arr => arr.map(a => a.id === selectedApi.id ? { ...a, key_set: false, key_hint: '—' } : a));
          return;
        }
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
          // 演示数据:只改本地那一行,不写凭据、不写全局目录(管理员带 ?demo=1 时会写到真东西上)。
          if (useMock) {
            const key = (payload.api_key || '').trim();
            setApis(arr => {
              const patch = {
                name: payload.name || cfg?.name || catalogId,
                base_url: payload.base_url || '',
                proxy: payload.proxy || 'direct',
                proxy_url: payload.proxy === 'http_proxy' ? (payload.proxy_url || '').trim() : '',
                key_set: true,
                ...(key ? { key_hint: key.slice(-4) } : {}),
              };
              if (arr.some(a => a.id === catalogId)) return arr.map(a => a.id === catalogId ? { ...a, ...patch } : a);
              return [...arr, { id: catalogId, credential_id: credentialId, status: 'configured', enabled: true,
                connectivity: { status: 'untested' }, models: [], ...patch }];
            });
            setSelectedApiId(catalogId);
            setEditingApi(null); setAddingApi(false);
            return;
          }
          // 中转站: 普通用户也可添加自定义 OpenAI 兼容端点 — 后端 me.py 放行未知
          // provider(必带 base_url) + set_credential 的 _validate_base_url 做 SSRF 防护。
          try {
            // 只写**当前用户自己的**凭据(credentials.set):接口地址 = 凭据的 base_url_override,
            // 连接方式 / 代理也是凭据字段。管理员同样如此 —— 以前管理员在这里改 Base URL 或新增
            // 自建中转站,会顺手 upsertApi 写全局目录:改地址 = 把平台里这个供应商的默认地址换成他的
            // 中转站,所有没配个人地址的用户连同自己的 key 都被发到那里;新增中转站 = 在全局目录里
            // 建一行。而同一次保存本来就会把地址写进管理员自己的凭据,写全局那一步对他本人是多余的。
            // 平台默认地址来自内置目录种子,不在设置页维护。
            const existing = apis.find(a => a.id === catalogId);
            const keyProvided = !!(payload.api_key && payload.api_key.trim());
            // 连接方式:选了 HTTP 代理才带代理地址,选直连 = 空串(= 清掉已存代理)。
            const proxyUrl = payload.proxy === 'http_proxy' ? (payload.proxy_url || '').trim() : '';
            // key 从不回显:「编辑」只改接口地址、不重填 key 时,base_url 也必须落库。
            // 否则改 URL 保存后毫无变化(必须删 key 重填才生效)——这正是本次上报的 bug。
            const baseUrlChanged = !addingApi && existing && ((payload.base_url || '') !== (existing.base_url || ''));
            // 同理「只改连接方式 / 代理地址」也要落库。以前这种保存前端什么都不发,却提示保存成功
            // (反馈 #107:本地版的出路正是在连接方式里配 HTTP 代理,结果配了也存不上)。
            const proxyChanged = !addingApi && existing && (proxyUrl !== (existing.proxy_url || ''));
            // 免鉴权(本地/自托管)= 显式选项:即使一个字符的 key 都没填也必须落库,
            // 否则「勾了免 Key + 填了地址」保存后什么都没发生(和不勾一模一样)。
            const noAuth = !!payload.no_auth;
            // 写凭据只有一处请求:「带 key」和「keep_key 只改地址 / 代理」共用,失败交给下面的 catch
            // 单条报错(带 detail)。设置页不再写全局目录,也就不存在「元数据已保存但 key 写入失败」
            // 这种半截状态。
            let credBody = null;
            if (keyProvided || noAuth) {
              credBody = {
                api_id: credentialId, api_key: keyProvided ? payload.api_key.trim() : '',
                base_url_override: payload.base_url || '',
                proxy: proxyUrl,
                no_auth: noAuth,
              };
            } else if ((baseUrlChanged || proxyChanged) && existing.key_set) {
              // keep_key:保留已存密钥,只更新 base_url_override 与连接方式(代理)。
              // 地址没改时回写凭据里原有的覆盖值(常为空 = 跟随目录),只动代理。
              credBody = {
                api_id: credentialId, api_key: '',
                base_url_override: baseUrlChanged ? (payload.base_url || '') : (existing.base_url_override || ''),
                keep_key: true,
                proxy: proxyUrl,
              };
            }
            // 编辑已有供应商:把列表上那个开关的当前状态一起发回去。后端没收到 enabled 时按「启用」
            // 落库(手机端 / iOS / 首配弹窗这些没有开关的地方,重填 key 就该能用),所以在这里关掉
            // 某个供应商、再改一下代理或重填 key,它会被悄悄打开。
            if (credBody && !addingApi && existing) credBody.enabled = existing.enabled !== false;
            if (credBody) await window.api.credentials.set(credBody);
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
            // 停用的供应商保存后不同步:后端这时也没拉模型(停用的凭据拉不到,不能因此清掉用户
            // 的模型清单),前端再同步只会弹一条错误的「需要先配置该 provider」。
            if (row.enabled !== false) await syncRemoteModels(row, { silent: false });
          } catch (e) {
            // detail 常是后端一整段可执行说明(如「云端连不到本地模型,请用桌面版…」),
            // 默认 2.4s 读不完 → 给失败态更长停留。
            window.__apiToast?.(t('settings.edit_api.save_fail'), { kind: "danger", detail: e?.message, duration: 9000 });
            // 失败也回读一次后端真实状态:请求超时 ≠ 没存上(反馈 #107:前端先超时报失败,
            // 后端随后把 key 落了库,列表却一直显示「还没有配置」)。
            try { await loadConfiguredApis(); } catch (_) {}
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
        isAdminUser={isAdminUser}
        demo={useMock}
        onSyncRemote={() => syncRemoteModels(apis.find(a => a.id === validateApi), { silent: false })}
        onClose={() => setValidateApi(null)}
        onConfirm={(toRemove, meta) => { removeModels(validateApi, toRemove, meta); setValidateApi(null); }}
      />
      <AddModelModal
        open={!!addModelApi}
        api={apis.find(a => a.id === addModelApi)}
        onClose={() => setAddModelApi(null)}
        onConfirm={(m) => { addModel(addModelApi, m); setAddModelApi(null); }}
      />
    </CSSpaceBetween>
  );
}

export {
  ModelsSection,
  ApiModelsList,
  AddModelModal,
  EditApiModal,
  ValidateModal,
  VisibilityModal,
  ProviderCard,
  ProviderConfigSection,
  ModelNameCell,
  HealthDot,
  MODELS_DATA,
  PROVIDERS_CONFIG,
  normalizeApiId,
};
