/* Admin — AdminRegistrationPage — 注册与邀请。从 pages/admin.jsx 纯机械拆出,JSX / props / fetch 路径零变化。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTable from '@cloudscape-design/components/table';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSAlert from '@cloudscape-design/components/alert';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSModal from '@cloudscape-design/components/modal';
import CSFormField from '@cloudscape-design/components/form-field';
import { fmtTime } from './shared.jsx';

/* ─────────────────────────────────────────────────────────────────
   页面 6：AdminRegistrationPage — 注册与邀请
   ───────────────────────────────────────────────────────────────── */
export function AdminRegistrationPage() {
  const { t } = useTranslation();
  const [regConfig, setRegConfig] = React.useState(null);
  const [inviteCodes, setInviteCodes] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [savingReg, setSavingReg] = React.useState(false);
  const [createModal, setCreateModal] = React.useState(false);
  const [createForm, setCreateForm] = React.useState({ count: '1', expires_days: '30', note: '' });
  const [creating, setCreating] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState(null);
  const [deleting, setDeleting] = React.useState(false);

  const modeOptions = [
    { value: 'open', label: t('admin_page.registration.mode_open') },
    { value: 'invite', label: t('admin_page.registration.mode_invite') },
    { value: 'closed', label: t('admin_page.registration.mode_closed') },
  ];

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const [reg, codes] = await Promise.all([
          window.api.admin.registration(),
          window.api.admin.inviteCodes(),
        ]);
        if (!cancelled) {
          setRegConfig(reg);
          setInviteCodes(codes.items || codes.codes || codes || []);
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function saveReg(patch) {
    setSavingReg(true);
    try {
      const next = { ...regConfig, ...patch };
      await window.api.admin.saveRegistration(next);
      setRegConfig(next);
      window.toast?.(t('admin_page.registration.save_ok'), { kind: 'ok' });
    } catch (e) {
      window.toast?.(t('admin_page.registration.save_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSavingReg(false);
    }
  }

  async function handleCreateCodes() {
    setCreating(true);
    try {
      await window.api.admin.createInviteCodes({
        count: Number(createForm.count),
        expires_days: Number(createForm.expires_days),
        note: createForm.note || undefined,
      });
      window.toast?.(t('admin_page.registration.create_ok'), { kind: 'ok' });
      setCreateModal(false);
      const codes = await window.api.admin.inviteCodes();
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) {
      window.toast?.(t('admin_page.registration.create_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(code) {
    setDeleting(true);
    try {
      await window.api.admin.deleteInviteCode(code);
      window.toast?.(t('admin_page.registration.delete_ok'), { kind: 'ok' });
      setDeleteTarget(null);
      const codes = await window.api.admin.inviteCodes();
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) {
      window.toast?.(t('admin_page.registration.delete_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}

      <CSContainer header={<CSHeader variant="h2">{t('admin_page.registration.config_title')}</CSHeader>}>
        {loading
          ? <CSBox color="inherit">{t('admin_page.common.loading')}</CSBox>
          : !regConfig
            ? <CSBox textAlign="center" color="inherit">{t('admin_page.registration.empty')}</CSBox>
            : (
              <CSSpaceBetween size="m">
                <CSFormField label={t('admin_page.registration.field_mode')}
                  description={t('admin_page.registration.mode_hint')}>
                  <CSSpaceBetween direction="horizontal" size="xs">
                    {modeOptions.map((opt) => (
                      <CSButton
                        key={opt.value}
                        variant={regConfig.mode === opt.value ? 'primary' : 'normal'}
                        onClick={() => saveReg({ mode: opt.value })}
                        loading={savingReg && regConfig.mode !== opt.value}
                      >
                        {opt.label}
                      </CSButton>
                    ))}
                  </CSSpaceBetween>
                </CSFormField>
                {/* 「邮箱验证」「自动审批」两个开关已撤:注册流程从不读它们(邮箱验证由部署模式
                    决定,也没有审批流程),留着只会让人以为改了有用。见 api/admin/registration.py。 */}
              </CSSpaceBetween>
            )
        }
      </CSContainer>

      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.registration.invite_description')}
            actions={
              <CSButton variant="primary" onClick={() => setCreateModal(true)}>{t('admin_page.registration.invite_create_btn')}</CSButton>
            }
          >
            {t('admin_page.registration.invite_title')}
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="code"
          items={inviteCodes}
          empty={<CSBox textAlign="center" color="inherit">{t('admin_page.registration.invite_empty')}</CSBox>}
          columnDefinitions={[
            { id: 'code', header: t('admin_page.registration.col_code'), cell: (c) => <code>{c.code}</code> },
            { id: 'note', header: t('admin_page.registration.col_note'), cell: (c) => c.note || '—' },
            {
              // 字段以 GET /api/admin/invite-codes 为准:used_at / used_by_username / expires_at。
              // 以前读 used_by / expired_at(后端从不返回)→ 用过的码永远显示「可用」、过期的也不标。
              id: 'status', header: t('admin_page.registration.col_status'),
              cell: (c) => c.used_at
                ? <CSBadge color="grey">{c.used_by_username
                    ? t('admin_page.registration.status_used', { user: c.used_by_username })
                    : t('admin_page.registration.status_used_anon')}</CSBadge>
                : c.expires_at && new Date(c.expires_at) < new Date()
                  ? <CSBadge color="red">{t('admin_page.registration.status_expired')}</CSBadge>
                  : <CSBadge color="green">{t('admin_page.registration.status_available')}</CSBadge>,
            },
            { id: 'expires', header: t('admin_page.registration.col_expires'), cell: (c) => fmtTime(c.expires_at) },
            { id: 'created', header: t('admin_page.common.created_at'), cell: (c) => fmtTime(c.created_at) },
            {
              id: 'actions', header: t('admin_page.common.actions'),
              cell: (c) => !c.used_at
                ? <CSButton variant="inline-link" onClick={() => setDeleteTarget(c.code)}>{t('common.delete')}</CSButton>
                : null,
            },
          ]}
        />
      </CSContainer>

      {createModal && (
        <CSModal
          visible
          onDismiss={() => !creating && setCreateModal(false)}
          header={t('admin_page.registration.create_modal_title')}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={creating} onClick={() => setCreateModal(false)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={creating} onClick={handleCreateCodes}>{t('admin_page.registration.create_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSFormField label={t('admin_page.registration.create_field_count')}>
              <CSInput
                type="number"
                value={createForm.count}
                onChange={({ detail }) => setCreateForm((f) => ({ ...f, count: detail.value }))}
              />
            </CSFormField>
            <CSFormField label={t('admin_page.registration.create_field_expires')}>
              <CSSelect
                selectedOption={{ value: createForm.expires_days, label: t('admin_page.registration.expires_days', { d: createForm.expires_days }) }}
                options={[7, 14, 30, 90, 180, 365].map((d) => ({ value: String(d), label: t('admin_page.registration.expires_days', { d }) }))}
                onChange={({ detail }) => setCreateForm((f) => ({ ...f, expires_days: detail.selectedOption.value }))}
              />
            </CSFormField>
            <CSFormField label={t('admin_page.registration.create_field_note')}>
              <CSInput
                value={createForm.note}
                onChange={({ detail }) => setCreateForm((f) => ({ ...f, note: detail.value }))}
                placeholder={t('admin_page.registration.create_note_placeholder')}
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {deleteTarget && (
        <CSModal
          visible
          onDismiss={() => !deleting && setDeleteTarget(null)}
          header={t('admin_page.registration.delete_modal_title')}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={deleting} onClick={() => setDeleteTarget(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={deleting} onClick={() => handleDelete(deleteTarget)}>{t('admin_page.registration.delete_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>{t('admin_page.registration.delete_confirm_body', { code: deleteTarget })}</CSBox>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}
