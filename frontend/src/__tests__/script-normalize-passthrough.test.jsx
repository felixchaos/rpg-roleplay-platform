/**
 * script-normalize-passthrough.test.jsx — 剧本列表数据真实经过 normalizeScript 之后,
 * 引用状态 / 复核状态 / 就绪度等字段还在。
 *
 * web 剧本列表(ScriptsList)和手机剧本页(MobileScripts)都用 window.__normalizeScript 处理
 * /api/scripts 回包,再交给 SharingModeSelector、列表徽标、ShareView。normalizeScript 以前是
 * 白名单,只留 id/title/…/owner_id,sharing_mode / current_pin_* / review_status / readiness 全被
 * 丢进 _raw:引用区块和「解除引用」永远不渲染,已复核的剧本在手机上点发布也永远提示先去复核。
 * 旧测试直接传原始对象,没经过 normalizeScript,所以一直是绿的。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '../i18n/index.js';

import { normalizeScript } from '../data-loader.js';
import { SharingModeSelector } from '../components/scripts/SharingModeSelector.jsx';
import { ShareView } from '../mobile/scripts/ShareView.jsx';
import { referenceInfo } from '../lib/script-sharing.js';

vi.mock('../mobile/icons.jsx', () => ({ Icon: () => null }));

// 一行 /api/scripts 回包(列与 workspace/listing.scripts_page 一致)
const RAW = {
  id: 7,
  owner_id: 1,
  title: '斗破',
  description: '',
  chapter_count: 12,
  word_count: 30000,
  updated_at: '2026-09-01T00:00:00Z',
  is_public: false,
  clone_count: 0,
  review_status: 'reviewed',
  reviewed_at: '2026-09-02T00:00:00Z',
  sharing_mode: 'floating-latest',
  current_pin_script_id: 9,
  current_pin_commit_id: null,
  head_commit_id: 33,
  forked_from_script_id: 2,
  cover_image_url: '/assets/cover.png',
  is_subscribed: false,
  readiness: { ok: false, missing: ['anchors'], items: [{ key: 'anchors', ok: false, count: 0, total: 0 }] },
};

function installApi() {
  window.api = {
    scripts: {
      unpin: vi.fn().mockResolvedValue({ ok: true, sharing_mode: 'private' }),
      setVisibility: vi.fn().mockResolvedValue({ ok: true }),
    },
  };
  window.__apiToast = vi.fn();
  window.__confirm = vi.fn().mockResolvedValue(true);
}

beforeEach(() => { installApi(); });

describe('normalizeScript', () => {
  it('后端字段整行透传,展示字段照旧派生', () => {
    const s = normalizeScript(RAW);
    for (const k of ['sharing_mode', 'current_pin_script_id', 'current_pin_commit_id', 'review_status',
      'reviewed_at', 'readiness', 'head_commit_id', 'forked_from_script_id', 'cover_image_url', 'owner_id']) {
      expect(s[k]).toEqual(RAW[k]);
    }
    expect(s.updated_at).not.toBe(RAW.updated_at); // 相对时间,不是原始 ISO
    expect(s._raw).toBe(RAW);
    expect(s.uid).toBe('scr_7');
    expect(normalizeScript({ id: 3 }).title).toBe('未命名剧本');
  });

  it('经过 normalizeScript 的剧本仍能识别引用状态', () => {
    expect(referenceInfo(normalizeScript(RAW))).toEqual({ mode: 'floating-latest', targetId: '9', commitId: '' });
  });

  it('引用自己的存量行不当引用展示', () => {
    expect(referenceInfo(normalizeScript({ ...RAW, current_pin_script_id: 7 }))).toBe(null);
  });
});

describe('真实列表数据 → 引用入口', () => {
  it('web 剧本详情:显示引用目标,可以解除', async () => {
    const onChanged = vi.fn();
    render(<SharingModeSelector script={normalizeScript(RAW)} currentUserId={1} onChanged={onChanged} />);
    expect(screen.getByText(/#9/)).toBeTruthy();
    fireEvent.click(screen.getByText('解除引用'));
    await waitFor(() => expect(window.api.scripts.unpin).toHaveBeenCalledWith(7));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('手机分享页:引用区块在,已复核的剧本可以直接发布', async () => {
    const nav = { toast: vi.fn() };
    const { container } = render(
      <ShareView script={normalizeScript(RAW)} currentUserId={1} onBack={() => {}} onRefresh={() => {}} nav={nav} />,
    );
    expect(screen.getByText('解除引用')).toBeTruthy();
    fireEvent.click(container.querySelector('.pl-toggle'));
    await waitFor(() => expect(window.api.scripts.setVisibility).toHaveBeenCalledWith(7, true));
    expect(nav.toast).not.toHaveBeenCalledWith('分享前需先完成剧本设定核对', expect.anything(), expect.anything());
  });

  it('手机分享页:未复核的剧本仍然拦下', async () => {
    const nav = { toast: vi.fn() };
    const { container } = render(
      <ShareView script={normalizeScript({ ...RAW, review_status: 'unreviewed' })} currentUserId={1}
        onBack={() => {}} onRefresh={() => {}} nav={nav} />,
    );
    fireEvent.click(container.querySelector('.pl-toggle'));
    await waitFor(() => expect(nav.toast).toHaveBeenCalledWith('分享前需先完成剧本设定核对', expect.anything(), expect.anything()));
    expect(window.api.scripts.setVisibility).not.toHaveBeenCalled();
  });
});
