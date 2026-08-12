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
        base_url: c.base_url_override, key_set: !!c.has_key, key_hint: c.key_hint || '',
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
      onAddModel={() => setAddModelApi(selectedApi.id)}
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
            const keyProvided = !!(payload.api_key && payload.api_key.trim());
            // key 从不回显:「编辑」只改接口地址、不重填 key 时,base_url 也必须落库。
            // 否则改 URL 保存后毫无变化(必须删 key 重填才生效)——这正是本次上报的 bug。
            const baseUrlChanged = !addingApi && existing && ((payload.base_url || '') !== (existing.base_url || ''));
            // 免鉴权(本地/自托管)= 显式选项:即使一个字符的 key 都没填也必须落库,
            // 否则「勾了免 Key + 填了地址」保存后什么都没发生(和不勾一模一样)。
            const noAuth = !!payload.no_auth;
            if (keyProvided || noAuth) {
              try {
                await window.api.credentials.set({
                  api_id: credentialId, api_key: keyProvided ? payload.api_key.trim() : '',
                  base_url_override: payload.base_url || '',
                  proxy: payload.proxy === 'http_proxy' ? (payload.proxy_url || '').trim() : '',
                  no_auth: noAuth,
                });
              } catch (e) {
                window.__apiToast?.(t('settings.edit_api.key_save_fail'), { kind: "warn", detail: e?.message, duration: 4000 });
                throw e;
              }
            } else if (baseUrlChanged && existing.key_set) {
              // 保留已存密钥与 proxy,只更新 base_url_override(keep_key)。
              try {
                await window.api.credentials.set({
                  api_id: credentialId, api_key: '',
                  base_url_override: payload.base_url || '', keep_key: true,
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
