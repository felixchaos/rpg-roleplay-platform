/* Admin pages — 全量系统管理页面集合
   8 个页面组件，全部通过 window.api.admin.* 从后端获取数据，禁止 mock/硬编码示例数据。 */

import React from 'react';
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
  const [users, setUsers] = React.useState([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [page, setPage] = React.useState(1);
  const limit = 20;
  const [search, setSearch] = React.useState('');
  const [roleFilter, setRoleFilter] = React.useState({ value: '', label: '全部角色' });
  const [statusFilter, setStatusFilter] = React.useState({ value: '', label: '全部状态' });

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
      if (!cancelled) setErr(e?.message || '加载失败');
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
        if (!cancelled) setErr(e?.message || '加载失败');
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
      window.toast?.('操作成功', { kind: 'ok' });
      setConfirmModal(null);
      load(page);
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || '未知错误'), { kind: 'danger' });
    } finally {
      setActionBusy(false);
    }
  }

  const roleOptions = [
    { value: '', label: '全部角色' },
    { value: 'admin', label: '管理员' },
    { value: 'user', label: '普通用户' },
  ];
  const statusOptions = [
    { value: '', label: '全部状态' },
    { value: 'active', label: '活跃' },
    { value: 'deactivated', label: '已停用' },
  ];

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="用户列表、角色分配、封禁与会话管理"
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton iconName="refresh" onClick={() => load(page)} loading={loading}>刷新</CSButton>
              </CSSpaceBetween>
            }
          >
            用户管理
          </CSHeader>
        }
      >
        <CSSpaceBetween size="m">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSInput
              placeholder="搜索用户名或显示名…"
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
            loadingText="加载中…"
            trackBy="id"
            items={users}
            empty={
              <CSBox textAlign="center" color="inherit">
                <CSBox padding={{ bottom: 's' }} variant="p" color="inherit">暂无用户数据</CSBox>
              </CSBox>
            }
            columnDefinitions={[
              { id: 'username', header: '用户名', cell: (u) => u.username || u.name || '—' },
              { id: 'display_name', header: '显示名', cell: (u) => u.display_name || '—' },
              {
                id: 'role', header: '角色',
                cell: (u) => u.role === 'admin'
                  ? <CSBadge color="severity-medium">管理员</CSBadge>
                  : <CSBadge color="grey">普通用户</CSBadge>,
              },
              {
                id: 'status', header: '状态',
                cell: (u) => u.deactivated_at
                  ? <CSStatusIndicator type="stopped">已停用</CSStatusIndicator>
                  : <CSStatusIndicator type="success">已激活</CSStatusIndicator>,
              },
              { id: 'last_login', header: '最后登录', cell: (u) => fmtTime(u.last_login_at || u.last_login) },
              {
                id: 'token_30d', header: '30天Token',
                cell: (u) => typeof u.token_usage_30d === 'number' ? u.token_usage_30d.toLocaleString() : '—',
              },
              {
                id: 'sessions', header: '活跃Session',
                cell: (u) => typeof u.active_session_count === 'number' ? u.active_session_count : '—',
              },
              {
                id: 'actions', header: '操作',
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
                            title: `停用 ${u.username}？`,
                            body: '停用后该用户无法登录，但数据保留。可随时恢复。',
                          })}
                        >停用</CSButton>
                      )}
                      {u.deactivated_at && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => setConfirmModal({
                            action: 'reactivate', user: u,
                            title: `恢复 ${u.username}？`,
                            body: '恢复后该用户可正常登录。',
                          })}
                        >恢复</CSButton>
                      )}
                      <CSButton
                        variant="inline-link"
                        onClick={() => setConfirmModal({
                          action: 'force-logout', user: u,
                          title: `强制下线 ${u.username}？`,
                          body: '该用户的所有 Session 将立即失效，需重新登录。',
                        })}
                      >强制下线</CSButton>
                      {u.role === 'user' && !isSelf && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => setConfirmModal({
                            action: 'set-admin', user: u,
                            title: `提升 ${u.username} 为管理员？`,
                            body: '该用户将获得系统管理权限。',
                          })}
                        >升为管理员</CSButton>
                      )}
                      {u.role === 'admin' && !isSelf && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => setConfirmModal({
                            action: 'set-user', user: u,
                            title: `降级 ${u.username} 为普通用户？`,
                            body: '该用户将失去系统管理权限。',
                          })}
                        >降为普通用户</CSButton>
                      )}
                    </CSSpaceBetween>
                  );
                },
              },
            ]}
            pagination={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</CSButton>
                <CSBox padding="xs">第 {page} 页，共约 {Math.ceil(total / limit)} 页</CSBox>
                <CSButton disabled={users.length < limit} onClick={() => setPage(p => p + 1)}>下一页</CSButton>
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
                <CSButton variant="link" disabled={actionBusy} onClick={() => setConfirmModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={actionBusy} onClick={doAction}>确认</CSButton>
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
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [days, setDays] = React.useState({ value: '30', label: '最近 30 天' });

  const daysOptions = [
    { value: '7', label: '最近 7 天' },
    { value: '14', label: '最近 14 天' },
    { value: '30', label: '最近 30 天' },
    { value: '90', label: '最近 90 天' },
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
        if (!cancelled) setErr(e?.message || '加载失败');
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
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}

      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="全平台 Token 消耗与成本总览"
            actions={
              <CSSelect
                selectedOption={days}
                options={daysOptions}
                onChange={({ detail }) => setDays(detail.selectedOption)}
              />
            }
          >
            全局用量
          </CSHeader>
        }
      >
        {loading
          ? <CSBox color="inherit">加载中…</CSBox>
          : !data
            ? <CSBox color="inherit" textAlign="center">暂无用量数据</CSBox>
            : (
              <CSKeyValuePairs
                columns={3}
                items={[
                  { label: '总请求数', value: (summary.total_requests || 0).toLocaleString() },
                  { label: '总 Token（入+出）', value: (summary.total_tokens || 0).toLocaleString() },
                  { label: '总成本 (USD)', value: typeof summary.total_cost === 'number' ? `$${summary.total_cost.toFixed(4)}` : '—' },
                ]}
              />
            )
        }
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2">按用户</CSHeader>}>
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="user_id"
          items={byUser}
          empty={<CSBox textAlign="center" color="inherit">暂无数据</CSBox>}
          columnDefinitions={[
            { id: 'rank', header: '#', cell: (_, idx) => idx + 1, width: 50 },
            { id: 'username', header: '用户名', cell: (u) => u.username || u.user_id || '—' },
            { id: 'tokens', header: 'Token 消耗', cell: (u) => (u.tokens || 0).toLocaleString() },
            { id: 'cost', header: '成本 (USD)', cell: (u) => typeof u.cost === 'number' ? `$${u.cost.toFixed(4)}` : '—' },
            {
              id: 'pct', header: '占比',
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

      <CSContainer header={<CSHeader variant="h2">按 API</CSHeader>}>
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="api_id"
          items={byApi}
          empty={<CSBox textAlign="center" color="inherit">暂无数据</CSBox>}
          columnDefinitions={[
            { id: 'api_id', header: 'API', cell: (a) => a.api_id || a.api || '—' },
            { id: 'tokens', header: 'Token', cell: (a) => (a.tokens || 0).toLocaleString() },
            { id: 'cost', header: '成本 (USD)', cell: (a) => typeof a.cost === 'number' ? `$${a.cost.toFixed(4)}` : '—' },
          ]}
        />
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2">按天趋势</CSHeader>}>
        {loading
          ? <CSBox color="inherit">加载中…</CSBox>
          : byDay.length === 0
            ? <CSBox textAlign="center" color="inherit">暂无数据</CSBox>
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
  const [items, setItems] = React.useState([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [page, setPage] = React.useState(1);
  const limit = 50;
  const [actionFilter, setActionFilter] = React.useState({ value: '', label: '全部操作' });
  const [expandedDetail, setExpandedDetail] = React.useState(null);

  const actionOptions = [
    { value: '', label: '全部操作' },
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
        if (!cancelled) setErr(e?.message || '加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [page, actionFilter.value]);

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="管理员操作记录与安全事件"
            actions={
              <CSSelect
                selectedOption={actionFilter}
                options={actionOptions}
                onChange={({ detail }) => { setActionFilter(detail.selectedOption); setPage(1); }}
              />
            }
          >
            审计日志
          </CSHeader>
        }
      >
        <CSSpaceBetween size="m">
          <CSTable
            loading={loading}
            loadingText="加载中…"
            trackBy="id"
            items={items}
            empty={<CSBox textAlign="center" color="inherit">暂无审计记录</CSBox>}
            columnDefinitions={[
              { id: 'created_at', header: '时间', cell: (r) => fmtTime(r.created_at || r.timestamp) },
              { id: 'operator', header: '操作者', cell: (r) => r.operator || r.user || r.username || '—' },
              {
                id: 'action_type', header: '操作类型',
                cell: (r) => <CSBadge color="blue">{r.action_type || r.action || '—'}</CSBadge>,
              },
              { id: 'target', header: '目标', cell: (r) => r.target || r.resource || '—' },
              {
                id: 'detail', header: '详情',
                cell: (r) => {
                  const key = r.id || r.created_at;
                  const raw = r.detail || r.meta || r.extra;
                  if (!raw) return '—';
                  const str = typeof raw === 'string' ? raw : JSON.stringify(raw, null, 2);
                  const isExpanded = expandedDetail === key;
                  return (
                    <div>
                      <CSButton variant="inline-link" onClick={() => setExpandedDetail(isExpanded ? null : key)}>
                        {isExpanded ? '收起' : '展开'}
                      </CSButton>
                      {isExpanded && <pre style={{ fontSize: 11, maxWidth: 400, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: '4px 0 0' }}>{str}</pre>}
                    </div>
                  );
                },
              },
              { id: 'ip', header: 'IP', cell: (r) => r.ip || r.ip_address || '—' },
            ]}
            pagination={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</CSButton>
                <CSBox padding="xs">第 {page} 页</CSBox>
                <CSButton disabled={items.length < limit} onClick={() => setPage(p => p + 1)}>下一页</CSButton>
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
      setErr(e?.message || '加载失败');
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
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="数据库、内存、磁盘与进程状态（每 30 秒自动刷新）"
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                {lastUpdate && (
                  <CSBox color="text-body-secondary" variant="small">
                    最后更新：{lastUpdate.toLocaleTimeString('zh-CN', { hour12: false })}
                  </CSBox>
                )}
                <CSButton iconName="refresh" loading={refreshing} onClick={() => fetchHealth(true)}>刷新</CSButton>
              </CSSpaceBetween>
            }
          >
            系统健康
          </CSHeader>
        }
      >
        {loading && !data
          ? <CSBox color="inherit">加载中…</CSBox>
          : !data
            ? <CSBox textAlign="center" color="inherit">暂无健康数据</CSBox>
            : (
              <CSColumnLayout columns={2} variant="text-grid">
                <div>
                  <CSSpaceBetween size="s">
                    <div>
                      <strong>数据库</strong>
                      <div>
                        <CSStatusIndicator type={db.ok === false ? 'error' : 'success'}>
                          {db.ok === false ? '连接失败' : '连通正常'}
                        </CSStatusIndicator>
                        {typeof db.latency_ms === 'number' && (
                          <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-text-body-secondary)' }}>
                            延迟 {db.latency_ms} ms
                          </span>
                        )}
                      </div>
                    </div>
                    <div>
                      <strong>内存</strong>
                      <div>
                        {typeof mem.rss_mb === 'number'
                          ? <CSStatusIndicator type="success">RSS {mem.rss_mb} MB</CSStatusIndicator>
                          : <CSStatusIndicator type="pending">无数据</CSStatusIndicator>
                        }
                      </div>
                    </div>
                  </CSSpaceBetween>
                </div>
                <div>
                  <CSSpaceBetween size="s">
                    <div>
                      <strong>磁盘</strong>
                      <div>
                        {diskPct !== null
                          ? <CSStatusIndicator type={diskPct > 90 ? 'warning' : 'success'}>
                              已用 {diskPct}%
                            </CSStatusIndicator>
                          : <CSStatusIndicator type="pending">无数据</CSStatusIndicator>
                        }
                      </div>
                    </div>
                    <div>
                      <strong>进程</strong>
                      <div>
                        {proc.pid
                          ? <CSStatusIndicator type="success">
                              PID {proc.pid}
                              {proc.uptime_s && <span style={{ marginLeft: 8, fontSize: 12 }}>运行 {Math.round(proc.uptime_s / 60)} 分钟</span>}
                            </CSStatusIndicator>
                          : <CSStatusIndicator type="pending">无数据</CSStatusIndicator>
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
  const [lines, setLines] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [linesCount, setLinesCount] = React.useState({ value: '100', label: '100 行' });
  const [levelFilter, setLevelFilter] = React.useState({ value: '', label: '全部级别' });

  const linesOptions = [
    { value: '50', label: '50 行' },
    { value: '100', label: '100 行' },
    { value: '200', label: '200 行' },
    { value: '500', label: '500 行' },
  ];
  const levelOptions = [
    { value: '', label: '全部级别' },
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
      setErr(e?.message || '加载失败');
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
        if (!cancelled) setErr(e?.message || '加载失败');
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
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="运行时日志查看与下载"
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
                <CSButton iconName="download" onClick={handleDownload} disabled={!lines.length}>下载</CSButton>
                <CSButton iconName="refresh" onClick={fetchLogs} loading={loading}>刷新</CSButton>
              </CSSpaceBetween>
            }
          >
            系统日志
          </CSHeader>
        }
      >
        {loading
          ? <CSBox color="inherit">加载中…</CSBox>
          : filtered.length === 0
            ? <CSBox textAlign="center" color="inherit">暂无日志数据</CSBox>
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
    { value: 'open', label: '开放注册' },
    { value: 'invite', label: '仅邀请' },
    { value: 'closed', label: '关闭注册' },
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
        if (!cancelled) setErr(e?.message || '加载失败');
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
      window.toast?.('注册配置已保存', { kind: 'ok' });
    } catch (e) {
      window.toast?.('保存失败: ' + (e?.message || ''), { kind: 'danger' });
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
      window.toast?.('邀请码已生成', { kind: 'ok' });
      setCreateModal(false);
      const codes = await window.api.admin.inviteCodes();
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) {
      window.toast?.('生成失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(code) {
    setDeleting(true);
    try {
      await window.api.admin.deleteInviteCode(code);
      window.toast?.('邀请码已删除', { kind: 'ok' });
      setDeleteTarget(null);
      const codes = await window.api.admin.inviteCodes();
      setInviteCodes(codes.items || codes.codes || codes || []);
    } catch (e) {
      window.toast?.('删除失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}

      <CSContainer header={<CSHeader variant="h2">注册配置</CSHeader>}>
        {loading
          ? <CSBox color="inherit">加载中…</CSBox>
          : !regConfig
            ? <CSBox textAlign="center" color="inherit">暂无配置数据</CSBox>
            : (
              <CSSpaceBetween size="m">
                <CSFormField label="注册模式">
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
                <CSFormField label="邮箱验证">
                  <CSToggle
                    checked={!!regConfig.email_verification}
                    onChange={({ detail }) => saveReg({ email_verification: detail.checked })}
                  >
                    {regConfig.email_verification ? '已开启' : '已关闭'}
                  </CSToggle>
                </CSFormField>
                <CSFormField label="自动审批">
                  <CSToggle
                    checked={!!regConfig.auto_approve}
                    onChange={({ detail }) => saveReg({ auto_approve: detail.checked })}
                  >
                    {regConfig.auto_approve ? '已开启' : '已关闭'}
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
            description="管理邀请码，控制受邀注册"
            actions={
              <CSButton variant="primary" onClick={() => setCreateModal(true)}>生成邀请码</CSButton>
            }
          >
            邀请码管理
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="code"
          items={inviteCodes}
          empty={<CSBox textAlign="center" color="inherit">暂无邀请码</CSBox>}
          columnDefinitions={[
            { id: 'code', header: '邀请码', cell: (c) => <code>{c.code}</code> },
            { id: 'note', header: '备注', cell: (c) => c.note || '—' },
            {
              id: 'status', header: '状态',
              cell: (c) => c.used_by
                ? <CSBadge color="grey">已使用 @{c.used_by}</CSBadge>
                : c.expired_at && new Date(c.expired_at) < new Date()
                  ? <CSBadge color="red">已过期</CSBadge>
                  : <CSBadge color="green">可用</CSBadge>,
            },
            { id: 'expires', header: '过期时间', cell: (c) => fmtTime(c.expires_at || c.expired_at) },
            { id: 'created', header: '创建时间', cell: (c) => fmtTime(c.created_at) },
            {
              id: 'actions', header: '操作',
              cell: (c) => !c.used_by
                ? <CSButton variant="inline-link" onClick={() => setDeleteTarget(c.code)}>删除</CSButton>
                : null,
            },
          ]}
        />
      </CSContainer>

      {createModal && (
        <CSModal
          visible
          onDismiss={() => !creating && setCreateModal(false)}
          header="生成邀请码"
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={creating} onClick={() => setCreateModal(false)}>取消</CSButton>
                <CSButton variant="primary" loading={creating} onClick={handleCreateCodes}>生成</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSFormField label="数量（1-10）">
              <CSInput
                type="number"
                value={createForm.count}
                onChange={({ detail }) => setCreateForm((f) => ({ ...f, count: detail.value }))}
              />
            </CSFormField>
            <CSFormField label="过期天数">
              <CSSelect
                selectedOption={{ value: createForm.expires_days, label: `${createForm.expires_days} 天` }}
                options={[7, 14, 30, 90, 180, 365].map((d) => ({ value: String(d), label: `${d} 天` }))}
                onChange={({ detail }) => setCreateForm((f) => ({ ...f, expires_days: detail.selectedOption.value }))}
              />
            </CSFormField>
            <CSFormField label="备注（可选）">
              <CSInput
                value={createForm.note}
                onChange={({ detail }) => setCreateForm((f) => ({ ...f, note: detail.value }))}
                placeholder="说明用途…"
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {deleteTarget && (
        <CSModal
          visible
          onDismiss={() => !deleting && setDeleteTarget(null)}
          header="删除邀请码"
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</CSButton>
                <CSButton variant="primary" loading={deleting} onClick={() => handleDelete(deleteTarget)}>删除</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>确定删除邀请码 <code>{deleteTarget}</code> 吗？删除后无法撤销。</CSBox>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 7：AdminSecurityPage — 安全配置
   ───────────────────────────────────────────────────────────────── */
export function AdminSecurityPage() {
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
        if (!cancelled) setErr(e?.message || '加载失败');
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
      window.toast?.('安全配置已保存', { kind: 'ok' });
    } catch (e) {
      window.toast?.('保存失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSaving(false);
    }
  }

  const d = draft || {};

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      {loading
        ? <CSBox color="inherit">加载中…</CSBox>
        : !draft
          ? <CSBox textAlign="center" color="inherit">暂无配置数据</CSBox>
          : (
            <>
              <CSContainer header={<CSHeader variant="h2">速率限制</CSHeader>}>
                <CSAlert type="info">速率限制参数修改后需重启服务才能生效。</CSAlert>
                <CSSpaceBetween size="m">
                  <CSColumnLayout columns={3} variant="text-grid">
                    <CSFormField label="每 IP 最大请求数">
                      <CSInput
                        type="number"
                        value={String(d.rate_limit?.max_per_ip ?? '')}
                        onChange={({ detail }) => upd('rate_limit.max_per_ip', Number(detail.value))}
                      />
                    </CSFormField>
                    <CSFormField label="每用户最大请求数">
                      <CSInput
                        type="number"
                        value={String(d.rate_limit?.max_per_user ?? '')}
                        onChange={({ detail }) => upd('rate_limit.max_per_user', Number(detail.value))}
                      />
                    </CSFormField>
                    <CSFormField label="时间窗口（分钟）">
                      <CSInput
                        type="number"
                        value={String(d.rate_limit?.window_minutes ?? '')}
                        onChange={({ detail }) => upd('rate_limit.window_minutes', Number(detail.value))}
                      />
                    </CSFormField>
                  </CSColumnLayout>
                </CSSpaceBetween>
              </CSContainer>

              <CSContainer header={<CSHeader variant="h2">密码策略</CSHeader>}>
                <CSSpaceBetween size="m">
                  <CSColumnLayout columns={2} variant="text-grid">
                    <CSFormField label="最小长度">
                      <CSInput
                        type="number"
                        value={String(d.password?.min_length ?? '')}
                        onChange={({ detail }) => upd('password.min_length', Number(detail.value))}
                      />
                    </CSFormField>
                    <CSFormField label="需要数字">
                      <CSToggle
                        checked={!!d.password?.require_digit}
                        onChange={({ detail }) => upd('password.require_digit', detail.checked)}
                      >
                        {d.password?.require_digit ? '是' : '否'}
                      </CSToggle>
                    </CSFormField>
                  </CSColumnLayout>
                </CSSpaceBetween>
              </CSContainer>

              <CSContainer header={<CSHeader variant="h2">Session 策略</CSHeader>}>
                <CSFormField label="Session 超时（天）">
                  <CSInput
                    type="number"
                    value={String(d.session?.timeout_days ?? '')}
                    onChange={({ detail }) => upd('session.timeout_days', Number(detail.value))}
                    style={{ maxWidth: 200 }}
                  />
                </CSFormField>
              </CSContainer>

              <CSContainer header={<CSHeader variant="h2">登录锁定策略</CSHeader>}>
                <CSColumnLayout columns={2} variant="text-grid">
                  <CSFormField label="失败次数阈值">
                    <CSInput
                      type="number"
                      value={String(d.lockout?.max_attempts ?? '')}
                      onChange={({ detail }) => upd('lockout.max_attempts', Number(detail.value))}
                    />
                  </CSFormField>
                  <CSFormField label="锁定时长（分钟）">
                    <CSInput
                      type="number"
                      value={String(d.lockout?.lockout_minutes ?? '')}
                      onChange={({ detail }) => upd('lockout.lockout_minutes', Number(detail.value))}
                    />
                  </CSFormField>
                </CSColumnLayout>
              </CSContainer>

              <CSContainer header={<CSHeader variant="h2">IP 黑名单</CSHeader>}>
                <CSFormField label="每行一个 IP 或 CIDR（如 192.168.1.0/24）">
                  <CSTextarea
                    value={Array.isArray(d.ip_blocklist) ? d.ip_blocklist.join('\n') : (d.ip_blocklist || '')}
                    onChange={({ detail }) => upd('ip_blocklist', detail.value.split('\n').map((s) => s.trim()).filter(Boolean))}
                    rows={6}
                    placeholder="192.168.1.1&#10;10.0.0.0/8"
                  />
                </CSFormField>
              </CSContainer>

              <CSBox float="right">
                <CSButton variant="primary" loading={saving} onClick={save}>保存安全配置</CSButton>
              </CSBox>
            </>
          )
      }
    </CSSpaceBetween>
  );
}

/* ─────────────────────────────────────────────────────────────────
   页面 8：AdminMaintenancePage — 维护模式
   ───────────────────────────────────────────────────────────────── */
export function AdminMaintenancePage() {
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
        if (!cancelled) setErr(e?.message || '加载失败');
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
      window.toast?.('维护配置已保存', { kind: 'ok' });
    } catch (e) {
      window.toast?.('保存失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSaving(false);
    }
  }

  async function handleRestart() {
    setRestarting(true);
    try {
      await window.api.admin.restart();
      window.toast?.('重启指令已发送，服务将优雅重载', { kind: 'ok', duration: 5000 });
      setRestartModal(false);
    } catch (e) {
      window.toast?.('重启失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setRestarting(false);
    }
  }

  const d = draft || {};

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}

      <CSContainer header={<CSHeader variant="h2" description="开启后所有用户将看到维护公告">维护模式</CSHeader>}>
        {loading
          ? <CSBox color="inherit">加载中…</CSBox>
          : !draft
            ? <CSBox textAlign="center" color="inherit">暂无配置数据</CSBox>
            : (
              <CSSpaceBetween size="m">
                {d.enabled && (
                  <CSAlert type="warning">
                    维护模式已开启，所有用户访问时将看到维护公告。请尽快完成维护后关闭。
                  </CSAlert>
                )}
                <CSFormField label="维护模式开关">
                  <CSToggle
                    checked={!!d.enabled}
                    onChange={({ detail }) => setDraft((prev) => ({ ...prev, enabled: detail.checked }))}
                  >
                    {d.enabled ? '已开启' : '已关闭'}
                  </CSToggle>
                </CSFormField>
                <CSFormField label="公告内容（支持多行）">
                  <CSTextarea
                    value={d.message || ''}
                    onChange={({ detail }) => setDraft((prev) => ({ ...prev, message: detail.value }))}
                    rows={4}
                    placeholder="正在进行系统升级维护，预计 XX 分钟后恢复…"
                  />
                </CSFormField>
                {d.started_at && (
                  <CSFormField label="维护开始时间">
                    <CSBox color="text-body-secondary">{fmtTime(d.started_at)}</CSBox>
                  </CSFormField>
                )}
                <CSBox float="right">
                  <CSButton variant="primary" loading={saving} onClick={save}>保存</CSButton>
                </CSBox>
              </CSSpaceBetween>
            )
        }
      </CSContainer>

      <CSContainer header={<CSHeader variant="h2" description="发送优雅重载信号到后端服务">服务重启</CSHeader>}>
        <CSSpaceBetween size="m">
          <CSAlert type="warning">
            重启会短暂中断服务（通常 5-15 秒）。建议在维护模式开启后进行。
          </CSAlert>
          <CSButton
            variant="normal"
            iconName="status-warning"
            onClick={() => setRestartModal(true)}
          >
            重启服务
          </CSButton>
        </CSSpaceBetween>
      </CSContainer>

      {restartModal && (
        <CSModal
          visible
          onDismiss={() => !restarting && setRestartModal(false)}
          header="确认重启服务"
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={restarting} onClick={() => setRestartModal(false)}>取消</CSButton>
                <CSButton variant="primary" loading={restarting} onClick={handleRestart}>确认重启</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>
            服务将发送优雅重载（SIGTERM/graceful reload）信号，当前进行中的请求会尽量完成。
            重启期间（约 5-15 秒）新请求可能失败，请确保已通知用户或已开启维护模式。
          </CSBox>
        </CSModal>
      )}
    </CSSpaceBetween>
  );
}
