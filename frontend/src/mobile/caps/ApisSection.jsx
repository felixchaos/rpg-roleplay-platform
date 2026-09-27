/* Extracted from pages/MobileCaps.jsx — mechanical split, byte-for-byte. */
import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { Sheet } from '../Sheet.jsx';
import { MField, EmptyState } from './shared.jsx';

/* ──────────────────────────────────────────────────────────────────
   APIS — BYOK 凭证管理
   ────────────────────────────────────────────────────────────────── */
function ApisSection({ toast }) {
  const { t } = useTranslation();
  const [creds, setCreds] = useState({});       // api_id → { key_set, key_hint }
  const [providers, setProviders] = useState([]); // from /api/models catalog
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [editProv, setEditProv] = useState(null); // provider being edited
  const [keyVal, setKeyVal] = useState('');
  const [saveBusy, setSaveBusy] = useState(false);
  const [testResult, setTestResult] = useState(null); // { id, ok, message }

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const [credsRes, modelsRes] = await Promise.all([
        window.api.credentials.list().catch(() => ({ items: [] })),
        window.api.models.list().catch(() => null),
      ]);
      const credMap = {};
      for (const c of (credsRes?.items || credsRes?.credentials || [])) {
        credMap[c.api_id || c.id] = c;
      }
      setCreds(credMap);

      const apis = (modelsRes && modelsRes.apis) || [];
      setProviders(apis.filter(a => a.id !== 'local' && a.id !== 'builtin'));
    } catch (e) {
      setErr(e?.message || t('mobile.caps.error.load_failed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const openEdit = (prov) => {
    setEditProv(prov);
    setKeyVal('');
    setTestResult(null);
  };

  const handleSave = async () => {
    if (!keyVal.trim()) { toast(t('mobile.caps.apis.form.key_required'), 'warn'); return; }
    setSaveBusy(true);
    try {
      // 不带 proxy 键:手机端没有连接方式输入,后端据此保留在别处配好的代理。
      await window.api.credentials.set({ api_id: editProv.id, api_key: keyVal.trim() });
      toast(t('mobile.caps.apis.toast.key_saved'), 'ok');
      setEditProv(null);
      load();
    } catch (e) {
      toast(t('mobile.caps.toast.save_failed') + ': ' + (e?.message || ''), 'danger');
      // 请求超时 ≠ 没存上:回读后端真实状态,别让已落库的 key 显示成未配置。
      load();
    } finally {
      setSaveBusy(false);
    }
  };

  const handleRemove = async (prov) => {
    if (!await window.__confirm({ message: t('mobile.caps.apis.confirm.delete_key', { name: prov.name || prov.id }), danger: true })) return;
    try {
      await window.api.credentials.remove({ api_id: prov.id });
      toast(t('mobile.caps.toast.deleted'), 'ok');
      load();
    } catch (e) {
      toast(t('mobile.caps.toast.delete_failed', { msg: e?.message || '' }), 'danger');
    }
  };

  const handleTest = async (prov) => {
    setTestResult({ id: prov.id, busy: true });
    try {
      const r = await window.api.credentials.test({ api_id: prov.id });
      setTestResult({ id: prov.id, ok: r?.ok !== false, message: r?.message || (r?.ok !== false ? t('mobile.caps.apis.test.ok') : t('mobile.caps.apis.test.failed')) });
    } catch (e) {
      setTestResult({ id: prov.id, ok: false, message: e?.message || t('mobile.caps.apis.test.request_failed') });
    }
  };

  const credOf = (prov) => creds[prov.id] || creds[prov.id?.toLowerCase()] || null;
  const isSet = (prov) => !!(credOf(prov)?.key_set);

  return (
    <>
      <div className="pl-pad">
        <div className="pl-sec-head" style={{ marginBottom: 4 }}>
          <h2 style={{ margin: 0 }}>{t('mobile.caps.apis.title')}</h2>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6, marginBottom: 16 }}>
          {t('mobile.caps.apis.description')}
        </div>
        {err && (
          <div style={{ marginBottom: 14, padding: '12px 14px', borderRadius: 12, background: 'var(--danger-soft)', border: '1px solid rgba(200,103,93,0.3)', color: 'var(--danger)', fontSize: 13 }}>
            {err}
          </div>
        )}
        {loading && providers.length === 0 ? (
          <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '40px 0' }}>{t('common.loading')}</div>
        ) : providers.length === 0 ? (
          <EmptyState icon="braces" titleKey="mobile.caps.apis.empty_title" descKey="mobile.caps.apis.empty_desc" />
        ) : (
          <div className="pl-group">
            {providers.map((prov, idx) => {
              const set = isSet(prov);
              const cred = credOf(prov);
              const tr = testResult?.id === prov.id ? testResult : null;
              return (
                <div key={prov.id} style={{ padding: '13px 14px', borderBottom: idx < providers.length - 1 ? '1px solid var(--line-soft)' : 'none', display: 'grid', gap: 7 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div className="pl-prov-logo">
                      {(prov.name || prov.id || '?').slice(0, 2).toUpperCase()}
                    </div>
                    <div className="pl-prov-id" style={{ flex: 1, minWidth: 0 }}>
                      <strong>{prov.name || prov.id}</strong>
                      <div className="key mono">{set ? (cred?.key_hint ? `…${cred.key_hint}` : t('mobile.caps.apis.key_set')) : t('mobile.caps.apis.key_unset')}</div>
                    </div>
                    <div style={{ display: 'flex', gap: 6, flex: 'none' }}>
                      {set && (
                        <button onClick={() => handleTest(prov)} style={{ padding: '5px 9px', borderRadius: 8, border: '1px solid var(--line-soft)', background: 'var(--panel-2)', color: 'var(--muted)', fontSize: 11.5 }}>
                          {tr?.busy ? '…' : t('mobile.caps.apis.test_btn')}
                        </button>
                      )}
                      <button onClick={() => openEdit(prov)} style={{ padding: '5px 9px', borderRadius: 8, border: '1px solid var(--accent-edge)', background: 'var(--accent-soft)', color: 'var(--accent)', fontSize: 11.5 }}>
                        {set ? t('mobile.caps.apis.replace_btn') : t('mobile.caps.apis.set_btn')}
                      </button>
                    </div>
                  </div>
                  {tr && !tr.busy && (
                    <div style={{ fontSize: 12, padding: '7px 10px', borderRadius: 8, background: tr.ok ? 'var(--ok-soft)' : 'var(--danger-soft)', color: tr.ok ? 'var(--ok)' : 'var(--danger)', border: `1px solid ${tr.ok ? 'rgba(126,184,142,0.3)' : 'rgba(200,103,93,0.3)'}` }}>
                      {tr.ok ? '✓' : '✗'} {tr.message}
                    </div>
                  )}
                  {set && (
                    <button onClick={() => handleRemove(prov)} style={{ alignSelf: 'start', fontSize: 11.5, color: 'var(--danger)', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}>
                      {t('mobile.caps.apis.delete_key_btn')}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Edit Key Sheet */}
      <Sheet open={!!editProv} title={t('mobile.caps.apis.sheet.title', { name: editProv?.name || editProv?.id || '' })} hint="POST /api/v1/me/credentials" onClose={() => setEditProv(null)} zIndex={70} maxHeight="88%">
        <div style={{ padding: '4px 4px 8px' }}>
          <MField label="API Key" desc={isSet(editProv || {}) ? t('mobile.caps.apis.form.key_desc_existing') : t('mobile.caps.apis.form.key_desc_new')}>
            <input
              className="pl-input"
              type="password"
              placeholder={isSet(editProv || {}) ? t('mobile.caps.apis.form.key_placeholder_existing') : 'sk-…'}
              autoComplete="new-password"
              value={keyVal}
              onChange={e => setKeyVal(e.target.value)}
              style={{ fontSize: 16 }}
            />
          </MField>
          <div className="sheet-actions" style={{ marginTop: 8 }}>
            <button className="sheet-btn" onClick={() => setEditProv(null)}>{t('common.cancel')}</button>
            <button className="sheet-btn primary" onClick={handleSave} disabled={saveBusy || !keyVal.trim()}>
              {saveBusy ? t('mobile.caps.apis.form.saving') : t('common.save')}
            </button>
          </div>
        </div>
      </Sheet>
    </>
  );
}

export { ApisSection };
