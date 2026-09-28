/**
 * script-edit-worldbook-contract.test.jsx — 剧本详情「世界书」编辑器与后端响应的形状对齐。
 *
 * 1. 新建条目后继续改、再点保存:POST /api/scripts/{sid}/worldbook 回的是 {ok, entry:{id,…}},
 *    旧代码读 r.id / r.entry_id(都不存在)→ 草稿 id 变 null → 第二次保存打
 *    PUT /worldbook/null(422),删除同样打到 null。
 * 2. 标签:后端把 tags 存在 metadata.tags,列表行没有顶层 tags;旧代码只读 e.tags →
 *    已有标签永远显示为空,且任何一次保存都回写 tags:[] 把它们清掉。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../platform-app.jsx', () => ({ usePlatformData: () => ({ user: { id: 5 } }) }));

import { WorldbookEditorView } from '../pages/script-edit-worldbook.jsx';

const ROW = {
  id: 7, title: '灯塔', content: '北港灯塔的设定', priority: 60, enabled: true,
  metadata: { tags: ['地点', '主线'], source: 'editor' },
};

function installApi({ rows = [] } = {}) {
  const scripts = {
    worldbook: vi.fn().mockResolvedValue({ ok: true, items: rows }),
    worldbookCreate: vi.fn().mockResolvedValue({
      ok: true, commit_id: 3,
      entry: { id: 42, title: '新条目', content: '初稿', priority: 50, enabled: true, metadata: { tags: [] } },
    }),
    worldbookUpdate: vi.fn().mockResolvedValue({ ok: true, entry: {} }),
    worldbookDelete: vi.fn().mockResolvedValue({ ok: true }),
    worldbookBatch: vi.fn().mockResolvedValue({ ok: true }),
    fork: vi.fn().mockResolvedValue({ ok: true }),
  };
  window.api = { scripts };
  window.__apiToast = vi.fn();
  return scripts;
}

const inputByLabel = (label) => screen.getByLabelText(label);

beforeEach(() => { vi.clearAllMocks(); });

describe('WorldbookEditorView —— 新建后继续编辑', () => {
  it('第二次保存打的是新条目真实 id,不是 null', async () => {
    const api = installApi();
    render(<WorldbookEditorView script={{ id: 1, owner_id: 5 }} />);
    await waitFor(() => expect(api.worldbook).toHaveBeenCalled());

    fireEvent.click(screen.getByText('新建条目'));
    fireEvent.change(inputByLabel('标题'), { target: { value: '新条目' } });
    fireEvent.change(inputByLabel('内容'), { target: { value: '初稿' } });
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(api.worldbookCreate).toHaveBeenCalledTimes(1));

    fireEvent.change(inputByLabel('内容'), { target: { value: '改过的第二稿' } });
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(api.worldbookUpdate).toHaveBeenCalledTimes(1));
    const [sid, eid, body] = api.worldbookUpdate.mock.calls[0];
    expect(sid).toBe(1);
    expect(eid).toBe(42);
    expect(body.content).toBe('改过的第二稿');
  });
});

describe('WorldbookEditorView —— 标签', () => {
  it('已有标签(metadata.tags)显示出来,保存正文时原样带回,不被清空', async () => {
    const api = installApi({ rows: [ROW] });
    render(<WorldbookEditorView script={{ id: 1, owner_id: 5 }} />);
    await screen.findByText('灯塔');
    fireEvent.click(screen.getByText('灯塔'));
    expect(await screen.findByText('主线')).toBeTruthy();

    fireEvent.change(inputByLabel('内容'), { target: { value: '北港灯塔的设定(修订)' } });
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(api.worldbookUpdate).toHaveBeenCalledTimes(1));
    const body = api.worldbookUpdate.mock.calls[0][2];
    expect(body.tags).toEqual(['地点', '主线']);
  });
});
