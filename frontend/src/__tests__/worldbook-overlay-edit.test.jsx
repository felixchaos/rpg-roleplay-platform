/**
 * worldbook-overlay-edit.test.jsx — 存档内世界书条目「编辑」入口(07-30 反馈)。
 *
 * 面板此前只有「+」和「×」:写错一个字只能删掉重打整段,而 overlay 正文常是几百字的
 * 功法/设定长文。加编辑后最大的风险是**接错端点** —— 前科(v1.76.0)是「添加模型」按钮
 * 接了 admin-only 的全局端点。所以这里逐条锁死:
 *   · 编辑 → overlayUpdate({id, ...}),绝不打 overlayAdd
 *   · 新增 → overlayAdd(不带 id),绝不打 overlayUpdate
 *   · 编辑态预填的是**完整正文**(列表只显示 140 字截断,拿截断值回写会截断条目)
 *   · keys 数组 ↔ 逗号串 往返不丢
 *   · 删掉正在编辑的那条 → 表单关闭(别停留在已不存在的条目上)
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { WorldbookOverlaySection } from '../components/game/WorldbookSections.jsx';

const LONG = '功'.repeat(400);

const ENTRY = {
  id: 77,
  title: '周天命星炼窍法',
  content: LONG,
  keys: ['命窍', '炼窍'],
  priority: 60,
};

function installApi(overrides = {}) {
  const api = {
    overlayList: vi.fn().mockResolvedValue({ additions: [ENTRY] }),
    overlayAdd: vi.fn().mockResolvedValue({ ok: true }),
    overlayUpdate: vi.fn().mockResolvedValue({ ok: true }),
    overlayRemove: vi.fn().mockResolvedValue({ ok: true }),
    ...overrides,
  };
  window.api = { worldbook: api };
  window.__apiToast = vi.fn();
  window.__confirm = vi.fn().mockResolvedValue(true);
  return api;
}

const byLabel = (name) => screen.getByLabelText(name);

async function renderLoaded() {
  const r = render(<WorldbookOverlaySection />);
  await screen.findByText('周天命星炼窍法');
  return r;
}

describe('WorldbookOverlaySection —— 编辑已有条目', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('列表行有编辑按钮', async () => {
    installApi();
    await renderLoaded();
    expect(byLabel('编辑')).toBeTruthy();
  });

  it('点编辑 → 表单预填完整正文(不是 140 字截断值)', async () => {
    installApi();
    await renderLoaded();
    fireEvent.click(byLabel('编辑'));
    const ta = screen.getByPlaceholderText('正文设定');
    expect(ta.value).toBe(LONG);
    expect(ta.value.length).toBe(400);
    // keys 数组 → 逗号串
    expect(screen.getByPlaceholderText('触发关键词，逗号分隔（可空）').value).toBe('命窍, 炼窍');
    expect(screen.getByPlaceholderText(/标题/).value).toBe('周天命星炼窍法');
  });

  it('保存修改打 overlayUpdate(带 id),绝不打 overlayAdd', async () => {
    const api = installApi();
    await renderLoaded();
    fireEvent.click(byLabel('编辑'));
    fireEvent.change(screen.getByPlaceholderText(/标题/), { target: { value: '周天命星炼窍法（修订）' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(api.overlayUpdate).toHaveBeenCalledTimes(1));
    expect(api.overlayAdd).not.toHaveBeenCalled();
    const arg = api.overlayUpdate.mock.calls[0][0];
    expect(arg.id).toBe(77);
    expect(arg.title).toBe('周天命星炼窍法（修订）');
    expect(arg.content).toBe(LONG);          // 正文原样带回,没被截断
    expect(arg.keys).toEqual(['命窍', '炼窍']);
    expect(arg.priority).toBe(60);
  });

  it('新增走 overlayAdd(不带 id),绝不打 overlayUpdate', async () => {
    const api = installApi();
    await renderLoaded();
    fireEvent.click(byLabel('新增条目'));
    fireEvent.change(screen.getByPlaceholderText(/标题/), { target: { value: '断剑·残' } });
    fireEvent.change(screen.getByPlaceholderText('正文设定'), { target: { value: '锋断而意不断' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(api.overlayAdd).toHaveBeenCalledTimes(1));
    expect(api.overlayUpdate).not.toHaveBeenCalled();
    expect(api.overlayAdd.mock.calls[0][0].id).toBeUndefined();
  });

  it('先编辑再点「+」→ 切回新增,表单清空(别把旧条目的 id 带进新增)', async () => {
    const api = installApi();
    await renderLoaded();
    fireEvent.click(byLabel('编辑'));
    fireEvent.click(byLabel('新增条目'));   // 关闭
    fireEvent.click(byLabel('新增条目'));   // 重开 = 新增态
    expect(screen.getByPlaceholderText(/标题/).value).toBe('');

    fireEvent.change(screen.getByPlaceholderText(/标题/), { target: { value: 'X' } });
    fireEvent.change(screen.getByPlaceholderText('正文设定'), { target: { value: 'Y' } });
    fireEvent.click(screen.getByText('保存'));
    await waitFor(() => expect(api.overlayAdd).toHaveBeenCalledTimes(1));
    expect(api.overlayUpdate).not.toHaveBeenCalled();
  });

  it('取消 → 关表单且不发请求', async () => {
    const api = installApi();
    await renderLoaded();
    fireEvent.click(byLabel('编辑'));
    fireEvent.click(screen.getByText('取消'));
    expect(screen.queryByPlaceholderText('正文设定')).toBeNull();
    expect(api.overlayUpdate).not.toHaveBeenCalled();
  });

  it('删掉正在编辑的条目 → 表单关闭', async () => {
    installApi({ overlayList: vi.fn().mockResolvedValue({ additions: [ENTRY] }) });
    await renderLoaded();
    fireEvent.click(byLabel('编辑'));
    expect(screen.getByPlaceholderText('正文设定')).toBeTruthy();
    fireEvent.click(byLabel('删除'));
    await waitFor(() => expect(screen.queryByPlaceholderText('正文设定')).toBeNull());
  });

  it('标题或正文清空 → 前端就拦住,不发请求', async () => {
    const api = installApi();
    await renderLoaded();
    fireEvent.click(byLabel('编辑'));
    fireEvent.change(screen.getByPlaceholderText('正文设定'), { target: { value: '   ' } });
    fireEvent.click(screen.getByText('保存'));
    expect(api.overlayUpdate).not.toHaveBeenCalled();
  });
});
