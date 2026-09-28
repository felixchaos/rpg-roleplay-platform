/**
 * memory-section-int.test.jsx — 设置 → 记忆页的数值项保存前取整,并以取整后的值回显。
 *
 * 后端 MemorySettings 的数值项全是 int,逐字段校验时非整数整项丢弃、回落默认值:在「固定记忆
 * 条数上限」输入 7.5(或召回条数的数字框输入 2.5 后失焦),前端照存 7.5,刷新后页面仍显示 7.5,
 * GM 实际用的却是默认 20 / 5。界面上看到的必须就是生效的值。web / 手机两端同一口径
 * (lib/int-setting.js);iOS 本来就按 Int 存。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, fireEvent, waitFor, act } from '@testing-library/react';
import '../i18n/index.js';
import { toIntSetting } from '../lib/int-setting.js';

const saveSpy = vi.fn();
vi.mock('../platform-app.jsx', () => ({ useAutoSave: () => saveSpy }));

describe('toIntSetting', () => {
  it('取整后夹到区间;空 / 非数字给 null', () => {
    expect(toIntSetting('7.5', 5, 100)).toBe(8);
    expect(toIntSetting(2.4, 2, 20)).toBe(2);
    expect(toIntSetting('300', 5, 100)).toBe(100);
    expect(toIntSetting('1', 5, 100)).toBe(5);
    expect(toIntSetting('', 5, 100)).toBe(null);
    expect(toIntSetting('abc', 5, 100)).toBe(null);
  });
});

function installProfile(preferences = {}) {
  window.api = { account: { profile: vi.fn().mockResolvedValue({ preferences }), preferences: vi.fn().mockResolvedValue({}) } };
}

describe('web 记忆页', () => {
  beforeEach(() => { saveSpy.mockClear(); vi.useRealTimers(); });

  async function mountWeb() {
    const { MemorySection } = await import('../components/settings/memory-section.jsx');
    const r = render(<MemorySection />);
    await waitFor(() => expect(window.api.account.profile).toHaveBeenCalled());
    await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
    return r;
  }

  it('召回条数数字框输入 2.5 失焦 → 存 3 并显示 3', async () => {
    installProfile();
    const { container } = await mountWeb();
    const num = container.querySelectorAll('input[type="number"]')[0];
    fireEvent.change(num, { target: { value: '2.5' } });
    fireEvent.blur(num);
    expect(saveSpy).toHaveBeenLastCalledWith('recall_depth', 3);
    expect(num.value).toBe('3');
  });

  it('固定记忆条数上限输入 7.5 → 存 8,失焦后显示 8(不存 7.5)', async () => {
    installProfile();
    const { container } = await mountWeb();
    const inputs = container.querySelectorAll('input[type="number"]');
    const pinned = inputs[inputs.length - 1];
    fireEvent.change(pinned, { target: { value: '7.5' } });
    expect(saveSpy.mock.calls.some(([k, v]) => k === 'pinned_max' && !Number.isInteger(v))).toBe(false);
    expect(saveSpy).toHaveBeenLastCalledWith('pinned_max', 8);
    fireEvent.blur(pinned);
    await waitFor(() => expect(pinned.value).toBe('8'));
  });

  it('清空后失焦 → 不保存,回显上一次的有效值', async () => {
    installProfile({ 'memory.recall_depth': 7 });
    const { container } = await mountWeb();
    const num = container.querySelectorAll('input[type="number"]')[0];
    await waitFor(() => expect(num.value).toBe('7'));
    fireEvent.change(num, { target: { value: '' } });
    fireEvent.blur(num);
    expect(saveSpy).not.toHaveBeenCalled();
    expect(num.value).toBe('7');
  });
});

describe('手机端记忆页', () => {
  beforeEach(() => { vi.useRealTimers(); });

  it('固定记忆条数上限输入 7.5 失焦 → 存 8 并显示 8', async () => {
    installProfile();
    const { MemorySection } = await import('../mobile/settings/memory-section.jsx');
    const { container } = render(<MemorySection />);
    await waitFor(() => expect(window.api.account.profile).toHaveBeenCalled());
    const pinned = container.querySelector('input[type="number"]');
    fireEvent.change(pinned, { target: { value: '7.5' } });
    fireEvent.blur(pinned);
    expect(pinned.value).toBe('8');
    await waitFor(() => expect(window.api.account.preferences).toHaveBeenCalled(), { timeout: 2000 });
    expect(window.api.account.preferences.mock.calls.at(-1)[0]).toEqual({ 'memory.pinned_max': 8 });
  });
});
