/* MobileAdmin — SectionCsamReports(admin-csam-reports)。从 pages/MobileAdmin.jsx 拆出。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { LoadingRow, ErrRow, EmptyRow } from './shared.jsx';
import { InputSheet } from './sheets.jsx';

/* ══════════════════════════════════════════
   Section: admin-csam-reports
══════════════════════════════════════════ */
function SectionCsamReports({ nav }) {
  const { t } = useTranslation();
  const [reports, setReports] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState('pending');
  const [decideTarget, setDecideTarget] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true); setErr(null);
    try { const r = await window.api.admin.csamReports.list({ status: statusFilter }); setReports(r.reports || r || []); }
    catch (e) { setErr(e?.message || t('mobile.admin.load_failed')); }
    finally { setLoading(false); }
  }, [statusFilter]);

  React.useEffect(() => { load(); }, [load]);

  async function doDecide(vals) {
    if (!decideTarget || !vals.decision) return;
    setBusy(true);
    try {
      await window.api.admin.csamReports.decision(decideTarget.id, { decision: vals.decision, notes: vals.notes || '' });
      nav.toast(t('mobile.admin.processed'), 'ok');
      setDecideTarget(null);
      load();
    } catch (e) { nav.toast(t('mobile.admin.action_failed', { msg: e?.message || '' }), 'danger'); }
    finally { setBusy(false); }
  }

  const decisionColor = { founded: 'var(--danger)', escalate: 'var(--info)', unfounded: 'var(--muted)' };
  // 取值与后端 admin_csam_decision 一致(founded | unfounded | escalate),文案与桌面管理页共用。
  const decisionLabel = {
    founded: t('admin_page.csam.badge_founded'),
    escalate: t('admin_page.csam.badge_escalate'),
    unfounded: t('admin_page.csam.badge_unfounded'),
  };
  const decisionOptions = [
    { value: 'founded', label: t('admin_page.csam.decision_founded') },
    { value: 'escalate', label: t('admin_page.csam.decision_escalate') },
    { value: 'unfounded', label: t('admin_page.csam.decision_unfounded') },
  ];

  return (
    <>
      <div className="pl-head">
        <button className="pl-headbtn" onClick={() => nav.go('admin')}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title"><strong style={{ fontSize: 15 }}>{t('mobile.admin.section.csam_reports')}</strong></div>
        <button className="pl-headbtn" onClick={load} disabled={loading}><Icon name="refresh" size={18} /></button>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          <div style={{ padding: '10px 13px', borderRadius: 10, background: 'var(--warn-soft)', border: '1px solid rgba(212,179,102,0.4)', fontSize: 12.5, color: 'var(--warn)', marginBottom: 12 }}>
            {t('mobile.admin.csam.review_notice')}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            {[['pending', t('mobile.admin.csam.status_pending')], ['decided', t('mobile.admin.csam.status_decided')], ['all', t('common.all')]].map(([v, l]) => (
              <button key={v} onClick={() => setStatusFilter(v)}
                style={{ flex: 1, padding: '7px 4px', borderRadius: 999, fontSize: 12, border: '1px solid', borderColor: statusFilter === v ? 'var(--accent-edge)' : 'var(--line)', background: statusFilter === v ? 'var(--accent-soft)' : 'var(--panel-2)', color: statusFilter === v ? 'var(--accent)' : 'var(--muted)' }}>
                {l}
              </button>
            ))}
          </div>

          {loading ? <LoadingRow /> : err ? <ErrRow msg={err} onRetry={load} /> : reports.length === 0 ? <EmptyRow text={t('mobile.admin.csam.empty')} /> : (
            <div className="pl-sec">
              {reports.map((r) => (
                <div key={r.id} style={{ border: '1px solid var(--line-soft)', borderRadius: 12, background: 'var(--panel)', marginBottom: 8, overflow: 'hidden' }}>
                  <div style={{ padding: '11px 13px' }}>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: r.status === 'pending' ? 'var(--danger)' : 'var(--muted)' }}>{r.status === 'pending' ? t('mobile.admin.csam.status_pending') : t('mobile.admin.csam.status_decided')}</span>
                      {r.decision && <span style={{ fontSize: 11, color: decisionColor[r.decision] || 'var(--muted)' }}>{decisionLabel[r.decision] || r.decision}</span>}
                      <span style={{ fontSize: 10.5, color: 'var(--muted-3)', marginLeft: 'auto' }}>#{r.id}</span>
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text-quiet)' }}>{t('mobile.admin.csam.reported_user')}{r.reported_username || `uid:${r.reported_user_id}`}</div>
                    {r.cybertip_report_id && <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 3 }}>CyberTip: {r.cybertip_report_id}</div>}
                  </div>
                  {r.status === 'pending' && (
                    <button style={{ width: '100%', padding: '9px', fontSize: 12.5, color: 'var(--info)', borderTop: '1px solid var(--line-soft)' }}
                      onClick={() => setDecideTarget(r)}>
                      {t('mobile.admin.csam.decide_btn')}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {decideTarget && (
        <InputSheet
          title={t('mobile.admin.csam.decide_sheet_title', { id: decideTarget.id })}
          fields={[
            { key: 'decision', label: t('mobile.admin.csam.decision_label'), options: decisionOptions, placeholder: t('admin_page.csam.decision_select_placeholder') },
            { key: 'notes', label: t('mobile.admin.csam.notes_label'), multiline: true, placeholder: t('mobile.admin.csam.notes_placeholder') },
          ]}
          busy={busy} onConfirm={doDecide} onCancel={() => setDecideTarget(null)}
        />
      )}
    </>
  );
}

export { SectionCsamReports };
