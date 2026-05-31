/* Admin pages — 全量系统管理页面集合
   8 个页面组件，全部通过 window.api.admin.* 从后端获取数据，禁止 mock/硬编码示例数据。 */

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
import CSToggle from '@cloudscape-design/components/toggle';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSModal from '@cloudscape-design/components/modal';
import CSFormField from '@cloudscape-design/components/form-field';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';

/* ── 通用工具 ─────────────────────────────────────────────────── */
function fmtTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString('zh-CN', { hour12: false }); } catch (_) { return iso; }
}

/* ─────────────────────────────────────────────────────────────────
   页面 1：AdminUsersPage — 用户管理
   ───────────────────────────────────────────────────────────────── */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const [users, setUsers] = React.useState([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [page, setPage] = React.useState(1);
  const limit = 20;
  const [search, setSearch] = React.useState('');
  const [roleFilter, setRoleFilter] = React.useState({ value: '', label: t('admin_page.users.role_all') });
  const [statusFilter, setStatusFilter] = React.useState({ value: '', label: t('admin_page.users.status_all') });

  // 确认 modal 状态
  const [confirmModal, setConfirmModal] = React.useState(null); // { action, user, title, body }
  const [actionBusy, setActionBusy] = React.useState(false);

  const me = window.RPG_AUTH && window.RPG_AUTH.user;

  const load = React.useCallback(async (p = page) => {
    setLoading(true);
    setErr(null);
    let cancelled = false;
    try {
      const params = { page: p, limit };
      if (search) params.search = search;
      if (roleFilter.value) params.role = roleFilter.value;
      if (statusFilter.value) params.status = statusFilter.value;
      const res = await window.api.admin.users(params);
      if (!cancelled) {
        setUsers(res.users || res.items || res || []);
        setTotal(res.total || (res.users || res.items || res || []).length);
      }
    } catch (e) {
      if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      if (!cancelled) setLoading(false);
    }
    return () => { cancelled = true; };
  }, [page, search, roleFilter.value, statusFilter.value]);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const params = { page, limit };
        if (search) params.search = search;
        if (roleFilter.value) params.role = roleFilter.value;
        if (statusFilter.value) params.status = statusFilter.value;
        const res = await window.api.admin.users(params);
        if (!cancelled) {
          setUsers(res.users || res.items || res || []);
          setTotal(res.total || (res.users || res.items || res || []).length);
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [page, roleFilter.value, statusFilter.value]);

  async function doAction() {
    if (!confirmModal) return;
    setActionBusy(true);
    try {
      const { action, user } = confirmModal;
      if (action === 'deactivate') await window.api.admin.deactivateUser(user.id);
      else if (action === 'reactivate') await window.api.admin.reactivateUser(user.id);
      else if (action === 'force-logout') await window.api.admin.forceLogout(user.id);
      else if (action === 'set-admin') await window.api.admin.updateUser(user.id, { role: 'admin' });
      else if (action === 'set-user') await window.api.admin.updateUser(user.id, { role: 'user' });
      window.toast?.(t('admin_page.common.op_ok'), { kind: 'ok' });
      setConfirmModal(null);
      load(page);
    } catch (e) {
      window.toast?.(t('admin_page.common.op_fail') + ': ' + (e?.message || t('common.unknown')), { kind: 'danger' });
    } finally {
      setActionBusy(false);
    }
  }

  const roleOptions = [
    { value: '', label: t('admin_page.users.role_all') },
    { value: 'admin', label: t('admin_page.users.role_admin') },
    { value: 'user', label: t('admin_page.users.role_user') },
  ];
  const statusOptions = [
    { value: '', label: t('admin_page.users.status_all') },
    { value: 'active', label: t('admin_page.users.status_active') },
    { value: 'deactivated', label: t('admin_page.users.status_deactivated') },
  ];

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.users.description')}
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton iconName="refresh" onClick={() => load(page)} loading={loading}>{t('admin_page.common.refresh')}</CSButton>
              </CSSpaceBetween>
            }
          >
            {t('admin_page.users.title')}
          </CSHeader>
        }
      >
        <CSSpaceBetween size="m">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSInput
              placeholder={t('admin_page.users.search_placeholder')}
              value={search}
              onChange={({ detail }) => setSearch(detail.value)}
              onKeyDown={({ detail }) => { if (detail.key === 'Enter') { setPage(1); load(1); } }}
              type="search"
            />
            <CSSelect
              selectedOption={roleFilter}
              options={roleOptions}
              onChange={({ detail }) => { setRoleFilter(detail.selectedOption); setPage(1); }}
            />
            <CSSelect
              selectedOption={statusFilter}
              options={statusOptions}
              onChange={({ detail }) => { setStatusFilter(detail.selectedOption); setPage(1); }}
            />
          </CSSpaceBetween>
          <CSTable
            loading={loading}
            loadingText={t('admin_page.common.loading')}
            trackBy="id"
            items={users}
            empty={
              <CSBox textAlign="center" color="inherit">
                <CSBox padding={{ bottom: 's' }} variant="p" color="inherit">{t('admin_page.users.empty')}</CSBox>
              </CSBox>
            }
            columnDefinitions={[
              { id: 'username', header: t('admin_page.users.col_username'), cell: (u) => u.username || u.name || '—' },
              { id: 'display_name', header: t('admin_page.users.col_display_name'), cell: (u) => u.display_name || '—' },
              {
                id: 'role', header: t('admin_page.users.col_role'),
                cell: (u) => u.role === 'admin'
                  ? <CSBadge color="severity-medium">{t('admin_page.users.role_admin')}</CSBadge>
                  : <CSBadge color="grey">{t('admin_page.users.role_user')}</CSBadge>,
              },
              {
                id: 'status', header: t('admin_page.users.col_status'),
                cell: (u) => u.deactivated_at
                  ? <CSStatusIndicator type="stopped">{t('admin_page.users.status_stopped')}</CSStatusIndicator>
                  : <CSStatusIndicator type="success">{t('admin_page.users.status_active_label')}</CSStatusIndicator>,
              },
              { id: 'last_login', header: t('admin_page.users.col_last_login'), cell: (u) => fmtTime(u.last_login_at || u.last_login) },
              {
                id: 'token_30d', header: t('admin_page.users.col_token_30d'),
                cell: (u) => typeof u.token_usage_30d === 'number' ? u.token_usage_30d.toLocaleString() : '—',
              },
              {
                id: 'sessions', header: t('admin_page.users.col_sessions'),
                cell: (u) => typeof u.active_session_count === 'number' ? u.active_session_count : '—',
              },
              {
                id: 'actions', header: t('admin_page.common.actions'),
                cell: (u) => {
                  const isSelf = me && (me.id === u.id || me.username === u.username);
                  return (
                    <CSSpaceBetween direction="horizontal" size="xs">
                      {!u.deactivated_at && (
                        <CSButton
                          variant="inline-link"
                          disabled={isSelf}
                          onClick={() => setConfirmModal({
                            action: 'deactivate', user: u,
                            title: t('admin_page.users.confirm_deactivate_title', { name: u.username }),
                            body: t('admin_page.users.confirm_deactivate_body'),
                          })}
                        >{t('admin_page.users.deactivate')}</CSButton>
                      )}
                      {u.deactivated_at && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => setConfirmModal({
                            action: 'reactivate', user: u,
                            title: t('admin_page.users.confirm_reactivate_title', { name: u.username }),
                            body: t('admin_page.users.confirm_reactivate_body'),
                          })}
                        >{t('admin_page.users.reactivate')}</CSButton>
                      )}
                      <CSButton
                        variant="inline-link"
                        onClick={() => setConfirmModal({
                          action: 'force-logout', user: u,
                          title: t('admin_page.users.confirm_force_logout_title', { name: u.username }),
                          body: t('admin_page.users.confirm_force_logout_body'),
                        })}
                      >{t('admin_page.users.force_logout')}</CSButton>
                      {u.role === 'user' && !isSelf && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => setConfirmModal({
                            action: 'set-admin', user: u,
                            title: t('admin_page.users.confirm_set_admin_title', { name: u.username }),
                            body: t('admin_page.users.confirm_set_admin_body'),
                          })}
                        >{t('admin_page.users.set_admin')}</CSButton>
                      )}
                      {u.role === 'admin' && !isSelf && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => setConfirmModal({
                            action: 'set-user', user: u,
                            title: t('admin_page.users.confirm_set_user_title', { name: u.username }),
                            body: t('admin_page.users.confirm_set_user_body'),
                          })}
                        >{t('admin_page.users.set_user')}</CSButton>
                      )}
                    </CSSpaceBetween>
                  );
                },
              },
            ]}
            pagination={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton disabled={page <= 1} onClick={() => setPage(p => p - 1)}>{t('admin_page.common.prev_page')}</CSButton>
                <CSBox padding="xs">{t('admin_page.common.page_info', { page, total: Math.ceil(total / limit) })}</CSBox>
                <CSButton disabled={users.length < limit} onClick={() => setPage(p => p + 1)}>{t('admin_page.common.next_page')}</CSButton>
              </CSSpaceBetween>
            }
          />
        </CSSpaceBetween>
      </CSContainer>

      {confirmModal && (
        <CSModal
          visible
          onDismiss={() => !actionBusy && setConfirmModal(null)}
          header={confirmModal.title}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={actionBusy} onClick={() => setConfirmModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={actionBusy} onClick={doAction}>{t('admin_page.common.confirm')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>{confirmModal.body}</CSBox>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 2：AdminGlobalUsagePage — 全局用量
   ───────────────────────────────────────────────────────────────── */
export function AdminGlobalUsagePage() {
  const { t } = useTranslation();
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [days, setDays] = React.useState({ value: '30', label: t('admin_page.usage.days_30') });

  const daysOptions = [
    { value: '7', label: t('admin_page.usage.days_7') },
    { value: '14', label: t('admin_page.usage.days_14') },
    { value: '30', label: t('admin_page.usage.days_30') },
    { value: '90', label: t('admin_page.usage.days_90') },
  ];

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const res = await window.api.admin.globalUsage({ days: Number(days.value) });
        if (!cancelled) setData(res);
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [days.value]);

  const summary = data?.summary || {};
  const byUser = data?.by_user || [];
  const byApi = data?.by_api || [];
  const byDay = data?.by_day || [];
  const maxDayTokens = byDay.reduce((m, d) => Math.max(m, d.tokens || 0), 1);

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}

      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.usage.description')}
            actions={
              <CSSelect
                selectedOption={days}
                options={daysOptions}
                onChange={({ detail }) => setDays(detail.selectedOption)}
              />
            }
          >
            {t('admin_page.usage.title')}
          </CSHeader>
        }
      >
        {loading
          ? <CSBox color="inherit">{t('admin_page.common.loading')}</CSBox>
          : !data
            ? <CSBox color="inherit" textAlign="center">{t('admin_page.usage.empty')}</CSBox>
            : (
              <CSKeyValuePairs
                columns={3}
                items={[
                  { label: t('admin_page.usage.kv_requests'), value: (summary.total_requests || 0).toLocaleString() },
                  { label: t('admin_page.usage.kv_tokens'), value: (summary.total_tokens || 0).toLocaleString() },
                  { label: t('admin_page.usage.kv_cost'), value: typeof summary.total_cost === 'number' ? `$${summary.total_cost.toFixed(4)}` : '—' },
                ]}
              />
            )
        }
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2">{t('admin_page.usage.by_user')}</CSHeader>}>
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="user_id"
          items={byUser}
          empty={<CSBox textAlign="center" color="inherit">{t('admin_page.usage.empty_generic')}</CSBox>}
          columnDefinitions={[
            { id: 'rank', header: t('admin_page.usage.col_rank'), cell: (_, idx) => idx + 1, width: 50 },
            { id: 'username', header: t('admin_page.usage.col_username'), cell: (u) => u.username || u.user_id || '—' },
            { id: 'tokens', header: t('admin_page.usage.col_tokens'), cell: (u) => (u.tokens || 0).toLocaleString() },
            { id: 'cost', header: t('admin_page.usage.col_cost'), cell: (u) => typeof u.cost === 'number' ? `$${u.cost.toFixed(4)}` : '—' },
            {
              id: 'pct', header: t('admin_page.usage.col_pct'),
              cell: (u) => {
                const pct = summary.total_tokens > 0 ? Math.round((u.tokens / summary.total_tokens) * 100) : 0;
                return (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ flex: 1, height: 6, background: 'var(--color-background-status-inactive, #d1d5db)', borderRadius: 3 }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: 'var(--color-background-status-positive, #037f0c)', borderRadius: 3 }} />
                    </div>
                    <span style={{ fontSize: 12, minWidth: 30 }}>{pct}%</span>
                  </div>
                );
              },
            },
          ]}
        />
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2">{t('admin_page.usage.by_api')}</CSHeader>}>
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="api_id"
          items={byApi}
          empty={<CSBox textAlign="center" color="inherit">{t('admin_page.usage.empty_generic')}</CSBox>}
          columnDefinitions={[
            { id: 'api_id', header: t('admin_page.usage.col_api'), cell: (a) => a.api_id || a.api || '—' },
            { id: 'tokens', header: t('admin_page.usage.col_token'), cell: (a) => (a.tokens || 0).toLocaleString() },
            { id: 'cost', header: t('admin_page.usage.col_cost'), cell: (a) => typeof a.cost === 'number' ? `$${a.cost.toFixed(4)}` : '—' },
          ]}
        />
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2">{t('admin_page.usage.by_day')}</CSHeader>}>
        {loading
          ? <CSBox color="inherit">{t('admin_page.common.loading')}</CSBox>
          : byDay.length === 0
            ? <CSBox textAlign="center" color="inherit">{t('admin_page.usage.empty_generic')}</CSBox>
            : (
              <CSSpaceBetween size="xs">
                {byDay.map((d) => {
                  const barPct = Math.max(2, Math.round((d.tokens || 0) / maxDayTokens * 100));
                  return (
                    <div key={d.date} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
                      <span style={{ minWidth: 90, color: 'var(--color-text-body-secondary, #5f6b7a)' }}>{d.date}</span>
                      <div style={{ flex: 1, height: 14, background: 'var(--color-background-status-inactive, #d1d5db)', borderRadius: 3 }}>
                        <div style={{ width: `${barPct}%`, height: '100%', background: 'var(--color-background-status-info, #0972d3)', borderRadius: 3 }} />
                      </div>
                      <span style={{ minWidth: 80, textAlign: 'right' }}>{(d.tokens || 0).toLocaleString()}</span>
                    </div>
                  );
                })}
              </CSSpaceBetween>
            )
        }
      </CSContainer>
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 3：AdminAuditPage — 审计日志
   ───────────────────────────────────────────────────────────────── */
