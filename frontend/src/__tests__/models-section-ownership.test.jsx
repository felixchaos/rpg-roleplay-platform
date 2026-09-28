/**
 * models-section-ownership.test.jsx — 设置 → 模型页剩下三处按归属写。
 *
 * 上一轮只把「单个模型启停」「校验后删除」改成按归属路由,同页还有三处一律打管理员端点:
 *   · 改显示名:POST /api/models/model(管理员专用)。普通用户 403 被 catch 吞掉,名字在界面上
 *     改了、刷新又变回来;
 *   · 供应商总开关:POST /api/models/api(管理员专用,关的是全平台)。可开关显示的是用户自己凭据
 *     的启用态 —— 普通用户 403 被吞,管理员一点就把所有人的这个供应商关了;
 *   · 校验弹窗「全部添加」:逐个 POST /api/models/model,普通用户 N 次 403,只提示「成功 0 失败 N」。
 * 现在:改名走 lib/model-overlay-write.js 同一套归属路由;供应商开关写用户自己的凭据启用态;
 * 普通用户的「全部添加」= 重新同步自己的清单(这正是「把远端模型加进我的列表」)。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import i18n from '../i18n/index.js';

import { renameModel, isAdminOnlyModelEdit } from '../lib/model-overlay-write.js';

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
  EditApiModal: () => null, AddModelModal: () => null, VisibilityModal: () => null,
  ValidateModal: (props) => { globalThis.__validateProps = props; return null; },
}));

function fakeLibApi() {
  return { models: {
    meRenameModel: vi.fn().mockResolvedValue({ ok: true }),
    upsertModel: vi.fn().mockResolvedValue({ ok: true }),
  } };
}

describe('renameModel(归属路由)', () => {
  it('用户自己的模型 → 按用户的改名端点,不碰全局目录', async () => {
    const api = fakeLibApi();
    await renameModel(api, { apiId: 'openai', model: { id: 'gpt-x', synced: true }, display: '主力', isAdmin: true });
    expect(api.models.meRenameModel).toHaveBeenCalledWith({ api_id: 'openai', model: 'gpt-x', display_name: '主力' });
    expect(api.models.upsertModel).not.toHaveBeenCalled();
  });
  it('内置目录模型 + 管理员 → 全局目录(只发显示名,后端补丁语义不动启停)', async () => {
    const api = fakeLibApi();
    await renameModel(api, { apiId: 'openai', model: { id: 'gpt-y' }, display: 'GPT Y', isAdmin: true });
    expect(api.models.upsertModel).toHaveBeenCalledWith({ api_id: 'openai', real_name: 'gpt-y', display_name: 'GPT Y' });
  });
  it('内置目录模型 + 普通用户 → 不发请求,抛「仅管理员」', async () => {
    const api = fakeLibApi();
    await expect(renameModel(api, { apiId: 'openai', model: { id: 'gpt-y' }, display: 'x', isAdmin: false }))
      .rejects.toSatisfy(isAdminOnlyModelEdit);
    expect(api.models.upsertModel).not.toHaveBeenCalled();
    expect(api.models.meRenameModel).not.toHaveBeenCalled();
  });
});

function installApi({ role }) {
  window.RPG_AUTH = { authed: true, online: true };
  window.MOCK_PLATFORM = { user: { role } };
  window.__apiToast = vi.fn();
  window.__refreshPlatform = vi.fn().mockResolvedValue({});
  window.api = {
    models: {
      list: vi.fn().mockResolvedValue({ models: { apis: [
        { id: 'deepseek', display_name: 'DeepSeek', models: [{ real_name: 'deepseek-chat' }] },
      ] } }),
      syncRemote: vi.fn().mockResolvedValue({ ok: true, models: [
        { real_name: 'deepseek-chat', display_name: 'deepseek-chat', synced: true },
      ], synced: 1, remote_total: 1 }),
      upsertApi: vi.fn().mockResolvedValue({ ok: true }),
      upsertModel: vi.fn().mockResolvedValue({ ok: true }),
      meRenameModel: vi.fn().mockResolvedValue({ ok: true }),
    },
    credentials: {
      list: vi.fn().mockResolvedValue({ items: [
        { api_id: 'deepseek', has_credential: true, key_hint: 'abcd', enabled: true },
      ] }),
      setEnabled: vi.fn().mockResolvedValue({ ok: true, enabled: false }),
    },
  };
}

async function mountAndSelect() {
  const { ModelsSection } = await import('../components/settings/models-section.jsx');
  globalThis.__apiTableProps = null; globalThis.__detailProps = null; globalThis.__validateProps = null;
  render(<ModelsSection />);
  await waitFor(() => {
    const row = globalThis.__apiTableProps?.items?.find((a) => a.id === 'deepseek');
    expect(row?.models?.[0]?.synced).toBe(true);
  });
  await act(async () => {
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    globalThis.__apiTableProps.onRowClick({ detail: { item: row } });
  });
  await waitFor(() => expect(globalThis.__detailProps).toBeTruthy());
}

const toggleCell = () => globalThis.__apiTableProps.columnDefinitions.find((c) => c.id === 'go');

describe('ModelsSection 按归属写', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('普通用户改自己模型的显示名 → 按用户的端点', async () => {
    installApi({ role: 'user' });
    await mountAndSelect();
    await act(async () => { await globalThis.__detailProps.onRenameModel('deepseek-chat', '主力'); });
    await waitFor(() => expect(window.api.models.meRenameModel).toHaveBeenCalledWith({ api_id: 'deepseek', model: 'deepseek-chat', display_name: '主力' }));
    expect(window.api.models.upsertModel).not.toHaveBeenCalled();
  });

  it('改名失败 → 回滚成原名并提示,不再静默吞掉', async () => {
    installApi({ role: 'user' });
    window.api.models.meRenameModel = vi.fn().mockRejectedValue(new Error('boom'));
    await mountAndSelect();
    await act(async () => { await globalThis.__detailProps.onRenameModel('deepseek-chat', '主力'); });
    await waitFor(() => expect(window.__apiToast).toHaveBeenCalled());
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    expect(row.models[0].display).toBe('deepseek-chat');
  });

  it('供应商总开关 → 写用户自己凭据的启用态,不打全局目录', async () => {
    installApi({ role: 'user' });
    await mountAndSelect();
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    const cell = toggleCell().cell(row);
    await act(async () => { await cell.props.children.props.set(false); });
    await waitFor(() => expect(window.api.credentials.setEnabled).toHaveBeenCalledWith({ api_id: 'deepseek', enabled: false }));
    expect(window.api.models.upsertApi).not.toHaveBeenCalled();
  });

  it('供应商开关失败 → 回滚并提示', async () => {
    installApi({ role: 'admin' });
    window.api.credentials.setEnabled = vi.fn().mockRejectedValue(new Error('nope'));
    await mountAndSelect();
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    await act(async () => { await toggleCell().cell(row).props.children.props.set(false); });
    await waitFor(() => expect(window.__apiToast).toHaveBeenCalled());
    expect(globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek').enabled).toBe(true);
    expect(window.api.models.upsertApi).not.toHaveBeenCalled();
  });

  it('停用的供应商点联通性 → 不发同步请求,提示先打开开关(不再弹「需要先配置」)', async () => {
    installApi({ role: 'user' });
    await mountAndSelect();
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    await act(async () => { await toggleCell().cell(row).props.children.props.set(false); });
    await waitFor(() => expect(globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek').enabled).toBe(false));
    window.api.models.syncRemote.mockClear();
    window.__apiToast.mockClear();
    const off = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    const conn = globalThis.__apiTableProps.columnDefinitions.find((c) => c.id === 'connectivity').cell(off);
    await act(async () => { await conn.props.onClick({ stopPropagation() {} }); });
    expect(window.api.models.syncRemote).not.toHaveBeenCalled();
    expect(window.__apiToast).toHaveBeenCalledTimes(1);
    expect(window.__apiToast.mock.calls[0][0]).toBe(i18n.t('settings.models.sync_skip_disabled'));
    expect(window.__apiToast.mock.calls[0][1]).toMatchObject({ kind: 'info' });
  });

  it('重新打开供应商开关 → 静默补一次同步(停用期间重填的 key 没拉过模型)', async () => {
    installApi({ role: 'user' });
    window.api.credentials.list = vi.fn().mockResolvedValue({ items: [
      { api_id: 'deepseek', has_credential: true, key_hint: 'abcd', enabled: false },
    ] });
    window.api.credentials.setEnabled = vi.fn().mockResolvedValue({ ok: true, enabled: true });
    const { ModelsSection } = await import('../components/settings/models-section.jsx');
    globalThis.__apiTableProps = null;
    render(<ModelsSection />);
    await waitFor(() => expect(globalThis.__apiTableProps?.items?.find((a) => a.id === 'deepseek')).toBeTruthy());
    expect(window.api.models.syncRemote).not.toHaveBeenCalled();
    const row = globalThis.__apiTableProps.items.find((a) => a.id === 'deepseek');
    await act(async () => { await toggleCell().cell(row).props.children.props.set(true); });
    await waitFor(() => expect(window.api.models.syncRemote).toHaveBeenCalledTimes(1));
    expect(window.__apiToast).not.toHaveBeenCalled();
  });

  it('校验弹窗拿到身份与「重新同步」回调(普通用户的全部添加 = 同步自己的清单)', async () => {
    installApi({ role: 'user' });
    await mountAndSelect();
    await act(async () => { globalThis.__detailProps.onValidate(); });
    await waitFor(() => expect(globalThis.__validateProps?.open).toBe(true));
    expect(globalThis.__validateProps.isAdminUser).toBe(false);
    window.api.models.syncRemote.mockClear();
    await act(async () => { await globalThis.__validateProps.onSyncRemote(); });
    expect(window.api.models.syncRemote).toHaveBeenCalledWith({ api_id: 'deepseek', base_url: '' });
  });
});
