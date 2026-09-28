/* MobileAdmin — SectionDmcaTakedowns / SectionDmcaStrikes(admin-dmca-*)。
   纯机械从 pages/MobileAdmin.jsx 拆出,逐字节等价。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { LoadingRow, ErrRow, EmptyRow, fmtDate } from './shared.jsx';
import { InputSheet } from './sheets.jsx';

/* ══════════════════════════════════════════
   Section: admin-dmca-takedowns
══════════════════════════════════════════ */
function SectionDmcaTakedowns({ nav }) {
  const { t } = useTranslation();
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState('open');
  const [actionSheet, setActionSheet] = React.useState(null); // { item, action }
  const [actionBusy, setActionBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true); setErr(null);
    try { const r = await window.api.admin.dmcaTakedowns.list({ status: statusFilter }); setItems(r.takedowns || r || []); }
    catch (e) { setErr(e?.message || t('mobile.admin.load_failed')); }
    finally { setLoading(false); }
  }, [statusFilter]);

  React.useEffect(() => { load(); }, [load]);

  async function doAction(vals) {
    if (!actionSheet) return;
    setActionBusy(true);
    try {
      await window.api.admin.dmcaTakedowns.action(actionSheet.item.id, { action: actionSheet.action, reason: vals?.reason || '' });
      nav.toast(t('mobile.admin.action_success'), 'ok');
      setActionSheet(null);
      load();
    } catch (e) { nav.toast(t('mobile.admin.action_failed', { msg: e?.message || '' }), 'danger'); }
    finally { setActionBusy(false); }
  }

  const statusColor = { open: 'var(--danger)', counter_received: 'var(--info)', closed: 'var(--muted)', restored: 'var(--ok)', rejected: 'var(--muted)' };
  const statusLabel = {
    open: t('mobile.admin.dmca.status_open'),
    counter_received: t('mobile.admin.dmca.status_counter_received'),
    closed: t('mobile.admin.dmca.status_closed'),
    restored: t('mobile.admin.dmca.status_restored'),
    rejected: t('mobile.admin.dmca.status_rejected'),
  };

  return (
    <>
      <div className="pl-head">
        <button className="pl-headbtn" onClick={() => nav.go('admin')}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title"><strong style={{ fontSize: 15 }}>{t('mobile.admin.section.dmca_takedowns')}</strong></div>
        <button className="pl-headbtn" onClick={load} disabled={loading}><Icon name="refresh" size={18} /></button>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            {Object.entries(statusLabel).concat([['all', t('common.all')]]).map(([v, l]) => (
              <button key={v} onClick={() => setStatusFilter(v)}
                style={{ padding: '4px 11px', borderRadius: 999, fontSize: 12, border: '1px solid', borderColor: statusFilter === v ? 'var(--accent-edge)' : 'var(--line)', background: statusFilter === v ? 'var(--accent-soft)' : 'var(--panel-2)', color: statusFilter === v ? 'var(--accent)' : 'var(--muted)' }}>
                {l}
              </button>
            ))}
          </div>

          {loading ? <LoadingRow /> : err ? <ErrRow msg={err} onRetry={load} /> : items.length === 0 ? <EmptyRow text={t('mobile.admin.no_records')} /> : (
            <div className="pl-sec">
              {items.map((item) => (
                <div key={item.id} style={{ border: '1px solid var(--line-soft)', borderRadius: 12, background: 'var(--panel)', marginBottom: 8, overflow: 'hidden' }}>
                  <div style={{ padding: '11px 13px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: statusColor[item.status] || 'var(--muted)' }}>{statusLabel[item.status] || item.status}</span>
                      <span style={{ fontSize: 10.5, color: 'var(--muted-3)', marginLeft: 'auto' }}>#{item.id} · {fmtDate(item.created_at)}</span>
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text-quiet)', marginBottom: 3 }}>{item.complainant_name || '—'}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.infringing_url || '—'}</div>
                  </div>
                  {item.status === 'open' && (
                    <div style={{ display: 'flex', borderTop: '1px solid var(--line-soft)' }}>
                      {/* action 必须是后端认的 takedown | reject(与桌面管理页一致);以前「批准下架」
                          发的是 'grant',后端恒 400,手机上根本批不了下架。 */}
                      {['takedown', 'reject'].map((action, i) => (
                        <button key={action}
                          style={{ flex: 1, padding: '9px 4px', fontSize: 12, color: action === 'takedown' ? 'var(--ok)' : 'var(--danger)', borderRight: i === 0 ? '1px solid var(--line-soft)' : 'none' }}
                          onClick={() => setActionSheet({ item, action })}>
                          {action === 'takedown' ? t('mobile.admin.dmca.grant_btn') : t('mobile.admin.dmca.reject_btn')}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {actionSheet && (
        <InputSheet
          title={actionSheet.action === 'takedown' ? t('mobile.admin.dmca.grant_sheet_title') : t('mobile.admin.dmca.reject_sheet_title')}
          fields={[{ key: 'reason', label: t('mobile.admin.dmca.reason_label'), multiline: true, placeholder: t('mobile.admin.dmca.reason_placeholder') }]}
          busy={actionBusy}
          onConfirm={doAction}
          onCancel={() => setActionSheet(null)}
        />
      )}
    </>
  );
}

/* ══════════════════════════════════════════
   Section: admin-dmca-strikes
══════════════════════════════════════════ */
function SectionDmcaStrikes({ nav }) {
  const { t } = useTranslation();
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [incTarget, setIncTarget] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true); setErr(null);
    try { const r = await window.api.admin.dmcaStrikes.list(); setItems(r.strikes || r || []); }
    catch (e) { setErr(e?.message || t('mobile.admin.load_failed')); }
    finally { setLoading(false); }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function doIncrement(vals) {
    if (!incTarget) return;
    setBusy(true);
    try {
      await window.api.admin.dmcaStrikes.increment(incTarget.user_id, { reason: vals?.reason || '' });
      nav.toast(t('mobile.admin.strikes.added'), 'ok');
      setIncTarget(null);
      load();
    } catch (e) { nav.toast(t('mobile.admin.action_failed', { msg: e?.message || '' }), 'danger'); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="pl-head">
        <button className="pl-headbtn" onClick={() => nav.go('admin')}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title"><strong style={{ fontSize: 15 }}>{t('mobile.admin.strikes.title')}</strong></div>
        <button className="pl-headbtn" onClick={load} disabled={loading}><Icon name="refresh" size={18} /></button>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading ? <LoadingRow /> : err ? <ErrRow msg={err} onRetry={load} /> : items.length === 0 ? <EmptyRow text={t('mobile.admin.strikes.empty')} /> : (
            <div className="pl-sec">
              {items.map((s) => (
                <div key={s.user_id} className="pl-row">
                  <span className="pl-row-ic warn"><Icon name="warn" size={17} /></span>
                  <span className="pl-row-tx">
                    <strong style={{ fontSize: 13 }}>{s.username || s.user_id}</strong>
                    <span className="mono">{t('mobile.admin.strikes.count', { count: s.strike_count || 0 })}</span>
                  </span>
                  <button style={{ fontSize: 12, color: 'var(--warn)', padding: '4px 8px' }} onClick={() => setIncTarget(s)}>{t('mobile.admin.strikes.add_btn')}</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {incTarget && (
        <InputSheet
          title={t('mobile.admin.strikes.sheet_title', { username: incTarget.username || incTarget.user_id })}
          fields={[{ key: 'reason', label: t('mobile.admin.strikes.reason_label'), multiline: true, placeholder: t('mobile.admin.strikes.reason_placeholder') }]}
          busy={busy} onConfirm={doIncrement} onCancel={() => setIncTarget(null)}
        />
      )}
    </>
  );
}

export { SectionDmcaTakedowns, SectionDmcaStrikes };