export function AdminAuditPage() {
  const { t } = useTranslation();
  const [items, setItems] = React.useState([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [page, setPage] = React.useState(1);
  const limit = 50;
  const [actionFilter, setActionFilter] = React.useState({ value: '', label: t('admin_page.audit.filter_all') });
  const [expandedDetail, setExpandedDetail] = React.useState(null);

  const actionOptions = [
    { value: '', label: t('admin_page.audit.filter_all') },
    { value: 'user', label: 'user.*' },
    { value: 'config', label: 'config.*' },
    { value: 'maintenance', label: 'maintenance.*' },
    { value: 'invite', label: 'invite.*' },
  ];

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const params = { page, limit };
        if (actionFilter.value) params.action_prefix = actionFilter.value;
        const res = await window.api.admin.auditLog(params);
        if (!cancelled) {
          setItems(res.items || res.logs || res || []);
          setTotal(res.total || (res.items || res.logs || res || []).length);
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [page, actionFilter.value]);

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.audit.description')}
            actions={
              <CSSelect
                selectedOption={actionFilter}
                options={actionOptions}
                onChange={({ detail }) => { setActionFilter(detail.selectedOption); setPage(1); }}
              />
            }
          >
            {t('admin_page.audit.title')}
          </CSHeader>
        }
      >
        <CSSpaceBetween size="m">
          <CSTable
            loading={loading}
            loadingText={t('admin_page.common.loading')}
            trackBy="id"
            items={items}
            empty={<CSBox textAlign="center" color="inherit">{t('admin_page.audit.empty')}</CSBox>}
            columnDefinitions={[
              { id: 'created_at', header: t('admin_page.audit.col_time'), cell: (r) => fmtTime(r.created_at || r.timestamp) },
              { id: 'operator', header: t('admin_page.audit.col_operator'), cell: (r) => r.operator || r.user || r.username || '—' },
              {
                id: 'action_type', header: t('admin_page.audit.col_action_type'),
                cell: (r) => <CSBadge color="blue">{r.action_type || r.action || '—'}</CSBadge>,
              },
              { id: 'target', header: t('admin_page.audit.col_target'), cell: (r) => r.target || r.resource || '—' },
              {
                id: 'detail', header: t('admin_page.audit.col_detail'),
                cell: (r) => {
                  const key = r.id || r.created_at;
                  const raw = r.detail || r.meta || r.extra;
                  if (!raw) return '—';
                  const str = typeof raw === 'string' ? raw : JSON.stringify(raw, null, 2);
                  const isExpanded = expandedDetail === key;
                  return (
                    <div>
                      <CSButton variant="inline-link" onClick={() => setExpandedDetail(isExpanded ? null : key)}>
                        {isExpanded ? t('admin_page.common.collapse') : t('admin_page.common.expand')}
                      </CSButton>
                      {isExpanded && <pre style={{ fontSize: 11, maxWidth: 400, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: '4px 0 0' }}>{str}</pre>}
                    </div>
                  );
                },
              },
              { id: 'ip', header: t('admin_page.audit.col_ip'), cell: (r) => r.ip || r.ip_address || '—' },
            ]}
            pagination={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton disabled={page <= 1} onClick={() => setPage(p => p - 1)}>{t('admin_page.common.prev_page')}</CSButton>
                <CSBox padding="xs">{t('admin_page.common.page_simple', { page })}</CSBox>
                <CSButton disabled={items.length < limit} onClick={() => setPage(p => p + 1)}>{t('admin_page.common.next_page')}</CSButton>
              </CSSpaceBetween>
            }
          />
        </CSSpaceBetween>
      </CSContainer>
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 4：AdminHealthPage — 系统健康
   ───────────────────────────────────────────────────────────────── */
export function AdminHealthPage() {
  const { t } = useTranslation();
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [lastUpdate, setLastUpdate] = React.useState(null);
  const [refreshing, setRefreshing] = React.useState(false);

  const fetchHealth = React.useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    else setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.health();
      setData(res);
      setLastUpdate(new Date());
    } catch (e) {
      setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  React.useEffect(() => {
    let cancelled = false;
    fetchHealth();
    const id = setInterval(() => {
      if (!cancelled) fetchHealth();
    }, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [fetchHealth]);

  const db = data?.database || data?.db || {};
  const mem = data?.memory || {};
  const disk = data?.disk || {};
  const proc = data?.process || data?.proc || {};
  const diskPct = typeof disk.used_percent === 'number' ? disk.used_percent : null;

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.health.description')}
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                {lastUpdate && (
                  <CSBox color="text-body-secondary" variant="small">
                    {t('admin_page.health.last_update', { time: lastUpdate.toLocaleTimeString('zh-CN', { hour12: false }) })}
                  </CSBox>
                )}
                <CSButton iconName="refresh" loading={refreshing} onClick={() => fetchHealth(true)}>{t('admin_page.common.refresh')}</CSButton>
              </CSSpaceBetween>
            }
          >
            {t('admin_page.health.title')}
          </CSHeader>
        }
      >
        {loading && !data
          ? <CSBox color="inherit">{t('admin_page.common.loading')}</CSBox>
          : !data
            ? <CSBox textAlign="center" color="inherit">{t('admin_page.health.empty')}</CSBox>
            : (
              <CSColumnLayout columns={2} variant="text-grid">
                <div>
                  <CSSpaceBetween size="s">
                    <div>
                      <strong>{t('admin_page.health.db_title')}</strong>
                      <div>
                        <CSStatusIndicator type={db.ok === false ? 'error' : 'success'}>
                          {db.ok === false ? t('admin_page.health.db_fail') : t('admin_page.health.db_ok')}
                        </CSStatusIndicator>
                        {typeof db.latency_ms === 'number' && (
                          <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-text-body-secondary)' }}>
                            {t('admin_page.health.db_latency', { ms: db.latency_ms })}
                          </span>
                        )}
                      </div>
                    </div>
                    <div>
                      <strong>{t('admin_page.health.mem_title')}</strong>
                      <div>
                        {typeof mem.rss_mb === 'number'
                          ? <CSStatusIndicator type="success">RSS {mem.rss_mb} MB</CSStatusIndicator>
                          : <CSStatusIndicator type="pending">{t('admin_page.health.mem_no_data')}</CSStatusIndicator>
                        }
                      </div>
                    </div>
                  </CSSpaceBetween>
                </div>
                <div>
                  <CSSpaceBetween size="s">
                    <div>
                      <strong>{t('admin_page.health.disk_title')}</strong>
                      <div>
                        {diskPct !== null
                          ? <CSStatusIndicator type={diskPct > 90 ? 'warning' : 'success'}>
                              {t('admin_page.health.disk_used', { pct: diskPct })}
                            </CSStatusIndicator>
                          : <CSStatusIndicator type="pending">{t('admin_page.health.disk_no_data')}</CSStatusIndicator>
                        }
                      </div>
                    </div>
                    <div>
                      <strong>{t('admin_page.health.proc_title')}</strong>
                      <div>
                        {proc.pid
                          ? <CSStatusIndicator type="success">
                              PID {proc.pid}
                              {proc.uptime_s && <span style={{ marginLeft: 8, fontSize: 12 }}>{t('admin_page.health.proc_uptime', { min: Math.round(proc.uptime_s / 60) })}</span>}
                            </CSStatusIndicator>
                          : <CSStatusIndicator type="pending">{t('admin_page.health.proc_no_data')}</CSStatusIndicator>
                        }
                      </div>
                    </div>
                  </CSSpaceBetween>
                </div>
              </CSColumnLayout>
            )
        }
      </CSContainer>
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 5：AdminLogsPage — 系统日志
   ───────────────────────────────────────────────────────────────── */
export function AdminLogsPage() {
  const { t } = useTranslation();
  const [lines, setLines] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [linesCount, setLinesCount] = React.useState({ value: '100', label: t('admin_page.logs.lines_100') });
  const [levelFilter, setLevelFilter] = React.useState({ value: '', label: t('admin_page.logs.level_all') });

  const linesOptions = [
    { value: '50', label: t('admin_page.logs.lines_50') },
    { value: '100', label: t('admin_page.logs.lines_100') },
    { value: '200', label: t('admin_page.logs.lines_200') },
    { value: '500', label: t('admin_page.logs.lines_500') },
  ];
  const levelOptions = [
    { value: '', label: t('admin_page.logs.level_all') },
    { value: 'ERROR', label: 'ERROR' },
    { value: 'WARN', label: 'WARN' },
    { value: 'INFO', label: 'INFO' },
  ];

  const fetchLogs = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.logs({ lines: Number(linesCount.value) });
      setLines(res.lines || res || []);
    } catch (e) {
      setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      setLoading(false);
    }
  }, [linesCount.value]);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const res = await window.api.admin.logs({ lines: Number(linesCount.value) });
        if (!cancelled) setLines(res.lines || res || []);
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [linesCount.value]);

  const filtered = levelFilter.value
    ? lines.filter((l) => {
        const s = typeof l === 'string' ? l : String(l);
        return s.includes(levelFilter.value);
      })
    : lines;

  function handleDownload() {
    const content = (lines || []).join('\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `system-logs-${Date.now()}.log`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function lineColor(line) {
    const s = typeof line === 'string' ? line : String(line);
    if (s.includes('ERROR')) return '#f87171';
    if (s.includes('WARN')) return '#fb923c';
    return undefined;
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.logs.description')}
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={linesCount}
                  options={linesOptions}
                  onChange={({ detail }) => setLinesCount(detail.selectedOption)}
                />
                <CSSelect
                  selectedOption={levelFilter}
                  options={levelOptions}
                  onChange={({ detail }) => setLevelFilter(detail.selectedOption)}
                />
                <CSButton iconName="download" onClick={handleDownload} disabled={!lines.length}>{t('admin_page.common.download')}</CSButton>
                <CSButton iconName="refresh" onClick={fetchLogs} loading={loading}>{t('admin_page.common.refresh')}</CSButton>
              </CSSpaceBetween>
            }
          >
            {t('admin_page.logs.title')}
          </CSHeader>
        }
      >
        {loading
          ? <CSBox color="inherit">{t('admin_page.common.loading')}</CSBox>
          : filtered.length === 0
            ? <CSBox textAlign="center" color="inherit">{t('admin_page.logs.empty')}</CSBox>
            : (
              <pre style={{ fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6, height: 500, overflowY: 'auto', margin: 0, padding: 8, background: 'var(--color-background-container-content, #fff)', borderRadius: 4 }}>
                {filtered.map((line, i) => {
                  const s = typeof line === 'string' ? line : String(line);
                  const color = lineColor(s);
                  return (
                    <span key={i} style={color ? { color, display: 'block' } : { display: 'block' }}>
                      {s}
                    </span>
                  );
                })}
              </pre>
            )
        }
      </CSContainer>
    </CSSpaceBetween>
  );
}

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
                <CSFormField label={t('admin_page.registration.field_mode')}>
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
                <CSFormField label={t('admin_page.registration.field_email_verify')}>
                  <CSToggle
                    checked={!!regConfig.email_verification}
                    onChange={({ detail }) => saveReg({ email_verification: detail.checked })}
                  >
                    {regConfig.email_verification ? t('admin_page.common.toggle_on') : t('admin_page.common.toggle_off')}
                  </CSToggle>
                </CSFormField>
                <CSFormField label={t('admin_page.registration.field_auto_approve')}>
                  <CSToggle
                    checked={!!regConfig.auto_approve}
                    onChange={({ detail }) => saveReg({ auto_approve: detail.checked })}
                  >
                    {regConfig.auto_approve ? t('admin_page.common.toggle_on') : t('admin_page.common.toggle_off')}
                  </CSToggle>
                </CSFormField>
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
              id: 'status', header: t('admin_page.registration.col_status'),
              cell: (c) => c.used_by
                ? <CSBadge color="grey">{t('admin_page.registration.status_used', { user: c.used_by })}</CSBadge>
                : c.expired_at && new Date(c.expired_at) < new Date()
                  ? <CSBadge color="red">{t('admin_page.registration.status_expired')}</CSBadge>
                  : <CSBadge color="green">{t('admin_page.registration.status_available')}</CSBadge>,
            },
            { id: 'expires', header: t('admin_page.registration.col_expires'), cell: (c) => fmtTime(c.expires_at || c.expired_at) },
            { id: 'created', header: t('admin_page.common.created_at'), cell: (c) => fmtTime(c.created_at) },
            {
              id: 'actions', header: t('admin_page.common.actions'),
              cell: (c) => !c.used_by
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

/* ─────────────────────────────────────────────────────────────────
   页面 7：AdminSecurityPage — 安全配置
   ───────────────────────────────────────────────────────────────── */
export function AdminSecurityPage() {
  const { t } = useTranslation();
  const [config, setConfig] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const [draft, setDraft] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const res = await window.api.admin.securityConfig();
        if (!cancelled) {
          setConfig(res);
          setDraft(JSON.parse(JSON.stringify(res)));
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  function upd(path, val) {
    setDraft((d) => {
      if (!d) return d;
      const next = JSON.parse(JSON.stringify(d));
      const keys = path.split('.');
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = val;
      return next;
    });
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      await window.api.admin.saveSecurityConfig(draft);
      setConfig(draft);
      window.toast?.(t('admin_page.security.save_ok'), { kind: 'ok' });
    } catch (e) {
      window.toast?.(t('admin_page.security.save_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSaving(false);
    }
  }

  const d = draft || {};

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      {loading
        ? <CSBox key="loading" color="inherit">{t('admin_page.common.loading')}</CSBox>
        : !draft
          ? <CSBox key="empty" textAlign="center" color="inherit">{t('admin_page.security.empty')}</CSBox>
          : null}
      {!loading && draft && (
        <CSContainer key="rate-limit" header={<CSHeader variant="h2">{t('admin_page.security.rate_limit_title')}</CSHeader>}>
          <CSAlert type="info">{t('admin_page.security.rate_limit_notice')}</CSAlert>
          <CSSpaceBetween size="m">
            <CSColumnLayout columns={3} variant="text-grid">
              <CSFormField label={t('admin_page.security.field_max_per_ip')}>
                <CSInput
                  type="number"
                  value={String(d.rate_limit?.max_per_ip ?? '')}
                  onChange={({ detail }) => upd('rate_limit.max_per_ip', Number(detail.value))}
                />
              </CSFormField>
              <CSFormField label={t('admin_page.security.field_max_per_user')}>
                <CSInput
                  type="number"
                  value={String(d.rate_limit?.max_per_user ?? '')}
                  onChange={({ detail }) => upd('rate_limit.max_per_user', Number(detail.value))}
                />
              </CSFormField>
              <CSFormField label={t('admin_page.security.field_window_min')}>
                <CSInput
                  type="number"
                  value={String(d.rate_limit?.window_minutes ?? '')}
                  onChange={({ detail }) => upd('rate_limit.window_minutes', Number(detail.value))}
                />
              </CSFormField>
            </CSColumnLayout>
          </CSSpaceBetween>
        </CSContainer>
      )}
      {!loading && draft && (
        <CSContainer key="password" header={<CSHeader variant="h2">{t('admin_page.security.password_title')}</CSHeader>}>
          <CSSpaceBetween size="m">
            <CSColumnLayout columns={2} variant="text-grid">
              <CSFormField label={t('admin_page.security.field_min_length')}>
                <CSInput
                  type="number"
                  value={String(d.password?.min_length ?? '')}
                  onChange={({ detail }) => upd('password.min_length', Number(detail.value))}
                />
              </CSFormField>
              <CSFormField label={t('admin_page.security.field_require_digit')}>
                <CSToggle
                  checked={!!d.password?.require_digit}
                  onChange={({ detail }) => upd('password.require_digit', detail.checked)}
                >
                  {d.password?.require_digit ? t('admin_page.security.digit_yes') : t('admin_page.security.digit_no')}
                </CSToggle>
              </CSFormField>
            </CSColumnLayout>
          </CSSpaceBetween>
        </CSContainer>
      )}
      {!loading && draft && (
        <CSContainer key="session" header={<CSHeader variant="h2">{t('admin_page.security.session_title')}</CSHeader>}>
          <CSFormField label={t('admin_page.security.field_session_timeout')}>
            <CSInput
              type="number"
              value={String(d.session?.timeout_days ?? '')}
              onChange={({ detail }) => upd('session.timeout_days', Number(detail.value))}
              style={{ maxWidth: 200 }}
            />
          </CSFormField>
        </CSContainer>
      )}
      {!loading && draft && (
        <CSContainer key="lockout" header={<CSHeader variant="h2">{t('admin_page.security.lockout_title')}</CSHeader>}>
          <CSColumnLayout columns={2} variant="text-grid">
            <CSFormField label={t('admin_page.security.field_max_attempts')}>
              <CSInput
                type="number"
                value={String(d.lockout?.max_attempts ?? '')}
                onChange={({ detail }) => upd('lockout.max_attempts', Number(detail.value))}
              />
            </CSFormField>
            <CSFormField label={t('admin_page.security.field_lockout_minutes')}>
              <CSInput
                type="number"
                value={String(d.lockout?.lockout_minutes ?? '')}
                onChange={({ detail }) => upd('lockout.lockout_minutes', Number(detail.value))}
              />
            </CSFormField>
          </CSColumnLayout>
        </CSContainer>
      )}
      {!loading && draft && (
        <CSContainer key="ip-blocklist" header={<CSHeader variant="h2">{t('admin_page.security.ip_blocklist_title')}</CSHeader>}>
          <CSFormField label={t('admin_page.security.field_ip_blocklist')}>
            <CSTextarea
              value={Array.isArray(d.ip_blocklist) ? d.ip_blocklist.join('\n') : (d.ip_blocklist || '')}
              onChange={({ detail }) => upd('ip_blocklist', detail.value.split('\n').map((s) => s.trim()).filter(Boolean))}
              rows={6}
              placeholder="192.168.1.1&#10;10.0.0.0/8"
            />
          </CSFormField>
        </CSContainer>
      )}
      {!loading && draft && (
        <CSBox key="save-btn" float="right">
          <CSButton variant="primary" loading={saving} onClick={save}>{t('admin_page.security.save_btn')}</CSButton>
        </CSBox>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 9：AdminDmcaTakedownsPage — DMCA 下架队列
   ───────────────────────────────────────────────────────────────── */
export function AdminDmcaTakedownsPage() {
  const { t } = useTranslation();
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState({ value: 'open', label: t('admin_page.dmca_takedowns.status_open') });
  const [actionModal, setActionModal] = React.useState(null); // { item, action }
  const [actionReason, setActionReason] = React.useState('');
  const [actionBusy, setActionBusy] = React.useState(false);
  const [createModal, setCreateModal] = React.useState(false);
  const [createForm, setCreateForm] = React.useState({
    complainant_name: '', complainant_email: '', infringing_url: '', original_work_desc: '',
  });
  const [creating, setCreating] = React.useState(false);
  const [counterModal, setCounterModal] = React.useState(null); // item
  const [counterNotes, setCounterNotes] = React.useState('');
  const [counterBusy, setCounterBusy] = React.useState(false);

  const statusOptions = [
    { value: 'open', label: t('admin_page.dmca_takedowns.status_open') },
    { value: 'counter_received', label: t('admin_page.dmca_takedowns.status_counter') },
    { value: 'closed', label: t('admin_page.dmca_takedowns.status_closed') },
    { value: 'restored', label: t('admin_page.dmca_takedowns.status_restored') },
    { value: 'rejected', label: t('admin_page.dmca_takedowns.status_rejected') },
    { value: 'all', label: t('admin_page.dmca_takedowns.status_all') },
  ];

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.dmcaTakedowns({ status: statusFilter.value });
      setItems(res.takedowns || res || []);
    } catch (e) {
      setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      setLoading(false);
    }
  }, [statusFilter.value]);

  React.useEffect(() => { load(); }, [load]);

  async function doAction() {
    if (!actionModal) return;
    setActionBusy(true);
    try {
      await window.api.admin.dmcaTakedownAction(actionModal.item.id, {
        action: actionModal.action, reason: actionReason,
      });
      window.toast?.(t('admin_page.dmca_takedowns.op_ok'), { kind: 'ok' });
      setActionModal(null);
      setActionReason('');
      load();
    } catch (e) {
      window.toast?.(t('admin_page.dmca_takedowns.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setActionBusy(false);
    }
  }

  async function doCreate() {
    setCreating(true);
    try {
      await window.api.admin.dmcaTakedownCreate(createForm);
      window.toast?.(t('admin_page.dmca_takedowns.create_ok'), { kind: 'ok' });
      setCreateModal(false);
      setCreateForm({ complainant_name: '', complainant_email: '', infringing_url: '', original_work_desc: '' });
      load();
    } catch (e) {
      window.toast?.(t('admin_page.dmca_takedowns.create_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setCreating(false);
    }
  }

  async function doCounter() {
    if (!counterModal) return;
    setCounterBusy(true);
    try {
      await window.api.admin.dmcaTakedownCounter(counterModal.id, { notes: counterNotes });
      window.toast?.(t('admin_page.dmca_takedowns.counter_ok'), { kind: 'ok' });
      setCounterModal(null);
      setCounterNotes('');
      load();
    } catch (e) {
      window.toast?.(t('admin_page.common.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setCounterBusy(false);
    }
  }

  function statusBadge(s) {
    const map = {
      open: ['red', t('admin_page.dmca_takedowns.status_open')],
      counter_received: ['blue', t('admin_page.dmca_takedowns.status_counter')],
      closed: ['grey', t('admin_page.dmca_takedowns.status_closed')],
      restored: ['green', t('admin_page.dmca_takedowns.status_restored')],
      rejected: ['severity-low', t('admin_page.dmca_takedowns.status_rejected')],
    };
    const [color, label] = map[s] || ['grey', s];
    return <CSBadge color={color}>{label}</CSBadge>;
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.dmca_takedowns.description')}
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={statusFilter}
                  options={statusOptions}
                  onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                />
                <CSButton variant="primary" onClick={() => setCreateModal(true)}>{t('admin_page.dmca_takedowns.create_btn')}</CSButton>
                <CSButton iconName="refresh" onClick={load} loading={loading}>{t('admin_page.common.refresh')}</CSButton>
              </CSSpaceBetween>
            }
          >
            {t('admin_page.dmca_takedowns.title')}
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="id"
          items={items}
          empty={<CSBox textAlign="center" color="inherit">{t('admin_page.dmca_takedowns.empty')}</CSBox>}
          columnDefinitions={[
            { id: 'id', header: t('admin_page.dmca_takedowns.col_id'), cell: (r) => `#${r.id}`, width: 60 },
            { id: 'complainant', header: t('admin_page.dmca_takedowns.col_complainant'), cell: (r) => `${r.complainant_name || '—'} <${r.complainant_email || '—'}>` },
            { id: 'url', header: t('admin_page.dmca_takedowns.col_url'), cell: (r) => <a href={r.infringing_url} target="_blank" rel="noopener noreferrer" style={{ wordBreak: 'break-all' }}>{r.infringing_url}</a> },
            { id: 'status', header: t('admin_page.dmca_takedowns.col_status'), cell: (r) => statusBadge(r.status) },
            { id: 'restore_after', header: t('admin_page.dmca_takedowns.col_restore_after'), cell: (r) => r.restore_after ? fmtTime(r.restore_after) : '—' },
            { id: 'created_at', header: t('admin_page.dmca_takedowns.col_created_at'), cell: (r) => fmtTime(r.created_at) },
            {
              id: 'actions', header: t('admin_page.common.actions'),
              cell: (r) => (
                <CSSpaceBetween direction="horizontal" size="xs">
                  {r.status === 'open' && (
                    <CSButton key="takedown" variant="inline-link" onClick={() => { setActionModal({ item: r, action: 'takedown' }); setActionReason(''); }}>{t('admin_page.dmca_takedowns.btn_takedown')}</CSButton>
                  )}
                  {r.status === 'open' && (
                    <CSButton key="reject" variant="inline-link" onClick={() => { setActionModal({ item: r, action: 'reject' }); setActionReason(''); }}>{t('admin_page.dmca_takedowns.btn_reject')}</CSButton>
                  )}
                  {r.status === 'closed' && (
                    <CSButton key="counter" variant="inline-link" onClick={() => { setCounterModal(r); setCounterNotes(''); }}>{t('admin_page.dmca_takedowns.btn_counter')}</CSButton>
                  )}
                  {r.status === 'counter_received' && r.restore_after && new Date(r.restore_after) <= new Date() && (
                    <CSButton key="restore" variant="inline-link" onClick={() => { setActionModal({ item: r, action: 'restore' }); setActionReason(t('admin_page.dmca_takedowns.restore_default_reason')); }}>{t('admin_page.dmca_takedowns.btn_restore')}</CSButton>
                  )}
                </CSSpaceBetween>
              ),
            },
          ]}
        />
      </CSContainer>

      {/* create notice modal */}
      {createModal && (
        <CSModal
          visible
          onDismiss={() => !creating && setCreateModal(false)}
          header={t('admin_page.dmca_takedowns.create_modal_title')}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={creating} onClick={() => setCreateModal(false)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={creating} onClick={doCreate}>{t('admin_page.common.submit')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSFormField label={t('admin_page.dmca_takedowns.field_complainant_name')}>
              <CSInput value={createForm.complainant_name} onChange={({ detail }) => setCreateForm((f) => ({ ...f, complainant_name: detail.value }))} />
            </CSFormField>
            <CSFormField label={t('admin_page.dmca_takedowns.field_complainant_email')}>
              <CSInput value={createForm.complainant_email} onChange={({ detail }) => setCreateForm((f) => ({ ...f, complainant_email: detail.value }))} type="email" />
            </CSFormField>
            <CSFormField label={t('admin_page.dmca_takedowns.field_infringing_url')}>
              <CSInput value={createForm.infringing_url} onChange={({ detail }) => setCreateForm((f) => ({ ...f, infringing_url: detail.value }))} placeholder="https://play.stellatrix.icu/..." />
            </CSFormField>
            <CSFormField label={t('admin_page.dmca_takedowns.field_original_work')}>
              <CSTextarea value={createForm.original_work_desc} onChange={({ detail }) => setCreateForm((f) => ({ ...f, original_work_desc: detail.value }))} rows={3} />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {/* action modal */}
      {actionModal && (
        <CSModal
          visible
          onDismiss={() => !actionBusy && setActionModal(null)}
          header={t('admin_page.dmca_takedowns.action_modal_title', { action: actionModal.action === 'takedown' ? t('admin_page.dmca_takedowns.action_takedown_label') : actionModal.action === 'restore' ? t('admin_page.dmca_takedowns.action_restore_label') : t('admin_page.dmca_takedowns.action_reject_label') })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={actionBusy} onClick={() => setActionModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={actionBusy} onClick={doAction}>{t('admin_page.common.confirm')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSBox>{t('admin_page.dmca_takedowns.action_record_label', { id: actionModal.item.id, url: actionModal.item.infringing_url })}</CSBox>
            <CSFormField label={t('admin_page.dmca_takedowns.action_reason_label')}>
              <CSTextarea value={actionReason} onChange={({ detail }) => setActionReason(detail.value)} rows={3} placeholder={t('admin_page.dmca_takedowns.action_reason_placeholder')} />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {/* counter notice modal */}
      {counterModal && (
        <CSModal
          visible
          onDismiss={() => !counterBusy && setCounterModal(null)}
          header={t('admin_page.dmca_takedowns.counter_modal_title')}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={counterBusy} onClick={() => setCounterModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={counterBusy} onClick={doCounter}>{t('admin_page.common.submit')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="info">{t('admin_page.dmca_takedowns.counter_info')}</CSAlert>
            <CSFormField label={t('admin_page.dmca_takedowns.counter_notes_label')}>
              <CSTextarea value={counterNotes} onChange={({ detail }) => setCounterNotes(detail.value)} rows={3} placeholder={t('admin_page.dmca_takedowns.counter_notes_placeholder')} />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 10：AdminDmcaStrikesPage — Strike 管理
   ───────────────────────────────────────────────────────────────── */
export function AdminDmcaStrikesPage() {
  const { t } = useTranslation();
  const [users, setUsers] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [strikeModal, setStrikeModal] = React.useState(null); // { user_id, username }
  const [strikeReason, setStrikeReason] = React.useState('');
  const [strikeBusy, setStrikeBusy] = React.useState(false);
  const [expanded, setExpanded] = React.useState(null); // user_id

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.dmcaStrikes();
      setUsers(res.users || []);
    } catch (e) {
      setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function doStrike() {
    if (!strikeModal) return;
    setStrikeBusy(true);
    try {
      const res = await window.api.admin.dmcaStrikeIncrement(strikeModal.user_id, { reason: strikeReason });
      if (res.terminate) {
        window.toast?.(t('admin_page.dmca_strikes.strike_added_terminated', { count: res.strike_count }), { kind: 'danger', duration: 8000 });
      } else {
        window.toast?.(t('admin_page.dmca_strikes.strike_added_ok', { count: res.strike_count }), { kind: 'ok' });
      }
      setStrikeModal(null);
      setStrikeReason('');
      load();
    } catch (e) {
      window.toast?.(t('admin_page.dmca_strikes.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setStrikeBusy(false);
    }
  }

  function strikeBadgeColor(count) {
    if (count >= 3) return 'red';
    if (count === 2) return 'severity-medium';
    return 'severity-low';
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.dmca_strikes.description')}
            actions={<CSButton iconName="refresh" onClick={load} loading={loading}>{t('admin_page.common.refresh')}</CSButton>}
          >
            {t('admin_page.dmca_strikes.title')}
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="user_id"
          items={users}
          empty={<CSBox textAlign="center" color="inherit">{t('admin_page.dmca_strikes.empty')}</CSBox>}
          columnDefinitions={[
            { id: 'username', header: t('admin_page.dmca_strikes.col_username'), cell: (u) => u.username || `uid:${u.user_id}` },
            {
              id: 'count', header: t('admin_page.dmca_strikes.col_count'),
              cell: (u) => <CSBadge color={strikeBadgeColor(u.strike_count)}>{u.strike_count} / 3</CSBadge>,
            },
            {
              id: 'history', header: t('admin_page.dmca_strikes.col_history'),
              cell: (u) => {
                const isExp = expanded === u.user_id;
                return (
                  <div>
                    <CSButton variant="inline-link" onClick={() => setExpanded(isExp ? null : u.user_id)}>
                      {isExp ? t('admin_page.common.collapse') : t('admin_page.common.expand')}
                    </CSButton>
                    {isExp && (
                      <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: 12 }}>
                        {(u.strikes || []).map((s) => (
                          <li key={s.id}><code>{fmtTime(s.created_at)}</code> — {s.reason}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              },
            },
            {
              id: 'actions', header: t('admin_page.common.actions'),
              cell: (u) => u.strike_count < 3 && (
                <CSButton
                  variant="inline-link"
                  onClick={() => { setStrikeModal({ user_id: u.user_id, username: u.username }); setStrikeReason(''); }}
                >
                  {t('admin_page.dmca_strikes.btn_add')}
                </CSButton>
              ),
            },
          ]}
        />
      </CSContainer>

      {strikeModal && (
        <CSModal
          visible
          onDismiss={() => !strikeBusy && setStrikeModal(null)}
          header={t('admin_page.dmca_strikes.strike_modal_title', { name: strikeModal.username })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={strikeBusy} onClick={() => setStrikeModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={strikeBusy} onClick={doStrike}>{t('admin_page.dmca_strikes.strike_confirm_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="warning">{t('admin_page.dmca_strikes.strike_warning')}</CSAlert>
            <CSFormField label={t('admin_page.dmca_strikes.strike_reason_label')}>
              <CSTextarea
                value={strikeReason}
                onChange={({ detail }) => setStrikeReason(detail.value)}
                rows={3}
                placeholder={t('admin_page.dmca_strikes.strike_reason_placeholder')}
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 11：AdminCsamReportsPage — CSAM 举报管理
   ───────────────────────────────────────────────────────────────── */
export function AdminCsamReportsPage() {
  const { t } = useTranslation();
  const [reports, setReports] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState({ value: 'pending', label: t('admin_page.csam.status_pending') });
  const [decisionModal, setDecisionModal] = React.useState(null); // report item
  const [decisionForm, setDecisionForm] = React.useState({ decision: '', notes: '' });
  const [deciding, setDeciding] = React.useState(false);

  const statusOptions = [
    { value: 'pending', label: t('admin_page.csam.status_pending') },
    { value: 'decided', label: t('admin_page.csam.status_decided') },
    { value: 'all', label: t('admin_page.csam.status_all') },
  ];
  const decisionOptions = [
    { value: 'founded', label: t('admin_page.csam.decision_founded') },
    { value: 'escalate', label: t('admin_page.csam.decision_escalate') },
    { value: 'unfounded', label: t('admin_page.csam.decision_unfounded') },
  ];

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.csamReports({ status: statusFilter.value });
      setReports(res.reports || []);
    } catch (e) {
      setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      setLoading(false);
    }
  }, [statusFilter.value]);

  React.useEffect(() => { load(); }, [load]);

  async function doDecision() {
    if (!decisionModal || !decisionForm.decision) return;
    setDeciding(true);
    try {
      await window.api.admin.csamDecision(decisionModal.id, decisionForm);
      window.toast?.(t('admin_page.csam.decided_ok'), { kind: 'ok' });
      setDecisionModal(null);
      setDecisionForm({ decision: '', notes: '' });
      load();
    } catch (e) {
      window.toast?.(t('admin_page.csam.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setDeciding(false);
    }
  }

  function decisionBadge(d) {
    const map = {
      founded: ['red', t('admin_page.csam.badge_founded')],
      escalate: ['blue', t('admin_page.csam.badge_escalate')],
      unfounded: ['grey', t('admin_page.csam.badge_unfounded')],
    };
    const [color, label] = map[d] || ['grey', d || '—'];
    return <CSBadge color={color}>{label}</CSBadge>;
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}
      <CSAlert type="warning">{t('admin_page.csam.warning')}</CSAlert>
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.csam.description')}
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={statusFilter}
                  options={statusOptions}
                  onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                />
                <CSButton iconName="refresh" onClick={load} loading={loading}>{t('admin_page.common.refresh')}</CSButton>
              </CSSpaceBetween>
            }
          >
            {t('admin_page.csam.title')}
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="id"
          items={reports}
          empty={<CSBox textAlign="center" color="inherit">{t('admin_page.csam.empty')}</CSBox>}
          columnDefinitions={[
            { id: 'id', header: t('admin_page.csam.col_id'), cell: (r) => `#${r.id}`, width: 60 },
            { id: 'reported_user', header: t('admin_page.csam.col_reported_user'), cell: (r) => r.reported_username || `uid:${r.reported_user_id}` || '—' },
            { id: 'content_url', header: t('admin_page.csam.col_content'), cell: (r) => r.content_url || t('admin_page.csam.content_no_url') },
            { id: 'status', header: t('admin_page.csam.col_status'), cell: (r) => r.status === 'pending' ? <CSBadge color="red">{t('admin_page.csam.badge_pending')}</CSBadge> : <CSBadge color="grey">{t('admin_page.csam.badge_decided')}</CSBadge> },
            { id: 'decision', header: t('admin_page.csam.col_decision'), cell: (r) => r.decision ? decisionBadge(r.decision) : '—' },
            { id: 'cybertip', header: t('admin_page.csam.col_cybertip'), cell: (r) => r.cybertip_report_id || '—' },
            { id: 'created_at', header: t('admin_page.csam.col_created_at'), cell: (r) => fmtTime(r.created_at) },
            {
              id: 'actions', header: t('admin_page.common.actions'),
              cell: (r) => r.status === 'pending' && (
                <CSButton
                  variant="inline-link"
                  onClick={() => { setDecisionModal(r); setDecisionForm({ decision: '', notes: '' }); }}
                >
                  {t('admin_page.csam.btn_decide')}
                </CSButton>
              ),
            },
          ]}
        />
      </CSContainer>

      {decisionModal && (
        <CSModal
          visible
          onDismiss={() => !deciding && setDecisionModal(null)}
          header={t('admin_page.csam.decision_modal_title', { id: decisionModal.id })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={deciding} onClick={() => setDecisionModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={deciding} disabled={!decisionForm.decision} onClick={doDecision}>{t('admin_page.common.confirm')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="warning">{t('admin_page.csam.decision_warning')}</CSAlert>
            <CSFormField label={t('admin_page.csam.decision_field')}>
              <CSSelect
                selectedOption={decisionOptions.find((o) => o.value === decisionForm.decision) || { value: '', label: t('admin_page.csam.decision_select_placeholder') }}
                options={decisionOptions}
                onChange={({ detail }) => setDecisionForm((f) => ({ ...f, decision: detail.selectedOption.value }))}
              />
            </CSFormField>
            <CSFormField label={t('admin_page.csam.notes_field')}>
              <CSTextarea
                value={decisionForm.notes}
                onChange={({ detail }) => setDecisionForm((f) => ({ ...f, notes: detail.value }))}
                rows={3}
                placeholder={t('admin_page.csam.notes_placeholder')}
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 12：AdminAupActionsPage — AUP 账户暂停 / 解封 / 终止
   ───────────────────────────────────────────────────────────────── */
export function AdminAupActionsPage() {
  const { t } = useTranslation();
  const [search, setSearch] = React.useState('');
  const [users, setUsers] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const [suspendModal, setSuspendModal] = React.useState(null); // user
  const [suspendForm, setSuspendForm] = React.useState({ reason: '', duration_days: '' });
  const [suspendBusy, setSuspendBusy] = React.useState(false);
  const [unsuspendModal, setUnsuspendModal] = React.useState(null); // user
  const [unsuspendBusy, setUnsuspendBusy] = React.useState(false);
  const [terminateModal, setTerminateModal] = React.useState(null); // user
  const [terminateReason, setTerminateReason] = React.useState('');
  const [terminateBusy, setTerminateBusy] = React.useState(false);

  async function doSearch() {
    if (!search.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.users({ search, limit: 20 });
      setUsers(res.users || []);
    } catch (e) {
      setErr(e?.message || t('admin_page.aup.search_fail'));
    } finally {
      setLoading(false);
    }
  }

  async function doSuspend() {
    if (!suspendModal) return;
    setSuspendBusy(true);
    try {
      const body = { reason: suspendForm.reason };
      if (suspendForm.duration_days) body.duration_days = Number(suspendForm.duration_days);
      await window.api.admin.suspendUser(suspendModal.id, body);
      window.toast?.(t('admin_page.aup.suspend_ok'), { kind: 'ok' });
      setSuspendModal(null);
      setSuspendForm({ reason: '', duration_days: '' });
      doSearch();
    } catch (e) {
      window.toast?.(t('admin_page.common.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSuspendBusy(false);
    }
  }

  async function doUnsuspend() {
    if (!unsuspendModal) return;
    setUnsuspendBusy(true);
    try {
      await window.api.admin.unsuspendUser(unsuspendModal.id);
      window.toast?.(t('admin_page.aup.unsuspend_ok'), { kind: 'ok' });
      setUnsuspendModal(null);
      doSearch();
    } catch (e) {
      window.toast?.(t('admin_page.common.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setUnsuspendBusy(false);
    }
  }

  async function doTerminate() {
    if (!terminateModal) return;
    setTerminateBusy(true);
    try {
      await window.api.admin.terminateUser(terminateModal.id, { reason: terminateReason });
      window.toast?.(t('admin_page.aup.terminate_ok'), { kind: 'ok', duration: 6000 });
      setTerminateModal(null);
      setTerminateReason('');
      doSearch();
    } catch (e) {
      window.toast?.(t('admin_page.common.op_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setTerminateBusy(false);
    }
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.aup.error_title')}>{err}</CSAlert>}

      <CSContainer
        header={
          <CSHeader variant="h2" description={t('admin_page.aup.description')}>
            {t('admin_page.aup.title')}
          </CSHeader>
        }
      >
        <CSSpaceBetween size="m">
          <CSAlert type="info">{t('admin_page.aup.info')}</CSAlert>
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSInput
              placeholder={t('admin_page.aup.search_placeholder')}
              value={search}
              onChange={({ detail }) => setSearch(detail.value)}
              onKeyDown={({ detail }) => { if (detail.key === 'Enter') doSearch(); }}
              type="search"
            />
            <CSButton onClick={doSearch} loading={loading}>{t('admin_page.aup.search_btn')}</CSButton>
          </CSSpaceBetween>

          {users.length > 0 && (
            <CSTable
              loading={loading}
              loadingText={t('admin_page.common.loading')}
              trackBy="id"
              items={users}
              empty={<CSBox textAlign="center" color="inherit">{t('admin_page.aup.no_results')}</CSBox>}
              columnDefinitions={[
                { id: 'username', header: t('admin_page.aup.col_username'), cell: (u) => u.username },
                { id: 'display_name', header: t('admin_page.aup.col_display_name'), cell: (u) => u.display_name || '—' },
                {
                  id: 'status', header: t('admin_page.aup.col_status'),
                  cell: (u) => u.deactivated_at
                    ? <CSStatusIndicator type="stopped">{t('admin_page.aup.status_suspended')}</CSStatusIndicator>
                    : <CSStatusIndicator type="success">{t('admin_page.aup.status_active')}</CSStatusIndicator>,
                },
                { id: 'ban_reason', header: t('admin_page.aup.col_ban_reason'), cell: (u) => u.ban_reason || '—' },
                {
                  id: 'actions', header: t('admin_page.common.actions'),
                  cell: (u) => (
                    <CSSpaceBetween direction="horizontal" size="xs">
                      {!u.deactivated_at && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => { setSuspendModal(u); setSuspendForm({ reason: '', duration_days: '' }); }}
                        >
                          {t('admin_page.aup.btn_suspend')}
                        </CSButton>
                      )}
                      {u.deactivated_at && (
                        <CSButton variant="inline-link" onClick={() => setUnsuspendModal(u)}>{t('admin_page.aup.btn_unsuspend')}</CSButton>
                      )}
                      <CSButton
                        variant="inline-link"
                        onClick={() => { setTerminateModal(u); setTerminateReason(''); }}
                      >
                        {t('admin_page.aup.btn_terminate')}
                      </CSButton>
                    </CSSpaceBetween>
                  ),
                },
              ]}
            />
          )}
        </CSSpaceBetween>
      </CSContainer>

      {/* suspend modal */}
      {suspendModal && (
        <CSModal
          visible
          onDismiss={() => !suspendBusy && setSuspendModal(null)}
          header={t('admin_page.aup.suspend_modal_title', { name: suspendModal.username })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={suspendBusy} onClick={() => setSuspendModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={suspendBusy} disabled={!suspendForm.reason} onClick={doSuspend}>{t('admin_page.aup.suspend_confirm_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSFormField label={t('admin_page.aup.suspend_reason_label')}>
              <CSTextarea
                value={suspendForm.reason}
                onChange={({ detail }) => setSuspendForm((f) => ({ ...f, reason: detail.value }))}
                rows={3}
                placeholder={t('admin_page.aup.suspend_reason_placeholder')}
              />
            </CSFormField>
            <CSFormField label={t('admin_page.aup.suspend_days_label')}>
              <CSInput
                type="number"
                value={suspendForm.duration_days}
                onChange={({ detail }) => setSuspendForm((f) => ({ ...f, duration_days: detail.value }))}
                placeholder={t('admin_page.aup.suspend_days_placeholder')}
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {/* unsuspend modal */}
      {unsuspendModal && (
        <CSModal
          visible
          onDismiss={() => !unsuspendBusy && setUnsuspendModal(null)}
          header={t('admin_page.aup.unsuspend_modal_title', { name: unsuspendModal.username })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={unsuspendBusy} onClick={() => setUnsuspendModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={unsuspendBusy} onClick={doUnsuspend}>{t('admin_page.aup.unsuspend_confirm_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>{t('admin_page.aup.unsuspend_confirm', { name: unsuspendModal.username })}</CSBox>
        </CSModal>
      )}

      {/* terminate modal */}
      {terminateModal && (
        <CSModal
          visible
          onDismiss={() => !terminateBusy && setTerminateModal(null)}
          header={t('admin_page.aup.terminate_modal_title', { name: terminateModal.username })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={terminateBusy} onClick={() => setTerminateModal(null)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={terminateBusy} disabled={!terminateReason} onClick={doTerminate}>{t('admin_page.aup.terminate_confirm_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="error">{t('admin_page.aup.terminate_warning')}</CSAlert>
            <CSFormField label={t('admin_page.aup.terminate_reason_label')}>
              <CSTextarea
                value={terminateReason}
                onChange={({ detail }) => setTerminateReason(detail.value)}
                rows={3}
                placeholder={t('admin_page.aup.terminate_reason_placeholder')}
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 8：AdminMaintenancePage — 维护模式
   ───────────────────────────────────────────────────────────────── */
export function AdminMaintenancePage() {
  const { t } = useTranslation();
  const [config, setConfig] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const [draft, setDraft] = React.useState(null);
  const [restartModal, setRestartModal] = React.useState(false);
  const [restarting, setRestarting] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const res = await window.api.admin.maintenance();
        if (!cancelled) {
          setConfig(res);
          setDraft(JSON.parse(JSON.stringify(res)));
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('admin_page.common.load_fail'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      await window.api.admin.saveMaintenance(draft);
      setConfig(draft);
      window.toast?.(t('admin_page.maintenance.save_ok'), { kind: 'ok' });
    } catch (e) {
      window.toast?.(t('admin_page.maintenance.save_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSaving(false);
    }
  }

  async function handleRestart() {
    setRestarting(true);
    try {
      await window.api.admin.restart();
      window.toast?.(t('admin_page.maintenance.restart_ok'), { kind: 'ok', duration: 5000 });
      setRestartModal(false);
    } catch (e) {
      window.toast?.(t('admin_page.maintenance.restart_fail') + ': ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setRestarting(false);
    }
  }

  const d = draft || {};

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}

      <CSContainer header={<CSHeader variant="h2" description={t('admin_page.maintenance.mode_description')}>{t('admin_page.maintenance.mode_title')}</CSHeader>}>
        {loading
          ? <CSBox color="inherit">{t('admin_page.common.loading')}</CSBox>
          : !draft
            ? <CSBox textAlign="center" color="inherit">{t('admin_page.maintenance.empty')}</CSBox>
            : (
              <CSSpaceBetween size="m">
                {d.enabled && (
                  <CSAlert type="warning">{t('admin_page.maintenance.mode_warning')}</CSAlert>
                )}
                <CSFormField label={t('admin_page.maintenance.field_toggle')}>
                  <CSToggle
                    checked={!!d.enabled}
                    onChange={({ detail }) => setDraft((prev) => ({ ...prev, enabled: detail.checked }))}
                  >
                    {d.enabled ? t('admin_page.common.toggle_on') : t('admin_page.common.toggle_off')}
                  </CSToggle>
                </CSFormField>
                <CSFormField label={t('admin_page.maintenance.field_message')}>
                  <CSTextarea
                    value={d.message || ''}
                    onChange={({ detail }) => setDraft((prev) => ({ ...prev, message: detail.value }))}
                    rows={4}
                    placeholder={t('admin_page.maintenance.message_placeholder')}
                  />
                </CSFormField>
                {d.started_at && (
                  <CSFormField label={t('admin_page.maintenance.field_started_at')}>
                    <CSBox color="text-body-secondary">{fmtTime(d.started_at)}</CSBox>
                  </CSFormField>
                )}
                <CSBox float="right">
                  <CSButton variant="primary" loading={saving} onClick={save}>{t('common.save')}</CSButton>
                </CSBox>
              </CSSpaceBetween>
            )
        }
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2" description={t('admin_page.maintenance.restart_description')}>{t('admin_page.maintenance.restart_title')}</CSHeader>}>
        <CSSpaceBetween size="m">
          <CSAlert type="warning">{t('admin_page.maintenance.restart_warning')}</CSAlert>
          <CSButton
            variant="normal"
            iconName="status-warning"
            onClick={() => setRestartModal(true)}
          >
            {t('admin_page.maintenance.restart_btn')}
          </CSButton>
        </CSSpaceBetween>
      </CSContainer>

      {restartModal && (
        <CSModal
          visible
          onDismiss={() => !restarting && setRestartModal(false)}
          header={t('admin_page.maintenance.restart_modal_title')}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={restarting} onClick={() => setRestartModal(false)}>{t('admin_page.common.cancel')}</CSButton>
                <CSButton variant="primary" loading={restarting} onClick={handleRestart}>{t('admin_page.maintenance.restart_confirm_btn')}</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>{t('admin_page.maintenance.restart_modal_body')}</CSBox>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   AdminFeedbackPage — 反馈审查队列 (FB-03)
   ───────────────────────────────────────────────────────────────── */
export function AdminFeedbackPage() {
  const { t } = useTranslation();
  const [items, setItems]           = React.useState([]);
  const [loading, setLoading]       = React.useState(true);
  const [err, setErr]               = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState({ value: 'unreviewed', label: t('admin_page.feedback.status_unreviewed') });
  const [detailModal, setDetailModal]   = React.useState(null); // feedback item
  const [actionBusy, setActionBusy]     = React.useState(false);
  const [actionErr, setActionErr]       = React.useState(null);
  const [terminateReason, setTerminateReason] = React.useState('');

  const statusOptions = [
    { value: 'unreviewed', label: t('admin_page.feedback.status_unreviewed') },
    { value: 'reviewed',   label: t('admin_page.feedback.status_reviewed') },
    { value: 'all',        label: t('admin_page.feedback.status_all') },
  ];

  const load = React.useCallback(async (filter) => {
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(
        `/api/admin/feedback?status=${encodeURIComponent(filter)}&limit=50`,
        { credentials: 'include' },
      );
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      setItems(data.items || []);
    } catch (e) {
      setErr(e?.message || t('admin_page.common.load_fail'));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { load(statusFilter.value); }, [statusFilter.value]);

  async function doDecision(feedbackId, decision, notes) {
    setActionBusy(true);
    setActionErr(null);
    try {
      const res = await fetch(`/api/admin/feedback/${feedbackId}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ decision, notes: notes || '' }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      window.toast?.(t('admin_page.feedback.op_ok'), { kind: 'ok' });
      setDetailModal(null);
      setTerminateReason('');
      load(statusFilter.value);
    } catch (e) {
      setActionErr(e?.message || t('admin_page.feedback.op_fail'));
    } finally {
      setActionBusy(false);
    }
  }

  const decisionBadge = (d) => {
    if (!d) return <CSBadge color="grey">{t('admin_page.feedback.badge_pending')}</CSBadge>;
    if (d === 'ok') return <CSBadge color="green">OK</CSBadge>;
    if (d === 'nsfw_terminate') return <CSBadge color="red">{t('admin_page.feedback.badge_terminate')}</CSBadge>;
    if (d === 'spam') return <CSBadge color="severity-medium">{t('admin_page.feedback.badge_spam')}</CSBadge>;
    return <CSBadge color="grey">{d}</CSBadge>;
  };

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header={t('admin_page.common.load_fail')}>{err}</CSAlert>}

      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description={t('admin_page.feedback.description')}
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={statusFilter}
                  options={statusOptions}
                  onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                />
                <CSButton iconName="refresh" onClick={() => load(statusFilter.value)} loading={loading}>
                  {t('admin_page.common.refresh')}
                </CSButton>
              </CSSpaceBetween>
            }
          >
            {t('admin_page.feedback.title')}
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText={t('admin_page.common.loading')}
          trackBy="id"
          items={items}
          empty={
            <CSBox textAlign="center" color="inherit">
              <CSBox padding={{ bottom: 's' }} variant="p" color="inherit">{t('admin_page.feedback.empty')}</CSBox>
            </CSBox>
          }
          columnDefinitions={[
            { id: 'id',      header: t('admin_page.feedback.col_id'),      cell: (f) => f.id },
            { id: 'user',    header: t('admin_page.feedback.col_user'),     cell: (f) => f.username || '—' },
            { id: 'ts',      header: t('admin_page.feedback.col_ts'),       cell: (f) => fmtTime(f.created_at) },
            { id: 'status',  header: t('admin_page.feedback.col_status'),   cell: (f) => decisionBadge(f.review_decision) },
            {
              id: 'preview', header: t('admin_page.feedback.col_preview'),
              cell: (f) => (
                <span style={{ maxWidth: 300, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {(f.free_text || '').slice(0, 80) || t('admin_page.feedback.detail_empty')}
                </span>
              ),
            },
            {
              id: 'actions', header: t('admin_page.feedback.col_actions'),
              cell: (f) => (
                <CSButton variant="inline-link" onClick={() => { setDetailModal(f); setActionErr(null); setTerminateReason(''); }}>
                  {t('admin_page.feedback.btn_view')}
                </CSButton>
              ),
            },
          ]}
        />
      </CSContainer>

      {/* detail + action modal */}
      {detailModal && (
        <CSModal
          visible
          size="large"
          onDismiss={() => !actionBusy && setDetailModal(null)}
          header={t('admin_page.feedback.detail_modal_title', { id: detailModal.id, user: detailModal.username })}
          footer={
            !detailModal.review_decision ? (
              <CSBox float="right">
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton variant="link" disabled={actionBusy} onClick={() => setDetailModal(null)}>{t('admin_page.feedback.btn_cancel')}</CSButton>
                  <CSButton variant="normal" loading={actionBusy} onClick={() => doDecision(detailModal.id, 'spam')}>
                    {t('admin_page.feedback.btn_spam')}
                  </CSButton>
                  <CSButton variant="primary" loading={actionBusy} onClick={() => doDecision(detailModal.id, 'ok')}>
                    {t('admin_page.feedback.btn_ok')}
                  </CSButton>
                  <CSButton
                    variant="primary"
                    iconName="status-warning"
                    loading={actionBusy}
                    disabled={!terminateReason.trim()}
                    onClick={() => doDecision(detailModal.id, 'nsfw_terminate', terminateReason)}
                  >
                    {t('admin_page.feedback.btn_terminate_nsfw')}
                  </CSButton>
                </CSSpaceBetween>
              </CSBox>
            ) : (
              <CSBox float="right">
                <CSButton variant="link" onClick={() => setDetailModal(null)}>{t('admin_page.feedback.btn_close')}</CSButton>
              </CSBox>
            )
          }
        >
          <CSSpaceBetween size="m">
            {actionErr && <CSAlert type="error">{actionErr}</CSAlert>}

            <CSBox>
              <strong>{t('admin_page.feedback.detail_submit_time')}</strong>{fmtTime(detailModal.created_at)}
              {'　'}
              <strong>{t('admin_page.feedback.detail_status_label')}</strong>{decisionBadge(detailModal.review_decision)}
              {detailModal.reviewed_at && (
                <span>{'　'}<strong>{t('admin_page.feedback.detail_review_time')}</strong>{fmtTime(detailModal.reviewed_at)}</span>
              )}
            </CSBox>

            <CSBox>
              <strong>{t('admin_page.feedback.detail_free_text')}</strong>
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--color-background-container-content)', padding: 8, borderRadius: 4 }}>
                {detailModal.free_text || t('admin_page.feedback.detail_empty')}
              </pre>
            </CSBox>

            {Array.isArray(detailModal.excerpts) && detailModal.excerpts.length > 0 && (
              <CSBox>
                <strong>{t('admin_page.feedback.detail_excerpts', { count: detailModal.excerpts.length })}</strong>
                {detailModal.excerpts.map((ex, i) => (
                  <CSBox key={i} padding={{ top: 'xs' }}>
                    <CSBadge color="grey">session: {ex.session_id}</CSBadge>
                    {' '}range: {ex.range}
                    <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: 4, background: 'var(--color-background-container-content)', padding: 8, borderRadius: 4 }}>
                      {ex.plaintext}
                    </pre>
                  </CSBox>
                ))}
              </CSBox>
            )}

            {!detailModal.review_decision && (
              <CSFormField
                label={t('admin_page.feedback.terminate_reason_label')}
                description={t('admin_page.feedback.terminate_reason_desc')}
              >
                <CSTextarea
                  value={terminateReason}
                  onChange={({ detail }) => setTerminateReason(detail.value)}
                  placeholder={t('admin_page.feedback.terminate_reason_placeholder')}
                  rows={3}
                  disabled={actionBusy}
                />
              </CSFormField>
            )}
          </CSSpaceBetween>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}
