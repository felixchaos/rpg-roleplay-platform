/**
 * models-section-validate-remove.test.jsx — 设置 → 模型 → 校验弹窗「删除 N 个」。
 *
 * 弹窗里的待删清单 local_only 来自 GET /api/models/diff,它拿**全局目录**和供应商远端清单比:
 * 「目录里有、远端已下线」的模型。可设置页一打开就会自动同步,视图随即换成用户自己的清单
 * (远端清单 + 手填模型),这些下线模型本来就不在视图里。
 *
 * 所以删除目标必须按弹窗传进来的 id 构造,不能先拿当前视图过滤:过滤会把视图外的 id 直接丢掉,
 * 管理员勾了下线模型点删除,弹窗关掉、什么请求都没发、也没有提示,全局目录原样保留。
 * 视图外的 id 按平台内置目录模型处理:管理员走全局删除,普通用户明确提示「只有管理员能删」。
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import i18n from '../i18n/index.js';

vi.mock('../platform-app.jsx', () => ({
  SettingsToggle: () => null,
  ResizableSplit: ({ top, bottom }) => (<div>{top}{bottom}</div>),
}));
vi.mock('@cloudscape-design/components/table', () => ({
  default: (props) => { globalThis.__apiTableProps = props; return null; },
}));
vi.mock('../components/settings/model-list.jsx', () => ({
  ApiDetailPanel: (props) => { globalThis.__detailProps = props; return null; },
  ApiModelsList: () => null, ModelNameCell: () => null, HealthDot: () => null,
}));
vi.mock('../components/settings/provider-config.jsx', () => ({
  ProviderCard: () => null, ProviderConfigSection: () => null,
}));
vi.mock('../components/settings/model-modals.jsx', () => ({
  EditApiModal: (props) => { globalThis.__editApiProps = props; return null; },
  AddModelModal: (props) => { globalThis.__addModelProps = props; return null; },
  VisibilityModal: (props) => { globalThis.__visibilityProps = props; return null; },
  ValidateModal: (props) => { globalThis.__validateProps = props; return null; },
}));

function installApi({ role, syncedModels }) {
  window.RPG_AUTH = { authed: true, online: true };
  window.MOCK_PLATFORM = { user: { role } };
  window.__apiToast = vi.fn();
  window.__refreshPlatform = vi.fn().mockResolvedValue({});
  window.api = {
    models: {
      // 全局目录里还挂着已下线的 deepseek-coder-old
      list: vi.fn().mockResolvedValue({ models: { apis: [
        { id: 'deepseek', display_name: 'DeepSeek', models: [
          { real_name: 'deepseek-chat' }, { real_name: 'deepseek-coder-old' },
        ] },
      ] } }),
      // 自动同步后视图 = 用户自己的清单,只剩远端还在的 deepseek-chat
      syncRemote: vi.fn().mockResolvedValue({ ok: true, models: syncedModels || [
        { real_name: 'deepseek-chat', synced: true },
      ], synced: 1, remote_total: 1 }),
      upsertApi: vi.fn().mockResolvedValue({ ok: true }),
      deleteModel: vi.fn().mockResolvedValue({ ok: true }),
      meDeleteModel: vi.fn().mockResolvedValue({ ok: true }),
    },
    credentials: {
      list: vi.fn().mockResolvedValue({ items: [
        { api_id: 'deepseek', has_credential: true, key_hint: 'abcd' },
      ] }),
    },
  };
}

async function openValidateAndDelete(ids, meta, viewIds = ['deepseek-chat']) {
  const { ModelsSection } = await import('../components/settings/models-section.jsx');
  globalThis.__apiTableProps = null;
  globalThis.__detailProps = null;
  globalThis.__validateProps = null;
  render(<ModelsSection />);
  await waitFor(() => expect(window.api.models.syncRemote).toHaveBeenCalled());
  // 等自动同步把视图换成用户清单
  await waitFor(() => {
    const row = globalThis.__apiTableProps?.items?.find((a) => a.id === 'deepseek');
    expect(row?.models.map((m) => m.id)).toEqual(viewIds);
  });
  await act(async () => {
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    globalThis.__apiTableProps.onRowClick({ detail: { item: row } });
  });
  await waitFor(() => expect(globalThis.__detailProps).toBeTruthy());
  await act(async () => { globalThis.__detailProps.onValidate(); });
  await waitFor(() => expect(globalThis.__validateProps?.open).toBe(true));
  await act(async () => { await globalThis.__validateProps.onConfirm(ids, meta); });
}

describe('校验弹窗删除:目标按 id 构造,不按当前视图过滤', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('管理员删一个不在视图里的下线模型 → 走全局删除', async () => {
    installApi({ role: 'admin' });
    await openValidateAndDelete(['deepseek-coder-old']);
    await waitFor(() => expect(window.api.models.deleteModel).toHaveBeenCalledTimes(1));
    expect(window.api.models.deleteModel).toHaveBeenCalledWith({ api_id: 'deepseek', real_name: 'deepseek-coder-old' });
    expect(window.api.models.meDeleteModel).not.toHaveBeenCalled();
    expect(window.__apiToast).not.toHaveBeenCalled();
  });

  it('普通用户删不在视图里的模型 → 不发请求,明确提示只有管理员能删', async () => {
    installApi({ role: 'user' });
    await openValidateAndDelete(['deepseek-coder-old']);
    await waitFor(() => expect(window.__apiToast).toHaveBeenCalledTimes(1));
    expect(window.api.models.deleteModel).not.toHaveBeenCalled();
    expect(window.api.models.meDeleteModel).not.toHaveBeenCalled();
    const [msg, opts] = window.__apiToast.mock.calls[0];
    expect(msg).toBe(i18n.t('settings.models.builtin_admin_only'));
    expect(msg).toContain('只有管理员');
    expect(opts).toMatchObject({ kind: 'warn', detail: 'deepseek-coder-old' });
  });

  it('视图里自己的模型照旧走按用户的删除端点', async () => {
    installApi({ role: 'user' });
    await openValidateAndDelete(['deepseek-chat'], { base: 'user', localOnly: ['deepseek-chat'] });
    await waitFor(() => expect(window.api.models.meDeleteModel).toHaveBeenCalledTimes(1));
    expect(window.api.models.meDeleteModel).toHaveBeenCalledWith({ api_id: 'deepseek', real_name: 'deepseek-chat' });
    expect(window.api.models.deleteModel).not.toHaveBeenCalled();
  });

  it('管理员按全局目录比(base=catalog):下线条目与自己手填的模型同名 → 删目录条目,不删自己的', async () => {
    installApi({ role: 'admin', syncedModels: [
      { real_name: 'deepseek-chat', synced: true },
      { real_name: 'deepseek-coder-old', synced: true },   // 自己手填的同名模型
    ] });
    await openValidateAndDelete(['deepseek-coder-old'], { base: 'catalog', localOnly: ['deepseek-coder-old'] },
      ['deepseek-chat', 'deepseek-coder-old']);
    await waitFor(() => expect(window.api.models.deleteModel).toHaveBeenCalledTimes(1));
    expect(window.api.models.deleteModel).toHaveBeenCalledWith({ api_id: 'deepseek', real_name: 'deepseek-coder-old' });
    expect(window.api.models.meDeleteModel).not.toHaveBeenCalled();
  });

  it('管理员按自己的清单比(base=user,如中转站):同名模型按自己的删', async () => {
    installApi({ role: 'admin', syncedModels: [
      { real_name: 'deepseek-chat', synced: true },
      { real_name: 'deepseek-coder-old', synced: true },
    ] });
    await openValidateAndDelete(['deepseek-coder-old'], { base: 'user', localOnly: ['deepseek-coder-old'] },
      ['deepseek-chat', 'deepseek-coder-old']);
    await waitFor(() => expect(window.api.models.meDeleteModel).toHaveBeenCalledTimes(1));
    expect(window.api.models.deleteModel).not.toHaveBeenCalled();
  });
});

// 匿名访客 / ?demo=1 看的是内置演示数据(MODELS_DATA),不是谁的真实配置:启停和删除只在本地
// 生效,不发请求。按归属路由接进来之后,演示页点开关会回弹并提示「只有管理员能改」,这里锁住。
describe('演示数据(匿名访客)', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  function installAnon() {
    window.RPG_AUTH = { authed: false };
    window.MOCK_PLATFORM = { user: null };
    window.__apiToast = vi.fn();
    window.__confirm = vi.fn().mockResolvedValue(true);
    window.__refreshPlatform = vi.fn().mockResolvedValue({});
    window.api = {
      models: {
        list: vi.fn(), syncRemote: vi.fn(), upsertModel: vi.fn(), deleteModel: vi.fn(),
        meVisibility: vi.fn(), meDeleteModel: vi.fn(), visibility: vi.fn(),
        meUpsertModel: vi.fn(), upsertApi: vi.fn(), diff: vi.fn(),
      },
      credentials: { list: vi.fn(), set: vi.fn(), remove: vi.fn(), setEnabled: vi.fn() },
    };
  }

  // 已登录的管理员带 ?demo=1 打开设置页:看到的同样是演示数据,但他的会话能写全局目录 /
  // 自己的真实凭据。演示数据里的模型 id 与真实目录同名,发出去就改到真东西上了。
  function installAdminDemo() {
    installAnon();
    window.RPG_AUTH = { authed: true, online: true };
    window.MOCK_PLATFORM = { user: { role: 'admin' } };
    window.history.replaceState(null, '', '/settings?demo=1');
  }

  async function mountAndSelect() {
    const { ModelsSection } = await import('../components/settings/models-section.jsx');
    globalThis.__apiTableProps = null;
    globalThis.__detailProps = null;
    globalThis.__validateProps = null;
    globalThis.__editApiProps = null;
    globalThis.__addModelProps = null;
    globalThis.__visibilityProps = null;
    render(<ModelsSection />);
    await waitFor(() => expect(globalThis.__apiTableProps?.items?.length).toBeGreaterThan(0));
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'openai');
    await act(async () => { globalThis.__apiTableProps.onRowClick({ detail: { item: row } }); });
    await waitFor(() => expect(globalThis.__detailProps?.api?.id).toBe('openai'));
    return row;
  }

  const shownRow = () => globalThis.__apiTableProps.items.find((a) => a.id === 'openai');
  const shownModels = () => shownRow().models;
  const calledAny = () => [...Object.values(window.api.models), ...Object.values(window.api.credentials)]
    .some((fn) => fn.mock.calls.length > 0);
  afterEach(() => { window.history.replaceState(null, '', '/'); });

  it('点单个模型的开关 → 本地翻转,不回弹、不提示、不发请求', async () => {
    installAnon();
    const row = await mountAndSelect();
    const target = row.models.find((m) => m.enabled);
    await act(async () => { await globalThis.__detailProps.onToggleModel(target.id); });
    await waitFor(() => expect(shownModels().find((m) => m.id === target.id).enabled).toBe(false));
    expect(window.__apiToast).not.toHaveBeenCalled();
    expect(calledAny()).toBe(false);
  });

  it('管理员带 ?demo=1:管理显示模型 → 只改本地,不改全局目录', async () => {
    installAdminDemo();
    const row = await mountAndSelect();
    const keep = row.models[0].id;
    await act(async () => { globalThis.__detailProps.onVisibility(); });
    await waitFor(() => expect(globalThis.__visibilityProps?.open).toBe(true));
    await act(async () => { await globalThis.__visibilityProps.onConfirm([keep]); });
    await waitFor(() => expect(shownModels().filter((m) => m.visible).map((m) => m.id)).toEqual([keep]));
    expect(calledAny()).toBe(false);
  });

  it('管理员带 ?demo=1:添加模型 → 只加到本地列表,不写真实 overlay', async () => {
    installAdminDemo();
    await mountAndSelect();
    await act(async () => { globalThis.__detailProps.onAddModel(); });
    await waitFor(() => expect(globalThis.__addModelProps?.open).toBe(true));
    await act(async () => { await globalThis.__addModelProps.onConfirm({ id: 'my-demo-model', display: '演示' }); });
    await waitFor(() => expect(shownModels().some((m) => m.id === 'my-demo-model')).toBe(true));
    expect(calledAny()).toBe(false);
    expect(window.__apiToast).not.toHaveBeenCalled();
  });

  it('管理员带 ?demo=1:编辑供应商 → 只改本地,不写全局目录也不写自己的凭据', async () => {
    installAdminDemo();
    await mountAndSelect();
    await act(async () => { globalThis.__detailProps.onEdit(); });
    await waitFor(() => expect(globalThis.__editApiProps?.open).toBe(true));
    await act(async () => {
      await globalThis.__editApiProps.onConfirm({ id: 'openai', name: 'OpenAI', base_url: 'https://relay.example.com/v1',
        api_key: 'sk-demo', proxy: 'direct', proxy_url: '', no_auth: false });
    });
    await waitFor(() => expect(shownRow().base_url).toBe('https://relay.example.com/v1'));
    expect(calledAny()).toBe(false);
  });

  it('演示数据:点联通性 / 删除 Key → 不发请求', async () => {
    installAdminDemo();
    const row = await mountAndSelect();
    const conn = globalThis.__apiTableProps.columnDefinitions.find((c) => c.id === 'connectivity').cell(row);
    await act(async () => { await conn.props.onClick({ stopPropagation() {} }); });
    await act(async () => { await globalThis.__detailProps.onDeleteKey(); });
    expect(calledAny()).toBe(false);
  });

  it('演示数据:校验弹窗拿到 demo 标记(不去打后端 diff)', async () => {
    installAnon();
    await mountAndSelect();
    await act(async () => { globalThis.__detailProps.onValidate(); });
    await waitFor(() => expect(globalThis.__validateProps?.open).toBe(true));
    expect(globalThis.__validateProps.demo).toBe(true);
  });

  it('校验弹窗删除 → 本地移除,不提示、不发请求', async () => {
    installAnon();
    const row = await mountAndSelect();
    const target = row.models[0];
    await act(async () => { globalThis.__detailProps.onValidate(); });
    await waitFor(() => expect(globalThis.__validateProps?.open).toBe(true));
    await act(async () => { await globalThis.__validateProps.onConfirm([target.id]); });
    await waitFor(() => expect(shownModels().some((m) => m.id === target.id)).toBe(false));
    expect(window.__apiToast).not.toHaveBeenCalled();
    expect(calledAny()).toBe(false);
  });
});
