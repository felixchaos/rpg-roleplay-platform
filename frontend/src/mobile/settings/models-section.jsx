import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { normalizeProviderId, credentialToCatalogId, catalogToCredentialId } from '../../components/catalog-helpers.js';
import { Toggle } from './shared.jsx';
import { useReactiveUser } from '../../platform-app.jsx';
import { setModelEnabled, isAdminOnlyModelEdit } from '../../lib/model-overlay-write.js';

const normId = normalizeProviderId;
const credId = catalogToCredentialId;
const catId = credentialToCatalogId;

/* 供应商详情子视图 */
function ProviderDetail({ api, onBack, onSync, onToggleModel, onDeleteKey, nav }) {
  const { t } = useTranslation();
  const [showModels, setShowModels] = useState(true);
  const conn = api.connectivity || {};
  const enabledCount = api.models.filter(m => m.enabled).length;

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>{api.name}</strong>
          <span className="sub">{t('mobile.settings.models.provider_byok')}</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={onSync} title={t('mobile.settings.models.sync_models')}><Icon name="refresh" size={17} /></button>
          <button className="pl-headbtn" onClick={onDeleteKey} title={t('mobile.settings.models.delete_key')} style={{ color: 'var(--danger)' }}><Icon name="trash" size={17} /></button>
        </div>
      </div>
      <div className="pl-body">
        <div className="pl-pad">
          {/* 供应商信息 */}
          <div className="pl-card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Provider ID</span>
                <span className="mono" style={{ color: 'var(--text-quiet)' }}>{api.id}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Base URL</span>
                <span className="mono" style={{ color: 'var(--text-quiet)', fontSize: 11, maxWidth: '60%', textAlign: 'right', wordBreak: 'break-all' }}>{api.base_url || '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>API Key</span>
                <span className="mono" style={{ color: 'var(--text-quiet)' }}>•••• {api.key_hint}</span>
              </div>
              {api.enabled===false && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--muted)' }}>{t('mobile.settings.models.status')}</span>
                  <span style={{ color: 'var(--muted-2)', fontSize: 12, fontWeight: 600 }}>{t('common.disabled')}</span>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>{t('mobile.settings.models.connectivity')}</span>
                <span style={{
                  color: conn.status==='ok' ? 'var(--ok)' : conn.status==='err' ? 'var(--danger)' : 'var(--muted)',
                  fontSize: 12
                }}>
                  {conn.status==='ok' ? `✓ ${t('mobile.settings.models.conn_ok')}${conn.latency_ms ? ` · ${conn.latency_ms}ms` : ''}` :
                   conn.status==='err' ? `✗ ${t('common.error')}` :
                   conn.status==='checking' ? t('mobile.settings.models.conn_syncing') : t('mobile.settings.models.conn_untested')}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>{t('mobile.settings.models.model_count')}</span>
                <span style={{ color: 'var(--text-quiet)' }}>{t('mobile.settings.models.model_count_value', { enabled: enabledCount, total: api.models.length })}</span>
              </div>
            </div>
          </div>

          {/* 模型列表 */}
          <div className="pl-sec">
            <div className="pl-sec-head">
              <h2>{t('mobile.settings.models.models_heading', { count: api.models.length })}</h2>
              <button className="act" onClick={() => setShowModels(v => !v)}>
                {showModels ? t('mobile.settings.common.collapse') : t('mobile.settings.common.expand')} <Icon name={showModels ? 'chevron_up' : 'chevron_down'} size={13} />
              </button>
            </div>
            {showModels && (
              <div className="pl-group">
                {api.models.length === 0 && (
                  <div style={{ padding: '18px 14px', color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>
                    {t('mobile.settings.models.no_models_hint')}
                  </div>
                )}
                {api.models.map(m => (
                  <div key={m.id} className="pl-setrow">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 'none' }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                        background: m.health==='ok' ? 'var(--ok)' : m.health==='err' ? 'var(--danger)' : 'var(--muted-3)'
                      }} />
                    </div>
                    <div className="pl-setrow-tx">
                      <strong style={{ fontSize: 13 }}>{m.display}</strong>
                      <span className="mono">{m.real_name}</span>
                    </div>
                    <Toggle on={m.enabled} onChange={() => onToggleModel(m.id)} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/* models section 需要直接渲染(含内部子视图切换),包装一层让 onBack 工作 */
function ModelsSection({ nav, onBack }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState(null);
  const [apis, setApis] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState('');
  const autoSynced = useRef(new Set());
  const user = useReactiveUser();
  const isAdmin = !!(user && user.role === 'admin');

  const mapModel = useCallback((m) => ({
    id: m.real_name || m.id,
    display: m.display_name || m.real_name || m.id,
    real_name: m.real_name || m.id,
    enabled: m.enabled !== false,
    visible: m.hidden !== true,
    // 用户自己的模型(同步来的 / 手填的):启停走按用户的端点(见 lib/model-overlay-write.js)
    synced: m.synced === true,
    capabilities: m.capabilities || {},
    health: m.health || 'untested',
    health_latency_ms: m.health_latency_ms,
  }), []);

  const load = useCallback(async () => {
    const [data, creds] = await Promise.all([
      window.api.models.list(),
      window.api.credentials.list().catch(() => ({ items:[] })),
    ]);
    const credMap = {};
    for (const c of (creds?.items || creds?.credentials || [])) {
      const cid = normId(c.api_id || c.id);
      credMap[cid] = { has_key: !!(c.has_credential||c.has_key||c.key_hint), key_hint: c.key_hint||'', enabled: c.enabled!==false, base_url_override: c.base_url_override||'', proxy_url: c.proxy_url || '' };
    }
    const list = data?.models?.apis || data?.apis || [];
    const rows = Array.isArray(list) ? list.map(api => {
      const cataId = catId(api.api_id || api.id);
      const criId = credId(cataId);
      const cred = credMap[criId] || credMap[normId(cataId)] || {};
      return {
        id: cataId, credential_id: criId,
        name: api.display_name || api.name || cataId,
        // 用户自己的 base_url_override(中转站)优先;非 admin 的 api.base_url 已被后端 redact 成空。
        base_url: cred.base_url_override || api.base_url || '',
        key_set: !!cred.has_key, key_hint: cred.key_hint || '—',
        connectivity: { status:'untested' },
        // proxy 从凭据读(api.proxy 对非 admin 恒 undefined);后端 list_credentials 返回 proxy_url。
        enabled: cred.enabled !== false, proxy: cred.proxy_url ? 'http_proxy' : 'direct',
        models: (api.models || api.entries || []).map(mapModel),
      };
    }).filter(a => a.key_set) : [];
    // 中转站 / 自定义供应商:把不在全局 catalog 里、但带 base_url_override 的用户凭据合成为
    // provider 行,否则保存后在列表里看不到、无法选模型;models=[] 由用户点同步从中转站拉取。
    const catalogIds = new Set((Array.isArray(list) ? list : []).map(a => normId(catId(a.api_id || a.id))));
    const customRows = Object.entries(credMap)
      .filter(([cid, c]) => c.has_key && c.base_url_override && !catalogIds.has(normId(cid)))
      .map(([cid, c]) => ({
        id: cid, credential_id: cid, name: cid,
        base_url: c.base_url_override, key_set: true, key_hint: c.key_hint || '—',
        connectivity: { status:'untested' },
        enabled: c.enabled !== false, proxy: c.proxy_url ? 'http_proxy' : 'direct',
        models: [], _custom: true,
      }));
    const allRows = [...rows, ...customRows];
    setApis(allRows);
    return allRows;
  }, [mapModel]);

  const syncRemote = useCallback(async (api, silent=false) => {
    if (!api) return;
    // 停用的供应商不同步:后端拉取入口跳过停用的凭据,必然失败,列表上会挂一个「错误」、
    // 手动刷新还弹「需要先配置该 provider」(用户明明配过 key)。卡片上已经标了「已停用」。
    if (api.enabled === false) {
      if (!silent) nav.toast(t('mobile.settings.models.sync_skip_disabled'), 'warn', 'warn');
      return;
    }
    const aId = catId(api.id);
    setApis(arr => arr.map(a => a.id===aId ? { ...a, connectivity: { ...a.connectivity, status:'checking' } } : a));
    try {
      const r = await window.api.models.syncRemote({ api_id: aId, base_url: api.base_url||'' });
      if (!r?.ok) throw new Error(r?.error||'sync failed');
      const models = (r.models||[]).map(mapModel);
      setApis(arr => arr.map(a => a.id===aId ? {
        ...a, models,
        connectivity: { status:'ok', latency_ms:r.latency_ms, remote_total:r.remote_total??models.length },
      } : a));
      if (!silent) nav.toast(t('mobile.settings.models.sync_done', { count: models.length }), 'ok', 'refresh');
    } catch (e) {
      setApis(arr => arr.map(a => a.id===aId ? { ...a, connectivity: { status:'err', error:e?.message||'' } } : a));
      if (!silent) nav.toast(t('mobile.settings.models.sync_failed', { msg: e?.message||'' }), 'danger', 'warn');
    }
  }, [mapModel, nav, t]);

  const reload = useCallback(async () => {
    try { setLoadErr(''); await load(); }
    catch (e) { setLoadErr(e?.message || t('mobile.settings.models.load_failed')); }
    finally { setLoading(false); }
  }, [load, t]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (loading) return;
    apis.forEach(api => {
      if (autoSynced.current.has(api.id)) return;
      autoSynced.current.add(api.id);
      syncRemote(api, true);
    });
  }, [loading, apis, syncRemote]);

  const selectedApi = apis.find(a => a.id===selected) || null;

  /* 供应商详情 */
  if (selectedApi) {
    return (
      <ProviderDetail
        api={selectedApi}
        onBack={() => setSelected(null)}
        onSync={() => syncRemote(selectedApi)}
        nav={nav}
        onToggleModel={async (mId) => {
          const m = selectedApi.models.find(x => x.id===mId);
          const prev = !!m?.enabled;
          setApis(arr => arr.map(a => a.id===selectedApi.id ? { ...a, models: a.models.map(x => x.id===mId ? { ...x, enabled:!x.enabled } : x) } : a));
          try {
            // 自己的模型走按用户的端点,内置目录模型只有管理员能改;以前一律打管理员专用的
            // 全局端点,普通用户开关一个自己同步来的模型也 403。
            await setModelEnabled(window.api, { apiId: selectedApi.id, model: m || { id: mId }, enabled: !prev, isAdmin });
          } catch (e) {
            setApis(arr => arr.map(a => a.id===selectedApi.id ? { ...a, models: a.models.map(x => x.id===mId ? { ...x, enabled: prev } : x) } : a));
            nav.toast((isAdminOnlyModelEdit(e) || e?.status===403) ? t('mobile.settings.models.admin_only') : t('mobile.settings.models.save_failed', { msg: e?.message||'' }), 'danger', 'warn');
          }
        }}
        onDeleteKey={async () => {
          if (!await window.__confirm({ message: t('mobile.settings.models.delete_key_confirm', { name: selectedApi.name }), danger: true })) return;
          try {
            await window.api.credentials.remove({ api_id: credId(selectedApi.id) });
            setSelected(null);
            setApis(arr => arr.filter(a => a.id!==selectedApi.id));
            nav.toast(t('mobile.settings.models.key_deleted'), 'ok', 'trash');
          } catch (e) { nav.toast(t('mobile.settings.models.delete_failed', { msg: e?.message||'' }), 'danger', 'warn'); }
        }}
      />
    );
  }

  /* 供应商列表 */
  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>{t('mobile.settings.section.models.label')}</strong>
          <span className="sub">{t('mobile.settings.models.provider_count', { count: apis.length })}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && (
            <div className="pl-empty">
              <div className="ic"><Icon name="cpu" size={22} /></div>
              <p>{t('common.loading')}</p>
            </div>
          )}
          {!loading && loadErr && (
            <div className="pl-empty">
              <div className="ic"><Icon name="warn" size={22} /></div>
              <h3>{t('mobile.settings.models.load_failed')}</h3>
              <p>{loadErr}</p>
              <button className="pl-btn-ghost" style={{ marginTop:12, height:38, fontSize:13, width:'auto', padding:'0 18px' }}
                onClick={() => { setLoading(true); setLoadErr(''); reload(); }}>{t('mobile.settings.models.retry')}</button>
            </div>
          )}
          {!loading && !loadErr && apis.length===0 && (
            <div className="pl-empty">
              <div className="ic"><Icon name="key" size={22} /></div>
              <h3>{t('mobile.settings.models.no_providers')}</h3>
              <p>{t('mobile.settings.models.no_providers_hint')}</p>
            </div>
          )}
          {!loading && !loadErr && apis.map(pv => {
            const conn = pv.connectivity || {};
            const enabledCnt = pv.models.filter(m => m.enabled).length;
            const statusOk = conn.status==='ok';
            const statusErr = conn.status==='err';
            const statusBusy = conn.status==='checking';
            return (
              <button key={pv.id} className="pl-prov" style={{ width:'100%', textAlign:'left', marginBottom:10 }}
                onClick={() => setSelected(pv.id)}>
                <div className="pl-prov-head">
                  <span className="pl-prov-logo">{pv.name.slice(0,1)}</span>
                  <span className="pl-prov-id">
                    <strong>
                      {pv.name}
                      {pv.enabled===false && <span style={{ marginLeft:6, fontSize:11, fontWeight:600, color:'var(--muted-2)', border:'1px solid var(--line-soft)', borderRadius:4, padding:'1px 6px' }}>{t('common.disabled')}</span>}
                    </strong>
                    <span className="key mono">•••• {pv.key_hint}</span>
                  </span>
                  <span className={`pl-status ${statusOk?'online':''}`}>
                    <span className="d" style={statusErr ? { background:'var(--danger)' } : statusBusy ? { background:'var(--warn)' } : {}} />
                    {statusOk ? `✓ ${conn.latency_ms||''}${conn.latency_ms?'ms':t('mobile.settings.models.status_connected')}` :
                     statusErr ? `✗ ${t('common.error')}` : statusBusy ? t('mobile.settings.models.status_syncing') : t('mobile.settings.models.conn_untested')}
                  </span>
                  <Icon name="chevron_right" size={16} style={{ color:'var(--muted-3)', marginLeft:4 }} />
                </div>
                {pv.models.slice(0,2).map(m => (
                  <div key={m.id} className="pl-model-row">
                    <span className="mname" style={{ color: m.enabled ? 'var(--text)' : 'var(--muted-2)' }}>{m.display}</span>
                    <span className="mmeta" style={{ color: m.health==='ok'?'var(--ok)':m.health==='err'?'var(--danger)':'var(--muted-3)' }}>
                      {m.health==='ok' ? '✓' : m.health==='err' ? '✗' : '?'}
                    </span>
                  </div>
                ))}
                {pv.models.length > 0 && (
                  <div className="pl-model-row" style={{ justifyContent:'flex-end', color:'var(--muted-2)', fontSize:11 }}>
                    {t('mobile.settings.models.enabled_count', { enabled: enabledCnt, total: pv.models.length })}
                    {pv.models.length > 2 && ` · +${pv.models.length-2}`}
                  </div>
                )}
              </button>
            );
          })}
          <div style={{ padding:'8px 0', fontSize:12, color:'var(--muted)', textAlign:'center', lineHeight:1.6 }}>
            {t('mobile.settings.models.add_provider_hint')}
          </div>
        </div>
      </div>
    </>
  );
}

export { ProviderDetail, ModelsSection };
