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
   页面 9：AdminDmcaTakedownsPage — DMCA 下架队列
   ───────────────────────────────────────────────────────────────── */
export function AdminDmcaTakedownsPage() {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState({ value: 'open', label: '待处理' });
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
    { value: 'open', label: '待处理' },
    { value: 'counter_received', label: '已收反通知' },
    { value: 'closed', label: '已下架' },
    { value: 'restored', label: '已恢复' },
    { value: 'rejected', label: '已拒绝' },
    { value: 'all', label: '全部' },
  ];

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.dmcaTakedowns({ status: statusFilter.value });
      setItems(res.takedowns || res || []);
    } catch (e) {
      setErr(e?.message || '加载失败');
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
      window.toast?.('操作成功', { kind: 'ok' });
      setActionModal(null);
      setActionReason('');
      load();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setActionBusy(false);
    }
  }

  async function doCreate() {
    setCreating(true);
    try {
      await window.api.admin.dmcaTakedownCreate(createForm);
      window.toast?.('通知已录入', { kind: 'ok' });
      setCreateModal(false);
      setCreateForm({ complainant_name: '', complainant_email: '', infringing_url: '', original_work_desc: '' });
      load();
    } catch (e) {
      window.toast?.('录入失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setCreating(false);
    }
  }

  async function doCounter() {
    if (!counterModal) return;
    setCounterBusy(true);
    try {
      await window.api.admin.dmcaTakedownCounter(counterModal.id, { notes: counterNotes });
      window.toast?.('反通知已录入，10 天计时开始', { kind: 'ok' });
      setCounterModal(null);
      setCounterNotes('');
      load();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setCounterBusy(false);
    }
  }

  function statusBadge(s) {
    const map = {
      open: ['red', '待处理'],
      counter_received: ['blue', '已收反通知'],
      closed: ['grey', '已下架'],
      restored: ['green', '已恢复'],
      rejected: ['severity-low', '已拒绝'],
    };
    const [color, label] = map[s] || ['grey', s];
    return <CSBadge color={color}>{label}</CSBadge>;
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="DMCA 下架通知队列管理（DM-01..04）"
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={statusFilter}
                  options={statusOptions}
                  onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                />
                <CSButton variant="primary" onClick={() => setCreateModal(true)}>录入通知</CSButton>
                <CSButton iconName="refresh" onClick={load} loading={loading}>刷新</CSButton>
              </CSSpaceBetween>
            }
          >
            DMCA 下架队列
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="id"
          items={items}
          empty={<CSBox textAlign="center" color="inherit">暂无记录</CSBox>}
          columnDefinitions={[
            { id: 'id', header: 'ID', cell: (r) => `#${r.id}`, width: 60 },
            { id: 'complainant', header: '举报人', cell: (r) => `${r.complainant_name || '—'} <${r.complainant_email || '—'}>` },
            { id: 'url', header: '涉嫌内容 URL', cell: (r) => <a href={r.infringing_url} target="_blank" rel="noopener noreferrer" style={{ wordBreak: 'break-all' }}>{r.infringing_url}</a> },
            { id: 'status', header: '状态', cell: (r) => statusBadge(r.status) },
            { id: 'restore_after', header: '可恢复时间', cell: (r) => r.restore_after ? fmtTime(r.restore_after) : '—' },
            { id: 'created_at', header: '录入时间', cell: (r) => fmtTime(r.created_at) },
            {
              id: 'actions', header: '操作',
              cell: (r) => (
                <CSSpaceBetween direction="horizontal" size="xs">
                  {r.status === 'open' && (
                    <>
                      <CSButton variant="inline-link" onClick={() => { setActionModal({ item: r, action: 'takedown' }); setActionReason(''); }}>下架</CSButton>
                      <CSButton variant="inline-link" onClick={() => { setActionModal({ item: r, action: 'reject' }); setActionReason(''); }}>拒绝</CSButton>
                    </>
                  )}
                  {r.status === 'closed' && (
                    <>
                      <CSButton variant="inline-link" onClick={() => { setCounterModal(r); setCounterNotes(''); }}>录入反通知</CSButton>
                    </>
                  )}
                  {r.status === 'counter_received' && r.restore_after && new Date(r.restore_after) <= new Date() && (
                    <CSButton variant="inline-link" onClick={() => { setActionModal({ item: r, action: 'restore' }); setActionReason('反通知期满，无禁令，恢复内容'); }}>恢复</CSButton>
                  )}
                </CSSpaceBetween>
              ),
            },
          ]}
        />
      </CSContainer>

      {/* 录入通知 Modal */}
      {createModal && (
        <CSModal
          visible
          onDismiss={() => !creating && setCreateModal(false)}
          header="录入 DMCA 通知"
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={creating} onClick={() => setCreateModal(false)}>取消</CSButton>
                <CSButton variant="primary" loading={creating} onClick={doCreate}>提交</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSFormField label="举报人姓名 *">
              <CSInput value={createForm.complainant_name} onChange={({ detail }) => setCreateForm((f) => ({ ...f, complainant_name: detail.value }))} />
            </CSFormField>
            <CSFormField label="举报人邮箱 *">
              <CSInput value={createForm.complainant_email} onChange={({ detail }) => setCreateForm((f) => ({ ...f, complainant_email: detail.value }))} type="email" />
            </CSFormField>
            <CSFormField label="涉嫌侵权内容 URL *">
              <CSInput value={createForm.infringing_url} onChange={({ detail }) => setCreateForm((f) => ({ ...f, infringing_url: detail.value }))} placeholder="https://play.stellatrix.icu/..." />
            </CSFormField>
            <CSFormField label="原始作品描述">
              <CSTextarea value={createForm.original_work_desc} onChange={({ detail }) => setCreateForm((f) => ({ ...f, original_work_desc: detail.value }))} rows={3} />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {/* 执行操作 Modal */}
      {actionModal && (
        <CSModal
          visible
          onDismiss={() => !actionBusy && setActionModal(null)}
          header={`确认操作：${actionModal.action === 'takedown' ? '下架' : actionModal.action === 'restore' ? '恢复' : '拒绝'}`}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={actionBusy} onClick={() => setActionModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={actionBusy} onClick={doAction}>确认</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSBox>记录 #{actionModal.item.id} — {actionModal.item.infringing_url}</CSBox>
            <CSFormField label="原因（必填）">
              <CSTextarea value={actionReason} onChange={({ detail }) => setActionReason(detail.value)} rows={3} placeholder="填写操作原因，将记录入审计日志…" />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {/* 录入反通知 Modal */}
      {counterModal && (
        <CSModal
          visible
          onDismiss={() => !counterBusy && setCounterModal(null)}
          header="录入反通知"
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={counterBusy} onClick={() => setCounterModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={counterBusy} onClick={doCounter}>提交</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="info">录入后系统将自动设置 10 天恢复计时。请确保已将反通知转发给原举报人。</CSAlert>
            <CSFormField label="反通知备注">
              <CSTextarea value={counterNotes} onChange={({ detail }) => setCounterNotes(detail.value)} rows={3} placeholder="反通知摘要、接收时间等…" />
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
      setErr(e?.message || '加载失败');
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
        window.toast?.(`Strike 已添加（共 ${res.strike_count} 次），账户已自动终止`, { kind: 'danger', duration: 8000 });
      } else {
        window.toast?.(`Strike 已添加（共 ${res.strike_count}/${3} 次）`, { kind: 'ok' });
      }
      setStrikeModal(null);
      setStrikeReason('');
      load();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
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
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="DMCA 累犯记录（3 次触发账户终止）"
            actions={<CSButton iconName="refresh" onClick={load} loading={loading}>刷新</CSButton>}
          >
            Strike 记录
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="user_id"
          items={users}
          empty={<CSBox textAlign="center" color="inherit">暂无 Strike 记录</CSBox>}
          columnDefinitions={[
            { id: 'username', header: '用户名', cell: (u) => u.username || `uid:${u.user_id}` },
            {
              id: 'count', header: 'Strike 数',
              cell: (u) => <CSBadge color={strikeBadgeColor(u.strike_count)}>{u.strike_count} / 3</CSBadge>,
            },
            {
              id: 'history', header: '历史记录',
              cell: (u) => {
                const isExp = expanded === u.user_id;
                return (
                  <div>
                    <CSButton variant="inline-link" onClick={() => setExpanded(isExp ? null : u.user_id)}>
                      {isExp ? '收起' : '展开'}
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
              id: 'actions', header: '操作',
              cell: (u) => u.strike_count < 3 && (
                <CSButton
                  variant="inline-link"
                  onClick={() => { setStrikeModal({ user_id: u.user_id, username: u.username }); setStrikeReason(''); }}
                >
                  +Strike
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
          header={`添加 Strike — ${strikeModal.username}`}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={strikeBusy} onClick={() => setStrikeModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={strikeBusy} onClick={doStrike}>确认添加</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="warning">添加 Strike 后，若达到 3 次将自动触发账户终止流程，请谨慎操作。</CSAlert>
            <CSFormField label="原因（必填，关联 Takedown ID）">
              <CSTextarea
                value={strikeReason}
                onChange={({ detail }) => setStrikeReason(detail.value)}
                rows={3}
                placeholder="如：DMCA 下架记录 #42，合规通知已验证"
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
  const [reports, setReports] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState({ value: 'pending', label: '待决定' });
  const [decisionModal, setDecisionModal] = React.useState(null); // report item
  const [decisionForm, setDecisionForm] = React.useState({ decision: '', notes: '' });
  const [deciding, setDeciding] = React.useState(false);

  const statusOptions = [
    { value: 'pending', label: '待决定' },
    { value: 'decided', label: '已决定' },
    { value: 'all', label: '全部' },
  ];
  const decisionOptions = [
    { value: 'founded', label: '成立（founded）— 触发上报流程' },
    { value: 'escalate', label: '升级（escalate）— 需更高级别确认' },
    { value: 'unfounded', label: '不成立（unfounded）— 关闭' },
  ];

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await window.api.admin.csamReports({ status: statusFilter.value });
      setReports(res.reports || []);
    } catch (e) {
      setErr(e?.message || '加载失败');
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
      window.toast?.('决定已记录', { kind: 'ok' });
      setDecisionModal(null);
      setDecisionForm({ decision: '', notes: '' });
      load();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setDeciding(false);
    }
  }

  function decisionBadge(d) {
    const map = { founded: ['red', '成立'], escalate: ['blue', '已升级'], unfounded: ['grey', '不成立'] };
    const [color, label] = map[d] || ['grey', d || '—'];
    return <CSBadge color={color}>{label}</CSBadge>;
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}
      <CSAlert type="warning">
        CSAM 举报涉及极度敏感内容，请严格遵守 <code>docs/runbooks/csam.md</code> 规程。
        处理人须限制知情范围，不得直接查看内容本体。
      </CSAlert>
      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="CSAM 内容举报记录与决定（CSAM-01..04）"
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={statusFilter}
                  options={statusOptions}
                  onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                />
                <CSButton iconName="refresh" onClick={load} loading={loading}>刷新</CSButton>
              </CSSpaceBetween>
            }
          >
            CSAM 举报
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="id"
          items={reports}
          empty={<CSBox textAlign="center" color="inherit">暂无举报记录</CSBox>}
          columnDefinitions={[
            { id: 'id', header: 'ID', cell: (r) => `#${r.id}`, width: 60 },
            { id: 'reported_user', header: '被举报用户', cell: (r) => r.reported_username || `uid:${r.reported_user_id}` || '—' },
            { id: 'content_url', header: '内容', cell: (r) => r.content_url || '（描述见详情）' },
            { id: 'status', header: '状态', cell: (r) => r.status === 'pending' ? <CSBadge color="red">待决定</CSBadge> : <CSBadge color="grey">已决定</CSBadge> },
            { id: 'decision', header: '决定', cell: (r) => r.decision ? decisionBadge(r.decision) : '—' },
            { id: 'cybertip', header: 'CyberTip ID', cell: (r) => r.cybertip_report_id || '—' },
            { id: 'created_at', header: '举报时间', cell: (r) => fmtTime(r.created_at) },
            {
              id: 'actions', header: '操作',
              cell: (r) => r.status === 'pending' && (
                <CSButton
                  variant="inline-link"
                  onClick={() => { setDecisionModal(r); setDecisionForm({ decision: '', notes: '' }); }}
                >
                  标记决定
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
          header={`标记决定 — 举报 #${decisionModal.id}`}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={deciding} onClick={() => setDecisionModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={deciding} disabled={!decisionForm.decision} onClick={doDecision}>确认</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="warning">
              选择"成立"后，请立即按 csam.md 流程向 NCMEC CyberTipline 上报，并暂停被举报账户。
            </CSAlert>
            <CSFormField label="决定 *">
              <CSSelect
                selectedOption={decisionOptions.find((o) => o.value === decisionForm.decision) || { value: '', label: '请选择…' }}
                options={decisionOptions}
                onChange={({ detail }) => setDecisionForm((f) => ({ ...f, decision: detail.selectedOption.value }))}
              />
            </CSFormField>
            <CSFormField label="备注">
              <CSTextarea
                value={decisionForm.notes}
                onChange={({ detail }) => setDecisionForm((f) => ({ ...f, notes: detail.value }))}
                rows={3}
                placeholder="决定依据、CyberTipline 报告 ID 等…"
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
      setErr(e?.message || '搜索失败');
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
      window.toast?.('账户已暂停', { kind: 'ok' });
      setSuspendModal(null);
      setSuspendForm({ reason: '', duration_days: '' });
      doSearch();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setSuspendBusy(false);
    }
  }

  async function doUnsuspend() {
    if (!unsuspendModal) return;
    setUnsuspendBusy(true);
    try {
      await window.api.admin.unsuspendUser(unsuspendModal.id);
      window.toast?.('账户已解封', { kind: 'ok' });
      setUnsuspendModal(null);
      doSearch();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setUnsuspendBusy(false);
    }
  }

  async function doTerminate() {
    if (!terminateModal) return;
    setTerminateBusy(true);
    try {
      await window.api.admin.terminateUser(terminateModal.id, { reason: terminateReason });
      window.toast?.('账户已永久终止', { kind: 'ok', duration: 6000 });
      setTerminateModal(null);
      setTerminateReason('');
      doSearch();
    } catch (e) {
      window.toast?.('操作失败: ' + (e?.message || ''), { kind: 'danger' });
    } finally {
      setTerminateBusy(false);
    }
  }

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="错误">{err}</CSAlert>}

      <CSContainer
        header={
          <CSHeader variant="h2" description="AUP 暂停、解封、永久终止（AUP-01..03）">
            AUP 账户处置
          </CSHeader>
        }
      >
        <CSSpaceBetween size="m">
          <CSAlert type="info">搜索用户后，可对其执行暂停（临时）、解封或永久终止操作。终止操作不可逆。</CSAlert>
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSInput
              placeholder="搜索用户名或显示名…"
              value={search}
              onChange={({ detail }) => setSearch(detail.value)}
              onKeyDown={({ detail }) => { if (detail.key === 'Enter') doSearch(); }}
              type="search"
            />
            <CSButton onClick={doSearch} loading={loading}>搜索</CSButton>
          </CSSpaceBetween>

          {users.length > 0 && (
            <CSTable
              loading={loading}
              loadingText="加载中…"
              trackBy="id"
              items={users}
              empty={<CSBox textAlign="center" color="inherit">无结果</CSBox>}
              columnDefinitions={[
                { id: 'username', header: '用户名', cell: (u) => u.username },
                { id: 'display_name', header: '显示名', cell: (u) => u.display_name || '—' },
                {
                  id: 'status', header: '状态',
                  cell: (u) => u.deactivated_at
                    ? <CSStatusIndicator type="stopped">已暂停/停用</CSStatusIndicator>
                    : <CSStatusIndicator type="success">正常</CSStatusIndicator>,
                },
                { id: 'ban_reason', header: '封禁原因', cell: (u) => u.ban_reason || '—' },
                {
                  id: 'actions', header: '操作',
                  cell: (u) => (
                    <CSSpaceBetween direction="horizontal" size="xs">
                      {!u.deactivated_at && (
                        <CSButton
                          variant="inline-link"
                          onClick={() => { setSuspendModal(u); setSuspendForm({ reason: '', duration_days: '' }); }}
                        >
                          暂停
                        </CSButton>
                      )}
                      {u.deactivated_at && (
                        <CSButton variant="inline-link" onClick={() => setUnsuspendModal(u)}>解封</CSButton>
                      )}
                      <CSButton
                        variant="inline-link"
                        onClick={() => { setTerminateModal(u); setTerminateReason(''); }}
                      >
                        永久终止
                      </CSButton>
                    </CSSpaceBetween>
                  ),
                },
              ]}
            />
          )}
        </CSSpaceBetween>
      </CSContainer>

      {/* 暂停 Modal */}
      {suspendModal && (
        <CSModal
          visible
          onDismiss={() => !suspendBusy && setSuspendModal(null)}
          header={`暂停账户 — ${suspendModal.username}`}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={suspendBusy} onClick={() => setSuspendModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={suspendBusy} disabled={!suspendForm.reason} onClick={doSuspend}>确认暂停</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSFormField label="暂停原因 *">
              <CSTextarea
                value={suspendForm.reason}
                onChange={({ detail }) => setSuspendForm((f) => ({ ...f, reason: detail.value }))}
                rows={3}
                placeholder="违规行为描述，将通过邮件告知用户…"
              />
            </CSFormField>
            <CSFormField label="暂停天数（留空 = 无限期）">
              <CSInput
                type="number"
                value={suspendForm.duration_days}
                onChange={({ detail }) => setSuspendForm((f) => ({ ...f, duration_days: detail.value }))}
                placeholder="如：7、30、90"
              />
            </CSFormField>
          </CSSpaceBetween>
        </CSModal>
      )}

      {/* 解封 Modal */}
      {unsuspendModal && (
        <CSModal
          visible
          onDismiss={() => !unsuspendBusy && setUnsuspendModal(null)}
          header={`解封账户 — ${unsuspendModal.username}`}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={unsuspendBusy} onClick={() => setUnsuspendModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={unsuspendBusy} onClick={doUnsuspend}>确认解封</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSBox>确认解封账户 <strong>{unsuspendModal.username}</strong>？解封后该用户可正常登录。</CSBox>
        </CSModal>
      )}

      {/* 终止 Modal */}
      {terminateModal && (
        <CSModal
          visible
          onDismiss={() => !terminateBusy && setTerminateModal(null)}
          header={`永久终止账户 — ${terminateModal.username}`}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton variant="link" disabled={terminateBusy} onClick={() => setTerminateModal(null)}>取消</CSButton>
                <CSButton variant="primary" loading={terminateBusy} disabled={!terminateReason} onClick={doTerminate}>确认终止（不可逆）</CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSAlert type="error">
              永久终止将撤销所有 Session、写入封禁名单，且无法撤销。请确认已完成申诉审查程序。
            </CSAlert>
            <CSFormField label="终止原因 *">
              <CSTextarea
                value={terminateReason}
                onChange={({ detail }) => setTerminateReason(detail.value)}
                rows={3}
                placeholder="如：AUP 累犯，已完成申诉流程（Ticket #XXXX）"
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

/* ─────────────────────────────────────────────────────────────────
   AdminFeedbackPage — 反馈审查队列 (FB-03)
   ───────────────────────────────────────────────────────────────── */
export function AdminFeedbackPage() {
  const [items, setItems]           = React.useState([]);
  const [loading, setLoading]       = React.useState(true);
  const [err, setErr]               = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState({ value: 'unreviewed', label: '待审核' });
  const [detailModal, setDetailModal]   = React.useState(null); // feedback item
  const [actionBusy, setActionBusy]     = React.useState(false);
  const [actionErr, setActionErr]       = React.useState(null);
  const [terminateReason, setTerminateReason] = React.useState('');

  const statusOptions = [
    { value: 'unreviewed', label: '待审核' },
    { value: 'reviewed',   label: '已审核' },
    { value: 'all',        label: '全部'   },
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
      setErr(e?.message || '加载失败');
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
      window.toast?.('操作成功', { kind: 'ok' });
      setDetailModal(null);
      setTerminateReason('');
      load(statusFilter.value);
    } catch (e) {
      setActionErr(e?.message || '操作失败');
    } finally {
      setActionBusy(false);
    }
  }

  const decisionBadge = (d) => {
    if (!d) return <CSBadge color="grey">待审核</CSBadge>;
    if (d === 'ok') return <CSBadge color="green">OK</CSBadge>;
    if (d === 'nsfw_terminate') return <CSBadge color="red">终止</CSBadge>;
    if (d === 'spam') return <CSBadge color="severity-medium">垃圾</CSBadge>;
    return <CSBadge color="grey">{d}</CSBadge>;
  };

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" header="加载失败">{err}</CSAlert>}

      <CSContainer
        header={
          <CSHeader
            variant="h2"
            description="用户提交的反馈审查队列，标记 OK / NSFW终止 / 垃圾"
            actions={
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSSelect
                  selectedOption={statusFilter}
                  options={statusOptions}
                  onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                />
                <CSButton iconName="refresh" onClick={() => load(statusFilter.value)} loading={loading}>
                  刷新
                </CSButton>
              </CSSpaceBetween>
            }
          >
            反馈审查
          </CSHeader>
        }
      >
        <CSTable
          loading={loading}
          loadingText="加载中…"
          trackBy="id"
          items={items}
          empty={
            <CSBox textAlign="center" color="inherit">
              <CSBox padding={{ bottom: 's' }} variant="p" color="inherit">暂无反馈数据</CSBox>
            </CSBox>
          }
          columnDefinitions={[
            { id: 'id',      header: 'ID',       cell: (f) => f.id },
            { id: 'user',    header: '用户',      cell: (f) => f.username || '—' },
            { id: 'ts',      header: '提交时间',   cell: (f) => fmtTime(f.created_at) },
            { id: 'status',  header: '状态',      cell: (f) => decisionBadge(f.review_decision) },
            {
              id: 'preview', header: '内容摘要',
              cell: (f) => (
                <span style={{ maxWidth: 300, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {(f.free_text || '').slice(0, 80) || '（空）'}
                </span>
              ),
            },
            {
              id: 'actions', header: '操作',
              cell: (f) => (
                <CSButton variant="inline-link" onClick={() => { setDetailModal(f); setActionErr(null); setTerminateReason(''); }}>
                  查看 / 处理
                </CSButton>
              ),
            },
          ]}
        />
      </CSContainer>

      {/* ── 详情 + 操作 Modal ── */}
      {detailModal && (
        <CSModal
          visible
          size="large"
          onDismiss={() => !actionBusy && setDetailModal(null)}
          header={`反馈 #${detailModal.id} — ${detailModal.username}`}
          footer={
            !detailModal.review_decision ? (
              <CSBox float="right">
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton variant="link" disabled={actionBusy} onClick={() => setDetailModal(null)}>取消</CSButton>
                  <CSButton variant="normal" loading={actionBusy} onClick={() => doDecision(detailModal.id, 'spam')}>
                    标垃圾
                  </CSButton>
                  <CSButton variant="primary" loading={actionBusy} onClick={() => doDecision(detailModal.id, 'ok')}>
                    标 OK
                  </CSButton>
                  <CSButton
                    variant="primary"
                    iconName="status-warning"
                    loading={actionBusy}
                    disabled={!terminateReason.trim()}
                    onClick={() => doDecision(detailModal.id, 'nsfw_terminate', terminateReason)}
                  >
                    终止账号 (NSFW)
                  </CSButton>
                </CSSpaceBetween>
              </CSBox>
            ) : (
              <CSBox float="right">
                <CSButton variant="link" onClick={() => setDetailModal(null)}>关闭</CSButton>
              </CSBox>
            )
          }
        >
          <CSSpaceBetween size="m">
            {actionErr && <CSAlert type="error">{actionErr}</CSAlert>}

            <CSBox>
              <strong>提交时间：</strong>{fmtTime(detailModal.created_at)}
              {'　'}
              <strong>状态：</strong>{decisionBadge(detailModal.review_decision)}
              {detailModal.reviewed_at && (
                <span>{'　'}<strong>审核时间：</strong>{fmtTime(detailModal.reviewed_at)}</span>
              )}
            </CSBox>

            <CSBox>
              <strong>自由文本：</strong>
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--color-background-container-content)', padding: 8, borderRadius: 4 }}>
                {detailModal.free_text || '（空）'}
              </pre>
            </CSBox>

            {Array.isArray(detailModal.excerpts) && detailModal.excerpts.length > 0 && (
              <CSBox>
                <strong>节选（{detailModal.excerpts.length} 段）：</strong>
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
                label="终止理由（终止账号(NSFW)时必填）"
                description="该理由会写入 account_delete_queue，请简明说明违规内容"
              >
                <CSTextarea
                  value={terminateReason}
                  onChange={({ detail }) => setTerminateReason(detail.value)}
                  placeholder="如: 提交了含露骨 NSFW 内容的节选…"
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
