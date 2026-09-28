/**
 * mobile-models-disabled-sync.test.jsx — 手机端「设置 → 模型」:停用的供应商不同步。
 *
 * 供应商总开关切的是用户自己凭据的 enabled。后端拉取模型的入口判断「有没有凭据」时跳过停用的行,
 * 对停用的供应商同步必然失败:一打开页面,自动同步就让卡片挂上「错误」;手动点刷新还弹
 * 「同步失败:需要先配置该 provider」—— 用户明明配过 key。桌面设置页同一处已修(见
 * models-section-ownership.test.jsx),这里锁手机端孪生。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import i18n from '../i18n/index.js';

vi.mock('../platform-app.jsx', () => ({ useReactiveUser: () => ({ role: 'user' }) }));

function installApi(enabled) {
  window.api = {
    models: {
      list: vi.fn().mockResolvedValue({ models: { apis: [
        { id: 'deepseek', display_name: 'DeepSeek', models: [{ real_name: 'deepseek-chat', synced: true }] },
      ] } }),
      syncRemote: vi.fn().mockResolvedValue({ ok: true, models: [{ real_name: 'deepseek-chat', synced: true }] }),
    },
    credentials: {
      list: vi.fn().mockResolvedValue({ items: [
        { api_id: 'deepseek', has_credential: true, key_hint: 'abcd', enabled },
      ] }),
    },
  };
}

async function mount() {
  const { ModelsSection } = await import('../mobile/settings/models-section.jsx');
  const nav = { toast: vi.fn() };
  render(<ModelsSection nav={nav} onBack={() => {}} />);
  await screen.findByText('DeepSeek');
  return nav;
}

describe('手机端模型页:停用的供应商', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('打开页面不自动同步停用的供应商;手动刷新提示已停用,不发请求', async () => {
    installApi(false);
    const nav = await mount();
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(window.api.models.syncRemote).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByText('DeepSeek').closest('button')); });
    const refresh = await screen.findByTitle(i18n.t('mobile.settings.models.sync_models'));
    await act(async () => { fireEvent.click(refresh); });
    expect(window.api.models.syncRemote).not.toHaveBeenCalled();
    expect(nav.toast).toHaveBeenCalledTimes(1);
    expect(nav.toast.mock.calls[0][0]).toBe(i18n.t('mobile.settings.models.sync_skip_disabled'));
  });

  it('启用的供应商照旧自动同步', async () => {
    installApi(true);
    await mount();
    await waitFor(() => expect(window.api.models.syncRemote).toHaveBeenCalledTimes(1));
  });
});
