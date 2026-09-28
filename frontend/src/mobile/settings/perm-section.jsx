import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { SetGroup, usePrefSave } from './shared.jsx';

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 权限 (permissions)                                         */
/* ────────────────────────────────────────────────────────────────── */
// 只保留后端真的会读的默认权限模式。「高风险字段白名单」「自定义白名单」没有任何后端读方
// (写入闸在完全访问模式下一律放行,没有按字段弹确认的机制),已下线 —— 与 web / iOS 同批,
// 理由见 components/settings/perm-section.jsx。

function PermissionsSection({ nav }) {
  const { t } = useTranslation();
  const save = usePrefSave('perm');
  const [mode, setMode] = useState('review');
  // 审计日志
  const [auditEntries, setAuditEntries] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditErr, setAuditErr] = useState('');
  const [auditFilter, setAuditFilter] = useState('all');
  const [showAudit, setShowAudit] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const v = p['perm.default_mode'] || p.default_perm_mode;
        if (v) setMode(v);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const loadAudit = useCallback(async () => {
    setAuditLoading(true); setAuditErr('');
    try {
      const s = await window.api.game.state();
      const perms = (s && (s.permissions || s.state?.permissions)) || {};
      const log = Array.isArray(perms.audit_log) ? perms.audit_log : [];
      setAuditEntries(log.slice().reverse());
    } catch (e) { setAuditErr(e?.message || t('mobile.settings.perm.load_failed')); }
    finally { setAuditLoading(false); }
  }, []);

  const KIND_META = {
    write:            { label: t('mobile.settings.perm.kind_write'), color:'var(--ok)' },
    parse_error:      { label: t('mobile.settings.perm.kind_parse_error'), color:'var(--warn)' },
    rejected:         { label: t('mobile.settings.perm.kind_rejected'), color:'var(--danger)' },
    hard_forbidden:   { label: t('mobile.settings.perm.kind_hard_forbidden'), color:'var(--danger)' },
    extractor_error:  { label: t('mobile.settings.perm.kind_extractor_error'), color:'var(--warn)' },
    set_parser_error: { label: t('mobile.settings.perm.kind_set_parser_error'), color:'var(--warn)' },
    clarify_yield:    { label: t('mobile.settings.perm.kind_clarify_yield'), color:'var(--ok)' },
    acceptance_unmet: { label: t('mobile.settings.perm.kind_acceptance_unmet'), color:'var(--warn)' },
    question_skip:    { label: t('mobile.settings.perm.kind_question_skip'), color:'var(--muted)' },
  };
  const filteredAudit = auditFilter==='all' ? auditEntries : auditEntries.filter(e => e.kind===auditFilter);

  return (
    <>
      {/* 默认权限模式 */}
      <SetGroup title={t('mobile.settings.perm.gm_write_perm')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.perm.default_mode')}</strong>
            <span>{t('mobile.settings.perm.default_mode_desc')}</span>
          </div>
        </div>
        <div style={{ padding: '8px 13px 13px' }}>
          <div className="pl-seg2">
            {[['default',t('mobile.settings.perm.mode_default')],['review',t('mobile.settings.perm.mode_review')],['full_access',t('mobile.settings.perm.mode_full_access')]].map(([id, l]) => (
              <button key={id} className={mode===id?'active accent':''} onClick={() => { setMode(id); save('default_mode',id); }}>{l}</button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 8, lineHeight: 1.5 }}>
            {mode==='review' ? t('mobile.settings.perm.mode_review_hint') : mode==='full_access' ? t('mobile.settings.perm.mode_full_access_hint') : t('mobile.settings.perm.mode_default_hint')}
          </div>
        </div>

      </SetGroup>

      {/* 审计日志 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head">
          <h2>{t('mobile.settings.perm.audit_log')}</h2>
          <button className="act" onClick={() => { if (!showAudit) loadAudit(); setShowAudit(v => !v); }}>
            {showAudit ? t('mobile.settings.common.collapse') : t('mobile.settings.common.expand')} <Icon name={showAudit ? 'chevron_up' : 'chevron_down'} size={13} />
          </button>
        </div>
        {showAudit && (
          <div style={{ fontSize: 11, color: 'var(--muted-2)', padding: '0 0 8px', lineHeight: 1.5 }}>
            {t('mobile.settings.perm.audit_scope_note', '仅显示最近活动会话的操作记录，无活动游戏时可能为空。')}
          </div>
        )}
        {showAudit && (
          <div className="pl-card" style={{ padding: 12 }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
              <button className="pl-btn-ghost" style={{ height: 36, fontSize: 12, flex: 1 }}
                disabled={auditLoading} onClick={loadAudit}>
                <Icon name="refresh" size={13} /> {auditLoading ? t('common.loading') : t('mobile.settings.perm.refresh_log')}
              </button>
            </div>
            {auditErr && <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 8 }}>{auditErr}</div>}

            {/* 类型筛选 */}
            {auditEntries.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                {['all', ...Object.keys(KIND_META)].map(k => {
                  const count = k==='all' ? auditEntries.length : auditEntries.filter(e => e.kind===k).length;
                  if (k!=='all' && count===0) return null;
                  return (
                    <button key={k} onClick={() => setAuditFilter(k)}
                      className={auditFilter===k ? 'pill accent' : 'pill'}
                      style={{ cursor: 'pointer', fontSize: 10.5, height: 26, transition: 'all .15s' }}>
                      {k==='all' ? t('common.all') : (KIND_META[k]?.label || k)} · {count}
                    </button>
                  );
                })}
              </div>
            )}

            {auditEntries.length===0 && !auditLoading && (
              <div style={{ fontSize: 12.5, color: 'var(--muted)', textAlign: 'center', padding: '16px 0' }}>
                {t('mobile.settings.perm.no_audit_log')}
              </div>
            )}

            {filteredAudit.slice(0, 30).map((e, i) => {
              const meta = KIND_META[e.kind] || { label: e.kind, color: 'var(--muted)' };
              const detail = e.path
                ? `${e.path} = ${typeof e.value==='string' ? e.value : JSON.stringify(e.value)}`
                : (e.raw_spec || e.hint || '—');
              return (
                <div key={i} style={{
                  padding: '8px 0', borderBottom: '1px solid var(--line-soft)',
                  fontSize: 11.5, display: 'grid', gap: 3,
                }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', padding: '2px 7px',
                      borderRadius: 5, border: `1px solid ${meta.color}`, color: meta.color,
                      fontSize: 10.5, flexShrink: 0,
                    }}>{meta.label}</span>
                    <span className="mono" style={{ color: 'var(--muted-2)', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(e.ts||'').replace('T',' ').slice(0, 16)}
                    </span>
                    {e.source && <span style={{ color: 'var(--muted-2)', fontSize: 10 }}>{e.source}</span>}
                  </div>
                  <div style={{ color: 'var(--text-quiet)', lineHeight: 1.4, wordBreak: 'break-word' }}>{detail}</div>
                  {e.hint && e.path && <div style={{ color: 'var(--muted-2)', fontSize: 10.5 }}>· {e.hint}</div>}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

export { PermissionsSection };
