/**
 * api-config-twin-reload.test.jsx — 反馈 #107 的孪生入口补测。
 *
 * 保存凭据请求超时 ≠ 没存上(后端可能已经落库),所以每个能存 key 的入口失败后都要回读一次
 * 后端真实状态,不然已经存上的 key 会一直显示成「未配置」。设置页和首配拦截弹窗在
 * api-config-save-resilience.test.jsx 里锁了,这里补另外两个入口:
 *
 * 1. 设置页「供应商配置」卡片(ProviderConfigSection):失败后回读凭据,失败提示停留够久;
 *    这张卡片没有连接方式输入,请求不带 proxy 键(后端据此保留已存代理)。
 * 2. 手机端 ApisSection:失败后重新 load(凭据 + 目录);同样不带 proxy 键。
 *
 * 两个组件都用真实渲染 + 真实输入框,只把 window.api 换成桩。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

function networkError() {
  return Object.assign(new Error('网络异常:signal timed out'), { code: 'network' });
}

describe('设置页供应商卡片:保存失败后回读', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('credentials.set 超时 → 报失败(停留 9s)并回读凭据;请求不带 proxy 键', async () => {
    const credSet = vi.fn().mockRejectedValue(networkError());
    window.__apiToast = vi.fn();
    window.api = {
      credentials: { list: vi.fn().mockResolvedValue({ items: [] }), set: credSet },
      models: { upsertApi: vi.fn().mockResolvedValue({}) },
    };
    const { ProviderConfigSection } = await import('../components/settings/provider-config.jsx');
    render(<ProviderConfigSection />);
    await waitFor(() => expect(window.api.credentials.list).toHaveBeenCalledTimes(1));

    // 第一张普通供应商卡片:填 key → 保存
    const keyInput = document.querySelector('input[type="password"]');
    expect(keyInput).toBeTruthy();
    fireEvent.change(keyInput, { target: { value: 'sk-twin' } });
    const saveBtn = screen.getAllByRole('button', { name: '保存' })
      .find((b) => !b.disabled);
    expect(saveBtn).toBeTruthy();
    await act(async () => { fireEvent.click(saveBtn); });

    await waitFor(() => expect(window.api.credentials.list).toHaveBeenCalledTimes(2));
    expect(credSet).toHaveBeenCalledTimes(1);
    expect(credSet.mock.calls[0][0].api_key).toBe('sk-twin');
    expect('proxy' in credSet.mock.calls[0][0]).toBe(false);
    const dangerCall = window.__apiToast.mock.calls.find(([, o]) => o && o.kind === 'danger');
    expect(dangerCall).toBeTruthy();
    expect(dangerCall[1].duration).toBe(9000);
  });
});

describe('手机端 ApisSection:保存失败后回读', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('credentials.set 超时 → toast 报失败并重新 load;请求不带 proxy 键', async () => {
    const credSet = vi.fn().mockRejectedValue(networkError());
    const toast = vi.fn();
    window.api = {
      credentials: {
        list: vi.fn().mockResolvedValue({ items: [] }),
        set: credSet,
        remove: vi.fn(),
        test: vi.fn(),
      },
      models: {
        list: vi.fn().mockResolvedValue({ apis: [{ id: 'deepseek', name: 'DeepSeek' }] }),
      },
    };
    const { ApisSection } = await import('../mobile/caps/ApisSection.jsx');
    render(<ApisSection toast={toast} />);
    await screen.findByText('DeepSeek');
    expect(window.api.credentials.list).toHaveBeenCalledTimes(1);

    // 打开编辑抽屉(未配置时按钮是「设置」类文案,取卡片里唯一的按钮)
    const openBtn = screen.getByText('DeepSeek').closest('div[style*="grid"]').querySelector('button');
    await act(async () => { fireEvent.click(openBtn); });
    const keyInput = await waitFor(() => {
      const el = document.querySelector('input.pl-input[type="password"]');
      if (!el) throw new Error('sheet not open');
      return el;
    });
    fireEvent.change(keyInput, { target: { value: 'sk-mobile' } });
    const saveBtn = document.querySelector('.sheet-btn.primary');
    await act(async () => { fireEvent.click(saveBtn); });

    await waitFor(() => expect(window.api.credentials.list).toHaveBeenCalledTimes(2));
    expect(credSet).toHaveBeenCalledTimes(1);
    expect(credSet.mock.calls[0][0]).toEqual({ api_id: 'deepseek', api_key: 'sk-mobile' });
    expect(toast.mock.calls.some(([, kind]) => kind === 'danger')).toBe(true);
  });
});
