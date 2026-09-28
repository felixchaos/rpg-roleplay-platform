/**
 * script-sharing-ref.test.jsx — 剧本「共享模式 / 引用模式」三处入口的契约。
 *
 * 旧的剧本详情「共享模式」选择器有三个问题:
 *   · 「公开」发 mode='public' 给 /pin,后端只认 pinned-snapshot / floating-latest,必回 400;
 *   · 引用模式把 target_script_id 填成剧本自己,保存成功但等于没做;
 *   · commit id 是整数,手机分享页和剧本列表徽标对它调 .slice,渲染直接抛错。
 * 现在:没有「设置引用」的入口(没有目标剧本选择器,选自己没有意义);只在剧本确实引用了
 * 另一个剧本时展示引用状态,并提供「解除引用」。公开发布走各自已有的发布开关(is_public)。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '../i18n/index.js';

import { SharingModeSelector } from '../components/scripts/SharingModeSelector.jsx';
import { ShareView } from '../mobile/scripts/ShareView.jsx';
import { referenceInfo, isReferenceMode } from '../lib/script-sharing.js';

vi.mock('../mobile/icons.jsx', () => ({ Icon: () => null }));

function installApi() {
  window.api = {
    scripts: {
      pin: vi.fn().mockResolvedValue({ ok: true }),
      unpin: vi.fn().mockResolvedValue({ ok: true, sharing_mode: 'private' }),
      commits: vi.fn().mockResolvedValue({ items: [] }),
      setVisibility: vi.fn().mockResolvedValue({ ok: true }),
    },
  };
  window.__apiToast = vi.fn();
  window.__confirm = vi.fn().mockResolvedValue(true);
}

beforeEach(() => { installApi(); });

describe('referenceInfo', () => {
  it('只认后端真实存在的两种引用模式', () => {
    expect(isReferenceMode('public')).toBe(false);
    expect(isReferenceMode('private')).toBe(false);
    expect(isReferenceMode('floating-latest')).toBe(true);
    expect(isReferenceMode('pinned-snapshot')).toBe(true);
  });
  it('整数 commit id 不抛错,转成字符串', () => {
    const info = referenceInfo({ sharing_mode: 'pinned-snapshot', current_pin_script_id: 9, current_pin_commit_id: 123 });
    expect(info).toEqual({ mode: 'pinned-snapshot', targetId: '9', commitId: '123' });
  });
  it('没有目标剧本就不算引用', () => {
    expect(referenceInfo({ sharing_mode: 'floating-latest', current_pin_script_id: null })).toBe(null);
  });
});

describe('web 剧本详情 SharingModeSelector', () => {
  it('普通剧本:什么都不渲染,没有「公开」选项,也不会发 pin', () => {
    const { container } = render(
      <SharingModeSelector script={{ id: 5, owner_id: 1, sharing_mode: 'private' }} currentUserId={1} />,
    );
    expect(container.textContent).not.toContain('公开');
    expect(container.innerHTML).toBe('');
    expect(window.api.scripts.pin).not.toHaveBeenCalled();
  });

  it('引用了别的剧本:显示目标,可以解除引用,且从不把自己当目标', async () => {
    const onChanged = vi.fn();
    render(
      <SharingModeSelector
        script={{ id: 5, owner_id: 1, sharing_mode: 'pinned-snapshot', current_pin_script_id: 9, current_pin_commit_id: 123 }}
        currentUserId={1}
        onChanged={onChanged}
      />,
    );
    expect(screen.getByText(/#9/)).toBeTruthy();
    expect(screen.getByText(/#123/)).toBeTruthy();
    fireEvent.click(screen.getByText('解除引用'));
    await waitFor(() => expect(window.api.scripts.unpin).toHaveBeenCalledWith(5));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(window.api.scripts.pin).not.toHaveBeenCalled();
  });
});

describe('手机分享页 ShareView', () => {
  const nav = { toast: vi.fn() };
  it('整数 commit id 不再让页面崩溃,显示引用状态并可解除', async () => {
    const onRefresh = vi.fn();
    render(
      <ShareView
        script={{ id: 5, owner_id: 1, title: 'T', sharing_mode: 'pinned-snapshot', current_pin_script_id: 9, current_pin_commit_id: 123 }}
        currentUserId={1}
        onBack={() => {}}
        onRefresh={onRefresh}
        nav={nav}
      />,
    );
    expect(screen.getByText(/#9/)).toBeTruthy();
    expect(screen.getByText(/#123/)).toBeTruthy();
    fireEvent.click(screen.getByText('解除引用'));
    await waitFor(() => expect(window.api.scripts.unpin).toHaveBeenCalledWith(5));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it('普通剧本不出现引用区块', () => {
    render(
      <ShareView script={{ id: 5, owner_id: 1, title: 'T', sharing_mode: 'private' }}
        currentUserId={1} onBack={() => {}} onRefresh={() => {}} nav={nav} />,
    );
    expect(screen.queryByText('解除引用')).toBe(null);
  });
});
