/* MobileAdmin — SectionRegistration(admin-registration)。纯机械从 pages/MobileAdmin.jsx 拆出,逐字节等价。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { LoadingRow, ErrRow, EmptyRow, fmtDate } from './shared.jsx';
import { ConfirmSheet, InputSheet } from './sheets.jsx';

/* ══════════════════════════════════════════
   Section: admin-registration
══════════════════════════════════════════ */
function SectionRegistration({ nav }) {
  const { t } = useTranslation();
  const [regConfig, setRegConfig] = React.useState(null);
  const [inviteCodes, setInviteCodes] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const [showCreate, setShowCreate] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState(null);
  const [deleting, setDeleting] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [reg, codes] = await Promise.all([window.api.admin.registration(), window.api.admin.inviteCodes()]);
      setRegConfig(reg);
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) { setErr(e?.message || t('mobile.admin.load_failed')); }
    finally { setLoading(false); }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function saveReg(patch) {
    setSaving(true);
    try {
      const next = { ...regConfig, ...patch };
      await window.api.admin.saveRegistration(next);
      setRegConfig(next);
      nav.toast(t('mobile.admin.save_success'), 'ok');
    } catch (e) { nav.toast(t('mobile.admin.save_failed', { msg: e?.message || '' }), 'danger'); }
    finally { setSaving(false); }
  }

  async function handleCreate(vals) {
    setCreating(true);
    try {
      await window.api.admin.createInviteCodes({ count: Number(vals.count) || 1, expires_days: Number(vals.expires_days) || 30, note: vals.note || undefined });
      nav.toast(t('mobile.admin.registration.invite_created'), 'ok');
      setShowCreate(false);
      const codes = await window.api.admin.inviteCodes();
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) { nav.toast(t('mobile.admin.registration.create_failed', { msg: e?.message || '' }), 'danger'); }
    finally { setCreating(false); }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await window.api.admin.deleteInviteCode(deleteTarget);
      nav.toast(t('mobile.admin.deleted'), 'ok');
      setDeleteTarget(null);
      const codes = await window.api.admin.inviteCodes();
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) { nav.toast(t('mobile.admin.delete_failed', { msg: e?.message || '' }), 'danger'); }
    finally { setDeleting(false); }
  }

  return (
    <>
      <div className="pl-head">
        <button className="pl-headbtn" onClick={() => nav.go('admin')}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title"><strong style={{ fontSize: 15 }}>{t('mobile.admin.section.registration')}</strong></div>
        <button className="pl-headbtn" onClick={load} disabled={loading}><Icon name="refresh" size={18} /></button>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading ? <LoadingRow /> : err ? <ErrRow msg={err} onRetry={load} /> : !regConfig ? <EmptyRow /> : (
            <>
              <div className="pl-sec">
                <div className="pl-sec-head"><h2>{t('mobile.admin.registration.mode_heading')}</h2></div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                  {[['open', t('mobile.admin.registration.mode_open')], ['invite', t('mobile.admin.registration.mode_invite')], ['closed', t('mobile.admin.registration.mode_closed')]].map(([v, l]) => (
                    <button key={v}
                      onClick={() => saveReg({ mode: v })}
                      style={{ flex: 1, padding: '10px 4px', borderRadius: 10, fontSize: 13, fontWeight: regConfig.mode === v ? 600 : 400, border: '1px solid', borderColor: regConfig.mode === v ? 'var(--accent-edge)' : 'var(--line)', background: regConfig.mode === v ? 'var(--accent-soft)' : 'var(--panel-2)', color: regConfig.mode === v ? 'var(--accent)' : 'var(--muted)' }}>
                      {saving ? '…' : l}
                    </button>
                  ))}
                </div>
                {/* 「邮箱验证」「自动审批」已撤:后端注册流程从不读它们,与网页管理页同步。 */}
                <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>{t('mobile.admin.registration.mode_hint')}</div>
              </div>

              <div className="pl-sec">
                <div className="pl-sec-head"><h2>{t('mobile.admin.registration.invite_codes_heading', { count: inviteCodes.length })}</h2>
                  <button className="act" onClick={() => setShowCreate(true)}><Icon name="plus" size={13} /> {t('mobile.admin.registration.create_btn')}</button>
                </div>
                {inviteCodes.length === 0 ? <EmptyRow text={t('mobile.admin.registration.no_codes')} /> : inviteCodes.map((c) => (
                  <div key={c.code} className="pl-row" style={{ cursor: 'default' }}>
                    <span className="pl-row-ic info"><Icon name="key" size={17} /></span>
                    <span className="pl-row-tx">
                      <strong className="mono" style={{ fontSize: 13 }}>{c.code}</strong>
                      <span>{c.used_at ? t('mobile.admin.registration.code_used') : t('mobile.admin.registration.code_unused')}{c.expires_at ? ` · ${t('mobile.admin.registration.expires')} ${fmtDate(c.expires_at)}` : ''}{c.note ? ` · ${c.note}` : ''}</span>
                    </span>
                    {/* 已用判据是后端返回的 used_at(以前读后端不返回的 used 字段 → 用过的码也显示「未使用」) */}
                    {!c.used_at && <button style={{ fontSize: 12, color: 'var(--danger)', padding: '4px 8px' }} onClick={() => setDeleteTarget(c.code)}>{t('common.delete')}</button>}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {showCreate && (
        <InputSheet
          title={t('mobile.admin.registration.create_sheet_title')}
          fields={[
            { key: 'count', label: t('mobile.admin.registration.field_count'), default: '1', type: 'number' },
            { key: 'expires_days', label: t('mobile.admin.registration.field_expires_days'), default: '30', type: 'number' },
            { key: 'note', label: t('mobile.admin.registration.field_note'), default: '' },
          ]}
          busy={creating}
          onConfirm={handleCreate}
          onCancel={() => setShowCreate(false)}
        />
      )}
      {deleteTarget && (
        <ConfirmSheet
          title={t('mobile.admin.registration.delete_code_title', { code: deleteTarget })} body={t('mobile.admin.registration.delete_code_body')}
          danger busy={deleting} onConfirm={handleDelete} onCancel={() => setDeleteTarget(null)}
        />
      )}
    </>
  );
}

export { SectionRegistration };
